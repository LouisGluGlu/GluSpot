"""Platform-neutral contracts for external position, seek, and calibration.

Positions/rates use seconds; ``observed_at`` uses a monotonic clock and
``updated_at``/``wall_time`` use Unix seconds. A track identity includes the
selected session's identity so identical songs in two players remain isolated.
Implementations perform native I/O on their owning background thread only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TimelineSample:
    """One independent player observation, before prediction or UI optimism."""

    session_id: str
    track_id: str
    position_seconds: float
    observed_at: float
    wall_time: float
    updated_at: float | None = None
    is_playing: bool = False
    rate: float = 1.0
    start_seconds: float = 0.0
    end_seconds: float = 0.0
    source: str = "smtc"

    @property
    def identity(self) -> tuple[str, str]:
        return self.session_id, self.track_id


class PositionSource(Protocol):
    """Read raw player state asynchronously, without projecting a playhead."""

    async def read(self) -> TimelineSample | None: ...


class SeekBackend(PositionSource, Protocol):
    """Issue at most one physical operation for each idempotency key.

    A true response is an acknowledgement, never proof that playback moved.
    ``read`` must return independent physical state, not an optimistic target.
    """

    async def seek(self, position_seconds: float, *, request_id: str) -> bool: ...


class Calibrator(Protocol):
    """Obtain optional, bounded-cost physical evidence outside the UI thread."""

    async def calibrate(self) -> TimelineSample | None: ...
