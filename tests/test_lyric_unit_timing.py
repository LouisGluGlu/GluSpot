from __future__ import annotations

from dataclasses import dataclass
import unittest

from local.lyric_unit_timing import (
    estimated_row_read_end,
    estimated_row_reading_progress,
    explicit_slow_unit_groups,
    genuinely_timed_unit_row,
    lyric_timing_quality_label,
    real_unit_row_finished_before_next_start,
    song_has_genuine_unit_timing,
    timed_highlight_wave_progress,
    timed_unit_reading_front,
)


@dataclass(frozen=True)
class Unit:
    start_seconds: float
    end_seconds: float | None
    text: str


class TimedUnitReadingTests(unittest.TestCase):
    def test_whole_song_precision_and_estimated_preview_keep_real_clock_untouched(self):
        real = (Unit(0.0, 0.5, "真"), Unit(0.5, 1.0, "字"))
        self.assertTrue(song_has_genuine_unit_timing(("unit", "line"), (real, ())))
        self.assertFalse(song_has_genuine_unit_timing(("line", "unit"), ((), (Unit(0.0, None, "整句歌词"),))))
        self.assertEqual(estimated_row_read_end(2.0, 6.0), 5.0)
        self.assertEqual(estimated_row_reading_progress(2.0, 6.0, 3.5), 0.5)
        self.assertEqual(estimated_row_reading_progress(2.0, 6.0, 5.0), 1.0)
        self.assertEqual(estimated_row_read_end(2.0, 3.0), 3.0)
        self.assertAlmostEqual(estimated_row_reading_progress(2.0, 3.0, 2.5), 0.5)
        self.assertEqual(estimated_row_read_end(2.0, 2.8), 2.8)
        self.assertEqual(estimated_row_reading_progress(2.0, 2.8, 2.8), 1.0)
        self.assertIsNone(estimated_row_read_end(2.0, None))
        self.assertIsNone(estimated_row_reading_progress(2.0, None, 3.0))

    def test_obo_precision_requires_independent_spoken_unit_times(self):
        latin = (Unit(0.0, 1.0, "Hello "), Unit(1.0, 2.0, "world"))
        cjk = (Unit(3.0, 4.0, "你"), Unit(4.0, 5.0, "好"))
        self.assertTrue(genuinely_timed_unit_row(latin))
        self.assertTrue(genuinely_timed_unit_row(cjk))
        self.assertTrue(genuinely_timed_unit_row((Unit(45.0, 50.0, "shine"),)))
        self.assertFalse(genuinely_timed_unit_row((Unit(45.0, None, "Passionfruit"),)))
        self.assertFalse(genuinely_timed_unit_row((Unit(45.0, 45.0, "Passionfruit"),)))
        self.assertEqual(lyric_timing_quality_label(("unit", "unit"), (latin, cjk)), "真实逐字")

        sentence_markers = ((Unit(0.0, None, "Hello world"),), (Unit(3.0, None, "Good night"),))
        self.assertFalse(genuinely_timed_unit_row(sentence_markers[0]))
        self.assertFalse(genuinely_timed_unit_row((Unit(0.0, 1.0, "你好"),)))
        self.assertFalse(genuinely_timed_unit_row((Unit(0.0, 1.0, "Hello"), Unit(0.0, 1.0, "world"))))
        self.assertEqual(lyric_timing_quality_label(("unit", "unit"), sentence_markers), "行计时")
        self.assertEqual(
            lyric_timing_quality_label(("unit",), ((Unit(45.0, None, "Passionfruit"),),)),
            "行计时",
        )
        self.assertEqual(lyric_timing_quality_label(("unit", "unit"), (latin, sentence_markers[1])), "部分逐字")

    def test_real_unit_times_drive_the_front_without_advancing_future_characters(self):
        units = (
            Unit(10.0, 10.4, "谁"),
            Unit(10.4, 10.8, "人"),
            Unit(10.8, 12.1, "都"),
            Unit(12.1, 13.5, "可"),
            Unit(13.5, 15.0, "以"),
        )
        xs = (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)
        self.assertAlmostEqual(timed_unit_reading_front("谁人都可以", units, 10.2, xs), 0.5)
        self.assertAlmostEqual(timed_unit_reading_front("谁人都可以", units, 10.6, xs), 1.5)
        self.assertAlmostEqual(timed_unit_reading_front("谁人都可以", units, 11.45, xs), 2.5)
        self.assertEqual(timed_unit_reading_front("谁人都可以", units, 15.0, xs), 5.0)

    def test_explicit_slow_characters_group_only_the_trailing_three(self):
        units = (
            Unit(10.0, 10.4, "谁"),
            Unit(10.4, 10.8, "人"),
            Unit(10.8, 12.1, "都"),
            Unit(12.1, 13.5, "可"),
            Unit(13.5, 15.0, "以"),
        )
        groups = explicit_slow_unit_groups("谁人都可以", units)
        self.assertEqual(len(groups), 1)
        self.assertEqual((groups[0].start_offset, groups[0].end_offset), (2, 5))
        self.assertEqual((groups[0].start_seconds, groups[0].end_seconds), (10.8, 15.0))

    def test_real_slow_latin_word_expands_one_timed_unit_into_a_five_letter_wave(self):
        units = (Unit(45.0, 50.0, "shine"),)
        groups = explicit_slow_unit_groups("shine", units)
        self.assertEqual(len(groups), 1)
        self.assertEqual((groups[0].start_offset, groups[0].end_offset), (0, 5))
        self.assertEqual(groups[0].unit_starts, (45.0,) * 5)
        self.assertEqual(groups[0].unit_ends, (50.0,) * 5)
        self.assertAlmostEqual(timed_highlight_wave_progress(groups[0], 47.5), 0.5)
        self.assertAlmostEqual(
            timed_unit_reading_front("shine", units, 47.5, tuple(range(6))), 2.5,
        )
        # A whole CJK phrase with one timestamp has no independent character
        # durations. It may read across the phrase, but must not claim each
        # character was genuinely timed for the slow-word highlight.
        self.assertEqual(explicit_slow_unit_groups("都可以", (Unit(45.0, 50.0, "都可以"),)), ())
        spaced = (Unit(45.0, 50.0, "shine "), Unit(50.0, 51.0, "on"))
        self.assertEqual(
            [(group.start_offset, group.end_offset) for group in explicit_slow_unit_groups("shine on", spaced)],
            [(0, 5)],
        )
        self.assertAlmostEqual(
            timed_highlight_wave_progress(explicit_slow_unit_groups("shine on", spaced)[0], 49.0),
            0.8,
        )

    def test_apostrophe_words_keep_attached_punctuation_in_explicit_slow_groups(self):
        text = "To me fallin', to me goin'"
        units = (
            Unit(0.0, 0.4, "To "), Unit(0.4, 0.8, "me "),
            Unit(0.8, 2.2, "fallin', "), Unit(2.2, 2.6, "to "),
            Unit(2.6, 3.0, "me "), Unit(3.0, 4.5, "goin'"),
        )
        groups = explicit_slow_unit_groups(text, units)
        self.assertEqual([(group.start_offset, group.end_offset) for group in groups], [(6, 14), (21, 26)])
        self.assertEqual([(group.start_seconds, group.end_seconds) for group in groups], [(0.8, 2.2), (3.0, 4.5)])
        self.assertEqual(groups[0].unit_starts, (0.8,) * len("fallin',"))
        self.assertEqual(groups[1].unit_ends, (4.5,) * len("goin'"))
        self.assertAlmostEqual(timed_highlight_wave_progress(groups[0], 1.5), 0.5)

    def test_slow_contractions_still_require_explicit_duration_over_one_second(self):
        text = "I'm goin’"
        units = (Unit(0.0, 1.0, "I'm "), Unit(1.0, None, "goin’"))
        self.assertEqual(explicit_slow_unit_groups(text, units), ())
        groups = explicit_slow_unit_groups(text, (Unit(0.0, 1.1, "I'm "), Unit(1.1, 2.2, "goin’")))
        self.assertEqual([(group.start_offset, group.end_offset) for group in groups], [(0, 3), (4, 9)])

    def test_wave_respects_unequal_real_character_durations(self):
        units = (
            Unit(10.0, 11.1, "都"), Unit(11.1, 13.2, "可"), Unit(13.2, 14.3, "以"),
        )
        group = explicit_slow_unit_groups("都可以", units)[0]
        self.assertAlmostEqual(timed_highlight_wave_progress(group, 11.1), 1 / 3)
        self.assertAlmostEqual(timed_highlight_wave_progress(group, 12.15), 0.5)
        self.assertAlmostEqual(timed_highlight_wave_progress(group, 13.2), 2 / 3)

    def test_unknown_final_end_can_read_but_never_claims_explicit_long_highlight(self):
        units = (Unit(1.0, None, "一"), Unit(2.0, None, "二"))
        self.assertEqual(timed_unit_reading_front("一二", units, 1.5, (0, 10, 20), line_end=4.0), 5.0)
        self.assertEqual(timed_unit_reading_front("一二", units, 3.0, (0, 10, 20), line_end=4.0), 15.0)
        self.assertEqual(explicit_slow_unit_groups("一二", units), ())

    def test_real_obo_row_can_move_visual_focus_only_after_explicit_final_end(self):
        units = (Unit(10.0, 10.5, "Hello "), Unit(10.5, 11.3, "world"))
        check = lambda when, next_start=12.0: real_unit_row_finished_before_next_start(
            "Hello world", units, when, next_start,
        )
        self.assertFalse(check(11.29))
        self.assertTrue(check(11.3))
        self.assertTrue(check(11.99))
        self.assertFalse(check(12.0))
        self.assertFalse(check(11.4, 11.3))

    def test_visual_focus_never_guesses_a_missing_end_or_mismatched_row(self):
        missing_final = (Unit(10.0, None, "Hello "), Unit(10.5, None, "world"))
        self.assertFalse(real_unit_row_finished_before_next_start("Hello world", missing_final, 11.5, 12.0))
        explicit_final = (Unit(10.0, None, "Hello "), Unit(10.5, 11.3, "world"))
        self.assertTrue(real_unit_row_finished_before_next_start("Hello world", explicit_final, 11.5, 12.0))
        self.assertFalse(real_unit_row_finished_before_next_start("Hello there", explicit_final, 11.5, 12.0))
        self.assertFalse(real_unit_row_finished_before_next_start("Hello world", explicit_final, 11.5, None))
        self.assertFalse(real_unit_row_finished_before_next_start("Hello world", (Unit(10.0, 11.3, "Hello world"),), 11.5, 12.0))
        # A source overlap cannot claim the entire row finished just because
        # its last word did.
        overlap = (Unit(10.0, 12.2, "Hello "), Unit(10.5, 11.3, "world"))
        self.assertFalse(real_unit_row_finished_before_next_start("Hello world", overlap, 11.5, 12.0))

    def test_whitespace_collapse_maps_source_units_to_display_row(self):
        units = (Unit(0.0, 1.0, "Hello   "), Unit(1.0, 2.0, "world"))
        front = timed_unit_reading_front("Hello world", units, 1.5, tuple(range(12)))
        self.assertAlmostEqual(front, 8.5)

    def test_mismatched_text_or_bad_clock_falls_back(self):
        units = (Unit(0.0, 1.0, "hello"),)
        self.assertIsNone(timed_unit_reading_front("world", units, 0.5, tuple(range(6))))
        self.assertIsNone(timed_unit_reading_front("hello", units, 0.5, (0, 1)))
        self.assertIsNone(timed_unit_reading_front("hello", (Unit(1.0, 0.0, "hello"),), 0.5, tuple(range(6))))


if __name__ == "__main__":
    unittest.main()
