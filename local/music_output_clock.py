"""Continuous display compensation; this clock never controls audio or seeks."""
from __future__ import annotations

import math


class OutputLatencyClock:
    # Sender estimates arrive in batches. Apply their corrections between
    # frames, leaving a playing lyric clock with positive forward velocity.
    CORRECTION_SECONDS = 0.25
    MAX_RATE = 0.16

    def __init__(self):
        self.value = 0.0
        self.target = 0.0
        self.at = None
        self.generation = None

    def sample(self, target: float, now: float, generation: object, *, playing: bool) -> float:
        target = float(target)
        target = max(0.0, min(5.0, target)) if math.isfinite(target) else self.target
        if self.at is None or generation != self.generation or now < self.at or not playing:
            self.value = self.target = target
        else:
            elapsed = now - self.at
            # Advance toward the previous observation before accepting this
            # one. A new sample cannot retroactively alter an already painted
            # frame or be multiplied by the number of view/paint calls.
            correction = (self.target - self.value) * -math.expm1(-elapsed / self.CORRECTION_SECONDS)
            self.value += max(-self.MAX_RATE * elapsed, min(self.MAX_RATE * elapsed, correction))
            self.target = target
        self.at, self.generation = now, generation
        return self.value
