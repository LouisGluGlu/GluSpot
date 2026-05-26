from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


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


TimedLineLike = tuple[float, str]


def active_index_for_position(
    timed_lyrics: Sequence[TimedLineLike],
    position_seconds: float,
    lead_seconds: float = 0.12,
) -> int:
    if not timed_lyrics:
        return 0
    position = max(0.0, float(position_seconds)) + max(0.0, float(lead_seconds))
    index = 0
    for line_index, (line_time, _text) in enumerate(timed_lyrics):
        if float(line_time) <= position:
            index = line_index
        else:
            break
    return max(0, min(len(timed_lyrics) - 1, index))


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


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
