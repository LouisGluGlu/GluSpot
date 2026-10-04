from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from api.audio_routes import AudioRouteSnapshot
from local.island_stack_visual import IslandStackVisual
from local.music_lyric_motion import MusicLyricMotion
from local.music_output_clock import OutputLatencyClock
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_lyric_visual_layers import _LayerOwner
from tests.test_music_obo_handoff import _Owner as _OboOwner


class ContinuousOutputCompensationTests(unittest.TestCase):
    def test_batched_estimates_do_not_stop_or_reverse_the_real_word_clock(self):
        for hz in (30, 60, 120):
            with self.subTest(hz=hz):
                clock, motion = OutputLatencyClock(), MusicLyricMotion()
                previous = None
                # Source correction and 150ms/500ms estimate delivery share
                # the actual motion path, rather than testing only a filter.
                for frame in range(8 * hz + 1):
                    seconds = frame / hz
                    target = 1.5 + min(0.25, int(seconds / 0.15) * 0.012)
                    delay = clock.sample(target, 100 + seconds, "group", playing=True)
                    source = 10 + seconds - (0.035 if 1 < seconds < 3 else 0)
                    motion.frame([(0, "long sentence"), (30, "next sentence")], source,
                                 100 + seconds, "song", sample_id=frame,
                                 sample_position=source, presentation_offset=-delay)
                    if previous is not None:
                        advance = motion.last_position - previous
                        self.assertGreater(advance, 0)
                        self.assertLessEqual(advance, 1.42 / hz + 1e-9)
                    previous = motion.last_position
                self.assertAlmostEqual(clock.value, 1.75, places=7)
                self.assertAlmostEqual(motion.last_source_position, 18, places=5)

    def test_new_observation_does_not_retroactively_move_a_repeated_frame(self):
        clock = OutputLatencyClock()
        self.assertEqual(clock.sample(1.5, 100, "one", playing=True), 1.5)
        self.assertEqual(clock.sample(1.52, 100.5, "one", playing=True), 1.5)
        first = clock.sample(1.52, 100.51, "one", playing=True)
        self.assertGreater(first, 1.5)
        self.assertEqual(clock.sample(1.52, 100.51, "one", playing=True), first)
        self.assertEqual(clock.sample(1.9, 100.51, "other", playing=True), 1.9)
        self.assertEqual(clock.sample(1.93, 100.52, "other", playing=False), 1.93)
        self.assertEqual(clock.sample(0, 100.53, None, playing=True), 0)


class SharedViewCompensationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_automatic_slew_keeps_focus_pose_but_manual_offset_retargets_it(self):
        owner = _LayerOwner()
        owner.media_position_source = "windows-media"
        owner.media_source_app = "cloudmusic.exe"
        owner.media_raw_position_seconds = owner.media_position_seconds = 8.0
        owner.media_position_sampled_at = 100.0
        owner.media_duration_seconds = 120
        owner.lyric_offset_seconds = owner.media_app_lyric_offset_seconds = 0
        owner.timed_lyrics = [(0.0, "one continuous sentence"), (20.0, "next")]
        route = [AudioRouteSnapshot(state="casting", active_id="remote", group_id="group",
                                    cast_latency_seconds=1.5)]
        owner.data_provider = SimpleNamespace(get_audio_route_snapshot=lambda: route[0])
        owner._effective_lyric_offset_seconds = lambda include_output_latency=True: IslandStackVisual._effective_lyric_offset_seconds(owner, include_output_latency)
        owner._estimated_media_position = lambda now: 8 + now - 100
        window = MusicSurfaceWindow(owner)
        try:
            with patch("local.island_stack_visual.time.monotonic", return_value=100):
                window._lyric_frame(owner.timed_lyrics, 100)
            window._obo_advanced_focus_index = 0
            window._obo_advanced_focus_pose = (1.0, 2.0)
            route[0] = replace(route[0], cast_latency_seconds=1.53)
            with patch("local.island_stack_visual.time.monotonic", return_value=100.5):
                window._lyric_frame(owner.timed_lyrics, 100.5)
            with patch("local.island_stack_visual.time.monotonic", return_value=100.52):
                window._lyric_frame(owner.timed_lyrics, 100.52)
            self.assertEqual(window._obo_advanced_focus_index, 0)
            self.assertEqual(window._obo_advanced_focus_pose, (1.0, 2.0))
            owner.lyric_offset_seconds = -0.03
            with patch("local.island_stack_visual.time.monotonic", return_value=100.54):
                window._lyric_frame(owner.timed_lyrics, 100.54)
            self.assertIsNone(window._obo_advanced_focus_index)
            self.assertIsNone(window._obo_advanced_focus_pose)
            self.assertAlmostEqual(window._lyric_motion.last_source_position, 8.54)
            self.assertIsNone(window._lyric_motion.pending_clock_position)
        finally:
            window.shutdown()
            owner.close()


class OboCompensationRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _OboOwner()
        self.route = AudioRouteSnapshot(state="ready")
        self.owner.data_provider = SimpleNamespace(get_audio_route_snapshot=lambda: self.route)
        self.owner._estimated_media_position = lambda now: self.owner.media_position_seconds
        self.owner._effective_lyric_offset_seconds = lambda include_output_latency=True: (
            IslandStackVisual._effective_lyric_offset_seconds(self.owner, include_output_latency))
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.image = QImage(960, 540, QImage.Format.Format_RGB32)
        self.addCleanup(self.owner.close)
        self.addCleanup(self.window.shutdown)

    def _render(self, position, now):
        self.owner.media_position_seconds = position
        self.owner.media_raw_position_seconds = position
        self.owner.media_position_observed_seconds = position
        self.owner.media_position_sampled_at = now
        with patch("local.music_surface_window.time.monotonic", return_value=now), \
                patch.object(self.window, "_lyric_view_anchor", wraps=self.window._lyric_view_anchor) as anchor:
            self.window.render(self.image)
        return anchor.call_args.args[0]

    def test_paused_delay_edit_returns_focus_to_the_unfinished_real_unit_row(self):
        self.route = AudioRouteSnapshot(state="casting", active_id="remote", cast_source_id="local",
                                        cast_latency_seconds=1.5)
        self.assertEqual(self._render(2.8, 100), 1)
        self.assertIsNotNone(self.window._obo_advanced_focus_pose)
        self.owner.media_is_playing = False
        self.route = replace(self.route, cast_latency_seconds=1.8)
        self.assertEqual(self._render(2.8, 100.1), 0)
        self.assertAlmostEqual(self.window._lyric_motion.last_source_position, 2.8)
        self.assertAlmostEqual(self.window._lyric_motion.last_position, 1.0)
        self.assertIsNone(self.window._obo_advanced_focus_pose)

    def test_confirmed_external_seek_within_one_row_retires_early_focus(self):
        self.owner.timed_lyrics = [(0.0, "Hello world"), (12.0, "Next sentence has four words"),
                                  (18.0, "Later sentence"), (21.0, "Final sentence")]
        self.assertEqual(self._render(8.0, 100), 1)
        self.assertEqual(self._render(.5, 100.1), 1)
        self.assertIsNotNone(self.window._lyric_motion.pending_clock_position)
        self.assertEqual(self._render(.6, 100.2), 0)
        self.assertIsNone(self.window._lyric_motion.pending_clock_position)
        self.assertAlmostEqual(self.window._lyric_motion.last_source_position, .6)
        self.assertEqual(self.window._lyric_motion.active_index, 0)
        self.assertIsNone(self.window._obo_advanced_focus_pose)

    def test_playing_estimate_slew_preserves_the_actual_early_focus_pose(self):
        self.route = AudioRouteSnapshot(state="casting", active_id="remote", cast_source_id="local",
                                        cast_latency_seconds=1.5)
        self.assertEqual(self._render(2.8, 100), 1)
        original = self.window._obo_advanced_focus_pose
        self.route = replace(self.route, cast_latency_seconds=1.53)
        for position, now in ((2.82, 100.02), (2.84, 100.04), (2.86, 100.06)):
            self.assertEqual(self._render(position, now), 1)
            pose = self.window._obo_advanced_focus_pose
            self.assertEqual(pose[0], original[0])
            self.assertGreaterEqual(pose[-1], original[-1])
        self.assertGreater(self.window._lyric_motion.last_position, 1.3)
