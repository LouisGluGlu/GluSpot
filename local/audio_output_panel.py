"""Music-surface output selector; all device work stays behind the provider."""
from __future__ import annotations

import math
import time

from PIL import Image, ImageEnhance, ImageFilter
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import (
    QAbstractButton, QGraphicsOpacityEffect, QInputDialog, QLabel,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from local.audio_output_icons import paint_audio_output_icon, paint_audio_output_status
from local.audio_output_panel_pose import output_panel_pose
from local.music_lyric_motion import MotionSimulationClock
from local.music_surface_palette import relative_luminance
from local.no_wheel_widgets import NoWheelSlider
from local.liquid_glass import GlassPushButton, paint_liquid_glass
from local.control_hover import control_local_position, install_control_hover


class _VolumeSlider(NoWheelSlider):
    """Explicit pointer/keyboard scrubber; wheel always belongs to the list."""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setRange(0, 100)
        self.setSingleStep(2)
        self.setPageStep(10)
        self.fill, self.track = QColor(170, 170, 175), QColor(255, 255, 255, 35)
        self.setCursor(Qt.CursorShape.SizeHorCursor)

    def _set_pointer_value(self, position):
        span = max(1., self.width() - 16.)
        fraction = max(0., min(1., (position.x() - 8.) / span))
        self.setValue(round(self.minimum() + (self.maximum() - self.minimum()) * fraction))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.setSliderDown(True)
            self._set_pointer_value(event.position())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self._set_pointer_value(event.position())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self._set_pointer_value(event.position())
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            self.paint_contents(painter)

    def paint_contents(self, painter):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        start, end = QPointF(8, self.height() / 2), QPointF(self.width() - 8, self.height() / 2)
        fraction = (self.value() - self.minimum()) / max(1, self.maximum() - self.minimum())
        thumb = QPointF(start.x() + (end.x() - start.x()) * fraction, start.y())
        painter.setPen(QPen(self.track, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(start, end)
        painter.setPen(QPen(self.fill, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(start, thumb)
        painter.setPen(QPen(QColor(255, 255, 255, 100), 1))
        painter.setBrush(self.fill)
        painter.drawEllipse(thumb, 4.5, 4.5)
        if self.hasFocus():
            painter.setPen(QPen(self.fill, 1, Qt.PenStyle.DotLine))
            painter.drawLine(8, self.height() - 3, self.width() - 8, self.height() - 3)


class _RowActionButton(QAbstractButton):
    """Separate keyboard/accessibility target; the row paints its glyph."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def paintEvent(self, event):
        if self.hasFocus():
            with QPainter(self) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(QPen(self.parentWidget().accent, 1, Qt.PenStyle.DotLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(QRectF(self.rect()).adjusted(3, 3, -3, -3), 8, 8)


def _paint_volume_symbol(painter, rect, color, *, silent=False, unknown=False):
    """Compact speaker/waves or speaker/cross, using the row's contrast ink."""
    painter.save()
    painter.translate(rect.topLeft())
    painter.scale(rect.width() / 24, rect.height() / 24)
    painter.setPen(QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(color)
    speaker = QPainterPath()
    speaker.moveTo(3, 8)
    speaker.lineTo(7, 8)
    speaker.lineTo(12, 4)
    speaker.lineTo(12, 20)
    speaker.lineTo(7, 16)
    speaker.lineTo(3, 16)
    speaker.closeSubpath()
    painter.drawPath(speaker)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if silent:
        painter.drawLine(QPointF(16, 9), QPointF(21, 15))
        painter.drawLine(QPointF(21, 9), QPointF(16, 15))
    elif not unknown:
        painter.drawArc(QRectF(9, 7, 9, 10), -60 * 16, 120 * 16)
        painter.drawArc(QRectF(8, 3, 15, 18), -60 * 16, 120 * 16)
    painter.restore()


class _OutputRow(QAbstractButton):
    """Accessible route button with a separate actual-device volume control."""

    volume_requested = Signal(str, float)
    solo_requested = Signal(str)
    join_requested = Signal(str)
    mute_requested = Signal(str)
    details_animation_requested = Signal()

    def __init__(self, device: AudioRouteDevice, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.device, self.state = device, "available"
        self.progress = self.elapsed = 0.0
        self.ink, self.muted = QColor(245, 245, 245), QColor(180, 180, 185)
        self.accent = QColor(self.ink)
        self.hover_fill, self.selected_fill = QColor(255, 255, 255, 14), QColor(245, 245, 247)
        self.track_fill = QColor(125, 125, 130)
        self.label_font = QFont("Segoe UI", 10)
        self._row_height = 56
        self.delay_text = ""
        self.grouped = False
        self._requested_volume: float | None = None
        self._request_until = 0.0
        self.details_visible = False
        self.details_progress = 0.0
        self._pointer_action = ""
        self.volume_slider = _VolumeSlider(self)
        self.volume_slider.valueChanged.connect(self._volume_changed)
        self.solo_button = _RowActionButton(self)
        self.solo_button.clicked.connect(lambda: self.solo_requested.emit(self.device.id))
        self.join_button = _RowActionButton(self)
        self.join_button.clicked.connect(lambda: self.join_requested.emit(self.device.id))
        self.clicked.connect(self._activate)
        self.setFixedHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCheckable(True)
        self.set_device(device)
        install_control_hover(self, max_scale=1.04, enabled_when=self._hover_motion_allowed,
                              bounds_provider=self._hover_bounds)

    def _hover_bounds(self):
        ancestor = self.parentWidget()
        while ancestor is not None:
            if isinstance(ancestor, AudioOutputPanel):
                viewport = ancestor.scroll.viewport()
                origin = self.parentWidget().mapFrom(viewport, QPoint())
                return QRectF(origin, viewport.size()).adjusted(1, 1, -1, -1).intersected(
                    QRectF(self.parentWidget().rect()))
            ancestor = ancestor.parentWidget()
        return QRectF(self.parentWidget().rect()) if self.parentWidget() else QRectF(self.geometry())

    def _hover_motion_allowed(self):
        ancestor = self.parentWidget()
        while ancestor is not None:
            if isinstance(ancestor, AudioOutputPanel):
                return (ancestor._native_content_ready()
                        and self._hover_bounds().contains(QRectF(self.geometry())))
            ancestor = ancestor.parentWidget()
        return True

    def nextCheckState(self):
        # Selection belongs to confirmed provider snapshots, including for
        # keyboard and accessibility activation of this checkable button.
        pass

    def _activate(self):
        action = self._pointer_action or ("mute" if self.state == "active" else "solo")
        signal = self.mute_requested if action == "mute" else self.join_requested if action == "join" else self.solo_requested
        signal.emit(self.device.id)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.state == "available" and self.join_button.geometry().contains(event.position().toPoint()):
                self._pointer_action = "join"
            else:
                self._pointer_action = ("solo" if event.position().x() < 52 or self.state != "active"
                                        else "mute")
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._pointer_action = ""

    def set_details_visible(self, visible, *, immediate=False):
        visible = bool(visible and self.state == "active" and self.isEnabled())
        if self.details_visible != visible:
            self.details_visible = visible
            if not immediate:
                self.details_animation_requested.emit()
            self.update()
        if immediate:
            self.details_progress = float(visible)
            self.update()

    @property
    def details_animating(self):
        return abs(float(self.details_visible) - self.details_progress) > .001

    @property
    def name_font_size(self):
        normal = max(13, round(14 * self.height() / 56))
        return normal + (12 - normal) * self.details_progress

    @property
    def displayed_volume(self):
        value = self.device.volume
        if value is None or not math.isfinite(value):
            return None
        return 0 if self.device.volume_muted else round(max(0.0, min(1.0, value)) * 100)

    def _volume_changed(self, value):
        self._requested_volume = value / 100.0
        self._request_until = time.monotonic() + 2.0
        self.volume_requested.emit(self.device.id, self._requested_volume)

    def set_device(self, device):
        self.device = device
        # A live AirPlay session survives a transient mDNS disappearance.
        usable = bool(device.available or self.state in {"active", "pending"} and
                      device.transport == "airplay" and device.volume is not None)
        self.setEnabled(usable)
        if self.state != "active" or not usable:
            # Inactive rows retain their ordinary font even during a route
            # transition; a hover fade belongs only to a confirmed output.
            self.set_details_visible(False, immediate=True)
        self.setAccessibleName(str(device.name))
        self.setToolTip(str(device.name))
        known = device.volume is not None and math.isfinite(device.volume)
        editable = known and (self.state == "active" or self.volume_slider.isSliderDown())
        if not editable and self.volume_slider.hasFocus():
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.volume_slider.setVisible(editable)
        self.volume_slider.setEnabled(editable and usable)
        if self.state != "active" and self.solo_button.hasFocus():
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        if self.state != "available" and self.join_button.hasFocus():
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.solo_button.setVisible(self.state == "active")
        self.solo_button.setEnabled(usable)
        self.solo_button.setAccessibleName(f"仅播放到{device.name}")
        self.solo_button.setToolTip("仅使用此设备播放")
        self.join_button.setVisible(self.state == "available")
        self.join_button.setEnabled(usable)
        self.join_button.setAccessibleName(f"加入{device.name}同步播放")
        self.join_button.setToolTip("加入同步播放")
        muted = "（已静音）" if device.volume_muted else ""
        self.volume_slider.setAccessibleName(f"{device.name}的音量{muted}")
        self.volume_slider.setToolTip(f"拖动调整设备音量{muted}；方向键微调，滚轮滚动列表")
        if not known:
            self._requested_volume = None
        elif not self.volume_slider.isSliderDown():
            if (self._requested_volume is not None and
                    (abs(device.volume - self._requested_volume) <= .005 or time.monotonic() >= self._request_until)):
                self._requested_volume = None
            if self._requested_volume is None:
                self.volume_slider.blockSignals(True)
                self.volume_slider.setValue(self.displayed_volume)
                self.volume_slider.blockSignals(False)
        self._layout_volume()

    @property
    def subtitle(self):
        if self.state == "pending":
            return "正在连接…"
        if self.state == "active":
            if self.grouped:
                return "同步播放"
            return "正在投放" if self.device.transport == "airplay" else "当前输出"
        if not self.device.available:
            return "设备不可用"
        if self.device.transport == "airplay":
            return "未连接"
        return "蓝牙音频" if self.device.is_bluetooth else "本地音频"

    def _layout_volume(self):
        self.volume_slider.setGeometry(52, max(26, self.height() - 28),
                                       max(1, self.width() - 66), 24)
        self.solo_button.setGeometry(1, 1, 47, max(1, self.height() - 2))
        self.join_button.setGeometry(max(0, self.width() - 44), 1, 43, max(1, self.height() - 2))

    def set_row_height(self, height):
        self._row_height = max(64 if self.state == "active" or self.volume_slider.isSliderDown() else 48, round(height))
        self.setFixedHeight(self._row_height)
        self._layout_volume()

    def volume_fill_rect(self):
        """Displayed volume fill; a muted endpoint has no audible white fill."""
        bounds = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        fraction = 0 if self.device.volume_muted else self.volume_slider.value() / 100.0
        return QRectF(bounds.left(), bounds.top(), bounds.width() * fraction, bounds.height())

    def volume_hit(self, position):
        if self.state != "active" or not self.volume_slider.isEnabled() or self.volume_slider.isHidden():
            return False
        return QRectF(self.volume_slider.geometry()).contains(position)

    def resizeEvent(self, event):
        self._layout_volume()
        super().resizeEvent(event)

    def advance(self, elapsed: float):
        self.elapsed += elapsed
        self.progress += ((1.0 if self.state == "active" else 0.0) - self.progress) * (1 - math.exp(-elapsed * 15))
        target = float(self.details_visible)
        self.details_progress += (target - self.details_progress) * (1 - math.exp(-elapsed * 18))
        if not self.details_animating:
            self.details_progress = target
        self.sync_volume_style()
        self.update()
        self.volume_slider.update()

    def sync_volume_style(self):
        # Slider colors use the same selected-pose progression as the capsule.
        target = QColor(103, 103, 109)
        self.volume_slider.fill = QColor(*(round(a * (1 - self.progress) + b * self.progress)
                                         for a, b in zip(self.muted.getRgb()[:3], target.getRgb()[:3])))
        self.volume_slider.track = QColor(196, 196, 202) if self.progress > .5 else QColor(self.muted.red(), self.muted.green(), self.muted.blue(), 55)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            self.paint_contents(painter, include_volume=False)

    def paint_contents(self, painter, *, include_volume=True):
        """Draw cached row state on its widget or the unfolding chooser."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        capsule = QPainterPath()
        capsule.addRoundedRect(bounds, 27, 27)
        track = QColor(self.track_fill)
        track.setAlpha(round(255 * self.progress))
        paint_liquid_glass(painter, bounds, radius=27, base_color=track,
                          light=self.ink.lightness() < 128, hovered=self.underMouse(),
                          pressed=self.isDown(), opacity=1.0 if self.isEnabled() else .48)
        if not self.volume_slider.isHidden():
            painter.save()
            painter.setClipRect(self.volume_fill_rect(), Qt.ClipOperation.IntersectClip)
            paint_liquid_glass(painter, bounds, radius=27, base_color=self.selected_fill,
                              light=True, active=True, hovered=self.underMouse(), pressed=self.isDown(),
                              opacity=self.progress)
            painter.restore()
        if self.hasFocus():
            painter.setPen(QPen(self.accent, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(capsule)
        if self.progress < .1 and (self.underMouse() or self.hasFocus()):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.hover_fill)
            painter.drawRoundedRect(bounds, 30, 30)
        ink = QColor(self.ink if self.progress > .5 else self.muted)
        arrival = 1 - math.exp(-self.elapsed * 12)
        painter.save()
        icon = QRectF(11, (self.height() - 30) / 2, 30, 30)
        painter.translate(icon.center())
        painter.scale(.84 + .16 * arrival, .84 + .16 * arrival)
        painter.translate(-icon.center())
        # Draw once over the track and once over the white volume portion,
        # so the exact same glyph stays legible as the volume boundary moves.
        paint_audio_output_icon(painter, icon, self.device.kind, ink)
        if not self.volume_slider.isHidden():
            painter.setClipRect(self.volume_fill_rect(), Qt.ClipOperation.IntersectClip)
            paint_audio_output_icon(painter, icon, self.device.kind, QColor(40, 40, 44))
        painter.restore()
        font = QFont(self.label_font)
        normal_size = max(13, round(14 * self.height() / 56))
        font.setPixelSize(normal_size)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(ink)
        detail = bool(self.delay_text or self.state == "pending")
        right_space = 57 if self.state == "active" else 47 if self.state == "available" else 12
        name_rect = QRectF(52, 7 if self.state == "active" else (self.height() - 34) / 2 if detail else (self.height() - 25) / 2,
                           max(1, self.width() - 52 - right_space), 18 if detail else 25)
        name_scale = self.name_font_size / normal_size
        name = QFontMetricsF(font).elidedText(str(self.device.name), Qt.TextElideMode.ElideRight,
                                           name_rect.width() / name_scale)
        self._paint_name(painter, name_rect, name, name_scale)
        if not self.volume_slider.isHidden():
            painter.save()
            painter.setClipRect(self.volume_fill_rect())
            painter.setPen(QColor(40, 40, 44))
            self._paint_name(painter, name_rect, name, name_scale)
            painter.restore()
        font.setPixelSize(10)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        if self.delay_text:
            # Keep compensation diagnostics out of the drag track.
            detail = QRectF(52, 25 if self.state == "active" else (self.height() - 34) / 2 + 18,
                            name_rect.width(), 12)
            painter.setPen(ink)
            painter.drawText(detail, Qt.AlignmentFlag.AlignVCenter, self.delay_text)
            if not self.volume_slider.isHidden():
                painter.save()
                painter.setClipRect(self.volume_fill_rect())
                painter.setPen(QColor(40, 40, 44))
                painter.drawText(detail, Qt.AlignmentFlag.AlignVCenter, self.delay_text)
                painter.restore()
        if self.state == "active":
            volume = self.displayed_volume
            glyph = QRectF(self.width() - 56, 8, 18, 18)
            amount = QRectF(self.width() - 35, 6, 25, 23)
            font.setPixelSize(12)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            for color, clipped in ((ink, False), (QColor(40, 40, 44), True)):
                painter.save()
                if clipped:
                    painter.setClipRect(self.volume_fill_rect(), Qt.ClipOperation.IntersectClip)
                _paint_volume_symbol(painter, glyph, color, silent=volume == 0, unknown=volume is None)
                painter.setPen(color)
                painter.drawText(amount, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                                 "—" if volume is None else str(volume))
                painter.restore()
        elif self.state == "available":
            # Paint in the row as well as the morph so both poses share the
            # exact same pixels. The child supplies the accessible hit target.
            plus = QRectF(self.width() - 34, (self.height() - 18) / 2, 18, 18)
            color = QColor(0, 157, 205) if self.isEnabled() else QColor(self.muted)
            painter.setPen(QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(plus)
            painter.drawLine(QPointF(plus.center().x() - 4, plus.center().y()),
                             QPointF(plus.center().x() + 4, plus.center().y()))
            painter.drawLine(QPointF(plus.center().x(), plus.center().y() - 4),
                             QPointF(plus.center().x(), plus.center().y() + 4))
        if self.state == "pending":
            painter.setPen(QPen(ink, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(QRectF(self.width() - 25, (self.height() - 12) / 2, 12, 12),
                            int(-self.elapsed * 280 * 16), 250 * 16)
        if include_volume and not self.volume_slider.isHidden():
            painter.save()
            painter.translate(self.volume_slider.pos())
            self.volume_slider.paint_contents(painter)
            painter.restore()

    @staticmethod
    def _paint_name(painter, rect, text, scale):
        if scale == 1.0:
            painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter, text)
            return
        # Qt pixel font sizes are integral. Transform the native glyphs at the
        # row's actual DPR so intermediate sizes remain continuous.
        painter.save()
        painter.translate(rect.left(), rect.center().y())
        painter.scale(scale, scale)
        painter.drawText(QRectF(0, -rect.height() / scale / 2,
                               rect.width() / scale, rect.height() / scale),
                         Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()

class AudioOutputPanel(QWidget):
    """Cached frosted popup that queues actions through published route state."""

    dismissed = Signal()
    APPEAR_SECONDS = .32
    MATERIAL_INTERVAL = 1.0
    ROW_HOVER_SECONDS = .5

    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self.provider = provider
        self._snapshot: AudioRouteSnapshot | None = None
        self._rows: dict[str, _OutputRow] = {}
        self._device_order = ()
        self._anchor = QPoint()
        self._appearance_elapsed = 0.0
        self._reveal = self._reveal_velocity = 0.0
        self._reveal_target = 0.0
        self._source_rect = QRectF()
        self._source_kinds: tuple[str, ...] = ()
        self._hover_volume_id = ""
        self._hover_volume_since = 0.0
        self._hover_row_id = ""
        self._hover_row_since = 0.0
        self._list_wheel_remainder = 0.0
        self._edge_offset = self._edge_velocity = 0.0
        self._rows_base_pos = QPoint()
        self._clock = MotionSimulationClock()
        self._action_error = ""
        self._ui_key = None
        self._style_key = None
        self._max_height = 460
        self._row_height = 56
        self._inset = 12
        self._material = QImage()
        self._material_at, self._material_key = -math.inf, None
        self._background = QColor(35, 35, 38)
        self._ink, self._muted = QColor(245, 245, 247), QColor(195, 195, 200)
        self._accent = QColor(self._ink)
        self._hover_fill, self._selected_fill = QColor(255, 255, 255, 14), QColor(245, 245, 247)
        self._label_font = QFont()
        self._label_font.setFamilies(["Segoe UI", "Microsoft YaHei UI"])
        self._label_font.setPixelSize(13)
        self.setObjectName("GluSpotAudioOutputPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("音频输出设备")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(self._inset, self._inset, self._inset, self._inset)
        layout.setSpacing(6)
        # The compact chooser has no toolbar. Keep scope and maintenance
        # operations accessible through descriptions/shortcuts, not permanent
        # controls competing with the device list.
        self.title_label = QLabel("音频输出", self)
        self.title_label.hide()
        self.refresh_button = GlassPushButton("↻", self)
        self.refresh_button.setAccessibleName("刷新音频设备")
        self.refresh_button.setToolTip("刷新设备")
        self.refresh_button.clicked.connect(self._refresh)
        self.refresh_button.setShortcut("F5")
        self.add_button = GlassPushButton("＋", self)
        self.add_button.setAccessibleName("通过 IP 地址添加 AirPlay 设备")
        self.add_button.setToolTip("通过 IP 地址添加 AirPlay 设备")
        self.add_button.setEnabled(callable(getattr(provider, "add_homepod", None)))
        self.add_button.clicked.connect(self._add_homepod)
        self.add_button.setShortcut("Ctrl+N")
        self.close_button = GlassPushButton("×", self)
        self.close_button.setAccessibleName("关闭设备面板")
        self.close_button.clicked.connect(self.dismiss)
        for button in (self.refresh_button, self.add_button, self.close_button):
            button.setFixedSize(28, 28)
            button.hide()
        self.scope_label = QLabel(self)
        self.scope_label.setWordWrap(True)
        self.scope_label.hide()
        self.status_label = QLabel("正在读取设备…")
        self.status_label.setWordWrap(True)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rows_widget = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_widget)
        self.rows_layout.setContentsMargins(6, 6, 6, 6)
        self.rows_layout.setSpacing(9)
        self.scroll.setWidget(self.rows_widget)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.status_label)
        self.empty_label = QLabel("暂无可用输出设备")
        self.empty_label.setMargin(12)
        self.rows_layout.addWidget(self.empty_label)
        self.stop_button = GlassPushButton("停止投放")
        self.stop_button.setFixedHeight(30)
        self.stop_button.clicked.connect(self._stop)
        self.rows_layout.addWidget(self.stop_button)
        self.rows_layout.addStretch(1)
        self._install_pointer_filters(self)
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._opacity.setEnabled(False)
        # Child layout/hit targets stay native. During the morph, the chooser
        # draws the same cached rows into its continuous antialiased shell.
        self._scroll_opacity = QGraphicsOpacityEffect(self.scroll)
        self.scroll.setGraphicsEffect(self._scroll_opacity)
        self._status_opacity = QGraphicsOpacityEffect(self.status_label)
        self.status_label.setGraphicsEffect(self._status_opacity)
        self._animation_timer = QTimer(self)
        self._animation_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._animation_timer.setInterval(16)
        self._animation_timer.timeout.connect(self._advance_animation)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(350)
        self._poll_timer.timeout.connect(self.sync_snapshot)
        self._hover_detail_timer = QTimer(self)
        self._hover_detail_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._hover_detail_timer.setSingleShot(True)
        self._hover_detail_timer.setInterval(round(self.ROW_HOVER_SECONDS * 1000))
        self._hover_detail_timer.timeout.connect(self._reveal_hover_details)
        self._sync_surface_style()
        self.hide()

    @property
    def reveal_progress(self):
        return self._reveal

    @property
    def is_closing(self):
        return self.isVisible() and self._reveal_target == 0.0

    def _native_content_ready(self):
        return (self._reveal_target == 1.0 and abs(self._reveal - 1.0) < .001
                and abs(self._reveal_velocity) < .02)

    def _install_pointer_filters(self, widget):
        widget.setMouseTracking(True)
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.setMouseTracking(True)
            child.installEventFilter(self)

    def _row_at(self, global_position):
        if not self.isVisible() or not self._native_content_ready():
            return None
        viewport = self.scroll.viewport()
        if not viewport.rect().contains(viewport.mapFromGlobal(global_position.toPoint())):
            return None
        for row in self._rows.values():
            position = control_local_position(row, global_position)
            capsule = QPainterPath()
            capsule.addRoundedRect(QRectF(row.rect()).adjusted(1, 1, -1, -1), 27, 27)
            if row.isEnabled() and capsule.contains(position):
                return row
        return None

    def _volume_row_at(self, global_position):
        row = self._row_at(global_position)
        return row if row and row.volume_hit(control_local_position(row, global_position)) else None

    def _clear_row_hover(self):
        self._hover_detail_timer.stop()
        if row := self._rows.get(self._hover_row_id):
            row.set_details_visible(False)
        self._hover_row_id = self._hover_volume_id = ""

    def _reveal_hover_details(self):
        if (self.isVisible() and self._native_content_ready()
                and (row := self._rows.get(self._hover_row_id)) and row.isEnabled()
                and row.state == "active"):
            row.set_details_visible(True)

    def _update_volume_hover(self, global_position, now):
        hovered = self._row_at(global_position)
        hovered_id = hovered.device.id if hovered else ""
        if hovered_id != self._hover_row_id:
            self._clear_row_hover()
            self._hover_row_id, self._hover_row_since = hovered_id, now
            if hovered and hovered.state == "active" and hovered.isEnabled():
                self._hover_detail_timer.start()
        elif hovered and now - self._hover_row_since >= self.ROW_HOVER_SECONDS:
            self._reveal_hover_details()
        row = hovered if hovered and hovered.volume_hit(control_local_position(hovered, global_position)) else None
        identity = row.device.id if row else ""
        if identity != self._hover_volume_id:
            self._hover_volume_id = identity
            self._hover_volume_since = now
        return row

    def eventFilter(self, watched, event):
        kind = event.type()
        if (kind in {QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease}
                and self.isVisible() and not self._native_content_ready()):
            # The native rows still have their final layout for accessibility
            # and painting, but compressed rows must never own hidden clicks.
            event.accept()
            return True
        if kind in {QEvent.Type.MouseMove, QEvent.Type.Enter} and hasattr(event, "globalPosition"):
            self._update_volume_hover(event.globalPosition(), time.monotonic())
        elif kind == QEvent.Type.Leave:
            # Time spent outside the row cannot arm its volume control.
            # Moving between the row and its join target keeps one dwell.
            if watched is self or watched is self.scroll.viewport() or isinstance(watched, _OutputRow):
                self._clear_row_hover()
        elif kind == QEvent.Type.Wheel and self.isVisible():
            self._handle_wheel(event)
            return True
        return super().eventFilter(watched, event)

    def wheelEvent(self, event):
        self._handle_wheel(event)

    def _handle_wheel(self, event):
        """Wheel always scrolls the chooser, never changes an endpoint gain."""
        event.accept()
        if not self._native_content_ready():
            return
        # A held scrub gesture owns its fixed coordinate frame until release.
        # Scrolling underneath it would move/crop the captured target.
        if any(row.volume_slider.isSliderDown() for row in self._rows.values()):
            return
        now = time.monotonic()
        self._update_volume_hover(event.globalPosition(), now)
        pixel = event.pixelDelta()
        angle = event.angleDelta()
        # Pixel deltas are already in the platform's natural-scroll direction;
        # Qt's inverted() is informational and must not invert them again.
        delta = pixel.y() if not pixel.isNull() else angle.y() / 120.0 * 36.0
        if not delta:
            return
        self._scroll_list(delta)

    def _scroll_list(self, delta):
        bar = self.scroll.verticalScrollBar()
        self._list_wheel_remainder += -delta
        step = math.trunc(self._list_wheel_remainder)
        self._list_wheel_remainder -= step
        before = bar.value()
        bar.setValue(before + step)
        unconsumed = step - (bar.value() - before)
        if bar.value() != before:
            # Scrolling changes the row beneath the pointer; restart only its
            # half-second typography/detail hover, retaining list wheel ownership.
            self._clear_row_hover()
        if unconsumed:
            movable = bar.maximum() - bar.minimum()
            limit = min(13.0, movable * .055)
            if limit > 0:
                direction = -1.0 if unconsumed > 0 else 1.0
                self._edge_offset = max(-limit, min(limit, self._edge_offset + direction * min(limit, abs(unconsumed) * .15)))
                self._edge_velocity = 0.0
                self._apply_edge_offset()
                self._start_animation()

    def _apply_edge_offset(self):
        self._rows_base_pos = QPoint(0, -self.scroll.verticalScrollBar().value())
        self.rows_widget.move(self._rows_base_pos + QPoint(0, round(self._edge_offset)))

    def _start_animation(self):
        if self.isVisible() and not self._animation_timer.isActive():
            self._clock.reset(time.monotonic())
            self._animation_timer.start()

    def _sync_surface_style(self):
        parent = self.parentWidget()
        sample = getattr(parent, "_background_sample_colors", None)
        colors = sample(QRectF(self.geometry())) if callable(sample) else (QColor(30, 30, 32),)
        backdrop = QColor(*(round(sum(color.getRgb()[i] for color in colors) / len(colors)) for i in range(3)))
        light = relative_luminance(backdrop) > .45
        target = QColor(248, 248, 250) if light else QColor(43, 43, 46)
        background = QColor(*(round(a * .22 + b * .78) for a, b in zip(backdrop.getRgb()[:3], target.getRgb()[:3])))
        factory = getattr(parent, "_surface_font", None)
        font = factory(13, QFont.Weight.Normal) if callable(factory) else self._label_font
        key = (background.rgba(), light, font.toString())
        if key == self._style_key:
            return
        self._style_key = key
        self._background, self._label_font = background, QFont(font)
        self._ink = QColor(30, 30, 34) if light else QColor(246, 246, 248)
        self._muted = QColor(89, 89, 96) if light else QColor(190, 190, 198)
        self._accent = QColor(self._ink)
        self._hover_fill = QColor(0, 0, 0, 10) if light else QColor(255, 255, 255, 14)
        self.setFont(font)
        title_font = QFont(font)
        title_font.setPixelSize(14)
        title_font.setWeight(QFont.Weight.DemiBold)
        self.title_label.setFont(title_font)
        minor = QFont(font)
        minor.setPixelSize(11)
        self.status_label.setFont(minor)
        self.status_label.setMaximumHeight(math.ceil(QFontMetricsF(minor).lineSpacing()) * 3)
        self.scope_label.setFont(minor)
        self.scope_label.setStyleSheet(f"color: {self._muted.name()};")
        ink, muted = self._ink.name(), self._muted.name()
        fill = "0,0,0" if light else "255,255,255"
        self.setStyleSheet(f"""
            QLabel {{ color: {ink}; background: transparent; border: none; }}
            QPushButton {{ color: {muted}; background: transparent; border: 1px solid transparent; border-radius: 14px; padding: 0 4px; }}
            QPushButton:hover {{ color: {ink}; background: rgba({fill},18); }}
            QPushButton:focus {{ color: {ink}; border: 1px solid {muted}; }}
            QPushButton:disabled {{ color: {muted}; }}
            QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: transparent; width: 4px; }}
            QScrollBar::handle:vertical {{ background: {muted}; border-radius: 2px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        for button in (self.refresh_button, self.add_button, self.close_button, self.stop_button):
            button.setGlassLight(light)
            button.setProperty("glassRadius", 14.0)
        for row in self._rows.values():
            self._style_row(row)

    def _style_row(self, row):
        row.set_row_height(self._row_height)
        row.ink, row.muted, row.accent = self._ink, self._muted, self._accent
        row.hover_fill, row.selected_fill, row.label_font = self._hover_fill, self._selected_fill, self._label_font
        row.track_fill = QColor(155, 155, 161) if relative_luminance(self._background) > .45 else QColor(123, 123, 128)
        row.sync_volume_style()

    def _capture_material(self, force=False):
        """Blur the displayed backdrop without repainting the music page."""
        parent = self.parentWidget()
        if parent is None or not self.width() or not self.height():
            return
        now, geometry = time.monotonic(), self.geometry()
        backdrop = getattr(parent, "_background_cache", None)
        backdrop_key = getattr(parent, "_background_cache_key", None)
        # The music page stretches a small raster to its logical bounds. Its
        # key records those bounds, so stale frames from a resize cannot be
        # treated as a crop of the currently displayed page.
        displayed = (isinstance(backdrop, QImage) and not backdrop.isNull()
                     and isinstance(backdrop_key, tuple) and len(backdrop_key) >= 2
                     and backdrop_key[:2] == (parent.width(), parent.height()))
        geometry_key = (geometry.x(), geometry.y(), geometry.width(), geometry.height(), self._style_key)
        key = geometry_key + (backdrop.cacheKey() if displayed else None,)
        same_bounds = self._material_key is not None and self._material_key[:4] == geometry_key[:4]
        if not force and same_bounds:
            if displayed and key == self._material_key:
                return
            if isinstance(backdrop, QImage) and not displayed and self._material_key[:-1] == geometry_key:
                # An unavailable music frame needs one fallback, rather than
                # another full lyric/page render on every material interval.
                return
            if now - self._material_at < self.MATERIAL_INTERVAL:
                return
        region = geometry.adjusted(-24, -24, 24, 24).intersected(parent.rect())
        if region.isEmpty():
            return
        source = QImage(max(1, math.ceil(region.width() / 2)), max(1, math.ceil(region.height() / 2)), QImage.Format.Format_RGBA8888)
        source.fill(self._background)
        with QPainter(source) as painter:
            if displayed:
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                raster_region = QRectF(region.x() * backdrop.width() / parent.width(),
                                       region.y() * backdrop.height() / parent.height(),
                                       region.width() * backdrop.width() / parent.width(),
                                       region.height() * backdrop.height() / parent.height())
                painter.drawImage(QRectF(source.rect()), backdrop, raster_region)
            else:
                painter.scale(.5, .5)
                # Omitting DrawChildren prevents recursive popup capture.
                parent.render(painter, QPoint(), QRegion(region), QWidget.RenderFlag.DrawWindowBackground)
        bitmap = Image.frombytes("RGBA", (source.width(), source.height()), bytes(source.constBits()), "raw", "RGBA", source.bytesPerLine())
        bitmap = ImageEnhance.Color(bitmap).enhance(.35).filter(ImageFilter.GaussianBlur(9))
        light = relative_luminance(self._background) > .45
        veil = (248, 248, 250, 188) if light else (43, 43, 46, 176)
        bitmap = Image.alpha_composite(bitmap, Image.new("RGBA", bitmap.size, veil))
        blurred = QImage(bitmap.tobytes(), bitmap.width, bitmap.height, bitmap.width * 4, QImage.Format.Format_RGBA8888).copy()
        offset = geometry.topLeft() - region.topLeft()
        self._material = blurred.copy(round(offset.x() / 2), round(offset.y() / 2), max(1, round(geometry.width() / 2)), max(1, round(geometry.height() / 2)))
        self._material_key, self._material_at = key, now
        self.update()

    @property
    def render_pose(self):
        """Read-only geometry for native previews and hit-test verification."""
        source = self._source_rect.translated(-self.x(), -self.y())
        full = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        row = self._rows.get(self._device_order[0]) if self._device_order else None
        if row is not None:
            first = QRectF(row.mapTo(self, QPoint()), row.size())
        else:
            first = QRectF(self._inset, self._inset,
                           max(1, self.width() - self._inset * 2), self._row_height)
        return output_panel_pose(self._reveal, source, full, first)

    def _pose_path(self):
        pose = self.render_pose
        path = QPainterPath()
        path.addRoundedRect(pose.shell_rect, pose.radius, pose.radius)
        return path

    def contains_visible_point(self, point):
        return self._pose_path().contains(QPointF(point))

    def _paint_morph_row(self, painter, row, target, opacity=1.0):
        if target.width() <= 0 or target.height() <= 0:
            return
        painter.save()
        painter.setOpacity(max(0.0, min(1.0, opacity)))
        painter.translate(target.topLeft())
        sx, sy = target.width() / max(1, row.width()), target.height() / max(1, row.height())
        painter.scale(sx, sy)
        row.paint_contents(painter)
        painter.restore()

    def _paint_unfolding_rows(self, painter, pose):
        rows = [self._rows[identity] for identity in self._device_order]
        if not rows:
            return
        # Paint back to front: compressed members remain behind the first
        # capsule, then occupy their ordinary scroll-layout positions.
        first = pose.first_rect
        for index in range(len(rows) - 1, 0, -1):
            row = rows[index]
            normal = QPointF(row.mapTo(self, QPoint()))
            compressed = first.top() + min(index, 3) * first.height() * .13
            y = compressed + (normal.y() - compressed) * pose.row_unfold
            target = QRectF(first.left(), y, first.width(), first.height())
            if not target.intersects(pose.shell_rect):
                continue
            tail = 1.0 if index <= 3 else max(0.0, min(1.0, (pose.row_unfold - .18) / .82))
            self._paint_morph_row(painter, row, target,
                                 tail * pose.horizontal * (.5 + .5 * pose.row_unfold))
        # Before enough width exists for a readable row, its source face and
        # glyph travel together. They hand off to the first device capsule.
        row_blend = max(0.0, min(1.0, (pose.horizontal - .35) / .65))
        self._paint_morph_row(painter, rows[0], first, row_blend)
        if row_blend < 1:
            painter.save()
            painter.setOpacity(1 - row_blend)
            paint_liquid_glass(painter, first, radius=first.height() / 2,
                              base_color=self._selected_fill, light=True, active=True)
            diameter = min(first.height(), self._source_rect.height())
            target = QRectF(first.left() + (first.width() - diameter) * (1 - pose.horizontal) / 2,
                            first.center().y() - diameter / 2, diameter, diameter)
            paint_audio_output_status(painter, target, self._source_kinds, QColor(40, 40, 44))
            painter.restore()

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pose = self.render_pose
            shape = self._pose_path()
            painter.setClipPath(shape)
            painter.fillRect(pose.shell_rect, self._background)
            if not self._material.isNull():
                painter.drawImage(QRectF(self.rect()), self._material)
            if self._scroll_opacity.opacity() < 1:
                self._paint_unfolding_rows(painter, pose)
            painter.setClipping(False)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, 65), 1))
            painter.drawPath(shape)

    def _fit_to_content(self):
        visible = [self.rows_layout.itemAt(i) for i in range(self.rows_layout.count())
                   if self.rows_layout.itemAt(i).widget() is not None and not self.rows_layout.itemAt(i).widget().isHidden()]
        margins = self.rows_layout.contentsMargins()
        height = (sum(item.sizeHint().height() for item in visible)
                  + max(0, len(visible) - 1) * self.rows_layout.spacing()
                  + margins.top() + margins.bottom())
        self.rows_widget.setMinimumHeight(height)
        status = (min(self.status_label.heightForWidth(max(1, self.width() - self._inset * 2)),
                      self.status_label.maximumHeight()) + 9 if not self.status_label.isHidden() else 0)
        self.resize(self.width(), min(self._max_height, self._inset * 2 + status + height))
        parent = self.parentWidget()
        if parent is not None:
            # A source control can legitimately sit less than twelve pixels
            # below the page edge. Its complete circle must remain inside
            # the child widget throughout the first frame of the morph.
            self._anchor.setY(max(0, min(self._anchor.y(), parent.height() - self.height() - 12)))
        self.move(self._anchor)
        self._rows_base_pos = QPoint(0, -self.scroll.verticalScrollBar().value())

    def _sync_tab_order(self):
        controls = []
        for device_id in self._device_order:
            row = self._rows[device_id]
            controls.append(row)
            if not row.solo_button.isHidden():
                controls.append(row.solo_button)
            if not row.volume_slider.isHidden():
                controls.append(row.volume_slider)
            elif not row.join_button.isHidden():
                controls.append(row.join_button)
        controls.append(self.stop_button)
        for previous, current in zip(controls, controls[1:]):
            QWidget.setTabOrder(previous, current)

    def open_at(self, anchor: QPoint, *, source_rect: QRectF | None = None):
        was_visible = self.isVisible()
        parent = self.parentWidget()
        # The source occupies the menu's first control slot, rather than
        # remaining a separate button above a downward-floating popup.
        self._source_rect = QRectF(source_rect) if source_rect is not None else QRectF(
            anchor.x() - 42, max(12, anchor.y() - 42), 42, 42)
        # Logical Qt sizes already follow display DPI. Match the source
        # control's proportion without double-scaling by devicePixelRatio.
        desired_width = max(280, round(self._source_rect.height() * 6.2))
        width = min(desired_width, max(80, parent.width() - 24)) if parent else desired_width
        self._row_height = max(48, round(self._source_rect.height() * 1.05))
        right, top = self._source_rect.right(), self._source_rect.top()
        x = max(12, min(right - width, parent.width() - width - 12)) if parent else right - width
        y = max(0, min(top, parent.height() - 80)) if parent else top
        # QWidget bounds are integral; contain the exact fractional source
        # circle on both axes instead of clipping its right edge after rounding.
        self._anchor = QPoint(math.floor(x), math.floor(y))
        width = max(width, math.ceil(right) - self._anchor.x())
        self._max_height = min(460, max(80, parent.height() - self._anchor.y() - 12)) if parent else 460
        self.resize(width, self._max_height)
        if not was_visible:
            self._appearance_elapsed = 0.0
            self._reveal = self._reveal_velocity = 0.0
            self._edge_offset = self._edge_velocity = 0.0
            self._clear_row_hover()
        self._reveal_target = 1.0
        self._clock.reset(time.monotonic())
        self.move(self._anchor)
        self.sync_snapshot()
        self._capture_material(force=True)
        self._apply_reveal_pose()
        self.show()
        self.raise_()
        self.layout().activate()
        self.rows_layout.activate()
        if self._snapshot:
            confirmed, pending = self._route_members(self._snapshot)
            selected = confirmed | pending if self._snapshot.state == "reconfiguring" else pending or confirmed
            active_row = next((self._rows[identity] for identity in self._device_order
                               if identity in selected), None)
            if active_row:
                self.scroll.ensureWidgetVisible(active_row, 0, 8)
        self.setFocus(Qt.FocusReason.PopupFocusReason)
        self._animation_timer.start()
        self._poll_timer.start()
        self._refresh()

    def dismiss(self, *, immediate=False):
        was_visible = self.isVisible()
        if not was_visible:
            return
        if immediate or self._reveal < .001:
            self.hide()
            self.dismissed.emit()
            return
        self._reveal_target = 0.0
        self._clear_row_hover()
        self._poll_timer.stop()
        self._start_animation()

    def _apply_reveal_pose(self):
        self.move(self._anchor)
        self.layout().activate()
        self.rows_layout.activate()
        self._opacity.setOpacity(1.0)
        native = self._native_content_ready()
        newly_native = native and self._scroll_opacity.isEnabled()
        self._scroll_opacity.setOpacity(1.0 if native else 0.0)
        self._status_opacity.setOpacity(1.0 if native else 0.0)
        self._scroll_opacity.setEnabled(not native)
        self._status_opacity.setEnabled(not native)
        if native:
            self.clearMask()
        else:
            # A conservative rectangular input region leaves the painted
            # curve's fractional alpha intact. Binary curved masks alias.
            self.setMask(QRegion(self.render_pose.shell_rect.adjusted(-1, -1, 1, 1).toAlignedRect()))
        if newly_native and self.isVisible():
            # Unfolding can place the first row beneath a stationary pointer.
            # Arm its normal dwell once the native hit surface becomes ready.
            self._update_volume_hover(QPointF(QCursor.pos()), time.monotonic())
        self.update()

    def hideEvent(self, event):
        self._animation_timer.stop()
        self._poll_timer.stop()
        self._reveal_target = self._reveal = self._reveal_velocity = 0.0
        self._clear_row_hover()
        for row in self._rows.values():
            row.set_details_visible(False, immediate=True)
            slider = row.volume_slider
            blocked = slider.blockSignals(True)
            slider.setSliderDown(False)
            slider.blockSignals(blocked)
            row.setDown(False)
            row._pointer_action = ""
        self._edge_offset = self._edge_velocity = 0.0
        self.clearMask()
        super().hideEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss()
            event.accept()
        elif event.key() == Qt.Key.Key_F5:
            self._refresh()
            event.accept()
        elif event.key() == Qt.Key.Key_N and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if self.add_button.isEnabled():
                self._add_homepod()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _refresh(self):
        self._action_error = ""
        try:
            self.provider.refresh_audio_outputs()
        except Exception:
            self._action_error = "暂时无法刷新设备，请稍后重试。"
        self.sync_snapshot()

    def _select(self, device_id: str):
        self._action_error = ""
        try:
            if not self.provider.select_audio_output(device_id):
                self._action_error = "无法切换到此设备，请刷新后重试。"
        except Exception:
            self._action_error = "无法切换到此设备，请刷新后重试。"
        self.sync_snapshot()

    def _join(self, device_id: str):
        self._action_error = ""
        try:
            join = getattr(self.provider, "toggle_audio_output", None)
            if not callable(join) or not join(device_id):
                self._action_error = "暂时无法将此设备加入同步播放。"
        except Exception:
            self._action_error = "暂时无法将此设备加入同步播放。"
        self.sync_snapshot()

    def _toggle_mute(self, device_id: str):
        self._action_error = ""
        try:
            accepted = self.provider.toggle_audio_output_mute(device_id)
        except Exception:
            accepted = False
        if not accepted:
            self._action_error = "暂时无法切换此设备静音。"
        self.sync_snapshot()

    def _volume_changed(self, device_id: str, volume: float):
        self._action_error = ""
        try:
            accepted = self.provider.set_audio_output_volume(device_id, volume)
        except Exception:
            accepted = False
        if not accepted:
            self._action_error = "暂时无法调整此设备音量。"
            row = self._rows.get(device_id)
            if row:
                row._requested_volume = None
                row.volume_slider.setSliderDown(False)
        self.sync_snapshot()

    def _stop(self):
        self._action_error = ""
        try:
            self.provider.stop_audio_cast()
        except Exception:
            self._action_error = "暂时无法停止投放，请稍后重试。"
        self.sync_snapshot()

    def _add_homepod(self):
        host, accepted = QInputDialog.getText(self, "添加 AirPlay 设备", "同一局域网中的 AirPlay 设备 IP 地址")
        if not accepted:
            return
        self._action_error = ""
        try:
            if not self.provider.add_homepod(host.strip()):
                self._action_error = "请填写有效的 AirPlay 设备 IP 地址。"
        except Exception:
            self._action_error = "暂时无法添加 AirPlay 设备，请稍后重试。"
        self.sync_snapshot()

    @staticmethod
    def _route_members(snapshot):
        pending = set(snapshot.pending_ids) or ({snapshot.pending_id} if snapshot.pending_id else set())
        # Replacement targets supersede the old group. During a live join,
        # reconfiguring explicitly retains confirmed playback members.
        confirmed = set(snapshot.member_ids) if not pending or snapshot.state == "reconfiguring" else set()
        # Explicit members and pending targets own selection. In particular,
        # a group's capture/default endpoint is not an implicit extra member.
        if not confirmed and not pending and snapshot.active_id:
            confirmed = {snapshot.active_id}
        return confirmed, pending

    def _maintain_visible_state(self, snapshot):
        if self.isVisible() and self._appearance_elapsed >= self.APPEAR_SECONDS:
            self._capture_material()
        animating = (snapshot.pending_id or snapshot.pending_ids or any(
            abs(row.progress - (1 if row.state == "active" else 0)) > .01 or row.elapsed < .5
            or row.details_animating
            for row in self._rows.values()))
        if self.isVisible() and animating and not self._animation_timer.isActive():
            self._clock.reset(time.monotonic())
            self._animation_timer.start()

    def sync_snapshot(self):
        """Read provider state; never discover or perform device I/O in the UI."""
        self._sync_surface_style()
        try:
            snapshot = self.provider.get_audio_route_snapshot()
        except Exception:
            self._ui_key = None
            self._clear_row_hover()
            self.status_label.setText("音频设备暂时不可用。")
            self.status_label.setToolTip(self.status_label.text())
            self.status_label.setAccessibleDescription(self.status_label.text())
            self.status_label.show()
            self.stop_button.setEnabled(False)
            for row in self._rows.values():
                row.setEnabled(False)
            self._fit_to_content()
            return
        self._snapshot = snapshot
        confirmed, pending_ids = self._route_members(snapshot)
        self._source_kinds = tuple(device.kind for device in snapshot.devices if device.id in confirmed)
        desired = confirmed | pending_ids if snapshot.state == "reconfiguring" else pending_ids or confirmed
        # Preserve provider order within both buckets; clicks alone cannot
        # change selection, and the capture/default endpoint is not a member.
        published_order = tuple(device.id for device in snapshot.devices)
        order = tuple(identity for identity in published_order if identity in desired) + tuple(
            identity for identity in published_order if identity not in desired)
        if any(row.volume_slider.isSliderDown() for row in self._rows.values()):
            # Moving a row underneath a held pointer changes the scrubber's
            # coordinate system. Retain existing positions until release,
            # while still accepting device additions/removals and new state.
            retained = tuple(identity for identity in self._device_order if identity in published_order)
            order = retained + tuple(identity for identity in order if identity not in retained)
        ui_key = (snapshot, self._action_error, self._style_key, self.width(), self._max_height)
        if ui_key == self._ui_key and order == self._device_order:
            # Even an unchanged provider snapshot must retire a volume request
            # that did not receive readback; no route action is issued here.
            for row in self._rows.values():
                if row._requested_volume is not None:
                    row.set_device(row.device)
            self._maintain_visible_state(snapshot)
            return
        self._ui_key = ui_key
        grouped = (snapshot.capture_scope == "music_process_tree" if snapshot.capture_scope else
                   len(desired) > 1)
        casting = any(d.transport == "airplay" and d.id in desired for d in snapshot.devices)
        self.title_label.setText(f"同步播放 · {len(desired)} 个设备" if grouped else
                                 "单设备投放" if casting else "音频输出")
        owned = snapshot.capture_scope == "owned-player"
        if owned:
            scope = "程序内播放 · 当前输出设备" if desired else "程序内播放 · 系统默认输出"
        elif pending_ids:
            scope = ("正在切换到同步播放 · 仅当前音乐软件" if grouped else
                     "正在切换到单设备投放 · 电脑全部声音" if casting else
                     "正在切换输出 · 电脑全部声音")
        else:
            scope = ("仅当前音乐软件 · 自动歌词补偿" if grouped else
                     "电脑全部声音 · 自动歌词补偿" if casting else
                     "电脑全部声音 · 多选时仅当前音乐软件")
        self.scope_label.setText(scope)
        description = scope + "。F5 刷新设备，Ctrl+N 添加 AirPlay 设备，Esc 关闭。"
        if owned:
            description = snapshot.status_message or scope
        self.setAccessibleDescription(description)
        self.setToolTip(description if owned else scope)
        if order != self._device_order:
            self._clear_row_hover()
            for row in self._rows.values():
                self.rows_layout.removeWidget(row)
            for device_id in tuple(self._rows):
                if device_id not in order:
                    departed = self._rows.pop(device_id)
                    # Deferred QObject destruction must not leave its native
                    # pixels or hover overlay visible in an empty/rebuilt list.
                    departed.hide()
                    departed.deleteLater()
            devices = {device.id: device for device in snapshot.devices}
            for index, device_id in enumerate(order):
                device = devices[device_id]
                row = self._rows.get(device_id)
                if row is None:
                    row = _OutputRow(device, self.rows_widget)
                    row.solo_requested.connect(self._select)
                    row.join_requested.connect(self._join)
                    row.mute_requested.connect(self._toggle_mute)
                    row.details_animation_requested.connect(self._start_animation)
                    row.volume_requested.connect(self._volume_changed)
                    row.volume_slider.sliderReleased.connect(self.sync_snapshot)
                    self._rows[device.id] = row
                    self._install_pointer_filters(row)
                self.rows_layout.insertWidget(index, row)
                # Newly inserted widgets on an already visible chooser are
                # otherwise shown by a later layout event, after height fitting.
                row.show()
            self._device_order = order
        for device in snapshot.devices:
            row = self._rows[device.id]
            pending = device.id in pending_ids
            selected = device.id in confirmed
            row.state = "pending" if pending else "active" if selected else "available"
            row.grouped = grouped
            row.setChecked(row.state == "active")
            row.delay_text = f"+{snapshot.cast_latency_seconds:.2f}s" if row.state == "active" and device.transport == "airplay" else ""
            row.set_device(device)
            self._style_row(row)
            action = "点击图标独占播放，点击名称切换静音，拖动下方滑块调整音量，滚轮滚动列表" if selected else "点击名称独占播放，点击加号加入同步播放，滚轮滚动列表"
            row.setAccessibleDescription(row.subtitle + "，" + scope + "，" + action + (f"，自动歌词补偿 {snapshot.cast_latency_seconds:.2f} 秒" if row.delay_text else ""))
            row.setToolTip(f"{device.name} · {scope}" + (" · 音量暂不可用" if device.volume is None else ""))
            if row.delay_text:
                row.setToolTip(f"{device.name} · {scope} · 自动歌词补偿 {snapshot.cast_latency_seconds:.2f} 秒（根据投放缓冲估计）")
            row.update()
        self.empty_label.setText("使用系统默认输出" if owned else "暂无可用输出设备")
        self.empty_label.setVisible(not order)
        message = self._action_error or snapshot.status_message
        if (not self._action_error and snapshot.state not in {"error", "failed", "connecting", "reconfiguring"}
                and message.startswith("选择程序内音乐的播放设备")):
            message = ""
        if owned and not self._action_error and snapshot.state == "unavailable" and message == "程序内播放使用系统默认输出；暂不支持同步播放到多个设备":
            message = ""
        if not self._action_error and ((snapshot.state == "casting" and message.startswith(("正在投放到", "正在同步播放到")))
                                      or message.startswith(("已调整 ", "已选择 "))):
            message = ""
        self.status_label.setText(message)
        self.status_label.setToolTip(message)
        self.status_label.setAccessibleDescription(message)
        self.status_label.setVisible(bool(message))
        error = bool(self._action_error or snapshot.state in {"error", "failed"})
        error_color = "#a22d2d" if relative_luminance(self._background) > .45 else "#ffb9b3"
        self.status_label.setStyleSheet(f"color: {error_color if error else self._muted.name()};")
        self.stop_button.setText("停止同步播放" if grouped else "停止投放")
        # A music-only group owns its capture/render lifecycle even when all
        # members are Windows outputs. It must remain explicitly stoppable.
        stoppable = casting or grouped
        self.stop_button.setEnabled(stoppable)
        self.stop_button.setVisible(stoppable)
        self._fit_to_content()
        self._sync_tab_order()
        self._maintain_visible_state(snapshot)

    def _advance_animation(self, now: float | None = None):
        elapsed = self._clock.advance(time.monotonic() if now is None else now)
        self._appearance_elapsed += elapsed
        # Small fixed steps keep the damped spring stable across slow frames.
        steps = max(1, math.ceil(elapsed / .008))
        step = elapsed / steps
        for _ in range(steps):
            acceleration = (self._reveal_target - self._reveal) * 400.0 - self._reveal_velocity * 32.0
            self._reveal_velocity += acceleration * step
            self._reveal += self._reveal_velocity * step
            self._edge_velocity += (-self._edge_offset * 240.0 - self._edge_velocity * 25.0) * step
            self._edge_offset += self._edge_velocity * step
        settled = abs(self._reveal_target - self._reveal) < .001 and abs(self._reveal_velocity) < .02
        if settled:
            self._reveal = self._reveal_target
            self._reveal_velocity = 0.0
            if self._reveal_target == 0.0:
                self.dismiss(immediate=True)
                return
        self._apply_reveal_pose()
        self._rows_base_pos = QPoint(0, -self.scroll.verticalScrollBar().value())
        self._apply_edge_offset()
        for row in self._rows.values():
            row.advance(elapsed)
        pending = bool(self._snapshot and (self._snapshot.pending_id or self._snapshot.pending_ids))
        rows_animating = any(row.elapsed < .5 or abs(row.progress - (1 if row.state == "active" else 0)) > .01
                             or row.details_animating for row in self._rows.values())
        if settled and not pending and not rows_animating and abs(self._edge_offset) < .1 and abs(self._edge_velocity) < .2:
            self._edge_offset = self._edge_velocity = 0.0
            self._apply_edge_offset()
            self._animation_timer.stop()
