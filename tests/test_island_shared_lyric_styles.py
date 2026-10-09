from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.models import TimedLyricUnit
from local.island_stack_visual import IslandStackVisual
from local.music_lyric_reading import LyricReadingRenderer


class IslandSharedLyricStylesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        IslandStackVisual.load_embedded_lyric_fonts()

    @staticmethod
    def _visual(style: str) -> IslandStackVisual:
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyric_flow_style = style
        visual._u = lambda value: float(value)
        visual.font_shrink_scale = 1.0
        visual.high_quality_font_rendering = False
        visual.music_ambience_style = "sea_level"
        visual.music_surface_reading_strategy = "gold_reading"
        visual.location_mode = "free"
        visual.top_notch_height_mode = "standard"
        visual.lyric_font_size = 18
        visual.enable_dual_line_lyrics = True
        visual.dual_line_font_size = 12
        visual.dual_line_secondary_weight = 72
        visual.dual_line_secondary_spacing = 100
        visual.lyrics = ["Shine softly 春風\n春风轻拂", "Then glow\n随后闪耀", "Following line"]
        visual.lyric_index = visual.prev_lyric_index = 0
        visual.lyric_changed_at = 100.0
        visual.lyric_transition_kind = ""
        visual.media_track_id = "shared-island-reader"
        visual.track_title = "Shared island reader"
        visual.artist = "Fixture"
        visual.media_is_playing = True
        visual.expanded = True
        visual.scene_suppressed = False
        visual._should_paint_album_island = lambda: False
        visual._is_instrumental_track = lambda: False
        visual._lyric_phase = lambda _now: {"progress": 0.45}
        visual._lyric_layout_cache = {}
        visual._wave_reading_renderer = LyricReadingRenderer()
        return visual

    @staticmethod
    def _render(visual: IslandStackVisual, expanded: bool, now: float = 100.0) -> QImage:
        image = QImage(440, 200, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        rect = QRectF(0, 0, 420, 190 if expanded else 90)
        with patch("local.island_stack_visual.time.monotonic", return_value=now):
            with QPainter(image) as painter:
                if expanded:
                    visual._paint_lyric_viewport(painter, rect)
                else:
                    visual._paint_compact_lyric_viewport(painter, rect)
        return image

    def test_only_typeface_changes_when_legacy_effect_knobs_differ(self):
        for expanded in (False, True):
            with self.subTest(expanded=expanded):
                wave = self._visual("wave_stream")
                gelv = self._visual("gelv_songscatter")
                # Hold typography constant to compare every remaining pixel:
                # placement, opacity, read front, clipping and translation.
                for visual in (wave, gelv):
                    visual._wave_stream_font = lambda size, *_args, **_kwargs: QFont("Arial", int(size))
                gelv.gelv_primary_font_size = 30
                gelv.gelv_secondary_font_size = 24
                gelv.gelv_initial_word_spacing = 320
                gelv.gelv_word_spacing = 420
                gelv.gelv_word_fill_enabled = False
                gelv.gelv_color_fill_enabled = True
                gelv._paint_gelv_compact_lyrics = lambda *_args: self.fail("Legacy Gelv paint ran")
                gelv._paint_gelv_expanded_lyrics = lambda *_args: self.fail("Legacy Gelv paint ran")
                self.assertEqual(self._render(wave, expanded), self._render(gelv, expanded))

    def test_gelv_shared_layout_uses_embedded_serif_and_separate_cache_entry(self):
        visual = self._visual("wave_stream")
        wave = visual._lyric_layout("Shine 春風 😀", 18, True)
        wave_font = visual._wave_stream_font(18, QFont.Weight.Bold)
        visual.lyric_flow_style = "gelv_songscatter"
        gelv = visual._lyric_layout("Shine 春風 😀", 18, True)
        gelv_font = visual._wave_stream_font(18, QFont.Weight.Bold)
        self.assertEqual(wave_font.families()[0], "Alibaba PuHuiTi")
        self.assertEqual(gelv_font.families()[0], "Noto Serif SC ExtraBold")
        self.assertEqual(gelv_font.pointSize(), wave_font.pointSize())
        self.assertEqual(gelv.chunks, wave.chunks)
        self.assertEqual(gelv.chunks, ("Shine ", "春", "風 ", "😀"))
        self.assertNotEqual(gelv.font_key, wave.font_key)
        self.assertIsNot(gelv, wave)
        self.assertIs(visual._lyric_layout("Shine 春風 😀", 18, True), gelv)

    def test_active_and_inactive_expanded_rows_use_same_serif_channel(self):
        visual = self._visual("gelv_songscatter")
        calls = []
        visual._paint_scrolling_text = lambda *_args, **kwargs: calls.append(kwargs["font"])
        visual._paint_expanded_lyric_row(
            object(), QRectF(0, 0, 300, 30), "Following line", 11, 76,
            False, False, {"progress": 0.4},
        )
        self.assertEqual(calls[0].families()[0], "Noto Serif SC ExtraBold")
        fonts = []
        visual._wave_reading_renderer.paint = lambda _p, _r, _x, _t, font, *_args, **_kw: fonts.append(font)
        layout = visual._lyric_layout("Shine softly", 18, True)
        visual._paint_wave_stream_lyric_chunks(
            object(), QRectF(0, 0, 300, 45), 0, layout, 18, True,
            QColor(255, 255, 255, 120), QColor(255, 255, 255, 240), 0.4, 1.0,
        )
        self.assertEqual(fonts[0].families()[0], calls[0].families()[0])

    def test_low_perception_bilingual_block_uses_shared_half_second_ramp(self):
        calls_by_style = []
        for style in ("wave_stream", "gelv_songscatter"):
            visual = self._visual(style)
            visual._lyric_phase = lambda _now: {"progress": 0.5, "whole_row": True, "whole_block_progress": 0.35}
            visual._wave_stream_font = lambda size, *_args, **_kwargs: QFont("Arial", int(size))
            calls = []
            visual._wave_reading_renderer.paint_whole_block = lambda _p, rows, progress: calls.append((rows, progress))
            self._render(visual, False)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(calls[0][0]), 2)
            self.assertEqual(calls[0][1], 0.35)
            calls_by_style.append(calls)
        self.assertEqual(calls_by_style[0], calls_by_style[1])

    @staticmethod
    def _phase_visual(style: str, *, real_units: bool, strategy: str, position: float):
        visual = IslandSharedLyricStylesTests._visual(style)
        visual.music_surface_reading_strategy = strategy
        visual.timed_lyrics = [(0.0, "Shine softly\n春风轻拂"), (4.0, "Then glow\n随后闪耀"), (8.0, "Following")]
        visual.timed_lyric_units = [
            (TimedLyricUnit(0.0, 0.8, "Shine "), TimedLyricUnit(0.8, 0.8, "softly")) if real_units else (), (), (),
        ]
        visual.timed_lyric_timing_kinds = ["unit" if real_units else "line", "line", "line"]
        visual._stable_lyric_source_position = lambda _now: position
        visual._effective_lyric_offset_seconds = lambda: 0.0
        visual._prepare_island_lyric_offset = lambda *_args: None
        del visual._lyric_phase
        return visual

    def test_true_unit_front_and_finished_sentence_focus_match_wave(self):
        for strategy in ("gold_reading", "low_perception"):
            for position in (0.2, 0.9, 1.8, 3.0):
                with self.subTest(strategy=strategy, position=position):
                    wave = self._phase_visual("wave_stream", real_units=True, strategy=strategy, position=position)
                    gelv = self._phase_visual("gelv_songscatter", real_units=True, strategy=strategy, position=position)
                    wave_phase, gelv_phase = wave._lyric_phase(100.0), gelv._lyric_phase(100.0)
                    self.assertEqual(gelv_phase, wave_phase)
                    self.assertFalse(gelv_phase["whole_row"])
                    self.assertEqual(gelv.lyric_index, 0)
                    self.assertIn("timed_units", gelv_phase)
                    if position >= 1.8:
                        self.assertEqual(gelv_phase["visual_focus_index"], 1)

    def test_line_only_low_perception_phase_matches_shared_wave_block(self):
        for position in (0.1, 0.25, 0.75):
            wave = self._phase_visual("wave_stream", real_units=False, strategy="low_perception", position=position)
            gelv = self._phase_visual("gelv_songscatter", real_units=False, strategy="low_perception", position=position)
            phase = gelv._lyric_phase(100.0)
            self.assertEqual(phase, wave._lyric_phase(100.0))
            self.assertTrue(phase["whole_row"])
            self.assertAlmostEqual(phase["whole_block_progress"], min(1, position / 0.5))

    def test_mixed_timing_song_estimates_missing_row_units_for_both_strategies(self):
        for strategy in ("gold_reading", "low_perception"):
            phases = []
            for style in ("wave_stream", "gelv_songscatter"):
                visual = self._phase_visual(style, real_units=True, strategy=strategy, position=5.0)
                visual.lyric_index = 1
                phase = visual._lyric_phase(100.0)
                self.assertFalse(phase["whole_row"])
                self.assertGreater(phase["progress"], 0)
                self.assertLess(phase["progress"], 1)
                self.assertNotIn("whole_block_progress", phase)
                self.assertNotIn("timed_units", phase)
                phases.append(phase)
            self.assertEqual(phases[0], phases[1])

    def test_skipped_rows_have_identical_spring_pose_and_seek_reversal(self):
        sequences = []
        for style in ("wave_stream", "gelv_songscatter"):
            visual = self._visual(style)
            visual.lyrics = [f"Sentence {index}" for index in range(80)]
            visual.enable_dual_line_lyrics = False
            visual.media_is_playing = False
            visual._music_lyric_tick_plan = lambda *_args: SimpleNamespace(
                sync_timed_lyrics=False,
                fallback_advance=SimpleNamespace(should_advance=False),
            )
            rows = []
            visual._paint_expanded_lyric_row = lambda _p, rect, text, _s, _a, _b, active, _phase: rows.append((text, rect.top(), active))
            frames = []
            for step in range(61):
                now = 100.0 + step / 60.0
                if step in (1, 30):
                    visual.prev_lyric_index = visual.lyric_index
                    visual.lyric_index = 40 if step == 1 else 2
                    visual.lyric_transition_kind = "seek"
                visual._handle_music_lyric_tick(now, 1.0)
                rows.clear()
                self._render(visual, True, now)
                self.assertTrue(rows)
                first, last = visual._wave_line_spring.visible_range
                self.assertLessEqual(last - first, 16)
                frames.append(tuple(rows))
            sequences.append(tuple(frames))
        self.assertEqual(sequences[0], sequences[1])

    def test_removed_ember_style_normalizes_without_old_effect_or_cover_settings(self):
        visual = self._visual("gelv_songscatter")
        visual._clear_lyric_metrics_cache = lambda: None
        visual._pulse_stack = lambda: None
        visual.update = lambda: None
        visual.ember_show_cover = False
        visual.compact_lyric_show_cover = True
        visual.set_lyric_flow_style("ember_fade", notify=False)
        self.assertEqual(visual.lyric_flow_style, "wave_stream")
        self.assertNotIn("ember_fade", visual.LYRIC_FLOW_STYLES)
        self.assertTrue(visual._compact_lyric_cover_enabled())
        self.assertFalse(visual._active_lyric_color_fill_enabled())


if __name__ == "__main__":
    unittest.main()
