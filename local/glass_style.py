"""Confirmed Clear control tokens; consumers opt in explicitly.

Logical pixels at the existing material level of 50. These tokens do not
change the shared widget, island or popup material implementations.
"""
from dataclasses import dataclass, replace
import os


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

    def at_level(self, level: int, *, light: bool = False) -> "GlassStyle":
        from local.theme_tokens import glass_tint_amount, solid_weight
        from local.ui_theme import theme_transition

        transition = theme_transition()
        if transition.active:
            amount = transition.current().glass_amount
            light = not transition.current().dark
        else:
            amount = glass_tint_amount(level, light=light)
        weight = solid_weight(amount, light=light)
        # Clear keeps the style's own tint. Tinted replaces the scene with a fill.
        tint = min(1.0, self.tint_alpha * (1.0 - weight) + weight * (0.98 if light else 0.94))
        return replace(
            self,
            blur=self.blur * (0.55 + 0.90 * weight),
            veil=self.veil * (0.35 + 1.40 * weight),
            tint_alpha=tint,
        )


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

# Optional moving-control material refresh interval; zero preserves per-frame refresh.
try:
    GLASS_MOVING_REFRESH_SECONDS = max(0., float(os.environ.get("GLUSPOT_GLASS_MOVING_REFRESH", "0")))
except ValueError:
    GLASS_MOVING_REFRESH_SECONDS = 0.
