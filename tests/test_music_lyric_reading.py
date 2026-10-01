from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.models import TimedLyricUnit
from local.island_stack_visual import IslandStackVisual
from local.music_lyric_presentation import present_timed_lyric_rows
from local.music_lyric_reading import (
    LyricReadingRenderer,
    read_wipe_image_at_front,
    reading_front,
    reading_unit_offsets,
    utf16_slice,
    whole_block_reading_gradient,
)


class MusicLyricReadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        IslandStackVisual.load_embedded_lyric_fonts()

    @staticmethod
    def _unit_texts(text: str) -> list[str]:
        offsets = reading_unit_offsets(text)
        return [utf16_slice(text, start, end - start) for start, end in zip(offsets, offsets[1:])]

    @staticmethod
    def _ink_bounds(image: QImage) -> tuple[int, int, int, int]:
        coords = [
            (x, y)
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 40
        ]
        return (
            min(x for x, _y in coords),
            min(y for _x, y in coords),
            max(x for x, _y in coords),
            max(y for _x, y in coords),
        )

    @staticmethod
    def _visual() -> IslandStackVisual:
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        visual.font_shrink_scale = 1.0
        visual.high_quality_font_rendering = False
        visual._lyric_layout_cache = {}
        visual._wave_reading_renderer = LyricReadingRenderer()
        visual._wave_stream_font = lambda *_args, **_kwargs: QFont("Arial", 18)
        return visual

    def _paint_active(self, visual: IslandStackVisual, text: str, progress: float, rect: QRectF) -> QImage:
        image = QImage(420, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        with QPainter(image) as painter:
            visual._paint_live_lyric_text(
                painter,
                rect,
                text,
                18,
                QColor(122, 74, 112, 220),
                QColor(255, 255, 255, 240),
                True,
                {"progress": progress, "confidence": 0.78},
                centered=True,
                centered_scroll=True,
            )
        return image

    def test_latin_words_and_cjk_graphemes_rise_as_intact_units(self):
        self.assertEqual(self._unit_texts("Shine softly 春風"), ["Shine ", "softly ", "春", "風"])
        self.assertEqual(self._unit_texts("안녕 반가워"), ["안", "녕 ", "반", "가", "워"])
        self.assertEqual(self._unit_texts("😀 Shine"), ["😀 ", "Shine"])

    def test_compact_word_lift_keeps_width_and_settles_above_start(self):
        visual = self._visual()
        rect = QRectF(0.0, 8.0, 360.0, 76.0)
        start = self._paint_active(visual, "Shine softly", 0.0, rect)
        middle = self._paint_active(visual, "Shine softly", 0.5, rect)
        end = self._paint_active(visual, "Shine softly", 1.0, rect)
        bounds = [self._ink_bounds(image) for image in (start, middle, end)]
        self.assertEqual([right - left for left, _top, right, _bottom in bounds], [bounds[0][2] - bounds[0][0]] * 3)
        self.assertLess(bounds[1][1], bounds[0][1])
        self.assertLess(bounds[2][3], bounds[0][3])
        self.assertLessEqual(bounds[2][3], bounds[1][3])

    def test_island_reading_strategy_uses_real_unit_evidence_for_whole_song(self):
        visual = self._visual()
        visual.track_title = "计时测试"
        visual.lyrics = ["真逐字", "只有行时间"]
        visual.timed_lyrics = [(0.0, "真逐字"), (4.0, "只有行时间")]
        visual.timed_lyric_units = [
            (TimedLyricUnit(0.0, 1.0, "真"), TimedLyricUnit(1.0, 2.0, "逐"), TimedLyricUnit(2.0, 3.0, "字")),
            (),
        ]
        visual.timed_lyric_timing_kinds = ["unit", "line"]
        visual.music_surface_reading_strategy = "gold"
        self.assertFalse(visual._island_whole_row_reading(0))
        self.assertFalse(visual._island_whole_row_reading(1))
        visual.music_surface_reading_strategy = "low_perception"
        self.assertFalse(visual._island_whole_row_reading(0))
        self.assertFalse(visual._island_whole_row_reading(1))
        visual.timed_lyric_timing_kinds = ["line", "line"]
        self.assertTrue(visual._island_whole_row_reading(0))
        self.assertTrue(visual._island_whole_row_reading(1))
        visual.timed_lyric_timing_kinds = ["synthetic", "synthetic"]
        self.assertTrue(visual._island_whole_row_reading(0))

    def test_island_timing_classification_excludes_removed_credit_and_caches_survivors(self):
        visual = self._visual()
        visual.media_track_id = "credit-only-unit"
        visual.track_title = "Credit then song"
        visual.artist = "Singer"
        visual.music_surface_reading_strategy = "low_perception"
        visual.enable_dual_line_lyrics = False
        visual.music_intro_cleanup_enabled = True
        visual.timed_lyrics = [
            (0.0, "作词：Someone"), (3.0, "Hello world"), (6.0, "Shine"),
        ]
        visual.timed_lyric_units = [
            (
                TimedLyricUnit(0.0, 0.2, "作"), TimedLyricUnit(0.2, 0.4, "词"),
                TimedLyricUnit(0.4, 0.4, "："), TimedLyricUnit(0.5, 1.0, "Someone"),
            ), (), (),
        ]
        visual.timed_lyric_timing_kinds = ["unit", "line", "line"]
        with patch("local.island_stack_visual.present_timed_lyric_rows", wraps=present_timed_lyric_rows) as clean:
            self.assertFalse(visual._island_song_has_genuine_unit_timing())
            self.assertTrue(visual._island_whole_row_reading(1))
            self.assertEqual(clean.call_count, 1)
            visual.media_track_id = "same-rows-new-track"
            self.assertFalse(visual._island_song_has_genuine_unit_timing())
            self.assertEqual(clean.call_count, 2)
            visual.timed_lyric_units = [
                visual.timed_lyric_units[0], (), (TimedLyricUnit(6.0, 7.0, "Shine"),),
            ]
            visual.timed_lyric_timing_kinds = ["unit", "line", "unit"]
            self.assertTrue(visual._island_song_has_genuine_unit_timing())
            self.assertFalse(visual._island_whole_row_reading(1))
            self.assertEqual(clean.call_count, 3)
            visual.enable_dual_line_lyrics = True
            self.assertTrue(visual._island_song_has_genuine_unit_timing())
            self.assertEqual(clean.call_count, 4)
            visual.music_intro_cleanup_enabled = False
            self.assertTrue(visual._island_song_has_genuine_unit_timing())
            self.assertEqual(clean.call_count, 5)

    def test_low_perception_whole_row_draws_without_read_wipe_or_lift(self):
        visual = self._visual()
        renderer = visual._wave_reading_renderer
        font = QFont("Arial", 18)
        rect = QRectF(0.0, 8.0, 360.0, 76.0)
        frame = QImage(420, 100, QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(0)
        with patch("local.music_lyric_reading.read_wipe_image_at_front") as wipe:
            with QPainter(frame) as painter:
                renderer.paint(
                    painter, rect, 20.0, "Shine", font, 0.4,
                    QColor(120, 80, 80), QColor(255, 255, 255), whole_row=True,
                )
        wipe.assert_not_called()
        self.assertGreater(self._ink_bounds(frame)[2], self._ink_bounds(frame)[0])

    def test_whole_block_gradient_uses_one_diagonal_across_original_and_translation(self):
        renderer = LyricReadingRenderer()
        rows = (
            (QRectF(10.0, 10.0, 300.0, 42.0), 24.0, "ABCD ABCD", QFont("Arial", 24),
             QColor(80, 80, 80), QColor(255, 255, 255)),
            (QRectF(10.0, 52.0, 300.0, 36.0), 24.0, "EFGH EFGH", QFont("Arial", 20),
             QColor(80, 80, 80), QColor(255, 255, 255)),
        )
        images = []
        for progress in (0.0, 0.5, 1.0):
            image = QImage(320, 100, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            with QPainter(image) as painter:
                renderer.paint_whole_block(painter, rows, progress)
            images.append(image)
        self.assertEqual(len(renderer.whole_lines), 2)
        positions = ((36, 31), (158, 31), (36, 69), (150, 69))

        def average_brightness(image, point):
            cx, cy = point
            colors = [image.pixelColor(x, y).red() for y in range(cy - 5, cy + 6)
                      for x in range(cx - 5, cx + 6) if image.pixelColor(x, y).alpha() > 25]
            return sum(colors) / len(colors) if colors else 0.0

        self.assertTrue(all(average_brightness(images[0], point) <
                            average_brightness(images[2], point) for point in positions))
        self.assertGreater(average_brightness(images[1], positions[0]),
                           average_brightness(images[1], positions[-1]))
        gradient = whole_block_reading_gradient(
            QRectF(20, 10, 200, 80), 0.5, QColor(80, 80, 80), QColor(255, 255, 255),
        )
        self.assertLess(gradient.start().x(), gradient.finalStop().x())
        self.assertLess(gradient.start().y(), gradient.finalStop().y())

    def test_whole_block_gradient_has_continuous_endpoints(self):
        bounds = QRectF(10.0, 10.0, 200.0, 80.0)
        unread = QColor(80, 80, 80)
        read = QColor(255, 255, 255)
        phases = (0.0, 0.001, 0.01, 0.99, 0.999, 1.0)
        gradients = [whole_block_reading_gradient(bounds, phase, unread, read) for phase in phases]
        first_colors = [gradient.stops()[0][1].red() for gradient in gradients]
        last_colors = [gradient.stops()[-1][1].red() for gradient in gradients]
        self.assertEqual(first_colors[0], unread.red())
        self.assertEqual(last_colors[-1], read.red())
        self.assertLessEqual(first_colors[1] - first_colors[0], 1)
        self.assertLessEqual(last_colors[-1] - last_colors[-2], 1)

    def test_island_low_mode_half_second_is_source_timed_and_pause_stable(self):
        visual = self._visual()
        visual.music_surface_reading_strategy = "low_perception"
        visual.lyrics = ["First line\n第一行", "Second line"]
        visual.timed_lyrics = [(10.0, visual.lyrics[0]), (14.0, visual.lyrics[1])]
        visual.timed_lyric_units = [(), ()]
        visual.timed_lyric_timing_kinds = ["line", "line"]
        visual.lyric_index = 0
        visual.lyric_changed_at = 100.0
        visual.lyric_last_phase_index = -1
        visual.lyric_last_phase_progress = 0.0
        visual.lyric_last_phase_at = 100.0
        visual.media_is_playing = True
        visual._effective_lyric_offset_seconds = lambda: 0.0
        position = [10.0]
        visual._estimated_media_position = lambda _now: position[0]
        phases = []
        for at, source in ((100.0, 10.0), (100.1, 10.1), (100.25, 10.25), (100.5, 10.5)):
            position[0] = source
            phases.append(visual._lyric_phase(at)["whole_block_progress"])
        for actual, expected in zip(phases, (0.0, 0.2, 0.5, 1.0)):
            self.assertAlmostEqual(actual, expected)
        position[0] = 10.25
        visual.media_is_playing = False
        self.assertEqual(visual._lyric_phase(200.0)["whole_block_progress"], 0.5)
        self.assertEqual(visual._lyric_phase(210.0)["whole_block_progress"], 0.5)
        position[0] = 10.12
        visual.media_position_source = "local-seek"
        self.assertAlmostEqual(visual._lyric_phase(201.0)["whole_block_progress"], 0.24)

    def test_island_mixed_timing_song_reads_estimated_rows_and_focuses_next_unread(self):
        visual = self._visual()
        visual.media_track_id = "mixed-song"
        visual.lyrics = ["Hello world", "Estimated words", "Next words"]
        visual.timed_lyrics = [(0.0, visual.lyrics[0]), (3.0, visual.lyrics[1]), (7.0, visual.lyrics[2])]
        visual.timed_lyric_units = [
            (TimedLyricUnit(0.0, 0.5, "Hello "), TimedLyricUnit(0.5, 1.0, "world")),
            (), (),
        ]
        visual.timed_lyric_timing_kinds = ["unit", "line", "line"]
        visual.music_surface_reading_strategy = "low_perception"
        visual.lyric_flow_style = "wave_stream"
        visual.lyric_index = 0
        visual.lyric_changed_at = 100.0
        visual.lyric_last_phase_index = -1
        visual.lyric_last_phase_progress = 0.0
        visual.lyric_last_phase_at = 100.0
        visual.media_is_playing = True
        offset = [0.0]
        visual._effective_lyric_offset_seconds = lambda: offset[0]
        position = [1.05]
        visual._estimated_media_position = lambda _now: position[0]
        real_ended = visual._lyric_phase(101.05)
        self.assertEqual(real_ended["visual_focus_index"], 1)
        self.assertFalse(real_ended["whole_row"])
        self.assertNotIn("whole_block_progress", real_ended)
        self.assertEqual(visual.lyric_index, 0)
        offset[0] = -1.0
        self.assertEqual(visual._lyric_phase(101.06)["visual_focus_index"], 0)
        offset[0] = 0.0
        visual.lyric_index = 1
        position[0] = 5.0
        midpoint = visual._lyric_phase(105.0)
        self.assertEqual(midpoint["visual_focus_index"], 1)
        self.assertAlmostEqual(midpoint["progress"], 2.0 / 3.0)
        position[0] = 6.0
        early = visual._lyric_phase(106.0)
        self.assertEqual(early["visual_focus_index"], 2)
        self.assertEqual(early["progress"], 1.0)
        self.assertEqual(visual.lyric_index, 1)

    def test_island_no_real_units_keeps_whole_song_line_mode_without_early_focus(self):
        visual = self._visual()
        visual.media_track_id = "line-song"
        visual.lyrics = ["First line", "Second line"]
        visual.timed_lyrics = [(0.0, visual.lyrics[0]), (4.0, visual.lyrics[1])]
        visual.timed_lyric_units = [(), ()]
        visual.timed_lyric_timing_kinds = ["line", "line"]
        visual.music_surface_reading_strategy = "low_perception"
        visual.lyric_flow_style = "wave_stream"
        visual.lyric_index = 0
        visual.lyric_changed_at = 100.0
        visual.lyric_last_phase_index = -1
        visual.lyric_last_phase_progress = 0.0
        visual.lyric_last_phase_at = 100.0
        visual.media_is_playing = True
        visual._effective_lyric_offset_seconds = lambda: 0.0
        visual._estimated_media_position = lambda _now: 3.5
        phase = visual._lyric_phase(103.5)
        self.assertEqual(phase["visual_focus_index"], 0)
        self.assertTrue(phase["whole_row"])
        self.assertEqual(phase["whole_block_progress"], 1.0)

    def test_island_gold_pure_line_song_does_not_compress_or_focus_early(self):
        visual = self._visual()
        visual.media_track_id = "gold-lines"
        visual.lyrics = ["First line", "Second line"]
        visual.timed_lyrics = [(0.0, visual.lyrics[0]), (4.0, visual.lyrics[1])]
        visual.timed_lyric_units = [(), ()]
        visual.timed_lyric_timing_kinds = ["line", "line"]
        visual.music_surface_reading_strategy = "gold"
        visual.lyric_flow_style = "wave_stream"
        visual.lyric_index = 0
        visual.lyric_changed_at = 100.0
        visual.lyric_last_phase_index = -1
        visual.lyric_last_phase_progress = 0.0
        visual.lyric_last_phase_at = 100.0
        visual.media_is_playing = True
        visual._effective_lyric_offset_seconds = lambda: 0.0
        visual._estimated_media_position = lambda _now: 3.5
        phase = visual._lyric_phase(103.5)
        self.assertEqual(phase["visual_focus_index"], 0)
        self.assertFalse(phase["whole_row"])
        self.assertLess(phase["progress"], 1.0)

    def test_ember_extra_offset_uses_same_estimated_end_for_mixed_timing_song(self):
        visual = self._visual()
        visual.timed_lyrics = [(0.0, "Hi"), (3.0, "Long sentence"), (7.0, "Next")]
        visual.timed_lyric_units = [
            (TimedLyricUnit(0.0, 0.8, "Hi"),), (), (),
        ]
        visual.timed_lyric_timing_kinds = ["unit", "line", "line"]
        visual.lyric_index = 1
        visual.lyric_changed_at = 100.0
        visual.lyric_transition_kind = ""
        visual.media_is_playing = True
        visual.ember_lyric_offset_seconds = 0.5
        visual.ember_last_phase_index = -1
        visual.ember_last_phase_progress = 0.0
        visual.ember_last_phase_at = 100.0
        visual._effective_lyric_offset_seconds = lambda: 0.0
        visual._estimated_media_position = lambda _now: 5.0
        phase = visual._ember_lyric_phase(105.0)
        self.assertEqual(int(phase["active_index"]), 1)
        self.assertAlmostEqual(float(phase["progress"]), 2.5 / 3.0)
        self.assertEqual(visual.lyric_index, 1)

    def test_island_compact_uses_visual_next_without_changing_source_index(self):
        visual = self._visual()
        visual.lyrics = ["Already read", "Next unread"]
        visual.lyric_index = 0
        visual._should_paint_album_island = lambda: False
        visual._ember_enabled = lambda: False
        visual._gelv_enabled = lambda: False
        visual._compact_lyric_lines = lambda: visual.lyrics
        visual._lyric_phase = lambda _now: {"visual_focus_index": 1, "progress": 1.0,
                                            "timed_primary_text": "Already read", "timed_units": (object(),)}
        visual._compact_primary_lyric_font_size = lambda: 18
        visual._lyric_display_lines = lambda text: [text]
        visual._music_lyric_base_text_color = lambda alpha=132: QColor(90, 90, 90, alpha)
        visual._music_lyric_text_color = lambda alpha=246: QColor(255, 255, 255, alpha)
        frame = QImage(500, 100, QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(0)
        with patch.object(visual, "_paint_live_lyric_text") as paint:
            with QPainter(frame) as painter:
                visual._paint_compact_lyric_viewport(painter, QRectF(0, 0, 480, 90))
        self.assertEqual(visual.lyric_index, 0)
        self.assertEqual(paint.call_args.args[2], "Next unread")
        self.assertEqual(paint.call_args.args[7]["progress"], 0.0)
        self.assertNotIn("timed_units", paint.call_args.args[7])

    def test_island_expanded_spring_focuses_unread_next_without_source_mutation(self):
        visual = self._visual()
        visual.lyrics = ["Already read", "Next unread", "Following"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.lyric_transition_kind = ""
        visual.media_track_id = "visual-focus"
        visual.track_title = "visual-focus"
        visual.artist = "artist"
        visual._should_paint_album_island = lambda: False
        visual._ember_enabled = lambda: False
        visual._gelv_enabled = lambda: False
        visual._is_instrumental_track = lambda: False
        visual._lyric_display_lines = lambda text: [text]
        visual._lyric_phase = lambda _now: {"visual_focus_index": 1, "progress": 1.0,
                                            "timed_primary_text": "Already read", "timed_units": (object(),)}
        visual._lyric_transition_progress = lambda _duration: (1.0, 1.0)
        visual.lyric_font_size = 18
        frame = QImage(500, 200, QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(0)
        with patch.object(visual, "_paint_expanded_lyric_row") as paint:
            with QPainter(frame) as painter:
                visual._paint_lyric_viewport(painter, QRectF(0, 0, 480, 190))
        centered = [call for call in paint.call_args_list if call.args[6]]
        self.assertEqual(len(centered), 1)
        self.assertEqual(centered[0].args[2], "Next unread")
        self.assertEqual(centered[0].args[7]["progress"], 0.0)
        self.assertEqual(visual.lyric_index, 0)

    def test_low_mode_compact_bilingual_rows_share_one_block_renderer_call(self):
        visual = self._visual()
        visual.music_surface_reading_strategy = "low_perception"
        visual.lyric_flow_style = "wave_stream"
        visual.lyrics = ["Hello world\n你好世界"]
        visual.lyric_index = 0
        visual._should_paint_album_island = lambda: False
        visual._ember_enabled = lambda: False
        visual._gelv_enabled = lambda: False
        visual._compact_lyric_lines = lambda: visual.lyrics
        visual._lyric_phase = lambda _now: {"whole_block_progress": 0.4}
        visual._compact_primary_lyric_font_size = lambda: 18
        visual._lyric_display_lines = lambda _text: ["Hello world", "你好世界"]
        visual._music_lyric_base_text_color = lambda alpha=132: QColor(90, 90, 90, alpha)
        visual._music_lyric_text_color = lambda alpha=246: QColor(255, 255, 255, alpha)
        visual._dual_line_secondary_font_size = lambda: 12
        visual.dual_line_secondary_weight = 72
        visual.dual_line_secondary_spacing = 100
        frame = QImage(500, 100, QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(0)
        with patch.object(visual._wave_reading_renderer, "paint_whole_block") as block:
            with QPainter(frame) as painter:
                visual._paint_compact_lyric_viewport(painter, QRectF(0, 0, 480, 90))
        self.assertEqual(block.call_count, 1)
        self.assertEqual([row[2] for row in block.call_args.args[1]], ["Hello world", "你好世界"])

    def test_intro_punctuation_starts_at_same_unread_color_as_other_lyrics(self):
        renderer = LyricReadingRenderer()
        unread = QColor(120, 120, 120, 255)
        read = QColor(255, 255, 255, 255)
        rows = ((QRectF(0, 0, 240, 60), 24.0, "· · ·", QFont("Arial", 20), unread, read),)
        frame = QImage(260, 70, QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(0)
        with QPainter(frame) as painter:
            renderer.paint_whole_block(painter, rows, 0.0)
        colors = [frame.pixelColor(x, y) for y in range(70) for x in range(260)
                  if frame.pixelColor(x, y).alpha() > 240]
        self.assertTrue(colors)
        self.assertTrue(all(abs(color.red() - unread.red()) <= 1 for color in colors))

    def test_slow_lift_produces_intermediate_subpixel_frames(self):
        visual = self._visual()
        rect = QRectF(0.0, 8.0, 360.0, 76.0)
        # Equal colors isolate vertical motion from the horizontal read wipe.
        frames = []
        for step in range(31):
            progress = 0.15 + 0.60 * step / 30.0
            image = QImage(420, 100, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            with QPainter(image) as painter:
                visual._paint_live_lyric_text(
                    painter, rect, "Shine", 18,
                    QColor(255, 255, 255, 255), QColor(255, 255, 255, 255),
                    True, {"progress": progress}, centered=True,
                )
            frames.append(bytes(image.constBits()))
        self.assertGreaterEqual(len(set(frames)), 20)

    def test_real_unit_front_uses_shaped_character_widths(self):
        visual = self._visual()
        renderer = visual._wave_reading_renderer
        font = QFont("Alibaba PuHuiTi", 28)
        run = renderer.run("Wi", font)
        self.assertGreater(
            run.char_boundaries[1] - run.char_boundaries[0],
            run.char_boundaries[2] - run.char_boundaries[1],
        )
        units = (
            TimedLyricUnit(10.0, 11.0, "W"),
            TimedLyricUnit(11.0, 12.0, "i"),
        )
        expected_hard_front = run.char_boundaries[1] * 0.5
        feather = reading_front(0.25, run.width, font, size_scale=run.size_scale, resolve_point_size=True)[1]
        image = QImage(300, 90, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        with patch("local.music_lyric_reading.read_wipe_image_at_front", wraps=read_wipe_image_at_front) as wipe:
            with QPainter(image) as painter:
                renderer.paint(
                    painter, QRectF(0.0, 0.0, 250.0, 80.0), 12.0,
                    "Wi", font, 0.25, QColor(100, 100, 100), QColor(255, 255, 255),
                    timed_units=units, timed_position=10.5, timed_line_end=12.0,
                )
        self.assertTrue(wipe.call_args_list)
        self.assertAlmostEqual(wipe.call_args_list[0].kwargs["front"], expected_hard_front + feather)
        self.assertNotAlmostEqual(expected_hard_front, run.width * 0.25, delta=1.0)

    def test_clamped_timed_front_preserves_white_edge_and_lift_after_clock_backstep(self):
        visual = self._visual()
        visual.timed_lyrics = [(10.0, "Wi"), (12.0, "next")]
        visual.timed_lyric_units = [
            (TimedLyricUnit(10.0, 11.0, "W"), TimedLyricUnit(11.0, 12.0, "i")),
            (),
        ]
        visual.timed_lyric_timing_kinds = ["unit", "line"]
        visual.lyric_index = 0
        visual.lyric_changed_at = 10.0
        visual.lyric_last_phase_index = -1
        visual.lyric_last_phase_progress = 0.0
        visual.lyric_last_phase_at = 10.0
        visual.lyric_last_phase_timing_kind = ""
        visual.lyric_transition_kind = ""
        visual.media_position_source = "windows-media"
        visual.media_is_playing = True
        visual._effective_lyric_offset_seconds = lambda: 0.0
        position = [10.5]
        visual._estimated_media_position = lambda _now: position[0]
        rect = QRectF(0.0, 8.0, 360.0, 76.0)

        def shown_state(phase):
            image = QImage(420, 100, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            fronts = []
            with patch("local.music_lyric_reading.read_wipe_image_at_front", wraps=read_wipe_image_at_front) as wipe:
                with QPainter(image) as painter:
                    visual._paint_live_lyric_text(
                        painter, rect, "Wi", 18,
                        QColor(96, 96, 96), QColor(255, 255, 255),
                        True, phase, centered=True,
                    )
                fronts = [call.kwargs["front"] for call in wipe.call_args_list]
            return bytes(image.constBits()), fronts

        forward_phase = visual._lyric_phase(10.5)
        forward_pixels, forward_fronts = shown_state(forward_phase)
        position[0] = 10.45
        backstep_phase = visual._lyric_phase(10.55)
        backstep_pixels, backstep_fronts = shown_state(backstep_phase)
        self.assertEqual(forward_phase["timed_character_front"], backstep_phase["timed_character_front"])
        self.assertEqual(forward_fronts, backstep_fronts)
        self.assertEqual(forward_pixels, backstep_pixels)

        visual.lyric_transition_kind = "seek"
        seek_phase = visual._lyric_phase(10.60)
        seek_pixels, seek_fronts = shown_state(seek_phase)
        self.assertLess(seek_phase["timed_character_front"], backstep_phase["timed_character_front"])
        self.assertLess(seek_fronts[0], backstep_fronts[0])
        self.assertNotEqual(seek_pixels, backstep_pixels)

        position[0] = 10.5
        visual.lyric_transition_kind = ""
        visual.media_is_playing = False
        visual._lyric_phase(10.65)
        position[0] = 10.45
        pause_phase = visual._lyric_phase(10.70)
        pause_pixels, pause_fronts = shown_state(pause_phase)
        self.assertLess(pause_fronts[0], forward_fronts[0])
        self.assertNotEqual(pause_pixels, forward_pixels)

        visual.media_is_playing = True
        visual.media_position_source = "local-seek"
        position[0] = 10.35
        local_seek_phase = visual._lyric_phase(10.75)
        _local_seek_pixels, local_seek_fronts = shown_state(local_seek_phase)
        self.assertLess(local_seek_fronts[0], pause_fronts[0])

    def test_line_timed_lift_overlaps_neighboring_units(self):
        visual = self._visual()
        renderer = visual._wave_reading_renderer
        text = "甲乙丙丁戊己"
        font = QFont("Alibaba PuHuiTi", 28)
        run = renderer.run(text, font)
        rect = QRectF(0.0, 0.0, 300.0, 80.0)

        def origins_at(progress):
            image = QImage(360, 90, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            origins = []
            with patch.object(renderer, "_draw_subpixel_image", side_effect=lambda _p, origin, _layer: origins.append((origin.x(), origin.y()))):
                with QPainter(image) as painter:
                    renderer.paint(
                        painter, rect, 12.0, text, font, progress,
                        QColor(100, 100, 100), QColor(255, 255, 255),
                    )
            return [
                next(y for x, y in origins if abs(x - (12.0 + left)) < 0.01)
                for _source, left in run.units
            ]

        start = origins_at(0.0)
        first_wave = origins_at(1.0 / 8.0)
        second_wave = origins_at(2.0 / 8.0)
        end = origins_at(1.0)
        self.assertLess(first_wave[0], start[0])
        self.assertLess(first_wave[1], start[1])
        self.assertAlmostEqual(first_wave[2], start[2], delta=0.01)
        self.assertLess(second_wave[0], first_wave[0])
        self.assertLess(second_wave[2], first_wave[2])
        self.assertTrue(all(final < initial for final, initial in zip(end, start)))

    def test_timed_lift_overlaps_neighboring_units_without_moving_read_front(self):
        visual = self._visual()
        renderer = visual._wave_reading_renderer
        font = QFont("Alibaba PuHuiTi", 28)
        text = "甲乙丙丁戊己"
        run = renderer.run(text, font)
        units = tuple(
            TimedLyricUnit(10.0 + index, 11.0 + index, character)
            for index, character in enumerate(text)
        )
        rect = QRectF(0.0, 0.0, 300.0, 80.0)

        def origins_at(position):
            image = QImage(360, 90, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            origins = []
            with patch.object(renderer, "_draw_subpixel_image", side_effect=lambda _p, origin, _layer: origins.append((origin.x(), origin.y()))):
                with patch("local.music_lyric_reading.read_wipe_image_at_front", wraps=read_wipe_image_at_front) as wipe:
                    with QPainter(image) as painter:
                        renderer.paint(
                            painter, rect, 12.0, text, font, 0.0,
                            QColor(100, 100, 100), QColor(255, 255, 255),
                            timed_units=units, timed_position=position, timed_line_end=16.0,
                        )
            heights = [
                next(y for x, y in origins if abs(x - (12.0 + left)) < 0.01)
                for _source, left in run.units
            ]
            return heights, [call.kwargs["front"] for call in wipe.call_args_list]

        start, _start_fronts = origins_at(10.0)
        first_wave, fronts = origins_at(10.75)
        second_wave, _second_fronts = origins_at(11.5)
        self.assertLess(first_wave[0], start[0])
        self.assertLess(first_wave[1], start[1])
        self.assertAlmostEqual(first_wave[2], start[2], delta=0.01)
        self.assertLess(second_wave[0], first_wave[0])
        self.assertLess(second_wave[2], first_wave[2])
        hard_front = run.char_boundaries[0] + 0.75 * (run.char_boundaries[1] - run.char_boundaries[0])
        feather = reading_front(0.0, run.width, font, size_scale=run.size_scale, resolve_point_size=True)[1]
        self.assertLess(hard_front, run.char_boundaries[1])
        self.assertTrue(fronts)
        self.assertAlmostEqual(fronts[0], hard_front + feather, delta=0.01)

    def test_long_first_timed_word_maps_lift_to_spoken_unit_clock(self):
        renderer = LyricReadingRenderer()
        text = "extraordinarily a b"
        font = QFont("Alibaba PuHuiTi", 28)
        run = renderer.run(text, font)
        self.assertEqual(len(run.units), 3)
        units = (
            TimedLyricUnit(10.0, 12.0, "extraordinarily "),
            TimedLyricUnit(13.0, 14.0, "a "),
            TimedLyricUnit(15.0, 16.0, "b"),
        )
        frame = QImage(500, 90, QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(0)
        from local.music_lyric_motion import continuous_read_lift

        lift_clocks = {}
        color_fronts = {}
        with patch("local.music_lyric_reading.continuous_read_lift", wraps=continuous_read_lift) as lift:
            with patch("local.music_lyric_reading.read_wipe_image_at_front", wraps=read_wipe_image_at_front) as wipe:
                for position in (11.0, 12.0, 12.5, 13.5):
                    lift.reset_mock()
                    wipe.reset_mock()
                    with QPainter(frame) as painter:
                        renderer.paint(
                            painter, QRectF(0.0, 0.0, 480.0, 80.0), 12.0,
                            text, font, 0.0, QColor(100, 100, 100), QColor(255, 255, 255),
                            timed_units=units, timed_position=position, timed_line_end=16.0,
                        )
                    lift_clocks[position] = lift.call_args_list[0].args[0]
                    color_fronts[position] = wipe.call_args_list[0].kwargs["front"]
        self.assertGreater(lift_clocks[11.0], 0.0)
        self.assertLess(lift_clocks[11.0], 1.0 / 3.0)
        self.assertAlmostEqual(lift_clocks[12.0], 1.0 / 3.0)
        self.assertEqual(lift_clocks[12.0], lift_clocks[12.5])
        self.assertGreater(lift_clocks[13.5], 1.0 / 3.0)
        self.assertLess(lift_clocks[13.5], 2.0 / 3.0)
        self.assertEqual(color_fronts[12.0], color_fronts[12.5])
        self.assertAlmostEqual(color_fronts[12.5] - color_fronts[11.0], run.unit_boundaries[1] * 0.5)

    def test_translation_row_does_not_borrow_primary_timed_units(self):
        visual = self._visual()
        phase = {
            "progress": 0.25,
            "timed_primary_text": "\u8c01\u4eba\u90fd",
            "timed_units": (TimedLyricUnit(10.0, 11.0, "\u8c01"),),
            "timed_position": 10.5,
        }
        image = QImage(420, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        with patch.object(visual, "_paint_wave_stream_lyric_chunks") as draw:
            with QPainter(image) as painter:
                visual._paint_live_lyric_text(
                    painter, QRectF(0.0, 8.0, 360.0, 76.0), "Anyone", 18,
                    QColor(100, 100, 100), QColor(255, 255, 255), True, phase,
                )
        self.assertNotIn("timed_units", draw.call_args.kwargs)

    def test_short_island_row_does_not_crop_rising_word_ink(self):
        visual = self._visual()
        text = "Shine softly"
        progress = 0.95
        row = QRectF(34.0, 35.0, 350.0, 15.0)
        image = QImage(420, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        with QPainter(image) as painter:
            visual._paint_live_lyric_text(
                painter, row, text, 18,
                QColor(122, 74, 112, 220), QColor(255, 255, 255, 240),
                True, {"progress": progress}, centered=True, centered_scroll=True,
            )
        direct = QImage(420, 100, QImage.Format.Format_ARGB32_Premultiplied)
        direct.fill(0)
        layout = visual._lyric_layout(text, 18, True)
        x = visual._scrolling_text_x(row, layout.total_width, True, True, focus_px=progress * layout.total_width)
        with QPainter(direct) as painter:
            visual._wave_reading_renderer.paint(
                painter, row, x, text, visual._wave_stream_font(18), progress,
                QColor(122, 74, 112, 220), QColor(255, 255, 255, 240),
            )
        self.assertEqual(bytes(image.constBits()), bytes(direct.constBits()))
        top = self._ink_bounds(image)[1]
        self.assertLess(top, row.top())

    def test_detailed_legacy_knobs_do_not_change_wave_reading_frames(self):
        visual = self._visual()
        rect = QRectF(0.0, 8.0, 360.0, 76.0)
        visual.wave_word_fill_enabled = True
        visual.wave_color_fill_enabled = True
        visual.lyric_elastic_strength = 160
        visual.lyric_elastic_mode = "left"
        visual.lyric_word_spacing = 150
        enabled = self._paint_active(visual, "Shine softly", 0.42, rect)
        visual.wave_word_fill_enabled = False
        visual.wave_color_fill_enabled = False
        visual.lyric_elastic_strength = 0
        visual.lyric_elastic_mode = "both"
        visual.lyric_word_spacing = 60
        disabled = self._paint_active(visual, "Shine softly", 0.42, rect)
        self.assertEqual(bytes(enabled.constBits()), bytes(disabled.constBits()))

    def test_paused_phase_renders_identically_without_time_breath(self):
        visual = self._visual()
        rect = QRectF(0.0, 8.0, 360.0, 76.0)
        visual.media_is_playing = True
        before = self._paint_active(visual, "Shine softly", 0.42, rect)
        visual.media_is_playing = False
        after = self._paint_active(visual, "Shine softly", 0.42, rect)
        self.assertEqual(bytes(before.constBits()), bytes(after.constBits()))

    def test_high_refresh_request_only_tracks_playing_visible_lyrics(self):
        visual = self._visual()
        visual.animation_fps = 60
        visual.lyric_animation_fps = 120
        visual.media_is_playing = True
        visual.lyrics = ["Shine softly"]
        visual._should_paint_album_island = lambda: False
        with patch.object(IslandStackVisual, "_display_refresh_rate_fps_cap", return_value=144):
            self.assertEqual(visual._effective_animation_fps(True, now=10.0), 120)
            visual.media_is_playing = False
            self.assertEqual(visual._effective_animation_fps(True, now=10.0), 60)
            visual.media_is_playing = True
            visual._should_paint_album_island = lambda: True
            self.assertEqual(visual._effective_animation_fps(True, now=10.0), 60)
            visual._should_paint_album_island = lambda: False
            self.assertEqual(visual._effective_animation_fps(False, now=10.0), 60)

    def test_shared_feather_crosses_word_boundary_without_restarting(self):
        visual = self._visual()
        font = QFont("Arial", 18)
        run = visual._wave_reading_renderer.run("Shine softly", font)
        self.assertEqual(len(run.units), 2)
        first_end = run.units[1][1]
        source = QImage(80, 20, QImage.Format.Format_ARGB32_Premultiplied)
        source.fill(QColor(255, 255, 255, 255))
        left = read_wipe_image_at_front(source, front=first_end + 17.0, feather=28.0, x_offset=first_end - 10.0)
        right = read_wipe_image_at_front(source, front=first_end + 17.0, feather=28.0, x_offset=first_end + 10.0)
        # Both portions use the same global pixel front.  Across adjacent
        # layers, the same x coordinate yields the same intermediate alpha.
        self.assertAlmostEqual(left.pixelColor(20, 10).alpha(), right.pixelColor(0, 10).alpha(), delta=1)
        self.assertGreater(left.pixelColor(20, 10).alpha(), 0)
        self.assertLess(left.pixelColor(20, 10).alpha(), 255)

    def test_expanded_line_follows_position_spring_on_adjacent_line_change(self):
        visual = self._visual()
        visual.lyrics = ["one", "two", "three"]
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.media_track_id = "track"
        visual.track_title = "title"
        visual.artist = "artist"
        visual.lyric_font_size = 18
        visual.lyric_transition_kind = ""
        visual.lyric_changed_at = time.monotonic()
        visual._should_paint_album_island = lambda: False
        visual._ember_enabled = lambda: False
        visual._gelv_enabled = lambda: False
        visual._is_instrumental_track = lambda: False
        visual._current_lyric_display_lines = lambda: [visual.lyrics[visual.lyric_index]]
        visual._lyric_phase = lambda _now: {"progress": 0.0, "confidence": 0.78}
        visual._lyric_transition_progress = lambda _duration: (1.0, 1.0)
        rows = []
        visual._paint_expanded_lyric_row = (
            lambda _painter, row, text, _size, _alpha, _bold, active, _phase:
            rows.append((text, row.top(), active))
        )
        rect = QRectF(0.0, 0.0, 300.0, 180.0)
        image = QImage(300, 180, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        with QPainter(image) as painter:
            with patch("local.island_stack_visual.time.monotonic", return_value=100.0):
                visual._paint_lyric_viewport(painter, rect)
            rows.clear()
            visual.prev_lyric_index = 0
            visual.lyric_index = 1
            with patch("local.island_stack_visual.time.monotonic", return_value=100.016):
                visual._paint_lyric_viewport(painter, rect)
            entering_top = next(top for text, top, active in rows if text == "two" and active)
            rows.clear()
            with patch("local.island_stack_visual.time.monotonic", return_value=100.076):
                visual._paint_lyric_viewport(painter, rect)
            later_top = next(top for text, top, active in rows if text == "two" and active)
        target_top = rect.center().y() - max(30.0, 18.0 + 12.0) / 2.0
        self.assertGreater(entering_top, later_top)
        self.assertGreater(later_top, target_top)


if __name__ == "__main__":
    unittest.main()
