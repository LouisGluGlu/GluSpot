"""Small vector speech glyph driven only by measured PCM frequency bands."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen


def paint_personal_dj_icon(painter: QPainter, rect: QRectF, color: QColor, *, bands=None):
    levels = (bands.low, bands.mid, bands.high) if bands is not None else (0.0, 0.0, 0.0)
    levels = tuple(max(0.0, min(1.0, float(level) * 4.0)) for level in levels)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for index, base in enumerate((.24, .55, .82, .55, .24)):
        energy = levels[(0, 1, 2, 1, 0)[index]]
        height = rect.height() * min(.98, base + energy * .16)
        x = rect.left() + rect.width() * (.12 + index * .19)
        y = rect.center().y()
        curve = QPainterPath(QPointF(x + rect.width() * .045, y - height / 2))
        curve.cubicTo(x - rect.width() * .085, y - height * .32,
                      x - rect.width() * .085, y + height * .32,
                      x + rect.width() * .045, y + height / 2)
        for spread, alpha in ((4.4, 25), (2.6, 48)):
            if energy <= .001:
                continue
            glow = QColor(color)
            glow.setAlpha(round(alpha * energy))
            painter.setPen(QPen(glow, rect.width() * .05 * spread, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawPath(curve)
        ink = QColor(color)
        ink.setAlpha(round(175 + 80 * energy))
        painter.setPen(QPen(ink, max(1.0, rect.width() * .05), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(curve)
    painter.restore()
