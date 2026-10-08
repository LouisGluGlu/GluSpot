"""Native presentation glyphs and bounded artwork hover motion."""
from __future__ import annotations

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from local.music_lyric_motion import MotionSimulationClock


PRESENTATION_GLYPH_STATES = {"fullscreen": (1.0, 1.0), "windowed": (1.0, 0.0), "island": (0.0, 0.0)}


def paint_presentation_glyph(
    painter: QPainter, rect: QRectF, mode: str, color: QColor,
    *, previous: str | tuple[float, float] | None = None, progress: float = 1.0,
) -> None:
    """Morph the shared screen/pill shapes to the requested destination."""
    modes = PRESENTATION_GLYPH_STATES
    destination = modes.get(mode, modes["island"])
    origin = previous if isinstance(previous, tuple) else modes.get(previous, destination)
    fraction = max(0.0, min(1.0, float(progress)))
    screen, filled = (a + (b-a)*fraction for a, b in zip(origin, destination))
    painter.save()
    painter.translate(rect.topLeft())
    painter.scale(rect.width()/24.0, rect.height()/24.0)
    ink = QColor(color)
    if screen > 0.001:
        outline = QColor(ink)
        outline.setAlphaF(ink.alphaF()*screen)
        fill = QColor(ink)
        fill.setAlphaF(ink.alphaF()*screen*filled)
        painter.setPen(QPen(outline, 2.0, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(5.5, 3.5, 16.5, 14.0), 2.0, 2.0)
    pill = QRectF(2.0-screen, 8.0+5.0*screen, 20.0-4.5*screen, 9.0-2.5*screen)
    level = 1.0-0.37*screen
    pill_ink = QColor(round(ink.red()*level), round(ink.green()*level),
                      round(ink.blue()*level), ink.alpha())
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(pill_ink)
    painter.drawRoundedRect(pill, pill.height()/2.0, pill.height()/2.0)
    painter.restore()


class ArtworkHoverMotion:
    """A retargetable spring advanced by the island timer, never by paint."""
    def __init__(self):
        self.clock = MotionSimulationClock()
        self.value = 0.0
        self.velocity = 0.0
        self.target = 0.0

    def clear(self, now: float) -> None:
        self.value = self.velocity = self.target = 0.0
        self.clock.reset(now)

    def advance(self, hovered: bool, now: float) -> bool:
        previous = self.value
        self.target = float(bool(hovered))
        dt = self.clock.advance(now)
        # Exact underdamped spring solution avoids refresh-rate dependence.
        omega, damping = 24.0, 0.64
        frequency = omega*math.sqrt(1.0-damping*damping)
        delta = self.value-self.target
        decay = math.exp(-damping*omega*dt)
        sine, cosine = math.sin(frequency*dt), math.cos(frequency*dt)
        coefficient = (self.velocity+damping*omega*delta)/frequency
        self.value = self.target+decay*(delta*cosine+coefficient*sine)
        self.velocity = decay*(self.velocity*cosine-(damping*omega*self.velocity+omega*omega*delta)/frequency*sine)
        if abs(self.value-self.target) < 0.0002 and abs(self.velocity) < 0.002:
            self.value, self.velocity = self.target, 0.0
        return abs(previous-self.value) > 1e-6

    @property
    def active(self) -> bool:
        return abs(self.value-self.target) > 0.0002 or abs(self.velocity) > 0.002

    @property
    def scale(self) -> float:
        return 1.0+0.085*max(-0.04, min(1.09, self.value))
