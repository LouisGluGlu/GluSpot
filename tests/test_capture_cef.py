"""Independent physical CEF evidence and the existing seek verifier."""
import asyncio
import unittest
from dataclasses import replace

from api.capture.cef import CEF_SEEK_POLICY, CefPositionSource, CefSeekBackend
from api.capture.contracts import TimelineSample
from api.capture.runtime import CaptureRuntime
from api.capture.seek import SeekExecutor, SeekState
from tests.test_capture_smtc import Session, matches, visual

from tests.capture_async import CaptureAsyncTestCase


class Clock:
    value = 100.0

    def __call__(self):
        return self.value


class FakeCdp:
    def __init__(self, clock):
        self.clock = clock
        self.ready = True
        self.listeners = set()
        self.writes = []
        self.move = True
        self.latest = self.payload(10)

    def payload(self, position, **changes):
        return dict(trackId='native-song', position=position, duration=200,
                    received_at=self.clock(), generation=1, rate=1.0,
                    playing=True, **changes)

    def subscribe(self, callback):
        self.listeners.add(callback)
        return lambda: self.listeners.discard(callback)

    def emit(self, payload):
        self.latest = payload
        for listener in tuple(self.listeners):
            listener(payload)

    async def readback(self):
        return self.latest

    async def seek(self, track_id, request_id, seconds):
        self.writes.append((track_id, request_id, seconds))
        if self.move:
            self.clock.value += 0.03
            self.emit(self.payload(seconds + 0.03))
        return True


class CefSourceTests(CaptureAsyncTestCase):
    def setUp(self):
        self.clock = Clock()
        self.session = FakeCdp(self.clock)
        self.source = CefPositionSource(self.session, session_id='cloudmusic.exe',
                                        track_id='provider-song', clock=self.clock)

    def tearDown(self):
        self.source.close()

    async def test_native_sample_keeps_provider_identity(self):
        sample = await self.source.read()
        self.assertEqual(sample.identity, ('cloudmusic.exe', 'provider-song'))
        self.assertEqual(sample.source, 'cef')
        self.assertEqual(sample.end_seconds, 200)
        self.assertTrue(sample.is_playing)

    async def test_string_track_identity_survives_duration_drift_and_same_tick_seek(self):
        self.session.emit(dict(trackId='1390072207_BRKMZZ', position=14.046,
                               duration=14.046 / .36996, received_at=self.clock(),
                               generation=1, sequence=1, playing=False))
        source = CefPositionSource(self.session, clock=self.clock)
        try:
            before = await source.read()
            backend = CefSeekBackend(source)
            async def seek(track_id, request_id, seconds):
                self.session.emit(dict(trackId=track_id, position=seconds,
                                       duration=seconds / .5268, received_at=self.clock(),
                                       generation=1, sequence=2, playing=False))
                return True
            self.session.seek = seek
            self.assertTrue(await backend.seek(20, request_id='real-shape'))
            readback = await backend.read()
            self.assertEqual(before.identity, readback.identity)
            self.assertEqual(readback.position_seconds, 20)
            self.assertNotEqual(before.end_seconds, readback.end_seconds)
        finally:
            source.close()

    async def test_native_replacement_invalidates_until_metadata_rebind(self):
        payload = self.session.payload(50)
        payload['trackId'] = 'replacement'
        self.session.emit(payload)
        self.assertIsNone(await self.source.read())
        self.assertEqual(self.source.native_track_id, 'native-song')

    async def test_disconnect_and_expired_samples_fail_closed(self):
        self.clock.value += 2.01
        self.assertIsNone(await self.source.read())
        self.clock.value = 100
        self.session.ready = False
        self.assertIsNone(await self.source.read())

    async def test_confirmed_pause_remains_valid_only_while_heartbeat_healthy(self):
        payload = self.session.payload(10)
        payload['playing'] = False
        self.session.emit(payload)
        self.clock.value += 30
        self.assertEqual((await self.source.read()).position_seconds, 10)
        self.session.ready = False
        self.assertIsNone(await self.source.read())

    async def test_invalid_values_never_reach_model(self):
        for value in (float('nan'), float('inf'), -1, 'missing'):
            self.session.emit(self.session.payload(value))
            self.assertIsNone(await self.source.read())

    async def test_unknown_playstate_preserves_known_bound_state(self):
        payload = self.session.payload(20)
        payload['playing'] = None
        self.session.emit(payload)
        self.assertTrue((await self.source.read()).is_playing)

    async def test_excluded_native_track_cannot_bind_replacement_metadata(self):
        replacement = CefPositionSource(self.session, track_id='new-provider-song',
                                         excluded_native_track_id='native-song', clock=self.clock)
        try:
            self.assertIsNone(await replacement.read())
            self.session.emit(self.session.payload(50))
            self.assertIsNone(await replacement.read())
            payload = dict(self.session.payload(1), trackId='native-replacement')
            self.session.emit(payload)
            sample = await replacement.read()
            self.assertEqual(sample.track_id, 'new-provider-song')
            self.assertEqual(sample.position_seconds, 1)
            self.assertEqual(replacement.native_track_id, 'native-replacement')
        finally:
            replacement.close()

    async def test_smtc_transport_state_preserves_native_position_time(self):
        payload = dict(self.session.payload(20), playing=None)
        self.session.emit(payload)
        before = await self.source.read()
        self.clock.value += .2
        self.source.update_transport_state(False)
        after = await self.source.read()
        self.assertFalse(after.is_playing)
        self.assertEqual(after.position_seconds, before.position_seconds)
        self.assertEqual(after.observed_at, self.clock())
        self.assertEqual(self.source.progress_observed_at, before.observed_at)
        self.source._receive(await self.session.readback())
        repeated = await self.source.read()
        self.assertEqual(repeated.observed_at, after.observed_at)
        self.assertFalse(repeated.is_playing)

    async def test_wait_sample_waits_for_new_physical_event(self):
        waiting = asyncio.create_task(self.source.wait_sample(timeout=.2, after=100))
        await asyncio.sleep(0)
        self.clock.value += .1
        self.session.emit(self.session.payload(10.1))
        sample = await waiting
        self.assertEqual(sample.position_seconds, 10.1)

    async def test_seek_ack_without_new_event_cannot_confirm(self):
        backend = CefSeekBackend(self.source)
        self.session.move = False
        self.assertTrue(await backend.seek(80, request_id='one'))
        self.assertIsNone(await backend.read())
        self.assertFalse(await backend.seek(80, request_id='one'))
        self.assertEqual(len(self.session.writes), 1)

    async def test_state_receipt_after_seek_is_not_native_progress_readback(self):
        self.session.move = False
        backend = CefSeekBackend(self.source)
        self.assertTrue(await backend.seek(80, request_id='one'))
        self.clock.value += .2
        state = dict(self.session.latest, playing=False, event='PlayState',
                     state_received_at=self.clock())
        self.session.emit(state)
        sample = await self.source.read()
        self.assertFalse(sample.is_playing)
        self.assertEqual(sample.observed_at, self.clock())
        self.assertIsNone(await backend.read())

    async def test_seek_clamps_and_uses_fresh_native_readback(self):
        backend = CefSeekBackend(self.source)
        await backend.seek(500, request_id='one')
        self.assertEqual(self.session.writes, [('native-song', 'one', 200)])
        self.assertEqual((await backend.read()).position_seconds, 200)

    async def test_renderer_replacement_cannot_confirm_seek(self):
        backend = CefSeekBackend(self.source)
        await backend.seek(50, request_id='one')
        payload = self.session.payload(50)
        payload['generation'] = 2
        self.session.emit(payload)
        self.assertIsNone(await backend.read())

    async def test_cef_reuses_serialized_seek_executor(self):
        backend = CefSeekBackend(self.source)

        async def current():
            return True

        async def no_delay(_):
            pass

        executor = SeekExecutor(clock=self.clock, sleep=no_delay)
        result = await executor.execute(80, identity=('cloudmusic.exe', 'provider-song'),
                                        request_id='one', smtc=backend, fallback=None,
                                        guard=current, policy=CEF_SEEK_POLICY, primary_name='cef')
        self.assertTrue(result.succeeded)
        self.assertEqual(result.backend, 'cef')
        self.assertEqual(result.state, SeekState.SUCCEEDED)
        self.assertLess(abs(result.error_seconds), .001)
        repeated = await executor.execute(80, identity=('cloudmusic.exe', 'provider-song'),
                                          request_id='one', smtc=backend, fallback=None,
                                          guard=current, policy=CEF_SEEK_POLICY, primary_name='cef')
        self.assertIs(result, repeated)
        self.assertEqual(len(self.session.writes), 1)


class CefRuntimeTests(CaptureAsyncTestCase):
    async def test_resume_after_long_pause_does_not_project_paused_interval(self):
        clock = Clock()
        cdp = FakeCdp(clock)
        cdp.latest = dict(cdp.payload(50), sequence=1)
        runtime = CaptureRuntime(clock=clock, cef_session=cdp)
        try:
            await runtime.bind(Session(), visual().track, matches, mode='smtc')
            native_observed_at = cdp.latest['received_at']
            clock.value += .2
            cdp.emit(dict(cdp.latest, playing=False, event='PlayState',
                          state_received_at=clock()))
            paused_position = runtime.project(visual()).track.position_seconds
            self.assertAlmostEqual(paused_position, 50.2)
            clock.value += 30
            self.assertAlmostEqual(runtime.project(visual()).track.position_seconds,
                                   paused_position)
            cdp.emit(dict(cdp.latest, playing=True, event='PlayState',
                          state_received_at=clock()))
            resumed = runtime.project(visual()).track
            self.assertTrue(resumed.is_playing)
            self.assertAlmostEqual(resumed.position_seconds, paused_position)
            self.assertEqual(cdp.latest['received_at'], native_observed_at)
            clock.value += .5
            self.assertAlmostEqual(runtime.project(visual()).track.position_seconds,
                                   paused_position + .5)
        finally:
            await runtime.close()

    async def test_metadata_replacement_waits_for_different_native_track(self):
        clock = Clock()
        cdp = FakeCdp(clock)
        runtime = CaptureRuntime(clock=clock, cef_session=cdp)
        session = Session()
        try:
            await runtime.bind(session, visual().track, matches, mode='smtc')
            replacement = replace(visual().track, track_id='provider-replacement')
            await runtime.bind(session, replacement, matches, mode='smtc')
            self.assertIsNone(await runtime._cef_source.read())
            self.assertNotEqual(runtime.diagnostics()['position_route'], 'cef')
            cdp.emit(cdp.payload(10.5))
            self.assertIsNone(await runtime._cef_source.read())
            clock.value += .1
            cdp.emit(dict(cdp.payload(1), trackId='native-replacement'))
            sample = await runtime._cef_source.read()
            self.assertEqual(sample.identity, ('cloudmusic.exe', 'provider-replacement'))
            self.assertEqual(sample.position_seconds, 1)
            self.assertEqual(runtime.diagnostics()['position_route'], 'cef')
        finally:
            await runtime.close()

    async def test_confirmed_pause_projects_while_heartbeat_healthy(self):
        clock = Clock()
        cdp = FakeCdp(clock)
        cdp.latest = dict(cdp.payload(50), playing=False)
        runtime = CaptureRuntime(clock=clock, cef_session=cdp)
        try:
            await runtime.bind(Session(), visual().track, matches, mode='smtc')
            clock.value += 30
            projected = runtime.project(visual())
            self.assertEqual(projected.track.position_seconds, 50)
            self.assertFalse(projected.track.is_playing)
            cdp.ready = False
            self.assertEqual(runtime.project(visual()).track.position_seconds,
                             visual().track.position_seconds)
        finally:
            await runtime.close()

    async def test_smtc_resume_after_long_pause_preserves_display_position(self):
        clock = Clock()
        cdp = FakeCdp(clock)
        cdp.latest = dict(cdp.payload(50), playing=None)
        runtime = CaptureRuntime(clock=clock, cef_session=cdp)
        try:
            await runtime.bind(Session(), visual().track, matches, mode='smtc')
            original_progress_time = runtime._cef_source.progress_observed_at
            clock.value += .2
            paused = TimelineSample('cloudmusic.exe', 'song', 0, clock(), 1000,
                                    is_playing=False, end_seconds=0)
            runtime._publish_smtc(paused)
            position = runtime.project(visual()).track.position_seconds
            self.assertAlmostEqual(position, 50.2)
            clock.value += 30
            runtime._publish_smtc(replace(paused, is_playing=True, observed_at=clock()))
            projected = runtime.project(visual()).track
            self.assertTrue(projected.is_playing)
            self.assertAlmostEqual(projected.position_seconds, position)
            self.assertEqual(runtime._cef_source.progress_observed_at, original_progress_time)
            clock.value += .1
            self.assertAlmostEqual(runtime.project(visual()).track.position_seconds,
                                   position + .1)
        finally:
            await runtime.close()

    async def test_unknown_native_state_applies_smtc_pause_without_progress(self):
        clock = Clock()
        cdp = FakeCdp(clock)
        cdp.latest = dict(cdp.payload(50), playing=None)
        runtime = CaptureRuntime(clock=clock, cef_session=cdp)
        try:
            await runtime.bind(Session(), visual().track, matches, mode='smtc')
            before = await runtime._cef_source.read()
            clock.value += .2
            paused = TimelineSample('cloudmusic.exe', 'song', 0, clock(), 1000,
                                    is_playing=False, end_seconds=0)
            runtime._publish_smtc(paused)
            sample = await runtime._cef_source.read()
            self.assertFalse(sample.is_playing)
            self.assertEqual(sample.observed_at, clock())
            self.assertEqual(runtime._cef_source.progress_observed_at, before.observed_at)
            self.assertEqual(sample.position_seconds, before.position_seconds)
            clock.value += 30
            projected = runtime.project(visual())
            self.assertFalse(projected.track.is_playing)
            self.assertAlmostEqual(projected.track.position_seconds, 50.2)
        finally:
            await runtime.close()

    async def test_push_source_wins_then_nonzero_smtc_falls_back(self):
        clock = Clock()
        cdp = FakeCdp(clock)
        cdp.latest = cdp.payload(50)
        runtime = CaptureRuntime(clock=clock, cef_session=cdp)
        session = Session()
        try:
            await runtime.bind(session, visual().track, matches, mode='smtc')
            self.assertEqual(runtime.diagnostics()['position_route'], 'cef')
            self.assertEqual(runtime.project(visual()).track.position_seconds, 50)
            cdp.ready = False
            cdp.emit(None)
            await runtime.collector.collect_once()
            self.assertEqual(runtime.diagnostics()['position_route'], 'smtc')
            self.assertEqual(runtime.project(visual()).track.position_seconds, 10)
        finally:
            await runtime.close()
        self.assertFalse(cdp.listeners)

    async def test_zero_smtc_uses_read_only_uia(self):
        clock = Clock()
        cdp = FakeCdp(clock)
        cdp.ready = False
        session = Session()
        session.get_timeline_properties = lambda: type('Timeline', (), dict(position=0, start_time=0, end_time=0))()
        reads = []

        class Uia:
            def __init__(self, service, track, **_):
                self.track = track

            async def read(self):
                reads.append(1)
                return TimelineSample('cloudmusic.exe', 'song', 25, clock(), 1000,
                                      end_seconds=200, source='uia')

            def close(self):
                pass

        runtime = CaptureRuntime(clock=clock, cef_session=cdp, uia_factory=Uia)
        try:
            await runtime.bind(session, visual().track, matches, mode='smtc')
            await asyncio.sleep(.01)
            self.assertEqual(runtime._position_route, 'uia')
            self.assertEqual(runtime.project(visual()).track.position_seconds, 25)
            self.assertFalse(await runtime.seek(80, 'song', lambda: None))
            self.assertEqual(len(reads), 1)
            self.assertFalse(cdp.writes)
        finally:
            await runtime.close()

    async def test_requested_without_session_rejects_zero_smtc_and_keeps_uia_read_only(self):
        clock = Clock()
        session = Session()
        session.get_timeline_properties = lambda: type('Timeline', (), dict(position=0, start_time=0, end_time=0))()
        reads = []

        class Uia:
            def __init__(self, service, track, **_):
                self.track = track

            async def read(self):
                reads.append(1)
                return TimelineSample('cloudmusic.exe', 'song', 25, clock(), 1000,
                                      end_seconds=200, source='uia')

            async def seek(self, *_args, **_kwargs):
                raise AssertionError('UIA fallback must be read-only')

            def close(self):
                pass

        runtime = CaptureRuntime(clock=clock, cef_requested=True, uia_factory=Uia)
        try:
            await runtime.bind(session, visual().track, matches, mode='smtc')
            await asyncio.sleep(.01)
            self.assertIsNone(runtime._cef_source)
            self.assertFalse(runtime._smtc_timeline_valid)
            self.assertEqual(runtime._position_route, 'uia')
            self.assertEqual(runtime.project(visual()).track.position_seconds, 25)
            self.assertFalse(await runtime.seek(80, 'song', lambda: None))
            await runtime.collector.collect_once()
            self.assertEqual(len(reads), 1)
            clock.value += 1
            await runtime.collector.collect_once()
            await asyncio.sleep(.01)
            self.assertEqual(len(reads), 2)
        finally:
            await runtime.close()

    async def test_requested_without_session_never_seeks_uia_after_smtc_failure(self):
        clock = Clock()
        session = Session()
        writes = []

        async def acknowledged_without_moving(target):
            writes.append(target)
            return True

        session.try_change_playback_position_async = acknowledged_without_moving

        class Uia:
            def __init__(self, service, track, **_):
                self.track = track

            async def calibrate(self, **_):
                raise AssertionError('Requested CDP chain does not use legacy calibration')

            async def seek(self, *_args, **_kwargs):
                raise AssertionError('UIA fallback must be read-only')

            def close(self):
                pass

        async def no_delay(_):
            pass

        async def current():
            return True

        executor = SeekExecutor(clock=clock, sleep=no_delay)
        runtime = CaptureRuntime(clock=clock, cef_requested=True, uia_factory=Uia,
                                 executor=executor)
        try:
            await runtime.bind(session, visual().track, matches, mode='hybrid')
            self.assertTrue(runtime._smtc_timeline_valid)
            self.assertEqual(runtime._position_route, 'smtc')
            self.assertFalse(await runtime.seek(80, 'song', current))
            self.assertEqual(writes, [800_000_000])
            self.assertEqual(executor.last_result.reason, 'fake-ack')
        finally:
            await runtime.close()
