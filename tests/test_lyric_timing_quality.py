from __future__ import annotations

import unittest

from api.lyric_timeline import has_sufficient_word_timing, lyric_timing_quality
from api.models import TimedLyricLine as Row, TimedLyricUnit as Unit
from local.lyric_unit_timing import DisplayTimedLyricUnit


class LyricTimingQualityTests(unittest.TestCase):
    def test_one_timed_word_does_not_certify_a_long_song(self):
        rows = (Row(0., "Hello", (Unit(0., 1., "Hello"),), "unit"),
                Row(2., "This song still has a lot of untimed words"))
        quality = lyric_timing_quality(rows)
        self.assertEqual(quality.spoken_tokens, 10)
        self.assertEqual(quality.genuine_word_tokens, 1)
        self.assertEqual(quality.line_tokens, 9)
        self.assertAlmostEqual(quality.word_coverage, 1 / 10)
        self.assertFalse(has_sufficient_word_timing(rows))

    def test_attached_translation_and_romanization_do_not_dilute_original(self):
        rows = (Row(0., "你好\nHello friend\nni hao", (Unit(0., 1., "你"), Unit(1., 2., "好")), "unit"),)
        quality = lyric_timing_quality(rows)
        self.assertEqual((quality.spoken_tokens, quality.genuine_word_tokens), (2, 2))
        self.assertEqual(quality.word_coverage, 1.)
        self.assertTrue(has_sufficient_word_timing(rows))

    def test_mixed_source_segments_keep_words_but_never_inflate_them(self):
        rows = (Row(0., "Hello my good friend", (
            Unit(0., 1., "Hello "), Unit(1., 3., "my good "), Unit(3., 4., "friend"),
        ), "unit"),)
        quality = lyric_timing_quality(rows)
        self.assertEqual((quality.spoken_tokens, quality.genuine_word_tokens, quality.segment_tokens), (4, 2, 2))
        self.assertEqual((quality.word_coverage, quality.segment_coverage), (.5, .5))

    def test_latin_letters_and_syllables_count_original_word_once(self):
        rows = (Row(0., "Hello world", (
            Unit(0., .2, "He"), Unit(.2, .4, "l"), Unit(.4, .8, "lo "), Unit(1., 2., "world"),
        ), "unit"),)
        quality = lyric_timing_quality(rows)
        self.assertEqual((quality.spoken_tokens, quality.genuine_word_tokens), (2, 2))
        self.assertEqual(quality.word_coverage, 1.)

    def test_single_start_only_marker_and_mismatched_text_do_not_certify(self):
        for units in ((Unit(0., None, "Hello"),), (Unit(0., 1., "world"),)):
            with self.subTest(units=units):
                quality = lyric_timing_quality((Row(0., "Hello", units, "unit"),))
                self.assertEqual(quality.genuine_word_tokens, 0)

    def test_same_start_fragments_are_not_independent_word_times(self):
        quality = lyric_timing_quality((Row(0., "Hello world", (
            Unit(0., 1., "Hello "), Unit(0., 1., "world"),
        ), "unit"),))
        self.assertEqual(quality.genuine_word_tokens, 0)
        self.assertEqual(quality.segment_tokens, 2)
        self.assertFalse(has_sufficient_word_timing(()))

    def test_display_estimates_and_synthetic_rows_never_certify(self):
        unit = DisplayTimedLyricUnit(0., 1., "Hello", True, "line")
        quality = lyric_timing_quality((Row(0., "Hello", (unit,), "unit"), Row(2., "world", (), "synthetic")))
        self.assertEqual((quality.spoken_tokens, quality.genuine_word_tokens, quality.estimated_tokens), (2, 0, 2))

    def test_threshold_uses_tokens_instead_of_row_count(self):
        rows = [Row(0., " ".join(["word"] * 19), tuple(Unit(float(i), float(i + 1), "word " if i < 18 else "word") for i in range(19)), "unit"), Row(20., "end")]
        self.assertEqual(lyric_timing_quality(rows).word_coverage, .95)
        self.assertTrue(has_sufficient_word_timing(rows))
        self.assertFalse(has_sufficient_word_timing(rows, minimum_coverage=.96))
        for invalid in (0., -1., 1.1, float("nan")):
            self.assertFalse(has_sufficient_word_timing(rows, minimum_coverage=invalid))

    def test_malformed_or_nonfinite_clocks_leave_source_as_line_quality(self):
        for units in ((Unit(float("nan"), 1., "Hello"),), (Unit(2., 1., "Hello"),)):
            quality = lyric_timing_quality((Row(0., "Hello", units, "unit"),))
            self.assertEqual((quality.genuine_word_tokens, quality.line_tokens), (0, 1))


if __name__ == "__main__":
    unittest.main()
