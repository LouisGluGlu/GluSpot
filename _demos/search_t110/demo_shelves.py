"""方案 B：四类结果各成一条横向货架。"""
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


class ShelfSearch(DemoSearch):
    def __init__(self):
        super().__init__("搜索方案 B · 横向货架")

    def paint_results(self, painter):
        groups = grouped(self.query.text())
        self.group_rects = {}
        if not any(groups.values()):
            painter.setPen(QColor("#b7c0ce"))
            painter.drawText(QRectF(24, 160, self.width() - 48, 40), Qt.AlignmentFlag.AlignCenter, "没有匹配的结果")
            return
        top = 80
        for kind in GROUPS:
            items = groups[kind]
            if not items:
                continue
            heading = QRectF(24, top, 160, 26)
            self.group_rects[kind] = heading
            self.paint_heading(painter, heading, kind)
            x = 24
            for item in items:
                card = QRectF(x, top + 32, 168, 92)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#1c212b"))
                painter.drawRoundedRect(card, 12, 12)
                self.paint_match(painter, QRectF(card.x() + 12, card.y() + 16, card.width() - 24, 24), item.title, 12, "#f4f6fb")
                self.paint_match(painter, QRectF(card.x() + 12, card.y() + 46, card.width() - 24, 20), item.subtitle, 10, "#9aa6b8")
                x += 180
            top += 140

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Down:
            order = [kind for kind in GROUPS if kind in self.group_rects]
            if order:
                current = next((kind for kind, rect in self.group_rects.items() if rect.y() > 80), order[0])
                index = order.index(current)
                kind = order[min(len(order) - 1, index + 1)]
                self.query.setText(grouped(self.query.text())[kind][0].title)
                return
        super().keyPressEvent(event)


def main():
    app = QApplication(sys.argv)
    window = ShelfSearch()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
