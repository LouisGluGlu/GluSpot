"""Small shared artwork placeholder; painting never fetches or decodes images."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainterPath


def paint_artwork_skeleton(painter, rect, ink, seconds, *, radius=6.):
    """Paint a clipped, slow light sweep using the caller's visible-frame clock."""
    if rect.isEmpty():
        return
    painter.save()
    shape = QPainterPath()
    shape.addRoundedRect(rect, radius, radius)
    painter.setClipPath(shape, Qt.ClipOperation.IntersectClip)
    base = QColor(ink)
    base.setAlpha(23)
    painter.fillPath(shape, base)
    width = max(1., rect.width())
    center = rect.left() + ((float(seconds) / 1.8) % 1.) * width * 3. - width
    sweep = QLinearGradient(center - width * .65, rect.top(), center + width * .65, rect.bottom())
    for position, alpha in ((0., 0), (.32, 0), (.5, 48), (.68, 0), (1., 0)):
        color = QColor(ink)
        color.setAlpha(alpha)
        sweep.setColorAt(position, color)
    painter.fillRect(QRectF(rect), sweep)
    painter.restore()
