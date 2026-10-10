"""Pure timing helpers for genuinely timestamped lyric units.

The caller supplies the horizontal position of every Python character
boundary. This keeps source timestamps independent of a particular Qt font or
layout while allowing the same clock to drive large and island lyric views.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence
import unicodedata

from api.lyric_timeline import genuinely_timed_unit_row


def lyric_timing_quality_label(
    kinds: Sequence[str],
    unit_rows: Sequence[Sequence[object]],
    *,
    has_lyrics: bool = True,
) -> str:
    """Name source precision without suggesting that interpolation is real timing."""
    if not kinds:
        return "纯文本" if has_lyrics else "无歌词"
    unit_count = sum(
        kind == "unit" and index < len(unit_rows) and genuinely_timed_unit_row(unit_rows[index])
        for index, kind in enumerate(kinds)
    )
    if unit_count:
        return "真实逐字" if unit_count == len(kinds) else "部分逐字"
    return "行计时" if any(kind != "synthetic" for kind in kinds) else "整行显示"


def song_has_genuine_unit_timing(
    kinds: Sequence[str], unit_rows: Sequence[Sequence[object]],
) -> bool:
    """Choose a reading strategy once for the surviving lyric timeline.

    A partially timed song is still a reading song: untimed rows interpolate
    between their own row timestamps instead of switching paint mode mid-song.
    Callers pass only display-surviving rows, not removed intro/credit rows.
    """
    return any(
        kind == "unit" and index < len(unit_rows)
        and genuinely_timed_unit_row(unit_rows[index])
        for index, kind in enumerate(kinds)
    )


def estimated_row_read_end(start: float, next_start: float | None) -> float | None:
    """Leave a one-second unread next-row preview for long estimated lines.

    Rows lasting at most one second retain their complete source interval.
    This only applies to rows whose unit timing is estimated; real units must
    keep their measured ends unchanged.
    """
    if next_start is None:
        return None
    beginning, following = float(start), float(next_start)
    if not math.isfinite(beginning) or not math.isfinite(following):
        return None
    if following <= beginning:
        return following
    return following - 1.0 if following - beginning > 1.0 else following


def estimated_row_reading_progress(
    start: float, next_start: float | None, position: float,
) -> float | None:
    """Return a source-clock fraction that reaches white at the preview cue."""
    end = estimated_row_read_end(start, next_start)
    if end is None:
        return None
    beginning, now = float(start), float(position)
    if not math.isfinite(now):
        return 0.0
    return max(0.0, min(1.0, (now - beginning) / max(1e-9, end - beginning)))


def duet_estimated_read_start(
    source_start: float, peer_text: str, peer_units: Sequence[object],
    peer_body_offsets: Sequence[int] | None,
    *, peer_source_start: float | None = None,
) -> float:
    """Borrow a measured sung onset only between matching source row times.

    Used only for the voice lacking unit timing. The source timestamps and
    the other voice's measured units stay untouched. A visual duet pair can
    contain staggered voices; its layout alone cannot establish shared onset.
    """
    beginning = float(source_start)
    if peer_source_start is None:
        return beginning
    peer_beginning = float(peer_source_start)
    if not math.isfinite(peer_beginning) or abs(beginning - peer_beginning) > 0.0005:
        return beginning
    if not peer_body_offsets or not genuinely_timed_unit_row(peer_units):
        return beginning
    boundaries = _aligned_offsets(peer_text, peer_units)
    if boundaries is None:
        return beginning
    first_body = int(peer_body_offsets[0])
    for index, unit in enumerate(peer_units):
        if boundaries[index + 1] > first_body:
            onset = float(unit.start_seconds)
            return max(beginning, onset) if math.isfinite(onset) else beginning
    return beginning


@dataclass(frozen=True)
class TimedHighlightGroup:
    """A contiguous run of individually timed slow characters."""

    start_offset: int
    end_offset: int
    start_seconds: float
    end_seconds: float
    unit_starts: tuple[float, ...] = ()
    unit_ends: tuple[float, ...] = ()


def timed_highlight_wave_progress(group: TimedHighlightGroup, position: float) -> float:
    """Move one continuous wave at each source unit's actual pace.

    Several displayed letters can share one word timestamp. They then split
    only that word's measured interval, while adjacent words/characters may
    occupy very different real durations.
    """
    count = len(group.unit_starts)
    if not count or count != len(group.unit_ends):
        return 0.0
    try:
        now = float(position)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(now):
        return 0.0
    front = 0.0
    index = 0
    while index < count:
        start, end = group.unit_starts[index], group.unit_ends[index]
        next_index = index + 1
        while (
            next_index < count
            and group.unit_starts[next_index] == start
            and group.unit_ends[next_index] == end
        ):
            next_index += 1
        if now >= end:
            front = float(next_index)
        elif now > start:
            front = index + (next_index - index) * (now - start) / max(1e-9, end - start)
            break
        else:
            break
        index = next_index
    return max(0.0, min(1.0, front / count))


def _aligned_offsets(text: str, units: Sequence[object]) -> tuple[int, ...] | None:
    """Map source unit boundaries to a whitespace-normalized display row."""
    source = "".join(str(getattr(unit, "text", "")) for unit in units)
    if not source or not text:
        return None
    if source == text:
        offsets = [0]
        for unit in units:
            offsets.append(offsets[-1] + len(str(getattr(unit, "text", ""))))
        return tuple(offsets)
    if " ".join(source.split()) != " ".join(text.split()):
        return None
    # Reconstruct a boundary mapping rather than assuming a collapsed source
    # space occupies the same number of display characters.
    mapping = [0] * (len(source) + 1)
    src = dst = 0
    while src < len(source):
        if source[src].isspace():
            src_end = src + 1
            while src_end < len(source) and source[src_end].isspace():
                src_end += 1
            dst_end = dst
            while dst_end < len(text) and text[dst_end].isspace():
                dst_end += 1
            for index in range(src + 1, src_end + 1):
                mapping[index] = dst_end
            src, dst = src_end, dst_end
            continue
        if dst >= len(text) or source[src] != text[dst]:
            return None
        src += 1
        dst += 1
        mapping[src] = dst
    if dst != len(text):
        return None
    offsets = [0]
    consumed = 0
    for unit in units:
        consumed += len(str(getattr(unit, "text", "")))
        offsets.append(mapping[consumed])
    return tuple(offsets)


def timed_unit_reading_front(
    text: str,
    units: Sequence[object],
    position: float,
    boundaries: Sequence[float],
    *,
    line_end: float | None = None,
) -> float | None:
    """Return the actual time-driven hard reading front in boundary x units.

    ``boundaries`` has ``len(text) + 1`` entries. Missing unit end times use
    the next unit start, or ``line_end`` for the last unit. An unknown final
    duration becomes an instantaneous last unit, never an invented long read.
    ``None`` means the units do not align or contain unusable timestamps.
    """
    offsets = _aligned_offsets(str(text), units)
    if offsets is None or len(boundaries) != len(text) + 1:
        return None
    try:
        xs = tuple(float(value) for value in boundaries)
        now = float(position)
        starts = tuple(float(getattr(unit, "start_seconds")) for unit in units)
        ends = tuple(
            None if getattr(unit, "end_seconds", None) is None
            else float(getattr(unit, "end_seconds"))
            for unit in units
        )
        final_end = None if line_end is None else float(line_end)
    except (TypeError, ValueError, OverflowError, AttributeError):
        return None
    if not math.isfinite(now) or any(not math.isfinite(value) for value in xs + starts):
        return None
    if any(xs[index + 1] < xs[index] for index in range(len(xs) - 1)):
        return None
    if any(end is not None and not math.isfinite(end) for end in ends):
        return None
    if final_end is not None and not math.isfinite(final_end):
        return None
    if any(starts[index + 1] < starts[index] for index in range(len(starts) - 1)):
        return None
    front = xs[0]
    for index, start in enumerate(starts):
        end = ends[index]
        if end is None:
            end = starts[index + 1] if index + 1 < len(starts) else final_end
        if end is None:
            end = start
        if end < start:
            return None
        if now < start:
            break
        left, right = xs[offsets[index]], xs[offsets[index + 1]]
        if now >= end:
            fraction = 1.0
        elif now <= start:
            fraction = 0.0
        else:
            fraction = (now - start) / max(1e-9, end - start)
        front = max(front, left + (right - left) * fraction)
    return front


def real_unit_row_finished_before_next_start(
    text: str,
    units: Sequence[object],
    position: float,
    next_start: float | None,
) -> bool:
    """Whether a real OBO row has ended while the next row is still unread.

    This is a *visual focus* cue, not an active-lyric/time change.  A missing
    final end time may be estimated for the reading front, but is not evidence
    that the row finished before the next source timestamp.  Keeping those
    decisions separate prevents an early scroll from reading the next row.
    """
    if next_start is None or not genuinely_timed_unit_row(units):
        return False
    if _aligned_offsets(str(text), units) is None:
        return False
    try:
        now = float(position)
        following = float(next_start)
        spoken = tuple(
            unit for unit in units
            if any(character.isalnum() for character in str(getattr(unit, "text", "")))
        )
        if not spoken:
            return False
        final_end = float(getattr(spoken[-1], "end_seconds"))
        ends = tuple(
            float(value)
            for unit in spoken
            if (value := getattr(unit, "end_seconds", None)) is not None
        )
    except (TypeError, ValueError, OverflowError, AttributeError):
        return False
    if not ends or not math.isfinite(now) or not math.isfinite(following):
        return False
    if not math.isfinite(final_end) or any(not math.isfinite(end) for end in ends):
        return False
    finished = max(final_end, *ends)
    return finished < following and finished <= now < following


def explicit_slow_unit_groups(
    text: str,
    units: Sequence[object],
    *,
    min_duration: float = 1.0,
) -> tuple[TimedHighlightGroup, ...]:
    """Select slow timed characters and Latin words with attached punctuation."""
    offsets = _aligned_offsets(str(text), units)
    if offsets is None:
        return ()
    result: list[TimedHighlightGroup] = []
    current: TimedHighlightGroup | None = None
    for index, unit in enumerate(units):
        token = str(getattr(unit, "text", ""))
        spoken = token.strip()
        leading_space = len(token) - len(token.lstrip())
        spoken_start = offsets[index] + leading_space
        spoken_end = spoken_start + len(spoken)
        try:
            start = float(getattr(unit, "start_seconds"))
            raw_end = getattr(unit, "end_seconds", None)
            end = None if raw_end is None else float(raw_end)
        except (TypeError, ValueError, OverflowError, AttributeError):
            end = None
            start = 0.0
        # Timed sources often attach apostrophes and sentence punctuation to
        # the word they time (for example, "fallin', "). Keep the entire
        # displayed token in the glow group while checking its spoken core.
        core_start, core_end = 0, len(spoken)
        while core_start < core_end and unicodedata.category(spoken[core_start]).startswith("P"):
            core_start += 1
        while core_end > core_start and unicodedata.category(spoken[core_end - 1]).startswith("P"):
            core_end -= 1
        core = spoken[core_start:core_end]
        connectors = {"'", "’", "-", "‐", "‑"}
        latin_letters = tuple(character.isalpha() and ord(character) < 0x2E80 for character in core)
        alphabetic_word = bool(core) and all(
            latin_letters[position] or (
                character in connectors
                and position > 0 and position + 1 < len(core)
                and latin_letters[position - 1]
                and latin_letters[position + 1]
            )
            for position, character in enumerate(core)
        )
        spoken_characters = alphabetic_word or (len(spoken) == 1 and spoken.isalnum())
        eligible = (
            spoken_characters
            and end is not None and math.isfinite(start) and math.isfinite(end)
            and end - start > float(min_duration)
            and offsets[index + 1] - offsets[index] == len(token)
        )
        if not eligible:
            if current is not None:
                result.append(current)
                current = None
            continue
        if current is not None and current.end_offset == spoken_start and start >= current.start_seconds:
            current = TimedHighlightGroup(
                current.start_offset, spoken_end, current.start_seconds,
                max(current.end_seconds, end),
                current.unit_starts + (start,) * len(spoken),
                current.unit_ends + (end,) * len(spoken),
            )
        else:
            if current is not None:
                result.append(current)
            current = TimedHighlightGroup(
                spoken_start, spoken_end, start, end,
                (start,) * len(spoken), (end,) * len(spoken),
            )
    if current is not None:
        result.append(current)
    return tuple(result)
