from __future__ import annotations

import unittest

from api.lyric_timeline import genuinely_timed_unit_row
from api.models import TimedLyricUnit as Unit
from local.lyric_unit_timing import (
    display_row_estimate_boundaries,
    display_row_reading_front,
    explicit_slow_unit_groups,
    resolve_display_row_timing,
)


class DisplayLyricTimingTests(unittest.TestCase):
    def test_real_words_preserve_source_unit_objects_and_irregular_clock(self):
        units = (Unit(10., 10.3, "Hello "), Unit(10.3, 15., "world"))
        plan = resolve_display_row_timing("Hello world", units, "unit", start=10., next_start=20.)
        self.assertEqual(plan.quality, "word")
        self.assertIs(plan.source_units, units)
        self.assertTrue(plan.has_reading_timing)
        self.assertFalse(plan.has_estimates)
        self.assertAlmostEqual(display_row_reading_front(plan, 12.65, tuple(range(12))), 8.5)
        self.assertEqual(plan.display_units[1].start_seconds, 10.3)
        self.assertEqual(plan.display_units[1].end_seconds, 15.)

    def test_multiword_real_segments_keep_outer_anchors_and_estimate_only_inside(self):
        units = (Unit(0., 4., "Hello world "), Unit(6., 7., "again"))
        plan = resolve_display_row_timing("Hello world again", units, "unit", start=0., next_start=10.)
        self.assertEqual(plan.quality, "segment")
        self.assertIs(plan.source_units, units)
        self.assertEqual([(u.start_seconds, u.end_seconds, u.text) for u in plan.display_units], [
            (0., 2., "Hello "), (2., 4., "world "), (6., 7., "again"),
        ])
        self.assertEqual([u.estimated for u in plan.display_units], [True, True, False])
        self.assertEqual(plan.display_units[0].timing_origin, "segment")
        self.assertEqual(plan.reading_progress(5.), 12 / 17)
        self.assertFalse(genuinely_timed_unit_row(plan.display_units))
        self.assertEqual(explicit_slow_unit_groups("Hello world again", plan.display_units)[0].source_text, "again")

    def test_cjk_segment_splits_are_estimates_not_measured_long_syllables(self):
        units = (Unit(1., 7., "都可以"),)
        plan = resolve_display_row_timing("都可以", units, "unit", start=1.)
        self.assertEqual(plan.quality, "segment")
        self.assertEqual([(u.start_seconds, u.end_seconds) for u in plan.display_units], [(1., 3.), (3., 5.), (5., 7.)])
        self.assertAlmostEqual(plan.reading_progress(4.), .5)
        self.assertFalse(genuinely_timed_unit_row(plan.display_units))
        self.assertEqual(explicit_slow_unit_groups("都可以", plan.display_units), ())

    def test_simultaneous_fragments_do_not_jump_to_last_word_at_onset(self):
        plan = resolve_display_row_timing("Hello world", (
            Unit(1., 5., "Hello "), Unit(1., 5., "world"),
        ), "unit", start=1.)
        self.assertEqual(plan.quality, "segment")
        self.assertEqual(plan.reading_progress(1.), 0.)
        self.assertGreater(plan.reading_progress(3.), 0.)
        self.assertLess(plan.reading_progress(3.), 1.)
        self.assertEqual(plan.reading_progress(5.), 1.)

    def test_line_reading_uses_row_clock_and_existing_preview_instead_of_half_second(self):
        plan = resolve_display_row_timing("Hello world", (), "line", start=10., next_start=18.)
        self.assertEqual(plan.quality, "estimated")
        self.assertTrue(plan.has_estimates)
        self.assertEqual(plan.end_seconds, 17.)
        self.assertLess(plan.reading_progress(10.5), .1)
        self.assertAlmostEqual(plan.reading_progress(13.5), 6 / 11)
        self.assertEqual(plan.reading_progress(17.), 1.)
        self.assertTrue(all(unit.timing_origin == "line" for unit in plan.display_units))
        self.assertFalse(genuinely_timed_unit_row(plan.display_units))

    def test_no_safe_end_and_synthetic_rows_stay_line_only(self):
        for kwargs in ({"next_start": None}, {"next_start": 9.}, {"next_start": float("nan")}, {"kind": "synthetic", "next_start": 18.}):
            args = {"kind": "line", "start": 10., **kwargs}
            plan = resolve_display_row_timing("Hello world", **args)
            self.assertEqual(plan.quality, "line")
            self.assertFalse(plan.has_reading_timing)
            self.assertIsNone(plan.reading_progress(11.))

    def test_missing_end_keeps_real_starts_but_marks_completion_as_estimated(self):
        units = (Unit(1., None, "你"), Unit(3., None, "好"))
        plan = resolve_display_row_timing("你好", units, "unit", start=1., next_start=8.)
        self.assertEqual(plan.quality, "word")
        self.assertEqual([(u.start_seconds, u.end_seconds) for u in plan.display_units], [(1., 3.), (3., 8.)])
        self.assertTrue(plan.has_estimates)
        self.assertFalse(genuinely_timed_unit_row(plan.display_units))
        self.assertEqual(plan.source_units, units)
        self.assertTrue(all(unit.end_seconds is None for unit in units))

    def test_missing_source_end_retains_raw_bracket_boundary_without_preview_shortening(self):
        units = (Unit(0., 1., "春"), Unit(1., None, "風"))
        plan = resolve_display_row_timing("春風", units, "unit", start=0., next_start=2.)
        self.assertEqual(plan.end_seconds, 2.)
        self.assertEqual(plan.display_units[-1].end_seconds, 2.)
        self.assertEqual(plan.display_units[-1].timing_origin, "missing-end")
        self.assertTrue(plan.display_units[-1].estimated)
        self.assertAlmostEqual(plan.reading_progress(1.5), .75)
        self.assertAlmostEqual(plan.reading_progress(1.8), .9)
        self.assertIsNone(units[-1].end_seconds)
        self.assertEqual(plan.source_units, units)
        # Ordinary row estimates retain their existing next-row preview.
        line = resolve_display_row_timing("春風", start=0., next_start=2.)
        self.assertEqual(line.end_seconds, 1.)

    def test_explicit_source_end_overlapping_bracket_remains_authoritative(self):
        units = (Unit(0., 1., "春"), Unit(1., 2.5, "風"))
        plan = resolve_display_row_timing("春風", units, "unit", start=0., next_start=2.)
        self.assertEqual(plan.end_seconds, 2.5)
        self.assertEqual(plan.display_units[-1].end_seconds, 2.5)
        self.assertFalse(plan.has_estimates)
        self.assertAlmostEqual(plan.reading_progress(1.75), .75)
        self.assertEqual(plan.source_units, units)

    def test_duet_display_overrides_do_not_replace_missing_source_end_boundary(self):
        units = (Unit(0., 1., "春"), Unit(1., None, "風"))
        original = resolve_display_row_timing("春風", units, "unit", start=0., next_start=2.)
        overridden = resolve_display_row_timing("春風", units, "unit", start=0., next_start=2.,
            estimated_start=.5, estimated_end=1.)
        self.assertEqual(original, overridden)
        self.assertEqual(overridden.end_seconds, 2.)

    def test_multiword_missing_end_keeps_distinct_source_anchors(self):
        units = (Unit(1., None, "Hello world "), Unit(7., 9., "again"))
        plan = resolve_display_row_timing("Hello world again", units, "unit", start=1., next_start=11.)
        self.assertEqual(plan.quality, "segment")
        self.assertEqual(plan.display_units[0].start_seconds, 1.)
        self.assertEqual(plan.display_units[1].end_seconds, 7.)
        self.assertEqual(plan.display_units[2].start_seconds, 7.)
        self.assertEqual(plan.display_units[2].end_seconds, 9.)

    def test_missing_tail_end_does_not_reuse_earlier_end_as_invalid_final_bound(self):
        units = (Unit(0., 1., "Hello "), Unit(3., None, "world"))
        plan = resolve_display_row_timing("Hello world", units, "unit", start=0.)
        self.assertIsNone(plan.end_seconds)
        self.assertIsNotNone(plan.reading_progress(2.))
        self.assertEqual(plan.reading_progress(3.), 1.)
        self.assertTrue(plan.display_units[-1].estimated)

    def test_whitespace_normalization_preserves_alignment_and_anchor_times(self):
        units = (Unit(1., 5., "Hello   world "), Unit(7., 9., "again"))
        plan = resolve_display_row_timing("Hello world again", units, "unit", start=1., next_start=10.)
        self.assertEqual(plan.quality, "segment")
        self.assertEqual("".join(unit.text for unit in plan.display_units), "Hello world again")
        self.assertEqual(plan.display_units[1].end_seconds, 5.)
        self.assertEqual(plan.display_units[2].start_seconds, 7.)

    def test_mismatched_source_uses_bounded_display_estimate_without_editing_source(self):
        units = (Unit(1., 3., "another lyric"),)
        plan = resolve_display_row_timing("Hello world", units, "unit", start=1., next_start=8.)
        self.assertEqual(plan.quality, "estimated")
        self.assertIs(plan.source_units, units)
        self.assertEqual(units[0].text, "another lyric")

    def test_playback_position_alone_controls_pause_seek_back_and_new_song(self):
        plan = resolve_display_row_timing("Hello world", start=10., next_start=18.)
        paused = plan.reading_progress(12.)
        for _ in range(5):
            self.assertEqual(plan.reading_progress(12.), paused)
        self.assertGreater(plan.reading_progress(15.), paused)
        self.assertLess(plan.reading_progress(11.), paused)
        next_song = resolve_display_row_timing("你好", start=0., next_start=5.)
        self.assertEqual(next_song.reading_progress(0.), 0.)
        self.assertEqual(plan.reading_progress(12.), paused)

    def test_invalid_clock_is_not_converted_to_real_timing(self):
        for units in ((Unit(float("nan"), 1., "Hello"),), (Unit(3., 1., "Hello"),)):
            plan = resolve_display_row_timing("Hello", units, "unit", start=1., next_start=8.)
            self.assertEqual(plan.quality, "estimated")
            self.assertTrue(all(unit.estimated for unit in plan.display_units))

    def test_display_interpolation_cannot_supply_reliable_interlude_evidence(self):
        from local.music_lyric_intervals import add_interlude_rows
        source = [(0., "Hello world"), (20., "Next")]
        plan = resolve_display_row_timing("Hello world", start=0., next_start=3.)
        self.assertEqual(plan.end_seconds, 2.)
        shown = add_interlude_rows(source, source, (
            plan.display_units, (Unit(20., 21., "Next"),),
        ), ("unit", "unit"))
        self.assertEqual(shown, source)

    def test_verified_estimate_boundaries_apply_only_to_line_fallback(self):
        line = resolve_display_row_timing("Hello world", start=0., next_start=10.,
            estimated_start=2., estimated_end=8.)
        self.assertEqual((line.start_seconds, line.end_seconds), (2., 8.))
        self.assertEqual(line.reading_progress(1.), 0.)
        self.assertEqual(line.reading_progress(8.), 1.)
        for units in ((Unit(0., 1., "Hello "), Unit(1., 3., "world")), (Unit(0., 3., "Hello world"),)):
            source = resolve_display_row_timing("Hello world", units, "unit", start=0., next_start=10.)
            overridden = resolve_display_row_timing("Hello world", units, "unit", start=0., next_start=10.,
                estimated_start=2., estimated_end=8.)
            self.assertEqual(source, overridden)

    def test_verified_tail_bound_allows_estimate_but_no_bound_does_not_guess(self):
        self.assertEqual(resolve_display_row_timing("Hello", start=2.).quality, "line")
        plan = resolve_display_row_timing("Hello", start=2., estimated_start=3., estimated_end=6.)
        self.assertEqual((plan.quality, plan.start_seconds, plan.end_seconds), ("estimated", 3., 6.))
        self.assertEqual(plan.reading_progress(6.), 1.)
        self.assertEqual(resolve_display_row_timing("Hello", kind="synthetic", start=2., estimated_end=6.).quality, "line")

    def test_invalid_estimate_override_does_not_force_or_reverse_a_reading_interval(self):
        for beginning, ending in ((-1., 4.), (2., 1.), (float("nan"), 4.), (1., float("nan"))):
            plan = resolve_display_row_timing("Hello", start=0., next_start=8.,
                estimated_start=beginning, estimated_end=ending)
            self.assertEqual(plan.quality, "line")
        bounded = resolve_display_row_timing("Hello", start=0., next_start=8., estimated_end=12.)
        self.assertEqual(bounded.end_seconds, 8.)

    def test_duet_boundaries_skip_peer_and_borrow_only_matching_source_onset(self):
        rows = [(0., "甲：春風"), (0., "乙：明月"), (10., "Next")]
        peer = (Unit(2., 3., "春"), Unit(3., 4., "風"))
        units = [peer, (), ()]
        first = display_row_estimate_boundaries(rows, units, 0, singer_names="甲、乙")
        second = display_row_estimate_boundaries(rows, units, 1, singer_names="甲、乙")
        self.assertEqual((first.next_start, first.estimated_end), (10., 10.))
        self.assertEqual((second.estimated_start, second.estimated_end), (2., 10.))
        staggered = [(0., rows[0][1]), (.5, rows[1][1]), rows[2]]
        self.assertEqual(display_row_estimate_boundaries(staggered, units, 1,
            singer_names="甲、乙").estimated_start, .5)

    def test_duet_borrow_skips_source_singer_prefix_and_keeps_tail_unbounded(self):
        rows = [(0., "甲：春風"), (0., "乙：明月")]
        peer = (Unit(0., .1, "甲"), Unit(.1, .2, "："), Unit(2., 3., "春"), Unit(3., 4., "風"))
        bounds = display_row_estimate_boundaries(rows, [peer, ()], 1, singer_names="甲、乙")
        self.assertEqual(bounds.estimated_start, 2.)
        self.assertIsNone(bounds.estimated_end)
        self.assertIsNone(bounds.next_start)
        plan = resolve_display_row_timing("乙：明月", start=0., next_start=bounds.next_start,
            estimated_start=bounds.estimated_start, estimated_end=bounds.estimated_end)
        self.assertEqual(plan.quality, "line")

    def test_unverified_colons_do_not_form_duet_and_same_time_rows_are_skipped(self):
        rows = [(0., "他说：我爱你"), (0., "Another row"), (8., "Next")]
        bounds = display_row_estimate_boundaries(rows, [(), (), ()], 0)
        self.assertEqual(bounds.next_start, 8.)
        self.assertIsNone(bounds.estimated_start)
        self.assertIsNone(bounds.estimated_end)

    def test_verified_singer_label_uses_no_estimated_sung_duration(self):
        text = "薛 : Hello world"
        rows = [(0., text), (0., "刘：星光"), (10., "Next")]
        bounds = display_row_estimate_boundaries(rows, [(), (), ()], 0,
            singer_names="薛之谦、刘惜君")
        self.assertEqual(bounds.body_offset, len("薛 : "))
        plan = resolve_display_row_timing(text, start=0., next_start=bounds.next_start,
            estimated_start=bounds.estimated_start, estimated_end=bounds.estimated_end,
            estimated_body_offset=bounds.body_offset)
        self.assertEqual(plan.display_units[0].text, "薛 : ")
        self.assertEqual((plan.display_units[0].start_seconds, plan.display_units[0].end_seconds), (0., 0.))
        self.assertEqual(plan.display_units[0].timing_origin, "label")
        self.assertEqual(plan.display_units[1].start_seconds, 0.)
        front = display_row_reading_front(plan, 5., tuple(range(len(text) + 1)))
        self.assertEqual(front - bounds.body_offset, 6.)
        self.assertEqual("".join(unit.text for unit in plan.display_units), text)

    def test_solo_verified_prefix_can_exclude_label_but_ordinary_colon_cannot(self):
        rows = [(0., "薛：Hello world"), (10., "Next")]
        verified = display_row_estimate_boundaries(rows, [(), ()], 0, singer_names="薛之谦")
        unverified = display_row_estimate_boundaries(rows, [(), ()], 0)
        self.assertEqual(verified.body_offset, 2)
        self.assertEqual(unverified.body_offset, 0)
        for units in ((Unit(0., 2., "薛：Hello world"),), (Unit(0., .1, "薛："), Unit(1., 2., "Hello "), Unit(2., 3., "world"))):
            original = resolve_display_row_timing(rows[0][1], units, "unit", start=0., next_start=10.)
            skipped = resolve_display_row_timing(rows[0][1], units, "unit", start=0., next_start=10., estimated_body_offset=2)
            self.assertEqual(original, skipped)


if __name__ == "__main__":
    unittest.main()
