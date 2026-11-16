"""Display-only singer roles preserve raw lyric clocks and precise word seeks."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QFontMetricsF, QImage, QPainter, QTransform
from PySide6.QtWidgets import QApplication

from api.models import TimedLyricLine, TimedLyricUnit
from local.lyric_unit_timing import timed_unit_reading_front
from local.music_lyric_preparation import prepare_lyric_presentation, snapshot_lyric_input
from local.music_lyric_presentation import analyze_duet_lyric_rows, split_display_layers
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_lyric_visual_layers import _LayerOwner


def _units(text, start=0.):
    return tuple(TimedLyricUnit(start + index, start + index + 1., char)
                 for index, char in enumerate(text))


class SingerTagPresentationTests(unittest.TestCase):
    def test_closed_leading_whitelist_strips_only_role_from_primary(self):
        for role in ("男", "女", "合", "男声", "女声", "合唱"):
            for opening, closing in (("(", ")"), ("（", "）")):
                with self.subTest(role=role, opening=opening):
                    raw = f"  {opening}{role}{closing} 明天要和你见面"
                    layers = split_display_layers(raw)
                    self.assertEqual(layers.role_tag, role)
                    self.assertEqual(layers.primary_text, "明天要和你见面")
                    self.assertIsNone(layers.parenthetical_text)
                    self.assertEqual(layers.original_text, raw)
                    self.assertEqual(len(layers.primary_source_offsets), len(layers.primary_text) + 1)
                    original = layers.original_primary_text
                    self.assertEqual("".join(original[index] for index in layers.primary_source_offsets[:-1]),
                                     layers.primary_text)

    def test_standalone_role_is_content_without_a_sung_remainder(self):
        for raw in ("(男)", "（女）", "(合唱)   "):
            with self.subTest(raw=raw):
                layers = split_display_layers(raw)
                self.assertIsNone(layers.role_tag)
                self.assertEqual(layers.primary_text, raw.strip())

    def test_nonleading_unknown_or_unclosed_tokens_keep_existing_policy(self):
        for raw in ("我最珍爱 (和声)", "我唱 (男)", "(独唱)继续唱", "(男声主唱)继续唱",
                    "(男 )继续唱", "(男继续唱", "（女继续唱"):
            with self.subTest(raw=raw):
                self.assertIsNone(split_display_layers(raw).role_tag)
        backing = split_display_layers("我最珍爱 (和声)")
        self.assertEqual(backing.primary_text, "我最珍爱")
        self.assertEqual(backing.parenthetical_text, "和声")

    def test_role_preserves_translation_and_backing_vocal_layers(self):
        raw = "（女）我的心跳都不规则 (和声)\n翻译：My heartbeat is irregular"
        layers = split_display_layers(raw, source_text=raw)
        self.assertEqual(layers.role_tag, "女")
        self.assertEqual(layers.primary_text, "我的心跳都不规则")
        self.assertEqual(layers.parenthetical_text, "和声")
        self.assertEqual(layers.translation_text, "My heartbeat is irregular")
        self.assertEqual(layers.original_text, raw)
        hidden = split_display_layers(raw, source_text=raw, show_translation=False)
        self.assertEqual(hidden.primary_text, layers.primary_text)
        self.assertEqual(hidden.role_tag, layers.role_tag)
        self.assertIsNone(hidden.translation_text)

    def test_male_female_pair_keeps_times_and_labels_and_chorus_stays_unpaired(self):
        raw = ((0., "(男)明天要和你见面"), (.5, "（女）我的心跳都不规则"),
               (4., "(合)你是我最珍贵"), (4.2, "(男声)继续唱"))
        duet = analyze_duet_lyric_rows(raw)
        self.assertEqual([(group.first_index, group.second_index) for group in duet.groups], [(0, 1)])
        self.assertEqual([row.role_tag for row in duet.rows], ["男", "女", "合", "男声"])
        self.assertEqual([row.alignment for row in duet.rows[:3]], ["left", "right", "left"])
        self.assertEqual([(row.start_seconds, row.original_text) for row in duet.rows], list(raw))
        self.assertEqual(duet.rows[0].body_text, "明天要和你见面")

    def test_preparation_preserves_raw_units_and_translations_with_role_layers(self):
        raw = "(男)明天见\n翻译：See you tomorrow"
        source_owner = SimpleNamespace(
            media_track_id="singer-tags", track_title="你最珍贵", artist="张学友 / 高慧君",
            timed_lyrics=[(0., raw), (.5, "（女）等着你"), (5., "下一句")],
            timed_lyric_units=[_units("明天见"), (), ()],
            timed_lyric_timing_kinds=["unit", "line", "line"],
            lyrics=[raw, "（女）等着你", "下一句"], enable_dual_line_lyrics=True,
        )
        source = snapshot_lyric_input(source_owner)
        prepared = prepare_lyric_presentation(source)
        index = next(index for index, row in enumerate(prepared.timed) if row[0] == 0.)
        self.assertEqual(prepared.layers[index].role_tag, "男")
        self.assertEqual(prepared.layers[index].primary_text, "明天见")
        self.assertEqual(prepared.layers[index].translation_text, "See you tomorrow")
        self.assertEqual(prepared.units[index], source.units[0])
        self.assertEqual(prepared.raw_source_texts[index], raw)
        self.assertEqual(prepared.source, source)
        hidden = prepare_lyric_presentation(replace(source, translated=False))
        self.assertEqual(hidden.layers[index].role_tag, "男")
        self.assertIsNone(hidden.layers[index].translation_text)
        self.assertEqual(source_owner.timed_lyrics[0][1], raw)

    def test_real_ink_front_maps_full_tag_units_and_body_only_units_without_retiming(self):
        raw = "（男）明天见"
        layers = split_display_layers(raw)
        offsets = MusicSurfaceWindow._visible_primary_source_offsets(raw, layers)
        self.assertEqual(offsets, (3, 4, 5, 6))
        for units, position in ((_units(raw), 4.5), (_units("明天见", start=3.), 4.5)):
            with self.subTest(unit_text="".join(unit.text for unit in units)):
                front = timed_unit_reading_front(raw, units, position, tuple(range(len(raw) + 1)))
                self.assertIsNotNone(front)
                self.assertAlmostEqual(MusicSurfaceWindow._project_source_front(front, offsets), 1.5)
        self.assertIsNone(timed_unit_reading_front(raw, _units("另一句"), 1., tuple(range(len(raw) + 1))))


class SingerTagSurfaceTests(unittest.TestCase):
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

    def test_prepared_and_fallback_visual_layers_share_role_and_source_mapping(self):
        raw = "（女）明天见\n翻译：See you tomorrow"
        self.owner.timed_lyrics = [(0., raw), (6., "下一句")]
        self.owner.timed_lyric_units = [_units("明天见"), ()]
        self.owner.timed_lyric_timing_kinds = ["unit", "line"]
        lines, timed = self.window._display_lyrics()
        prepared = self.window._visual_lyric_layers_for_rows(lines, timed, outgoing=False)[0]
        self.window._prepared_lyric_layers = None
        self.window._visual_lyric_cache_key = None
        fallback = self.window._visual_lyric_layers_for_rows(lines, timed, outgoing=False)[0]
        for layers in (prepared, fallback):
            self.assertEqual(layers.role_tag, "女")
            self.assertEqual(layers.primary_text, "明天见")
            self.assertEqual(layers.translation_text, "See you tomorrow")
            self.assertEqual(self.window._visible_primary_source_offsets(raw, layers), (3, 4, 5, 6))

    def test_word_hit_boxes_seek_the_primary_char_for_both_unit_conventions(self):
        raw = "(男)明天见"
        layers = split_display_layers(raw)
        font = self.window._surface_font(38, QFont.Weight.Bold)
        shaped, height = self.window._wrapped_lyric_layout(layers.primary_text, font, 600.)
        row = QRectF(60., 100., 600., height)
        self.window._lyric_word_hit_track_key = (self.window._visual_track_identity(), "")
        for units in (_units(raw), _units("明天见", start=3.)):
            with self.subTest(unit_text="".join(unit.text for unit in units)):
                self.window._lyric_word_hit_rects.clear()
                self.window._populate_lyric_word_hit_rects(
                    0, layers.primary_text, raw, layers, shaped, units, row, (),
                    QTransform(), QRectF(0., 0., 960., 540.), 0.,
                )
                hits = list(self.window._lyric_word_hit_rects.values())
                self.assertEqual(len(hits), 3)
                self.assertEqual([seconds for _rect, seconds in hits], [3., 4., 5.])
                self.assertTrue(all(rect.left() >= row.left() for rect, _seconds in hits))
                for rect, seconds in hits:
                    self.assertEqual(self.window._lyric_word_target_at(rect.center())[2], seconds)

    def test_dim_role_pill_stays_outside_the_timed_run(self):
        font = self.window._surface_font(38, QFont.Weight.Bold)
        image = QImage(320, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        origin = QPointF(30., 10.)
        with QPainter(image) as painter:
            pill = self.window._paint_lyric_role_tag(painter, "男声", font, origin)
        self.assertLessEqual(pill.right(), origin.x())
        self.assertGreater(pill.width(), 0.)
        self.assertLess(pill.height(), QFontMetricsF(font).height())


class SingerTagNarrowTests(unittest.TestCase):
    from tests.test_narrow_chrome import NarrowChromeTests as _Fixtures
    setUpClass = _Fixtures.__dict__["setUpClass"]
    setUp = _Fixtures.setUp
    tearDown = _Fixtures.tearDown

    def test_narrow_live_role_keeps_body_width_and_source_word_seeks(self):
        from _demos.narrow3.layout import NarrowLayout
        window = self.window
        raw = "（女）明天见"
        layout = NarrowLayout(window.width(), window.height())
        line = TimedLyricLine(0., raw, _units("明天见", start=3.), "word")
        with patch.object(window, "source_lines", return_value=(line,)), \
             patch.object(window, "lyric_focus", return_value=(0, 0.)), \
             patch.object(window, "ink_position", return_value=4.5):
            runs = window.real_lyric_blocks(layout)[0][3]
            self.assertEqual(runs[0][0], "明天见")
            self.assertEqual(len(runs), 1, "role must not create a backing-vocal row")
            window.publish_live_lyric_hits(layout)
            hits = list(window._lyric_word_hit_rects.values())
            self.assertEqual([seconds for _rect, seconds in hits], [3., 4., 5.])
            for rect, seconds in hits:
                self.assertEqual(window.live_lyric_action_at(rect.center())[2], seconds)

    def test_narrow_live_role_pill_is_visible_with_measured_body_ink(self):
        from _demos.narrow3.layout import NarrowLayout
        window = self.window
        line = TimedLyricLine(0., "(男)明天见", _units("(男)明天见"), "word")
        image = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with patch.object(window, "source_lines", return_value=(line,)), \
             patch.object(window, "lyric_focus", return_value=(0, 0.)), \
             patch.object(window, "ink_position", return_value=4.5), \
             patch.object(window, "_paint_lyric_role_tag", wraps=window._paint_lyric_role_tag) as pill, \
             patch.object(window, "_paint_wrapped_lifted_lyric") as ink:
            with QPainter(image) as painter:
                window.paint_live_lyrics(painter, NarrowLayout(window.width(), window.height()))
            pill.assert_called_once()
            self.assertEqual(pill.call_args.args[1], "男")
            self.assertEqual(ink.call_args.args[2], "明天见")
            progresses = ink.call_args.kwargs["real_progresses"]
            self.assertTrue(progresses)
            self.assertGreater(progresses[0], 0.)
            self.assertLess(progresses[0], 1.)


if __name__ == "__main__":
    unittest.main()
