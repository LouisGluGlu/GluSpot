"""Music-surface output selector; all device work stays behind the provider."""
from __future__ import annotations

import math
import time

from PIL import Image, ImageEnhance, ImageFilter
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import (
    QAbstractButton, QGraphicsOpacityEffect, QHBoxLayout, QInputDialog, QLabel,
    QPushButton, QScrollArea, QSizePolicy, QStyle, QVBoxLayout, QWidget,
)

from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from local.audio_output_icons import paint_audio_output_icon  # Shared with connection notices.
from local.music_lyric_motion import MotionSimulationClock
from local.music_surface_palette import relative_luminance
from local.no_wheel_widgets import NoWheelSlider


class _VolumeSlider(NoWheelSlider):
    """Slim absolute-position scrubber; scrolling never changes volume."""

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setRange(0, 100)
        self.setSingleStep(2)
        self.setPageStep(10)
        self.fill, self.track = QColor(170, 170, 175), QColor(255, 255, 255, 35)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _set_from_pointer(self, x):
        self.setValue(QStyle.sliderValueFromPosition(0, 100, round(x - 6), max(1, self.width() - 12),
                      self.layoutDirection() == Qt.LayoutDirection.RightToLeft))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.setSliderDown(True)
            self._set_from_pointer(event.position().x())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self._set_from_pointer(event.position().x())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self._set_from_pointer(event.position().x())
            self.setSliderDown(False)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            groove = QRectF(6, self.height() / 2 - 2, max(1, self.width() - 12), 4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.track)
            painter.drawRoundedRect(groove, 2, 2)
            fraction = self.value() / 100.0
            reverse = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
            portion = QRectF(groove.right() - groove.width() * fraction if reverse else groove.left(),
                             groove.top(), groove.width() * fraction, groove.height())
            painter.setBrush(self.fill)
            painter.drawRoundedRect(portion, 2, 2)
            if self.hasFocus() or self.underMouse() or self.isSliderDown():
                painter.drawEllipse(QPointF(portion.left() if reverse else portion.right(), groove.center().y()), 4, 4)


class _OutputRow(QAbstractButton):
    """Accessible route button with a separate actual-device volume control."""

    volume_requested = Signal(str, float)

    def __init__(self, device: AudioRouteDevice, parent=None):
        super().__init__(parent)
        self.device, self.state = device, "available"
        self.progress = self.elapsed = 0.0
        self.ink, self.muted = QColor(245, 245, 245), QColor(180, 180, 185)
        self.accent = QColor(self.ink)
        self.hover_fill, self.selected_fill = QColor(255, 255, 255, 14), QColor(245, 245, 247)
        self.label_font = QFont("Segoe UI", 10)
        self.delay_text = ""
        self.grouped = False
        self._requested_volume: float | None = None
        self._request_until = 0.0
        self.volume_slider = _VolumeSlider(self)
        self.volume_slider.valueChanged.connect(self._volume_changed)
        self.setFixedHeight(68)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCheckable(True)
        self.set_device(device)

    def nextCheckState(self):
        # Selection belongs to confirmed provider snapshots, including for
        # keyboard and accessibility activation of this checkable button.
        pass

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
        self.setAccessibleName(str(device.name))
        self.setToolTip(str(device.name))
        known = device.volume is not None and math.isfinite(device.volume)
        self.volume_slider.setVisible(known)
        self.volume_slider.setEnabled(known and usable)
        muted = "（已静音）" if device.volume_muted else ""
        self.volume_slider.setAccessibleName(f"{device.name}的音量{muted}")
        self.volume_slider.setToolTip(f"设备音量{muted}")
        if not known:
            self._requested_volume = None
        elif not self.volume_slider.isSliderDown():
            if (self._requested_volume is not None and
                    (abs(device.volume - self._requested_volume) <= .011 or time.monotonic() >= self._request_until)):
                self._requested_volume = None
            if self._requested_volume is None:
                self.volume_slider.blockSignals(True)
                self.volume_slider.setValue(round(max(0.0, min(1.0, device.volume)) * 100))
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
        reserve = 68 if self.delay_text else 12
        self.volume_slider.setGeometry(58, 34, max(1, self.width() - 58 - reserve), 25)

    def resizeEvent(self, event):
        self._layout_volume()
        super().resizeEvent(event)

    def advance(self, elapsed: float):
        self.elapsed += elapsed
        self.progress += ((1.0 if self.state == "active" else 0.0) - self.progress) * (1 - math.exp(-elapsed * 15))
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
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            bounds = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            fill = QColor(self.selected_fill)
            fill.setAlpha(round(255 * self.progress))
            painter.setPen(QPen(self.accent, 1) if self.hasFocus() else Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(bounds, 30, 30)
            if self.progress < .1 and (self.underMouse() or self.hasFocus()):
                painter.setBrush(self.hover_fill)
                painter.drawRoundedRect(bounds, 30, 30)
            dark = QColor(40, 40, 44)
            ink = QColor(*(round(a * (1 - self.progress) + b * self.progress)
                           for a, b in zip(self.muted.getRgb()[:3], dark.getRgb()[:3])))
            arrival = 1 - math.exp(-self.elapsed * 12)
            painter.save()
            icon = QRectF(15, 18, 30, 32)
            painter.translate(icon.center())
            painter.scale(.84 + .16 * arrival, .84 + .16 * arrival)
            painter.translate(-icon.center())
            paint_audio_output_icon(painter, icon, self.device.kind, ink)
            painter.restore()
            font = QFont(self.label_font)
            font.setPixelSize(14)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(ink)
            name_rect = QRectF(63, 9, max(1, self.width() - 94), 25)
            name = QFontMetricsF(font).elidedText(str(self.device.name), Qt.TextElideMode.ElideRight, name_rect.width())
            painter.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter, name)
            font.setPixelSize(10)
            font.setWeight(QFont.Weight.Normal)
            painter.setFont(font)
            if self.volume_slider.isHidden():
                detail = "音量暂不可用" if self.state == "active" else self.subtitle
                painter.drawText(QRectF(63, 34, name_rect.width(), 24), Qt.AlignmentFlag.AlignVCenter, detail)
            if self.delay_text:
                painter.drawText(QRectF(self.width() - 68, 34, 55, 25), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self.delay_text)
            if self.state == "pending":
                painter.setPen(QPen(ink, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawArc(QRectF(self.width() - 25, 15, 12, 12), int(-self.elapsed * 280 * 16), 250 * 16)
            elif self.state == "active":
                # Retain a visible membership mark on pale backdrops, where
                # the selected capsule alone offers little contrast.
                center = QPointF(self.width() - 19, 21)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(ink)
                painter.drawEllipse(center, 7, 7)
                painter.setPen(QPen(self.selected_fill, 1.5, Qt.PenStyle.SolidLine,
                                    Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                check = QPainterPath(center + QPointF(-3, 0))
                check.lineTo(center + QPointF(-.7, 2.3))
                check.lineTo(center + QPointF(3.3, -2.2))
                painter.drawPath(check)


class AudioOutputPanel(QWidget):
    """Cached frosted popup that queues actions through published route state."""

    dismissed = Signal()
    APPEAR_SECONDS = .32
    MATERIAL_INTERVAL = 1.0

    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self.provider = provider
        self._snapshot: AudioRouteSnapshot | None = None
        self._rows: dict[str, _OutputRow] = {}
        self._device_order = ()
        self._anchor = QPoint()
        self._appearance_elapsed = 0.0
        self._clock = MotionSimulationClock()
        self._action_error = ""
        self._ui_key = None
        self._style_key = None
        self._max_height = 460
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
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(9)
        header = QHBoxLayout()
        header.setSpacing(4)
        self.title_label = QLabel("音频输出")
        header.addWidget(self.title_label)
        header.addStretch()
        self.refresh_button = QPushButton("↻")
        self.refresh_button.setAccessibleName("刷新音频设备")
        self.refresh_button.setToolTip("刷新设备")
        self.refresh_button.clicked.connect(self._refresh)
        self.add_button = QPushButton("＋")
        self.add_button.setAccessibleName("通过 IP 地址添加 AirPlay 设备")
        self.add_button.setToolTip("通过 IP 地址添加 AirPlay 设备")
        self.add_button.setEnabled(callable(getattr(provider, "add_homepod", None)))
        self.add_button.clicked.connect(self._add_homepod)
        self.close_button = QPushButton("×")
        self.close_button.setAccessibleName("关闭设备面板")
        self.close_button.clicked.connect(self.dismiss)
        for button in (self.refresh_button, self.add_button, self.close_button):
            button.setFixedSize(28, 28)
            header.addWidget(button)
        layout.addLayout(header)
        self.scope_label = QLabel()
        self.scope_label.setWordWrap(True)
        layout.addWidget(self.scope_label)
        self.status_label = QLabel("正在读取设备…")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rows_widget = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_widget)
        self.rows_layout.setContentsMargins(0, 0, 2, 0)
        self.rows_layout.setSpacing(5)
        self.scroll.setWidget(self.rows_widget)
        layout.addWidget(self.scroll, 1)
        self.empty_label = QLabel("暂无可用输出设备")
        self.empty_label.setMargin(12)
        self.rows_layout.addWidget(self.empty_label)
        self.stop_button = QPushButton("停止投放")
        self.stop_button.setFixedHeight(30)
        self.stop_button.clicked.connect(self._stop)
        self.rows_layout.addWidget(self.stop_button)
        self.rows_layout.addStretch(1)
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._animation_timer = QTimer(self)
        self._animation_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._animation_timer.setInterval(16)
        self._animation_timer.timeout.connect(self._advance_animation)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(350)
        self._poll_timer.timeout.connect(self.sync_snapshot)
        self._sync_surface_style()
        self.hide()

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
        for row in self._rows.values():
            self._style_row(row)

    def _style_row(self, row):
        row.ink, row.muted, row.accent = self._ink, self._muted, self._accent
        row.hover_fill, row.selected_fill, row.label_font = self._hover_fill, self._selected_fill, self._label_font
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

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            bounds = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
            shape = QPainterPath()
            shape.addRoundedRect(bounds, 30, 30)
            painter.setClipPath(shape)
            painter.fillRect(bounds, self._background)
            if not self._material.isNull():
                painter.drawImage(bounds, self._material)
            painter.setClipping(False)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, 35), 1))
            painter.drawPath(shape)

    def _fit_to_content(self):
        visible = [self.rows_layout.itemAt(i) for i in range(self.rows_layout.count())
                   if self.rows_layout.itemAt(i).widget() is not None and not self.rows_layout.itemAt(i).widget().isHidden()]
        height = sum(item.sizeHint().height() for item in visible) + max(0, len(visible) - 1) * self.rows_layout.spacing()
        status = self.status_label.sizeHint().height() + 9 if not self.status_label.isHidden() else 0
        scope = self.scope_label.heightForWidth(max(1, self.width() - 30)) + 9
        self.resize(self.width(), min(self._max_height, 67 + scope + status + height))
        parent = self.parentWidget()
        if parent is not None:
            self._anchor.setY(max(12, min(self._anchor.y(), parent.height() - self.height() - 12)))
        if self._appearance_elapsed >= self.APPEAR_SECONDS:
            self.move(self._anchor)

    def _sync_tab_order(self):
        controls = [self.refresh_button, self.add_button, self.close_button]
        for device_id in self._device_order:
            row = self._rows[device_id]
            controls.append(row)
            if not row.volume_slider.isHidden():
                controls.append(row.volume_slider)
        controls.append(self.stop_button)
        for previous, current in zip(controls, controls[1:]):
            QWidget.setTabOrder(previous, current)

    def open_at(self, anchor: QPoint):
        self._anchor = QPoint(anchor)
        parent = self.parentWidget()
        width = min(344, max(260, parent.width() - 24)) if parent else 344
        self._max_height = min(460, max(220, parent.height() - anchor.y() - 16)) if parent else 460
        self.resize(width, self._max_height)
        self._anchor.setX(max(12, min(anchor.x() - width, parent.width() - width - 12)) if parent else anchor.x())
        self._anchor.setY(max(12, anchor.y()))
        self._appearance_elapsed = 0.0
        self._clock.reset(time.monotonic())
        self._opacity.setOpacity(0)
        self.move(self._anchor + QPoint(0, -10))
        self.sync_snapshot()
        self._capture_material(force=True)
        self.show()
        self.raise_()
        self.layout().activate()
        self.rows_layout.activate()
        if self._snapshot:
            confirmed, pending = self._route_members(self._snapshot)
            selected = pending or confirmed
            active_row = next((self._rows[identity] for identity in self._device_order
                               if identity in selected), None)
            if active_row:
                self.scroll.ensureWidgetVisible(active_row, 0, 8)
        self.setFocus(Qt.FocusReason.PopupFocusReason)
        self._animation_timer.start()
        self._poll_timer.start()
        self._refresh()

    def dismiss(self):
        was_visible = self.isVisible()
        self.hide()
        if was_visible:
            self.dismissed.emit()

    def hideEvent(self, event):
        self._animation_timer.stop()
        self._poll_timer.stop()
        super().hideEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss()
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
            select = getattr(self.provider, "toggle_audio_output", None)
            if not callable(select):
                select = self.provider.select_audio_output
            if not select(device_id):
                self._action_error = "无法切换到此设备，请刷新后重试。"
        except Exception:
            self._action_error = "无法切换到此设备，请刷新后重试。"
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
        # A published replacement target supersedes the previous group. Old
        # member evidence must not remain selected during that transition.
        confirmed = set(snapshot.member_ids) if not pending else set()
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
            self.status_label.setText("音频设备暂时不可用。")
            self.status_label.show()
            self.stop_button.setEnabled(False)
            for row in self._rows.values():
                row.setEnabled(False)
            self._fit_to_content()
            return
        self._snapshot = snapshot
        confirmed, pending_ids = self._route_members(snapshot)
        desired = pending_ids or confirmed
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
        if pending_ids:
            scope = ("正在切换到同步播放 · 仅当前音乐软件" if grouped else
                     "正在切换到单设备投放 · 电脑全部声音" if casting else
                     "正在切换输出 · 电脑全部声音")
        else:
            scope = ("仅当前音乐软件 · 自动歌词补偿" if grouped else
                     "电脑全部声音 · 自动歌词补偿" if casting else
                     "电脑全部声音 · 多选时仅当前音乐软件")
        self.scope_label.setText(scope)
        if order != self._device_order:
            for row in self._rows.values():
                self.rows_layout.removeWidget(row)
            for device_id in tuple(self._rows):
                if device_id not in order:
                    self._rows.pop(device_id).deleteLater()
            devices = {device.id: device for device in snapshot.devices}
            for index, device_id in enumerate(order):
                device = devices[device_id]
                row = self._rows.get(device_id)
                if row is None:
                    row = _OutputRow(device, self.rows_widget)
                    row.clicked.connect(lambda _checked=False, device_id=device.id: self._select(device_id))
                    row.volume_requested.connect(self._volume_changed)
                    row.volume_slider.sliderReleased.connect(self.sync_snapshot)
                    self._rows[device.id] = row
                self.rows_layout.insertWidget(index, row)
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
            row.setAccessibleDescription(row.subtitle + (f"，自动歌词补偿 {snapshot.cast_latency_seconds:.2f} 秒" if row.delay_text else ""))
            if row.delay_text:
                row.setToolTip(f"{device.name} · 自动歌词补偿 {snapshot.cast_latency_seconds:.2f} 秒（根据投放缓冲估计）")
            row.update()
        self.empty_label.setVisible(not order)
        message = self._action_error or snapshot.status_message
        if not self._action_error and ((snapshot.state == "casting" and message.startswith(("正在投放到", "正在同步播放到")))
                                      or message.startswith(("已调整 ", "已选择 "))):
            message = ""
        self.status_label.setText(message)
        self.status_label.setVisible(bool(message))
        error = bool(self._action_error or snapshot.state in {"error", "failed"})
        error_color = "#a22d2d" if relative_luminance(self._background) > .45 else "#ffb9b3"
        self.status_label.setStyleSheet(f"color: {error_color if error else self._muted.name()};")
        self.stop_button.setText("停止同步播放" if grouped else "停止投放")
        self.stop_button.setEnabled(casting)
        self.stop_button.setVisible(casting)
        self._fit_to_content()
        self._sync_tab_order()
        self._maintain_visible_state(snapshot)

    def _advance_animation(self, now: float | None = None):
        elapsed = self._clock.advance(time.monotonic() if now is None else now)
        self._appearance_elapsed += elapsed
        phase = min(1.0, self._appearance_elapsed / self.APPEAR_SECONDS)
        settle = (1 - (1 + 3.2 * phase) * math.exp(-3.2 * phase)) / (1 - 4.2 * math.exp(-3.2))
        self._opacity.setOpacity(max(0.0, min(1.0, settle)))
        self.move(self._anchor + QPoint(0, round(-10 * (1 - settle))))
        for row in self._rows.values():
            row.advance(elapsed)
        pending = bool(self._snapshot and (self._snapshot.pending_id or self._snapshot.pending_ids))
        rows_animating = any(row.elapsed < .5 or abs(row.progress - (1 if row.state == "active" else 0)) > .01 for row in self._rows.values())
        if phase >= 1 and not pending and not rows_animating:
            self._animation_timer.stop()
