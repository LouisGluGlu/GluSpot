"""Immutable output state shared through the media-provider boundary.

Audio routing owns its own worker and clock. These snapshots contain no PCM,
network handles, credentials, or GUI objects.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AudioRouteDevice:
    id: str
    name: str
    kind: str = "device"
    transport: str = "windows"
    is_bluetooth: bool = False
    is_default: bool = False
    available: bool = True
    host: str = ""
    port: int = 7000
    device_id: str = ""
    # Actual endpoint scalar or confirmed current receiver volume. None means
    # this device has no usable volume evidence/control in the current session.
    volume: float | None = None
    volume_muted: bool = False
    route_state: str = "available"
    route_message: str = ""


@dataclass(frozen=True)
class AudioRouteSnapshot:
    devices: tuple[AudioRouteDevice, ...] = ()
    active_id: str = ""
    pending_id: str = ""
    state: str = "idle"
    status_message: str = ""
    revision: int = 0
    cast_latency_seconds: float = 0.0
    discovery_state: str = "idle"
    cast_source_id: str = ""
    event_serial: int = 0
    event_name: str = ""
    event_message: str = ""
    cast_latency_auto: bool = True
    cast_latency_estimate_seconds: float | None = None
    member_ids: tuple[str, ...] = ()
    pending_ids: tuple[str, ...] = ()
    group_id: str = ""
    capture_scope: str = ""
    music_source_app_id: str = ""
