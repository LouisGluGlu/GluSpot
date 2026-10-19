from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence, Tuple
import unicodedata


def _spoken_token_count(text: str) -> int:
    """Count Latin words and CJK graphemes in one source timestamp span."""
    count = 0
    in_word = False
    for index, character in enumerate(str(text)):
        cjk = (
            "\u2e80" <= character <= "\u9fff"
            or "\uf900" <= character <= "\ufaff"
            or "\u3040" <= character <= "\u30ff"
            or "\u31f0" <= character <= "\u31ff"
            or "\u1100" <= character <= "\u11ff"
            or "\u3130" <= character <= "\u318f"
            or "\uac00" <= character <= "\ud7af"
        )
        if cjk:
            count += 1
            in_word = False
        elif character.isalnum():
            if not in_word:
                count += 1
            in_word = True
        elif unicodedata.category(character).startswith("M") and in_word:
            continue
        elif character in {"'", "’", "-", "‐", "‑"} and in_word and (
            index + 1 < len(text) and text[index + 1].isalnum()
        ):
            continue
        else:
            in_word = False
    return count


def _spoken_token_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Return the same spoken tokens as ``_spoken_token_count`` with offsets."""
    spans: list[tuple[int, int]] = []
    in_word = False
    for index, character in enumerate(str(text)):
        cjk = (
            "\u2e80" <= character <= "\u9fff" or "\uf900" <= character <= "\ufaff"
            or "\u3040" <= character <= "\u30ff" or "\u31f0" <= character <= "\u31ff"
            or "\u1100" <= character <= "\u11ff" or "\u3130" <= character <= "\u318f"
            or "\uac00" <= character <= "\ud7af"
        )
        if cjk:
            spans.append((index, index + 1))
            in_word = False
        elif character.isalnum():
            if in_word:
                spans[-1] = (spans[-1][0], index + 1)
            else:
                spans.append((index, index + 1))
            in_word = True
        elif unicodedata.category(character).startswith("M") and in_word:
            spans[-1] = (spans[-1][0], index + 1)
        elif character in {"'", "’", "-", "‐", "‑"} and in_word and (
            index + 1 < len(text) and text[index + 1].isalnum()
        ):
            spans[-1] = (spans[-1][0], index + 1)
        else:
            in_word = False
    return tuple(spans)


def genuinely_timed_unit_row(units: Sequence[object]) -> bool:
    """Require separate source times for every spoken word or CJK grapheme.

    Some enhanced LRC files put a single word marker in front of an entire
    sentence. That is a usable row anchor, but it cannot certify OBO timing.
    A single Latin word with an explicit start and end is genuinely timed.
    A start-only marker has no measured reading duration; its end would have
    to be guessed from the next lyric row.
    """
    starts: list[float] = []
    ends: list[float | None] = []
    for unit in units:
        # Display interpolation deliberately uses a separate, marked unit
        # type. Its convenient timestamps must never certify source timing.
        if bool(getattr(unit, "estimated", False)):
            return False
        count = _spoken_token_count(str(getattr(unit, "text", "")))
        if count > 1:
            return False
        if not count:
            continue
        try:
            start = float(getattr(unit, "start_seconds"))
            end_value = getattr(unit, "end_seconds", None)
            end = None if end_value is None else float(end_value)
        except (TypeError, ValueError, OverflowError, AttributeError):
            return False
        if not math.isfinite(start) or (end is not None and (not math.isfinite(end) or end < start)):
            return False
        starts.append(start)
        ends.append(end)
    if len(starts) == 1:
        return ends[0] is not None and ends[0] > starts[0]
    return bool(starts) and all(starts[index] > starts[index - 1] for index in range(1, len(starts)))


@dataclass(frozen=True)
class LyricTimingQuality:
    """Token-weighted source precision, independent of display interpolation.

    Only the original (first subrow) contributes to the denominator; attached
    translation/romanization does not dilute it. A timed multiword span is a
    segment, never several genuinely timed words. ``word_coverage`` is zero
    for an empty timeline, so one measured word cannot certify a long song.
    """

    spoken_tokens: int = 0
    genuine_word_tokens: int = 0
    segment_tokens: int = 0
    estimated_tokens: int = 0
    line_tokens: int = 0

    @property
    def word_coverage(self) -> float:
        return self.genuine_word_tokens / self.spoken_tokens if self.spoken_tokens else 0.0

    @property
    def segment_coverage(self) -> float:
        return self.segment_tokens / self.spoken_tokens if self.spoken_tokens else 0.0


def lyric_timing_quality(rows: Sequence[object]) -> LyricTimingQuality:
    """Measure aligned original vocal tokens rather than counting lyric rows.

    Mixed word/segment rows retain their independently measured words. Units
    with invalid or unordered clocks, mismatched text, or display-estimated
    timestamps cannot improve genuine coverage. This helper does not filter
    presentation credits or modify source rows/cache records.
    """
    total = words = segments = estimates = 0
    for row in rows:
        subrows = str(getattr(row, "text", "")).replace("\u2028", "\n").splitlines()
        primary = subrows[0] if subrows else ""
        count = _spoken_token_count(primary)
        total += count
        if not count:
            continue
        kind = str(getattr(row, "timing_kind", "line"))
        if kind == "synthetic":
            estimates += count
            continue
        units = tuple(getattr(row, "units", ()) or ())
        if kind != "unit" or not units:
            continue
        source = "".join(str(getattr(unit, "text", "")) for unit in units)
        if " ".join(source.split()) != " ".join(primary.split()):
            continue
        spoken = []
        source_offset = 0
        valid = True
        for unit in units:
            unit_text = str(getattr(unit, "text", ""))
            left, source_offset = source_offset, source_offset + len(unit_text)
            token_count = _spoken_token_count(unit_text)
            if not token_count:
                continue
            try:
                beginning = float(getattr(unit, "start_seconds"))
                raw_end = getattr(unit, "end_seconds", None)
                ending = None if raw_end is None else float(raw_end)
            except (TypeError, ValueError, OverflowError, AttributeError):
                valid = False
                break
            if not math.isfinite(beginning) or (
                ending is not None and (not math.isfinite(ending) or ending < beginning)
            ):
                valid = False
                break
            spoken.append((beginning, ending, token_count, bool(getattr(unit, "estimated", False)), left, source_offset))
        if not valid or not spoken or any(
            spoken[index][0] < spoken[index - 1][0] for index in range(1, len(spoken))
        ):
            continue
        categories: list[tuple[int, int, str]] = []
        distinct_starts = {entry[0] for entry in spoken}
        for beginning, ending, token_count, estimated, left, right in spoken:
            category = "line"
            if estimated:
                category = "estimated"
            elif len(distinct_starts) > 1 or (ending is not None and ending > beginning):
                shared_start = sum(entry[0] == beginning for entry in spoken) > 1
                category = "word" if token_count == 1 and not shared_start else "segment"
            categories.append((left, right, category))
        # Some real word formats time individual Latin syllables/letters.
        # Count their containing original word once, rather than allowing a
        # five-letter word to claim five tokens of coverage.
        for left, right in _spoken_token_spans(source):
            covered = {category for begin, end, category in categories if begin < right and end > left}
            if covered == {"word"}:
                words += 1
            elif "estimated" in covered:
                estimates += 1
            elif "segment" in covered and "line" not in covered:
                segments += 1
    return LyricTimingQuality(total, words, segments, estimates, max(0, total - words - segments - estimates))


def has_sufficient_word_timing(
    rows: Sequence[object], *, minimum_coverage: float = 0.95,
) -> bool:
    """Stop richer-source lookup only when at least 95% of words are timed.

    The explicit threshold is an acquisition/upgrade policy, not a claim that
    estimated or segment timestamps became real word timestamps.
    """
    quality = lyric_timing_quality(rows)
    threshold = float(minimum_coverage)
    return bool(quality.spoken_tokens) and math.isfinite(threshold) and (
        0.0 < threshold <= 1.0 and quality.word_coverage >= threshold
    )


@dataclass(frozen=True)
class LyricPhase:
    active_index: int
    previous_index: int
    next_index: int
    line_count: int
    progress: float
    confidence: float
    span: float
    elapsed: float
    remaining: float
    lead_in: float
    source: str

    def as_paint_state(self) -> dict[str, float]:
        return {
            "active_index": float(self.active_index),
            "previous_index": float(self.previous_index),
            "next_index": float(self.next_index),
            "line_count": float(self.line_count),
            "progress": self.progress,
            "confidence": self.confidence,
            "span": self.span,
            "elapsed": self.elapsed,
            "remaining": self.remaining,
            "lead_in": self.lead_in,
            "source": self.source,
        }


@dataclass(frozen=True)
class LyricTimelineDiagnostics:
    estimated_position_seconds: float
    effective_position_seconds: float
    lyric_offset_seconds: float
    visible_lyric_index: int
    visible_current_lyric: str
    visible_lyrics_count: int
    timed_lyrics_count: int
    position_active_index: int
    sync_lag_lines: int
    current_line_start_seconds: float | None
    next_line_start_seconds: float | None
    phase: LyricPhase
    phase_state: dict[str, float]

    def as_dict(self) -> dict[str, object]:
        return {
            "estimated_position_seconds": self.estimated_position_seconds,
            "effective_position_seconds": self.effective_position_seconds,
            "lyric_offset_seconds": self.lyric_offset_seconds,
            "visible_lyric_index": self.visible_lyric_index,
            "visible_current_lyric": self.visible_current_lyric,
            "visible_lyrics_count": self.visible_lyrics_count,
            "timed_lyrics_count": self.timed_lyrics_count,
            "timed_lyrics_available": self.timed_lyrics_count > 0,
            "position_active_index": self.position_active_index,
            "sync_lag_lines": self.sync_lag_lines,
            "current_line_start_seconds": self.current_line_start_seconds,
            "next_line_start_seconds": self.next_line_start_seconds,
            "phase_active_index": self.phase.active_index,
            "phase_previous_index": self.phase.previous_index,
            "phase_next_index": self.phase.next_index,
            "phase_progress": float(self.phase_state.get("progress", self.phase.progress)),
            "phase_confidence": float(self.phase_state.get("confidence", self.phase.confidence)),
            "phase_span": float(self.phase_state.get("span", self.phase.span)),
            "phase_elapsed": float(self.phase_state.get("elapsed", self.phase.elapsed)),
            "phase_remaining": float(self.phase_state.get("remaining", self.phase.remaining)),
            "phase_lead_in": float(self.phase_state.get("lead_in", self.phase.lead_in)),
            "phase_source": str(self.phase_state.get("source", self.phase.source)),
        }


TimedLineLike = Tuple[float, str]


def active_index_for_position(
    timed_lyrics: Sequence[TimedLineLike],
    position_seconds: float,
    lead_seconds: float = 0.12,
) -> int:
    if not timed_lyrics:
        return 0
    position = max(0.0, float(position_seconds)) + max(0.0, float(lead_seconds))
    # bisect's key argument was added in Python 3.10. Keep the same rightmost
    # equal timestamp and logarithmic lookup on the Python 3.8 legacy runtime.
    low, high = 0, len(timed_lyrics)
    while low < high:
        middle = (low + high) // 2
        if position < _line_time(timed_lyrics[middle]):
            high = middle
        else:
            low = middle + 1
    index = low - 1
    return max(0, min(len(timed_lyrics) - 1, index))


def active_index_near_position(
    timed_lyrics: Sequence[TimedLineLike],
    position_seconds: float,
    current_index: int,
    lead_seconds: float = 0.12,
    scan_window: int = 6,
) -> int:
    if not timed_lyrics:
        return 0
    position = max(0.0, float(position_seconds)) + max(0.0, float(lead_seconds))
    line_count = len(timed_lyrics)
    index = max(0, min(line_count - 1, int(current_index)))
    steps = max(1, int(scan_window))

    if position >= _line_time(timed_lyrics[index]):
        for _ in range(steps):
            next_index = index + 1
            if next_index >= line_count:
                return index
            if _line_time(timed_lyrics[next_index]) > position:
                return index
            index = next_index
        return active_index_for_position(timed_lyrics, position, lead_seconds=0.0)

    for _ in range(steps):
        if index <= 0:
            return 0
        index -= 1
        if _line_time(timed_lyrics[index]) <= position:
            return index
    return active_index_for_position(timed_lyrics, position, lead_seconds=0.0)


def lyric_phase_for_index(
    timed_lyrics: Sequence[TimedLineLike],
    active_index: int,
    position_seconds: float,
    changed_elapsed_seconds: float,
    fallback_span_seconds: float = 2.45,
) -> LyricPhase:
    fallback_span = max(0.35, float(fallback_span_seconds))
    if not timed_lyrics:
        elapsed = max(0.0, float(changed_elapsed_seconds))
        return LyricPhase(
            active_index=0,
            previous_index=0,
            next_index=0,
            line_count=0,
            progress=_clamp01(elapsed / fallback_span),
            confidence=0.26,
            span=fallback_span,
            elapsed=elapsed,
            remaining=max(0.0, fallback_span - elapsed),
            lead_in=0.0,
            source="untimed",
        )

    index = max(0, min(len(timed_lyrics) - 1, int(active_index)))
    previous_index = max(0, index - 1)
    next_index = min(len(timed_lyrics) - 1, index + 1)
    if index >= len(timed_lyrics) - 1:
        elapsed = max(0.0, float(changed_elapsed_seconds))
        return LyricPhase(
            active_index=index,
            previous_index=previous_index,
            next_index=next_index,
            line_count=len(timed_lyrics),
            progress=_clamp01(elapsed / fallback_span),
            confidence=0.34,
            span=fallback_span,
            elapsed=elapsed,
            remaining=max(0.0, fallback_span - elapsed),
            lead_in=0.0,
            source="tail",
        )

    current_time = float(timed_lyrics[index][0])
    next_time = float(timed_lyrics[index + 1][0])
    span = max(0.35, next_time - current_time)
    elapsed = max(0.0, float(position_seconds) - current_time)
    lead_in = max(0.0, current_time - float(position_seconds))
    return LyricPhase(
        active_index=index,
        previous_index=previous_index,
        next_index=next_index,
        line_count=len(timed_lyrics),
        progress=_clamp01(elapsed / span),
        confidence=0.78,
        span=span,
        elapsed=elapsed,
        remaining=max(0.0, span - elapsed),
        lead_in=lead_in,
        source="timed",
    )


def lyric_phase_near_position(
    timed_lyrics: Sequence[TimedLineLike],
    position_seconds: float,
    current_index: int,
    changed_elapsed_seconds: float,
    lead_seconds: float = 0.12,
    scan_window: int = 6,
    fallback_span_seconds: float = 2.45,
) -> LyricPhase:
    active_index = active_index_near_position(
        timed_lyrics,
        position_seconds,
        current_index,
        lead_seconds=lead_seconds,
        scan_window=scan_window,
    )
    return lyric_phase_for_index(
        timed_lyrics,
        active_index,
        position_seconds=position_seconds,
        changed_elapsed_seconds=changed_elapsed_seconds,
        fallback_span_seconds=fallback_span_seconds,
    )


def smoothed_lyric_phase_state(
    phase: LyricPhase,
    *,
    previous_index: int,
    previous_progress: float,
    previous_at: float,
    now: float,
    media_is_playing: bool,
    transition_kind: str = "",
) -> dict[str, float]:
    state = phase.as_paint_state()
    progress = _clamp01(float(state.get("progress", 0.0)))
    if (
        bool(media_is_playing)
        and state.get("source") == "timed"
        and int(phase.active_index) == int(previous_index)
        and str(transition_kind or "") != "seek"
        and progress + 0.018 < float(previous_progress)
    ):
        span = max(0.35, float(state.get("span", 0.35) or 0.35))
        progress = min(1.0, float(previous_progress) + max(0.0, float(now) - float(previous_at)) / span)
        state["progress"] = progress
        elapsed = max(0.0, span * progress)
        state["elapsed"] = elapsed
        state["remaining"] = max(0.0, span - elapsed)
    return state


def lyric_timeline_diagnostics(
    *,
    timed_lyrics: Sequence[TimedLineLike],
    lyrics: Sequence[str],
    lyric_index: int,
    estimated_position_seconds: float,
    lyric_offset_seconds: float,
    changed_elapsed_seconds: float,
    previous_phase_index: int,
    previous_phase_progress: float,
    previous_phase_at: float,
    now: float,
    media_is_playing: bool,
    transition_kind: str = "",
) -> LyricTimelineDiagnostics:
    visible_lyrics = [str(line or "") for line in lyrics or ()]
    visible_count = len(visible_lyrics)
    raw_index = int(lyric_index)
    visible_index = raw_index
    current_lyric = ""
    if visible_count:
        visible_index = raw_index % visible_count
        current_lyric = visible_lyrics[visible_index]

    estimated = max(0.0, float(estimated_position_seconds))
    offset = float(lyric_offset_seconds or 0.0)
    effective_position = max(0.0, estimated + offset)
    timed_count = len(timed_lyrics or ())
    position_index = active_index_near_position(timed_lyrics, effective_position, raw_index) if timed_count else visible_index
    phase = lyric_phase_for_index(
        timed_lyrics,
        raw_index,
        position_seconds=effective_position,
        changed_elapsed_seconds=changed_elapsed_seconds,
    )
    phase_state = smoothed_lyric_phase_state(
        phase,
        previous_index=previous_phase_index,
        previous_progress=previous_phase_progress,
        previous_at=previous_phase_at,
        now=now,
        media_is_playing=media_is_playing,
        transition_kind=transition_kind,
    )
    current_start = _line_start_or_none(timed_lyrics, raw_index)
    next_start = _line_start_or_none(timed_lyrics, raw_index + 1)
    return LyricTimelineDiagnostics(
        estimated_position_seconds=estimated,
        effective_position_seconds=effective_position,
        lyric_offset_seconds=offset,
        visible_lyric_index=visible_index,
        visible_current_lyric=current_lyric,
        visible_lyrics_count=visible_count,
        timed_lyrics_count=timed_count,
        position_active_index=position_index,
        sync_lag_lines=position_index - raw_index if timed_count else 0,
        current_line_start_seconds=current_start,
        next_line_start_seconds=next_start,
        phase=phase,
        phase_state=phase_state,
    )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _line_time(line: TimedLineLike) -> float:
    return float(line[0])


def _line_start_or_none(timed_lyrics: Sequence[TimedLineLike], index: int) -> float | None:
    try:
        if index < 0 or index >= len(timed_lyrics):
            return None
        return float(timed_lyrics[index][0])
    except (TypeError, ValueError, IndexError):
        return None
