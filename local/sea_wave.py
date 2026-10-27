"""Sea-only audio envelopes and bounded, frequency-shaped water motion."""
from __future__ import annotations

import math
import os

SEA_WAVE_INTENSITY = "medium"
INTENSITIES = {"gentle": 1.0, "medium": 1.55, "strong": 3.6, "vocal": 1.0}
INTENSITY_LABELS = {"gentle": "柔和", "medium": "适中", "strong": "强烈", "vocal": "人声"}


def sea_intensity(value=None):
    value = value or os.environ.get("GLUSPOT_SEA_INTENSITY", SEA_WAVE_INTENSITY)
    return value if value in INTENSITIES else SEA_WAVE_INTENSITY


def _follow(current, target, dt, half_life):
    return current + (target - current) * (1.0 - 2.0 ** (-dt / half_life))


class SeaWaveState:
    def __init__(self):
        self.peaks = [0.0] * 3
        self.bands = [0.0] * 3
        self.average = 0.0
        self.impulse = 0.0
        self.swell = 0.0
        self.velocity = 0.0
        self.updated_at = None
        self.vocal_peak = 0.0
        self.vocal = 0.0
        self.vocal_average = 0.0
        self.vocal_impulse = 0.0

    def update(self, bands, now, vocal=0.0):
        dt = 1 / 60 if self.updated_at is None else max(0.0, min(0.25, now - self.updated_at))
        self.updated_at = now
        for i, raw in enumerate(bands):
            raw = max(0.0, min(1.0, float(raw)))
            self.peaks[i] = max(raw, self.peaks[i] * 2 ** (-dt / 3.0))
            target = min(1.0, raw / max(self.peaks[i], 0.008)) if raw > 0.003 else 0.0
            self.bands[i] = _follow(self.bands[i], target, dt, 0.03 if target > self.bands[i] else 0.22)
        vocal = max(0.0, min(1.0, float(vocal)))
        self.vocal_peak = max(vocal, self.vocal_peak * 2 ** (-dt / 3.0))
        vocal_target = min(1.0, vocal / max(self.vocal_peak, 0.008)) if vocal > 0.003 else 0.0
        self.vocal = _follow(self.vocal, vocal_target, dt, 0.04 if vocal_target > self.vocal else 0.30)
        excess_vocal = max(0.0, self.vocal - self.vocal_average * 1.1 - 0.06)
        self.vocal_average = _follow(self.vocal_average, self.vocal, dt, 0.4)
        self.vocal_impulse = max(excess_vocal, self.vocal_impulse * 2 ** (-dt / 0.13))
        low = self.bands[0]
        # Continuous positive flux above the slow baseline; no refractory
        # interval, so closely spaced drum hits are not silently discarded.
        excess = max(0.0, low - self.average * 1.15 - 0.08)
        self.average = _follow(self.average, low, dt, 0.45)
        self.impulse = max(excess, self.impulse * 2 ** (-dt / 0.16))
        # Stable damped spring, subdivided to avoid frame-rate-dependent instability.
        steps = max(1, math.ceil(dt / 0.008))
        step = dt / steps
        for _ in range(steps):
            target = self.impulse
            self.velocity += (45.0 * (target - self.swell) - 12.0 * self.velocity) * step
            self.swell += self.velocity * step

    def displacement(self, pos, phase, intensity):
        low, mid, high = self.bands
        if sea_intensity(intensity) == "vocal":
            # Vocal energy lifts the whole line; a rounded mid-scale crest
            # moves gently across it without coupling to bass.
            crest = math.cos((pos - 0.5) * math.tau + 0.25 * math.sin(phase * 0.3))
            motion = self.vocal * (17.0 + 22.0 * crest) + self.vocal_impulse * 9.0
            if motion >= 0:
                return 49.0 * math.tanh(motion / 49.0) * (0.3 + 0.7 * self.vocal)
            return 3.0 * math.tanh(motion / 3.0)
        strength = INTENSITIES[sea_intensity(intensity)]
        # Low frequencies make broad swells, mids shape shorter waves and highs
        # add fine ripples. The upward bias keeps the crest clear of lyric ink.
        broad = math.sin(pos * math.tau * 1.1 + phase)
        middle = math.sin(pos * math.tau * 2.3 - phase * 0.7)
        fine = math.sin(pos * math.tau * 4.1 + phase * 0.4)
        motion = (low * (7.0 + 13.0 * broad) + mid * 4.0 * middle
                  + high * 1.5 * fine + self.swell * 18.0
                  + self.impulse * 12.0) * strength
        if motion >= 0.0:
            # A low-band envelope keeps the breadth responsive even where
            # the strong setting approaches the upper soft limit.
            return 49.0 * math.tanh(motion / 49.0) * (0.35 + 0.65 * low)
        return 3.0 * math.tanh(motion / 3.0)
