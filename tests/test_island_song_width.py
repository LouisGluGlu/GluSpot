"""Song-stable island width, critical spring, and the album-edge anchor."""
from __future__ import annotations

import unittest

from local.spring_motion import Spring, WIDE_NARROW_SPRING
from local.taskbar_positioning import (
    compact_width_anchors_center,
    compact_width_spring_damping,
    held_screen_left_window_x,
    moderate_song_line_width,
)


class ModerateSongWidthTests(unittest.TestCase):
    def test_sixtieth_percentile_ignores_one_long_chorus(self):
        # Ten lines. Index round(9 * 0.6) = 5, the sixth width once sorted.
        widths = [100, 120, 140, 160, 180, 200, 220, 240, 260, 800]
        self.assertEqual(moderate_song_line_width(widths), 200)

    def test_empty_and_single_line(self):
        self.assertEqual(moderate_song_line_width([]), 0.0)
        self.assertEqual(moderate_song_line_width([0, -3, "no"]), 0.0)
        self.assertEqual(moderate_song_line_width([180]), 180.0)

    def test_same_widths_do_not_change_when_reordered(self):
        widths = [40, 90, 70, 55]
        self.assertEqual(moderate_song_line_width(widths), moderate_song_line_width(list(reversed(widths))))


class WidthAnchorTests(unittest.TestCase):
    def test_magnet_presets_and_attached_bars_grow_from_the_center(self):
        for preset in ("top_center", "top_left", "top_right", "bottom_left", "bottom_right"):
            self.assertTrue(compact_width_anchors_center(location_mode="free", position_preset=preset))
        for mode in ("top", "stick", "finder", "tasker"):
            self.assertTrue(compact_width_anchors_center(location_mode=mode, position_preset="last"))

    def test_a_free_island_keeps_the_album_edge(self):
        self.assertFalse(compact_width_anchors_center(location_mode="free", position_preset="last"))
        self.assertFalse(compact_width_anchors_center(location_mode="", position_preset=""))

    def test_song_change_uses_critical_damping_and_a_steady_width_does_not(self):
        self.assertEqual(compact_width_spring_damping(430, 520, default=0.76), 1.0)
        self.assertEqual(compact_width_spring_damping(520, 520.2, default=0.76), 0.76)

    def test_window_shift_holds_the_screen_left_while_the_shell_grows(self):
        # Canvas-centered shell: growing 40px moves the local left 20px left.
        anchor = 640.0
        self.assertEqual(held_screen_left_window_x(anchor, 200.0), 440.0)
        self.assertEqual(held_screen_left_window_x(anchor, 180.0), 460.0)
        self.assertEqual(held_screen_left_window_x(anchor, 180.0) + 180.0, anchor)

    def test_critical_spring_reaches_the_new_width_without_passing_it(self):
        spring = Spring(WIDE_NARROW_SPRING, value=430.0, velocity=0.0, target=520.0)
        self.assertEqual(spring.config.damping_ratio, 1.0)
        seen = [spring.value]
        for _ in range(80):
            seen.append(spring.step(1.0 / 60.0))
        self.assertLessEqual(max(seen), 520.0 + 1e-6)
        self.assertGreater(seen[-1], 519.0)
        self.assertTrue(all(b >= a - 1e-9 for a, b in zip(seen, seen[1:])))


if __name__ == "__main__":
    unittest.main()
