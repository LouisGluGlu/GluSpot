from __future__ import annotations

from collections import deque
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QApplication, QWidget

from local.music_surface_window import MusicSurfaceWindow


class _BudgetOwner(QWidget):
    def __init__(self):
        super().__init__()
        self.media_track_id = "prewarm-budget"
        self.track_title = "Budgeted title"
        self.artist = "Artist"
        self.media_is_playing = True
        self.media_position_seconds = 1.0
        self.music_intro_cleanup_enabled = False
        self.enable_dual_line_lyrics = False
        self._music_windowed_preference = False
        self.timed_lyrics = [(float(i * 4), f"Ordinary lyric {i}") for i in range(4)]


class _Clock:
    def __init__(self):
        self.value = 10.0

    def __call__(self):
        return self.value

    def spend(self, seconds):
        self.value += seconds


class MusicPrewarmBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _BudgetOwner()
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.window._reset_control_idle(now=100.0)
        self.window._track_visual = (self.owner.media_track_id,)
        self.window._last_full_animation_update_at = 100.0
        self.window._has_timed_lyrics = True
        self.window._lyric_animation_rect = QRect(400, 0, 560, 540)
        self.window._active_lyric_animation_rect = QRect(440, 170, 400, 150)
        self.clock = _Clock()

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _queue_glow(self):
        font = self.window._surface_font(38, QFont.Weight.Bold)
        layout, _ = self.window._wrapped_lyric_layout("shine", font, 400.0)
        self.window._song_layout_key = ("budget-layout",)
        self.window._pending_glow_prewarm = (
            (self.window._song_layout_key, 0), "shine", font, 400.0,
            layout, "character_lift", QColor(100, 150, 180), 0, 0,
        )

    def test_due_blur_reserves_the_tick_instead_of_stacking_metadata_and_glow(self):
        self._queue_glow()
        pending = self.window._pending_glow_prewarm
        self.window._lyric_blur_prewarm_queue.append(("synthetic pending blur",))
        self.window._lyric_blur_next_prewarm_at = 100.0

        def blur(_now):
            self.window._lyric_blur_prewarm_queue.popleft()
            self.clock.spend(0.006)

        with (
            patch("local.music_surface_window.time.perf_counter", side_effect=self.clock),
            patch("local.music_surface_window.time.monotonic", return_value=100.05),
            patch.object(self.window, "_advance_lyric_blur_prewarm", side_effect=blur),
            patch.object(self.window, "_prewarm_idle_metadata_raster") as metadata,
            patch.object(self.window, "_advance_isolated_glyph_units", return_value=((), False)) as glyph,
        ):
            self.window._on_animation_tick()
        metadata.assert_not_called()
        glyph.assert_not_called()
        self.assertEqual(self.window._pending_glow_prewarm, pending)

    def test_one_fast_blur_still_does_not_start_a_second_cold_raster(self):
        self._queue_glow()
        self.window._lyric_blur_prewarm_queue.append(("synthetic pending blur",))
        self.window._lyric_blur_next_prewarm_at = 100.0

        def blur(_now):
            self.window._lyric_blur_prewarm_queue.popleft()
            self.clock.spend(0.0001)

        with (
            patch("local.music_surface_window.time.perf_counter", side_effect=self.clock),
            patch("local.music_surface_window.time.monotonic", return_value=100.05),
            patch.object(self.window, "_advance_lyric_blur_prewarm", side_effect=blur),
            patch.object(self.window, "_prewarm_idle_metadata_raster") as metadata,
            patch.object(self.window, "_advance_isolated_glyph_units", return_value=((), False)) as glyph,
        ):
            self.window._on_animation_tick()
        metadata.assert_not_called()
        glyph.assert_not_called()

    def test_idle_and_expanded_metadata_rasters_do_not_build_in_the_same_tick(self):
        self.window._control_last_motion_at = 99.0

        def metadata():
            self.window._metadata_idle_raster_token = ("new-token",)
            self.clock.spend(0.0001)

        with (
            patch("local.music_surface_window.time.perf_counter", side_effect=self.clock),
            patch("local.music_surface_window.time.monotonic", return_value=100.05),
            patch.object(self.window, "_prewarm_idle_metadata_raster", side_effect=metadata),
            patch.object(self.window, "_prewarm_metadata_text") as expanded,
        ):
            self.window._on_animation_tick()
        expanded.assert_not_called()

    def test_fast_timer_accounts_for_tick_work_in_addition_to_paint_work(self):
        self.window._recent_paint_costs.extend([2.0] * 20)

        def metadata():
            self.clock.spend(0.004)

        with (
            patch("local.music_surface_window.time.perf_counter", side_effect=self.clock),
            patch("local.music_surface_window.time.monotonic", return_value=100.05),
            patch.object(self.window, "screen", return_value=SimpleNamespace(refreshRate=lambda: 170.0)),
            patch.object(self.window, "_prewarm_idle_metadata_raster", side_effect=metadata),
        ):
            self.window._on_animation_tick()
        self.assertEqual(self.window._timer.interval(), self.window.ANIMATION_FRAME_MS)

    def test_deferred_layout_stops_after_elapsed_budget_and_eventually_publishes_exact_rows(self):
        font = self.window._surface_font(38, QFont.Weight.Bold)
        rows = [f"Pending song lyric {i}" for i in range(30)]
        original = self.window._wrapped_lyric_layout

        def slow_shape(*args, **kwargs):
            result = original(*args, **kwargs)
            self.clock.spend(0.003)
            return result

        with (
            patch("local.music_surface_window.time.perf_counter", side_effect=self.clock),
            patch.object(self.window, "_wrapped_lyric_layout", side_effect=slow_shape),
        ):
            layouts, tops = self.window._song_lyric_layouts(rows, font, 400.0, 20.0, defer=True)
        self.assertEqual((layouts, tops), ((), ()))
        self.assertEqual(len(self.window._song_layout_build["layouts"]), 1)
        while self.window._song_layout_build is not None:
            self.window._advance_song_layout_build(max_rows=24)
        layouts, tops = self.window._song_lyric_layouts(rows, font, 400.0, 20.0)
        self.assertEqual([layout.text() for layout, _height in layouts], rows)
        self.assertEqual(len(tops), len(rows))

    def test_time_budget_never_splits_a_duet_group_or_publishes_old_track(self):
        font = self.window._surface_font(38, QFont.Weight.Bold)
        first = [f"First song {i}" for i in range(30)]
        replacement = [f"Second song {i}" for i in range(30)]
        original = self.window._wrapped_lyric_layout

        def slow_shape(*args, **kwargs):
            result = original(*args, **kwargs)
            self.clock.spend(0.003)
            return result

        with (
            patch("local.music_surface_window.time.perf_counter", side_effect=self.clock),
            patch.object(self.window, "_wrapped_lyric_layout", side_effect=slow_shape),
        ):
            self.window._song_lyric_layouts(first, font, 400.0, 20.0, defer=True, pair_starts=(0,))
            self.assertEqual(len(self.window._song_layout_build["layouts"]), 2)
            self.assertEqual(self.window._song_layout_build["tops"], [0.0, 0.0])
            self.window._song_lyric_layouts(replacement, font, 400.0, 20.0, defer=True)
            self.assertEqual(len(self.window._song_layout_build["layouts"]), 1)
            self.assertEqual(self.window._song_layout_build["lines"], tuple(replacement))
        while self.window._song_layout_build is not None:
            self.window._advance_song_layout_build(max_rows=24)
        self.assertEqual([layout.text() for layout, _height in self.window._song_layouts], replacement)


if __name__ == "__main__":
    unittest.main()
