from __future__ import annotations

import math
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.models import TimedLyricUnit
from local.island_stack_visual import IslandStackVisual
from local.music_lyric_reading import LyricReadingRenderer
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_lyric_visual_layers import _LayerOwner


class IslandLyricFollowGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        IslandStackVisual.load_embedded_lyric_fonts()

    @staticmethod
    def _visual(style="wave_stream"):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = style
        visual.lyric_font_size = 24
        visual.dual_line_font_size = 13
        visual.enable_dual_line_lyrics = False
        visual.font_shrink_scale = 1.0
        visual.location_mode = "top"
        visual.top_notch_height_mode = "standard"
        visual.high_quality_font_rendering = False
        visual._lyric_layout_cache = {}
        visual._wave_reading_renderer = LyricReadingRenderer()
        visual.lyrics = ["ÀÉgj 映照着世界", "Next words", "Following words"]
        visual.timed_lyrics = [(0.0, visual.lyrics[0]), (4.0, visual.lyrics[1]), (8.0, visual.lyrics[2])]
        visual.timed_lyric_units = [(), (), ()]
        visual.timed_lyric_timing_kinds = ["line"] * 3
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.lyric_changed_at = 100.0
        visual.lyric_transition_kind = ""
        visual.lyric_last_phase_index = -1
        visual.lyric_last_phase_progress = 0.0
        visual.lyric_last_phase_at = 100.0
        visual.track_title = "Follow geometry"
        visual.artist = "Test singer"
        visual.media_track_id = "follow-geometry"
        visual.media_source_app = "test-player"
        visual.media_position_source = "windows-media"
        visual.media_position_seconds = 0.0
        visual.media_position_sync_at = 100.0
        visual.media_position_sampled_at = 100.0
        visual.media_position_observed_seconds = 0.0
        visual.media_duration_seconds = 12.0
        visual.media_is_playing = True
        visual.music_surface_reading_strategy = "gold"
        visual._effective_lyric_offset_seconds = lambda: 0.0
        visual._should_paint_album_island = lambda: False
        visual._is_instrumental_track = lambda: False
        visual._empty_music_text_active = lambda: False
        visual._music_lyric_base_text_color = lambda alpha=132: QColor(90, 90, 90, alpha)
        visual._music_lyric_text_color = lambda alpha=246: QColor(255, 255, 255, alpha)
        visual._effective_music_ambience_style = lambda: "solid_album"
        return visual

    def test_first_island_paint_uses_current_source_when_owner_tick_is_old(self):
        for style in ("wave_stream", "gelv_songscatter"):
            for strategy in ("gold", "low_perception"):
                with self.subTest(style=style, strategy=strategy):
                    visual = self._visual(style)
                    visual.music_surface_reading_strategy = strategy
                    visual._lyric_phase(100.0)
                    # The owner timer still holds row 0 when a visible paint
                    # arrives after returning from a different music surface.
                    phase = visual._lyric_phase(105.0)
                    self.assertEqual(visual.lyric_index, 0)
                    self.assertEqual(phase["active_index"], 1)
                    self.assertEqual(phase["visual_focus_index"], 1)
                    image = QImage(500, 100, QImage.Format.Format_ARGB32_Premultiplied)
                    image.fill(0)
                    with patch.object(visual, "_paint_live_lyric_text") as paint:
                        with patch("local.island_stack_visual.time.monotonic", return_value=105.0):
                            with QPainter(image) as painter:
                                visual._paint_compact_lyric_viewport(painter, QRectF(0, 0, 490, 90))
                    self.assertEqual(paint.call_args.args[2], "Next words")
                    self.assertGreater(paint.call_args.args[7]["progress"], 0.0)
                    if strategy == "low_perception":
                        self.assertEqual(paint.call_args.args[7]["whole_block_progress"], 1.0)

    def test_island_and_large_surface_share_validated_source_progress_after_hidden_time(self):
        visual = self._visual()
        owner = _LayerOwner()
        for name in ("media_track_id", "track_title", "artist", "media_source_app", "media_position_source",
                     "media_position_seconds", "media_position_sync_at", "media_position_sampled_at",
                     "media_position_observed_seconds", "media_duration_seconds", "media_is_playing",
                     "timed_lyrics", "timed_lyric_units", "timed_lyric_timing_kinds"):
            setattr(owner, name, getattr(visual, name))
        owner._estimated_media_position = visual._estimated_media_position
        owner._effective_lyric_offset_seconds = lambda: 0.0
        window = MusicSurfaceWindow(owner)
        try:
            for now in (100.0, 100.016, 100.032, 105.0, 105.016, 105.032):
                phase = visual._lyric_phase(now)
                frame = window._lyric_frame(visual.timed_lyrics, now)
                self.assertEqual(int(phase["active_index"]), frame.active_index)
                self.assertAlmostEqual(phase["progress"], frame.reading_progress, places=6)
                self.assertAlmostEqual(visual._island_lyric_source_clock.last_source_position,
                                       window._lyric_motion.last_source_position, places=6)
        finally:
            window.shutdown()
            owner.close()

    def test_frozen_uia_row_does_not_advance_even_when_owner_tick_is_old(self):
        visual = self._visual()
        visual.media_position_source = "netease-uia-lyrics"
        visual._lyric_phase(100.0)
        phase = visual._lyric_phase(110.0)
        self.assertEqual(phase["active_index"], 0)
        self.assertEqual(phase["visual_focus_index"], 0)

    def test_paused_explicit_seek_reanchors_first_paint_without_mutating_owner_index(self):
        visual = self._visual()
        visual._lyric_phase(100.0)
        visual.media_is_playing = False
        visual.media_position_source = "local-seek"
        visual.media_position_seconds = 5.0
        visual.media_position_observed_seconds = 5.0
        visual.media_position_sync_at = visual.media_position_sampled_at = 101.0
        phase = visual._lyric_phase(101.0)
        self.assertEqual(phase["active_index"], 1)
        self.assertEqual(phase["visual_focus_index"], 1)
        self.assertAlmostEqual(phase["progress"], 0.25)
        self.assertEqual(visual.lyric_index, 0)

    def test_real_units_on_first_corrected_paint_keep_actual_reading_time(self):
        visual = self._visual()
        visual.lyrics[1] = "Next words"
        visual.timed_lyric_timing_kinds[1] = "unit"
        visual.timed_lyric_units[1] = (TimedLyricUnit(4.0, 5.0, "Next "), TimedLyricUnit(5.0, 6.0, "words"))
        visual.music_surface_reading_strategy = "low_perception"
        visual._lyric_phase(100.0)
        phase = visual._lyric_phase(104.5)
        self.assertEqual(phase["active_index"], 1)
        self.assertEqual(phase["visual_focus_index"], 1)
        self.assertAlmostEqual(phase["timed_position"], 4.5)
        self.assertGreater(phase["timed_character_front"], 0.0)

    def test_first_paint_single_to_bilingual_uses_one_phase_for_shell_and_both_rows(self):
        for style in ("wave_stream", "gelv_songscatter"):
            with self.subTest(style=style):
                visual = self._visual(style)
                visual.enable_dual_line_lyrics = True
                visual.lyrics[1] = "New lyric\n新的译文"
                visual.timed_lyrics[1] = (4.0, visual.lyrics[1])
                heights = []

                def paint_frame(_event):
                    self.assertEqual(visual._current_lyric_display_lines(), ["New lyric", "新的译文"])
                    heights.append(visual._u(visual._top_notch_music_height_raw()))
                    top, bottom = visual._compact_lyric_vertical_padding(True)
                    rect = QRectF(20, 40+top, 540, heights[-1]-top-bottom)
                    image = QImage(600, 170, QImage.Format.Format_ARGB32_Premultiplied)
                    image.fill(0)
                    with patch.object(visual, "_paint_live_lyric_text") as ink:
                        with QPainter(image) as painter:
                            visual._paint_compact_lyric_viewport(painter, rect)
                    self.assertEqual([call.args[2] for call in ink.call_args_list], ["New lyric", "新的译文"])
                    self.assertEqual(ink.call_args_list[0].args[3], visual._compact_primary_lyric_font_size())

                with patch.object(visual, "_paint_island_frame", side_effect=paint_frame):
                    with patch.object(visual, "_lyric_phase", wraps=visual._lyric_phase) as phase:
                        for now in (105.0, 105.016):
                            with patch("local.island_stack_visual.time.monotonic", return_value=now):
                                visual.paintEvent(None)
                        self.assertEqual(phase.call_count, 2)
                self.assertEqual(heights[0], heights[1])
                self.assertEqual(visual.lyric_index, 0)
                self.assertIsNone(visual._island_lyric_frame_at)
                self.assertIsNone(visual._island_lyric_frame_phase)

    def test_display_phase_lease_is_cleared_when_frame_raises(self):
        visual = self._visual()
        with patch.object(visual, "_paint_island_frame", side_effect=RuntimeError("paint interrupted")):
            with self.assertRaises(RuntimeError):
                visual.paintEvent(None)
        self.assertIsNone(visual._island_lyric_frame_at)
        self.assertIsNone(visual._island_lyric_frame_phase)

    def test_stale_owner_tail_first_paint_uses_tail_start_in_source_time(self):
        for playing in (True, False):
            with self.subTest(playing=playing):
                visual = self._visual()
                visual._lyric_phase(100.0)
                visual.media_is_playing = playing
                visual.media_position_source = "local-seek"
                visual.media_position_seconds = visual.media_position_observed_seconds = 8.1
                visual.media_position_sampled_at = visual.media_position_sync_at = 108.1
                phase = visual._lyric_phase(108.1)
                self.assertEqual(phase["active_index"], 2)
                self.assertAlmostEqual(phase["elapsed"], 0.1)
                self.assertAlmostEqual(phase["progress"], 0.1/2.45)
                self.assertEqual(visual.lyric_index, 0)

    def test_untimed_rows_retain_owner_wall_clock_reading(self):
        visual = self._visual()
        visual.timed_lyrics = []
        visual.lyric_index = 1
        visual.lyric_changed_at = 104.0
        phase = visual._lyric_phase(105.0)
        self.assertEqual(phase["active_index"], 1)
        self.assertEqual(phase["visual_focus_index"], 1)
        self.assertAlmostEqual(phase["progress"], 1.0/2.45)

    @staticmethod
    def _render_row(visual, rect, text, size, progress, dpr, clip):
        image = QImage(math.ceil(600*dpr), math.ceil(170*dpr), QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(0)
        with QPainter(image) as painter:
            if clip:
                painter.setClipRect(rect)
            visual._paint_live_lyric_text(painter, rect, text, size, QColor(90, 90, 90),
                                         QColor(255, 255, 255), True, {"progress": progress},
                                         centered=True, centered_scroll=True)
        return image

    def test_maximum_font_top_standard_preserves_every_lifted_ink_pixel(self):
        for style in ("wave_stream", "gelv_songscatter"):
            for dpr in (1.0, 1.25, 1.5, 2.0):
                for text in ("ÀÉgj 映照着世界", "ǜÅg अंधेरा 한글"):
                    with self.subTest(style=style, dpr=dpr, text=text):
                        visual = self._visual(style)
                        visual.lyrics[0] = text
                        shell_height = visual._u(visual._top_notch_music_height_raw())
                        top, bottom = visual._compact_lyric_vertical_padding(False)
                        rect = QRectF(20, 40+top, 540, shell_height-top-bottom)
                        size = visual._compact_primary_lyric_font_size()
                        self.assertEqual(visual._fit_island_lyric_row_size(text, size, True, rect.height()), size)
                        for progress in (0.0, 0.35, 0.75, 1.0):
                            reference = self._render_row(visual, rect, text, size, progress, dpr, False)
                            clipped = self._render_row(visual, rect, text, size, progress, dpr, True)
                            self.assertEqual(reference, clipped, f"reading progress {progress}")

    def test_fixed_height_row_fits_glyphs_before_painting(self):
        for style in ("wave_stream", "gelv_songscatter"):
            visual = self._visual(style)
            text = visual.lyrics[0]
            original = visual._compact_primary_lyric_font_size()
            fitted = visual._fit_island_lyric_row_size(text, original, True, 24.0)
            self.assertLess(fitted, original)
            self.assertLessEqual(visual._island_lyric_row_content_height(text, fitted, True), 24.0)
            rect = QRectF(20, 40, 540, 24)
            self.assertEqual(self._render_row(visual, rect, text, fitted, 0.0, 1.25, False),
                             self._render_row(visual, rect, text, fitted, 0.0, 1.25, True))

    def test_bilingual_rows_reserve_full_font_travel_inside_live_viewport(self):
        for style in ("wave_stream", "gelv_songscatter"):
            for shell_height in (46.0, 80.0, 100.0):
                with self.subTest(style=style, shell_height=shell_height):
                    visual = self._visual(style)
                    texts = ("ÀÉgj 映照着世界", "ǜÅg 翻译世界")
                    rect = QRectF(20, 30, 540, shell_height)
                    first, second, primary, secondary = visual._compact_dual_line_lyric_rows(rect, 20, False, texts)
                    self.assertGreaterEqual(first.top(), rect.top())
                    self.assertLessEqual(second.bottom(), rect.bottom())
                    self.assertLess(first.bottom(), second.top())
                    for row, text, size in ((first, texts[0], primary), (second, texts[1], secondary)):
                        self.assertGreaterEqual(row.height(), visual._island_lyric_row_content_height(text, size, True))
                        self.assertEqual(self._render_row(visual, row, text, size, 0.0, 1.25, False),
                                         self._render_row(visual, row, text, size, 0.0, 1.25, True))


if __name__ == "__main__":
    unittest.main()
