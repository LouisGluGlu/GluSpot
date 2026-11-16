"""Preview the two broadcast styles in wide and narrow cover slots.

Dark background. No network, no player, no CDP. Keys 1–4 switch the
broadcast state. Esc quits. ``--grab DIR`` writes one PNG per state and exits.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from local.broadcast_indicator import paint_broadcast_indicator
from local.personal_dj_icon import paint_personal_dj_icon


STATES = ("preparing", "speaking", "paused", "ended")
STATE_LABELS = {
    "preparing": "准备中",
    "speaking": "正在播报",
    "paused": "暂停",
    "ended": "结束",
}


class BroadcastStyleDemo(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("播报样式")
        self.resize(980, 760)
        self.state = "speaking"
        self._started = 0.0
        self._clock = QTimer(self)
        self._clock.setInterval(16)
        self._clock.timeout.connect(self.update)
        self._clock.start()
        self._origin = None

    def _elapsed(self) -> float:
        from time import monotonic
        if self._origin is None:
            self._origin = monotonic()
        moment = monotonic() - self._origin
        if not self.isVisible():
            return 0.0
        return moment

    def keyPressEvent(self, event):
        keys = {
            Qt.Key.Key_1: "preparing",
            Qt.Key.Key_2: "speaking",
            Qt.Key.Key_3: "paused",
            Qt.Key.Key_4: "ended",
        }
        if event.key() in keys:
            self.state = keys[event.key()]
            self.update()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#121418"))
        painter.setPen(QColor("#f2f4f8"))
        painter.setFont(QFont("sans-serif", 16))
        painter.drawText(QRectF(24, 16, 900, 32), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         f"播报样式    {STATE_LABELS[self.state]}    1 准备  2 播报  3 暂停  4 结束")
        columns = (("样式一", "rings"), ("样式二", "face"))
        slots = (("宽", 168.0), ("窄", 280.0))
        elapsed = self._elapsed() if broadcast_motion_animates(self.state) else 1.2
        for index, (label, style) in enumerate(columns):
            left = 48 + index * 460
            painter.setPen(QColor("#f2f4f8"))
            painter.setFont(QFont("sans-serif", 18))
            painter.drawText(QRectF(left, 64, 400, 32), Qt.AlignmentFlag.AlignLeft, label)
            for row, (slot_label, side) in enumerate(slots):
                top = 120 + row * 310
                rect = QRectF(left, top, side, side)
                painter.setPen(QPen(QColor("#3a4150"), 1))
                painter.setBrush(QColor("#1b1f27"))
                painter.drawRoundedRect(rect, side * 0.055, side * 0.055)
                paint_broadcast_indicator(
                    painter, rect, side * 0.055,
                    style=style, state=self.state, elapsed=elapsed,
                    color=QColor("#f0d0a0"),
                    ring_painter=paint_personal_dj_icon,
                    neutral=QColor("#f5f6f8"),
                    backdrop=QColor("#1b1f27"),
                    slot=1.0, blend=1.0,
                )
                painter.setPen(QColor("#c5cad3"))
                painter.setFont(QFont("sans-serif", 13))
                painter.drawText(QRectF(left, top + side + 8, side, 24), Qt.AlignmentFlag.AlignLeft, slot_label)
        painter.end()


def broadcast_motion_animates(state: str) -> bool:
    from local.broadcast_indicator import broadcast_motion
    return broadcast_motion(state).animate


def grab(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    window = BroadcastStyleDemo()
    window.show()
    app = QApplication.instance()
    for state in STATES:
        window.state = state
        window.repaint()
        app.processEvents()
        image = window.grab().toImage()
        image.save(str(directory / f"{state}.png"))
    window.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--grab", type=Path)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    if args.grab:
        grab(args.grab)
        return
    window = BroadcastStyleDemo()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
