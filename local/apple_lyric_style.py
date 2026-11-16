"""Apple lyric display policy. Source clocks and models remain unchanged.

The eight sample knob names deliberately match _demos/apple_lyrics/timeline.py.
Replace one AppleKnobs value to try another pack; drawing has no second pack.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from PySide6.QtCore import QTextBoundaryFinder

from local.lyric_unit_timing import DisplayRowTiming, DisplayTimedLyricUnit, _aligned_offsets


@dataclass(frozen=True)
class AppleKnobs:
    block_gap_ratio: float = 1.75
    translation_gap_ratio: float = 0.25
    other_scale: float = 0.88
    blur_px: float = 2.2
    unread_alpha: float = 0.55
    feather_chars: float = 0.75
    # Kept so the sample knob pack still has this name. Product scrolling does
    # not time itself with it; the wide/narrow spring has no fixed duration.
    scroll_ms: float = 350.0
    lead_ms: float = 350.0
    # Sample relationships, kept here as well as its exposed slider fields.
    read_alpha: float = 1.0
    reference_unread: float = 0.55
    adjacent_alpha_ratio: float = 0.25
    far_alpha_ratio: float = 0.10
    translation_alpha_ratio: float = 0.45
    translation_size_ratio: float = 0.50
    parenthetical_size_ratio: float = 0.84
    blur_distance_growth: float = 0.85
    blur_distance_limit: float = 2.5
    far_distance: float = 3.0
    fade_start: float = 2.6
    fade_end: float = 4.0
    wipe_gap_power: float = 0.45
    anchor_fraction: float = 0.43

    def lead_seconds(self) -> float:
        return max(0.0, self.lead_ms / 1000.0)

    def scroll_seconds(self) -> float:
        return max(0.0, self.scroll_ms / 1000.0)

    def _alpha(self, reference: float) -> float:
        return clamp(self.unread_alpha * reference / max(1e-9, self.reference_unread))

    def adjacent_alpha(self) -> float:
        return self._alpha(self.adjacent_alpha_ratio)

    def far_alpha(self) -> float:
        return self._alpha(self.far_alpha_ratio)

    def translation_alpha(self) -> float:
        return self._alpha(self.translation_alpha_ratio)


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def smoothstep(value: float) -> float:
    value = clamp(value)
    return value * value * (3.0 - 2.0 * value)


def first_unit_start(start: float, units=(), kind: str = "line") -> float:
    if kind == "unit" and units:
        onset = float(units[0].start_seconds)
        if math.isfinite(onset):
            return onset
    return float(start)


def scroll_spring_phase(elapsed: float) -> float:
    """How far a line change has traveled since its lead instant.

    The previous curve was Hermite smoothstep, ``t*t*(3-2*t)``, stretched
    across ``scroll_ms`` (350 ms). That is a fixed-duration ease. Scrolling
    now uses the same critically damped spring as wide/narrow layout changes:
    mass 1, stiffness 220, damping ratio 1. It starts from rest and has no
    fixed duration. ``elapsed`` is seconds after ``next onset - lead``.
    """
    if elapsed <= 0.0 or not math.isfinite(elapsed):
        return 0.0
    from local.spring_motion import WIDE_NARROW_SPRING, Spring
    spring = Spring(WIDE_NARROW_SPRING, 0.0, 0.0, 1.0)
    spring.step(elapsed)
    return clamp(spring.value)


def displayed_focus(starts: tuple[float, ...], position: float, knobs: AppleKnobs) -> float:
    """Sheet clock: next onset minus lead, then the wide/narrow spring.

    The lead instant is unchanged. ``scroll_ms`` does not scale this spring.
    """
    target = 0
    for index, start in enumerate(starts):
        if position >= start - knobs.lead_seconds():
            target = index
    if target == 0:
        return 0.0
    elapsed = position - (starts[target] - knobs.lead_seconds())
    return target - 1 + scroll_spring_phase(elapsed)


@dataclass(frozen=True)
class LineAppearance:
    scale: float
    blur: float
    read_alpha: float
    unread_alpha: float
    translation_alpha: float


def line_appearance(distance: float, knobs: AppleKnobs) -> LineAppearance:
    amount = abs(distance)
    blend = smoothstep(amount)
    scale = 1.0 + (knobs.other_scale - 1.0) * blend
    blur = knobs.blur_px * (amount if amount < 1.0 else
        1.0 + knobs.blur_distance_growth * min(knobs.blur_distance_limit, amount - 1.0))
    adjacent, far = knobs.adjacent_alpha(), knobs.far_alpha()
    if amount >= 1.0:
        level = adjacent + (far - adjacent) * smoothstep(
            (amount - 1.0) / max(1e-9, knobs.far_distance - 1.0))
        read = unread = level
    else:
        read = knobs.read_alpha + (adjacent - knobs.read_alpha) * blend
        gap = (knobs.read_alpha - knobs.unread_alpha) * (1.0 - blend) ** knobs.wipe_gap_power
        unread = max(0.0, read - gap)
    fade = 1.0 - clamp((amount - knobs.fade_start) / max(1e-9, knobs.fade_end - knobs.fade_start))
    return LineAppearance(scale, max(0.0, blur), clamp(read * fade), clamp(unread * fade),
                         clamp(unread * fade * knobs.translation_alpha() / max(1e-9, knobs.unread_alpha)))


def character_spans(text: str) -> tuple[str, ...]:
    """Unicode graphemes, including Latin characters, spaces and punctuation."""
    finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, text)
    encoded = text.encode("utf-16-le")
    result, start = [], 0
    while (end := finder.toNextBoundary()) >= 0:
        result.append(encoded[start * 2:end * 2].decode("utf-16-le"))
        start = end
    return tuple(result)


def row_timing(text: str, units=(), kind: str = "line", *, start: float,
               end: float | None) -> DisplayRowTiming:
    """Source intervals or an explicitly estimated equal-character fallback.

    The caller supplies the next line onset (or known track duration). A
    missing unit end is display-only evidence, never a sustained-note cue.
    """
    units = tuple(units)
    valid = kind == "unit" and bool(units) and _aligned_offsets(text, units) is not None
    if valid:
        previous = -math.inf
        for unit in units:
            onset, ending = unit.start_seconds, unit.end_seconds
            if (not math.isfinite(onset) or onset < previous or
                    ending is not None and (not math.isfinite(ending) or ending < onset)):
                valid = False
                break
            previous = onset
    if valid:
        display = []
        for index, unit in enumerate(units):
            missing = unit.end_seconds is None
            ending = (units[index + 1].start_seconds if index + 1 < len(units) else end) if missing else unit.end_seconds
            if ending is not None and ending < unit.start_seconds:
                ending = None
            display.append(DisplayTimedLyricUnit(unit.start_seconds, ending, unit.text,
                missing or bool(getattr(unit, "estimated", False)), "missing-end" if missing else "source"))
        return DisplayRowTiming(text, "word", units, tuple(display), float(start), end)
    if kind == "synthetic" or not text or end is None or not math.isfinite(end) or end <= start:
        return DisplayRowTiming(text, "line", units, (), float(start), None)
    parts = character_spans(text)
    step = (end - start) / len(parts)
    display = tuple(DisplayTimedLyricUnit(start + index * step, start + (index + 1) * step,
        part, True, "line") for index, part in enumerate(parts))
    return DisplayRowTiming(text, "estimated", units, display, float(start), end)


def unit_progress(unit: DisplayTimedLyricUnit, position: float) -> float:
    """Audio time only; deliberately accepts no visual lead/config argument."""
    if position <= unit.start_seconds or unit.end_seconds is None:
        return 0.0
    if unit.end_seconds <= unit.start_seconds:
        return 1.0
    return clamp((position - unit.start_seconds) / (unit.end_seconds - unit.start_seconds))


def blur_layers(radius: float, knobs: AppleKnobs) -> tuple[tuple[float, float], ...]:
    """Crossfade a small fixed atlas, so scrolling never requests fresh blurs."""
    radii = sorted(set((0.0, knobs.blur_px, knobs.blur_px * (1.0 + knobs.blur_distance_growth),
                       knobs.blur_px * (1.0 + knobs.blur_distance_growth * knobs.blur_distance_limit))))
    if radius <= 0.0 or radii[-1] <= 0.0:
        return ((0.0, 1.0),)
    for left, right in zip(radii, radii[1:]):
        if radius <= right:
            amount = (radius - left) / (right - left)
            return tuple((value, weight) for value, weight in ((left, 1.0 - amount), (right, amount)) if weight > 0.0)
    return ((radii[-1], 1.0),)
