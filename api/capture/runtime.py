"""Provider-owned opt-in capture lifecycle and immutable visual projection."""
from __future__ import annotations

import asyncio
import secrets
import threading
import time
from dataclasses import replace

from api.capture.position import PositionModel
from api.capture.seek import SeekExecutor, seek_policy
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
        self._last_received_at = 0.0
        self._closed = False
        self.executor = SeekExecutor(clock=clock)
        self.fallback = None
        self.policy = seek_policy("")
        self._optimistic = None
        self._seek_task = None
        self.seek_attempts = 0
        self.seek_successes = 0
        self._uia_worker = None
        self._calibration_task = None

    async def bind(self, session, track, matches):
        if self._closed:
            return
        identity = (str(session.source_app_user_model_id), track.track_id)
        if self.identity == identity and self.source is not None and self.source.session is session:
            if self.fallback is not None:
                self.fallback.track = track
            return
        if self.collector is not None:
            await self.collector.close()
        with self._lock:
            if self.identity != identity:
                self.model = PositionModel()
                self._valid = False
            self.identity = identity
        if self._calibration_task is not None:
            self._calibration_task.cancel()
            self._calibration_task = None
        if self.fallback is not None:
            self.fallback.close()
            self.fallback = None
        hint = identity[0].casefold()
        service = ('netease' if any(value in hint for value in ('cloudmusic', 'netease', 'orpheus'))
                   else 'qq' if 'qqmusic' in hint else '')
        if service:
            from api.capture.uia import StaWorker, UiaBackend
            if self._uia_worker is None:
                self._uia_worker = StaWorker()
            self.fallback = UiaBackend(service, track, worker=self._uia_worker, clock=self.clock)
        self.policy = seek_policy(identity[0])
        self.source = SmtcSessionSource(session, track.track_id, matches, clock=self.clock)
        self.collector = SmtcCollector(self.source, self._publish)
        await self.collector.collect_once()
        self.collector.start()

    def _publish(self, sample):
        self._last_received_at = self.clock()
        with self._lock:
            self._valid = sample is not None and sample.identity == self.identity
            if self._valid:
                if sample.source == 'uia':
                    self.model.calibrate(sample)
                else:
                    self.model.observe(sample)
        if (sample is not None and sample.source != 'uia' and self.fallback is not None
                and not self.executor.busy and not self._closed
                and (self._calibration_task is None or self._calibration_task.done())):
            self._calibration_task = asyncio.create_task(self._calibrate())

    async def _calibrate(self):
        backend = self.fallback
        source = self.source
        with self._lock:
            snapshot = self.model.snapshot(self.clock())
            anomaly = (snapshot is not None and snapshot.anchor_age > 15.0) or abs(self.model.last_error_seconds) > 1.5
        sample = await backend.calibrate(anomaly=anomaly)
        if (sample is not None and backend is self.fallback and source is self.source
                and not self._closed and not self.executor.busy
                and await source.matches(source.session)):
            self._publish(sample)

    def project(self, data, now=None):
        """Replace only matching external state; never project another session."""
        track = data.track
        identity = (track.source_app_id, track.track_id)
        with self._lock:
            snapshot = self.model.snapshot(self.clock() if now is None else now) if self._valid and (self.clock() if now is None else now) - self._last_received_at <= 2.0 else None
        if snapshot is None or snapshot.identity != identity or str(track.track_id).startswith('owned:'):
            return data
        position = snapshot.position_seconds
        sampled_at = snapshot.sampled_at
        observed_seconds = snapshot.observed_seconds
        with self._lock:
            optimistic = self._optimistic
        if optimistic is not None and optimistic[0] == identity:
            sampled_at, observed_seconds = optimistic[2], optimistic[1]
            position = optimistic[1] + (max(0.0, (self.clock() if now is None else now) - optimistic[2]) * snapshot.rate)
            if track.duration_seconds > 0:
                position = min(track.duration_seconds, position)
        return replace(data, track=replace(track, position_seconds=position,
                                            is_playing=snapshot.is_playing,
                                            position_source='capture-model', position_confidence=0.95,
                                            position_sampled_at=sampled_at,
                                            position_observed_seconds=observed_seconds))

    async def seek(self, target, track_id, guard):
        """Optimistically project a target while verifying independent raw state."""
        source = self.source
        identity = self.identity
        if self._closed or source is None or identity is None or identity[1] != track_id:
            return False
        if self.executor.busy:
            return False
        self._seek_task = asyncio.current_task()
        self.seek_attempts += 1

        async def current():
            return (not self._closed and self.identity == identity and self.source is source
                    and await guard() and await source.matches(source.session))

        def optimistic(position):
            with self._lock:
                self._optimistic = (identity, position, self.clock())

        try:
            result = await self.executor.execute(target, identity=identity,
                                                 request_id=secrets.token_hex(12), smtc=source,
                                                 fallback=self.fallback, guard=current,
                                                 policy=self.policy, optimistic=optimistic)
            if result.succeeded:
                backend = source if result.backend == 'smtc' else self.fallback
                try:
                    sample = await asyncio.wait_for(backend.read(), timeout=self.policy.read_timeout)
                except Exception:
                    sample = None
                if sample is not None and await current():
                    self._publish(sample)
                self.seek_successes += 1
            return result.succeeded
        finally:
            with self._lock:
                self._optimistic = None
            self._seek_task = None

    def diagnostics(self):
        with self._lock:
            result = dict(enabled=True, valid=self._valid, identity=self.identity,
                          reanchors=self.model.reanchors,
                          position_error_seconds=self.model.last_error_seconds)
        result['seek'] = dict(attempts=self.seek_attempts, successes=self.seek_successes,
                              state=self.executor.state.value,
                              reason=self.executor.last_result.reason if self.executor.last_result else '',
                              success_rate=self.seek_successes / self.seek_attempts if self.seek_attempts else None)
        result['uia'] = self.fallback.diagnostics() if self.fallback is not None else {}
        result['smtc'] = self.collector.diagnostics() if self.collector is not None else {}
        return result

    async def close(self):
        self._closed = True
        if self._calibration_task is not None:
            self._calibration_task.cancel()
        if self.fallback is not None:
            self.fallback.close()
        if self._uia_worker is not None:
            self._uia_worker.close()
        if self._seek_task is not None and self._seek_task is not asyncio.current_task():
            self._seek_task.cancel()
        with self._lock:
            self._valid = False
        if self.collector is not None:
            await self.collector.close()
