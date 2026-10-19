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

from api.lyric_timeline import _spoken_token_spans, genuinely_timed_unit_row


@dataclass(frozen=True)
class DisplayTimedLyricUnit:
    """Ephemeral reading unit; never a provider/cache or interlude record.

    ``estimated`` marks both line interpolation and words split inside a
    genuine multiword source segment. ``timing_origin`` distinguishes those
    cases from unchanged source word anchors and missing-end interpolation.
    """

    start_seconds: float
    end_seconds: float | None
    text: str
    estimated: bool = False
    timing_origin: str = "source"


@dataclass(frozen=True)
class DisplayRowTiming:
    """One immutable, display-only timing decision shared by every surface.

    Quality is ``word`` (source word anchors), ``segment`` (source spans,
    estimated words within each span), ``estimated`` (row interpolation), or
    ``line`` (no safe reading interval). ``source_units`` retains the original
    objects. Only ``display_units`` may be fed to a reading renderer.
    """

    text: str
    quality: str
    source_units: tuple[object, ...]
    display_units: tuple[DisplayTimedLyricUnit, ...]
    start_seconds: float
    end_seconds: float | None

    @property
    def has_reading_timing(self) -> bool:
        return bool(self.display_units)

    @property
    def has_estimates(self) -> bool:
        return any(unit.estimated for unit in self.display_units)

    def reading_progress(self, position: float) -> float | None:
        front = display_row_reading_front(self, position, tuple(range(len(self.text) + 1)))
        return None if front is None else max(0.0, min(1.0, front / max(1, len(self.text))))


def _split_display_span(
    text: str, start: float, end: float | None, *, estimated: bool, origin: str,
) -> tuple[DisplayTimedLyricUnit, ...]:
    spans = _spoken_token_spans(text)
    if len(spans) <= 1 or end is None or end <= start:
        return (DisplayTimedLyricUnit(start, end, text, estimated, origin),) if text else ()
    # Punctuation and whitespace stay attached to their preceding word. The
    # original start and end are exact anchors; only the internal allocation
    # is estimated. A long Latin word gets more time than a short connector.
    offsets = (0,) + tuple(left for left, _right in spans[1:]) + (len(text),)
    weights = tuple(max(1, right - left) for left, right in spans)
    total = sum(weights)
    result = []
    consumed = 0
    for index, weight in enumerate(weights):
        beginning = start + (end - start) * consumed / total
        consumed += weight
        ending = end if index + 1 == len(weights) else start + (end - start) * consumed / total
        result.append(DisplayTimedLyricUnit(
            beginning, ending, text[offsets[index]:offsets[index + 1]], True, origin,
        ))
    return tuple(result)


def resolve_display_row_timing(
    text: str, units: Sequence[object] = (), kind: str = "line", *,
    start: float, next_start: float | None = None,
    estimated_start: float | None = None, estimated_end: float | None = None,
    estimated_body_offset: int = 0,
) -> DisplayRowTiming:
    """Keep source anchors while deriving explicitly estimated reading words.

    This does not change source text, unit objects, cache data, interval
    detection, or real long-syllable evidence. Synthetic rows have no real
    clock interval and stay line-only. Ordinary LRC can use its next real row
    for a display estimate with the existing one-second preview policy.

    A caller may supply a verified duet's final display estimate boundaries.
    ``estimated_end`` is already a reading end and is not shortened again.
    Neither override moves valid source word/segment anchors, or fills their
    missing ends. Without a next source row or explicit verified end, ordinary
    tail rows remain line-only.
    A verified singer prefix may be excluded from estimated sung duration
    through ``estimated_body_offset`` while retaining raw text alignment.
    """
    text, raw_units = str(text), tuple(units)
    try:
        beginning = float(start)
    except (TypeError, ValueError, OverflowError):
        beginning = math.nan
    try:
        following = None if next_start is None else float(next_start)
    except (TypeError, ValueError, OverflowError):
        following = None
    if following is not None and not math.isfinite(following):
        following = None
    empty = DisplayRowTiming(text, "line", raw_units, (), beginning, None)
    if not text or not math.isfinite(beginning) or kind == "synthetic":
        return empty
    def estimated_row(anchor: float = beginning) -> DisplayRowTiming:
        try:
            anchor = anchor if estimated_start is None else float(estimated_start)
            end = estimated_row_read_end(anchor, following) if estimated_end is None else float(estimated_end)
        except (TypeError, ValueError, OverflowError):
            return empty
        if not math.isfinite(anchor) or anchor < beginning or (end is not None and not math.isfinite(end)):
            return empty
        if end is not None and following is not None:
            end = min(end, following)
        if end is None or end <= anchor:
            return empty
        try:
            body_offset = int(estimated_body_offset)
        except (TypeError, ValueError, OverflowError):
            body_offset = 0
        if not 0 <= body_offset < len(text):
            body_offset = 0
        prefix = (DisplayTimedLyricUnit(anchor, anchor, text[:body_offset], True, "label"),) if body_offset else ()
        estimated_units = prefix + _split_display_span(text[body_offset:], anchor, end, estimated=True, origin="line")
        return DisplayRowTiming(text, "estimated", raw_units, estimated_units, anchor, end)

    offsets = _aligned_offsets(text, raw_units) if kind == "unit" and raw_units else None
    if offsets is None:
        return estimated_row()
    clocks = []
    for unit in raw_units:
        try:
            onset = float(getattr(unit, "start_seconds"))
            raw_end = getattr(unit, "end_seconds", None)
            ending = None if raw_end is None else float(raw_end)
        except (TypeError, ValueError, OverflowError, AttributeError):
            return estimated_row()
        if not math.isfinite(onset) or (
            ending is not None and (not math.isfinite(ending) or ending < onset)
        ):
            return estimated_row()
        clocks.append((onset, ending))
    if any(clocks[index][0] < clocks[index - 1][0] for index in range(1, len(clocks))):
        return estimated_row()
    if len(clocks) == 1 and clocks[0][1] in {None, clocks[0][0]}:
        return estimated_row(clocks[0][0])

    genuine = genuinely_timed_unit_row(raw_units)
    display_units = []
    index = 0
    while index < len(raw_units):
        onset, ending = clocks[index]
        stop = index + 1
        # Simultaneous sentence fragments are one source segment, not several
        # independently sung words. Avoid jumping to its last fragment at t0.
        while stop < len(raw_units) and clocks[stop][0] == onset:
            stop += 1
        if stop > index + 1:
            known_ends = [value for _onset, value in clocks[index:stop] if value is not None]
            ending = max(known_ends) if known_ends else None
        missing_end = ending is None
        if ending is None:
            # A missing source-unit end is bounded by the next actual onset,
            # including a separately timed attached backing-vocal row. The
            # one-second display preview belongs only to line interpolation;
            # applying it here truncates a word which is still being sung.
            ending = clocks[stop][0] if stop < len(clocks) else following
        if ending is not None and ending < onset:
            ending = None
        fragment = text[offsets[index]:offsets[stop]]
        segment = len(_spoken_token_spans(fragment)) > 1 or stop > index + 1
        display_units.extend(_split_display_span(
            fragment, onset, ending,
            estimated=missing_end or segment or any(bool(getattr(unit, "estimated", False)) for unit in raw_units[index:stop]),
            origin="segment" if segment else ("missing-end" if missing_end else "source"),
        ))
        index = stop
    if not display_units:
        return empty
    end_times = [unit.end_seconds for unit in display_units if unit.end_seconds is not None]
    return DisplayRowTiming(
        text, "word" if genuine else "segment", raw_units, tuple(display_units),
        clocks[0][0], max(end_times) if end_times and len(end_times) == len(display_units) else None,
    )


def display_row_reading_front(
    timing: DisplayRowTiming, position: float, boundaries: Sequence[float],
) -> float | None:
    """Evaluate the same display plan directly from the current play clock."""
    if not timing.has_reading_timing:
        return None
    return timed_unit_reading_front(
        timing.text, timing.display_units, position, boundaries,
        line_end=timing.end_seconds,
    )


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
class DisplayRowEstimateBoundaries:
    """Display interpolation bounds; not evidence of a measured vocal end."""

    next_start: float | None
    estimated_start: float | None = None
    estimated_end: float | None = None
    body_offset: int = 0


def display_row_estimate_boundaries(
    rows: Sequence[tuple[float, str]], unit_rows: Sequence[Sequence[object]],
    index: int, *, singer_names: Sequence[str] | str = (),
    duet_presentation: object | None = None,
) -> DisplayRowEstimateBoundaries:
    """Skip simultaneous/paired voices before choosing a reading interval.

    Only verified singer groups may borrow a peer's body onset, and only at
    exactly matching source times through ``duet_estimated_read_start``.
    Staggered duets keep each source start. The final group never invents an
    end from track duration or a fallback animation span.
    """
    if not 0 <= index < len(rows):
        return DisplayRowEstimateBoundaries(None)
    beginning = float(rows[index][0])
    following = next((float(start) for start, _text in rows[index + 1:]
                      if math.isfinite(float(start)) and float(start) > beginning), None)
    if duet_presentation is None:
        from local.music_lyric_presentation import analyze_duet_lyric_rows
        duet_presentation = analyze_duet_lyric_rows(rows, singer_names=singer_names)
    row = duet_presentation.rows[index]
    own_primary = str(rows[index][1]).replace("\u2028", "\n").splitlines()
    own_body = str(row.body_text).replace("\u2028", "\n").splitlines()
    own_text = own_primary[0] if own_primary else ""
    body_text = own_body[0] if own_body else ""
    own_body_offset = own_text.find(body_text) if row.singer is not None and body_text else 0
    own_body_offset = max(0, own_body_offset)
    group = next((group for group in duet_presentation.groups
                  if index in (group.first_index, group.second_index)), None)
    if group is None:
        return DisplayRowEstimateBoundaries(following, body_offset=own_body_offset)
    group_start = max(float(rows[group.first_index][0]), float(rows[group.second_index][0]))
    following = next((float(start) for start, _text in rows[group.second_index + 1:]
                      if math.isfinite(float(start)) and float(start) > group_start), None)
    peer = group.second_index if index == group.first_index else group.first_index
    raw_primary = str(rows[peer][1]).replace("\u2028", "\n").splitlines()
    body_primary = str(duet_presentation.rows[peer].body_text).replace("\u2028", "\n").splitlines()
    primary = raw_primary[0] if raw_primary else ""
    body = body_primary[0] if body_primary else ""
    peer_units = unit_rows[peer] if peer < len(unit_rows) else ()
    body_offset = primary.find(body) if body else -1
    offsets = (body_offset,) if body_offset >= 0 else None
    source_text = "".join(str(getattr(unit, "text", "")) for unit in peer_units)
    if " ".join(source_text.split()) == " ".join(body.split()):
        primary, offsets = body, (0,)
    onset = duet_estimated_read_start(
        beginning, primary, peer_units, offsets,
        peer_source_start=float(rows[peer][0]),
    )
    return DisplayRowEstimateBoundaries(following, onset, following, own_body_offset)


@dataclass(frozen=True)
class TimedHighlightGroup:
    """A contiguous run of individually timed slow characters."""

    start_offset: int
    end_offset: int
    start_seconds: float
    end_seconds: float
    unit_starts: tuple[float, ...] = ()
    unit_ends: tuple[float, ...] = ()
    source_text: str = ""


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
    min_duration: float = 0.8,
) -> tuple[TimedHighlightGroup, ...]:
    """Select measured characters/words lasting at least the fixed 0.8 s cue.

    ``min_duration`` remains accepted for historical callers; it no longer
    changes the product's highlight threshold.
    """
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
                unicodedata.category(character).startswith("M")
                and position > 0
                and (latin_letters[position - 1] or unicodedata.category(core[position - 1]).startswith("M"))
            ) or (
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
            and not bool(getattr(unit, "estimated", False))
            and end is not None and math.isfinite(start) and math.isfinite(end)
            and end - start >= 0.8 - 1e-9
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
                current.source_text + spoken,
            )
        else:
            if current is not None:
                result.append(current)
            current = TimedHighlightGroup(
                spoken_start, spoken_end, start, end,
                (start,) * len(spoken), (end,) * len(spoken),
                spoken,
            )
    if current is not None:
        result.append(current)
    return tuple(result)
