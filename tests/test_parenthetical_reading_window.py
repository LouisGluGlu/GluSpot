from __future__ import annotations

import math
import unittest

from local.music_lyric_motion import parenthetical_reading_window


class ParentheticalReadingWindowTests(unittest.TestCase):
    def test_attached_aside_spans_last_fifth_of_main_through_first_quarter_of_next(self):
        window = parenthetical_reading_window(0.0, 10.0, 14.0, 22.0)

        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual((window.start_seconds, window.end_seconds), (8.0, 16.0))
        self.assertEqual(window.progress(8.0), 0.0)
        self.assertEqual(window.progress(10.0), 0.25)
        self.assertEqual(window.progress(14.0), 0.75)
        self.assertEqual(window.progress(16.0), 1.0)

    def test_progress_is_clock_sampled_for_pause_seek_and_reverse(self):
        window = parenthetical_reading_window(2.0, 12.0, 15.0, 23.0)
        self.assertIsNotNone(window)
        assert window is not None

        paused_position = 16.0
        paused = window.progress(paused_position)
        self.assertEqual(window.progress(paused_position), paused)
        forward_positions = (10.0, 12.0, 15.0, 17.0)
        forward = tuple(window.progress(position) for position in forward_positions)
        reverse = tuple(window.progress(position) for position in reversed(forward_positions))
        self.assertEqual(reverse, tuple(reversed(forward)))
        self.assertEqual(window.progress(12.0), forward[1])  # seek back to the same media time

    def test_final_row_uses_only_the_main_rows_remaining_fifth(self):
        window = parenthetical_reading_window(5.0, 15.0)

        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual((window.start_seconds, window.end_seconds), (13.0, 15.0))
        self.assertEqual(window.progress(14.0), 0.5)
        self.assertEqual(window.progress(15.0), 1.0)

    def test_missing_or_invalid_next_bounds_fall_back_to_main_end(self):
        for next_start, next_end in (
            (None, None),
            (10.0, None),
            (math.nan, 20.0),
            (12.0, math.inf),
            (14.0, 14.0),
            ("invalid", 20.0),
            (1.0, 2.0),  # first-quarter endpoint precedes the main row's final fifth
        ):
            with self.subTest(next_start=next_start, next_end=next_end):
                window = parenthetical_reading_window(0.0, 10.0, next_start, next_end)
                self.assertIsNotNone(window)
                assert window is not None
                self.assertEqual((window.start_seconds, window.end_seconds), (8.0, 10.0))

    def test_a_next_interval_may_overlap_main_when_its_window_end_is_after_aside_start(self):
        window = parenthetical_reading_window(0.0, 10.0, 7.0, 15.0)

        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual((window.start_seconds, window.end_seconds), (8.0, 9.0))

    def test_invalid_main_bounds_and_positions_are_safe(self):
        for main_start, main_end in (
            (math.nan, 10.0),
            (0.0, math.inf),
            (4.0, 4.0),
            (5.0, 4.0),
            ("invalid", 10.0),
        ):
            with self.subTest(main_start=main_start, main_end=main_end):
                self.assertIsNone(parenthetical_reading_window(main_start, main_end))

        window = parenthetical_reading_window(0.0, 10.0)
        self.assertIsNotNone(window)
        assert window is not None
        self.assertEqual(window.progress(math.nan), 0.0)
        self.assertEqual(window.progress(math.inf), 0.0)
        self.assertEqual(window.progress("invalid"), 0.0)
        self.assertEqual(window.progress(-math.inf), 0.0)


if __name__ == "__main__":
    unittest.main()
