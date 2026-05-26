from __future__ import annotations

import unittest

from api.lyric_timeline import active_index_for_position, lyric_phase_for_index


class LyricTimelineTests(unittest.TestCase):
    def test_selects_active_line_from_media_position(self):
        lines = ((0.0, "one"), (8.0, "two"), (12.0, "three"))

        self.assertEqual(active_index_for_position(lines, 7.7), 0)
        self.assertEqual(active_index_for_position(lines, 7.9), 1)
        self.assertEqual(active_index_for_position(lines, 12.8), 2)

    def test_calculates_line_phase_between_current_and_next_line(self):
        lines = ((10.0, "one"), (14.0, "two"))

        phase = lyric_phase_for_index(lines, 0, position_seconds=11.0, changed_elapsed_seconds=0.2)

        self.assertAlmostEqual(phase.progress, 0.25)
        self.assertAlmostEqual(phase.elapsed, 1.0)
        self.assertAlmostEqual(phase.remaining, 3.0)
        self.assertAlmostEqual(phase.lead_in, 0.0)
        self.assertAlmostEqual(phase.span, 4.0)
        self.assertEqual(phase.source, "timed")
        self.assertEqual(phase.active_index, 0)
        self.assertEqual(phase.previous_index, 0)
        self.assertEqual(phase.next_index, 1)
        self.assertEqual(phase.line_count, 2)
        self.assertGreater(phase.confidence, 0.7)

    def test_uses_elapsed_fallback_for_untimed_or_last_line(self):
        untimed = lyric_phase_for_index((), 0, position_seconds=0.0, changed_elapsed_seconds=1.225)
        tail = lyric_phase_for_index(((0.0, "only"),), 0, position_seconds=30.0, changed_elapsed_seconds=1.225)

        self.assertAlmostEqual(untimed.progress, 0.5)
        self.assertAlmostEqual(tail.progress, 0.5)
        self.assertLess(untimed.confidence, 0.5)
        self.assertLess(tail.confidence, 0.5)


if __name__ == "__main__":
    unittest.main()
