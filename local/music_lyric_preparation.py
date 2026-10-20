"""Immutable, display-only lyric preparation. No Qt objects or provider writes."""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from bisect import bisect_left, bisect_right
import math
import sys

from api.models import TimedLyricUnit
from local.lyric_unit_timing import (
    DisplayRowTiming, display_row_estimate_boundaries, resolve_display_row_timing,
    timed_unit_reading_front, genuinely_timed_unit_row,
)
from local.music_lyric_intervals import add_interlude_rows
from local.music_lyric_presentation import (
    INTRO_COUNTDOWN_TEXT, DuetLyricPresentation, DisplayLyricLayers,
    analyze_duet_lyric_rows, attached_parenthetical_read_end,
    present_lyric_text, present_timed_lyric_rows, split_display_layers,
)


@dataclass(frozen=True)
class LyricInputUnit:
    start_seconds: float
    end_seconds: float | None
    text: str
    estimated: bool = False


@dataclass(frozen=True)
class LyricPresentationInput:
    identity: str
    title: str
    artist: str
    timed: tuple[tuple[float, str], ...]
    units: tuple[tuple[object, ...], ...]
    kinds: tuple[str, ...]
    lines: tuple[str, ...]
    translated: bool


@dataclass(frozen=True)
class PreparedLyricPresentation:
    source: LyricPresentationInput
    lines: tuple[str, ...]
    timed: tuple[tuple[float, str], ...]
    units: tuple[tuple[object, ...], ...]
    kinds: tuple[str, ...]
    raw_source_texts: tuple[str, ...]
    read_ends: tuple[float | None, ...]
    timings: tuple[DisplayRowTiming, ...]
    duet: DuetLyricPresentation | None
    layers: tuple[DisplayLyricLayers, ...]
    island_rows: tuple[str, ...]
    island_timings: tuple[DisplayRowTiming, ...]
    has_genuine_timing: bool


def primary_text(text: str) -> str:
    rows = str(text or "").replace("\u2028", "\n").splitlines()
    return rows[0].strip() if rows else ""


def snapshot_lyric_input(owner, *, translated: bool | None = None, source=None) -> LyricPresentationInput:
    """Called on the owner thread; copy only immutable values into a worker."""
    timed, units, kinds = source if source is not None else (
        getattr(owner, "timed_lyrics", ()) or (),
        getattr(owner, "timed_lyric_units", ()) or (),
        getattr(owner, "timed_lyric_timing_kinds", ()) or (),
    )
    frozen_units = []
    for row in units:
        frozen_units.append(tuple(
            unit if isinstance(unit, TimedLyricUnit) else LyricInputUnit(
                float(getattr(unit, "start_seconds", math.nan)),
                None if getattr(unit, "end_seconds", None) is None else float(unit.end_seconds),
                str(getattr(unit, "text", "") or ""), bool(getattr(unit, "estimated", False)),
            ) for unit in (row or ())
        ))
    title = str(getattr(owner, "track_title", "") or "")
    artist = str(getattr(owner, "artist", "") or "")
    identity = str(getattr(owner, "media_track_id", "") or "") or f"{title}\x1f{artist}"
    return LyricPresentationInput(
        identity, title, artist, tuple((float(start), str(text)) for start, text in timed),
        tuple(frozen_units), tuple(str(kind or "line") for kind in kinds),
        tuple(str(line) for line in (getattr(owner, "lyrics", ()) or ())),
        bool(getattr(owner, "enable_dual_line_lyrics", False)) if translated is None else bool(translated),
    )


def coalesced_timed_lyrics(timed_lyrics, *, clean_intro=True, singer_names=""):
    """Preserve verified same-time voices and coalesce original/translation."""
    grouped = []
    expanded = []
    for raw_time, raw_text in timed_lyrics:
        parts = str(raw_text or "").replace("\u2028", "\n").splitlines()
        voice_indices = [index for index, part in enumerate(parts) if analyze_duet_lyric_rows(
            ((0., part),), singer_names=singer_names,
        ).rows[0].singer is not None]
        if len(voice_indices) >= 2 and voice_indices[0] == 0:
            for start, end in zip(voice_indices, (*voice_indices[1:], len(parts))):
                expanded.append((raw_time, "\n".join(parts[start:end])))
        else:
            expanded.append((raw_time, raw_text))
    for raw_time, raw_text in expanded:
        try:
            start = max(0., float(raw_time))
        except (TypeError, ValueError):
            continue
        text = str(raw_text or "").strip()
        if not text:
            continue
        if grouped and abs(grouped[-1][0] - start) <= .0005:
            if analyze_duet_lyric_rows(
                ((grouped[-1][0], "\n".join(grouped[-1][1])), (start, text)),
                singer_names=singer_names,
            ).groups:
                grouped.append((start, [text]))
            elif text not in grouped[-1][1]:
                grouped[-1][1].append(text)
        else:
            grouped.append((start, [text]))
    return [(start, "\n".join(parts)) for start, parts in grouped if parts]


def _row_plans(rows, units, kinds, artist, duet, read_ends=()):
    plans = []
    for index, (start, text) in enumerate(rows):
        bounds = display_row_estimate_boundaries(rows, units, index, singer_names=artist, duet_presentation=duet)
        following = read_ends[index] if index < len(read_ends) and read_ends[index] is not None else bounds.next_start
        plans.append(resolve_display_row_timing(
            primary_text(text), units[index] if index < len(units) else (), kinds[index] if index < len(kinds) else "line",
            start=float(start), next_start=following, estimated_start=bounds.estimated_start,
            estimated_end=bounds.estimated_end, estimated_body_offset=bounds.body_offset,
        ))
    return tuple(plans)


def prepare_lyric_presentation(source: LyricPresentationInput) -> PreparedLyricPresentation:
    """Full-song CPU work; safe on a worker, never modifies source or cache."""
    kwargs = dict(show_translation=source.translated, clean_intro=True, singer_names=source.artist)
    # Island indexes intentionally remain raw. Its standalone backing vocals
    # retain their original starts rather than appearing on the previous row.
    island_displayed = present_timed_lyric_rows(
        tuple(enumerate(source.lines)), **kwargs, intro_countdown=False, attach_parentheticals=False,
    )
    indexed = {int(index): text for index, text in island_displayed}
    island_rows = tuple(indexed.get(index, "") for index in range(len(source.lines)))
    if not source.timed:
        # The surface attaches independent untimed backing-vocal rows; the
        # island deliberately retains its raw indexes. Keep both established
        # projections rather than reusing the island's non-attaching view.
        surface_rows = present_timed_lyric_rows(tuple(enumerate(source.lines)), **kwargs, intro_countdown=False)
        lines = tuple(text.replace("\n", "\u2028") for _index, text in surface_rows)
        if source.title.strip() in {"", "暂无媒体", "暂无播放", "媒体初始化中"}:
            lines = ()
        else:
            lines = lines or ("暂无歌词",)
        layers = tuple(split_display_layers(text, show_translation=source.translated) for text in lines)
        return PreparedLyricPresentation(source, lines, (), (), (), source.lines, (), (), None, layers,
                                         island_rows, (), False)
    coalesced = coalesced_timed_lyrics(source.timed, singer_names=source.artist)
    timed = tuple(add_interlude_rows(
        present_timed_lyric_rows(coalesced, **kwargs), source.timed, source.units, source.kinds,
        show_translation=source.translated, singer_names=source.artist,
    ))
    duet = analyze_duet_lyric_rows(timed, singer_names=source.artist)
    # Index prepared primary texts once. The old paint path reparsed a song
    # prefix for each row (quadratic work) merely to recover provenance.
    def prepared_primary(text):
        return primary_text(present_lyric_text(text, show_translation=source.translated,
                                              clean_intro=True, clean_credits=False))
    raw_prepared = tuple(prepared_primary(text) for _start, text in source.timed)
    coalesced_prepared = tuple(prepared_primary(text) for _start, text in coalesced)
    sorted_coalesced = sorted((float(start), index) for index, (start, _text) in enumerate(coalesced))
    coalesced_times = [start for start, _index in sorted_coalesced]
    mapped_units, mapped_kinds, mapped_sources, read_ends = [], [], [], []
    cursor = 0
    for start, text in timed:
        primary = primary_text(text)
        read_ends.append(attached_parenthetical_read_end(
            text, coalesced, float(start), show_translation=source.translated, clean_intro=True,
        ))
        left, right = bisect_left(coalesced_times, start - .0005), bisect_right(coalesced_times, start + .0005)
        matching = [index for _stamp, index in sorted_coalesced[left:right] if coalesced_prepared[index] == primary]
        mapped_sources.append(coalesced[min(matching)][1] if matching else text)
        match = None
        for index in range(cursor, len(source.timed)):
            stamp, raw_text = source.timed[index]
            if abs(stamp - start) > .0005:
                if stamp > start + .0005:
                    break
                continue
            if primary_text(raw_text) == primary or raw_prepared[index] == primary:
                match = index
                break
        units, kind = (), "line"
        if match is not None:
            units = source.units[match] if match < len(source.units) else ()
            kind = source.kinds[match] if match < len(source.kinds) else "line"
            if kind == "unit" and timed_unit_reading_front(primary, units, start, range(len(primary) + 1)) is None:
                units, kind = (), "line"
            cursor = match + 1
        mapped_units.append(units)
        mapped_kinds.append(kind)
    plans = _row_plans(timed, mapped_units, mapped_kinds, source.artist, duet, read_ends)
    if (timed == source.timed
            and tuple(mapped_units) == tuple(source.units[index] if index < len(source.units) else ()
                                              for index in range(len(source.timed)))
            and tuple(mapped_kinds) == tuple(source.kinds[index] if index < len(source.kinds) else "line"
                                              for index in range(len(source.timed)))
            and all(end is None for end in read_ends)):
        # Identical immutable timelines need only one timing pass. Any credit,
        # translation/voice coalescing, attached vocal end or inserted gap
        # keeps the island's independent raw-index projection below.
        island_plans = plans
    else:
        island_duet = analyze_duet_lyric_rows(source.timed, singer_names=source.artist)
        island_plans = _row_plans(source.timed, source.units, source.kinds, source.artist, island_duet)
    layers = tuple(split_display_layers(row.body_text, source_text=mapped_sources[index],
                                       show_translation=source.translated) for index, row in enumerate(duet.rows))
    genuine = any(kind == "unit" and genuinely_timed_unit_row(units)
                  for kind, units in zip(mapped_kinds, mapped_units))
    return PreparedLyricPresentation(
        source, tuple(text.replace("\n", "\u2028") for _start, text in timed) if timed else ("暂无歌词",),
        timed, tuple(mapped_units), tuple(mapped_kinds), tuple(mapped_sources), tuple(read_ends), plans,
        duet, layers, island_rows, island_plans, genuine,
    )


def retained_size(value) -> int:
    """Count owned immutable Python objects once (not QImage/shared pixels)."""
    seen = set()
    def size(item):
        if id(item) in seen:
            return 0
        seen.add(id(item))
        total = sys.getsizeof(item)
        if is_dataclass(item):
            return total + sys.getsizeof(getattr(item, "__dict__", {})) + sum(size(getattr(item, field.name)) for field in fields(item))
        if isinstance(item, (tuple, list)):
            return total + sum(size(child) for child in item)
        return total
    return size(value)
