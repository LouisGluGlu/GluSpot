from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtGui import QColor, QFont, QImage
from PySide6.QtWidgets import QApplication, QWidget

from api.models import TimedLyricUnit
from local.music_surface_window import MusicSurfaceWindow


class _Owner(QWidget):
    def __init__(self):
        super().__init__()
        self.media_track_id = "cold-obo"
        self.track_title = "Cold OBO"
        self.artist = "Artist"
        self.album_color = QColor(92, 51, 137)
        self.secondary_color = QColor(31, 35, 65)
        self.album_art = QImage(128, 128, QImage.Format.Format_RGB32)
        self.album_art.fill(self.album_color)
        self.media_is_playing = True
        self.media_position_seconds = 1.0
        self.music_surface_reading_strategy = "gold"
        self.music_intro_cleanup_enabled = False
        self.enable_dual_line_lyrics = False
        self._music_windowed_preference = False
        self.timed_lyrics = [
            (0.0, "Shine through the quiet night"),
            (6.0, "We walk an ordinary road together tonight"),
            (12.0, "Another ordinary sentence follows this one"),
            (18.0, "The final ordinary sentence has enough words"),
        ]
        self.timed_lyric_units = [
            (
                TimedLyricUnit(0.0, 2.0, "Shine "),
                TimedLyricUnit(2.0, 3.0, "through "),
                TimedLyricUnit(3.0, 4.0, "the "),
                TimedLyricUnit(4.0, 5.0, "quiet "),
                TimedLyricUnit(5.0, 6.0, "night"),
            ),
            (),
            (),
            (),
        ]
        self.timed_lyric_timing_kinds = ["unit", "line", "line", "line"]

    def _estimated_media_position(self, _now):
        return self.media_position_seconds

    def _run_background(self, _name, callback):
        callback()

    def _restore_island_from_music_surface(self):
        pass


class MusicLyricColdPrewarmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _Owner()
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.output = QImage(self.window.size(), QImage.Format.Format_RGB32)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def test_cold_real_obo_uses_qtextline_without_lyric_image_rasters(self):
        with (
            patch.object(self.window, "_glyph_run", side_effect=AssertionError("cold glyph raster")),
            patch.object(self.window, "_advance_isolated_glyph_units", side_effect=AssertionError("cold isolated raster")),
            patch.object(self.window, "_paint_cold_emphasis_reading", wraps=self.window._paint_cold_emphasis_reading) as cold,
        ):
            self.window.render(self.output)
        self.assertEqual(self.window._last_lyric_emphasis_mode, "scatter_glow")
        self.assertTrue(cold.called)
        self.assertIsNotNone(cold.call_args.kwargs["real_progresses"])
        self.assertIsNotNone(self.window._pending_glow_prewarm)

    def test_cold_visual_width_matches_isolated_raster_with_wrap_spaces(self):
        font = self.window._surface_font(38, QFont.Weight.Bold)
        for text in (
            "Shine through the quiet night",
            "A much longer line that wraps into several separate visual phrases",
            "Hello 世界 👩‍💻",
        ):
            layout, _height = self.window._wrapped_lyric_layout(text, font, 270.0)
            units = (TimedLyricUnit(0.0, 10.0, text),)
            for step in range(1, 100):
                self.window._real_visual_line_progresses(text, layout, units, step / 10.0)
            for index in range(layout.lineCount()):
                line = layout.lineAt(index)
                segment = self.window._utf16_slice(text, line.textStart(), line.textLength())
                key = (segment, font.toString())
                self.assertIn(key, self.window._reading_line_width_cache)
                image, _boundaries = self.window._glyph_run(segment, font, "reading")
                self.assertEqual(self.window._reading_line_width_cache[key], image.width())

    def test_next_ordinary_row_prewarms_one_reading_unit_per_tick(self):
        self.window.render(self.output)
        self.assertIsNotNone(self.window._pending_reading_prewarm)
        for _ in range(128):
            if self.window._pending_glow_prewarm is None:
                break
            self.window._on_animation_tick()
        self.assertIsNone(self.window._pending_glow_prewarm)
        self.assertIsNotNone(self.window._pending_reading_prewarm)

        with patch.object(
            self.window, "_advance_isolated_glyph_units",
            wraps=self.window._advance_isolated_glyph_units,
        ) as advance:
            self.window._on_animation_tick()
        reading_calls = [call for call in advance.call_args_list if call.args[2] == "reading"]
        self.assertEqual(len(reading_calls), 1)
        self.assertEqual(reading_calls[0].kwargs["max_units"], 1)

        for _ in range(128):
            if self.window._pending_reading_prewarm is None:
                break
            self.window._on_animation_tick()
        self.assertIsNone(self.window._pending_reading_prewarm)
        text = self.owner.timed_lyrics[1][1]
        layout = self.window._song_layouts[1][0]
        color = self.window._future_lyric_color(self.window._lyric_clip_rect)
        font = layout.font()
        self.assertTrue(self.window._lyric_reading_assets_ready(text, font, layout, color))

        self.owner.media_position_seconds = 6.5
        self.owner.media_position_source = "local-seek"
        self.owner.media_position_sampled_at = 123.0
        advance = self.window._advance_isolated_glyph_units

        def cached_only(segment, segment_font, unit_mode="reading", **kwargs):
            self.assertIn(
                (segment, segment_font.toString(), unit_mode),
                self.window._isolated_glyph_cache,
                "ordinary boundary shaped an unprepared word run",
            )
            return advance(segment, segment_font, unit_mode, **kwargs)

        with patch.object(
            self.window, "_advance_isolated_glyph_units",
            side_effect=cached_only,
        ):
            self.window.render(self.output)
        self.assertEqual(self.window._last_lyric_emphasis_mode, "none")

    def test_song_change_discards_pending_reading_build(self):
        self.window.render(self.output)
        self.assertIsNotNone(self.window._pending_reading_prewarm)
        self.owner.media_track_id = "replacement"
        self.owner.timed_lyrics = [
            (0.0, "A new song"), (4.0, "Another row"),
            (8.0, "The third row"), (12.0, "The fourth row"),
        ]
        self.owner.timed_lyric_units = [(), (), (), ()]
        self.owner.timed_lyric_timing_kinds = ["line"] * 4
        self.window.render(self.output)
        with patch.object(
            self.window, "_advance_isolated_glyph_units",
            wraps=self.window._advance_isolated_glyph_units,
        ) as advance:
            self.window._on_animation_tick()
        self.assertFalse(any(call.args[0] == "We walk an ordinary road together tonight" for call in advance.call_args_list))


if __name__ == "__main__":
    unittest.main()
