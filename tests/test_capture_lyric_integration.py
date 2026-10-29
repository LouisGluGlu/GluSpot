"""Accepted model discontinuities must reach both existing lyric consumers."""
import asyncio
import threading
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PySide6.QtTest import QTest

from api.capture.runtime import CaptureRuntime
from api.models import TimedLyricLine, TimedLyricUnit
from api.providers import EmptyIslandDataProvider, PlaybackSourceProvider, WindowsMediaProvider
from local.lyric_unit_timing import timed_unit_reading_front
from local.music_surface_window import MusicSurfaceWindow
from tests import test_music_playback_sync as playback_tests
from tests.capture_async import run_async
from tests.test_capture_cef import Clock, FakeCdp
from tests.test_capture_smtc import Session, matches


class CaptureLyricIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        playback_tests.MusicPlaybackClockTests.setUpClass()

    def test_sparse_external_seek_reanchors_island_and_large_surface(self):
        # Reuse the real V2 owner fixture and its lifecycle cleanup.
        visual, _data, _track = playback_tests.MusicPlaybackClockTests._owner(self)
        surface = MusicSurfaceWindow(visual)
        self.addCleanup(surface.shutdown)
        visual.media_position_source = 'capture-model'
        visual.media_position_sampled_at = 100
        visual.media_position_observed_seconds = 30
        surface._lyric_frame([(0, 'first'), (40, 'second')], 100)
        visual._stable_lyric_source_position(100)
        for playing in (True, False):
            with self.subTest(playing=playing):
                visual.media_is_playing = playing
                visual.media_position_seconds = 60
                visual.media_position_sync_at = 101
                visual.media_position_sampled_at = 101
                visual.media_position_observed_seconds = 60
                self.assertAlmostEqual(visual._stable_lyric_source_position(101), 60)
                surface._lyric_frame([(0, 'first'), (40, 'second')], 101)
                self.assertAlmostEqual(surface._lyric_motion.last_source_position, 60)

    def test_cef_projection_reaches_line_and_word_clocks_with_zero_smtc(self):
        """A provider frame must carry physical CEF time into both lyric views."""
        visual, data, track = playback_tests.MusicPlaybackClockTests._owner(self)
        units = (TimedLyricUnit(40, 42, 'Two '), TimedLyricUnit(42, 44, 'words'))
        track = replace(track, position_seconds=0, source_app_id='cloudmusic.exe',
                        timed_lyrics=(TimedLyricLine(0, 'First'),
                                      TimedLyricLine(40, 'Two words', units, 'unit'),
                                      TimedLyricLine(50, 'Third')))
        data = replace(data, track=track)
        surface = MusicSurfaceWindow(visual)
        self.addCleanup(surface.shutdown)

        async def exercise():
            clock = Clock()
            cdp = FakeCdp(clock)
            cdp.latest = cdp.payload(39)
            runtime = CaptureRuntime(clock=clock, cef_session=cdp)
            provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
            provider.settings_getter = lambda key, default=None: (
                True if key == 'v2_capture_position_enabled' else default)
            provider._capture_runtime = runtime
            session = Session()
            session.get_timeline_properties = lambda: SimpleNamespace(
                position=0, start_time=0, end_time=0)
            try:
                await runtime.bind(session, track, matches, mode='hybrid')
                for timestamp, position, index, front in (
                    (100, 39, 0, None), (100.25, 41, 1, 2),
                    (100.5, 43, 1, 6.5), (100.75, 51, 2, None),
                    # A fresh backwards native seek must also reach both views.
                    (101, 41, 1, 2),
                ):
                    clock.value = timestamp
                    cdp.emit(cdp.payload(position))
                    projected = provider._project_visual_position(data, timestamp, timestamp)
                    self.assertEqual(runtime.diagnostics()['position_route'], 'cef')
                    self.assertEqual(projected.track.position_source, 'capture-model')
                    self.assertEqual(projected.track.position_seconds, position)
                    with patch('local.island_stack_visual.time.monotonic', return_value=timestamp):
                        visual._apply_visual_data(projected)
                    visual._sync_timed_lyrics(timestamp, allow_backward=True)
                    self.assertEqual(visual.lyric_index, index)
                    self.assertAlmostEqual(visual._stable_lyric_source_position(timestamp), position)
                    frame = surface._lyric_frame(visual.timed_lyrics, timestamp)
                    self.assertEqual(frame.active_index, index)
                    self.assertAlmostEqual(surface._lyric_motion.last_source_position, position)
                    if front is not None:
                        # The renderer's unit-reading helper consumes this same
                        # position and the source units retained by the owner.
                        self.assertEqual(visual.timed_lyric_units[index], units)
                        self.assertAlmostEqual(timed_unit_reading_front(
                            'Two words', visual.timed_lyric_units[index],
                            surface._lyric_motion.last_position, range(10)), front)
            finally:
                await runtime.close()

        run_async(exercise())

    def test_cef_through_shared_provider_and_owner_tick_reaches_narrow(self):
        from _demos.narrow3.narrow_window import NarrowStudyWindow

        owner, data, track = playback_tests.MusicPlaybackClockTests._owner(self)
        track = replace(track, source_app_id='cloudmusic.exe', position_seconds=0,
                        timed_lyrics=(TimedLyricLine(0, 'First'),
                                      TimedLyricLine(40, 'Second')))
        data = replace(data, track=track)
        system = EmptyIslandDataProvider(data)
        owned = Mock()
        owned.track = None
        settings = lambda key, default=None: {
            'v2_owned_playback_enabled': False,
            'v2_system_capture_enabled': True,
        }.get(key, default)
        with patch("local.owned_audio_bridge.OwnedAudioBridge"):
            owner.data_provider = PlaybackSourceProvider(system, owned, settings)
        surface = NarrowStudyWindow(owner, 'B', 'left', live=True)
        self.addCleanup(surface.close)
        self.addCleanup(surface.shutdown)
        owner.music_fullscreen_overlay = surface
        owner._music_surface_active = True
        owner.timer.start()
        surface.show()
        playback_tests.MusicPlaybackClockTests.app.processEvents()

        async def exercise():
            clock = Clock()
            cdp = FakeCdp(clock)
            cdp.latest = cdp.payload(39)
            runtime = CaptureRuntime(clock=clock, cef_session=cdp)
            provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
            provider.settings_getter = lambda key, default=None: (
                True if key == 'v2_capture_position_enabled' else default)
            provider._capture_runtime = runtime
            session = Session()
            session.get_timeline_properties = lambda: SimpleNamespace(position=0, start_time=0, end_time=0)
            try:
                await runtime.bind(session, track, matches, mode='hybrid')
                for timestamp, position, index in ((100, 39, 0), (101, 41, 1), (102, 45, 1)):
                    clock.value = timestamp
                    cdp.emit(cdp.payload(position))
                    system.visual_data = provider._project_visual_position(data, timestamp, timestamp)
                    owner.last_media_refresh_at = 0
                    with patch('local.island_stack_visual.time.monotonic', return_value=timestamp):
                        self.assertTrue(owner.timer.isActive())
                        owner.timer.timeout.emit()
                        self.assertEqual(owner.media_position_source, 'capture-model')
                        self.assertEqual(owner.lyric_index, index)
                        self.assertEqual(surface.lyric_focus()[0], index)
                        self.assertAlmostEqual(surface.playback_position(), owner.lyric_last_sync_position)
                        self.assertAlmostEqual(surface.playback_position(), position)
            finally:
                await runtime.close()

        run_async(exercise())

    def test_word_target_uses_provider_cef_seek_and_rejects_replaced_track(self):
        """The lyric command retains the provider's physical route and fence."""
        visual, data, track = playback_tests.MusicPlaybackClockTests._owner(self)
        units = (TimedLyricUnit(40, 42, 'Two '), TimedLyricUnit(42, 44, 'words'))
        track = replace(track, position_seconds=0, source_app_id='cloudmusic.exe',
                        timed_lyrics=(TimedLyricLine(0, 'First'),
                                      TimedLyricLine(40, 'Two words', units, 'unit'),
                                      TimedLyricLine(50, 'Third')))
        data = replace(data, track=track)
        visual._apply_visual_data(data)
        deadline = time.perf_counter() + 3
        while visual._lyric_presentation_store.get() is None and time.perf_counter() < deadline:
            QTest.qWait(5)
        self.assertIsNotNone(visual._lyric_presentation_store.get())
        visual._effective_lyric_offset_seconds = lambda **_kwargs: 1.5
        callbacks = []
        visual._run_background = lambda _name, callback: callbacks.append(callback)
        surface = MusicSurfaceWindow(visual)
        self.addCleanup(surface.shutdown)
        surface._lyric_frame(visual.timed_lyrics, 100)

        async def exercise():
            clock = Clock()
            cdp = FakeCdp(clock)
            runtime = CaptureRuntime(clock=clock, cef_session=cdp)
            provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
            provider.settings_getter = lambda key, default=None: (
                True if key == 'v2_capture_position_enabled' else default)
            provider._capture_runtime = runtime
            provider._capture_executor = runtime.executor
            provider._last_data = data
            provider._loop = asyncio.get_running_loop()
            provider._thread = threading.current_thread()
            provider._request_immediate_media_refresh = Mock()
            session = Session()
            session.get_timeline_properties = lambda: SimpleNamespace(
                position=0, start_time=0, end_time=0)
            session.try_change_playback_position_async = AsyncMock(
                side_effect=AssertionError('CEF route must own the mutation'))
            provider._get_session = AsyncMock(return_value=session)
            provider._seek_session_matches_track = AsyncMock(return_value=True)
            visual.data_provider = provider
            try:
                await runtime.bind(session, track, matches, mode='smtc')
                surface._seek_to_lyric(1, 42.0)
                self.assertEqual(len(callbacks), 1)
                await asyncio.to_thread(callbacks.pop())
                playback_tests.MusicPlaybackClockTests.app.processEvents()
                self.assertEqual(len(cdp.writes), 1)
                self.assertEqual(cdp.writes[0][0], 'native-song')
                self.assertEqual(cdp.writes[0][2], 40.5)
                self.assertEqual(runtime.executor.last_result.backend, 'cef')
                self.assertTrue(runtime.executor.last_result.succeeded)
                session.try_change_playback_position_async.assert_not_awaited()
                provider._request_immediate_media_refresh.assert_called_once()

                # A click queued for the old UI must not seek its replacement.
                surface._seek_to_lyric(1, 42.0)
                self.assertEqual(len(callbacks), 1)
                provider._last_data = replace(data, track=replace(track, track_id='replacement'))
                await asyncio.to_thread(callbacks.pop())
                playback_tests.MusicPlaybackClockTests.app.processEvents()
                self.assertEqual(len(cdp.writes), 1)
                self.assertFalse(surface._seek_pending)
            finally:
                await runtime.close()

        run_async(exercise())
