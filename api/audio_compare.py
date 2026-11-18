"""Compare two recordings of what should be the same song.

Apple Music only hands out an unencrypted 30-second preview, so one side is
always a short clip. The clip is located inside the other, full recording,
then compared frame by frame. Input is 8 kHz mono float PCM (the decoder used
by the sea-level background produces exactly that). No network, no Qt.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

RATE = 8000
HOP = 800             # 10 frames a second
WINDOW = 2048
BANDS = 24
LOW_HZ, HIGH_HZ = 90.0, 3800.0

# Tuned on synthetic recordings (re-encoding noise, gain, EQ tilt, a replaced
# passage, an unrelated song); not yet checked against a large real catalog.
SAME_SCORE = 0.80      # mean frame similarity at the best offset
ALIGN_MIN = 0.45       # below this the clip was not found at all
DIFF_FRAME = 0.55      # a smoothed frame below this differs
DIFF_MIN_SECONDS = 1.0
SAME_MAX_DIFF = 0.05   # at most 5 % of the clip may differ for "same"


@dataclass(frozen=True)
class AudioVerdict:
    """same: the clip matches the full recording. Segments are in clip seconds."""

    same: bool
    score: float
    offset_seconds: float = 0.0     # where the clip starts inside the full recording
    differing: tuple[tuple[float, float], ...] = ()
    found: bool = True

    def full_segments(self) -> tuple[tuple[float, float], ...]:
        """The differing passages in the full recording's own time."""
        return tuple((start + self.offset_seconds, end + self.offset_seconds) for start, end in self.differing)


def _band_edges(window: int = WINDOW, rate: int = RATE) -> np.ndarray:
    freqs = np.fft.rfftfreq(window, 1.0 / rate)
    edges_hz = np.geomspace(LOW_HZ, HIGH_HZ, BANDS + 1)
    return np.searchsorted(freqs, edges_hz)


def features(pcm: np.ndarray) -> np.ndarray:
    """Log band energies, one row per 0.1 s, each row centred and unit length."""
    pcm = np.asarray(pcm, dtype=np.float32)
    count = max(0, (len(pcm) - WINDOW) // HOP + 1)
    if count == 0:
        return np.zeros((0, BANDS), dtype=np.float32)
    index = np.arange(WINDOW)[None, :] + HOP * np.arange(count)[:, None]
    frames = pcm[index] * np.hanning(WINDOW).astype(np.float32)[None, :]
    power = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    edges = _band_edges()
    bands = np.stack([power[:, edges[i]:max(edges[i] + 1, edges[i + 1])].sum(axis=1) for i in range(BANDS)], axis=1)
    logs = np.log10(bands + 1e-9).astype(np.float32)
    return logs


def _normalise(rows: np.ndarray) -> np.ndarray:
    """Remove each band's level (EQ, gain) over the window, then the frame mean; unit rows."""
    if len(rows) == 0:
        return rows
    rows = rows - rows.mean(axis=0, keepdims=True)
    rows = rows - rows.mean(axis=1, keepdims=True)
    norm = np.linalg.norm(rows, axis=1, keepdims=True)
    return rows / np.maximum(norm, 1e-6)


def _best_offset(clip: np.ndarray, full: np.ndarray) -> tuple[int, float]:
    """The offset (in frames) of clip inside full with the highest mean similarity."""
    n, m = len(clip), len(full)
    if n == 0 or m < n:
        return 0, 0.0
    a = _normalise(clip)
    # Coarse: each full frame centred and unit length on its own; correlate
    # every band through the FFT and sum. Then check the best peaks exactly.
    rough = full - full.mean(axis=1, keepdims=True)
    rough = rough / np.maximum(np.linalg.norm(rough, axis=1, keepdims=True), 1e-6)
    size = 1 << int(np.ceil(np.log2(m + n)))
    total = np.zeros(m - n + 1, dtype=np.float64)
    for band in range(a.shape[1]):
        corr = np.fft.irfft(np.fft.rfft(rough[:, band], size) * np.conj(np.fft.rfft(a[:, band], size)), size)
        total += corr[:m - n + 1]
    order = np.argsort(total)[::-1][:24]
    best, best_score = 0, -1.0
    for offset in order:
        score = _mean_similarity(a, _normalise(full[offset:offset + n]))
        if score > best_score:
            best, best_score = int(offset), score
    return best, float(best_score)


def _mean_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float((a * b).sum(axis=1).mean()) if len(a) else 0.0


def _segments(mask: np.ndarray, min_frames: int) -> tuple[tuple[float, float], ...]:
    found = []
    start = None
    for index, flag in enumerate(list(mask) + [False]):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            if index - start >= min_frames:
                found.append((round(start * HOP / RATE, 1), round((index * HOP + WINDOW) / RATE, 1)))
            start = None
    return tuple(found)


def compare(clip_pcm: np.ndarray, full_pcm: np.ndarray) -> AudioVerdict:
    """Is the short clip a passage of the full recording, and where does it differ?"""
    clip, full = features(clip_pcm), features(full_pcm)
    if len(clip) < 20 or len(full) < len(clip):
        return AudioVerdict(False, 0.0, found=False)
    offset, score = _best_offset(clip, full)
    seconds = round(offset * HOP / RATE, 2)
    if score < ALIGN_MIN:
        length = round(len(clip_pcm) / RATE, 1)
        return AudioVerdict(False, score, seconds, ((0.0, length),), found=False)
    a, b = _normalise(clip), _normalise(full[offset:offset + len(clip)])
    per_frame = (a * b).sum(axis=1)
    kernel = np.ones(10) / 10.0
    smooth = np.convolve(per_frame, kernel, mode="same")
    mask = smooth < DIFF_FRAME
    differing = _segments(mask, int(DIFF_MIN_SECONDS * RATE / HOP))
    share = float(mask.mean())
    same = score >= SAME_SCORE and share <= SAME_MAX_DIFF and not differing
    return AudioVerdict(same, score, seconds, () if same else differing)
