"""Per-song display range for the island wave and compact spectral bars.

The sea ripple and the four compact bars are live meters, not a stored
waveform. Each playing track therefore keeps its own loudness histogram.
Robust percentiles set the low and high of that song; one spike cannot pin
the range. The current sample is then eased into about 15%–100% of the
full wave height. A nearly flat song uses a minimum span so the scale never
divides by zero, and a true digital silence stays on the floor.
"""
from __future__ import annotations

import math


DISPLAY_LOW = 0.15
DISPLAY_HIGH = 1.0
LOW_QUANTILE = 0.10
HIGH_QUANTILE = 0.90
# Loudness units on the same 0–1 scale as the ripple target. Variation
# smaller than this is not stretched across the whole display.
MIN_SPAN = 0.04
BINS = 64
# The bottom histogram bin is digital silence. A quiet but nonzero song
# starts in the next bin and can still reach the middle of the display.
NOISE_FLOOR = 1.0 / BINS
# About 0.8 s at the 30 Hz audio clock before percentiles are trusted.
MIN_SAMPLES = 24
# Full ripple amplitude at level 1 and beat 0. Display 1.0 uses this height
# and display 0.15 uses 15% of it, matching update_ripple's expression.
REFERENCE_AMPLITUDE = 0.20 + 12.5 + 10.0 * (0.98 ** 1.5)
_MAX_GAIN = 160.0


def song_wave_loudness(peak, vocal=0.0, *, playing=True):
    """Instantaneous ripple target. None means the meter is unavailable."""
    if not playing:
        return 0.0
    if peak is None:
        return None
    try:
        peak_value = float(peak)
        vocal_value = float(vocal)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(peak_value):
        return None
    target = math.sqrt(max(0.0, min(1.0, peak_value)))
    if math.isfinite(vocal_value):
        target = min(1.0, target + 1.4 * max(0.0, min(1.0, vocal_value) - 0.02))
    return target


def map_song_wave_display(value, low, high):
    """Stretch one loudness sample into the fixed display range."""
    try:
        sample = float(value)
        floor = float(low)
        ceiling = float(high)
    except (TypeError, ValueError):
        return DISPLAY_LOW
    if not math.isfinite(sample) or not math.isfinite(floor) or not math.isfinite(ceiling):
        return DISPLAY_LOW
    if ceiling < NOISE_FLOOR and floor < NOISE_FLOOR:
        return DISPLAY_LOW
    span = ceiling - floor
    if span < MIN_SPAN:
        # A flat song stays near the middle of its own level and still has
        # a bounded slope, instead of exploding or collapsing to a line.
        middle = (floor + ceiling) * 0.5
        floor = middle - MIN_SPAN * 0.5
        span = MIN_SPAN
    raw = (sample - floor) / span
    if raw < 0.0:
        raw = 0.0
    elif raw > 1.0:
        raw = 1.0
    return DISPLAY_LOW + (DISPLAY_HIGH - DISPLAY_LOW) * raw


class SongWaveDisplay:
    """Whole-track histogram for the song currently being heard."""

    def __init__(self):
        self.track_id = ""
        self.counts = [0] * BINS
        self.total = 0
        self.level = DISPLAY_LOW
        self.active = False
        self.updated_at = None

    def observe(self, track_id, loudness, now, *, playing=True):
        now = float(now)
        previous = self.updated_at
        dt = 1.0 / 30.0 if previous is None else max(0.0, min(0.25, now - previous))
        self.updated_at = now
        track_id = str(track_id or "")
        if track_id != self.track_id:
            self.track_id = track_id
            self.counts = [0] * BINS
            self.total = 0
        sample = loudness
        try:
            sample = None if loudness is None else float(loudness)
        except (TypeError, ValueError):
            sample = None
        audible = bool(playing and track_id and sample is not None and math.isfinite(sample))
        self.active = audible
        if not audible:
            return self.level
        sample = max(0.0, min(1.0, sample))
        self._push(sample)
        low, high = self.percentiles()
        target = map_song_wave_display(sample, low, high)
        tau = 0.12 if target > self.level else 0.28
        self.level += (target - self.level) * -math.expm1(-dt / tau) if dt > 0.0 else 0.0
        self.level = max(DISPLAY_LOW, min(DISPLAY_HIGH, self.level))
        return self.level

    def percentiles(self):
        if self.total <= 0:
            return 0.0, 0.0
        if self.total < MIN_SAMPLES:
            return self._mean(), self._mean()
        return self._quantile(LOW_QUANTILE), self._quantile(HIGH_QUANTILE)

    def displacement_gain(self, amplitude):
        """Scale an absolute ripple pose onto this song's display height."""
        if not self.active:
            return 1.0
        try:
            current = float(amplitude)
        except (TypeError, ValueError):
            current = 0.0
        if not math.isfinite(current) or current < 1e-4:
            current = 1e-4
        gain = (REFERENCE_AMPLITUDE * self.level) / current
        if not math.isfinite(gain) or gain < 0.0:
            return 1.0
        return min(gain, _MAX_GAIN)

    def bar_levels(self, levels):
        """Keep spectral ratios and give the tallest bar this song's height."""
        shown = []
        for level in levels:
            try:
                value = float(level)
            except (TypeError, ValueError):
                value = 0.0
            if not math.isfinite(value) or value < 0.0:
                value = 0.0
            shown.append(min(1.0, value))
        if not self.active or not shown:
            return tuple(shown)
        peak = max(shown)
        if peak <= 1e-6:
            return tuple(shown)
        scale = self.level / peak
        return tuple(min(1.0, value * scale) for value in shown)

    def _push(self, sample):
        index = min(BINS - 1, int(sample * BINS))
        self.counts[index] += 1
        self.total += 1
        if self.total > 20000:
            self.counts = [count // 2 for count in self.counts]
            self.total = sum(self.counts)

    def _mean(self):
        if self.total <= 0:
            return 0.0
        weighted = sum((index + 0.5) / BINS * count for index, count in enumerate(self.counts))
        return weighted / self.total

    def _quantile(self, quantile):
        threshold = max(1.0, float(quantile) * self.total)
        accumulated = 0
        for index, count in enumerate(self.counts):
            accumulated += count
            if accumulated >= threshold:
                return (index + 0.5) / BINS
        return (BINS - 0.5) / BINS
