"""Sea-only audio envelopes, travelling waves, and local ripple propagation."""
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
    """A small one-dimensional water surface updated at the audio tick."""

    def __init__(self, point_count=49):
        self.point_count = point_count
        self.peaks = [0.0] * 3
        self.bands = [0.0] * 3
        self.average = 0.0
        self.impulse = 0.0
        self.updated_at = None
        self.vocal_peak = 0.0
        self.vocal = 0.0
        self.vocal_average = 0.0
        self.vocal_impulse = 0.0
        self.swell = 0.0  # compatibility diagnostic; surface is now a chain
        self.surface = [0.0] * point_count
        self.velocity = [0.0] * point_count
        self._onset_at = -10.0
        self._onset_index = point_count // 2
        self._time = 0.0
        self.water_level_lift = 0.0
        self.water_level_target = 0.0

    def update(self, bands, now, vocal=0.0, intensity=None):
        dt = 1 / 60 if self.updated_at is None else max(0.0, min(0.25, now - self.updated_at))
        self.updated_at = now
        self._time += dt
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
        excess = max(0.0, low - self.average * 1.15 - 0.08)
        self.average = _follow(self.average, low, dt, 0.45)
        self.impulse = max(excess, self.impulse * 2 ** (-dt / 0.16))

        active_vocal = intensity == "vocal"
        mode_drive = vocal if active_vocal else low
        extra = (self.vocal * 8.0 + self.vocal_impulse * 4.0) if active_vocal else self.impulse * 7.0
        self.water_level_target = max(0.0, min(32.0, mode_drive * 24.0 + extra))
        self.water_level_lift = _follow(self.water_level_lift, self.water_level_target, dt, 0.09)

        # A new onset drops a local disturbance into alternating positions. The
        # refractory period is short enough for syllables, but prevents chatter.
        drive = self.vocal_impulse if active_vocal else self.impulse
        if drive > 0.055 and self._time - self._onset_at > 0.12:
            self._onset_index = (self._onset_index + (13 if self._onset_index % 2 else -17)) % self.point_count
            self.velocity[self._onset_index] -= 2.0 * drive
            self._onset_at = self._time

        # Damped spring chain. Neighbor spread makes the disturbance travel;
        # substeps keep the result stable and independent of display cadence.
        steps = max(1, math.ceil(dt / 0.006))
        step = dt / steps
        for _ in range(steps):
            old = self.surface
            next_surface = old[:]
            for i in range(self.point_count):
                left = old[i - 1] if i else old[1]
                right = old[i + 1] if i + 1 < self.point_count else old[-2]
                acceleration = (-18.0 * old[i] - 3.8 * self.velocity[i]
                                + 30.0 * (left + right - 2.0 * old[i]))
                self.velocity[i] += acceleration * step
                next_surface[i] = old[i] + self.velocity[i] * step
            self.surface = next_surface

    def displacement(self, pos, phase, intensity):
        mode = sea_intensity(intensity)
        if mode == "vocal":
            low, mid, high = self.bands
            envelope = self.vocal
            onset = self.vocal_impulse
            amplitudes = (18.0 * envelope, 12.0 * envelope, 4.0 * envelope)
        else:
            low, mid, high = self.bands
            strength = INTENSITIES[mode]
            amplitudes = (11.0 * low * strength, 7.0 * mid * strength, 2.8 * high * strength)
            onset = self.impulse
        # Long waves run faster than short waves: a compact dispersion-inspired
        # set of phase speeds creates visible horizontal motion.
        travelling = (
            amplitudes[0] * math.sin(pos * math.tau * 0.85 - self._time * 2.5 + phase * 0.4)
            + amplitudes[1] * math.sin(pos * math.tau * 1.75 - self._time * 3.8 - phase * 0.3)
            + amplitudes[2] * math.sin(pos * math.tau * 3.7 - self._time * 5.5 + phase * 0.2)
        )
        point_index = int(round(max(0.0, min(1.0, pos)) * (self.point_count - 1)))
        ripple = self.surface[point_index]
        local_onset = onset * 1.0 * math.exp(-((point_index - self._onset_index) / 5.0) ** 2)
        # Only a small mean lift is shared by all points; the spring chain and
        # travelling waves provide the visible surface motion.
        travelling += ripple * (1.0 + 0.35 * onset) + local_onset
        if mode == "vocal":
            travelling += onset * 5.0 * math.sin(pos * math.tau * 1.45 - self._time * 3.2)
        lift = self.water_level_lift
        if mode == "vocal":
            lift = max(lift, self.vocal * 20.0)
        else:
            lift = max(lift, low * 16.0 * INTENSITIES[mode] * 0.55)
        total = lift + travelling
        if total >= 0.0:
            limit = 49.0 if mode != "vocal" else 44.0
            return limit * math.tanh(total / limit)
        return max(-3.0, lift + 3.0 * math.tanh(travelling / 3.0))
