"""宽窄弹簧对比。左边是固定时长，右边是弹簧。

Windows 上双击同目录的 弹簧宽窄对比.bat。
仓库根目录也可以：

    python _demos/spring_transition/demo.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 真机桌面。截图模式才改用离屏，而且必须赶在创建 QApplication 之前。
_SHOTS = "--shots" in sys.argv
if _SHOTS:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
else:
    os.environ.pop("QT_QPA_PLATFORM", None)

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import (
    QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget

from local.liquid_glass import paint_liquid_glass
from local.spring_motion import (
    EDGE_SPRING, SCROLL_SPRING, WIDE_NARROW_SPRING, FlickScroll, Spring, SpringTicker,
    background_follow,
)

COVERS = ROOT / "_demos" / "narrow3" / "covers"
SONG = "夜航"
ARTIST = "GluSpot"
LINES = (
    "夜色落在窗边",
    "城市还亮着",
    "这一句停在中间",
    "下一句轻轻过来",
    "封面留在左侧",
    "词跟着宽度走",
)
PLAYLIST = (
    ("夜航", "GluSpot", "night_sea.jpg"),
    ("日落城", "窗边", "sunset_city.jpg"),
    ("粉树林", "回声", "pink_forest.jpg"),
    ("夹片", "短句", "clip.jpg"),
    ("夜航 · 伴奏", "GluSpot", "night_sea.jpg"),
    ("日落城 · 现场", "窗边", "sunset_city.jpg"),
    ("粉树林 · 慢", "回声", "pink_forest.jpg"),
    ("窗", "短句", "clip.jpg"),
)


def font(size, weight=QFont.Weight.Normal):
    face = QFont()
    face.setFamilies(["Microsoft YaHei", "PingFang SC", "Segoe UI", "WenQuanYi Micro Hei", "Droid Sans Fallback"])
    face.setPixelSize(int(size))
    face.setWeight(weight)
    return face


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_rect(start, end, t):
    return QRectF(
        lerp(start.x(), end.x(), t), lerp(start.y(), end.y(), t),
        lerp(start.width(), end.width(), t), lerp(start.height(), end.height(), t),
    )


def cubic_inout(t):
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        return 4 * t * t * t
    return 1 - ((-2 * t + 2) ** 3) / 2


def load_cover(name):
    image = QImage(str(COVERS / name))
    if image.isNull():
        image = QImage(640, 640, QImage.Format.Format_RGB32)
        image.fill(QColor("#355c7d"))
    return image


def blur_cover(source):
    small = source.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                          Qt.TransformationMode.SmoothTransformation)
    try:
        from PIL import Image, ImageFilter
        rgba = small.convertToFormat(QImage.Format.Format_RGBA8888)
        raw = Image.frombytes("RGBA", (rgba.width(), rgba.height()), bytes(rgba.bits()))
        raw = raw.filter(ImageFilter.GaussianBlur(10))
        out = QImage(raw.tobytes(), raw.size[0], raw.size[1], raw.size[0] * 4, QImage.Format.Format_RGBA8888).copy()
        return out
    except Exception:
        return small


class GlassButton(QWidget):
    clicked = Signal()

    def __init__(self, text, parent=None, checkable=False):
        super().__init__(parent)
        self.text = text
        self.checkable = checkable
        self.checked = False
        self._pressed = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(36)
        self.setMinimumWidth(132)

    def mousePressEvent(self, event):
        self._pressed = True
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        self._pressed = False
        if self.rect().contains(event.position().toPoint()):
            if self.checkable:
                self.checked = not self.checked
            self.clicked.emit()
        self.update()
        event.accept()

    def paintEvent(self, _event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            paint_liquid_glass(
                painter, rect, radius=rect.height() / 2, refractive=True,
                backdrop=self.window()._glass_backdrop, backdrop_rect=rect,
                pressed=self._pressed, active=self.checked, material_level=62,
                cache_token=("btn", self.text, self.checked, round(rect.width())),
            )
            painter.setPen(QColor("#f7f4ee"))
            painter.setFont(font(13, QFont.Weight.DemiBold))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text)


class GlassSlider(QWidget):
    changed = Signal(float)

    def __init__(self, title, low, high, value, suffix="", parent=None):
        super().__init__(parent)
        self.title = title
        self.low = low
        self.high = high
        self.value = value
        self.suffix = suffix
        self._drag = False
        self.setFixedHeight(58)
        self.setMinimumWidth(280)
        self.setMouseTracking(True)

    def _ratio(self):
        return (self.value - self.low) / max(1e-6, self.high - self.low)

    def _set_from_x(self, x):
        track = self._track()
        ratio = (x - track.left()) / max(1.0, track.width())
        ratio = max(0.0, min(1.0, ratio))
        self.value = self.low + (self.high - self.low) * ratio
        self.changed.emit(self.value)
        self.update()

    def _track(self):
        return QRectF(16, 34, self.width() - 32, 8)

    def mousePressEvent(self, event):
        self._drag = True
        self._set_from_x(event.position().x())

    def mouseMoveEvent(self, event):
        if self._drag:
            self._set_from_x(event.position().x())

    def mouseReleaseEvent(self, event):
        self._drag = False
        self.update()

    def paintEvent(self, _event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QColor("#c9c2b6"))
            painter.setFont(font(12, QFont.Weight.Medium))
            painter.drawText(QRectF(16, 4, self.width() * 0.6, 22), Qt.AlignmentFlag.AlignVCenter, self.title)
            painter.setPen(QColor("#f7f4ee"))
            painter.setFont(font(13, QFont.Weight.DemiBold))
            label = f"{self.value:.2f}{self.suffix}" if self.high <= 2 else f"{self.value:.0f}{self.suffix}"
            painter.drawText(QRectF(self.width() * 0.45, 4, self.width() * 0.55 - 16, 22),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, label)
            track = self._track()
            paint_liquid_glass(
                painter, track, radius=4, refractive=True,
                backdrop=self.window()._glass_backdrop, backdrop_rect=track,
                material_level=48, cache_token=("track", round(track.width())),
            )
            fill = QRectF(track)
            fill.setWidth(max(8, track.width() * self._ratio()))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 244, 230, 90))
            painter.drawRoundedRect(fill, 4, 4)
            thumb = QRectF(0, 0, 18, 18)
            thumb.moveCenter(QPointF(fill.right(), track.center().y()))
            paint_liquid_glass(
                painter, thumb, radius=9, refractive=True,
                backdrop=self.window()._glass_backdrop, backdrop_rect=thumb,
                hovered=self._drag, material_level=70, cache_token=("thumb", self._drag),
            )


class CurveCard(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = WIDE_NARROW_SPRING
        self.setMinimumHeight(150)

    def paintEvent(self, _event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            card = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            paint_liquid_glass(
                painter, card, radius=18, refractive=True,
                backdrop=self.window()._glass_backdrop, backdrop_rect=card,
                material_level=40, cache_token=("curve", round(card.width()), round(self.config.stiffness),
                                               round(self.config.damping_ratio * 100)),
            )
            plot = card.adjusted(18, 36, -18, -16)
            painter.setPen(QColor("#f7f4ee"))
            painter.setFont(font(13, QFont.Weight.DemiBold))
            painter.drawText(QRectF(card.left() + 16, card.top() + 8, card.width() - 32, 22),
                             "这一下会怎么走")
            painter.setPen(QPen(QColor(255, 255, 255, 28), 1))
            painter.drawLine(plot.bottomLeft(), plot.bottomRight())
            painter.drawLine(plot.bottomLeft(), plot.topLeft())

            def point(t, value):
                return QPointF(plot.left() + plot.width() * t, plot.bottom() - plot.height() * max(0.0, min(1.15, value)))

            cubic_path = QPainterPath()
            spring_path = QPainterPath()
            spring = Spring(self.config, 0.0, 0.0, 1.0)
            steps = 80
            for index in range(steps + 1):
                t = index / steps
                shown = min(1.0, t / 0.82)
                if index == 0:
                    cubic_path.moveTo(point(shown, cubic_inout(t)))
                else:
                    cubic_path.lineTo(point(shown, cubic_inout(t)))
                spring.step(1.2 / steps)
                if index == 0:
                    spring_path.moveTo(point(shown, spring.value))
                else:
                    spring_path.lineTo(point(shown, min(1.15, spring.value)))
            painter.setPen(QPen(QColor("#8d867c"), 1.6, Qt.PenStyle.DashLine))
            painter.drawPath(cubic_path)
            painter.setPen(QPen(QColor("#f3d2b0"), 2.2))
            painter.drawPath(spring_path)
            painter.setFont(font(11))
            painter.setPen(QColor("#8d867c"))
            painter.drawText(QRectF(plot.left(), plot.bottom() + 2, 80, 16), "固定时长")
            painter.setPen(QColor("#f3d2b0"))
            painter.drawText(QRectF(plot.right() - 70, plot.bottom() + 2, 70, 16),
                             Qt.AlignmentFlag.AlignRight, "弹簧")


class MusicStage(QWidget):
    """一块能拖宽拖窄的播放窗。fraction 1 是宽，0 是窄。"""

    NARROW = 300.0
    WIDE = 560.0

    def __init__(self, title, spring_mode, cover, backdrop, parent=None):
        super().__init__(parent)
        self.title = title
        self.spring_mode = spring_mode
        self.cover = cover
        self.backdrop = backdrop
        self.fraction = 1.0
        self._drag = None
        self._token = object()
        self._motion = Spring(WIDE_NARROW_SPRING, 1.0, 0.0, 1.0)
        self._cubic = QVariantAnimation(self)
        self._cubic.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._cubic.valueChanged.connect(self._on_cubic)
        self.setMinimumSize(int(self.WIDE) + 24, 460)
        self.setMouseTracking(True)

    def set_config(self, config):
        self._motion.config = config
        self._motion.retarget(self._motion.target)

    def width_now(self):
        return self.NARROW + (self.WIDE - self.NARROW) * self.fraction

    def go(self, wide, slow):
        target = 1.0 if wide else 0.0
        scale = 4.0 if slow else 1.0
        if self.spring_mode:
            self._cubic.stop()
            self._motion.value = self.fraction
            self._motion.retarget(target)
            self._slow = scale
            SpringTicker.instance().add(self._token, self._tick)
        else:
            SpringTicker.instance().remove(self._token)
            self._cubic.stop()
            self._cubic.setDuration(int(1000 * scale))
            self._cubic.setStartValue(self.fraction)
            self._cubic.setEndValue(target)
            self._cubic.start()
        self.update()

    def _on_cubic(self, value):
        self.fraction = max(0.0, min(1.0, float(value)))
        self.update()

    def _tick(self, dt, _dropped):
        self._motion.step(dt / getattr(self, "_slow", 1.0))
        self.fraction = max(0.0, min(1.0, self._motion.value))
        self.update()
        if self._motion.settled:
            SpringTicker.instance().remove(self._token)

    def _window_rect(self):
        rect = QRectF(8, 28, self.width_now(), self.height() - 36)
        return rect

    def _edge(self):
        window = self._window_rect()
        return QRectF(window.right() - 14, window.top(), 18, window.height())

    def mousePressEvent(self, event):
        if self._edge().contains(event.position()):
            self._drag = (event.position().x(), self.width_now())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self.setCursor(Qt.CursorShape.SizeHorCursor if self._edge().contains(event.position()) or self._drag
                       else Qt.CursorShape.ArrowCursor)
        if self._drag is None:
            return
        origin_x, origin_w = self._drag
        width = origin_w + event.position().x() - origin_x
        width = max(self.NARROW, min(self.WIDE, width))
        self.fraction = (width - self.NARROW) / (self.WIDE - self.NARROW)
        self._cubic.stop()
        SpringTicker.instance().remove(self._token)
        self.update()

    def mouseReleaseEvent(self, event):
        if self._drag is None:
            return
        self._drag = None
        host = self.window()
        self.go(self.fraction >= 0.5, getattr(host, "slow", False))

    def paintEvent(self, _event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QColor("#d9d0c3"))
            painter.setFont(font(13, QFont.Weight.DemiBold))
            painter.drawText(QRectF(12, 2, self.width() - 20, 22), self.title)
            window = self._window_rect()
            self._paint_player(painter, window, self.fraction)

    def _paint_player(self, painter, window, fraction):
        painter.save()
        shadow = QRectF(window)
        shadow.translate(0, 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 70))
        painter.drawRoundedRect(shadow, 28, 28)
        clip = QPainterPath()
        clip.addRoundedRect(window, 26, 26)
        painter.setClipPath(clip)
        painter.drawImage(window, self.backdrop)
        veil = QLinearGradient(window.topLeft(), window.bottomRight())
        veil.setColorAt(0, QColor(8, 10, 16, 90))
        veil.setColorAt(1, QColor(8, 10, 16, 150))
        painter.fillRect(window, veil)

        cover_wide = QRectF(window.left() + 28, window.top() + 28, 168, 168)
        cover_narrow = QRectF(window.left() + 20, window.top() + 56, 84, 84)
        cover = lerp_rect(cover_narrow, cover_wide, fraction)
        title_wide = QRectF(cover_wide.left(), cover_wide.bottom() + 16, cover_wide.width() + 20, 32)
        title_narrow = QRectF(cover_narrow.right() + 14, cover_narrow.top() + 8, window.width() - 140, 28)
        title = lerp_rect(title_narrow, title_wide, fraction)
        artist_box = QRectF(title.left(), title.bottom() + 2, title.width(), 20)
        lyric_wide = QRectF(cover_wide.right() + 28, window.top() + 36, window.right() - cover_wide.right() - 48, window.height() - 120)
        lyric_narrow = QRectF(window.left() + 24, cover_narrow.bottom() + 28, window.width() - 48, window.height() - cover_narrow.bottom() - 110)
        lyrics = lerp_rect(lyric_narrow, lyric_wide, fraction)

        path = QPainterPath()
        path.addRoundedRect(cover, 16, 16)
        painter.save()
        painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
        painter.drawImage(cover, self.cover)
        painter.restore()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1))
        painter.drawRoundedRect(cover, 16, 16)

        painter.setPen(QColor("#f7f4ee"))
        painter.setFont(font(20 if fraction > 0.55 else 16, QFont.Weight.Bold))
        painter.drawText(title, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, SONG)
        painter.setPen(QColor(247, 244, 238, 150))
        painter.setFont(font(12, QFont.Weight.Medium))
        painter.drawText(artist_box, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, ARTIST)

        focus = 2
        line_h = max(28.0, lyrics.height() / 7)
        painter.setClipRect(lyrics)
        for index, line in enumerate(LINES):
            y = lyrics.top() + (index - focus) * line_h + lyrics.height() * 0.42
            row = QRectF(lyrics.left(), y, lyrics.width(), line_h)
            if row.bottom() < lyrics.top() or row.top() > lyrics.bottom():
                continue
            distance = abs(index - focus)
            if distance == 0:
                painter.setPen(QColor("#ffffff"))
                painter.setFont(font(18, QFont.Weight.Bold))
            elif distance == 1:
                painter.setPen(QColor(255, 255, 255, 150))
                painter.setFont(font(15, QFont.Weight.DemiBold))
            else:
                painter.setPen(QColor(255, 255, 255, 70))
                painter.setFont(font(14))
            painter.drawText(row, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
        painter.setClipping(False)

        self._paint_chrome(painter, window, fraction)
        grip = QRectF(window.right() - 10, window.center().y() - 18, 4, 36)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 90))
        painter.drawRoundedRect(grip, 2, 2)
        painter.restore()

    def _paint_chrome(self, painter, window, fraction):
        buttons = ("上一首", "播放", "下一首")
        gap = 10
        side = lerp(40, 46, fraction)
        total = side * 3 + gap * 2
        left = window.center().x() - total / 2
        top = window.bottom() - side - lerp(22, 28, fraction)
        for index, name in enumerate(buttons):
            rect = QRectF(left + index * (side + gap), top, side, side)
            paint_liquid_glass(
                painter, rect, radius=side / 2, refractive=True,
                backdrop=self.backdrop, backdrop_rect=self._backdrop_sample(window, rect),
                material_level=64, cache_token=("transport", name, round(side)),
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#f7f4ee"))
            center = rect.center()
            if name == "播放":
                path = QPainterPath()
                path.moveTo(center.x() - 4, center.y() - 8)
                path.lineTo(center.x() + 8, center.y())
                path.lineTo(center.x() - 4, center.y() + 8)
                path.closeSubpath()
                painter.drawPath(path)
            else:
                direction = -1 if name == "上一首" else 1
                path = QPainterPath()
                tip = center.x() + 6 * direction
                back = center.x() - 2 * direction
                path.moveTo(back, center.y() - 7)
                path.lineTo(tip, center.y())
                path.lineTo(back, center.y() + 7)
                path.closeSubpath()
                painter.drawPath(path)
                bar = QRectF(center.x() + (8 if direction > 0 else -10), center.y() - 7, 2.4, 14)
                painter.drawRoundedRect(bar, 1, 1)
        for index in range(3):
            dot = QRectF(window.right() - 28 - (2 - index) * 34, window.top() + 16, 26, 26)
            dot.translate(lerp(40, 0, fraction), lerp(18, 0, fraction))
            paint_liquid_glass(
                painter, dot, radius=13, refractive=True,
                backdrop=self.backdrop, backdrop_rect=self._backdrop_sample(window, dot),
                material_level=58, cache_token=("util", index),
            )

    def _backdrop_sample(self, window, rect):
        if self.backdrop.isNull():
            return rect
        sx = self.backdrop.width() / max(1.0, window.width())
        sy = self.backdrop.height() / max(1.0, window.height())
        return QRectF((rect.left() - window.left()) * sx, (rect.top() - window.top()) * sy,
                      rect.width() * sx, rect.height() * sy)


class PlaylistCard(QWidget):
    def __init__(self, covers, parent=None):
        super().__init__(parent)
        self.rows = [(title, artist, covers[name]) for title, artist, name in PLAYLIST]
        self.row_h = 64.0
        self.offset = 0.0
        self.flick = FlickScroll()
        self.flick.on_step = self._on_step
        self._press = None
        self.setMinimumHeight(280)
        self.setMouseTracking(True)

    def set_config(self, damping, stiffness):
        self.flick.scroll_config = SCROLL_SPRING.replaced(
            stiffness=stiffness, damping_ratio=1.0 if damping >= 0.98 else damping)
        self.flick.edge_config = EDGE_SPRING.replaced(stiffness=stiffness, damping_ratio=min(1.15, damping))

    def _limit(self):
        return max(0.0, len(self.rows) * self.row_h - (self.height() - 36))

    def _on_step(self, result):
        if result[0] == "overscroll":
            self.flick.overscroll = result[1]
        else:
            self.offset = max(0.0, min(self._limit(), result[1]))
            self.flick.overscroll = result[2]
        self.update()

    def mousePressEvent(self, event):
        self.flick.cancel()
        self._press = (event.position().y(), self.offset, event.position().y())
        event.accept()

    def mouseMoveEvent(self, event):
        if self._press is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        origin_y, origin, last_y = self._press
        self._press = (origin_y, origin, event.position().y())
        self.flick.observe(-(event.position().y() - last_y), event.timestamp() / 1000.0 or 0.0)
        committed, overscroll = self.flick.absorb(
            origin, origin - (event.position().y() - origin_y), 0.0, self._limit(), self.height())
        self.offset = committed
        self.flick.overscroll = overscroll
        self.update()

    def mouseReleaseEvent(self, event):
        if self._press is None:
            return
        self._press = None
        if abs(self.flick.overscroll) > 0.5:
            self.flick._arm_edge()
        else:
            self.flick.arm_coast(self.offset, 0.0, self._limit(), span=self.height())
        event.accept()

    def wheelEvent(self, event):
        delta = event.pixelDelta().y() or event.angleDelta().y() / 120.0 * self.row_h
        self.flick.cancel()
        self.flick.observe(delta, 0.0)
        committed, overscroll = self.flick.absorb(self.offset, self.offset - delta, 0.0, self._limit(), self.height())
        self.offset = committed
        self.flick.overscroll = overscroll
        self.flick.arm_coast(committed, 0.0, self._limit(), span=self.height())
        self.update()

    def paintEvent(self, _event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            card = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            path = QPainterPath()
            path.addRoundedRect(card, 22, 22)
            painter.setClipPath(path)
            painter.fillRect(card, QColor("#141820"))
            painter.setPen(QColor("#f7f4ee"))
            painter.setFont(font(14, QFont.Weight.DemiBold))
            painter.drawText(QRectF(16, 10, card.width() - 32, 24), "播放列表 · 拖一下，到边会回弹")
            list_top = 40
            list_bottom = card.bottom() - 28
            painter.setClipRect(QRectF(card.left(), list_top, card.width(), list_bottom - list_top))
            top = list_top - self.offset + self.flick.overscroll
            for index, (title, artist, cover) in enumerate(self.rows):
                row = QRectF(12, top + index * self.row_h, card.width() - 24, self.row_h - 8)
                if row.bottom() < list_top or row.top() > list_bottom:
                    continue
                paint_liquid_glass(
                    painter, row, radius=14, refractive=True,
                    backdrop=self.window()._glass_backdrop, backdrop_rect=row,
                    material_level=36, active=index == 0,
                    cache_token=("row", index, round(row.width())),
                )
                thumb = QRectF(row.left() + 8, row.top() + 8, row.height() - 16, row.height() - 16)
                clip = QPainterPath()
                clip.addRoundedRect(thumb, 8, 8)
                painter.save()
                painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
                painter.drawImage(thumb, cover)
                painter.restore()
                painter.setPen(QColor("#f7f4ee"))
                painter.setFont(font(14, QFont.Weight.DemiBold))
                painter.drawText(QRectF(thumb.right() + 12, row.top() + 6, row.width() - 80, 24), title)
                painter.setPen(QColor(247, 244, 238, 140))
                painter.setFont(font(12))
                painter.drawText(QRectF(thumb.right() + 12, row.top() + 28, row.width() - 80, 18), artist)
            painter.setClipping(False)
            painter.fillRect(QRectF(card.left(), list_bottom, card.width(), card.bottom() - list_bottom), QColor("#141820"))
            painter.setPen(QColor("#b7aa9a"))
            painter.setFont(font(11))
            painter.drawText(QRectF(16, card.bottom() - 22, card.width() - 32, 16),
                             f"速度 {self.flick.velocity:.0f}    越界 {self.flick.overscroll:.0f}")


class ExpandCard(QWidget):
    def __init__(self, title, spring_mode, cover, backdrop, parent=None):
        super().__init__(parent)
        self.caption = title
        self.spring_mode = spring_mode
        self.cover = cover
        self.backdrop = backdrop
        self.fraction = 0.0
        self._token = object()
        self._motion = Spring(WIDE_NARROW_SPRING, 0.0, 0.0, 0.0)
        self._cubic = QVariantAnimation(self)
        self._cubic.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._cubic.valueChanged.connect(self._on_cubic)
        self.setMinimumHeight(250)

    def set_config(self, config):
        self._motion.config = config
        self._motion.retarget(self._motion.target)

    def go(self, expanded, slow):
        target = 1.0 if expanded else 0.0
        scale = 4.0 if slow else 1.0
        if self.spring_mode:
            self._cubic.stop()
            self._motion.value = self.fraction
            self._motion.retarget(target)
            self._slow = scale
            SpringTicker.instance().add(self._token, self._tick)
        else:
            SpringTicker.instance().remove(self._token)
            self._cubic.stop()
            self._cubic.setDuration(int(340 * scale))
            self._cubic.setStartValue(self.fraction)
            self._cubic.setEndValue(target)
            self._cubic.start()

    def _on_cubic(self, value):
        self.fraction = float(value)
        self.update()

    def _tick(self, dt, _dropped):
        self._motion.step(dt / getattr(self, "_slow", 1.0))
        self.fraction = max(0.0, min(1.0, self._motion.value))
        self.update()
        if self._motion.settled:
            SpringTicker.instance().remove(self._token)

    def mousePressEvent(self, event):
        host = self.window()
        self.go(self.fraction < 0.5, getattr(host, "slow", False))
        event.accept()

    def paintEvent(self, _event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QColor("#d9d0c3"))
            painter.setFont(font(13, QFont.Weight.DemiBold))
            painter.drawText(QRectF(8, 4, self.width() - 16, 22), self.caption)
            fraction = self.fraction
            blend = background_follow(fraction, 0.34) if self.spring_mode else (1.0 if fraction >= 0.38 else fraction / 0.38)
            card = QRectF(24, 40, self.width() - 48, lerp(72, 200, fraction))
            painter.save()
            clip = QPainterPath()
            clip.addRoundedRect(card, lerp(36, 18, fraction), lerp(36, 18, fraction))
            painter.setClipPath(clip)
            painter.setOpacity(1.0 - blend)
            painter.drawImage(card, self.cover)
            painter.setOpacity(blend)
            painter.drawImage(card, self.backdrop)
            painter.setOpacity(1.0)
            cover = QRectF(card.left() + 14, card.center().y() - 22, 44, 44)
            cover.moveTop(lerp(card.center().y() - 22, card.top() + 16, fraction))
            path = QPainterPath()
            path.addRoundedRect(cover, 10, 10)
            painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
            painter.drawImage(cover, self.cover)
            painter.restore()
            painter.setPen(QColor("#f7f4ee"))
            painter.setFont(font(14, QFont.Weight.Bold))
            painter.drawText(QRectF(cover.right() + 10, cover.top(), card.right() - cover.right() - 20, 22), SONG)
            painter.setPen(QColor(247, 244, 238, 150))
            painter.setFont(font(11))
            painter.drawText(QRectF(cover.right() + 10, cover.top() + 22, card.right() - cover.right() - 20, 16), ARTIST)
            if fraction > 0.45:
                painter.setPen(QColor(255, 255, 255, int(180 * min(1.0, (fraction - 0.45) / 0.4))))
                painter.setFont(font(13))
                painter.drawText(QRectF(card.left() + 16, card.top() + 78, card.width() - 32, 80), LINES[2] + "\n" + LINES[3])


class Shell(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("弹簧对比")
        self.resize(1240, 980)
        self.slow = False
        self._want_wide = False
        self.cover = load_cover("night_sea.jpg")
        self.backdrop = blur_cover(self.cover)
        self._glass_backdrop = QImage(self.size(), QImage.Format.Format_RGB32)
        self._paint_page_backdrop()
        covers = {name: load_cover(name) for _t, _a, name in PLAYLIST}
        self.play = GlassButton("播放宽窄")
        self.slow_button = GlassButton("慢放 0.25×", checkable=True)
        self.damping = GlassSlider("阻尼比", 0.55, 1.35, 1.0)
        self.stiffness = GlassSlider("刚度", 40, 800, 220)
        self.curve = CurveCard()
        self.left = MusicStage("左边 · 固定时长 · 拖右边", False, self.cover, self.backdrop)
        self.right = MusicStage("右边 · 弹簧 · 拖右边", True, self.cover, self.backdrop)
        self.playlist = PlaylistCard(covers)
        self.expand_old = ExpandCard("展开 · 旧的 38% 切断", False, self.cover, self.backdrop)
        self.expand_new = ExpandCard("展开 · 背景跟着弹簧", True, self.cover, self.backdrop)
        head = QHBoxLayout()
        head.addWidget(self.play)
        head.addWidget(self.slow_button)
        head.addSpacing(12)
        head.addWidget(self.damping, 1)
        head.addWidget(self.stiffness, 1)
        stages = QHBoxLayout()
        stages.addWidget(self.left, 1)
        stages.addWidget(self.right, 1)
        lower = QHBoxLayout()
        lower.addWidget(self.playlist, 1)
        column = QVBoxLayout()
        column.addWidget(self.expand_old, 1)
        column.addWidget(self.expand_new, 1)
        lower.addLayout(column, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        layout.addLayout(head)
        layout.addWidget(self.curve)
        layout.addLayout(stages, 3)
        layout.addLayout(lower, 2)
        self.play.clicked.connect(self._play)
        self.slow_button.clicked.connect(self._toggle_slow)
        self.damping.changed.connect(self._retune)
        self.stiffness.changed.connect(self._retune)
        self._retune()

    def _paint_page_backdrop(self):
        self._glass_backdrop = QImage(max(1, self.width()), max(1, self.height()), QImage.Format.Format_RGB32)
        with QPainter(self._glass_backdrop) as painter:
            painter.drawImage(self.rect(), self.backdrop)
            painter.fillRect(self.rect(), QColor(10, 12, 16, 180))

    def resizeEvent(self, event):
        self._paint_page_backdrop()
        super().resizeEvent(event)

    def _toggle_slow(self):
        self.slow = self.slow_button.checked

    def _play(self):
        self._want_wide = not self._want_wide
        for stage in (self.left, self.right):
            stage.go(self._want_wide, self.slow)
        for card in (self.expand_old, self.expand_new):
            card.go(self._want_wide, self.slow)

    def _retune(self, *_args):
        config = WIDE_NARROW_SPRING.replaced(
            damping_ratio=self.damping.value, stiffness=self.stiffness.value)
        self.curve.config = config
        self.right.set_config(config)
        self.expand_new.set_config(config)
        self.playlist.set_config(self.damping.value, self.stiffness.value)
        self.curve.update()

    def paintEvent(self, _event):
        with QPainter(self) as painter:
            painter.drawImage(self.rect(), self._glass_backdrop)


def grab(widget):
    image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    widget.render(image)
    return image


def save_shots(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    shell = Shell()
    shell.resize(1240, 980)
    shell.show()
    app.processEvents()
    grab(shell).save(str(directory / "overview.png"))
    shell._want_wide = False
    shell.left.fraction = 1.0
    shell.right.fraction = 1.0
    shell.left._cubic.stop()
    shell.right._motion = Spring(shell.right._motion.config, 1.0, 0.0, 0.0)
    for _ in range(22):
        shell.left.fraction = cubic_inout(min(1.0, (_ + 1) / 28))
        shell.left.fraction = 1.0 - shell.left.fraction
        shell.right._motion.step(0.016)
        shell.right.fraction = max(0.0, min(1.0, shell.right._motion.value))
    shell.expand_old.fraction = 0.35
    shell.expand_new.fraction = background_follow(0.35, 0.34)
    shell.playlist.offset = 0
    shell.playlist.flick.overscroll = -42
    shell.update()
    shell.left.update()
    shell.right.update()
    app.processEvents()
    grab(shell).save(str(directory / "mid-transition.png"))
    print(directory)


def main():
    if _SHOTS:
        target = sys.argv[sys.argv.index("--shots") + 1]
        save_shots(target)
        return 0
    app = QApplication(sys.argv)
    window = Shell()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
