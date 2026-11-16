"""Translation icon motion. Other toolbar buttons stay flat glass.

Private broadcast is the only control with a status lamp. Translation feedback
is the 文 / A swap itself. Three motions share one critically damped spring
(damping ratio 1). A new click retargets from the current pose.
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter

from local.personal_dj_icon import paint_personal_dj_icon

from _demos.choices_t110.common import (
    Stage, application, capture_window, ink, paint_flat_disc, paint_icon, ui_font,
)


class CriticalSpring:
    """ζ = 1. Position and velocity survive a new target."""

    def __init__(self, omega: float = 11.0):
        self.omega = omega
        self.value = 0.0
        self.velocity = 0.0
        self.target = 0.0

    def set_target(self, on: bool) -> None:
        self.target = 1.0 if on else 0.0

    def step(self, dt: float) -> float:
        remaining = min(max(0.0, dt), 0.05)
        omega = self.omega
        while remaining > 0.0:
            step = min(0.008, remaining)
            acceleration = omega * omega * (self.target - self.value) - 2.0 * omega * self.velocity
            self.velocity += acceleration * step
            self.value += self.velocity * step
            remaining -= step
        if abs(self.target - self.value) < 0.001 and abs(self.velocity) < 0.02:
            self.value = self.target
            self.velocity = 0.0
        return self.value


def glyph_slots(rect: QRectF) -> tuple[QPointF, QPointF]:
    center = rect.center()
    primary = QPointF(center.x() - rect.width() * 0.10, center.y() - rect.height() * 0.02)
    secondary = QPointF(center.x() + rect.width() * 0.16, center.y() + rect.height() * 0.16)
    return primary, secondary


def translation_pose(kind: str, rect: QRectF, progress: float) -> tuple[QPointF, QPointF, float, float, float]:
    """Return 文 point, A point, 文 size, A size, and a horizontal scale for the flip."""
    progress = max(0.0, min(1.0, progress))
    primary, secondary = glyph_slots(rect)
    big = rect.width() * 0.34
    small = rect.width() * 0.22
    if kind == "flip":
        scale = abs(math.cos(math.pi * progress))
        if progress < 0.5:
            return primary, secondary, big, small, scale
        return secondary, primary, small, big, scale
    wen_size = big + (small - big) * progress
    aye_size = small + (big - small) * progress
    if kind == "arc":
        lift = rect.height() * 0.22
        side = rect.width() * 0.24 * math.sin(math.pi * progress)
        wen_travel = math.sin(progress * math.pi / 2)
        aye_travel = 1.0 - math.cos(progress * math.pi / 2)
        wen = QPointF(
            primary.x() + (secondary.x() - primary.x()) * wen_travel + side,
            primary.y() + (secondary.y() - primary.y()) * wen_travel - lift * math.sin(math.pi * progress),
        )
        aye = QPointF(
            secondary.x() + (primary.x() - secondary.x()) * aye_travel - side,
            secondary.y() + (primary.y() - secondary.y()) * aye_travel + lift * math.sin(math.pi * progress),
        )
        return wen, aye, wen_size, aye_size, 1.0
    bow = math.sin(math.pi * progress) * rect.height() * 0.20
    wen = QPointF(
        primary.x() + (secondary.x() - primary.x()) * progress,
        primary.y() + (secondary.y() - primary.y()) * progress + bow,
    )
    aye = QPointF(
        secondary.x() + (primary.x() - secondary.x()) * progress,
        secondary.y() + (primary.y() - secondary.y()) * progress - bow,
    )
    return wen, aye, wen_size, aye_size, 1.0


def paint_translation(painter: QPainter, rect: QRectF, kind: str, progress: float, color: QColor) -> None:
    wen, aye, wen_size, aye_size, scale = translation_pose(kind, rect, progress)
    painter.save()
    painter.setPen(color)
    painter.translate(rect.center())
    painter.scale(max(0.02, scale), 1.0)
    painter.translate(-rect.center())

    def draw(point: QPointF, text: str, pixel: float) -> None:
        painter.setFont(ui_font(max(8, pixel), bold=True))
        box = QRectF(point.x() - pixel, point.y() - pixel * 0.7, pixel * 2, pixel * 1.4)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    if aye.y() <= wen.y():
        draw(aye, "A", aye_size)
        draw(wen, "文", wen_size)
    else:
        draw(wen, "文", wen_size)
        draw(aye, "A", aye_size)
    painter.restore()


class ActiveDemo(Stage):
    def __init__(self):
        super().__init__("2  译文图标动画", (
            ("slide", "A  交叉滑过"), ("flip", "B  翻转折返"), ("arc", "C  弧线交换"),
        ))
        self.resize(1180, 640)
        self.springs = {key: CriticalSpring() for key, _label in self.options}
        self.on = {key: False for key, _label in self.options}
        self.broadcast_on = True
        self.hover = ""
        self._last = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._frozen = False

    def description(self) -> str:
        return "已选定 A 交叉滑过。图标大小还等补充，这里先不改大小。弹簧阻尼比 1，连点从当前位置折返。状态灯只在私人播报上。"

    def _tick(self) -> None:
        if self._frozen:
            return
        now = time.monotonic()
        dt = now - self._last
        self._last = now
        for spring in self.springs.values():
            spring.step(dt)
        self.update()

    def _columns(self) -> list[tuple[str, QRectF]]:
        body = self.body_rect().adjusted(8, 8, -8, -8)
        gap = 14.0
        width = (body.width() - gap * 2) / 3
        return [(key, QRectF(body.left() + index * (width + gap), body.top(), width, body.height()))
                for index, (key, _label) in enumerate(self.options)]

    def _big(self, column: QRectF) -> QRectF:
        side = min(168.0, column.width() - 48)
        return QRectF(column.center().x() - side / 2, column.top() + 78, side, side)

    def _row(self, column: QRectF) -> list[tuple[str, QRectF]]:
        keys = ("heart", "playlist", "broadcast", "loop", "translate")
        side = 34.0
        gap = 8.0
        used = len(keys) * side + (len(keys) - 1) * gap
        x = column.center().x() - used / 2
        y = column.bottom() - 78
        return [(key, QRectF(x + index * (side + gap), y, side, side)) for index, key in enumerate(keys)]

    def _hit(self, point) -> tuple[str, str]:
        for kind, column in self._columns():
            if self._big(column).contains(point):
                return kind, "translate"
            for key, rect in self._row(column):
                if rect.contains(point):
                    return kind, key
        return "", ""

    def mousePressEvent(self, event) -> None:
        kind, key = self._hit(event.position())
        if key == "translate" and kind:
            self.on[kind] = not self.on[kind]
            self.springs[kind].set_target(self.on[kind])
            self.update()
            event.accept()
            return
        if key == "broadcast":
            self.broadcast_on = not self.broadcast_on
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def paint_body(self, painter: QPainter, _backdrop) -> None:
        notes = {
            "slide": "两个字交叉滑过，一个向上让，一个向下让，终点换位。",
            "flip": "按钮横向收到一条线，再展开。收到最薄时两个字已经换好。",
            "arc": "两个字沿相反的弧线走到对方的位置。",
        }
        for kind, column in self._columns():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 70))
            painter.drawRoundedRect(column, 18, 18)
            painter.setPen(ink())
            painter.setFont(ui_font(16, bold=True))
            painter.drawText(column.adjusted(16, 14, -16, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                             dict(self.options)[kind])
            painter.setFont(ui_font(12))
            painter.setPen(ink(200))
            painter.drawText(QRectF(column.left() + 16, column.top() + 40, column.width() - 32, 36),
                             Qt.TextFlag.TextWordWrap, notes[kind])
            progress = self.springs[kind].value
            big = self._big(column)
            paint_flat_disc(painter, big)
            paint_translation(painter, big, kind, progress, ink())
            if progress < 0.08:
                state = "翻译未开"
            elif progress > 0.92:
                state = "翻译已开"
            else:
                state = "交换中"
            painter.setFont(ui_font(12))
            painter.setPen(ink(210))
            painter.drawText(QRectF(column.left(), big.bottom() + 6, column.width(), 20),
                             Qt.AlignmentFlag.AlignCenter, f"{state} · {progress:.2f}")
            for key, rect in self._row(column):
                paint_flat_disc(painter, rect)
                self._paint_small(painter, kind, key, rect, progress)

    def _paint_small(self, painter: QPainter, kind: str, key: str, rect: QRectF, progress: float) -> None:
        color = ink()
        icon = rect.adjusted(8, 8, -8, -8)
        if key == "heart":
            paint_icon(painter, icon, "heart_outline", color)
        elif key == "playlist":
            paint_icon(painter, icon, "playlist_add", color)
        elif key == "loop":
            paint_icon(painter, icon, "sequence", color)
        elif key == "broadcast":
            paint_personal_dj_icon(painter, icon, color, elapsed=0.4)
            if self.broadcast_on:
                dot = QRectF(rect.right() - 9, rect.bottom() - 9, 7, 7)
                painter.setPen(QColor(16, 18, 22, 220))
                painter.setBrush(QColor("#3fd27f"))
                painter.drawEllipse(dot)
        else:
            paint_translation(painter, rect, kind, progress, color)

    def freeze(self, progresses: dict[str, float]) -> None:
        self.timer.stop()
        self._frozen = True
        for key, value in progresses.items():
            self.springs[key].value = value
            self.springs[key].velocity = 0.0
            self.springs[key].target = 1.0 if value >= 0.5 else 0.0
            self.on[key] = value >= 0.5
        self.update()


def capture(directory) -> list:
    from pathlib import Path
    directory = Path(directory)
    spring = CriticalSpring()
    spring.set_target(True)
    peak = 0.0
    for _ in range(80):
        spring.step(0.016)
        peak = max(peak, spring.value)
    assert peak <= 1.02
    assert spring.value > 0.5
    mid = spring.value
    spring.set_target(False)
    spring.step(0.016)
    assert spring.value < mid
    rect = QRectF(0, 0, 80, 80)
    off = translation_pose("slide", rect, 0.0)
    on = translation_pose("slide", rect, 1.0)
    assert abs(off[0].x() - on[1].x()) < 1.0
    assert abs(off[1].x() - on[0].x()) < 1.0
    assert translation_pose("flip", rect, 0.5)[4] < 0.05
    app = application()
    window = ActiveDemo()
    from _demos.choices_t110.common import assert_painted, contact_sheet, save_image
    frames = []
    window.freeze({"slide": 0.0, "flip": 0.0, "arc": 0.0})
    image = capture_window(window)
    assert_painted(image, "translate off")
    frames.append(("关：文在主位，A 在右下", image))
    save_image(image, directory / "translate_off.png")
    window.freeze({"slide": 0.45, "flip": 0.45, "arc": 0.45})
    image = capture_window(window)
    assert_painted(image, "translate mid")
    frames.append(("换到一半：滑过 / 翻薄 / 走弧", image))
    save_image(image, directory / "translate_mid.png")
    window.freeze({"slide": 1.0, "flip": 1.0, "arc": 1.0})
    image = capture_window(window)
    assert_painted(image, "translate on")
    frames.append(("开：A 在主位，文在右下", image))
    save_image(image, directory / "translate_on.png")
    sheet = contact_sheet(frames, directory / "active_sheet.png", columns=1)
    window.close()
    del app
    return ["translation spring", str(sheet)]


def main() -> None:
    app = application()
    window = ActiveDemo()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
