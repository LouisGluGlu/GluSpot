from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtGui import QFont, QImage
from PySide6.QtWidgets import QApplication, QWidget

from local.lyric_unit_timing import timed_unit_reading_front
from local.music_lyric_presentation import split_display_layers
from local.music_surface_window import MusicSurfaceWindow


class _LayerOwner(QWidget):
    def __init__(self):
        super().__init__()
        self._music_windowed_preference = False
        self.music_intro_cleanup_enabled = False
        self.enable_dual_line_lyrics = True
        self.music_surface_reading_strategy = "gold"
        self.track_title = "Visual layers"
        self.artist = "Test singer"
        self.media_track_id = "visual-layers"
        self.media_is_playing = True
        self.media_position_seconds = 0.5
        self.timed_lyrics = []
        self.timed_lyric_units = []
        self.timed_lyric_timing_kinds = []
        self.data_provider = self

    def _run_background(self, _name, callback):
        callback()


class MusicLyricVisualLayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _LayerOwner()
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _render(self):
        image = QImage(960, 540, QImage.Format.Format_RGB32)
        self.window.render(image)
        return image

    def _four_rows(self, first: str):
        self.owner.timed_lyrics = [
            (0.0, first), (20.0, "Second line"),
            (40.0, "Third line"), (60.0, "Fourth line"),
        ]
        self.owner.timed_lyric_units = [(), (), (), ()]
        self.owner.timed_lyric_timing_kinds = ["line"] * 4

    def test_render_builds_independent_small_regular_translation_and_parenthesis_layouts(self):
        self._four_rows("Shine (echo) on me\n翻译：照耀（回声）我")
        self._render()

        primary_layout, _ = self.window._song_layouts[0]
        secondary_layout, secondary_height, secondary_top = self.window._song_secondary_layouts[0]
        parenthetical_layout, parenthetical_height, parenthetical_top = self.window._song_parenthetical_layouts[0]
        bracket_translation, bracket_translation_height, bracket_translation_top = (
            self.window._song_parenthetical_translation_layouts[0]
        )
        self.assertEqual(primary_layout.text(), "Shine on me")
        self.assertEqual(secondary_layout.text(), "照耀我")
        self.assertEqual(parenthetical_layout.text(), "echo")
        self.assertEqual(bracket_translation.text(), "回声")
        self.assertEqual(primary_layout.font().weight(), QFont.Weight.Bold)
        self.assertEqual(secondary_layout.font().weight(), QFont.Weight.Normal)
        self.assertEqual(parenthetical_layout.font().weight(), QFont.Weight.Bold)
        self.assertEqual(bracket_translation.font().weight(), QFont.Weight.Normal)
        self.assertLess(secondary_layout.font().pixelSize(), primary_layout.font().pixelSize())
        scale = self.window._surface_scale()
        self.assertEqual(secondary_layout.font().pixelSize(), round((self.window.LYRIC_PIXEL_SIZE - 6) * scale))
        self.assertEqual(parenthetical_layout.font().pixelSize(), round((self.window.LYRIC_PIXEL_SIZE - 6) * scale))
        self.assertGreater(secondary_top, self.window._song_layouts[0][1])
        self.assertAlmostEqual(parenthetical_top, self.window._song_layouts[0][1])
        self.assertAlmostEqual(bracket_translation_top, parenthetical_top + parenthetical_height)
        self.assertGreater(secondary_top, bracket_translation_top + bracket_translation_height)
        self.assertGreaterEqual(
            self.window._song_parenthetical_extra_heights[0],
            bracket_translation_top + bracket_translation_height - self.window._song_row_heights[0],
        )
        self.assertGreater(parenthetical_height, 0)

        # The focused row opens its annotation lane; future rows keep their
        # compact original/translation stack until their own focus arrives.
        now = time.monotonic()
        factors = self.window._parenthetical_reserve_factors(0, now)
        self.assertEqual(factors[0], 1.0)
        self.assertGreater(self.window._song_parenthetical_extra_heights[0], 0.0)
        self.assertGreater(self.window._song_row_tops[1], self.window._song_row_tops[0])

    def test_parentheses_only_row_has_no_visible_primary_ink_while_unread(self):
        self._four_rows("(echo)")
        self.owner.media_position_seconds = 25.0
        self._render()

        # The annotation belongs only to the focused row. A fallback to the
        # original source would leak literal brackets into the unread lyric.
        self.assertEqual(self.window._song_layouts[0][0].text(), " ")
        self.assertEqual(self.window._song_parenthetical_layouts[0][0].text(), "echo")

    def test_parenthetical_enters_downward_with_overshoot_and_reads_in_bold(self):
        self._four_rows("Shine (echo) on me")
        # A delayed first paint must still start an entrance animation.
        self.owner.media_position_seconds = 4.0
        captured = []

        def capture(_painter, row, text, font, _layout, progress, **_kwargs):
            if text == "echo":
                captured.append((row.top(), font.weight(), progress))

        with patch.object(self.window, "_paint_wrapped_lifted_lyric", side_effect=capture):
            for instant in (100.0, 100.10, 100.30, 100.60):
                with patch("local.music_surface_window.time.monotonic", return_value=instant):
                    self._render()
        self.assertEqual(len(captured), 3)
        entering, overshoot, settled = captured
        self.assertLess(entering[0], settled[0])
        self.assertGreater(overshoot[0], settled[0])
        self.assertTrue(all(weight == QFont.Weight.Bold for _y, weight, _p in captured))
        # Popout begins with the row, while the annotation itself remains
        # unread until the primary lyric reaches its final fifth.
        self.assertTrue(all(progress == 0.0 for _y, _weight, progress in captured))

    def test_bracket_translation_sits_below_bracket_and_reads_together(self):
        self._four_rows("Shine (echo) on me\n翻译：照耀（回声）我")
        self.owner.media_position_seconds = 4.0
        captured = {}

        def capture(_painter, row, text, font, _layout, progress, **_kwargs):
            if text in {"echo", "回声"}:
                captured[text] = (row.top(), font.weight(), progress)

        with patch.object(self.window, "_paint_wrapped_lifted_lyric", side_effect=capture):
            with patch("local.music_surface_window.time.monotonic", return_value=100.0):
                self._render()
            with patch("local.music_surface_window.time.monotonic", return_value=100.6):
                self._render()
        self.assertEqual(set(captured), {"echo", "回声"})
        self.assertGreater(captured["回声"][0], captured["echo"][0])
        self.assertEqual(captured["echo"][1], QFont.Weight.Bold)
        self.assertEqual(captured["回声"][1], QFont.Weight.Normal)
        self.assertAlmostEqual(captured["echo"][2], captured["回声"][2])

    def test_line_timed_bracket_and_translation_read_during_primary_final_fifth(self):
        self._four_rows("Shine (echo) on me\n翻译：照耀（回声）我")
        self.owner.media_position_source = "local-seek"
        self.owner.media_position_seconds = 1.0
        self.owner.media_position_sampled_at = 99.0
        with patch("local.music_surface_window.time.monotonic", return_value=99.0):
            self._render()

        def progress_at(position: float, now: float):
            self.owner.media_position_seconds = position
            self.owner.media_position_sampled_at = now
            painted = {}

            def capture(_painter, _row, text, _font, _layout, progress, **_kwargs):
                if text in {"echo", "回声"}:
                    painted[text] = progress

            with patch.object(self.window, "_paint_wrapped_lifted_lyric", side_effect=capture):
                with patch("local.music_surface_window.time.monotonic", return_value=now):
                    self._render()
            self.assertEqual(set(painted), {"echo", "回声"})
            self.assertAlmostEqual(painted["echo"], painted["回声"])
            return painted["echo"]

        self.assertEqual(progress_at(15.0, 100.0), 0.0)
        self.assertEqual(progress_at(16.0, 101.0), 0.0)
        self.assertAlmostEqual(progress_at(18.0, 102.0), 0.5, places=3)
        self.assertGreater(progress_at(19.99, 103.0), 0.99)

    def test_real_unit_bracket_read_uses_visible_primary_progress(self):
        source = "Shine (echo) on me"
        self._four_rows(source + "\n翻译：照耀（回声）我")
        self.owner.timed_lyric_units[0] = tuple(
            SimpleNamespace(start_seconds=float(index), end_seconds=float(index + 1), text=char)
            for index, char in enumerate(source)
        )
        self.owner.timed_lyric_timing_kinds[0] = "unit"
        self.owner.media_position_source = "local-seek"
        self.owner.media_position_seconds = 0.1
        self.owner.media_position_sampled_at = 100.0
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self._render()
        layers = self.window._visual_lyric_layers_for_rows(
            *self.window._display_lyrics(), outgoing=False,
        )[0]
        offsets = self.window._visible_primary_source_offsets(source, layers)
        self.assertIsNotNone(offsets)

        for step, position in enumerate((15.7, 16.5, 17.99)):
            with self.subTest(position=position):
                self.owner.media_position_seconds = position
                self.owner.media_position_sampled_at = 101.0 + step
                painted = {}

                def capture(_painter, _row, text, _font, _layout, progress, **_kwargs):
                    if text in {"echo", "回声"}:
                        painted[text] = progress

                with patch.object(self.window, "_paint_wrapped_lifted_lyric", side_effect=capture):
                    with patch("local.music_surface_window.time.monotonic", return_value=101.0 + step):
                        self._render()
                front = timed_unit_reading_front(
                    source, self.owner.timed_lyric_units[0],
                    self.window._lyric_motion.last_position,
                    range(len(source) + 1), line_end=20.0,
                )
                visible = self.window._project_source_front(front, offsets) / len(layers.primary_text)
                expected = max(0.0, min(1.0, (visible - 0.8) / 0.2))
                self.assertEqual(set(painted), {"echo", "回声"})
                self.assertAlmostEqual(painted["echo"], expected, places=5)
                self.assertAlmostEqual(painted["回声"], expected, places=5)
                if position == 15.7:
                    self.assertEqual(expected, 0.0)
                elif position == 17.99:
                    self.assertGreater(expected, 0.99)

    def test_sentence_gap_increases_without_widening_wrapped_line_spacing(self):
        self._four_rows("A long sentence " * 8)
        self._render()
        layout, height = self.window._song_layouts[0]
        self.assertGreater(layout.lineCount(), 1)
        sentence_gap = self.window._song_row_tops[1] - height
        self.assertGreaterEqual(sentence_gap, 540.0 * 0.035)
        self.assertLess(layout.lineAt(1).y() - layout.lineAt(0).y(), layout.lineAt(0).height())

    def test_real_obo_front_holds_through_removed_parenthesis_then_resumes(self):
        source = "Shine (echo) on me"
        layers = split_display_layers(source + "\n照耀我")
        self.assertEqual(layers.primary_text, "Shine on me")
        offsets = self.window._visible_primary_source_offsets(source, layers)
        self.assertEqual(offsets, (0, 1, 2, 3, 4, 5, 13, 14, 15, 16, 17, 18))
        units = tuple(
            SimpleNamespace(start_seconds=float(index), end_seconds=float(index + 1), text=char)
            for index, char in enumerate(source)
        )
        font = self.window._surface_font(38, QFont.Weight.Bold)
        layout, _ = self.window._wrapped_lyric_layout(layers.primary_text, font, 600.0)

        def progress(position):
            return self.window._real_visual_line_progresses(
                layers.primary_text, layout, units, position,
                line_end=20.0, source_text=source, visible_source_offsets=offsets,
            )[0]

        self.assertEqual(progress(6.5), progress(12.5))
        self.assertLess(progress(12.5), progress(13.5))
        self.assertEqual(progress(18.0), 1.0)
        prefixed = self.window._visible_primary_source_offsets("薛：" + source, layers)
        self.assertEqual(prefixed[0], 2)
        self.assertEqual(prefixed[6], 15)

    def test_rendered_translation_uses_primary_unit_read_percentage(self):
        source = "Shine (echo) on me"
        self._four_rows(source + "\n照耀我")
        self.owner.media_position_seconds = 13.5
        self.owner.timed_lyric_units[0] = tuple(
            SimpleNamespace(start_seconds=float(index), end_seconds=float(index + 1), text=char)
            for index, char in enumerate(source)
        )
        self.owner.timed_lyric_timing_kinds[0] = "unit"
        calls = []

        def capture(_painter, _row, text, font, _layout, progress, **kwargs):
            calls.append((text, font, progress, kwargs))

        with patch.object(self.window, "_paint_wrapped_lifted_lyric", side_effect=capture):
            self._render()
        painted = {text: (font, progress, kwargs) for text, font, progress, kwargs in calls}
        self.assertIn("Shine on me", painted)
        self.assertIn("照耀我", painted)
        self.assertEqual(painted["照耀我"][0].weight(), QFont.Weight.Normal)
        self.assertIsNotNone(painted["Shine on me"][2]["real_progresses"])

        shown = self.window._display_lyrics()[0]
        layers = self.window._visual_lyric_layers_for_rows(
            *self.window._display_lyrics(), outgoing=False,
        )[0]
        self.assertTrue(shown)
        offsets = self.window._visible_primary_source_offsets(source, layers)
        front = timed_unit_reading_front(
            source, self.owner.timed_lyric_units[0], self.window._lyric_motion.last_position,
            tuple(range(len(source) + 1)), line_end=20.0,
        )
        expected = self.window._project_source_front(front, offsets) / len(layers.primary_text)
        self.assertAlmostEqual(painted["照耀我"][1], expected, places=4)

    def test_low_perception_line_timing_switches_primary_and_translation_together(self):
        self.owner.music_surface_reading_strategy = "low_perception"
        self._four_rows("One line to sing\n一句歌词")
        self.owner.media_position_seconds = 1.0
        lifted = []
        whole = []

        def capture_lifted(_painter, _row, text, *_args, **_kwargs):
            lifted.append(text)

        def capture_whole(_painter, _layout, _origin, text, *_args, **_kwargs):
            whole.append(text)

        with (
            patch.object(self.window, "_paint_wrapped_lifted_lyric", side_effect=capture_lifted),
            patch.object(self.window, "_draw_sprung_cached_wrapped_layout", side_effect=capture_whole),
        ):
            self._render()
        self.assertIn("One line to sing", whole)
        self.assertIn("一句歌词", whole)
        self.assertNotIn("One line to sing", lifted)
        self.assertNotIn("一句歌词", lifted)

    def test_low_perception_real_unit_timing_reads_primary_and_translation(self):
        self.owner.music_surface_reading_strategy = "low_perception"
        self._four_rows("Hello world\n你好世界")
        self.owner.timed_lyric_units[0] = (
            SimpleNamespace(start_seconds=0.0, end_seconds=0.8, text="Hello "),
            SimpleNamespace(start_seconds=0.8, end_seconds=1.6, text="world"),
        )
        self.owner.timed_lyric_timing_kinds[0] = "unit"
        self.owner.media_position_seconds = 1.0
        lifted = []
        with patch.object(self.window, "_paint_wrapped_lifted_lyric", side_effect=lambda _p, _r, text, *_a, **_k: lifted.append(text)):
            self._render()
        self.assertIn("Hello world", lifted)
        self.assertIn("你好世界", lifted)

    def test_separate_same_time_explicit_translation_keeps_its_identity(self):
        self.owner.timed_lyrics = [
            (0.0, "Hello"), (0.0, "翻译：Love"),
            (20.0, "Second line"), (40.0, "Third line"),
            (60.0, "Fourth line"),
        ]
        self.owner.timed_lyric_units = [()] * 5
        self.owner.timed_lyric_timing_kinds = ["line"] * 5
        self._render()
        self.assertEqual(self.window._song_layouts[0][0].text(), "Hello")
        self.assertEqual(self.window._song_secondary_layouts[0][0].text(), "Love")

    def test_whole_parenthesized_translation_renders_under_its_own_sentence(self):
        self._four_rows("Shine on\n（继续闪耀）")
        self._render()
        self.assertEqual(self.window._song_layouts[0][0].text(), "Shine on")
        self.assertEqual(self.window._song_secondary_layouts[0][0].text(), "继续闪耀")
        self.assertIsNone(self.window._song_parenthetical_layouts[0])
        self.assertEqual(self.window._song_layouts[1][0].text(), "Second line")

    def test_verified_duet_rows_share_a_visual_top_and_have_separate_hit_columns(self):
        self.owner.artist = "薛之谦、刘惜君"
        for second_delay in (0.0, 0.75):
            with self.subTest(second_delay=second_delay):
                self.owner.media_track_id = f"duet-pair-{second_delay}"
                self.owner.media_position_seconds = 183.25
                self.owner.timed_lyrics = [
                    (183.04, "薛：我等你的雨季 如此安静"),
                    (183.04 + second_delay, "刘：我记得是雨季 爱上的你"),
                    (191.08, "薛之谦：我记得那草坪 一览无遗"),
                    (191.08 + second_delay, "刘惜君：我等你的草坪 高楼耸起"),
                ]
                self.owner.timed_lyric_units = [()] * 4
                self.owner.timed_lyric_timing_kinds = ["line"] * 4
                self._render()

                plan = self.window._display_duet_presentation
                self.assertEqual([(group.first_index, group.second_index) for group in plan.groups],
                                 [(0, 1), (2, 3)])
                self.assertEqual([layout.text() for layout, _height in self.window._song_layouts], [
                    "我等你的雨季 如此安静", "我记得是雨季 爱上的你",
                    "我记得那草坪 一览无遗", "我等你的草坪 高楼耸起",
                ])
                tops = self.window._song_row_tops
                self.assertEqual(tops[0], tops[1])
                self.assertEqual(tops[2], tops[3])
                self.assertGreater(tops[2], tops[1])
                self.assertEqual(tuple(sorted(self.window._song_visual_tops)),
                                 self.window._song_visual_tops)
                self.assertEqual([start for start, _text in self.window._display_lyrics()[1]],
                                 [row[0] for row in self.owner.timed_lyrics])

                left = self.window._lyric_hit_rects[0]
                right = self.window._lyric_hit_rects[1]
                self.assertGreater(left.width(), 0)
                self.assertGreater(right.width(), 0)
                self.assertLess(left.right(), right.left())
                self.assertAlmostEqual(left.top(), right.top(), delta=1.0)
                self.assertTrue(left.contains(left.center()))
                self.assertFalse(right.contains(left.center()))
                self.assertTrue(right.contains(right.center()))
                self.assertFalse(left.contains(right.center()))

    def test_provider_combined_duet_row_keeps_each_voices_translation(self):
        self.owner.artist = "薛之谦、刘惜君"
        self.owner.timed_lyrics = [
            (0.0, "薛：第一位歌词\n翻译：First voice\n刘：第二位歌词\n翻译：Second voice"),
            (6.0, "后面的句子"), (9.0, "最后的句子"),
        ]
        self.owner.timed_lyric_units = [()] * 3
        self.owner.timed_lyric_timing_kinds = ["line"] * 3
        self._render()
        self.assertEqual(len(self.window._display_duet_presentation.groups), 1)
        self.assertEqual([layout.text() for layout, _height in self.window._song_layouts][:2],
                         ["第一位歌词", "第二位歌词"])
        self.assertEqual([part[0].text() for part in self.window._song_secondary_layouts[:2]],
                         ["First voice", "Second voice"])

    def test_duet_groups_keep_both_columns_together_through_spring_change(self):
        self.owner.artist = "薛之谦、刘惜君"
        self.owner.timed_lyrics = [
            (0.0, "薛：第一位歌词"), (0.0, "刘：第二位歌词"),
            (3.0, "薛：第三位歌词"), (3.0, "刘：第四位歌词"),
            (6.0, "下面一句"),
        ]
        self.owner.timed_lyric_units = [()] * 5
        self.owner.timed_lyric_timing_kinds = ["line"] * 5
        for position in (2.90, 3.10, 3.13, 3.18):
            self.owner.media_position_seconds = position
            self.owner.media_position_sampled_at = 100.0 + position
            with patch("local.music_surface_window.time.monotonic", return_value=100.0 + position):
                self._render()
            left, right = self.window._lyric_hit_rects[2], self.window._lyric_hit_rects[3]
            self.assertAlmostEqual(left.top(), right.top(), delta=0.01)
        self.assertEqual(self.window._logical_line_spring.sequence_index, 1)

    def test_duet_layout_rejects_unprefixed_or_2_53_second_apart_rows(self):
        self.owner.artist = "薛之谦、刘惜君"
        variants = (
            ((182.36, "我等你的雨季 如此安静"), (184.89, "我记得是雨季 爱上的你")),
            ((182.36, "薛：我等你的雨季 如此安静"), (184.89, "刘：我记得是雨季 爱上的你")),
            ((182.36, "薛：我等你的雨季 如此安静"), (182.86, "我记得是雨季 爱上的你")),
        )
        for variant_index, first_two in enumerate(variants):
            with self.subTest(variant=variant_index):
                self.owner.media_track_id = f"duet-reject-{variant_index}"
                self.owner.media_position_seconds = 182.5
                self.owner.timed_lyrics = [
                    *first_two, (190.0, "第三句"), (194.0, "第四句"),
                ]
                self.owner.timed_lyric_units = [()] * 4
                self.owner.timed_lyric_timing_kinds = ["line"] * 4
                self._render()

                self.assertFalse(self.window._display_duet_presentation.groups)
                self.assertLess(self.window._song_row_tops[0], self.window._song_row_tops[1])
                self.assertGreater(self.window._song_layouts[0][0].text(), "")
                self.assertGreater(self.window._song_layouts[1][0].text(), "")

    def test_duet_second_voice_keeps_first_voice_reading_until_group_end(self):
        self.owner.artist = "薛之谦、刘惜君"
        self.owner.timed_lyrics = [
            (10.0, "薛：我先开始唱"), (10.75, "刘：我后开始唱"),
            (16.0, "第三句"), (20.0, "第四句"),
        ]
        self.owner.timed_lyric_units = [()] * 4
        self.owner.timed_lyric_timing_kinds = ["line"] * 4
        self.owner.media_position_seconds = 11.0
        self.owner.media_position_source = "local-seek"
        self.owner.media_position_sampled_at = time.monotonic()
        painted = {}

        def capture(_painter, _row, text, _font, _layout, progress, **_kwargs):
            painted[text] = progress

        with patch.object(self.window, "_paint_wrapped_lifted_lyric", side_effect=capture):
            self._render()
        self.assertEqual(self.window._lyric_motion.active_index, 1)
        self.assertIn("我先开始唱", painted)
        self.assertIn("我后开始唱", painted)
        self.assertGreater(painted["我先开始唱"], painted["我后开始唱"])
        self.assertAlmostEqual(painted["我先开始唱"], 1.0 / 6.0, delta=0.05)
        self.assertAlmostEqual(painted["我后开始唱"], 0.25 / 5.25, delta=0.05)

    def test_one_late_verified_singer_aligns_following_rows_right(self):
        self.owner.artist = "薛之谦、刘惜君"
        self.owner.media_track_id = "solo-singer-right"
        self.owner.timed_lyrics = [
            (0.0, "先唱一句"),
            (4.0, "刘惜君：我继续唱"),
            (8.0, "没有前缀也跟随"),
            # A phrase-internal colon is protected even at the final edge;
            # a compact unknown label alone is eligible for edge cleanup.
            (12.0, "我的爱：本就无需解释"),
        ]
        self.owner.timed_lyric_units = [()] * 4
        self.owner.timed_lyric_timing_kinds = ["line"] * 4
        self.owner.media_position_seconds = 4.5
        self._render()

        plan = self.window._display_duet_presentation
        self.assertEqual([row.alignment for row in plan.rows],
                         ["left", "right", "right", "right"])
        self.assertFalse(plan.groups)
        self.assertEqual([layout.text() for layout, _height in self.window._song_layouts], [
            "先唱一句", "我继续唱", "没有前缀也跟随", "我的爱：本就无需解释",
        ])
        positions = [layout.lineAt(0).x() for layout, _height in self.window._song_layouts]
        self.assertEqual(positions[0], 0.0)
        self.assertTrue(all(position > 0.0 for position in positions[1:]))


if __name__ == "__main__":
    unittest.main()
