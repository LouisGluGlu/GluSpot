"""Recent searches, shown in place of results while the search box is empty."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from local.playback_entry_window import _entry_font

ROW_HEIGHT = 40
HEADING_HEIGHT = 40


def _ink(widget):
    from local.theme_tokens import hex_rgb
    from local.ui_theme import active_tokens
    tokens = active_tokens(widget)
    if tokens.dark and tokens.shade >= 0.999:
        return QColor("#f3f4f7"), QColor("#adb8c8"), True
    return QColor(hex_rgb(tokens.text)), QColor(hex_rgb(tokens.muted)), tokens.dark


def _clock_glyph(painter, rect, color):
    painter.save()
    painter.setPen(QPen(color, 1.4))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    side = min(rect.width(), rect.height())
    circle = QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2, side, side)
    painter.drawEllipse(circle)
    c = circle.center()
    path = QPainterPath(c)
    path.lineTo(c.x(), c.y() - side * 0.28)
    path.moveTo(c)
    path.lineTo(c.x() + side * 0.22, c.y() + side * 0.08)
    painter.drawPath(path)
    painter.restore()


class SearchHistoryPanel(QWidget):
    """Newest first. A click searches again; 清除 forgets them all."""

    queryChosen = Signal(str)
    clearRequested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.items: tuple = ()
        self._hover = -2            # -1 is the clear link
        self.setMouseTracking(True)
        self.hide()

    def set_items(self, items):
        self.items = tuple(reversed(tuple(items)))
        self.update()

    def clear_rect(self):
        return QRectF(self.width() - 64, 8, 52, HEADING_HEIGHT - 16)

    def row_rect(self, index):
        return QRectF(8, HEADING_HEIGHT + index * ROW_HEIGHT, self.width() - 16, ROW_HEIGHT)

    def _hit(self, point):
        if self.clear_rect().contains(point):
            return -1
        index = int((point.y() - HEADING_HEIGHT) // ROW_HEIGHT)
        if point.y() >= HEADING_HEIGHT and 0 <= index < len(self.items):
            return index
        return -2

    def mouseMoveEvent(self, event):
        hover = self._hit(event.position())
        if hover != self._hover:
            self._hover = hover
            self.setCursor(Qt.CursorShape.PointingHandCursor if hover != -2 else Qt.CursorShape.ArrowCursor)
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover = -2
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            hit = self._hit(event.position())
            if hit == -1:
                self.clearRequested.emit()
            elif hit >= 0:
                self.queryChosen.emit(self.items[hit])
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        ink, muted, dark = _ink(self)
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            heading = _entry_font(13)
            heading.setWeight(QFont.Weight.Bold)
            p.setFont(heading)
            p.setPen(ink)
            p.drawText(QRectF(16, 0, self.width() - 96, HEADING_HEIGHT), Qt.AlignmentFlag.AlignVCenter, "最近搜索")
            p.setFont(_entry_font(12))
            p.setPen(ink if self._hover == -1 else muted)
            p.drawText(self.clear_rect(), Qt.AlignmentFlag.AlignCenter, "清除")
            p.setFont(_entry_font(14))
            for index, text in enumerate(self.items):
                row = self.row_rect(index)
                if row.top() > self.height():
                    break
                if index == self._hover:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(255, 255, 255, 24) if dark else QColor(0, 0, 0, 20))
                    p.drawRoundedRect(row.adjusted(0, 2, 0, -2), 10, 10)
                _clock_glyph(p, QRectF(row.left() + 10, row.center().y() - 7, 14, 14), muted)
                p.setPen(ink)
                p.drawText(row.adjusted(36, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter,
                           p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, int(row.width() - 44)))
