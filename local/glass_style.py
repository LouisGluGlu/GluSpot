"""Confirmed Clear control tokens; consumers opt in explicitly.

Logical pixels at the existing material level of 50. These tokens do not
change the shared widget, island or popup material implementations.
"""
from dataclasses import dataclass, replace

from local.settings_store import normalize_music_glass_level


@dataclass(frozen=True)
class GlassStyle:
    blur: float = 1.2
    bezel: float = 9.
    lens: float = 6.
    chroma: float = .15
    veil: float = .05
    tint_alpha: float = 0.
    sat: float = 1.3
    rim: float = 1.
    glow: float = .45
    sheen: float = .05

    def at_level(self, level: int) -> "GlassStyle":
        amount = normalize_music_glass_level(level) / 50.
        return replace(self, blur=self.blur * amount, veil=self.veil * amount)


GLASS_CLEAR = GlassStyle()
GLASS_DJ = GlassStyle(veil=0., tint_alpha=.72, sat=1.)
GLASS_GLYPH = GlassStyle(bezel=3.2, lens=2.2, chroma=.12, veil=0.,
                         tint_alpha=.42, glow=.5, sheen=.06)
GLASS_SELECTED_ALPHA = .86
GLASS_SELECTED_INK = "#0b0c0e"
DJ_TINT = (112, 112, 112)
PRESS_HOLD_SECONDS = .3
PRESS_STIFFNESS = 420.
PRESS_DAMPING = 30.
PRESS_GROWTH = .14
CONTROL_COMPOSITE_DPR = 2.
