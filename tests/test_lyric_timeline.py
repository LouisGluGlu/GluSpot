from __future__ import annotations

import unittest

from api.lyric_timeline import (
    active_index_for_position,
    active_index_near_position,
    lyric_phase_for_index,
    lyric_phase_near_position,
    lyric_timeline_diagnostics,
    smoothed_lyric_phase_state,
)


class LyricTimelineTests(unittest.TestCase):
    def test_selects_active_line_from_media_position(self):
        lines = ((0.0, "one"), (8.0, "two"), (12.0, "three"))

        self.assertEqual(active_index_for_position(lines, 7.7), 0)
        self.assertEqual(active_index_for_position(lines, 7.9), 1)
        self.assertEqual(active_index_for_position(lines, 12.8), 2)

    def test_active_line_lookup_uses_indexed_access_without_linear_iteration(self):
        class IndexedTimedLines:
            def __init__(self, count: int):
                self.count = count

            def __len__(self):
                return self.count

            def __getitem__(self, index: int):
                if index < 0 or index >= self.count:
                    raise IndexError(index)
                return (float(index * 4), f"line {index}")

            def __iter__(self):
                raise AssertionError("active line lookup should not linearly iterate every lyric")

        self.assertEqual(active_index_for_position(IndexedTimedLines(1000), 321.0, lead_seconds=0.0), 80)

    def test_active_line_near_lookup_advances_from_current_index_without_binary_search(self):
        class CountingTimedLines:
            def __init__(self, count: int):
                self.count = count
                self.accesses = 0

            def __len__(self):
                return self.count

            def __getitem__(self, index: int):
                if index < 0 or index >= self.count:
                    raise IndexError(index)
                self.accesses += 1
                return (float(index * 4), f"line {index}")

            def __iter__(self):
                raise AssertionError("near lookup should not linearly iterate every lyric")

        lines = CountingTimedLines(1000)

        self.assertEqual(active_index_near_position(lines, 324.2, current_index=80, lead_seconds=0.0), 81)
        self.assertLessEqual(lines.accesses, 5)

    def test_active_line_near_lookup_falls_back_for_large_seek_without_linear_scan(self):
        class IndexedTimedLines:
            def __init__(self, count: int):
                self.count = count
                self.accesses = 0

            def __len__(self):
                return self.count

            def __getitem__(self, index: int):
                if index < 0 or index >= self.count:
                    raise IndexError(index)
                self.accesses += 1
                return (float(index * 4), f"line {index}")

            def __iter__(self):
                raise AssertionError("large seek fallback should still avoid linear iteration")

        lines = IndexedTimedLines(1000)

        self.assertEqual(active_index_near_position(lines, 3200.0, current_index=12, lead_seconds=0.0), 800)
        self.assertLess(lines.accesses, 40)

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

    def test_position_phase_can_advance_visual_index_without_mutating_sync_index(self):
        lines = ((0.0, "one"), (4.0, "two"), (8.0, "three"))

        phase = lyric_phase_near_position(lines, 4.25, current_index=0, changed_elapsed_seconds=0.3, lead_seconds=0.0)

        self.assertEqual(phase.active_index, 1)
        self.assertAlmostEqual(phase.progress, 0.0625)
        self.assertEqual(phase.source, "timed")

    def test_smoothed_phase_state_prevents_small_paint_progress_backstep(self):
        phase = lyric_phase_for_index(
            ((0.0, "one"), (4.0, "two")),
            0,
            position_seconds=1.2,
            changed_elapsed_seconds=1.0,
        )

        state = smoothed_lyric_phase_state(
            phase,
            previous_index=0,
            previous_progress=0.5,
            previous_at=20.0,
            now=20.4,
            media_is_playing=True,
            transition_kind="",
        )

        self.assertGreaterEqual(state["progress"], 0.5)

    def test_lyric_timeline_diagnostics_reports_position_lag_without_mutation(self):
        lines = ((0.0, "one"), (4.0, "two"), (8.0, "three"))

        diagnostics = lyric_timeline_diagnostics(
            timed_lyrics=lines,
            lyrics=("one", "two", "three"),
            lyric_index=0,
            estimated_position_seconds=8.2,
            lyric_offset_seconds=0.0,
            changed_elapsed_seconds=0.3,
            previous_phase_index=0,
            previous_phase_progress=0.2,
            previous_phase_at=20.0,
            now=20.1,
            media_is_playing=True,
        ).as_dict()

        self.assertEqual(diagnostics["visible_lyric_index"], 0)
        self.assertEqual(diagnostics["visible_current_lyric"], "one")
        self.assertEqual(diagnostics["position_active_index"], 2)
        self.assertEqual(diagnostics["sync_lag_lines"], 2)
        self.assertEqual(diagnostics["timed_lyrics_count"], 3)
        self.assertTrue(diagnostics["timed_lyrics_available"])
        self.assertAlmostEqual(diagnostics["current_line_start_seconds"], 0.0, delta=0.001)
        self.assertAlmostEqual(diagnostics["next_line_start_seconds"], 4.0, delta=0.001)
        self.assertEqual(diagnostics["phase_source"], "timed")


if __name__ == "__main__":
    unittest.main()
