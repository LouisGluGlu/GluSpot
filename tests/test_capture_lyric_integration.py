"""Accepted model discontinuities must reach both existing lyric consumers."""
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from api.capture.runtime import CaptureRuntime
from api.models import TimedLyricLine, TimedLyricUnit
from api.providers import WindowsMediaProvider
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
