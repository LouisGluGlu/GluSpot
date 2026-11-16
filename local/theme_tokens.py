"""Shared light/dark materials for every GluSpot surface.

Other work should read these tokens instead of picking its own dark colours.
``glass_amount`` is 0 at the clear end and 1 at the tinted end. Light mode
uses a curve that sits closer to solid at the saved default of 50.
"""
from __future__ import annotations

from dataclasses import dataclass
import colorsys

from local.settings_store import normalize_music_glass_level


# Opaque page bases. Light is white with black ink. Dark is black with white ink.
LIGHT_BACKGROUND = (255, 255, 255)
DARK_BACKGROUND = (0, 0, 0)
LIGHT_SOLID = (255, 255, 255)
DARK_SOLID = (18, 18, 18)
LIGHT_TEXT = (0, 0, 0)
DARK_TEXT = (255, 255, 255)
LIGHT_MUTED = (72, 76, 84)
DARK_MUTED = (198, 200, 206)
LIGHT_POPUP = (255, 255, 255)
DARK_POPUP = (36, 38, 43)  # #24262b, the existing music tooltip face
LIGHT_BORDER = (210, 210, 216)
DARK_BORDER = (85, 87, 94)
THEME_TRANSITION_MS = 360


def solid_weight(amount: float, *, light: bool = False) -> float:
    """How much of the glass is a solid plate.

    Light mode is option B: the saved default of 50 is already a solid plate.
    Dark mode stays glassy until the slider approaches 100, so existing dark
    icon tests still see the scene.
    """
    amount = max(0.0, min(1.0, amount))
    if light:
        return 1.0 - (1.0 - amount) ** 2.2
    return amount ** 8.0


def glass_tint_amount(level: int, *, light: bool) -> float:
    """Map the 0–100 slider onto clear (0) … tinted (1).

    Light mode is option B: the default of 50 is already mostly solid, while
    0 stays clear and 100 is a full fill. Dark mode stays linear.
    """
    unit = normalize_music_glass_level(level) / 100.0
    if light:
        return 1.0 - (1.0 - unit) ** 1.85
    return unit


def glass_face_opacity(level: int, *, light: bool) -> float:
    """Button-face strength. Clear stays translucent; tinted is opaque."""
    amount = glass_tint_amount(level, light=light)
    return 0.18 + 0.82 * amount


def glass_scene_opacity(level: int, *, light: bool) -> float:
    """How much of the sampled background remains inside the glass."""
    amount = glass_tint_amount(level, light=light)
    return 0.90 * (1.0 - solid_weight(amount, light=light) ** 0.45)


def glass_edge_boost(level: int, *, light: bool) -> float:
    """Clear glass keeps a bright hairline; tinted glass quiets the edge."""
    amount = glass_tint_amount(level, light=light)
    return 1.25 - 0.85 * amount


def _lerp(start: float, end: float, amount: float) -> float:
    return start + (end - start) * amount


def _lerp_channel(start: int, end: int, amount: float) -> int:
    return max(0, min(255, round(_lerp(start, end, amount))))


def _mix(base: tuple[int, int, int], accent: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    return tuple(_lerp_channel(channel, other, amount) for channel, other in zip(base, accent))


def themed_base(dark: bool, accent: tuple[int, int, int] | None = None) -> tuple[int, int, int]:
    """Opaque light or dark base. A cover may tint it without flipping the shade."""
    base = DARK_BACKGROUND if dark else LIGHT_BACKGROUND
    if accent is None:
        return base
    mixed = _mix(base, accent, 0.10)
    if dark:
        return tuple(min(42, max(12, channel)) for channel in mixed)
    return tuple(max(246, min(252, channel)) for channel in mixed)


def glass_fill_rgba(dark: bool, level: int, accent: tuple[int, int, int] | None = None) -> tuple[int, int, int, int]:
    amount = solid_weight(glass_tint_amount(level, light=not dark), light=not dark)
    if dark:
        clear = (255, 255, 255, 34)
        solid = (*themed_base(True, accent), 255)
        # The tinted fill sits a step above the page so the edge still reads.
        solid = (*_mix(DARK_SOLID, solid[:3], 0.35), 255)
    else:
        clear = (255, 255, 255, 48)
        # Leave a little headroom under pure white so a hover wash can still lift.
        solid = (*tuple(min(250, channel) for channel in themed_base(False, accent)), 255)
    return tuple(_lerp_channel(channel, other, amount) for channel, other in zip(clear, solid))


def library_surface_rgb(dark: bool) -> dict[str, tuple[int, int, int]]:
    """Distinct library planes. Sea level uses the dark set."""
    if dark:
        return {
            "page": (0, 0, 0),
            "sidebar": (24, 24, 26),
            "content": (8, 8, 10),
            "header": (36, 36, 40),
            "ink": (255, 255, 255),
            "muted": (198, 198, 204),
        }
    return {
        "page": (255, 255, 255),
        "sidebar": (236, 236, 240),
        "content": (255, 255, 255),
        "header": (226, 226, 232),
        "ink": (0, 0, 0),
        "muted": (72, 72, 78),
    }


def control_plate_rgba(dark: bool, opacity: float) -> tuple[int, int, int, int]:
    """Flat control fill. Opacity is 0..1 and is stored separately per mode."""
    amount = max(0.0, min(1.0, float(opacity)))
    alpha = max(0, min(255, round(amount * 255)))
    if dark:
        return (0, 0, 0, alpha)
    return (255, 255, 255, alpha)


def glass_edge_rgba(dark: bool, level: int) -> tuple[int, int, int, int]:
    amount = glass_tint_amount(level, light=not dark)
    if dark:
        clear = (255, 255, 255, 214)
        solid = (255, 255, 255, 42)
    else:
        # A white hairline disappears on a white page.
        clear = (28, 28, 32, 176)
        solid = (28, 28, 32, 72)
    return tuple(_lerp_channel(channel, other, amount) for channel, other in zip(clear, solid))


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    channels = []
    for value in rgb[:3]:
        component = value / 255.0
        channels.append(component / 12.92 if component <= 0.04045 else ((component + 0.055) / 1.055) ** 2.4)
    return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722


def backdrop_needs_dark_ink(rgb: tuple[int, ...]) -> bool:
    """Near-white covers and light pages cannot carry white or pale gray type."""
    return relative_luminance(rgb[:3]) >= 0.40


def readable_ink(rgb: tuple[int, ...], *, secondary: bool = False) -> tuple[int, int, int]:
    """Primary ink stays at least 4.5:1. Secondary ink stays at least 3:1."""
    if backdrop_needs_dark_ink(rgb):
        return LIGHT_MUTED if secondary else LIGHT_TEXT
    return DARK_MUTED if secondary else DARK_TEXT


def contrast_ratio(foreground: tuple[int, ...], background: tuple[int, ...]) -> float:
    lighter = relative_luminance(foreground[:3])
    darker = relative_luminance(background[:3])
    if lighter < darker:
        lighter, darker = darker, lighter
    return (lighter + 0.05) / (darker + 0.05)


def composite(fill: tuple[int, int, int, int], base: tuple[int, int, int]) -> tuple[int, int, int]:
    alpha = fill[3] / 255.0
    return tuple(round(base[index] * (1.0 - alpha) + fill[index] * alpha) for index in range(3))


def ease_inout_cubic(progress: float) -> float:
    progress = max(0.0, min(1.0, float(progress)))
    if progress < 0.5:
        return 4.0 * progress * progress * progress
    return 1.0 - ((-2.0 * progress + 2.0) ** 3) / 2.0


def hex_rgb(rgb: tuple[int, ...]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb[:3])


@dataclass(frozen=True)
class ThemeTokens:
    shade: float
    glass_level: int
    glass_amount: float
    background: tuple[int, int, int]
    text: tuple[int, int, int]
    muted: tuple[int, int, int]
    icon: tuple[int, int, int]
    glass_fill: tuple[int, int, int, int]
    glass_edge: tuple[int, int, int, int]
    popup: tuple[int, int, int]
    popup_text: tuple[int, int, int]
    border: tuple[int, int, int]
    panel: tuple[int, int, int]

    @property
    def dark(self) -> bool:
        return self.shade >= 0.5


def resolve_tokens(
    dark: bool,
    level: int = 50,
    accent: tuple[int, int, int] | None = None,
) -> ThemeTokens:
    level = normalize_music_glass_level(level)
    light = not dark
    background = themed_base(dark, accent)
    text = DARK_TEXT if dark else LIGHT_TEXT
    muted = DARK_MUTED if dark else LIGHT_MUTED
    return ThemeTokens(
        shade=1.0 if dark else 0.0,
        glass_level=level,
        glass_amount=glass_tint_amount(level, light=light),
        background=background,
        text=text,
        muted=muted,
        icon=text,
        glass_fill=glass_fill_rgba(dark, level, accent),
        glass_edge=glass_edge_rgba(dark, level),
        popup=DARK_POPUP if dark else LIGHT_POPUP,
        popup_text=DARK_TEXT if dark else LIGHT_TEXT,
        border=DARK_BORDER if dark else LIGHT_BORDER,
        panel=(28, 30, 36) if dark else (255, 255, 255),
    )


def _lerp_rgb(start: tuple[int, ...], end: tuple[int, ...], amount: float) -> tuple[int, ...]:
    return tuple(_lerp_channel(a, b, amount) for a, b in zip(start, end))


def interpolate_tokens(start: ThemeTokens, end: ThemeTokens, progress: float) -> ThemeTokens:
    amount = ease_inout_cubic(progress)
    return ThemeTokens(
        shade=_lerp(start.shade, end.shade, amount),
        glass_level=_lerp_channel(start.glass_level, end.glass_level, amount),
        glass_amount=_lerp(start.glass_amount, end.glass_amount, amount),
        background=_lerp_rgb(start.background, end.background, amount),
        text=_lerp_rgb(start.text, end.text, amount),
        muted=_lerp_rgb(start.muted, end.muted, amount),
        icon=_lerp_rgb(start.icon, end.icon, amount),
        glass_fill=_lerp_rgb(start.glass_fill, end.glass_fill, amount),
        glass_edge=_lerp_rgb(start.glass_edge, end.glass_edge, amount),
        popup=_lerp_rgb(start.popup, end.popup, amount),
        popup_text=_lerp_rgb(start.popup_text, end.popup_text, amount),
        border=_lerp_rgb(start.border, end.border, amount),
        panel=_lerp_rgb(start.panel, end.panel, amount),
    )


def tokens_match(left: ThemeTokens, right: ThemeTokens) -> bool:
    return (
        abs(left.shade - right.shade) < 0.001
        and left.glass_level == right.glass_level
        and left.background == right.background
        and left.glass_fill == right.glass_fill
        and left.text == right.text
    )


def chrome_qss(tokens: ThemeTokens) -> dict[str, str]:
    text = hex_rgb(tokens.text)
    muted = hex_rgb(tokens.muted)
    popup = hex_rgb(tokens.popup)
    popup_text = hex_rgb(tokens.popup_text)
    border = hex_rgb(tokens.border)
    return {
        "text": text,
        "muted": muted,
        "popup": popup,
        "popup_text": popup_text,
        "border": border,
        "tooltip": (
            f"QToolTip {{ color: {popup_text}; background-color: {popup}; "
            f"border: 1px solid {border}; border-radius: 7px; padding: 6px 9px; }}"
        ),
        "label": f"color: {text}; background: transparent;",
        "button": f"QPushButton {{ color: {text}; background: transparent; border: none; }}",
        "muted_label": f"color: {muted}; background: transparent;",
    }


def accent_from_qcolor(color) -> tuple[int, int, int] | None:
    if color is None:
        return None
    try:
        if not color.isValid():
            return None
        red, green, blue, _alpha = color.getRgb()
    except Exception:
        return None
    # A near-grey cover should not invent a tint.
    hue, saturation, value = colorsys.rgb_to_hsv(red / 255.0, green / 255.0, blue / 255.0)
    if saturation < 0.08 or value < 0.06:
        return None
    del hue
    return red, green, blue
