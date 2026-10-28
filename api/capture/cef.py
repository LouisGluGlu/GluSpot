"""Push-only CEF observations and independently verified NetEase seeks.

This layer translates native track IDs to the provider's existing identity.
It never projects a target into physical evidence. CdpSession owns all wire I/O;
``read`` normally consumes its binding cache, and seek readback explicitly asks
the adapter for its latest native event state without polling the playhead.
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import replace

from api.capture.contracts import TimelineSample
from api.capture.seek import SeekPolicy


CEF_SEEK_POLICY = SeekPolicy(tolerance_seconds=0.05, read_delays=(0.04, 0.04, 0.06),
                             request_timeout=0.8, read_timeout=0.15)


class CefPositionSource:
    """One metadata-bound PositionSource, fed only by Runtime.bindingCalled.

    Native identity changes invalidate this binding until the provider binds
    the replacement metadata. Samples from a previous renderer generation may
    not confirm a seek in the replacement renderer.
    """

    def __init__(self, session, *, session_id='cloudmusic.exe', track_id=None,
                 native_track_id=None, excluded_native_track_id=None,
                 duration_seconds=0.0, is_playing=False,
                 clock=time.monotonic, wall_clock=time.time, stale_seconds=2.0):
        self.session = session
        self.session_id = str(session_id)
        self.track_id = str(track_id) if track_id is not None else None
        self.native_track_id = str(native_track_id) if native_track_id is not None else None
        self.excluded_native_track_id = (str(excluded_native_track_id)
                                         if excluded_native_track_id is not None else None)
        self.duration_seconds = duration_seconds
        self.is_playing = is_playing
        self.clock = clock
        self.wall_clock = wall_clock
        self.stale_seconds = stale_seconds
        self._sample = None
        self._progress_observed_at = None
        self._progress_sequence = None
        self._state_observed_at = None
        self._generation = None
        self._closed = False
        self._listeners = set()
        self._changed = asyncio.Event()
        self._unsubscribe = session.subscribe(self._receive)
        if session.latest is not None:
            self._receive(session.latest)

    @property
    def generation(self):
        return self._generation

    @property
    def progress_observed_at(self):
        """Receipt of physical progress, unaffected by transport state edges."""
        return self._progress_observed_at

    @property
    def progress_sequence(self):
        return self._progress_sequence

    @property
    def fresh(self):
        sample = self._sample
        if self._closed or not self.session.ready or sample is None:
            return False
        age = self.clock() - sample.observed_at
        # Native progress can stop while paused. The CDP heartbeat still
        # fences this evidence; a confirmed pause does not age the playhead.
        return age >= 0 and (not sample.is_playing or age <= self.stale_seconds)

    def subscribe(self, callback):
        self._listeners.add(callback)
        return lambda: self._listeners.discard(callback)

    def _receive(self, payload):
        if self._closed:
            return
        sample = self._parse(payload)
        self._sample = sample
        if sample is None:
            self._progress_observed_at = None
            self._progress_sequence = None
            self._state_observed_at = None
        self._changed.set()
        for callback in tuple(self._listeners):
            callback(sample)

    def _parse(self, payload):
        if not isinstance(payload, dict):
            return None
        native_id = str(payload.get('trackId') or '')
        if not native_id:
            return None
        # Provider metadata can arrive before the native track event. Do not
        # relabel the previous song's cached progress as the replacement song.
        if self.native_track_id is None and native_id == self.excluded_native_track_id:
            return None
        if self.native_track_id is None:
            self.native_track_id = native_id
            self.excluded_native_track_id = None
        if native_id != self.native_track_id:
            return None
        try:
            position = float(payload['position'])
            progress_observed = float(payload['received_at'])
            state_observed = float(payload.get('state_received_at', progress_observed))
            observed = max(progress_observed, state_observed,
                           self._state_observed_at if self._state_observed_at is not None else progress_observed)
            duration = float(payload.get('duration') or self.duration_seconds)
            rate = float(payload.get('rate', 1.0))
        except (TypeError, ValueError, KeyError):
            return None
        if not all(math.isfinite(value) for value in (position, progress_observed, state_observed, observed, duration, rate)) or position < 0 or duration < 0 or rate < 0:
            return None
        self._generation = payload.get('generation')
        self._progress_observed_at = progress_observed
        self._progress_sequence = payload.get('sequence')
        self._state_observed_at = observed
        playing = payload.get('playing')
        if isinstance(playing, bool):
            self.is_playing = playing
        return TimelineSample(self.session_id, self.track_id or native_id,
                              min(position, duration) if duration > 0 else position,
                              observed, self.wall_clock(), is_playing=self.is_playing,
                              rate=rate, end_seconds=duration, source='cef')

    async def read(self):
        return self._sample if self.fresh else None

    def update_transport_state(self, is_playing):
        """Use SMTC state only when the native adapter has no verified state.

        A state edge is observed now so a long pause cannot advance the model
        on resume. The separate progress timestamp remains physical seek proof.
        """
        if not isinstance(is_playing, bool) or self.is_playing == is_playing:
            return
        self.is_playing = is_playing
        if isinstance(self.session.latest, dict) and self.session.latest.get('playing') is None:
            self._state_observed_at = self.clock()
            self._receive(dict(self.session.latest, state_received_at=self._state_observed_at))

    async def wait_sample(self, *, timeout=2.0, after=None):
        deadline = asyncio.get_running_loop().time() + timeout
        while not self._closed:
            self._changed.clear()
            sample = await self.read()
            if sample is not None and (after is None or sample.observed_at > after):
                return sample
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            try:
                await asyncio.wait_for(self._changed.wait(), remaining)
            except asyncio.TimeoutError:
                return None
        return None

    def close(self):
        self._closed = True
        self._unsubscribe()
        self._listeners.clear()
        self._changed.set()

    def diagnostics(self):
        return dict(ready=self.session.ready, native_track_id=self.native_track_id,
                    excluded_native_track_id=self.excluded_native_track_id,
                    generation=self._generation,
                    progress_observed_at=self._progress_observed_at,
                    sample_age_seconds=(max(0.0, self.clock() - self._sample.observed_at)
                                        if self._sample is not None else None))


class CefSeekBackend:
    """One mutation per request ID, with fresh native progress as readback."""

    def __init__(self, source: CefPositionSource):
        self.source = source
        self._requests = set()
        self._requested_at = None
        self._request_generation = None
        self._request_sequence = None

    async def read(self):
        if self._requested_at is None:
            return await self.source.read()
        payload = await self.source.session.readback()
        if payload is not None:
            self.source._receive(payload)
        sample = await self.source.read()
        progress_at = self.source.progress_observed_at
        if sample is None or progress_at is None or progress_at <= self._requested_at:
            return None
        sequence = self.source.progress_sequence
        if self._request_sequence is not None and (sequence is None or sequence <= self._request_sequence):
            return None
        if self.source.generation != self._request_generation:
            return None
        # The model may observe a later pause/resume edge. Verification uses
        # only the position's own physical receipt time.
        return replace(sample, observed_at=progress_at)

    async def seek(self, position_seconds, *, request_id):
        if request_id in self._requests or not math.isfinite(position_seconds) or position_seconds < 0:
            return False
        sample = await self.source.read()
        if sample is None or self.source.native_track_id is None:
            return False
        self._requests.add(request_id)
        if len(self._requests) > 256:
            self._requests = {request_id}
        self._requested_at = self.source.clock()
        self._request_generation = self.source.generation
        self._request_sequence = self.source.progress_sequence
        if sample.end_seconds > sample.start_seconds:
            position_seconds = min(sample.end_seconds, position_seconds)
        return bool(await self.source.session.seek(self.source.native_track_id, request_id,
                                                   max(sample.start_seconds, position_seconds)))
