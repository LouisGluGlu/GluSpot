"""One look for "about to choose" and "chosen" rows on every list.

Hovered (待选): a light white frame with a faint wash, so the pointer's
row reads as an outline. Selected (选中, e.g. the song playing): a solid,
brighter plate with no frame. The two never share a colour, on the library
list, a playlist page and the playback window's queue alike.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen


def hover_colors(dark: bool) -> tuple[QColor, QColor]:
    """(wash, frame) of a hovered row."""
    if dark:
        return QColor(255, 255, 255, 14), QColor(255, 255, 255, 92)
    return QColor(255, 255, 255, 140), QColor(0, 0, 0, 46)


def selected_color(dark: bool) -> QColor:
    return QColor(255, 255, 255, 40) if dark else QColor(0, 0, 0, 26)


def paint_row_state(painter: QPainter, rect: QRectF, radius: float, *, dark: bool,
                    hovered: bool = False, selected: bool = False) -> None:
    if not (hovered or selected):
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if selected:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(selected_color(dark))
        painter.drawRoundedRect(QRectF(rect), radius, radius)
    else:
        wash, frame = hover_colors(dark)
        painter.setBrush(wash)
        painter.setPen(QPen(frame, 1.0))
        painter.drawRoundedRect(QRectF(rect).adjusted(.5, .5, -.5, -.5), radius, radius)
    painter.restore()
