"""方案 A：歌曲、专辑、艺人、歌单分成上下四段列表。"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from common import GROUPS, DemoSearch, grouped


class SectionSearch(DemoSearch):
    def __init__(self):
        super().__init__("搜索方案 A · 分组列表")
        self._flat = []

    def paint_results(self, painter):
        groups = grouped(self.query.text())
        self.group_rects = {}
        self._flat = []
        y = 80
        if not any(groups.values()):
            painter.setPen(QColor("#b7c0ce"))
            painter.drawText(QRectF(24, 160, self.width() - 48, 40), Qt.AlignmentFlag.AlignCenter, "没有匹配的结果")
            return
        for kind in GROUPS:
            items = groups[kind]
            if not items:
                continue
            heading = QRectF(24, y, self.width() - 48, 28)
            self.group_rects[kind] = heading
            self.paint_heading(painter, heading, kind)
            y += 32
            for item in items:
                row = QRectF(24, y, self.width() - 48, 52)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#1c212b"))
                painter.drawRoundedRect(row, 10, 10)
                self.paint_match(painter, QRectF(row.x() + 16, row.y() + 6, row.width() - 32, 22), item.title, 13, "#f4f6fb")
                self.paint_match(painter, QRectF(row.x() + 16, row.y() + 28, row.width() - 32, 18), item.subtitle, 10, "#9aa6b8")
                self._flat.append((kind, item, row))
                y += 60

    def keyPressEvent(self, event):
        if event.key() not in {Qt.Key.Key_Down, Qt.Key.Key_Up} or not self._flat:
            super().keyPressEvent(event)
            return
        titles = [item.title for _, item, _ in self._flat]
        current = self.query.text().strip()
        if current in titles:
            index = titles.index(current)
            index = min(len(titles) - 1, index + 1) if event.key() == Qt.Key.Key_Down else max(0, index - 1)
        else:
            index = 0
        self.query.setText(self._flat[index][1].title)


def main():
    app = QApplication(sys.argv)
    window = SectionSearch()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
