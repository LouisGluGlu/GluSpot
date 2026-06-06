from __future__ import annotations

import math
import random
import threading
import time
import warnings

from api.models import AudioFrame


class LiveAudioAnalyzer:
    def __init__(self):
        self._frame = AudioFrame(0.0, 0.0, 0.0)
        self._thread: threading.Thread | None = None
        self._running = False
        self._status = "starting"
        self._last_error = ""
        self._last_real_frame_at = 0.0
        self._started_at = time.monotonic()
        self._smooth = [0.22, 0.20, 0.14]
        self._start_capture()

    def get_frame(self, is_playing: bool = True, position_seconds: float = 0.0) -> AudioFrame:
        if self._has_recent_real_frame():
            return self._frame if is_playing else self._dampen(self._frame)
        return self._estimated_frame(is_playing, position_seconds)

    def has_recent_real_frame(self) -> bool:
        return self._has_recent_real_frame()

    def diagnostics(self) -> dict[str, object]:
        return {
            "status": self._status,
            "running": self._running,
            "has_recent_frame": self._has_recent_real_frame(),
            "last_real_frame_age": round(time.monotonic() - self._last_real_frame_at, 3) if self._last_real_frame_at else None,
            "last_error": self._last_error,
        }

    def _start_capture(self):
        self._running = True
        self._status = "starting"
        self._thread = threading.Thread(target=self._capture_loop, name="LiveAudioAnalyzer", daemon=True)
        self._thread.start()

    def _capture_loop(self):
        try:
            import numpy as np
            import soundcard as sc
            try:
                from soundcard.mediafoundation import SoundcardRuntimeWarning
            except Exception:
                SoundcardRuntimeWarning = RuntimeWarning

            speaker = sc.default_speaker()
            block_size = 1024
            self._status = "capturing"
            with sc.get_microphone(id=str(speaker.name), include_loopback=True).recorder(samplerate=44100, channels=2) as recorder:
                while self._running:
                    with warnings.catch_warnings():
                        warnings.filterwarnings("ignore", category=SoundcardRuntimeWarning, message="data discontinuity.*")
                        block = recorder.record(numframes=block_size)
                    frame = self._analyze_block(block, 44100, np)
                    if frame is not None:
                        self._frame = frame
                        self._last_real_frame_at = time.monotonic()
        except Exception as exc:
            self._last_error = str(exc)
            self._status = "unavailable"
            self._running = False

    def _analyze_block(self, block, sample_rate: int, np) -> AudioFrame | None:
        data = np.asarray(block, dtype=np.float32)
        if data.size == 0:
            return None
        if len(data.shape) > 1:
            data = data.mean(axis=1)
        data = data - float(np.mean(data))
        if float(np.max(np.abs(data))) < 0.00008:
            return None
        window = np.hanning(len(data))
        spectrum = np.abs(np.fft.rfft(data * window))
        freqs = np.fft.rfftfreq(len(data), 1.0 / sample_rate)
        low = self._band_energy(spectrum, freqs, 20, 220, np)
        mid = self._band_energy(spectrum, freqs, 220, 2200, np)
        high = self._band_energy(spectrum, freqs, 2200, 14000, np)
        values = [low, mid, high]
        normalized = []
        for index, value in enumerate(values):
            compressed = max(0.0, min(1.0, math.log1p(value * 96.0) / math.log1p(96.0)))
            self._smooth[index] = self._smooth[index] * 0.52 + compressed * 0.48
            normalized.append(self._smooth[index])
        return AudioFrame(*normalized)

    def _band_energy(self, spectrum, freqs, low_hz: int, high_hz: int, np) -> float:
        mask = (freqs >= low_hz) & (freqs < high_hz)
        if not np.any(mask):
            return 0.0
        return float(np.sqrt(np.mean(np.square(spectrum[mask]))) / max(1.0, len(spectrum)))

    def _has_recent_real_frame(self) -> bool:
        return self._last_real_frame_at > 0 and time.monotonic() - self._last_real_frame_at < 1.2

    def _dampen(self, frame: AudioFrame) -> AudioFrame:
        return AudioFrame(frame.low * 0.18, frame.mid * 0.14, frame.high * 0.10)

    def _estimated_frame(self, is_playing: bool, position_seconds: float) -> AudioFrame:
        if not is_playing:
            return AudioFrame(0.02, 0.015, 0.01)
        t = position_seconds if position_seconds > 0 else time.monotonic() - self._started_at
        low = 0.40 + math.sin(t * 2.25) * 0.18 + random.random() * 0.05
        mid = 0.34 + math.sin(t * 3.10 + 1.2) * 0.16 + random.random() * 0.05
        high = 0.22 + math.sin(t * 5.40 + 2.4) * 0.12 + random.random() * 0.06
        return AudioFrame(max(0.0, min(1.0, low)), max(0.0, min(1.0, mid)), max(0.0, min(1.0, high)))
