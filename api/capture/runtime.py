"""Provider-owned opt-in capture lifecycle and immutable visual projection."""
from __future__ import annotations

import asyncio
import secrets
import sys
import threading
import time
from dataclasses import replace

from api.capture.capabilities import capability_for
from api.capture.position import PositionModel
from api.capture.seek import SeekExecutor, record_seek_decision, seek_policy
from api.capture.smtc import SmtcCollector, SmtcSessionSource


class CaptureRuntime:
    """Own one session binding on the provider's background asyncio loop.

    Only ``project``/``diagnostics`` cross into UI/background callers; they use
    a short model lock, never WinRT/UIA I/O. Native callbacks, rebinding and
    teardown belong to the provider loop. The legacy provider remains separate.
    """

    def __init__(self, *, clock=time.monotonic, uia_factory=None, uia_worker=None, executor=None,
                 cef_session=None, cef_requested=False):
        self.clock = clock
        self._lock = threading.Lock()
        self.model = PositionModel()
        self.collector: SmtcCollector | None = None
        self.source: SmtcSessionSource | None = None
        self.identity: tuple[str, str] | None = None
        self._valid = False
        self._last_received_at = 0.0
        self._closed = False
        self.executor = executor or SeekExecutor(clock=clock)
        self.fallback = None
        self.policy = seek_policy("")
        self._optimistic = None
        self._seek_task = None
        self.seek_attempts = 0
        self.seek_successes = 0
        self.smtc_trust: bool | None = None
        self._player_metrics = {}
        self._uia_worker = uia_worker
        self._owns_uia_worker = uia_worker is None
        self._duration = 0.0
        self._uia_factory = uia_factory
        self._calibration_task = None
        self.cef_session = cef_session
        # An unavailable transport must retain the requested fallback policy.
        # Session existence represents successful launch, never user intent.
        self.cef_requested = bool(cef_requested or cef_session is not None)
        self._cef_route_requested = False
        self._cef_source = None
        self._cef_backend = None
        self._cef_seek_enabled = True
        self._smtc_timeline_valid = False
        self._position_route = 'smtc'
        self._next_uia_read = 0.0
        self._last_projection = None
        self.capture_epoch_id = 0
        self._capture_epoch_source = None
        self._projected_epoch_id = 0
        self._first_projected_observation = None

    async def bind(self, session, track, matches, *, mode=None):
        if self._closed:
            return
        identity = (str(session.source_app_user_model_id), track.track_id)
        capability = capability_for(identity[0])
        self._duration = track.duration_seconds
        mode = mode or capability.mode
        if (self.identity == identity and getattr(self, '_mode', None) == mode
                and self.source is not None and (self._valid or self._cef_route_requested)
                and self.collector is not None and self.collector._task is not None
                and not self.collector._task.done()):
            if self.fallback is not None:
                self.fallback.track = track
            return
        if self.collector is not None:
            await self.collector.close()
        previous_native_id = None
        if self._cef_source is not None:
            if self.identity != identity:
                previous_native_id = (self._cef_source.native_track_id
                                      or self._cef_source.excluded_native_track_id)
            self._cef_source.close()
            self._cef_source = None
            self._cef_backend = None
        with self._lock:
            if self.identity != identity:
                self.model = PositionModel()
                self._valid = False
                self._capture_epoch_source = None
            self.identity = identity
        if self._calibration_task is not None:
            self._calibration_task.cancel()
            self._calibration_task = None
        if self.fallback is not None:
            self.fallback.close()
            self.fallback = None
        self._mode = mode
        self._capability = capability
        service = capability.service
        self._cef_route_requested = self.cef_requested and service == 'netease'
        cef_enabled = self.cef_session is not None and service == 'netease'
        if (mode == 'hybrid' or self._cef_route_requested) and service in {'netease', 'qq'} and (sys.platform == 'win32' or self._uia_factory is not None):
            from api.capture.uia import StaWorker, UiaBackend
            if self._uia_worker is None:
                self._uia_worker = StaWorker()
            self.fallback = (self._uia_factory or UiaBackend)(service, track, worker=self._uia_worker, clock=self.clock)
        self.policy = capability.policy
        self.source = SmtcSessionSource(session, track.track_id, matches, clock=self.clock)
        if cef_enabled:
            from api.capture.cef import CefPositionSource, CefSeekBackend
            self._cef_source = CefPositionSource(
                self.cef_session, session_id=identity[0], track_id=identity[1],
                excluded_native_track_id=previous_native_id,
                duration_seconds=track.duration_seconds, is_playing=track.is_playing,
                clock=self.clock)
            self._cef_backend = CefSeekBackend(self._cef_source)
            self._cef_seek_enabled = True
            self._cef_source.subscribe(self._publish_cef)
            initial = await self._cef_source.read()
            if initial is not None:
                self._publish_cef(initial)
        self.collector = SmtcCollector(self.source, self._publish_smtc)
        try:
            await self.collector.collect_once()
            self.collector.start()
        except BaseException:
            # A cancelled media refresh must not leave a binding which looks
            # live but has no event subscriptions or polling task.
            await self.collector.close()
            self.source = None
            raise

    def _cef_fresh(self):
        source = self._cef_source
        return source is not None and source.fresh

    def _publish_cef(self, sample):
        if sample is not None:
            self._position_route = 'cef'
            self._publish(sample)
        elif self._position_route == 'cef':
            # A renderer disconnect or track change must stop projection now,
            # before the next ordinary SMTC fallback observation arrives.
            self._publish(None)

    def _publish_smtc(self, sample):
        if not self._cef_route_requested:
            self._publish(sample)
            return
        if (self._cef_source is not None and sample is not None and isinstance(self.cef_session.latest, dict)
                and self.cef_session.latest.get('playing') is None):
            # PlayState numeric enum values are not verified across versions.
            # Keep physical SMTC transport state until the adapter has a
            # verified boolean state; progress still comes from native pushes.
            self._cef_source.update_transport_state(sample.is_playing)
        self._smtc_timeline_valid = (sample is not None and
                                    (sample.position_seconds > 0 or sample.end_seconds > sample.start_seconds))
        if self._cef_fresh():
            return
        if self._smtc_timeline_valid:
            self._position_route = 'smtc'
            self._publish(sample)
        else:
            if self._position_route != 'uia' or self.clock() - self._last_received_at > 2.0:
                self._publish(None)
            if (self.fallback is not None and not self._closed and self._seek_task is None
                    and self.clock() >= self._next_uia_read
                    and (self._calibration_task is None or self._calibration_task.done())):
                self._next_uia_read = self.clock() + 1.0
                self._calibration_task = asyncio.create_task(self._read_uia_fallback())

    async def _read_uia_fallback(self):
        backend = self.fallback
        source = self.source
        try:
            sample = await backend.read()
        except Exception:
            sample = None
        if (sample is not None and backend is self.fallback and source is self.source
                and not self._closed and not self._cef_fresh() and not self._smtc_timeline_valid
                and self._seek_task is None and await source.matches(source.session)):
            self._position_route = 'uia'
            # With no SMTC anchor, UIA supplies the base observation. This path
            # remains read-only; it never invents a seek capability.
            with self._lock:
                self.model.observe(sample, lyric_duration=self._duration)
                self._valid = self.model.sample is sample
                self._accept_epoch(sample)
                self._last_received_at = self.clock()

    def _accept_epoch(self, sample):
        # Called under the model lock, only for accepted physical evidence.
        source = (sample.identity, sample.source)
        if self._valid and source != self._capture_epoch_source:
            self.capture_epoch_id += 1
            self._capture_epoch_source = source

    def _publish(self, sample):
        self._last_received_at = self.clock()
        with self._lock:
            self._valid = sample is not None and sample.identity == self.identity
            if self._valid:
                if sample.source == 'uia':
                    self._valid = self.model.calibrate(sample)
                else:
                    self.model.observe(sample, lyric_duration=self._duration)
                    self._valid = self.model.sample is sample
                self._accept_epoch(sample)
        if (sample is not None and sample.source != 'uia' and self.fallback is not None
                and not self._cef_route_requested
                and self._seek_task is None and not self._closed
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
                and not self._closed and self._seek_task is None
                and await source.matches(source.session)):
            self._publish(sample)

    def project(self, data, now=None):
        """Replace only matching external state; never project another session."""
        track = data.track
        identity = (track.source_app_id, track.track_id)
        instant = self.clock() if now is None else now
        paused_cef_fresh = (self._position_route == 'cef' and self._cef_source is not None
                            and self._cef_source.fresh)
        with self._lock:
            fresh = self._valid and (paused_cef_fresh or instant - self._last_received_at <= 2.0)
            snapshot = self.model.snapshot(instant) if fresh else None
            epoch = self.capture_epoch_id
            if snapshot is not None and snapshot.identity == identity:
                observation = self.model.sample.observed_at
                if epoch != self._projected_epoch_id:
                    self._projected_epoch_id = epoch
                    self._first_projected_observation = observation
                first_sample = observation == self._first_projected_observation
            else:
                first_sample = False
        if snapshot is None or snapshot.identity != identity or str(track.track_id).startswith('owned:'):
            if (self._cef_route_requested and self.identity == identity
                    and not str(track.track_id).startswith('owned:')):
                # Returning the legacy data here resurrected an invented clock
                # from an all-zero SMTC timeline. Retain the last displayed
                # capture position and keep actual transport state independent.
                with self._lock:
                    prior = self._last_projection
                position = prior[1] if prior is not None and prior[0] == identity else 0.0
                return replace(data, track=replace(
                    track, position_seconds=position, position_source='capture-unavailable',
                    position_confidence=0.0, position_sampled_at=0.0,
                    position_observed_seconds=None))
            return data
        position = snapshot.position_seconds
        sampled_at = snapshot.sampled_at
        observed_seconds = snapshot.observed_seconds
        with self._lock:
            optimistic = self._optimistic
        if optimistic is not None and optimistic[0] == identity:
            sampled_at, observed_seconds = optimistic[2], optimistic[1]
            position = optimistic[1] + (max(0.0, instant - optimistic[2]) * snapshot.rate)
            if track.duration_seconds > 0:
                position = min(track.duration_seconds, position)
        with self._lock:
            self._last_projection = (identity, position)
        return replace(data, track=replace(
            track, position_seconds=position, is_playing=snapshot.is_playing,
            position_source='capture-model', position_confidence=0.95,
            position_sampled_at=sampled_at, position_observed_seconds=observed_seconds,
            capture_epoch_id=epoch, is_first_sample=first_sample))

    async def seek(self, target, track_id, guard):
        """Optimistically project a target while verifying independent raw state."""
        source = self.source
        identity = self.identity
        if self._closed or source is None or identity is None or identity[1] != track_id:
            record_seek_decision('runtime', 'binding-unavailable' if self._closed or source is None or identity is None
                                 else 'track-mismatch', target=target)
            return False
        if self._seek_task is not None or (self._uia_worker is not None and self._uia_worker.busy):
            record_seek_decision('runtime', 'busy' if self._seek_task is not None else 'uia-worker-busy', target=target)
            return False
        task = asyncio.current_task()
        self._seek_task = task
        self.seek_attempts += 1

        async def current():
            return (not self._closed and self.identity == identity and self.source is source
                    and await guard() and await source.matches(source.session))

        def optimistic(position):
            with self._lock:
                self._optimistic = (identity, position, self.clock())

        try:
            primary = source
            fallback = self.fallback
            primary_name, fallback_name = 'smtc', 'uia'
            policy = self.policy
            if self._cef_route_requested:
                # CEF failure degrades seek independently from position. UIA
                # in the new transport chain is deliberately read-only.
                fallback = None
                if self._cef_seek_enabled and self._cef_fresh():
                    from api.capture.cef import CEF_SEEK_POLICY
                    primary, primary_name, policy = self._cef_backend, 'cef', CEF_SEEK_POLICY
                elif not self._smtc_timeline_valid:
                    record_seek_decision('runtime', 'cef-seek-disabled' if not self._cef_seek_enabled else
                                         'no-current-observation', target=target, backend='cef',
                                         port=getattr(self.cef_session, 'port', None))
                    return False
            record_seek_decision('runtime', 'executing', target=target, backend=primary_name,
                                 port=getattr(self.cef_session, 'port', None) if primary_name == 'cef' else None)
            result = await self.executor.execute(target, identity=identity,
                                                 request_id=secrets.token_hex(12), smtc=primary,
                                                 fallback=fallback, guard=current,
                                                 policy=policy, optimistic=optimistic,
                                                 primary_name=primary_name, fallback_name=fallback_name)
            record_seek_decision('runtime-result', result.reason, target=target, backend=primary_name,
                                 port=getattr(self.cef_session, 'port', None) if primary_name == 'cef' else None,
                                 error_seconds=result.error_seconds)
            # A refused stale gesture or missing preflight observation says
            # nothing about CEF's physical seek support. Only a failed native
            # attempt degrades that route. Executor uncertainty remains shared
            # across bindings and prevents another physical write.
            if primary_name == 'cef' and result.reason in {'unverified', 'fake-ack', 'request-timeout', 'request-error'}:
                self._cef_seek_enabled = False
            if result.succeeded:
                self.smtc_trust = result.backend == 'smtc'
            elif result.smtc_acknowledged and result.reason == 'fake-ack':
                self.smtc_trust = False
            with self._lock:
                metrics = self._player_metrics.setdefault(self._capability.service, dict(
                    attempts=0, successes=0, smtc_successes=0, uia_successes=0, cef_successes=0,
                    error_seconds=None))
                metrics['attempts'] += 1
                metrics['successes'] += int(result.succeeded)
                metrics['smtc_successes'] += int(result.succeeded and result.backend == 'smtc')
                metrics['uia_successes'] += int(result.succeeded and result.backend == 'uia')
                metrics['cef_successes'] += int(result.succeeded and result.backend == 'cef')
                metrics['error_seconds'] = result.error_seconds
            if result.succeeded:
                backend = primary if result.backend == primary_name else fallback
                try:
                    sample = await asyncio.wait_for(backend.read(), timeout=policy.read_timeout)
                except Exception:
                    sample = None
                if sample is not None and await current():
                    self._publish(sample)
                self.seek_successes += 1
            return result.succeeded
        finally:
            if self._seek_task is task:
                with self._lock:
                    self._optimistic = None
                self._seek_task = None

    def diagnostics(self):
        with self._lock:
            result = dict(enabled=True, valid=self._valid, identity=self.identity,
                          capture_epoch_id=self.capture_epoch_id,
                          reanchors=self.model.reanchors,
                          position_error_seconds=self.model.last_error_seconds)
            result['players'] = {key: dict(values) for key, values in self._player_metrics.items()}
        result['mode'] = getattr(self, '_mode', 'smtc')
        result['capability'] = getattr(self, '_capability', capability_for('')).service
        result['smtc_seek_trusted'] = self.smtc_trust
        result['position_route'] = self._position_route
        result['cef'] = (dict(self._cef_source.diagnostics(), seek_enabled=self._cef_seek_enabled)
                         if self._cef_source is not None else {})
        result['seek'] = dict(attempts=self.seek_attempts, successes=self.seek_successes,
                              state=self.executor.state.value,
                              reason=self.executor.last_result.reason if self.executor.last_result else '',
                              success_rate=self.seek_successes / self.seek_attempts if self.seek_attempts else None)
        result['uia'] = self.fallback.diagnostics() if self.fallback is not None else {}
        result['smtc'] = self.collector.diagnostics() if self.collector is not None else {}
        return result

    async def close(self):
        self._closed = True
        if self._cef_source is not None:
            self._cef_source.close()
        if self._calibration_task is not None:
            self._calibration_task.cancel()
        if self.fallback is not None:
            self.fallback.close()
        if self._uia_worker is not None and self._owns_uia_worker:
            self._uia_worker.close()
        if self._seek_task is not None and self._seek_task is not asyncio.current_task():
            self._seek_task.cancel()
        with self._lock:
            self._valid = False
        if self.collector is not None:
            await self.collector.close()
