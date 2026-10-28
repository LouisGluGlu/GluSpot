"""Mock-native event, polling, lifecycle and provider-thread integration."""
import asyncio
import threading
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from api.capture.contracts import TimelineSample
from api.capture.runtime import CaptureRuntime
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
