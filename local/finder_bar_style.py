"""Shared immersive Finder/Tasker Bar material and settings preview."""
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QLinearGradient, QPainterPath


def finder_fade_positions(rect: QRectF, unit=1.0):
    lane = 8.0 * unit + max(1.0, rect.height() - 4.0) + 4.0 * unit
    start = min(.32, lane / max(1.0, rect.width()))
    return start, min(.58, start + 24.0 * unit / max(1.0, rect.width()))


# Invisible but present: a layered window lets clicks through pixels whose
# alpha is 0, so a "transparent" bar sent clicks on the island to the Finder
# bar behind it (which hid the island). Alpha 1 looks the same and is hit.
HIT_FLOOR = QColor(0, 0, 0, 1)


def paint_finder_background(painter, rect, style, pigment, unit=1.0):
    painter.fillRect(rect, HIT_FLOOR)
    if style == "transparent":
        return
    color = QColor(pigment)
    color.setAlpha(222)
    if style == "fade":
        gradient = QLinearGradient(rect.topLeft(), rect.topRight())
        edge = QColor(color)
        edge.setAlpha(0)
        start, end = finder_fade_positions(rect, unit)
        for stop, ink in ((0., edge), (start, edge), (end, color), (.82, color), (1., edge)):
            gradient.setColorAt(stop, ink)
        painter.fillRect(rect, gradient)
    else:
        path = QPainterPath()
        radius = min(7.0 * unit, rect.height() / 3.0)
        path.addRoundedRect(rect.adjusted(0., 1., 0., -1.), radius, radius)
        painter.fillPath(path, color)
