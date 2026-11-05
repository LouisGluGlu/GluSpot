"""First-run playback choices and source login display; no network or settings I/O.

The application controller owns synchronous OnlineMusicService calls in its
workers. Only a service-supplied QR image is displayed. Signals are the entire
outbound interface; closing this window never quits the tray application.
"""

from __future__ import annotations

from local.runtime_platform import normalize_playback_selection, owned_playback_only, system_media_unavailable_reason

import math
import os
import time
from dataclasses import dataclass

from PySide6.QtCore import QByteArray, QBuffer, QEasingCurve, QIODevice, QPoint, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QImageReader, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient, QTransform
from PySide6.QtWidgets import QAbstractButton, QLabel, QMessageBox, QPushButton, QWidget

from api.online_music import AccountSnapshot
from local.branding import APP_NAME
from local.island_stack_visual import IslandStackVisual
from local.music_lyric_reading import LyricReadingRenderer
from local.music_surface_background import AlbumFlowBackgroundRenderer
from local.music_surface_palette import idle_surface_palette
from local.backdrop_variants import BACKDROP_VARIANTS, resolve_backdrop_variant
from local.music_source_icons import paint_music_source_icon
from local.source_page_backdrop import (
    FIELD_BLEND_INTERVAL_MS, FIELD_BLEND_SECONDS, avatar_dominant_color,
    crossfade_color_fields, icon_dominant_color, render_source_color_field,
)
from local.liquid_glass import GlassPushButton, paint_liquid_glass, widget_glass_backdrop, widget_glass_level
from local.control_hover import install_control_hover
from local.music_page_resize import MusicPageResizeMixin
from local.login_layout_variants import login_variant_layout


@dataclass(frozen=True)
class EntrySource:
    source_id: str
    name: str
    color: str
    login_supported: bool = True


@dataclass(frozen=True)
class ChoicePlacement:
    """Mapped native-island geometry; the window never detects a system bar."""
    mode: str
    geometry: QRect
    content: QRectF
    shell: QPainterPath
    color: QColor


ENTRY_SOURCES = (
    EntrySource("netease", "网易云音乐", "#df2a16"),
    EntrySource("kugou", "酷狗音乐", "#368bff"),
    EntrySource("qq", "QQ 音乐", "#efd700"),
    EntrySource("qishui", "汽水音乐", "#183d30", False),
    EntrySource("spotify", "Spotify", "#1db954", False),
    EntrySource("apple_music", "Apple Music", "#fa5268", False),
)
LOGIN_SOURCE_IDS = frozenset(source.source_id for source in ENTRY_SOURCES if source.login_supported)
LOGIN_VARIANTS = ("classic", "sidebar", "focus", "hero")


def _entry_font(pixels: int, *, heavy: bool = False) -> QFont:
    font = QFont(IslandStackVisual.best_available_font_family("Alibaba PuHuiTi"))
    font.setPixelSize(pixels)
    font.setWeight(QFont.Weight.Black if heavy else QFont.Weight.Bold)
    return font


class EntryNavigationButton(QAbstractButton):
    """Shared native navigation geometry with a 44 px interactive target."""
    def __init__(self, kind: str, label: str, parent: QWidget):
        super().__init__(parent)
        self.kind = kind
        self.setAccessibleName(label)
        self.setToolTip(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        install_control_hover(self)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            backdrop, source = widget_glass_backdrop(self, rect)
            if not self.property("glassClusterMember") or self.underMouse() or self.hasFocus():
                paint_liquid_glass(painter, rect, radius=12, hovered=self.underMouse(), pressed=self.isDown(),
                                  backdrop=backdrop, backdrop_rect=source, refractive=True, cache_token=id(self),
                                  material_level=widget_glass_level(self),
                                  border_color=QColor("#494b51") if self.hasFocus() else None)
            painter.setPen(QPen(QColor("#e8e9ec"), 2, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            center = rect.center()
            if self.kind == "back":
                painter.drawLine(center + QPointF(3, -7), center + QPointF(-4, 0))
                painter.drawLine(center + QPointF(-4, 0), center + QPointF(3, 7))
            else:
                painter.drawLine(center + QPointF(-5, -5), center + QPointF(5, 5))
                painter.drawLine(center + QPointF(-5, 5), center + QPointF(5, -5))


class PlaybackModeButton(QAbstractButton):
    """Accessible capsule with the existing lyric reading sweep on selection."""

    SWEEP_SECONDS = 0.78

    def __init__(self, text: str, parent: QWidget, *, description: str = "", island_style: bool = False):
        super().__init__(parent)
        self.setText(text)
        self.setAccessibleName(text)
        self.description = description
        self.island_style = island_style
        self.setAccessibleDescription(description)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reading = LyricReadingRenderer()
        self._progress = 0.0
        self._selected_at: float | None = None
        self._keyboard_focus = False
        self._rejected_at: float | None = None
        self.toggled.connect(self._selection_changed)
        install_control_hover(self)

    def _selection_changed(self, checked: bool):
        self._selected_at = time.monotonic() if checked else None
        self._progress = 0.0
        self.update()

    def settle(self):
        self._selected_at = None
        self._progress = 1.0 if self.isChecked() else 0.0
        self.update()

    def advance(self, now: float):
        if self._rejected_at is not None:
            if now - self._rejected_at >= 1.1:
                self._rejected_at = None
            self.update()
        if self._selected_at is not None:
            self._progress = min(1.0, max(0.0, (now - self._selected_at) / self.SWEEP_SECONDS))
            if self._progress >= 1.0:
                self._selected_at = None
            self.update()

    def release_rasters(self):
        self._selected_at = None
        self._rejected_at = None
        self._reading = LyricReadingRenderer()

    def focusInEvent(self, event):
        self._keyboard_focus = event.reason() in {Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason}
        super().focusInEvent(event)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if self.island_style:
                rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
                face = QColor("#ffffff") if self.isChecked() else QColor("#707175")
                if self.isDown():
                    face = face.darker(112)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(face)
                painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
                font = _entry_font(max(14, min(21, int((rect.width() - 16) / max(1, len(self.text()))))), heavy=True)
                layout = self._reading.whole_line(self.text(), font)
                width = layout.lineAt(0).naturalTextWidth() if layout.lineCount() else 0
                self._reading.paint_whole_block(painter, ((
                    rect, rect.center().x() - width / 2, self.text(), font,
                    QColor("#d8d8da"), QColor("#050505"),
                ),), self._progress if self.isChecked() else 0.0)
                if self._rejected_at is not None:
                    phase = min(1.0, max(0.0, (time.monotonic() - self._rejected_at) / 1.1))
                    painter.setPen(QPen(QColor(255, 181, 86, round(235 * (1.0 - phase))), 2))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), rect.height() / 2, rect.height() / 2)
                if self.hasFocus() and self._keyboard_focus:
                    painter.setPen(QPen(QColor("#92949a"), 2, Qt.PenStyle.DotLine))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), rect.height() / 2, rect.height() / 2)
                return
            rect = QRectF(self.rect()).adjusted(10, 10, -10, -10)
            if self.isChecked():
                for spread, alpha in ((8, 13), (6, 20), (4, 30), (2, 50)):
                    painter.setPen(QPen(QColor(255, 255, 255, alpha), spread * 2))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
            backdrop, source = widget_glass_backdrop(self, rect)
            paint_liquid_glass(painter, rect, radius=rect.height() / 2, light=self.isChecked(),
                              base_color=QColor("#f4f4f4") if self.isChecked() else QColor("#8c8e91"),
                              border_color=QColor(255, 255, 255, 225) if self.isChecked() else None,
                              backdrop=backdrop, backdrop_rect=source, refractive=True, cache_token=id(self),
                              hovered=self.underMouse(), pressed=self.isDown(), active=self.isChecked())
            if self._rejected_at is not None:
                phase = min(1.0, max(0.0, (time.monotonic() - self._rejected_at) / 1.1))
                painter.setPen(QPen(QColor(155, 64, 18, round(235 * (1.0 - phase))), 2.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect.adjusted(-3 * phase, -3 * phase, 3 * phase, 3 * phase), rect.height() / 2, rect.height() / 2)
            if self.description:
                title_rect = QRectF(rect.x() + 12, rect.y() + (9 if rect.height() < 70 else 17),
                                    rect.width() - 52, 28)
                font = _entry_font(max(13, min(25, int(title_rect.width() / max(1, len(self.text()))))), heavy=True)
            else:
                title_rect = rect.adjusted(8, 0, -8, 0)
                font = _entry_font(max(18, min(32, round(rect.height() * .43))), heavy=True)
            layout = self._reading.whole_line(self.text(), font)
            width = layout.lineAt(0).naturalTextWidth() if layout.lineCount() else 0
            self._reading.paint_whole_block(painter, ((
                title_rect, title_rect.center().x() - width / 2,
                self.text(), font, QColor("#d5d5d5"), QColor("#090909"),
            ),), self._progress if self.isChecked() else 0.0)
            if self.description:
                ink = QColor("#292d34") if self.isChecked() else QColor("#e1e3e7")
                painter.setPen(ink)
                painter.setFont(_entry_font(11 if rect.width() < 200 else 12))
                caption = painter.fontMetrics().elidedText(self.description, Qt.TextElideMode.ElideRight,
                                                           max(1, round(rect.width() - 24)))
                painter.drawText(QRectF(rect.x() + 12, title_rect.bottom() + 1, rect.width() - 24, 20),
                                 Qt.AlignmentFlag.AlignCenter, caption)
                check = QRectF(rect.right() - 31, title_rect.center().y() - 8, 16, 16)
                painter.setPen(QPen(ink, 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(check)
                if self.isChecked():
                    path = QPainterPath()
                    path.moveTo(check.x() + 4, check.y() + 8)
                    path.lineTo(check.x() + 7, check.y() + 11)
                    path.lineTo(check.x() + 12, check.y() + 5)
                    painter.setPen(QPen(ink, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                        Qt.PenJoinStyle.RoundJoin))
                    painter.drawPath(path)
            if self.hasFocus() and self._keyboard_focus:
                painter.setPen(QPen(QColor("#131313"), 2, Qt.PenStyle.DotLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect.adjusted(4, 4, -4, -4), rect.height() / 2, rect.height() / 2)


class MusicSourceButton(QAbstractButton):
    """User-supplied source artwork with the existing themed selection rim."""

    def __init__(self, source: EntrySource, parent: QWidget):
        super().__init__(parent)
        self.source = source
        self.show_caption = True
        self.login_style = "classic"
        self.connection_status = ""
        self.source_status = "signed_out" if source.login_supported else "upcoming"
        self.status_opacity = 1.0
        self._availability_status = ""
        self._availability_reason = ""
        self.setText(source.name)
        self.setAccessibleName(source.name)
        self.setToolTip(source.name)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        install_control_hover(self)

    @property
    def availability_status(self) -> str:
        """Short, visible status for an unavailable source tile."""
        return self._availability_status

    def set_availability_status(self, status: str = "", reason: str = "") -> None:
        """Keep the concise tile label separate from the backend diagnostic.

        The source service owns the full reason.  The tile only presents a
        bounded summary while exposing the complete diagnostic to tooltips and
        assistive technology.
        """
        self._availability_status = str(status or "").strip()[:32]
        self._availability_reason = str(reason or "").strip()[:256]
        self._update_status_accessibility()
        self.update()

    def set_connection_status(self, status: str):
        self.source_status = status
        self.connection_status = {"connected": "已连接", "signed_out": "未登录",
                                  "error": "登录异常或已过期", "upcoming": "即将支持"}[status]
        self._update_status_accessibility()
        self.update()

    def _update_status_accessibility(self):
        status = self.connection_status
        if self._availability_status and self._availability_status != status:
            status = f"{status} · {self._availability_status}" if status else self._availability_status
        text = f"{self.source.name}（{status}）" if status else self.source.name
        self.setAccessibleName(text)
        description = text + (f"。{self._availability_reason}" if self._availability_reason else "")
        self.setAccessibleDescription(description)
        self.setToolTip(description)

    def _icon_rect(self) -> QRectF:
        if self.login_style == "classic":
            short = self.height() < 64
            horizontal = self.height() <= 72 and self.width() >= 116
            side = 16 if self.height() < 56 else 22 if short else 28 if self.height() <= 72 else 36
            if horizontal:
                return QRectF(12, (self.height() - side) / 2, side, side)
            return QRectF((self.width() - side) / 2,
                          4 if short else 8 if self.height() <= 72 else 14, side, side)
        side = 26 if self.height() > 48 else 22
        row = self.login_style in {"sidebar", "hero"}
        x = (self.width() - side) / 2 if self.login_style in {"focus", "sidebar-icons"} else 14 if row else 12
        return QRectF(x, (self.height() - side) / 2, side, side)

    def _status_dot_rect(self) -> QRectF:
        """10 px lamp whose center sits just outside the icon's bottom-right corner."""
        if not self.source.login_supported:
            return QRectF()
        icon = self._icon_rect()
        size = 10.0
        # The colored disc is 10 px. A 2 px ring is painted outside it, and the
        # center hangs 3 px past the icon so the lamp reads as a badge.
        overhang = 3.0
        ring = 2.0
        x = icon.right() + overhang - size / 2
        y = icon.bottom() + overhang - size / 2
        x = min(max(ring, x), max(ring, self.width() - size - ring))
        y = min(max(ring, y), max(ring, self.height() - size - ring))
        return QRectF(round(x), round(y), size, size)

    def status_rect(self) -> QRect:
        """Only this small area is invalidated by the source pulse clock."""
        dot = self._status_dot_rect()
        return dot.adjusted(-2, -2, 2, 2).toAlignedRect() if not dot.isEmpty() else QRect()

    def _paint_status_dot(self, painter: QPainter):
        dot = self._status_dot_rect()
        if dot.isEmpty():
            return
        painter.save()
        if self.source_status == "connected":
            color = QColor("#3fd27f")
            color.setAlphaF(self.status_opacity)
        elif self.source_status == "error":
            color = QColor("#ff5a5a")
        else:
            color = QColor("#7b7b82")
        painter.setPen(Qt.PenStyle.NoPen)
        # Opaque row-dark ring, then the colored disc, so the lamp sits on the icon.
        painter.setBrush(QColor(26, 28, 34))
        painter.drawEllipse(dot.adjusted(-2, -2, 2, 2))
        painter.setBrush(color)
        painter.drawEllipse(dot)
        painter.restore()

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if self.login_style != "classic":
                self._paint_login_variant(painter)
                return
            upcoming = not self.source.login_supported
            enabled = self.isEnabled() and not upcoming
            checked = enabled and self.isChecked()
            face = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            backdrop, source = widget_glass_backdrop(self, face)
            painter.setOpacity(1.0 if enabled else .45)
            border = QColor(self.source.color) if checked else QColor("#3c3e44")
            paint_liquid_glass(painter, face, radius=14,
                              backdrop=backdrop, backdrop_rect=source, refractive=True, cache_token=id(self),
                              material_level=widget_glass_level(self), border_color=border,
                              hovered=enabled and self.underMouse(), pressed=enabled and self.isDown(), active=checked)
            if enabled and (checked or self.hasFocus()):
                painter.setPen(QPen(border, 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(face.adjusted(1, 1, -1, -1), 13, 13)
            short = self.height() < 64
            horizontal = self.height() <= 72 and self.width() >= 116
            icon = self._icon_rect()
            if horizontal:
                text_x = icon.right() + 10
                title = QRectF(text_x, (4 if short else 10) if self._availability_status else (self.height() - 20) / 2,
                               self.width() - text_x - 8, 16 if short and self._availability_status else 20)
                status = QRectF(text_x, 24 if short else 32, title.width(), 12 if short else 16)
            else:
                title = QRectF(4, icon.bottom() + (1 if short else 4), self.width() - 8, 14 if short else 18)
                status = QRectF(4, self.height() - (12 if short else 17), self.width() - 8, 12)
            paint_music_source_icon(painter, icon, self.source.source_id)
            self._paint_status_dot(painter)
            if self.show_caption:
                painter.setOpacity(.50 if upcoming else .82 if not self.isEnabled() else 1.)
                painter.setFont(_entry_font(10 if short or horizontal and self.width() < 140 else 11 if self.width() < 116 else 12))
                painter.setPen(QColor("#e2e3e7"))
                align = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if horizontal else Qt.AlignmentFlag.AlignHCenter)
                name = painter.fontMetrics().elidedText(self.source.name, Qt.TextElideMode.ElideRight, round(title.width()))
                painter.drawText(title, align, name)
                if self._availability_status:
                    painter.setOpacity(.36 if upcoming else .82)
                    painter.setFont(_entry_font(9 if short else 10))
                    painter.setPen(QColor("#b9bcc6"))
                    caption = painter.fontMetrics().elidedText(self._availability_status, Qt.TextElideMode.ElideRight, round(status.width()))
                    painter.drawText(status, align, caption)

    def _paint_login_variant(self, painter: QPainter):
        face = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        upcoming = not self.source.login_supported
        # The same availability policy as classic also gates variant chrome.
        enabled = self.isEnabled() and not upcoming
        checked = enabled and self.isChecked()
        sidebar = self.login_style in {"sidebar", "sidebar-icons"}
        row = self.login_style in {"sidebar", "hero"}
        radius = 14 if row or sidebar else face.height() / 2
        backdrop, source = widget_glass_backdrop(self, face)
        painter.setOpacity(1. if enabled else .45)
        border = QColor(self.source.color) if checked else QColor(255, 255, 255, 26)
        if sidebar and checked:
            border.setAlpha(115)
        if not self.property("glassClusterMember") or self.underMouse() or self.hasFocus():
            paint_liquid_glass(painter, face, radius=radius, backdrop=backdrop, backdrop_rect=source,
                               refractive=True, cache_token=id(self), material_level=widget_glass_level(self),
                               light=sidebar, opacity=.75 if sidebar else 1.,
                               border_color=border, active=checked,
                               hovered=enabled and self.underMouse(), pressed=enabled and self.isDown())
        if enabled and (self.hasFocus() or (checked and not sidebar)):
            painter.setPen(QPen(QColor("#b6bac5") if self.hasFocus() else border, 1.3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(face.adjusted(1, 1, -1, -1), radius - 1, radius - 1)
        icon = self._icon_rect()
        paint_music_source_icon(painter, icon, self.source.source_id)
        self._paint_status_dot(painter)
        if self.login_style in {"focus", "sidebar-icons"}:
            return
        painter.setPen(QColor("#f0f1f5"))
        painter.setFont(_entry_font(12 if row else 11))
        text_x = icon.right() + (12 if row else 8)
        status = self._availability_status or self.connection_status
        has_chip = self.login_style == "hero" and bool(status)
        title = QRectF(text_x, 0, self.width() - text_x - (90 if has_chip else 34 if sidebar else 10), self.height())
        if status and not has_chip and not sidebar:
            title.setHeight(self.height() / 2 + 2)
            title.moveTop(3)
        painter.drawText(title, Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(self.source.name, Qt.TextElideMode.ElideRight, round(title.width())))
        if has_chip:
            chip = QRectF(self.width() - 78, (self.height() - 24) / 2, 64, 24)
            connected = status == "已连接"
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(94, 202, 163, 30) if connected else QColor(255, 255, 255, 13))
            painter.drawRoundedRect(chip, 12, 12)
            painter.setPen(QColor("#a3ddc7") if connected else QColor("#adb2bf"))
            painter.setFont(_entry_font(10))
            painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, status)
        elif status and not sidebar:
            painter.setPen(QColor("#a5aab7"))
            painter.setFont(_entry_font(9))
            painter.drawText(QRectF(text_x, self.height() / 2 + 1, title.width(), self.height() / 2 - 3),
                             Qt.AlignmentFlag.AlignVCenter, status)


class SourceFlatButton(GlassPushButton):
    """Source-page action: flat translucent glass, 1 px rim, no specular highlight.

    Shared glass buttons keep their own painter. This subclass only replaces
    the face of the source-page login, use, and logout actions.
    """

    def __init__(self, text: str, parent: QWidget | None = None, *, primary: bool = False):
        super().__init__(text, parent)
        self.primary = primary
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(_entry_font(14))
        self.setProperty("glassRadius", 22.0)
        self.setProperty("glassRefractive", True)
        self.setGlassLight(True)

    def paintEvent(self, _event):
        rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        if rect.width() < 2 or rect.height() < 2:
            return
        radius = min(rect.width(), rect.height()) / 2
        fill = 74 if self.primary else 56
        border = 96 if self.primary else 56
        if self.isEnabled() and self.isDown():
            fill = max(40, fill - 12)
        elif self.isEnabled() and self.underMouse():
            fill += 16
        if not self.isEnabled():
            fill, border = 32, 40
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(255, 255, 255, border), 1))
            painter.setBrush(QColor(255, 255, 255, fill))
            painter.drawRoundedRect(rect, radius, radius)
            if self.primary and self.isEnabled():
                ink = QColor("#f6f6f6")
            elif self.isEnabled():
                ink = QColor("#ececec")
            else:
                ink = QColor("#9296a1")
            painter.setPen(ink)
            painter.setFont(self.font())
            label = painter.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, max(1, self.width() - 8))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, label)


class PlaybackEntryWindow(MusicPageResizeMixin, QWidget):
    selectionAccepted = Signal(bool, bool)
    sourceSelected = Signal(str)
    refreshRequested = Signal()
    loginAccepted = Signal(str)
    sourceLogoutRequested = Signal(str)
    backRequested = Signal()
    closed = Signal()

    PALETTE_INTERVAL_SECONDS = 5.0
    PALETTES = (
        ("#f0c488", "#c8874d", "#bdcf9b", "#d493a2"),
        ("#b5d5d7", "#658f94", "#e4c19b", "#b9b3d4"),
        ("#d5bbd6", "#9571a6", "#e5c894", "#94b7aa"),
        ("#cbd5ac", "#7c9c8a", "#e8ba92", "#c7a9bc"),
    )

    def __init__(self, parent: QWidget | None = None, *, owned: bool = True, system: bool = True):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        IslandStackVisual.load_embedded_lyric_fonts()
        self.setWindowTitle(f"{APP_NAME} · 播放来源")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setProperty("controlHoverDisabled", True)
        self.setMouseTracking(True)
        self._resize_page(370, 176)
        self.page = "modes"
        requested_variant = os.environ.get("GLUSPOT_LOGIN_VARIANT", "sidebar").strip().lower()
        self._login_variant = requested_variant if requested_variant in LOGIN_VARIANTS else "sidebar"
        self.selected_source = ""
        self.login_state = "unavailable"
        self._accept_snapshots = True
        self._source_available: dict[str, bool] = {}
        self._source_reasons: dict[str, str] = {}
        self._source_status = {source.source_id: "signed_out" if source.login_supported else "upcoming"
                               for source in ENTRY_SOURCES}
        self._qr_image = QImage()
        self._account: AccountSnapshot | None = None
        self._account_image = QImage()
        self._backdrop_variant = None
        self._flow = AlbumFlowBackgroundRenderer()
        self._background_frame = QImage()
        self._source_background_size = None
        self._background_palette = idle_surface_palette()
        self._palette_index = 0
        self._palette_started_at = time.monotonic()
        self._expanded = 0.0
        self._drag_origin: QPoint | None = None
        self._mode_back_enabled = True
        self._mode_warning_until = 0.0
        self._system_session_hint = "系统抓取通过 Windows SMTC 同步外部播放器。"
        self._choices_placement: ChoicePlacement | None = None
        self._contraction_frame = QImage()
        self._contraction_target: QRect | None = None
        self._contraction_origin = QRect()
        self._contraction_fraction = 0.0
        self._contraction_finished = None
        self._contraction = QVariantAnimation(self)
        self._contraction.setDuration(450)
        self._contraction.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._contraction.valueChanged.connect(self._advance_contraction)
        self._contraction.finished.connect(self._finish_contraction)
        self._page_morph = QVariantAnimation(self)
        self._page_morph.setDuration(420)
        self._page_morph.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._page_morph.valueChanged.connect(self._advance_page_morph)
        self._page_morph.finished.connect(self._finish_page_morph)
        self._page_morph_origin = QRect()
        self._page_morph_target = QRect()
        self._page_morph_from = QImage()
        self._page_morph_to = QImage()
        self._page_morph_fraction = 0.0
        self._expansion = QVariantAnimation(self)
        self._expansion.setDuration(300)
        self._expansion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._expansion.valueChanged.connect(self._set_expanded)
        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(33)
        self._frame_timer.timeout.connect(self._advance)
        self._status_pulse_started_at = time.monotonic()
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(90)
        self._status_timer.timeout.connect(self._advance_status_pulse)
        self._field_clock = time.monotonic()
        self._field_seconds = 0.0
        self._field_from: tuple[QColor | None, QColor] | None = None
        self._field_target: tuple[QColor | None, QColor] | None = None
        self._field_blend_at = 0.0
        self._field_plate_from = QImage()
        self._field_plate_to = QImage()
        self._field_timer = QTimer(self)
        self._field_timer.setInterval(200)
        self._field_timer.timeout.connect(self._advance_platform_field)

        self.owned_button = PlaybackModeButton("程序内播放", self, description="连接账号，播放音乐库", island_style=True)
        self.system_button = PlaybackModeButton("系统内抓取", self, description="同步外部播放器与歌词", island_style=True)
        owned, system = normalize_playback_selection(owned, system)
        self.owned_button.setChecked(bool(owned) or not system)
        self.system_button.setChecked(bool(system))
        if owned_playback_only():
            self.system_button.setEnabled(False)
            self.owned_button.setEnabled(False)
        self.owned_button.settle()
        self.system_button.settle()
        self.owned_button.toggled.connect(lambda checked: self._ensure_mode(self.owned_button, checked))
        self.system_button.toggled.connect(lambda checked: self._ensure_mode(self.system_button, checked))
        self.continue_button = QPushButton("确定", self)
        self.continue_button.setAccessibleName("继续选择播放来源")
        self.continue_button.clicked.connect(self._accept_modes)
        self.close_button = EntryNavigationButton("close", "关闭播放来源窗口", self)
        self.close_button.setAccessibleName("关闭播放来源窗口")
        self.close_button.clicked.connect(self.close)
        self.back_button = EntryNavigationButton("back", "返回播放模式", self)
        self.back_button.setAccessibleName("返回播放模式")
        self.back_button.clicked.connect(self._go_back)
        self.refresh_button = SourceFlatButton("刷新二维码", self, primary=True)
        self.refresh_button.clicked.connect(self._refresh_login)
        self.enter_button = SourceFlatButton("继续", self, primary=True)
        self.enter_button.clicked.connect(self._accept_login)
        self.account_logout_button = SourceFlatButton("退出登录", self, primary=False)
        self.account_logout_button.setAccessibleName("退出登录")
        self.account_logout_button.clicked.connect(self._confirm_visible_logout)
        self.account_logout_button.hide()
        self.message_label = QLabel("选择音乐来源", self)
        self.message_label.setWordWrap(True)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setFont(_entry_font(14))
        self.message_label.setStyleSheet("color: #b3b6c0; background: transparent;")
        self.mode_hint = QLabel(self)
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.mode_hint.setFont(_entry_font(13))
        self.mode_hint.setStyleSheet("color: #f4f5f7; background: transparent;")
        self.owned_button.setToolTip("在 GluSpot 内连接音乐账号并播放歌单")
        self.system_button.setToolTip("通过 Windows SMTC 同步外部播放器的歌曲与播放状态")
        self._update_mode_hint()
        self.source_buttons = {source.source_id: MusicSourceButton(source, self) for source in ENTRY_SOURCES}
        for source_id, button in self.source_buttons.items():
            button.set_connection_status(self._source_status[source_id])
            button.setEnabled(False)
            if button.source.login_supported:
                button.setToolTip(f"{button.source.name}：正在确认服务状态")
            else:
                button.setToolTip(f"{button.source.name}：即将支持，暂未提供登录适配器")
                button.set_availability_status("即将支持", "暂未提供登录适配器")
            button.clicked.connect(lambda checked=False, sid=source_id: self.select_source(sid))
        self._logout_confirmation = None
        for button in (self.refresh_button, self.enter_button, self.account_logout_button):
            button.setGlassLight(True)
            button.setProperty("glassRadius", 22.0)
            button.setProperty("glassRefractive", True)
            button.setFont(_entry_font(15))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet("QPushButton { color: #f4f5f7; background: transparent; border: none; border-radius: 22px; padding: 6px 8px; } QPushButton:disabled { color: #9296a1; }")
        self.continue_button.setFont(_entry_font(21, heavy=True))
        self.continue_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.continue_button.setStyleSheet("QPushButton { color: #050505; background: #ffffff; border: none; border-radius: 21px; padding: 0; } QPushButton:pressed { background: #dedee0; } QPushButton:focus { border: 2px solid #92949a; }")
        self._layout_children()

    @property
    def selection(self) -> tuple[bool, bool]:
        return self.owned_button.isChecked(), self.system_button.isChecked()

    @property
    def login_variant(self) -> str:
        return self._login_variant

    @property
    def backdrop_variant(self) -> str:
        return resolve_backdrop_variant(self._backdrop_variant)

    def set_backdrop_variant(self, name: str | None):
        """Replace the static backdrop while preserving the login session."""
        if name is not None:
            name = str(name).strip().lower()
            if name not in BACKDROP_VARIANTS:
                raise ValueError(f"Unknown backdrop variant: {name!r}")
        self._backdrop_variant = name
        self._source_background_size = None
        if self.page == "sources":
            self._source_backdrop()
        self.update()

    def set_login_variant(self, name: str):
        """Switch presentation without restarting or replacing the login session."""
        name = str(name).strip().lower()
        if name not in LOGIN_VARIANTS:
            raise ValueError(f"Unknown login variant: {name!r}")
        if name == self._login_variant:
            return
        self._login_variant = name
        self._source_background_size = None
        if self.page == "sources" and name != "classic" and self.height() < 400:
            # A classic picker is only 264 px high. Use the same screen/shared
            # geometry boundary as show_sources before exposing a detail pane.
            self._resize_page(self.width(), 560)
        self._layout_children()
        self.update()

    def _variant_layout(self):
        return login_variant_layout(self.login_variant, self.width(), self.height(),
                                    tuple(source.login_supported for source in ENTRY_SOURCES))

    def _resize_page(self, width: int, height: int):
        # QWidget.screen() reparents shared wrappers to this transient widget.
        screen = QGuiApplication.screenAt(self.frameGeometry().center()) or QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)
        self._available_page_height = available.height()
        shared = getattr(self, "_shared_music_geometry", QRect())
        if getattr(self, "page", "modes") == "sources" and shared.isValid():
            # QR/account expansion changes the content, never the native
            # destination geometry established by the music-page controller.
            self.setMinimumSize(min(330, available.width()), min(400, available.height()))
            self.setGeometry(shared)
            return
        self.setMinimumSize(0, 0)
        self.resize(min(width, available.width()), min(height, available.height()))

    def _ensure_mode(self, changed: PlaybackModeButton, checked: bool):
        if not checked and not any(self.selection):
            changed.setChecked(True)
            changed._rejected_at = time.monotonic()
            self._mode_warning_until = time.monotonic() + 2.4
        self._update_mode_hint()

    def set_system_session_hint(self, message: str):
        """Present provider evidence; never infer another app's SMTC switch."""
        self._system_session_hint = str(message)
        self._update_mode_hint()

    def _update_mode_hint(self):
        if not hasattr(self, "mode_hint"):
            return
        if owned_playback_only():
            self.mode_hint.setText(system_media_unavailable_reason())
            self.mode_hint.setToolTip(system_media_unavailable_reason())
            return
        self.mode_hint.setToolTip(self._system_session_hint)
        self.mode_hint.setAccessibleDescription(self._system_session_hint)
        if time.monotonic() < self._mode_warning_until:
            self.mode_hint.setText("至少保留一种")
            return
        self.mode_hint.setText("可在设置中更改")

    def _accept_modes(self):
        owned, system = self.selection
        self.selectionAccepted.emit(owned, system)

    def show_choices(self):
        """Select the first-run page; the controller decides when to show it."""
        self.show_modes()

    def show_choices_from(self, global_origin: QRect):
        """Expand the installer-owned island in already-mapped Qt coordinates."""
        if not isinstance(global_origin, QRect) or not global_origin.isValid():
            raise ValueError("Playback choices require a valid logical island QRect")
        self.show_modes()
        target = self._choices_placement.geometry if self._choices_placement is not None else self._anchored_page_geometry(global_origin)
        self._start_page_morph(global_origin, target, QImage())

    def set_choices_placement(self, placement: ChoicePlacement):
        """Follow the controller's real Top/Finder shell without moving login."""
        previous = self._choices_placement
        if previous == placement:
            # Native first-show placement (including an external window
            # manager) may move this top-level window without changing the
            # validated bar. The regular presence tick must repair the actual
            # geometry too, rather than treating unchanged evidence as proof
            # that the choices are still attached to their owner.
            self._reconcile_choices_geometry()
            return
        animate = previous is not None and self.page == "modes" and self.isVisible()
        origin = QRect(self.geometry())
        frame = self.transition_snapshot() if animate else QImage()
        self._choices_placement = placement
        if self.page != "modes":
            return
        if animate and previous.geometry != placement.geometry:
            self._start_page_morph(origin, placement.geometry, frame)
        elif self._page_morph_to.isNull():
            self.setGeometry(placement.geometry)
            self._layout_children()
        self.update()

    def _reconcile_choices_geometry(self):
        """Reassert an attached first-use shell after native window mapping."""
        placement = self._choices_placement
        if (self.page != "modes" or placement is None or not self.isVisible()
                or not self._page_morph_to.isNull() or self._contraction_target is not None):
            return
        if self.geometry() != placement.geometry:
            self.setGeometry(placement.geometry)
            self._layout_children()
            self.update()

    def _choices_content_rect(self) -> QRectF:
        placement = self._choices_placement
        return QRectF(placement.content) if placement is not None else QRectF(self.rect())

    def show_login(self):
        """Select the source page without starting a backend login request."""
        animate = self.isVisible() and self.page == "modes"
        origin = QRect(self.geometry())
        frame = self.transition_snapshot() if animate else QImage()
        self.show_sources()
        if animate:
            self._start_page_morph(origin, self._anchored_page_geometry(origin), frame)

    def set_mode_back_enabled(self, enabled: bool):
        """Prevent completed onboarding from being re-entered via login Back."""
        self._mode_back_enabled = bool(enabled)
        self.back_button.setAccessibleName("返回播放模式" if enabled else "返回音乐来源")
        self.back_button.setToolTip(self.back_button.accessibleName())
        self._layout_children()

    def _go_back(self):
        if self.page != "sources":
            return
        if self.selected_source:
            self.show_sources()
            self.backRequested.emit()
            return
        if self._mode_back_enabled:
            self.show_modes()
            return
        self.show_sources()
        self.backRequested.emit()

    def show_modes(self):
        self._cancel_page_morph()
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self._release_login()
        self.page = "modes"
        self.setMinimumSize(0, 0)
        self._accept_snapshots = True
        self.selected_source = ""
        self._background_frame = QImage()
        if self._choices_placement is not None:
            self.setGeometry(self._choices_placement.geometry)
        else:
            self._resize_page(370, 176)
        self._layout_children()
        if self.isVisible():
            self._frame_timer.start()
            self._advance()
        self.backRequested.emit()
        if was_visible:
            self.show()
        self.update()

    def show_sources(self):
        self._cancel_page_morph()
        was_visible = self.isVisible()
        # Only the edge-attached first-use island needs topmost placement.
        # Login retains the normal window level so settings/modal UI can focus.
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self._release_login()
        self._frame_timer.stop()
        self.page = "sources"
        self._background_frame = QImage()
        self._source_background_size = None
        self._accept_snapshots = True
        self.selected_source = ""
        for button in self.source_buttons.values():
            button.setChecked(False)
        self._expanded = 0.0
        self._resize_page(560, 264 if self.login_variant == "classic" else 560)
        self.message_label.setText("选择音乐来源")
        self._layout_children()
        if was_visible:
            self.show()
        self.update()

    def _anchored_page_geometry(self, origin: QRect) -> QRect:
        screen = QGuiApplication.screenAt(origin.center()) or QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)
        x = max(available.left(), min(origin.center().x() - (self.width() - 1) // 2,
                                      available.right() - self.width() + 1))
        y = max(available.top(), min(origin.y(), available.bottom() - self.height() + 1))
        return QRect(x, y, self.width(), self.height())

    def _cancel_page_morph(self):
        if not hasattr(self, "_page_morph"):
            return
        self._page_morph.stop()
        self._page_morph_from = QImage()
        self._page_morph_to = QImage()

    def _start_page_morph(self, origin: QRect, target: QRect, frame: QImage):
        self._cancel_page_morph()
        self.setGeometry(target)
        self._layout_children()
        target_frame = self.transition_snapshot()
        self._page_morph_origin = QRect(origin)
        self._page_morph_target = QRect(target)
        self._page_morph_from = frame
        self._page_morph_to = target_frame
        self._page_morph_fraction = 0.0
        self.setGeometry(origin)
        for child in self.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            child.hide()
        self._page_morph.setStartValue(0.0)
        self._page_morph.setEndValue(1.0)
        self._page_morph.start()

    def _advance_page_morph(self, value):
        fraction = self._page_morph_fraction = float(value)
        origin, target = self._page_morph_origin, self._page_morph_target
        self.setGeometry(QRect(*(round(a + (b - a) * fraction) for a, b in zip(
            (origin.x(), origin.y(), origin.width(), origin.height()),
            (target.x(), target.y(), target.width(), target.height()),
        ))))
        self.update()

    def _finish_page_morph(self):
        self.setGeometry(self._page_morph_target)
        self._cancel_page_morph()
        self._layout_children()
        self.update()

    def set_sources(self, sources):
        catalog = {str(getattr(source, "source_id", "")): source for source in sources}
        for source_id, button in self.source_buttons.items():
            source = catalog.get(source_id)
            available = bool(button.source.login_supported and source is not None and getattr(source, "available", False))
            reason = str(getattr(source, "reason", "") or "当前服务不可用") if not available else ""
            if not button.source.login_supported:
                reason = str(getattr(source, "reason", "") or "暂未提供登录适配器")
                status = "即将支持"
            else:
                status = self._source_availability_status(source_id, reason) if not available else ""
            self._source_available[source_id] = available
            self._source_reasons[source_id] = reason
            button.setEnabled(available)
            button.setToolTip(button.source.name if available else f"{button.source.name}：{reason}")
            button.set_availability_status(status, reason)
            button.set_connection_status(self._source_status[source_id])
        if self.selected_source and not self._source_available.get(self.selected_source, False):
            self.set_login_snapshot(type("Unavailable", (), {"source_id": self.selected_source, "state": "unavailable", "message": self._source_reasons[self.selected_source]})())
        elif not self.selected_source:
            self.message_label.setText("选择音乐来源" if any(self._source_available.values()) else self._source_unavailable_message())
        self._sync_status_pulse()

    def _set_source_status(self, source_id: str, status: str):
        button = self.source_buttons.get(source_id)
        if button is None or not button.source.login_supported:
            return
        self._source_status[source_id] = status
        button.set_connection_status(status)
        self._sync_status_pulse()
        if self.isVisible():
            self._layout_children()

    def _confirm_visible_logout(self):
        account = self._account
        source_id = account.source_id if account is not None else self.selected_source
        if source_id:
            self._confirm_source_logout(source_id)

    def _confirm_source_logout(self, source_id: str):
        if self._source_status.get(source_id) != "connected":
            return
        if self._logout_confirmation is not None:
            self._logout_confirmation.raise_()
            self._logout_confirmation.activateWindow()
            return
        button = self.source_buttons.get(source_id)
        name = button.source.name if button is not None else source_id
        dialog = QMessageBox(self)
        dialog.setWindowTitle("退出登录")
        dialog.setText(f"确认退出{name}？")
        dialog.setInformativeText("只清除这个平台的登录记录，其他平台不受影响。")
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        dialog.button(QMessageBox.StandardButton.Ok).setText("退出")
        dialog.button(QMessageBox.StandardButton.Cancel).setText("取消")
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        dialog.setEscapeButton(QMessageBox.StandardButton.Cancel)
        self._logout_confirmation = dialog

        def finished(result):
            if self._logout_confirmation is not dialog:
                return
            self._logout_confirmation = None
            if result == int(QMessageBox.StandardButton.Ok) and self._source_status.get(source_id) == "connected":
                self.sourceLogoutRequested.emit(source_id)
            dialog.deleteLater()

        dialog.finished.connect(finished)
        dialog.open()

    @staticmethod
    def _account_status(account: AccountSnapshot) -> str:
        if account.state in {"error", "expired"}:
            return "error"
        return "connected" if account.logged_in else "signed_out"

    def _visible_connected_buttons(self):
        if (self.page != "sources" or not self.isVisible() or self.login_variant != "sidebar"
                or not self._accept_snapshots or not self._page_morph_to.isNull()
                or self._contraction_target is not None):
            return ()
        return tuple(button for button in self.source_buttons.values()
                     if button.isVisible() and button.source_status == "connected" and not button.status_rect().isEmpty())

    def _sync_status_pulse(self):
        if self._visible_connected_buttons():
            if not self._status_timer.isActive():
                self._status_timer.start()
        else:
            self._status_timer.stop()

    def _advance_status_pulse(self, now: float | None = None):
        buttons = self._visible_connected_buttons()
        if not buttons:
            self._status_timer.stop()
            return
        now = time.monotonic() if now is None else now
        opacity = .55 + .45 * (.5 - .5 * math.cos(math.tau * (now - self._status_pulse_started_at) / 2.0))
        for button in buttons:
            button.status_opacity = opacity
            button.update(button.status_rect())

    @staticmethod
    def _source_availability_status(source_id: str, reason: str) -> str:
        """Map provider diagnostics to a short source-page status label."""
        text = str(reason or "")
        if "不支持" in text or "尚无已验证" in text:
            return "暂不支持"
        if "版本不匹配" in text or "更新" in text:
            return "需更新运行时"
        if "未安装" in text or "适配依赖" in text:
            return "需安装运行时"
        return "暂不可用"

    def _source_unavailable_message(self) -> str:
        statuses = {button.availability_status for button in self.source_buttons.values()}
        hints = []
        if "需安装运行时" in statuses and "需更新运行时" in statuses:
            hints.append("请修复在线音乐运行时")
        elif "需安装运行时" in statuses:
            hints.append("请安装在线音乐运行时")
        elif "需更新运行时" in statuses:
            hints.append("请更新在线音乐运行时")
        if not hints:
            return "没有可用音乐来源，请稍后重试或返回系统抓取"
        return "没有可用音乐来源；" + "；".join(hints) + "。"

    def select_source(self, source_id: str):
        if source_id not in self.source_buttons or not self._source_available.get(source_id, False):
            return
        if source_id == self.selected_source:
            # A checked Qt button toggles before clicked. Keep the active
            # source selected without cancelling its QR/account transaction.
            # Retry is the adjacent explicit action, not another source click.
            self.source_buttons[source_id].setChecked(True)
            self._sync_status_pulse()
            return
        self._release_login()
        self.selected_source = source_id
        self.login_state = "starting"
        for sid, button in self.source_buttons.items():
            button.setChecked(sid == source_id)
        self.message_label.setText("正在请求登录二维码…")
        self._expansion.setStartValue(self._expanded)
        self._expansion.setEndValue(1.0)
        self._expansion.start()
        self._layout_children()
        self.sourceSelected.emit(source_id)

    def _set_expanded(self, value):
        self._expanded = float(value)
        if self.login_variant == "classic":
            self._resize_page(560, round(264 + (224 if self._account is not None else 296) * self._expanded))
        self._layout_children()
        self.update()

    def set_login_snapshot(self, snapshot):
        """Display an immutable backend result; never create an invented QR."""
        if not self._accept_snapshots or self.page != "sources":
            return
        source_id = str(getattr(snapshot, "source_id", ""))
        state = str(getattr(snapshot, "state", "unavailable"))
        account = getattr(snapshot, "account", None)
        status = ("error" if state in {"error", "expired"} else self._account_status(account)
                  if isinstance(account, AccountSnapshot) and account.source_id == source_id
                  else self._source_status.get(source_id, "signed_out") if state == "confirmed" else "signed_out")
        self._set_source_status(source_id, status)
        if not self.selected_source or source_id != self.selected_source:
            return
        self.login_state = state
        self._login_failure_stage = getattr(snapshot, "failure_stage", "")
        self._qr_image = QImage()
        payload = getattr(snapshot, "qr_image_bytes", b"")
        if self.login_state not in {"confirmed", "expired", "error", "unavailable", "unsupported", "cancelled"} and isinstance(payload, bytes) and 0 < len(payload) <= 4 * 1024 * 1024:
            buffer = QBuffer()
            buffer.setData(QByteArray(payload))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            reader = QImageReader(buffer)
            size = reader.size()
            if 0 < size.width() <= 2048 and 0 < size.height() <= 2048:
                decoded = reader.read()
                if not decoded.isNull():
                    self._qr_image = decoded.scaled(512, 512, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
        defaults = {"starting": "正在请求登录二维码…", "loading": "正在请求登录二维码…", "waiting": "等待扫码", "scanned": "已扫码，请在手机确认", "confirmed": "登录成功", "expired": "二维码已过期，请刷新", "cancelled": "登录已取消", "unavailable": "当前音乐服务不可用", "unsupported": "当前音乐来源暂不支持登录", "error": "登录暂不可用，请重试"}
        message = str(getattr(snapshot, "message", "") or defaults.get(self.login_state, "正在确认登录状态"))
        backend_message = message
        # The adjacent guide explains how to scan. Condense only the service's
        # known generic instructions; retain distinct provider diagnostics.
        standard_messages = {
            "waiting": {"使用手机音乐应用扫码登录", "使用对应音乐 App 扫码登录"},
            "scanned": {"已扫码，请在手机上确认", "已扫码，请在音乐 App 中确认"},
        }
        if message in standard_messages.get(self.login_state, set()):
            message = defaults[self.login_state]
        if (self._qr_image.isNull() and self.login_state in {"waiting", "scanned"}
                and self._login_failure_stage != "account_status"):
            message = "登录服务未提供可显示的二维码，请刷新重试"
        self.message_label.setText(message)
        self.message_label.setToolTip(backend_message if message != backend_message else "")
        self.message_label.setAccessibleDescription(backend_message if message != backend_message else "")
        account = getattr(snapshot, "account", None)
        if self.login_state == "confirmed" and isinstance(account, AccountSnapshot) and account.logged_in:
            self.set_account(account)
        self._layout_children()
        self.update()

    def set_account(self, account: AccountSnapshot):
        """Display a verified account for the currently selected platform."""
        if not self._accept_snapshots or self.page != "sources" or not isinstance(account, AccountSnapshot):
            return
        self._set_source_status(account.source_id, self._account_status(account))
        if not account.logged_in or account.source_id != self.selected_source:
            return
        if self._account is None or self._account.user_id != account.user_id:
            self._account_image = QImage()
        self._account = account
        self._qr_image = QImage()
        self.login_state = "confirmed"
        self.enter_button.setText("使用此平台")
        self.enter_button.setFont(_entry_font(15))
        self._set_expanded(self._expanded)
        self.update()

    def set_account_artwork(self, image: QImage):
        """Accept controller-loaded artwork; this window performs no I/O."""
        if not self._accept_snapshots or self._account is None or not isinstance(image, QImage):
            return
        self._account_image = image.scaled(512, 512, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation) if not image.isNull() else QImage()
        if self.page == "sources" and (self.selected_source or self.login_variant == "hero"):
            self._source_background_size = None
            self._source_backdrop()
        self.update()

    def _refresh_login(self):
        if self.selected_source and self._source_available.get(self.selected_source, False):
            self._qr_image = QImage()
            self.login_state = "starting"
            self._set_source_status(self.selected_source, "signed_out")
            self.message_label.setText("正在刷新登录二维码…")
            self.message_label.setToolTip("")
            self.message_label.setAccessibleDescription("")
            self._layout_children()
            self.refreshRequested.emit()
            self.update()

    def _accept_login(self):
        if self.login_state == "confirmed" and self.selected_source:
            self.loginAccepted.emit(self.selected_source)

    def _release_login(self):
        self._status_timer.stop()
        self._expansion.stop()
        self._qr_image = QImage()
        self._account = None
        self._account_image = QImage()
        self.login_state = "unavailable"
        if hasattr(self, "message_label"):
            self.message_label.setToolTip("")
            self.message_label.setAccessibleDescription("")
        if hasattr(self, "enter_button"):
            self.enter_button.setText("继续")
            self.enter_button.setFont(_entry_font(15))

    def set_background_palette(self, palette):
        """Accept the library's prepared artwork roles; never extract in login paint."""
        self._background_palette = palette if palette is not None else idle_surface_palette()
        self._source_background_size = None
        if self.page == "sources":
            self._source_backdrop()
            self.update()

    def _source_backdrop(self):
        """Library field with no platform selected; avatar/icon blobs once one is."""
        if self.selected_source:
            self._paint_selected_field(time.monotonic())
        else:
            self._paint_library_field()
        self._sync_field_timer()

    def _platform_field_key(self):
        avatar_key = 0 if self._account_image.isNull() else self._account_image.cacheKey()
        return (self.width(), self.height(), self.login_variant, self.backdrop_variant,
                self.selected_source, avatar_key)

    def _platform_color_targets(self) -> tuple[QColor | None, QColor]:
        icon = icon_dominant_color(self.selected_source)
        avatar = None
        if (self._account is not None and self._account.source_id == self.selected_source
                and not self._account_image.isNull()):
            avatar = avatar_dominant_color(self._account_image)
        return avatar, icon

    @staticmethod
    def _color_id(color: QColor | None):
        return None if color is None else color.rgba()

    def _field_blend_amount(self, now: float) -> float:
        if self._field_target is None or self._field_from == self._field_target:
            return 1.0
        elapsed = now - self._field_blend_at
        if elapsed >= FIELD_BLEND_SECONDS:
            return 1.0
        return max(0.0, elapsed / FIELD_BLEND_SECONDS)

    def _retarget_platform_colors(self, now: float):
        target = self._platform_color_targets()
        target_id = (self._color_id(target[0]), self._color_id(target[1]))
        current_id = None if self._field_target is None else (
            self._color_id(self._field_target[0]), self._color_id(self._field_target[1]))
        plate = self._compose_field_plate(target)
        colors_changed = (self._field_target is not None and target_id != current_id
                          and not self._background_frame.isNull())
        if not colors_changed:
            # Variant or size changes replace the plate immediately. A color
            # change is the only path that crossfades.
            self._field_from = target
            self._field_target = target
            self._field_blend_at = now - FIELD_BLEND_SECONDS
            self._field_plate_from = QImage()
            self._field_plate_to = plate
            self._background_frame = plate
            return
        self._field_from = self._field_target
        self._field_plate_from = self._background_frame.copy()
        self._field_target = target
        self._field_plate_to = plate
        if self._field_plate_from.size() != plate.size():
            self._field_plate_from = self._compose_field_plate(self._field_from)
        self._field_blend_at = now

    def _compose_field_plate(self, colors: tuple[QColor | None, QColor]) -> QImage:
        avatar, icon = colors
        frame = render_source_color_field(self.size(), avatar, icon, self._field_seconds, self.backdrop_variant)
        with QPainter(frame) as painter:
            veil = (152 if self.login_variant == "sidebar" else 188) if self.backdrop_variant == "classic" else 28
            painter.fillRect(frame.rect(), QColor(12, 14, 20, veil))
        return frame

    def _blit_platform_field(self, now: float):
        amount = self._field_blend_amount(now)
        if amount < 1.0 and not self._field_plate_from.isNull() and not self._field_plate_to.isNull():
            self._background_frame = crossfade_color_fields(self._field_plate_from, self._field_plate_to, amount)
            return
        if self._field_plate_to.isNull() and self._field_target is not None:
            self._field_plate_to = self._compose_field_plate(self._field_target)
        if not self._field_plate_to.isNull():
            self._background_frame = self._field_plate_to.copy()

    def _paint_selected_field(self, now: float, *, animate: bool = False):
        key = self._platform_field_key()
        if not animate and self._source_background_size == key and not self._background_frame.isNull():
            return
        if self._source_background_size != key or self._field_target is None:
            self._retarget_platform_colors(now)
        self._source_background_size = key
        self._blit_platform_field(now)

    def _advance_platform_field(self):
        if self.page != "sources" or not self.isVisible() or not self.selected_source:
            self._field_timer.stop()
            return
        now = time.monotonic()
        blending = self._field_blend_amount(now) < 1.0
        if blending:
            # Pose stays put so the cached plates remain aligned.
            self._field_clock = now
            self._blit_platform_field(now)
            self.update()
            return
        self._field_seconds += max(0.0, now - self._field_clock)
        self._field_clock = now
        if self._field_target is not None:
            self._field_plate_to = self._compose_field_plate(self._field_target)
            self._background_frame = self._field_plate_to
        self.update()
        self._sync_field_timer()

    def _sync_field_timer(self):
        want = self.page == "sources" and self.isVisible() and bool(self.selected_source)
        if not want:
            if self._field_timer.isActive():
                self._field_timer.stop()
            return
        blending = self._field_blend_amount(time.monotonic()) < 1.0
        interval = FIELD_BLEND_INTERVAL_MS if blending else 200
        if self._field_timer.interval() != interval:
            self._field_timer.setInterval(interval)
        if not self._field_timer.isActive():
            self._field_clock = time.monotonic()
            self._field_timer.start()

    def _paint_library_field(self):
        """Freeze the shared artwork scene; no login timer/capture."""
        palette = self._background_palette
        colors = palette.ambient_colors or (palette.primary, palette.secondary)
        key = (self.width(), self.height(), self.login_variant,
               (self.selected_source, self._account_image.cacheKey()) if self.login_variant == "hero" else None,
               self.backdrop_variant, tuple(color.rgba() for color in colors), palette.ambient_weights)
        if self._source_background_size == key and not self._background_frame.isNull():
            return
        renderer = AlbumFlowBackgroundRenderer(self._backdrop_variant)
        self._background_frame = renderer.render(self.size(), colors, 0.0, intensity=0.0,
                                                 weights=palette.ambient_weights).copy()
        with QPainter(self._background_frame) as painter:
            veil = (152 if self.login_variant == "sidebar" else 188) if self.backdrop_variant == "classic" else 28
            painter.fillRect(self._background_frame.rect(), QColor(12, 14, 20, veil))
            if self.login_variant == "hero" and self.backdrop_variant == "classic":
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                frame = QRectF(self._background_frame.rect())
                if not self._account_image.isNull():
                    # A tiny local raster stretches into a soft avatar wash;
                    # neither a network fetch nor a moving background is needed.
                    soft = self._account_image.scaled(8, 8, Qt.AspectRatioMode.IgnoreAspectRatio,
                                                       Qt.TransformationMode.SmoothTransformation)
                    painter.setOpacity(.25)
                    painter.drawImage(frame.adjusted(-frame.width() * .25, -frame.height() * .5,
                                                      frame.width() * .25, frame.height() * .3), soft)
                    painter.setOpacity(1.)
                    shade = QLinearGradient(frame.topLeft(), frame.topRight())
                    shade.setColorAt(0, QColor(12, 14, 20, 0))
                    shade.setColorAt(1, QColor(12, 14, 20, 100))
                    painter.fillRect(frame, shade)
                tint = QColor(self.source_buttons.get(self.selected_source, self.source_buttons["netease"]).source.color)
                tint.setAlpha(55)
                glow = QRadialGradient(QPointF(frame.width() * .28, frame.height() * .18), frame.width() * .70)
                glow.setColorAt(0, tint)
                glow.setColorAt(1, QColor(0, 0, 0, 0))
                painter.fillRect(frame, glow)
        self._source_background_size = key

    def _compact_login_layout(self) -> bool:
        # Choose the short-display composition once for the target screen.
        # A normal-height expansion passes through 264..480 px; that must not
        # move the source row into compact mode and back during the animation.
        return bool(self.selected_source) and (self._available_page_height < 480 or
                                               (self._expanded > .85 and self.height() < 480))

    def _source_pane_rect(self) -> QRectF:
        if self.login_variant != "classic":
            return self._variant_layout().pane
        top = max(rect.bottom() for rect in self._source_tile_rects()) + (16 if self._compact_login_layout() else 24)
        return QRectF(32, top, self.width() - 64, max(1, self.height() - top - 24))

    def _source_tile_rects(self) -> tuple[QRectF, ...]:
        """Keep every platform visible without compressing its label or target."""
        if self.login_variant != "classic":
            return self._variant_layout().tiles
        columns = 6 if self.width() >= 720 else 3
        compact = self._compact_login_layout()
        top = 64 if compact else 112 if columns == 6 else 72
        height = (44 if self.height() < 380 else 56) if compact else 96 if columns == 6 else 64
        gap = 8 if columns == 3 else 12
        tile_width = (self.width() - 64 - gap * (columns - 1)) / columns
        return tuple(QRectF(32 + (index % columns) * (tile_width + gap),
                            top + (index // columns) * (height + gap), tile_width, height)
                     for index in range(len(self.source_buttons)))

    def _stacked_login_layout(self) -> bool:
        if self.login_variant != "classic":
            return self._variant_layout().centered
        return self.width() < 480 and self._source_pane_rect().height() >= 288

    def _login_content_rects(self) -> tuple[QRectF, QRectF]:
        """Center the QR composition; short displays keep two compact lanes."""
        if self.login_variant != "classic":
            return self._variant_layout().login_content()
        pane = self._source_pane_rect()
        if self._stacked_login_layout():
            side = min(176., max(96., pane.height() - 192.))
            qr = QRectF(pane.center().x() - side / 2,
                        pane.center().y() - (side + 166) / 2, side, side)
            return qr, QRectF(pane.left() + 20, qr.bottom() + 12, pane.width() - 40,
                             pane.bottom() - qr.bottom() - 28)
        compact = self._compact_login_layout()
        side = max(96, min(128 if compact else 192, pane.height() - 32,
                           (self.width() - 96) * .42))
        gap = 16 if compact or self.width() < 620 else 36
        detail_width = min(280., max(1., pane.width() - side - gap - 32))
        group_width = side + gap + detail_width
        left = pane.center().x() - group_width / 2
        qr = QRectF(left, pane.center().y() - side / 2, side, side)
        return qr, QRectF(qr.right() + gap, pane.top() + (8 if compact else 24),
                         detail_width, max(1, pane.height() - 40))

    def _login_detail_geometry(self) -> tuple[QRectF, QRectF, QRectF, QRectF]:
        if self.login_variant != "classic":
            return self._variant_layout().login_detail()
        _qr, detail = self._login_content_rects()
        compact = self._compact_login_layout()
        stacked = self._stacked_login_layout()
        title = QRectF(detail.x(), detail.y(), detail.width(), 24 if compact or stacked else 32)
        helper_height = 32 if stacked else 48 if detail.width() < 150 else 32 if compact else 48
        helper = QRectF(detail.x(), title.bottom() + (4 if compact or stacked else 8), detail.width(), helper_height)
        if stacked:
            message = QRectF(detail.x(), helper.bottom() + 6, detail.width(), 32)
            action_width = min(152, detail.width())
            action = QRectF(detail.center().x() - action_width / 2, message.bottom() + 12, action_width, 44)
            return title, helper, message, action
        action_y = self._source_pane_rect().bottom() - (8 if compact else 16) - 44
        message_y = helper.bottom() + (4 if compact else 6)
        message = QRectF(detail.x(), message_y, detail.width(), max(24, min(76, action_y - 8 - message_y)))
        action_width = min(152, detail.width())
        action = QRectF(detail.x(), action_y, action_width, 44)
        return title, helper, message, action

    def _account_content_rects(self) -> tuple[QRectF, QRectF, QRectF]:
        if self.login_variant != "classic":
            return self._variant_layout().account_content()
        pane = self._source_pane_rect()
        compact = self._compact_login_layout()
        side = 96 if compact else 128
        avatar = QRectF(56 if compact else 64, pane.center().y() - side / 2, side, side)
        text_x = avatar.right() + (24 if compact else 40)
        name = QRectF(text_x, pane.top() + (14 if compact else 60), self.width() - 32 - text_x, 32 if compact else 40)
        action = QRectF(text_x, pane.bottom() - (8 if compact else 36) - 38, min(300., name.width()), 38)
        return avatar, name, action

    def _account_caption(self, source: EntrySource) -> tuple[str, QColor]:
        status = self._source_status.get(source.source_id, "signed_out")
        if status == "error":
            return f"{source.name} · 连接出错", QColor("#ff8a7a")
        if status == "signed_out":
            return f"{source.name} · 未连接", QColor("#aeb9c5")
        return f"{source.name} · 已连接", QColor("#aeb9c5")

    @staticmethod
    def _flat_action_rect(slot: QRectF) -> QRect:
        top = slot.top() + max(0.0, (slot.height() - 38) / 2)
        return QRect(round(slot.left()), round(top), max(1, round(slot.width())), 38)

    @staticmethod
    def _split_account_actions(slot: QRectF) -> tuple[QRect, QRect]:
        gap = 10.0
        width = max(1.0, slot.width())
        left = min(156.0, max(96.0, (width - gap) * 0.56))
        if left + gap + 88 > width:
            left = max(1.0, (width - gap) / 2.0)
        right = max(1.0, width - gap - left)
        top = slot.top() + max(0.0, (slot.height() - 38) / 2)
        return (
            QRect(round(slot.left()), round(top), round(left), 38),
            QRect(round(slot.left() + left + gap), round(top), round(right), 38),
        )

    def _login_placeholder_text(self) -> str:
        return {
            "starting": "正在连接…", "loading": "正在读取账号…",
            "expired": "二维码已过期", "error": "暂时无法连接",
            "unsupported": "暂不支持登录", "unavailable": "服务不可用",
            "cancelled": "登录已取消", "confirmed": "已登录",
        }.get(self.login_state, "暂无二维码")

    def _login_action_text(self) -> str:
        if getattr(self, "_login_failure_stage", "") == "account_status":
            return "重试账号核验"
        if self.login_state in {"starting", "loading"}:
            return "正在连接…"
        return "刷新二维码"

    def _layout_children(self):
        modes = self.page == "modes"
        self.owned_button.setVisible(modes)
        self.system_button.setVisible(modes)
        self.continue_button.setVisible(modes)
        self.mode_hint.setVisible(modes)
        self.close_button.setGeometry(self.width() - 68, 16, 44, 44)
        self.close_button.setVisible(not modes)
        self.back_button.setGeometry(24, 16, 44, 44)
        self.back_button.setVisible(not modes and (self._mode_back_enabled or bool(self.selected_source)))
        self.back_button.setAccessibleName("返回音乐来源" if self.selected_source else "返回播放模式")
        self.back_button.setToolTip(self.back_button.accessibleName())
        if modes:
            body = self._choices_content_rect().toAlignedRect()
            inset, gutter = (28 if body.width() >= 360 else 20), 20
            card_width = (body.width() - inset * 2 - gutter) // 2
            self.owned_button.setGeometry(body.x() + inset, body.y() + 22, card_width, 46)
            self.system_button.setGeometry(body.x() + inset + card_width + gutter, body.y() + 22, card_width, 46)
            action_x = body.x() + max(inset, round(body.width() * .245))
            self.continue_button.setGeometry(action_x, body.bottom() + 1 - 64, 84, 44)
            hint_x = action_x + 94
            self.mode_hint.setFont(_entry_font(13))
            self.mode_hint.setGeometry(hint_x, body.bottom() + 1 - 62, body.right() + 1 - hint_x - inset, 38)
        for button, rect in zip(self.source_buttons.values(), self._source_tile_rects()):
            button.login_style = ("sidebar" if self.width() >= 720 and self.height() >= 520 else "sidebar-icons") if self.login_variant == "sidebar" else self.login_variant
            if self.login_variant == "hero" and self.height() < 500:
                button.login_style = "pill"
            button.setVisible(not modes and not rect.isEmpty())
            button.setGeometry(rect.toAlignedRect())
            button.update()
        expanded = not modes and self._expanded > .85
        account_card = expanded and self._account is not None
        self.message_label.setVisible(not modes and not account_card)
        if expanded and not account_card:
            _title, _helper, message, _action = self._login_detail_geometry()
            self.message_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | (
                Qt.AlignmentFlag.AlignHCenter if self._stacked_login_layout() else Qt.AlignmentFlag.AlignLeft))
            self.message_label.setFont(_entry_font(12 if self._compact_login_layout() or self._stacked_login_layout() else 14))
            self.message_label.setGeometry(message.toAlignedRect())
        else:
            self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.message_label.setFont(_entry_font(14))
            message_top = round(max(rect.bottom() for rect in self._source_tile_rects()) + 16)
            self.message_label.setGeometry((self._source_pane_rect().adjusted(20, 16, -20, -16)).toAlignedRect()
                                           if not modes and self.login_variant != "classic"
                                           else QRect(32, message_top, self.width() - 64, 24))
        show_pair = bool(account_card and self._source_status.get(self._account.source_id) == "connected")
        error_card = bool(account_card and not show_pair)
        self.account_logout_button.setVisible(show_pair)
        self.enter_button.setVisible(show_pair or (expanded and self.login_state == "confirmed" and not error_card))
        self.refresh_button.setVisible((expanded and self.login_state != "confirmed") or error_card)
        self.refresh_button.setEnabled(self.login_state not in {"starting", "loading", "unavailable", "unsupported"})
        self.refresh_button.setText(self._login_action_text())
        self.refresh_button.setFont(_entry_font(12 if self._compact_login_layout() else 15))
        self.refresh_button.setAccessibleName(self.refresh_button.text())
        _title, _helper, _message, login_action = self._login_detail_geometry()
        for button in (self.refresh_button, self.enter_button, self.account_logout_button):
            button.setProperty("glassMaterialLevel", widget_glass_level(self))
        if show_pair:
            use, leave = self._split_account_actions(self._account_content_rects()[2])
            self.enter_button.setGeometry(use)
            self.account_logout_button.setGeometry(leave)
            source_name = next(item.name for item in ENTRY_SOURCES if item.source_id == self._account.source_id)
            self.account_logout_button.setAccessibleName(f"退出{source_name}登录")
        elif error_card:
            self.refresh_button.setGeometry(self._flat_action_rect(self._account_content_rects()[2]))
        else:
            self.refresh_button.setGeometry(self._flat_action_rect(login_action))
            if self.enter_button.isVisible():
                self.enter_button.setGeometry((self.width() - 120) // 2, self.height() - 84, 120, 38)
        if not modes:
            self._source_backdrop()
        if not self._page_morph_to.isNull():
            for child in self.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
                child.hide()
        self._sync_status_pulse()

    def _advance(self):
        if not self.isVisible():
            self._frame_timer.stop()
            return
        now = time.monotonic()
        if self.page == "modes":
            self.owned_button.advance(now)
            self.system_button.advance(now)
            if self._mode_warning_until and now >= self._mode_warning_until:
                self._mode_warning_until = 0.0
                self._update_mode_hint()
            self.update()

    def transition_snapshot(self) -> QImage:
        """A UI-only frame for the controller's contraction into the island."""
        return self.grab().toImage()

    def contract_to(self, global_target: QRect, finished=None):
        """Contract a frozen UI frame into the controller-owned island geometry.

        No island or playback state is created here. The completion callback
        runs once after a successful contraction; user close cancels it.
        """
        if not isinstance(global_target, QRect) or not global_target.isValid():
            raise ValueError("Playback entry contraction requires a valid global QRect")
        self._contraction.stop()
        self._contraction_finished = None
        self._contraction_frame = self.transition_snapshot()
        self._contraction_origin = QRect(self.geometry())
        self._contraction_target = QRect(global_target)
        self._contraction_fraction = 0.0
        self._contraction_finished = finished
        self._frame_timer.stop()
        self._accept_snapshots = False
        self._release_login()
        for child in self.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            child.hide()
        self._contraction.setStartValue(0.0)
        self._contraction.setEndValue(1.0)
        self._contraction.start()

    def _advance_contraction(self, value):
        if self._contraction_target is None:
            return
        fraction = float(value)
        self._contraction_fraction = fraction
        first, target = self._contraction_origin, self._contraction_target
        self.setGeometry(QRect(*(round(a + (b - a) * fraction) for a, b in zip(
            (first.x(), first.y(), first.width(), first.height()),
            (target.x(), target.y(), target.width(), target.height()),
        ))))
        self.update()

    def _finish_contraction(self):
        callback = self._contraction_finished
        self._contraction_finished = None
        self.hide()
        self._contraction_frame = QImage()
        self._contraction_target = None
        if callback is not None:
            callback()

    def content_geometry(self) -> QRect:
        return QRect(self.mapToGlobal(QPoint(0, 0)), self.size())

    def showEvent(self, event):
        super().showEvent(event)
        self._accept_snapshots = True
        self._palette_started_at = time.monotonic()
        if self.page == "modes":
            self._frame_timer.start()
            self._advance()
            # Run after the native show has completed. In particular Qt 5's
            # first mapped frame must not leave a valid Finder shell at the
            # desktop's default centered position.
            QTimer.singleShot(0, self._reconcile_choices_geometry)
        else:
            self._source_backdrop()
        self._sync_status_pulse()

    def hideEvent(self, event):
        self._end_page_resize()
        self._cancel_page_morph()
        self._frame_timer.stop()
        self._field_timer.stop()
        self._accept_snapshots = False
        self._contraction.stop()
        self._contraction_finished = None
        self._contraction_frame = QImage()
        self._contraction_target = None
        self._release_login()
        self.owned_button.release_rasters()
        self.system_button.release_rasters()
        self._background_frame = QImage()
        self._flow = AlbumFlowBackgroundRenderer()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._cancel_page_morph()
        self._frame_timer.stop()
        self._field_timer.stop()
        self._contraction.stop()
        self._contraction_finished = None
        self._contraction_frame = QImage()
        self._contraction_target = None
        self._accept_snapshots = False
        self._release_login()
        self.closed.emit()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "owned_button") and getattr(self, "_contraction_target", None) is None:
            self._layout_children()

    def mousePressEvent(self, event):
        if self._begin_page_resize(event):
            return
        if event.button() == Qt.MouseButton.LeftButton and not (self.page == "modes" and self._choices_placement is not None):
            handle = self.windowHandle()
            if handle is None or not handle.startSystemMove():
                self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._move_page_resize(event):
            return
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._end_page_resize()
        self._drag_origin = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self.page == "sources" and (self._mode_back_enabled or self.selected_source):
                self._go_back()
            else:
                self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def _paint_variant_glass(self, painter: QPainter, rect: QRectF, token: str, radius: float = 22):
        sx = self._background_frame.width() / max(1, self.width())
        sy = self._background_frame.height() / max(1, self.height())
        paint_liquid_glass(painter, rect, radius=radius, backdrop=self._background_frame,
                           backdrop_rect=QRectF(rect.x() * sx, rect.y() * sy, rect.width() * sx, rect.height() * sy),
                           refractive=True, cache_token=(id(self), token),
                           light=self.login_variant == "sidebar", material_level=widget_glass_level(self),
                           opacity=.68 if self.login_variant == "sidebar" else 1.)

    def _paint_variant_avatar(self, painter: QPainter, rect: QRectF, source: EntrySource):
        painter.setPen(QPen(QColor(255, 255, 255, 65), 1.5))
        painter.setBrush(QColor("#343740"))
        painter.drawEllipse(rect)
        if self._account_image.isNull():
            paint_music_source_icon(painter, rect.adjusted(rect.width() * .2, rect.height() * .2,
                                                          -rect.width() * .2, -rect.height() * .2), source.source_id)
            return
        painter.save()
        clip = QPainterPath()
        clip.addEllipse(rect.adjusted(2, 2, -2, -2))
        painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        side = min(self._account_image.width(), self._account_image.height())
        crop = QRectF((self._account_image.width() - side) / 2, (self._account_image.height() - side) / 2, side, side)
        painter.drawImage(rect, self._account_image, crop)
        painter.restore()

    def _paint_login_variant(self, painter: QPainter):
        layout = self._variant_layout()
        wide = self.width() >= 720 and self.height() >= 520
        source = next((item for item in ENTRY_SOURCES if item.source_id == self.selected_source), ENTRY_SOURCES[0])
        painter.setFont(_entry_font(23))
        painter.setPen(QColor("#f1f2f5"))
        painter.drawText(QRectF(84 if self.back_button.isVisible() else 24, 20, self.width() - 168, 36),
                         Qt.AlignmentFlag.AlignVCenter, "音乐来源")
        if self.login_variant == "sidebar":
            self._paint_variant_glass(painter, layout.rail, "sidebar", 22 if wide else 20)
            if wide:
                painter.setFont(_entry_font(12))
                painter.setPen(QColor("#aeb3c0"))
                painter.drawText(QRectF(36, 76, self.width() - 72, 20), Qt.AlignmentFlag.AlignVCenter,
                                 "连接你的音乐账号，继续收听收藏与歌单")
            painter.setPen(QPen(QColor(255, 255, 255, 25), 1))
            separator = 320 if wide else (layout.tiles[2].bottom() + layout.tiles[3].top()) / 2
            painter.drawLine(QPointF(layout.rail.left() + 16, separator), QPointF(layout.rail.right() - 16, separator))
        elif self.login_variant == "focus":
            switch = layout.tiles[0].united(layout.tiles[2]).adjusted(-5, -5, 5, 5)
            self._paint_variant_glass(painter, switch, "segmented-switch", switch.height() / 2)
        elif self.login_variant == "hero" and self.height() >= 500:
            self._paint_variant_avatar(painter, QRectF(32 if wide else 24, 80, 52, 52), source)
            painter.setFont(_entry_font(24 if wide else 21))
            painter.setPen(QColor("#f3f2f6"))
            painter.drawText(QRectF(100 if wide else 90, 78, self.width() - 124, 32),
                             Qt.AlignmentFlag.AlignVCenter, "你的音乐账号")
            painter.setFont(_entry_font(12))
            painter.setPen(QColor("#b4b6c4"))
            painter.drawText(QRectF(100 if wide else 90, 111, self.width() - 124, 22),
                             Qt.AlignmentFlag.AlignVCenter, "继续收听收藏与歌单")
        painter.setFont(_entry_font(10 if wide and self.login_variant != "focus" else 11))
        painter.setPen(QColor("#8d93a3"))
        painter.drawText(layout.upcoming,
                         Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if wide and self.login_variant != "focus"
                                                        else Qt.AlignmentFlag.AlignHCenter),
                         "即将支持" if wide and self.login_variant in {"sidebar", "hero"} else "更多平台即将支持")
        self._paint_variant_glass(painter, layout.pane, "variant-detail")
        if self._expanded <= .85:
            return
        if self._account is not None:
            avatar, name_rect, _action = self._account_content_rects()
            self._paint_variant_avatar(painter, avatar, source)
            alignment = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignHCenter if layout.centered else Qt.AlignmentFlag.AlignLeft)
            painter.setFont(_entry_font(24 if layout.centered else 22))
            painter.setPen(QColor("#f5f5f8"))
            painter.drawText(name_rect, alignment, painter.fontMetrics().elidedText(
                self._account.nickname or "已登录账号", Qt.TextElideMode.ElideRight, round(name_rect.width())))
            caption, ink = self._account_caption(source)
            painter.setFont(_entry_font(12))
            painter.setPen(ink)
            painter.drawText(QRectF(name_rect.x(), name_rect.bottom() + 4, name_rect.width(), 24),
                             alignment, caption)
            return
        qr, _detail = self._login_content_rects()
        painter.setPen(QPen(QColor(255, 255, 255, 55), 1))
        painter.setBrush(QColor("#ffffff") if not self._qr_image.isNull() else QColor(255, 255, 255, 8))
        painter.drawRoundedRect(qr, 16, 16)
        if not self._qr_image.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            painter.drawImage(qr.adjusted(10, 10, -10, -10), self._qr_image)
        else:
            icon_side = min(40., qr.width() * .28)
            paint_music_source_icon(painter, QRectF(qr.center().x() - icon_side / 2,
                                                    qr.center().y() - icon_side, icon_side, icon_side), source.source_id)
            painter.setFont(_entry_font(12))
            painter.setPen(QColor("#c0c5d0"))
            painter.drawText(QRectF(qr.x() + 6, qr.center().y() + 10, qr.width() - 12, 32),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._login_placeholder_text())
        title, helper, _message, _action = self._login_detail_geometry()
        alignment = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignHCenter if layout.centered else Qt.AlignmentFlag.AlignLeft)
        painter.setFont(_entry_font(19 if helper.width() > 180 else 17))
        painter.setPen(QColor("#f0f2f6"))
        painter.drawText(title, alignment, painter.fontMetrics().elidedText(
            f"连接{source.name}", Qt.TextElideMode.ElideRight, round(title.width())))
        painter.setFont(_entry_font(12))
        painter.setPen(QColor("#aeb5c4"))
        guide = ("正在确认账号与登录服务" if self.login_state in {"starting", "loading"}
                 else "已扫码，请在手机上确认登录" if self.login_state == "scanned"
                 else "请重试连接，或选择其他音乐来源" if self._qr_image.isNull()
                 else f"打开{source.name} App，扫描二维码登录")
        painter.drawText(helper, alignment | Qt.TextFlag.TextWordWrap, guide)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            panel = QRectF(self.rect()).adjusted(2, 2, -2, -2)
            clip = QPainterPath()
            radius = min(panel.height() / 2, 32 if self.page == "modes" else 24)
            clip.addRoundedRect(panel, radius, radius)
            placement = self._choices_placement if self.page == "modes" else None
            if placement is not None:
                scale = QTransform.fromScale(self.width() / placement.geometry.width(), self.height() / placement.geometry.height())
                clip = scale.map(placement.shell)
                panel = QRectF(self.rect())
            painter.setClipPath(clip)
            if not self._page_morph_to.isNull():
                painter.fillRect(panel, QColor("#000000"))
                fraction = self._page_morph_fraction
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                if not self._page_morph_from.isNull():
                    painter.setOpacity(max(0.0, 1.0 - fraction / .38))
                    painter.drawImage(panel, self._page_morph_from)
                painter.setOpacity(max(0.0, min(1.0, (fraction - .30) / .70)))
                painter.drawImage(panel, self._page_morph_to)
                return
            if self._contraction_target is not None and not self._contraction_frame.isNull():
                painter.setOpacity(1.0 - max(0.0, (self._contraction_fraction - .65) / .35))
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawImage(panel, self._contraction_frame)
                return
            if self.page == "modes":
                if placement is not None and placement.mode == "finder":
                    painter.fillPath(clip, placement.color)
                    body = QPainterPath()
                    body.addRoundedRect(placement.content, 32, 32)
                    painter.fillPath(body, QColor("#000000"))
                else:
                    painter.fillRect(panel, QColor("#000000"))
            else:
                painter.fillRect(panel, QColor("#15171c"))
                if not self._background_frame.isNull():
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                    painter.drawImage(panel, self._background_frame)
                if self.login_variant != "classic":
                    self._paint_login_variant(painter)
                    return
                painter.setFont(_entry_font(23))
                painter.setPen(QColor("#f1f2f5"))
                painter.drawText(QRectF(84 if self.back_button.isVisible() else 24, 20, self.width() - 168, 36),
                                 Qt.AlignmentFlag.AlignVCenter, "音乐来源")
                if not self._compact_login_layout() and self.width() >= 720:
                    painter.setFont(_entry_font(13))
                    painter.setPen(QColor("#b8bdc7"))
                    painter.drawText(QRectF(32, 76, self.width() - 64, 24), Qt.AlignmentFlag.AlignVCenter,
                                     "连接你的音乐账号，继续收听收藏与歌单")
                if self._expanded > .05:
                    pane = self._source_pane_rect()
                    sx = self._background_frame.width() / max(1, self.width())
                    sy = self._background_frame.height() / max(1, self.height())
                    source_rect = QRectF(pane.x() * sx, pane.y() * sy, pane.width() * sx, pane.height() * sy)
                    paint_liquid_glass(painter, pane, radius=20, backdrop=self._background_frame,
                                       backdrop_rect=source_rect, refractive=True,
                                       cache_token=(id(self), "login-pane"))
                    if self._expanded > .85:
                        if self._account is not None:
                            avatar, name_rect, _action = self._account_content_rects()
                            source = next(item for item in ENTRY_SOURCES if item.source_id == self._account.source_id)
                            painter.setPen(QPen(QColor(source.color), 6))
                            painter.setBrush(QColor("#343740"))
                            painter.drawEllipse(avatar)
                            if not self._account_image.isNull():
                                painter.save()
                                avatar_clip = QPainterPath()
                                avatar_clip.addEllipse(avatar.adjusted(3, 3, -3, -3))
                                painter.setClipPath(avatar_clip, Qt.ClipOperation.IntersectClip)
                                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                                side = min(self._account_image.width(), self._account_image.height())
                                crop = QRectF((self._account_image.width() - side) / 2,
                                              (self._account_image.height() - side) / 2, side, side)
                                painter.drawImage(avatar, self._account_image, crop)
                                painter.restore()
                            painter.setFont(_entry_font(18 if self._compact_login_layout() else 22))
                            painter.setPen(QColor("#f4f5f8"))
                            name = painter.fontMetrics().elidedText(self._account.nickname or "已登录账号", Qt.TextElideMode.ElideRight, round(name_rect.width()))
                            painter.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter, name)
                            caption, ink = self._account_caption(source)
                            painter.setFont(_entry_font(13))
                            painter.setPen(ink)
                            painter.drawText(QRectF(name_rect.x(), name_rect.bottom() + 4, name_rect.width(), 24),
                                             Qt.AlignmentFlag.AlignVCenter, caption)
                            return
                        qr, detail = self._login_content_rects()
                        source = next((item for item in ENTRY_SOURCES if item.source_id == self.selected_source), ENTRY_SOURCES[0])
                        if not self._qr_image.isNull():
                            # Keep service pixels exact and high contrast. The
                            # glass material surrounds, never blurs, the QR.
                            painter.setPen(QPen(QColor(255, 255, 255, 190), 1))
                            painter.setBrush(QColor("#ffffff"))
                            painter.drawRoundedRect(qr, 16, 16)
                            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                            painter.drawImage(qr.adjusted(12, 12, -12, -12), self._qr_image)
                        else:
                            # A failure must not keep pretending it is waiting
                            # for a live QR. Match the actual backend state.
                            painter.setPen(QPen(QColor(255, 255, 255, 45), 1))
                            painter.setBrush(QColor(255, 255, 255, 9))
                            painter.drawRoundedRect(qr, 16, 16)
                            icon = QRectF(qr.center().x() - 22, qr.center().y() - 46, 44, 44)
                            paint_music_source_icon(painter, icon, source.source_id)
                            painter.setFont(_entry_font(14))
                            painter.setPen(QColor("#c5cad5"))
                            painter.drawText(QRectF(qr.x() + 10, qr.center().y() + 12, qr.width() - 20, 48),
                                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                                             self._login_placeholder_text())
                        title_rect, helper_rect, _message_rect, _action = self._login_detail_geometry()
                        painter.setFont(_entry_font(18 if self._compact_login_layout() else 20))
                        painter.setPen(QColor("#f4f5f8"))
                        title = source.name if title_rect.width() < 150 else f"连接{source.name}"
                        detail_alignment = Qt.AlignmentFlag.AlignVCenter | (
                            Qt.AlignmentFlag.AlignHCenter if self._stacked_login_layout() else Qt.AlignmentFlag.AlignLeft)
                        painter.drawText(title_rect, detail_alignment,
                                         painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight,
                                                                          round(title_rect.width())))
                        painter.setFont(_entry_font(12 if self._compact_login_layout() else 13))
                        painter.setPen(QColor("#b8bdc7"))
                        if self.login_state in {"starting", "loading"}:
                            helper = "正在确认账号与登录服务。\n你也可以选择其他平台。"
                        elif self.login_state == "scanned":
                            helper = "已扫码，在手机上确认后\n即可进入音乐库。"
                        elif self._qr_image.isNull():
                            helper = "请重试连接，或在上方\n选择其他音乐来源。"
                        else:
                            helper = f"打开{source.name} App\n扫描二维码登录"
                        if helper_rect.width() < 150:
                            # Minimum-width pages have two short helper lines;
                            # preserve the separate backend-status/action rows.
                            helper = ("确认账号与\n登录服务…" if self.login_state in {"starting", "loading"}
                                      else "请在手机上\n确认登录" if self.login_state == "scanned"
                                      else "请重试连接" if self._qr_image.isNull()
                                      else "打开音乐 App\n扫描二维码")
                        painter.drawText(helper_rect,
                                         detail_alignment | Qt.TextFlag.TextWordWrap, helper)
