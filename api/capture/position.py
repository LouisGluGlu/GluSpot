"""Deterministic SMTC anchor projection; no native I/O or clocks are hidden here."""
from __future__ import annotations

import math
from dataclasses import dataclass

from api.capture.contracts import TimelineSample


@dataclass(frozen=True)
class PositionSnapshot:
    """Predicted position plus the independent evidence from which it came."""

    position_seconds: float
    sampled_at: float
    observed_seconds: float
    is_playing: bool
    rate: float
    identity: tuple[str, str]
    anchor_age: float


class PositionModel:
    """Project one player's timeline using a single wall-to-monotonic mapping.

    Repeated polls of an unchanged SMTC anchor never reset the clock. Playback
    transitions freeze/resume the predicted position when the player repeats an
    old anchor. New evidence corrects prediction only outside ``drift_seconds``.
    A wall-clock adjustment cannot move a running prediction; new anchors seen
    during such an adjustment use receipt time until the clocks agree again.
    Callers serialize access and supply clocks, making recordings replayable.
    """

    def __init__(self, drift_seconds: float = 0.4):
        self.drift_seconds = drift_seconds
        self._sample: TimelineSample | None = None
        self._signature = None
        self._wall_to_mono = 0.0
        self._anchor_position = 0.0
        self._anchor_mono = 0.0
        self._rate = 0.0
        self._lower = 0.0
        self._upper: float | None = None
        self.last_error_seconds = 0.0
        self.reanchors = 0

    @property
    def sample(self) -> TimelineSample | None:
        return self._sample

    def observe(self, sample: TimelineSample, *, lyric_duration: float = 0.0) -> bool:
        """Accept physical evidence; return whether the prediction reanchored.

        Out-of-order callbacks and invalid numeric values are ignored. A change
        of track or session resets all clock/evidence state, including A→B→A.
        ``lyric_duration`` is an optional known song bound, never an estimated
        final line timestamp.
        """
        numbers = (sample.position_seconds, sample.observed_at, sample.wall_time,
                   sample.rate, sample.start_seconds, sample.end_seconds, lyric_duration)
        if not all(math.isfinite(v) for v in numbers):
            return False
        previous = self._sample
        changed_track = previous is None or sample.identity != previous.identity
        if not changed_track and sample.observed_at < previous.observed_at:
            return False
        if changed_track:
            self._wall_to_mono = sample.observed_at - sample.wall_time
            self._signature = None
        self._lower = max(0.0, sample.start_seconds)
        bounds = [v for v in (sample.end_seconds, lyric_duration) if v > self._lower]
        self._upper = min(bounds) if bounds else None
        updated = sample.updated_at
        if updated is not None and not math.isfinite(updated):
            updated = None
        signature = (sample.position_seconds, updated)
        anchor_changed = signature != self._signature
        rate = max(0.0, sample.rate) if sample.is_playing else 0.0
        playback_changed = previous is not None and (rate != self._rate or sample.is_playing != previous.is_playing)
        projected = self.position(sample.observed_at) if previous is not None else sample.position_seconds
        anchor_mono = sample.observed_at
        clock_shift = abs(sample.wall_time + self._wall_to_mono - sample.observed_at) > 2.0
        if updated is not None and not clock_shift:
            anchor_mono = min(sample.observed_at, updated + self._wall_to_mono)
        candidate = self._clamp(sample.position_seconds + max(0.0, sample.observed_at - anchor_mono) * rate)
        self.last_error_seconds = candidate - projected if anchor_changed and not changed_track else 0.0
        reanchor = changed_track or playback_changed or (anchor_changed and abs(self.last_error_seconds) > self.drift_seconds)
        if reanchor:
            # A repeated, old anchor at pause/resume describes the last source
            # update rather than the instant the transport state changed.
            position = projected if playback_changed and not anchor_changed else candidate
            self._anchor_position = self._clamp(position)
            self._anchor_mono = sample.observed_at
            self._rate = rate
            self.reanchors += 1
        self._sample = sample
        self._signature = signature
        return reanchor

    def calibrate(self, sample: TimelineSample) -> bool:
        """Apply independent UIA progress without replacing the SMTC signature.

        The unchanged old SMTC anchor must not undo a physical correction on
        the next poll. A genuinely changed SMTC anchor still wins normally.
        """
        if (self._sample is None or sample.identity != self._sample.identity
                or sample.observed_at < self._sample.observed_at
                or not math.isfinite(sample.position_seconds)):
            return False
        self.last_error_seconds = sample.position_seconds - self.position(sample.observed_at)
        self._anchor_position = self._clamp(sample.position_seconds)
        self._anchor_mono = sample.observed_at
        self._rate = max(0.0, sample.rate) if sample.is_playing else 0.0
        self._sample = sample
        self.reanchors += 1
        return True

    def position(self, now: float) -> float:
        """Read a bounded prediction; backwards monotonic inputs cannot rewind."""
        return self._clamp(self._anchor_position + max(0.0, now - self._anchor_mono) * self._rate)

    def snapshot(self, now: float) -> PositionSnapshot | None:
        if self._sample is None:
            return None
        return PositionSnapshot(self.position(now), self._anchor_mono,
                                self._sample.position_seconds, self._sample.is_playing,
                                self._rate, self._sample.identity,
                                max(0.0, now - self._anchor_mono))

    def _clamp(self, value: float) -> float:
        value = max(self._lower, value)
        return min(self._upper, value) if self._upper is not None else value
