"""Project side album covers as complete surfaces, rather than crop their edges."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPolygonF, QTransform


def album_cover_projection(rect: QRectF, perspective: dict[str, object]) -> QTransform:
    """Map a cover onto its symmetric, gently compressed perspective quad.

    The existing cover painter supplies its rounded corners, artwork and sheen;
    transforming it together keeps every part attached to the same surface.
    A zero perspective amount remains an exact identity transform.
    """
    rect = QRectF(rect)
    if rect.width() <= 0.0 or rect.height() <= 0.0:
        return QTransform()
    left_height = max(0.45, min(1.0, float(perspective.get("left_height", 1.0))))
    right_height = max(0.45, min(1.0, float(perspective.get("right_height", 1.0))))
    # Height taper supplies depth; a small width reduction avoids the unnaturally
    # broad side covers produced by cutting a trapezoid from an unchanged square.
    width = rect.width() * (1.0 - abs(left_height - right_height) * 0.28)
    left = rect.center().x() - width / 2.0
    right = rect.center().x() + width / 2.0
    center_y = rect.center().y()
    source = QPolygonF([rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()])
    target = QPolygonF([
        QPointF(left, center_y - rect.height() * left_height / 2.0),
        QPointF(right, center_y - rect.height() * right_height / 2.0),
        QPointF(right, center_y + rect.height() * right_height / 2.0),
        QPointF(left, center_y + rect.height() * left_height / 2.0),
    ])
    transform = QTransform()
    if not QTransform.quadToQuad(source, target, transform):
        return QTransform()
    return transform
