"""Provider-owned opt-in capture lifecycle and immutable visual projection."""
from __future__ import annotations

import threading
import time
from dataclasses import replace

from api.capture.position import PositionModel
from api.capture.smtc import SmtcCollector, SmtcSessionSource


class CaptureRuntime:
    """Own one session binding on the provider's background asyncio loop.

    Only ``project``/``diagnostics`` cross into UI/background callers; they use
    a short model lock, never WinRT/UIA I/O. Native callbacks, rebinding and
    teardown belong to the provider loop. The legacy provider remains separate.
    """

    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self._lock = threading.Lock()
        self.model = PositionModel()
        self.collector: SmtcCollector | None = None
        self.source: SmtcSessionSource | None = None
        self.identity: tuple[str, str] | None = None
        self._valid = False
        self._closed = False

    async def bind(self, session, track, matches):
        if self._closed:
            return
        identity = (str(session.source_app_user_model_id), track.track_id)
        if self.identity == identity and self.source is not None and self.source.session is session:
            return
        if self.collector is not None:
            await self.collector.close()
        with self._lock:
            if self.identity != identity:
                self.model = PositionModel()
                self._valid = False
            self.identity = identity
        self.source = SmtcSessionSource(session, track.track_id, matches, clock=self.clock)
        self.collector = SmtcCollector(self.source, self._publish)
        await self.collector.collect_once()
        self.collector.start()

    def _publish(self, sample):
        with self._lock:
            self._valid = sample is not None and sample.identity == self.identity
            if self._valid:
                self.model.observe(sample)

    def project(self, data, now=None):
        """Replace only matching external state; never project another session."""
        track = data.track
        identity = (track.source_app_id, track.track_id)
        with self._lock:
            snapshot = self.model.snapshot(self.clock() if now is None else now) if self._valid else None
        if snapshot is None or snapshot.identity != identity or str(track.track_id).startswith('owned:'):
            return data
        return replace(data, track=replace(track, position_seconds=snapshot.position_seconds,
                                            is_playing=snapshot.is_playing,
                                            position_source='capture-model', position_confidence=0.95,
                                            position_sampled_at=snapshot.sampled_at,
                                            position_observed_seconds=snapshot.observed_seconds))

    def diagnostics(self):
        with self._lock:
            result = dict(enabled=True, valid=self._valid, identity=self.identity,
                          reanchors=self.model.reanchors,
                          position_error_seconds=self.model.last_error_seconds)
        result['smtc'] = self.collector.diagnostics() if self.collector is not None else {}
        return result

    async def close(self):
        self._closed = True
        with self._lock:
            self._valid = False
        if self.collector is not None:
            await self.collector.close()
