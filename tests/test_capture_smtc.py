"""Mock-native event, polling, lifecycle and provider-thread integration."""
import asyncio
import threading
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from api.capture.capabilities import capability_for
from api.capture.contracts import TimelineSample
from api.capture.runtime import CaptureRuntime
from api.capture.seek import SeekExecutor, SeekPolicy
from api.capture.smtc import SmtcCollector, SmtcSessionSource
from api.providers import EmptyIslandDataProvider, WindowsMediaProvider
from local.media_position_pipeline import media_position_update_plan


class Session:
    source_app_user_model_id = 'cloudmusic.exe'

    def __init__(self):
        self.events = {}
        self.removed = []
        self.threads = []
        self.position = 10.0
        self.playing = True

    def get_timeline_properties(self):
        self.threads.append(threading.get_ident())
        return SimpleNamespace(position=self.position, start_time=0, end_time=200)

    def get_playback_info(self):
        return SimpleNamespace(playback_status=4 if self.playing else 3, playback_rate=1.0)

    def __getattr__(self, name):
        if name.startswith('add_'):
            def add(callback):
                self.events[name[4:]] = callback
                return name[4:]
            return add
        if name.startswith('remove_'):
            return lambda token: self.removed.append(token)
        raise AttributeError(name)


async def matches(_session):
    return True


def visual():
    data = EmptyIslandDataProvider().get_visual_data()
    return replace(data, track=replace(data.track, track_id='song', source_app_id='cloudmusic.exe', is_playing=True))


class CollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_initial_bind_can_rebind_same_session_and_track(self):
        runtime = CaptureRuntime(clock=lambda: 100)
        session = Session()
        initial_read_started = asyncio.Event()

        async def blocked_matches(_session):
            initial_read_started.set()
            await asyncio.Future()

        binding = asyncio.create_task(
            runtime.bind(session, visual().track, blocked_matches, mode='smtc'))
        try:
            await asyncio.wait_for(initial_read_started.wait(), timeout=0.2)
            binding.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await binding

            # A cache refresh may immediately retry with the same native
            # session wrapper. The interrupted binding must not suppress it.
            await runtime.bind(session, visual().track, matches, mode='smtc')
            self.assertEqual(len(session.events), 3)
            self.assertEqual(runtime.project(visual()).track.position_source, 'capture-model')
            session.position = 42
            await runtime.collector.collect_once()
            self.assertEqual(runtime.project(visual()).track.position_seconds, 42)
        finally:
            binding.cancel()
            await runtime.close()

    async def test_seek_remains_serialized_during_final_success_read(self):
        runtime = CaptureRuntime(clock=lambda: 100)
        runtime.identity = ('app', 'song')
        runtime._capability = capability_for('app')
        final_read_started = asyncio.Event()
        release_final_read = asyncio.Event()
        writes = []

        class Source:
            session = object()
            reads = 0
            position = 10.0

            async def matches(self, _session):
                return True

            async def read(self):
                self.reads += 1
                sample = TimelineSample('app', 'song', self.position, 100, 1000,
                                        end_seconds=200)
                if self.reads == 3:
                    # Pre-seek and verification reads finished; runtime is
                    # now copying independent success evidence into its model.
                    final_read_started.set()
                    await release_final_read.wait()
                return sample

            async def seek(self, target, *, request_id):
                writes.append(target)
                self.position = target
                return True

        async def no_delay(_seconds):
            await asyncio.sleep(0)

        async def current():
            return True

        runtime.source = Source()
        runtime.executor = SeekExecutor(clock=lambda: 100, sleep=no_delay)
        runtime.policy = SeekPolicy(read_delays=(0,), read_timeout=1, request_timeout=1)
        first = asyncio.create_task(runtime.seek(80, 'song', current))
        try:
            await asyncio.wait_for(final_read_started.wait(), timeout=0.2)
            self.assertFalse(runtime.executor.busy)
            self.assertFalse(await runtime.seek(90, 'song', current))
            self.assertIs(runtime._seek_task, first)
            self.assertEqual(runtime._optimistic, (('app', 'song'), 80, 100))
            self.assertEqual(writes, [80])
            release_final_read.set()
            self.assertTrue(await first)
            self.assertIsNone(runtime._seek_task)
            self.assertIsNone(runtime._optimistic)
        finally:
            release_final_read.set()
            if not first.done():
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
            await runtime.close()

    async def test_event_during_read_triggers_another_read_before_poll_deadline(self):
        """An event queued after a read starts must survive that read's result."""
        session = Session()
        source = SmtcSessionSource(session, 'song', matches)
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        received_new = asyncio.Event()
        original_read = source.read
        calls = 0

        async def read():
            nonlocal calls
            calls += 1
            sample = await original_read()
            if calls == 1:
                first_started.set()
                await release_first.wait()
            return sample

        source.read = read
        samples = []

        def publish(sample):
            samples.append(sample)
            if sample is not None and sample.position_seconds == 40:
                received_new.set()

        collector = SmtcCollector(source, publish, poll_seconds=0.5)
        collector.start()
        try:
            await asyncio.wait_for(first_started.wait(), timeout=0.2)
            session.position = 40
            thread = threading.Thread(target=session.events['timeline_properties_changed'])
            thread.start()
            thread.join()
            # Deliver the queued native callback while the first read still
            # holds its older physical sample.
            await asyncio.sleep(0)
            self.assertEqual(collector.stats['events'], 1)
            release_first.set()
            await asyncio.wait_for(received_new.wait(), timeout=0.2)
            self.assertEqual([sample.position_seconds for sample in samples], [10, 40])
            self.assertEqual(calls, 2)
            self.assertEqual(collector.stats['event_latency_samples'], 1)
        finally:
            release_first.set()
            await collector.close()

    async def test_read_error_invalidates_projection_and_next_read_recovers(self):
        runtime = CaptureRuntime(clock=lambda: 100)
        data = visual()
        runtime.identity = (data.track.source_app_id, data.track.track_id)
        source = SmtcSessionSource(Session(), 'song', matches,
                                   clock=lambda: 100, wall_clock=lambda: 1000)
        collector = SmtcCollector(source, runtime._publish)
        await collector.collect_once()
        self.assertEqual(runtime.project(data).track.position_source, 'capture-model')

        timeline = source.session.get_timeline_properties

        def failed_timeline():
            raise RuntimeError('native session disappeared')

        source.session.get_timeline_properties = failed_timeline
        await collector.collect_once()
        self.assertEqual(collector.stats['errors'], 1)
        self.assertFalse(runtime.diagnostics()['valid'])
        self.assertIs(runtime.project(data), data)

        source.session.get_timeline_properties = timeline
        source.session.position = 42
        await collector.collect_once()
        self.assertTrue(runtime.diagnostics()['valid'])
        self.assertEqual(runtime.project(data).track.position_seconds, 42)

    async def test_read_timeout_invalidates_previous_projection(self):
        runtime = CaptureRuntime(clock=lambda: 100)
        data = visual()
        runtime.identity = (data.track.source_app_id, data.track.track_id)
        runtime._publish(TimelineSample('cloudmusic.exe', 'song', 20, 100, 1000,
                                       is_playing=True))
        pending_cancelled = asyncio.Event()

        async def unresponsive_matches(_session):
            try:
                await asyncio.Future()
            finally:
                pending_cancelled.set()

        collector = SmtcCollector(
            SmtcSessionSource(Session(), 'song', unresponsive_matches), runtime._publish)
        await collector.collect_once()
        self.assertTrue(pending_cancelled.is_set())
        self.assertEqual(collector.stats['errors'], 1)
        self.assertFalse(runtime.diagnostics()['valid'])
        self.assertIs(runtime.project(data), data)

    async def test_events_from_native_thread_are_queued_and_removed(self):
        session = Session()
        source = SmtcSessionSource(session, 'song', matches)
        samples = []
        collector = SmtcCollector(source, samples.append)
        collector.start()
        try:
            await asyncio.sleep(0)
            session.position = 40
            thread = threading.Thread(target=session.events['timeline_properties_changed'])
            thread.start()
            thread.join()
            await asyncio.sleep(0.015)
            self.assertEqual(samples[-1].position_seconds, 40)
            self.assertGreaterEqual(collector.stats['events'], 1)
            self.assertEqual(collector.stats['subscriptions'], 3)
            self.assertGreaterEqual(collector.stats['event_latency_samples'], 1)
            self.assertTrue(all(t == threading.get_ident() for t in session.threads))
        finally:
            await collector.close()
        self.assertEqual(len(session.removed), 3)
        session.events['playback_info_changed']()  # Late callback is harmless.

    async def test_poll_fallback_without_events_and_error_recovery(self):
        session = Session()
        for event in SmtcCollector.EVENTS:
            setattr(session, 'add_' + event, None)
        samples = []
        collector = SmtcCollector(SmtcSessionSource(session, 'song', matches), samples.append)
        collector.poll_seconds = 0.01
        collector.start()
        try:
            await asyncio.sleep(0.035)
            self.assertGreaterEqual(len(samples), 2)
            self.assertEqual(collector.stats['subscriptions'], 0)
        finally:
            await collector.close()

    async def test_rebinding_fences_track_and_removes_old_tokens(self):
        runtime = CaptureRuntime(clock=lambda: 100)
        first, second = Session(), Session()
        data = visual()
        await runtime.bind(first, data.track, matches)
        runtime._publish(TimelineSample('cloudmusic.exe', 'song', 20, 100, 1000, is_playing=True))
        self.assertEqual(runtime.project(data).track.position_seconds, 20)
        changed = replace(data.track, track_id='new')
        await runtime.bind(second, changed, matches)
        self.assertEqual(len(first.removed), 3)
        self.assertIs(runtime.project(data), data)
        runtime._publish(None)
        self.assertFalse(runtime.diagnostics()['valid'])
        await runtime.close()
        self.assertEqual(len(second.removed), 3)

    async def test_source_timestamp_rate_and_metadata_guard(self):
        session = Session()
        source = SmtcSessionSource(session, 'song', matches, clock=lambda: 10, wall_clock=lambda: 100)
        sample = await source.read()
        self.assertEqual(sample.observed_at, 10)
        self.assertEqual(sample.rate, 1)
        self.assertEqual(sample.end_seconds, 200)
        async def replaced(_session):
            return False
        source.matches = replaced
        self.assertIsNone(await source.read())


class ProviderCaptureTests(unittest.TestCase):
    def test_runtime_projection_expires_after_two_seconds_without_receipt(self):
        clock = [100.0]
        runtime = CaptureRuntime(clock=lambda: clock[0])
        data = visual()
        runtime.identity = (data.track.source_app_id, data.track.track_id)
        runtime._publish(TimelineSample('cloudmusic.exe', 'song', 40, 100, 1000,
                                       is_playing=True))
        self.assertEqual(runtime.project(data, now=102.0).track.position_seconds, 42)
        self.assertIs(runtime.project(data, now=102.001), data)

        # An independent new physical receipt renews the bounded lifetime.
        clock[0] = 101.5
        runtime._publish(TimelineSample('cloudmusic.exe', 'song', 50, 101.5, 1001.5,
                                       is_playing=True))
        self.assertEqual(runtime.project(data, now=103.5).track.position_seconds, 52)
        self.assertIs(runtime.project(data, now=103.501), data)

    def test_flag_off_does_not_create_runtime(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda _k, default=None: default
        with patch('api.capture.runtime.CaptureRuntime', side_effect=AssertionError('flag off')):
            asyncio.run(provider._bind_capture_session(Session(), visual().track))
        self.assertIsNone(getattr(provider, '_capture_runtime', None))

    def test_io_runs_on_provider_background_loop_and_shutdown_unsubscribes(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda k, default=None: True if k == 'v2_capture_position_enabled' else default
        provider._capture_runtime = None
        provider._seek_session_matches_track = lambda *_args: matches(None)
        session = Session()
        provider._start_loop()
        try:
            future = asyncio.run_coroutine_threadsafe(provider._bind_capture_session(session, visual().track), provider._loop)
            future.result(timeout=2)
            data = provider._project_visual_position(visual(), time.monotonic(), 0)
            self.assertEqual(data.track.position_source, 'capture-model')
            self.assertTrue(all(t == provider._thread.ident for t in session.threads))
            asyncio.run_coroutine_threadsafe(provider._capture_runtime.close(), provider._loop).result(timeout=2)
            self.assertEqual(len(session.removed), 3)
        finally:
            provider._loop.call_soon_threadsafe(provider._loop.stop)
            provider._thread.join(timeout=2)
            provider._loop.close()

    def test_lyric_hysteresis_preserves_small_correction_but_accepts_seek(self):
        args = dict(track_changed=False, incoming_is_playing=True, incoming_position_source='capture-model',
                    incoming_position_confidence=.95, incoming_position_interactive=False,
                    incoming_source_foreground=False, previous_position_seconds=30,
                    previous_sync_at=100, previous_raw_position_seconds=30,
                    previous_raw_position_source='capture-model', now=100.1)
        self.assertEqual(media_position_update_plan(incoming_position_seconds=29.8, **args).position_seconds, 30)
        self.assertEqual(media_position_update_plan(incoming_position_seconds=10, **args).position_seconds, 10)


class RuntimeSeekTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_native_wrapper_for_same_player_does_not_abort_seek_binding(self):
        runtime = CaptureRuntime()
        first, refreshed = Session(), Session()
        try:
            await runtime.bind(first, visual().track, matches, mode='smtc')
            source, collector = runtime.source, runtime.collector
            await runtime.bind(refreshed, visual().track, matches, mode='smtc')
            self.assertIs(runtime.source, source)
            self.assertIs(runtime.collector, collector)
            self.assertEqual(first.removed, [])
        finally:
            await runtime.close()

    async def test_capture_toggles_preserve_uncertain_seek_and_quarantined_worker(self):
        """Disabling either scope cannot authorize another ambiguous mutation."""
        from api.capture.uia import StaWorker

        for disabled_scope in ('flag', 'profile'):
            with self.subTest(scope=disabled_scope):
                settings = {'v2_capture_position_enabled': True}
                provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
                provider.settings_getter = lambda key, default=None: settings.get(key, default)
                provider._seek_session_matches_track = lambda *_args: matches(None)
                release_worker = threading.Event()
                worker_started = threading.Event()
                worker = StaWorker(timeout=0.01, initializer=lambda: None)
                provider._capture_uia_worker = worker
                session = Session()
                writes = []

                def pending_native_write():
                    worker_started.set()
                    release_worker.wait(1)

                async def unresolved_seek(requested):
                    writes.append(requested)
                    await asyncio.Future()

                session.try_change_playback_position_async = unresolved_seek
                try:
                    await provider._bind_capture_session(session, visual().track)
                    original = provider._capture_runtime
                    original.policy = SeekPolicy(request_timeout=0.01)
                    self.assertFalse(await original.seek(80, 'song', lambda: matches(None)))
                    self.assertEqual(original.executor.last_result.state.value, 'uncertain')

                    with self.assertRaises(asyncio.TimeoutError):
                        await worker.call(pending_native_write)
                    self.assertTrue(worker_started.is_set())

                    if disabled_scope == 'flag':
                        settings['v2_capture_position_enabled'] = False
                    else:
                        settings['v2_capture_app_profiles'] = {'netease': {'mode': 'off'}}
                    await provider._bind_capture_session(session, visual().track)
                    self.assertIsNone(provider._capture_runtime)
                    self.assertTrue(worker.busy)

                    settings['v2_capture_position_enabled'] = True
                    settings['v2_capture_app_profiles'] = {'netease': {'mode': 'smtc'}}
                    await provider._bind_capture_session(session, visual().track)
                    replacement = provider._capture_runtime
                    self.assertIsNot(replacement, original)
                    self.assertIs(replacement.executor, original.executor)
                    self.assertIs(replacement._uia_worker, worker)
                    self.assertFalse(await replacement.seek(90, 'song', lambda: matches(None)))
                    self.assertEqual(writes, [800_000_000])
                    with self.assertRaises(RuntimeError):
                        await worker.call(lambda: writes.append('unexpected UIA retry'))
                finally:
                    active = getattr(provider, '_capture_runtime', None)
                    if active is not None:
                        await active.close()
                    worker.close()
                    release_worker.set()
                    if worker._thread is not None:
                        await asyncio.to_thread(worker._thread.join, 1)

    async def test_real_runtime_mock_session_verifies_and_records_metrics(self):
        session = Session()
        writes = []
        async def seek(requested):
            writes.append(requested)
            session.position = requested / 10_000_000
            return True
        session.try_change_playback_position_async = seek
        runtime = CaptureRuntime()
        await runtime.bind(session, visual().track, matches, mode='smtc')
        from api.capture.seek import SeekPolicy
        runtime.policy = SeekPolicy(read_delays=(0,))
        try:
            self.assertTrue(await runtime.seek(80, 'song', lambda: matches(session)))
            self.assertEqual(writes, [800_000_000])
            self.assertTrue(runtime.diagnostics()['smtc_seek_trusted'])
            self.assertEqual(runtime.diagnostics()['players']['netease']['smtc_successes'], 1)
        finally:
            await runtime.close()

    async def test_mock_fake_ack_rolls_back_optimistic_ui_without_second_write(self):
        session = Session()
        writes = []
        async def seek(requested):
            writes.append(requested)
            return True
        session.try_change_playback_position_async = seek
        runtime = CaptureRuntime()
        await runtime.bind(session, visual().track, matches, mode='smtc')
        from api.capture.seek import SeekPolicy
        runtime.policy = SeekPolicy(read_delays=(0, 0))
        try:
            self.assertFalse(await runtime.seek(80, 'song', lambda: matches(session)))
            self.assertEqual(writes, [800_000_000])
            self.assertFalse(runtime.diagnostics()['smtc_seek_trusted'])
            self.assertLess(runtime.project(visual()).track.position_seconds, 20)
        finally:
            await runtime.close()

    async def test_profile_off_stops_collector_without_changing_legacy_route(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        settings = {'v2_capture_position_enabled': True}
        provider.settings_getter = lambda key, default=None: settings.get(key, default)
        provider._capture_runtime = None
        provider._seek_session_matches_track = lambda *_args: matches(None)
        session = Session()
        await provider._bind_capture_session(session, visual().track)
        settings['v2_capture_app_profiles'] = {'netease': {'mode': 'off'}}
        await provider._bind_capture_session(session, visual().track)
        self.assertIsNone(provider._capture_runtime)
        self.assertEqual(len(session.removed), 3)


class PriorCaptureMutationTests(unittest.TestCase):
    def test_disabling_flag_cannot_issue_legacy_seek_over_uncertain_capture_mutation(self):
        from api.capture.seek import SeekExecutor
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda _key, default=None: default
        provider._capture_executor = SeekExecutor()
        provider._capture_executor._uncertain = True
        with patch.object(provider, '_seek_position', side_effect=AssertionError('second mutation')):
            self.assertFalse(provider.seek_playback_position(50))

    def test_disabling_flag_cannot_issue_legacy_seek_while_shared_worker_is_busy(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda _key, default=None: default
        provider._capture_uia_worker = SimpleNamespace(busy=True)
        with patch.object(provider, '_seek_position', side_effect=AssertionError('second mutation')):
            self.assertFalse(provider.seek_playback_position(50))
