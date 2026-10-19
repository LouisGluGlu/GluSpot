"""Display-only vocal gaps derived from retained, genuinely timed lyrics.

Provider rows, source units and persisted lyric text are never modified here.
The existing three-dot row is reused for both the intro and middle gaps.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from api.lyric_timeline import genuinely_timed_unit_row
from local.music_lyric_presentation import (
    DisplayLyricLayers,
    INTRO_COUNTDOWN_TEXT,
    analyze_duet_lyric_rows,
    present_lyric_text,
    split_display_layers,
)


INTERLUDE_MIN_GAP_SECONDS = 3.0
_TIME_EPSILON = 0.0005
# Millisecond source boundaries may subtract to 3.0000000000000004. Only
# suppress floating-point noise here; a real 3.001-second gap still qualifies.
_GAP_COMPARISON_EPSILON = 1e-9


@dataclass(frozen=True)
class LyricGap:
    kind: str
    start_seconds: float
    end_seconds: float
    progress: float


def _normalized(text: str) -> str:
    return " ".join(str(text or "").split())


def _first_subrow(text: str) -> str:
    parts = str(text or "").replace("\u2028", "\n").splitlines()
    return _normalized(parts[0]) if parts else ""


def _measured_end(primary: str, units: Sequence[object], kind: str, singer_names: Sequence[str] | str = ()) -> float | None:
    """Require aligned source words and explicit, valid ends for every unit."""
    if kind != "unit" or not genuinely_timed_unit_row(units):
        return None
    source_text = _normalized("".join(str(getattr(unit, "text", "")) for unit in units))
    if source_text != primary:
        voice = analyze_duet_lyric_rows(((0., primary),), singer_names=singer_names).rows[0]
        if voice.singer is None or source_text != _normalized(voice.body_text):
            return None
    ends: list[float] = []
    for unit in units:
        if not str(getattr(unit, "text", "")).strip():
            continue
        try:
            start = float(getattr(unit, "start_seconds"))
            end = float(getattr(unit, "end_seconds"))
        except (TypeError, ValueError, AttributeError, OverflowError):
            return None
        if not math.isfinite(start) or not math.isfinite(end) or end < start:
            return None
        ends.append(end)
    return max(ends) if ends else None


def add_interlude_rows(
    displayed_rows: Sequence[tuple[float, str]],
    source_rows: Sequence[tuple[float, str]],
    source_units: Sequence[Sequence[object]],
    source_kinds: Sequence[str],
    *,
    show_translation: bool = True,
    singer_names: Sequence[str] | str = (),
) -> list[tuple[float, str]]:
    """Insert reliable middle gaps into an already filtered display timeline.

    Same-time duet rows and later backing vocals attached to a preceding row
    must all have measured ends. Earlier overlapping sustained vocals retain
    their real maximum end. An unverified vocal prevents subsequent gaps:
    without a measured end a later row cannot prove the earlier voice stopped.
    No tail gap or middle gap for ordinary/synthetic LRC is inferred.

    Existing middle markers are rebuilt, making repeated presentation passes
    idempotent. A leading zero-time intro marker is retained unchanged.
    """
    original = [(float(start), str(text)) for start, text in displayed_rows]
    intro = bool(original and original[0] == (0.0, INTRO_COUNTDOWN_TEXT))
    rows = [(start, text) for start, text in original if text != INTRO_COUNTDOWN_TEXT]
    if not rows:
        return []
    starts = sorted(set(start for start, _text in rows))
    if any(not math.isfinite(start) for start in starts):
        return original
    by_start = {start: [] for start in starts}
    for start, text in rows:
        by_start[start].append(text)

    # A source row only contributes when its primary survived the final
    # presentation. Same-time translations therefore cannot become vocals,
    # and removed production rows do not extend the retained timeline.
    sources: list[tuple[float, str, DisplayLyricLayers, float | None]] = []
    for index, (raw_start, raw_text) in enumerate(source_rows):
        try:
            start = float(raw_start)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(start):
            continue
        prepared = present_lyric_text(
            raw_text, show_translation=show_translation, clean_intro=True,
            clean_credits=False,
        )
        if not prepared or prepared == INTRO_COUNTDOWN_TEXT:
            continue
        primary = _first_subrow(prepared)
        layers = split_display_layers(prepared, source_text=raw_text,
                                      show_translation=show_translation)
        units = source_units[index] if index < len(source_units) else ()
        kind = source_kinds[index] if index < len(source_kinds) else "line"
        end = _measured_end(primary, units, kind, singer_names)
        first_layers = split_display_layers(
            prepared.replace("\u2028", "\n").splitlines()[0],
            show_translation=show_translation,
        )
        # Additional sung subrows/backing vocals inside one source row do
        # not acquire the primary's measured end. Their timing is unknown
        # unless it belongs to the same primary's aligned unit sequence.
        if (
            _normalized(layers.original_primary_text) != _normalized(first_layers.original_primary_text)
            or _normalized(layers.parenthetical_text or "") != _normalized(first_layers.parenthetical_text or "")
        ):
            end = None
        sources.append((start, primary, layers, end))

    group_ends: dict[float, list[float | None]] = {start: [] for start in starts}
    matched_sources: set[int] = set()
    for start, texts in by_start.items():
        for text in texts:
            primary = _first_subrow(text)
            matches = [index for index, (source_start, source_primary, _layers, _end)
                       in enumerate(sources)
                       if abs(source_start - start) <= _TIME_EPSILON
                       and source_primary == primary]
            matched_sources.update(matches)
            # A duplicated primary can carry additional backing vocals in
            # another source record. Without every retained contribution's
            # end, a more precise duplicate cannot prove silence for both.
            measured = [sources[index][3] for index in matches]
            group_ends[start].append(
                max(measured) if measured and all(end is not None for end in measured) else None,
            )

    # The presenter attaches standalone parentheses to the preceding main
    # sentence. Retain their independent source timing even though that row
    # no longer has a separate display timestamp.
    for index, (start, _primary, layers, end) in enumerate(sources):
        if index in matched_sources or layers.primary_text or not layers.parenthetical_text:
            continue
        previous = next((candidate for candidate in reversed(starts)
                         if candidate <= start + _TIME_EPSILON), None)
        if previous is None:
            continue
        annotation = _normalized(layers.parenthetical_text)
        if any(annotation in _normalized(split_display_layers(
            text, show_translation=show_translation,
        ).parenthetical_text or "") for text in by_start[previous]):
            group_ends[previous].append(end)

    markers: dict[float, float] = {}
    sustained_end = -math.inf
    unknown_vocal_end = False
    for index, start in enumerate(starts[:-1]):
        ends = group_ends[start]
        unknown_vocal_end = unknown_vocal_end or not ends or any(end is None for end in ends)
        known = [end for end in ends if end is not None]
        if known:
            sustained_end = max(sustained_end, *known)
        following = starts[index + 1]
        if not unknown_vocal_end:
            ending = max(start, sustained_end)
            if following - ending > INTERLUDE_MIN_GAP_SECONDS + _GAP_COMPARISON_EPSILON:
                markers[start] = ending

    result: list[tuple[float, str]] = [(0.0, INTRO_COUNTDOWN_TEXT)] if intro else []
    for index, (start, text) in enumerate(rows):
        result.append((start, text))
        if start in markers and (index + 1 == len(rows) or rows[index + 1][0] != start):
            result.append((markers[start], INTRO_COUNTDOWN_TEXT))
    return result


def gap_at_position(
    displayed_rows: Sequence[tuple[float, str]], position_seconds: float,
) -> LyricGap | None:
    """Resolve intro/interlude progress directly from the source playback clock.

    No look-ahead is applied: a real lyric's exact start ends its preceding
    gap. Pause, seeking and background playback require no timer state here.
    """
    try:
        position = float(position_seconds)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(position):
        return None
    for index, (raw_start, text) in enumerate(displayed_rows):
        if text != INTRO_COUNTDOWN_TEXT or index + 1 >= len(displayed_rows):
            continue
        try:
            start = float(raw_start)
            end = float(displayed_rows[index + 1][0])
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(start) and math.isfinite(end) and start <= position < end:
            return LyricGap(
                "intro" if index == 0 and start == 0.0 else "interlude",
                start, end, (position - start) / (end - start),
            )
    return None
