from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.music_surface_window import MusicSurfaceWindow
from local.island_stack_visual import IslandStackVisual
from tests.test_music_lyric_visual_layers import _LayerOwner


class MusicAnimationEngineIntegrationTests(unittest.TestCase):
    """Drive real layout/paint dispatch while independently sampling timer motion."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _LayerOwner()
        self.owner.music_surface_reading_strategy = "low_perception"
        self.owner.music_sparse_highlight_seconds_per_unit = 2.0
        self.owner.enable_dual_line_lyrics = False
        self.owner.media_position_source = "local-seek"
        self.owner.media_position_sampled_at = 100.0
        self.owner.media_position_seconds = 1.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self._set_rows([(float(index * 4), f"Sentence {index}") for index in range(20)])
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _set_rows(self, rows):
        self.owner.timed_lyrics = list(rows)
        self.owner.timed_lyric_units = [()] * len(rows)
        self.owner.timed_lyric_timing_kinds = ["line"] * len(rows)

    def _render(self, now: float):
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch("local.music_surface_window.time.monotonic", return_value=now):
            self.window.render(image)
        self.assertFalse(image.isNull())
        return image

    def _seek(self, position: float, now: float):
        self.owner.media_position_seconds = position
        self.owner.media_position_sampled_at = now

    def _tick(self, now: float):
        with patch("local.music_surface_window.time.monotonic", return_value=now):
            self.window._on_animation_tick()

    def _pose(self, row_index: int):
        visual_index = self.window._song_visual_ranges[row_index][0]
        chain = self.window._logical_line_spring
        return chain.positions[visual_index], chain.velocities[visual_index], chain.targets[visual_index]

    def _moving_pose(self):
        self._render(100.0)
        self._seek(5.0, 100.016)
        self._render(100.016)
        self._render(100.096)
        position, velocity, target = self._pose(2)
        self.assertGreater(abs(position - target), 1.0)
        self.assertGreater(abs(velocity), 1.0)
        return position, velocity, target

    def test_verified_multi_row_seek_scrolls_and_eventually_settles(self):
        self._render(100.0)
        old_position, _velocity, _target = self._pose(2)
        self._seek(25.0, 100.008)
        self._render(100.008)
        moving_position, _velocity, target = self._pose(2)
        self.assertEqual(self.window._lyric_motion.active_index, 6)
        self.assertLess(abs(moving_position - old_position), 8.0)
        self.assertGreater(abs(moving_position - target), 100.0)
        self.assertTrue(self.window._lyric_spring_active)

        for step in range(1, 241):
            self._tick(100.008 + step / 120.0)
        self._render(102.008)
        final_position, final_velocity, final_target = self._pose(2)
        self.assertAlmostEqual(final_position, final_target, delta=0.10)
        self.assertAlmostEqual(final_velocity, 0.0, delta=0.10)

    def test_timer_updates_clock_then_moves_existing_springs_without_paints(self):
        self._render(100.0)
        self._seek(25.0, 100.020)
        self._tick(100.020)
        self.assertEqual(self.window._lyric_motion.active_index, 6)
        self.assertEqual(self.window._lyric_motion.last_at, 100.020)

        # Paint publishes the destination geometry once. The following timer
        # samples advance the physical rows even when Qt coalesces updates.
        self._render(100.020)
        first_position, _velocity, first_target = self._pose(2)
        positions = []
        for step in range(1, 17):
            self._tick(100.020 + step / 120.0)
            positions.append(self._pose(2)[0])
        self.assertLess(abs(positions[-1] - first_target), abs(first_position - first_target))
        self.assertGreater(len({round(value, 5) for value in positions}), 12)
        before_paint = self._pose(2)
        self._render(100.020 + 16 / 120.0)
        self.assertAlmostEqual(self._pose(2)[0], before_paint[0], delta=0.001)

    def test_same_song_insertion_and_timing_enrichment_keep_surviving_row_pose(self):
        position, velocity, _target = self._moving_pose()
        # New source rows and more precise times arrive during the current
        # spring. The visible sentence keeps its own physical identity.
        enriched = [(0.0, "New opening sentence")]
        enriched.extend((start + 0.05, text) for start, text in self.owner.timed_lyrics)
        self._set_rows(enriched)
        self._render(100.096)
        enriched_position, enriched_velocity, _target = self._pose(3)
        self.assertAlmostEqual(enriched_position, position, delta=0.001)
        self.assertAlmostEqual(enriched_velocity, velocity, delta=0.001)
        self.assertEqual(self.window._lyric_motion.active_index, 2)

        self._tick(100.112)
        self.assertNotAlmostEqual(self._pose(3)[0], enriched_position, delta=0.001)

    def test_rapid_forward_backward_seek_keeps_pose_and_follows_latest_target(self):
        old_position, old_velocity, _target = self._moving_pose()
        self._seek(25.0, 100.097)
        self._render(100.096)
        forward_position, forward_velocity, forward_target = self._pose(2)
        self.assertAlmostEqual(forward_position, old_position, delta=0.001)
        self.assertGreater(abs(forward_position - forward_target), 100.0)

        # Retarget at the same simulation instant; replacing the command must
        # not reset the row's pose or let the earlier destination finish later.
        self._seek(1.0, 100.098)
        self._render(100.096)
        reverse_position, reverse_velocity, reverse_target = self._pose(2)
        self.assertEqual(self.window._lyric_motion.active_index, 0)
        self.assertAlmostEqual(reverse_position, forward_position, delta=0.001)
        self.assertAlmostEqual(reverse_velocity, forward_velocity, delta=0.001)
        self.assertGreater(reverse_target, forward_target)
        self.assertGreater(abs(old_velocity), 1.0)

        for step in range(1, 241):
            self._tick(100.096 + step / 120.0)
        self._render(102.096)
        final_position, final_velocity, final_target = self._pose(2)
        self.assertEqual(self.window._lyric_motion.active_index, 0)
        self.assertAlmostEqual(final_position, final_target, delta=0.10)
        self.assertAlmostEqual(final_velocity, 0.0, delta=0.10)

    def test_output_latency_reversal_keeps_row_pose_and_timer_drives_intermediate_scroll(self):
        offset = [0.0]
        self.owner._effective_lyric_offset_seconds = lambda: offset[0]
        self.owner.media_position_seconds = 8.2
        self.owner.media_raw_position_seconds = 8.2
        self.owner.media_position_source = "windows-media"
        self._render(100.0)
        initial = self._pose(2)
        offset[0] = -1.5
        self._render(100.0)
        cast_pose = self._pose(2)
        self.assertEqual(self.window._lyric_motion.active_index, 1)
        self.assertAlmostEqual(cast_pose[0], initial[0], delta=0.001)
        self.assertAlmostEqual(cast_pose[1], initial[1], delta=0.001)
        self.assertGreater(abs(cast_pose[0] - cast_pose[2]), 30.0)
        positions = []
        for step in range(1, 17):
            now = 100.0 + step / 120.0
            self.owner.media_position_seconds = 8.2 + step / 120.0
            self._tick(now)
            positions.append(self._pose(2)[0])
        self.assertGreater(len({round(value, 4) for value in positions}), 10)
        moving = self._pose(2)
        offset[0] = 0.0
        self._render(100.0 + 16 / 120.0)
        stopped = self._pose(2)
        self.assertEqual(self.window._lyric_motion.active_index, 2)
        self.assertAlmostEqual(stopped[0], moving[0], delta=0.001)
        self.assertAlmostEqual(stopped[1], moving[1], delta=0.001)
        self.assertAlmostEqual(self.window._lyric_motion.last_source_position, 8.2 + 16 / 120.0)
        self.assertIsNone(self.window._lyric_motion.pending_clock_position)


class WaveIslandAnimationEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.visual = IslandStackVisual.__new__(IslandStackVisual)
        visual = self.visual
        visual._u = lambda value: float(value)
        visual.lyrics = [f"Sentence {index}" for index in range(240)]
        visual.media_track_id = "wave-motion-engine"
        visual.track_title = "Wave motion"
        visual.artist = "Fixture"
        visual.lyric_flow_style = "wave_stream"
        visual.lyric_index = visual.prev_lyric_index = 0
        visual.lyric_font_size = 18
        visual.lyric_transition_kind = ""
        visual.enable_dual_line_lyrics = False
        visual.expanded = True
        visual.scene_suppressed = False
        visual.media_is_playing = False
        visual._should_paint_album_island = lambda: False
        visual._ember_enabled = lambda: False
        visual._gelv_enabled = lambda: False
        visual._is_instrumental_track = lambda: False
        visual._lyric_phase = lambda _now: {"progress": 0.0}
        visual._music_lyric_tick_plan = lambda _now, _speed: SimpleNamespace(
            sync_timed_lyrics=False, fallback_advance=SimpleNamespace(should_advance=False),
        )
        self.rows = []

        def observe(painter, row, text, _size, _alpha, _bold, active, _phase):
            self.rows.append((text, row.top(), active))
            painter.drawText(row, text)

        visual._paint_expanded_lyric_row = observe

    def _render(self, now: float):
        self.rows.clear()
        image = QImage(300, 180, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        with patch("local.island_stack_visual.time.monotonic", return_value=now):
            with QPainter(image) as painter:
                self.visual._paint_lyric_viewport(painter, QRectF(0.0, 0.0, 300.0, 180.0))
        return tuple(self.rows)

    def test_long_seek_draws_crossing_sentences_without_blank_island_or_full_song_steps(self):
        self._render(100.0)
        self.visual.prev_lyric_index = 0
        self.visual.lyric_index = 80
        self.visual.lyric_transition_kind = "seek"
        first = self._render(100.0)
        self.assertTrue(first)
        self.assertTrue(any(text == "Sentence 0" for text, _top, _active in first))
        crossings = set()
        for step in range(1, 161):
            now = 100.0 + step / 120.0
            self.visual._handle_music_lyric_tick(now, 1.0)
            rows = self._render(now)
            self.assertTrue(rows, f"The island lost all rows at transition frame {step}")
            crossings.update(text for text, _top, _active in rows)
            first_index, last_index = self.visual._wave_line_spring.visible_range
            self.assertLessEqual(last_index - first_index, 16)
        self.assertTrue(any(f"Sentence {index}" in crossings for index in range(10, 70)))
        self.assertTrue(any(text == "Sentence 80" and active for text, _top, active in rows))

    def test_paused_wave_seek_keeps_running_without_paints(self):
        self._render(100.0)
        self.visual.prev_lyric_index = 0
        self.visual.lyric_index = 4
        self.visual.lyric_transition_kind = "seek"
        self._render(100.0)
        spring = self.visual._wave_line_spring
        before = spring.positions[1]
        self.assertTrue(self.visual._handle_music_lyric_tick(100.032, 1.0))
        self.assertLess(spring.positions[1], before)
        self.assertEqual(spring.last_at, 100.032)
        self.visual.lyric_flow_style = "gelv_songscatter"
        self.visual._handle_music_lyric_tick(100.064, 1.0)
        self.assertEqual(spring.last_at, 100.064)

    def test_row_list_enrichment_preserves_existing_primary_pose(self):
        self._render(100.0)
        self.visual.lyric_index = 1
        self._render(100.016)
        self._render(100.040)
        spring = self.visual._wave_line_spring
        position, velocity = spring.positions[2], spring.velocities[2]
        self.visual.lyrics = ["New opening"] + [text + "\nNew translation" for text in self.visual.lyrics]
        self.visual.lyric_index = 2
        self._render(100.040)
        self.assertAlmostEqual(spring.positions[3], position, delta=0.001)
        self.assertAlmostEqual(spring.velocities[3], velocity, delta=0.001)


if __name__ == "__main__":
    unittest.main()
