from __future__ import annotations

import unittest
import time
from unittest.mock import patch

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from api.models import TimedLyricUnit
from local.lyric_unit_timing import TimedHighlightGroup, explicit_slow_unit_groups
from local.music_lyric_motion import (
    GLYPH_FLOAT_LIFT,
    READING_LIFT_FULL_BANDS,
    READING_LIFT_SAMPLES_PER_BAND,
    continuous_read_lift,
    sparse_glyph_frame,
)
from local.music_surface_window import MusicSurfaceWindow


REQUESTED_POSES = (
    (0, 0, 0, 0, 0, 0),
    (1, 1, 0, 0, 0, 0),
    (2, 1, 1, 0, 0, 0),
    (2, 2, 1, 1, 0, 0),
    (3, 2, 2, 1, 1, 0),
    (3, 3, 2, 2, 1, 1),
    (3, 3, 3, 2, 2, 1),
    (3, 3, 3, 3, 2, 2),
    (3, 3, 3, 3, 3, 3),
)


class ContinuousReadingLiftTests(unittest.TestCase):
    def test_six_units_retain_the_requested_staircase_as_representative_poses(self):
        for frame, expected in enumerate(REQUESTED_POSES):
            with self.subTest(frame=frame):
                actual = tuple(continuous_read_lift(frame / 8.0, index, 6) * 3.0 for index in range(6))
                # Exact pose-by-pose interpolation alternated odd/even
                # motion. The continuous per-unit rise preserves the shape
                # within one logical level, without inserting those stops.
                self.assertTrue(all(abs(a - b) < 0.7 for a, b in zip(actual, expected)))
                self.assertEqual(tuple(a > 0.0 for a in actual), tuple(b > 0 for b in expected))

    def test_each_launched_unit_keeps_moving_until_it_lands_without_odd_even_stalls(self):
        for count in (1, 2, 6, 17, 48):
            for index in range(count):
                heights = [continuous_read_lift(step / 4096.0, index, count) for step in range(4097)]
                moving = [(a, b) for a, b in zip(heights, heights[1:]) if 0.0 < a < 1.0 and b < 1.0]
                with self.subTest(count=count, index=index):
                    self.assertTrue(moving)
                    self.assertTrue(all(b > a for a, b in moving))
                    self.assertLess(max(b - a for a, b in zip(heights, heights[1:])), 1.0 / 16.0)

    def test_intermediate_positions_are_smooth_monotone_and_never_drop(self):
        for count in (1, 2, 6, 17):
            for index in range(count):
                with self.subTest(count=count, index=index):
                    heights = [continuous_read_lift(step / 2048.0, index, count) for step in range(2049)]
                    self.assertEqual(heights[0], 0.0)
                    self.assertEqual(heights[-1], 1.0)
                    self.assertEqual(heights, sorted(heights))
                    # No unit can jump by a whole logical height band.
                    self.assertLess(max(b - a for a, b in zip(heights, heights[1:])), 1.0 / 16.0)
        self.assertEqual(continuous_read_lift(-1.0, 0, 6), 0.0)
        self.assertEqual(continuous_read_lift(2.0, 5, 6), 1.0)

    def test_invalid_clock_values_do_not_crash_or_displace_unread_ink(self):
        for value in (None, float("nan"), float("-inf"), "invalid"):
            with self.subTest(value=value):
                self.assertEqual(continuous_read_lift(value, 0, 6), 0.0)
        self.assertEqual(continuous_read_lift(float("inf"), 5, 6), 1.0)
        self.assertEqual(continuous_read_lift(0.5, None, 6), 0.0)
        self.assertEqual(continuous_read_lift(0.5, 0, float("nan")), 0.0)

    def test_followers_never_overtake_the_preceding_unit_at_any_phase(self):
        for count in (1, 2, 6, 17, 48):
            for tick in range(1025):
                heights = [continuous_read_lift(tick / 1024.0, index, count) for index in range(count)]
                with self.subTest(count=count, tick=tick):
                    self.assertTrue(all(lead + 1e-12 >= follower for lead, follower in zip(heights, heights[1:])))

    def test_sparse_highlight_white_finishes_at_half_while_eight_stage_wave_continues(self):
        at_half = [sparse_glyph_frame(0.5, index, 5) for index in range(5)]
        self.assertTrue(all(frame.read_fraction == 1.0 for frame in at_half))
        self.assertTrue(any(frame.glow > 0.0 for frame in at_half))
        self.assertTrue(any(frame.scale > 1.0 for frame in at_half))
        self.assertTrue(all(sparse_glyph_frame(0.0, index, 5).read_fraction == 0.0 for index in range(5)))
        self.assertTrue(all(sparse_glyph_frame(1.0, index, 5).glow == 0.0 for index in range(5)))

    def test_highlight_uses_same_overlapping_rise_at_character_granularity(self):
        for count in (2, 5, 17, 48):
            overlap_seen = False
            for tick in range(257):
                progress = tick / 256.0
                heights = [sparse_glyph_frame(progress, index, count).lift for index in range(count)]
                for index, height in enumerate(heights):
                    self.assertAlmostEqual(height, GLYPH_FLOAT_LIFT * continuous_read_lift(progress, index, count))
                overlap_seen |= any(0.0 < follower <= lead < GLYPH_FLOAT_LIFT
                                    for lead, follower in zip(heights, heights[1:]))
            self.assertTrue(overlap_seen)


class MusicSurfaceContinuousLiftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.owner._music_windowed_preference = False
        self.window = MusicSurfaceWindow(self.owner)
        self.font = self.window._surface_font(48, QFont.Weight.Bold)
        self.output = QImage(600, 120, QImage.Format.Format_ARGB32_Premultiplied)
        self.output.fill(Qt.GlobalColor.transparent)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def positions(self, text, progress):
        with QPainter(self.output) as painter, patch.object(self.window, "_draw_sparse_static_run", return_value=False), patch.object(
            self.window, "_draw_continuous_read_unit",
        ) as draw:
            self.window._paint_lifted_run(painter, QPointF(10.0, 10.0), 90.0, text, self.font, progress)
        return tuple(call.args[2].y() for call in draw.call_args_list)

    def test_actual_latin_and_cjk_painter_follows_continuous_staircase_at_each_height(self):
        scale = max(0.5, self.font.pixelSize() / self.window.LYRIC_PIXEL_SIZE)
        for level in (1, 2, 4):
            self.owner.music_surface_reading_lift_level = level
            height = GLYPH_FLOAT_LIFT * level / 4.0 * scale
            for text in ("A B C D E F", "甲乙丙丁戊己"):
                start = self.positions(text, 0.0)
                self.assertEqual(len(start), 6)
                for frame in range(9):
                    current = self.positions(text, frame / 8.0)
                    with self.subTest(level=level, text=text, frame=frame):
                        for index in range(6):
                            self.assertAlmostEqual(start[index] - current[index], height * continuous_read_lift(frame / 8.0, index, 6))

    def test_actual_painter_never_alternates_odd_even_velocity_platforms(self):
        for level in (1, 2, 4):
            self.owner.music_surface_reading_lift_level = level
            for text in ("A B C D E F", "甲乙丙丁戊己"):
                baseline = self.positions(text, 0.0)
                frames = [self.positions(text, step / 256.0) for step in range(257)]
                lifts = [tuple(start - y for start, y in zip(baseline, frame)) for frame in frames]
                full = lifts[-1][0]
                with self.subTest(level=level, text=text):
                    for heights in lifts:
                        self.assertTrue(all(a + 1e-10 >= b for a, b in zip(heights, heights[1:])))
                    for index in range(6):
                        middle = [(a[index], b[index]) for a, b in zip(lifts, lifts[1:])
                                  if full * 0.01 < a[index] < full * 0.99 and b[index] < full * 0.99]
                        self.assertTrue(middle)
                        self.assertTrue(all(b - a > 1e-8 for a, b in middle))

    def test_painter_supports_160_80_40_distinct_positions_without_extra_clock_ticks(self):
        scale = max(0.5, self.font.pixelSize() / self.window.LYRIC_PIXEL_SIZE)
        for level, expected_samples in ((1, 40), (2, 80), (4, 160)):
            with self.subTest(level=level):
                self.owner.music_surface_reading_lift_level = level
                bands = READING_LIFT_FULL_BANDS * level // 4
                samples = bands * READING_LIFT_SAMPLES_PER_BAND
                self.assertEqual(samples, expected_samples)
                # The existing playback progress may sample this continuous
                # curve at any cadence; the lift adds no independent timer.
                positions = [self.positions("shine", step / samples)[0] for step in range(samples + 1)]
                self.assertGreaterEqual(len(set(round(y, 6) for y in positions)), expected_samples)
                self.assertEqual(positions, sorted(positions, reverse=True))
                self.assertAlmostEqual(positions[0] - positions[-1], GLYPH_FLOAT_LIFT * level / 4.0 * scale)
                self.assertEqual(self.positions("shine", 0.5), self.positions("shine", 0.5))

    def test_wrapped_sentence_keeps_one_wave_across_visual_rows(self):
        text = "one two three four five six seven eight"
        layout, height = self.window._wrapped_lyric_layout(text, self.font, 250.0)
        runs = self.window._wrapped_lyric_runs(text, self.font, layout)
        self.assertGreater(layout.lineCount(), 2)
        total_units = len(self.window._reading_unit_offsets(text)) - 1
        self.assertEqual(sum(run[4] for run in runs), total_units)
        captures = []

        def capture(_painter, _origin, _height, segment, _font, local, _color, _mode, **kwargs):
            captures.append((segment, local, kwargs))

        with QPainter(self.output) as painter, patch.object(self.window, "_paint_lifted_run", side_effect=capture):
            self.window._paint_wrapped_lifted_lyric(
                painter, QRectF(10.0, 10.0, 250.0, height), text, self.font, layout, 0.35,
                runs=runs,
            )
        self.assertEqual(len(captures), len(runs))
        self.assertEqual(tuple(capture[1] for capture in captures), self.window._visual_line_progresses(
            0.35, tuple(run[4] for run in runs),
        ))
        self.assertEqual(tuple(index for _segment, _local, kwargs in captures
                               for index in kwargs["lift_unit_indices"]), tuple(range(total_units)))
        self.assertTrue(all(kwargs["lift_unit_count"] == total_units for _segment, _local, kwargs in captures))
        self.assertTrue(all(kwargs["lift_progress"] == 0.35 for _segment, _local, kwargs in captures))

        # An exact wrapped boundary must not restart the next row's lift.
        next_row = captures[1][2]
        first_index = next_row["lift_unit_indices"][0]
        self.assertGreater(first_index, 0)
        self.assertGreater(continuous_read_lift(0.35, first_index, total_units), 0.0)

    def test_genuine_unit_pause_holds_wrapped_wave_and_local_read_front(self):
        text = "甲乙丙丁戊己庚辛壬癸"
        layout, height = self.window._wrapped_lyric_layout(text, self.font, 130.0)
        self.assertGreater(layout.lineCount(), 1)
        runs = self.window._wrapped_lyric_runs(text, self.font, layout)
        frames = []

        def capture(_painter, _origin, _height, segment, _font, local, _color, _mode, **kwargs):
            frames.append((segment, local, kwargs["lift_progress"], kwargs["lift_unit_indices"]))

        real_local_progresses = (1.0,) + (0.0,) * (len(runs) - 1)
        with QPainter(self.output) as painter, patch.object(self.window, "_paint_lifted_run", side_effect=capture):
            for reported_row_progress in (0.20, 0.40):
                self.window._paint_wrapped_lifted_lyric(
                    painter, QRectF(10.0, 10.0, 130.0, height), text, self.font,
                    layout, reported_row_progress, runs=runs,
                    real_progresses=real_local_progresses,
                )
        middle = len(runs)
        self.assertEqual(frames[:middle], frames[middle:])
        self.assertTrue(all(item[1] == 0.0 for item in frames[1:middle]))
        self.assertGreater(frames[0][2], 0.0)
        self.assertLess(frames[0][2], 1.0)

    def test_forced_wrap_inside_one_word_keeps_one_lift_identity(self):
        text = "supercalifragilisticexpialidocious"
        layout, height = self.window._wrapped_lyric_layout(text, self.font, 130.0)
        self.assertGreater(layout.lineCount(), 2)
        captures = []

        def capture(_painter, _origin, _height, _segment, _font, _local, _color, _mode, **kwargs):
            captures.append((kwargs["lift_unit_indices"], kwargs["lift_unit_count"]))

        with QPainter(self.output) as painter, patch.object(self.window, "_paint_lifted_run", side_effect=capture):
            self.window._paint_wrapped_lifted_lyric(
                painter, QRectF(10.0, 10.0, 130.0, height), text, self.font, layout, 0.5,
            )
        self.assertEqual(len(captures), layout.lineCount())
        self.assertTrue(all(indices == (0,) and count == 1 for indices, count in captures))

    def test_real_long_first_word_advances_wave_by_spoken_units_across_wraps(self):
        text = "supercalifragilisticexpialidocious a b"
        units = (
            TimedLyricUnit(10.0, 12.0, "supercalifragilisticexpialidocious "),
            TimedLyricUnit(13.0, 14.0, "a "),
            TimedLyricUnit(15.0, 16.0, "b"),
        )
        layout, height = self.window._wrapped_lyric_layout(text, self.font, 130.0)
        self.assertGreater(layout.lineCount(), 2)
        samples = {}

        def capture(_painter, _origin, _height, _segment, _font, _local, _color, _mode, **kwargs):
            samples.setdefault(position, []).append(kwargs["lift_progress"])

        with QPainter(self.output) as painter, patch.object(self.window, "_paint_lifted_run", side_effect=capture):
            for position in (11.0, 12.0, 12.5, 13.5):
                progresses = self.window._real_visual_line_progresses(
                    text, layout, units, position, line_end=16.0,
                )
                self.window._paint_wrapped_lifted_lyric(
                    painter, QRectF(10.0, 10.0, 130.0, height), text, self.font,
                    layout, 0.0, real_progresses=progresses,
                )
        self.assertGreater(samples[11.0][0], 0.0)
        self.assertLess(samples[11.0][0], 1.0 / 3.0)
        self.assertAlmostEqual(samples[12.0][0], 1.0 / 3.0)
        self.assertEqual(samples[12.0], samples[12.5])
        self.assertGreater(samples[13.5][0], 1.0 / 3.0)
        self.assertLess(samples[13.5][0], 2.0 / 3.0)
        self.assertTrue(all(len(set(values)) == 1 for values in samples.values()))

    def test_genuine_unit_clock_keeps_wrapped_ordinary_step_order_over_time(self):
        text = "supercalifragilisticexpialidocious a b c d"
        units = (
            TimedLyricUnit(10.0, 12.0, "supercalifragilisticexpialidocious "),
            TimedLyricUnit(12.0, 12.8, "a "),
            TimedLyricUnit(12.8, 13.6, "b "),
            TimedLyricUnit(13.6, 14.4, "c "),
            TimedLyricUnit(14.4, 15.2, "d"),
        )
        layout, _height = self.window._wrapped_lyric_layout(text, self.font, 135.0)
        self.assertGreater(layout.lineCount(), 2)
        runs = self.window._wrapped_lyric_runs(text, self.font, layout)
        fronts = []
        for tick in range(131):
            position = 10.0 + tick * 0.04
            actual = self.window._real_visual_line_progresses(text, layout, units, position, line_end=15.2)
            front, mappings, count = self.window._wrapped_lift_wave(
                text, self.font, layout, runs, 0.0, "reading", actual,
            )
            ordinals = tuple(index for line in mappings for index in line)
            self.assertEqual(ordinals, tuple(sorted(ordinals)))
            heights = [continuous_read_lift(front, index, count) for index in range(count)]
            self.assertTrue(all(lead + 1e-12 >= next_ for lead, next_ in zip(heights, heights[1:])))
            fronts.append(front)
        self.assertEqual(fronts, sorted(fronts))
        self.assertGreater(fronts[-1], 0.99)

    def test_unmatched_sparse_glyph_uses_sentence_wave_without_retiming_highlight(self):
        group = TimedHighlightGroup(
            start_offset=0, end_offset=1, start_seconds=10.0, end_seconds=12.0,
            unit_starts=(10.0,), unit_ends=(12.0,),
        )
        first = self.window._real_sparse_glyph_states(
            "甲乙", self.font, 0, (group,), 0.9, 11.0,
            wave_progress=0.25, wave_unit_indices=(0, 1), wave_unit_count=6,
        )
        following = self.window._real_sparse_glyph_states(
            "丙丁", self.font, 2, (group,), 0.0, 11.0,
            wave_progress=0.25, wave_unit_indices=(2, 3), wave_unit_count=6,
        )
        self.assertEqual(first[0], sparse_glyph_frame(0.5, 0, 1))
        for state, index in zip((first[1],) + following, (1, 2, 3)):
            self.assertAlmostEqual(state.lift, GLYPH_FLOAT_LIFT * continuous_read_lift(0.25, index, 6))

    def test_real_highlight_group_reads_by_half_elapsed_without_changing_ordinary_front(self):
        text = "shine next"
        units = (
            TimedLyricUnit(10.0, 15.0, "shine "),
            TimedLyricUnit(15.0, 15.5, "next"),
        )
        groups = explicit_slow_unit_groups(text, units)
        self.assertEqual(len(groups), 1)
        states = self.window._real_sparse_glyph_states(
            text, self.font, 0, groups, 0.12, 12.5,
        )
        self.assertTrue(all(state.highlighted and state.read_fraction == 1.0 for state in states[:5]))
        self.assertTrue(any(state.glow > 0.0 for state in states[:5]))
        self.assertTrue(all(not state.highlighted for state in states[5:]))
        early = self.window._real_sparse_glyph_states(text, self.font, 0, groups, 0.0, 10.05)
        self.assertTrue(0.0 < early[0].read_fraction < 1.0)
        self.assertTrue(all(state.read_fraction == 0.0 for state in early[1:5]))

        image, _boundaries = self.window._glyph_run(text, self.font, "character")
        for source, _left in self.window._isolated_glyph_units(text, self.font, "character"):
            self.window._sparse_glyph_halo(source)
        output = QImage(800, 150, QImage.Format.Format_ARGB32_Premultiplied)
        output.fill(Qt.GlobalColor.transparent)
        painted_fronts = []
        draw = self.window._draw_continuous_read_unit

        def capture(*args, **kwargs):
            painted_fronts.append(args[4])
            return draw(*args, **kwargs)

        with QPainter(output) as painter, patch.object(
            self.window, "_draw_sparse_static_run", return_value=False,
        ), patch.object(self.window, "_draw_continuous_read_unit", side_effect=capture):
            self.window._paint_sparse_glyph_run(
                painter, QPointF(10.0, 10.0), 90.0, text, self.font,
                0.12, QColor(130, 90, 80), glyph_states=states,
            )
        original_front = self.window._reading_front(0.12, image.width(), self.font, text)[0]
        self.assertTrue(all(front > original_front for front in painted_fronts[:5]))
        self.assertTrue(all(abs(front - original_front) < 1e-6 for front in painted_fronts[5:]))

    def test_cold_highlight_reads_its_span_by_half_without_whitening_ordinary_words(self):
        text = "shine next"
        units = (
            TimedLyricUnit(10.0, 15.0, "shine "),
            TimedLyricUnit(15.0, 15.5, "next"),
        )
        groups = explicit_slow_unit_groups(text, units)
        layout, height = self.window._wrapped_lyric_layout(text, self.font, 750.0)
        row = QRectF(20.0, 20.0, 750.0, height)
        ordinary_left = int(row.left() + layout.lineAt(0).cursorToX(self.window._utf16_offset(text, 6))[0])

        def render(position, with_highlight):
            output = QImage(800, 150, QImage.Format.Format_ARGB32_Premultiplied)
            output.fill(Qt.GlobalColor.transparent)
            local = self.window._real_visual_line_progresses(text, layout, units, position, line_end=15.5)
            with QPainter(output) as painter:
                self.window._paint_cold_emphasis_reading(
                    painter, row, text, self.font, layout, 0.0,
                    QColor(130, 90, 80), (0.0,),
                    real_progresses=local,
                    highlight_groups=groups if with_highlight else (),
                    highlight_position=position,
                )
            return output

        before_a, before_b = render(10.0, False), render(10.0, True)
        self.assertEqual(bytes(before_a.constBits()), bytes(before_b.constBits()))
        original, advanced = render(12.5, False), render(12.5, True)
        self.assertTrue(any(
            original.pixelColor(x, y) != advanced.pixelColor(x, y)
            for y in range(original.height()) for x in range(int(row.left()), ordinary_left)
        ))
        self.assertTrue(all(
            original.pixelColor(x, y) == advanced.pixelColor(x, y)
            for y in range(original.height()) for x in range(ordinary_left + 2, original.width())
        ))

    def test_timed_trailing_space_or_apostrophe_does_not_drop_final_highlight_glyph(self):
        for text in ("shine next", "fallin' next"):
            spoken = text.split()[0]
            units = (
                TimedLyricUnit(10.0, 15.0, spoken + " "),
                TimedLyricUnit(15.0, 15.5, "next"),
            )
            groups = explicit_slow_unit_groups(text, units)
            states = self.window._real_sparse_glyph_states(
                text, self.font, 0, groups, 0.5, 12.5,
            )
            glyph_offsets = self.window._character_unit_offsets(text)
            trailing_glyph = max(index for index, start in enumerate(glyph_offsets[:-1]) if start < len(spoken))
            with self.subTest(text=text):
                self.assertTrue(states[trailing_glyph].highlighted)
                self.assertEqual(states[trailing_glyph].read_fraction, 1.0)

    def test_real_highlight_counts_combining_letters_as_one_grapheme_across_wraps(self):
        text = "a\u0301bcdefghijklmnop"
        units = (TimedLyricUnit(10.0, 18.0, text),)
        groups = explicit_slow_unit_groups(text, units)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].source_text, text)
        layout, _height = self.window._wrapped_lyric_layout(text, self.font, 160.0)
        runs = self.window._wrapped_lyric_runs(text, self.font, layout, "character")
        self.assertGreater(layout.lineCount(), 1)
        whole = self.window._real_sparse_glyph_states(text, self.font, 0, groups, 0.2, 12.0)
        self.assertEqual(len(whole), len(text) - 1)
        wrapped = []
        for index, (segment, *_rest) in enumerate(runs):
            offset = len(self.window._utf16_slice(text, 0, layout.lineAt(index).textStart()))
            wrapped.extend(self.window._real_sparse_glyph_states(segment, self.font, offset, groups, 0.2, 12.0))
        self.assertEqual(tuple(wrapped), whole)
        for index, state in enumerate(whole):
            self.assertAlmostEqual(state.lift, GLYPH_FLOAT_LIFT * continuous_read_lift(0.25, index, len(whole)))

    def test_projection_retains_grapheme_source_for_highlight_after_singer_prefix(self):
        text = "a\u0301bcde"
        source = "Alex：" + text
        group = explicit_slow_unit_groups(source, (
            TimedLyricUnit(9.0, 9.3, "Alex："), TimedLyricUnit(10.0, 15.0, text),
        ))
        self.assertEqual(len(group), 1)
        prefix = len("Alex：")
        projected = self.window._project_highlight_groups(group, tuple(range(prefix, len(source) + 1)))
        self.assertEqual(projected[0].source_text, text)
        self.assertEqual((projected[0].start_offset, projected[0].end_offset), (0, len(text)))

    def test_real_group_does_not_emit_halo_until_that_glyph_is_white(self):
        text = "shine"
        units = (TimedLyricUnit(10.0, 15.0, text),)
        groups = explicit_slow_unit_groups(text, units)
        for source, _left in self.window._isolated_glyph_units(text, self.font, "character"):
            self.window._sparse_glyph_halo(source)
        original = self.window._draw_subpixel_lyric_image
        for position, expected_halo in ((10.05, False), (10.6, True)):
            states = self.window._real_sparse_glyph_states(
                text, self.font, 0, groups, (position - 10.0) / 5.0, position,
            )
            self.assertGreater(states[0].glow, 0.0)
            self.assertEqual(states[0].read_fraction >= 1.0, expected_halo)
            halo_draws = []

            def capture(painter, origin, image):
                if painter.compositionMode() == QPainter.CompositionMode.CompositionMode_Screen:
                    halo_draws.append(image.cacheKey())
                return original(painter, origin, image)

            output = QImage(500, 120, QImage.Format.Format_ARGB32_Premultiplied)
            output.fill(Qt.GlobalColor.transparent)
            with QPainter(output) as painter, patch.object(
                self.window, "_draw_subpixel_lyric_image", side_effect=capture,
            ):
                self.window._paint_sparse_glyph_run(
                    painter, QPointF(10.0, 10.0), 90.0, text, self.font,
                    (position - 10.0) / 5.0, QColor(130, 90, 80), glyph_states=states,
                )
            with self.subTest(position=position):
                self.assertEqual(bool(halo_draws), expected_halo)

    def test_mixed_highlight_raster_keeps_ordinary_word_ordinals_through_wraps(self):
        text = "shine through a quiet moment while the melody holds"
        layout, _height = self.window._wrapped_lyric_layout(text, self.font, 250.0)
        self.assertGreater(layout.lineCount(), 1)
        runs = self.window._wrapped_lyric_runs(text, self.font, layout, "character")
        progress, mappings, count = self.window._wrapped_lift_wave(
            text, self.font, layout, runs, 0.32, "character", None,
        )
        self.assertEqual(progress, 0.32)
        self.assertEqual(count, len(self.window._reading_unit_offsets(text)) - 1)
        flat = tuple(index for indices in mappings for index in indices)
        self.assertEqual(flat, tuple(sorted(flat)))
        self.assertTrue(any(left == right for left, right in zip(flat, flat[1:])))
        heights = [continuous_read_lift(progress, index, count) for index in flat]
        self.assertTrue(all(left + 1e-12 >= right for left, right in zip(heights, heights[1:])))

    def test_real_wrapped_mixed_painter_keeps_ordinary_words_in_order_and_in_motion(self):
        text = "A B C shine D E F"
        words = text.split()
        starts = (10.0, 10.5, 11.0, 11.5, 15.0, 15.5, 16.0)
        ends = (10.5, 11.0, 11.5, 15.0, 15.5, 16.0, 16.5)
        units = tuple(TimedLyricUnit(start, end, word + (" " if index < len(words) - 1 else ""))
                      for index, (word, start, end) in enumerate(zip(words, starts, ends)))
        groups = explicit_slow_unit_groups(text, units)
        self.assertEqual(len(groups), 1)
        layout, _height = self.window._wrapped_lyric_layout(text, self.font, 180.0)
        self.assertGreater(layout.lineCount(), 1)
        runs = self.window._wrapped_lyric_runs(text, self.font, layout, "character")
        for segment, *_rest in runs:
            for source, _left in self.window._isolated_glyph_units(segment, self.font, "character"):
                self.window._sparse_glyph_halo(source)

        def painted(position):
            actual = self.window._real_visual_line_progresses(text, layout, units, position, line_end=16.5)
            wave, mappings, count = self.window._wrapped_lift_wave(
                text, self.font, layout, runs, 0.0, "character", actual,
            )
            ordinary = []
            with QPainter(self.output) as painter, patch.object(self.window, "_draw_sparse_static_run", return_value=False):
                for line_index, ((segment, x, y, height, _glyphs), local) in enumerate(zip(runs, actual)):
                    line = layout.lineAt(line_index)
                    start_py = len(self.window._utf16_slice(text, 0, line.textStart()))
                    states = self.window._real_sparse_glyph_states(
                        segment, self.font, start_py, groups, local, position,
                        wave_progress=wave, wave_unit_indices=mappings[line_index], wave_unit_count=count,
                    )
                    painted_y = []

                    def capture(*args, **kwargs):
                        painted_y.append(args[0].worldTransform().map(args[2]).y())

                    with patch.object(self.window, "_draw_continuous_read_unit", side_effect=capture):
                        self.window._paint_sparse_glyph_run(
                            painter, QPointF(10.0 + x, 10.0 + y), height, segment, self.font,
                            local, QColor(130, 90, 80), glyph_states=states,
                        )
                    self.assertEqual(len(painted_y), len(states))
                    for ordinal, state, glyph_y in zip(mappings[line_index], states, painted_y):
                        if not state.highlighted:
                            ordinary.append((ordinal, glyph_y))
            return wave, tuple(ordinary)

        _wave, baseline = painted(10.0)
        frames = []
        fronts = []
        for tick in range(261):
            front, positions = painted(10.0 + tick * 0.025)
            self.assertEqual(tuple(index for index, _y in baseline), tuple(index for index, _y in positions))
            heights = tuple(start_y - y for (_index, start_y), (_ordinal, y) in zip(baseline, positions))
            self.assertTrue(all(lead + 1e-10 >= follower for lead, follower in zip(heights, heights[1:])))
            frames.append(heights)
            fronts.append(front)
        self.assertEqual(fronts, sorted(fronts))
        for glyph_index, (ordinal, _y) in enumerate(baseline):
            full = frames[-1][glyph_index]
            middle = [(a[glyph_index], b[glyph_index]) for a, b, pa, pb in zip(frames, frames[1:], fronts, fronts[1:])
                      if full * 0.01 < a[glyph_index] < full * 0.99 and b[glyph_index] < full * 0.99 and pb > pa]
            with self.subTest(ordinal=ordinal):
                self.assertTrue(middle)
                self.assertTrue(all(b - a > 1e-8 for a, b in middle))

    def test_wrapped_line_level_highlight_uses_one_character_wave_and_half_read(self):
        text = "shine brightly across every horizon and beyond"
        layout, _height = self.window._wrapped_lyric_layout(text, self.font, 250.0)
        runs = self.window._wrapped_lyric_runs(text, self.font, layout, "character")
        self.assertGreater(layout.lineCount(), 2)
        wave, mappings, count = self.window._wrapped_lift_wave(
            text, self.font, layout, runs, 0.5,
            "character", None, ordinal_mode="character",
        )
        self.assertEqual(wave, 0.5)
        self.assertEqual(tuple(index for segment in mappings for index in segment), tuple(range(count)))
        self.assertTrue(all(sparse_glyph_frame(wave, index, count).read_fraction == 1.0 for index in range(count)))
        self.assertTrue(any(
            sparse_glyph_frame(wave, index, count).glow > 0.0 for index in range(count)
        ))

    def test_long_native_dpr_reading_does_not_build_a_frame_raster_cache(self):
        text = " ".join(f"word{index}" for index in range(24))
        self.window._lyric_spring_active = False
        image = QImage(2400, 260, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(2.0)
        source_keys = None
        started = time.perf_counter()
        for frame in range(161):
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                self.window._paint_lifted_run(
                    painter, QPointF(10.0, 10.0), 90.0, text,
                    self.font, frame / 160.0,
                )
            current_keys = tuple(source.cacheKey() for source, _left in self.window._isolated_glyph_units(text, self.font))
            if source_keys is None:
                source_keys = current_keys
            self.assertEqual(current_keys, source_keys)
        self.assertLess(len(self.window._sparse_static_run_cache), 65)
        self.assertLessEqual(self.window._sparse_static_run_cache_bytes, 16 * 1024 * 1024)
        # A loose stall guard, not a claim of 160 visible frames per second.
        self.assertLess(time.perf_counter() - started, 15.0)

    def test_static_run_batch_matches_individual_ink_at_native_dpr(self):
        text = "one two three four five six seven eight nine ten eleven twelve"
        self.window._lyric_spring_active = False
        for dpr in (1.0, 2.0):
            for progress in (0.0, 0.22, 0.51, 1.0):
                images = []
                for batched in (False, True):
                    image = QImage(round(1000 * dpr), round(130 * dpr), QImage.Format.Format_ARGB32_Premultiplied)
                    image.setDevicePixelRatio(dpr)
                    image.fill(Qt.GlobalColor.transparent)
                    with QPainter(image) as painter:
                        if batched:
                            self.window._paint_lifted_run(
                                painter, QPointF(11.35, 8.0), 100.0, text, self.font,
                                progress, QColor(150, 110, 200, 210),
                            )
                        else:
                            with patch.object(self.window, "_draw_sparse_static_run", return_value=False):
                                self.window._paint_lifted_run(
                                    painter, QPointF(11.35, 8.0), 100.0, text, self.font,
                                    progress, QColor(150, 110, 200, 210),
                                )
                    images.append(image)
                reference, optimized = images
                differing = 0
                largest = 0
                total = 0
                for y in range(reference.height()):
                    for x in range(reference.width()):
                        a, b = reference.pixelColor(x, y), optimized.pixelColor(x, y)
                        difference = max(abs(a.red() - b.red()), abs(a.green() - b.green()),
                                         abs(a.blue() - b.blue()), abs(a.alpha() - b.alpha()))
                        differing += difference > 3
                        largest = max(largest, difference)
                        total += a.alpha() > 0
                with self.subTest(dpr=dpr, progress=progress):
                    self.assertLess(differing / max(1, total), 0.02)
                    self.assertLess(largest, 25)

    def test_spring_movement_skips_position_phase_composite_allocations(self):
        text = "one two three four five six seven eight nine ten eleven twelve"
        self.window._lyric_spring_active = True
        before = (len(self.window._sparse_static_run_cache), self.window._sparse_static_run_cache_bytes)
        with patch.object(self.window, "_draw_sparse_static_run", wraps=self.window._draw_sparse_static_run) as batched:
            for position in (10.02, 10.17, 10.41, 10.73):
                self.output.fill(Qt.GlobalColor.transparent)
                with QPainter(self.output) as painter:
                    self.window._paint_lifted_run(
                        painter, QPointF(10.0, position), 90.0,
                        text, self.font, 0.5,
                    )
        self.assertEqual(batched.call_count, 0)
        self.assertEqual((len(self.window._sparse_static_run_cache), self.window._sparse_static_run_cache_bytes), before)


if __name__ == "__main__":
    unittest.main()
