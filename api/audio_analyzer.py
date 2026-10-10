from __future__ import annotations

import math
import random
import threading
import time
import warnings
from collections import deque

from api.models import AudioFrame


CAPTURE_SAMPLE_RATE = 44100
CAPTURE_READ_FRAMES = 1024
CAPTURE_BUFFER_FRAMES = 4096
OUTPUT_CHECK_INTERVAL_SECONDS = 2.0
DISCONTINUITY_WINDOW_SECONDS = 2.0
DISCONTINUITY_LIMIT = 3


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
        self._stop_event = threading.Event()
        self._capture_active = False
        self._capture_device_id = ""
        self._suspended_device_id = ""
        self._capture_discontinuities = 0
        self._capture_restarts = 0
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
            "capture_active": bool(getattr(self, "_capture_active", False)),
            "capture_device_id": str(getattr(self, "_capture_device_id", "")),
            "suspended_device_id": str(getattr(self, "_suspended_device_id", "")),
            "capture_discontinuities": int(getattr(self, "_capture_discontinuities", 0)),
            "capture_restarts": int(getattr(self, "_capture_restarts", 0)),
            "capture_buffer_frames": CAPTURE_BUFFER_FRAMES,
        }

    def stop(self):
        self._running = False
        stop_event = getattr(self, "_stop_event", None)
        if stop_event is not None:
            stop_event.set()
        if self._status != "unavailable":
            self._status = "stopped"
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.2)

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

            while self._running:
                speaker = sc.default_speaker()
                endpoint_id = speaker.id
                self._capture_device_id = str(endpoint_id)
                if str(getattr(self, "_suspended_device_id", "")) == str(endpoint_id):
                    self._wait_for_output_change(sc, endpoint_id)
                    continue
                # Match the render endpoint by its unique WASAPI ID. A headset's
                # output and real microphone can share a friendly name; selecting
                # by name may open the microphone and switch Bluetooth to hands-free.
                loopback = sc.get_microphone(id=endpoint_id, include_loopback=True)
                if not getattr(loopback, "isloopback", False):
                    raise RuntimeError("default speaker did not resolve to a loopback endpoint")
                try:
                    self._capture_endpoint(sc, loopback, endpoint_id, SoundcardRuntimeWarning, np)
                except Exception as exc:
                    if not self._running:
                        break
                    self._suspend_capture(endpoint_id, str(exc))
                finally:
                    self._capture_active = False
                if self._running and str(getattr(self, "_suspended_device_id", "")) == str(endpoint_id):
                    # An optional visualizer must not keep opening a failing
                    # Bluetooth endpoint every provider retry. Remain on its
                    # existing visual fallback until the output actually changes.
                    self._wait_for_output_change(sc, endpoint_id)
                if self._running:
                    self._capture_restarts = int(getattr(self, "_capture_restarts", 0)) + 1
        except Exception as exc:
            self._last_error = str(exc)
            self._status = "unavailable"
            self._running = False

    def _capture_endpoint(self, sc, loopback, endpoint_id, warning_type, np):
        recent_discontinuities: deque[float] = deque()
        next_output_check = time.monotonic() + OUTPUT_CHECK_INTERVAL_SECONDS
        # This is a background meter, not a low-latency audio client. Soundcard's
        # default buffer is a device period (often about 10 ms); give capture
        # roughly 93 ms of headroom without changing its 1024-frame reads.
        with loopback.recorder(
            samplerate=CAPTURE_SAMPLE_RATE,
            channels=2,
            blocksize=CAPTURE_BUFFER_FRAMES,
            exclusive_mode=False,
        ) as recorder:
            self._capture_active = True
            self._suspended_device_id = ""
            self._last_error = ""
            self._status = "capturing"
            while self._running:
                with warnings.catch_warnings(record=True) as captured_warnings:
                    warnings.filterwarnings("always", category=warning_type, message="data discontinuity.*")
                    block = recorder.record(numframes=CAPTURE_READ_FRAMES)
                if not self._running:
                    break
                now = time.monotonic()
                discontinuities = [
                    item for item in captured_warnings
                    if issubclass(item.category, warning_type) and str(item.message).startswith("data discontinuity")
                ]
                for item in captured_warnings:
                    if item not in discontinuities:
                        warnings.warn_explicit(item.message, item.category, item.filename, item.lineno)
                if discontinuities:
                    self._capture_discontinuities = int(getattr(self, "_capture_discontinuities", 0)) + len(discontinuities)
                    recent_discontinuities.extend(now for _item in discontinuities)
                while recent_discontinuities and now - recent_discontinuities[0] > DISCONTINUITY_WINDOW_SECONDS:
                    recent_discontinuities.popleft()
                if len(recent_discontinuities) >= DISCONTINUITY_LIMIT:
                    self._suspend_capture(endpoint_id, "repeated loopback data discontinuity; live analysis suspended")
                    break
                frame = self._analyze_block(block, CAPTURE_SAMPLE_RATE, np)
                if frame is not None:
                    self._frame = frame
                    self._last_real_frame_at = now
                if now >= next_output_check:
                    next_output_check = now + OUTPUT_CHECK_INTERVAL_SECONDS
                    if sc.default_speaker().id != endpoint_id:
                        # Leave the old context before opening the new endpoint.
                        self._last_real_frame_at = 0.0
                        break

    def _suspend_capture(self, endpoint_id, reason: str):
        self._suspended_device_id = str(endpoint_id)
        self._status = "suspended"
        self._last_error = reason
        self._last_real_frame_at = 0.0

    def _wait_for_output_change(self, sc, endpoint_id):
        while self._running:
            stop_event = getattr(self, "_stop_event", None)
            if stop_event is not None:
                if stop_event.wait(OUTPUT_CHECK_INTERVAL_SECONDS):
                    break
            else:
                time.sleep(OUTPUT_CHECK_INTERVAL_SECONDS)
            if not self._running:
                break
            try:
                if sc.default_speaker().id != endpoint_id:
                    return
            except Exception:
                # A disconnected headset can briefly leave no default output.
                # Enumeration is safe; do not open a stream until it changes.
                continue

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
