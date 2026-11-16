"""SMTC observation and event/poll collection on an owning asyncio thread.

No WinRT module is imported here: the provider supplies a native or fake session
from its existing background loop. Native event callbacks only wake that loop.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Callable

from api.capture.contracts import TimelineSample


def seconds(value) -> float:
    """Convert WinRT TimeSpan projections or a fake numeric seconds value."""
    if value is None:
        return 0.0
    total = getattr(value, 'total_seconds', None)
    return float(total()) if callable(total) else float(value)


def unix_time(value) -> float | None:
    """WinSDK projects DateTime as datetime; absent timestamps stay absent."""
    if isinstance(value, datetime):
        # WinRT DateTime is UTC. Some projection versions omit tzinfo.
        from datetime import timezone
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).timestamp()
    return float(value) if isinstance(value, (float, int)) else None


class SmtcSessionSource:
    """One identity-bound native session; all methods run on the provider loop."""

    def __init__(self, session, track_id: str, matches, *, clock=time.monotonic, wall_clock=time.time):
        self.session = session
        self.track_id = track_id
        self.session_id = str(session.source_app_user_model_id)
        self.matches = matches
        self.clock = clock
        self.wall_clock = wall_clock
        self._requests: set[str] = set()

    async def read(self) -> TimelineSample | None:
        if not await self.matches(self.session):
            return None
        timeline = self.session.get_timeline_properties()
        playback = self.session.get_playback_info()
        status = getattr(playback, 'playback_status', None)
        # WinSDK IntEnum and plain mock integers both expose the GSMTC value 4.
        playing = getattr(status, 'value', status) == 4
        raw_rate = getattr(playback, 'playback_rate', None)
        rate = float(raw_rate) if raw_rate is not None else 1.0
        return TimelineSample(self.session_id, self.track_id,
                              seconds(getattr(timeline, 'position', None)),
                              self.clock(), self.wall_clock(),
                              updated_at=unix_time(getattr(timeline, 'last_updated_time', None)),
                              is_playing=playing, rate=rate,
                              start_seconds=seconds(getattr(timeline, 'start_time', None)),
                              end_seconds=seconds(getattr(timeline, 'end_time', None)))

    async def seek(self, position_seconds: float, *, request_id: str) -> bool:
        if request_id in self._requests or not await self.matches(self.session):
            return False
        self._requests.add(request_id)
        # Keep idempotency memory bounded to this short-lived binding. Eviction
        # is safe because the executor never reuses completed request IDs.
        if len(self._requests) > 256:
            self._requests = {request_id}
        return bool(await self.session.try_change_playback_position_async(
            max(0, round(position_seconds * 10_000_000))))


class SmtcCollector:
    """Coalesce native events and poll at 2 Hz, with bounded diagnostic counters."""

    EVENTS = ('timeline_properties_changed', 'playback_info_changed', 'media_properties_changed')

    def __init__(self, source: SmtcSessionSource, publish: Callable, *, poll_seconds=0.5, clock=time.monotonic):
        self.source = source
        self.publish = publish
        self.poll_seconds = max(0.5, float(poll_seconds))
        self.clock = clock
        self._loop = None
        self._wake = asyncio.Event()
        self._task = None
        self._tokens = []
        self._closed = False
        self._event_at: float | None = None
        self.stats = dict(events=0, polls=0, samples=0, errors=0, subscriptions=0,
                          event_delay_seconds=0.0, max_event_delay_seconds=0.0,
                          event_latency_samples=0, started_at=0.0)

    def start(self):
        self._loop = asyncio.get_running_loop()
        self.stats['started_at'] = self.clock()
        for event in self.EVENTS:
            add = getattr(self.source.session, 'add_' + event, None)
            remove = getattr(self.source.session, 'remove_' + event, None)
            if callable(add) and callable(remove):
                try:
                    token = add(self._on_event)
                    self._tokens.append((remove, token))
                except Exception:
                    self.stats['errors'] += 1
        self.stats['subscriptions'] = len(self._tokens)
        self._task = self._loop.create_task(self._run())

    def _on_event(self, *_args):
        arrived = self.clock()
        if self._loop is not None and not self._closed:
            try:
                self._loop.call_soon_threadsafe(self._signal, arrived)
            except RuntimeError:
                pass  # Shutdown raced a final native callback.

    def _signal(self, arrived):
        if self._closed:
            return
        self.stats['events'] += 1
        if self._event_at is None:
            self._event_at = arrived
        self._wake.set()

    async def collect_once(self, *, event_at=None):
        try:
            sample = await asyncio.wait_for(self.source.read(), timeout=0.8)
            if sample is not None:
                self.publish(sample)
                self.stats['samples'] += 1
            else:
                self.publish(None)  # Metadata changed; stop projecting old song.
            if event_at is not None:
                delay = max(0.0, self.clock() - event_at)
                self.stats['event_delay_seconds'] = delay
                self.stats['max_event_delay_seconds'] = max(delay, self.stats['max_event_delay_seconds'])
                self.stats['event_latency_samples'] += 1
        except Exception:
            self.stats['errors'] += 1
            self.publish(None)

    async def _run(self):
        while not self._closed:
            self.stats['polls'] += 1
            event_at = self._event_at
            self._event_at = None
            self._wake.clear()
            await self.collect_once(event_at=event_at)
            if self._wake.is_set():
                continue  # Events arriving during a read must trigger another read.
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass

    def diagnostics(self):
        result = dict(self.stats)
        elapsed = max(0.001, self.clock() - result['started_at'])
        result['events_per_second'] = result['events'] / elapsed
        return result

    async def close(self):
        self._closed = True
        for remove, token in self._tokens:
            try:
                remove(token)
            except Exception:
                self.stats['errors'] += 1
        self._tokens.clear()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
