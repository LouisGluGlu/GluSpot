"""Five travelling speech rings, with measured PCM deformation and one accent."""
from __future__ import annotations

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen


DJ_RING_TRAVEL_SECONDS = .85
DJ_RING_HOLD_SECONDS = .5
DJ_RING_CYCLE_SECONDS = DJ_RING_TRAVEL_SECONDS + DJ_RING_HOLD_SECONDS


def personal_dj_ring_poses(elapsed: float):
    """Keep ring identity through right-to-left travel and the half-second hold."""
    elapsed = max(0.0, float(elapsed))
    phase = min(1.0, (elapsed % DJ_RING_CYCLE_SECONDS) / DJ_RING_TRAVEL_SECONDS)
    travel = 1.0 - (1.0 - phase) ** 3
    poses = []
    for identity in range(5):
        slot = ((identity + .5 - 5.0 * travel) % 5.0) - .5
        edge = min((slot + .5) / .3, (4.5 - slot) / .3, 1.0)
        opacity = max(.0, edge) if 0.0 < phase < 1.0 else 1.0
        x = .14 + slot * .18
        height = .38 + .56 * max(0.0, 1.0 - abs(slot - 2.0) / 2.0)
        poses.append((identity, x, height, opacity))
    return tuple(sorted(poses, key=lambda item: item[1]))


def paint_personal_dj_icon(
    painter: QPainter, rect: QRectF, color: QColor, *, bands=None,
    elapsed: float = 0.0, neutral: QColor | None = None,
):
    """Paint hollow overlapping outlines; only identity four carries the accent."""
    values = (bands.low, bands.mid, bands.high) if bands is not None else (0.0, 0.0, 0.0)
    levels = tuple(max(0.0, min(1.0, float(level) * 4.0)) for level in values)
    neutral = QColor(neutral or QColor(245, 246, 248))
    painter.save()
    parent_opacity = painter.opacity()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for identity, x, height_fraction, opacity in personal_dj_ring_poses(elapsed):
        energy = levels[(0, 1, 2, 1, 0)[identity]]
        height = rect.height() * min(1.0, height_fraction + energy * .10)
        width = rect.width() * (.15 + height_fraction * .13 + energy * .055)
        ellipse = QRectF(rect.left() + rect.width() * x - width / 2,
                         rect.center().y() - height / 2, width, height)
        painter.setOpacity(parent_opacity * opacity)
        ink = QColor(color if identity == 4 else neutral)
        pen_width = max(.85, rect.width() * .037)
        if identity == 4:
            for multiplier, alpha in ((3.8, 24), (2.2, 48)):
                glow = QColor(ink)
                glow.setAlpha(round(alpha * (.35 + .65 * energy)))
                painter.setPen(QPen(glow, pen_width * multiplier))
                painter.drawEllipse(ellipse)
        ink.setAlpha(round(200 + 55 * energy))
        painter.setPen(QPen(ink, pen_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawEllipse(ellipse)
    painter.restore()


def personal_dj_status_color(state: str) -> QColor | None:
    value = {"loading": "#248eea", "ready": "#16c777", "failed": "#ec373f"}.get(state)
    return QColor(value) if value else None


def personal_dj_status_opacity(state: str, elapsed: float) -> float:
    if state in {"loading", "ready"}:
        return .5 - .5 * math.cos(max(0.0, elapsed) * math.tau / 1.7)
    return 1.0
