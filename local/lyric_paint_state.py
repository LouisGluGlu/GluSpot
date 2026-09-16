from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from api.lyric_timeline import (
    TimedLineLike,
    lyric_phase_for_index,
    lyric_phase_near_position,
    smoothed_lyric_phase_state,
)


@dataclass(frozen=True)
class LyricPaintPhaseCache:
    active_index: int
    progress: float
    updated_at: float


@dataclass(frozen=True)
class LyricPaintState:
    phase: dict[str, object]
    cache: LyricPaintPhaseCache


def build_lyric_paint_state(
    *,
    timed_lyrics: Sequence[TimedLineLike],
    lyric_index: object,
    estimated_position_seconds: object,
    lyric_offset_seconds: object,
    lyric_changed_at: object,
    previous_phase_index: object,
    previous_phase_progress: object,
    previous_phase_at: object,
    now: object,
    media_is_playing: object,
    transition_kind: object = "",
    position_selects_active_index: object = False,
    lead_seconds: object = 0.12,
) -> LyricPaintState:
    now_value = _coerce_float(now)
    current_index = _coerce_int(lyric_index)
    position = max(0.0, _coerce_float(estimated_position_seconds) + _coerce_float(lyric_offset_seconds))
    lyrics = timed_lyrics or ()
    changed_elapsed = now_value - _coerce_float(lyric_changed_at, now_value)
    if lyrics and bool(position_selects_active_index):
        phase = lyric_phase_near_position(
            lyrics,
            position,
            current_index,
            changed_elapsed_seconds=changed_elapsed,
            lead_seconds=_coerce_float(lead_seconds, 0.12),
        )
        cache_index = _coerce_int(getattr(phase, "active_index", current_index), current_index)
    else:
        phase = lyric_phase_for_index(
            lyrics,
            current_index,
            position_seconds=position,
            changed_elapsed_seconds=changed_elapsed,
        )
        cache_index = current_index
    state = smoothed_lyric_phase_state(
        phase,
        previous_index=_coerce_int(previous_phase_index, -1),
        previous_progress=_coerce_float(previous_phase_progress),
        previous_at=_coerce_float(previous_phase_at, now_value),
        now=now_value,
        media_is_playing=bool(media_is_playing),
        transition_kind=str(transition_kind or ""),
    )
    progress = _clamp01(_coerce_float(state.get("progress")))
    state["progress"] = progress
    return LyricPaintState(
        phase=state,
        cache=LyricPaintPhaseCache(
            active_index=cache_index,
            progress=progress,
            updated_at=now_value,
        ),
    )


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
