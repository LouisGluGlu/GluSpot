"""Magnet dock. One confirmed rule.

The island follows the pointer while it is held. Staying in the same place
for more than two seconds pins it there, even if it covers the tile. A release
inside two seconds snaps in with a critically damped spring only when at least
half the island width overlaps the tile. The caption uses a latch so it does
not flicker around that halfway line.
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
from PySide6.QtGui import QColor, QPainter, QPen

from _demos.choices_t110.common import (
    SourceFlatButton, Stage, application, capture_window, ink, ui_font,
)


SNAP_SECONDS = 2.0
OVERLAP_ARM = 0.50
OVERLAP_DROP = 0.40
DWELL_MOVE = 8.0
ISLAND_SIZE = (248.0, 64.0)


def width_overlap(island: QRectF, tile: QRectF) -> float:
    if island.width() <= 1 or not island.intersects(tile):
        return 0.0
    span = min(island.right(), tile.right()) - max(island.left(), tile.left())
    return max(0.0, span / float(island.width()))


class MagnetLatch:
    """Overlap and dwell latches. Paint never calls this."""

    def __init__(self):
        self.armed = False
        self.pinned = False
        self.anchor: tuple[float, float] | None = None
        self.anchor_time = 0.0
        self.elapsed = 0.0

    def observe(self, x: float, y: float, overlap: float, now: float) -> None:
        if self.anchor is None or math.hypot(x - self.anchor[0], y - self.anchor[1]) > DWELL_MOVE:
            self.anchor = (x, y)
            self.anchor_time = now
            self.pinned = False
            self.elapsed = 0.0
        else:
            self.elapsed = max(0.0, now - self.anchor_time)
            if self.elapsed > SNAP_SECONDS:
                self.pinned = True
        if overlap >= OVERLAP_ARM:
            self.armed = True
        elif overlap <= OVERLAP_DROP:
            self.armed = False

    def decision(self) -> str:
        if self.pinned:
            return "stay"
        if self.armed and self.elapsed <= SNAP_SECONDS:
            return "snap"
        return "stay"

    def caption(self) -> str:
        if self.pinned:
            return "将停在这里"
        if self.armed:
            return "松手吸附"
        return "吸附"


class RectSpring:
    """ζ = 1. Idle while the pointer owns the island."""

    def __init__(self, omega: float = 12.0):
        self.omega = omega
        self.active = False
        self.value = [0.0, 0.0, 0.0, 0.0]
        self.velocity = [0.0, 0.0, 0.0, 0.0]
        self.target = [0.0, 0.0, 0.0, 0.0]

    def start(self, current: QRectF, target: QRectF) -> None:
        self.active = True
        self.value = [current.x(), current.y(), current.width(), current.height()]
        self.velocity = [0.0, 0.0, 0.0, 0.0]
        self.target = [target.x(), target.y(), target.width(), target.height()]

    def step(self, dt: float) -> QRectF:
        remaining = min(max(0.0, dt), 0.05)
        omega = self.omega
        while remaining > 0.0:
            step = min(0.008, remaining)
            for index in range(4):
                acceleration = omega * omega * (self.target[index] - self.value[index]) - 2.0 * omega * self.velocity[index]
                self.velocity[index] += acceleration * step
                self.value[index] += self.velocity[index] * step
            remaining -= step
        if all(abs(self.target[index] - self.value[index]) < 0.4 and abs(self.velocity[index]) < 8.0 for index in range(4)):
            self.value = list(self.target)
            self.velocity = [0.0, 0.0, 0.0, 0.0]
            self.active = False
        return QRectF(*self.value)

    def cancel(self) -> None:
        self.active = False


def docked_rect(tile: QRectF) -> QRectF:
    width, height = ISLAND_SIZE
    return QRectF(tile.center().x() - width / 2.0, tile.center().y() - height / 2.0, width, height)


class MagnetDemo(Stage):
    def __init__(self):
        super().__init__("1  磁吸停靠", (("rule", "规则已确认"),))
        self.mode = "finder"
        self.resize(1100, 760)
        self.island = QRectF(120, 240, *ISLAND_SIZE)
        self.dragging = False
        self.drag_offset = QPointF()
        self.latch = MagnetLatch()
        self.spring = RectSpring()
        self.snapped = False
        self.overlap = 0.0
        self.note = "按住小岛拖到蓝色磁贴上"
        self._last = time.monotonic()
        self._clock = time.monotonic()
        self._frozen = False
        self.finder_button = SourceFlatButton("Finder 栏", self, primary=True)
        self.taskbar_button = SourceFlatButton("任务栏", self, primary=False)
        self.reset_button = SourceFlatButton("复位", self, primary=False)
        self.finder_button.clicked.connect(lambda: self.set_mode("finder"))
        self.taskbar_button.clicked.connect(lambda: self.set_mode("taskbar"))
        self.reset_button.clicked.connect(self.reset_island)
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def description(self) -> str:
        return "同一位置停超过 2 秒就停在原地，哪怕几乎盖住磁贴。只有 2 秒内松手，并且岛宽有一半压在磁贴上，才会弹簧吸进去。拖的时候岛只跟手。"

    def set_mode(self, mode: str) -> None:
        self.mode = "taskbar" if mode == "taskbar" else "finder"
        self.finder_button.primary = self.mode == "finder"
        self.taskbar_button.primary = self.mode == "taskbar"
        self.finder_button.update()
        self.taskbar_button.update()
        self.spring.cancel()
        self.snapped = False
        self.latch = MagnetLatch()
        self.note = "模式已切换"
        self.update()

    def reset_island(self) -> None:
        screen = self.screen_rect()
        self.island = QRectF(screen.left() + 36, screen.center().y() - 20, *ISLAND_SIZE)
        self.spring.cancel()
        self.snapped = False
        self.dragging = False
        self.latch = MagnetLatch()
        self.overlap = 0.0
        self.note = "已复位"
        self.update()

    def relayout(self) -> None:
        self.finder_button.setGeometry(self.width() - 360, 74, 110, 34)
        self.taskbar_button.setGeometry(self.width() - 242, 74, 96, 34)
        self.reset_button.setGeometry(self.width() - 138, 74, 72, 34)

    def screen_rect(self) -> QRectF:
        return self.body_rect().adjusted(8, 8, -8, -28)

    def tile_rect(self) -> QRectF:
        screen = self.screen_rect()
        width, height = 340.0, 84.0
        x = screen.center().x() - width / 2.0
        if self.mode == "finder":
            bar = self.finder_bar()
            return QRectF(x, bar.center().y() - height / 2.0, width, height)
        bar = self.taskbar_rect()
        return QRectF(x, bar.center().y() - height / 2.0, width, height)

    def finder_bar(self) -> QRectF:
        screen = self.screen_rect()
        return QRectF(screen.left(), screen.top(), screen.width(), 92)

    def taskbar_rect(self) -> QRectF:
        screen = self.screen_rect()
        return QRectF(screen.left(), screen.bottom() - 92, screen.width(), 92)

    def _now(self) -> float:
        return self._clock if self._frozen else time.monotonic()

    def _follow(self, point: QPointF) -> None:
        screen = self.screen_rect()
        x = point.x() - self.drag_offset.x()
        y = point.y() - self.drag_offset.y()
        x = min(max(screen.left(), x), screen.right() - self.island.width())
        y = min(max(screen.top(), y), screen.bottom() - self.island.height())
        self.island.moveTo(x, y)

    def _observe(self) -> None:
        self.overlap = width_overlap(self.island, self.tile_rect())
        self.latch.observe(self.island.x(), self.island.y(), self.overlap, self._now())

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.05, max(0.0, now - self._last))
        self._last = now
        if self._frozen:
            return
        if self.dragging:
            self._observe()
        elif self.spring.active:
            self.island = self.spring.step(dt)
            if not self.spring.active:
                self.snapped = True
                self.note = "已吸进磁贴"
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.island.contains(event.position()):
            self.spring.cancel()
            self.snapped = False
            self.dragging = True
            width, height = ISLAND_SIZE
            point = event.position()
            grabbed_x = (point.x() - self.island.left()) / max(1.0, self.island.width())
            grabbed_y = (point.y() - self.island.top()) / max(1.0, self.island.height())
            self.island = QRectF(point.x() - grabbed_x * width, point.y() - grabbed_y * height, width, height)
            self.drag_offset = point - self.island.topLeft()
            self.latch = MagnetLatch()
            self._observe()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not self.dragging:
            super().mouseMoveEvent(event)
            return
        before = QPointF(self.island.topLeft())
        self._follow(event.position())
        # The grabbed point stays on the cursor. Nothing else may move the island.
        self._observe()
        self._last_pointer_delta = self.island.topLeft() - before
        event.accept()
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if not (self.dragging and event.button() == Qt.MouseButton.LeftButton):
            super().mouseReleaseEvent(event)
            return
        self.dragging = False
        self._observe()
        if self.latch.decision() == "snap":
            self.spring.start(QRectF(self.island), docked_rect(self.tile_rect()))
            self.note = "正在吸进磁贴"
        else:
            self.spring.cancel()
            self.snapped = False
            self.note = "停在原地" if self.latch.pinned else "没有吸附"
        self.update()
        event.accept()

    def paint_body(self, painter: QPainter, _backdrop) -> None:
        screen = self.screen_rect()
        tile = self.tile_rect()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(8, 10, 14, 150))
        painter.drawRoundedRect(screen, 18, 18)
        bar = self.finder_bar() if self.mode == "finder" else self.taskbar_rect()
        painter.setBrush(QColor(255, 255, 255, 28))
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1))
        painter.drawRoundedRect(bar, 16, 16)
        painter.setFont(ui_font(12, bold=True))
        painter.setPen(ink(180))
        painter.drawText(bar.adjusted(16, 0, -16, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, "Finder 栏" if self.mode == "finder" else "任务栏")
        pinned = self.latch.pinned
        fill = QColor(186, 122, 48, 168) if pinned else QColor(37, 112, 214, 158)
        edge = QColor(255, 214, 150) if pinned else QColor(120, 186, 255)
        painter.setBrush(fill)
        painter.setPen(QPen(edge, 2.0))
        painter.drawRoundedRect(tile.adjusted(1, 1, -1, -1), 18, 18)
        caption = self.latch.caption()
        painter.setFont(ui_font(13, bold=True))
        painter.setPen(edge)
        if self.mode == "finder":
            caption_rect = QRectF(tile.left(), tile.bottom() + 8, tile.width(), 22)
        else:
            caption_rect = QRectF(tile.left(), tile.top() - 26, tile.width(), 22)
        painter.drawText(caption_rect, Qt.AlignmentFlag.AlignCenter, caption)
        self._paint_island(painter)
        painter.setFont(ui_font(13))
        painter.setPen(ink())
        state = "拖动中" if self.dragging else ("已贴合" if self.snapped else "静止")
        painter.drawText(QRectF(screen.left(), screen.bottom() + 4, screen.width(), 22),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         f"{state} · 重叠 {round(self.overlap * 100)}% · 停留 {self.latch.elapsed:.1f}s · {caption} · {self.note}")

    def _paint_island(self, painter: QPainter) -> None:
        rect = self.island
        painter.setPen(QPen(QColor(255, 255, 255, 80), 1))
        painter.setBrush(QColor(28, 30, 36, 210))
        painter.drawRoundedRect(rect, 16, 16)
        cover = QRectF(rect.left() + 10, rect.center().y() - 20, 40, 40)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#d493a2"))
        painter.drawRoundedRect(cover, 10, 10)
        painter.setPen(ink())
        painter.setFont(ui_font(15, bold=True))
        painter.drawText(QRectF(cover.right() + 12, rect.top() + 10, rect.width() - 78, 22),
                         Qt.AlignmentFlag.AlignVCenter, "What Would U Do")
        painter.setFont(ui_font(12))
        painter.setPen(ink(180))
        painter.drawText(QRectF(cover.right() + 12, rect.top() + 32, rect.width() - 78, 18),
                         Qt.AlignmentFlag.AlignVCenter, "Haley Smalls")

    def preview(self, kind: str, mode: str = "finder") -> None:
        self.timer.stop()
        self._frozen = True
        self.spring.cancel()
        self.set_mode(mode)
        self.dragging = False
        self.snapped = False
        tile = self.tile_rect()
        self.latch = MagnetLatch()
        if kind == "pin":
            self.island = QRectF(tile.center().x() - ISLAND_SIZE[0] / 2.0 + 28, tile.center().y() - ISLAND_SIZE[1] / 2.0, *ISLAND_SIZE)
            self.overlap = width_overlap(self.island, tile)
            self.latch.armed = True
            self.latch.pinned = True
            self.latch.elapsed = 2.4
            self.note = "停超过 2 秒，松手也不吸"
        elif kind == "snap":
            self.island = QRectF(docked_rect(tile))
            self.snapped = True
            self.overlap = width_overlap(self.island, tile)
            self.latch.armed = True
            self.latch.elapsed = 0.4
            self.note = "已吸进磁贴"
        else:
            self.island = QRectF(tile.left() + 24, tile.top() + 10, *ISLAND_SIZE)
            self.overlap = width_overlap(self.island, tile)
            self.latch.armed = True
            self.latch.elapsed = 0.6
            self.dragging = True
            self.note = "重叠够，两秒内松手会吸"
        self.update()


def capture(directory) -> list:
    from pathlib import Path
    directory = Path(directory)
    latch = MagnetLatch()
    latch.observe(0, 0, 0.2, 0.0)
    latch.observe(2, 1, 0.62, 0.4)
    assert latch.decision() == "snap"
    assert latch.caption() == "松手吸附"
    latch.observe(3, 1, 0.99, 2.5)
    assert latch.pinned
    assert latch.decision() == "stay"
    assert latch.caption() == "将停在这里"
    jitter = MagnetLatch()
    jitter.observe(0, 0, 0.52, 0.0)
    jitter.observe(1, 0, 0.47, 0.2)
    jitter.observe(0, 1, 0.44, 0.4)
    assert jitter.armed and jitter.caption() == "松手吸附"
    jitter.observe(0, 1, 0.30, 0.5)
    assert not jitter.armed and jitter.caption() == "吸附"
    moved = MagnetLatch()
    moved.observe(0, 0, 0.9, 0.0)
    moved.observe(1, 0, 0.9, 2.4)
    assert moved.pinned
    moved.observe(40, 0, 0.9, 2.5)
    assert not moved.pinned and moved.elapsed == 0.0
    spring = RectSpring()
    spring.start(QRectF(0, 0, 10, 10), QRectF(40, 0, 10, 10))
    peak = 0.0
    for _ in range(80):
        rect = spring.step(0.016)
        peak = max(peak, rect.x())
    assert peak <= 40.5
    app = application()
    window = MagnetDemo()
    window.preview("arm")
    point = QPointF(window.island.x() + 12, window.island.y() + 8)
    window.dragging = True
    window.drag_offset = point - window.island.topLeft()
    window._follow(point)
    assert abs(window.island.x() + window.drag_offset.x() - point.x()) < 0.1
    held = QPointF(window.island.topLeft())
    window._follow(point)
    assert window.island.topLeft() == held
    from _demos.choices_t110.common import assert_painted, contact_sheet, save_image
    frames = []
    for kind, mode, label in (
        ("arm", "finder", "两秒内，重叠够，松手会吸"),
        ("pin", "finder", "停超过 2 秒，将停在这里"),
        ("snap", "finder", "弹簧吸进磁贴"),
        ("pin", "taskbar", "任务栏同样先停住"),
    ):
        window.preview(kind, mode)
        image = capture_window(window)
        assert_painted(image, label)
        frames.append((label, image))
        save_image(image, directory / f"magnet_{mode}_{kind}.png")
    sheet = contact_sheet(frames, directory / "magnet_sheet.png", columns=2)
    window.close()
    del app
    return ["magnet dwell", str(sheet)]


def main() -> None:
    app = application()
    window = MagnetDemo()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
