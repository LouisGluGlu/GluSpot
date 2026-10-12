"""Five hollow speech rings with measured PCM deformation and positional hue."""
from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPen
from local.music_surface_palette import relative_luminance


DJ_RING_TRAVEL_SECONDS = 2.0
DJ_RING_HOLD_SECONDS = .5
DJ_RING_CYCLE_SECONDS = DJ_RING_TRAVEL_SECONDS + DJ_RING_HOLD_SECONDS


def personal_dj_travel_progress(elapsed: float) -> float:
    """A short smooth attack followed by a long release, then a quiet hold."""
    phase = min(1.0, (max(0.0, float(elapsed)) % DJ_RING_CYCLE_SECONDS) / DJ_RING_TRAVEL_SECONDS)
    # Integral of a positive pulse velocity, 56*p*(1-p)^6. Both ends
    # have zero velocity; no frame accumulation or loop-boundary snap.
    return 1.0 - (1.0 - phase) ** 7 * (1.0 + 7.0 * phase)


def personal_dj_ring_poses(elapsed: float):
    """Keep ring identity through right-to-left travel and the half-second hold."""
    elapsed = max(0.0, float(elapsed))
    phase = min(1.0, (elapsed % DJ_RING_CYCLE_SECONDS) / DJ_RING_TRAVEL_SECONDS)
    travel = personal_dj_travel_progress(elapsed)
    poses = []
    for identity in range(5):
        slot = ((identity + .5 - 5.0 * travel) % 5.0) - .5
        edge = min((slot + .5) / .3, (4.5 - slot) / .3, 1.0)
        fade = max(0.0, edge)
        opacity = fade * fade * (3.0 - 2.0 * fade) if 0.0 < phase < 1.0 else 1.0
        x = .14 + slot * .18
        depth = min(2.0, abs(slot - 2.0))
        height = .38 + .28 * (1.0 + math.cos(depth * math.pi / 2.0))
        poses.append((identity, x, height, opacity))
    return tuple(sorted(poses, key=lambda item: item[1]))


def paint_personal_dj_icon(
    painter: QPainter, rect: QRectF, color: QColor, *, bands=None,
    elapsed: float = 0.0, neutral: QColor | None = None,
    max_lightening: float = .60,
    backdrop: QColor | Callable[[QRectF], QColor] | None = None,
):
    """Keep page tones, or blend island side light into its actual backdrop."""
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
        # Only island callers supply their background. Their left rim keeps
        # the cover characteristic instead of gaining positional white tint.
        ink = (QColor(color) if backdrop is not None else
               personal_dj_ring_color(color, x, neutral=neutral, max_lightening=max_lightening))
        background = QColor(backdrop(ellipse) if callable(backdrop) else backdrop) if backdrop is not None else None
        if background is not None and not background.isValid():
            background = None
        pen_width = max(.85, rect.width() * .037)
        def side_light(alpha: int):
            gradient = QLinearGradient(QPointF(ellipse.left(), ellipse.center().y()),
                                       QPointF(ellipse.right(), ellipse.center().y()))
            if background is not None:
                for stop, blend, opacity_fraction in ((0.0, 0.0, 1.0), (.28, .12, .95),
                                                      (.58, .58, .68), (1.0, 1.0, .30)):
                    tone = QColor(*(round(a + (b - a) * blend)
                        for a, b in zip(ink.getRgb()[:3], background.getRgb()[:3])))
                    tone.setAlpha(round(alpha * opacity_fraction))
                    gradient.setColorAt(stop, tone)
            else:
                for stop, shade, opacity_fraction in ((0.0, 100, 1.0), (.3, 108, .95),
                                                      (.55, 155, .68), (1.0, 240, .30)):
                    tone = ink.darker(shade)
                    tone.setAlpha(round(alpha * opacity_fraction))
                    gradient.setColorAt(stop, tone)
            return QBrush(gradient)
        if identity == 4:
            for multiplier, alpha in ((3.8, 24), (2.2, 48)):
                painter.setPen(QPen(side_light(round(alpha * (.35 + .65 * energy))), pen_width * multiplier))
                painter.drawEllipse(ellipse)
        painter.setPen(QPen(side_light(round(230 + 25 * energy)), pen_width,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawEllipse(ellipse)
    painter.restore()


def personal_dj_ring_color(color: QColor, x: float, *, neutral: QColor | None = None,
                          max_lightening: float = .60) -> QColor:
    """Blend by live position, so a travelling ring never jumps between hues."""
    distance = min(1.0, abs(float(x) - .5) / .36)
    fraction = max(0.0, min(.60, float(max_lightening))) * distance
    # Disabled rings retain their existing neutral, non-glowing appearance.
    target = QColor(neutral or QColor(245, 246, 248))
    if target.lightness() < 80:
        return QColor(color)
    return QColor(round(color.red() + (255 - color.red()) * fraction),
                  round(color.green() + (255 - color.green()) * fraction),
                  round(color.blue() + (255 - color.blue()) * fraction), color.alpha())


def personal_dj_lightening_limit(color: QColor, backdrop: QColor) -> float:
    """Keep pale side rings from crossing a light/gray island's background."""
    alpha = min(color.alphaF(), 230. / 255.)
    backdrop_luminance = relative_luminance(backdrop)
    def composite_at(fraction):
        ring = QColor(*(round(a + (255 - a) * fraction) for a in color.getRgb()[:3]))
        return QColor(*(round(a * alpha + b * (1. - alpha))
            for a, b in zip(ring.getRgb()[:3], backdrop.getRgb()[:3])))
    def contrast(fraction):
        composite = composite_at(fraction)
        luminance = relative_luminance(composite)
        return (max(luminance, backdrop_luminance) + .05) / (min(luminance, backdrop_luminance) + .05)
    # If ink is already lighter than the background, moving toward white
    # cannot cross it. Preserve the established full-page/dark-island tones.
    if relative_luminance(color) >= backdrop_luminance:
        return .60
    threshold = min(2.30, contrast(0.0))
    # Search only up to the background crossing, never a brighter end point
    # that regained contrast after passing through an invisible middle tone.
    low, high = 0.0, .60
    for _ in range(14):
        mid = (low + high) / 2.
        composite = composite_at(mid)
        if relative_luminance(composite) >= backdrop_luminance or contrast(mid) < threshold:
            high = mid
        else:
            low = mid
    return low


def personal_dj_status_color(state: str) -> QColor | None:
    value = {"loading": "#248eea", "ready": "#16c777", "failed": "#ec373f"}.get(state)
    return QColor(value) if value else None


def personal_dj_status_opacity(state: str, elapsed: float) -> float:
    if state in {"loading", "ready"}:
        return .5 - .5 * math.cos(max(0.0, elapsed) * math.tau / 1.7)
    return 1.0
