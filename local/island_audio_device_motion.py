"""Inline island device motion without device I/O or changes to lyric time.

The notice lifetime uses real monotonic time. Its entering, leaving and
content transitions use the shared bounded visual clock, so a delayed GUI
frame neither jumps the pose nor keeps an expired notice open for seconds.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
import time

from local.music_lyric_motion import MotionSimulationClock


@dataclass(frozen=True)
class IslandDevicePayload:
    id: str
    name: str
    kind: str
    battery_percent: int | None = None


@dataclass(frozen=True)
class IslandDevicePose:
    payload: IslandDevicePayload
    previous: IslandDevicePayload | None = None
    reveal: float = 0.0
    content_blend: float = 1.0


def reliable_device_battery(device: object, now: float | None = None) -> int | None:
    """Return only recent, exact-device Windows driver cache evidence.

    ``now`` and the observation are Unix timestamps. Reading a driver cache
    does not establish when the accessory measured its battery; the source
    remains explicit on the provider model rather than claiming live charge.
    """
    percent = getattr(device, "battery_percent", None)
    source = getattr(device, "battery_source", "")
    observed = getattr(device, "battery_observed_at", None)
    if (type(percent) is not int or not 0 <= percent <= 100
            or source != "windows-driver-cache"
            or isinstance(observed, bool)
            or not isinstance(observed, (int, float))):
        return None
    current = time.time() if now is None else now
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        return None
    try:
        observed_value = float(observed)
        current_value = float(current)
    except (ValueError, TypeError, OverflowError):
        return None
    if not math.isfinite(observed_value) or not math.isfinite(current_value):
        return None
    if observed_value <= 0.0 or not 0.0 <= current_value - observed_value <= 30.0:
        return None
    return percent


def _smooth(value: float) -> float:
    value = min(1.0, max(0.0, value))
    return value * value * (3.0 - 2.0 * value)


def _motion_time(now: float) -> float:
    value = float(now)
    if not math.isfinite(value):
        raise ValueError("Island device motion requires a finite monotonic time")
    return value


class IslandAudioDeviceMotion:
    """One transient pose; new identities join its current displayed reveal.

    Same-device metadata updates never extend the five-second lifetime. A
    replacement arriving during a content fade waits at most that short fade
    before joining, keeping both visible layers continuous. Further arrivals
    replace the pending payload; this is a bounded latest-event slot, not a
    queue of stale notices. Hide and surface changes call ``clear`` explicitly.
    """

    HOLD_SECONDS = 5.0
    ENTER_SECONDS = 0.32
    EXIT_SECONDS = 0.32
    CONTENT_SECONDS = 0.24

    def __init__(self):
        self._clock = MotionSimulationClock()
        self._pose: IslandDevicePose | None = None
        self._pending: IslandDevicePayload | None = None
        self._latest_id = ""
        self._deadline = 0.0
        self._phase = ""
        self._phase_from = 0.0
        self._phase_elapsed = 0.0
        self._content_elapsed = 0.0
        self._wall_now = 0.0

    @property
    def pose(self) -> IslandDevicePose | None:
        return self._pose

    @property
    def active(self) -> bool:
        return self._pose is not None

    def announce(self, payload: IslandDevicePayload, now: float) -> bool:
        """Start/join a notice; return whether this is a new device identity."""
        now = _motion_time(now)
        if self._pose is not None and payload.id == self._latest_id:
            if self._pending is not None:
                self._pending = payload
            else:
                self._pose = replace(self._pose, payload=payload)
            return False

        self._latest_id = payload.id
        self._wall_now = now
        self._deadline = now + self.HOLD_SECONDS
        self._clock.reset(now)
        self._phase = "enter"
        self._phase_elapsed = 0.0
        self._phase_from = self._pose.reveal if self._pose is not None else 0.0
        if self._pose is None:
            self._pose = IslandDevicePose(payload)
        elif self._pose.payload.id == payload.id:
            self._pending = None
            self._pose = replace(self._pose, payload=payload)
        elif self._pose.previous is not None and self._pose.content_blend < 1.0:
            self._pending = payload
        else:
            self._begin_content_fade(payload)
        return True

    def _begin_content_fade(self, payload: IslandDevicePayload) -> None:
        previous = self._pose.payload
        self._pose = IslandDevicePose(payload, previous, self._pose.reveal, 0.0)
        self._content_elapsed = 0.0

    def advance(self, now: float) -> bool:
        """Advance bounded transforms while expiring hold on real time."""
        now = _motion_time(now)
        if self._pose is None:
            return False
        before = self._pose
        self._wall_now = max(self._wall_now, now)
        if self._wall_now >= self._deadline and self._phase != "exit":
            self._phase = "exit"
            self._phase_from = self._pose.reveal
            self._phase_elapsed = 0.0
            self._pending = None

        elapsed = self._clock.advance(now)
        self._phase_elapsed += elapsed
        if self._phase == "exit":
            reveal = self._phase_from * (1.0 - _smooth(self._phase_elapsed / self.EXIT_SECONDS))
            if self._phase_elapsed >= self.EXIT_SECONDS:
                self.clear(now)
                return True
        elif self._phase == "enter":
            reveal = self._phase_from + (1.0 - self._phase_from) * _smooth(self._phase_elapsed / self.ENTER_SECONDS)
            if self._phase_elapsed >= self.ENTER_SECONDS:
                self._phase = "hold"
        else:
            reveal = 1.0
        self._pose = replace(self._pose, reveal=reveal)

        if self._pose.previous is not None:
            self._content_elapsed += elapsed
            blend = _smooth(self._content_elapsed / self.CONTENT_SECONDS)
            self._pose = replace(self._pose, content_blend=blend)
            if blend >= 1.0:
                self._pose = replace(self._pose, previous=None)
                if self._pending is not None and self._phase != "exit":
                    pending, self._pending = self._pending, None
                    self._begin_content_fade(pending)
        return self._pose != before

    def clear(self, now: float) -> None:
        """Drop the transient when another surface owns the island."""
        now = _motion_time(now)
        self._pose = None
        self._pending = None
        self._latest_id = ""
        self._deadline = 0.0
        self._phase = ""
        self._phase_from = 0.0
        self._phase_elapsed = 0.0
        self._content_elapsed = 0.0
        self._wall_now = now
        self._clock.reset(now)
