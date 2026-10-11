"""Bounded actual-volume history and short-lived user intents, without I/O."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import math
import threading
import time

from api.audio_routes import AudioRouteDevice


def output_volume_target(value):
    """An absolute device command in the UI's integer-percent units.

    This applies only to commands. Actual endpoint/sender readback retains its
    precision and remains authoritative; there is no additional Qt gain.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        scalar = float(value)
    except OverflowError:
        return None
    if not math.isfinite(scalar) or not 0 <= scalar <= 1:
        return None
    return math.floor(scalar * 100 + .5) / 100


@dataclass(frozen=True)
class _VolumeIntent:
    value: float
    fence: object
    expires_at: float


class AudioOutputVolumeHistory:
    """Remember confirmed positive scalars; intents never become device facts."""

    def __init__(self, max_devices=64, intent_seconds=2.0, clock=time.monotonic):
        if isinstance(max_devices, bool) or not isinstance(max_devices, int) or max_devices < 1:
            raise ValueError("max_devices must be a positive integer")
        if not isinstance(intent_seconds, (int, float)) or not math.isfinite(intent_seconds) or intent_seconds <= 0:
            raise ValueError("intent_seconds must be finite and positive")
        self._limit, self._seconds, self._clock = max_devices, float(intent_seconds), clock
        self._history: OrderedDict[str, float] = OrderedDict()
        self._intents: OrderedDict[str, _VolumeIntent] = OrderedDict()
        self._lock = threading.RLock()

    @staticmethod
    def _scalar(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            result = float(value)
        except OverflowError:
            return None
        return result if math.isfinite(result) and 0 <= result <= 1 else None

    def _expire(self, now):
        for identity, intent in tuple(self._intents.items()):
            if now >= intent.expires_at:
                del self._intents[identity]

    def _trim(self, values):
        while len(values) > self._limit:
            values.popitem(last=False)

    def observe(self, device: AudioRouteDevice, fence):
        """Consume real readback and retire confirmed or stale session intents."""
        with self._lock:
            self._expire(self._clock())
            actual = self._scalar(device.volume)
            if actual is not None and actual > 0:
                self._history[device.id] = actual
                self._history.move_to_end(device.id)
                self._trim(self._history)
            audible = 0 if device.volume_muted else actual
            intent = self._intents.get(device.id)
            if intent is not None and (intent.fence != fence or audible is not None
                    and math.isclose(audible, intent.value, abs_tol=.005)):
                del self._intents[device.id]

    def request(self, device_id: str, value: float, fence):
        """Track an accepted request for rapid toggles, without storing readback."""
        scalar = self._scalar(value)
        if scalar is None:
            raise ValueError("volume must be a scalar between zero and one")
        with self._lock:
            now = self._clock()
            self._expire(now)
            self._intents[device_id] = _VolumeIntent(scalar, fence, now + self._seconds)
            self._intents.move_to_end(device_id)
            self._trim(self._intents)

    def toggle(self, device: AudioRouteDevice, fence) -> float:
        """Mute, or restore the last real positive volume (otherwise 30%)."""
        with self._lock:
            self.observe(device, fence)
            intent = self._intents.get(device.id)
            audible = intent.value if intent is not None else 0 if device.volume_muted else self._scalar(device.volume)
            target = (output_volume_target(self._history.get(device.id, .3)) or .3) if audible == 0 else 0.0
            self.request(device.id, target, fence)
            return target

    def discard(self, device_id: str, fence=None, value=None):
        """Retire a rejected intent; an older failure cannot erase a newer value."""
        with self._lock:
            intent = self._intents.get(device_id)
            if (intent is not None and (fence is None or intent.fence == fence)
                    and (value is None or intent.value == value)):
                del self._intents[device_id]

    def reset_intents(self):
        """Fence a session transition while retaining bounded device history."""
        with self._lock:
            self._intents.clear()
