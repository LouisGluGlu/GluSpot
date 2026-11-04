"""Four absolute spectral envelopes for the collapsed sea island.

The provider already supplies 24 logarithmic FFT bands (50–10000 Hz).
Keep this view independent of the sea's automatic gain: quiet input must
stay quiet, even after a long quiet passage. Painting never advances audio.
"""
from __future__ import annotations

import math


class IslandAudioBars:
    def __init__(self):
        self.levels = (0.0,) * 4
        self.updated_at = None

    def update(self, spectrum, now, *, bands=(), vocal=0.0, is_playing=True):
        dt = 1.0 / 30.0 if self.updated_at is None else max(0.0, min(.25, now - self.updated_at))
        self.updated_at = now
        targets = (0.0,) * 4
        if is_playing:
            if len(spectrum) == 24:
                # Six adjacent log bands per column; absolute compression,
                # no oscillators, random motion or quiet-signal normalization.
                targets = tuple(min(1.0, math.log1p(max(0.0,
                    max(spectrum[i:i + 6]) - .00025) * 96.0) / math.log(25.0))
                    for i in range(0, 24, 6))
            elif len(bands) == 3:
                # Legacy real providers have three bands and a center-vocal
                # band. A peak-only meter supplies no spectral information.
                targets = tuple(max(0.0, min(1.0, (v - .006) / .994))
                                for v in (bands[0], bands[1], vocal, bands[2]))
        self.levels = tuple(old + (target - old) * -math.expm1(
            -dt / (.055 if target > old else .220))
            for old, target in zip(self.levels, targets))

