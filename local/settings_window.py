from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QRectF, QRunnable, QSize, QStringListModel, Qt, QThreadPool, QTimer, QUrl, QVariantAnimation, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, QRegion
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QCompleter,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from local.ios_widgets import IosSwitch
from local.liquid_glass import GlassPushButton
from local.control_hover import install_container_hover, install_control_hover, install_item_hover
from local.branding import APP_NAME, install_custom_icon, resolve_icon_paths
from local.mac_style import (
    mac_preferences_stylesheet,
    mac_preferences_theme,
    mac_sidebar_icon,
    mac_sidebar_icon_colors,
    mac_traffic_lights,
)
from local.no_wheel_widgets import NoWheelComboBox, NoWheelSlider, NoWheelSpinBox
from local.music_lyric_motion import SPARSE_MIN_SECONDS_PER_UNIT, normalize_sparse_seconds_per_unit
from local.music_surface_palette import solid_album_ink_color
from local.settings_store import (
    DEFAULT_MUSIC_AMBIENCE_VALUES,
    DEFAULT_SETTINGS,
    DEFAULT_MUSIC_APP_PROFILES,
    DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES,
    MUSIC_READING_GRADIENT_DEFAULT_STEPS,
    MUSIC_READING_LIFT_DEFAULT_LEVEL,
    MUSIC_READING_STRATEGY_DEFAULT,
    MYDOCKFINDER_LISTEN_PROCESS_CHOICES,
    PLACEMENT_MODE_LABELS,
    normalize_music_reading_gradient_steps,
    normalize_music_reading_lift_level,
    normalize_music_reading_strategy,
    normalize_music_ambience_style,
    normalize_mydockfinder_listener_process_names,
    normalize_finder_bar_style,
    placement_mode_label,
)
from local.startup import StartupManager, build_startup_command, normalize_startup_mode
from local.ui_theme import (
    app_dark_mode,
    system_dark_mode,
    is_theme_change_event,
    music_ambience_style_for_theme,
    music_ambience_theme_category,
    style_combo_popup,
)
from local.diagnostics_presentation import DIAGNOSTIC_FIELD_ROWS, format_settings_diagnostics


class _MusicCacheClearSignals(QObject):
    finished = Signal(str, object)


class _MusicCacheClearJob(QRunnable):
    def __init__(self, clear_cache, kind: str):
        super().__init__()
        self.clear_cache = clear_cache
        self.kind = kind
        self.signals = _MusicCacheClearSignals()

    def run(self):
        try:
            result = self.clear_cache(self.kind)
        except Exception as exc:
            result = {"deleted_files": 0, "deleted_bytes": 0, "errors": [str(exc)]}
        self.signals.finished.emit(self.kind, result)


class ColorButton(GlassPushButton):
    def __init__(self, color: str, callback, parent=None):
        super().__init__(parent)
        self._callback = callback
        self._color = "#46D6FF"
        self.setFixedSize(34, 34)
        self.clicked.connect(self._choose)
        self.set_color(color)

    def color(self) -> str:
        return self._color

    def set_color(self, color: str):
        qcolor = QColor(str(color))
        if not qcolor.isValid():
            qcolor = QColor("#46D6FF")
        self._color = qcolor.name().upper()
        self.setGlassLight(qcolor.lightness() > 155)
        self.setProperty("glassRadius", 17.0)
        self.setStyleSheet(
            f"QPushButton {{ border: 2px solid #ffffff; border-radius: 17px; background: {self._color}; }}"
            "QPushButton:hover { border-color: #2f7df6; }"
        )

    def _choose(self):
        color = QColorDialog.getColor(QColor(self._color), self, "选择颜色")
        if color.isValid():
            self.set_color(color.name())
            self._callback()


class StatusLamp(QWidget):
    def __init__(self, active: bool, text: str = "", parent=None):
        super().__init__(parent)
        self._active = bool(active)
        self._text = str(text or "")
        self.setObjectName("StatusLamp")
        self.setFixedSize(8, 8)
        self.setToolTip(self._text)

    def set_active(self, active: bool, text: str = ""):
        self._active = bool(active)
        self._text = str(text or "")
        self.setToolTip(self._text)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_lamp(painter)
        painter.end()

    def _paint_lamp(self, painter: QPainter):
        rect = QRectF(self.rect()).adjusted(0.8, 0.8, -0.8, -0.8)
        base = QColor("#1fc95b" if self._active else "#ff453a")
        shadow = QColor(base)
        shadow.setAlpha(80 if self._active else 64)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow)
        painter.drawEllipse(rect.adjusted(-0.6, -0.6, 0.6, 0.6))

        radial = QRadialGradient(rect.center().x() - rect.width() * 0.25, rect.center().y() - rect.height() * 0.32, rect.width() * 0.78)
        highlight = QColor("#f5fff7" if self._active else "#fff1ef")
        highlight.setAlpha(235)
        edge = QColor(base.darker(150))
        edge.setAlpha(245)
        radial.setColorAt(0.0, highlight)
        radial.setColorAt(0.28, base.lighter(118))
        radial.setColorAt(1.0, edge)
        painter.setBrush(radial)
        painter.drawEllipse(rect)

        gleam = QRectF(rect.left() + rect.width() * 0.2, rect.top() + rect.height() * 0.15, rect.width() * 0.34, rect.height() * 0.26)
        painter.setBrush(QColor(255, 255, 255, 190))
        painter.drawEllipse(gleam)


class MusicAmbiencePreview(QWidget):
    DEFAULT_VALUES = {
        "ambient_intensity": 0.62,
        "music_flow_intensity": 0.74,
        "compact_duotone_balance": 58,
        "expanded_duotone_balance": 58,
        "diffraction_frequency": 0.46,
    }
    VALID_STYLES = set(DEFAULT_MUSIC_AMBIENCE_VALUES)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._style = "sea_level"
        self._values = dict(self.DEFAULT_VALUES)
        self._album_color = QColor("#16BCFF")
        self._secondary_color = QColor("#FF3A94")
        self._yaoguang_outer_color = QColor("#6C35F2")
        self._yaoguang_blur_strength = 58
        self._yaoguang_color_source = "custom"
        self._yaoguang_album_inverse_enabled = False
        self._playing = True
        self.setObjectName("MusicAmbiencePreview")
        self.setFixedHeight(58)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(320, 58)

    def set_preview_state(
        self,
        style: str,
        values: dict[str, object] | None = None,
        album_color: QColor | str | None = None,
        secondary_color: QColor | str | None = None,
        yaoguang_outer_color: QColor | str | None = None,
        yaoguang_blur_strength: int | None = None,
        yaoguang_color_source: str | None = None,
        yaoguang_album_inverse_enabled: bool | None = None,
        playing: bool = True,
    ):
        self._style = normalize_music_ambience_style(style)
        merged = dict(self.DEFAULT_VALUES)
        if isinstance(values, dict):
            for key in merged:
                if key in values:
                    merged[key] = values[key]
        self._values = merged
        self._album_color = self._safe_color(album_color, "#16BCFF")
        self._secondary_color = self._safe_color(secondary_color, "#FF3A94")
        self._yaoguang_outer_color = self._safe_color(yaoguang_outer_color, "#6C35F2")
        if yaoguang_blur_strength is not None:
            self._yaoguang_blur_strength = max(0, min(200, int(yaoguang_blur_strength)))
        if yaoguang_color_source is not None:
            value = str(yaoguang_color_source or "custom").strip().lower()
            self._yaoguang_color_source = "album" if value == "album" else "custom"
        if yaoguang_album_inverse_enabled is not None:
            self._yaoguang_album_inverse_enabled = bool(yaoguang_album_inverse_enabled)
        self._playing = bool(playing)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(7.0, 5.0, -7.0, -5.0)
        radius = min(20.0, rect.height() * 0.42)
        painter.setPen(Qt.PenStyle.NoPen)
        shadow = QColor(0, 0, 0, 42)
        painter.setBrush(shadow)
        painter.drawRoundedRect(rect.adjusted(0.0, 2.0, 0.0, 3.0), radius, radius)

        style = self._style
        if style == "system":
            style = "pure_black" if system_dark_mode() else "pure_white"
        if style == "pure_white":
            self._paint_pure_white(painter, rect, radius)
        elif style == "pure_black":
            self._paint_pure_black(painter, rect, radius)
        elif style == "solid_album":
            self._paint_solid_album(painter, rect, radius)
        elif style == "yaoguang":
            self._paint_yaoguang(painter, rect, radius)
        elif style == "bilateral_diffraction":
            self._paint_bilateral_diffraction(painter, rect, radius)
        elif style == "duotone_full":
            self._paint_full_duotone(painter, rect, radius)
        else:
            self._paint_sea_level(painter, rect, radius)
        self._paint_preview_content(painter, rect, radius, style)
        self._paint_border(painter, rect, radius, style == "pure_white")
        painter.end()

    def _paint_sea_level(self, painter: QPainter, rect: QRectF, radius: float):
        path = self._rounded_path(rect, radius)
        painter.fillPath(path, self._dark_body_gradient(rect))
        painter.save()
        painter.setClipPath(path)
        album = self._album_color.lighter(120)
        secondary = self._secondary_color.lighter(118)
        bridge = self._mix_color(album, secondary, 0.45)
        intensity = self._float_value("ambient_intensity", 0.62)
        flow = self._float_value("music_flow_intensity", 0.74)

        horizon_y = rect.top() + rect.height() * 0.50
        sea = QLinearGradient(0, rect.top(), 0, rect.bottom())
        sea.setColorAt(0.00, QColor(album.red(), album.green(), album.blue(), 0))
        sea.setColorAt(0.44, QColor(bridge.red(), bridge.green(), bridge.blue(), int(42 + 62 * intensity)))
        sea.setColorAt(0.70, QColor(album.red(), album.green(), album.blue(), int(74 + 78 * intensity)))
        sea.setColorAt(1.00, QColor(secondary.red(), secondary.green(), secondary.blue(), int(42 + 58 * intensity)))
        painter.fillRect(rect, sea)

        phase = 0.65 + flow * 1.8
        wave = QPainterPath(QPointF(rect.left(), horizon_y))
        count = 18
        for index in range(count + 1):
            progress = index / count
            x = rect.left() + rect.width() * progress
            y = horizon_y + math.sin(progress * math.tau * 1.4 + phase) * rect.height() * 0.045
            wave.lineTo(QPointF(x, y))
        wave.lineTo(QPointF(rect.right(), rect.bottom()))
        wave.lineTo(QPointF(rect.left(), rect.bottom()))
        wave.closeSubpath()
        wave_color = QColor(album.red(), album.green(), album.blue(), int(92 + 66 * intensity))
        painter.fillPath(wave, wave_color)
        painter.setPen(QPen(QColor(255, 255, 255, 36), 1.0))
        painter.drawLine(QPointF(rect.left() + 18, horizon_y), QPointF(rect.right() - 18, horizon_y + 1.0))
        painter.restore()

    def _paint_full_duotone(self, painter: QPainter, rect: QRectF, radius: float):
        path = self._rounded_path(rect, radius)
        painter.save()
        painter.setClipPath(path)
        album = self._album_color.lighter(126)
        secondary = self._secondary_color.lighter(116)
        bridge = self._mix_color(album, secondary, 0.50)
        balance = max(0.24, min(0.76, self._percent_value("compact_duotone_balance", 58.0) / 100.0))

        base = QLinearGradient(0, rect.top(), 0, rect.bottom())
        base.setColorAt(0.00, QColor(secondary.red(), secondary.green(), secondary.blue(), 230))
        base.setColorAt(max(0.10, balance - 0.08), QColor(secondary.red(), secondary.green(), secondary.blue(), 218))
        base.setColorAt(balance, QColor(bridge.red(), bridge.green(), bridge.blue(), 238))
        base.setColorAt(min(0.92, balance + 0.10), QColor(album.red(), album.green(), album.blue(), 226))
        base.setColorAt(1.00, QColor(album.red(), album.green(), album.blue(), 238))
        painter.fillRect(rect, base)

        flow = self._float_value("music_flow_intensity", 0.74)
        self._paint_mist(painter, QPointF(rect.left() + rect.width() * 0.22, rect.top() + rect.height() * 0.36), rect.width() * (0.33 + flow * 0.10), rect.height() * 0.72, secondary, 82)
        self._paint_mist(painter, QPointF(rect.left() + rect.width() * 0.80, rect.top() + rect.height() * 0.72), rect.width() * (0.34 + flow * 0.09), rect.height() * 0.70, album, 90)
        self._paint_mist(painter, QPointF(rect.center().x(), rect.center().y()), rect.width() * 0.30, rect.height() * 0.52, bridge, 52)
        sheen = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
        sheen.setColorAt(0.00, QColor(255, 255, 255, 22))
        sheen.setColorAt(0.44, QColor(255, 255, 255, 0))
        sheen.setColorAt(1.00, QColor(255, 255, 255, 18))
        painter.fillRect(rect, sheen)
        painter.restore()

    def _paint_bilateral_diffraction(self, painter: QPainter, rect: QRectF, radius: float):
        path = self._rounded_path(rect, radius)
        painter.fillPath(path, QColor(0, 0, 0, 255))
        painter.save()
        painter.setClipPath(path)
        album = self._album_color.lighter(132)
        secondary = self._secondary_color.lighter(125)
        frequency = self._float_value("diffraction_frequency", 0.46)
        intensity = self._float_value("ambient_intensity", 0.62)
        ring_count = max(5, min(12, int(round(5 + frequency * 7))))

        left_center = QPointF(rect.left() + rect.width() * 0.06, rect.center().y())
        right_center = QPointF(rect.right() - rect.width() * 0.06, rect.center().y())
        self._paint_mist(painter, left_center, rect.width() * 0.36, rect.height() * 1.18, album, int(72 + intensity * 80))
        self._paint_mist(painter, right_center, rect.width() * 0.36, rect.height() * 1.18, secondary, int(72 + intensity * 80))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for index in range(ring_count):
            progress = index / max(1, ring_count - 1)
            alpha = int((92 - progress * 58) * (0.68 + intensity * 0.32))
            width = rect.width() * (0.18 + progress * 0.48)
            height = rect.height() * (0.36 + progress * 1.05)
            painter.setPen(QPen(QColor(album.red(), album.green(), album.blue(), alpha), 1.0))
            painter.drawEllipse(QRectF(left_center.x() - width * 0.5, left_center.y() - height * 0.5, width, height))
            painter.setPen(QPen(QColor(secondary.red(), secondary.green(), secondary.blue(), alpha), 1.0))
            painter.drawEllipse(QRectF(right_center.x() - width * 0.5, right_center.y() - height * 0.5, width, height))
        center = QLinearGradient(rect.left(), rect.center().y(), rect.right(), rect.center().y())
        center.setColorAt(0.00, QColor(album.red(), album.green(), album.blue(), 64))
        center.setColorAt(0.50, QColor(255, 255, 255, 18))
        center.setColorAt(1.00, QColor(secondary.red(), secondary.green(), secondary.blue(), 64))
        painter.fillRect(rect.adjusted(0, rect.height() * 0.40, 0, -rect.height() * 0.40), center)
        painter.restore()

    def _paint_pure_black(self, painter: QPainter, rect: QRectF, radius: float):
        path = self._rounded_path(rect, radius)
        painter.fillPath(path, QColor(0, 0, 0, 255))
        painter.save()
        painter.setClipPath(path)
        sheen = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
        sheen.setColorAt(0.00, QColor(255, 255, 255, 18))
        sheen.setColorAt(0.38, QColor(255, 255, 255, 4))
        sheen.setColorAt(1.00, QColor(255, 255, 255, 10))
        painter.fillRect(rect, sheen)
        painter.setPen(QPen(QColor(255, 255, 255, 26), 1.0))
        painter.drawLine(QPointF(rect.left() + 18, rect.top() + 9), QPointF(rect.right() - 18, rect.top() + 9))
        painter.restore()

    def _paint_solid_album(self, painter: QPainter, rect: QRectF, radius: float):
        path = self._rounded_path(rect, radius)
        painter.fillPath(path, self._album_color)

    def _yaoguang_effective_preview_color(self) -> QColor:
        if str(getattr(self, "_yaoguang_color_source", "custom")) == "album":
            color = QColor(self._album_color)
            hue, saturation, value, _alpha = color.getHsvF()
            if hue >= 0 and saturation >= 0.16 and 0.18 < value < 0.98:
                if bool(getattr(self, "_yaoguang_album_inverse_enabled", False)):
                    hue = (hue + 0.5) % 1.0
                return self._yaoguang_preview_color(QColor.fromHsvF(hue, saturation, value, 1.0))
        return self._yaoguang_preview_color(self._yaoguang_outer_color)

    def _paint_yaoguang_soft_rect(self, painter: QPainter, rect: QRectF, radius: float, color: QColor, softness: float):
        strength = max(0.0, min(2.0, float(softness)))
        if strength > 0.0:
            normalized = min(1.0, strength)
            extra = max(0.0, strength - 1.0)
            spread = min(rect.width(), rect.height()) * (0.10 + 0.28 * normalized + 0.20 * extra)
            passes = max(4, min(26, int(round(4 + normalized * 14 + extra * 8))))
            for step in range(passes, 0, -1):
                t = step / passes
                grow = spread * t
                glow = QColor(color)
                glow.setAlpha(max(2, int(color.alpha() * (0.08 + 0.13 * normalized + 0.05 * extra) * (1.0 - t * 0.68))))
                glow_rect = rect.adjusted(-grow, -grow, grow, grow)
                painter.fillPath(self._rounded_path(glow_rect, radius + grow), glow)
        painter.fillPath(self._rounded_path(rect, radius), color)

    def _paint_yaoguang(self, painter: QPainter, rect: QRectF, radius: float):
        path = self._rounded_path(rect, radius)
        outer = self._yaoguang_effective_preview_color()
        hue, saturation, value, _alpha = outer.getHsvF()
        if hue < 0:
            hue = 0.72
        mid = QColor.fromHsvF(hue, max(0.40, saturation * 0.62), min(0.98, max(0.74, value + 0.16)), 1.0)
        pale = QColor.fromHsvF(hue, max(0.16, saturation * 0.28), 0.93, 1.0)

        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(path, QColor(2, 1, 5, 255))

        frame = max(4.0, min(rect.width(), rect.height()) * 0.17)
        inner = rect.adjusted(frame, frame, -frame, -frame)
        inner_radius = min(inner.height() / 2.0, max(2.0, radius - frame))
        local_mid = inner.adjusted(inner.width() * 0.085, 0.0, -inner.width() * 0.085, 0.0)
        local_pale = inner.adjusted(inner.width() * 0.165, 0.0, -inner.width() * 0.165, 0.0)
        local_radius = min(inner.height() / 2.0, inner_radius)
        softness = max(0, min(200, int(self._yaoguang_blur_strength))) / 100.0
        painter.setClipPath(self._rounded_path(inner, inner_radius))
        self._paint_yaoguang_soft_rect(painter, inner, local_radius, outer, softness * 0.72)
        self._paint_yaoguang_soft_rect(painter, local_mid, local_radius, mid, softness)
        self._paint_yaoguang_soft_rect(painter, local_pale, local_radius, pale, min(1.0, softness * 1.12))
        painter.restore()

    def _paint_pure_white(self, painter: QPainter, rect: QRectF, radius: float):
        path = self._rounded_path(rect, radius)
        base = QLinearGradient(0, rect.top(), 0, rect.bottom())
        base.setColorAt(0.0, QColor(255, 255, 255, 255))
        base.setColorAt(0.62, QColor(247, 249, 252, 255))
        base.setColorAt(1.0, QColor(231, 236, 244, 255))
        painter.fillPath(path, base)
        painter.save()
        painter.setClipPath(path)
        sheen = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
        sheen.setColorAt(0.00, QColor(255, 255, 255, 92))
        sheen.setColorAt(0.58, QColor(225, 229, 236, 18))
        sheen.setColorAt(1.00, QColor(188, 196, 210, 34))
        painter.fillRect(rect, sheen)
        painter.restore()

    def _paint_preview_content(self, painter: QPainter, rect: QRectF, radius: float, style: str):
        light = style in {"pure_white", "yaoguang"}
        text = QColor(28, 30, 34, 210) if light else QColor(255, 255, 255, 224)
        muted = QColor(74, 82, 96, 160) if light else QColor(255, 255, 255, 112)
        if style == "solid_album":
            text = solid_album_ink_color(self._album_color, 246)
            muted = solid_album_ink_color(self._album_color, 150)
            light = text.red() == 0
        cover = QRectF(rect.left() + 12.0, rect.center().y() - 15.0, 30.0, 30.0)
        cover_gradient = QLinearGradient(cover.topLeft(), cover.bottomRight())
        cover_gradient.setColorAt(0.0, self._album_color.lighter(132))
        cover_gradient.setColorAt(1.0, self._secondary_color.lighter(116))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(cover_gradient)
        painter.drawRoundedRect(cover, 8.0, 8.0)
        painter.setPen(QPen(QColor(255, 255, 255, 62) if not light else QColor(0, 0, 0, 20), 1.0))
        painter.drawRoundedRect(cover.adjusted(0.5, 0.5, -0.5, -0.5), 7.5, 7.5)

        text_left = cover.right() + 11.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(text)
        painter.drawRoundedRect(QRectF(text_left, rect.top() + 14.0, rect.width() * 0.30, 5.5), 2.7, 2.7)
        painter.setBrush(muted)
        painter.drawRoundedRect(QRectF(text_left, rect.top() + 26.0, rect.width() * 0.22, 4.5), 2.2, 2.2)

        control_color = QColor(255, 255, 255, 194) if not light else QColor(32, 36, 44, 152)
        painter.setBrush(control_color)
        center_x = rect.right() - 51.0
        for index, height in enumerate((8.0, 15.0, 11.0)):
            bar = QRectF(center_x + index * 7.0, rect.center().y() - height / 2.0, 3.0, height)
            painter.drawRoundedRect(bar, 1.5, 1.5)
        painter.setBrush(QColor(255, 255, 255, 68) if not light else QColor(0, 0, 0, 34))
        painter.drawRoundedRect(QRectF(rect.right() - 22.0, rect.center().y() - 9.0, 8.0, 18.0), 4.0, 4.0)

    def _paint_border(self, painter: QPainter, rect: QRectF, radius: float, light: bool):
        stroke = QColor(255, 255, 255, 46) if not light else QColor(170, 176, 188, 82)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(stroke, 1.0))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

    def _paint_mist(self, painter: QPainter, center: QPointF, radius_x: float, radius_y: float, color: QColor, alpha: int):
        painter.save()
        painter.translate(center)
        painter.scale(max(1.0, radius_x), max(1.0, radius_y))
        glow = QRadialGradient(QPointF(0.0, 0.0), 1.0)
        glow.setColorAt(0.00, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.58)))
        glow.setColorAt(0.28, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.38)))
        glow.setColorAt(0.64, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.14)))
        glow.setColorAt(1.00, QColor(color.red(), color.green(), color.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(-1.0, -1.0, 2.0, 2.0))
        painter.restore()

    def _dark_body_gradient(self, rect: QRectF) -> QLinearGradient:
        body = QLinearGradient(0, rect.top(), 0, rect.bottom())
        body.setColorAt(0.00, QColor(4, 8, 18, 255))
        body.setColorAt(0.46, QColor(7, 13, 26, 255))
        body.setColorAt(1.00, QColor(13, 24, 44, 255))
        return body

    def _rounded_path(self, rect: QRectF, radius: float) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        return path

    def _float_value(self, key: str, fallback: float) -> float:
        try:
            return max(0.0, min(1.0, float(self._values.get(key, fallback))))
        except (TypeError, ValueError):
            return fallback

    def _percent_value(self, key: str, fallback: float) -> float:
        try:
            value = float(self._values.get(key, fallback))
        except (TypeError, ValueError):
            value = fallback
        if value <= 1.0:
            value *= 100.0
        return max(0.0, min(100.0, value))

    def _safe_color(self, value: QColor | str | None, fallback: str) -> QColor:
        color = QColor(value) if value is not None else QColor(fallback)
        return color if color.isValid() else QColor(fallback)

    def _yaoguang_preview_color(self, value: QColor) -> QColor:
        color = QColor(value)
        if not color.isValid():
            color = QColor("#6C35F2")
        hue, saturation, value, _alpha = color.getHsvF()
        if hue < 0 or saturation < 0.16 or value <= 0.18 or value >= 0.98:
            hue, saturation, value, _alpha = QColor("#6C35F2").getHsvF()
        return QColor.fromHsvF(hue, max(0.68, min(0.96, saturation)), max(0.54, min(0.96, value)), 1.0)

    def _mix_color(self, first: QColor, second: QColor, amount: float) -> QColor:
        amount = max(0.0, min(1.0, amount))
        return QColor(
            int(first.red() + (second.red() - first.red()) * amount),
            int(first.green() + (second.green() - first.green()) * amount),
            int(first.blue() + (second.blue() - first.blue()) * amount),
        )


class SmoothScrollArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scroll_mode_name = "ScrollPerPixel"
        self._scroll_target = 0
        self._last_wheel_at = 0.0
        self._scroll_animation = QVariantAnimation(self)
        self._scroll_animation.valueChanged.connect(self._apply_scroll_value)

    def wheelEvent(self, event):
        bar = self.verticalScrollBar()
        delta = int(event.pixelDelta().y() or event.angleDelta().y())
        if delta == 0 or bar.maximum() <= bar.minimum():
            event.ignore()
            return
        now = time.monotonic()
        continuous = now - self._last_wheel_at < 0.18
        self._last_wheel_at = now
        start = int(bar.value())
        if not self._scroll_animation.state() == QVariantAnimation.State.Running:
            self._scroll_target = start
        step = -delta if event.pixelDelta().y() else int(-delta / 120 * 62)
        self._scroll_target = max(bar.minimum(), min(bar.maximum(), self._scroll_target + step))
        self._scroll_animation.stop()
        self._scroll_animation.setStartValue(start)
        self._scroll_animation.setEndValue(self._scroll_target)
        self._scroll_animation.setDuration(135 if continuous else 245)
        self._scroll_animation.setEasingCurve(QEasingCurve.Type.OutQuint)
        self._scroll_animation.start()
        event.accept()

    def _apply_scroll_value(self, value):
        self.verticalScrollBar().setValue(int(value))


class SettingsWindow(QDialog):
    PLUGIN_IDS = ("time", "weather", "agenda")

    FONT_CHOICES = (
        "Alibaba PuHuiTi",
        "Alibaba PuHuiTi Heavy",
        "Noto Serif SC ExtraBold",
        "JiangChengXieSong 700W",
        "JiangChengXieSong 900W",
    )

    SETTINGS_FONT_FALLBACKS = {
        "Alibaba PuHuiTi": ("Alibaba PuHuiTi", "Segoe UI"),
        "Alibaba PuHuiTi Heavy": ("Alibaba PuHuiTi Heavy", "Alibaba PuHuiTi", "Segoe UI"),
        "Noto Serif SC ExtraBold": ("Noto Serif SC ExtraBold", "Noto Serif SC", "Alibaba PuHuiTi", "Segoe UI"),
        "JiangChengXieSong 700W": ("JiangChengXieSong 700W", "JiangChengXieSong", "Alibaba PuHuiTi", "Segoe UI"),
        "JiangChengXieSong 900W": ("JiangChengXieSong 900W", "JiangChengXieSong", "Alibaba PuHuiTi", "Segoe UI"),
    }

    SETTINGS_FONT_WEIGHTS = {
        "Alibaba PuHuiTi Heavy": QFont.Weight.Black,
        "Noto Serif SC ExtraBold": QFont.Weight.ExtraBold,
        "JiangChengXieSong 900W": QFont.Weight.Bold,
    }

    EMPTY_ISLAND_DISPLAY_MODES = (
        ("横条/竖条", "visible_bar"),
        ("指定字段", "text"),
        ("时间", "time"),
    )
    NO_MUSIC_ISLAND_PRIORITIES = (
        ("插件岛", "plugin"),
        ("无岛模式", "empty"),
    )

    MUSIC_AMBIENCE_STYLES = (
        ("海平面", "sea_level"),
        ("纯色背景", "solid_album"),
        ("系统", "system"),
    )
    MUSIC_EXPANDED_LAYOUT_STYLES = (
        ("常规", "standard"),
        ("双板", "dual_panel"),
    )
    COLOR_FILL_BLOCKED_BACKGROUND_STYLES = {"sea_level", "solid_album"}
    COLOR_FILL_BLOCKED_REASON = "海平面与纯色背景下不可用"
    EMBER_COLOR_FILL_BACKGROUND_STYLES = {"pure_black", "pure_white"}
    EMBER_COLOR_FILL_BLOCKED_REASON = "彩色背景下不可用"
    EMBER_COLOR_BACK_GRAY_BACKGROUND_STYLES = {"sea_level", "duotone_full", "solid_album"}
    EMBER_LINE_MODES = (
        ("分行", "split"),
        ("滚动", "scroll"),
    )

    SETTINGS_SIDEBAR_ICON_FILES = {
        "基础设置": {"dark": "基础设置（白）.svg", "light": "基础设置 (黑).svg"},
        "媒体控制": {"dark": "媒体控制（白）.svg", "light": "媒体控制（黑）.svg"},
        "全屏歌词": {"dark": "全屏歌词（白）.svg", "light": "全屏歌词（黑）.svg"},
        "插件设置": {"dark": "插件设置（白）.svg", "light": "插件设置（黑）.svg"},
        "通知设置": {"dark": "通知设置（白）.svg", "light": "通知设置（黑）.svg"},
        "关于我": {"dark": "关于我（白）.svg", "light": "关于我（黑）.svg"},
    }

    SIDE_ISLAND_STYLES = (
        ("当前胶囊", "standard"),
        ("预留样式 A", "scheme_a"),
    )

    PLUGIN_ISLAND_STYLES = (
        ("当前样式", "standard"),
        ("预留样式 A", "scheme_a"),
    )

    TIME_ALARM_SIDE_DISPLAYS = (
        ("显示备注", "note"),
        ("显示时间", "time"),
    )

    PLUGIN_FULLSCREEN_ALERT_STYLES = (
        ("弹幕提醒", "barrage"),
        ("弹窗提醒", "toast"),
        ("全屏提醒", "fullscreen"),
    )

    BARRAGE_DISPLAY_MODES = (
        ("仅全屏", "fullscreen"),
        ("任何时候", "always"),
    )

    MUSIC_PLAYER_MODES = (
        ("网易云音乐", "netease"),
        ("网易云音乐（BetterNCM）", "netease_betterncm"),
        ("QQ音乐", "qq"),
        ("汽水音乐（待适配）", "qishui"),
        ("酷狗音乐（待适配）", "kugou"),
    )

    MUSIC_APP_PROFILE_PANELS = (
        ("netease", "网易云音乐", r"C:\Program Files\NetEase\CloudMusic\cloudmusic.exe"),
        ("qq", "QQ音乐", r"C:\Program Files\Tencent\QQMusic\QQMusic.exe"),
        ("qishui", "汽水音乐", r"C:\Program Files\QishuiMusic\qishui.exe"),
        ("kugou", "酷狗音乐", r"C:\Program Files\KuGou\KGMusic\KuGou.exe"),
    )

    ANIMATION_SPEEDS = (
        ("0.5x", 0.5),
        ("0.75x", 0.75),
        ("1x", 1.0),
        ("2x", 2.0),
    )

    TIMEZONES = (
        ("本地时区", "local"),
        ("中国 UTC+8", "UTC+8"),
        ("日本 UTC+9", "UTC+9"),
        ("美国西部 UTC-8", "UTC-8"),
        ("美国东部 UTC-5", "UTC-5"),
        ("伦敦 UTC+0", "UTC+0"),
    )

    THEME_MODES = (
        ("跟随系统", "system"),
        ("浅色", "light"),
        ("深色", "dark"),
        ("自定时间", "schedule"),
    )

    STARTUP_MODES = (
        ("无", "none"),
        ("Windows 启动项", "registry"),
        ("计划任务（推荐）", "task"),
    )

    LYRIC_FLOW_STYLES = (
        ("波光逐流", "wave_stream"),
        ("格律宋散", "gelv_songscatter"),
        ("专辑岛", "album_island"),
    )

    NO_LYRICS_DISPLAY_MODES = (
        ("显示特定文字", "text"),
        ("专辑岛模式", "album_island"),
    )

    DUAL_LINE_SECONDARY_SOURCES = (
        ("中文翻译", "translation"),
        ("音译 / 罗马音", "transliteration"),
    )

    LYRIC_CHINESE_CONVERSIONS = (
        ("不转换", "off"),
        ("转为简体中文", "simplified"),
        ("转为繁体中文", "traditional"),
    )

    ALBUM_ISLAND_COVER_COUNTS = (
        ("3 张", 3),
        ("5 张", 5),
        ("7 张", 7),
    )

    ALBUM_ISLAND_SECONDARY_STYLES = (
        ("透视", "perspective_dim"),
    )

    LOCATION_MODES = tuple((label, mode) for mode, label in PLACEMENT_MODE_LABELS.items())

    FINDER_BAR_STYLES = (
        ("无背景 · 封面与歌词", "transparent"),
        ("圆角底色", "capsule"),
        ("左右渐隐色条", "fade"),
    )

    TOP_NOTCH_HEIGHT_MODES = (
        ("紧凑", "compact"),
        ("标准", "standard"),
        ("宽松", "spacious"),
    )

    POSITION_PRESETS = (
        ("上次位置", "last"),
        ("顶部居中", "top_center"),
        ("左上角", "top_left"),
        ("右上角", "top_right"),
        ("左下角", "bottom_left"),
        ("右下角", "bottom_right"),
    )

    TASKBAR_CORNER_STYLES = (
        ("Win11 小圆角", "win11"),
        ("Win10 直角", "win10"),
    )

    TASKBAR_AUTO_HIDE_MODES = (
        ("Auto", "auto"),
        ("Enabled", "enabled"),
        ("Disabled", "disabled"),
    )

    COMPACT_ROW_VPAD = 10
    APPEARANCE_ROW_VPAD = 6
    APPEARANCE_LABEL_WIDTH = 100

    def __init__(self, island_widget: QWidget, settings_manager=None, parent=None):
        super().__init__(parent)
        self.island_widget = island_widget
        self.settings = settings_manager
        self._plugin_page_index = -1
        self._barrage_adjust_generation = 0
        self._barrage_page_index = -1
        self._diagnostics_page_index = 3
        self._diagnostic_labels: dict[str, QLabel] = {}
        self._music_cache_clear_job = None
        self._detail_panels: list[tuple[QPushButton, QFrame]] = []
        self._applying_theme = False
        self._window_drag_global = None
        self._window_drag_pos = None
        self._row_vpad_override: int | None = None
        self._row_label_width_override: int | None = None
        self._compact_card_active = False
        self._active_search_page = None
        self._settings_pages: list[dict[str, object]] = []
        self._section_labels: list[tuple[QLabel, int, int]] = []
        self._settings_search_entries: list[dict[str, object]] = []
        self._settings_search_entry_seq = 0
        self._last_settings_page_index = 0
        self._settings_search_page_index = -1
        self._settings_search_completer_model: QStringListModel | None = None
        self.theme_dark_time_rows: list[QWidget] = []
        self.music_background_follow_theme_checkboxes: list[IosSwitch] = []
        self.tasker_follow_taskbar_color_checkboxes: list[IosSwitch] = []
        self.tasker_follow_taskbar_color_hint_labels: list[QLabel] = []
        self.icon_changed_callback = None
        self.setWindowTitle(f"{APP_NAME} 设置")
        self.setMinimumSize(688, 496)
        self.setWindowFlags(
            (self.windowFlags() | Qt.WindowType.FramelessWindowHint)
            & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._build_ui()
        self._update_theme_schedule_controls()
        self._update_music_background_follow_dependency_controls()
        self._apply_settings_font()
        install_container_hover(self)
        install_item_hover(self.nav)

    def _theme(self) -> dict[str, str]:
        return mac_preferences_theme()

    def _apply_theme(self):
        if getattr(self, "_applying_theme", False):
            return
        self._applying_theme = True
        t = self._theme()
        try:
            self.setStyleSheet(mac_preferences_stylesheet(t))
            self._refresh_glass_buttons()
        finally:
            self._applying_theme = False

    def _refresh_glass_buttons(self):
        light = not self._settings_dark_mode()
        for button in self.findChildren(GlassPushButton):
            if isinstance(button, ColorButton):
                continue
            button.setGlassLight(light and button.objectName() not in {"Primary", "Danger"})
            button.setProperty("glassRadius", 5.0)
            if button.objectName() == "DetailToggle":
                button.setProperty("glassRadius", 7.0)

    def _section_style(self, top: int = 12, bottom: int = 8) -> str:
        return f"font-size: 15px; font-weight: 700; color: {self._theme()['section_text']}; padding: {top}px 0 {bottom}px 0;"

    def _register_section_label(self, label: QLabel, top: int = 12, bottom: int = 8) -> QLabel:
        self._section_labels.append((label, int(top), int(bottom)))
        label.setStyleSheet(self._section_style(top, bottom))
        return label

    def _refresh_section_labels(self):
        for label, top, bottom in getattr(self, "_section_labels", []):
            label.setStyleSheet(self._section_style(top, bottom))

    def _hint_style(self, top: int = 2, bottom: int = 8) -> str:
        return f"color: {self._theme()['muted']}; padding: {top}px 0 {bottom}px 0;"

    def changeEvent(self, event):
        if getattr(self, "_applying_theme", False):
            super().changeEvent(event)
            return
        if is_theme_change_event(event):
            self._apply_theme()
            self._refresh_section_labels()
            self._refresh_settings_sidebar_icons()
            self._sync_music_background_to_theme()
            for combo in self.findChildren(QComboBox):
                self._style_combo_popup(combo)
        super().changeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_rounded_window_mask()

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_rounded_window_mask()

    def _apply_rounded_window_mask(self):
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 14.0, 14.0)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def _settings_dark_mode(self) -> bool:
        getter = self.settings.get if self.settings is not None and hasattr(self.settings, "get") else None
        return app_dark_mode(getter)

    def _settings_sidebar_icon_path(self, title: str) -> Path | None:
        files = self.SETTINGS_SIDEBAR_ICON_FILES.get(str(title or ""))
        if not files:
            return None
        theme_key = "dark" if self._settings_dark_mode() else "light"
        filename = files.get(theme_key)
        if not filename:
            return None
        return Path(__file__).resolve().parents[1] / "assets" / "seeting" / filename

    def _settings_sidebar_icon(self, title: str, fallback_icon: QIcon) -> tuple[QIcon, str]:
        path = self._settings_sidebar_icon_path(title)
        if path is not None and path.exists():
            return QIcon(str(path)), str(path)
        return fallback_icon, ""

    def _refresh_settings_sidebar_icons(self):
        if not getattr(self, "_settings_pages", None):
            return
        for page in self._settings_pages:
            fallback = page.get("fallback_icon")
            if not isinstance(fallback, QIcon):
                fallback = page.get("icon")
            if not isinstance(fallback, QIcon):
                continue
            icon, icon_path = self._settings_sidebar_icon(str(page.get("title", "")), fallback)
            page["icon"] = icon
            page["icon_path"] = icon_path
        for entry in self._settings_search_entries:
            try:
                page_index = int(entry.get("page_index", -1))
            except (TypeError, ValueError):
                continue
            if 0 <= page_index < len(self._settings_pages):
                entry["icon"] = self._settings_pages[page_index].get("icon")
        if hasattr(self, "nav"):
            for row in range(self.nav.count()):
                item = self.nav.item(row)
                if item is None:
                    continue
                try:
                    page_index = int(item.data(Qt.ItemDataRole.UserRole))
                except (TypeError, ValueError):
                    continue
                if 0 <= page_index < len(self._settings_pages):
                    icon = self._settings_pages[page_index].get("icon")
                    if isinstance(icon, QIcon):
                        item.setIcon(icon)

    def _build_ui(self):
        self._apply_theme()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        shell = QFrame()
        shell.setObjectName("MacWindowShell")
        outer.addWidget(shell)

        root = QHBoxLayout(shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        side = QFrame()
        side.setObjectName("MacSidebar")
        side.setFixedWidth(157)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(8, 8, 8, 10)
        side_layout.setSpacing(6)

        side_layout.addWidget(mac_traffic_lights(self), 0, Qt.AlignmentFlag.AlignLeft)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("MacSearch")
        self.search_input.setPlaceholderText("搜索")
        self._settings_search_completer_model = QStringListModel(self)
        self.search_completer = QCompleter(self._settings_search_completer_model, self.search_input)
        self.search_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.search_completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.search_completer.setMaxVisibleItems(8)
        self.search_completer.activated.connect(self._apply_settings_search_suggestion)
        self.search_input.setCompleter(self.search_completer)
        self.search_input.textChanged.connect(self._filter_settings_nav)
        side_layout.addWidget(self.search_input)

        self.nav = QListWidget()
        self.nav.setObjectName("MacSidebarList")
        self.nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.nav.setIconSize(QSize(24, 24))
        side_layout.addWidget(self.nav, 1)
        root.addWidget(side)

        content = QWidget()
        content.setObjectName("MacContentPane")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        title_bar = QFrame()
        title_bar.setObjectName("MacTitleBar")
        title_bar.setFixedHeight(42)
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(18, 0, 18, 0)
        title_layout.setSpacing(8)
        self.title_label = QLabel()
        self.title_label.setObjectName("MacTitle")
        title_layout.addWidget(self.title_label)
        title_layout.addStretch(1)
        content_layout.addWidget(title_bar)

        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, 1)
        root.addWidget(content, 1)

        pages = (
            ("基础设置", self._basic_settings_page()),
            ("媒体控制", self._media_page()),
            ("全屏歌词", self._music_surface_lyrics_page()),
            ("关于我", self._about_page()),
        )
        colors = mac_sidebar_icon_colors()
        for index, (name, page) in enumerate(pages):
            fallback_icon = mac_sidebar_icon(colors[index % len(colors)], name[:1])
            icon, icon_path = self._settings_sidebar_icon(name, fallback_icon)
            self.stack.addWidget(page)
            self._settings_pages.append({"title": name, "page": page, "icon": icon, "fallback_icon": fallback_icon, "icon_path": str(icon_path)})
            self._register_settings_search_entry(name, page, terms=(name,), page=page, page_index=index, priority=12)
            for entry in getattr(page, "_settings_search_entries", []):
                entry["page_index"] = index
                entry["page_title"] = name
                entry["icon"] = icon
                if entry not in self._settings_search_entries:
                    self._settings_search_entries.append(entry)
        self.settings_search_page = self._create_settings_search_page()
        self._settings_search_page_index = self.stack.addWidget(self.settings_search_page)
        self.nav.currentRowChanged.connect(self._select_settings_page)
        self._rebuild_settings_nav("")
        self.nav.setCurrentRow(0)
        self._refresh_glass_buttons()

    def _create_settings_search_page(self) -> QScrollArea:
        scroll = SmoothScrollArea()
        scroll.setObjectName("MacContentScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.verticalScrollBar().setSingleStep(24)
        page = QWidget()
        page.setObjectName("MacScrollBody")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 14, 24, 22)
        layout.setSpacing(12)
        scroll.setWidget(page)
        self.settings_search_results_layout = layout
        return scroll

    def _select_settings_page(self, row: int):
        item = self.nav.item(row) if row >= 0 else None
        page_index = item.data(Qt.ItemDataRole.UserRole) if item is not None else row
        try:
            page_index = int(page_index)
        except (TypeError, ValueError):
            return
        if page_index < 0 or page_index >= len(getattr(self, "_settings_pages", [])):
            return
        self._last_settings_page_index = page_index
        self.stack.setCurrentIndex(page_index)
        self.title_label.setText(str(self._settings_pages[page_index].get("title", "")))
        entry_id = item.data(Qt.ItemDataRole.UserRole + 1) if item is not None else None
        if entry_id:
            QTimer.singleShot(0, lambda page_index=page_index, entry_id=str(entry_id): self._scroll_to_search_entry(page_index, entry_id))
        self._settings_page_changed(page_index)

    def _filter_settings_nav(self, text: str):
        query = str(text or "").strip()
        self._update_settings_search_suggestions(query)
        if query:
            self._show_settings_search_results(query)
        else:
            self._restore_settings_page_after_search()

    def _register_settings_search_entry(
        self,
        title: str,
        widget: QWidget | None,
        terms=(),
        page: QWidget | None = None,
        page_index: int | None = None,
        priority: int = 0,
    ):
        search_page = page or getattr(self, "_active_search_page", None)
        if search_page is None:
            return
        self._settings_search_entry_seq += 1
        term_values = [str(title or "")]
        for term in terms or ():
            text = str(term or "").strip()
            if text:
                term_values.append(text)
        entry = {
            "id": f"settings-search-{self._settings_search_entry_seq}",
            "title": str(title or "").strip(),
            "terms": tuple(dict.fromkeys(term_values)),
            "widget": widget,
            "page": search_page,
            "page_index": page_index,
            "page_title": "",
            "icon": None,
            "order": self._settings_search_entry_seq,
            "priority": int(priority),
        }
        entries = getattr(search_page, "_settings_search_entries", None)
        if entries is None:
            entries = []
            search_page._settings_search_entries = entries
        entries.append(entry)
        if page_index is not None:
            self._settings_search_entries.append(entry)

    def _settings_search_widget_terms(self, widget: QWidget | None) -> list[str]:
        if widget is None:
            return []
        terms: list[str] = []

        def add_text(value):
            text = str(value or "").strip()
            if text:
                terms.append(text)

        if isinstance(widget, QComboBox):
            for index in range(widget.count()):
                add_text(widget.itemText(index))
                add_text(widget.itemData(index))
        elif isinstance(widget, QLineEdit):
            add_text(widget.placeholderText())
            add_text(widget.text())
        elif isinstance(widget, IosSwitch):
            add_text(getattr(widget, "_text", ""))
        elif isinstance(widget, (QLabel, QPushButton)):
            add_text(widget.text())
        for child in widget.findChildren(QWidget):
            if isinstance(child, QComboBox):
                for index in range(child.count()):
                    add_text(child.itemText(index))
                    add_text(child.itemData(index))
            elif isinstance(child, QLineEdit):
                add_text(child.placeholderText())
                add_text(child.text())
            elif isinstance(child, IosSwitch):
                add_text(getattr(child, "_text", ""))
            elif isinstance(child, (QLabel, QPushButton)):
                add_text(child.text())
        return terms

    def _normalize_settings_search_text(self, text: str) -> str:
        skip = set(" \t\r\n:_-—–·/\\|,.，。:：;；()（）[]【】{}<>《》\"'`")
        return "".join(char.casefold() for char in str(text or "") if char not in skip)

    def _settings_search_subsequence_score(self, query: str, target: str) -> int:
        positions: list[int] = []
        start = 0
        for char in query:
            found = target.find(char, start)
            if found < 0:
                return 0
            positions.append(found)
            start = found + 1
        if not positions:
            return 0
        spread = positions[-1] - positions[0] + 1
        adjacent = sum(1 for left, right in zip(positions, positions[1:]) if right == left + 1)
        return max(1, 4800 - spread * 18 + adjacent * 42 - len(target))

    def _settings_search_score(self, query: str, target: str) -> int:
        query_norm = self._normalize_settings_search_text(query)
        target_norm = self._normalize_settings_search_text(target)
        if not query_norm or not target_norm:
            return 0
        if query_norm == target_norm:
            return 10000
        if target_norm.startswith(query_norm):
            return 9000 - len(target_norm)
        contains_at = target_norm.find(query_norm)
        if contains_at >= 0:
            return 8200 - contains_at * 24 - len(target_norm)
        return self._settings_search_subsequence_score(query_norm, target_norm)

    def _settings_search_matches(self, query: str) -> list[dict[str, object]]:
        matches: list[tuple[int, int, int, dict[str, object]]] = []
        for entry in getattr(self, "_settings_search_entries", []):
            page_index = entry.get("page_index")
            if page_index is None:
                continue
            score = 0
            for term in entry.get("terms", ()):
                score = max(score, self._settings_search_score(query, str(term)))
            if score <= 0:
                continue
            score += int(entry.get("priority", 0) or 0)
            matches.append((score, -int(entry.get("order", 0) or 0), -int(page_index), entry))
        matches.sort(key=lambda item: (-item[0], -item[1], -item[2]))
        return [entry for _score, _order, _page, entry in matches]

    def _settings_search_suggestions(self, query: str, limit: int = 8) -> list[str]:
        query = str(query or "").strip()
        if not query:
            return []
        candidates: dict[str, tuple[int, int, int, str]] = {}
        for entry in getattr(self, "_settings_search_entries", []):
            priority = int(entry.get("priority", 0) or 0)
            order = int(entry.get("order", 0) or 0)
            for term in entry.get("terms", ()):
                text = str(term or "").strip()
                if not text:
                    continue
                score = self._settings_search_score(query, text)
                if score <= 0:
                    continue
                normalized = self._normalize_settings_search_text(text)
                ranked = (score + priority, -len(text), -order, text)
                if normalized not in candidates or ranked > candidates[normalized]:
                    candidates[normalized] = ranked
        ranked_terms = sorted(candidates.values(), key=lambda item: (-item[0], -item[1], -item[2], item[3]))
        return [text for _score, _length, _order, text in ranked_terms[:limit]]

    def _update_settings_search_suggestions(self, query: str):
        model = getattr(self, "_settings_search_completer_model", None)
        if model is None:
            return
        suggestions = self._settings_search_suggestions(query)
        model.setStringList(suggestions)
        completer = getattr(self, "search_completer", None)
        if query and suggestions and completer is not None and self.search_input.hasFocus():
            completer.complete()

    def _apply_settings_search_suggestion(self, suggestion):
        text = suggestion.data() if hasattr(suggestion, "data") else suggestion
        text = str(text or "").strip()
        if text:
            self.search_input.setText(text)
            self.search_input.setFocus()

    def _settings_search_best_term(self, entry: dict[str, object], query: str) -> str:
        ranked: list[tuple[int, int, str]] = []
        for term in entry.get("terms", ()):
            text = str(term or "").strip()
            if not text:
                continue
            score = self._settings_search_score(query, text)
            if score > 0:
                ranked.append((score, -len(text), text))
        if not ranked:
            return ""
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return ranked[0][2]

    def _clear_settings_search_results(self):
        layout = getattr(self, "settings_search_results_layout", None)
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _populate_settings_search_results(self, query: str, matches: list[dict[str, object]]):
        layout = getattr(self, "settings_search_results_layout", None)
        if layout is None:
            return
        self._clear_settings_search_results()
        summary = QLabel(f"找到 {len(matches)} 个相关设置项")
        summary.setObjectName("SearchResultSummary")
        layout.addWidget(summary)
        if not matches:
            empty = QLabel("没有匹配的设置项")
            empty.setObjectName("SearchResultEmpty")
            layout.addWidget(empty)
            layout.addStretch(1)
            return
        for entry in matches:
            row = QFrame()
            row.setObjectName("SearchResultRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 9, 10, 9)
            row_layout.setSpacing(10)

            text_box = QVBoxLayout()
            text_box.setContentsMargins(0, 0, 0, 0)
            text_box.setSpacing(2)
            title_label = QLabel(str(entry.get("title", "")))
            title_label.setObjectName("SearchResultTitle")
            title_label.setWordWrap(True)
            best_term = self._settings_search_best_term(entry, query)
            page_title = str(entry.get("page_title", "") or "")
            if best_term and best_term != title_label.text():
                meta_text = f"{page_title} · {best_term}" if page_title else best_term
            else:
                meta_text = page_title
            meta_label = QLabel(meta_text)
            meta_label.setObjectName("SearchResultMeta")
            meta_label.setWordWrap(True)
            text_box.addWidget(title_label)
            if meta_text:
                text_box.addWidget(meta_label)
            row_layout.addLayout(text_box, 1)

            open_button = GlassPushButton("打开")
            open_button.setObjectName("SearchResultAction")
            open_button.setGlassLight(not self._settings_dark_mode())
            open_button.setProperty("glassRadius", 5.0)
            entry_id = str(entry.get("id", ""))
            open_button.clicked.connect(lambda _checked=False, item_id=entry_id: self._activate_settings_search_entry(item_id))
            row_layout.addWidget(open_button, 0, Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(row)
        layout.addStretch(1)

    def _show_settings_search_results(self, query: str):
        current_page = self.stack.currentIndex() if hasattr(self, "stack") else -1
        if 0 <= current_page < len(getattr(self, "_settings_pages", [])):
            self._last_settings_page_index = current_page
        matches = self._settings_search_matches(query)
        self._populate_settings_search_results(query, matches)
        if getattr(self, "settings_search_page", None) is not None:
            self.stack.setCurrentWidget(self.settings_search_page)
        self.title_label.setText("搜索结果")

    def _sync_settings_nav_to_page(self, page_index: int):
        if not hasattr(self, "nav"):
            return
        self.nav.blockSignals(True)
        try:
            for row in range(self.nav.count()):
                item = self.nav.item(row)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == page_index:
                    self.nav.setCurrentRow(row)
                    break
        finally:
            self.nav.blockSignals(False)

    def _restore_settings_page_after_search(self):
        current_page = self.stack.currentIndex() if hasattr(self, "stack") else -1
        if 0 <= current_page < len(getattr(self, "_settings_pages", [])):
            page_index = current_page
        else:
            page_index = int(getattr(self, "_last_settings_page_index", 0) or 0)
        page_index = max(0, min(page_index, len(getattr(self, "_settings_pages", [])) - 1))
        self._clear_settings_search_results()
        self._sync_settings_nav_to_page(page_index)
        self.stack.setCurrentIndex(page_index)
        self.title_label.setText(str(self._settings_pages[page_index].get("title", "")))
        self._last_settings_page_index = page_index
        self._settings_page_changed(page_index)

    def _activate_settings_search_entry(self, entry_id: str):
        entry = next((item for item in self._settings_search_entries if str(item.get("id", "")) == str(entry_id)), None)
        if entry is None:
            return
        try:
            page_index = int(entry.get("page_index", 0) or 0)
        except (TypeError, ValueError):
            return
        if page_index < 0 or page_index >= len(getattr(self, "_settings_pages", [])):
            return
        self._last_settings_page_index = page_index
        self._sync_settings_nav_to_page(page_index)
        self.stack.setCurrentIndex(page_index)
        self.title_label.setText(str(self._settings_pages[page_index].get("title", "")))
        self._settings_page_changed(page_index)
        QTimer.singleShot(0, lambda page_index=page_index, entry_id=str(entry_id): self._scroll_to_search_entry(page_index, entry_id))

    def _rebuild_settings_nav(self, query: str = ""):
        current_page = self.stack.currentIndex() if hasattr(self, "stack") else 0
        if current_page < 0 or current_page >= len(getattr(self, "_settings_pages", [])):
            current_page = int(getattr(self, "_last_settings_page_index", 0) or 0)
        self.nav.blockSignals(True)
        self.nav.clear()
        for page_index, page in enumerate(getattr(self, "_settings_pages", [])):
            item = QListWidgetItem(page.get("icon"), str(page.get("title", "")))
            item.setData(Qt.ItemDataRole.UserRole, page_index)
            item.setData(Qt.ItemDataRole.UserRole + 1, "")
            self.nav.addItem(item)
        target_row = max(0, min(self.nav.count() - 1, current_page))
        self.nav.blockSignals(False)
        if self.nav.count() > 0:
            self.nav.setCurrentRow(target_row)

    def _scroll_to_search_entry(self, page_index: int, entry_id: str):
        if page_index < 0 or page_index >= self.stack.count():
            return
        page = self.stack.widget(page_index)
        if not isinstance(page, QScrollArea):
            return
        entry = next((item for item in self._settings_search_entries if str(item.get("id", "")) == str(entry_id)), None)
        if entry is None:
            return
        widget = entry.get("widget")
        body = page.widget()
        if not isinstance(widget, QWidget) or body is None:
            return
        try:
            y = widget.mapTo(body, widget.rect().topLeft()).y()
        except RuntimeError:
            return
        page.verticalScrollBar().setValue(max(0, y - 16))

    def _is_window_drag_area(self, point) -> bool:
        if point.y() > 44 and point.x() > 157:
            return False
        child = self.childAt(point.toPoint())
        if child is None:
            return True
        if isinstance(child, (QLineEdit, QListWidget, QComboBox, QPushButton, IosSwitch, QSlider, QSpinBox)):
            return False
        return True

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._is_window_drag_area(event.position()):
            self._window_drag_global = event.globalPosition().toPoint()
            self._window_drag_pos = self.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._window_drag_global is not None and self._window_drag_pos is not None:
            self.move(self._window_drag_pos + event.globalPosition().toPoint() - self._window_drag_global)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._window_drag_global = None
        self._window_drag_pos = None
        super().mouseReleaseEvent(event)

    def _page(self, title: str) -> QWidget:
        scroll = SmoothScrollArea()
        scroll.setObjectName("MacContentScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.verticalScrollBar().setSingleStep(24)
        page = QWidget()
        page.setObjectName("MacScrollBody")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 14, 24, 22)
        layout.setSpacing(14)
        scroll.setWidget(page)
        scroll._content_layout = layout
        scroll._settings_search_title = title
        scroll._settings_search_entries = []
        self._active_search_page = scroll
        return scroll

    def _row_vpad(self) -> int:
        override = getattr(self, "_row_vpad_override", None)
        if override is not None:
            return max(0, int(override))
        return self.COMPACT_ROW_VPAD

    def _row_label_width(self) -> int:
        override = getattr(self, "_row_label_width_override", None)
        if override is not None:
            return max(80, int(override))
        return 120

    def _begin_compact_appearance_page(self, page: QWidget):
        self._row_vpad_override = self.APPEARANCE_ROW_VPAD
        self._row_label_width_override = self.APPEARANCE_LABEL_WIDTH
        self._compact_card_active = True
        layout = getattr(page, "_content_layout", None)
        if layout is not None:
            layout.setContentsMargins(14, 8, 16, 14)
            layout.setSpacing(10)

    def _end_compact_appearance_page(self):
        self._row_vpad_override = None
        self._row_label_width_override = None
        self._compact_card_active = False

    def _card(self, parent_layout: QVBoxLayout) -> QFrame:
        card = QFrame()
        card.setObjectName("MacCard")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(card)
        if getattr(self, "_compact_card_active", False):
            layout.setContentsMargins(12, 7, 12, 7)
        else:
            layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(3 if getattr(self, "_compact_card_active", False) else 4)
        parent_layout.addWidget(card)
        return card

    def _row(self, layout: QVBoxLayout, title: str, control: QWidget | None):
        if control is None:
            return None
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, self._row_vpad(), 0, self._row_vpad())
        row_layout.setSpacing(12 if getattr(self, "_compact_card_active", False) else 14)
        label = QLabel(title)
        label.setObjectName("RowTitle")
        label.setMinimumWidth(self._row_label_width())
        if control.sizePolicy().horizontalPolicy() != QSizePolicy.Policy.Ignored:
            control.setSizePolicy(QSizePolicy.Policy.Expanding, control.sizePolicy().verticalPolicy())
        row_layout.addWidget(label, 0)
        row_layout.addWidget(control, 1)
        layout.addWidget(row)
        self._register_settings_search_entry(title, row, terms=self._settings_search_widget_terms(control), priority=8)
        return row

    def _path_picker_row(self, layout: QVBoxLayout, title: str, line_edit: QLineEdit, browse_callback, stacked: bool = False):
        container = QWidget()
        container_layout = QHBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(8)
        line_edit.setSizePolicy(QSizePolicy.Policy.Expanding, line_edit.sizePolicy().verticalPolicy())
        browse_button = GlassPushButton("选择")
        browse_button.setObjectName("PathBrowseButton")
        browse_button.clicked.connect(browse_callback)
        container_layout.addWidget(line_edit, 1)
        container_layout.addWidget(browse_button, 0)
        if not stacked:
            self._row(layout, title, container)
            return browse_button

        row = QWidget()
        row.setObjectName("PathPickerRowStacked")
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(0, self._row_vpad(), 0, self._row_vpad())
        row_layout.setSpacing(7)
        label = QLabel(title)
        label.setObjectName("RowTitle")
        row_layout.addWidget(label)
        row_layout.addWidget(container)
        layout.addWidget(row)
        self._register_settings_search_entry(title, row, terms=self._settings_search_widget_terms(container), priority=8)
        return row

    def _section_label(self, text: str, top: int = 12, bottom: int = 8) -> QLabel:
        label = QLabel(text)
        self._register_section_label(label, top, bottom)
        self._register_settings_search_entry(text, label, terms=(text,), priority=3)
        return label

    def _readonly_value(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Value")
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    def _diagnostic_value_label(self) -> QLabel:
        label = QLabel("-")
        label.setObjectName("DiagnosticValue")
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return label

    def _status_indicator(self, active: bool, text: str = "") -> StatusLamp:
        return StatusLamp(active, text or ("可用" if active else "不可用"))

    def _set_status_indicator(self, lamp: StatusLamp, active: bool, text: str = ""):
        lamp.set_active(active, text or ("可用" if active else "不可用"))

    def _set_switch_enabled(self, switch: IosSwitch | None, enabled: bool, reason: str = ""):
        if switch is None:
            return
        if isinstance(switch, IosSwitch):
            switch.set_unavailable_reason("" if enabled else reason)
        switch.setEnabled(enabled)

    def _set_switch_checked(self, switch: IosSwitch | None, checked: bool):
        if switch is None:
            return
        switch.blockSignals(True)
        switch.setChecked(bool(checked))
        switch.blockSignals(False)

    def _set_island_bool_setting(self, attr: str, value: bool):
        setter = getattr(self.island_widget, f"set_{attr}", None)
        if callable(setter):
            setter(bool(value), notify=False)
        else:
            setattr(self.island_widget, attr, bool(value))
            if hasattr(self.island_widget, "update"):
                self.island_widget.update()

    def _indicator_row(self, layout: QVBoxLayout, title: str, indicator: StatusLamp, control: QWidget):
        row_widget = QWidget()
        grid = QGridLayout(row_widget)
        grid.setContentsMargins(0, self._row_vpad(), 0, self._row_vpad())
        grid.setHorizontalSpacing(10 if getattr(self, "_compact_card_active", False) else 12)
        grid.setVerticalSpacing(2)
        grid.setColumnMinimumWidth(0, 12)
        grid.setColumnMinimumWidth(1, self._row_label_width())
        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 0)
        grid.setColumnStretch(2, 1)
        label = QLabel(title)
        label.setObjectName("RowTitle")
        label.setFixedWidth(self._row_label_width())
        control.setSizePolicy(QSizePolicy.Policy.Expanding, control.sizePolicy().verticalPolicy())
        grid.addWidget(indicator, 0, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(label, 0, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if control.maximumWidth() < 100000:
            grid.addWidget(control, 0, 2, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        else:
            grid.addWidget(control, 0, 2)
        layout.addWidget(row_widget)
        self._register_settings_search_entry(title, row_widget, terms=self._settings_search_widget_terms(control), priority=8)
        return row_widget

    def _control_group(self, layout: QVBoxLayout) -> QFrame:
        group = QFrame()
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(2)
        layout.addWidget(group)
        return group

    def _detail_panel(self, layout: QVBoxLayout, title: str, indicator: StatusLamp | None = None) -> tuple[QFrame, QVBoxLayout, QPushButton]:
        panel = QFrame()
        panel.setObjectName("DetailPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 4, 0, 0)
        panel_layout.setSpacing(0)

        toggle = GlassPushButton()
        toggle.setObjectName("DetailToggle")
        toggle.setCheckable(True)
        toggle.setProperty("detail_title", title)
        toggle.setText(f"{title} 详细设置")
        if indicator is None:
            panel_layout.addWidget(toggle)
        else:
            header = QWidget()
            header.setObjectName("DetailToggleRow")
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(0, 0, 0, 0)
            header_layout.setSpacing(8)
            header_layout.addWidget(indicator, 0, Qt.AlignmentFlag.AlignVCenter)
            header_layout.addWidget(toggle, 1)
            panel_layout.addWidget(header)

        body = QFrame()
        body.setObjectName("DetailBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(10, 8, 10, 8)
        body_layout.setSpacing(4)
        body.setVisible(False)
        panel_layout.addWidget(body)

        def update_open_state(opened: bool):
            body.setVisible(opened)
            base_title = str(toggle.property("detail_title") or title)
            toggle.setText(f"{base_title} 详细设置" + (" · 收起" if opened else ""))

        toggle.toggled.connect(update_open_state)
        install_control_hover(toggle, menu_callback=lambda: toggle.setChecked(True))
        layout.addWidget(panel)
        self._detail_panels.append((toggle, body))
        self._register_settings_search_entry(title, panel, terms=(toggle.text(),), priority=6)
        return panel, body_layout, toggle

    def _set_detail_panel_title(self, toggle: QPushButton, title: str):
        toggle.setProperty("detail_title", title)
        opened = bool(toggle.isChecked())
        toggle.setText(f"{title} 详细设置" + (" · 收起" if opened else ""))

    def _music_app_profiles_setting(self) -> dict[str, dict[str, object]]:
        raw = self._setting_value("v2_music_app_profiles", {}) or {}
        profiles: dict[str, dict[str, object]] = {}
        for service, defaults in DEFAULT_MUSIC_APP_PROFILES.items():
            profile = dict(defaults)
            if isinstance(raw, dict) and isinstance(raw.get(service), dict):
                saved = raw.get(service) or {}
                for key in defaults:
                    if key in saved:
                        profile[key] = saved[key]
            profiles[service] = profile
        return profiles

    def _music_app_profile(self, service: str) -> dict[str, object]:
        profiles = self._music_app_profiles_setting()
        key = str(service or "").strip().casefold()
        profile = dict(profiles.get(key, dict(DEFAULT_MUSIC_APP_PROFILES.get(key, {}))))
        if key == "netease" and not str(profile.get("path", "") or "").strip():
            legacy_path = self._legacy_netease_executable_path()
            if legacy_path:
                profile["path"] = legacy_path
        return profile

    def _legacy_netease_executable_path(self) -> str:
        path = str(self._setting_value("v2_netease_betterncm_path", "") or "").strip().strip('"')
        if self._looks_like_executable_path(path):
            return path
        return ""

    @staticmethod
    def _looks_like_executable_path(path: str) -> bool:
        normalized = str(path or "").replace("/", "\\").strip().strip('"')
        basename = normalized.rsplit("\\", 1)[-1].casefold()
        return bool(basename.endswith(".exe") and len(basename) > 4)

    def _build_music_app_profile_panel(self, layout: QVBoxLayout, service: str, label: str, placeholder: str):
        profile = self._music_app_profile(service)
        indicator = self._status_indicator(bool(profile.get("sync_enabled", False)), self._music_app_profile_status_text(service))
        panel, body_layout, _toggle = self._detail_panel(layout, label, indicator=indicator)
        sync_check = self._check(
            "启用同步插件",
            bool(profile.get("sync_enabled", False)),
            lambda enabled, service=service: self._set_music_app_profile_sync_enabled(service, enabled),
        )
        body_layout.addWidget(sync_check)
        path_input = QLineEdit(str(profile.get("path", "") or ""))
        path_input.setPlaceholderText(placeholder)
        path_input.editingFinished.connect(lambda service=service, widget=path_input: self._set_music_app_profile_path(service, widget))
        self._path_picker_row(
            body_layout,
            "应用路径",
            path_input,
            lambda _checked=False, service=service, widget=path_input: self._browse_music_app_profile_path(service, widget),
        )
        status_label = QLabel(self._music_app_profile_status_text(service))
        status_label.setObjectName("Muted")
        status_label.setWordWrap(True)
        self._row(body_layout, "监听状态", status_label)
        offset_slider = self._slider_row(
            body_layout,
            "歌词偏移",
            -12000,
            12000,
            int(round(float(profile.get("lyric_offset_seconds", 0.0) or 0.0) * 1000)),
            lambda value, service=service: self._set_music_app_profile_lyric_offset(service, value),
            " ms",
        )
        controls = {
            "panel": panel,
            "sync": sync_check,
            "path": path_input,
            "status": status_label,
            "indicator": indicator,
            "offset": offset_slider,
        }
        if service == "netease":
            betterncm_check = self._check(
                "启用 BetterNCM 接口",
                bool(profile.get("betterncm_enabled", False)),
                self._set_music_app_profile_betterncm_enabled,
            )
            body_layout.addWidget(betterncm_check)
            betterncm_path = QLineEdit(str(profile.get("betterncm_path", "") or self._setting_value("v2_netease_betterncm_path", "") or ""))
            betterncm_path.setPlaceholderText(r"C:\betterncm")
            betterncm_path.editingFinished.connect(lambda widget=betterncm_path: self._set_music_app_profile_betterncm_path(widget))
            self._path_picker_row(
                body_layout,
                "BetterNCM",
                betterncm_path,
                lambda _checked=False, widget=betterncm_path: self._browse_betterncm_path(widget),
            )
            controls["betterncm_enabled"] = betterncm_check
            controls["betterncm_path"] = betterncm_path
        self.music_app_profile_controls[service] = controls

    def _music_app_profile_status_text(self, service: str) -> str:
        profile = self._music_app_profile(service)
        sync_enabled = bool(profile.get("sync_enabled", False))
        path = str(profile.get("path", "") or "").strip()
        service_key = str(service or "").strip().casefold()
        if not sync_enabled:
            return "未启用，不加载对应监听器。"
        if not path:
            return "已开启但未填写应用路径，因此不会监听。"
        basename = path.replace("/", "\\").rsplit("\\", 1)[-1] or path
        if service_key in {"netease", "qq"}:
            return f"已启用，匹配到 {basename} 时才会加载同步监听。"
        return "已保存路径；该播放器的专用进度读取仍待适配，当前主要使用系统媒体信息。"

    def _music_sync_summary_text(self) -> str:
        enabled: list[str] = []
        missing_path: list[str] = []
        pending: list[str] = []
        labels = {service: label for service, label, _placeholder in self.MUSIC_APP_PROFILE_PANELS}
        for service, _label, _placeholder in self.MUSIC_APP_PROFILE_PANELS:
            profile = self._music_app_profile(service)
            if not bool(profile.get("sync_enabled", False)):
                continue
            if not str(profile.get("path", "") or "").strip():
                missing_path.append(labels.get(service, service))
            elif service in {"netease", "qq"}:
                enabled.append(labels.get(service, service))
            else:
                pending.append(labels.get(service, service))
        if enabled:
            text = "已开启：" + "、".join(enabled)
            if missing_path:
                text += "；缺少路径：" + "、".join(missing_path)
            if pending:
                text += "；待适配：" + "、".join(pending)
            return text
        if missing_path:
            return "有同步插件已开启，但缺少应用路径：" + "、".join(missing_path)
        if pending:
            return "已保存待适配播放器：" + "、".join(pending)
        return "未启用专用同步插件；不会加载播放器监听器。"

    def _update_music_app_profile_statuses(self):
        if hasattr(self, "music_sync_status"):
            self.music_sync_status.setText(self._music_sync_summary_text())
        for service, controls in getattr(self, "music_app_profile_controls", {}).items():
            status = controls.get("status")
            status_text = self._music_app_profile_status_text(service)
            if status is not None:
                status.setText(status_text)
            indicator = controls.get("indicator")
            if indicator is not None:
                self._set_status_indicator(indicator, bool(self._music_app_profile(service).get("sync_enabled", False)), status_text)

    def _collapse_detail_panels(self):
        for toggle, body in getattr(self, "_detail_panels", []):
            toggle.blockSignals(True)
            toggle.setChecked(False)
            toggle.blockSignals(False)
            body.setVisible(False)
            base_title = str(toggle.property("detail_title") or "详细")
            toggle.setText(f"{base_title} 详细设置")

    def _preview_frame(self, height: int = 42) -> QFrame:
        frame = QFrame()
        frame.setFixedHeight(height)
        frame.setStyleSheet("QFrame { border-radius: 8px; background: #111827; border: 1px solid #e5e7eb; }")
        return frame

    def _check(self, text: str, checked: bool, callback) -> IosSwitch:
        box = IosSwitch(text, checked)
        box.setChecked(checked)
        box.toggled.connect(callback)
        self._register_settings_search_entry(text, box, terms=(text,), priority=7)
        return box

    def _update_theme_schedule_controls(self):
        scheduled = str(self._setting_value("v2_theme_mode", "system") or "system").strip().lower() in {"schedule", "scheduled", "custom"}
        for row in getattr(self, "theme_dark_time_rows", []):
            if row is not None:
                row.setVisible(scheduled)
        for control in (getattr(self, "theme_dark_start", None), getattr(self, "theme_dark_end", None)):
            parent = control.parentWidget() if control is not None else None
            if parent is not None:
                parent.setVisible(scheduled)

    def _update_music_background_follow_dependency_controls(self):
        follow_enabled = bool(self._setting_value("v2_music_background_follow_theme", False))
        tasker_enabled = bool(self._setting_value("v2_tasker_follow_taskbar_color", False))
        for checkbox in getattr(self, "music_background_follow_theme_checkboxes", []):
            self._set_switch_checked(checkbox, follow_enabled)
        for checkbox in getattr(self, "tasker_follow_taskbar_color_checkboxes", []):
            self._set_switch_checked(checkbox, tasker_enabled)
        for hint in getattr(self, "tasker_follow_taskbar_color_hint_labels", []):
            hint.setVisible(follow_enabled and not tasker_enabled)

    def _slider_row(self, layout: QVBoxLayout, title: str, minimum: int, maximum: int, value: int, callback, suffix: str = "") -> QSlider:
        row = QWidget()
        grid = QGridLayout(row)
        grid.setContentsMargins(0, self._row_vpad(), 0, self._row_vpad())
        grid.setHorizontalSpacing(12 if getattr(self, "_compact_card_active", False) else 14)
        grid.setVerticalSpacing(6)
        title_label = QLabel(title)
        title_label.setObjectName("RowTitle")
        value_label = QLabel(f"{value}{suffix}")
        value_label.setObjectName("Value")
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        slider = NoWheelSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        slider._value_label = value_label
        slider._value_suffix = suffix
        slider._title_label = title_label
        slider.valueChanged.connect(lambda v, slider=slider: self._update_slider_value_label(slider, v))
        slider.valueChanged.connect(callback)
        grid.addWidget(title_label, 0, 0)
        grid.addWidget(value_label, 0, 1)
        grid.addWidget(slider, 1, 0, 1, 2)
        layout.addWidget(row)
        self._register_settings_search_entry(title, row, terms=(title, f"{minimum}", f"{maximum}", value_label.text()), priority=8)
        return slider

    def _update_slider_value_label(self, slider: QSlider, value: int):
        value_label = getattr(slider, "_value_label", None)
        if value_label is None:
            return
        formatter = getattr(slider, "_value_formatter", None)
        if callable(formatter):
            try:
                value_label.setText(str(formatter(value)))
                return
            except Exception:
                pass
        value_label.setText(f"{value}{getattr(slider, '_value_suffix', '')}")

    def _combo(self, items: list[tuple[str, object]], current, callback) -> QComboBox:
        combo = NoWheelComboBox()
        for label, data in items:
            combo.addItem(label, data)
        for index in range(combo.count()):
            if combo.itemData(index) == current:
                combo.setCurrentIndex(index)
                break
        combo.currentIndexChanged.connect(callback)
        self._style_combo_popup(combo)
        return combo

    def _set_slider_value(self, slider: QSlider, value: int):
        slider.blockSignals(True)
        slider.setValue(value)
        slider.blockSignals(False)
        self._update_slider_value_label(slider, value)

    @staticmethod
    def _display_refresh_rate_fps_cap() -> int:
        try:
            rates = []
            for screen in QGuiApplication.screens() or []:
                try:
                    rate = float(screen.refreshRate())
                except Exception:
                    rate = 0.0
                if rate > 0.0:
                    rates.append(rate)
            if rates:
                return max(30, int(round(max(rates))))
        except Exception:
            pass
        return 60

    def _fps_setting_value(self, value: object) -> int:
        try:
            numeric = int(round(float(value)))
        except (TypeError, ValueError):
            numeric = 60
        return max(30, min(self._display_refresh_rate_fps_cap(), numeric))

    @staticmethod
    def _fps_value_label(value: int) -> str:
        suffix = " · 高性能消耗" if int(value) >= 60 else ""
        return f"{int(value)} FPS{suffix}"

    def _bind_fps_slider_label(self, slider: QSlider):
        slider._value_formatter = self._fps_value_label
        self._update_slider_value_label(slider, slider.value())

    def _style_combo_popup(self, combo: QComboBox):
        style_combo_popup(combo, self._theme())

    def _location_mode_items(self) -> list[tuple[str, str]]:
        available = getattr(self.island_widget, "available_location_modes", None)
        if callable(available):
            modes = available()
        elif bool(getattr(self.island_widget, "mydockfinder_running", False)):
            modes = ["finder", "free"] if self._finder_bar_presence()[0] else ["free"]
        else:
            modes = ["top", "free", "tasker"]
        return [(placement_mode_label(mode), mode) for mode in modes if mode in PLACEMENT_MODE_LABELS]

    def _location_mode_label(self) -> str:
        current = str(getattr(self.island_widget, "location_mode", self._setting_value("v2_location_mode", "top")) or "top")
        return placement_mode_label(current)

    def _finder_bar_presence(self) -> tuple[bool, bool]:
        diagnostics = getattr(self.island_widget, "mydockfinder_diagnostics", {}) or {}
        if not isinstance(diagnostics, dict):
            diagnostics = {}
        bars = diagnostics.get("finder_bars") or []
        if not isinstance(bars, (list, tuple)):
            bars = []
        enabled = bool(getattr(self.island_widget, "mydockfinder_running", False)) and bool(
            getattr(self.island_widget, "finder_bar_enabled", diagnostics.get("finder_enabled", False))
        )
        visible = enabled and bool(getattr(self.island_widget, "finder_bar_visible", any(
            isinstance(bar, dict) and bar.get("enabled") and bar.get("visible")
            and not bar.get("cloaked") and not bar.get("minimized")
            for bar in bars
        )))
        return enabled, visible

    def _finder_bar_status_text(self) -> str:
        if not bool(getattr(self.island_widget, "mydockfinder_running", False)):
            return "MyDockFinder 未运行。可使用 Top Island、Free 或 Tasker Bar。"
        enabled, visible = self._finder_bar_presence()
        if not enabled:
            return "Dock 已运行，尚未检测到已开启的 MyFinder 顶栏。当前仅可使用 Free。"
        if visible:
            return "MyFinder 顶栏已开启并显示。Finder Bar 随顶栏显示，播放区域可左右拖动。"
        return "MyFinder 顶栏已开启，当前自动隐藏。Finder Bar 随顶栏隐藏与显示。"

    def _refresh_placement_mode_options(self):
        combo = getattr(self, "placement_mode", None)
        if not isinstance(combo, QComboBox):
            return
        current = str(getattr(self.island_widget, "location_mode", "top") or "top")
        if current == "stick":
            current = "free"
        items = self._location_mode_items()
        combo.blockSignals(True)
        if items != [(combo.itemText(index), combo.itemData(index)) for index in range(combo.count())]:
            combo.clear()
            for label, mode in items:
                combo.addItem(label, mode)
        self._set_combo_data(combo, current if current in {mode for _label, mode in items} else "free")
        combo.blockSignals(False)
        self._style_combo_popup(combo)

    def _mydockfinder_listener_process_names_setting(self) -> tuple[str, ...]:
        raw = getattr(self.island_widget, "mydockfinder_listener_process_names", None)
        if raw is None:
            raw = self._setting_value(
                "v2_mydockfinder_listener_process_names",
                list(DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES),
            )
        return normalize_mydockfinder_listener_process_names(raw)

    def _mydockfinder_status_text(self) -> str:
        running = bool(getattr(self.island_widget, "mydockfinder_running", False))
        diagnostics = getattr(self.island_widget, "mydockfinder_diagnostics", {}) or {}
        if not isinstance(diagnostics, dict):
            diagnostics = {}
        configured = str(diagnostics.get("configured_path") or getattr(self.island_widget, "mydockfinder_path", "") or "").strip()
        matched = str(diagnostics.get("matched_name") or "").strip()
        reason = str(diagnostics.get("reason") or "").strip()
        if running:
            detail = matched or "MyDockFinder"
            return f"已检测到 {detail}。"
        candidates = diagnostics.get("candidate_names") or []
        if isinstance(candidates, (list, tuple, set)):
            candidate_text = "、".join(str(item) for item in list(candidates)[:6])
        else:
            candidate_text = str(candidates or "")
        if not candidate_text:
            candidate_text = "、".join(self._mydockfinder_listener_process_names_setting())
        if not configured:
            return f"未填写应用位置。监听会扫描已勾选进程：{candidate_text or '无'}。"
        if candidate_text:
            return f"未检测到。已扫描：{candidate_text}。{reason or '请确认 MyDockFinder 已启动。'}"
        return f"未检测到。{reason or '请确认 MyDockFinder 已启动。'}"

    def _mydockfinder_process_checklist(self) -> QWidget:
        selected = set(self._mydockfinder_listener_process_names_setting())
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        group: dict[str, IosSwitch] = {}
        for process_name, label in MYDOCKFINDER_LISTEN_PROCESS_CHOICES:
            checkbox = self._check(
                label,
                process_name in selected,
                lambda enabled, name=process_name: self._set_mydockfinder_listener_process(name, enabled),
            )
            group[process_name] = checkbox
            layout.addWidget(checkbox)
        if not hasattr(self, "mydockfinder_process_checkbox_groups"):
            self.mydockfinder_process_checkbox_groups = []
        self.mydockfinder_process_checkbox_groups.append(group)
        self.mydockfinder_process_checkboxes = group
        return container

    def _sync_mydockfinder_process_checkboxes(self):
        selected = set(self._mydockfinder_listener_process_names_setting())
        for group in getattr(self, "mydockfinder_process_checkbox_groups", []):
            for process_name, checkbox in group.items():
                checkbox.blockSignals(True)
                checkbox.setChecked(process_name in selected)
                checkbox.blockSignals(False)

    def _basic_settings_page(self) -> QWidget:
        page = self._page("基础设置")
        layout: QVBoxLayout = page._content_layout
        self._begin_compact_appearance_page(page)
        c = self._card(layout).layout()
        c.addWidget(self._section_label("界面设置", 2, 6))
        self.theme_mode = self._combo(
            list(self.THEME_MODES),
            str(self._setting_value("v2_theme_mode", "system") or "system"),
            self._set_theme_mode,
        )
        self._row(c, "界面模式", self.theme_mode)
        self.startup_mode = self._combo(
            list(self.STARTUP_MODES),
            normalize_startup_mode(str(self._setting_value("v2_startup_mode", "none") or "none")),
            self._set_startup_mode,
        )
        self.startup_mode.setToolTip("计划任务更适合随用户登录启动；服务模式不适合带界面的托盘应用。")
        self._row(c, "开机启动", self.startup_mode)
        self.theme_dark_start = QLineEdit(str(self._setting_value("v2_theme_dark_start", "22:00") or "22:00"))
        self.theme_dark_start.setPlaceholderText("22:00")
        self.theme_dark_start.editingFinished.connect(self._set_theme_dark_start)
        self._row(c, "深色开始", self.theme_dark_start)
        self.theme_dark_time_rows.append(c.itemAt(c.count() - 1).widget())
        self.theme_dark_end = QLineEdit(str(self._setting_value("v2_theme_dark_end", "07:00") or "07:00"))
        self.theme_dark_end.setPlaceholderText("07:00")
        self.theme_dark_end.editingFinished.connect(self._set_theme_dark_end)
        self._row(c, "深色结束", self.theme_dark_end)
        self.theme_dark_time_rows.append(c.itemAt(c.count() - 1).widget())
        self.music_background_follow_theme_checkbox = self._check(
            "音乐岛跟随界面颜色",
            bool(self._setting_value("v2_music_background_follow_theme", False)),
            self._set_music_background_follow_theme,
        )
        c.addWidget(self.music_background_follow_theme_checkbox)
        self.music_background_follow_theme_checkboxes.append(self.music_background_follow_theme_checkbox)

        c = self._card(layout).layout()
        c.addWidget(self._section_label("动画", 2, 6))
        self.animation_speed = self._combo(
            list(self.ANIMATION_SPEEDS), float(getattr(self.island_widget, "animation_speed", 1.0)),
            self._set_animation_speed,
        )
        self._row(c, "动画倍速", self.animation_speed)
        fps_cap = self._display_refresh_rate_fps_cap()
        self.fps_slider = self._slider_row(
            c, "默认动画帧率", 30, fps_cap,
            self._fps_setting_value(getattr(self.island_widget, "animation_fps", 60)),
            self._set_animation_fps, " FPS",
        )
        self._bind_fps_slider_label(self.fps_slider)
        self.lyric_fps_slider = self._slider_row(
            c, "歌词动画帧率", 30, fps_cap,
            self._fps_setting_value(getattr(self.island_widget, "lyric_animation_fps", self._setting_value("v2_lyric_animation_fps", 120))),
            self._set_lyric_animation_fps, " FPS",
        )
        self._bind_fps_slider_label(self.lyric_fps_slider)
        self._end_compact_appearance_page()
        layout.addStretch(1)
        return page

    def _media_page(self) -> QWidget:
        page = self._page("媒体控制")
        layout: QVBoxLayout = page._content_layout
        c = self._card(layout).layout()
        c.addWidget(self._section_label("播放来源", 2, 8))
        self.owned_playback_switch = self._check(
            "程序内播放", bool(self._setting_value("v2_owned_playback_enabled", True)),
            lambda enabled: self._set_playback_source("v2_owned_playback_enabled", enabled),
        )
        self.system_capture_switch = self._check(
            "系统抓取", bool(self._setting_value("v2_system_capture_enabled", False)),
            lambda enabled: self._set_playback_source("v2_system_capture_enabled", enabled),
        )
        c.addWidget(self.owned_playback_switch)
        c.addWidget(self.system_capture_switch)
        self.online_login_button = GlassPushButton("登录音乐来源")
        self.online_login_button.clicked.connect(self._open_online_music_login)
        c.addWidget(self.online_login_button)
        self.personal_dj_voice_button = GlassPushButton("私人DJ音色")
        self.personal_dj_voice_button.clicked.connect(self._open_personal_dj_voices)
        c.addWidget(self.personal_dj_voice_button)

        c = self._card(layout).layout()
        c.addWidget(self._section_label("音乐软件监听", 2, 8))
        self.music_sync_status = QLabel(self._music_sync_summary_text())
        self.music_sync_status.setObjectName("Muted")
        self.music_sync_status.setWordWrap(True)
        c.addWidget(self.music_sync_status)
        player_hint = QLabel("系统抓取时，填写播放器路径后可开启对应同步插件。未填写路径时不会加载监听器；网易云和 QQ 已接入专用进度读取，其它播放器先保留入口。")
        player_hint.setWordWrap(True)
        player_hint.setStyleSheet(self._hint_style(2, 12))
        c.addWidget(player_hint)
        self.music_app_profile_controls = {}
        for service, label, placeholder in self.MUSIC_APP_PROFILE_PANELS:
            self._build_music_app_profile_panel(c, service, label, placeholder)
        self._update_music_app_profile_statuses()

        c = self._card(layout).layout()
        c.addWidget(self._section_label("MyDockFinder 监听", 2, 8))
        running = bool(getattr(self.island_widget, "mydockfinder_running", False))
        self.mydockfinder_status_indicator = self._status_indicator(running, "已检测" if running else "未检测")
        self.mydockfinder_status_label = QLabel(self._mydockfinder_status_text())
        self.mydockfinder_status_label.setObjectName("Muted")
        self.mydockfinder_status_label.setWordWrap(True)
        self._indicator_row(c, "检测状态", self.mydockfinder_status_indicator, self.mydockfinder_status_label)
        finder_enabled, finder_visible = self._finder_bar_presence()
        self.myfinder_status_indicator = self._status_indicator(finder_enabled, "已显示" if finder_visible else "已隐藏" if finder_enabled else "未开启")
        self.myfinder_status_label = QLabel(self._finder_bar_status_text())
        self.myfinder_status_label.setObjectName("Muted")
        self.myfinder_status_label.setWordWrap(True)
        self._indicator_row(c, "MyFinder 顶栏", self.myfinder_status_indicator, self.myfinder_status_label)
        self.mydockfinder_path_input = QLineEdit(str(getattr(self.island_widget, "mydockfinder_path", self._setting_value("v2_mydockfinder_path", "")) or ""))
        self.mydockfinder_path_input.setPlaceholderText(r"C:\Program Files\MyDockFinder\MyDockFinder.exe")
        self.mydockfinder_path_input.editingFinished.connect(self._set_mydockfinder_path)
        self.mydockfinder_path_row = self._path_picker_row(
            c, "MyDockFinder 应用位置", self.mydockfinder_path_input,
            self._browse_mydockfinder_path, stacked=True,
        )
        _panel, listener_layout, self.mydockfinder_listener_detail_toggle = self._detail_panel(c, "MyDockFinder 监听进程")
        self.mydockfinder_listener_detail_body = self._detail_panels[-1][1]
        listener_layout.addWidget(self._mydockfinder_process_checklist())
        self.placement_mode = self._combo(
            self._location_mode_items(), str(getattr(self.island_widget, "location_mode", "top") or "top"),
            self._set_placement_mode,
        )
        self._row(c, "显示位置", self.placement_mode)
        self.finder_bar_style = self._combo(
            list(self.FINDER_BAR_STYLES), normalize_finder_bar_style(getattr(
                self.island_widget, "finder_bar_style", self._setting_value("v2_finder_bar_style", "transparent"),
            )), self._set_finder_bar_style,
        )
        self._row(c, "Finder Bar 样式", self.finder_bar_style)
        self.finder_bar_hint = QLabel("Finder Bar 在 MyFinder 顶栏内展示封面与歌词，可直接左右拖动；样式与拖动位置单独保存。Tasker Bar 占位 Windows 任务栏，Top Island 固定顶部，Free 在屏幕中自由浮动。")
        self.finder_bar_hint.setObjectName("Muted")
        self.finder_bar_hint.setWordWrap(True)
        c.addWidget(self.finder_bar_hint)
        self._update_taskbar_controls()

        c = self._card(layout).layout()
        c.addWidget(self._section_label("音乐岛样式", 2, 8))
        self.music_ambience_style = self._combo(
            list(self.MUSIC_AMBIENCE_STYLES),
            str(getattr(self.island_widget, "music_ambience_style", "sea_level")),
            self._set_music_ambience_style,
        )
        self._row(c, "背景样式", self.music_ambience_style)
        self.music_preview = MusicAmbiencePreview(self)
        c.addWidget(self.music_preview)
        self.lyric_flow_style = self._combo(
            list(self.LYRIC_FLOW_STYLES),
            str(getattr(self.island_widget, "lyric_flow_style", self._setting_value("v2_lyric_flow_style", "wave_stream"))),
            self._set_lyric_flow_style,
        )
        self._row(c, "文字流转", self.lyric_flow_style)
        self.reset_album_record_button = GlassPushButton("归零已听记录")
        self.reset_album_record_button.clicked.connect(self._reset_album_island_record)
        c.addWidget(self.reset_album_record_button)

        c = self._card(layout).layout()
        c.addWidget(self._section_label("歌词与预载", 2, 8))
        self.music_surface_preload_count = self._slider_row(
            c, "音乐页预载歌曲", 1, 6,
            max(1, min(6, int(self._setting_value("v2_music_surface_preload_count", 6)))),
            self._set_music_surface_preload_count, " 首",
        )
        self.lyric_chinese_conversion = self._combo(
            list(self.LYRIC_CHINESE_CONVERSIONS),
            str(self._setting_value("v2_lyric_chinese_conversion", "off") or "off"),
            self._set_lyric_chinese_conversion,
        )
        self._row(c, "歌词繁简转换", self.lyric_chinese_conversion)
        hint = QLabel("会根据当前播放器优先选择歌词源；线上歌词会写入用户缓存，本地 .lrc 可继续手动放入项目 lyrics 目录。")
        hint.setWordWrap(True)
        hint.setStyleSheet(self._hint_style(2, 8))
        c.addWidget(hint)
        self._update_music_ambience_controls()
        self._update_lyric_flow_controls()
        self._update_previews()
        layout.addStretch(1)
        return page

    @staticmethod
    def _reading_gradient_label(value: int) -> str:
        if int(value) <= 0:
            return "无渐变"
        return f"拉丁 {value / 10:.1f} 字 / 中文 {value / 15:.1f} 字"

    def _music_surface_lyrics_page(self) -> QWidget:
        page = self._page("全屏歌词")
        layout: QVBoxLayout = page._content_layout
        card = self._card(layout)
        c = card.layout()

        hint = QLabel("读字方案同步全屏、窗口和灵动岛；金标读字用金色涟漪反馈，不显示额外徽标。其余参数调整全屏与窗口音乐页。")
        hint.setWordWrap(True)
        hint.setStyleSheet(self._hint_style(2, 8))
        c.addWidget(hint)

        self.music_surface_reading_strategy = self._combo(
            [("金标读字", "gold"), ("低感知方案", "low_perception")],
            normalize_music_reading_strategy(getattr(
                self.island_widget,
                "music_surface_reading_strategy",
                self._setting_value("v2_music_surface_reading_strategy", MUSIC_READING_STRATEGY_DEFAULT),
            )),
            self._set_music_surface_reading_strategy,
        )
        self._row(c, "歌词读字方案", self.music_surface_reading_strategy)
        strategy_hint = QLabel(
            "金标读字：真实逐字优先，只有行时间时按行内均分读字。"
            "低感知方案：全曲有真实逐字时间时全曲读字，缺少逐字时间的句子按行估算；全曲没有真实逐字时间则整行切换。"
            "金标读字保留金色涟漪反馈，音乐页不显示额外图标或金标。"
        )
        strategy_hint.setWordWrap(True)
        strategy_hint.setStyleSheet(self._hint_style(2, 8))
        c.addWidget(strategy_hint)

        self.music_surface_reading_lift_level = self._combo(
            [("0（不抬升）", 0), ("原高度的 1/4", 1), ("原高度的 1/2", 2), ("原高度", 4)],
            normalize_music_reading_lift_level(getattr(
                self.island_widget,
                "music_surface_reading_lift_level",
                self._setting_value("v2_music_surface_reading_lift_level", MUSIC_READING_LIFT_DEFAULT_LEVEL),
            )),
            self._set_music_surface_reading_lift_level,
        )
        self._row(c, "读字抬升高度", self.music_surface_reading_lift_level)
        self.music_surface_reading_gradient_steps = self._slider_row(
            c, "读字渐变范围", 0, 30,
            normalize_music_reading_gradient_steps(getattr(
                self.island_widget,
                "music_surface_reading_gradient_steps",
                self._setting_value("v2_music_surface_reading_gradient_steps", MUSIC_READING_GRADIENT_DEFAULT_STEPS),
            )),
            self._set_music_surface_reading_gradient_steps,
        )
        self.music_surface_reading_gradient_steps._value_formatter = self._reading_gradient_label
        self._update_slider_value_label(
            self.music_surface_reading_gradient_steps,
            self.music_surface_reading_gradient_steps.value(),
        )
        gradient_hint = QLabel("最左侧为清晰分界；最右侧最多跨 3 个拉丁字符或 2 个中文字，混合歌词按文字宽度过渡。")
        gradient_hint.setWordWrap(True)
        gradient_hint.setStyleSheet(self._hint_style(2, 8))
        c.addWidget(gradient_hint)

        self.music_sparse_highlight_seconds_per_unit = self._slider_row(
            c, "高光触发阈值（每字词）", 8, 20,
            int(round(10 * normalize_sparse_seconds_per_unit(getattr(
                self.island_widget,
                "music_sparse_highlight_seconds_per_unit",
                self._setting_value("v2_music_sparse_highlight_seconds_per_unit", SPARSE_MIN_SECONDS_PER_UNIT),
            )))),
            self._set_music_sparse_highlight_seconds_per_unit,
        )
        self.music_sparse_highlight_seconds_per_unit._value_formatter = lambda value: f"{value / 10:.1f} 秒"
        self._update_slider_value_label(
            self.music_sparse_highlight_seconds_per_unit,
            self.music_sparse_highlight_seconds_per_unit.value(),
        )
        self.music_intro_cleanup_checkbox = self._check(
            "片头制作信息改为 · · · 倒计时",
            bool(getattr(self.island_widget, "music_intro_cleanup_enabled", self._setting_value("v2_music_intro_cleanup_enabled", True))),
            self._set_music_intro_cleanup_enabled,
        )
        c.addWidget(self.music_intro_cleanup_checkbox)
        secondary_card = self._card(layout).layout()
        self.dual_line_secondary_source = self._combo(
            list(self.DUAL_LINE_SECONDARY_SOURCES),
            str(self._setting_value("v2_dual_line_secondary_source", "translation") or "translation"),
            self._set_dual_line_secondary_source,
        )
        self._row(secondary_card, "次行内容", self.dual_line_secondary_source)
        cantonese_title = QLabel("粤语次行内容")
        self._register_section_label(cantonese_title, 12, 8)
        secondary_card.addWidget(cantonese_title)
        self.cantonese_secondary_priority = self._combo(
            [("音译优先（粤拼）", "phonetic_first"), ("翻译优先", "translation_first")],
            str(self._setting_value("v2_cantonese_secondary_priority", "phonetic_first")),
            self._set_cantonese_secondary_priority,
        )
        self._row(secondary_card, "粤语显示顺序", self.cantonese_secondary_priority)
        cantonese_hint = QLabel("优先显示选定内容，缺少时显示另一项。识别粤语标注、已有粤拼或典型粤语用字后，优先使用歌词源音译，缺少音译时在后台生成粤拼。")
        cantonese_hint.setWordWrap(True)
        cantonese_hint.setStyleSheet(self._hint_style(2, 8))
        secondary_card.addWidget(cantonese_hint)
        c.addStretch(1)
        layout.addStretch(1)
        return page

    def _about_page(self) -> QWidget:
        page = self._page("关于我")
        layout: QVBoxLayout = page._content_layout

        account_card = self._card(layout)
        a = account_card.layout()
        user_id = str(self._setting_value("v2_user_id", "") or Path.home().name or "local-user")
        self._row(a, "用户 ID", self._readonly_value(user_id))
        self._row(a, "作者信息", self._readonly_value(APP_NAME))

        icon_card = self._card(layout)
        icon_layout = icon_card.layout()
        self.app_icon_preview = QLabel()
        self.app_icon_preview.setObjectName("AppIconPreview")
        self.app_icon_preview.setFixedSize(58, 58)
        self.app_icon_preview.setScaledContents(False)
        self._row(icon_layout, "全局图标", self.app_icon_preview)
        self.custom_icon_path_label = self._readonly_value("")
        self._row(icon_layout, "当前图标", self.custom_icon_path_label)
        icon_buttons = QWidget()
        icon_buttons_layout = QHBoxLayout(icon_buttons)
        icon_buttons_layout.setContentsMargins(0, 0, 0, 0)
        icon_buttons_layout.setSpacing(8)
        choose_icon = GlassPushButton("上传图标")
        choose_icon.setObjectName("Primary")
        choose_icon.clicked.connect(self._choose_custom_icon)
        reset_icon = GlassPushButton("恢复默认")
        reset_icon.clicked.connect(self._reset_custom_icon)
        icon_buttons_layout.addWidget(choose_icon)
        icon_buttons_layout.addWidget(reset_icon)
        icon_buttons_layout.addStretch(1)
        self._row(icon_layout, "图标设置", icon_buttons)
        self._sync_icon_preview()

        location_card = self._card(layout)
        l = location_card.layout()
        app_location = str(Path(__file__).resolve().parents[1])
        self.app_location_label = self._readonly_value(app_location)
        self._row(l, "应用位置", self.app_location_label)
        open_location = GlassPushButton("打开位置")
        open_location.clicked.connect(self._open_app_location)
        self._row(l, "浏览", open_location)

        cache_card = self._card(layout)
        cache_layout = cache_card.layout()
        cache_layout.addWidget(self._section_label("缓存管理", top=0))
        self.clear_artwork_cache_button = GlassPushButton("清理封面并重试")
        self.clear_artwork_cache_button.clicked.connect(lambda: self._clear_music_cache("artwork"))
        self._row(cache_layout, "专辑封面缓存", self.clear_artwork_cache_button)
        self.clear_lyrics_cache_button = GlassPushButton("清理在线歌词")
        self.clear_lyrics_cache_button.clicked.connect(lambda: self._clear_music_cache("lyrics"))
        self._row(cache_layout, "在线歌词缓存", self.clear_lyrics_cache_button)
        cache_hint = QLabel("保留手动歌词、设置和歌词偏移；清理后重新获取当前歌曲。")
        cache_hint.setObjectName("Muted")
        cache_hint.setWordWrap(True)
        cache_layout.addWidget(cache_hint)
        self.music_cache_status = QLabel("")
        self.music_cache_status.setObjectName("Muted")
        self.music_cache_status.setWordWrap(True)
        self.music_cache_status.setTextFormat(Qt.TextFormat.PlainText)
        cache_layout.addWidget(self.music_cache_status)

        diagnostic_card = self._card(layout)
        d = diagnostic_card.layout()
        self._diagnostic_labels = {}
        _panel, diagnostic_layout, _toggle = self._detail_panel(d, "软件诊断")
        for key, title in DIAGNOSTIC_FIELD_ROWS:
            if key.startswith(("message_", "wechat_", "qq_notification_", "dingtalk_notification_")):
                continue
            label = self._diagnostic_value_label()
            self._diagnostic_labels[key] = label
            self._row(diagnostic_layout, title, label)
        refresh = GlassPushButton("刷新诊断")
        refresh.setObjectName("Primary")
        refresh.clicked.connect(self._refresh_diagnostics)
        diagnostic_layout.addWidget(refresh)
        export = GlassPushButton("导出诊断")
        export.clicked.connect(self._export_diagnostics)
        diagnostic_layout.addWidget(export)
        self._diagnostic_export_status = QLabel("")
        self._diagnostic_export_status.setObjectName("Muted")
        self._diagnostic_export_status.setWordWrap(True)
        diagnostic_layout.addWidget(self._diagnostic_export_status)
        self._refresh_diagnostics()
        layout.addStretch(1)
        return page

    def _clear_music_cache(self, kind: str):
        if self._music_cache_clear_job is not None:
            return
        provider = getattr(self.island_widget, "data_provider", None)
        clear_cache = getattr(provider, "clear_music_cache", None)
        if not callable(clear_cache):
            self.music_cache_status.setText("当前媒体来源不支持缓存清理。")
            return
        self.clear_artwork_cache_button.setEnabled(False)
        self.clear_lyrics_cache_button.setEnabled(False)
        self.music_cache_status.setText("正在清理封面缓存…" if kind == "artwork" else "正在清理在线歌词缓存…")
        job = _MusicCacheClearJob(clear_cache, kind)
        # This receiver outlives settings. Closing this dialog while disk
        # cleanup runs must not leave the music view's old decoded cache alive.
        notify = getattr(self.island_widget, "on_music_cache_cleared", None)
        if callable(notify):
            job.signals.finished.connect(notify, Qt.ConnectionType.QueuedConnection)
        job.signals.finished.connect(self._music_cache_cleared, Qt.ConnectionType.QueuedConnection)
        self._music_cache_clear_job = job
        QThreadPool.globalInstance().start(job)

    @Slot(str, object)
    def _music_cache_cleared(self, kind: str, result):
        self._music_cache_clear_job = None
        self.clear_artwork_cache_button.setEnabled(True)
        self.clear_lyrics_cache_button.setEnabled(True)
        report = result if isinstance(result, dict) else {"errors": ["清理未返回结果"]}
        errors = report.get("errors", [])
        if report.get("refresh_requested"):
            notify = getattr(self.island_widget, "on_music_cache_cleared", None)
            if not callable(notify):
                self.island_widget.last_media_refresh_at = -999.0
                self.island_widget.update()
        removed = int(report.get("deleted_files", 0))
        size_mb = max(0, int(report.get("deleted_bytes", 0))) / (1024 * 1024)
        noun = "封面" if kind == "artwork" else "在线歌词"
        status = f"已清理{noun}缓存：{removed} 个文件，{size_mb:.1f} MB。"
        if report.get("refresh_requested"):
            status += "正在重新获取当前歌曲。"
        if errors:
            status += "部分缓存未能清理：" + "；".join(str(error) for error in errors[:3])
        self.music_cache_status.setText(status)

    def _barrage_position_max(self) -> int:
        try:
            screen = QApplication.primaryScreen()
            if screen is not None:
                return max(240, int(screen.geometry().height()) - 1)
        except Exception:
            pass
        return 240

    def _fullscreen_alert_x_offset_max(self) -> int:
        try:
            screen = QApplication.primaryScreen()
            if screen is not None:
                return max(240, int(screen.geometry().width() // 2))
        except Exception:
            pass
        return 600

    def _fullscreen_alert_y_offset_max(self) -> int:
        try:
            screen = QApplication.primaryScreen()
            if screen is not None:
                return max(240, int(screen.geometry().height()) - 1)
        except Exception:
            pass
        return 600

    def _actions_page(self) -> QWidget:
        page = self._page("快捷动作")
        layout: QVBoxLayout = page._content_layout
        card = self._card(layout)
        c = card.layout()

        row_one = QHBoxLayout()
        add_btn = GlassPushButton("新增消息")
        add_btn.setObjectName("Primary")
        add_btn.clicked.connect(self._add_message)
        clear_btn = GlassPushButton("清空消息")
        clear_btn.clicked.connect(self._clear_messages)
        row_one.addWidget(add_btn)
        row_one.addWidget(clear_btn)
        c.addLayout(row_one)

        row_two = QHBoxLayout()
        for index, label in enumerate(("插件 1", "插件 2", "插件 3")):
            button = GlassPushButton(label)
            button.clicked.connect(lambda _checked=False, i=index: self._focus_plugin(i))
            row_two.addWidget(button)
        c.addLayout(row_two)

        hint = QLabel("设置会即时应用，并在下次启动时恢复。")
        hint.setStyleSheet(self._hint_style(18, 0))
        c.addWidget(hint)
        c.addStretch(1)
        layout.addStretch(1)
        return page

    def _diagnostics_page(self) -> QWidget:
        page = self._page("诊断")
        layout: QVBoxLayout = page._content_layout
        card = self._card(layout)
        c = card.layout()
        self._diagnostic_labels = {}
        for key, title in DIAGNOSTIC_FIELD_ROWS:
            label = self._diagnostic_value_label()
            self._diagnostic_labels[key] = label
            self._row(c, title, label)
        refresh = GlassPushButton("刷新诊断")
        refresh.setObjectName("Primary")
        refresh.clicked.connect(self._refresh_diagnostics)
        c.addWidget(refresh)
        export = GlassPushButton("导出诊断")
        export.clicked.connect(self._export_diagnostics)
        c.addWidget(export)
        self._diagnostic_export_status = QLabel("")
        self._diagnostic_export_status.setObjectName("Muted")
        self._diagnostic_export_status.setWordWrap(True)
        c.addWidget(self._diagnostic_export_status)
        c.addStretch(1)
        layout.addStretch(1)
        self._refresh_diagnostics()
        return page

    def sync_from_widget(self):
        for control, key, default in (
            (self.owned_playback_switch, "v2_owned_playback_enabled", True),
            (self.system_capture_switch, "v2_system_capture_enabled", False),
            (self.music_background_follow_theme_checkbox, "v2_music_background_follow_theme", False),
        ):
            self._set_switch_checked(control, bool(self._setting_value(key, default)))
        self._set_switch_checked(
            self.music_intro_cleanup_checkbox,
            bool(getattr(self.island_widget, "music_intro_cleanup_enabled", self._setting_value("v2_music_intro_cleanup_enabled", True))),
        )
        for control, value in (
            (self.theme_mode, str(self._setting_value("v2_theme_mode", "system") or "system")),
            (self.startup_mode, normalize_startup_mode(str(self._setting_value("v2_startup_mode", "none") or "none"))),
            (self.finder_bar_style, normalize_finder_bar_style(getattr(
                self.island_widget, "finder_bar_style", self._setting_value("v2_finder_bar_style", "transparent"),
            ))),
            (self.animation_speed, float(getattr(self.island_widget, "animation_speed", 1.0))),
            (self.music_ambience_style, self._current_music_ambience_style()),
            (self.lyric_flow_style, str(getattr(self.island_widget, "lyric_flow_style", self._setting_value("v2_lyric_flow_style", "wave_stream")))),
            (self.lyric_chinese_conversion, str(self._setting_value("v2_lyric_chinese_conversion", "off") or "off")),
            (self.dual_line_secondary_source, str(self._setting_value("v2_dual_line_secondary_source", "translation") or "translation")),
            (self.cantonese_secondary_priority, str(self._setting_value("v2_cantonese_secondary_priority", "phonetic_first"))),
            (self.music_surface_reading_strategy, normalize_music_reading_strategy(getattr(
                self.island_widget, "music_surface_reading_strategy",
                self._setting_value("v2_music_surface_reading_strategy", MUSIC_READING_STRATEGY_DEFAULT),
            ))),
            (self.music_surface_reading_lift_level, normalize_music_reading_lift_level(getattr(
                self.island_widget, "music_surface_reading_lift_level",
                self._setting_value("v2_music_surface_reading_lift_level", MUSIC_READING_LIFT_DEFAULT_LEVEL),
            ))),
        ):
            self._set_combo_data(control, value)
        for control, value in (
            (self.theme_dark_start, str(self._setting_value("v2_theme_dark_start", "22:00") or "22:00")),
            (self.theme_dark_end, str(self._setting_value("v2_theme_dark_end", "07:00") or "07:00")),
            (self.mydockfinder_path_input, str(getattr(self.island_widget, "mydockfinder_path", self._setting_value("v2_mydockfinder_path", "")) or "")),
        ):
            control.blockSignals(True)
            control.setText(value)
            control.blockSignals(False)
        for control, value in (
            (self.music_surface_preload_count, max(1, min(6, int(self._setting_value("v2_music_surface_preload_count", 6))))),
            (self.fps_slider, self._fps_setting_value(getattr(self.island_widget, "animation_fps", 60))),
            (self.lyric_fps_slider, self._fps_setting_value(getattr(self.island_widget, "lyric_animation_fps", self._setting_value("v2_lyric_animation_fps", 120)))),
            (self.music_surface_reading_gradient_steps, normalize_music_reading_gradient_steps(getattr(
                self.island_widget, "music_surface_reading_gradient_steps",
                self._setting_value("v2_music_surface_reading_gradient_steps", MUSIC_READING_GRADIENT_DEFAULT_STEPS),
            ))),
            (self.music_sparse_highlight_seconds_per_unit, int(round(10 * normalize_sparse_seconds_per_unit(getattr(
                self.island_widget, "music_sparse_highlight_seconds_per_unit",
                self._setting_value("v2_music_sparse_highlight_seconds_per_unit", SPARSE_MIN_SECONDS_PER_UNIT),
            ))))),
        ):
            self._set_slider_value(control, value)
            self._update_slider_value_label(control, control.value())
        self.mydockfinder_status_label.setText(self._mydockfinder_status_text())
        self.myfinder_status_label.setText(self._finder_bar_status_text())
        self._refresh_placement_mode_options()
        self._sync_mydockfinder_process_checkboxes()
        self._update_taskbar_controls()
        self._update_music_app_profile_controls()
        self._update_theme_schedule_controls()
        self._update_music_ambience_controls()
        self._update_lyric_flow_controls()
        self._update_previews()
        self._sync_icon_preview()
        self._refresh_diagnostics()

    def _update_player_progress_controls(self):
        enabled = bool(getattr(self, "player_progress_plugin_checkbox", None) and self.player_progress_plugin_checkbox.isChecked())
        auto_detect = bool(getattr(self, "player_progress_auto_detect_checkbox", None) and self.player_progress_auto_detect_checkbox.isChecked())
        if hasattr(self, "player_progress_auto_detect_checkbox"):
            self._set_switch_enabled(self.player_progress_auto_detect_checkbox, enabled, "需先开启播放器进度插件")
        if hasattr(self, "music_player_mode"):
            self.music_player_mode.setEnabled(enabled and not auto_detect)
        self._update_music_app_profile_statuses()

    def _update_music_app_profile_controls(self):
        controls_by_service = getattr(self, "music_app_profile_controls", {})
        if not controls_by_service:
            return
        for service, controls in controls_by_service.items():
            profile = self._music_app_profile(service)
            sync = controls.get("sync")
            if sync is not None:
                sync.blockSignals(True)
                sync.setChecked(bool(profile.get("sync_enabled", False)))
                sync.blockSignals(False)
            path = controls.get("path")
            if path is not None:
                path.blockSignals(True)
                path.setText(str(profile.get("path", "") or ""))
                path.blockSignals(False)
            offset = controls.get("offset")
            if offset is not None:
                self._set_slider_value(offset, int(round(float(profile.get("lyric_offset_seconds", 0.0) or 0.0) * 1000)))
            betterncm_enabled = controls.get("betterncm_enabled")
            if betterncm_enabled is not None:
                betterncm_enabled.blockSignals(True)
                betterncm_enabled.setChecked(bool(profile.get("betterncm_enabled", False)))
                betterncm_enabled.blockSignals(False)
            betterncm_path = controls.get("betterncm_path")
            if betterncm_path is not None:
                betterncm_path.blockSignals(True)
                betterncm_path.setText(str(profile.get("betterncm_path", "") or self._setting_value("v2_netease_betterncm_path", "") or ""))
                betterncm_path.blockSignals(False)
        self._update_music_app_profile_statuses()

    def _update_message_controls(self):
        enabled = bool(getattr(self, "message_detection_checkbox", None) and self.message_detection_checkbox.isChecked())
        for name in ("wechat_notification_checkbox", "qq_notification_checkbox", "dingtalk_notification_checkbox"):
            widget = getattr(self, name, None)
            if widget is not None:
                self._set_switch_enabled(widget, enabled, "需先开启消息通知总开关")
        if hasattr(self, "wechat_detail_checkbox"):
            self._set_switch_enabled(self.wechat_detail_checkbox, enabled and bool(self.wechat_notification_checkbox.isChecked()), "需先开启微信通知")
        if hasattr(self, "qq_detail_checkbox"):
            self._set_switch_enabled(self.qq_detail_checkbox, enabled and bool(self.qq_notification_checkbox.isChecked()), "需先开启 QQ 通知")
        if hasattr(self, "dingtalk_detail_checkbox"):
            self._set_switch_enabled(self.dingtalk_detail_checkbox, enabled and bool(self.dingtalk_notification_checkbox.isChecked()), "需先开启钉钉通知")

    def _dual_line_lyrics_available_for_settings(self) -> bool:
        location = str(getattr(self.island_widget, "location_mode", self._setting_value("v2_location_mode", "free")) or "free")
        return location != "tasker"

    def _dual_line_lyrics_status_text(self) -> str:
        return "可用" if self._dual_line_lyrics_available_for_settings() else "Tasker 模式下不可用"

    def _update_lyric_flow_controls(self):
        style = str(getattr(self.island_widget, "lyric_flow_style", self._setting_value("v2_lyric_flow_style", "wave_stream")))
        reset_button = getattr(self, "reset_album_record_button", None)
        if reset_button is not None:
            reset_button.setVisible(style == "album_island")

    def _active_color_fill_enabled_for_settings(self) -> bool:
        checker = getattr(self.island_widget, "_active_lyric_color_fill_enabled", None)
        if callable(checker):
            return bool(checker())
        return False

    def _color_fill_background_available_for_settings(self) -> bool:
        style = self._current_music_ambience_style()
        if style not in self.COLOR_FILL_BLOCKED_BACKGROUND_STYLES:
            return True
        return False

    def _ember_color_fill_background_available_for_settings(self) -> bool:
        return self._current_music_ambience_style() in self.EMBER_COLOR_FILL_BACKGROUND_STYLES

    def _ember_color_back_gray_available_for_settings(self) -> bool:
        style = str(getattr(self.island_widget, "lyric_flow_style", self._setting_value("v2_lyric_flow_style", "wave_stream")) or "wave_stream")
        return style == "ember_fade" and self._current_music_ambience_style() in self.EMBER_COLOR_BACK_GRAY_BACKGROUND_STYLES

    def _color_fill_unavailable_reason(self) -> str:
        if not self._color_fill_background_available_for_settings():
            return self.COLOR_FILL_BLOCKED_REASON
        return "当前文字流样式下不可用"

    def _ember_color_fill_unavailable_reason(self) -> str:
        if not self._ember_color_fill_background_available_for_settings():
            return self.EMBER_COLOR_FILL_BLOCKED_REASON
        return "当前文字流样式下不可用"

    def _gelv_color_fill_available_for_settings(self) -> bool:
        style = str(getattr(self.island_widget, "lyric_flow_style", self._setting_value("v2_lyric_flow_style", "wave_stream")) or "wave_stream")
        if style != "gelv_songscatter":
            return False
        return self._color_fill_background_available_for_settings()

    def _ember_color_fill_available_for_settings(self) -> bool:
        style = str(getattr(self.island_widget, "lyric_flow_style", self._setting_value("v2_lyric_flow_style", "wave_stream")) or "wave_stream")
        if style != "ember_fade":
            return False
        return self._ember_color_fill_background_available_for_settings()

    def _music_ambience_values(self) -> dict[str, dict[str, float | int]]:
        return {style: dict(defaults) for style, defaults in DEFAULT_MUSIC_AMBIENCE_VALUES.items()}

    def _current_music_ambience_style(self) -> str:
        style = str(getattr(self.island_widget, "music_ambience_style", self._setting_value("v2_music_ambience_style", "sea_level")) or "sea_level")
        return normalize_music_ambience_style(style)

    def _current_music_ambience_control_values(self) -> dict[str, float | int]:
        return {
            "ambient_intensity": max(0.0, min(1.0, int(self.wave_slider.value()) / 100.0)),
            "music_flow_intensity": max(0.0, min(1.0, int(self.music_flow_slider.value()) / 100.0)),
            "compact_duotone_balance": int(getattr(self.island_widget, "compact_duotone_balance", 50)),
            "expanded_duotone_balance": int(getattr(self.island_widget, "expanded_duotone_balance", 50)),
        }

    def _save_music_ambience_values_for_style(self, style: str | None = None):
        if not all(hasattr(self, name) for name in ("wave_slider", "music_flow_slider")):
            return
        style = style or self._current_music_ambience_style()
        if style not in DEFAULT_MUSIC_AMBIENCE_VALUES:
            return
        values = self._music_ambience_values()
        current_values = self._current_music_ambience_control_values()
        values[style] = current_values
        self._save("v2_music_ambience_values", values)
        if style == self._current_music_ambience_style():
            self._save_legacy_music_ambience_values(current_values)

    def _save_legacy_music_ambience_values(self, values: dict[str, float | int]):
        self._save("v2_ambient_intensity", float(values["ambient_intensity"]))
        self._save("v2_music_flow_intensity", float(values["music_flow_intensity"]))
        self._save("v2_compact_duotone_balance", int(values["compact_duotone_balance"]))
        self._save("v2_expanded_duotone_balance", int(values["expanded_duotone_balance"]))

    def _apply_music_ambience_values_for_style(self, style: str):
        values = self._music_ambience_values().get(style, DEFAULT_MUSIC_AMBIENCE_VALUES["sea_level"])
        ambient = max(0.0, min(1.0, float(values.get("ambient_intensity", 0.0))))
        flow = max(0.0, min(1.0, float(values.get("music_flow_intensity", 0.74))))
        compact = max(0, min(100, int(values.get("compact_duotone_balance", 50))))
        expanded = max(0, min(100, int(values.get("expanded_duotone_balance", 50))))
        self.island_widget.ambient_intensity = ambient
        if hasattr(self.island_widget, "set_music_flow_intensity"):
            self.island_widget.set_music_flow_intensity(flow, notify=False)
        else:
            self.island_widget.music_flow_intensity = flow
        if hasattr(self.island_widget, "set_duotone_balance"):
            self.island_widget.set_duotone_balance(compact=compact, expanded=expanded, notify=False)
        else:
            self.island_widget.compact_duotone_balance = compact
            self.island_widget.expanded_duotone_balance = expanded
        if hasattr(self, "wave_slider"):
            self._set_slider_value(self.wave_slider, int(round(ambient * 100)))
            self._set_slider_value(self.music_flow_slider, int(round(flow * 100)))
        self._save_legacy_music_ambience_values(
            {
                "ambient_intensity": ambient,
                "music_flow_intensity": flow,
                "compact_duotone_balance": compact,
                "expanded_duotone_balance": expanded,
            }
        )
        if hasattr(self.island_widget, "update"):
            self.island_widget.update()

    def _update_music_ambience_controls(self):
        if not hasattr(self, "music_ambience_style"):
            return
        allowed = self._music_background_options_allowed()
        current_style = self._current_music_ambience_style()
        raw_style = str(getattr(self.island_widget, "music_ambience_style", current_style) or "sea_level")
        if raw_style != current_style:
            current_style = normalize_music_ambience_style(raw_style)
            if hasattr(self.island_widget, "set_music_ambience_style"):
                self.island_widget.set_music_ambience_style(current_style, notify=False)
            else:
                self.island_widget.music_ambience_style = current_style
            self._save("v2_music_ambience_style", current_style)
            self._apply_music_ambience_values_for_style(current_style)
        for index in range(self.music_ambience_style.count()):
            style = str(self.music_ambience_style.itemData(index) or "")
            item = self.music_ambience_style.model().item(index)
            if item is not None:
                item.setEnabled(style in allowed)
        self._set_combo_data(self.music_ambience_style, current_style)
        if hasattr(self, "_music_ambience_detail_toggle"):
            self._set_detail_panel_title(self._music_ambience_detail_toggle, f"{self.music_ambience_style.currentText()}背景")
        reactive_slider = getattr(self, "music_reactive_background_intensity", None)
        if reactive_slider is not None:
            reactive_slider.setEnabled(bool(getattr(self.island_widget, "music_reactive_background_enabled", False)))
        self._update_music_ambience_control_visibility(current_style)

    def _set_slider_row_visible(self, slider: QSlider, visible: bool, title: str | None = None):
        row = slider.parentWidget()
        if row is not None:
            row.setVisible(bool(visible))
        title_label = getattr(slider, "_title_label", None)
        if title is not None and title_label is not None:
            title_label.setText(title)

    def _update_music_ambience_control_visibility(self, style: str):
        if not all(hasattr(self, name) for name in ("wave_slider", "music_flow_slider")):
            return
        style = str(style or "sea_level")
        if hasattr(self, "_music_ambience_controls"):
            self._music_ambience_controls.setVisible(style != "system")
        self._set_slider_row_visible(self.wave_slider, style == "sea_level", "饱和光强度")
        self._set_slider_row_visible(self.music_flow_slider, style == "sea_level", "流光浮动强度")
        fallback_button = getattr(self, "solid_album_background_fallback", None)
        if fallback_button is not None:
            row = fallback_button.parentWidget()
            if row is not None:
                row.setVisible(style == "solid_album")
            fallback_button.setEnabled(style == "solid_album")

    def _music_background_options_allowed(self) -> set[str]:
        return set(DEFAULT_MUSIC_AMBIENCE_VALUES)

    def _set_combo_text(self, combo: QComboBox, text: str):
        index = combo.findText(text)
        if index < 0:
            combo.addItem(text)
            index = combo.findText(text)
        combo.setCurrentIndex(index)

    def _set_combo_data(self, combo: QComboBox, data):
        combo.blockSignals(True)
        for index in range(combo.count()):
            if combo.itemData(index) == data:
                combo.setCurrentIndex(index)
                break
        combo.blockSignals(False)

    def _update_previews(self):
        preview = getattr(self, "music_preview", None)
        if isinstance(preview, MusicAmbiencePreview):
            style = self._current_music_ambience_style()
            values = self._music_ambience_values().get(style, DEFAULT_MUSIC_AMBIENCE_VALUES["sea_level"])
            album_color = QColor(getattr(self.island_widget, "album_color", QColor("#16BCFF")))
            solid_color = getattr(self.island_widget, "_solid_album_background_color", None)
            if style == "solid_album" and callable(solid_color):
                album_color = QColor(solid_color())
            secondary_color = QColor(getattr(self.island_widget, "secondary_color", QColor("#FF3A94")))
            preview.set_preview_state(
                style,
                values,
                album_color=album_color,
                secondary_color=secondary_color,
                playing=bool(getattr(self.island_widget, "media_is_playing", True)),
            )

    def _save(self, key: str, value):
        if self.settings:
            self.settings.set(key, value)

    def _music_background_follow_previous_style(self) -> str:
        value = str(self._setting_value("v2_music_background_follow_previous_style", "") or "").strip().lower()
        if value and value in self._music_background_options_allowed() and music_ambience_theme_category(value) == "dark":
            return value
        return ""

    def _set_music_background_follow_previous_style(self, style: str):
        value = str(style or "").strip().lower()
        if value and (value not in self._music_background_options_allowed() or music_ambience_theme_category(value) != "dark"):
            value = ""
        self._save("v2_music_background_follow_previous_style", value)
        if hasattr(self.island_widget, "music_background_follow_previous_style"):
            self.island_widget.music_background_follow_previous_style = value

    def _set_music_background_style_for_theme(self, style: str) -> bool:
        style = normalize_music_ambience_style(style)
        if style == self._current_music_ambience_style():
            return False
        if hasattr(self.island_widget, "set_music_ambience_style"):
            self.island_widget.set_music_ambience_style(style, notify=False)
        else:
            self.island_widget.music_ambience_style = style
            if hasattr(self.island_widget, "update"):
                self.island_widget.update()
        self._save("v2_music_ambience_style", style)
        if hasattr(self, "music_ambience_style"):
            self._set_combo_data(self.music_ambience_style, style)
            self._apply_music_ambience_values_for_style(style)
            self._update_music_ambience_controls()
        self._update_lyric_flow_controls()
        self._update_previews()
        return True

    def _sync_music_background_to_theme(self, force: bool = False) -> bool:
        enabled = bool(self._setting_value("v2_music_background_follow_theme", False))
        current_style = self._current_music_ambience_style()
        previous_style = self._music_background_follow_previous_style()
        if current_style == "system" and not previous_style:
            self._update_previews()
            return False
        target_style = normalize_music_ambience_style(music_ambience_style_for_theme(
            self._settings_dark_mode(),
            current_style,
            follow_enabled=enabled,
            previous_dark_style=previous_style,
        ))
        if target_style == current_style:
            if self._settings_dark_mode() and previous_style:
                self._set_music_background_follow_previous_style("")
            return False
        if not self._settings_dark_mode() and music_ambience_theme_category(current_style) == "dark":
            self._set_music_background_follow_previous_style(current_style)
        changed = self._set_music_background_style_for_theme(target_style)
        if self._settings_dark_mode():
            self._set_music_background_follow_previous_style("")
        return changed

    def _refresh_theme_after_setting_change(self):
        self._apply_theme()
        self._refresh_section_labels()
        self._refresh_settings_sidebar_icons()
        self._sync_music_background_to_theme(force=True)
        self._update_theme_schedule_controls()
        for combo in self.findChildren(QComboBox):
            self._style_combo_popup(combo)
        for switch in self.findChildren(IosSwitch):
            switch.update()
        if hasattr(self.island_widget, "update"):
            self.island_widget.update()

    def _setting_value(self, key: str, default=None):
        if self.settings:
            return self.settings.get(key, default)
        return default

    def _imported_settings_fonts(self) -> list[str]:
        raw = self._setting_value("v2_imported_settings_fonts", [])
        if not isinstance(raw, (list, tuple, set)):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

    def _settings_font_items(self) -> list[tuple[str, str]]:
        items = [(family, family) for family in self.FONT_CHOICES]
        existing = {value for _label, value in items}
        for family in self._imported_settings_fonts():
            if family not in existing:
                items.append((family, family))
                existing.add(family)
        return items

    def _refresh_settings_font_combo(self):
        current = str(self._setting_value("v2_settings_font_family", self.FONT_CHOICES[0]) or self.FONT_CHOICES[0])
        self.settings_font_combo.blockSignals(True)
        self.settings_font_combo.clear()
        for label, value in self._settings_font_items():
            self.settings_font_combo.addItem(label, value)
        self.settings_font_combo.blockSignals(False)
        self._set_combo_data(self.settings_font_combo, current)
        self._style_combo_popup(self.settings_font_combo)
        self._update_settings_font_status()

    def _apply_settings_font(self):
        requested = str(DEFAULT_SETTINGS["v2_settings_font_family"]).strip()
        family = self._resolve_settings_font_family(requested)
        app = QApplication.instance()
        font = QFont(app.font() if app is not None else QFont())
        candidates = list(self.SETTINGS_FONT_FALLBACKS.get(requested, (requested,)))
        if family not in candidates:
            candidates.insert(0, family)
        font.setFamilies(candidates)
        font.setPointSize(9)
        font.setWeight(self.SETTINGS_FONT_WEIGHTS.get(requested, QFont.Weight.Normal))
        self.setFont(font)
        for widget in self.findChildren(QWidget):
            widget.setFont(font)
        self._update_settings_font_status(family)

    def _resolve_settings_font_family(self, requested: str) -> str:
        try:
            from local.island_stack_visual import IslandStackVisual

            IslandStackVisual.load_embedded_lyric_fonts()
        except Exception:
            pass
        families = set(QFontDatabase.families())
        for candidate in self.SETTINGS_FONT_FALLBACKS.get(requested, (requested,)):
            if candidate in families:
                return candidate
        return requested

    def _update_settings_font_status(self, actual_family: str | None = None):
        if not hasattr(self, "settings_font_status"):
            return
        requested = str(self._setting_value("v2_settings_font_family", self.FONT_CHOICES[0]) or self.FONT_CHOICES[0]).strip()
        actual = actual_family or self._resolve_settings_font_family(requested)
        if actual == requested:
            text = f"已即时应用：{actual}"
        else:
            text = f"已即时应用：{actual}（{requested} 不可用时自动替代）"
        self.settings_font_status.setText(text)

    def _enabled_plugins_setting(self) -> list[str]:
        known = ["time", "weather", "agenda"]
        raw = getattr(self.island_widget, "enabled_plugin_ids", None)
        if raw is None:
            raw = self._setting_value("v2_enabled_plugins", known)
        if isinstance(raw, (list, tuple, set)):
            enabled = {str(item or "").strip().casefold() for item in raw}
            return [plugin_id for plugin_id in known if plugin_id in enabled]
        try:
            count = max(0, min(len(known), int(getattr(self.island_widget, "enabled_plugin_count", len(known)))))
        except (TypeError, ValueError):
            count = len(known)
        return known[:count]

    def _apply_enabled_plugins(self, plugin_ids: list[str]):
        known = ["time", "weather", "agenda"]
        enabled = [plugin_id for plugin_id in known if plugin_id in {str(item).casefold() for item in plugin_ids}]
        self.island_widget.enabled_plugin_ids = set(enabled)
        self.island_widget.enabled_plugin_count = len(enabled)
        if hasattr(self.island_widget, "_set_plugin_focus"):
            self.island_widget._set_plugin_focus(min(getattr(self.island_widget, "plugin_focus_target", 0), max(0, len(enabled) - 1)), immediate=True)
        if hasattr(self.island_widget, "update"):
            self.island_widget.update()

    def closeEvent(self, event):
        if hasattr(self.island_widget, "set_settings_window_active"):
            self.island_widget.set_settings_window_active(False)
        if self.settings and hasattr(self.settings, "save_now"):
            self.settings.save_now()
        super().closeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)

    def hideEvent(self, event):
        if hasattr(self.island_widget, "set_settings_window_active"):
            self.island_widget.set_settings_window_active(False)
        super().hideEvent(event)

    def _settings_page_changed(self, row: int):
        self._collapse_detail_panels()
        if row != self._barrage_page_index and hasattr(self.island_widget, "set_settings_window_active"):
            self.island_widget.set_settings_window_active(False)
        if row == self._diagnostics_page_index:
            self._refresh_diagnostics()

    def _refresh_diagnostics(self):
        diagnostics = {}
        try:
            diag_fn = getattr(self.island_widget, "provider_diagnostics", None)
            if callable(diag_fn):
                diagnostics = diag_fn()
        except Exception as exc:
            diagnostics = {"provider_status": f"error: {exc}"}
        values = format_settings_diagnostics(
            diagnostics,
            music_player_mode=self._music_player_mode_setting(),
            music_player_mode_label=self._music_player_mode_label,
        )
        for key, label in self._diagnostic_labels.items():
            label.setText(str(values.get(key, "-")))

    def _export_diagnostics(self, _checked: bool = False):
        label = getattr(self, "_diagnostic_export_status", None)
        try:
            export_fn = getattr(self.island_widget, "export_runtime_diagnostics", None)
            if not callable(export_fn):
                raise RuntimeError("diagnostic export is not available")
            path = export_fn()
            if label is not None:
                label.setText(f"已导出：{path}")
        except Exception as exc:
            if label is not None:
                label.setText(f"导出失败：{exc}")

    def _music_player_mode_label(self, mode: object) -> str:
        mode = str(mode or "netease")
        for label, value in self.MUSIC_PLAYER_MODES:
            if value == mode:
                return label
        return mode

    def _music_player_mode_setting(self) -> str:
        mode = str(self._setting_value("v2_music_player_mode", "netease") or "netease").strip().lower()
        if mode in {"", "auto", "system"}:
            return "netease"
        return mode if mode in {"netease", "netease_betterncm", "qishui", "qq", "kugou"} else "netease"

    def _current_settings_page_index(self) -> int:
        return int(self.stack.currentIndex()) if hasattr(self, "stack") else -1

    def _begin_barrage_adjust_preview(self):
        if self._current_settings_page_index() != self._barrage_page_index:
            return
        self._barrage_adjust_generation += 1
        generation = self._barrage_adjust_generation
        if hasattr(self.island_widget, "show_barrage_preview"):
            self.island_widget.show_barrage_preview()

        def release():
            if generation == self._barrage_adjust_generation and hasattr(self.island_widget, "set_settings_window_active"):
                self.island_widget.set_settings_window_active(False)

        QTimer.singleShot(6000, release)

    def _begin_plugin_alert_adjust_preview(self):
        if self._current_settings_page_index() != self._plugin_page_index:
            return
        if hasattr(self.island_widget, "show_plugin_fullscreen_alert_preview"):
            self.island_widget.show_plugin_fullscreen_alert_preview()

    def _touch(self):
        self.island_widget._enforce_stack_limit()
        self.island_widget._pulse_stack()
        self.island_widget.update()

    def _set_music_playing(self, enabled: bool):
        self.island_widget.music_playing = enabled
        self._save("v2_music_playing", enabled)
        self._touch()

    def _set_music_auto_detect(self, enabled: bool):
        self.island_widget.music_auto_detect = bool(enabled)
        self._save("v2_music_auto_detect", bool(enabled))
        self._touch()

    def _set_playback_source(self, key: str, enabled: bool):
        other = "v2_system_capture_enabled" if key == "v2_owned_playback_enabled" else "v2_owned_playback_enabled"
        control = self.owned_playback_switch if key == "v2_owned_playback_enabled" else self.system_capture_switch
        if not enabled and not bool(self._setting_value(other, other == "v2_owned_playback_enabled")):
            self._set_switch_checked(control, True)
            return
        self._save(key, bool(enabled))
        self._touch()

    def _open_online_music_login(self):
        callback = getattr(self, "online_music_login_callback", None)
        if callable(callback):
            callback()

    def _open_personal_dj_voices(self):
        callback = getattr(self, "personal_dj_voice_callback", None)
        if callable(callback):
            callback()

    def _set_persistent_plugin(self, enabled: bool):
        self.island_widget.persistent_plugin = enabled
        self._save("v2_persistent_plugin", enabled)
        self._touch()

    def _set_super_topmost(self, enabled: bool):
        if hasattr(self.island_widget, "set_super_topmost"):
            self.island_widget.set_super_topmost(bool(enabled), notify=False)
        else:
            self.island_widget.super_topmost_enabled = bool(enabled)
        self._save("v2_super_topmost", bool(enabled))
        self._touch()

    def _set_location_mode(self, _index: int = -1):
        if not isinstance(getattr(self, "location_mode", None), QComboBox):
            return
        mode = str(self.location_mode.currentData() or "free")
        if hasattr(self.island_widget, "set_location_mode"):
            self.island_widget.set_location_mode(mode, notify=False)
            mode = str(getattr(self.island_widget, "location_mode", mode))
        else:
            self.island_widget.location_mode = mode
            self.island_widget.finder_mode = mode == "finder"
            self.island_widget.update()
        self._save("v2_location_mode", mode)
        self._save("v2_finder_mode", mode == "finder")
        self._refresh_location_mode_options()
        self._set_combo_data(self.location_mode, mode)
        self._update_taskbar_controls()
        self._update_lyric_flow_controls()
        self._touch()

    def _set_placement_mode(self, _index: int = -1):
        mode = str(self.placement_mode.currentData() or "free")
        if mode not in {value for _label, value in self._location_mode_items()}:
            self._refresh_placement_mode_options()
            return
        setter = getattr(self.island_widget, "set_location_mode", None)
        if callable(setter):
            setter(mode, notify=False)
        else:
            self.island_widget.location_mode = mode
            self.island_widget.finder_mode = mode == "finder"
        self._refresh_placement_mode_options()
        self._update_taskbar_controls()
        self._update_lyric_flow_controls()
        self._touch()

    def _set_finder_bar_style(self, _index: int = -1):
        style = normalize_finder_bar_style(self.finder_bar_style.currentData())
        setter = getattr(self.island_widget, "set_finder_bar_style", None)
        if callable(setter):
            setter(style, notify=False)
        else:
            self.island_widget.finder_bar_style = style
        self._save("v2_finder_bar_style", style)
        self._touch()

    def _set_top_notch_height_mode(self, _index: int = -1):
        if not isinstance(getattr(self, "top_notch_height_mode", None), QComboBox):
            return
        mode = str(self.top_notch_height_mode.currentData() or "standard")
        if hasattr(self.island_widget, "set_top_notch_height_mode"):
            self.island_widget.set_top_notch_height_mode(mode, notify=False)
            mode = str(getattr(self.island_widget, "top_notch_height_mode", mode))
        else:
            self.island_widget.top_notch_height_mode = mode
            if hasattr(self.island_widget, "update"):
                self.island_widget.update()
        self._save("v2_top_notch_height_mode", mode)
        self._set_combo_data(self.top_notch_height_mode, mode)
        self._touch()

    def _set_position_preset(self, _index: int = -1):
        preset = str(self.position_preset.currentData() or "last")
        if hasattr(self.island_widget, "apply_position_preset"):
            self.island_widget.apply_position_preset(preset, notify=False)
            preset = str(getattr(self.island_widget, "position_preset", preset) or "last")
        else:
            self.island_widget.position_preset = preset
        self._save("v2_position_preset", preset)
        self._save("v2_location_mode", str(getattr(self.island_widget, "location_mode", self._setting_value("v2_location_mode", "free"))))
        self._set_combo_data(self.position_preset, preset)
        self._refresh_location_mode_options()
        self._update_taskbar_controls()
        self._update_lyric_flow_controls()
        self._touch()

    def _set_taskbar_corner_style(self, _index: int = -1):
        style = str(self.taskbar_corner_style.currentData() or "win11")
        if hasattr(self.island_widget, "set_taskbar_corner_style"):
            self.island_widget.set_taskbar_corner_style(style, notify=False)
        else:
            self.island_widget.taskbar_corner_style = style
            self.island_widget.update()
        self._save("v2_taskbar_corner_style", style)
        self._touch()

    def _set_taskbar_auto_hide_mode(self, _index: int = -1):
        mode = str(self.taskbar_auto_hide_mode.currentData() or "auto")
        if hasattr(self.island_widget, "set_taskbar_auto_hide_mode"):
            self.island_widget.set_taskbar_auto_hide_mode(mode, notify=False)
            mode = str(getattr(self.island_widget, "taskbar_auto_hide_mode", mode))
        else:
            self.island_widget.taskbar_auto_hide_mode = mode
        self._save("v2_taskbar_auto_hide_mode", mode)
        self._touch()

    def _set_taskbar_left_handle_color(self):
        color = self.taskbar_left_handle_color.color()
        if hasattr(self.island_widget, "set_taskbar_left_handle_color"):
            self.island_widget.set_taskbar_left_handle_color(color, notify=False)
            color = str(getattr(self.island_widget, "taskbar_left_handle_color", color))
        else:
            self.island_widget.taskbar_left_handle_color = color
            self.island_widget.update()
        self.taskbar_left_handle_color.set_color(color)
        self._save("v2_taskbar_left_handle_color", color)
        self._touch()

    def _refresh_location_mode_options(self):
        if not hasattr(self, "location_mode"):
            return
        current = str(getattr(self.island_widget, "location_mode", self._setting_value("v2_location_mode", "top")))
        if not isinstance(self.location_mode, QComboBox):
            self.location_mode.setText(self._location_mode_label())
            return
        items = self._location_mode_items()
        self.location_mode.blockSignals(True)
        self.location_mode.clear()
        for label, value in items:
            self.location_mode.addItem(label, value)
        if current not in {value for _label, value in items}:
            current = items[0][1] if items else "free"
            if hasattr(self.island_widget, "set_location_mode"):
                self.island_widget.set_location_mode(current, notify=False)
            else:
                self.island_widget.location_mode = current
            self._save("v2_location_mode", current)
        self.location_mode.blockSignals(False)
        self._set_combo_data(self.location_mode, current)
        self._style_combo_popup(self.location_mode)

    def _update_taskbar_controls(self):
        location = str(getattr(self.island_widget, "location_mode", self._setting_value("v2_location_mode", "free")) or "free")
        mydockfinder_running = bool(
            getattr(self.island_widget, "_mydockfinder_available", lambda: bool(getattr(self.island_widget, "mydockfinder_running", False)))()
        )
        tasker_available = location != "finder" and not mydockfinder_running
        for widget in (
            getattr(self, "taskbar_corner_style", None),
            getattr(self, "taskbar_auto_hide_mode", None),
            getattr(self, "taskbar_left_handle_color", None),
        ):
            if widget is not None:
                widget.setEnabled(tasker_available)
        top_height = getattr(self, "top_notch_height_mode", None)
        if top_height is not None:
            top_height.setEnabled(not mydockfinder_running)
        for indicator in (
            getattr(self, "taskbar_corner_status", None),
            getattr(self, "taskbar_auto_hide_status", None),
            getattr(self, "taskbar_left_handle_status", None),
        ):
            if indicator is not None:
                self._set_status_indicator(indicator, tasker_available, "可选" if tasker_available else "不可选")
        if hasattr(self, "mydockfinder_status_indicator"):
            self._set_status_indicator(
                self.mydockfinder_status_indicator,
                mydockfinder_running,
                "已检测" if mydockfinder_running else "未检测",
            )
        if hasattr(self, "myfinder_status_indicator"):
            enabled, visible = self._finder_bar_presence()
            self._set_status_indicator(
                self.myfinder_status_indicator, enabled,
                "已显示" if visible else "已隐藏" if enabled else "未开启",
            )
        if hasattr(self, "location_mode") and not isinstance(self.location_mode, QComboBox):
            self.location_mode.setText(self._location_mode_label())

    def _browse_mydockfinder_path(self, _checked: bool = False):
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "选择 MyDockFinder 应用",
            str(self.mydockfinder_path_input.text() or ""),
            "Executable Files (*.exe);;All Files (*)",
        )
        if not path:
            return
        self.mydockfinder_path_input.setText(path)
        self._set_mydockfinder_path()

    def _set_mydockfinder_path(self):
        value = str(self.mydockfinder_path_input.text() or "").strip().strip('"')
        self.mydockfinder_path_input.setText(value)
        if hasattr(self.island_widget, "set_mydockfinder_path"):
            self.island_widget.set_mydockfinder_path(value, notify=False)
        else:
            self.island_widget.mydockfinder_path = value
        self._save("v2_mydockfinder_path", value)
        if hasattr(self, "mydockfinder_status_label"):
            self.mydockfinder_status_label.setText(self._mydockfinder_status_text())
        self._update_taskbar_controls()
        self._touch()

    def _set_mydockfinder_listener_process(self, process_name: str, enabled: bool):
        normalized = normalize_mydockfinder_listener_process_names((process_name,))
        if not normalized:
            return
        process_name = normalized[0]
        selected = set(self._mydockfinder_listener_process_names_setting())
        if enabled:
            selected.add(process_name)
        else:
            selected.discard(process_name)
        ordered = list(normalize_mydockfinder_listener_process_names(selected))
        if hasattr(self.island_widget, "set_mydockfinder_listener_process_names"):
            self.island_widget.set_mydockfinder_listener_process_names(ordered, notify=False)
        else:
            self.island_widget.mydockfinder_listener_process_names = tuple(ordered)
        self._save("v2_mydockfinder_listener_process_names", ordered)
        self._sync_mydockfinder_process_checkboxes()
        if hasattr(self, "mydockfinder_status_label"):
            self.mydockfinder_status_label.setText(self._mydockfinder_status_text())
        self._update_taskbar_controls()
        self._touch()

    def _open_app_location(self, _checked: bool = False):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1])))

    def _sync_icon_preview(self):
        icon_paths = resolve_icon_paths(self._setting_value("v2_custom_icon_path", ""))
        label = getattr(self, "custom_icon_path_label", None)
        if label is not None:
            custom_path = str(self._setting_value("v2_custom_icon_path", "") or "").strip()
            label.setText(custom_path or "默认图标")
        preview = getattr(self, "app_icon_preview", None)
        if preview is not None:
            pixmap = QPixmap(str(icon_paths.preview_icon))
            if not pixmap.isNull():
                preview.setPixmap(pixmap.scaled(58, 58, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def _choose_custom_icon(self, _checked: bool = False):
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "上传图标",
            "",
            "Image Files (*.png *.jpg *.jpeg *.bmp *.webp *.ico);;All Files (*)",
        )
        if not path:
            return
        try:
            installed = install_custom_icon(path)
        except Exception as exc:
            if hasattr(self, "custom_icon_path_label"):
                self.custom_icon_path_label.setText(f"图标导入失败：{exc}")
            return
        ico_path = str(installed.get("ico_path", "") or "")
        self._save("v2_custom_icon_path", ico_path)
        self._sync_icon_preview()
        callback = getattr(self, "icon_changed_callback", None)
        if callable(callback):
            callback(ico_path)

    def _reset_custom_icon(self, _checked: bool = False):
        self._save("v2_custom_icon_path", "")
        self._sync_icon_preview()
        callback = getattr(self, "icon_changed_callback", None)
        if callable(callback):
            callback("")

    def _set_message_detection(self, enabled: bool):
        self._save("v2_enable_message_detection", bool(enabled))
        self._update_message_controls()
        self._refresh_diagnostics()
        self._touch()

    def _set_wechat_notification(self, enabled: bool):
        self._save("v2_enable_wechat_notification", bool(enabled))
        if not enabled and hasattr(self, "wechat_detail_checkbox") and self.wechat_detail_checkbox.isChecked():
            self.wechat_detail_checkbox.blockSignals(True)
            self.wechat_detail_checkbox.setChecked(False)
            self.wechat_detail_checkbox.blockSignals(False)
            self._save("v2_enable_wechat_detail_notification", False)
        self._update_message_controls()
        self._refresh_diagnostics()
        self._touch()

    def _set_wechat_detail_notification(self, enabled: bool):
        enabled = bool(enabled and getattr(self, "wechat_notification_checkbox", None) and self.wechat_notification_checkbox.isChecked())
        if hasattr(self, "wechat_detail_checkbox") and self.wechat_detail_checkbox.isChecked() != enabled:
            self.wechat_detail_checkbox.blockSignals(True)
            self.wechat_detail_checkbox.setChecked(enabled)
            self.wechat_detail_checkbox.blockSignals(False)
        self._save("v2_enable_wechat_detail_notification", enabled)
        self._update_message_controls()
        self._refresh_diagnostics()
        self._touch()

    def _set_qq_notification(self, enabled: bool):
        self._save("v2_enable_qq_notification", bool(enabled))
        if not enabled and hasattr(self, "qq_detail_checkbox") and self.qq_detail_checkbox.isChecked():
            self.qq_detail_checkbox.blockSignals(True)
            self.qq_detail_checkbox.setChecked(False)
            self.qq_detail_checkbox.blockSignals(False)
            self._save("v2_enable_qq_detail_notification", False)
        self._update_message_controls()
        self._refresh_diagnostics()
        self._touch()

    def _set_qq_detail_notification(self, enabled: bool):
        enabled = bool(enabled and getattr(self, "qq_notification_checkbox", None) and self.qq_notification_checkbox.isChecked())
        if hasattr(self, "qq_detail_checkbox") and self.qq_detail_checkbox.isChecked() != enabled:
            self.qq_detail_checkbox.blockSignals(True)
            self.qq_detail_checkbox.setChecked(enabled)
            self.qq_detail_checkbox.blockSignals(False)
        self._save("v2_enable_qq_detail_notification", enabled)
        self._update_message_controls()
        self._refresh_diagnostics()
        self._touch()

    def _set_dingtalk_notification(self, enabled: bool):
        self._save("v2_enable_dingtalk_notification", bool(enabled))
        if not enabled and hasattr(self, "dingtalk_detail_checkbox") and self.dingtalk_detail_checkbox.isChecked():
            self.dingtalk_detail_checkbox.blockSignals(True)
            self.dingtalk_detail_checkbox.setChecked(False)
            self.dingtalk_detail_checkbox.blockSignals(False)
            self._save("v2_enable_dingtalk_detail_notification", False)
        self._update_message_controls()
        self._refresh_diagnostics()
        self._touch()

    def _set_dingtalk_detail_notification(self, enabled: bool):
        enabled = bool(enabled and getattr(self, "dingtalk_notification_checkbox", None) and self.dingtalk_notification_checkbox.isChecked())
        if hasattr(self, "dingtalk_detail_checkbox") and self.dingtalk_detail_checkbox.isChecked() != enabled:
            self.dingtalk_detail_checkbox.blockSignals(True)
            self.dingtalk_detail_checkbox.setChecked(enabled)
            self.dingtalk_detail_checkbox.blockSignals(False)
        self._save("v2_enable_dingtalk_detail_notification", enabled)
        self._update_message_controls()
        self._refresh_diagnostics()
        self._touch()

    def _set_manual_finder_available(self, enabled: bool):
        if hasattr(self.island_widget, "set_manual_finder_available"):
            self.island_widget.set_manual_finder_available(enabled, notify=False)
        elif hasattr(self.island_widget, "set_mydockfinder_running"):
            self.island_widget.set_mydockfinder_running(enabled, notify=False)
        else:
            self.island_widget.manual_finder_available = enabled
            if enabled and str(getattr(self.island_widget, "location_mode", "free")) == "tasker":
                self.island_widget.location_mode = "free"
        self._save("v2_manual_mydockfinder", enabled)
        self._refresh_location_mode_options()
        self._save("v2_location_mode", str(getattr(self.island_widget, "location_mode", self._setting_value("v2_location_mode", "free"))))
        self._update_taskbar_controls()
        self._touch()

    def _set_expanded(self, enabled: bool):
        if hasattr(self.island_widget, "_set_expanded"):
            self.island_widget._set_expanded(enabled)
        else:
            self.island_widget.expanded = enabled
        self._save("v2_expanded", enabled)
        self._touch()

    def _set_show_seconds(self, enabled: bool):
        self.island_widget.show_seconds = enabled
        self._save("v2_show_seconds", enabled)
        for checkbox_name in ("seconds_checkbox", "time_seconds_checkbox", "empty_time_seconds_checkbox"):
            checkbox = getattr(self, checkbox_name, None)
            if checkbox is None:
                continue
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(enabled))
            checkbox.blockSignals(False)
        self._touch()

    def _set_high_quality_font_rendering(self, enabled: bool):
        if hasattr(self.island_widget, "set_high_quality_font_rendering"):
            self.island_widget.set_high_quality_font_rendering(enabled, notify=False)
        else:
            self.island_widget.high_quality_font_rendering = bool(enabled)
            self.island_widget.update()
        self._save("v2_high_quality_font_rendering", bool(enabled))

    def _set_settings_font_family(self, _index: int = -1):
        family = str(self.settings_font_combo.currentData() or self.FONT_CHOICES[0]).strip() or self.FONT_CHOICES[0]
        self._save("v2_settings_font_family", family)
        self._apply_settings_font()

    def _import_settings_font(self):
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "导入字体",
            "",
            "Font Files (*.ttf *.otf *.ttc *.otc);;All Files (*)",
        )
        if not path:
            return
        font_id = QFontDatabase.addApplicationFont(path)
        if font_id < 0:
            return
        families = QFontDatabase.applicationFontFamilies(font_id)
        if not families:
            return
        family = str(families[0])
        imported = self._imported_settings_fonts()
        if family not in imported and family not in self.FONT_CHOICES:
            imported.append(family)
            self._save("v2_imported_settings_fonts", imported)
        self._refresh_settings_font_combo()
        self._set_combo_data(self.settings_font_combo, family)
        self._set_settings_font_family()

    def _set_theme_mode(self, _index: int = -1):
        mode = str(self.theme_mode.currentData() or "system")
        if mode not in {"system", "light", "dark", "schedule"}:
            mode = "system"
        self._save("v2_theme_mode", mode)
        self._update_theme_schedule_controls()
        self._refresh_theme_after_setting_change()

    def _set_music_background_follow_theme(self, enabled: bool):
        self._save("v2_music_background_follow_theme", bool(enabled))
        self._update_music_background_follow_dependency_controls()
        self._sync_music_background_to_theme(force=True)

    def _set_tasker_follow_taskbar_color(self, enabled: bool):
        value = bool(enabled)
        if hasattr(self.island_widget, "set_tasker_follow_taskbar_color"):
            self.island_widget.set_tasker_follow_taskbar_color(value, notify=False)
        else:
            self.island_widget.tasker_follow_taskbar_color = value
        self._save("v2_tasker_follow_taskbar_color", value)
        self._update_music_background_follow_dependency_controls()

    def _set_startup_mode(self, _index: int = -1):
        mode = normalize_startup_mode(str(self.startup_mode.currentData() or "none"))
        previous = normalize_startup_mode(str(self._setting_value("v2_startup_mode", "none") or "none"))
        applied = StartupManager().apply(mode, build_startup_command())
        if not applied:
            self._set_combo_data(self.startup_mode, previous)
            return
        self._save("v2_startup_mode", mode)

    def _normalize_theme_time(self, value: str, fallback: str) -> str:
        text = str(value or "").strip()
        if not text:
            text = fallback
        parts = text.replace(".", ":").split(":", 1)
        try:
            hour = max(0, min(23, int(parts[0])))
            minute = max(0, min(59, int(parts[1][:2]) if len(parts) > 1 and parts[1] else 0))
            return f"{hour:02d}:{minute:02d}"
        except ValueError:
            return fallback

    def _set_theme_dark_start(self):
        value = self._normalize_theme_time(self.theme_dark_start.text(), "22:00")
        self.theme_dark_start.setText(value)
        self._save("v2_theme_dark_start", value)
        self._refresh_theme_after_setting_change()

    def _set_theme_dark_end(self):
        value = self._normalize_theme_time(self.theme_dark_end.text(), "07:00")
        self.theme_dark_end.setText(value)
        self._save("v2_theme_dark_end", value)
        self._refresh_theme_after_setting_change()

    def _set_no_lyrics_text(self, value: str):
        text = " ".join(str(value or "暂无歌词").split())[:16] or "暂无歌词"
        if text != value:
            cursor = min(self.no_lyrics_input.cursorPosition(), len(text))
            self.no_lyrics_input.blockSignals(True)
            self.no_lyrics_input.setText(text)
            self.no_lyrics_input.setCursorPosition(cursor)
            self.no_lyrics_input.blockSignals(False)
        if hasattr(self.island_widget, "set_no_lyrics_text"):
            self.island_widget.set_no_lyrics_text(text, notify=False)
        else:
            self.island_widget.no_lyrics_text = text
            self.island_widget.update()
        self._save("v2_no_lyrics_text", text)

    def _set_lyric_flow_style(self, _index: int = -1):
        style = str(self.lyric_flow_style.currentData() or "wave_stream")
        if hasattr(self.island_widget, "set_lyric_flow_style"):
            self.island_widget.set_lyric_flow_style(style, notify=False)
        else:
            self.island_widget.lyric_flow_style = style
            self.island_widget.update()
        self._save("v2_lyric_flow_style", style)
        actual_style = str(getattr(self.island_widget, "music_ambience_style", self._setting_value("v2_music_ambience_style", "sea_level")) or "sea_level")
        self._save("v2_music_ambience_style", actual_style)
        if hasattr(self, "music_ambience_style"):
            self._apply_music_ambience_values_for_style(actual_style)
            self._update_music_ambience_controls()
        self._update_lyric_flow_controls()
        self._collapse_detail_panels()
        self._touch()

    def _set_gelv_color_fill_enabled(self, enabled: bool):
        enabled = bool(enabled)
        self._set_island_bool_setting("gelv_color_fill_enabled", enabled)
        self._set_switch_checked(getattr(self, "gelv_color_fill_checkbox", None), enabled)
        self._save("v2_gelv_color_fill_enabled", enabled)
        if hasattr(self, "music_ambience_style"):
            actual_style = self._current_music_ambience_style()
            self._save("v2_music_ambience_style", actual_style)
            self._apply_music_ambience_values_for_style(actual_style)
            self._update_music_ambience_controls()
        self._update_lyric_flow_controls()
        self._touch()

    def _set_gelv_word_fill_enabled(self, enabled: bool):
        enabled = bool(enabled)
        self._set_island_bool_setting("gelv_word_fill_enabled", enabled)
        self._set_switch_checked(getattr(self, "gelv_word_fill_checkbox", None), enabled)
        self._save("v2_gelv_word_fill_enabled", enabled)
        self._touch()

    def _set_wave_font_weight(self, _index: int = -1):
        weight = str(getattr(self, "wave_font_weight").currentData() or "bold")
        if weight not in {"bold", "heavy"}:
            weight = "bold"
        if hasattr(self.island_widget, "set_wave_font_weight"):
            self.island_widget.set_wave_font_weight(weight, notify=False)
            weight = str(getattr(self.island_widget, "wave_font_weight", weight) or weight)
        else:
            self.island_widget.wave_font_weight = weight
            self.island_widget.update()
        self._save("v2_wave_font_weight", weight)
        self._touch()

    def _set_ember_word_fill_enabled(self, enabled: bool):
        enabled = bool(enabled)
        self._set_island_bool_setting("ember_word_fill_enabled", enabled)
        self._set_switch_checked(getattr(self, "ember_word_fill_checkbox", None), enabled)
        self._save("v2_ember_word_fill_enabled", enabled)
        self._touch()

    def _set_ember_color_fill_enabled(self, enabled: bool):
        enabled = bool(enabled)
        self._set_island_bool_setting("ember_color_fill_enabled", enabled)
        self._set_switch_checked(getattr(self, "ember_color_fill_checkbox", None), enabled)
        self._save("v2_ember_color_fill_enabled", enabled)
        self._update_lyric_flow_controls()
        self._touch()

    def _set_ember_font_weight(self, _index: int = -1):
        weight = str(getattr(self, "ember_font_weight").currentData() or "normal")
        if weight not in {"normal", "bold"}:
            weight = "normal"
        if hasattr(self.island_widget, "set_ember_font_weight"):
            self.island_widget.set_ember_font_weight(weight, notify=False)
            weight = str(getattr(self.island_widget, "ember_font_weight", weight) or weight)
        else:
            self.island_widget.ember_font_weight = weight
            self.island_widget.update()
        self._save("v2_ember_font_weight", weight)
        self._touch()

    def _set_ember_font_size(self, value: int):
        size = max(12, min(24, int(value)))
        if hasattr(self.island_widget, "set_ember_font_size"):
            self.island_widget.set_ember_font_size(size, notify=False)
            size = int(getattr(self.island_widget, "ember_font_size", size))
        else:
            self.island_widget.ember_font_size = size
            self.island_widget.update()
        self._save("v2_ember_font_size", size)
        self._touch()

    def _set_ember_line_mode(self, _index: int = -1):
        mode = str(getattr(self, "ember_line_mode").currentData() or "split")
        if mode not in {"split", "scroll"}:
            mode = "split"
        if bool(getattr(self.island_widget, "ember_dual_line_enabled", self._setting_value("v2_ember_dual_line_enabled", False))):
            mode = "scroll"
        if hasattr(self.island_widget, "set_ember_line_mode"):
            self.island_widget.set_ember_line_mode(mode, notify=False)
            mode = str(getattr(self.island_widget, "ember_line_mode", mode) or mode)
        else:
            self.island_widget.ember_line_mode = mode
            self.island_widget.update()
        self._save("v2_ember_line_mode", mode)
        if hasattr(self, "ember_line_mode"):
            self._set_combo_data(self.ember_line_mode, mode)
        self._update_lyric_flow_controls()
        self._touch()

    def _set_ember_dual_line_enabled(self, enabled: bool):
        enabled = bool(enabled)
        if hasattr(self.island_widget, "set_ember_dual_line_enabled"):
            self.island_widget.set_ember_dual_line_enabled(enabled, notify=False)
        else:
            self.island_widget.ember_dual_line_enabled = enabled
            if enabled:
                self.island_widget.ember_line_mode = "scroll"
            self.island_widget.update()
        self._set_switch_checked(getattr(self, "ember_dual_line_checkbox", None), enabled)
        self._save("v2_ember_dual_line_enabled", enabled)
        if enabled:
            self._save("v2_ember_line_mode", "scroll")
            if hasattr(self, "ember_line_mode"):
                self._set_combo_data(self.ember_line_mode, "scroll")
        self._request_lyric_display_progress_follow()
        self._update_lyric_flow_controls()
        self._touch()

    def _set_ember_italic_enabled(self, enabled: bool):
        enabled = bool(enabled)
        if hasattr(self.island_widget, "set_ember_italic_enabled"):
            self.island_widget.set_ember_italic_enabled(enabled, notify=False)
        else:
            self.island_widget.ember_italic_enabled = enabled
            self.island_widget.update()
        self._set_switch_checked(getattr(self, "ember_italic_checkbox", None), enabled)
        self._save("v2_ember_italic_enabled", enabled)
        self._touch()

    def _set_ember_show_cover(self, enabled: bool):
        enabled = bool(enabled)
        if hasattr(self.island_widget, "set_ember_show_cover"):
            self.island_widget.set_ember_show_cover(enabled, notify=False)
        else:
            self.island_widget.ember_show_cover = enabled
            self.island_widget.update()
        self._set_switch_checked(getattr(self, "ember_show_cover_checkbox", None), enabled)
        self._save("v2_ember_show_cover", enabled)
        self._touch()

    def _set_gelv_font_family(self, _index: int = -1):
        from local.island_stack_visual import IslandStackVisual

        family = IslandStackVisual.GELV_FONT_FAMILY
        if hasattr(self.island_widget, "set_gelv_font_family"):
            self.island_widget.set_gelv_font_family(family, notify=False)
        else:
            self.island_widget.gelv_font_family = family
            self.island_widget.update()
        self._save("v2_gelv_font_family", family)
        self._touch()

    def _set_gelv_primary_font_size(self, value: int):
        size = max(14, min(30, int(value)))
        if hasattr(self.island_widget, "set_gelv_primary_font_size"):
            self.island_widget.set_gelv_primary_font_size(size, notify=False)
        else:
            self.island_widget.gelv_primary_font_size = size
            self.island_widget.update()
        self._save("v2_gelv_primary_font_size", size)
        self._touch()

    def _set_gelv_secondary_font_size(self, value: int):
        size = max(8, min(24, int(value)))
        if hasattr(self.island_widget, "set_gelv_secondary_font_size"):
            self.island_widget.set_gelv_secondary_font_size(size, notify=False)
        else:
            self.island_widget.gelv_secondary_font_size = size
            self.island_widget.update()
        self._save("v2_gelv_secondary_font_size", size)
        self._touch()

    def _set_gelv_dual_line_font_size(self, value: int):
        size = max(8, min(30, int(value)))
        if hasattr(self.island_widget, "set_gelv_dual_line_font_size"):
            self.island_widget.set_gelv_dual_line_font_size(size, notify=False)
        else:
            self.island_widget.gelv_dual_line_font_size = size
            self.island_widget.update()
        self._save("v2_gelv_dual_line_font_size", size)
        self._touch()

    def _set_gelv_initial_word_spacing(self, value: int):
        spacing = max(100, min(320, int(value)))
        if hasattr(self.island_widget, "set_gelv_initial_word_spacing"):
            self.island_widget.set_gelv_initial_word_spacing(spacing, notify=False)
        else:
            self.island_widget.gelv_initial_word_spacing = spacing
            self.island_widget.update()
        self._save("v2_gelv_initial_word_spacing", spacing)
        self._touch()

    def _set_gelv_word_spacing(self, value: int):
        spacing = max(100, min(420, int(value)))
        if hasattr(self.island_widget, "set_gelv_word_spacing"):
            self.island_widget.set_gelv_word_spacing(spacing, notify=False)
        else:
            self.island_widget.gelv_word_spacing = spacing
            self.island_widget.update()
        self._save("v2_gelv_word_spacing", spacing)
        self._touch()

    def _set_compact_lyric_show_cover(self, enabled: bool):
        enabled = bool(enabled)
        if hasattr(self.island_widget, "set_compact_lyric_show_cover"):
            self.island_widget.set_compact_lyric_show_cover(enabled, notify=False)
        else:
            self.island_widget.compact_lyric_show_cover = enabled
            self.island_widget.update()
        for checkbox in (getattr(self, "wave_compact_cover_checkbox", None), getattr(self, "gelv_compact_cover_checkbox", None)):
            if checkbox is None:
                continue
            checkbox.blockSignals(True)
            checkbox.setChecked(enabled)
            checkbox.blockSignals(False)
        self._save("v2_compact_lyric_show_cover", enabled)
        self._touch()

    def _set_ember_back_gray_mix(self, value: int):
        value = max(0, min(100, int(value)))
        if hasattr(self.island_widget, "set_ember_back_gray_mix"):
            self.island_widget.set_ember_back_gray_mix(value, notify=False)
            value = int(getattr(self.island_widget, "ember_back_gray_mix", value))
        else:
            self.island_widget.ember_back_gray_mix = value
            self.island_widget.update()
        self._save("v2_ember_back_gray_mix", value)
        self._touch()

    def _set_ember_color_back_gray_mix(self, value: int):
        value = max(0, min(100, int(value)))
        if hasattr(self.island_widget, "set_ember_color_back_gray_mix"):
            self.island_widget.set_ember_color_back_gray_mix(value, notify=False)
            value = int(getattr(self.island_widget, "ember_color_back_gray_mix", value))
        else:
            self.island_widget.ember_color_back_gray_mix = value
            self.island_widget.update()
        self._save("v2_ember_color_back_gray_mix", value)
        self._touch()

    def _set_ember_back_opacity(self, value: int):
        value = max(0, min(100, int(value)))
        if hasattr(self.island_widget, "set_ember_back_opacity"):
            self.island_widget.set_ember_back_opacity(value, notify=False)
            value = int(getattr(self.island_widget, "ember_back_opacity", value))
        else:
            self.island_widget.ember_back_opacity = value
            self.island_widget.update()
        self._save("v2_ember_back_opacity", value)
        self._touch()

    def _set_ember_blur_panel_strength(self, value: int):
        value = max(0, min(100, int(value)))
        if hasattr(self.island_widget, "set_ember_blur_panel_strength"):
            self.island_widget.set_ember_blur_panel_strength(value, notify=False)
            value = int(getattr(self.island_widget, "ember_blur_panel_strength", value))
        else:
            self.island_widget.ember_blur_panel_strength = value
            self.island_widget.update()
        self._save("v2_ember_blur_panel_strength", value)
        self._touch()

    def _set_ember_lyric_offset(self, value: int):
        seconds = max(0.0, min(4.0, float(value) / 1000.0))
        if hasattr(self.island_widget, "set_ember_lyric_offset_seconds"):
            self.island_widget.set_ember_lyric_offset_seconds(seconds, notify=False)
            seconds = float(getattr(self.island_widget, "ember_lyric_offset_seconds", seconds))
        else:
            self.island_widget.ember_lyric_offset_seconds = seconds
            self.island_widget.update()
        self._save("v2_ember_lyric_offset_seconds", seconds)
        self._touch()

    def _set_solid_album_background_fallback(self):
        color = self.solid_album_background_fallback.color()
        if hasattr(self.island_widget, "set_solid_album_background_fallback"):
            self.island_widget.set_solid_album_background_fallback(color, notify=False)
            color = str(getattr(self.island_widget, "solid_album_background_fallback", color))
        else:
            qcolor = QColor(str(color or "#16BCFF"))
            if not qcolor.isValid() or qcolor.saturation() < 32 or qcolor.value() < 36 or qcolor.value() > 236:
                qcolor = QColor("#16BCFF")
            color = qcolor.name().upper()
            self.island_widget.solid_album_background_fallback = color
            self.island_widget.update()
        self.solid_album_background_fallback.set_color(color)
        self._save("v2_solid_album_background_fallback", color)
        self._update_previews()
        self._touch()

    def _set_no_lyrics_display_mode(self, _index: int = -1):
        mode = str(self.no_lyrics_display_mode.currentData() or "text")
        if mode not in {"text", "album_island"}:
            mode = "text"
        setter = getattr(self.island_widget, "set_no_lyrics_display_mode", None)
        if callable(setter):
            setter(mode, notify=False)
        else:
            self.island_widget.no_lyrics_display_mode = mode
            self.island_widget.update()
        self._save("v2_no_lyrics_display_mode", mode)
        self._update_lyric_flow_controls()
        self._touch()

    def _set_album_island_cover_count(self, _index: int = -1):
        count = int(self.album_island_cover_count.currentData() or 5)
        if hasattr(self.island_widget, "set_album_island_cover_count"):
            self.island_widget.set_album_island_cover_count(count, notify=False)
        else:
            self.island_widget.album_island_cover_count = count
            self.island_widget.update()
        self._save("v2_album_island_cover_count", count)
        self._touch()

    def _set_album_island_cover_size(self, value: int):
        self._set_album_island_compact_cover_size(value)

    def _set_album_island_compact_cover_size(self, value: int):
        size = max(18, min(44, int(value)))
        if hasattr(self.island_widget, "set_album_island_compact_cover_size"):
            self.island_widget.set_album_island_compact_cover_size(size, notify=False)
        elif hasattr(self.island_widget, "set_album_island_cover_size"):
            self.island_widget.set_album_island_cover_size(size, notify=False)
        else:
            self.island_widget.album_island_cover_size = size
            self.island_widget.album_island_compact_cover_size = size
            self.island_widget.update()
        self._save("v2_album_island_cover_size", size)
        self._save("v2_album_island_compact_cover_size", size)
        self._touch()

    def _set_album_island_expanded_cover_size(self, value: int):
        size = max(36, min(96, int(value)))
        if hasattr(self.island_widget, "set_album_island_expanded_cover_size"):
            self.island_widget.set_album_island_expanded_cover_size(size, notify=False)
        else:
            self.island_widget.album_island_expanded_cover_size = size
            self.island_widget.update()
        self._save("v2_album_island_expanded_cover_size", size)
        self._touch()

    def _reset_album_island_record(self):
        if hasattr(self.island_widget, "reset_album_island_record"):
            self.island_widget.reset_album_island_record(notify=False)
        else:
            self.island_widget.listened_seconds_total = 0.0
            self.island_widget.album_history = []
            self.island_widget.update()
        self._save("v2_album_island_listened_seconds_total", 0.0)
        self._save("v2_album_island_history", [])
        self._touch()

    def _set_album_island_secondary_style(self, _index: int = -1):
        style = str(self.album_island_secondary_style.currentData() or "perspective_dim")
        if hasattr(self.island_widget, "set_album_island_secondary_style"):
            self.island_widget.set_album_island_secondary_style(style, notify=False)
        else:
            self.island_widget.album_island_secondary_style = style
            self.island_widget.update()
        self._save("v2_album_island_secondary_style", style)
        self._update_lyric_flow_controls()
        self._touch()

    def _set_album_island_perspective_amount(self, value: int):
        amount = max(0, min(200, int(value)))
        if hasattr(self.island_widget, "set_album_island_perspective_amount"):
            self.island_widget.set_album_island_perspective_amount(amount, notify=False)
        else:
            self.island_widget.album_island_perspective_amount = amount
            self.island_widget.update()
        self._save("v2_album_island_perspective_amount", amount)
        self._touch()

    def _set_album_island_spacing(self, value: int):
        spacing = max(48, min(170, int(value)))
        if hasattr(self.island_widget, "set_album_island_spacing"):
            self.island_widget.set_album_island_spacing(spacing, notify=False)
        else:
            self.island_widget.album_island_spacing = spacing
            self.island_widget.update()
        self._save("v2_album_island_spacing", spacing)
        self._touch()

    def _set_weather_location(self, value: str):
        self._save("v2_weather_location", str(value or "").strip() or "Beijing")

    def _set_weather_manual_location(self):
        value = str(self.weather_manual_location.text() or "").strip() or "北京"
        self.weather_manual_location.setText(value)
        self._save("v2_weather_manual_location", value)
        if not bool(self._setting_value("v2_weather_use_location", True)):
            self._save("v2_weather_location", value)
        self._save("v2_weather_refresh_nonce", str(time.monotonic()))

    def _set_weather_use_location(self, enabled: bool):
        self._save("v2_weather_use_location", bool(enabled))
        if not enabled:
            self._set_weather_manual_location()
        else:
            self._save("v2_weather_refresh_nonce", str(time.monotonic()))

    def _set_plugin_timezone(self, value: str):
        self._save("v2_plugin_timezone", str(value or "").strip() or "local")

    def _set_plugin_timezone_combo(self, _index: int = -1):
        value = str(self.plugin_timezone.currentData() or "local")
        self.plugin_timezone_text.setText(value)
        self._set_plugin_timezone(value)

    def _set_plugin_timezone_text(self):
        value = str(self.plugin_timezone_text.text() or "local").strip() or "local"
        self.plugin_timezone_text.setText(value)
        self._set_combo_data(self.plugin_timezone, value)
        self._set_plugin_timezone(value)

    def _alarm_rows_summary(self) -> str:
        raw = self._setting_value("v2_alarm_rows", "")
        if not raw:
            return "暂无闹钟"
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return "闹钟数据待整理"
        if isinstance(payload, dict):
            payload = payload.get("alarms") or payload.get("rows") or []
        if not isinstance(payload, list):
            return "闹钟数据待整理"
        rows = [row for row in payload if isinstance(row, dict)]
        enabled = sum(1 for row in rows if bool(row.get("enabled", True)))
        if not rows:
            return "暂无闹钟"
        return f"{enabled}/{len(rows)} 个已启用"

    def _open_time_alarm_editor(self):
        widget = self.island_widget
        widget.persistent_plugin = True
        enabled = set(self._enabled_plugins_setting())
        enabled.add("time")
        self._apply_enabled_plugins([plugin_id for plugin_id in ("time", "weather", "agenda") if plugin_id in enabled])
        widget.plugin_panel_open = True
        widget.plugin_time_mode = "alarm_editor"
        if hasattr(widget, "_set_plugin_focus"):
            widget._set_plugin_focus(0, immediate=True)
        if hasattr(widget, "_set_expanded"):
            widget._set_expanded(True)
        else:
            widget.expanded = True
        self._save("v2_persistent_plugin", True)
        self._save("v2_enabled_plugins", self._enabled_plugins_setting())
        self._save("v2_expanded", True)
        if hasattr(self, "plugin_alarm_summary"):
            self.plugin_alarm_summary.setText(self._alarm_rows_summary())
        self._touch()

    def _set_timer_end_at(self, value: str):
        self._save("v2_timer_end_at", str(value or "").strip())

    def _start_plugin_timer(self, minutes: int):
        minutes = max(1, min(600, int(minutes)))
        now = datetime.now().astimezone()
        self._save("v2_timer_started_at", now.isoformat(timespec="seconds"))
        self._save("v2_timer_end_at", (now + timedelta(minutes=minutes)).isoformat(timespec="seconds"))
        self._touch()

    def _clear_plugin_timer(self):
        self._save("v2_timer_started_at", "")
        self._save("v2_timer_end_at", "")
        self._touch()

    def _set_schedule_json_path(self, value: str):
        self._save("v2_schedule_json_path", str(value or "").strip())

    def _set_schedule_ics_path(self, value: str):
        self._save("v2_schedule_ics_path", str(value or "").strip())

    def _browse_schedule_json_path(self, _checked: bool = False):
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "选择日程 JSON",
            str(self.schedule_json_input.text() or ""),
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return
        self.schedule_json_input.setText(path)
        self._set_schedule_json_path(path)

    def _browse_schedule_ics_path(self, _checked: bool = False):
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "选择日程 ICS",
            str(self.schedule_ics_input.text() or ""),
            "Calendar Files (*.ics);;All Files (*)",
        )
        if not path:
            return
        self.schedule_ics_input.setText(path)
        self._set_schedule_ics_path(path)

    def _set_schedule_reminder_minutes(self):
        value = str(self.schedule_reminder_minutes.text() or "").strip() or "15,30,5"
        self.schedule_reminder_minutes.setText(value)
        self._save("v2_schedule_reminder_minutes", value)
        self._touch()

    def _set_enabled_plugins_from_checks(self, _checked: bool = False):
        enabled = []
        if getattr(self, "plugin_time_enabled", None) is not None and self.plugin_time_enabled.isChecked():
            enabled.append("time")
        if getattr(self, "plugin_weather_enabled", None) is not None and self.plugin_weather_enabled.isChecked():
            enabled.append("weather")
        if getattr(self, "plugin_agenda_enabled", None) is not None and self.plugin_agenda_enabled.isChecked():
            enabled.append("agenda")
        self._apply_enabled_plugins(enabled)
        self._save("v2_enabled_plugins", enabled)
        refresh = getattr(self.island_widget, "refresh_plugin_data", None)
        if callable(refresh):
            refresh()
        self._touch()

    def _set_side_island_style(self, _index: int = -1):
        style = str(self.side_island_style.currentData() or "standard")
        self.island_widget.side_island_style = style
        self._save("v2_side_island_style", style)
        self._touch()

    def _set_plugin_island_style(self, _index: int = -1):
        style = str(self.plugin_island_style.currentData() or "standard")
        self.island_widget.plugin_island_style = style
        self._save("v2_plugin_island_style", style)
        self._touch()

    def _set_plugin_fullscreen_alert_style(self, _index: int = -1):
        style = str(self.plugin_fullscreen_alert_style.currentData() or "toast")
        if hasattr(self.island_widget, "set_plugin_fullscreen_alert_style"):
            self.island_widget.set_plugin_fullscreen_alert_style(style, notify=False)
            style = str(getattr(self.island_widget, "plugin_fullscreen_alert_style", style))
        else:
            self.island_widget.plugin_fullscreen_alert_style = style
        self._save("v2_plugin_fullscreen_alert_style", style)
        self._begin_plugin_alert_adjust_preview()
        self._touch()

    def _set_plugin_fullscreen_alert_x_offset(self, value: int):
        if hasattr(self.island_widget, "set_plugin_fullscreen_alert_position"):
            self.island_widget.set_plugin_fullscreen_alert_position(x_offset=value, notify=False)
            value = int(getattr(self.island_widget, "plugin_fullscreen_alert_x_offset", value))
        else:
            self.island_widget.plugin_fullscreen_alert_x_offset = int(value)
            self.island_widget.update()
        self._save("v2_plugin_fullscreen_alert_x_offset", value)
        self._begin_plugin_alert_adjust_preview()

    def _set_plugin_fullscreen_alert_y_offset(self, value: int):
        if hasattr(self.island_widget, "set_plugin_fullscreen_alert_position"):
            self.island_widget.set_plugin_fullscreen_alert_position(y_offset=value, notify=False)
            value = int(getattr(self.island_widget, "plugin_fullscreen_alert_y_offset", value))
        else:
            self.island_widget.plugin_fullscreen_alert_y_offset = int(value)
            self.island_widget.update()
        self._save("v2_plugin_fullscreen_alert_y_offset", value)
        self._begin_plugin_alert_adjust_preview()

    def _set_time_alarm_side_display(self, _index: int = -1):
        mode = str(self.time_alarm_side_display.currentData() or "note")
        self.island_widget.time_alarm_side_display = mode
        self._save("v2_time_alarm_side_display", mode)
        self._touch()

    def _set_wave_intensity(self, value: int):
        intensity = max(0.0, min(1.0, value / 100.0))
        self.island_widget.ambient_intensity = intensity
        self._save("v2_ambient_intensity", intensity)
        self._save_music_ambience_values_for_style()
        self.island_widget.update()
        self._update_previews()

    def _set_music_ambience_style(self, _index: int = -1):
        previous_style = self._current_music_ambience_style()
        self._save_music_ambience_values_for_style(previous_style)
        style = self.music_ambience_style.currentData() or "sea_level"
        if hasattr(self.island_widget, "set_music_ambience_style"):
            self.island_widget.set_music_ambience_style(style, notify=False)
        else:
            self.island_widget.music_ambience_style = style
            self.island_widget.update()
        actual_style = self._current_music_ambience_style()
        self._save("v2_music_ambience_style", actual_style)
        self._set_music_background_follow_previous_style("")
        self._set_combo_data(self.music_ambience_style, actual_style)
        self._apply_music_ambience_values_for_style(actual_style)
        self._update_music_ambience_controls()
        self._update_lyric_flow_controls()
        self._collapse_detail_panels()
        self._update_previews()

    def _set_music_expanded_layout_style(self, _index: int = -1):
        style = str(self.music_expanded_layout_style.currentData() or "standard")
        if style not in {"standard", "dual_panel"}:
            style = "standard"
        if hasattr(self.island_widget, "set_music_expanded_layout_style"):
            self.island_widget.set_music_expanded_layout_style(style, notify=False)
        else:
            self.island_widget.music_expanded_layout_style = style
            self.island_widget.update()
        self._save("v2_music_expanded_layout_style", style)
        self._set_combo_data(self.music_expanded_layout_style, style)
        self._touch()

    def _set_music_flow_intensity(self, value: int):
        intensity = max(0.0, min(1.0, value / 100.0))
        if hasattr(self.island_widget, "set_music_flow_intensity"):
            self.island_widget.set_music_flow_intensity(intensity, notify=False)
        else:
            self.island_widget.music_flow_intensity = intensity
            self.island_widget.update()
        self._save("v2_music_flow_intensity", intensity)
        self._save_music_ambience_values_for_style()
        self._update_previews()

    def _set_music_reactive_background_enabled(self, enabled: bool):
        enabled = bool(enabled)
        if hasattr(self.island_widget, "set_music_reactive_background_enabled"):
            self.island_widget.set_music_reactive_background_enabled(enabled, notify=False)
            enabled = bool(getattr(self.island_widget, "music_reactive_background_enabled", enabled))
        else:
            self.island_widget.music_reactive_background_enabled = enabled
            self.island_widget.update()
        self._save("v2_music_reactive_background_enabled", enabled)
        self._update_music_ambience_controls()
        self._update_previews()
        self._touch()

    def _set_music_reactive_background_intensity(self, value: int):
        value = max(0, min(100, int(value)))
        if hasattr(self.island_widget, "set_music_reactive_background_intensity"):
            self.island_widget.set_music_reactive_background_intensity(value, notify=False)
            value = int(getattr(self.island_widget, "music_reactive_background_intensity", value))
        else:
            self.island_widget.music_reactive_background_intensity = value
            self.island_widget.update()
        self._save("v2_music_reactive_background_intensity", value)
        self._update_previews()
        self._touch()

    def _set_compact_duotone_balance(self, value: int):
        value = max(0, min(100, int(value)))
        expanded_value = value if self._current_music_ambience_style() == "duotone_full" else None
        if hasattr(self.island_widget, "set_duotone_balance"):
            self.island_widget.set_duotone_balance(compact=value, expanded=expanded_value, notify=False)
        else:
            self.island_widget.compact_duotone_balance = value
            if expanded_value is not None:
                self.island_widget.expanded_duotone_balance = expanded_value
            self.island_widget.update()
        if expanded_value is not None and hasattr(self, "expanded_duotone_balance"):
            self._set_slider_value(self.expanded_duotone_balance, expanded_value)
        self._save("v2_compact_duotone_balance", value)
        if expanded_value is not None:
            self._save("v2_expanded_duotone_balance", expanded_value)
        self._save_music_ambience_values_for_style()
        self._update_previews()

    def _set_expanded_duotone_balance(self, value: int):
        value = max(0, min(100, int(value)))
        if hasattr(self.island_widget, "set_duotone_balance"):
            self.island_widget.set_duotone_balance(expanded=value, notify=False)
        else:
            self.island_widget.expanded_duotone_balance = value
            self.island_widget.update()
        self._save("v2_expanded_duotone_balance", value)
        self._save_music_ambience_values_for_style()
        self._update_previews()

    def _set_player_progress_auto_detect(self, enabled: bool):
        self._save("v2_player_progress_auto_detect", bool(enabled))
        self._update_player_progress_controls()
        self._refresh_diagnostics()
        self._touch()

    def _set_music_player_mode(self, _index: int = -1):
        mode = str(self.music_player_mode.currentData() or "netease")
        self._save("v2_music_player_mode", mode)
        self._update_player_progress_controls()
        self._refresh_diagnostics()
        self._touch()

    def _save_music_app_profile_value(self, service: str, key: str, value):
        service = str(service or "").strip().casefold()
        profiles = self._music_app_profiles_setting()
        profile = dict(profiles.get(service, DEFAULT_MUSIC_APP_PROFILES.get(service, {})))
        profile[key] = value
        profiles[service] = profile
        self._save("v2_music_app_profiles", profiles)
        self._update_music_app_profile_statuses()
        self._refresh_diagnostics()
        self._touch()

    def _set_music_app_profile_path(self, service: str, widget: QLineEdit | None = None):
        controls = getattr(self, "music_app_profile_controls", {}).get(service, {})
        widget = widget or controls.get("path")
        path = "" if widget is None else str(widget.text() or "").strip().strip('"')
        if widget is not None:
            widget.setText(path)
        self._save_music_app_profile_value(service, "path", path)

    def _browse_music_app_profile_path(self, service: str, widget: QLineEdit | None = None):
        controls = getattr(self, "music_app_profile_controls", {}).get(service, {})
        widget = widget or controls.get("path")
        current = "" if widget is None else str(widget.text() or "")
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "选择音乐软件应用",
            current,
            "Executable Files (*.exe);;All Files (*)",
        )
        if not path:
            return
        if widget is not None:
            widget.setText(path)
        self._set_music_app_profile_path(service, widget)

    def _set_music_app_profile_sync_enabled(self, service: str, enabled: bool):
        self._save_music_app_profile_value(service, "sync_enabled", bool(enabled))
        self._update_player_progress_controls()

    def _set_music_app_profile_lyric_offset(self, service: str, value: int):
        seconds = max(-12.0, min(12.0, float(value) / 1000.0))
        self._save_music_app_profile_value(service, "lyric_offset_seconds", seconds)
        apply_active = getattr(self.island_widget, "set_active_music_app_lyric_offset", None)
        if callable(apply_active):
            apply_active(service, seconds)

    def _set_music_app_profile_betterncm_enabled(self, enabled: bool):
        self._save_music_app_profile_value("netease", "betterncm_enabled", bool(enabled))
        self._update_player_progress_controls()

    def _set_music_app_profile_betterncm_path(self, widget: QLineEdit | None = None):
        controls = getattr(self, "music_app_profile_controls", {}).get("netease", {})
        widget = widget or controls.get("betterncm_path")
        path = "" if widget is None else str(widget.text() or "").strip().strip('"')
        if widget is not None:
            widget.setText(path)
        self._save_music_app_profile_value("netease", "betterncm_path", path)
        self._save("v2_netease_betterncm_path", path)

    def _browse_betterncm_path(self, widget: QLineEdit | None = None):
        controls = getattr(self, "music_app_profile_controls", {}).get("netease", {})
        widget = widget or controls.get("betterncm_path")
        current = "" if widget is None else str(widget.text() or "")
        path = QFileDialog.getExistingDirectory(self, "选择 BetterNCM 目录", current)
        if not path:
            return
        if widget is not None:
            widget.setText(path)
        self._set_music_app_profile_betterncm_path(widget)

    def _set_player_progress_plugin_enabled(self, enabled: bool):
        self._save("v2_enable_player_progress_plugin", bool(enabled))
        self._update_player_progress_controls()
        self._refresh_diagnostics()
        self._touch()

    def _set_auto_expand_bounds(self, enabled: bool):
        if hasattr(self.island_widget, "set_auto_expand_bounds"):
            self.island_widget.set_auto_expand_bounds(enabled, notify=False)
        else:
            self.island_widget.auto_expand_bounds = enabled
            self.island_widget.update()
        self._save("v2_auto_expand_bounds", enabled)

    def _set_expand_direction(self, _index: int):
        direction = self.expand_direction.currentData() or "auto"
        if hasattr(self.island_widget, "set_expand_direction"):
            self.island_widget.set_expand_direction(direction, notify=False)
        else:
            self.island_widget.expand_direction = direction
            self.island_widget.update()
        self._save("v2_expand_direction", direction)

    def _set_barrage_display_mode(self, _index: int = -1):
        mode = str(self.barrage_display_mode.currentData() or "fullscreen")
        if hasattr(self.island_widget, "set_barrage_settings"):
            self.island_widget.set_barrage_settings(display_mode=mode, notify=False)
            mode = str(getattr(self.island_widget, "barrage_display_mode", mode))
        else:
            self.island_widget.barrage_display_mode = mode
            self.island_widget.update()
        self._begin_barrage_adjust_preview()
        self._save("v2_barrage_display_mode", mode)

    def _set_barrage_y_offset(self, value: int):
        if hasattr(self.island_widget, "set_barrage_settings"):
            self.island_widget.set_barrage_settings(y_offset=value, notify=False)
            value = int(getattr(self.island_widget, "barrage_y_offset", value))
        else:
            value = max(0, min(self._barrage_position_max(), int(value)))
            self.island_widget.barrage_y_offset = value
            self.island_widget.update()
        if hasattr(self, "barrage_y_offset"):
            if self.barrage_y_offset.maximum() != self._barrage_position_max():
                self.barrage_y_offset.setMaximum(self._barrage_position_max())
            if self.barrage_y_offset.value() != value:
                self._set_slider_value(self.barrage_y_offset, value)
        self._begin_barrage_adjust_preview()
        self._save("v2_barrage_y_offset", value)

    def _set_barrage_font_size(self, value: int):
        if hasattr(self.island_widget, "set_barrage_settings"):
            self.island_widget.set_barrage_settings(font_size=value, notify=False)
        else:
            self.island_widget.barrage_font_size = value
            self.island_widget.update()
        self._begin_barrage_adjust_preview()
        self._save("v2_barrage_font_size", value)

    def _set_barrage_speed(self, value: int):
        if hasattr(self.island_widget, "set_barrage_settings"):
            self.island_widget.set_barrage_settings(speed=value, notify=False)
        else:
            self.island_widget.barrage_speed = value
            self.island_widget.update()
        self._begin_barrage_adjust_preview()
        self._save("v2_barrage_speed", value)

    def _set_barrage_backdrop(self, enabled: bool):
        if hasattr(self.island_widget, "set_barrage_settings"):
            self.island_widget.set_barrage_settings(use_backdrop=enabled, notify=False)
        else:
            self.island_widget.barrage_use_backdrop = enabled
            self.island_widget.update()
        self._begin_barrage_adjust_preview()
        self._save("v2_barrage_use_backdrop", enabled)

    def _set_barrage_icon(self, enabled: bool):
        if hasattr(self.island_widget, "set_barrage_settings"):
            self.island_widget.set_barrage_settings(show_icon=enabled, notify=False)
        else:
            self.island_widget.barrage_show_icon = enabled
            self.island_widget.update()
        self._begin_barrage_adjust_preview()
        self._save("v2_barrage_show_icon", enabled)

    def _set_lyric_offset(self, value: int):
        seconds = max(-12.0, min(12.0, float(value) / 1000.0))
        if hasattr(self.island_widget, "set_lyric_offset_seconds"):
            self.island_widget.set_lyric_offset_seconds(seconds, notify=False)
        else:
            self.island_widget.lyric_offset_seconds = seconds
            self.island_widget.update()
        self._save("v2_lyric_offset_seconds", seconds)

    def _set_lyric_font_size(self, value: int):
        size = max(14, min(24, int(value)))
        if hasattr(self.island_widget, "set_lyric_font_size"):
            self.island_widget.set_lyric_font_size(size, notify=False)
        else:
            self.island_widget.lyric_font_size = size
            self.island_widget.update()
        self._save("v2_lyric_font_size", size)

    def _set_music_surface_reading_strategy(self, _index: int = -1):
        strategy = normalize_music_reading_strategy(self.music_surface_reading_strategy.currentData())
        setter = getattr(self.island_widget, "set_music_surface_reading_strategy", None)
        if callable(setter):
            setter(strategy, notify=False)
        else:
            self.island_widget.music_surface_reading_strategy = strategy
            surface = getattr(self.island_widget, "music_fullscreen_overlay", None)
            if surface is not None:
                surface.update()
        self._save("v2_music_surface_reading_strategy", strategy)

    def _set_music_surface_reading_lift_level(self, _index: int = -1):
        level = normalize_music_reading_lift_level(self.music_surface_reading_lift_level.currentData())
        setter = getattr(self.island_widget, "set_music_surface_reading_lift_level", None)
        if callable(setter):
            setter(level, notify=False)
        else:
            self.island_widget.music_surface_reading_lift_level = level
            surface = getattr(self.island_widget, "music_fullscreen_overlay", None)
            if surface is not None:
                surface.update()
        self._save("v2_music_surface_reading_lift_level", level)

    def _set_music_surface_reading_gradient_steps(self, value: int):
        steps = normalize_music_reading_gradient_steps(value)
        setter = getattr(self.island_widget, "set_music_surface_reading_gradient_steps", None)
        if callable(setter):
            setter(steps, notify=False)
        else:
            self.island_widget.music_surface_reading_gradient_steps = steps
            surface = getattr(self.island_widget, "music_fullscreen_overlay", None)
            if surface is not None:
                surface.update()
        self._save("v2_music_surface_reading_gradient_steps", steps)

    def _set_music_sparse_highlight_seconds_per_unit(self, value: int):
        seconds = normalize_sparse_seconds_per_unit(value / 10)
        if hasattr(self.island_widget, "set_music_sparse_highlight_seconds_per_unit"):
            self.island_widget.set_music_sparse_highlight_seconds_per_unit(seconds, notify=False)
        else:
            self.island_widget.music_sparse_highlight_seconds_per_unit = seconds
            self.island_widget.update()
        self._save("v2_music_sparse_highlight_seconds_per_unit", seconds)

    def _set_music_surface_preload_count(self, value: int):
        self._save("v2_music_surface_preload_count", max(1, min(6, int(value))))

    def _set_dual_line_lyrics(self, enabled: bool):
        enabled = bool(enabled)
        if hasattr(self.island_widget, "set_enable_dual_line_lyrics"):
            self.island_widget.set_enable_dual_line_lyrics(enabled, notify=False)
        else:
            self.island_widget.enable_dual_line_lyrics = enabled
            self.island_widget.update()
        for checkbox in (
            getattr(self, "dual_line_lyrics_checkbox", None),
            getattr(self, "gelv_dual_line_checkbox", None),
            getattr(self, "music_surface_translation_checkbox", None),
        ):
            if checkbox is None:
                continue
            checkbox.blockSignals(True)
            checkbox.setChecked(enabled)
            checkbox.blockSignals(False)
        self._save("v2_enable_dual_line_lyrics", enabled)
        self._update_lyric_flow_controls()
        self._request_lyric_display_progress_follow()
        self._touch()

    def _set_music_intro_cleanup_enabled(self, enabled: bool):
        enabled = bool(enabled)
        setter = getattr(self.island_widget, "set_music_intro_cleanup_enabled", None)
        if callable(setter):
            setter(enabled, notify=False)
        else:
            self.island_widget.music_intro_cleanup_enabled = enabled
            self.island_widget.update()
        self._save("v2_music_intro_cleanup_enabled", enabled)
        self._touch()

    def _request_lyric_display_progress_follow(self):
        provider = getattr(self.island_widget, "data_provider", None)
        refresh = getattr(provider, "_request_immediate_media_refresh", None)
        if callable(refresh):
            try:
                refresh()
                return
            except Exception:
                pass
        if hasattr(self.island_widget, "last_media_refresh_at"):
            self.island_widget.last_media_refresh_at = -999.0

    def _set_dual_line_secondary_source(self, _index: int = -1):
        combo = getattr(self, "dual_line_secondary_source", None)
        mode = str(combo.currentData() if combo is not None else "translation")
        if mode not in {"translation", "transliteration"}:
            mode = "translation"
        self._save("v2_dual_line_secondary_source", mode)
        self._request_lyric_display_progress_follow()
        self._touch()

    def _set_cantonese_secondary_priority(self, _index: int = -1):
        combo = getattr(self, "cantonese_secondary_priority", None)
        value = str(combo.currentData() if combo is not None else "phonetic_first")
        if value not in {"phonetic_first", "translation_first"}:
            value = "phonetic_first"
        self._save("v2_cantonese_secondary_priority", value)
        self._request_lyric_display_progress_follow()
        self._touch()

    def _set_lyric_chinese_conversion(self, _index: int = -1):
        combo = getattr(self, "lyric_chinese_conversion", None)
        mode = str(combo.currentData() if combo is not None else "off")
        if mode not in {"off", "simplified", "traditional"}:
            mode = "off"
        self._save("v2_lyric_chinese_conversion", mode)
        self._request_lyric_display_progress_follow()
        self._touch()

    def _set_dual_line_font_size(self, value: int):
        size = max(8, min(20, int(value)))
        if hasattr(self.island_widget, "set_dual_line_font_size"):
            self.island_widget.set_dual_line_font_size(size, notify=False)
        else:
            self.island_widget.dual_line_font_size = size
            self.island_widget.update()
        self._save("v2_dual_line_font_size", size)

    def _set_dual_line_secondary_weight(self, value: int):
        weight = max(40, min(100, int(value)))
        if hasattr(self.island_widget, "set_dual_line_secondary_weight"):
            self.island_widget.set_dual_line_secondary_weight(weight, notify=False)
        else:
            self.island_widget.dual_line_secondary_weight = weight
            self.island_widget.update()
        self._save("v2_dual_line_secondary_weight", weight)

    def _set_dual_line_secondary_spacing(self, value: int):
        spacing = max(60, min(150, int(value)))
        if hasattr(self.island_widget, "set_dual_line_secondary_spacing"):
            self.island_widget.set_dual_line_secondary_spacing(spacing, notify=False)
        else:
            self.island_widget.dual_line_secondary_spacing = spacing
            self.island_widget.update()
        self._save("v2_dual_line_secondary_spacing", spacing)

    def _set_font_family(self, family: str):
        family = family.strip() or self.FONT_CHOICES[0]
        if hasattr(self.island_widget, "set_ui_font"):
            self.island_widget.set_ui_font(family, notify=False)
            family = str(getattr(self.island_widget, "ui_font_family", family))
        app = QApplication.instance()
        if app:
            app.setFont(QFont(family, 9))
        self._save("v2_font_family", family)

    def _set_animation_fps(self, value: int):
        if hasattr(self.island_widget, "set_animation_fps"):
            self.island_widget.set_animation_fps(value, notify=False)
            value = int(getattr(self.island_widget, "animation_fps", value))
        else:
            self.island_widget.animation_fps = self._fps_setting_value(value)
            value = int(self.island_widget.animation_fps)
        self._save("v2_animation_fps", self._fps_setting_value(value))

    def _set_lyric_animation_fps(self, value: int):
        if hasattr(self.island_widget, "set_lyric_animation_fps"):
            self.island_widget.set_lyric_animation_fps(value, notify=False)
            value = int(getattr(self.island_widget, "lyric_animation_fps", value))
        else:
            self.island_widget.lyric_animation_fps = self._fps_setting_value(value)
            value = int(self.island_widget.lyric_animation_fps)
        self._save("v2_lyric_animation_fps", self._fps_setting_value(value))

    def _set_animation_speed(self, _index: int = -1):
        value = float(self.animation_speed.currentData() or 1.0)
        if hasattr(self.island_widget, "set_animation_speed"):
            self.island_widget.set_animation_speed(value, notify=False)
        self._save("v2_animation_speed", value)

    def _current_empty_island_display_mode(self) -> str:
        if hasattr(self, "empty_island_display_mode"):
            return str(self.empty_island_display_mode.currentData() or "visible_bar")
        return str(getattr(self.island_widget, "empty_island_display_mode", self._setting_value("v2_empty_island_display_mode", "visible_bar")) or "visible_bar")

    def _update_empty_island_time_seconds_visibility(self):
        mode = self._current_empty_island_display_mode()
        row = getattr(self, "empty_time_seconds_row", None)
        if row is not None:
            row.setVisible(mode == "time")

    def _set_empty_island_display_mode(self, _index: int = -1):
        mode = str(self.empty_island_display_mode.currentData() or "visible_bar")
        if hasattr(self.island_widget, "set_empty_island_display_mode"):
            self.island_widget.set_empty_island_display_mode(mode, notify=False)
            mode = str(getattr(self.island_widget, "empty_island_display_mode", mode))
        else:
            self.island_widget.empty_island_display_mode = mode
            self.island_widget.update()
        self._save("v2_empty_island_display_mode", mode)
        self._update_empty_island_time_seconds_visibility()
        self._touch()

    def _set_no_music_island_priority(self, _index: int = -1):
        priority = str(self.no_music_island_priority.currentData() or "plugin")
        if hasattr(self.island_widget, "set_no_music_island_priority"):
            self.island_widget.set_no_music_island_priority(priority, notify=False)
            priority = str(getattr(self.island_widget, "no_music_island_priority", priority))
        else:
            self.island_widget.no_music_island_priority = priority
            self.island_widget.update()
        self._save("v2_no_music_island_priority", priority)
        self._touch()

    def _add_message(self):
        self.island_widget.add_message()

    def _clear_messages(self):
        self.island_widget.messages.clear()
        self._touch()

    def _focus_plugin(self, index: int):
        self.island_widget._set_plugin_focus(index)
        if hasattr(self.island_widget, "_set_expanded"):
            self.island_widget._set_expanded(True)
        else:
            self.island_widget.expanded = True
        self.expanded_checkbox.setChecked(True)
        self._save("v2_expanded", True)
        self._touch()
