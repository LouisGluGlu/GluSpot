from __future__ import annotations

import math
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QCursor, QEnterEvent, QFocusEvent, QFont, QHideEvent, QImage, QKeyEvent, QPainter, QRegion, QTextLayout, QWheelEvent
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QWidget

from local.app import LocalIslandApp
from local.music_surface_window import MUSIC_WINDOW_ASPECT_RATIO, MusicSurfaceWindow, constrained_window_size
from local.music_lyric_reading import whole_block_reading_gradient
from local.music_lyric_motion import (
    GLYPH_FLOAT_LIFT,
    LogicalLineSpringChain,
    MotionSimulationClock,
    MusicLyricMotion,
    WrappedLineSpringChain,
    ambient_color,
    ambient_light_positions,
    continuous_read_lift,
    glyph_reading_state,
    lyric_emphasis_plan,
    lyric_glow_phase,
    lyric_glow_intensity,
    lyric_spoken_units,
    normalize_sparse_seconds_per_unit,
    sparse_glyph_frame,
)
from local.island_stack_visual import IslandKind, IslandStackVisual
from local.lyric_unit_timing import explicit_slow_unit_groups


class MusicFullscreenGeometryTests(unittest.TestCase):
    def test_width_driven_resize_keeps_ratio(self):
        size = constrained_window_size(1280, 300, QSize(640, 360), QSize(1600, 900))
        self.assertEqual(size, QSize(1280, 720))
        self.assertAlmostEqual(size.width() / size.height(), MUSIC_WINDOW_ASPECT_RATIO, places=2)

    def test_height_driven_resize_keeps_ratio(self):
        size = constrained_window_size(700, 600, QSize(640, 360), QSize(1600, 900), prefer_width=False)
        self.assertEqual(size, QSize(1067, 600))

    def test_resize_is_bounded_by_minimum_and_maximum(self):
        minimum = QSize(640, 360)
        maximum = QSize(1600, 900)
        self.assertEqual(constrained_window_size(100, 100, minimum, maximum), minimum)
        self.assertEqual(constrained_window_size(4000, 2000, minimum, maximum), maximum)


class MusicLyricMotionTests(unittest.TestCase):
    def test_line_timing_drives_reading_and_spring_scroll(self):
        motion = MusicLyricMotion()
        lyrics = [(0.0, "first"), (2.0, "second"), (4.0, "third")]
        first = motion.frame(lyrics, 1.0, 10.0, "song")
        self.assertEqual(first.active_index, 0)
        self.assertAlmostEqual(first.reading_progress, 0.5)
        changed = motion.frame(lyrics, 2.1, 11.1, "song")
        self.assertEqual(changed.active_index, 1)
        self.assertGreater(changed.scroll_index, 0.0)
        self.assertLess(changed.scroll_index, 1.0)
        settled = changed
        for tick in range(1, 31):
            settled = motion.frame(lyrics, 2.1 + tick * 0.025, 11.1 + tick * 0.025, "song")
        self.assertAlmostEqual(settled.scroll_index, 1.0, delta=0.1)

    def test_forward_line_is_not_held_behind_a_valid_media_clock(self):
        motion = MusicLyricMotion()
        lyrics = [(0.0, "first"), (2.0, "second")]
        motion.frame(lyrics, 1.99, 10.0, "song")
        self.assertEqual(motion.frame(lyrics, 2.01, 10.016, "song").active_index, 0)
        self.assertEqual(motion.frame(lyrics, 2.03, 10.032, "song").active_index, 1)

    def test_one_stale_clock_sample_does_not_replay_previous_line(self):
        motion = MusicLyricMotion()
        lyrics = [(0.0, "first"), (2.0, "second"), (4.0, "third")]
        self.assertEqual(motion.frame(lyrics, 2.2, 10.0, "song").active_index, 1)
        self.assertEqual(motion.frame(lyrics, 1.7, 10.016, "song").active_index, 1)
        self.assertEqual(motion.frame(lyrics, 2.25, 10.032, "song").active_index, 1)

    def test_confirmed_clock_correction_does_not_unread_within_one_sentence(self):
        motion = MusicLyricMotion()
        lyrics = [(0.0, "slow reading sentence"), (10.0, "next sentence")]
        first = motion.frame(lyrics, 5.0, 100.0, "song", sample_id="first")
        pending = motion.frame(lyrics, 4.0, 100.04, "song", sample_id="second")
        confirmed = motion.frame(lyrics, 4.0, 100.12, "song", sample_id="third")
        self.assertEqual((first.active_index, pending.active_index, confirmed.active_index), (0, 0, 0))
        self.assertGreaterEqual(confirmed.reading_progress, pending.reading_progress)
        self.assertGreaterEqual(motion.last_position, 5.0)
        seek = motion.frame(lyrics, 4.0, 100.2, "song", sample_id="seek", explicit_seek=True)
        self.assertLess(seek.reading_progress, confirmed.reading_progress)

    def test_large_confirmed_player_seek_can_rewind_within_one_sentence(self):
        motion = MusicLyricMotion()
        lyrics = [(0.0, "long sentence"), (12.0, "next sentence")]
        motion.frame(lyrics, 8.0, 100.0, "song", sample_id="first")
        motion.frame(lyrics, 3.0, 100.04, "song", sample_id="second")
        seek = motion.frame(lyrics, 3.0, 100.12, "song", sample_id="third")
        self.assertEqual(seek.active_index, 0)
        self.assertLess(seek.reading_progress, 0.3)

    def test_single_forward_clock_spike_waits_for_an_independent_sample(self):
        motion = MusicLyricMotion()
        lyrics = [(0.0, "one"), (2.0, "two"), (4.0, "three"), (6.0, "four")]
        first = motion.frame(lyrics, 0.80, 10.0, "song", sample_id=(1, 0.80))
        spike = motion.frame(lyrics, 6.80, 10.05, "song", sample_id=(2, 6.80))
        self.assertEqual((first.active_index, spike.active_index), (0, 0))
        # Reading progress follows the held clock too; a stale sample cannot
        # flash the last word of the sentence before the row advances.
        self.assertLess(spike.reading_progress, 0.60)
        confirmed = motion.frame(lyrics, 6.86, 10.12, "song", sample_id=(3, 6.86))
        self.assertEqual(confirmed.active_index, 3)

    def test_coarse_row_ceiling_survives_slow_paints_until_a_new_observation(self):
        lyrics = [(20.0, "observed"), (24.0, "next")]
        for frame_rate in (20, 30):
            with self.subTest(frame_rate=frame_rate):
                motion = MusicLyricMotion()
                motion.frame(
                    lyrics, 23.0, 10.0, "song", sample_id="coarse-row-1",
                    position_ceiling=23.92,
                )
                for tick in range(1, frame_rate * 5 + 1):
                    now = 10.0 + tick / frame_rate
                    frame = motion.frame(
                        lyrics, min(23.0 + tick / frame_rate, 23.92), now,
                        "song", sample_id="coarse-row-1", position_ceiling=23.92,
                    )
                    self.assertEqual(frame.active_index, 0)
                    self.assertLess(motion.last_position, 24.0)
                next_row = motion.frame(
                    lyrics, 24.1, now + 0.1, "song", sample_id="coarse-row-2",
                )
                self.assertEqual(next_row.active_index, 1)

    def test_repeated_track_identity_clears_pending_clock_and_timeline_state(self):
        motion = MusicLyricMotion()
        lyrics_a = [(0.0, "a one"), (4.0, "a two")]
        lyrics_b = [(0.0, "b one"), (2.0, "b two"), (5.0, "b three")]
        motion.frame(lyrics_a, 1.0, 10.0, "track-a", sample_id=(1, 1.0))
        motion.frame(lyrics_a, 7.0, 10.05, "track-a", sample_id=(2, 7.0))
        switched = motion.frame(lyrics_b, 3.2, 20.0, "track-b", sample_id=(3, 3.2))
        self.assertEqual(switched.active_index, 1)
        returned = motion.frame(lyrics_a, 0.10, 20.1, "track-a", sample_id=(4, 0.10))
        self.assertEqual((returned.active_index, returned.scroll_index), (0, 0.0))
        self.assertIsNone(motion.pending_clock_position)

    def test_sustained_backward_position_eventually_follows_without_explicit_seek(self):
        motion = MusicLyricMotion()
        lyrics = [(0.0, "first"), (2.0, "second"), (4.0, "third")]
        motion.frame(lyrics, 2.2, 10.0, "song")
        self.assertEqual(motion.frame(lyrics, 1.4, 10.016, "song").active_index, 1)
        self.assertEqual(motion.frame(lyrics, 1.4, 10.20, "song").active_index, 1)
        self.assertEqual(motion.frame(lyrics, 1.4, 10.31, "song").active_index, 0)

    def test_seek_scrolls_from_current_pose_but_new_track_anchors(self):
        motion = MusicLyricMotion()
        lyrics = [(float(i), str(i)) for i in range(12)]
        motion.frame(lyrics, 0.2, 1.0, "song-a")
        sought = motion.frame(lyrics, 9.0, 1.03, "song-a", explicit_seek=True)
        self.assertEqual(sought.active_index, 9)
        self.assertGreater(sought.scroll_index, 0.0)
        self.assertLess(sought.scroll_index, 9.0)
        self.assertEqual(motion.frame(lyrics, 1.0, 1.06, "song-b").scroll_index, 1.0)

    def test_untimed_lyrics_do_not_invent_reading_progress(self):
        motion = MusicLyricMotion()
        frame = motion.frame([], 12.0, 1.0, "song", fallback_index=2)
        self.assertEqual(frame.active_index, 2)
        self.assertEqual(frame.reading_progress, 0.0)

    def test_apple_glyph_float_has_intermediate_frames_and_rises_once(self):
        self.assertEqual(glyph_reading_state(0.0, 5, 10), (0.5, 0.0))
        samples = [glyph_reading_state(progress, 5, 10) for progress in (0.48, 0.52, 0.56, 0.60)]
        self.assertTrue(all(samples[index][0] < samples[index + 1][0] for index in range(3)))
        self.assertTrue(all(samples[index][1] < samples[index + 1][1] for index in range(3)))
        self.assertGreater(max(lift for _opacity, lift in samples), 4.0)
        self.assertGreater(len({round(lift, 3) for _opacity, lift in samples}), 2)
        self.assertEqual(glyph_reading_state(1.0, 5, 10), (1.0, GLYPH_FLOAT_LIFT))

    def test_sparse_slow_line_gets_highest_priority_effect_from_adjacent_timestamps(self):
        chinese = lyric_emphasis_plan("别走", 10.0, 15.0)
        self.assertEqual(chinese.mode, "scatter_glow")
        self.assertEqual(chinese.unit_count, 2)
        self.assertAlmostEqual(chinese.seconds_per_unit, 2.5)

        english = lyric_emphasis_plan("Stay", 20.0, 23.1)
        self.assertEqual(english.mode, "character_lift")
        self.assertEqual(lyric_spoken_units("Stay with me"), ("Stay", "with", "me"))
        self.assertEqual(lyric_emphasis_plan("one two three four five six", 0.0, 20.0).mode, "character_lift")
        self.assertEqual(lyric_emphasis_plan("last", 0.0, None).mode, "none")

    def test_sparse_line_includes_exactly_one_and_a_half_seconds_per_unit(self):
        self.assertEqual(lyric_emphasis_plan("别走", 10.0, 13.0).mode, "scatter_glow")
        self.assertEqual(lyric_emphasis_plan("Hold me", 10.0, 13.0).mode, "character_lift")
        self.assertEqual(lyric_emphasis_plan("你在何方", 10.0, 16.0).mode, "scatter_glow")
        self.assertEqual(lyric_emphasis_plan("你在何方", 10.0, 15.99).mode, "none")
        self.assertEqual(lyric_emphasis_plan("别走", 10.0, 12.99).mode, "none")

    def test_apostrophe_words_are_single_units_and_motion_line_respects_threshold(self):
        text = "To me fallin', to me goin'"
        self.assertEqual(lyric_spoken_units(text), ("To", "me", "fallin'", "to", "me", "goin'"))
        plan = lyric_emphasis_plan(text, 64.55, 70.53, min_seconds_per_unit=0.8)
        self.assertEqual(plan.unit_count, 6)
        self.assertAlmostEqual(plan.duration, 5.98)
        self.assertAlmostEqual(plan.seconds_per_unit, 5.98 / 6)
        self.assertEqual(plan.mode, "character_lift")
        self.assertEqual(lyric_emphasis_plan(text, 64.55, 70.53).mode, "none")
        self.assertEqual(lyric_emphasis_plan(text, 64.55, 70.53, min_seconds_per_unit=1.0).mode, "none")

    def test_sparse_threshold_bounds_preserve_duration_without_unit_count_limit(self):
        self.assertEqual(lyric_emphasis_plan("你在何方", 0.0, 4.0, min_seconds_per_unit=1.0).mode, "scatter_glow")
        self.assertEqual(lyric_emphasis_plan("你在何方", 0.0, 4.0, min_seconds_per_unit=2.0).mode, "none")
        self.assertEqual(lyric_emphasis_plan("别走", 0.0, 3.0, min_seconds_per_unit=1.5).mode, "scatter_glow")
        self.assertEqual(lyric_emphasis_plan("别走", 0.0, 2.99, min_seconds_per_unit=1.0).mode, "none")
        self.assertEqual(lyric_emphasis_plan("one two three four five six", 0.0, 20.0, min_seconds_per_unit=1.0).mode, "character_lift")
        self.assertEqual(lyric_emphasis_plan("last", 0.0, None, min_seconds_per_unit=1.0).mode, "none")

    def test_sparse_threshold_normalization_handles_corrupt_values(self):
        cases = ((None, 1.5), ("bad", 1.5), (float("nan"), 1.5),
                 (float("inf"), 1.5), (True, 1.5), ([], 1.5),
                 (0.2, 0.8), (3.0, 2.0), ("1.7", 1.7))
        for supplied, expected in cases:
            with self.subTest(supplied=supplied):
                self.assertEqual(normalize_sparse_seconds_per_unit(supplied), expected)
                lyric_emphasis_plan("别走", 0.0, 3.0, min_seconds_per_unit=supplied)

    def test_sparse_plan_ignores_malformed_or_nonfinite_timestamps(self):
        for start, next_start in (("bad", 4.0), (0.0, "bad"),
                                  (float("nan"), 4.0), (0.0, float("inf"))):
            with self.subTest(start=start, next_start=next_start):
                self.assertEqual(lyric_emphasis_plan("别走", start, next_start).mode, "none")

    def test_sparse_line_glow_has_one_second_reveal_three_second_hold_and_one_second_fade(self):
        phases = {seconds: lyric_glow_phase(seconds / 5.0) for seconds in (0, 0.5, 1, 2.5, 4, 4.5, 5)}
        self.assertEqual((phases[0].intensity, phases[0].visible_fraction), (0.0, 0.0))
        self.assertEqual(phases[0.5].stage, "reveal")
        self.assertAlmostEqual(phases[0.5].visible_fraction, 0.5)
        self.assertTrue(0.0 < phases[0.5].intensity < 1.0)
        for seconds in (1, 2.5, 4):
            self.assertEqual(phases[seconds].stage, "hold")
            self.assertEqual((phases[seconds].intensity, phases[seconds].visible_fraction), (1.0, 1.0))
            self.assertEqual(lyric_glow_intensity(seconds / 5.0), 1.0)
        self.assertEqual(phases[4.5].stage, "fade")
        self.assertAlmostEqual(phases[4.5].visible_fraction, 0.5)
        self.assertTrue(0.0 < phases[4.5].intensity < 1.0)
        self.assertEqual((phases[5].intensity, phases[5].visible_fraction), (0.0, 0.0))
        reveal = [lyric_glow_phase(step / 100.0) for step in range(21)]
        fade = [lyric_glow_phase(step / 100.0) for step in range(80, 101)]
        self.assertTrue(all(a.visible_fraction <= b.visible_fraction for a, b in zip(reveal, reveal[1:])))
        self.assertTrue(all(a.visible_fraction >= b.visible_fraction for a, b in zip(fade, fade[1:])))

    def test_sparse_word_uses_one_continuous_growth_and_light_wave(self):
        expected = (
            (0, 0, 0, 0, 0), (1, 0, 0, 0, 0), (2, 1, 0, 0, 0),
            (2, 2, 1, 0, 0), (2, 2, 2, 1, 0), (1, 2, 2, 2, 1),
            (0, 1, 1, 1, 0), (0, 0, 0, 0, 0),
        )
        for step, row in enumerate(expected):
            with self.subTest(step=step):
                frames = [sparse_glyph_frame(step / 7.0, index, 5) for index in range(5)]
                self.assertEqual(tuple(round(frame.glow * 2) for frame in frames), row)
                for frame in frames:
                    self.assertAlmostEqual(frame.scale, 1.0 + 0.125 * frame.glow)
        midpoint = sparse_glyph_frame(2.5 / 7.0, 1, 5)
        self.assertGreater(midpoint.glow, 0.5)
        self.assertLess(midpoint.glow, 1.0)
        reading_leads_lift = sparse_glyph_frame(0.45, 2, 5)
        self.assertGreater(reading_leads_lift.read_fraction, reading_leads_lift.lift / GLYPH_FLOAT_LIFT)
        long_word_tail = sparse_glyph_frame(5.0 / 7.0, 19, 20)
        self.assertGreater(long_word_tail.glow, 0.0)
        self.assertGreater(long_word_tail.read_fraction, 0.0)
        for index in range(5):
            samples = [sparse_glyph_frame(step / 100.0, index, 5) for step in range(101)]
            self.assertTrue(all(a.read_fraction <= b.read_fraction for a, b in zip(samples, samples[1:])))
            self.assertTrue(all(a.lift <= b.lift for a, b in zip(samples, samples[1:])))
            self.assertAlmostEqual(samples[-1].scale, 1.0)
            self.assertEqual(samples[-1].lift, GLYPH_FLOAT_LIFT)
            self.assertEqual(samples[-1].read_fraction, 1.0)

    def test_wrapped_visual_lines_form_a_delayed_compression_chain(self):
        chain = WrappedLineSpringChain()
        self.assertEqual(chain.frame("sentence", (10, 10, 10), 0.30, 1.0).offsets, (0.0, 0.0, 0.0))

        frames = [
            chain.frame("sentence", (10, 10, 10), 0.34, 1.0 + tick * 0.01)
            for tick in range(1, 61)
        ]
        peaks = [
            max(range(len(frames)), key=lambda frame_index: abs(frames[frame_index].offsets[line_index]))
            for line_index in range(3)
        ]
        self.assertLess(peaks[0], peaks[1])
        self.assertLess(peaks[1], peaks[2])
        self.assertTrue(all(frames[peaks[index]].offsets[index] > 0.0 for index in range(3)))
        self.assertGreater(frames[peaks[0]].offsets[0], frames[peaks[0]].offsets[1])
        self.assertEqual(frames[-1].offsets, (0.0, 0.0, 0.0))
        self.assertEqual(frames[-1].velocities, (0.0, 0.0, 0.0))

    def test_logical_visual_lines_keep_independent_staggered_positions(self):
        chain = LogicalLineSpringChain()
        initial = chain.frame("song", (0.0, 54.0, 108.0, 162.0), 0, 1.0, sequence_index=0)
        self.assertEqual(initial.positions, (0.0, 54.0, 108.0, 162.0))
        moved = chain.frame("song", (-54.0, 0.0, 54.0, 108.0), 1, 1.016, sequence_index=1)
        self.assertLess(moved.positions[0], 0.0)
        self.assertEqual(moved.positions[1], 54.0)
        self.assertEqual(moved.positions[2], 108.0)
        self.assertGreater(moved.delays[1], 0.0)
        self.assertGreater(moved.delays[2], 0.0)
        self.assertGreater(moved.delays[3], moved.delays[2])

        settled = moved
        for tick in range(1, 91):
            settled = chain.frame(
                "song", (-54.0, 0.0, 54.0, 108.0), 1,
                1.016 + tick / 120.0,
                sequence_index=1,
            )
        for actual, expected in zip(settled.positions, (-54.0, 0.0, 54.0, 108.0)):
            self.assertAlmostEqual(actual, expected, delta=0.08)

    def test_short_paint_stall_keeps_lyric_group_in_motion_instead_of_snapping(self):
        chain = LogicalLineSpringChain()
        chain.frame("song", (0.0, 54.0, 108.0), 0, 1.0, sequence_index=0)
        moving = chain.frame("song", (-54.0, 0.0, 54.0), 1, 1.016, sequence_index=1)
        after_stall = chain.frame("song", (-54.0, 0.0, 54.0), 1, 1.316, sequence_index=1)
        self.assertLess(after_stall.positions[0], moving.positions[0])
        self.assertGreater(after_stall.positions[0], -54.0)
        busy_resume = chain.frame("song", (-54.0, 0.0, 54.0), 1, 2.2, sequence_index=1)
        self.assertLess(busy_resume.positions[0], after_stall.positions[0])
        self.assertGreater(busy_resume.positions[0], -54.0)
        explicitly_reanchored = chain.frame(
            "song", (-54.0, 0.0, 54.0), 1, 2.21, sequence_index=1, animate=False,
        )
        self.assertEqual(explicitly_reanchored.positions, (-54.0, 0.0, 54.0))

    def test_long_song_spring_only_integrates_rows_near_the_viewport(self):
        chain = LogicalLineSpringChain()
        original = tuple(index * 54.0 for index in range(240))
        shifted = tuple(value - 54.0 for value in original)
        chain.frame("long-song", original, 0, 1.0, sequence_index=0, visible_range=(0, 20))
        frame = chain.frame(
            "long-song", shifted, 1, 1.016,
            sequence_index=1, visible_range=(0, 20),
        )
        self.assertLess(frame.positions[0], original[0])
        self.assertEqual(frame.positions[120], original[120])
        self.assertEqual(frame.velocities[120], 0.0)
        self.assertEqual(frame.delays[120], 0.0)
        entered = chain.frame(
            "long-song", shifted, 1, 1.032,
            sequence_index=1, visible_range=(118, 140),
        )
        self.assertGreater(entered.positions[120], shifted[120])
        self.assertLess(entered.positions[120], original[120])
        self.assertLess(entered.velocities[120], 0.0)

    def test_motion_clock_amortizes_stalls_and_does_not_double_step(self):
        clock = MotionSimulationClock()
        clock.reset(10.0)
        self.assertAlmostEqual(clock.advance(10.01), 0.01)
        self.assertAlmostEqual(clock.advance(20.0), 0.02)
        debt = clock.debt
        self.assertGreater(debt, 0.0)
        self.assertEqual(clock.advance(20.0), 0.0)
        self.assertEqual(clock.debt, debt)
        for tick in range(1, 31):
            self.assertLessEqual(clock.advance(20.0 + tick * 0.01), clock.MAX_FRAME_SECONDS)
        self.assertEqual(clock.debt, 0.0)
        self.assertEqual(clock.advance(5.0), 0.0)
        self.assertEqual(clock.debt, 0.0)

    def test_stall_recovery_respects_recent_cadence_at_each_refresh_rate(self):
        for refresh in (60, 170, 240):
            with self.subTest(refresh=refresh):
                clock = MotionSimulationClock()
                clock.reset(1.0)
                for tick in range(1, 6):
                    self.assertAlmostEqual(clock.advance(1.0 + tick / refresh), 1.0 / refresh)
                resumed_at = 5.0
                first = clock.advance(resumed_at)
                self.assertLessEqual(first, 2.0 / refresh + 1e-10)
                debt = clock.debt
                self.assertEqual(clock.advance(resumed_at), 0.0)
                self.assertEqual(clock.debt, debt)
                ticks = 0
                while clock.debt > 1e-10 and ticks < refresh:
                    ticks += 1
                    catch_up = clock.advance(resumed_at + ticks / refresh)
                    self.assertLessEqual(catch_up, 1.5 / refresh + 1e-10)
                self.assertEqual(clock.debt, 0.0)
                self.assertLess(ticks / refresh, 0.25)
                self.assertAlmostEqual(clock.advance(resumed_at + (ticks + 1) / refresh), 1.0 / refresh)

    def test_motion_clock_adapts_when_the_surface_sustains_a_slower_cadence(self):
        for refresh in (30, 60):
            with self.subTest(refresh=refresh):
                clock = MotionSimulationClock()
                clock.reset(1.0)
                for tick in range(1, 6):
                    clock.advance(1.0 + tick / 240.0)
                began = 1.0 + 5 / 240.0
                first = clock.advance(began + 1 / refresh)
                self.assertLessEqual(first, 2.0 / 240.0 + 1e-10)
                for tick in range(2, 9):
                    advanced = clock.advance(began + tick / refresh)
                self.assertAlmostEqual(advanced, 1.0 / refresh)
                # A render between every timer tick advances its small elapsed
                # time, but cannot poison cadence learning or stall recovery.
                for tick in range(9, 20):
                    clock.advance(began + (tick - 1) / refresh + 0.0001)
                    advanced = clock.advance(began + tick / refresh)
                self.assertGreaterEqual(advanced, 1.0 / refresh - 0.0001 - 1e-10)
                self.assertLessEqual(advanced, min(1.5 * (1.0 / refresh - 0.0001), clock.MAX_FRAME_SECONDS))
                first_stall = clock.advance(10.0)
                self.assertLessEqual(first_stall, min(2.0 / refresh, clock.MAX_FRAME_SECONDS))

    def test_active_spring_runs_without_paints_and_retains_velocity_on_reversal(self):
        chain = LogicalLineSpringChain()
        chain.frame("song", (0.0, 54.0, 108.0), 0, 1.0)
        chain.frame("song", (-54.0, 0.0, 54.0), 1, 1.016)
        moving = chain.advance(1.08)
        reversed_frame = chain.frame("song", (0.0, 54.0, 108.0), 0, 1.08)
        self.assertEqual(reversed_frame.positions, moving.positions)
        self.assertEqual(reversed_frame.velocities, moving.velocities)
        next_frame = chain.advance(1.096)
        self.assertNotEqual(next_frame.positions, moving.positions)
        for tick in range(1, 121):
            settled = chain.advance(1.096 + tick / 120.0)
        for actual, target in zip(settled.positions, (0.0, 54.0, 108.0)):
            self.assertAlmostEqual(actual, target, delta=0.08)

    def test_large_seek_has_intermediate_positions_and_bounded_settling_duration(self):
        for distance in (108.0, 10800.0):
            with self.subTest(distance=distance):
                chain = LogicalLineSpringChain()
                chain.frame("song", (0.0, 54.0, 108.0), 0, 1.0)
                sought = chain.frame(
                    "song", (-distance, 54.0 - distance, 108.0 - distance),
                    200, 1.0, sequence_index=200, seek=True,
                )
                self.assertEqual(sought.positions, (0.0, 54.0, 108.0))
                intermediate = chain.advance(1.016)
                self.assertGreater(intermediate.positions[0], -distance)
                self.assertLess(intermediate.positions[0], 0.0)
                self.assertAlmostEqual(intermediate.positions[1] - intermediate.positions[0], 54.0)
                for tick in range(1, 181):
                    settled = chain.advance(1.016 + tick / 120.0)
                self.assertAlmostEqual(settled.positions[0], -distance, delta=0.08)

    def test_enrichment_remaps_matching_rows_and_new_rows_join_current_roll(self):
        chain = LogicalLineSpringChain()
        chain.frame("old-layout", (0.0, 54.0), 0, 1.0, row_ids=("a", "b"))
        moving = chain.frame("old-layout", (-54.0, 0.0), 1, 1.05, row_ids=("a", "b"))
        enriched = chain.frame(
            "new-layout", (-54.0, -27.0, 0.0), 2, 1.05,
            sequence_index=1, row_ids=("a", "new", "b"),
        )
        self.assertEqual(enriched.positions[0], moving.positions[0])
        self.assertEqual(enriched.positions[2], moving.positions[1])
        self.assertEqual(enriched.velocities[0], moving.velocities[0])
        self.assertEqual(enriched.velocities[2], moving.velocities[1])
        self.assertNotEqual(enriched.positions[1], -27.0)
        self.assertEqual(chain.row_ids, ("a", "new", "b"))
        changed_song = chain.frame("other-song", (10.0, 64.0), 0, 1.06, row_ids=("c", "d"))
        self.assertEqual(changed_song.positions, (10.0, 64.0))

    def test_hidden_rows_are_predicted_and_join_the_physical_viewport_on_seek(self):
        chain = LogicalLineSpringChain()
        original = tuple(index * 54.0 for index in range(240))
        shifted = tuple(value - 10800.0 for value in original)
        chain.frame("song", original, 0, 1.0, visible_range=(0, 8))
        chain.frame("song", shifted, 200, 1.016, seek=True, visible_range=(0, 8))
        self.assertEqual(chain.positions[120], original[120])
        predicted = chain.position_for(120)
        self.assertAlmostEqual(predicted, shifted[120] + chain.viewport_offset)
        offset, _speed = chain.viewport_pose()
        entered = chain.frame("song", shifted, 200, 1.016, visible_range=(118, 126))
        self.assertAlmostEqual(entered.positions[120], shifted[120] + offset)
        self.assertLess(entered.velocities[120], 0.0)
        self.assertEqual(chain.visible_range, (118, 126))

    def test_spring_cadence_stays_close_across_refresh_rates(self):
        samples = []
        for refresh in (60, 120, 170, 240):
            chain = LogicalLineSpringChain()
            chain.frame("song", (0.0, 54.0, 108.0), 0, 1.0)
            chain.frame("song", (-54.0, 0.0, 54.0), 1, 1.0)
            for tick in range(1, int(refresh * 0.5) + 1):
                frame = chain.advance(1.0 + tick / refresh)
            samples.append(frame.positions)
        for row in zip(*samples):
            self.assertLess(max(row) - min(row), 0.5)

    def test_music_seek_reversal_retains_scroll_pose_and_validated_reading_time(self):
        motion = MusicLyricMotion()
        lyrics = [(float(index), str(index)) for index in range(12)]
        motion.frame(lyrics, 0.2, 1.0, "song", playing=False)
        moving = motion.frame(lyrics, 9.0, 1.02, "song", explicit_seek=True, playing=False)
        reversed_frame = motion.frame(lyrics, 2.5, 1.02, "song", explicit_seek=True, playing=False)
        self.assertEqual(reversed_frame.active_index, 2)
        self.assertEqual(reversed_frame.reading_progress, 0.5)
        self.assertEqual(reversed_frame.scroll_index, moving.scroll_index)
        self.assertEqual(reversed_frame.scroll_velocity, moving.scroll_velocity)

    def test_ambient_lights_have_three_slow_moving_sources_and_white_is_gray(self):
        self.assertEqual(len(ambient_light_positions(0.0)), 3)
        self.assertNotEqual(ambient_light_positions(0.0), ambient_light_positions(1.0))
        gray = ambient_color(QColor(255, 255, 255))
        self.assertEqual((gray.red(), gray.green(), gray.blue()), (90, 90, 90))


class _Owner(QWidget):
    def __init__(self):
        super().__init__()
        self._music_windowed_preference = False
        self.music_intro_cleanup_enabled = False
        self.enable_dual_line_lyrics = True
        self.restored = False
        self.seek_calls = []
        self.data_provider = self

    def seek_playback_position(self, seconds):
        self.seek_calls.append(seconds)
        return True

    def _run_background(self, _name, callback):
        callback()

    def _restore_island_from_music_surface(self):
        self.restored = True


class _LifecycleIsland(IslandStackVisual):
    def __init__(self):
        self.lifecycle_events = []
        super().__init__()

    def show(self):
        self.lifecycle_events.append(("island_show", bool(getattr(self, "_music_surface_active", False))))
        super().show()

    def hide(self):
        self.lifecycle_events.append(("island_hide", bool(getattr(self, "_music_surface_active", False))))
        super().hide()

    def raise_(self):
        self.lifecycle_events.append(("island_raise", bool(getattr(self, "_music_surface_active", False))))
        super().raise_()

    def activateWindow(self):
        self.lifecycle_events.append(("island_activate", bool(getattr(self, "_music_surface_active", False))))
        super().activateWindow()

    def _release_super_topmost(self):
        self.lifecycle_events.append(("release_topmost", bool(getattr(self, "_music_surface_active", False))))


class _LifecycleSurface:
    def __init__(self, owner, *, fail_to_show: bool = False):
        self.owner = owner
        self.visible = False
        self.fail_to_show = fail_to_show

    def isVisible(self):
        return self.visible

    def show_from_island(self, *, fullscreen=None, origin_geometry=None, origin_snapshot=None):
        self.owner.lifecycle_events.append(
            ("surface_show", self.owner.isVisible(), bool(self.owner._music_surface_active))
        )
        if self.fail_to_show:
            raise RuntimeError("surface failed to show")
        self.visible = True

    def hide(self):
        self.owner.lifecycle_events.append(("surface_hide", bool(self.owner._music_surface_active)))
        self.visible = False

    def raise_(self):
        self.owner.lifecycle_events.append(("surface_raise", bool(self.owner._music_surface_active)))

    def activateWindow(self):
        self.owner.lifecycle_events.append(("surface_activate", bool(self.owner._music_surface_active)))


class _PointerEvent:
    def __init__(
        self,
        point,
        *,
        global_point=None,
        button=Qt.MouseButton.LeftButton,
        buttons=Qt.MouseButton.NoButton,
    ):
        self._point = QPointF(point)
        self._global_point = QPointF(global_point if global_point is not None else point)
        self._button = button
        self._buttons = buttons
        self.accepted = False

    def button(self):
        return self._button

    def buttons(self):
        return self._buttons

    def position(self):
        return QPointF(self._point)

    def globalPosition(self):
        return QPointF(self._global_point)

    def accept(self):
        self.accepted = True


class _WheelEvent(_PointerEvent):
    def __init__(self, point, vertical_delta):
        super().__init__(point)
        self._vertical_delta = int(vertical_delta)

    def angleDelta(self):
        return QPoint(0, self._vertical_delta)


class MusicSurfaceWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _Owner()
        self.window = MusicSurfaceWindow(self.owner)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _finish_glow_prewarm(self):
        for _ in range(128):
            if self.window._pending_glow_prewarm is None:
                return
            self.window._on_animation_tick()
        self.fail("sparse lyric prewarm did not complete")

    def test_genuine_obo_focus_moves_after_last_unit_ends_before_next_row_reads(self):
        self.owner.media_track_id = "obo-early-focus"
        self.owner.media_is_playing = True
        self.owner.music_surface_reading_strategy = "gold"
        self.owner.timed_lyrics = [
            (0.0, "Hello world"), (3.0, "Next sentence"),
            (6.0, "Later sentence"), (9.0, "Final sentence"),
        ]
        self.owner.timed_lyric_units = [
            (
                SimpleNamespace(start_seconds=0.0, end_seconds=0.7, text="Hello "),
                SimpleNamespace(start_seconds=0.7, end_seconds=1.2, text="world"),
            ),
            (), (), (),
        ]
        self.owner.timed_lyric_timing_kinds = ["unit", "line", "line", "line"]
        self.owner.media_position_seconds = 1.5
        output = QImage(960, 540, QImage.Format.Format_RGB32)
        self.window.resize(960, 540)
        with patch.object(
            self.window, "_lyric_view_anchor", wraps=self.window._lyric_view_anchor,
        ) as anchor:
            self.window.render(output)
        self.assertEqual(self.window._lyric_motion.active_index, 0)
        self.assertEqual(anchor.call_args.args[0], 1)

    def test_obo_early_focus_does_not_recoil_during_clock_boundary_confirmation(self):
        self.owner.media_track_id = "obo-boundary-confirm"
        self.owner.media_is_playing = True
        self.owner.music_surface_reading_strategy = "gold"
        self.owner.timed_lyrics = [
            (0.0, "Hello world"), (3.0, "Next sentence"),
            (6.0, "Later sentence"), (9.0, "Final sentence"),
        ]
        self.owner.timed_lyric_units = [(
            SimpleNamespace(start_seconds=0.0, end_seconds=0.7, text="Hello "),
            SimpleNamespace(start_seconds=0.7, end_seconds=1.2, text="world"),
        ), (), (), ()]
        self.owner.timed_lyric_timing_kinds = ["unit", "line", "line", "line"]
        self.window.resize(960, 540)
        output = QImage(960, 540, QImage.Format.Format_RGB32)
        focus = []
        active = []
        for position in (2.9, 3.0, 3.01, 3.09, 3.2):
            self.owner.media_position_seconds = position
            self.owner.media_position_sampled_at = 100.0 + position
            with (
                patch("local.music_surface_window.time.monotonic", return_value=100.0 + position),
                patch.object(self.window, "_lyric_view_anchor", wraps=self.window._lyric_view_anchor) as anchor,
            ):
                self.window.render(output)
            focus.append(anchor.call_args.args[0])
            active.append(self.window._lyric_motion.active_index)
        self.assertEqual(focus, [1, 1, 1, 1, 1])
        self.assertEqual(active[0], 0)
        self.assertEqual(active[-1], 1)

        self.owner.media_position_seconds = 3.1
        self.owner.media_position_source = "local-seek"
        self.owner.media_position_sampled_at = 100.0
        with patch.object(
            self.window, "_lyric_view_anchor", wraps=self.window._lyric_view_anchor,
        ) as anchor:
            self.window.render(output)
        self.assertEqual(anchor.call_args.args[0], 1)

    def test_obo_early_focus_resets_on_confirmed_backward_seek(self):
        self.owner.media_track_id = "obo-backward-seek"
        self.owner.media_is_playing = True
        self.owner.music_surface_reading_strategy = "gold"
        self.owner.timed_lyrics = [
            (0.0, "Hello world"), (3.0, "Next sentence"),
            (6.0, "Later sentence"), (9.0, "Final sentence"),
        ]
        self.owner.timed_lyric_units = [(
            SimpleNamespace(start_seconds=0.0, end_seconds=0.7, text="Hello "),
            SimpleNamespace(start_seconds=0.7, end_seconds=1.2, text="world"),
        ), (), (), ()]
        self.owner.timed_lyric_timing_kinds = ["unit", "line", "line", "line"]
        self.window.resize(960, 540)
        output = QImage(960, 540, QImage.Format.Format_RGB32)

        self.owner.media_position_seconds = 1.5
        self.owner.media_position_sampled_at = 100.0
        with patch.object(self.window, "_lyric_view_anchor", wraps=self.window._lyric_view_anchor) as anchor:
            self.window.render(output)
        self.assertEqual(anchor.call_args.args[0], 1)

        self.owner.media_position_seconds = 0.5
        self.owner.media_position_source = "local-seek"
        self.owner.media_position_sampled_at = 101.0
        with patch.object(self.window, "_lyric_view_anchor", wraps=self.window._lyric_view_anchor) as anchor:
            self.window.render(output)
        self.assertEqual(self.window._lyric_motion.active_index, 0)
        self.assertEqual(anchor.call_args.args[0], 0)
        self.assertIsNone(self.window._obo_advanced_focus_index)

    def test_real_unit_metadata_stays_aligned_after_credit_and_countdown(self):
        units = tuple(
            SimpleNamespace(start_seconds=start, end_seconds=end, text=word)
            for start, end, word in (
                (10.0, 10.4, "谁"), (10.4, 10.8, "人"),
                (10.8, 12.1, "都"), (12.1, 13.5, "可"),
                (13.5, 15.0, "以"),
            )
        )
        self.owner.music_intro_cleanup_enabled = True
        self.owner.timed_lyrics = [
            (0.0, "作词: 制作人"),
            (10.0, "谁人都可以\nAnyone can"),
            (15.0, "下一句"),
        ]
        self.owner.timed_lyric_units = [(), units, ()]
        self.owner.timed_lyric_timing_kinds = ["line", "unit", "line"]
        lines, timed = self.window._display_lyrics()
        self.assertEqual(timed[0], (0.0, "· · ·"))
        self.assertEqual(lines[1], "谁人都可以\u2028Anyone can")
        self.assertEqual(self.window._display_timed_kinds, ("line", "unit", "line"))
        self.assertIs(self.window._display_timed_units[1], units)
        self.assertEqual(self.window._primary_timed_lyric_text(""), "")

        font = self.window._surface_font(38, QFont.Weight.Bold)
        layout, _height = self.window._wrapped_lyric_layout(lines[1], font, 600.0)
        early = self.window._real_visual_line_progresses(lines[1], layout, units, 10.2, line_end=15.0)
        middle = self.window._real_visual_line_progresses(lines[1], layout, units, 11.45, line_end=15.0)
        complete = self.window._real_visual_line_progresses(lines[1], layout, units, 15.0, line_end=15.0)
        self.assertIsNotNone(early)
        self.assertLess(early[0], middle[0])
        self.assertEqual(early[-1], 0.0)
        self.assertEqual(complete[-1], 1.0)
        groups = explicit_slow_unit_groups("谁人都可以", units)
        self.assertEqual((groups[0].start_offset, groups[0].end_offset), (2, 5))
        states = self.window._real_sparse_glyph_states(
            "谁人都可以", font, 0, groups, 0.5, 11.45,
        )
        self.assertGreater(states[2].glow, 0.0)
        self.assertEqual((states[3].scale, states[3].glow), (1.0, 0.0))
        self.assertEqual((states[4].scale, states[4].glow), (1.0, 0.0))

    def test_synthetic_timing_does_not_invent_a_word_read_or_sparse_plan(self):
        self.owner.timed_lyrics = [
            (0.0, "纯文本合成"), (3.0, "下一句"), (6.0, "第三句"), (9.0, "第四句"),
        ]
        self.owner.timed_lyric_units = [(), (), (), ()]
        self.owner.timed_lyric_timing_kinds = ["synthetic"] * 4
        _lines, timed = self.window._display_lyrics()
        self.assertEqual(self.window._display_timed_kinds, ("synthetic",) * 4)
        self.assertEqual(self.window._display_timed_units, ((),) * 4)
        self.assertEqual(timed[0][1], "纯文本合成")
        self.owner.media_position_seconds = 1.0
        self.owner.media_is_playing = False
        self.window.resize(960, 540)
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch.object(self.window, "_paint_wrapped_lifted_lyric") as reading, patch.object(
            self.window, "_paint_sparse_glyph_run",
        ) as highlight:
            self.window.render(image)
        reading.assert_not_called()
        highlight.assert_not_called()

    def test_low_perception_keeps_line_spring_but_skips_estimated_word_reading(self):
        self.owner.timed_lyrics = [
            (0.0, "第一句歌词"), (4.0, "第二句歌词"),
            (8.0, "第三句歌词"), (12.0, "第四句歌词"),
        ]
        self.owner.timed_lyric_units = [()] * 4
        self.owner.timed_lyric_timing_kinds = ["line"] * 4
        self.owner.media_position_seconds = 1.0
        self.owner.media_is_playing = False
        self.window.resize(960, 540)
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)

        self.owner.music_surface_reading_strategy = "gold"
        with patch.object(self.window, "_paint_wrapped_lifted_lyric") as reading:
            self.window.render(image)
        reading.assert_called()

        self.owner.music_surface_reading_strategy = "low_perception"
        with patch.object(self.window, "_paint_wrapped_lifted_lyric") as reading, patch.object(
            self.window, "_paint_sparse_glyph_run",
        ) as highlight, patch.object(
            self.window._logical_line_spring, "frame", wraps=self.window._logical_line_spring.frame,
        ) as spring:
            self.window.render(image)
        reading.assert_not_called()
        highlight.assert_not_called()
        spring.assert_called()
        self.assertEqual(self.window._last_lyric_emphasis_mode, "none")

    def test_low_perception_still_uses_real_unit_reading_and_quality_badge(self):
        self.owner.track_title = "mixed timing"
        units = (
            SimpleNamespace(start_seconds=0.0, end_seconds=0.5, text="真"),
            SimpleNamespace(start_seconds=0.5, end_seconds=1.0, text="逐"),
            SimpleNamespace(start_seconds=1.0, end_seconds=1.5, text="字"),
        )
        self.owner.timed_lyrics = [
            (0.0, "真逐字"), (3.0, "第二句"), (6.0, "第三句"), (9.0, "第四句"),
        ]
        self.owner.timed_lyric_units = [units, (), (), ()]
        self.owner.timed_lyric_timing_kinds = ["unit", "line", "line", "line"]
        self.owner.music_surface_reading_strategy = "low_perception"
        self.owner.media_position_seconds = 0.75
        self.owner.media_is_playing = False
        lines, timed = self.window._display_lyrics()
        self.assertEqual(self.window._timing_quality_badge(lines, timed), "部分逐字")
        self.window.resize(960, 540)
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch.object(self.window, "_paint_wrapped_lifted_lyric") as reading, patch.object(
            self.window, "_draw_low_perception_block_wipe",
        ) as block_wipe:
            self.window.render(image)
        reading.assert_called()
        block_wipe.assert_not_called()

    def test_low_perception_wrapped_original_and_translation_share_half_second_wipe(self):
        self.owner.music_surface_reading_strategy = "low_perception"
        self.owner.media_track_id = "bilingual-block-wipe"
        self.owner.media_is_playing = False
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.owner.timed_lyrics = [
            (0.0, "Earlier lyric"),
            (2.0, "Many original words wrapping across the music page\n多行歌词的翻译"),
            (6.0, "Later lyric"),
            (10.0, "Final lyric"),
        ]
        self.owner.timed_lyric_units = [()] * 4
        self.owner.timed_lyric_timing_kinds = ["line"] * 4
        self.window.resize(800, 450)
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        observed = []
        for position in (2.0, 2.25, 2.75):
            self.owner.media_position_seconds = position
            self.owner.media_raw_position_seconds = position
            self.owner.media_position_sampled_at = 100.0 + position
            with patch("local.music_surface_window.time.monotonic", return_value=100.0 + position), patch.object(
                self.window, "_draw_low_perception_block_wipe",
                wraps=self.window._draw_low_perception_block_wipe,
            ) as wipe:
                self.window.render(image)
            observed.append(wipe.call_args_list)
        self.assertEqual(len(observed[0]), 1)
        self.assertEqual(len(observed[1]), 1)
        self.assertEqual(len(observed[2]), 0)
        self.assertAlmostEqual(observed[0][0].args[2], 0.0)
        # The provider position is lightly smoothed, so this is inside the
        # half-second wipe rather than an exact wall-clock quarter second.
        self.assertGreater(observed[1][0].args[2], 0.3)
        self.assertLess(observed[1][0].args[2], 0.6)
        layers = observed[1][0].args[1]
        self.assertEqual(len(layers), 2)
        self.assertGreater(layers[0][0].lineCount(), 1)
        self.assertGreaterEqual(layers[1][0].lineCount(), 1)
        self.assertEqual(len(layers[0][2]), layers[0][0].lineCount())
        self.assertEqual(len(layers[1][2]), layers[1][0].lineCount())
        # An explicit backward seek recomputes the half-second phase from the
        # new source position; a paused row holds that exact color state.
        self.owner.media_position_seconds = 2.1
        self.owner.media_raw_position_seconds = 2.1
        self.owner.media_position_sampled_at = 120.0
        self.owner.media_position_source = "local-seek"
        with patch("local.music_surface_window.time.monotonic", return_value=120.0), patch.object(
            self.window, "_draw_low_perception_block_wipe",
            wraps=self.window._draw_low_perception_block_wipe,
        ) as seek_wipe:
            self.window.render(image)
        self.assertEqual(len(seek_wipe.call_args_list), 1)
        seek_phase = seek_wipe.call_args.args[2]
        self.assertGreaterEqual(seek_phase, 0.0)
        self.assertLess(seek_phase, 0.5)
        self.owner.media_position_source = ""
        with patch("local.music_surface_window.time.monotonic", return_value=121.0), patch.object(
            self.window, "_draw_low_perception_block_wipe",
            wraps=self.window._draw_low_perception_block_wipe,
        ) as paused_wipe:
            self.window.render(image)
        self.assertEqual(len(paused_wipe.call_args_list), 1)
        self.assertAlmostEqual(paused_wipe.call_args.args[2], seek_phase, delta=0.001)

    def test_low_perception_diagonal_wipe_reveals_top_left_before_translation_right(self):
        original_font = self.window._surface_font(32, QFont.Weight.Bold)
        translation_font = self.window._surface_font(22, QFont.Weight.Normal)
        original, original_height = self.window._wrapped_lyric_layout(
            "MMMM MMMM MMMM MMMM", original_font, 190.0,
        )
        translation, _ = self.window._wrapped_lyric_layout(
            "MMMM MMMM", translation_font, 190.0,
        )
        self.assertGreater(original.lineCount(), 1)
        layers = (
            (original, QPointF(20.0, 15.0), (0.0,) * original.lineCount()),
            (translation, QPointF(20.0, 20.0 + original_height), (0.0,) * translation.lineCount()),
        )
        unread = QColor(90, 50, 30)

        def draw(phase):
            image = QImage(250, 210, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                self.window._draw_low_perception_block_wipe(painter, layers, phase, unread)
            return image

        def mean_red(image, layout, origin, line_index, left_fraction, right_fraction):
            line = layout.lineAt(line_index)
            x0 = round(origin.x() + line.x() + line.naturalTextWidth() * left_fraction)
            x1 = round(origin.x() + line.x() + line.naturalTextWidth() * right_fraction)
            y0 = round(origin.y() + line.y())
            y1 = round(y0 + line.height())
            pixels = [
                image.pixelColor(x, y).red()
                for y in range(y0, y1) for x in range(x0, x1)
                if image.pixelColor(x, y).alpha() > 128
            ]
            self.assertGreater(len(pixels), 10)
            return sum(pixels) / len(pixels)

        first = (original, layers[0][1], 0, 0.0, 0.35)
        last = (translation, layers[1][1], translation.lineCount() - 1, 0.65, 1.0)
        initial = draw(0.0)
        quarter = draw(0.25)
        midpoint = draw(0.5)
        finished = draw(1.0)
        self.assertAlmostEqual(mean_red(initial, *first), unread.red(), delta=2)
        self.assertAlmostEqual(mean_red(initial, *last), unread.red(), delta=2)
        self.assertGreater(mean_red(quarter, *first), mean_red(quarter, *last) + 65)
        self.assertGreater(mean_red(midpoint, *first), mean_red(midpoint, *last) + 90)
        self.assertAlmostEqual(mean_red(finished, *first), 255, delta=2)
        self.assertAlmostEqual(mean_red(finished, *last), 255, delta=2)

    def test_low_perception_color_front_is_locked_to_block_during_line_spring(self):
        original_font = self.window._surface_font(32, QFont.Weight.Bold)
        translation_font = self.window._surface_font(22, QFont.Weight.Normal)
        original, original_height = self.window._wrapped_lyric_layout(
            "MMMM MMMM MMMM MMMM", original_font, 190.0,
        )
        translation, _ = self.window._wrapped_lyric_layout(
            "MMMM MMMM", translation_font, 190.0,
        )
        primary_origin = QPointF(25.0, 20.0)
        translation_origin = QPointF(25.0, 30.0 + original_height)
        resting = (
            (original, primary_origin, (0.0,) * original.lineCount()),
            (translation, translation_origin, (0.0,) * translation.lineCount()),
        )
        moving = (
            (original, primary_origin, tuple(7.0 + index * 4.0 for index in range(original.lineCount()))),
            (translation, translation_origin, (15.0,) * translation.lineCount()),
        )

        def paint(layers):
            image = QImage(270, 250, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter, patch(
                "local.music_surface_window.whole_block_reading_gradient",
                wraps=whole_block_reading_gradient,
            ) as gradient:
                self.window._draw_low_perception_block_wipe(
                    painter, layers, 0.46, QColor(90, 50, 30),
                )
            return image, gradient.call_args.args[0]

        still, still_bounds = paint(resting)
        sprung, sprung_bounds = paint(moving)
        self.assertEqual(still_bounds, sprung_bounds)
        for layer_index, line_index in ((0, 0), (0, original.lineCount() - 1), (1, 0)):
            layout, origin, _rest_offsets = resting[layer_index]
            spring_offset = moving[layer_index][2][line_index]
            line = layout.lineAt(line_index)
            x0 = round(origin.x() + line.x() + line.naturalTextWidth() * 0.12)
            x1 = round(origin.x() + line.x() + line.naturalTextWidth() * 0.30)
            y0 = round(origin.y() + line.y() + line.height() * 0.18)
            y1 = round(origin.y() + line.y() + line.height() * 0.78)

            def mean_red(image, delta):
                pixels = [
                    image.pixelColor(x, y + round(delta)).red()
                    for y in range(y0, y1) for x in range(x0, x1)
                    if image.pixelColor(x, y + round(delta)).alpha() > 128
                ]
                self.assertGreater(len(pixels), 10)
                return sum(pixels) / len(pixels)

            self.assertAlmostEqual(mean_red(still, 0), mean_red(sprung, spring_offset), delta=3)

    def test_low_perception_wipe_phase_uses_current_source_position_after_seek(self):
        phase = self.window._low_perception_block_progress
        self.assertEqual(phase(4.0, 4.0), 0.0)
        self.assertEqual(phase(4.25, 4.0), 0.5)
        self.assertEqual(phase(4.5, 4.0), 1.0)
        self.assertEqual(phase(4.25, 9.0), 0.0)
        self.assertAlmostEqual(phase(9.1, 9.0), 0.2)

    def test_genuine_song_shows_obo_next_to_idle_window_control(self):
        self.owner.track_title = "short song"
        self.owner.timed_lyrics = [(0.0, "真逐字"), (3.0, "第二句"), (6.0, "第三句")]
        self.owner.timed_lyric_units = [
            tuple(SimpleNamespace(start_seconds=float(start + offset), end_seconds=float(start + offset + 1), text=char)
                  for offset, char in enumerate(text))
            for start, text in ((0, "真逐字"), (3, "第二句"), (6, "第三句"))
        ]
        self.owner.timed_lyric_timing_kinds = ["unit"] * 3
        self.window.resize(960, 540)
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        self.owner.music_surface_reading_strategy = "gold"
        with patch.object(self.window, "_paint_control_layer", wraps=self.window._paint_control_layer) as layer:
            self.window.render(image)
        window_calls = [call for call in layer.call_args_list if call.args[4][0] == "window"]
        self.assertEqual(len(window_calls), 1)
        self.assertTrue(window_calls[0].args[4][3])
        self.assertTrue(window_calls[0].args[4][4])
        button = self.window._window_control_base_rect(QRectF(self.window.rect()))
        obo = self.window._obo_badge_rect(button, self.window._surface_scale())
        self.assertEqual(obo.size(), button.size())
        self.assertEqual(obo.top(), button.top())
        dark = image.pixelColor(round(obo.left() + 6), round(obo.center().y()))
        self.assertLess(dark.red(), 100)

        self.window._fullscreen = True
        with patch.object(self.window, "_paint_control_layer", wraps=self.window._paint_control_layer) as layer:
            self.window.render(image)
        fullscreen_calls = [call for call in layer.call_args_list if call.args[4][0] == "window"]
        self.assertEqual(len(fullscreen_calls), 1)
        self.assertTrue(fullscreen_calls[0].args[4][3])

        self.window._control_last_motion_at = 0.0
        self.window.render(image)
        hidden = image.pixelColor(round(obo.left() + 6), round(obo.center().y()))
        self.assertLess(abs(hidden.red() - dark.red()), 100)
        self.window._reset_control_idle()

        self.owner.timed_lyric_units = [self.owner.timed_lyric_units[0], (), self.owner.timed_lyric_units[2]]
        self.owner.timed_lyric_timing_kinds = ["unit", "line", "unit"]
        with patch.object(self.window, "_paint_control_layer", wraps=self.window._paint_control_layer) as layer:
            self.window.render(image)
        window_calls = [call for call in layer.call_args_list if call.args[4][0] == "window"]
        self.assertTrue(window_calls[0].args[4][3])
        self.assertTrue(window_calls[0].args[4][4])

    def test_sentence_sized_source_markers_do_not_claim_obo_or_low_perception_reading(self):
        self.owner.track_title = "row markers"
        self.owner.timed_lyrics = [
            (0.0, "Hello world"), (3.0, "Good night"),
            (6.0, "One more"), (9.0, "Last line"),
        ]
        self.owner.timed_lyric_units = [
            (SimpleNamespace(start_seconds=start, end_seconds=None, text=text),)
            for start, text in self.owner.timed_lyrics
        ]
        self.owner.timed_lyric_timing_kinds = ["unit"] * 4
        self.owner.music_surface_reading_strategy = "low_perception"
        lines, timed = self.window._display_lyrics()
        self.assertEqual(self.window._display_timed_kinds, ("unit",) * 4)
        self.assertEqual(self.window._timing_quality_badge(lines, timed), "行计时")
        self.window.resize(960, 540)
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch.object(self.window, "_paint_control_layer", wraps=self.window._paint_control_layer) as layer, patch.object(
            self.window, "_paint_wrapped_lifted_lyric",
        ) as reading:
            self.window.render(image)
        window_key = next(call for call in layer.call_args_list if call.args[4][0] == "window").args[4]
        self.assertFalse(window_key[3])
        reading.assert_not_called()

    def test_gold_badge_has_inactive_state_and_low_perception_hides_it(self):
        self.owner.track_title = "line-timed song"
        self.owner.timed_lyrics = [
            (0.0, "first line"), (3.0, "second line"),
            (6.0, "third line"), (9.0, "fourth line"),
        ]
        self.owner.timed_lyric_units = [()] * 4
        self.owner.timed_lyric_timing_kinds = ["line"] * 4
        self.window.resize(960, 540)
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)

        self.owner.music_surface_reading_strategy = "gold"
        with patch.object(self.window, "_paint_control_layer", wraps=self.window._paint_control_layer) as layer:
            self.window.render(image)
        key = next(call for call in layer.call_args_list if call.args[4][0] == "window").args[4]
        self.assertTrue(key[3])
        self.assertFalse(key[4])

        self.owner.music_surface_reading_strategy = "low_perception"
        with patch.object(self.window, "_paint_control_layer", wraps=self.window._paint_control_layer) as layer:
            self.window.render(image)
        key = next(call for call in layer.call_args_list if call.args[4][0] == "window").args[4]
        self.assertFalse(key[3])

    def test_passionfruit_style_start_only_word_marker_stays_white_obo_without_sparse_glow(self):
        self.owner.track_title = "Passionfruit"
        self.owner.music_surface_reading_strategy = "gold"
        self.owner.timed_lyrics = [
            (0.0, "Passionfruit"), (4.5, "Listen"),
            (8.0, "Seeing you"), (12.0, "Next line"),
        ]
        self.owner.timed_lyric_units = [
            (SimpleNamespace(start_seconds=0.0, end_seconds=None, text="Passionfruit"),),
            (), (), (),
        ]
        self.owner.timed_lyric_timing_kinds = ["unit", "line", "line", "line"]
        self.owner.media_position_seconds = 2.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(960, 540)
        lines, timed = self.window._display_lyrics()
        self.assertEqual(self.window._timing_quality_badge(lines, timed), "行计时")
        self.assertFalse(self.window._has_genuine_obo_timing(lines, timed))
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch.object(self.window, "_paint_control_layer", wraps=self.window._paint_control_layer) as layer, patch.object(
            self.window, "_paint_sparse_glyph_run",
        ) as glow:
            self.window.render(image)
        key = next(call for call in layer.call_args_list if call.args[4][0] == "window").args[4]
        self.assertTrue(key[3])  # Gold mode keeps the dark, white-text OBO mark.
        self.assertFalse(key[4])
        glow.assert_not_called()
        self.assertEqual(self.window._last_lyric_emphasis_mode, "none")
        self.assertIsNone(self.window._pending_glow_prewarm)
        # A later plain row in this falsely marked enhanced-LRC song must not
        # inherit a guessed long-word glow from the neighboring line times.
        self.owner.media_position_seconds = 5.5
        self.window.render(image)
        self.assertEqual(self.window._last_lyric_emphasis_mode, "none")
        self.assertIsNone(self.window._pending_glow_prewarm)

    def test_explicit_slow_word_has_genuine_obo_even_without_neighboring_units(self):
        self.owner.track_title = "one measured word"
        self.owner.timed_lyrics = [(0.0, "shine"), (5.0, "ordinary"), (8.0, "later"), (11.0, "end")]
        self.owner.timed_lyric_units = [
            (SimpleNamespace(start_seconds=0.0, end_seconds=5.0, text="shine"),),
            (), (), (),
        ]
        self.owner.timed_lyric_timing_kinds = ["unit", "line", "line", "line"]
        lines, timed = self.window._display_lyrics()
        self.assertEqual(self.window._timing_quality_badge(lines, timed), "部分逐字")
        self.assertTrue(self.window._has_genuine_obo_timing(lines, timed))

    def test_mixed_source_with_real_unit_timing_keeps_line_highlight_fallback(self):
        self.owner.timed_lyrics = [
            (0.0, "Passionfruit"), (4.5, "shine"), (9.5, "Stay"), (14.0, "ordinary line"),
        ]
        self.owner.timed_lyric_units = [
            (SimpleNamespace(start_seconds=0.0, end_seconds=None, text="Passionfruit"),),
            (SimpleNamespace(start_seconds=4.5, end_seconds=9.5, text="shine"),),
            (), (),
        ]
        self.owner.timed_lyric_timing_kinds = ["unit", "unit", "line", "line"]
        self.owner.media_position_seconds = 11.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(960, 540)
        _lines, timed = self.window._display_lyrics()
        self.assertFalse(self.window._has_only_unverified_unit_timing(timed))
        self.window.render(QImage(self.window.size(), QImage.Format.Format_RGB32))
        self.assertEqual(self.window._last_lyric_emphasis_mode, "character_lift")

    def test_loading_skeleton_has_seven_rows_and_moves_each_three_seconds(self):
        self.owner.track_title = "Loading song"
        self.owner.timed_lyrics = []
        self.owner.lyrics = ["查找歌词中"]
        self.window.resize(960, 540)
        rect = QRectF(410.0, 36.0, 470.0, 452.0)
        self.assertTrue(self.window._lyrics_lookup_pending())
        frames = []
        for second in (100.0, 100.9, 101.6, 103.7, 104.1):
            frame = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
            frame.fill(Qt.GlobalColor.transparent)
            with QPainter(frame) as painter:
                self.window._paint_lyric_loading(painter, rect, second, 1.0)
            frames.append(bytes(frame.constBits()))
        self.assertTrue(frames[0] == frames[1])
        self.assertTrue(frames[1] != frames[2])
        self.assertTrue(frames[2] != frames[3])
        self.assertTrue(frames[3] != frames[4])

        self.owner.lyrics = ["暂无歌词"]
        self.assertFalse(self.window._lyrics_lookup_pending())

    def test_loading_marker_paints_skeleton_without_shaping_placeholder_lyrics(self):
        self.owner.track_title = "Waiting for lyrics"
        self.owner.lyrics = ["查找歌词中"]
        self.owner.timed_lyrics = []
        self.window.resize(960, 540)
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window.render(image)
        with patch.object(self.window, "_paint_lyric_loading", wraps=self.window._paint_lyric_loading) as loading, patch.object(
            self.window, "_song_lyric_layouts", wraps=self.window._song_lyric_layouts,
        ) as layouts, patch("local.music_surface_window.time.monotonic", return_value=101.25):
            self.window.render(image)
        loading.assert_called()
        self.assertEqual(layouts.call_args.args[0], [])

    def test_offset_edit_reanchors_once_without_requesting_player_seek(self):
        offset = [0.0]
        self.owner.track_title = "offset test"
        self.owner.media_position_seconds = 2.0
        self.owner._effective_lyric_offset_seconds = lambda: offset[0]
        timed = [(0.0, "first"), (4.0, "second"), (8.0, "third")]
        self.window._lyric_frame(timed, 10.0)
        offset[0] = 5.0
        self.window.on_lyric_offset_changed()
        with patch.object(self.window._lyric_motion, "frame", wraps=self.window._lyric_motion.frame) as motion:
            frame = self.window._lyric_frame(timed, 10.02)
            self.assertTrue(motion.call_args.kwargs["explicit_seek"])
            self.assertEqual(frame.active_index, 1)
            self.window._lyric_frame(timed, 10.04)
            self.assertFalse(motion.call_args.kwargs["explicit_seek"])
        self.assertEqual(self.owner.seek_calls, [])

    def test_real_unit_front_reuses_stable_playhead_after_a_stale_clock_spike(self):
        self.owner.media_track_id = "real-timed-song"
        self.owner.timed_lyrics = [
            (0.0, "谁人都可以"), (8.0, "下一句"), (12.0, "第三句"), (16.0, "第四句"),
        ]
        units = tuple(
            SimpleNamespace(start_seconds=float(index), end_seconds=float(index + 1), text=char)
            for index, char in enumerate("谁人都可以")
        )
        self.owner.timed_lyric_units = [units, (), (), ()]
        self.owner.timed_lyric_timing_kinds = ["unit", "line", "line", "line"]
        self.owner.media_is_playing = True
        self.owner.media_position_seconds = 0.2
        self.owner.media_position_sync_at = 100.0
        self.owner.media_raw_position_seconds = 0.2
        self.owner.media_position_source = "windows-media"
        self.owner._estimated_media_position = lambda now: self.owner.media_position_seconds
        _lines, timed = self.window._display_lyrics()
        self.window._lyric_frame(timed, 100.0)
        self.owner.media_position_seconds = 14.0
        self.owner.media_raw_position_seconds = 14.0
        self.owner.media_position_sync_at = 100.01
        frame = self.window._lyric_frame(timed, 100.01)
        self.assertEqual(frame.active_index, 0)
        self.assertLess(self.window._lyric_motion.last_position, 0.3)
        font = self.window._surface_font(38, QFont.Weight.Bold)
        layout, _ = self.window._wrapped_lyric_layout("谁人都可以", font, 600.0)
        progress = self.window._real_visual_line_progresses(
            "谁人都可以", layout, units, self.window._lyric_motion.last_position, line_end=8.0,
        )
        self.assertLess(progress[0], 0.1)

    def test_real_wrapped_original_and_translation_have_separate_clock_spans(self):
        text = "谁人都可以看见未来\u2028Everyone can see the future"
        primary = "谁人都可以看见未来"
        units = tuple(
            SimpleNamespace(start_seconds=float(index), end_seconds=float(index + 1), text=char)
            for index, char in enumerate(primary)
        )
        font = self.window._surface_font(38, QFont.Weight.Bold)
        layout, _ = self.window._wrapped_lyric_layout(text, font, 170.0)
        self.assertGreater(layout.lineCount(), 2)
        at_first = self.window._real_visual_line_progresses(text, layout, units, 0.5, line_end=10.0)
        self.assertGreater(at_first[0], 0.0)
        self.assertTrue(all(value == 0.0 for value in at_first[1:]))
        at_later = self.window._real_visual_line_progresses(text, layout, units, 5.5, line_end=10.0)
        self.assertEqual(at_later[0], 1.0)
        self.assertGreater(at_later[1], 0.0)
        self.assertEqual(at_later[-1], 0.0)

    def test_real_slow_read_has_continuous_subpixel_lift_and_holds_when_paused(self):
        font = self.window._surface_font(38, QFont.Weight.Bold)
        text = "谁人都可以"
        units = tuple(
            SimpleNamespace(start_seconds=index * 2.0, end_seconds=(index + 1) * 2.0, text=char)
            for index, char in enumerate(text)
        )
        layout, _ = self.window._wrapped_lyric_layout(text, font, 600.0)
        image, boundaries = self.window._glyph_run(text, font)
        rises = []
        for tick in range(90):
            position = 1.0 + tick / 170.0
            progress = self.window._real_visual_line_progresses(text, layout, units, position, line_end=10.0)[0]
            rises.append(
                self.window._reading_lift_height()
                * continuous_read_lift(progress, 0, len(boundaries) - 1)
            )
        self.assertGreater(len(set(round(value, 4) for value in rises)), 85)
        self.assertEqual(rises, sorted(rises))
        held_a = self.window._real_visual_line_progresses(text, layout, units, 1.5, line_end=10.0)
        held_b = self.window._real_visual_line_progresses(text, layout, units, 1.5, line_end=10.0)
        self.assertEqual(held_a, held_b)

    def test_real_five_second_latin_word_paints_the_eight_stage_letter_glow(self):
        text = "shine"
        units = (SimpleNamespace(start_seconds=45.0, end_seconds=50.0, text=text),)
        groups = explicit_slow_unit_groups(text, units)
        font = self.window._surface_font(48, QFont.Weight.Bold)
        image, _ = self.window._glyph_run(text, font, "character")
        isolated = self.window._isolated_glyph_units(text, font, "character")
        for source, _left in isolated:
            self.window._sparse_glyph_halo(source)
        front = 1.5
        states = self.window._real_sparse_glyph_states(
            text, font, 0, groups, front / len(text), 46.5,
        )
        self.assertEqual(len(states), 5)
        self.assertGreater(max(state.scale for state in states), 1.0)
        self.assertGreater(max(state.glow for state in states), 0.0)
        self.assertEqual(states[-1].glow, 0.0)
        output = QImage(500, 150, QImage.Format.Format_ARGB32_Premultiplied)
        output.fill(Qt.GlobalColor.transparent)
        with QPainter(output) as painter:
            self.window._paint_sparse_glyph_run(
                painter, QPointF(15.0, 20.0), image.height(), text, font,
                front / len(text), QColor(130, 90, 80), glyph_states=states,
            )
        self.assertGreater(
            sum(1 for y in range(output.height()) for x in range(output.width())
                if output.pixelColor(x, y).alpha() > 0),
            100,
        )

    def test_metadata_marquee_round_trip_then_pauses_five_seconds_at_start(self):
        offset = self.window._metadata_scroll_offset
        self.assertEqual(offset(0.0, 1.5), (0.0, False))
        self.assertEqual(offset(140.0, 0.0), (0.0, False))
        self.assertEqual(offset(140.0, 4.999), (0.0, False))
        self.assertAlmostEqual(offset(140.0, 7.5)[0], 140.0)
        self.assertAlmostEqual(offset(140.0, 8.2)[0], 140.0)
        outbound = [offset(140.0, at)[0] for at in (5.0, 5.7, 6.5, 7.5)]
        inbound = [offset(140.0, at)[0] for at in (8.2, 8.9, 9.8, 10.7)]
        self.assertEqual(outbound, sorted(outbound))
        self.assertEqual(inbound, sorted(inbound, reverse=True))
        self.assertTrue(offset(140.0, 5.7)[1])
        self.assertTrue(offset(140.0, 8.9)[1])
        for at in (10.7, 12.0, 15.69):
            self.assertEqual(offset(140.0, at), (0.0, False))
        self.assertGreater(offset(140.0, 15.8)[0], 0.0)
        self.assertAlmostEqual(self.window._metadata_next_motion_delay(0.0), 5.0)
        self.assertAlmostEqual(self.window._metadata_next_motion_delay(7.7), 0.5)

    def test_long_metadata_is_clipped_and_track_change_restarts_both_fields(self):
        image = QImage(320, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        title = "A very long title that must remain inside its own slot"
        artist = "A very long artist name with multiple collaborators"
        font = QFont("Segoe UI", 20)
        clip = QRectF(70.0, 16.0, 110.0, 42.0)
        with QPainter(image) as painter:
            painter.setPen(QColor(255, 255, 255))
            self.window._paint_scrolling_metadata(
                painter, clip, title, font, 100.0, "track-a", field="title",
            )
            self.window._paint_scrolling_metadata(
                painter, clip, artist, font, 100.0, "track-a", field="artist",
            )
            self.window._paint_scrolling_metadata(
                painter, clip, title, font, 105.5, "track-a", field="title",
            )
        self.assertTrue(self.window._metadata_marquee_moving)
        self.assertTrue(self.window._metadata_animation_rect.contains(clip.center().toPoint()))
        self.assertEqual(self.window._metadata_marquee_states["title"][2], 100.0)
        self.assertEqual(self.window._metadata_marquee_states["artist"][2], 100.0)
        self.assertTrue(any(image.pixelColor(x, y).alpha() > 0
                            for x in range(75, 175) for y in range(20, 55)))
        self.assertEqual(image.pixelColor(68, 35).alpha(), 0)
        self.assertEqual(image.pixelColor(182, 35).alpha(), 0)
        with QPainter(image) as painter:
            self.window._paint_scrolling_metadata(
                painter, clip, title, font, 102.0, "track-b", field="title",
            )
            self.window._paint_scrolling_metadata(
                painter, clip, artist, font, 102.0, "track-b", field="artist",
            )
        self.assertEqual(self.window._metadata_marquee_states["title"][2], 102.0)
        self.assertEqual(self.window._metadata_marquee_states["artist"][2], 102.0)

    def test_long_metadata_scrolls_in_normal_layout_across_window_modes(self):
        self.owner.track_title = "A very long song title that cannot fit in the normal width"
        self.owner.artist = "A very long list of participating artists and composers"
        self.owner.timed_lyrics = [
            (0.0, "first line"), (4.0, "second line"),
            (8.0, "third line"), (12.0, "fourth line"),
        ]
        self.window.resize(960, 540)
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window.render(image)
        self.assertEqual(self.window._metadata_marquee_states["title"][2], 100.0)
        self.assertEqual(self.window._metadata_marquee_states["artist"][2], 100.0)
        with patch("local.music_surface_window.time.monotonic", return_value=105.5):
            self.window.render(image)
        self.assertTrue(self.window._metadata_marquee_moving)
        self.window._fullscreen = True
        with patch("local.music_surface_window.time.monotonic", return_value=106.0):
            self.window.render(image)
        self.assertEqual(self.window._metadata_marquee_states["title"][2], 100.0)
        self.assertEqual(self.window._metadata_marquee_states["artist"][2], 100.0)
        self.assertTrue(self.window._metadata_marquee_moving)

    def test_metadata_prewarm_preserves_marquee_width_and_windowed_native_text(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        text = "A very long title with enough letters to scroll across the artwork"
        font = self.window._surface_font(40.0)
        color = QColor(255, 255, 255, 242)
        slot = QRectF(20.0, 10.0, 260.0, 58.0)
        image = QImage(320, 90, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with (
            patch.object(self.window, "_control_reveal_at", return_value=0.5),
            patch.object(self.window, "_metadata_scroll_offset", wraps=self.window._metadata_scroll_offset) as scroll,
        ):
            with QPainter(image) as painter:
                painter.setPen(color)
                self.window._paint_scrolling_metadata(
                    painter, slot, text, font, 101.5, "track-a", field="title",
                )
            before = scroll.call_args.args[0]
            self.window._metadata_text_raster(
                text, self.window._metadata_reference_font("title"), color, 1.0,
                "track-a", create=True,
            )
            with QPainter(image) as painter:
                painter.setPen(color)
                self.window._paint_scrolling_metadata(
                    painter, slot, text, font, 101.5, "track-a", field="title",
                )
            self.assertAlmostEqual(scroll.call_args.args[0], before)

        self.window._fullscreen = False
        with (
            patch.object(self.window, "_control_reveal_at", return_value=1.0),
            patch.object(self.window, "_metadata_text_raster", side_effect=AssertionError("visible windowed text must remain native")),
        ):
            with QPainter(image) as painter:
                painter.setPen(color)
                self.window._paint_scrolling_metadata(
                    painter, slot, text, font, 101.5, "track-a", field="title", animate=False,
                )

    def test_metadata_idle_prewarm_builds_two_exact_and_two_expansion_rasters(self):
        self.owner.track_title = "A long song title to prewarm"
        self.owner.artist = "A long list of artists to prewarm"
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        self.window._reset_control_idle(100.0)
        for instant in (100.5, 100.51, 100.52, 101.0):
            with patch("local.music_surface_window.time.monotonic", return_value=instant):
                self.window._on_animation_tick()
        self.assertEqual(len(self.window._metadata_raster_cache), 4)
        self.assertEqual(len(self.window._metadata_prewarm_queue), 0)
        self.assertEqual(len(self.window._metadata_idle_raster_pending), 0)
        self.assertLessEqual(
            self.window._metadata_raster_cache_bytes,
            self.window.METADATA_RASTER_CACHE_MAX_BYTES,
        )

    def test_idle_metadata_prewarm_builds_one_raster_and_discards_old_track_job(self):
        self.owner.media_track_id = "song-a"
        self.owner.track_title = "A title"
        self.owner.artist = "An artist"
        self.window._reset_control_idle(100.0)
        with patch("local.music_surface_window.time.monotonic", return_value=100.1):
            self.window._prewarm_idle_metadata_raster()
        self.assertEqual(len(self.window._metadata_raster_cache), 1)
        self.assertEqual(len(self.window._metadata_idle_raster_pending), 1)
        self.owner.media_track_id = "song-b"
        self.owner.track_title = "A new title"
        with patch("local.music_surface_window.time.monotonic", return_value=100.2):
            self.window._prewarm_idle_metadata_raster()
        identities = [key[0] for key in self.window._metadata_raster_cache]
        self.assertEqual(identities, ["song-a", "song-b"])
        self.assertEqual(len(self.window._metadata_idle_raster_pending), 1)
        self.assertEqual(self.window._metadata_idle_raster_pending[0][-1], "song-b")

    def test_visible_metadata_uses_exact_font_raster_without_scaling_or_new_shaping(self):
        self.owner.media_track_id = "song"
        self.owner.track_title = "A long title with a stable exact size"
        self.owner.artist = "An artist"
        self.window.resize(1920, 1080)
        self.window._reset_control_idle(100.0)
        for dpr in (1.0, 2.0):
            with (
                self.subTest(dpr=dpr),
                patch.object(self.window, "devicePixelRatioF", return_value=dpr),
                patch("local.music_surface_window.time.monotonic", return_value=100.1),
            ):
                self.window._prewarm_idle_metadata_raster()
                self.window._prewarm_idle_metadata_raster()
                font = self.window._surface_font(
                    self.window.TITLE_PIXEL_SIZE * self.window._surface_scale(), QFont.Weight.Bold,
                )
                painter = Mock()
                painter.device.return_value.devicePixelRatioF.return_value = dpr
                painter.pen.return_value.color.return_value = QColor(255, 255, 255, 242)
                with patch.object(
                    self.window, "_metadata_text_raster", wraps=self.window._metadata_text_raster,
                ) as raster:
                    self.window._paint_scrolling_metadata(
                        painter, QRectF(4.0, 8.0, 800.0, 110.0), self.owner.track_title,
                        font, 100.1, "song", field="title", animate=False,
                    )
                painter.drawText.assert_not_called()
                painter.drawImage.assert_called_once()
                destination, source = painter.drawImage.call_args.args
                self.assertAlmostEqual(destination.width(), source.width() / dpr)
                self.assertAlmostEqual(destination.height(), source.height() / dpr)
                self.assertEqual(raster.call_count, 1)
                self.assertEqual(raster.call_args.args[1], font)
                self.assertFalse(raster.call_args.kwargs.get("create", False))

    def test_fullscreen_metadata_expands_in_staggered_rows_without_glyph_scaling(self):
        self.window._fullscreen = True
        for scale in (0.5, 1.0, 2.0, 2.5):
            with self.subTest(scale=scale):
                content = QRectF(60 * scale, 60 * scale, 1070 * scale, 546 * scale)
                cover = QRectF(content.left(), content.top(), 404 * scale, 404 * scale)
                visible = self.window._metadata_layout(cover, content, scale, 1.0)
                midway = self.window._metadata_layout(cover, content, scale, 0.5)
                hidden = self.window._metadata_layout(cover, content, scale, 0.0)
                self.assertLess(visible[0].pixelSize(), midway[0].pixelSize())
                self.assertLessEqual(midway[0].pixelSize(), hidden[0].pixelSize())
                self.assertLess(visible[2].pixelSize(), hidden[2].pixelSize())
                self.assertGreater(hidden[1].top(), visible[1].top())
                self.assertGreater(hidden[3].top(), visible[3].top())
                self.assertGreaterEqual(hidden[1].top(), cover.bottom())
                self.assertLess(hidden[3].bottom(), 666 * scale)
        self.window._fullscreen = False
        windowed_visible = self.window._metadata_layout(cover, content, scale, 1.0)
        windowed_idle = self.window._metadata_layout(cover, content, scale, 0.0)
        self.assertLess(windowed_visible[0].pixelSize(), windowed_idle[0].pixelSize())
        self.assertLess(windowed_visible[1].top(), windowed_idle[1].top())

    def test_metadata_transition_keeps_fractional_size_between_integer_font_steps(self):
        self.window._fullscreen = True
        content = QRectF(60.0, 60.0, 1070.0, 546.0)
        cover = QRectF(60.0, 60.0, 404.0, 404.0)
        first = self.window._metadata_layout(cover, content, 1.0, 0.500)
        first_draw_size = self.window._metadata_draw_pixel_sizes["title"]
        second = self.window._metadata_layout(cover, content, 1.0, 0.499)
        second_draw_size = self.window._metadata_draw_pixel_sizes["title"]
        self.assertEqual(first[0].pixelSize(), second[0].pixelSize())
        self.assertNotEqual(first_draw_size, second_draw_size)

    def test_active_marquee_gets_local_updates_between_ambient_frames(self):
        self.window._fullscreen = True
        self.window._reset_control_idle(100.0)
        self.window._metadata_marquee_moving = True
        self.window._metadata_animation_rect = QRect(67, 480, 390, 110)
        with (
            patch("local.music_surface_window.time.monotonic", return_value=100.1),
            patch.object(self.window, "update") as update,
        ):
            self.window._on_animation_tick()
        update.assert_any_call(QRect(67, 480, 390, 110))

    def test_lyric_only_frame_skips_metadata_and_control_ink(self):
        self.owner.media_track_id = "song"
        self.owner.timed_lyrics = [(0.0, "first line"), (4.0, "second line")]
        self.owner.media_position_seconds = 1.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        self.window.render(output)
        lyric_region = QRegion(self.window._active_lyric_animation_rect)
        with (
            patch.object(self.window, "_paint_scrolling_metadata", wraps=self.window._paint_scrolling_metadata) as metadata,
            patch.object(self.window, "_paint_control_layer", wraps=self.window._paint_control_layer) as controls,
        ):
            self.window.render(output, QPoint(0, 0), lyric_region)
        metadata.assert_not_called()
        controls.assert_not_called()
        self.assertEqual(len(self.window._media_button_rects), 3)
        self.assertIn("mode", self.window._button_rects)

    def test_control_only_repaint_does_not_rescale_out_of_region_cover(self):
        self.owner.album_art = QImage(600, 600, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor(160, 80, 35))
        self.window.resize(1190, 666)
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        self.window.render(output, QPoint(0, 0), QRegion(QRect(1030, 0, 160, 100)))
        self.assertTrue(self.window._cover_cache.isNull())
        surface = QRectF(self.window.rect())
        scale = self.window._surface_scale(surface)
        bottom_height = self.window.CONTROL_BOTTOM_REGION_HEIGHT * scale
        bottom_controls = QRectF(
            0.0, surface.bottom() - bottom_height, 505.0 * scale, bottom_height,
        ).toAlignedRect()
        self.window.render(output, QPoint(0, 0), QRegion(bottom_controls))
        self.assertTrue(self.window._cover_cache.isNull())
        self.window.render(output, QPoint(0, 0), QRegion(QRect(40, 40, 450, 450)))
        self.assertFalse(self.window._cover_cache.isNull())

    def test_noncurrent_lyric_blur_has_two_cached_levels(self):
        self.owner.timed_lyrics = [
            (0.0, "first line"), (3.0, "second line"),
            (6.0, "current line"), (9.0, "fourth line"),
            (12.0, "fifth line"), (15.0, "sixth line"),
            (18.0, "seventh line"),
        ]
        self.owner.media_position_seconds = 6.4
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(960, 540)
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        self.window.render(output)
        levels = {level for _source_key, level in self.window._lyric_blur_cache}
        self.assertEqual(levels, {1, 2})
        cache_count = len(self.window._lyric_blur_cache)
        self.window.render(output)
        self.assertEqual(len(self.window._lyric_blur_cache), cache_count)

    def test_next_wrapped_line_blurs_precede_its_boundary_paint(self):
        self.owner.media_track_id = "wrapped-prewarm"
        self.owner.media_is_playing = True
        self.owner.enable_dual_line_lyrics = False
        self.owner.timed_lyrics = [
            (
                float(index * 3),
                f"Line {index} we were just two lovers driving through the summer "
                "and remembering every word of this long verse",
            )
            for index in range(16)
        ]
        self.owner.media_position_seconds = 16.6
        # Keep this one source observation projecting while the autonomous
        # timer advances, just as the owner does for a playing provider.
        self.owner.media_position_sampled_at = 100.0
        self.owner._estimated_media_position = lambda now: 16.6 + max(0.0, now - 100.0)
        self.window.resize(1920, 1080)
        self.window._fullscreen = True
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window.render(output)
        self.assertEqual(self.window._lyric_motion.active_index, 5)
        self.assertTrue(self.window._lyric_blur_prewarm_queue)

        # At 60 Hz the 1.4-second lead still has enough ticks to finish.
        for step in range(84):
            if not self.window._lyric_blur_prewarm_queue:
                break
            previous = len(self.window._lyric_blur_cache)
            with patch("local.music_surface_window.time.monotonic", return_value=100.0 + (step + 1) / 60.0):
                self.window._on_animation_tick()
            self.assertLessEqual(len(self.window._lyric_blur_cache) - previous, 1)
        self.assertFalse(self.window._lyric_blur_prewarm_queue)

        self.owner.media_position_seconds = 18.1
        with (
            patch("local.music_surface_window.time.monotonic", return_value=102.0),
            patch("local.music_surface_window.ImageFilter.GaussianBlur", side_effect=AssertionError("boundary blur")),
        ):
            self.window.render(output)
            self.window.render(output)
        self.assertEqual(self.window._lyric_motion.active_index, 6)

    def test_blur_prewarm_discards_jobs_when_playback_context_changes(self):
        for change in ("track", "pause", "browse"):
            with self.subTest(change=change):
                owner = _Owner()
                owner.media_track_id = "prewarm-original"
                owner.media_is_playing = True
                owner.enable_dual_line_lyrics = False
                owner.timed_lyrics = [
                    (float(index * 3), f"Wrapped lyric {index} with enough words to fill several lines")
                    for index in range(8)
                ]
                owner.media_position_seconds = 4.8
                owner._estimated_media_position = lambda _now: owner.media_position_seconds
                window = MusicSurfaceWindow(owner)
                window.resize(960, 540)
                output = QImage(window.size(), QImage.Format.Format_RGB32)
                try:
                    with patch("local.music_surface_window.time.monotonic", return_value=100.0):
                        window.render(output)
                    self.assertTrue(window._lyric_blur_prewarm_queue)
                    if change == "track":
                        owner.media_track_id = "prewarm-new-track"
                    elif change == "pause":
                        owner.media_is_playing = False
                    else:
                        window.wheelEvent(_WheelEvent(window._lyric_clip_rect.center(), -120))
                    with (
                        patch("local.music_surface_window.time.monotonic", return_value=100.01),
                        patch("local.music_surface_window.ImageFilter.GaussianBlur", side_effect=AssertionError("stale blur")),
                    ):
                        window._on_animation_tick()
                    self.assertFalse(window._lyric_blur_prewarm_queue)
                    self.assertIsNone(window._lyric_blur_prewarm_token)
                finally:
                    window.shutdown()
                    owner.close()

    def test_wheel_browsed_lyric_is_crisp_without_changing_playback_reading(self):
        self.owner.media_track_id = "song"
        self.owner.timed_lyrics = [
            (float(index * 3), f"line {index}") for index in range(6)
        ]
        self.owner.media_position_seconds = 6.4
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(960, 540)
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window.render(output)
            wheel = _WheelEvent(self.window._lyric_clip_rect.center(), -120)
            self.window.wheelEvent(wheel)
        self.assertTrue(wheel.accepted)
        self.assertEqual(self.window._browse_index, 3.0)
        self.assertEqual(self.window._browse_until, 104.0)
        with (
            patch("local.music_surface_window.time.monotonic", return_value=100.1),
            patch.object(self.window, "_draw_future_wrapped_layout", wraps=self.window._draw_future_wrapped_layout) as future,
            patch.object(self.window, "_draw_sprung_cached_wrapped_layout", wraps=self.window._draw_sprung_cached_wrapped_layout) as sprung,
        ):
            self.window.render(output)
        browsed = [call for call in future.call_args_list if call.args[3] == "line 3"]
        playing = [call for call in sprung.call_args_list if call.args[3] == "line 2"]
        self.assertTrue(browsed)
        self.assertEqual(browsed[-1].kwargs["blur_level"], 0)
        self.assertTrue(playing)
        self.assertEqual(playing[-1].kwargs["blur_level"], 1)
        self.assertEqual(self.window._lyric_motion.active_index, 2)

    def test_wheel_browse_refreshes_timeout_and_returns_focus_to_live_line(self):
        self.owner.media_track_id = "song"
        self.owner.timed_lyrics = [
            (float(index * 3), f"line {index}") for index in range(7)
        ]
        self.owner.media_position_seconds = 6.4
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(960, 540)
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window.render(output)
            center = self.window._lyric_clip_rect.center()
            self.window.wheelEvent(_WheelEvent(center, -120))
        with patch("local.music_surface_window.time.monotonic", return_value=101.0):
            self.window.wheelEvent(_WheelEvent(center, -120))
        self.assertEqual(self.window._browse_index, 4.0)
        self.assertEqual(self.window._browse_until, 105.0)
        with patch("local.music_surface_window.time.monotonic", return_value=102.0):
            self.window.wheelEvent(_WheelEvent(center, 120))
        self.assertEqual(self.window._browse_index, 3.0)
        self.assertEqual(self.window._browse_until, 106.0)
        self.assertEqual(self.window._lyric_view_anchor(2, 7, 105.99), (3.0, 3, True))
        anchor, focus, manual = self.window._lyric_view_anchor(2, 7, 106.01)
        self.assertIsNone(self.window._browse_index)
        self.assertTrue(manual)
        self.assertGreater(anchor, 2.99)
        self.assertEqual(focus, 3)
        anchor, focus, manual = self.window._lyric_view_anchor(2, 7, 106.25)
        self.assertGreater(anchor, 2.0)
        self.assertLess(anchor, 3.0)
        self.assertIn(focus, {2, 3})
        self.assertTrue(manual)
        self.assertEqual(self.window._lyric_view_anchor(2, 7, 106.50), (2.0, 2, False))
        self.assertIsNone(self.window._browse_return_from)

    def test_animation_tick_releases_idle_browse_without_a_mouse_event(self):
        self.window._browse_index = 4.0
        self.window._browse_until = 104.0
        with (
            patch("local.music_surface_window.time.monotonic", return_value=104.01),
            patch.object(self.window, "update") as update,
        ):
            self.window._on_animation_tick()
        self.assertIsNone(self.window._browse_index)
        self.assertEqual(self.window._browse_return_from, 4.0)
        update.assert_called()

    def test_stale_browse_after_hidden_window_returns_directly_to_live_line(self):
        self.window._browse_index = 4.0
        self.window._browse_until = 104.0
        self.assertEqual(self.window._lyric_view_anchor(2, 7, 110.0), (2.0, 2, False))
        self.assertIsNone(self.window._browse_return_from)

    def test_blurred_lyric_keeps_ink_hue_at_translucent_edges(self):
        source = QImage(40, 40, QImage.Format.Format_ARGB32_Premultiplied)
        source.fill(Qt.GlobalColor.transparent)
        color = QColor(240, 110, 96)
        with QPainter(source) as painter:
            painter.fillRect(QRectF(12, 12, 16, 16), color)
        blurred = self.window._blurred_lyric_raster(source, 2, color)
        edge = blurred.pixelColor(10, 20)
        self.assertGreater(edge.alpha(), 0)
        self.assertEqual(edge.getRgb()[:3], color.getRgb()[:3])

    def test_blur_levels_spread_ink_without_clipping_wrapped_line_bleed(self):
        source = QImage(100, 80, QImage.Format.Format_ARGB32_Premultiplied)
        source.fill(Qt.GlobalColor.transparent)
        color = QColor(255, 255, 255)
        with QPainter(source) as painter:
            painter.fillRect(QRectF(30, 25, 30, 30), color)
        near = self.window._blurred_lyric_raster(source, 1, color)
        far = self.window._blurred_lyric_raster(source, 2, color)
        self.assertGreater(self.window._lyric_blur_radius(1), 1.3)
        self.assertGreater(self.window._lyric_blur_radius(2), 2.7)
        self.assertGreaterEqual(self.window._lyric_blur_bleed(2), self.window._lyric_blur_radius(2) * 3)
        self.assertGreater(near.pixelColor(27, 40).alpha(), 0)
        self.assertGreater(far.pixelColor(24, 40).alpha(), near.pixelColor(24, 40).alpha())
        self.assertLess(far.pixelColor(45, 40).alpha(), near.pixelColor(45, 40).alpha())

    def test_wrapped_blur_does_not_repaint_neighbor_during_spring(self):
        font = self.window._surface_font(28.0)
        text = "first phrase second phrase third phrase fourth phrase"
        layout, height = self.window._wrapped_lyric_layout(text, font, 185.0)
        self.assertGreater(layout.lineCount(), 1)
        offsets = tuple(10.0 if index == 0 else -10.0 for index in range(layout.lineCount()))
        color = QColor(240, 110, 96)
        origin = QPointF(25.0, 35.0)
        actual = QImage(420, 300, QImage.Format.Format_ARGB32_Premultiplied)
        actual.fill(Qt.GlobalColor.transparent)
        with QPainter(actual) as painter:
            self.window._draw_sprung_cached_wrapped_layout(
                painter, layout, origin, text, font, 185.0, height,
                color, offsets, blur_level=2,
            )
        expected = QImage(actual.size(), QImage.Format.Format_ARGB32_Premultiplied)
        expected.fill(Qt.GlobalColor.transparent)
        source, padding = self.window._lyric_raster(text, font, 185.0, height, layout, color)
        with QPainter(expected) as painter:
            for index, offset in enumerate(offsets):
                isolated, bleed = self.window._blurred_visual_line(
                    source, layout, index, 2, color, padding,
                )
                painter.drawImage(
                    QPointF(origin.x() - padding, origin.y() + layout.lineAt(index).y() + offset - bleed),
                    isolated,
                )
        self.assertEqual(bytes(actual.bits()), bytes(expected.bits()))

    def test_direct_wrapped_blur_helper_can_keep_native_resolution_on_high_dpi(self):
        font = self.window._surface_font(28.0)
        text = "one line"
        layout, height = self.window._wrapped_lyric_layout(text, font, 300.0)
        with patch.object(self.window, "devicePixelRatioF", return_value=2.0):
            source, padding = self.window._lyric_raster(
                text, font, 300.0, height, layout, QColor(255, 255, 255),
            )
        isolated, _bleed = self.window._blurred_visual_line(
            source, layout, 0, 2, QColor(255, 255, 255), padding,
        )
        self.assertEqual(isolated.devicePixelRatio(), 2.0)
        self.assertEqual(isolated.width(), source.width())
        self.assertTrue(any(
            isolated.pixelColor(x, y).alpha() > 100
            for y in range(isolated.height()) for x in range(isolated.width())
        ))

    def test_noncurrent_blurred_rows_use_bounded_raster_dpi(self):
        self.owner.timed_lyrics = [
            (0.0, "past line"), (3.0, "current line"),
            (6.0, "future line that wraps into another visual line for testing"),
            (9.0, "next future line"),
        ]
        self.owner.media_position_seconds = 3.3
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(960, 540)
        image = QImage(1920, 1080, QImage.Format.Format_RGB32)
        image.setDevicePixelRatio(2.0)
        with patch.object(self.window, "devicePixelRatioF", return_value=2.0):
            self.window.render(image)
        self.assertTrue(self.window._lyric_blur_cache)
        self.assertTrue(all(
            blurred.devicePixelRatio() <= self.window.BLURRED_LYRIC_DPR_CAP
            for blurred, _size in self.window._lyric_blur_cache.values()
        ))

    def test_tighter_wrapped_blur_does_not_include_previous_visual_line_ink(self):
        font = self.window._surface_font(38.0)
        text = "MMMMMMMM\u2028I"
        layout, height = self.window._wrapped_lyric_layout(text, font, 600.0)
        self.assertEqual(layout.lineCount(), 2)
        self.assertLess(layout.lineAt(1).y(), layout.lineAt(0).height())
        color = QColor(255, 255, 255)
        source, padding = self.window._lyric_raster(text, font, 600.0, height, layout, color)
        second, _bleed = self.window._blurred_visual_line(source, layout, 1, 2, color, padding)
        self.assertTrue(any(second.pixelColor(x, y).alpha() > 50
                            for y in range(second.height()) for x in range(0, 90)))
        self.assertFalse(any(second.pixelColor(x, y).alpha() > 0
                             for y in range(second.height()) for x in range(140, second.width())))

    def test_tighter_wrapped_clear_lines_do_not_ghost_during_spring(self):
        font = self.window._surface_font(38.0)
        text = "MMMMMMMM\u2028I"
        layout, height = self.window._wrapped_lyric_layout(text, font, 600.0)
        origin = QPointF(25.0, 35.0)
        offsets = (12.0, -12.0)
        color = QColor(255, 255, 255)
        actual = QImage(600, 200, QImage.Format.Format_ARGB32_Premultiplied)
        actual.fill(Qt.GlobalColor.transparent)
        with QPainter(actual) as painter:
            self.window._draw_sprung_cached_wrapped_layout(
                painter, layout, origin, text, font, 600.0, height, color, offsets,
            )
        expected = QImage(actual.size(), actual.format())
        expected.fill(Qt.GlobalColor.transparent)
        with QPainter(expected) as painter:
            painter.setPen(color)
            for index, offset in enumerate(offsets):
                layout.lineAt(index).draw(painter, QPointF(origin.x(), origin.y() + offset))
        self.assertEqual(bytes(actual.bits()), bytes(expected.bits()))

    def test_pause_cover_scaling_keeps_center_and_reverses_smoothly(self):
        self.owner.media_is_playing = True
        self.assertAlmostEqual(self.window._cover_scale_at(10.0), 0.8)
        self.assertAlmostEqual(self.window._cover_scale_at(10.4), 1.0)
        self.owner.media_is_playing = False
        self.assertAlmostEqual(self.window._cover_scale_at(10.5), 1.0)
        halfway = self.window._cover_scale_at(10.64)
        self.assertGreater(halfway, 0.8)
        self.assertLess(halfway, 1.0)
        self.assertAlmostEqual(self.window._cover_scale_at(10.9), 0.8)
        cover = QRectF(60.0, 60.0, 404.0, 404.0)
        small = self.window._scaled_cover_rect(cover, 0.8)
        self.assertEqual(small.center(), cover.center())
        self.assertAlmostEqual(small.width(), cover.width() * 0.8)
        self.owner.media_is_playing = True
        self.assertAlmostEqual(self.window._cover_scale_at(11.0), 0.8)
        self.assertGreater(self.window._cover_scale_at(11.14), 0.8)
        self.assertAlmostEqual(self.window._cover_scale_at(11.4), 1.0)

    def test_fullscreen_no_lyrics_art_moves_to_center_and_returns_continuously(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        self.owner.track_title = "Instrumental"
        self.owner.artist = "A musician"
        self.owner.lyrics = []
        self.owner.timed_lyrics = []
        original, centered = self.window._cover_layout_endpoints()
        first = self.window._current_cover_rect(100.0)
        middle = self.window._current_cover_rect(100.28)
        arrived = self.window._current_cover_rect(100.7)
        self.assertEqual(first, original)
        self.assertGreater(middle.width(), original.width())
        self.assertLess(middle.width(), centered.width())
        self.assertGreater(middle.center().x(), first.center().x())
        self.assertEqual(arrived, centered)
        self.assertAlmostEqual(arrived.center().x(), QRectF(self.window.rect()).center().x())
        self.assertLess(arrived.center().y(), QRectF(self.window.rect()).center().y())
        self.assertEqual(
            self.window._scaled_cover_rect(arrived, 0.8).center(), arrived.center(),
        )

        # Four resolved lyric rows reclaim the old column without a one-frame
        # jump; the same threshold applies to untimed rows.
        self.owner.timed_lyrics = [
            (0.0, "words have arrived"), (4.0, "second line"),
            (8.0, "third line"), (12.0, "fourth line"),
        ]
        returned_first = self.window._current_cover_rect(101.0)
        returned_middle = self.window._current_cover_rect(101.28)
        returned = self.window._current_cover_rect(101.7)
        self.assertEqual(returned_first, centered)
        self.assertLess(returned_middle.width(), centered.width())
        self.assertGreater(returned_middle.width(), original.width())
        self.assertEqual(returned, original)
        self.owner.timed_lyrics = []
        self.owner.lyrics = ["first", "second", "third", "fourth"]
        self.assertEqual(self.window._current_cover_rect(102.5), original)

    def test_no_lyrics_cover_requires_real_track_and_reuses_one_maximum_art_raster(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        self.owner.track_title = "暂无媒体"
        self.owner.album_art = QImage(600, 600, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor(160, 80, 35))
        self.assertFalse(self.window._no_lyrics_cover_target())
        self.owner.track_title = "Instrumental"
        self.assertTrue(self.window._no_lyrics_cover_target())
        self.owner.no_lyrics_text = "纯音乐"
        self.owner.lyrics = ["纯音乐"]
        self.assertTrue(self.window._no_lyrics_cover_target())
        self.owner.timed_lyrics = [(0.0, "纯音乐")]
        self.assertTrue(self.window._no_lyrics_cover_target())
        self.owner.timed_lyrics = []
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        keys = []
        for instant in (100.0, 100.25, 100.55, 100.8):
            with patch("local.music_surface_window.time.monotonic", return_value=instant):
                self.window.render(output)
            keys.append(self.window._cover_cache.cacheKey())
        self.assertTrue(all(key == keys[0] for key in keys))
        self.assertFalse(self.window._cover_cache.isNull())
        self.assertEqual(self.window._lyric_hit_rects, {})

        # The lyric column can be the only exposed region while artwork is
        # traveling home; metadata clipping must not own its cover mask.
        self.owner.timed_lyrics = [
            (0.0, "A new lyric"), (4.0, "second"),
            (8.0, "third"), (12.0, "fourth"),
        ]
        with patch("local.music_surface_window.time.monotonic", return_value=101.0):
            self.window.render(
                output, QPoint(0, 0), QRegion(QRect(650, 0, 500, 666)),
            )

    def test_short_song_centers_album_after_note_cleanup_and_hides_lyric_effects(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        self.owner.track_title = "A sparse song"
        self.owner.music_intro_cleanup_enabled = True
        self.owner.timed_lyrics = [
            (0.0, "制作人：A"),
            (1.0, "Album: notes"),
            (6.0, "闪耀"),
            (12.0, "第二句"),
            (17.0, "第三句"),
        ]
        lines, timed = self.window._display_lyrics()
        self.assertEqual(lines, ["· · ·", "闪耀", "第二句", "第三句"])
        self.assertEqual(timed, [(0.0, "· · ·"), (6.0, "闪耀"), (12.0, "第二句"), (17.0, "第三句")])
        self.assertTrue(self.window._no_lyrics_cover_target())
        self.owner.media_position_seconds = 7.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        self.window.render(output)
        self.assertEqual(self.window._lyric_hit_rects, {})
        self.assertEqual(self.window._last_lyric_emphasis_mode, "none")

        self.owner.timed_lyrics.append((21.0, "第四句"))
        self.assertFalse(self.window._no_lyrics_cover_target())
        self.owner.timed_lyrics = []
        self.owner.lyrics = ["查找歌词中"]
        # A renewed lookup keeps the previously painted centered layout;
        # pending text must not force it back into the lyric columns.
        self.assertTrue(self.window._no_lyrics_cover_target())
        self.owner.lyrics = ["one", "two", "three"]
        self.assertTrue(self.window._no_lyrics_cover_target())
        self.owner.lyrics.append("four")
        self.assertFalse(self.window._no_lyrics_cover_target())

    def test_sparse_emphasis_uses_next_surviving_lyric_after_note_filter(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        self.owner.track_title = "Timed song"
        self.owner.timed_lyrics = [
            # Only the opening credit run is removable; an interior colon
            # sentence now retains its own source time.
            (0.0, "作曲：Someone"), (0.0, "光"),
            (4.0, "long next lyric line"),
            (8.0, "third lyric line"),
            (12.0, "fourth lyric line"),
        ]
        self.owner.media_position_seconds = 0.5
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        _lines, timed = self.window._display_lyrics()
        self.assertEqual(timed[1][0], 4.0)
        self.assertFalse(self.window._no_lyrics_cover_target())
        self.window._seek_to_lyric(1)
        self.assertEqual(self.owner.seek_calls, [4.0])
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        self.window.render(output)
        self.assertEqual(self.window._last_lyric_emphasis_mode, "scatter_glow")

    def test_edge_visual_rows_remain_in_spring_and_paint_range(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        self.owner.track_title = "Long lyrics"
        self.owner.timed_lyrics = [
            (float(index * 4), f"row {index}") for index in range(30)
        ]
        self.owner.media_position_seconds = 40.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window._browse_index = 10.0
        self.window._browse_until = float("inf")
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window.render(output)
        clip = self.window._lyric_clip_rect
        ranges = self.window._song_visual_ranges
        targets = tuple(self.window._logical_line_spring.targets)
        positions = tuple(self.window._logical_line_spring.positions)
        top_indices = [
            index for index, (start, end) in enumerate(ranges)
            if any(
                positions[visual] < clip.top()
                and positions[visual] + self.window._song_visual_heights[visual] > 0.0
                for visual in range(start, end)
            )
        ]
        bottom_indices = [
            index for index, (start, end) in enumerate(ranges)
            if any(
                clip.bottom() < positions[visual] < self.window.height()
                for visual in range(start, end)
            )
        ]
        self.assertTrue(top_indices)
        self.assertTrue(bottom_indices)
        edge_indices = top_indices + bottom_indices
        with (
            patch("local.music_surface_window.time.monotonic", return_value=100.1),
            patch.object(
                self.window, "_draw_sprung_cached_wrapped_layout",
                wraps=self.window._draw_sprung_cached_wrapped_layout,
            ) as draw,
        ):
            self.window.render(output)
        painted = {call.args[3] for call in draw.call_args_list}
        self.assertTrue(all(f"row {index}" in painted for index in edge_indices))
        self.assertGreaterEqual(self.window._logical_line_spring.visible_range[1],
                                max(ranges[index][1] for index in edge_indices))
        # Move the manual anchor enough for the bottom row to cross the
        # former lyric inset boundary; it must remain in both frames.
        self.window._browse_index = 10.08
        with (
            patch("local.music_surface_window.time.monotonic", return_value=100.116),
            patch.object(
                self.window, "_draw_sprung_cached_wrapped_layout",
                wraps=self.window._draw_sprung_cached_wrapped_layout,
            ) as next_draw,
        ):
            self.window.render(output)
        next_painted = {call.args[3] for call in next_draw.call_args_list}
        self.assertTrue(all(f"row {index}" in next_painted for index in edge_indices))

    def test_pause_click_changes_icon_state_immediately_then_reconciles(self):
        sender = Mock()
        self.owner._send_playback_command_async = sender
        self.owner.media_is_playing = True
        self.owner.media_track_id = "song-a"
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window._send_media_command("toggle_play_pause")
        sender.assert_called_once_with("toggle_play_pause")
        self.assertFalse(self.window._display_playing_state(100.1))
        self.assertEqual(self.window._cover_scale_to, 0.8)
        self.owner.media_is_playing = False
        self.assertFalse(self.window._display_playing_state(100.2))
        self.assertIsNone(self.window._optimistic_playing)
        self.owner.media_is_playing = True
        self.window._send_media_command("toggle_play_pause")
        self.assertTrue(self.window._display_playing_state(self.window._optimistic_playing_until + 0.01))

    def test_read_wipe_moves_left_to_right_without_vertical_strip_seams(self):
        source = QImage(300, 60, QImage.Format.Format_ARGB32_Premultiplied)
        source.setDevicePixelRatio(2.0)
        source.fill(QColor(255, 255, 255))
        previous = [0] * source.width()
        for progress in (0.0, 0.2, 0.5, 0.8, 1.0):
            masked = self.window._read_wipe_image(source, progress)
            self.assertEqual(masked.devicePixelRatio(), 2.0)
            alphas = [masked.pixelColor(x, 30).alpha() for x in range(source.width())]
            self.assertTrue(all(left >= right for left, right in zip(alphas, alphas[1:])))
            self.assertTrue(all(current >= old for old, current in zip(previous, alphas)))
            if 0.0 < progress < 1.0:
                # A single continuous mask has no one-pixel dark spike at
                # any of the old fractional strip boundaries.
                self.assertLessEqual(max(abs(a - b) for a, b in zip(alphas, alphas[1:])), 20)
                self.assertTrue(any(0 < value < 255 for value in alphas))
            previous = alphas
        self.assertTrue(all(value == 255 for value in previous))

    def test_read_wipe_has_one_wide_continuous_front_across_unit_boundary(self):
        source = QImage(60, 24, QImage.Format.Format_ARGB32_Premultiplied)
        source.fill(QColor(255, 255, 255))
        font = self.window._surface_font(48, QFont.Weight.Bold)
        front, feather = self.window._reading_front(0.35, 160.0, font)
        self.assertGreaterEqual(feather, 65.0)
        left = self.window._read_wipe_image_at_front(
            source, front=front, feather=feather, x_offset=0.0,
        )
        right = self.window._read_wipe_image_at_front(
            source, front=front, feather=feather, x_offset=60.0,
        )
        alphas = [left.pixelColor(x, 12).alpha() for x in range(60)]
        alphas += [right.pixelColor(x, 12).alpha() for x in range(60)]
        self.assertLessEqual(abs(alphas[59] - alphas[60]), 5)
        self.assertGreater(len({value // 12 for value in alphas if 0 < value < 255}), 8)
        self.assertTrue(all(a >= b for a, b in zip(alphas, alphas[1:])))

    def test_reading_gradient_is_bounded_by_script_and_zero_has_moving_hard_edge(self):
        font = self.window._surface_font(48, QFont.Weight.Bold)
        latin = self.window._character_span_feather("shine", font, 30)
        cjk = self.window._character_span_feather("你好你好", font, 30)
        mixed = self.window._character_span_feather("shine你好", font, 30)
        self.assertGreater(latin, 0.0)
        self.assertGreater(cjk, 0.0)
        self.assertGreaterEqual(mixed, min(latin, cjk))
        self.assertLessEqual(mixed, max(latin, cjk))
        self.assertAlmostEqual(self.window._character_span_feather("shine", font, 15), latin / 2.0)
        self.assertEqual(self.window._character_span_feather("shine", font, 0), 0.0)

        source = QImage(300, 20, QImage.Format.Format_ARGB32_Premultiplied)
        source.fill(QColor(255, 255, 255))
        self.owner.music_surface_reading_gradient_steps = 0
        early_front, early_feather = self.window._reading_front(0.25, 300.0, font, "shine")
        late_front, late_feather = self.window._reading_front(0.75, 300.0, font, "shine")
        self.assertEqual(early_feather, 0.0)
        self.assertEqual(late_feather, 0.0)
        self.assertLess(early_front, late_front)
        crisp = self.window._read_wipe_image_at_front(
            source, front=early_front, feather=early_feather, x_offset=0.0,
        )
        self.assertGreater(crisp.pixelColor(60, 10).alpha(), 245)
        self.assertLess(crisp.pixelColor(100, 10).alpha(), 10)
        self.owner.music_surface_reading_gradient_steps = 30
        soft_front, soft_feather = self.window._reading_front(0.25, 300.0, font, "shine")
        self.assertAlmostEqual(soft_feather, latin)
        self.assertAlmostEqual(soft_front - soft_feather, early_front)
        soft = self.window._read_wipe_image_at_front(
            source, front=soft_front, feather=soft_feather, x_offset=0.0,
        )
        intermediate = [soft.pixelColor(x, 10).alpha() for x in range(source.width())]
        self.assertGreater(intermediate[60], 245)
        self.assertGreater(intermediate[100], 0)
        self.assertLess(intermediate[100], 255)
        self.assertGreater(sum(0 < value < 255 for value in intermediate), 10)

    def test_gradient_only_adds_ahead_of_read_characters_in_latin_and_cjk(self):
        font = self.window._surface_font(48, QFont.Weight.Bold)
        for text, completed in (("shine", 3), ("你好你好吗", 3)):
            image, boundaries = self.window._glyph_run(text, font, "character")
            self.assertGreater(len(boundaries), completed + 1)
            progress = boundaries[completed] / image.width()
            before = max(0, int(boundaries[completed] - 3))
            ahead = int((boundaries[completed] + boundaries[completed + 1]) / 2)
            source = QImage(image.width(), 20, QImage.Format.Format_ARGB32_Premultiplied)
            source.fill(QColor(255, 255, 255))
            self.owner.music_surface_reading_gradient_steps = 0
            hard_front, hard_feather = self.window._reading_front(progress, image.width(), font, text)
            hard = self.window._read_wipe_image_at_front(
                source, front=hard_front, feather=hard_feather, x_offset=0.0,
            )
            self.owner.music_surface_reading_gradient_steps = 30
            soft_front, soft_feather = self.window._reading_front(progress, image.width(), font, text)
            soft = self.window._read_wipe_image_at_front(
                source, front=soft_front, feather=soft_feather, x_offset=0.0,
            )
            self.assertAlmostEqual(soft_front - soft_feather, hard_front)
            self.assertGreater(hard.pixelColor(before, 10).alpha(), 245)
            self.assertGreater(soft.pixelColor(before, 10).alpha(), 245)
            self.assertLess(hard.pixelColor(ahead, 10).alpha(), 10)
            self.assertGreater(soft.pixelColor(ahead, 10).alpha(), 0)

    def test_reading_gradient_slider_reuses_line_span_cache(self):
        font = self.window._surface_font(48, QFont.Weight.Bold)
        text = "shine again"
        self.window._reading_feather_cache.clear()
        for steps in range(31):
            self.owner.music_surface_reading_gradient_steps = steps
            self.window._reading_front(0.42, 500.0, font, text)
        # Slider changes only scale one cached maximum span; they must not
        # create one QFontMetrics entry for every slider stop.
        self.assertEqual(len(self.window._reading_feather_cache), 1)
        cached = next(iter(self.window._reading_feather_cache.values()))
        self.assertAlmostEqual(cached, self.window._character_span_feather(text, font, 30))

    def test_track_switch_shapes_long_song_in_bounded_batches_and_cancels_stale_build(self):
        font = self.window._surface_font(48, QFont.Weight.Bold)
        first = [f"first song row {index} wrapped lyric" for index in range(180)]
        second = [f"second song row {index} wrapped lyric" for index in range(180)]
        with patch.object(
            self.window, "_wrapped_lyric_layout",
            wraps=self.window._wrapped_lyric_layout,
        ) as shape:
            layouts, tops = self.window._song_lyric_layouts(first, font, 400.0, 18.0, defer=True)
            self.assertEqual((layouts, tops), ((), ()))
            first_count = len(self.window._song_layout_build["layouts"])
            self.assertGreater(first_count, 0)
            self.assertLessEqual(first_count, 12)
            self.assertEqual(shape.call_count, first_count)
            # A track replaced before the first build settles must never
            # publish mixed geometry from the previous song.
            self.window._song_lyric_layouts(second, font, 400.0, 18.0, defer=True)
            second_count = len(self.window._song_layout_build["layouts"])
            self.assertGreater(second_count, 0)
            self.assertLessEqual(second_count, 12)
            self.assertEqual(shape.call_count, first_count + second_count)
            self.assertEqual(self.window._song_layout_build["lines"], tuple(second))
            while self.window._song_layout_build is not None:
                self.window._advance_song_layout_build(max_rows=24)
            layouts, tops = self.window._song_lyric_layouts(second, font, 400.0, 18.0)
            self.assertEqual(len(layouts), 180)
            self.assertEqual(len(tops), 180)
            self.assertIsNone(self.window._song_layout_build)
            self.assertEqual(self.window._song_layout_key[0], tuple(second))

    def test_lifted_words_share_one_line_wide_reading_front(self):
        text = "shine bright tonight"
        font = self.window._surface_font(48, QFont.Weight.Bold)
        output = QImage(900, 120, QImage.Format.Format_ARGB32_Premultiplied)
        output.fill(Qt.GlobalColor.transparent)
        with QPainter(output) as painter, patch.object(
            self.window, "_read_wipe_image_at_front",
            wraps=self.window._read_wipe_image_at_front,
        ) as wipe:
            self.window._paint_lifted_run(
                painter, QPointF(10.0, 10.0), 90.0, text, font, 0.58,
                QColor(160, 65, 120, 220),
            )
        self.assertGreaterEqual(wipe.call_count, 2)
        fronts = {round(call.kwargs["front"], 5) for call in wipe.call_args_list}
        feathers = {round(call.kwargs["feather"], 5) for call in wipe.call_args_list}
        offsets = {round(call.kwargs["x_offset"], 5) for call in wipe.call_args_list}
        self.assertEqual(len(fronts), 1)
        self.assertEqual(len(feathers), 1)
        self.assertGreater(len(offsets), 1)

    def test_zero_progress_lift_and_sparse_runs_keep_the_unread_tint(self):
        font = self.window._surface_font(52, QFont.Weight.Bold)
        unread = QColor(168, 112, 74)
        background = QColor(57, 34, 18)
        for mode, text in (("reading", "on my side"), ("character", "shine")):
            for source, _left in self.window._isolated_glyph_units(text, font, mode):
                if mode == "character":
                    self.window._sparse_glyph_halo(source)
            for steps in (0, 15, 30):
                with self.subTest(mode=mode, steps=steps):
                    self.owner.music_surface_reading_gradient_steps = steps
                    output = QImage(500, 120, QImage.Format.Format_RGB32)
                    output.fill(background)
                    with QPainter(output) as painter:
                        paint = (
                            self.window._paint_lifted_run if mode == "reading"
                            else self.window._paint_sparse_glyph_run
                        )
                        paint(painter, QPointF(20.0, 10.0), 90.0, text, font, 0.0, unread)
                    colors = [output.pixelColor(x, y) for y in range(120) for x in range(500)]
                    self.assertGreater(max(color.red() for color in colors), background.red())
                    self.assertLessEqual(max(color.red() for color in colors), unread.red() + 1)
                    self.assertLessEqual(max(color.green() for color in colors), unread.green() + 1)
                    self.assertLessEqual(max(color.blue() for color in colors), unread.blue() + 1)

    def test_waiting_wrapped_row_stays_unread_until_its_front_advances(self):
        text = "I found a good boy, and he's on my side"
        font = self.window._surface_font(72, QFont.Weight.Bold)
        layout, height = self.window._wrapped_lyric_layout(text, font, 1030.0)
        runs = self.window._wrapped_lyric_runs(text, font, layout)
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[1][0], "on my side")
        unread = QColor(168, 112, 74)
        background = QColor(57, 34, 18)
        row = QRectF(20.0, 20.0, 1030.0, height)
        # Separate the rows' ink while preserving their read progress so the
        # pixel assertion cannot include the first row's white descenders.
        offsets = (0.0, 24.0)
        waiting_row = QRect(
            0, math.ceil(row.top() + runs[1][2] + offsets[1]), 1100, 100,
        )
        for steps in (0, 15, 30):
            with self.subTest(steps=steps):
                self.owner.music_surface_reading_gradient_steps = steps
                peaks = []
                for progress in (0.25, 0.85):
                    progresses = self.window._visual_line_progresses(progress, tuple(run[4] for run in runs))
                    self.assertEqual(progresses[1] == 0.0, progress == 0.25)
                    output = QImage(1100, 260, QImage.Format.Format_RGB32)
                    output.fill(background)
                    paint_run = self.window._paint_lifted_run

                    def paint_second(*args, **kwargs):
                        if args[3] == "on my side":
                            return paint_run(*args, **kwargs)

                    with QPainter(output) as painter, patch.object(
                        self.window, "_future_visual_line_colors", return_value=(unread, unread),
                    ), patch.object(self.window, "_paint_lifted_run", side_effect=paint_second):
                        self.window._paint_wrapped_lifted_lyric(
                            painter, row, text, font, layout, progress,
                            runs=runs, line_offsets=offsets,
                        )
                    second = output.copy(waiting_row)
                    peak = max(second.pixelColor(x, y).red()
                               for y in range(second.height()) for x in range(second.width()))
                    peaks.append(peak)
                    self.assertGreater(peak, background.red())
                self.assertLessEqual(peaks[0], unread.red() + 1)
                self.assertGreater(peaks[1], unread.red() + 40)

    def test_word_lift_overlaps_neighbor_while_color_front_still_reads_first_word(self):
        text = "shine again"
        font = self.window._surface_font(48, QFont.Weight.Bold)
        whole, boundaries = self.window._glyph_run(text, font)
        self.assertEqual(len(boundaries), 3)
        self.owner.music_surface_reading_gradient_steps = 30
        positions = []
        fronts = []
        for progress in (0.0, 0.125, 0.25, 0.5, 1.0):
            output = QImage(700, 140, QImage.Format.Format_ARGB32_Premultiplied)
            output.fill(Qt.GlobalColor.transparent)
            with QPainter(output) as painter, patch.object(
                self.window, "_draw_continuous_read_unit",
            ) as draw, patch.object(self.window, "_draw_sparse_static_run", return_value=False):
                self.window._paint_lifted_run(
                    painter, QPointF(10.0, 10.0), 90.0, text, font, progress,
                )
            positions.append(draw.call_args_list[1].args[2].y())
            # Every word sees the same timestamp-driven color boundary.
            fronts.append(draw.call_args_list[1].args[4] - draw.call_args_list[1].args[5])
            self.assertEqual(draw.call_args_list[0].args[4:6], draw.call_args_list[1].args[4:6])
            self.assertAlmostEqual(fronts[-1], progress * whole.width())
        self.assertLess(fronts[1], boundaries[1])
        self.assertLess(positions[1], positions[0])
        self.assertLess(positions[2], positions[1])
        # Once launched, the neighbor keeps rising rather than stopping at
        # every other interpolated staircase pose. Its color front above is
        # still the original source progress, not its vertical animation.
        self.assertLess(positions[3], positions[2])
        self.assertLess(positions[4], positions[3])
        self.assertAlmostEqual(
            positions[0] - positions[4],
            GLYPH_FLOAT_LIFT * 0.5 * max(0.5, font.pixelSize() / self.window.LYRIC_PIXEL_SIZE),
            delta=0.01,
        )

    def test_small_word_lift_has_visible_subpixel_frames_at_one_and_two_x_dpr(self):
        font = self.window._surface_font(38, QFont.Weight.Bold)
        self.owner.music_surface_reading_lift_level = 2
        for dpr in (1.0, 2.0):
            with self.subTest(dpr=dpr):
                frames = []
                for step in range(41):
                    output = QImage(
                        round(260 * dpr), round(110 * dpr),
                        QImage.Format.Format_ARGB32_Premultiplied,
                    )
                    output.setDevicePixelRatio(dpr)
                    output.fill(Qt.GlobalColor.transparent)
                    with QPainter(output) as painter, patch.object(
                        self.window, "_reading_front", return_value=(0.0, 0.0),
                    ):
                        self.window._paint_lifted_run(
                            painter, QPointF(10.0, 10.0), 80.0, "shine", font,
                            0.5 + step / 80.0, QColor(255, 255, 255),
                        )
                    frames.append(bytes(output.constBits()))

                # The default rise is only 2.6 logical pixels. An integer
                # 1:1 blit exposes a few images despite 41 timer paints.
                self.assertGreater(len(set(frames)), 24)

    def test_word_lift_keeps_horizontal_ink_centroid_at_fractional_x(self):
        font = self.window._surface_font(38, QFont.Weight.Bold)
        self.owner.music_surface_reading_lift_level = 2
        for dpr in (1.0, 2.0):
            centers = []
            for progress in (0.0, 0.75, 1.0):
                output = QImage(
                    round(260 * dpr), round(110 * dpr),
                    QImage.Format.Format_ARGB32_Premultiplied,
                )
                output.setDevicePixelRatio(dpr)
                output.fill(Qt.GlobalColor.transparent)
                with QPainter(output) as painter, patch.object(
                    self.window, "_reading_front", return_value=(0.0, 0.0),
                ):
                    self.window._paint_lifted_run(
                        painter, QPointF(10.35, 10.0), 80.0,
                        "shine", font, progress, QColor(255, 255, 255),
                    )
                samples = [
                    (x, output.pixelColor(x, y).alpha())
                    for y in range(output.height()) for x in range(output.width())
                    if output.pixelColor(x, y).alpha()
                ]
                centers.append(sum(x * alpha for x, alpha in samples) / sum(alpha for _x, alpha in samples))
            with self.subTest(dpr=dpr):
                self.assertLess(max(centers) - min(centers), 0.12 * dpr)

    def test_zero_lift_keeps_exact_size_image_blits(self):
        font = self.window._surface_font(38, QFont.Weight.Bold)
        self.owner.music_surface_reading_lift_level = 0
        output = QImage(260, 110, QImage.Format.Format_ARGB32_Premultiplied)
        output.fill(Qt.GlobalColor.transparent)
        with QPainter(output) as painter, patch.object(
            self.window, "_draw_subpixel_lyric_image",
            wraps=self.window._draw_subpixel_lyric_image,
        ) as subpixel:
            self.window._paint_lifted_run(
                painter, QPointF(10.0, 10.0), 80.0, "shine", font, 0.75,
            )
        subpixel.assert_not_called()

    def test_legacy_highlight_value_uses_the_only_lifted_reading_path(self):
        first = "one two three four five six"
        self.owner.timed_lyrics = [
            (0.0, first), (4.0, "second lyric"),
            (8.0, "third lyric"), (12.0, "fourth lyric"),
        ]
        self.owner.media_position_seconds = 1.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.owner.fullscreen_reading_effect = "highlight"
        self.window.resize(960, 540)
        frame = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch.object(
            self.window, "_paint_wrapped_lifted_lyric", wraps=self.window._paint_wrapped_lifted_lyric,
        ) as lifted:
            self.window.render(frame)
        self.assertTrue(any(call.args[2] == first for call in lifted.call_args_list))

    def test_sparse_glow_reads_each_character_before_its_background_light(self):
        text = "shine"
        font = self.window._surface_font(52, QFont.Weight.Bold)
        for source, _left in self.window._isolated_glyph_units(text, font, "character"):
            self.window._sparse_glyph_halo(source)
        for progress, expect_halo in ((0.01, False), (0.12, True), (0.5, True)):
            output = QImage(500, 120, QImage.Format.Format_ARGB32_Premultiplied)
            output.fill(Qt.GlobalColor.transparent)
            halo_draws = []
            original = self.window._draw_subpixel_lyric_image

            def capture(painter, origin, image):
                if painter.compositionMode() == QPainter.CompositionMode.CompositionMode_Screen:
                    halo_draws.append(image.cacheKey())
                return original(painter, origin, image)

            with QPainter(output) as painter, patch.object(
                self.window, "_draw_subpixel_lyric_image", side_effect=capture,
            ):
                self.window._paint_sparse_glyph_run(
                    painter, QPointF(10.0, 10.0), 90.0, text, font, progress,
                    QColor(160, 65, 120, 220),
                )
            with self.subTest(progress=progress):
                self.assertEqual(bool(halo_draws), expect_halo)

    def test_sparse_lyric_keeps_original_growth_wave_and_raised_landing(self):
        text = "shine"
        font = self.window._surface_font(52, QFont.Weight.Bold)
        for source, _left in self.window._isolated_glyph_units(text, font, "character"):
            self.window._sparse_glyph_halo(source)

        class MotionRecorder:
            def __init__(self, painter):
                self.painter = painter
                self.translations = []
                self.scales = []
                self.composition = QPainter.CompositionMode.CompositionMode_SourceOver
                self.current_opacity = 1.0
                self.halo_opacities = []

            def __getattr__(self, name):
                return getattr(self.painter, name)

            def translate(self, center):
                self.translations.append(center)
                self.painter.translate(center)

            def scale(self, x, y):
                self.scales.append((x, y))
                self.painter.scale(x, y)

            def setCompositionMode(self, mode):
                self.composition = mode
                self.painter.setCompositionMode(mode)

            def setOpacity(self, opacity):
                self.current_opacity = opacity
                self.painter.setOpacity(opacity)

            def drawImage(self, *args):
                if self.composition == QPainter.CompositionMode.CompositionMode_Screen:
                    self.halo_opacities.append(self.current_opacity)
                self.painter.drawImage(*args)

        expected = (
            (0, 0, 0, 0, 0), (1, 0, 0, 0, 0), (2, 1, 0, 0, 0),
            (2, 2, 1, 0, 0), (2, 2, 2, 1, 0), (1, 2, 2, 2, 1),
            (0, 1, 1, 1, 0), (0, 0, 0, 0, 0),
        )
        frames = []
        for step, row in enumerate(expected):
            output = QImage(500, 120, QImage.Format.Format_ARGB32_Premultiplied)
            output.fill(Qt.GlobalColor.transparent)
            with QPainter(output) as painter:
                recorder = MotionRecorder(painter)
                self.window._paint_sparse_glyph_run(
                    recorder, QPointF(10.0, 10.0), 90.0, text, font, step / 7.0,
                    QColor(160, 65, 120, 220),
                )
            self.assertEqual(len(recorder.scales), len(row))
            for (scale_x, scale_y), level in zip(recorder.scales, row):
                self.assertAlmostEqual(scale_x, 1.0 + 0.0625 * level)
                self.assertAlmostEqual(scale_y, scale_x)
            self.assertEqual(
                tuple(round(opacity * 2) for opacity in recorder.halo_opacities),
                tuple(level for level in row if level),
            )
            frames.append(recorder.translations)
        lift_scale = max(0.5, font.pixelSize() / self.window.LYRIC_PIXEL_SIZE)
        for glyph in range(len(text)):
            y_positions = [frame[glyph].y() for frame in frames]
            self.assertAlmostEqual(
                y_positions[0] - y_positions[-1], GLYPH_FLOAT_LIFT * 0.5 * lift_scale,
                delta=0.01,
            )
            self.assertTrue(all(a >= b for a, b in zip(y_positions, y_positions[1:])))

    def test_four_lift_levels_scale_ordinary_and_sparse_landing_only(self):
        font = self.window._surface_font(52, QFont.Weight.Bold)
        sparse_text = "shine"
        for source, _left in self.window._isolated_glyph_units(sparse_text, font, "character"):
            self.window._sparse_glyph_halo(source)

        class SparseMotionRecorder:
            def __init__(self, painter):
                self.painter = painter
                self.translations = []
                self.scales = []

            def __getattr__(self, name):
                return getattr(self.painter, name)

            def translate(self, center):
                self.translations.append(center)
                self.painter.translate(center)

            def scale(self, x, y):
                self.scales.append((x, y))
                self.painter.scale(x, y)

        def ordinary_y(progress):
            output = QImage(700, 140, QImage.Format.Format_ARGB32_Premultiplied)
            output.fill(Qt.GlobalColor.transparent)
            with QPainter(output) as painter, patch.object(
                self.window, "_draw_continuous_read_unit",
            ) as draw, patch.object(self.window, "_draw_sparse_static_run", return_value=False):
                self.window._paint_lifted_run(
                    painter, QPointF(10.0, 10.0), 90.0, "hello world", font, progress,
                )
            return draw.call_args_list[0].args[2].y()

        def sparse_frame(progress):
            output = QImage(700, 140, QImage.Format.Format_ARGB32_Premultiplied)
            output.fill(Qt.GlobalColor.transparent)
            with QPainter(output) as painter:
                recorder = SparseMotionRecorder(painter)
                self.window._paint_sparse_glyph_run(
                    recorder, QPointF(10.0, 10.0), 90.0, sparse_text, font,
                    progress, QColor(160, 65, 120, 220),
                )
            return recorder.translations[0].y(), tuple(recorder.scales)

        lift_scale = max(0.5, font.pixelSize() / self.window.LYRIC_PIXEL_SIZE)
        for level in (0, 1, 2, 4):
            with self.subTest(level=level):
                self.owner.music_surface_reading_lift_level = level
                target = GLYPH_FLOAT_LIFT * level / 4.0 * lift_scale
                self.assertAlmostEqual(ordinary_y(0.0) - ordinary_y(1.0), target)
                sparse_start, _ = sparse_frame(0.0)
                sparse_end, _ = sparse_frame(1.0)
                self.assertAlmostEqual(sparse_start - sparse_end, target)
                _peak_y, peak_scales = sparse_frame(3.0 / 7.0)
                self.assertAlmostEqual(peak_scales[0][0], 1.125)
                self.assertTrue(all(abs(x - y) < 0.001 for x, y in peak_scales))

    def test_read_wipe_starts_at_text_ink_not_raster_padding(self):
        source = QImage(300, 60, QImage.Format.Format_ARGB32_Premultiplied)
        source.setDevicePixelRatio(2.0)
        source.fill(QColor(255, 255, 255))
        masked = self.window._read_wipe_image(source, 0.5, start=30.0, width=80.0)
        self.assertEqual(masked.pixelColor(55, 30).alpha(), 0)
        self.assertGreater(masked.pixelColor(65, 30).alpha(), 245)
        self.assertEqual(masked.pixelColor(225, 30).alpha(), 0)
        self.assertEqual(self.window._read_wipe_image(source, 0.999, start=30.0, width=80.0).pixelColor(225, 30).alpha(), 255)

    def test_long_read_wipe_preserves_completed_pixels_and_finishes_without_dark_edge(self):
        source = QImage(1000, 24, QImage.Format.Format_ARGB32_Premultiplied)
        source.fill(QColor(255, 255, 255))
        opening = self.window._read_wipe_image(source, 0.005)
        self.assertEqual(opening.pixelColor(0, 12).alpha(), 255)
        self.assertTrue(0 < opening.pixelColor(80, 12).alpha() < 255)
        self.assertEqual(opening.pixelColor(200, 12).alpha(), 0)

        near_end = self.window._read_wipe_image(source, 0.999)
        self.assertGreater(near_end.pixelColor(999, 12).alpha(), 220)
        self.assertEqual(self.window._read_wipe_image(source, 1.0).pixelColor(999, 12).alpha(), 255)
        frame = QImage(source.size(), QImage.Format.Format_RGB32)
        frame.fill(QColor(225, 225, 135))
        with QPainter(frame) as painter:
            painter.drawImage(0, 0, near_end)
        self.assertGreaterEqual(frame.pixelColor(999, 12).red(), 225)

    def test_glyph_read_wipe_draws_shape_once_with_smooth_fractional_front(self):
        source = QImage(300, 60, QImage.Format.Format_ARGB32_Premultiplied)
        source.setDevicePixelRatio(2.0)
        source.fill(QColor(255, 255, 255))
        output = QImage(360, 100, QImage.Format.Format_ARGB32_Premultiplied)
        output.fill(QColor(80, 60, 50))
        with QPainter(output) as painter:
            self.window._draw_glyph_read_wipe(
                painter, source, QPointF(20.25, 10.5), 0.5, 1.0,
            )
        reds = [output.pixelColor(x, 30).red() for x in range(20, 172)]
        self.assertTrue(all(left >= right for left, right in zip(reds, reds[1:])))
        self.assertLessEqual(max(abs(a - b) for a, b in zip(reds, reds[1:])), 20)

    def test_long_english_read_wipe_stays_inside_its_shaped_glyphs(self):
        text = "Can you even imagine falling"
        font = self.window._surface_font(60, QFont.Weight.Bold)
        origin = QPointF(8, 10)
        line_height = 100.0
        progress = 0.28  # Formerly repainted "Can" across "you".
        whole, _boundaries = self.window._glyph_run(text, font)
        units = self.window._isolated_glyph_units(text, font)
        lift_scale = max(0.5, font.pixelSize() / self.window.LYRIC_PIXEL_SIZE)
        target_lift = GLYPH_FLOAT_LIFT * 0.5
        top = origin.y() + (line_height - whole.height()) / 2.0 + target_lift * lift_scale
        allowed = QImage(900, 140, QImage.Format.Format_ARGB32_Premultiplied)
        allowed.fill(Qt.GlobalColor.transparent)
        with QPainter(allowed) as painter:
            for index, (source, left) in enumerate(units):
                lift = target_lift * continuous_read_lift(progress, index, len(units))
                painter.drawImage(QPointF(origin.x() + left, top - lift * lift_scale), source)
        actual = QImage(900, 140, QImage.Format.Format_ARGB32_Premultiplied)
        actual.fill(Qt.GlobalColor.transparent)
        with QPainter(actual) as painter:
            self.window._paint_lifted_run(
                painter, origin, line_height, text, font, progress,
                QColor(235, 237, 241, 220),
            )
        # Subpixel sampling can put antialiased ink one physical pixel past
        # the old integer-blit silhouette; a whole borrowed neighbor glyph
        # would extend much farther than that.
        outside_ink = sum(
            1
            for y in range(actual.height()) for x in range(actual.width())
            if actual.pixelColor(x, y).alpha() > 8
            and allowed.pixelColor(x, y).alpha() == 0
            and not any(
                allowed.pixelColor(nx, ny).alpha() > 0
                for ny in range(max(0, y - 1), min(allowed.height(), y + 2))
                for nx in range(max(0, x - 1), min(allowed.width(), x + 2))
            )
        )
        self.assertEqual(outside_ink, 0)

    def test_window_uses_normal_stacking_without_topmost_flag(self):
        flags = self.window.windowFlags()
        self.assertEqual(flags & Qt.WindowType.WindowType_Mask, Qt.WindowType.Window)
        self.assertFalse(flags & Qt.WindowType.WindowStaysOnTopHint)
        self.assertFalse(self.window._timer.isActive())

    def test_island_sparse_threshold_setter_clamps_and_persists_snapshot(self):
        island = IslandStackVisual()
        try:
            self.assertEqual(island.music_sparse_highlight_seconds_per_unit, 1.5)
            island.set_music_sparse_highlight_seconds_per_unit(0.5, notify=False)
            self.assertEqual(island.settings_snapshot()["v2_music_sparse_highlight_seconds_per_unit"], 0.8)
            island.set_music_sparse_highlight_seconds_per_unit(3.0, notify=False)
            self.assertEqual(island.settings_snapshot()["v2_music_sparse_highlight_seconds_per_unit"], 2.0)
            island.set_music_sparse_highlight_seconds_per_unit(float("nan"), notify=False)
            self.assertEqual(island.settings_snapshot()["v2_music_sparse_highlight_seconds_per_unit"], 1.5)
        finally:
            island.close()

    def test_island_reading_gradient_setter_clamps_and_persists_snapshot(self):
        island = IslandStackVisual()
        try:
            self.assertEqual(island.music_surface_reading_gradient_steps, 30)
            island.set_music_surface_reading_gradient_steps(-8, notify=False)
            self.assertEqual(island.settings_snapshot()["v2_music_surface_reading_gradient_steps"], 0)
            island.set_music_surface_reading_gradient_steps(90, notify=False)
            self.assertEqual(island.settings_snapshot()["v2_music_surface_reading_gradient_steps"], 30)
            island.set_music_surface_reading_gradient_steps(float("nan"), notify=False)
            self.assertEqual(island.settings_snapshot()["v2_music_surface_reading_gradient_steps"], 30)
        finally:
            island.close()

    def test_music_surface_lift_setter_persists_four_levels_without_old_effect(self):
        island = IslandStackVisual()
        try:
            self.assertEqual(island.music_surface_reading_lift_level, 2)
            self.assertNotIn("v2_fullscreen_reading_effect", island.settings_snapshot())
            for level in (0, 1, 2, 4):
                with self.subTest(level=level):
                    island.set_music_surface_reading_lift_level(level, notify=False)
                    self.assertEqual(island.settings_snapshot()["v2_music_surface_reading_lift_level"], level)
            island.set_music_surface_reading_lift_level(3, notify=False)
            self.assertEqual(island.settings_snapshot()["v2_music_surface_reading_lift_level"], 2)
        finally:
            island.close()

    def test_windowed_surface_is_the_only_visible_product_top_level(self):
        island = IslandStackVisual()
        try:
            island._music_windowed_preference = True
            island.show()
            self.app.processEvents()
            island._show_music_fullscreen()
            surface = island.music_fullscreen_overlay
            surface._mode_animation.stop()
            surface._finish_mode_transition()
            self.app.processEvents()

            visible_product_windows = [
                widget
                for widget in self.app.topLevelWidgets()
                if widget in {island, surface} and widget.isVisible()
            ]
            self.assertEqual(visible_product_windows, [surface])
            self.assertFalse(island.isVisible())
            self.assertTrue(surface.isWindow())
            self.assertIsNone(surface.parentWidget())
            self.assertFalse(surface.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

            # Even an explicit attempt to show the old top-level island is
            # rejected while the single music surface owns the lifecycle.
            island.show()
            self.app.processEvents()
            self.assertFalse(island.isVisible())
            self.assertTrue(surface.isVisible())
        finally:
            island.close()

    def test_island_entry_crops_transition_origin_to_visible_main_island(self):
        island = IslandStackVisual()
        try:
            island._music_windowed_preference = True
            island.show()
            self.app.processEvents()
            island.move(137, 91)

            main_global = island._current_main_global_rect().toAlignedRect()
            local_rect = QRect(
                island.mapFromGlobal(main_global.topLeft()),
                main_global.size(),
            ).intersected(island.rect())
            expected_global = QRect(
                island.mapToGlobal(local_rect.topLeft()),
                local_rect.size(),
            )
            self.assertTrue(expected_global.isValid())
            self.assertNotEqual(expected_global.size(), island.size())
            self.assertNotEqual(expected_global.topLeft(), local_rect.topLeft())

            island._show_music_fullscreen()
            surface = island.music_fullscreen_overlay
            surface._mode_animation.stop()

            self.assertEqual(surface._island_origin_geometry, expected_global)
            snapshot = surface._island_origin_snapshot
            self.assertFalse(snapshot.isNull())
            logical_snapshot_size = QSize(
                round(snapshot.width() / snapshot.devicePixelRatio()),
                round(snapshot.height() / snapshot.devicePixelRatio()),
            )
            self.assertEqual(logical_snapshot_size, expected_global.size())
        finally:
            island.close()

    def test_active_music_surface_blocks_native_island_topmost_reinforcement(self):
        island = IslandStackVisual()
        user32 = SimpleNamespace(SetWindowPos=Mock())
        try:
            island._music_surface_active = True
            island.music_fullscreen_overlay = SimpleNamespace(isVisible=lambda: False)
            island.topmost_reinforced_at = 0.0
            with (
                patch("local.island_stack_visual.sys.platform", "win32"),
                patch.object(__import__("ctypes"), "windll", SimpleNamespace(user32=user32), create=True),
                patch.object(island, "winId", return_value=123),
            ):
                island._force_super_topmost()
            user32.SetWindowPos.assert_not_called()
            self.assertEqual(island.topmost_reinforced_at, 0.0)
        finally:
            island._music_surface_active = False
            island.music_fullscreen_overlay = None
            island.close()

    def test_animation_clock_runs_only_while_surface_is_visible(self):
        self.window.show_from_island(fullscreen=False)
        self.app.processEvents()
        self.assertTrue(self.window._timer.isActive())
        self.window.hide()
        self.app.processEvents()
        self.assertFalse(self.window._timer.isActive())

    def test_priority_glow_requests_local_high_refresh_only_with_budget(self):
        self.owner.media_track_id = "song"
        self.owner.media_is_playing = True
        self.window._track_visual = ("song",)
        self.window._last_lyric_emphasis_mode = "character_lift"
        self.window._active_glow_stage = "reveal"
        self.window._lyric_animation_rect = QRect(200, 100, 400, 300)
        self.window._last_full_animation_update_at = 100.0
        self.window._recent_paint_costs.extend([2.0] * 20)
        with (
            patch.object(self.window, "screen", return_value=SimpleNamespace(refreshRate=lambda: 240.0)),
            patch("local.music_surface_window.time.monotonic", return_value=100.05),
            patch.object(self.window, "update") as update,
        ):
            self.window._on_animation_tick()
        self.assertEqual(self.window._timer.interval(), self.window.EMPHASIS_FRAME_MS)
        update.assert_called_once_with(self.window._lyric_animation_rect)

        self.window._recent_paint_costs.clear()
        self.window._recent_paint_costs.extend([11.0] * 20)
        with (
            patch.object(self.window, "screen", return_value=SimpleNamespace(refreshRate=lambda: 240.0)),
            patch("local.music_surface_window.time.monotonic", return_value=100.15),
            patch.object(self.window, "update") as update,
        ):
            self.window._on_animation_tick()
        self.assertEqual(self.window._timer.interval(), self.window.ANIMATION_FRAME_MS)
        update.assert_called_once_with()

    def test_ordinary_lyric_uses_display_matched_cadence_when_budget_allows(self):
        self.owner.media_track_id = "song"
        self.owner.media_is_playing = True
        self.window._track_visual = ("song",)
        self.window._has_timed_lyrics = True
        self.window._lyric_animation_rect = QRect(500, 0, 1000, 800)
        self.window._last_full_animation_update_at = 100.0
        self.window._recent_paint_costs.extend([2.0] * 20)
        with (
            patch.object(self.window, "screen", return_value=SimpleNamespace(refreshRate=lambda: 144.0)),
            patch("local.music_surface_window.time.monotonic", return_value=100.05),
            patch.object(self.window, "update") as update,
        ):
            self.window._on_animation_tick()
        self.assertEqual(self.window._timer.interval(), 7)
        update.assert_called_once_with(self.window._lyric_animation_rect)

    def test_ordinary_timed_reading_uses_local_dirty_region_between_ambient_frames(self):
        self.owner.media_track_id = "song"
        self.owner.media_is_playing = True
        self.window._track_visual = ("song",)
        self.window._has_timed_lyrics = True
        self.window._last_lyric_emphasis_mode = "none"
        self.window._lyric_animation_rect = QRect(500, 0, 1000, 800)
        self.window._active_lyric_animation_rect = QRect(800, 300, 480, 180)
        self.window._lyric_spring_active = False
        self.window._last_full_animation_update_at = 100.0
        with (
            patch("local.music_surface_window.time.monotonic", return_value=100.05),
            patch.object(self.window, "update") as update,
        ):
            self.window._on_animation_tick()
        update.assert_called_once_with(self.window._active_lyric_animation_rect)
        with (
            patch("local.music_surface_window.time.monotonic", return_value=100.13),
            patch.object(self.window, "update") as update,
        ):
            self.window._on_animation_tick()
        update.assert_called_once_with()

    def test_paused_timed_lyrics_do_not_repaint_on_every_timer_tick(self):
        self.owner.media_track_id = "song"
        self.owner.media_is_playing = False
        self.window._track_visual = ("song",)
        self.window._has_timed_lyrics = True
        self.window._lyric_spring_active = False
        self.window._last_full_animation_update_at = 100.0
        with (
            patch("local.music_surface_window.time.monotonic", return_value=100.05),
            patch.object(self.window, "update") as update,
        ):
            self.window._on_animation_tick()
        update.assert_not_called()
        with (
            patch("local.music_surface_window.time.monotonic", return_value=100.13),
            patch.object(self.window, "update") as update,
        ):
            self.window._on_animation_tick()
        update.assert_called_once_with()

    def test_paint_budget_samples_reset_after_track_or_geometry_change(self):
        event = SimpleNamespace(region=lambda: SimpleNamespace(
            boundingRect=lambda: QRect(0, 0, 100, 100),
        ))
        self.window._active_lyric_animation_rect = QRect(0, 0, 100, 100)
        with patch("local.music_surface_window.time.perf_counter", return_value=1.01):
            self.window._record_paint_cost(1.0, event)
        self.assertEqual(len(self.window._recent_paint_costs), 1)
        self.assertEqual(len(self.window._recent_priority_paint_costs), 1)
        self.window._recent_paint_costs.extend([2.0] * 20)
        self.window._recent_priority_paint_costs.extend([2.0] * 20)
        self.owner.media_track_id = "another-song"
        with patch("local.music_surface_window.time.perf_counter", return_value=2.01):
            self.window._record_paint_cost(2.0, event)
        self.assertEqual(len(self.window._recent_paint_costs), 1)
        self.assertEqual(len(self.window._recent_priority_paint_costs), 1)
        self.window.resize(1190, 666)
        with patch("local.music_surface_window.time.perf_counter", return_value=3.01):
            self.window._record_paint_cost(3.0, event)
        self.assertEqual(len(self.window._recent_paint_costs), 1)

    def test_settled_priority_lyric_uses_tight_active_row_dirty_region(self):
        self.owner.media_track_id = "song"
        self.owner.media_is_playing = True
        self.window._track_visual = ("song",)
        self.window._last_lyric_emphasis_mode = "character_lift"
        self.window._active_glow_stage = "hold"
        self.window._lyric_animation_rect = QRect(600, 0, 1320, 1080)
        self.window._active_lyric_animation_rect = QRect(780, 320, 720, 300)
        self.window._lyric_spring_active = False
        self.window._last_full_animation_update_at = 100.0
        with (
            patch("local.music_surface_window.time.monotonic", return_value=100.05),
            patch.object(self.window, "update") as update,
        ):
            self.window._on_animation_tick()
        update.assert_called_once_with(self.window._active_lyric_animation_rect)

        self.window._lyric_spring_active = True
        with (
            patch("local.music_surface_window.time.monotonic", return_value=100.06),
            patch.object(self.window, "update") as update,
        ):
            self.window._on_animation_tick()
        update.assert_called_once_with(self.window._lyric_animation_rect)

        self.window._lyric_spring_active = False
        self.window._active_glow_stage = "fade"
        with (
            patch("local.music_surface_window.time.monotonic", return_value=100.07),
            patch.object(self.window, "update") as update,
        ):
            self.window._on_animation_tick()
        update.assert_called_once_with(self.window._active_lyric_animation_rect)

    def test_settled_sparse_animation_changes_only_inside_active_dirty_region(self):
        self.owner.media_track_id = "song"
        self.owner.timed_lyrics = [(0.0, "shine"), (5.0, "ordinary next line")]
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(960, 540)
        self.owner.media_position_seconds = 1.0
        warm = QImage(self.window.size(), QImage.Format.Format_RGB32)
        warm.fill(Qt.GlobalColor.black)
        with patch("local.music_surface_window.time.monotonic", return_value=20.0):
            self.window.render(warm)
            self._finish_glow_prewarm()
            self.window.render(warm)
        first = QImage(warm)
        first_rect = QRect(self.window._active_lyric_animation_rect)
        self.assertFalse(self.window._lyric_spring_active)

        self.owner.media_position_seconds = 1.1
        second = QImage(self.window.size(), QImage.Format.Format_RGB32)
        second.fill(Qt.GlobalColor.black)
        with patch("local.music_surface_window.time.monotonic", return_value=20.10):
            self.window.render(second)
        dirty = first_rect.united(self.window._active_lyric_animation_rect)
        left, top, right, bottom = self.window.width(), self.window.height(), -1, -1
        first_bytes = first.bits().tobytes()
        second_bytes = second.bits().tobytes()
        for byte_index in range(0, min(len(first_bytes), len(second_bytes)), 4):
            if first_bytes[byte_index:byte_index + 4] == second_bytes[byte_index:byte_index + 4]:
                continue
            pixel_index = byte_index // 4
            x, y = pixel_index % self.window.width(), pixel_index // self.window.width()
            left, top, right, bottom = min(left, x), min(top, y), max(right, x), max(bottom, y)
        changed = QRect(left, top, right - left + 1, bottom - top + 1)
        self.assertTrue(changed.isValid())
        self.assertTrue(dirty.contains(changed), (dirty, changed))

    def test_return_to_island_remembers_runtime_window_geometry(self):
        self.window.show_from_island(fullscreen=False)
        self.window.setGeometry(140, 160, 960, 540)
        self.window.return_to_island()
        self.assertEqual(self.window._normal_geometry, QRect(140, 160, 960, 540))
        self.window.show_from_island(fullscreen=False)
        self.assertEqual(self.window.geometry(), QRect(140, 160, 960, 540))

    def test_return_button_contract_restores_owner(self):
        self.window.return_to_island()
        self.assertTrue(self.owner.restored)

    def test_windowed_preference_is_remembered(self):
        self.window._fullscreen = True
        self.window._normal_geometry = self.window._default_window_geometry()
        self.window.toggle_presentation_mode()
        self.assertEqual(self.window.presentation_mode, "windowed")
        self.assertTrue(self.owner._music_windowed_preference)

    def test_window_to_fullscreen_uses_geometry_transition_before_fullscreen(self):
        self.window._fullscreen = False
        self.window.setGeometry(self.window._default_window_geometry())
        self.window.toggle_presentation_mode()
        self.assertTrue(self.window._mode_animation.state())
        self.assertTrue(self.window._pending_fullscreen)
        self.assertFalse(self.window._fullscreen)
        self.window._mode_animation.stop()
        self.window._finish_mode_transition()
        self.assertTrue(self.window._fullscreen)
        self.assertEqual(self.window.presentation_mode, "fullscreen")

    def test_small_screen_default_stays_inside_available_bounds(self):
        self.window._available_geometry = lambda: QRect(100, 50, 800, 600)
        default = self.window._default_window_geometry()
        self.assertLessEqual(default.width(), 800)
        self.assertLessEqual(default.height(), 600)
        self.assertAlmostEqual(default.width() / default.height(), MUSIC_WINDOW_ASPECT_RATIO, delta=0.01)
        self.assertTrue(QRect(100, 50, 800, 600).contains(default))

    def test_all_edges_and_corners_resize_with_locked_ratio(self):
        self.window._available_geometry = lambda: QRect(0, 0, 3840, 2160)
        self.window._minimum_window_size = lambda: QSize(320, 180)
        original = QRect(400, 300, 960, 540)
        cases = (
            ("left", QPointF(0, 270), QPointF(-160, 0), {"left"}, QRect(240, 300, 1120, 630)),
            ("right", QPointF(959, 270), QPointF(160, 0), {"right"}, QRect(400, 300, 1120, 630)),
            ("top", QPointF(480, 0), QPointF(0, -90), {"top"}, QRect(400, 210, 1120, 630)),
            ("bottom", QPointF(480, 539), QPointF(0, 90), {"bottom"}, QRect(400, 300, 1120, 630)),
            ("top-left", QPointF(0, 0), QPointF(-160, 0), {"left", "top"}, QRect(240, 210, 1120, 630)),
            ("top-right", QPointF(959, 0), QPointF(0, -90), {"right", "top"}, QRect(400, 210, 1120, 630)),
            ("bottom-left", QPointF(0, 539), QPointF(0, 90), {"left", "bottom"}, QRect(240, 300, 1120, 630)),
            ("bottom-right", QPointF(959, 539), QPointF(160, 0), {"right", "bottom"}, QRect(400, 300, 1120, 630)),
        )
        for label, local_point, delta, expected_edges, expected_geometry in cases:
            with self.subTest(edge=label):
                self.window.setGeometry(original)
                press_global = QPointF(original.x() + local_point.x(), original.y() + local_point.y())
                self.assertEqual(self.window._edge_set(local_point), expected_edges)
                self.window.mousePressEvent(_PointerEvent(local_point, global_point=press_global))
                self.window.mouseMoveEvent(
                    _PointerEvent(
                        local_point + delta,
                        global_point=press_global + delta,
                        buttons=Qt.MouseButton.LeftButton,
                    )
                )
                self.assertEqual(self.window.geometry(), expected_geometry)
                self.assertAlmostEqual(
                    self.window.width() / self.window.height(),
                    MUSIC_WINDOW_ASPECT_RATIO,
                    delta=0.01,
                )

    def test_timed_lyrics_advance_in_rendered_music_surface(self):
        self.owner.timed_lyrics = [(0.0, "FIRST LINE"), (2.0, "SECOND LINE"), (4.0, "THIRD LINE")]
        self.owner.media_track_id = "test-track"
        self.owner.media_position_seconds = 0.3
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(960, 540)
        first = QImage(self.window.size(), QImage.Format.Format_RGB32)
        self.window.render(first)
        self.owner.media_position_seconds = 2.6
        for tick in range(25):
            self.window._lyric_frame(self.owner.timed_lyrics, 100.0 + tick / 60.0)
        second = QImage(self.window.size(), QImage.Format.Format_RGB32)
        self.window.render(second)
        self.assertNotEqual(first.bits().tobytes(), second.bits().tobytes())
        self.assertEqual(self.window._lyric_motion.frame(self.owner.timed_lyrics, 2.6, 101.0, "test-track").active_index, 1)

    def test_apple_lift_renders_and_reuses_shaped_glyphs(self):
        self.owner.timed_lyrics = [(0.0, "Hello 世界 👩‍💻"), (3.0, "next")]
        self.owner.media_position_seconds = 1.5
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(960, 540)
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        self.window.render(image)
        cached = self.window._glyph_cache
        self.assertIsNotNone(cached)
        # Latin is lifted by word; CJK remains one unit per grapheme and the
        # emoji remains an intact shaped unit.
        self.assertEqual(len(cached[1]) - 1, 4)
        self.window.render(image)
        self.assertIs(cached, self.window._glyph_cache)

    def test_sparse_line_effect_overrides_ordinary_lifted_reading(self):
        self.owner.timed_lyrics = [(0.0, "别走"), (5.0, "普通下一句")]
        self.owner.media_position_seconds = 2.5
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(960, 540)
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        self.window.render(image)
        self.assertEqual(self.window._last_lyric_emphasis_mode, "scatter_glow")

    def test_sparse_threshold_reconfigures_active_effect_without_restart(self):
        self.owner.timed_lyrics = [(0.0, "别再走"), (4.5, "普通下一句")]
        self.owner.media_position_seconds = 2.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(960, 540)
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        self.owner.music_sparse_highlight_seconds_per_unit = 1.0
        self.window.render(image)
        self.assertEqual(self.window._last_lyric_emphasis_mode, "scatter_glow")
        self.owner.music_sparse_highlight_seconds_per_unit = 2.0
        self.window.render(image)
        self.assertEqual(self.window._last_lyric_emphasis_mode, "none")

    def test_next_line_prewarm_uses_current_sparse_threshold(self):
        self.owner.timed_lyrics = [
            (0.0, "ordinary line with many spoken words"),
            (4.0, "别再走"),
            (8.5, "ordinary next line"),
        ]
        self.owner.media_position_seconds = 2.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(960, 540)
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        self.owner.music_sparse_highlight_seconds_per_unit = 2.0
        self.window.render(image)
        self.assertIsNone(self.window._pending_glow_prewarm)
        self.owner.music_sparse_highlight_seconds_per_unit = 1.0
        self.window.render(image)
        self.assertEqual(self.window._pending_glow_prewarm[1], "别再走")

    def test_sparse_alphabetic_line_forces_character_lift(self):
        self.owner.timed_lyrics = [(0.0, "Stay"), (4.5, "next line")]
        self.owner.media_position_seconds = 2.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(960, 540)
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        self.window.render(image)
        self.assertEqual(self.window._last_lyric_emphasis_mode, "character_lift")
        self.window._on_animation_tick()
        self.window.render(image)
        self.assertEqual(self.window._glyph_cache_key[2], "character")

    def test_sparse_english_word_keeps_character_lift_and_adds_cached_white_halo(self):
        self.owner.timed_lyrics = [(0.0, "shine"), (5.0, "next line")]
        self.owner.media_position_seconds = 1.5
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(960, 540)
        frame = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch.object(self.window, "_lyric_glow_sprite", wraps=self.window._lyric_glow_sprite) as sprite:
            self.window.render(frame)
            self.assertEqual(self.window._last_lyric_emphasis_mode, "character_lift")
            sprite.assert_not_called()
        self.assertFalse(self.window._lyric_glow_cache)
        self._finish_glow_prewarm()
        with patch.object(self.window, "_paint_sparse_glyph_run", wraps=self.window._paint_sparse_glyph_run) as glyphs:
            self.window.render(frame)
            self.assertEqual(self.window._glyph_cache_key[2], "character")
            self.assertEqual(glyphs.call_count, 1)
        sprites = tuple(value[0].cacheKey() for value in self.window._sparse_glyph_halo_cache.values())
        self.assertTrue(sprites)
        with patch.object(self.window, "_sparse_glyph_halo", wraps=self.window._sparse_glyph_halo) as sprite:
            self.owner.media_position_seconds = 2.5
            self.window.render(frame)
            sprite.assert_not_called()
        self.assertEqual(sprites, tuple(value[0].cacheKey() for value in self.window._sparse_glyph_halo_cache.values()))

    def test_next_sparse_line_scatter_is_prepared_before_it_becomes_active(self):
        self.owner.timed_lyrics = [
            (0.0, "ordinary line with many more than five spoken words here"),
            (4.0, "shine"),
            (9.0, "ordinary next line"),
        ]
        self.owner.media_position_seconds = 2.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(960, 540)
        frame = QImage(self.window.size(), QImage.Format.Format_RGB32)
        self.window.render(frame)
        pending = self.window._pending_glow_prewarm
        self.assertIsNotNone(pending)
        self.assertEqual(pending[1], "shine")
        self._finish_glow_prewarm()
        self.assertTrue(self.window._sparse_glyph_halo_cache)
        layout = self.window._song_layouts[1][0]
        lyric_font = self.window._surface_font(
            self.window.LYRIC_PIXEL_SIZE * self.window._surface_scale(), QFont.Weight.Bold,
        )
        self.assertTrue(self.window._lyric_emphasis_assets_ready(
            "shine", lyric_font, float(self.window._song_layout_key[2]), layout, "character_lift",
        ))
        cached_keys = tuple(value[0].cacheKey() for value in self.window._sparse_glyph_halo_cache.values())
        self.owner.media_position_seconds = 5.5
        self.window.render(frame)
        self.assertEqual(cached_keys, tuple(value[0].cacheKey() for value in self.window._sparse_glyph_halo_cache.values()))

    def test_cold_wrapped_sparse_lift_paint_does_not_build_glyph_or_glow_rasters(self):
        self.owner.timed_lyrics = [(0.0, "extraordinary illumination"), (15.0, "next")]
        self.owner.media_position_seconds = 2.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(640, 360)
        frame = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with (
            patch.object(self.window, "_glyph_run", wraps=self.window._glyph_run) as glyph,
            patch.object(self.window, "_lyric_glow_sprite", wraps=self.window._lyric_glow_sprite) as glow,
        ):
            self.window.render(frame)
            glyph.assert_not_called()
            glow.assert_not_called()
        self.assertIsNotNone(self.window._pending_glow_prewarm)
        self.window._on_animation_tick()
        self.assertTrue(self.window._sparse_glyph_halo_cache)
        self.assertTrue(self.window._glyph_run_cache)
        self.assertTrue(self.window._glyph_tint_cache)
        while self.window._pending_glow_prewarm is not None:
            self.window._on_animation_tick()
        tint_keys = tuple(self.window._glyph_tint_cache)
        self.window.render(frame)
        self.assertEqual(tuple(self.window._glyph_tint_cache), tint_keys)

    def test_sparse_prewarm_shapes_at_most_one_unit_per_animation_tick(self):
        text = "extraordinary illumination"
        self.owner.timed_lyrics = [(0.0, text), (15.0, "next line")]
        self.owner.media_position_seconds = 2.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(1920, 1080)
        self.window.render(QImage(self.window.size(), QImage.Format.Format_RGB32))
        self.assertIsNotNone(self.window._pending_glow_prewarm)
        with patch.object(
            self.window, "_advance_isolated_glyph_units",
            wraps=self.window._advance_isolated_glyph_units,
        ) as advance:
            self.window._on_animation_tick()
        self.assertEqual(advance.call_args.kwargs["max_units"], 1)
        self.assertIsNotNone(self.window._pending_glow_prewarm)
        partial = tuple(self.window._isolated_glyph_builds.values())
        self.assertEqual(len(partial), 1)
        self.assertEqual(len(partial[0]["result"]), 1)
        self._finish_glow_prewarm()
        self.assertFalse(self.window._isolated_glyph_builds)
        self.assertTrue(self.window._isolated_glyph_cache)

    def test_ambient_refresh_reuses_stationary_album_background(self):
        self.window.resize(960, 540)
        frame = QImage(self.window.size(), QImage.Format.Format_RGB32)
        rect = QRectF(self.window.rect())
        primary, secondary = QColor(180, 70, 55), QColor(32, 40, 50)
        with QPainter(frame) as painter:
            self.window._paint_background(painter, rect, primary, secondary, 1.0)
        base_key = self.window._background_base_cache.cacheKey()
        moving_key = self.window._background_cache.cacheKey()
        with QPainter(frame) as painter:
            self.window._paint_background(painter, rect, primary, secondary, 1.3)
        self.assertEqual(self.window._background_base_cache.cacheKey(), base_key)
        self.assertNotEqual(self.window._background_cache.cacheKey(), moving_key)
        with QPainter(frame) as painter:
            self.window._paint_background(painter, rect, QColor(40, 100, 160), secondary, 1.6)
        self.assertNotEqual(self.window._background_base_cache.cacheKey(), base_key)

    def test_wrapped_cjk_scatter_uses_the_same_per_glyph_layers(self):
        self.owner.timed_lyrics = [(0.0, "别走别走"), (12.0, "下一句")]
        self.owner.media_position_seconds = 2.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(520, 292)
        frame = QImage(self.window.size(), QImage.Format.Format_RGB32)
        self.window.render(frame)
        self._finish_glow_prewarm()
        self.assertTrue(self.window._sparse_glyph_halo_cache)
        with patch.object(self.window, "_glyph_run", wraps=self.window._glyph_run) as glyph:
            self.window.render(frame)
            self.assertGreater(glyph.call_count, 0)

    def test_glyph_scatter_halo_reveals_left_to_right_and_withdraws_right_to_left(self):
        font = self.window._surface_font(50.0, QFont.Weight.Bold)
        layout, _height = self.window._wrapped_lyric_layout("shine", font, 600.0)
        self.window._lyric_glow_sprite("shine", font, 600.0, layout, 0)
        origin = QPointF(90.0, 70.0)

        def render_glow(progress):
            image = QImage(450, 270, QImage.Format.Format_RGB32)
            image.fill(QColor(40, 40, 40))
            with QPainter(image) as painter:
                self.window._paint_lyric_scatter_glow(
                    painter, "shine", font, 600.0, layout, origin, (0.0,), progress,
                )
            # Compare halves of the complete halo image, not white text ink.
            left = sum(image.pixelColor(x, y).lightness() - 40 for x in range(25, 165, 2) for y in range(30, 210, 2))
            right = sum(image.pixelColor(x, y).lightness() - 40 for x in range(165, 305, 2) for y in range(30, 210, 2))
            return left, right

        self.assertEqual(render_glow(0.0), (0, 0))
        opening_left, opening_right = render_glow(0.1)
        full_left, full_right = render_glow(0.5)
        closing_left, closing_right = render_glow(0.9)
        self.assertGreater(opening_left, opening_right * 2)
        self.assertGreater(full_left, opening_left)
        self.assertGreater(full_right, opening_right)
        self.assertGreater(closing_left, closing_right * 2)
        self.assertEqual(render_glow(1.0), (0, 0))

    def test_every_wrapped_visual_line_has_a_local_nonempty_scatter_sprite(self):
        font = self.window._surface_font(50.0, QFont.Weight.Bold)
        text = "first wrapped phrase second wrapped phrase\u2028双语翻译继续换行"
        layout, _height = self.window._wrapped_lyric_layout(text, font, 250.0)
        self.assertGreaterEqual(layout.lineCount(), 3)

        for index in range(layout.lineCount()):
            with self.subTest(index=index):
                sprite, padding = self.window._lyric_glow_sprite(text, font, 250.0, layout, index)
                points = [
                    (x, y)
                    for y in range(sprite.height())
                    for x in range(sprite.width())
                    if sprite.pixelColor(x, y).alpha() > 0
                ]
                self.assertTrue(points)
                center_y = sum(point[1] for point in points) / len(points)
                self.assertGreater(center_y, padding * 0.45)
                self.assertLess(center_y, sprite.height() - padding * 0.35)

    def test_logical_resolution_glow_sprite_is_reused_across_dpr_changes(self):
        font = self.window._surface_font(50.0, QFont.Weight.Bold)
        layout, _height = self.window._wrapped_lyric_layout("shine", font, 600.0)
        with patch.object(self.window, "devicePixelRatioF", return_value=1.0):
            first, _padding = self.window._lyric_glow_sprite("shine", font, 600.0, layout, 0)
        entry_count = len(self.window._lyric_glow_cache)
        byte_count = self.window._lyric_glow_cache_bytes
        with patch.object(self.window, "devicePixelRatioF", return_value=2.0):
            second, _padding = self.window._lyric_glow_sprite("shine", font, 600.0, layout, 0)
        self.assertEqual(second.cacheKey(), first.cacheKey())
        self.assertEqual(len(self.window._lyric_glow_cache), entry_count)
        self.assertEqual(self.window._lyric_glow_cache_bytes, byte_count)

    def test_song_layout_and_display_text_reuse_across_long_lyric_animation_frames(self):
        self.owner.timed_lyrics = [(float(index * 3), f"Line {index} with a readable phrase") for index in range(180)]
        self.owner.media_position_seconds = 10.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(960, 540)
        frame = QImage(self.window.size(), QImage.Format.Format_RGB32)
        self.window.render(frame)
        # Long tracks now shape a bounded number of rows between frames.
        # Finish that scheduled first pass before checking reuse on later
        # animation frames; rebuilding while the queue is pending is expected.
        for _ in range(len(self.owner.timed_lyrics) + 1):
            if self.window._song_layout_build is None:
                break
            self.window._advance_song_layout_build(max_rows=12)
        self.assertIsNone(self.window._song_layout_build)
        self.assertEqual(len(self.window._song_layouts), len(self.owner.timed_lyrics))
        existing = self.window._song_layouts
        self.owner.timed_lyrics = list(self.owner.timed_lyrics)
        with (
            patch.object(self.window, "_wrapped_lyric_layout", wraps=self.window._wrapped_lyric_layout) as layout,
            patch.object(self.window, "_presentation_lyric_text", wraps=self.window._presentation_lyric_text) as display,
        ):
            self.window.render(frame)
            layout.assert_not_called()
            display.assert_not_called()
        self.assertIs(existing, self.window._song_layouts)
        self.owner.timed_lyrics[40] = (120.0, "The edited lyric needs a new layout")
        self.window.render(frame)
        self.assertIsNotNone(self.window._song_layout_build)
        for _ in range(len(self.owner.timed_lyrics) + 1):
            if self.window._song_layout_build is None:
                break
            self.window._advance_song_layout_build(max_rows=12)
        self.assertIsNone(self.window._song_layout_build)
        self.assertIsNot(existing, self.window._song_layouts)
        resized = self.window._song_layouts
        self.window.resize(1174, 652)
        self.window.render(QImage(self.window.size(), QImage.Format.Format_RGB32))
        self.assertIsNotNone(self.window._song_layout_build)
        for _ in range(len(self.owner.timed_lyrics) + 1):
            if self.window._song_layout_build is None:
                break
            self.window._advance_song_layout_build(max_rows=12)
        self.assertIsNone(self.window._song_layout_build)
        self.assertIsNot(resized, self.window._song_layouts)

    def test_lift_groups_english_words_and_keeps_cjk_graphemes_separate(self):
        text = "Deep in the shallow end沉沦于浅滩"
        boundaries = self.window._reading_unit_offsets(text)
        utf16 = text.encode("utf-16-le")
        units = [
            utf16[start * 2 : end * 2].decode("utf-16-le")
            for start, end in zip(boundaries, boundaries[1:])
        ]
        self.assertEqual(units[:5], ["Deep ", "in ", "the ", "shallow ", "end"])
        self.assertEqual(units[5:], list("沉沦于浅滩"))
        emoji = "Hello 👩‍💻 世界"
        emoji_boundaries = self.window._reading_unit_offsets(emoji)
        emoji_bytes = emoji.encode("utf-16-le")
        emoji_units = [
            emoji_bytes[start * 2 : end * 2].decode("utf-16-le")
            for start, end in zip(emoji_boundaries, emoji_boundaries[1:])
        ]
        self.assertIn("👩‍💻 ", emoji_units)

    def test_cjk_tracking_adds_space_without_changing_lyric_font_size(self):
        font = self.window._surface_font(38.0)
        text = "沉沦于浅滩"
        spaced, _height = self.window._wrapped_lyric_layout(text, font, 600.0)
        plain = QTextLayout(text, font)
        plain.beginLayout()
        plain_line = plain.createLine()
        plain.endLayout()
        self.assertGreater(spaced.lineAt(0).naturalTextWidth(), plain_line.naturalTextWidth())
        self.assertEqual(font.pixelSize(), 38)

    def test_apple_lift_draws_whole_shaped_units_and_blends_pale_unread_to_white(self):
        text = "An intentionally long Apple lyric line with 漢字 and punctuation, moving gently"
        font = QFont()
        font.setFamilies(["Microsoft YaHei UI"])
        font.setPixelSize(34)
        font.setWeight(QFont.Weight.Black)
        source, boundaries = self.window._glyph_run(text, font)
        self.assertGreaterEqual(len(boundaries), 5)
        self.assertLess(len(boundaries), 20)
        self.assertTrue(all(right >= left for left, right in zip(boundaries, boundaries[1:])))
        origin = QPointF(20.5, 45.25)
        line_height = 84.0

        class CountingPainter:
            def __init__(self, wrapped):
                self.wrapped = wrapped
                self.draw_count = 0

            def __getattr__(self, name):
                return getattr(self.wrapped, name)

            def drawImage(self, *args):
                self.draw_count += 1
                self.wrapped.drawImage(*args)

        for progress in (0.0, 0.02, 0.14, 0.5, 0.73, 0.98, 1.0):
            batched = QImage(1700, 180, QImage.Format.Format_ARGB32_Premultiplied)
            batched.fill(Qt.GlobalColor.transparent)
            with QPainter(batched) as painter:
                painter.setOpacity(0.83)
                counter = CountingPainter(painter)
                self.window._paint_lifted_run(
                    counter, origin, line_height, text, font, progress,
                    QColor(241, 222, 225, 186),
                )
                self.assertAlmostEqual(painter.opacity(), 0.83)
            self.assertLessEqual(counter.draw_count, (len(boundaries) - 1) * 2)
            self.assertFalse(batched.isNull())
        isolated = self.window._isolated_glyph_units(text, font)
        self.assertEqual(len(isolated), len(boundaries) - 1)
        self.assertTrue(all(unit.width() > 0 for unit, _x in isolated))

    def test_isolated_lift_units_keep_transparent_ink_bleed_on_every_edge(self):
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(42)
        font.setWeight(QFont.Weight.Bold)
        font.setItalic(True)

        units = self.window._isolated_glyph_units("Flowing italic words 漢字", font)

        self.assertGreaterEqual(len(units), 4)
        for image, _left in units:
            edge_alpha = []
            for x in (0, 1, image.width() - 2, image.width() - 1):
                edge_alpha.extend(image.pixelColor(x, y).alpha() for y in range(image.height()))
            for y in (0, 1, image.height() - 2, image.height() - 1):
                edge_alpha.extend(image.pixelColor(x, y).alpha() for x in range(image.width()))
            self.assertTrue(all(alpha == 0 for alpha in edge_alpha))

    def test_lyric_raster_cache_matches_layout_and_invalidates_with_visual_key(self):
        text = "Cached lyric line 漢字 wraps without changing its layout"
        font = QFont("Microsoft YaHei UI", 30)
        font.setWeight(QFont.Weight.Black)
        color = QColor(245, 250, 255, 218)
        layout, height = self.window._wrapped_lyric_layout(text, font, 360.0)
        origin = QPointF(20.25, 24.5)

        direct = QImage(520, 220, QImage.Format.Format_ARGB32_Premultiplied)
        direct.fill(Qt.GlobalColor.transparent)
        with QPainter(direct) as painter:
            self.window._draw_wrapped_layout(painter, layout, origin, color)
        cached = QImage(direct.size(), direct.format())
        cached.fill(Qt.GlobalColor.transparent)
        with QPainter(cached) as painter:
            self.window._draw_cached_wrapped_layout(
                painter, layout, origin, text, font, 360.0, height, color,
            )
        direct_bytes = direct.bits().tobytes()
        cached_bytes = cached.bits().tobytes()
        different = sum(left != right for left, right in zip(direct_bytes, cached_bytes))
        self.assertLess(different / len(direct_bytes), 0.005)

        first, _padding = self.window._lyric_raster(text, font, 360.0, height, layout, color)
        reused, _padding = self.window._lyric_raster(text, font, 360.0, height, layout, color)
        self.assertEqual(reused.cacheKey(), first.cacheKey())
        recolored, _padding = self.window._lyric_raster(
            text, font, 360.0, height, layout, QColor(255, 255, 255, 255),
        )
        self.assertNotEqual(recolored.cacheKey(), first.cacheKey())
        narrower_layout, narrower_height = self.window._wrapped_lyric_layout(text, font, 300.0)
        resized, _padding = self.window._lyric_raster(
            text, font, 300.0, narrower_height, narrower_layout, color,
        )
        self.assertNotEqual(resized.cacheKey(), first.cacheKey())
        next_track_text = text + " next track"
        next_layout, next_height = self.window._wrapped_lyric_layout(next_track_text, font, 360.0)
        next_track, _padding = self.window._lyric_raster(
            next_track_text, font, 360.0, next_height, next_layout, color,
        )
        self.assertNotEqual(next_track.cacheKey(), first.cacheKey())

        self.window._lyric_raster_cache.clear()
        self.window._lyric_raster_cache_bytes = 0
        self.window.LYRIC_RASTER_CACHE_MAX_ENTRIES = 2
        for suffix in ("one", "two", "three"):
            line = f"bounded cache {suffix}"
            line_layout, line_height = self.window._wrapped_lyric_layout(line, font, 360.0)
            self.window._lyric_raster(line, font, 360.0, line_height, line_layout, color)
        self.assertEqual(len(self.window._lyric_raster_cache), 2)
        self.assertLessEqual(
            self.window._lyric_raster_cache_bytes,
            self.window.LYRIC_RASTER_CACHE_MAX_BYTES,
        )

    def test_empty_media_does_not_also_display_no_lyrics(self):
        self.owner.track_title = "暂无媒体"
        self.assertEqual(self.window._display_lyrics(), ([], []))
        self.owner.track_title = "A song"
        self.assertEqual(self.window._display_lyrics(), (["暂无歌词"], []))

    def test_bilingual_clause_break_is_display_only_and_keeps_timestamps(self):
        self.owner.timed_lyrics = [(12.5, "Deep in the shallow end沉沦于浅滩")]
        lines, timed = self.window._display_lyrics()
        self.assertEqual(lines, ["Deep in the shallow end\u2028沉沦于浅滩"])
        self.assertEqual(timed, [(12.5, "Deep in the shallow end\n沉沦于浅滩")])
        self.assertEqual(self.owner.timed_lyrics, [(12.5, "Deep in the shallow end沉沦于浅滩")])
        font = self.window._surface_font(38.0)
        layout, _height = self.window._wrapped_lyric_layout(lines[0], font, 600.0)
        self.assertGreaterEqual(layout.lineCount(), 2)
        self.assertEqual(self.window._presentation_lyric_text("作曲：Aldwin Cajili"), "作曲：Aldwin Cajili")

    def test_intro_cleanup_countdown_keeps_sung_timestamp_and_seek_target(self):
        self.owner.music_intro_cleanup_enabled = True
        self.owner.timed_lyrics = [
            (0.3, "作词：A Singer"),
            (1.2, "[Verse 1]"),
            (5.0, "[Verse 1] Shine"),
            (8.0, "next line"),
        ]
        lines, timed = self.window._display_lyrics()
        self.assertEqual(timed, [(0.0, "· · ·"), (5.0, "Shine"), (8.0, "next line")])
        self.assertEqual(lines[0], "· · ·")
        self.owner.media_position_seconds = 2.5
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        frame = self.window._lyric_frame(timed, 100.0)
        self.assertEqual(frame.active_index, 0)
        self.assertAlmostEqual(frame.reading_progress, 0.5)
        self.window._seek_to_lyric(1)
        self.assertEqual(self.owner.seek_calls, [5.0])
        self.owner.music_intro_cleanup_enabled = False
        _lines, uncleaned = self.window._display_lyrics()
        self.assertEqual(uncleaned[0], (1.2, "[Verse 1]"))
        self.assertEqual(uncleaned[1], (5.0, "[Verse 1] Shine"))

    def test_music_surface_omits_intro_dots_before_five_seconds(self):
        self.owner.music_intro_cleanup_enabled = True
        self.owner.timed_lyrics = [(0.0, "作词：A"), (4.99, "Shine"), (8.0, "next")]
        lines, timed = self.window._display_lyrics()
        self.assertEqual(lines[0], "Shine")
        self.assertEqual(timed[0], (4.99, "Shine"))
        self.window._seek_to_lyric(0)
        self.assertEqual(self.owner.seek_calls, [4.99])
        self.owner.timed_lyrics = [(0.0, "作词：A"), (5.0, "Shine"), (8.0, "next")]
        lines, timed = self.window._display_lyrics()
        self.assertEqual(lines[0], "· · ·")
        self.assertEqual(timed[1], (5.0, "Shine"))

    def test_intro_countdown_dots_fill_left_to_right(self):
        font = self.window._surface_font(38.0)
        row = QRectF(20.0, 8.0, 200.0, 70.0)
        unread = QColor(107, 74, 151)
        radius = self.window._intro_dot_radius(font)
        stride = radius * 3.35
        centers = [
            (round(row.left() + radius * 1.2 + index * stride), round(row.top() + row.height() * 0.48))
            for index in range(3)
        ]

        def colors(progress):
            image = QImage(240, 90, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                self.window._paint_intro_countdown(
                    painter, row, font, progress, unread_color=unread,
                )
            return [image.pixelColor(x, y) for x, y in centers]

        initial = colors(0.0)
        middle = colors(0.5)
        completed = colors(1.0)
        self.assertTrue(all(color == unread for color in initial))
        self.assertGreater(middle[0].red(), middle[1].red())
        self.assertGreater(middle[1].red(), middle[2].red())
        self.assertTrue(all(color == QColor(255, 255, 255) for color in completed))

    def test_intro_countdown_unread_blur_keeps_album_lyric_tone(self):
        font = self.window._surface_font(38.0)
        row = QRectF(20.0, 8.0, 200.0, 70.0)
        unread = QColor(112, 68, 156)
        radius = self.window._intro_dot_radius(font)
        center = QPoint(
            round(row.left() + radius * 1.2),
            round(row.top() + row.height() * 0.48),
        )
        for level in (1, 2):
            image = QImage(240, 90, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                self.window._paint_intro_countdown(
                    painter, row, font, 0.0, level, unread_color=unread,
                )
            color = image.pixelColor(center)
            self.assertGreater(color.alpha(), 0)
            for actual, expected in zip(color.getRgb()[:3], unread.getRgb()[:3]):
                self.assertAlmostEqual(actual, expected, delta=2)

    def test_intro_countdown_blurred_dots_remain_full_sized(self):
        font = self.window._surface_font(38.0)
        row = QRectF(20.0, 8.0, 200.0, 70.0)
        radius = self.window._intro_dot_radius(font)
        self.assertGreater(radius, font.pixelSize() * 0.15)
        centers = [
            round(row.left() + radius * 1.2 + index * radius * 3.35)
            for index in range(3)
        ]
        y = round(row.top() + row.height() * 0.48)

        def image_at(level):
            image = QImage(240, 90, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                self.window._paint_intro_countdown(painter, row, font, 1.0, level)
            return image

        crisp, near, far = (image_at(level) for level in range(3))
        for x in centers:
            self.assertGreater(crisp.pixelColor(x, y).alpha(), 245)
            self.assertGreater(near.pixelColor(x, y).alpha(), 100)
            self.assertGreater(far.pixelColor(x, y).alpha(), 70)
            self.assertGreater(near.pixelColor(x + round(radius * 0.7), y).alpha(), 75)
            self.assertGreater(far.pixelColor(x + round(radius * 0.7), y).alpha(), 55)
        self.assertEqual(len(self.window._lyric_blur_cache), 2)

    def test_finished_intro_countdown_uses_dots_instead_of_small_text_glyphs(self):
        self.owner.music_intro_cleanup_enabled = True
        self.owner.timed_lyrics = [
            (0.0, "· · ·"), (5.0, "first lyric"), (9.0, "second lyric"),
        ]
        self.owner.media_position_seconds = 5.5
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(960, 540)
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch.object(self.window, "_paint_intro_countdown", wraps=self.window._paint_intro_countdown) as dots:
            self.window.render(output)
        self.assertTrue(dots.called)
        self.assertEqual(dots.call_args.args[3], 1.0)
        self.assertEqual(dots.call_args.args[4], 1)
        self.assertEqual(
            dots.call_args.kwargs["unread_color"].rgba(),
            self.window._future_lyric_color(QRectF(self.window.rect())).rgba(),
        )

    def test_credit_only_timed_source_shows_no_lyrics_placeholder(self):
        self.owner.music_intro_cleanup_enabled = True
        self.owner.timed_lyrics = [(0.0, "作词：A"), (1.0, "作曲：B")]
        self.assertEqual(self.window._display_lyrics(), (["暂无歌词"], []))

    def test_translation_visibility_uses_existing_dual_line_switch(self):
        self.owner.timed_lyrics = [
            (2.0, "I love you"), (2.0, "我爱你"),
            (6.0, "Deep in the shallow end沉沦于浅滩"),
        ]
        self.owner.enable_dual_line_lyrics = False
        lines, hidden = self.window._display_lyrics()
        self.assertEqual(hidden, [(2.0, "I love you"), (6.0, "Deep in the shallow end")])
        self.assertEqual(lines, ["I love you", "Deep in the shallow end"])
        self.owner.enable_dual_line_lyrics = True
        lines, shown = self.window._display_lyrics()
        self.assertEqual(shown[0], (2.0, "I love you\n我爱你"))
        self.assertEqual(shown[1], (6.0, "Deep in the shallow end\n沉沦于浅滩"))
        self.assertEqual(lines[1], "Deep in the shallow end\u2028沉沦于浅滩")
        self.assertEqual(len(self.owner.timed_lyrics), 3)

    def test_same_timestamp_original_and_translation_are_one_logical_sentence(self):
        self.owner.timed_lyrics = [
            (46.18, "I don't need no space"),
            (46.18, "我不需要空间"),
            (49.30, "Give me all your loving"),
            (49.30, "将你的爱都给我"),
        ]

        lines, timed = self.window._display_lyrics()

        self.assertEqual(len(timed), 2)
        self.assertEqual(timed[0], (46.18, "I don't need no space\n我不需要空间"))
        self.assertEqual(lines[0], "I don't need no space\u2028我不需要空间")
        self.owner.media_position_seconds = 46.8
        frame = self.window._lyric_frame(timed, 100.0)
        self.assertEqual(frame.active_index, 0)

    def test_same_timestamp_colon_note_does_not_displace_translation(self):
        self.owner.music_intro_cleanup_enabled = True
        self.owner.timed_lyrics = [
            (5.0, "制作人：甲"),
            (5.0, "Shine"),
            (5.0, "翻译：闪耀"),
            (10.0, "作曲：乙\nNext"),
            (10.0, "下一句"),
        ]
        self.owner.enable_dual_line_lyrics = True
        lines, timed = self.window._display_lyrics()
        self.assertEqual(timed, [(0.0, "· · ·"), (5.0, "Shine\n闪耀"), (10.0, "作曲：乙\nNext\n下一句")])
        self.assertEqual(lines[1], "Shine\u2028闪耀")
        self.owner.enable_dual_line_lyrics = False
        self.window._display_lyrics()  # Start the companion's elastic exit.
        with patch("local.music_surface_window.time.monotonic", return_value=self.window._translation_exit_at + 0.5):
            _lines, hidden = self.window._display_lyrics()
        self.assertEqual(hidden, [(0.0, "· · ·"), (5.0, "Shine"), (10.0, "作曲：乙\nNext")])

    def test_same_timestamp_unlabeled_intro_credit_does_not_displace_translation(self):
        self.owner.music_intro_cleanup_enabled = True
        self.owner.enable_dual_line_lyrics = True
        self.owner.timed_lyrics = [
            (6.0, "Produced by Alice"),
            (6.0, "Shine"),
            (6.0, "闪耀"),
            (10.0, "Next"),
        ]
        _lines, timed = self.window._display_lyrics()
        self.assertEqual(timed, [(0.0, "· · ·"), (6.0, "Shine\n闪耀"), (10.0, "Next")])

    def test_recurring_singer_labels_survive_whole_song_cleanup_with_unit_mapping(self):
        self.owner.music_intro_cleanup_enabled = True
        first_units = (SimpleNamespace(start_seconds=2.0, end_seconds=3.0, text="成龙：跟我唱"),)
        self.owner.timed_lyrics = [
            (0.0, "制作人：甲"), (1.0, "介绍：开场"),
            (2.0, "成龙：跟我唱"), (4.0, "一起唱"),
            (6.0, "成龙：再一起"),
        ]
        self.owner.timed_lyric_units = [(), (), first_units, (), ()]
        self.owner.timed_lyric_timing_kinds = ["line", "line", "unit", "line", "line"]
        lines, timed = self.window._display_lyrics()
        self.assertEqual([text for _time, text in timed], ["成龙：跟我唱", "一起唱", "成龙：再一起"])
        self.assertEqual(lines[0], "成龙：跟我唱")
        self.assertIs(self.window._display_timed_units[0], first_units)

    def test_sparse_priority_rule_uses_primary_line_not_same_timestamp_translation(self):
        timed = self.window._coalesced_timed_lyrics(
            [(10.0, "Stay"), (10.0, "留下"), (15.0, "next")]
        )

        plan = lyric_emphasis_plan(
            self.window._primary_timed_lyric_text(timed[0][1]),
            timed[0][0],
            timed[1][0],
        )

        self.assertEqual(plan.mode, "character_lift")

    def test_clicking_coalesced_translation_row_seeks_its_shared_timestamp(self):
        self.owner.media_track_id = "song"
        self.owner.timed_lyrics = [
            (0.0, "first"),
            (9.0, "second"),
            (9.0, "第二句"),
        ]
        self.owner._effective_lyric_offset_seconds = lambda: 1.5
        self.window._browse_track_key = "song"
        self.window._browse_index = 1.0

        self.window._seek_to_lyric(1)

        self.assertEqual(self.owner.seek_calls, [7.5])

    def test_unchanged_album_cover_reuses_scaled_image(self):
        self.owner.album_art = QImage(128, 128, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(Qt.GlobalColor.blue)
        self.window.resize(960, 540)
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        self.window.render(output)
        cache_key = self.window._cover_cache.cacheKey()
        self.window.render(output)
        self.assertEqual(self.window._cover_cache.cacheKey(), cache_key)

    def test_album_cover_cache_respects_high_dpi_even_when_unfocused(self):
        self.owner.album_art = QImage(1600, 1600, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(Qt.GlobalColor.blue)
        self.window.resize(960, 540)
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch.object(self.window, "devicePixelRatioF", return_value=2.0):
            self.window.render(output)
            cache = self.window._cover_cache
            self.assertEqual(cache.devicePixelRatio(), 2.0)
            self.assertGreater(cache.width(), 600)
            with patch.object(self.window, "isActiveWindow", return_value=False):
                self.window.render(output)
            self.assertEqual(self.window._cover_cache.cacheKey(), cache.cacheKey())

    def test_ambient_background_reuses_slow_frame_while_lyrics_continue_at_full_rate(self):
        self.owner.media_track_id = "song"
        self.owner.timed_lyrics = [(0.0, "A lyric reading across the surface"), (4.0, "Next line")]
        self.owner.media_position_seconds = 0.3
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(960, 540)
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch.object(self.window, "_paint_ambient_lights", wraps=self.window._paint_ambient_lights) as lights:
            with patch("local.music_surface_window.time.monotonic", return_value=20.0):
                self.window.render(output)
            first_frame = output.bits().tobytes()
            self.owner.media_position_seconds = 0.316
            with patch("local.music_surface_window.time.monotonic", return_value=20.016):
                self.window.render(output)
            self.assertEqual(lights.call_count, 1)
            self.assertNotEqual(first_frame, output.bits().tobytes())
            self.owner.media_position_seconds = 0.43
            with patch("local.music_surface_window.time.monotonic", return_value=20.13):
                self.window.render(output)
            self.assertEqual(lights.call_count, 2)

    def test_ambient_background_invalidates_on_track_colors_intensity_and_size(self):
        self.owner.media_track_id = "song-a"
        self.owner.album_color = QColor(160, 20, 30)
        self.owner.secondary_color = QColor(20, 30, 50)
        self.window.resize(960, 540)

        def render_at(now):
            output = QImage(self.window.size(), QImage.Format.Format_RGB32)
            with patch("local.music_surface_window.time.monotonic", return_value=now):
                self.window.render(output)

        with patch.object(self.window, "_paint_ambient_lights", wraps=self.window._paint_ambient_lights) as lights:
            render_at(20.0)
            self.owner.media_track_id = "song-b"
            render_at(20.01)
            self.assertEqual(lights.call_count, 2)
            # With no distinct accent, a secondary-only change intentionally
            # leaves the single-primary fallback background unchanged.
            self.owner.album_color = QColor(100, 40, 120)
            render_at(20.03)
            self.assertEqual(lights.call_count, 3)
            self.owner.music_flow_intensity = 0.86
            render_at(20.04)
            self.assertEqual(lights.call_count, 4)
            self.window.resize(3840, 2160)
            render_at(20.05)
            self.assertEqual(lights.call_count, 5)
            self.assertEqual(self.window._background_cache.size(), QSize(960, 540))

    def test_music_surface_uses_heavy_wrapped_lyrics_and_grouped_controls(self):
        self.owner.track_title = "Attention"
        self.owner.artist = "Charlie Puth"
        self.owner.timed_lyrics = [(0.0, "A deliberately long lyric line that must wrap without being elided"), (4.0, "next")]
        self.owner.media_position_seconds = 1.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(1174, 652)
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        self.window.render(output)

        font = self.window._surface_font(30.0, QFont.Weight.Black)
        layout, _height = self.window._wrapped_lyric_layout(self.owner.timed_lyrics[0][1], font, 360.0)
        self.assertGreaterEqual(font.weight(), QFont.Weight.Black)
        self.assertGreater(layout.lineCount(), 1)
        self.assertEqual(set(self.window._media_button_rects), {"previous", "toggle_play_pause", "next"})
        buttons = list(self.window._media_button_rects.values())
        self.assertLess(buttons[0].left(), buttons[1].left())
        self.assertLess(buttons[1].left(), buttons[2].left())

    def test_wrapped_sentence_has_tighter_visual_lines_than_neighboring_sentences(self):
        text = "Feet up on the dash, drivin' nowhere fast, burnin' through the summer"
        font = self.window._surface_font(38.0)
        layout, height = self.window._wrapped_lyric_layout(text, font, 360.0)
        self.assertGreaterEqual(layout.lineCount(), 2)
        first = layout.lineAt(0)
        second = layout.lineAt(1)
        self.assertAlmostEqual(second.y() - first.y(), round(first.height() * 0.86), delta=0.05)
        last = layout.lineAt(layout.lineCount() - 1)
        self.assertAlmostEqual(height, last.y() + last.height(), delta=0.05)

        layouts, row_tops = self.window._song_lyric_layouts([text, "Next lyric"], font, 360.0, 24.0)
        self.assertEqual(layouts[0][0], layout)
        self.assertAlmostEqual(row_tops[1] - row_tops[0] - height, 24.0)
        last_to_next = row_tops[1] - (row_tops[0] + last.y())
        self.assertGreater(last_to_next, second.y() - first.y())

    def test_music_surface_typography_uses_system_fallbacks_and_pixel_sizes(self):
        lyric = self.window._surface_font(self.window.LYRIC_PIXEL_SIZE, QFont.Weight.Bold)
        title = self.window._surface_font(self.window.TITLE_PIXEL_SIZE, QFont.Weight.Bold)
        artist = self.window._surface_font(self.window.ARTIST_PIXEL_SIZE, QFont.Weight.Medium)

        self.assertEqual(lyric.families()[:2], ["Segoe UI", "Microsoft YaHei UI"])
        self.assertEqual(lyric.pixelSize(), 38)
        self.assertEqual(title.pixelSize(), 34)
        self.assertEqual(title.weight(), QFont.Weight.Bold)
        self.assertEqual(artist.pixelSize(), 22)
        self.assertEqual(artist.weight(), QFont.Weight.Medium)
        self.assertEqual(lyric.pointSizeF(), -1.0)

        layout, height = self.window._wrapped_lyric_layout("像素字号不会裁掉歌词", lyric, 360.0)
        self.assertGreaterEqual(height, layout.lineAt(0).height())

    def test_future_lyric_color_is_subdued_album_tinted_and_contrasts_with_background(self):
        self.window.resize(320, 180)
        self.owner.album_color = QColor(204, 50, 54)
        region = QRectF(150.0, 12.0, 150.0, 150.0)
        for index, background in enumerate((QColor(24, 27, 34), QColor(236, 238, 242))):
            with self.subTest(background=background.name()):
                self.owner.media_track_id = f"contrast-artwork-{index}"
                self.window._background_cache = QImage(320, 180, QImage.Format.Format_RGB32)
                self.window._background_cache.fill(background)
                future = self.window._future_lyric_color(region)
                channels = future.getRgb()[:3]
                self.assertLess(min(channels), 200)
                self.assertGreater(future.red() - future.green(), 35)
                self.assertGreater(future.red() - future.blue(), 35)
                self.assertGreaterEqual(future.saturation(), 110)
                composite = self.window._composited_color(future, background)
                self.assertGreaterEqual(self.window._contrast_ratio(composite, background), 2.0)

    def test_multicolor_art_uses_primary_center_secondary_sides_and_gold_unread_ink(self):
        art = QImage(100, 100, QImage.Format.Format_RGB32)
        art.fill(QColor(8, 9, 12))
        with QPainter(art) as painter:
            painter.fillRect(0, 55, 100, 35, QColor(181, 112, 59))
            painter.fillRect(0, 90, 100, 10, QColor(226, 184, 43))
        self.owner.album_art = art
        self.owner.media_track_id = "warm-multicolor-art"
        self.window.resize(960, 540)

        palette = self.window._art_palette()
        self.assertGreater(palette.secondary.red(), palette.secondary.blue() + 60)
        self.assertGreater(palette.characteristic.red(), palette.characteristic.blue() + 80)

        frame = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with QPainter(frame) as painter:
            self.window._paint_background(
                painter, QRectF(self.window.rect()), palette.primary, palette.secondary, 1.0,
            )
        base = self.window._background_base_cache
        center = base.pixelColor(base.width() // 2, base.height() // 2)
        edge = base.pixelColor(0, base.height() // 2)
        self.assertLess(abs(center.red() - center.blue()), 8)
        self.assertGreater(edge.red(), edge.blue() + 25)
        self.assertGreater(edge.red(), center.red() + 12)
        # Exactly the central 70% holds the dominant role; each side owns
        # the remaining 15% blend, independent of the drifting ambience.
        for fraction in (0.16, 0.32, 0.50, 0.68, 0.84):
            self.assertEqual(base.pixelColor(round(base.width() * fraction), base.height() // 2), center)

        unread = self.window._future_lyric_color(QRectF(520, 100, 350, 240))
        self.assertGreater(unread.red(), unread.blue() + 55)
        self.assertLess(unread.value(), 235)
        for backdrop in self.window._background_sample_colors(QRectF(520, 100, 350, 240)):
            self.assertGreaterEqual(
                self.window._contrast_ratio(self.window._composited_color(unread, backdrop), backdrop),
                2.0,
            )

    def test_monochrome_album_unread_tone_is_distinct_from_white(self):
        self.window.resize(320, 180)
        self.owner.media_track_id = "monochrome-artwork"
        self.owner.album_color = QColor(78, 78, 78)
        background = QColor(65, 65, 65)
        self.window._background_cache = QImage(320, 180, QImage.Format.Format_RGB32)
        self.window._background_cache.fill(background)

        future = self.window._future_lyric_color(QRectF(150.0, 12.0, 150.0, 100.0))

        self.assertEqual(future.red(), future.green())
        self.assertEqual(future.green(), future.blue())
        self.assertLessEqual(future.value(), 195)
        self.assertGreaterEqual(
            self.window._contrast_ratio(self.window._composited_color(future, background), background),
            2.0,
        )

    def test_future_tone_is_fixed_across_ambient_frames_and_changes_with_album(self):
        self.window.resize(320, 180)
        self.owner.media_track_id = "one-song"
        self.owner.album_color = QColor(204, 50, 54)
        region = QRectF(150.0, 12.0, 150.0, 100.0)
        tones = []
        for shade in (62, 83, 91, 78, 62):
            self.window._background_cache = QImage(320, 180, QImage.Format.Format_RGB32)
            self.window._background_cache.fill(QColor(shade, shade, shade))
            tones.append(self.window._future_lyric_color(region).rgba())
        self.assertEqual(len(set(tones)), 1)

        self.owner.album_color = QColor(48, 84, 210)
        blue = self.window._future_lyric_color(region)
        self.assertNotEqual(blue.rgba(), tones[0])
        self.assertGreater(blue.blue(), blue.red() + 35)

    def test_wrapped_future_lyric_keeps_one_tone_across_visual_lines(self):
        self.window.resize(320, 180)
        self.owner.album_color = QColor(204, 50, 54)
        background = QImage(320, 180, QImage.Format.Format_RGB32)
        background.fill(QColor(226, 151, 108))
        with QPainter(background) as painter:
            painter.fillRect(QRectF(0, 80, 320, 100), QColor(35, 23, 27))
        self.window._background_cache = background
        font = self.window._surface_font(26.0, QFont.Weight.Bold)
        text = "one two three four five six seven"
        layout, height = self.window._wrapped_lyric_layout(text, font, 154.0)
        self.assertGreaterEqual(layout.lineCount(), 2)
        origin = QPointF(150.0, 12.0)
        colors = self.window._future_visual_line_colors(layout, origin)
        self.assertTrue(all(color.lightness() < 205 for color in colors))
        self.assertTrue(all(color.red() > color.green() for color in colors))
        self.assertEqual(colors[0].rgba(), colors[-1].rgba())

        output = QImage(320, 180, QImage.Format.Format_RGB32)
        output.fill(Qt.GlobalColor.black)
        with QPainter(output) as painter:
            self.window._draw_future_wrapped_layout(
                painter, layout, origin, text, font, 154.0, height,
            )
        self.assertGreaterEqual(len(self.window._lyric_raster_cache), 1)

        with patch.object(self.window, "_paint_lifted_run") as draw_run:
            with QPainter(output) as painter:
                self.window._paint_wrapped_lifted_lyric(
                    painter, QRectF(origin.x(), origin.y(), 154.0, height),
                    text, font, layout, 0.25,
                )
        active_unread = [call.args[6].rgba() for call in draw_run.call_args_list]
        self.assertGreaterEqual(len(active_unread), 2)
        self.assertEqual(len(set(active_unread)), 1)

    def test_future_lyric_tone_does_not_flip_near_old_luminance_threshold(self):
        self.window.resize(320, 180)
        self.owner.album_color = QColor(204, 50, 54)
        region = QRectF(150.0, 12.0, 150.0, 100.0)
        tones = []
        for shade in (148, 150, 148, 150):
            background = QImage(320, 180, QImage.Format.Format_RGB32)
            background.fill(QColor(shade, shade, shade))
            self.window._background_cache = background
            tone = self.window._future_lyric_color(region)
            tones.append(tone.rgba())
            composite = self.window._composited_color(tone, QColor(shade, shade, shade))
            self.assertGreaterEqual(
                self.window._contrast_ratio(composite, QColor(shade, shade, shade)), 2.0,
            )
        self.assertEqual(len(set(tones)), 1)

    def test_future_lyric_palette_direction_is_fixed_for_song_during_ambient_motion(self):
        self.window.resize(320, 180)
        self.owner.album_color = QColor(204, 50, 54)
        self.owner.media_track_id = "song-with-moving-light"
        region = QRectF(150.0, 12.0, 150.0, 100.0)
        tones = []
        for shade in (80, 111, 112, 111, 112):
            background = QImage(320, 180, QImage.Format.Format_RGB32)
            background.fill(QColor(shade, shade, shade))
            self.window._background_cache = background
            tones.append(self.window._future_lyric_color(region).value())
        self.assertLessEqual(max(tones[2:]) - min(tones[2:]), 20)
        self.assertLessEqual(abs(tones[2] - tones[1]), 20)

        self.owner.media_track_id = "new-song-with-light-artwork"
        background = QImage(320, 180, QImage.Format.Format_RGB32)
        background.fill(QColor(238, 238, 238))
        self.window._background_cache = background
        tone = self.window._future_lyric_color(region)
        composite = self.window._composited_color(tone, QColor(238, 238, 238))
        self.assertGreaterEqual(self.window._contrast_ratio(composite, QColor(238, 238, 238)), 2.0)

    def test_future_lyric_tone_varies_continuously_across_ambient_gray_levels(self):
        self.window.resize(320, 180)
        self.owner.album_color = QColor(204, 50, 54)
        region = QRectF(150.0, 12.0, 150.0, 100.0)
        for track_id, shades in (
            ("dark-starting-artwork", range(15, 241)),
            ("light-starting-artwork", range(240, 14, -1)),
        ):
            with self.subTest(track_id=track_id):
                self.owner.media_track_id = track_id
                values = []
                for shade in shades:
                    background = QImage(320, 180, QImage.Format.Format_RGB32)
                    background.fill(QColor(shade, shade, shade))
                    self.window._background_cache = background
                    values.append(self.window._future_lyric_color(region).value())
                self.assertLessEqual(
                    max(abs(current - previous) for previous, current in zip(values, values[1:])),
                    6,
                )

    def test_transport_has_three_large_slots_without_a_visible_backplate(self):
        self.owner.track_title = "Attention"
        self.owner.artist = "Charlie Puth"
        self.owner.album_color = QColor(204, 50, 54)
        self.owner.secondary_color = QColor(72, 25, 29)
        self.window.resize(1174, 652)
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        self.window.render(output)

        panel = self.window._media_control_panel_rect
        scale = self.window._surface_scale()
        self.assertAlmostEqual(panel.height(), 60.0 * scale)
        self.assertLessEqual(panel.width(), 350.0 * scale)
        buttons = list(self.window._media_button_rects.values())
        self.assertEqual(len(buttons), 3)
        self.assertAlmostEqual(buttons[0].left(), panel.left())
        self.assertAlmostEqual(buttons[0].right(), buttons[1].left())
        self.assertAlmostEqual(buttons[1].right(), buttons[2].left())
        self.assertAlmostEqual(buttons[2].right(), panel.right())
        self.assertTrue(all(abs(button.width() - panel.width() / 3.0) < 0.01 for button in buttons))
        cover_origin = self.window._transport_light_origin()
        self.assertLess(cover_origin.x(), panel.left() + panel.width())
        self.assertLess(cover_origin.y(), panel.top())

        background = QColor(22, 27, 34)
        unread = QColor(225, 125, 132, 242)
        controls = QRectF(8.0, 15.0, 350.0, 60.0)

        def paint(hovered="", pressed="", playing=True):
            self.owner.media_is_playing = playing
            self.window._hovered_media_command = hovered
            self.window._pressed_media_command = pressed
            image = QImage(380, 90, QImage.Format.Format_RGB32)
            image.fill(background)
            with patch.object(self.window, "_future_lyric_color", return_value=unread) as tone:
                with patch.object(self.window, "_paint_transport_icon", wraps=self.window._paint_transport_icon) as draw:
                    with QPainter(image) as painter:
                        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                        self.window._paint_media_controls(
                            painter, controls, self.owner.album_color, self.owner.secondary_color,
                        )
                colors = [call.args[3] for call in draw.call_args_list]
            tone.assert_called_once_with(self.window._media_control_panel_rect)
            return image, colors

        normal, colors = paint()
        normal_hit_rects = dict(self.window._media_button_rects)
        self.assertEqual(colors, [unread] * 3)
        for x, y in ((9, 16), (20, 45), (119, 45), (240, 45), (350, 45)):
            self.assertEqual(normal.pixelColor(x, y), background)
        for button in self.window._media_button_rects.values():
            self.assertGreaterEqual(button.width(), 100.0)
            self.assertGreaterEqual(button.height(), 50.0)
            center = button.center()
            ink = sum(
                normal.pixelColor(x, y) != background
                for y in range(round(center.y()) - 17, round(center.y()) + 18)
                for x in range(round(center.x()) - 20, round(center.x()) + 21)
            )
            self.assertGreater(ink, 250)

        hovered, colors = paint(hovered="previous")
        self.assertEqual(colors[0].getRgb()[:3], unread.getRgb()[:3])
        self.assertEqual(colors[0].alpha(), 255)
        self.assertEqual(colors[1:], [unread, unread])
        self.assertEqual(hovered.pixelColor(20, 45), background)
        self.assertEqual(self.window._media_button_rects, normal_hit_rects)

        pressed, colors = paint(pressed="next")
        self.assertEqual(colors[:2], [unread, unread])
        self.assertEqual(colors[2].alpha(), round(unread.alpha() * 0.70))
        self.assertEqual(pressed.pixelColor(350, 45), background)
        self.assertEqual(self.window._media_button_rects, normal_hit_rects)

        playing, _ = paint(playing=True)
        paused, _ = paint(playing=False)
        middle = self.window._media_button_rects["toggle_play_pause"].center()
        self.assertEqual(playing.pixelColor(round(middle.x()), round(middle.y())), background)
        self.assertNotEqual(paused.pixelColor(round(middle.x()), round(middle.y())), background)

    def test_fullscreen_skip_feedback_expands_circular_light_from_cover_and_scales_only_icon(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        self.owner.timed_lyrics = [
            (0.0, "Current lyric"), (4.0, "Next lyric"),
            (8.0, "Third lyric"), (12.0, "Fourth lyric"),
        ]
        self.window._reset_control_idle(now=50.0)
        controls = QRectF(100.0, 540.0, 350.0, 60.0)
        background = QColor(27, 30, 35)
        unread = QColor(229, 176, 104, 242)

        def render(at):
            image = QImage(1250, 720, QImage.Format.Format_RGB32)
            image.fill(background)
            icon_scales = []
            original = self.window._paint_transport_icon

            def inspect_icon(painter, rect, command, color):
                if command == "previous":
                    icon_scales.append(painter.worldTransform().m11())
                original(painter, rect, command, color)

            with (
                patch("local.music_surface_window.time.monotonic", return_value=at),
                patch.object(self.window, "_future_lyric_color", return_value=unread),
                patch.object(self.window, "_paint_transport_icon", side_effect=inspect_icon),
                QPainter(image) as painter,
            ):
                self.window._paint_transport_spatial_light(
                    painter, QRectF(self.window.rect()), at,
                )
                self.window._paint_media_controls(
                    painter, controls, QColor(80, 60, 45), QColor(60, 45, 35),
                )
            return image, icon_scales

        baseline, baseline_scales = render(50.0)
        hit_rects = dict(self.window._media_button_rects)
        self.assertEqual(len(baseline_scales), 1)
        self.window._start_transport_feedback("previous", now=50.0)
        opening, _ = render(50.0)
        self.assertEqual(opening, baseline)
        _, animated_scales = render(50.035)
        early_at, late_at = 50.80, 51.47
        early, _ = render(early_at)
        late, _ = render(late_at)
        self.assertEqual(self.window._media_button_rects, hit_rects)
        self.assertEqual(len(animated_scales), 1)
        self.assertGreater(animated_scales[0], baseline_scales[0] * 1.1)
        origin = self.window._transport_light_origin()
        self.assertAlmostEqual(origin.x(), 262.0)
        self.assertAlmostEqual(origin.y(), 262.0)
        max_radius = self.window._transport_light_max_radius(origin)
        duration = self.window._transport_feedback["previous"][3]
        self.assertAlmostEqual(duration, 1.72)
        self.assertEqual(self.window._transport_feedback_frame(0.0, duration)[1], 0.0)
        early_frame = self.window._transport_feedback_frame(early_at - 50.0, duration)
        late_frame = self.window._transport_feedback_frame(late_at - 50.0, duration)
        early_crest = round(max_radius * early_frame[1] * 0.79)
        late_crest = round(max_radius * late_frame[1] * 0.79)
        self.assertGreater(late_crest, early_crest + 500)
        near = (round(origin.x()) + early_crest, round(origin.y()))
        far = (round(origin.x()) + late_crest, round(origin.y()))
        self.assertGreater(early.pixelColor(*near).value(), baseline.pixelColor(*near).value() + 20)
        self.assertEqual(early.pixelColor(near[0], 650), baseline.pixelColor(near[0], 650))
        self.assertGreater(late.pixelColor(*far).value(), early.pixelColor(*far).value() + 20)
        reached_edge, edge_scales = render(51.5)
        self.assertAlmostEqual(edge_scales[0], baseline_scales[0], delta=0.01)
        self.assertAlmostEqual(
            origin.x() + max_radius * self.window._transport_feedback_frame(1.5, duration)[1] * 0.79,
            self.window.rect().right(), delta=1.0,
        )
        self.assertGreater(
            reached_edge.pixelColor(self.window.width() - 2, round(origin.y())).value(),
            baseline.pixelColor(self.window.width() - 2, round(origin.y())).value() + 20,
        )
        # The light is clipped to the music surface even in a larger target.
        self.assertEqual(late.pixelColor(1210, 320), background)
        self.assertEqual(late.pixelColor(300, 690), background)
        self.assertLess(self.window._transport_light_dirty_rect(0.0).width(), 10)
        # The cached field is a rotationally symmetric annulus: no solid disc
        # and no full-height vertical beam at its moving leading edge.
        sprite = self.window._cached_transport_light_sprite()
        sprite_center = sprite.width() // 2
        crest = round(sprite.width() * 0.5 * 0.79)
        samples = (
            sprite.pixelColor(sprite_center + crest, sprite_center).alpha(),
            sprite.pixelColor(sprite_center - crest, sprite_center).alpha(),
            sprite.pixelColor(sprite_center, sprite_center + crest).alpha(),
            sprite.pixelColor(sprite_center, sprite_center - crest).alpha(),
        )
        self.assertGreater(min(samples), 68)
        self.assertLess(max(samples), 82)
        self.assertLess(max(samples) - min(samples), 5)
        self.assertEqual(sprite.pixelColor(sprite_center, sprite_center).alpha(), 0)
        self.assertLess(sprite.pixelColor(sprite_center + crest, 1).alpha(), 5)
        self.assertEqual(sprite.pixelColor(sprite_center + round(sprite.width() * 0.5 * 0.65), sprite_center).alpha(), 0)
        self.assertGreater(sprite.pixelColor(sprite_center + round(sprite.width() * 0.5 * 0.72), sprite_center).alpha(), 20)
        self.assertGreater(sprite.pixelColor(sprite_center + round(sprite.width() * 0.5 * 0.88), sprite_center).alpha(), 20)
        self.assertLess(sprite.pixelColor(sprite_center + round(sprite.width() * 0.5 * 0.92), sprite_center).alpha(), 20)
        # Keep a continuous, visibly thick crest without the former broad
        # half-screen wash on colored backgrounds.
        radial_alphas = [sprite.pixelColor(sprite_center + offset, sprite_center).alpha()
                         for offset in range(sprite_center)]
        luminous_offsets = [offset for offset, alpha in enumerate(radial_alphas) if alpha >= 45]
        self.assertGreaterEqual(len(luminous_offsets), 22)
        self.assertEqual(len(luminous_offsets), luminous_offsets[-1] - luminous_offsets[0] + 1)
        self.assertLess(max(radial_alphas), 82)
        self.assertIsNone(self.window._transport_feedback_frame(duration, duration))

    def test_windowed_skip_paints_a_visible_cover_origin_ring(self):
        self.window.resize(960, 540)
        self.window._fullscreen = False
        self.window._reset_control_idle(now=20.0)
        self.window._media_button_rects = {"next": QRectF(260, 430, 80, 54)}
        self.window._start_transport_feedback("next", now=20.0)
        self.assertIn("next", self.window._transport_feedback)
        origin = self.window._transport_light_origin(20.8)
        frame = self.window._transport_feedback_frame(0.8, self.window._transport_feedback["next"][3])
        crest = self.window._transport_light_max_radius(origin, 20.8) * frame[1] * 0.79
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        image.fill(QColor(25, 26, 29))
        with QPainter(image) as painter:
            self.window._paint_transport_spatial_light(painter, QRectF(self.window.rect()), 20.8)
        point = (round(origin.x() + crest), round(origin.y()))
        self.assertGreater(image.pixelColor(*point).value(), 45)
        self.assertEqual(image.pixelColor(round(origin.x()), round(origin.y())), QColor(25, 26, 29))

    def test_automatic_next_repeats_ring_for_thumbnail_then_finishes_after_original(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        self.owner.album_art = QImage(1800, 1800, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor(65, 34, 77))
        self.owner.album_art_source = "original"
        self.owner.media_track_id = "song-a"
        self.window._track_transition(20.0, QColor(65, 34, 77), QColor(30, 25, 45))
        self.assertEqual(self.window._transport_feedback, {})

        self.owner.media_track_id = "song-b"
        self.owner.album_art = QImage(80, 80, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor(70, 35, 80))
        self.owner.album_art_source = "thumbnail"
        self.window._track_transition(21.0, QColor(70, 35, 80), QColor(30, 26, 43))
        self.assertTrue(self.window._art_loading_waiting)
        self.assertIn("art_loading", self.window._transport_feedback)
        self.assertEqual(self.window._transport_feedback["art_loading"][0], 21.0)
        self.window._update_transport_feedback_regions(22.5)
        self.assertEqual(self.window._transport_feedback["art_loading"][0], 22.5)
        self.window._update_transport_feedback_regions(24.0)
        self.assertEqual(self.window._transport_feedback["art_loading"][0], 24.0)

        # The final source replaces the preview without cutting off the
        # currently expanding ring.
        self.owner.album_art = QImage(1800, 1800, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor(80, 44, 85))
        self.owner.album_art_source = "original"
        self.window._track_transition(24.2, QColor(80, 44, 85), QColor(31, 26, 44))
        self.assertFalse(self.window._art_loading_waiting)
        self.assertIn("art_loading", self.window._transport_feedback)
        self.window._update_transport_feedback_regions(25.5)
        self.assertIn("art_loading", self.window._transport_feedback)
        self.window._update_transport_feedback_regions(25.8)
        self.assertNotIn("art_loading", self.window._transport_feedback)

    def test_genuine_obo_adds_one_slow_gold_ring_after_artwork_ring(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        self.owner.music_surface_reading_strategy = "gold"
        self.owner.media_track_id = "first"
        self.owner.track_title = "First"
        self.owner.artist = "Artist"
        self.owner.album_art = QImage(96, 96, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor(61, 39, 80))
        self.owner.album_art_source = "original"
        self.window._track_transition(20.0, QColor(61, 39, 80), QColor(29, 25, 44))

        self.owner.media_track_id = "genuine-obo"
        self.owner.track_title = "Timed song"
        self.owner.album_art_source = "thumbnail"
        self.owner.timed_lyrics = [
            (0.0, "Shine on"), (4.0, "Second line"),
            (8.0, "Third line"), (12.0, "Fourth line"),
        ]
        self.owner.timed_lyric_units = [
            (
                SimpleNamespace(start_seconds=0.0, end_seconds=1.0, text="Shine "),
                SimpleNamespace(start_seconds=1.0, end_seconds=2.0, text="on"),
            ), (), (), (),
        ]
        self.owner.timed_lyric_timing_kinds = ["unit", "line", "line", "line"]
        self.window._track_transition(21.0, QColor(61, 39, 80), QColor(29, 25, 44))
        self.assertIn("art_loading", self.window._transport_feedback)
        self.window._update_transport_feedback_regions(22.5)
        self.assertNotIn("obo_reveal", self.window._transport_feedback)

        self.owner.album_art = QImage(1800, 1800, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor(73, 43, 92))
        self.owner.album_art_source = "original"
        self.window._track_transition(22.6, QColor(73, 43, 92), QColor(29, 25, 44))
        self.window._update_transport_feedback_regions(23.0)
        self.assertIn("art_loading", self.window._transport_feedback)
        self.assertNotIn("obo_reveal", self.window._transport_feedback)
        self.window._update_transport_feedback_regions(24.3)
        self.assertNotIn("art_loading", self.window._transport_feedback)
        self.assertIn("obo_reveal", self.window._transport_feedback)
        gold_start, _origin, _scale, gold_duration = self.window._transport_feedback["obo_reveal"]
        self.assertEqual(gold_start, 24.3)
        self.assertGreater(gold_duration, self.window.TRANSPORT_LIGHT_EXPAND_SECONDS + self.window.TRANSPORT_LIGHT_FADE_SECONDS)

        ordinary = self.window._cached_transport_light_sprite()
        gold = self.window._cached_transport_light_sprite(gold=True)
        center = ordinary.width() // 2
        ordinary_width = sum(ordinary.pixelColor(center + x, center).alpha() >= 45 for x in range(center))
        gold_width = sum(gold.pixelColor(center + x, center).alpha() >= 45 for x in range(center))
        self.assertGreaterEqual(gold_width, ordinary_width * 2)
        crest = gold.pixelColor(center + round(center * 0.79), center)
        self.assertGreater(crest.red(), crest.blue() + 35)
        midpoint = gold_start + gold_duration * 0.5
        frame = self.window._transport_feedback_frame(midpoint - gold_start, gold_duration)
        origin = self.window._transport_light_origin(midpoint)
        crest_x = round(origin.x() + self.window._transport_light_max_radius(origin, midpoint) * frame[1] * 0.79)
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        image.fill(QColor(24, 24, 24))
        with QPainter(image) as painter:
            self.window._paint_transport_spatial_light(painter, QRectF(self.window.rect()), midpoint)
        lit = image.pixelColor(crest_x, round(origin.y()))
        self.assertGreater(lit.red(), lit.blue() + 12)
        self.assertGreater(lit.red(), 45)

        self.window._update_transport_feedback_regions(gold_start + gold_duration + 0.01)
        self.assertNotIn("obo_reveal", self.window._transport_feedback)
        self.window._update_transport_feedback_regions(gold_start + gold_duration + 0.2)
        self.assertNotIn("obo_reveal", self.window._transport_feedback)

    def test_low_perception_never_starts_obo_gold_ring(self):
        self.window.resize(1190, 666)
        self.owner.music_surface_reading_strategy = "low_perception"
        self.owner.media_track_id = "timed-song"
        self.owner.track_title = "Timed song"
        self.owner.artist = "Artist"
        self.owner.album_art = QImage(1800, 1800, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor(73, 43, 92))
        self.owner.album_art_source = "original"
        self.owner.timed_lyrics = [(0.0, "Shine on"), (4.0, "Next line")]
        self.owner.timed_lyric_units = [(
            SimpleNamespace(start_seconds=0.0, end_seconds=1.0, text="Shine "),
            SimpleNamespace(start_seconds=1.0, end_seconds=2.0, text="on"),
        ), ()]
        self.owner.timed_lyric_timing_kinds = ["unit", "line"]
        self.window._track_transition(20.0, QColor(73, 43, 92), QColor(29, 25, 44))
        self.window._update_transport_feedback_regions(21.0)
        self.assertNotIn("obo_reveal", self.window._transport_feedback)

    def test_new_track_cancels_an_active_obo_gold_ring(self):
        self.window.resize(1190, 666)
        self.owner.music_surface_reading_strategy = "gold"
        self.owner.media_track_id = "timed-song"
        self.owner.track_title = "Timed song"
        self.owner.artist = "Artist"
        self.owner.album_art = QImage(1800, 1800, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor(73, 43, 92))
        self.owner.album_art_source = "original"
        self.owner.timed_lyrics = [(0.0, "Shine on"), (4.0, "Next line")]
        self.owner.timed_lyric_units = [(
            SimpleNamespace(start_seconds=0.0, end_seconds=1.0, text="Shine "),
            SimpleNamespace(start_seconds=1.0, end_seconds=2.0, text="on"),
        ), ()]
        self.owner.timed_lyric_timing_kinds = ["unit", "line"]
        self.window._track_transition(20.0, QColor(73, 43, 92), QColor(29, 25, 44))
        self.window._update_transport_feedback_regions(20.1)
        self.assertIn("obo_reveal", self.window._transport_feedback)

        self.owner.media_track_id = "following-song"
        self.owner.track_title = "Following song"
        self.owner.timed_lyrics = [(0.0, "One line"), (4.0, "Another line")]
        self.owner.timed_lyric_units = [(), ()]
        self.owner.timed_lyric_timing_kinds = ["line", "line"]
        self.window._track_transition(20.2, QColor(73, 43, 92), QColor(29, 25, 44))
        self.assertNotIn("obo_reveal", self.window._transport_feedback)
        self.window._update_transport_feedback_regions(22.5)
        self.assertNotIn("obo_reveal", self.window._transport_feedback)

    def test_first_open_with_thumbnail_waits_for_a_changed_displayed_image(self):
        self.window.resize(960, 540)
        self.owner.track_title = "Loading album"
        self.owner.artist = "Artist"
        self.owner.media_track_id = "song"
        self.owner.album_art = QImage(90, 90, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor(62, 37, 82))
        self.owner.album_art_signature = "preview"
        self.owner.album_art_source = "thumbnail"
        self.window._track_transition(10.0, QColor(62, 37, 82), QColor(33, 23, 46))
        self.assertTrue(self.window._art_loading_waiting)
        self.assertIn("art_loading", self.window._transport_feedback)

        # A provider label can update ahead of the actual displayed bytes.
        self.owner.album_art_source = "original"
        self.owner.album_art_signature = "new-label-only"
        self.window._track_transition(10.2, QColor(62, 37, 82), QColor(33, 23, 46))
        self.assertTrue(self.window._art_loading_waiting)
        self.owner.album_art = QImage(600, 600, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor(73, 42, 90))
        self.owner.album_art_signature = "original"
        self.window._track_transition(10.4, QColor(73, 42, 90), QColor(33, 23, 46))
        self.assertFalse(self.window._art_loading_waiting)

    def test_preview_cover_blurs_once_and_original_uses_sharp_raster(self):
        image = QImage(120, 120, QImage.Format.Format_RGB32)
        image.fill(QColor(20, 20, 20))
        with QPainter(image) as painter:
            painter.fillRect(QRect(60, 0, 60, 120), QColor(240, 240, 240))
        blurred = self.window._blurred_preview_cover(image)
        self.assertEqual(self.window._blurred_preview_cover(image).cacheKey(), blurred.cacheKey())
        self.assertGreater(blurred.pixelColor(58, 60).value(), image.pixelColor(58, 60).value())
        self.assertLess(blurred.pixelColor(61, 60).value(), image.pixelColor(61, 60).value())

        self.window.resize(1920, 1080)
        self.owner.track_title = "First"
        self.owner.artist = "Artist"
        self.owner.media_track_id = "first"
        self.owner.album_art = image
        self.owner.album_art_source = "original"
        target = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch("local.music_surface_window.time.monotonic", return_value=20.0):
            self.window.render(target)
        self.owner.track_title = "Second"
        self.owner.media_track_id = "second"
        self.owner.album_art_source = "thumbnail"
        with (
            patch("local.music_surface_window.time.monotonic", return_value=21.0),
            patch.object(self.window, "_blurred_preview_cover", wraps=self.window._blurred_preview_cover) as blur,
        ):
            self.window.render(target)
            self.assertGreater(blur.call_count, 0)
            self.assertLessEqual(self.window._cover_cache.width(), 384)
        replacement = QImage(1200, 1200, QImage.Format.Format_RGB32)
        replacement.fill(QColor(21, 21, 21))
        self.owner.album_art = replacement
        self.owner.album_art_source = "original"
        with (
            patch("local.music_surface_window.time.monotonic", return_value=21.2),
            patch.object(self.window, "_blurred_preview_cover", wraps=self.window._blurred_preview_cover) as blur,
        ):
            self.window.render(target)
            blur.assert_not_called()
            self.assertGreater(self.window._cover_cache.width(), 384)

    def test_spatial_light_stays_behind_cover_and_text_without_changing_background_cache(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        self.owner.timed_lyrics = [
            (0.0, "Current lyric"), (4.0, "Next lyric"),
            (8.0, "Third lyric"), (12.0, "Fourth lyric"),
        ]
        self.window._reset_control_idle(now=50.0)
        art = QImage(500, 500, QImage.Format.Format_RGB32)
        art.fill(QColor(161, 36, 68))
        self.owner.album_art = art
        self.owner.track_title = "LIGHT"
        self.owner.artist = "Artist"
        self.owner.album_color = QColor(161, 36, 68)
        self.owner.secondary_color = QColor(62, 31, 72)

        def render(at):
            image = QImage(self.window.size(), QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.black)
            with patch("local.music_surface_window.time.monotonic", return_value=at):
                self.window.render(image)
            return image

        render(50.0)
        self.window._start_transport_feedback("previous", now=50.0)
        at = 50.80
        feedback = self.window._transport_feedback.pop("previous")
        baseline = render(at)
        self.window._transport_feedback["previous"] = feedback
        cached_background = QImage(self.window._background_cache)
        lit = render(at)
        self.assertEqual(lit.pixelColor(262, 262), QColor(161, 36, 68))
        self.assertEqual(lit.pixelColor(262, 262), baseline.pixelColor(262, 262))
        origin = self.window._transport_light_origin()
        frame = self.window._transport_feedback_frame(at - 50.0, feedback[3])
        ring_x = round(origin.x() + self.window._transport_light_max_radius(origin) * frame[1] * 0.79)
        ring_y = round(origin.y())
        self.assertGreater(lit.pixelColor(ring_x, ring_y).value(), baseline.pixelColor(ring_x, ring_y).value())
        self.assertEqual(self.window._background_cache, cached_background)

        def title_ink(image):
            return {
                (x, y) for y in range(470, 515) for x in range(62, 300)
                if image.pixelColor(x, y).value() > 235
                and image.pixelColor(x, y).saturation() < 35
            }

        ink_before, ink_after = title_ink(baseline), title_ink(lit)
        self.assertGreater(len(ink_before), 80)
        # Native additive light changes the backdrop of antialiased edge
        # pixels. Every opaque title interior must keep the same white ink.
        interior = {
            (x, y) for x, y in ink_before
            if all((x + dx, y + dy) in ink_before for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)))
        }
        self.assertGreater(len(interior), 40)
        self.assertTrue(interior.issubset(ink_after))

    def test_accelerating_wave_reaches_right_edge_within_two_seconds_on_high_dpi(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        self.owner.timed_lyrics = [(0.0, "Current lyric"), (4.0, "Next lyric")]
        self.window._reset_control_idle(now=50.0)
        self.window._media_button_rects = {"next": QRectF(300, 540, 100, 60)}
        self.window._start_transport_feedback("next", now=50.0)
        duration = self.window._transport_feedback["next"][3]
        key_times = (0.0, 0.375, 0.75, 1.125, 1.5)
        frames = [self.window._transport_feedback_frame(at, duration) for at in key_times]
        self.assertEqual([round(frame[1], 4) for frame in frames], [0.0, 0.1375, 0.35, 0.6375, 1.0])
        advances = [frames[index + 1][1] - frames[index][1] for index in range(4)]
        self.assertEqual([round(step, 4) for step in advances], [0.1375, 0.2125, 0.2875, 0.3625])
        self.assertLess(self.window._transport_feedback_frame(0.5, duration)[1], 0.25)
        self.assertGreater(self.window._transport_feedback_frame(1.0, duration)[1], 0.5)
        self.assertAlmostEqual(frames[-1][0], 1.0)
        self.assertGreater(frames[-1][2], 0.75)
        self.assertEqual(self.window._transport_feedback_frame(1.6, duration)[1], 1.0)
        self.assertLess(self.window._transport_feedback_frame(1.6, duration)[2], frames[-1][2])

        # The light is a background effect, so a control hide cannot end a
        # ripple that was already started by a visible previous/next button.
        self.window._control_reveal_from = 0.0
        self.window._control_reveal_target = 0.0
        image = QImage(2380, 1332, QImage.Format.Format_RGB32)
        image.setDevicePixelRatio(2.0)
        background = QColor(20, 20, 20)
        image.fill(background)
        with QPainter(image) as painter:
            self.window._paint_transport_spatial_light(painter, QRectF(self.window.rect()), 51.5)
        origin = self.window._transport_light_origin(51.5)
        edge_x = 2 * (self.window.width() - 2)
        edge_y = 2 * round(origin.y())
        self.assertGreater(image.pixelColor(edge_x, edge_y).value(), background.value() + 20)
        self.assertEqual(image.pixelColor(2 * round(origin.x()), edge_y), background)

    def test_ring_remains_visible_on_bright_background_and_renders_before_metadata(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        self.window._reset_control_idle(now=50.0)
        self.owner.track_title = "A bright album"
        self.owner.artist = "Artist"
        self.owner.timed_lyrics = [(float(index * 4), f"Line {index}") for index in range(6)]
        self.window._media_button_rects = {"next": QRectF(300, 540, 100, 60)}
        self.window._start_transport_feedback("next", now=50.0)
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        backdrop = QColor(195, 175, 208)
        image.fill(backdrop)
        with QPainter(image) as painter:
            self.window._paint_transport_spatial_light(painter, QRectF(self.window.rect()), 50.9)
        origin = self.window._transport_light_origin(50.9)
        frame = self.window._transport_feedback_frame(0.9, self.window._transport_feedback["next"][3])
        crest = self.window._transport_light_max_radius(origin, 50.9) * frame[1] * 0.79
        self.assertGreater(image.pixelColor(round(origin.x() + crest), round(origin.y())).value(), backdrop.value() + 35)
        self.assertEqual(image.pixelColor(round(origin.x()), round(origin.y())), backdrop)

        order = []
        ring = self.window._paint_transport_spatial_light
        metadata = self.window._paint_scrolling_metadata
        def paint_ring(*args):
            order.append("ring")
            return ring(*args)
        def paint_metadata(*args, **kwargs):
            order.append("metadata")
            return metadata(*args, **kwargs)
        with (
            patch.object(self.window, "_paint_transport_spatial_light", side_effect=paint_ring),
            patch.object(self.window, "_paint_scrolling_metadata", side_effect=paint_metadata),
            patch("local.music_surface_window.time.monotonic", return_value=50.9),
        ):
            self.window.render(image)
        self.assertIn("metadata", order)
        self.assertLess(order.index("ring"), order.index("metadata"))

    def test_centered_no_lyrics_cover_reveals_a_circular_ring_in_visible_space(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        self.window._reset_control_idle(now=50.0)
        self.owner.track_title = "No lyrics"
        self.owner.artist = "Artist"
        self.owner.timed_lyrics = []
        self.owner.album_color = QColor(105, 54, 83)
        self.owner.secondary_color = QColor(47, 40, 63)
        self.owner.album_art = QImage(500, 500, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor(156, 77, 91))
        self.window._cover_layout_from = 1.0
        self.window._cover_layout_to = 1.0
        self.window._cover_layout_started_at = 50.0

        def render(at):
            image = QImage(self.window.size(), QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.black)
            with patch("local.music_surface_window.time.monotonic", return_value=at):
                self.window.render(image)
            return image

        render(50.0)
        self.window._start_transport_feedback("next", now=50.0)
        at = 51.2
        lit = render(at)
        feedback = self.window._transport_feedback.pop("next")
        baseline = render(at)
        self.window._transport_feedback["next"] = feedback
        origin = self.window._transport_light_origin(at)
        cover = self.window._current_cover_rect(at)
        frame = self.window._transport_feedback_frame(at - 50.0, feedback[3])
        crest = self.window._transport_light_max_radius(origin, at) * frame[1] * 0.79
        self.assertGreater(crest, math.hypot(cover.width(), cover.height()) * 0.5)
        bright_points = []
        for degrees in range(0, 360, 5):
            angle = math.radians(degrees)
            x = round(origin.x() + crest * math.cos(angle))
            y = round(origin.y() + crest * math.sin(angle))
            if not (0 <= x < lit.width() and 0 <= y < lit.height()):
                continue
            if lit.pixelColor(x, y).value() > baseline.pixelColor(x, y).value() + 10:
                bright_points.append((x, y))
        # The moved artwork sits high in the viewport, so some upper ring
        # samples are correctly clipped by the window. Visible samples must
        # still span both axes rather than forming a vertical light streak.
        self.assertGreaterEqual(len(bright_points), 5)
        self.assertGreater(max(x for x, _y in bright_points) - min(x for x, _y in bright_points), 90)
        self.assertGreater(max(y for _x, y in bright_points) - min(y for _x, y in bright_points), 90)
        center = (round(origin.x()), round(origin.y()))
        self.assertEqual(lit.pixelColor(*center), baseline.pixelColor(*center))

    def test_fullscreen_skip_feedback_survives_track_change_and_restarts_on_rapid_click(self):
        self.window.resize(960, 540)
        self.window._fullscreen = True
        self.window._reset_control_idle(now=20.0)
        self.owner.album_art = QImage(1800, 1800, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(QColor(80, 50, 75))
        self.owner.album_art_source = "original"
        previous = QRectF(100, 100, 100, 60)
        next_button = QRectF(310, 100, 100, 60)
        self.window._media_button_rects = {"previous": previous, "next": next_button}
        self.window._lyric_hit_rects = {}
        self.window._button_rects = {}
        sent = []
        self.owner._send_playback_command_async = sent.append

        def click(command, at):
            center = self.window._media_button_rects[command].center()
            with patch("local.music_surface_window.time.monotonic", return_value=at):
                self.window.mousePressEvent(_PointerEvent(center))
                self.window.mouseReleaseEvent(_PointerEvent(center))

        click("previous", 20.0)
        self.assertEqual(sent, ["previous"])
        self.assertEqual(self.window._transport_feedback["previous"][0], 20.0)
        self.owner.track_title = "next track"
        self.window._track_transition(20.04, QColor(60, 40, 20), QColor(20, 40, 60))
        self.assertIn("previous", self.window._transport_feedback)

        click("previous", 20.08)
        click("next", 20.12)
        self.assertEqual(sent, ["previous", "previous", "next"])
        self.assertEqual(self.window._transport_feedback["previous"][0], 20.08)
        self.assertEqual(self.window._transport_feedback["next"][0], 20.12)
        with patch.object(self.window, "update") as update:
            self.window._update_transport_feedback_regions(20.16)
        self.assertEqual(update.call_count, 4)
        self.assertTrue(all(call.args and isinstance(call.args[0], (QRect, QRegion)) for call in update.call_args_list))
        self.assertTrue(any(
            QRegion(call.args[0]).boundingRect().width() < self.window.width()
            for call in update.call_args_list
        ))
        self.window._update_transport_feedback_regions(22.5)
        self.assertEqual(self.window._transport_feedback, {})

        self.owner.animation_speed = 2.0
        self.window._start_transport_feedback("next", now=22.55)
        self.assertAlmostEqual(self.window._transport_feedback["next"][3], 0.86)
        self.window._transport_feedback.clear()

        self.window._fullscreen = False
        self.window._reset_control_idle(now=22.6)
        self.window._start_transport_feedback("previous", now=22.6)
        self.assertIn("previous", self.window._transport_feedback)
        self.window._transport_feedback.clear()
        self.window._fullscreen = True
        self.window._start_transport_feedback("toggle_play_pause", now=22.6)
        self.assertEqual(self.window._transport_feedback, {})

    def test_restarting_fullscreen_skip_clears_the_previous_large_ring_extent(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        self.owner.timed_lyrics = [(0.0, "Current lyric"), (4.0, "Next lyric")]
        self.window._reset_control_idle(now=50.0)
        self.window._media_button_rects = {"previous": QRectF(90, 540, 100, 60)}
        self.window._start_transport_feedback("previous", now=50.0)
        origin = self.window._transport_light_origin()
        old_frame = self.window._transport_feedback_frame(
            1.5, self.window._transport_feedback["previous"][3],
        )
        old_crest = self.window._transport_light_max_radius(origin) * old_frame[1] * 0.70
        old_point = QPoint(round(origin.x() + old_crest), round(origin.y()))
        self.assertFalse(self.window._transport_light_dirty_rect(0.0).contains(old_point))
        with patch.object(self.window, "update") as update:
            self.window._start_transport_feedback("previous", now=51.5)
        dirty_regions = [call.args[0] for call in update.call_args_list]
        self.assertTrue(any(region.contains(old_point) for region in dirty_regions))
        self.assertEqual(self.window._transport_feedback["previous"][0], 51.5)

    def test_fullscreen_ring_follows_moving_cover_and_clears_its_previous_source(self):
        self.window.resize(1500, 1000)
        self.window._fullscreen = True
        self.window._reset_control_idle(now=50.0)
        self.window._media_button_rects = {"next": QRectF(350, 850, 100, 60)}
        with patch.object(self.window, "_current_cover_rect", return_value=QRectF(50, 50, 300, 300)):
            self.window._start_transport_feedback("next", now=50.0)
            previous_dirty = QRect(self.window._transport_light_dirty_regions["next"])
        moved_cover = QRectF(650, 300, 500, 500)
        with (
            patch.object(self.window, "_current_cover_rect", return_value=moved_cover),
            patch.object(self.window, "update") as update,
        ):
            self.assertEqual(self.window._transport_light_origin(50.01), moved_cover.center())
            self.window._update_transport_feedback_regions(50.01)
        ring_dirty = update.call_args_list[0].args[0]
        self.assertTrue(ring_dirty.contains(previous_dirty))
        self.assertTrue(ring_dirty.contains(self.window._transport_light_dirty_regions["next"]))

    def test_transport_icons_remain_visible_at_minimum_window_scale(self):
        self.owner.album_color = QColor(255, 255, 255)
        self.owner.secondary_color = QColor(32, 32, 32)
        self.owner.media_is_playing = True
        self.window.resize(595, 333)
        background = QColor(28, 28, 28)
        image = QImage(200, 50, QImage.Format.Format_RGB32)
        image.fill(background)
        with QPainter(image) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.window._paint_media_controls(
                painter, QRectF(8.0, 8.0, 175.0, 30.0),
                self.owner.album_color, self.owner.secondary_color,
            )
        self.assertEqual(len(self.window._media_button_rects), 3)
        for button in self.window._media_button_rects.values():
            self.assertGreaterEqual(button.width(), 50.0)
            center = button.center()
            ink = sum(
                image.pixelColor(x, y) != background
                for y in range(round(center.y()) - 11, round(center.y()) + 12)
                for x in range(round(center.x()) - 11, round(center.x()) + 12)
            )
            self.assertGreater(ink, 60)

    def test_top_right_control_is_one_restrained_mutually_exclusive_pill(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = False
        self.window._sync_window_control_rects()
        self.assertEqual(set(self.window._button_rects), {"return", "translation"})
        windowed_rect = self.window._button_rects["return"]
        self.assertEqual((windowed_rect.width(), windowed_rect.height()), (68.0, 34.0))

        self.window.resize(595, 333)
        self.window._sync_window_control_rects()
        half_rect = self.window._button_rects["return"]
        self.assertAlmostEqual(half_rect.width(), 34.0)
        self.assertAlmostEqual(half_rect.height(), 17.0)

        self.window._fullscreen = True
        self.window._sync_window_control_rects()
        self.assertEqual(set(self.window._button_rects), {"mode", "translation"})
        self.assertEqual(self.window._button_rects["mode"], half_rect)

    def test_fullscreen_controls_hide_after_five_seconds_and_restore_on_pointer_move(self):
        self.window._fullscreen = True
        self.window._reset_control_idle(100.0)
        self.assertEqual(self.window._control_reveal_at(104.999), 1.0)
        self.assertEqual(self.window._control_reveal_at(105.0), 1.0)
        midway = self.window._control_reveal_at(105.26)
        self.assertGreater(midway, 0.0)
        self.assertLess(midway, 1.0)
        self.assertEqual(self.window._control_reveal_at(105.52), 0.0)

        with patch("local.music_surface_window.time.monotonic", return_value=105.52):
            self.window._lyric_hit_rects = {0: QRectF(0, 0, 40, 40)}
            self.window.mouseMoveEvent(_PointerEvent(QPointF(20, 20)))
        recovering = self.window._control_reveal_at(105.78)
        self.assertGreater(recovering, 0.0)
        self.assertLess(recovering, 1.0)
        self.assertEqual(self.window._control_reveal_at(106.04), 1.0)
        self.assertEqual(self.window._control_reveal_at(110.519), 1.0)
        self.assertEqual(self.window._control_reveal_at(111.04), 0.0)

    def test_fullscreen_idle_cursor_hides_with_controls_and_restores_hover_cursor(self):
        self.window._fullscreen = True
        self.window._reset_control_idle(100.0)
        self.window.setCursor(Qt.CursorShape.PointingHandCursor)
        with (
            patch.object(self.window, "isVisible", return_value=True),
            patch.object(self.window, "isActiveWindow", return_value=True),
            patch.object(self.window, "underMouse", return_value=True),
            patch.object(QApplication, "setOverrideCursor") as global_hide,
            patch.object(QApplication, "restoreOverrideCursor") as global_restore,
        ):
            self.assertEqual(self.window._control_reveal_at(104.999), 1.0)
            self.assertEqual(self.window.cursor().shape(), Qt.CursorShape.PointingHandCursor)
            # Hide the system pointer as soon as the controls begin exiting,
            # rather than waiting for their final invisible animation frame.
            self.assertEqual(self.window._control_reveal_at(105.0), 1.0)
            self.assertEqual(self.window.cursor().shape(), Qt.CursorShape.BlankCursor)
            self.assertEqual(self.window._control_reveal_at(105.52), 0.0)
            self.window._lyric_hit_rects = {0: QRectF(0, 0, 40, 40)}
            with patch("local.music_surface_window.time.monotonic", return_value=105.52):
                self.window.mouseMoveEvent(_PointerEvent(QPointF(20, 20)))
            self.assertEqual(self.window.cursor().shape(), Qt.CursorShape.PointingHandCursor)
            self.assertFalse(self.window._idle_cursor_hidden)
            self.assertEqual(self.window._control_reveal_at(106.04), 1.0)
            global_hide.assert_not_called()
            global_restore.assert_not_called()

    def test_fullscreen_idle_cursor_restores_the_default_cursor(self):
        self.window._fullscreen = True
        self.window._reset_control_idle(100.0)
        self.window.unsetCursor()
        self.assertFalse(self.window.testAttribute(Qt.WidgetAttribute.WA_SetCursor))
        with (
            patch.object(self.window, "isVisible", return_value=True),
            patch.object(self.window, "isActiveWindow", return_value=True),
            patch.object(self.window, "underMouse", return_value=True),
        ):
            self.window._control_reveal_at(105.52)
            self.assertEqual(self.window.cursor().shape(), Qt.CursorShape.BlankCursor)
            self.assertIsNone(self.window._idle_cursor_restore_cursor)
            self.window._note_pointer_moved(106.0)
        # Qt resets a top-level widget's custom cursor to ArrowCursor; unlike
        # a child widget, it may keep WA_SetCursor after unsetCursor().
        self.assertEqual(self.window.cursor().shape(), Qt.CursorShape.ArrowCursor)

    def test_idle_cursor_is_scoped_to_the_active_fullscreen_surface(self):
        cases = (
            (False, True, True, True, None, False),
            (True, False, True, True, None, False),
            (True, True, False, True, None, False),
            (True, True, True, False, None, False),
            (True, True, True, True, False, False),
            (True, True, True, True, None, True),
        )
        for fullscreen, visible, active, pointer_inside, pending, returning in cases:
            with self.subTest(fullscreen=fullscreen, visible=visible, active=active,
                              pointer_inside=pointer_inside, pending=pending, returning=returning):
                self.window._reset_control_idle(100.0)
                self.window._fullscreen = fullscreen
                self.window._pending_fullscreen = pending
                self.window._island_returning = returning
                self.window.setCursor(Qt.CursorShape.CrossCursor)
                with (
                    patch.object(self.window, "isVisible", return_value=visible),
                    patch.object(self.window, "isActiveWindow", return_value=active),
                    patch.object(self.window, "underMouse", return_value=pointer_inside),
                ):
                    self.window._control_reveal_at(105.52)
                self.assertEqual(self.window.cursor().shape(), Qt.CursorShape.CrossCursor)
                self.assertFalse(self.window._idle_cursor_hidden)

    def test_hidden_cursor_reveals_on_click_without_activating_stale_controls(self):
        self.window._fullscreen = True
        self.window._reset_control_idle(100.0)
        self.window._media_button_rects = {"next": QRectF(280, 580, 110, 60)}
        with (
            patch.object(self.window, "isVisible", return_value=True),
            patch.object(self.window, "isActiveWindow", return_value=True),
            patch.object(self.window, "underMouse", return_value=True),
            patch("local.music_surface_window.time.monotonic", return_value=105.6),
            patch.object(self.window, "_send_media_command") as send_command,
        ):
            self.window._control_reveal_at(105.6)
            self.assertEqual(self.window.cursor().shape(), Qt.CursorShape.BlankCursor)
            self.window.mousePressEvent(_PointerEvent(QPointF(330, 600)))
            self.window.mouseReleaseEvent(_PointerEvent(QPointF(330, 600)))
            self.assertNotEqual(self.window.cursor().shape(), Qt.CursorShape.BlankCursor)
            self.assertEqual(self.window._control_reveal_target, 1.0)
            send_command.assert_not_called()

    def test_fullscreen_idle_cursor_reveals_on_wheel_key_and_pointer_enter(self):
        point = QPointF(20.0, 20.0)
        cases = (
            ("wheel", lambda: self.window.wheelEvent(QWheelEvent(
                point, point, QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
            ))),
            ("key", lambda: self.window.keyPressEvent(QKeyEvent(
                QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier,
            ))),
            ("enter", lambda: self.window.enterEvent(QEnterEvent(point, point, point))),
            ("focus-in", lambda: self.window.focusInEvent(QFocusEvent(QEvent.Type.FocusIn))),
        )
        with (
            patch.object(self.window, "isVisible", return_value=True),
            patch.object(self.window, "isActiveWindow", return_value=True),
            patch.object(self.window, "underMouse", return_value=True),
            patch("local.music_surface_window.time.monotonic", return_value=105.6),
        ):
            for name, interact in cases:
                with self.subTest(interaction=name):
                    self.window._fullscreen = True
                    self.window._reset_control_idle(100.0)
                    self.window._control_reveal_at(105.6)
                    self.assertEqual(self.window.cursor().shape(), Qt.CursorShape.BlankCursor)
                    interact()
                    self.assertFalse(self.window._idle_cursor_hidden)
                    self.assertNotEqual(self.window.cursor().shape(), Qt.CursorShape.BlankCursor)
                    self.assertEqual(self.window._control_reveal_target, 1.0)

    def test_fullscreen_idle_cursor_restores_on_leave_deactivation_and_hide(self):
        cases = (
            ("leave", lambda: self.window.leaveEvent(QEvent(QEvent.Type.Leave))),
            ("focus-out", lambda: self.window.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))),
            ("deactivate", lambda: self.window.changeEvent(QEvent(QEvent.Type.WindowDeactivate))),
            ("hide", lambda: self.window.hideEvent(QHideEvent())),
        )
        with (
            patch.object(self.window, "isVisible", return_value=True),
            patch.object(self.window, "isActiveWindow", return_value=True),
            patch.object(self.window, "underMouse", return_value=True),
        ):
            for name, exit_surface in cases:
                with self.subTest(exit=name):
                    self.window._fullscreen = True
                    self.window._reset_control_idle(100.0)
                    self.window._control_reveal_at(105.6)
                    self.assertEqual(self.window.cursor().shape(), Qt.CursorShape.BlankCursor)
                    exit_surface()
                    self.assertFalse(self.window._idle_cursor_hidden)
                    self.assertNotEqual(self.window.cursor().shape(), Qt.CursorShape.BlankCursor)

    def test_fullscreen_idle_cursor_restores_before_mode_and_island_transitions(self):
        with (
            patch.object(self.window, "isVisible", return_value=True),
            patch.object(self.window, "isActiveWindow", return_value=True),
            patch.object(self.window, "underMouse", return_value=True),
            patch.object(self.window, "_capture_transition_snapshot", return_value=QImage()),
        ):
            for name, transition in (("windowed", self.window.toggle_presentation_mode),
                                     ("island", self.window.return_to_island)):
                with self.subTest(transition=name):
                    self.window._cancel_mode_transition()
                    self.window._fullscreen = True
                    self.window._reset_control_idle(100.0)
                    self.window._control_reveal_at(105.6)
                    self.assertEqual(self.window.cursor().shape(), Qt.CursorShape.BlankCursor)
                    transition()
                    self.assertFalse(self.window._idle_cursor_hidden)
                    self.assertNotEqual(self.window.cursor().shape(), Qt.CursorShape.BlankCursor)

    def test_idle_cursor_stays_visible_through_real_transition_snapshot_paint(self):
        self.window.resize(960, 540)
        self.window._normal_geometry = QRect(100, 90, 640, 360)
        self.window._island_origin_geometry = QRect(100, 10, 240, 64)
        with (
            patch.object(self.window, "isVisible", return_value=True),
            patch.object(self.window, "isActiveWindow", return_value=True),
            patch.object(self.window, "underMouse", return_value=True),
            # Keep native show events from resetting the idle state and
            # masking a cursor re-hide during grab()'s real repaint.
            patch.object(self.window, "showNormal"),
            patch("local.music_surface_window.time.monotonic", return_value=105.6),
            patch.object(self.window, "_capture_transition_snapshot",
                         wraps=self.window._capture_transition_snapshot) as capture,
        ):
            for name, transition in (("windowed", self.window.toggle_presentation_mode),
                                     ("island", self.window.return_to_island)):
                with self.subTest(transition=name):
                    self.window._cancel_mode_transition()
                    self.window._fullscreen = True
                    self.window._reset_control_idle(100.0)
                    self.window._control_reveal_at(105.6)
                    self.assertEqual(self.window.cursor().shape(), Qt.CursorShape.BlankCursor)
                    transition()
                    self.assertFalse(self.window._geometry_transition_snapshot.isNull())
                    self.assertFalse(self.window._capturing_transition_snapshot)
                    self.assertFalse(self.window._idle_cursor_hidden)
                    self.assertNotEqual(self.window.cursor().shape(), Qt.CursorShape.BlankCursor)
            self.assertEqual(capture.call_count, 2)

    def test_idle_cursor_shutdown_preserves_other_widgets_and_global_override_stack(self):
        other = QWidget()
        other.setCursor(Qt.CursorShape.CrossCursor)
        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        try:
            self.window._fullscreen = True
            self.window._reset_control_idle(100.0)
            with (
                patch.object(self.window, "isVisible", return_value=True),
                patch.object(self.window, "isActiveWindow", return_value=True),
                patch.object(self.window, "underMouse", return_value=True),
            ):
                self.window._control_reveal_at(105.6)
                self.assertEqual(self.window.cursor().shape(), Qt.CursorShape.BlankCursor)
                self.assertEqual(other.cursor().shape(), Qt.CursorShape.CrossCursor)
                self.assertEqual(QApplication.overrideCursor().shape(), Qt.CursorShape.WaitCursor)
                self.window.shutdown()
                self.assertFalse(self.window._idle_cursor_hidden)
                self.assertNotEqual(self.window.cursor().shape(), Qt.CursorShape.BlankCursor)
                self.assertEqual(other.cursor().shape(), Qt.CursorShape.CrossCursor)
                self.assertEqual(QApplication.overrideCursor().shape(), Qt.CursorShape.WaitCursor)
        finally:
            QApplication.restoreOverrideCursor()
            other.close()

    def test_windowed_controls_hide_after_two_seconds_and_only_focus_reveals_them(self):
        self.window._fullscreen = False
        self.window._reset_control_idle(100.0)
        self.assertEqual(self.window._control_reveal_at(101.999), 1.0)
        self.assertLess(self.window._control_reveal_at(102.26), 1.0)
        self.assertEqual(self.window._control_reveal_at(102.52), 0.0)
        with patch.object(self.window, "isActiveWindow", return_value=False):
            self.window._note_pointer_moved(103.0)
        self.assertEqual(self.window._control_reveal_at(103.0), 0.0)
        with patch.object(self.window, "isActiveWindow", return_value=True):
            self.window._note_pointer_moved(103.0)
        self.assertGreater(self.window._control_reveal_at(103.26), 0.0)
        self.assertEqual(self.window._control_reveal_at(103.52), 1.0)
        self.assertEqual(self.window._control_reveal_at(104.999), 1.0)
        self.assertEqual(self.window._control_reveal_at(105.52), 0.0)

    def test_window_focus_restores_windowed_controls_without_pointer_motion(self):
        self.window._reset_control_idle(100.0)
        self.assertEqual(self.window._control_reveal_at(102.52), 0.0)
        event = QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.ActiveWindowFocusReason)
        with (
            patch("local.music_surface_window.time.monotonic", return_value=103.0),
            patch.object(self.window, "isActiveWindow", return_value=True),
        ):
            self.window.focusInEvent(event)
        self.assertGreater(self.window._control_reveal_at(103.26), 0.0)
        self.assertEqual(self.window._control_reveal_at(103.52), 1.0)

    def test_windowed_control_hit_targets_vanish_during_hide(self):
        self.window.resize(960, 540)
        self.window._reset_control_idle(100.0)
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch("local.music_surface_window.time.monotonic", return_value=101.0):
            self.window.render(output)
        self.assertIn("return", self.window._button_rects)
        self.assertEqual(len(self.window._media_button_rects), 3)
        with patch("local.music_surface_window.time.monotonic", return_value=102.26):
            self.window.render(output)
        self.assertEqual(self.window._button_rects, {})
        self.assertEqual(self.window._media_button_rects, {})

    def test_paused_windowed_controls_prewarm_locally_and_hide_on_time(self):
        self.owner.media_is_playing = False
        self.window.resize(960, 540)
        self.window._reset_control_idle(100.0)
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window.render(output)
        for index in range(2 * (len(self.window.CONTROL_BLUR_FRACTIONS) - 1)):
            instant = 100.4 + index * 0.05
            self.window._lyric_spring_active = False
            self.window._lyric_animation_rect = QRect()
            self.window._last_full_animation_update_at = instant
            with patch("local.music_surface_window.time.monotonic", return_value=instant):
                with patch.object(self.window, "update") as update:
                    self.window._on_animation_tick()
                self.assertTrue(update.call_args_list)
                self.assertTrue(all(call.args and isinstance(call.args[0], QRect)
                                    for call in update.call_args_list))
                self.window.render(output)
        warmed = {
            image_key[0][0]
            for image_key, radius in self.window._control_layer_cache
            if radius > 0.0
        }
        self.assertEqual(warmed, {"window", "transport"})
        self.assertEqual(self.window._control_prewarm_remaining, 0)
        self.window._lyric_spring_active = False
        self.window._lyric_animation_rect = QRect()
        self.window._last_full_animation_update_at = 102.05
        with patch("local.music_surface_window.time.monotonic", return_value=102.05):
            with patch.object(self.window, "update") as update:
                self.window._on_animation_tick()
        self.assertTrue(update.call_args_list)
        self.assertTrue(any(call.args and isinstance(call.args[0], QRect)
                            for call in update.call_args_list))
        self.assertEqual(self.window._control_reveal_at(102.52), 0.0)

    def test_idle_controls_blur_slide_out_without_leaving_click_targets(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        self.window._reset_control_idle(100.0)
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch("local.music_surface_window.time.monotonic", return_value=104.0):
            self.window.render(output)
        visible_window = self.window._button_rects["mode"]
        visible_media = dict(self.window._media_button_rects)
        self.assertEqual(len(visible_media), 3)

        with patch("local.music_surface_window.time.monotonic", return_value=105.26):
            self.window.render(output)
        self.assertTrue(any(radius > 0.0 for (_image_key, radius) in self.window._control_layer_cache))
        self.assertEqual(self.window._button_rects, {})
        self.assertEqual(self.window._media_button_rects, {})

        with patch("local.music_surface_window.time.monotonic", return_value=105.6):
            self.window.render(output)
        self.assertEqual(self.window._button_rects, {})
        self.assertEqual(self.window._media_button_rects, {})

        with patch("local.music_surface_window.time.monotonic", return_value=105.6):
            self.window._note_pointer_moved()
        with patch("local.music_surface_window.time.monotonic", return_value=106.2):
            self.window.render(output)
        self.assertEqual(self.window._button_rects["mode"], visible_window)
        self.assertEqual(self.window._media_button_rects, visible_media)

    def test_control_blur_prewarms_one_level_per_quiet_frame_without_filtering_during_motion(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        self.window._reset_control_idle(100.0)
        self.window._hovered_window_button = "mode"
        self.window._hovered_media_command = "next"
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        warmed_counts = []
        for frame in range(12):
            with patch("local.music_surface_window.time.monotonic", return_value=103.0 + frame * 0.04):
                self.window.render(output)
            warmed_counts.append(sum(
                radius > 0.0 for _image_key, radius in self.window._control_layer_cache
            ))
        self.assertTrue(all(
            0 <= current - previous <= 1
            for previous, current in zip(warmed_counts, warmed_counts[1:])
        ))
        self.assertEqual(warmed_counts[-1], 8)
        warmed_controls = {
            image_key[0][0]
            for image_key, radius in self.window._control_layer_cache
            if radius > 0.0
        }
        self.assertEqual(warmed_controls, {"window", "transport"})

        # The hide begins with non-hovered controls, regardless of where the
        # resting cursor was. No Pillow GaussianBlur may run in these paints.
        with patch("local.music_surface_window.ImageFilter.GaussianBlur", side_effect=AssertionError("paint-time blur")):
            for instant in (105.05, 105.17, 105.29, 105.41):
                with patch("local.music_surface_window.time.monotonic", return_value=instant):
                    self.window.render(output)
        self.assertEqual(self.window._button_rects, {})
        self.assertEqual(self.window._media_button_rects, {})

    def test_hidden_control_layers_use_bounded_dpi_on_high_dpi_screen(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        self.window._reset_control_idle(100.0)
        output = QImage(2380, 1332, QImage.Format.Format_RGB32)
        output.setDevicePixelRatio(2.0)
        with (
            patch.object(self.window, "devicePixelRatioF", return_value=2.0),
            patch("local.music_surface_window.time.monotonic", return_value=105.1),
        ):
            self.window.render(output)
        self.assertTrue(self.window._control_layer_cache)
        self.assertTrue(all(
            image.devicePixelRatio() <= self.window.CONTROL_LAYER_DPR_CAP
            for image in self.window._control_layer_cache.values()
        ))

    def test_stale_control_rect_cannot_activate_during_idle_hide(self):
        self.window.resize(1190, 666)
        self.window._fullscreen = True
        self.window._reset_control_idle(100.0)
        self.window._button_rects = {"mode": QRectF(1080, 18, 68, 34)}
        self.window._media_button_rects = {"next": QRectF(280, 580, 110, 60)}
        with patch("local.music_surface_window.time.monotonic", return_value=105.6):
            with patch.object(self.window, "toggle_presentation_mode") as switch_mode:
                with patch.object(self.window, "_send_media_command") as send_command:
                    for point in (QPointF(1110, 30), QPointF(330, 600)):
                        self.window.mousePressEvent(_PointerEvent(point))
                        self.window.mouseReleaseEvent(_PointerEvent(point))
            switch_mode.assert_not_called()
            send_command.assert_not_called()

    def test_lyric_click_uses_provider_seek_with_offset_and_restores_follow(self):
        self.owner.media_track_id = "song"
        self.owner.timed_lyrics = [(0.0, "first"), (9.0, "second")]
        self.owner._effective_lyric_offset_seconds = lambda: 1.5
        self.window._browse_track_key = "song"
        self.window._browse_index = 1.0
        self.window._browse_until = 123.0
        self.window._browse_return_from = 2.0
        self.window._seek_to_lyric(1)
        self.assertEqual(self.owner.seek_calls, [7.5])
        self.assertIsNone(self.window._browse_index)
        self.assertEqual(self.window._browse_until, 0.0)
        self.assertIsNone(self.window._browse_return_from)
        self.assertEqual(self.owner.last_media_refresh_at, 0.0)

    def test_failed_lyric_seek_resumes_real_clock_follow_without_faking_position(self):
        self.owner.media_track_id = "song"
        self.owner.timed_lyrics = [(0.0, "first"), (9.0, "second")]
        self.owner.seek_playback_position = lambda seconds: False
        self.window._browse_track_key = "song"
        self.window._browse_index = 1.0
        self.window._browse_until = 123.0
        self.window._browse_return_from = 2.0
        self.window._seek_to_lyric(1)
        self.assertIsNone(self.window._browse_index)
        self.assertEqual(self.window._browse_until, 0.0)
        self.assertIsNone(self.window._browse_return_from)
        self.assertFalse(self.window._seek_pending)

    def test_lyric_click_without_seek_capability_still_releases_browse(self):
        self.owner.media_track_id = "song"
        self.owner.timed_lyrics = [(0.0, "first"), (9.0, "second")]
        self.owner.data_provider = object()
        self.window._browse_track_key = "song"
        self.window._browse_index = 1.0
        self.window._browse_until = 123.0
        self.window._seek_to_lyric(1)
        self.assertIsNone(self.window._browse_index)
        self.assertEqual(self.window._browse_until, 0.0)
        self.assertFalse(self.window._seek_pending)

    def test_track_or_timeline_change_cancels_browse_return(self):
        self.owner.media_track_id = "song-a"
        self.owner.media_position_seconds = 0.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        original = [(0.0, "first"), (5.0, "second")]
        self.window._lyric_frame(original, 10.0)
        self.window._browse_index = 1.0
        self.window._browse_until = 14.0
        self.window._browse_return_from = 1.0
        self.owner.media_track_id = "song-b"
        self.window._lyric_frame(original, 10.1)
        self.assertIsNone(self.window._browse_index)
        self.assertEqual(self.window._browse_until, 0.0)
        self.assertIsNone(self.window._browse_return_from)
        self.window._browse_index = 1.0
        self.window._browse_until = 14.0
        self.window._lyric_frame([(0.0, "new first"), (5.0, "new second")], 10.2)
        self.assertIsNone(self.window._browse_index)

    def test_seek_completion_does_not_change_normal_window_stacking(self):
        self.window._browse_track_key = "song"
        self.window._browse_index = 1.0
        with (
            patch.object(self.window, "isVisible", return_value=True),
            patch.object(self.window, "isMinimized", return_value=False),
            patch.object(self.window, "raise_") as raise_window,
            patch.object(self.window, "activateWindow") as activate_window,
        ):
            self.window._on_seek_finished(True, 7.5, "song")
        raise_window.assert_not_called()
        activate_window.assert_not_called()
        self.assertIsNone(self.window._browse_index)
        self.assertFalse(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_replacing_timeline_reanchors_current_line_immediately(self):
        self.owner.media_track_id = "same-song"
        self.owner.media_position_seconds = 12.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        original = [(0.0, "placeholder"), (20.0, "later")]
        updated = [(0.0, "first"), (5.0, "second"), (10.0, "current"), (15.0, "next")]
        self.assertEqual(self.window._lyric_frame(original, 1.0).active_index, 0)
        frame = self.window._lyric_frame(updated, 1.03)
        self.assertEqual(frame.active_index, 2)
        self.assertEqual(frame.scroll_index, 2.0)

    def test_same_track_position_jitter_does_not_move_active_line_backward(self):
        self.owner.media_track_id = "same-song"
        self.owner.timed_lyrics = [(0.0, "first"), (2.0, "second"), (4.0, "third")]
        self.owner.media_position_seconds = 2.05
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds

        forward = self.window._lyric_frame(self.owner.timed_lyrics, 10.0)
        self.owner.media_position_seconds = 1.96
        jittered = self.window._lyric_frame(self.owner.timed_lyrics, 10.016)
        self.owner.media_position_seconds = 2.08
        recovered = self.window._lyric_frame(self.owner.timed_lyrics, 10.032)

        self.assertEqual(
            (forward.active_index, jittered.active_index, recovered.active_index),
            (1, 1, 1),
        )

    def test_island_row_changes_do_not_override_fullscreen_media_clock(self):
        self.owner.media_track_id = "same-song"
        self.owner.timed_lyrics = [(0.0, "first"), (2.0, "second"), (4.0, "third")]
        self.owner.media_position_seconds = 0.8
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.owner.lyric_index = 0
        self.owner.lyric_changed_at = 1.0
        self.assertEqual(self.window._lyric_frame(self.owner.timed_lyrics, 10.0).active_index, 0)

        # The island can momentarily jump because it uses a separately
        # debounced/led row marker. Full-screen timing must remain clock-owned.
        self.owner.lyric_index = 2
        self.owner.lyric_changed_at = 1.1
        self.owner.lyric_transition_kind = "line"
        frame = self.window._lyric_frame(self.owner.timed_lyrics, 10.016)
        self.assertEqual(frame.active_index, 0)
        self.assertGreater(frame.reading_progress, 0.0)

    def test_reprojected_uia_sample_cannot_confirm_forward_clock_jump(self):
        self.owner.media_track_id = "same-song"
        self.owner.timed_lyrics = [
            (0.0, "one"), (2.0, "two"), (4.0, "three"), (6.0, "four"),
        ]
        self.owner.media_is_playing = True
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.owner.media_position_seconds = 0.8
        self.owner.media_position_sync_at = 10.0
        self.owner.media_raw_position_seconds = 0.8
        self.owner.media_position_source = "windows-media"
        self.assertEqual(self.window._lyric_frame(self.owner.timed_lyrics, 10.0).active_index, 0)

        # One UIA observation is projected and delivered through several
        # visual polls. Their timestamps and projected positions differ, but
        # they are still the same underlying player observation.
        self.owner.media_position_source = "netease-uia-progress"
        self.owner.media_position_sampled_at = 10.04
        for now, position in ((10.05, 6.80), (10.15, 6.90), (10.27, 7.02)):
            self.owner.media_position_sync_at = now
            self.owner.media_raw_position_seconds = position
            self.owner.media_position_seconds = position
            frame = self.window._lyric_frame(self.owner.timed_lyrics, now)
            self.assertEqual(frame.active_index, 0)
            self.assertLess(frame.reading_progress, 0.60)

        self.owner.media_position_sampled_at = 10.35
        self.owner.media_position_sync_at = 10.36
        self.owner.media_raw_position_seconds = 7.11
        self.owner.media_position_seconds = 7.11
        self.assertEqual(self.window._lyric_frame(self.owner.timed_lyrics, 10.36).active_index, 3)

    def test_fullscreen_coarse_lyric_anchor_does_not_cross_next_row_on_slow_paints(self):
        self.owner.media_track_id = "same-song"
        self.owner.timed_lyrics = [(20.0, "observed"), (24.0, "next")]
        self.owner.media_is_playing = True
        self.owner.media_position_source = "netease-uia-lyrics"
        self.owner.media_position_seconds = 23.0
        self.owner.media_position_sync_at = 10.0
        self.owner.media_position_sampled_at = 10.0
        self.owner.media_raw_position_seconds = 23.0
        self.owner._estimated_media_position = (
            lambda now: min(23.0 + max(0.0, now - 10.0), 23.92)
        )
        for tick in range(121):
            now = 10.0 + tick / 20.0
            frame = self.window._lyric_frame(self.owner.timed_lyrics, now)
            self.assertEqual(frame.active_index, 0)
            self.assertLess(self.window._lyric_motion.last_position, 24.0)

        self.owner.media_position_seconds = 24.1
        self.owner.media_position_sync_at = 16.1
        self.owner.media_position_sampled_at = 16.1
        self.owner.media_raw_position_seconds = 24.1
        self.owner._estimated_media_position = lambda now: 24.1 + max(0.0, now - 16.1)
        self.assertEqual(self.window._lyric_frame(self.owner.timed_lyrics, 16.2).active_index, 1)

    def test_explicit_owner_seek_can_move_active_line_backward(self):
        self.owner.media_track_id = "same-song"
        self.owner.timed_lyrics = [(0.0, "first"), (2.0, "second"), (4.0, "third")]
        self.owner.media_position_seconds = 4.2
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.assertEqual(self.window._lyric_frame(self.owner.timed_lyrics, 10.0).active_index, 2)

        self.owner.media_position_seconds = 0.4
        self.owner.media_position_source = "local-seek"
        self.owner.media_position_sync_at = 10.01
        self.owner.media_raw_position_seconds = 0.4
        self.owner.lyric_index = 0
        self.owner.lyric_changed_at = 10.01
        self.owner.lyric_transition_kind = "seek"
        seeked = self.window._lyric_frame(self.owner.timed_lyrics, 10.02)
        self.assertEqual(seeked.active_index, 0)

    def test_cached_system_sample_is_not_a_second_jump_confirmation(self):
        self.owner.media_track_id = "clock-identity"
        self.owner.timed_lyrics = [(0.0, "one"), (2.0, "two"), (4.0, "three"), (6.0, "four")]
        self.owner.media_is_playing = True
        self.owner.media_position_source = "windows-media"
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.owner.media_position_seconds = 0.8
        self.owner.media_raw_position_seconds = 0.8
        self.owner.media_position_sync_at = 10.0
        self.owner.media_position_sampled_at = 10.0
        self.assertEqual(self.window._lyric_frame(self.owner.timed_lyrics, 10.0).active_index, 0)

        self.owner.media_position_seconds = 6.8
        self.owner.media_raw_position_seconds = 6.8
        self.owner.media_position_sync_at = 10.05
        self.owner.media_position_sampled_at = 10.05
        self.assertEqual(self.window._lyric_frame(self.owner.timed_lyrics, 10.05).active_index, 0)
        # A cached provider value can be handed to the UI again at a new sync
        # time. Its original observation is still the same one bad sample.
        self.owner.media_position_sync_at = 10.70
        self.assertEqual(self.window._lyric_frame(self.owner.timed_lyrics, 10.70).active_index, 0)

        self.owner.media_position_seconds = 7.50
        self.owner.media_raw_position_seconds = 7.50
        self.owner.media_position_sync_at = 10.75
        self.owner.media_position_sampled_at = 10.75
        self.assertEqual(self.window._lyric_frame(self.owner.timed_lyrics, 10.75).active_index, 3)

    def test_each_verified_local_seek_reanchors_once(self):
        self.owner.media_track_id = "two-seeks"
        self.owner.timed_lyrics = [(0.0, "one"), (2.0, "two"), (4.0, "three"), (6.0, "four")]
        self.owner.media_is_playing = True
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.owner.media_position_seconds = 0.5
        self.owner.media_position_source = "windows-media"
        self.owner.media_position_sampled_at = 10.0
        self.assertEqual(self.window._lyric_frame(self.owner.timed_lyrics, 10.0).active_index, 0)

        self.owner.media_position_source = "local-seek"
        self.owner.media_position_seconds = 6.5
        self.owner.media_position_sampled_at = 11.0
        self.assertEqual(self.window._lyric_frame(self.owner.timed_lyrics, 11.0).active_index, 3)
        with patch.object(self.window._lyric_motion, "frame", wraps=self.window._lyric_motion.frame) as motion:
            self.owner.media_position_seconds = 6.55
            self.window._lyric_frame(self.owner.timed_lyrics, 11.02)
            self.assertFalse(motion.call_args.kwargs["explicit_seek"])
            self.owner.media_position_seconds = 0.5
            self.owner.media_position_sampled_at = 12.0
            self.assertEqual(self.window._lyric_frame(self.owner.timed_lyrics, 12.0).active_index, 0)
            self.assertTrue(motion.call_args.kwargs["explicit_seek"])

    def test_track_change_transitions_but_same_track_cover_upgrade_does_not(self):
        self.owner.media_track_id = "track-a"
        self.owner.track_title = "A"
        self.owner.album_color = Qt.GlobalColor.red
        self.owner.album_art = QImage(64, 64, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(Qt.GlobalColor.red)
        self.window.resize(960, 540)
        output = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch("local.music_surface_window.time.monotonic", return_value=10.0):
            self.window.render(output)
        self.owner.album_art = QImage(128, 128, QImage.Format.Format_RGB32)
        self.owner.album_art.fill(Qt.GlobalColor.darkRed)
        with patch("local.music_surface_window.time.monotonic", return_value=10.1):
            self.window.render(output)
        self.assertIsNone(self.window._previous_track_visual)
        self.owner.media_track_id = "track-b"
        self.owner.track_title = "B"
        self.owner.album_color = Qt.GlobalColor.blue
        with patch("local.music_surface_window.time.monotonic", return_value=11.0):
            self.window.render(output)
        self.assertIsNotNone(self.window._previous_track_visual)
        self.assertEqual(self.window._track_transition_at, 11.0)
        previous_key = self.window._previous_cover_cache.cacheKey()
        with patch("local.music_surface_window.time.monotonic", return_value=11.18):
            self.window.render(output)
        self.assertEqual(self.window._previous_cover_cache.cacheKey(), previous_key)
        with patch("local.music_surface_window.time.monotonic", return_value=11.4):
            self.window.render(output)
        self.assertIsNone(self.window._previous_track_visual)

    def test_island_and_music_surface_are_mutually_exclusive(self):
        island = IslandStackVisual()
        try:
            island.show()
            self.app.processEvents()
            island._show_music_fullscreen()
            self.app.processEvents()
            self.assertFalse(island.isVisible())
            self.assertTrue(island.music_fullscreen_overlay.isVisible())
            self.assertTrue(island._music_surface_active)
            island.show()
            self.app.processEvents()
            self.assertFalse(island.isVisible())
            self.assertTrue(island.music_fullscreen_overlay.isVisible())
            island._close_music_fullscreen()
            self.app.processEvents()
            self.assertTrue(island._music_surface_active)
            self.assertTrue(island.music_fullscreen_overlay._island_returning)
            island.music_fullscreen_overlay._finish_mode_transition()
            self.app.processEvents()
            self.assertTrue(island.isVisible())
            self.assertFalse(island.music_fullscreen_overlay.isVisible())
            self.assertFalse(island._music_surface_active)
        finally:
            island.close()

    def test_music_surface_lifecycle_orders_hide_show_and_restore(self):
        island = _LifecycleIsland()
        surface = _LifecycleSurface(island)
        try:
            island.show()
            self.app.processEvents()
            island.lifecycle_events.clear()
            island.music_fullscreen_overlay = surface

            island._show_music_fullscreen()
            self.app.processEvents()
            self.assertEqual(
                island.lifecycle_events[:3],
                [
                    ("release_topmost", True),
                    ("island_hide", True),
                    ("surface_show", False, True),
                ],
            )
            self.assertTrue(island._music_surface_active)

            island.lifecycle_events.clear()
            island._restore_island_from_music_surface()
            self.app.processEvents()
            self.assertEqual(
                island.lifecycle_events[:4],
                [
                    ("surface_hide", True),
                    ("island_show", False),
                    ("island_raise", False),
                    ("island_activate", False),
                ],
            )
            self.assertTrue(island.isVisible())
        finally:
            island.music_fullscreen_overlay = None
            island.close()

    def test_music_surface_show_failure_rolls_back_island_visibility(self):
        island = _LifecycleIsland()
        surface = _LifecycleSurface(island, fail_to_show=True)
        try:
            island.show()
            self.app.processEvents()
            island.lifecycle_events.clear()
            island.music_fullscreen_overlay = surface
            island._show_music_fullscreen()
            self.app.processEvents()
            self.assertFalse(island._music_surface_active)
            self.assertTrue(island.isVisible())
            self.assertIn(("surface_hide", True), island.lifecycle_events)
            self.assertIn(("island_show", False), island.lifecycle_events)
        finally:
            island.music_fullscreen_overlay = None
            island.close()

    def test_taskbar_follow_cannot_show_island_while_music_surface_is_active(self):
        island = _LifecycleIsland()
        try:
            island._music_surface_active = True
            island.taskbar_hidden_by_follow = True
            island.lifecycle_events.clear()
            island._set_taskbar_follow_hidden(False)
            self.app.processEvents()
            self.assertFalse(island.isVisible())
            self.assertNotIn(("island_show", True), island.lifecycle_events)
        finally:
            island._music_surface_active = False
            island.close()

    def test_music_surface_tick_keeps_runtime_and_overlay_sync_without_island_frame_work(self):
        class VisibleSurface:
            def isVisible(self):
                return True

        island = IslandStackVisual()
        try:
            island.music_fullscreen_overlay = VisibleSurface()
            island.barrage_clock = 2.0
            hover_plan = SimpleNamespace(hovered=True, clock_delta=0.25)
            with (
                patch("local.island_stack_visual.time.monotonic", return_value=12.0),
                patch.object(island, "_refresh_media_data", return_value=True) as refresh_media,
                patch.object(island, "_update_scene_state") as update_scene,
                patch.object(island, "_prune_expired_messages") as prune_messages,
                patch.object(island, "_handle_fullscreen_overlay_sync") as sync_overlays,
                patch.object(island, "_barrage_hover_clock_plan", return_value=hover_plan) as barrage_plan,
                patch.object(island, "_handle_music_lyric_tick", return_value=True) as lyric_tick,
                patch.object(island, "_handle_tick_follow_refresh") as follow_refresh,
                patch.object(island, "_handle_tick_overlay_clock_and_topmost") as topmost_tick,
                patch.object(island, "_handle_tick_frame_execution") as frame_execution,
                patch.object(island, "_force_super_topmost") as force_topmost,
                patch.object(island, "update") as island_update,
            ):
                island._tick()

            refresh_media.assert_called_once_with(12.0)
            update_scene.assert_called_once_with(12.0)
            prune_messages.assert_called_once_with(12.0)
            sync_overlays.assert_called_once_with(12.0)
            barrage_plan.assert_called_once()
            lyric_tick.assert_called_once_with(12.0, island._animation_speed_factor())
            follow_refresh.assert_not_called()
            topmost_tick.assert_not_called()
            frame_execution.assert_not_called()
            force_topmost.assert_not_called()
            island_update.assert_not_called()
            self.assertTrue(island.barrage_hovered)
            self.assertEqual(island.barrage_clock, 2.25)
        finally:
            island.music_fullscreen_overlay = None
            island.close()

    def test_focusing_active_surface_restores_minimized_window_before_raising(self):
        class MinimizedSurface:
            _fullscreen = False

            def __init__(self):
                self.minimized = True
                self.events = []

            def isVisible(self):
                return True

            def isMinimized(self):
                return self.minimized

            def showNormal(self):
                self.events.append("showNormal")
                self.minimized = False

            def showFullScreen(self):
                self.events.append("showFullScreen")
                self.minimized = False

            def raise_(self):
                self.events.append("raise")

            def activateWindow(self):
                self.events.append("activate")

        island = IslandStackVisual()
        try:
            surface = MinimizedSurface()
            island._music_surface_active = True
            island.music_fullscreen_overlay = surface
            self.assertTrue(island._focus_active_music_surface())
            self.assertEqual(surface.events, ["showNormal", "raise", "activate"])

            surface._fullscreen = True
            surface.minimized = True
            surface.events.clear()
            self.assertTrue(island._focus_active_music_surface())
            self.assertEqual(surface.events, ["showFullScreen", "raise", "activate"])
        finally:
            island._music_surface_active = False
            island.music_fullscreen_overlay = None
            island.close()

    def test_music_surface_release_requires_matching_pressed_target(self):
        self.window.resize(960, 540)
        previous = QRectF(100, 100, 40, 40)
        next_button = QRectF(160, 100, 40, 40)
        first_lyric = QRectF(220, 100, 120, 40)
        second_lyric = QRectF(220, 160, 120, 40)
        return_button = QRectF(400, 100, 40, 40)
        self.window._media_button_rects = {"previous": previous, "next": next_button}
        self.window._lyric_hit_rects = {0: first_lyric, 1: second_lyric}
        self.window._button_rects = {"return": return_button}
        sent_commands = []
        self.owner._send_playback_command_async = lambda command: sent_commands.append(command)

        with (
            patch.object(self.window, "return_to_island") as return_to_island,
            patch.object(self.window, "_seek_to_lyric") as seek_to_lyric,
        ):
            self.window.mousePressEvent(_PointerEvent(previous.center()))
            self.window.mouseReleaseEvent(_PointerEvent(next_button.center()))
            self.window.mousePressEvent(_PointerEvent(QPointF(380, 100)))
            self.window.mouseReleaseEvent(_PointerEvent(return_button.center()))
            self.window.mousePressEvent(_PointerEvent(first_lyric.center()))
            self.window.mouseReleaseEvent(_PointerEvent(second_lyric.center()))

            self.assertEqual(sent_commands, [])
            return_to_island.assert_not_called()
            seek_to_lyric.assert_not_called()

            self.window.mousePressEvent(_PointerEvent(previous.center()))
            self.window.mouseReleaseEvent(_PointerEvent(previous.center()))
            self.assertEqual(sent_commands, ["previous"])

            self.window.mousePressEvent(_PointerEvent(return_button.center()))
            self.window.mouseReleaseEvent(_PointerEvent(return_button.center()))
            return_to_island.assert_called_once_with()

            self.window.mousePressEvent(_PointerEvent(first_lyric.center()))
            self.window.mouseReleaseEvent(_PointerEvent(first_lyric.center()))
            seek_to_lyric.assert_called_once_with(0)

    def test_geometry_snapshot_transition_blocks_stale_pointer_targets(self):
        self.window.resize(960, 540)
        window_target = QRectF(100, 100, 60, 40)
        media_target = QRectF(200, 100, 60, 40)
        lyric_target = QRectF(300, 100, 100, 40)
        self.window._button_rects = {"mode": window_target}
        self.window._media_button_rects = {"next": media_target}
        self.window._lyric_hit_rects = {0: lyric_target}
        self.window._geometry_transition_snapshot = QImage(16, 9, QImage.Format.Format_RGB32)
        self.window._geometry_transition_snapshot.fill(Qt.GlobalColor.black)
        sent_commands = []
        self.owner._send_playback_command_async = lambda command: sent_commands.append(command)

        with (
            patch.object(self.window, "toggle_presentation_mode") as toggle_mode,
            patch.object(self.window, "_seek_to_lyric") as seek_to_lyric,
        ):
            events = []
            for pending, returning in ((True, False), (None, True)):
                self.window._pending_fullscreen = pending
                self.window._island_returning = returning
                for target in (window_target, media_target, lyric_target):
                    press = _PointerEvent(target.center(), buttons=Qt.MouseButton.LeftButton)
                    move = _PointerEvent(target.center())
                    release = _PointerEvent(target.center())
                    self.window.mousePressEvent(press)
                    self.window.mouseMoveEvent(move)
                    self.window.mouseReleaseEvent(release)
                    events.extend((press, move, release))
            double_click = _PointerEvent(QPointF(500, 20))
            self.window.mouseDoubleClickEvent(double_click)

        self.assertTrue(all(event.accepted for event in events))
        self.assertTrue(double_click.accepted)
        self.assertEqual(sent_commands, [])
        toggle_mode.assert_not_called()
        seek_to_lyric.assert_not_called()
        self.assertEqual(self.window._pressed_media_command, "")

    def test_tray_double_click_focuses_active_music_surface_instead_of_island(self):
        class TrayWindow:
            _music_surface_active = True

            def __init__(self):
                self.focus_calls = 0

            def _focus_active_music_surface(self):
                self.focus_calls += 1
                return True

            def isVisible(self):
                raise AssertionError("tray must not inspect or toggle island visibility")

        class TrayHarness:
            toggle_window = LocalIslandApp.toggle_window

        harness = TrayHarness()
        harness.window = TrayWindow()
        LocalIslandApp._on_tray_activated(harness, QSystemTrayIcon.ActivationReason.DoubleClick)
        self.assertEqual(harness.window.focus_calls, 1)

    def test_tasker_cover_can_open_surface_without_stealing_media_button(self):
        island = IslandStackVisual()
        try:
            island.location_mode = "tasker"
            island.expanded = False
            island._main_kind = lambda: IslandKind.MUSIC
            island._taskbar_selected_drop_zone_local_rect = lambda: QRectF(0, 0, 180, 60)
            island._taskbar_left_handle_visible = lambda: False
            island.taskbar_left_handle_rect = QRectF()
            island.music_cover_rect = QRectF(4, 4, 40, 40)
            island.music_button_rects = {"music:toggle_play_pause": QRectF(60, 4, 40, 40)}
            self.assertEqual(island._hit_test(QPointF(20, 20)), "music:open_surface")
            self.assertEqual(island._hit_test(QPointF(80, 20)), "music:toggle_play_pause")
        finally:
            island.close()


if __name__ == "__main__":
    unittest.main()
