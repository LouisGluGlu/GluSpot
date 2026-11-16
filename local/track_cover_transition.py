"""Shared island/window cover flip and palette reflection, driven by host clocks."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QRadialGradient

from local.album_island_projection import album_cover_projection


TRACK_COVER_SECONDS = .640
ARTWORK_UPGRADE_SECONDS = .240


def cover_transition_progress(started, now, duration=TRACK_COVER_SECONDS):
    return max(0., min(1., (now - started) / duration)) if started > 0. else 1.


def paint_track_cover_flip(painter, rect, radius, progress, paint_face):
    """Paint one unmirrored face; paint_face receives whether it is outgoing."""
    if progress >= 1.:
        paint_face(False)
        return
    eased = progress * progress * (3. - 2. * progress)
    angle = math.pi * (eased if eased < .5 else eased - 1.)
    width = abs(math.cos(angle))
    if width < .012:
        return
    taper = .32 * abs(math.sin(angle))
    perspective = {"left_height": 1. - (taper if angle < 0. else 0.),
                   "right_height": 1. - (taper if angle >= 0. else 0.)}
    painter.save()
    painter.translate(rect.center())
    painter.scale(width, 1.)
    painter.translate(-rect.center())
    painter.setWorldTransform(album_cover_projection(rect, perspective), combine=True)
    painter.setRenderHint(painter.RenderHint.SmoothPixmapTransform)
    paint_face(eased < .5)
    shade = QPainterPath()
    shade.addRoundedRect(rect, radius, radius)
    painter.fillPath(shade, QColor(0, 0, 0, round(65 * (1. - width))))
    painter.restore()


def paint_cover_transition_light(painter, rect, progress, primary, secondary):
    """A soft palette reflection travels along and below the cover's lower edge."""
    if not 0. < progress < 1.:
        return
    envelope = math.sin(math.pi * progress) ** 2
    color = QColor.fromRgbF(primary.redF() * .62 + secondary.redF() * .38,
                           primary.greenF() * .62 + secondary.greenF() * .38,
                           primary.blueF() * .62 + secondary.blueF() * .38)
    center = QPointF(rect.left() + rect.width() * (.12 + .76 * progress), rect.bottom())
    painter.save()
    painter.translate(center)
    painter.scale(max(1., rect.width() * .60), max(1., rect.height() * .18))
    gradient = QRadialGradient(QPointF(), 1.)
    for stop, alpha in ((0., 165), (.32, 95), (.7, 24), (1., 0)):
        tint = QColor(color)
        tint.setAlpha(round(alpha * envelope))
        gradient.setColorAt(stop, tint)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawEllipse(QRectF(-1., -1., 2., 2.))
    painter.restore()
