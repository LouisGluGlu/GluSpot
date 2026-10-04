from __future__ import annotations

from dataclasses import replace
import math
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from api.audio_routes import AudioRouteSnapshot
from api.lyric_timeline import active_index_for_position
from local.lyric_unit_timing import timed_unit_reading_front
from local.music_lyric_motion import MusicLyricMotion
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_lyric_visual_layers import _LayerOwner


class MusicOutputClockTests(unittest.TestCase):
    def test_small_source_corrections_keep_every_playing_frame_advancing(self):
        rows = [(0.0, "one sustained sentence"), (30.0, "next sentence")]
        for hz in (30, 60, 120):
            for correction in (-0.035, 0.035):
                with self.subTest(hz=hz, correction=correction):
                    motion = MusicLyricMotion()
                    previous_position = previous_progress = None
                    for frame_index in range(hz * 5 + 1):
                        elapsed = frame_index / hz
                        # The provider delivers a fresh event twice per second
                        # and projects it between paints. A correction changes
                        # its anchor, while actual playback remains continuous.
                        sample_elapsed = (frame_index // (hz // 2)) / 2.0
                        bias = correction if 1.0 <= sample_elapsed < 2.0 else 0.0
                        observed = 10.0 + sample_elapsed + bias
                        projected = observed + elapsed - sample_elapsed
                        frame = motion.frame(rows, projected, 100.0 + elapsed, "song",
                                             sample_id=100.0 + sample_elapsed,
                                             sample_position=observed)
                        position = motion.last_source_position
                        if previous_position is not None:
                            self.assertGreater(position, previous_position)
                            self.assertGreater(frame.reading_progress, previous_progress)
                            self.assertLess(abs(position - (10.0 + elapsed)), 0.05)
                        previous_position, previous_progress = position, frame.reading_progress
                    # Following the real clock again must repay the correction;
                    # uninterrupted prediction cannot accumulate a new offset.
                    self.assertAlmostEqual(motion.last_source_position, 15.0, delta=0.0001)
                    self.assertIsNone(motion.pending_clock_position)

    def test_alternating_source_noise_does_not_accumulate_clock_drift(self):
        rows = [(0.0, "one sustained sentence"), (90.0, "next sentence")]
        for hz in (30, 60, 120):
            with self.subTest(hz=hz):
                motion = MusicLyricMotion()
                previous = None
                for frame_index in range(hz * 12 + 1):
                    elapsed = frame_index / hz
                    sample_number = frame_index // (hz // 2)
                    sample_elapsed = sample_number / 2.0
                    bias = (0.035 if sample_number % 2 else -0.035) if 1 <= elapsed < 10 else 0.0
                    observed = 10.0 + sample_elapsed + bias
                    position = observed + elapsed - sample_elapsed
                    motion.frame(rows, position, 100.0 + elapsed, "song",
                                 sample_id=sample_number, sample_position=observed)
                    if previous is not None:
                        self.assertGreater(motion.last_source_position, previous)
                    self.assertLess(abs(motion.last_source_position - (10.0 + elapsed)), 0.05)
                    previous = motion.last_source_position
                self.assertAlmostEqual(motion.last_source_position, 22.0, delta=0.0001)

    def test_small_clock_correction_cannot_advance_a_paused_source(self):
        motion = MusicLyricMotion()
        rows = [(0.0, "one sustained sentence"), (30.0, "next sentence")]
        motion.frame(rows, 10.0, 100.0, "song", playing=False,
                     sample_id=100.0, sample_position=10.0)
        for elapsed in (0.1, 0.2, 1.0, 2.0):
            motion.frame(rows, 9.965, 100.0 + elapsed, "song", playing=False,
                         sample_id=100.1, sample_position=9.965)
            self.assertEqual(motion.last_source_position, 10.0)
        motion.frame(rows, 9.965, 102.1, "song", playing=False, explicit_seek=True,
                     sample_id=102.1, sample_position=9.965)
        self.assertEqual(motion.last_source_position, 9.965)

    def test_cast_delay_changes_retarget_reading_without_changing_source_clock(self):
        motion = MusicLyricMotion()
        rows = [(index * 0.5, f"Sentence {index}") for index in range(80)]
        motion.frame(rows, 8.2, 100.0, "song", sample_id=100.0, sample_position=8.2)
        for now, position, delay in ((100.016, 8.216, 1.5), (100.1, 8.3, 2.5),
                                     (100.2, 8.4, 0.25), (100.35, 8.55, 0.0)):
            with self.subTest(delay=delay):
                before_pose = (motion.scroll_index, motion.velocity)
                frame = motion.frame(rows, position, now, "song", sample_id=now,
                                     sample_position=position, presentation_offset=-delay)
                self.assertAlmostEqual(motion.last_source_position, position)
                self.assertAlmostEqual(motion.last_position, position - delay)
                self.assertEqual(frame.active_index, active_index_for_position(rows, position - delay))
                self.assertIsNone(motion.pending_clock_position)
                repeated = motion.frame(rows, position, now, "song", sample_id=now,
                                        sample_position=position, presentation_offset=-delay)
                self.assertEqual((repeated.scroll_index, repeated.scroll_velocity),
                                 (frame.scroll_index, frame.scroll_velocity))
                self.assertLess(abs(frame.scroll_index - before_pose[0]), 1.0)

    def test_output_delay_does_not_confirm_or_clear_pending_source_jump(self):
        motion = MusicLyricMotion()
        rows = [(index * 4.0, str(index)) for index in range(40)]
        motion.frame(rows, 10.0, 100.0, "song", sample_id="base", sample_position=10.0)
        motion.frame(rows, 50.0, 100.1, "song", sample_id="bad", sample_position=50.0)
        pending = (motion.pending_clock_position, motion.pending_clock_sample,
                   motion.pending_clock_at, motion.pending_clock_sample_position)
        frame = motion.frame(rows, 50.1, 100.2, "song", sample_id="bad", sample_position=50.0,
                             presentation_offset=-1.5, presentation_reanchor=True)
        self.assertAlmostEqual(motion.last_source_position, 10.2)
        self.assertAlmostEqual(motion.last_position, 8.7)
        self.assertEqual(frame.active_index, 2)
        self.assertEqual((motion.pending_clock_position, motion.pending_clock_sample,
                          motion.pending_clock_at, motion.pending_clock_sample_position), pending)

    def test_stamp_updates_and_interpolation_cannot_confirm_frozen_wrong_position(self):
        rows = [(index * 4.0, str(index)) for index in range(40)]
        for projected in (False, True):
            with self.subTest(projected=projected):
                motion = MusicLyricMotion()
                motion.frame(rows, 10.0, 100.0, "song", sample_id=100.0, sample_position=10.0)
                for now in (100.1, 100.75, 101.4, 102.05):
                    candidate = 50.0 + now - 100.1 if projected else 50.0
                    frame = motion.frame(rows, candidate, now, "song", sample_id=now,
                                         sample_position=50.0)
                    self.assertAlmostEqual(motion.last_source_position, 10.0 + now - 100.0)
                    self.assertLessEqual(frame.active_index, 3)
                # A real readback that has advanced along the candidate
                # timeline remains able to establish an external seek.
                confirmed = motion.frame(rows, 52.6, 102.7, "song", sample_id=102.7,
                                         sample_position=52.6)
                if not projected:
                    confirmed = motion.frame(rows, 53.25, 103.35, "song", sample_id=103.35,
                                             sample_position=53.25)
                self.assertEqual(confirmed.active_index, 13)
                self.assertAlmostEqual(motion.last_source_position, 52.6 if projected else 53.25)

    def test_tiny_raw_position_noise_does_not_corroborate_a_multi_row_jump(self):
        motion = MusicLyricMotion()
        rows = [(index * 4.0, str(index)) for index in range(40)]
        motion.frame(rows, 10.0, 100.0, "song", sample_id=100.0, sample_position=10.0)
        motion.frame(rows, 50.0, 100.1, "song", sample_id=100.1, sample_position=50.0)
        frame = motion.frame(rows, 50.65, 100.75, "song", sample_id=100.75,
                             sample_position=50.001)
        self.assertEqual(frame.active_index, 2)
        self.assertAlmostEqual(motion.last_source_position, 10.75)

    def test_unknown_observation_holds_then_real_source_evidence_can_confirm(self):
        rows = [(index * 4.0, str(index)) for index in range(40)]
        for playing in (False, True):
            with self.subTest(playing=playing):
                motion = MusicLyricMotion()
                motion.frame(rows, 10.0, 100.0, "song", sample_id=100.0,
                             sample_position=10.0, playing=playing)
                for now in (100.1, 100.75):
                    position = 50.0 + (now - 100.1 if playing else 0.0)
                    motion.frame(rows, position, now, "song", sample_id=now,
                                 sample_position=float("nan"), playing=playing)
                    self.assertAlmostEqual(motion.last_source_position,
                                           10.0 + (now - 100.0 if playing else 0.0))
                candidate = 50.7 if playing else 50.0
                motion.frame(rows, candidate, 100.8, "song", sample_id=100.8,
                             sample_position=candidate, playing=playing)
                self.assertEqual(motion.pending_clock_sample, 100.8)
                confirmed = candidate + (0.1 if playing else 0.0)
                motion.frame(rows, confirmed, 100.9, "song", sample_id=100.9,
                             sample_position=confirmed, playing=playing)
                self.assertAlmostEqual(motion.last_source_position, confirmed)

    def test_first_pause_sample_cannot_confirm_a_playing_spike_candidate(self):
        motion = MusicLyricMotion()
        rows = [(index * 4.0, str(index)) for index in range(40)]
        motion.frame(rows, 10.0, 100.0, "song", sample_id=100.0, sample_position=10.0)
        motion.frame(rows, 50.0, 100.1, "song", sample_id=100.1, sample_position=50.0)
        motion.frame(rows, 50.65, 100.75, "song", sample_id=100.75, sample_position=50.0)
        paused = motion.frame(rows, 50.65, 100.76, "song", sample_id=100.75,
                              sample_position=50.0, playing=False)
        self.assertEqual(paused.active_index, 2)
        self.assertAlmostEqual(motion.last_source_position, 10.75)
        self.assertFalse(motion.pending_clock_playing)

    def test_real_backward_source_seek_and_output_delay_have_independent_positions(self):
        motion = MusicLyricMotion()
        rows = [(0.0, "long sentence"), (12.0, "next sentence")]
        motion.frame(rows, 8.0, 100.0, "song", sample_id=100.0, sample_position=8.0,
                     presentation_offset=-1.5)
        motion.frame(rows, 3.0, 100.04, "song", sample_id=100.04, sample_position=3.0,
                     presentation_offset=-1.5)
        confirmed = motion.frame(rows, 3.08, 100.12, "song", sample_id=100.12,
                                 sample_position=3.08, presentation_offset=-1.5)
        self.assertAlmostEqual(motion.last_source_position, 3.08)
        self.assertAlmostEqual(motion.last_position, 1.58)
        self.assertLess(confirmed.reading_progress, 0.15)

    def test_app_and_adapter_handoffs_cannot_confirm_each_others_pending_clock(self):
        rows = [(index * 4.0, str(index)) for index in range(40)]
        for next_generation in (("PlayerB", "windows-media"), ("PlayerA", "qq-uia-lyrics")):
            with self.subTest(generation=next_generation):
                motion = MusicLyricMotion()
                generation = ("PlayerA", "windows-media")
                motion.frame(rows, 10.0, 100.0, "song", sample_id=100.0,
                             sample_position=10.0, source_generation=generation)
                candidate = motion.frame(rows, 50.0, 100.1, "song", sample_id=100.1,
                                         sample_position=50.0, source_generation=generation)
                handoff = motion.frame(rows, 50.0, 100.1, "song", sample_id=100.1,
                                       sample_position=50.0, source_generation=next_generation)
                self.assertEqual((candidate.scroll_index, candidate.scroll_velocity),
                                 (handoff.scroll_index, handoff.scroll_velocity))
                # A genuinely fresh first read in another generation starts
                # its own candidate; evidence from the old app/adapter cannot
                # count as this source's confirmation.
                other_generation = ("PlayerC", next_generation[1])
                changed = motion.frame(rows, 50.7, 100.8, "song", sample_id=100.8,
                                       sample_position=50.7, source_generation=other_generation)
                self.assertAlmostEqual(motion.last_source_position, 10.8)
                self.assertEqual(changed.active_index, 2)
                self.assertEqual(motion.pending_clock_sample, 100.8)
                confirmed = motion.frame(rows, 50.8, 100.9, "song", sample_id=100.9,
                                         sample_position=50.8, source_generation=other_generation)
                self.assertEqual(confirmed.active_index, 12)

    def test_same_sentence_delay_edit_reanchors_word_front_without_clock_seek(self):
        motion = MusicLyricMotion()
        rows = [(0.0, "one sentence"), (12.0, "next sentence")]
        initial = motion.frame(rows, 8.0, 100.0, "song", sample_id=100.0, sample_position=8.0)
        shifted = motion.frame(rows, 8.0, 100.0, "song", sample_id=100.0, sample_position=8.0,
                               presentation_offset=-1.5)
        self.assertAlmostEqual(shifted.reading_progress, 6.5 / 12.0)
        self.assertLess(shifted.reading_progress, initial.reading_progress)
        self.assertEqual(motion.last_source_position, 8.0)
        self.assertEqual((initial.scroll_index, initial.scroll_velocity),
                         (shifted.scroll_index, shifted.scroll_velocity))
        self.assertIsNone(motion.pending_clock_position)

    def test_coarse_ceiling_limits_source_before_presentation_offset(self):
        motion = MusicLyricMotion()
        rows = [(20.0, "observed row"), (24.0, "next row")]
        for step in range(101):
            now = 100.0 + step / 20.0
            frame = motion.frame(rows, min(23.0 + step / 20.0, 23.92), now, "song",
                                 sample_id="coarse-row", sample_position=23.0,
                                 position_ceiling=23.92, presentation_offset=-1.5)
            self.assertLessEqual(motion.last_source_position, 23.92)
            self.assertLessEqual(motion.last_position, 22.42)
            self.assertEqual(frame.active_index, 0)

    def test_delay_before_first_word_does_not_reset_the_source_anchor_to_zero(self):
        motion = MusicLyricMotion()
        rows = [(0.0, "first sentence"), (4.0, "next sentence")]
        for step in range(121):
            source = 0.2 + step / 60.0
            now = 100.0 + step / 60.0
            motion.frame(rows, source, now, "song", sample_id=now, sample_position=source,
                         presentation_offset=-1.5)
            self.assertAlmostEqual(motion.last_source_position, source)
            self.assertAlmostEqual(motion.last_position, max(0.0, source - 1.5))
            self.assertIsNone(motion.pending_clock_position)


class MusicSurfaceOutputClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _LayerOwner()
        self.owner.media_position_source = "windows-media"
        self.owner.media_position_sampled_at = 100.0
        self.owner.media_raw_position_seconds = 8.2
        self.owner.media_position_seconds = 8.2
        self.owner.media_duration_seconds = 180.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.route = AudioRouteSnapshot(state="ready")
        self.owner.data_provider = SimpleNamespace(get_audio_route_snapshot=lambda: self.route)
        self.owner._effective_lyric_offset_seconds = self._offset
        self.owner.enable_dual_line_lyrics = False
        self.owner.music_surface_reading_strategy = "low_perception"
        self.owner.timed_lyrics = [(index * 2.0, f"这是第 {index} 句歌词") for index in range(30)]
        self.owner.timed_lyric_units = [()] * 30
        self.owner.timed_lyric_timing_kinds = ["line"] * 30
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _offset(self, include_output_latency=True):
        return -self.route.cast_latency_seconds if include_output_latency and self.route.state == "casting" else 0.0

    def _observe(self, position, now, raw=None):
        self.owner.media_position_seconds = position
        self.owner.media_raw_position_seconds = position if raw is None else raw
        self.owner.media_position_sampled_at = now

    def _frame(self, now):
        return self.window._lyric_frame(self.owner.timed_lyrics, now)

    def test_continuous_cast_start_slider_stop_never_become_source_seeks(self):
        self._frame(100.0)
        for now, raw, state, delay in ((100.016, 8.216, "casting", 1.5),
                                       (100.1, 8.3, "casting", 2.5),
                                       (100.2, 8.4, "casting", 0.25),
                                       (100.3, 8.5, "ready", 0.25)):
            self._observe(raw, now)
            self.route = replace(self.route, state=state, cast_latency_seconds=delay)
            with patch.object(self.window._lyric_motion, "frame", wraps=self.window._lyric_motion.frame) as motion:
                frame = self._frame(now)
            self.assertFalse(motion.call_args.kwargs["explicit_seek"])
            self.assertEqual(motion.call_args.args[1], raw)
            self.assertEqual(motion.call_args.kwargs["sample_position"], raw)
            self.assertAlmostEqual(self.window._lyric_motion.last_position, raw + self._offset())
            self.assertAlmostEqual(self.window._lyric_motion.last_source_position, raw)
            self.assertEqual(frame.active_index, active_index_for_position(self.owner.timed_lyrics, raw + self._offset()))
            self.assertIsNone(self.window._lyric_motion.pending_clock_position)

    def test_surface_timestamp_refresh_cannot_turn_frozen_observation_into_seek(self):
        self._frame(100.0)
        for now in (100.1, 100.75, 101.4):
            self._observe(50.0 + now - 100.1, now, raw=50.0)
            frame = self._frame(now)
            self.assertLess(frame.active_index, 6)
            self.assertAlmostEqual(self.window._lyric_motion.last_source_position, 8.2 + now - 100.0)

    def test_surface_uses_true_observation_instead_of_projected_raw_position(self):
        self.owner.media_position_observed_seconds = 8.2
        self._frame(100.0)
        self.owner.media_position_observed_seconds = 50.0
        for now in (100.1, 100.75, 101.4):
            projected = 50.0 + now - 100.1
            self._observe(projected, now, raw=projected)
            with patch.object(self.window._lyric_motion, "frame", wraps=self.window._lyric_motion.frame) as motion:
                frame = self._frame(now)
            self.assertEqual(motion.call_args.kwargs["sample_position"], 50.0)
            self.assertLess(frame.active_index, 6)
            self.assertAlmostEqual(self.window._lyric_motion.last_source_position, 8.2 + now - 100.0)

    def test_surface_explicit_unknown_observation_does_not_fallback_to_projected_raw(self):
        self.owner.media_position_observed_seconds = None
        self._frame(100.0)
        for now in (100.1, 100.75, 101.4):
            projected = 50.0 + now - 100.1
            self._observe(projected, now, raw=projected)
            with patch.object(self.window._lyric_motion, "frame", wraps=self.window._lyric_motion.frame) as motion:
                frame = self._frame(now)
            self.assertTrue(math.isnan(motion.call_args.kwargs["sample_position"]))
            self.assertLess(frame.active_index, 6)
            self.assertAlmostEqual(self.window._lyric_motion.last_source_position, 8.2 + now - 100.0)

    def test_unknown_paused_observation_never_becomes_seek_but_verified_local_seek_still_works(self):
        self.owner.media_position_observed_seconds = None
        self.owner.media_is_playing = False
        self._frame(100.0)
        for now in (100.1, 100.75, 101.4):
            self._observe(50.0, now)
            with patch.object(self.window._lyric_motion, "frame", wraps=self.window._lyric_motion.frame) as motion:
                frame = self._frame(now)
            self.assertFalse(motion.call_args.kwargs["explicit_seek"])
            self.assertEqual(frame.active_index, 4)
            self.assertEqual(self.window._lyric_motion.last_source_position, 8.2)
        self.owner.media_position_source = "local-seek"
        self._observe(28.0, 101.5)
        self._frame(101.5)
        self.assertEqual(self.window._lyric_motion.last_source_position, 28.0)

    def test_surface_same_song_app_handoff_does_not_reuse_paused_event_or_seek_target(self):
        self.owner.media_source_app = "PlayerA"
        self.owner.media_is_playing = False
        self._frame(100.0)
        self.window._pending_backward_seek = (28.0, 110.0)
        self.window._pending_backward_seek_app = "PlayerA"
        self.owner.media_source_app = "PlayerB"
        self._observe(28.0, 100.1)
        with patch.object(self.window._lyric_motion, "frame", wraps=self.window._lyric_motion.frame) as motion:
            handed_off = self._frame(100.1)
        self.assertFalse(motion.call_args.kwargs["explicit_seek"])
        self.assertEqual(motion.call_args.kwargs["source_generation"], ("PlayerB", "windows-media"))
        self.assertEqual(handed_off.active_index, 4)
        self.assertEqual(self.window._lyric_motion.last_source_position, 8.2)
        self.assertIsNone(self.window._pending_backward_seek)
        self._observe(30.0, 100.2)
        self._frame(100.2)
        self.assertEqual(self.window._lyric_motion.last_source_position, 30.0)

    def test_surface_adapter_handoff_reseeds_candidate_without_snapping_viewport(self):
        self.owner.media_source_app = "PlayerA"
        self._frame(100.0)
        self._observe(50.0, 100.1)
        self._frame(100.1)
        before = (self.window._lyric_motion.scroll_index, self.window._lyric_motion.velocity)
        self.owner.media_position_source = "qq-uia-lyrics"
        self._frame(100.1)
        self.assertEqual((self.window._lyric_motion.scroll_index, self.window._lyric_motion.velocity), before)
        self._observe(50.7, 100.8)
        self.owner.media_position_source = "windows-media"
        changed = self._frame(100.8)
        self.assertEqual(changed.active_index, 4)
        self.assertAlmostEqual(self.window._lyric_motion.last_source_position, 9.0)
        self.assertEqual(self.window._lyric_motion.pending_clock_sample[-1], "PlayerA")
        self.assertEqual(self.window._lyric_motion.pending_clock_at, 100.8)

    def test_async_seek_completion_from_previous_app_does_not_publish_new_app_target(self):
        callbacks = []
        self.owner.media_source_app = "PlayerA"
        self.owner._run_background = lambda _name, callback: callbacks.append(callback)
        self.owner.data_provider = SimpleNamespace(seek_playback_position=lambda *_args, **_kwargs: True)
        self._frame(100.0)
        self.window._seek_to_lyric(14)
        self.assertTrue(self.window._seek_pending)
        self.owner.media_source_app = "PlayerB"
        callbacks[0]()
        self.assertFalse(self.window._seek_pending)
        self.assertIsNone(self.window._pending_backward_seek)

    def test_paused_native_seek_is_single_event_and_remains_delayed(self):
        self.route = replace(self.route, state="casting", cast_latency_seconds=1.5)
        self.owner.media_is_playing = False
        self._frame(100.0)
        self._observe(28.0, 100.1)
        changed = self._frame(100.1)
        self.assertEqual(changed.active_index, 13)
        self.assertEqual(self.window._lyric_motion.last_source_position, 28.0)
        self.assertEqual(self.window._lyric_motion.last_position, 26.5)
        for now in (100.2, 100.75):
            self._frame(now)
            self.assertEqual(self.window._lyric_motion.last_position, 26.5)

    def test_surface_first_pause_reuses_hold_until_an_already_paused_seek_event(self):
        self._frame(100.0)
        self._observe(50.0, 100.1, raw=50.0)
        self._frame(100.1)
        self._observe(50.65, 100.75, raw=50.0)
        self._frame(100.75)
        self.owner.media_is_playing = False
        paused = self._frame(100.76)
        self.assertEqual(paused.active_index, 4)
        self.assertAlmostEqual(self.window._lyric_motion.last_source_position, 8.95)
        self._observe(28.0, 100.8)
        sought = self._frame(100.8)
        self.assertEqual(sought.active_index, 14)
        self.assertEqual(self.window._lyric_motion.last_source_position, 28.0)

    def test_late_timing_and_duplicate_timestamps_do_not_confirm_pending_spike(self):
        self._frame(100.0)
        self._observe(50.0, 100.1)
        self._frame(100.1)
        pending = self.window._lyric_motion.pending_clock_sample
        self.owner.timed_lyrics = [(0.0, "新增加的开头"), (0.0, "同时间的合唱")]
        self.owner.timed_lyrics += [(start + 0.05, text) for start, text in
                                  [(index * 2.0, f"这是第 {index} 句歌词") for index in range(30)]]
        self.route = replace(self.route, state="casting", cast_latency_seconds=1.5)
        self._frame(100.15)
        self.assertEqual(self.window._lyric_motion.pending_clock_sample, pending)
        self.assertAlmostEqual(self.window._lyric_motion.last_source_position, 8.35)
        self.assertAlmostEqual(self.window._lyric_motion.last_position, 6.85)
        self.assertEqual(self.window._lyric_motion.active_index,
                         active_index_for_position(self.owner.timed_lyrics, 6.85))

    def test_track_switch_and_late_first_timeline_use_current_delayed_source(self):
        self.route = replace(self.route, state="casting", cast_latency_seconds=1.5)
        self._frame(100.0)
        self.owner.media_track_id = "a-new-track"
        self.owner.timed_lyrics = []
        self._observe(0.2, 110.0)
        self._frame(110.0)
        self.assertEqual(self.window._lyric_motion.last_position, 0.0)
        self.owner.timed_lyrics = [(0.0, "第一句"), (2.0, "第二句"), (4.0, "第三句")]
        self._observe(4.7, 114.5)
        frame = self._frame(114.5)
        self.assertEqual(frame.active_index, 1)
        self.assertAlmostEqual(self.window._lyric_motion.last_source_position, 4.7)
        self.assertAlmostEqual(self.window._lyric_motion.last_position, 3.2)
        self.assertIsNone(self.window._lyric_motion.pending_clock_position)

    def test_source_filter_and_delay_both_feed_real_word_front_without_flashing_ahead(self):
        self.owner.timed_lyrics = [(0.0, "春风轻轻吹"), (10.0, "第二句"), (20.0, "第三句")]
        units = tuple(SimpleNamespace(start_seconds=index * 2.0, end_seconds=(index + 1) * 2.0,
                                      text=character) for index, character in enumerate("春风轻轻吹"))
        self._observe(4.2, 100.0)
        self._frame(100.0)
        original = timed_unit_reading_front("春风轻轻吹", units, self.window._lyric_motion.last_position,
                                           range(6), line_end=10.0)
        self.route = replace(self.route, state="casting", cast_latency_seconds=1.5)
        self._observe(4.216, 100.016)
        self._frame(100.016)
        delayed = timed_unit_reading_front("春风轻轻吹", units, self.window._lyric_motion.last_position,
                                          range(6), line_end=10.0)
        self.assertLess(delayed, original)
        self.assertAlmostEqual(delayed, 2.716 / 2.0)
        self._observe(50.0, 100.1, raw=50.0)
        self._frame(100.1)
        self._observe(50.65, 100.75, raw=50.0)
        self._frame(100.75)
        after_spike = timed_unit_reading_front("春风轻轻吹", units, self.window._lyric_motion.last_position,
                                              range(6), line_end=10.0)
        self.assertAlmostEqual(after_spike, (4.95 - 1.5) / 2.0)
        self.assertLess(after_spike, 2.0)
        self.assertEqual(self.window._lyric_motion.active_index, 0)


if __name__ == "__main__":
    unittest.main()
