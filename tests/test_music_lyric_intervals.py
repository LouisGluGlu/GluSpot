from __future__ import annotations

import copy
import unittest

from api.models import TimedLyricUnit
from local.music_lyric_intervals import add_interlude_rows, gap_at_position
from local.music_lyric_presentation import INTRO_COUNTDOWN_TEXT, present_timed_lyric_rows


def word(start, end, text):
    return (TimedLyricUnit(start, end, text),)


class MusicLyricIntervalsTests(unittest.TestCase):
    def display(self, source, units, kinds=None, **kwargs):
        shown = present_timed_lyric_rows(source, **kwargs)
        return add_interlude_rows(shown, source, units,
                                  kinds or ["unit"] * len(source),
                                  show_translation=kwargs.get("show_translation", True),
                                  singer_names=kwargs.get("singer_names", ()))

    def test_verified_duet_labels_do_not_need_fabricated_word_times(self):
        source = [(0., "甲：春"), (0., "乙：風"), (12., "Next")]
        units = [word(0., 1., "春"), word(0., 8., "風"), word(12., 13., "Next")]
        self.assertEqual(self.display(source, units, singer_names="甲、乙"),
            [source[0], source[1], (8., INTRO_COUNTDOWN_TEXT), source[2]])

    def test_unverified_colon_label_cannot_borrow_body_only_units(self):
        source = [(0., "他说：爱"), (12., "Next")]
        self.assertEqual(self.display(source, [word(0., 1., "爱"), word(12., 13., "Next")]), source)

    def test_exactly_three_seconds_is_not_an_interlude(self):
        source = [(0., "Hold"), (4., "Next")]
        self.assertEqual(self.display(source, [word(0., 1., "Hold"), word(4., 5., "Next")]), source)

    def test_exactly_three_seconds_with_fractional_source_times_does_not_round_up(self):
        source = [(0., "Hold"), (4.001, "Next")]
        self.assertGreater(4.001 - 1.001, 3.)
        self.assertEqual(self.display(source, [word(0., 1.001, "Hold"), word(4.001, 5., "Next")]), source)

    def test_one_millisecond_above_three_seconds_still_generates_a_gap(self):
        source = [(0., "Hold"), (4.002, "Next")]
        self.assertEqual(self.display(source, [word(0., 1.001, "Hold"), word(4.002, 5., "Next")]),
                         [(0., "Hold"), (1.001, INTRO_COUNTDOWN_TEXT), source[1]])

    def test_greater_than_three_seconds_uses_true_end_and_preserves_next_start(self):
        source = [(0., "Hold"), (4.001, "Next")]
        self.assertEqual(self.display(source, [word(0., 1., "Hold"), word(4.001, 5., "Next")]),
                         [(0., "Hold"), (1., INTRO_COUNTDOWN_TEXT), (4.001, "Next")])

    def test_lrc_and_synthetic_rows_do_not_infer_middle_gaps(self):
        source = [(0., "First"), (20., "Next")]
        for kinds in (["line", "line"], ["synthetic", "synthetic"]):
            with self.subTest(kinds=kinds):
                self.assertEqual(self.display(source, [(), ()], kinds), source)

    def test_missing_last_end_does_not_use_next_onset_as_an_estimate(self):
        source = [(0., "春風"), (20., "Next")]
        units = [(TimedLyricUnit(0., 1., "春"), TimedLyricUnit(1., None, "風")), word(20., 21., "Next")]
        self.assertEqual(self.display(source, units), source)

    def test_single_start_marker_for_whole_sentence_is_not_word_timing(self):
        source = [(0., "Whole sentence"), (20., "Next")]
        self.assertEqual(self.display(source, [word(0., 1., "Whole sentence"), word(20., 21., "Next")]), source)

    def test_units_must_match_the_retained_source_primary(self):
        source = [(0., "First"), (20., "Next")]
        self.assertEqual(self.display(source, [word(0., 1., "Other"), word(20., 21., "Next")]), source)

    def test_sustained_vocal_prevents_a_gap(self):
        source = [(0., "Hold"), (10., "Next")]
        self.assertEqual(self.display(source, [word(0., 8., "Hold"), word(10., 11., "Next")]), source)

    def test_earlier_overlapping_vocal_remains_authoritative(self):
        source = [(0., "Hold"), (2., "Echo"), (12., "Next")]
        self.assertEqual(self.display(source, [word(0., 10., "Hold"), word(2., 3., "Echo"), word(12., 13., "Next")]), source)

    def test_same_time_duet_uses_both_measured_ends(self):
        source = [(0., "甲：春"), (0., "乙：風"), (10., "Next")]
        units = [word(0., 1., "甲：春"), word(0., 8., "乙：風"), word(10., 11., "Next")]
        # The labels themselves have spoken characters, so split unit evidence
        # must reflect each source token rather than a whole-line marker.
        units[0] = tuple(TimedLyricUnit(0. + i * .1, 1., c) for i, c in enumerate("甲：春"))
        units[1] = tuple(TimedLyricUnit(0. + i * .1, 8., c) for i, c in enumerate("乙：風"))
        self.assertEqual(self.display(source, units, singer_names="甲、乙"), source)

    def test_unverified_duet_partner_prevents_a_gap(self):
        source = [(0., "A: Sing"), (0., "B: Echo"), (10., "Next")]
        self.assertEqual(self.display(source, [word(0., 1., "A: Sing"), (), word(10., 11., "Next")],
                                      ["unit", "line", "unit"], singer_names="A、B"), source)

    def test_attached_parenthetical_vocal_extends_the_gap_start(self):
        source = [(0., "Hold"), (2., "(echo)"), (10., "Next")]
        shown = self.display(source, [word(0., 1., "Hold"), word(2., 6., "(echo)"), word(10., 11., "Next")])
        self.assertEqual(shown, [(0., "Hold\n括号词：（echo）"), (6., INTRO_COUNTDOWN_TEXT), (10., "Next")])

    def test_attached_parenthetical_with_unknown_end_prevents_a_gap(self):
        source = [(0., "Hold"), (2., "(echo)"), (10., "Next")]
        expected = present_timed_lyric_rows(source)
        self.assertEqual(self.display(source, [word(0., 1., "Hold"), (), word(10., 11., "Next")],
                                      ["unit", "line", "unit"]), expected)

    def test_removed_credit_timing_does_not_fill_a_vocal_gap(self):
        source = [(0., "Hold"), (2., "作曲：Someone"), (10., "Next")]
        self.assertEqual(self.display(source, [word(0., 1., "Hold"), (), word(10., 11., "Next")],
                                      ["unit", "line", "unit"]),
                         [(0., "Hold"), (1., INTRO_COUNTDOWN_TEXT), (10., "Next")])

    def test_same_time_translation_does_not_create_an_unverified_voice(self):
        source = [(0., "Hold"), (0., "翻译：等候"), (10., "Next")]
        # This is the provider's coalesced original/translation display, while
        # the unit arrays remain indexed to the raw source rows.
        shown = [(0., "Hold\n等候"), (10., "Next")]
        self.assertEqual(add_interlude_rows(shown, source, [word(0., 1., "Hold"), (), word(10., 11., "Next")],
                                           ["unit", "line", "unit"]),
                         [(0., "Hold\n等候"), (1., INTRO_COUNTDOWN_TEXT), (10., "Next")])

    def test_translation_toggle_rebuilds_the_same_interlude(self):
        source = [(0., "Hold\n翻译：等候"), (2., "(echo)\n翻译：回声"), (10., "Next")]
        units = [word(0., 1., "Hold"), word(2., 6., "(echo)"), word(10., 11., "Next")]
        for enabled in (True, False, True):
            shown = self.display(source, units, show_translation=enabled)
            self.assertEqual([(start, text) for start, text in shown if text == INTRO_COUNTDOWN_TEXT],
                             [(6., INTRO_COUNTDOWN_TEXT)])

    def test_intro_threshold_and_true_row_times_are_unchanged(self):
        source = [(5., "Hold"), (14., "Next")]
        self.assertEqual(self.display(source, [word(5., 6., "Hold"), word(14., 15., "Next")]),
                         [(0., INTRO_COUNTDOWN_TEXT), (5., "Hold"), (6., INTRO_COUNTDOWN_TEXT), (14., "Next")])

    def test_no_tail_marker_and_no_metadata_only_fake_lyrics(self):
        self.assertEqual(self.display([(0., "Hold")], [word(0., 1., "Hold")]), [(0., "Hold")])
        self.assertEqual(self.display([(0., "作曲：Someone")], [()]), [])

    def test_repeated_generation_is_idempotent_and_source_is_unchanged(self):
        source = [(0., "Hold"), (10., "Next")]
        units = [word(0., 1., "Hold"), word(10., 11., "Next")]
        preserved = copy.deepcopy((source, units))
        shown = self.display(source, units)
        self.assertEqual(add_interlude_rows(shown, source, units, ["unit", "unit"]), shown)
        self.assertEqual((source, units), preserved)

    def test_later_measured_row_cannot_prove_an_earlier_unknown_voice_ended(self):
        source = [(0., "Untimed"), (4., "Hold"), (10., "Next")]
        self.assertEqual(self.display(source, [(), word(4., 5., "Hold"), word(10., 11., "Next")],
                                      ["line", "unit", "unit"]), source)

    def test_unknown_overlapping_duet_cannot_be_assumed_to_stop_at_next_row(self):
        source = [(0., "A: Sing"), (0., "B: Echo"), (4., "Hold"), (10., "Next")]
        self.assertEqual(self.display(
            source, [(), (), word(4., 5., "Hold"), word(10., 11., "Next")],
            ["unit", "line", "unit", "unit"], singer_names="A、B",
        ), source)

    def test_extra_backing_subrow_without_its_own_ends_blocks_the_gap(self):
        source = [(0., "Hold\n(echo)"), (10., "Next")]
        self.assertEqual(self.display(source, [word(0., 1., "Hold"), word(10., 11., "Next")]), source)

    def test_extra_untranslated_vocal_subrow_is_not_given_the_primary_end(self):
        source = [(0., "Hold\nStay"), (10., "Next")]
        self.assertEqual(self.display(source, [word(0., 1., "Hold"), word(10., 11., "Next")]), source)


class LyricGapPositionTests(unittest.TestCase):
    def setUp(self):
        self.rows = [(0., INTRO_COUNTDOWN_TEXT), (5., "Hold"),
                     (6., INTRO_COUNTDOWN_TEXT), (14., "Next")]

    def test_intro_and_interlude_have_distinct_kinds(self):
        self.assertEqual(gap_at_position(self.rows, 2.5).kind, "intro")
        self.assertEqual(gap_at_position(self.rows, 10.).kind, "interlude")

    def test_progress_uses_actual_position_and_stops_at_exact_next_onset(self):
        self.assertEqual(gap_at_position(self.rows, 6.).progress, 0.)
        self.assertEqual(gap_at_position(self.rows, 10.).progress, .5)
        self.assertIsNone(gap_at_position(self.rows, 14.))
        self.assertIsNone(gap_at_position(self.rows, 5.))

    def test_pause_seek_forward_back_and_track_switch_need_no_timer_state(self):
        self.assertEqual(gap_at_position(self.rows, 10.), gap_at_position(self.rows, 10.))
        self.assertEqual(gap_at_position(self.rows, 12.).progress, .75)
        self.assertEqual(gap_at_position(self.rows, 8.).progress, .25)
        self.assertIsNone(gap_at_position([(0., "Different")], 8.))
        self.assertEqual(gap_at_position(self.rows, 8.).progress, .25)

    def test_invalid_position_and_trailing_marker_are_not_gaps(self):
        for value in (float("nan"), float("inf"), None, "invalid", -1.):
            self.assertIsNone(gap_at_position(self.rows, value))
        self.assertIsNone(gap_at_position([(0., "Hold"), (1., INTRO_COUNTDOWN_TEXT)], 2.))


if __name__ == "__main__":
    unittest.main()
