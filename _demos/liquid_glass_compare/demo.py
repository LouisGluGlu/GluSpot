"""Liquid glass controls on a color photograph.

    python _demos/liquid_glass_compare/demo.py
    python _demos/liquid_glass_compare/demo.py --grab /opt/cursor/artifacts/glass
    python _demos/liquid_glass_compare/demo.py --idle-cpu

The product controls are not replaced. This window calls
``local.liquid_glass_controls``. The timer runs only while a thumb spring
is moving. Blur is cached until the photo or the blur radius changes.

The backdrop is a forest path photograph by Casey Horner (Unsplash).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from local.liquid_glass_controls import (
    ControlGlassMaterial,
    control_glass_ink,
    control_glass_levels,
    paint_control_glass,
    paint_control_lens,
    paint_merged_lens,
)

REFERENCES = {
    ("light", 0.0): Path("/home/ubuntu/.cursor/projects/agent-repos-PreGluspot/assets/27a1813b82e6ec9fe7ddc0ae9168e4a292a2d16d20f1fb2a7c824e0ede57d3da.png"),
    ("light", 0.5): Path("/home/ubuntu/.cursor/projects/agent-repos-PreGluspot/assets/0d60767efd1055140b2a63c1361b864c24670944392af606d124d23277df38bf.png"),
    ("light", 1.0): Path("/home/ubuntu/.cursor/projects/agent-repos-PreGluspot/assets/b3849feb133181bab018484b28df7788a20ca64bddc463d4293a4628cf6e6a05.png"),
    ("dark", 0.0): Path("/home/ubuntu/.cursor/projects/agent-repos-PreGluspot/assets/737f871ae1953d3a167c34e9b3a3ff6a4f0ad99c04e6a79f22d65c9c7380e74d.png"),
    ("dark", 0.5): Path("/home/ubuntu/.cursor/projects/agent-repos-PreGluspot/assets/6da82d17534f32cfb037a79dec1e273d0821c108723e4cea5d792fbce4549542.png"),
    ("dark", 1.0): Path("/home/ubuntu/.cursor/projects/agent-repos-PreGluspot/assets/252916bafe34e5319d871570c89ca4e5a564999d3b26a1b5611ae397424d21c7.png"),
}


BACKDROP = Path(__file__).with_name("backdrop.jpg")


def make_photo(width: int, height: int) -> QImage:
    """Cover-crop the forest path so green and the track both sit under the glass."""
    source = QImage(str(BACKDROP))
    if source.isNull():
        image = QImage(width, height, QImage.Format.Format_RGB32)
        image.fill(QColor(40, 120, 48))
        return image
    scale = max(width / source.width(), height / source.height())
    scaled = source.scaled(
        max(width, int(source.width() * scale) + 1),
        max(height, int(source.height() * scale) + 1),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - width) // 2)
    y = max(0, int((scaled.height() - height) * 0.35))
    return scaled.copy(x, y, width, height)


class _Spring:
    def __init__(self, value: float):
        self.value = value
        self.target = value
        self.velocity = 0.0

    def step(self, dt: float) -> bool:
        stiffness = 210.0
        damping = 0.62 * 2.0 * math.sqrt(stiffness)
        self.velocity += (-stiffness * (self.value - self.target) - damping * self.velocity) * dt
        self.value += self.velocity * dt
        return abs(self.value - self.target) > 0.4 or abs(self.velocity) > 6.0


class GlassDemo(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Liquid glass")
        self.resize(1180, 680)
        self.dark = False
        self.amount = 0.5
        self.progress = 0.46
        self._photo = QImage()
        self.material = ControlGlassMaterial()
        self._thumb = _Spring(26.0)
        self._merge = _Spring(0.0)
        self._grabbing = ""
        self._last_x = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._clock = None
        self.search_rect = QRectF()
        self.menu_rect = QRectF()

    def _sync_photo(self):
        if self._photo.size() != self.size() or self._photo.isNull():
            self._photo = make_photo(max(2, self.width()), max(2, self.height()))
            self.material.set_backdrop(self._photo)

    def paintEvent(self, _event):
        painter = QPainter(self)
        self.paint_scene(painter, QRectF(self.rect()))
        painter.end()

    def paint_scene(self, painter: QPainter, rect: QRectF):
        self._sync_photo()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.drawImage(rect, self._photo)
        ink = control_glass_ink(self.dark)
        font = QFont()
        font.setPixelSize(15)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(QRectF(28, 16, rect.width() - 180, 26), "控件透明度同时管透明和模糊")
        theme = QRectF(rect.right() - 92, 14, 64, 30)
        self._plate(painter, theme)
        painter.setPen(ink)
        painter.drawText(theme, Qt.AlignmentFlag.AlignCenter, "深色" if self.dark else "浅色")

        self.menu_rect = QRectF(36, 64, 54, 54)
        self.search_rect = QRectF(104, 64, 560, 54)
        self._plate(painter, self.menu_rect)
        self._plate(painter, self.search_rect)
        self._hamburger(painter, self.menu_rect, ink)
        self._search_glyph(painter, self.search_rect, ink)
        painter.setPen(ink)
        painter.drawText(self.search_rect.adjusted(46, 0, -16, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "搜索")

        track = QRectF(36, 156, 680, 58)
        self._plate(painter, track)
        self._progress(painter, track, ink)

        y = 250.0
        for index in range(5):
            button = QRectF(36 + index * 64, y, 48, 48)
            self._plate(painter, button)
            self._icon(painter, button, index, ink)

        bar = QRectF(36, rect.height() - 78, rect.width() - 72, 48)
        self._plate(painter, bar)
        painter.setPen(ink)
        painter.drawText(QRectF(bar.left() + 16, bar.top(), 110, bar.height()), Qt.AlignmentFlag.AlignVCenter, "控件透明度")
        slider = QRectF(bar.left() + 140, bar.center().y() - 3, bar.width() - 180, 6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 90))
        painter.drawRoundedRect(slider, 3, 3)
        filled = QRectF(slider.left(), slider.top(), slider.width() * self.amount, slider.height())
        painter.setBrush(QColor("#0a84ff"))
        painter.drawRoundedRect(filled, 3, 3)
        knob = QRectF(filled.right() - 13, slider.center().y() - 13, 26, 26)
        paint_control_lens(painter, knob, self.material, dark=self.dark, scale=2.2)
        self._slider_track = slider
        self._theme_rect = theme

    def _plate(self, painter: QPainter, rect: QRectF):
        paint_control_glass(painter, rect, self.material, dark=self.dark, amount=self.amount)

    def _hamburger(self, painter: QPainter, rect: QRectF, ink: QColor):
        painter.save()
        painter.setPen(QPen(ink, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for offset in (-6.0, 0.0, 6.0):
            y = rect.center().y() + offset
            painter.drawLine(QPointF(rect.center().x() - 8, y), QPointF(rect.center().x() + 8, y))
        painter.restore()

    def _search_glyph(self, painter: QPainter, rect: QRectF, ink: QColor):
        painter.save()
        painter.setPen(QPen(ink, 1.8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        glass = QRectF(rect.left() + 16, rect.center().y() - 7, 14, 14)
        painter.drawEllipse(glass)
        painter.drawLine(QPointF(glass.right() - 2, glass.bottom() - 2), QPointF(glass.right() + 4, glass.bottom() + 4))
        painter.restore()

    def _icon(self, painter: QPainter, rect: QRectF, index: int, ink: QColor):
        painter.save()
        painter.setPen(QPen(ink, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        center = rect.center()
        if index == 0:
            path_y = center.y()
            painter.drawLine(QPointF(center.x() - 7, path_y - 5), QPointF(center.x() + 8, path_y))
            painter.drawLine(QPointF(center.x() + 8, path_y), QPointF(center.x() - 7, path_y + 5))
            painter.drawLine(QPointF(center.x() - 7, path_y + 5), QPointF(center.x() - 7, path_y - 5))
        elif index == 1:
            painter.drawEllipse(QRectF(center.x() - 7, center.y() - 6, 12, 11))
            painter.drawLine(QPointF(center.x() + 3, center.y() + 3), QPointF(center.x() + 8, center.y() + 7))
        elif index == 2:
            painter.drawLine(QPointF(center.x() - 6, center.y() - 6), QPointF(center.x() + 6, center.y()))
            painter.drawLine(QPointF(center.x() + 6, center.y()), QPointF(center.x() - 6, center.y() + 6))
        elif index == 3:
            painter.setBrush(ink)
            painter.drawEllipse(QRectF(center.x() - 3, center.y() - 6, 6, 6))
            painter.drawLine(QPointF(center.x(), center.y()), QPointF(center.x(), center.y() + 8))
        else:
            for row in range(3):
                painter.drawLine(
                    QPointF(center.x() - 8, center.y() - 6 + row * 6),
                    QPointF(center.x() + 8, center.y() - 6 + row * 6),
                )
        painter.restore()

    def _progress(self, painter: QPainter, plate: QRectF, ink: QColor):
        left = QRectF(plate.left() + 10, plate.center().y() - 15, 30, 30)
        right = QRectF(plate.right() - 40, plate.center().y() - 15, 30, 30)
        line = QRectF(left.right() + 14, plate.center().y() - 3, right.left() - left.right() - 28, 6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 80))
        painter.drawRoundedRect(line, 3, 3)
        filled = QRectF(line.left(), line.top(), max(4.0, line.width() * self.progress), line.height())
        painter.setBrush(QColor("#0a84ff"))
        painter.drawRoundedRect(filled, 3, 3)
        width = max(26.0, self._thumb.value)
        thumb = QRectF(filled.right() - width / 2.0, plate.center().y() - 13, width, 26)
        gap_left = abs(thumb.center().x() - left.center().x())
        gap_right = abs(thumb.center().x() - right.center().x())
        if min(gap_left, gap_right) < 28 or self._merge.value > 0.05:
            icon = left if gap_left < gap_right else right
            other = right if icon is left else left
            paint_control_lens(painter, other, self.material, dark=self.dark, scale=1.6)
            paint_merged_lens(painter, thumb, icon, self.material, dark=self.dark, merge=max(self._merge.value, 0.65 if min(gap_left, gap_right) < 28 else 0.0))
        else:
            paint_control_lens(painter, left, self.material, dark=self.dark, scale=1.8)
            paint_control_lens(painter, right, self.material, dark=self.dark, scale=1.8)
            paint_control_lens(painter, thumb, self.material, dark=self.dark, scale=2.5)
        painter.setPen(ink)
        self._progress_line = line

    def mousePressEvent(self, event):
        position = event.position()
        if self._theme_rect.contains(position):
            self.dark = not self.dark
            self.update()
            return
        if abs(position.y() - self._slider_track.center().y()) < 24 and self._slider_track.left() - 20 < position.x() < self._slider_track.right() + 20:
            self._grabbing = "amount"
            self._set_amount(position.x())
        elif hasattr(self, "_progress_line") and abs(position.y() - self._progress_line.center().y()) < 28:
            self._grabbing = "progress"
            self._thumb.target = 44.0
            self._last_x = position.x()
            self._set_progress(position.x())
            self._arm()
        self.update()

    def mouseMoveEvent(self, event):
        position = event.position()
        if self._grabbing == "amount":
            self._set_amount(position.x())
        elif self._grabbing == "progress":
            if self._last_x is not None:
                speed = abs(position.x() - self._last_x)
                self._thumb.target = min(48.0, 40.0 + min(8.0, speed * 0.8))
            self._last_x = position.x()
            self._set_progress(position.x())
            self._merge.target = 1.0 if self.progress < 0.08 or self.progress > 0.92 else 0.0
            self._arm()
        self.update()

    def mouseReleaseEvent(self, _event):
        self._grabbing = ""
        self._thumb.target = 26.0
        self._merge.target = 0.0
        self._arm()
        self.update()

    def _set_amount(self, x: float):
        track = self._slider_track
        self.amount = min(1.0, max(0.0, (x - track.left()) / max(1.0, track.width())))

    def _set_progress(self, x: float):
        line = self._progress_line
        self.progress = min(1.0, max(0.0, (x - line.left()) / max(1.0, line.width())))

    def _arm(self):
        import time
        self._clock = time.monotonic()
        if not self._timer.isActive():
            self._timer.start(16)

    def _tick(self):
        import time
        now = time.monotonic()
        previous = self._clock or now
        dt = min(0.05, max(0.0, now - previous))
        self._clock = now
        moving = self._thumb.step(dt) or self._merge.step(dt)
        self.update()
        if not moving and self._grabbing != "progress":
            self._timer.stop()


def _render(window: GlassDemo) -> QImage:
    image = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.black)
    with QPainter(image) as painter:
        window.paint_scene(painter, QRectF(window.rect()))
    return image


def _compare(folder: Path, window: GlassDemo, theme: str, amount: float, reference: Path):
    window.dark = theme == "dark"
    window.amount = amount
    window.progress = 0.42
    window._thumb.value = 26.0
    window._merge.value = 0.0
    shot = _render(window)
    crop = shot.copy(window.menu_rect.united(window.search_rect).adjusted(-20, -24, 28, 24).toAlignedRect())
    ref = QImage(str(reference))
    scale = crop.height() / max(1, ref.height())
    ref = ref.scaled(max(1, int(ref.width() * scale)), crop.height(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    canvas = QImage(ref.width() + crop.width() + 36, crop.height() + 46, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(QColor("#1c1c1e") if window.dark else QColor("#f2f2f4"))
    levels = control_glass_levels(amount)
    label = f"{'深色' if window.dark else '浅色'}  {'最左' if amount < 0.1 else '最右' if amount > 0.9 else '中间'}  模糊 {levels.blur_px:.0f}px  罩色 {levels.tint * 100:.0f}%"
    with QPainter(canvas) as painter:
        painter.setPen(QColor("white" if window.dark else "black"))
        painter.drawText(QRectF(8, 4, canvas.width() - 16, 28), label + "    左：参考    右：demo")
        painter.drawImage(8, 36, ref)
        painter.drawImage(ref.width() + 24, 36, crop)
    name = f"compare-{theme}-{('left' if amount < 0.1 else 'right' if amount > 0.9 else 'mid')}.png"
    canvas.save(str(folder / name))
    shot.save(str(folder / f"window-{theme}-{('left' if amount < 0.1 else 'right' if amount > 0.9 else 'mid')}.png"))
    return folder / name


def grab(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    window = GlassDemo()
    window.resize(1180, 680)
    paths = []
    for theme in ("light", "dark"):
        for amount in (0.0, 0.5, 1.0):
            paths.append(_compare(folder, window, theme, amount, REFERENCES[(theme, amount)]))
    window.dark = False
    window.amount = 0.35
    window.progress = 0.08
    window._thumb.value = 46
    window._merge.value = 1
    _render(window).save(str(folder / "thumb-merged.png"))
    window.progress = 0.5
    window._merge.value = 0
    window._thumb.value = 44
    _render(window).save(str(folder / "thumb-stretched.png"))
    window.close()
    return paths


def idle_cpu(seconds: float = 2.0) -> float:
    import os
    import time
    window = GlassDemo()
    window.resize(900, 560)
    window.amount = 0.5
    _render(window)
    window._timer.stop()
    if window._timer.isActive():
        raise RuntimeError("idle timer still running")

    def cpu() -> float:
        parts = Path("/proc/self/stat").read_text().split()
        ticks = os.sysconf("SC_CLK_TCK")
        return (int(parts[13]) + int(parts[14])) / ticks

    before_builds = window.material.blur_builds
    start = cpu()
    time.sleep(seconds)
    used = (cpu() - start) / seconds
    if window.material.blur_builds != before_builds:
        raise RuntimeError("blur ran while idle")
    window.close()
    return used


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    if "--grab" in sys.argv:
        folder = Path(sys.argv[sys.argv.index("--grab") + 1])
        for path in grab(folder):
            print(path)
        return
    if "--idle-cpu" in sys.argv:
        used = idle_cpu()
        print(f"idle_cpu_fraction {used:.6f}")
        return
    window = GlassDemo()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
