from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from api.models import TimedLyricUnit
from local.island_stack_visual import IslandStackVisual
from local.music_lyric_intervals import add_interlude_rows, gap_at_position
from local.music_lyric_presentation import present_timed_lyric_rows


class IslandLyricIntervalsTests(unittest.TestCase):
    @staticmethod
    def visual():
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.lyrics = ["First lyric", "Next lyric"]
        visual.timed_lyrics = [(8., "First lyric"), (15., "Next lyric")]
        visual.timed_lyric_units = [
            (TimedLyricUnit(8., 9., "First "), TimedLyricUnit(9., 10., "lyric")),
            (TimedLyricUnit(15., 16., "Next "), TimedLyricUnit(16., 17., "lyric")),
        ]
        visual.timed_lyric_timing_kinds = ["unit", "unit"]
        visual.artist = "Fixture artist"
        visual.track_title = "Fixture song"
        visual.media_track_id = "fixture-1"
        visual.lyric_index = 0
        visual.prev_lyric_index = 0
        visual.media_is_playing = True
        visual.enable_dual_line_lyrics = True
        visual.music_surface_reading_strategy = "gold"
        visual.lyric_flow_style = "wave_stream"
        visual._stable_lyric_source_position = lambda _now: 12.5
        visual._effective_lyric_offset_seconds = lambda: 0.
        visual._prepare_island_lyric_offset = lambda *_args: None
        visual._u = float
        return visual

    def test_island_intro_and_interlude_match_shared_surface_timeline(self):
        visual = self.visual()
        rows = add_interlude_rows(
            present_timed_lyric_rows(visual.timed_lyrics, singer_names=visual.artist),
            visual.timed_lyrics, visual.timed_lyric_units,
            visual.timed_lyric_timing_kinds,
        )
        for position in (0., 4., 7.999, 8., 9.9, 10., 12.5, 14.999, 15., 18.):
            with self.subTest(position=position):
                self.assertEqual(visual._island_lyric_gap(position), gap_at_position(rows, position))

    def test_gap_uses_exact_position_without_ordinary_lyric_visual_lead(self):
        visual = self.visual()
        self.assertIsNone(visual._island_lyric_gap(9.99))
        self.assertEqual(visual._island_lyric_gap(10.).progress, 0.)
        self.assertAlmostEqual(visual._island_lyric_gap(14.95).progress, .99)
        self.assertIsNone(visual._island_lyric_gap(15.))

    def test_paused_gap_does_not_advance_with_wall_time_and_seeks_recompute(self):
        visual = self.visual()
        visual.media_is_playing = False
        first = visual._island_lyric_gap(now=100.)
        self.assertEqual(first, visual._island_lyric_gap(now=999.))
        visual._stable_lyric_source_position = lambda _now: 11.
        self.assertAlmostEqual(visual._island_lyric_gap(now=1000.).progress, .2)
        visual._stable_lyric_source_position = lambda _now: 14.
        self.assertAlmostEqual(visual._island_lyric_gap(now=1001.).progress, .8)
        visual._stable_lyric_source_position = lambda _now: 16.
        self.assertIsNone(visual._island_lyric_gap(now=1002.))

    def test_background_position_and_existing_output_offset_drive_gap(self):
        visual = self.visual()
        visual._stable_lyric_source_position = lambda now: 9. + (now - 100.)
        visual._effective_lyric_offset_seconds = lambda: -.5
        self.assertIsNone(visual._island_lyric_gap(now=101.))
        self.assertAlmostEqual(visual._island_lyric_gap(now=104.).progress, .5)

    def test_phase_adds_gap_fields_without_replacing_raw_active_index_or_units(self):
        visual = self.visual()
        raw_timed = tuple(visual.timed_lyrics)
        raw_units = tuple(visual.timed_lyric_units)
        phase = visual._lyric_phase(100.)
        self.assertEqual(phase["gap_kind"], "interlude")
        self.assertEqual(phase["gap_progress"], .5)
        self.assertEqual((phase["gap_start"], phase["gap_end"]), (10., 15.))
        self.assertEqual(phase["active_index"], 0)
        self.assertEqual(visual.lyric_index, 0)
        self.assertEqual(tuple(visual.timed_lyrics), raw_timed)
        self.assertEqual(tuple(visual.timed_lyric_units), raw_units)
        visual._stable_lyric_source_position = lambda _now: 15.
        self.assertNotIn("gap_kind", visual._lyric_phase(101.))

    def test_track_switch_source_reload_and_translation_switch_rebuild_cache(self):
        visual = self.visual()
        self.assertIsNotNone(visual._island_lyric_gap(12.))
        first_key = visual._island_lyric_gap_key
        visual.enable_dual_line_lyrics = False
        self.assertIsNotNone(visual._island_lyric_gap(12.))
        self.assertNotEqual(visual._island_lyric_gap_key, first_key)
        visual.media_track_id = "fixture-2"
        visual.lyrics = ["Other lyric"]
        visual.timed_lyrics = [(0., "Other lyric")]
        visual.timed_lyric_units = [(TimedLyricUnit(0., 15., "Other "), TimedLyricUnit(15., 30., "lyric"))]
        visual.timed_lyric_timing_kinds = ["unit"]
        self.assertIsNone(visual._island_lyric_gap(12.))
        self.assertNotEqual(visual._island_lyric_gap_key, first_key)
        visual.timed_lyrics = []
        self.assertIsNone(visual._island_lyric_gap(12.))

    def test_lrc_or_unknown_end_never_creates_middle_gap(self):
        visual = self.visual()
        for kind, unit in (("line", ()), ("synthetic", ()),
                           ("unit", (TimedLyricUnit(8., 9., "First "), TimedLyricUnit(9., None, "lyric")))):
            with self.subTest(kind=kind):
                visual.timed_lyric_units = [unit, visual.timed_lyric_units[1]]
                visual.timed_lyric_timing_kinds = [kind, "unit"]
                self.assertIsNone(visual._island_lyric_gap(12.))

    def test_metadata_only_loading_and_instrumental_have_no_gap(self):
        visual = self.visual()
        for text in ("作词：A", '{"t":0,"c":[{"tx":"作曲：B"}]}', "查找歌词中", "暂无歌词", "纯音乐"):
            with self.subTest(text=text):
                visual.media_track_id = text
                visual.lyrics = [text]
                visual.timed_lyrics = [(0., text)]
                visual.timed_lyric_units = [()]
                visual.timed_lyric_timing_kinds = ["line"]
                self.assertIsNone(visual._island_lyric_gap(12.))

    def test_dot_painter_changes_only_with_actual_gap_progress(self):
        visual = self.visual()
        visual._music_lyric_base_text_color = lambda _alpha: QColor(70, 70, 70)
        visual._music_lyric_text_color = lambda _alpha: QColor(255, 255, 255)
        renders = []
        for progress in (0., .5, 1.):
            visual._island_display_phase = lambda progress=progress: {"gap_kind": "interlude", "gap_progress": progress}
            image = QImage(180, 60, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            with QPainter(image) as painter:
                self.assertTrue(visual._paint_island_lyric_gap(painter, QRectF(0., 0., 180., 60.)))
            renders.append(bytes(image.constBits()))
        self.assertEqual(len(set(renders)), 3)
        visual._island_display_phase = lambda: {"progress": .5}
        with patch.object(visual, "_music_lyric_text_color") as ink:
            with QPainter(image) as painter:
                self.assertFalse(visual._paint_island_lyric_gap(painter, QRectF(0., 0., 180., 60.)))
            ink.assert_not_called()


if __name__ == "__main__":
    unittest.main()
