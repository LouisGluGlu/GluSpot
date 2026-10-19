from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QWidget

from local.music_surface_window import MusicSurfaceWindow


class _BracketOwner(QWidget):
    def __init__(self):
        super().__init__()
        self._music_windowed_preference = False
        self.music_intro_cleanup_enabled = False
        self.enable_dual_line_lyrics = True
        self.music_surface_reading_strategy = "gold"
        self.media_track_id = "attached-brackets"
        self.media_is_playing = True
        self.media_position_seconds = 1.0
        self.timed_lyrics = [
            (0.0, "For so long (long)\n翻译：漫长岁月里（岁月里）"),
            (20.0, "Second line"),
            (40.0, "Third line"),
            (60.0, "Fourth line"),
        ]
        self.timed_lyric_units = [(), (), (), ()]
        self.timed_lyric_timing_kinds = ["line"] * 4


class MusicBracketLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _BracketOwner()
        self.window = MusicSurfaceWindow(self.owner)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def render(self, width: int, height: int, now: float):
        self.window.resize(width, height)
        image = QImage(width, height, QImage.Format.Format_RGB32)
        with patch("local.music_surface_window.time.monotonic", return_value=now):
            self.window.render(image)
        return image

    def test_bracket_stack_has_no_extra_sentence_gap_at_window_and_fullscreen_sizes(self):
        for index, (width, height) in enumerate(((960, 540), (1280, 720), (1920, 1080))):
            with self.subTest(size=(width, height)):
                self.window._fullscreen = index == 2
                self.render(width, height, 100.0 + index)
                _secondary, secondary_height, secondary_top = self.window._song_secondary_layouts[0]
                _bracket, bracket_height, bracket_top = self.window._song_parenthetical_layouts[0]
                _translated, translated_height, translated_top = (
                    self.window._song_parenthetical_translation_layouts[0]
                )
                self.assertAlmostEqual(bracket_top, self.window._song_layouts[0][1])
                self.assertAlmostEqual(translated_top, bracket_top + bracket_height)
                self.assertGreater(secondary_top, translated_top + translated_height)
                self.assertAlmostEqual(
                    self.window._song_parenthetical_extra_heights[0],
                    bracket_height + translated_height,
                )
                sentence_gap = self.window._song_row_tops[1] - self.window._song_row_heights[0]
                self.assertGreaterEqual(sentence_gap, height * 0.035)

    def test_wrapped_original_without_translation_attaches_bracket_to_last_visual_line(self):
        self.owner.timed_lyrics[0] = (
            0.0, "But my head is holding up my heart, my heart (echo)",
        )
        self.render(960, 540, 100.0)
        layout, primary_height = self.window._song_layouts[0]
        self.assertGreater(layout.lineCount(), 1)
        self.assertIsNone(self.window._song_secondary_layouts[0])
        _bracket, _height, bracket_top = self.window._song_parenthetical_layouts[0]
        last = layout.lineAt(layout.lineCount() - 1)
        self.assertAlmostEqual(bracket_top, primary_height)
        self.assertAlmostEqual(bracket_top, last.y() + last.height())

    def test_unread_bracket_is_painted_immediately_below_its_sentence_block(self):
        self.owner.music_surface_reading_strategy = "low_perception"
        self.render(960, 540, 100.0)
        captured = {}
        original = self.window._paint_wrapped_lifted_lyric

        def record(painter, row, text, font, layout, progress, *args, **kwargs):
            offsets = kwargs.get("line_offsets") or (0.0,)
            # The primary reading row reserves the entire companion stack.
            # Measure the shaped primary's own final line instead of treating
            # the stack's row.bottom() as its sung sentence baseline.
            last = layout.lineAt(layout.lineCount() - 1)
            captured[text] = (
                row.top() + offsets[0],
                row.top() + last.y() + last.height() + offsets[-1], progress,
            )
            return original(painter, row, text, font, layout, progress, *args, **kwargs)

        static = self.window._draw_sprung_cached_wrapped_layout

        def record_static(painter, layout, origin, text, font, width, height, color, offsets=(), **kwargs):
            captured[text] = (origin.y() + (offsets[0] if offsets else 0.0),
                              origin.y() + height + (offsets[-1] if offsets else 0.0), 1.0)
            return static(painter, layout, origin, text, font, width, height, color, offsets, **kwargs)

        with patch.object(self.window, "_paint_wrapped_lifted_lyric", side_effect=record), patch.object(
            self.window, "_draw_sprung_cached_wrapped_layout", side_effect=record_static,
        ):
            self.render(960, 540, 100.6)
        self.assertEqual(captured["long"][2], 0.0)
        self.assertEqual(captured["岁月里"][2], 0.0)
        self.assertAlmostEqual(captured["long"][0], captured["For so long"][1])
        self.assertAlmostEqual(captured["岁月里"][0], captured["long"][1])
        self.assertGreater(captured["漫长岁月里"][0], captured["岁月里"][1])


if __name__ == "__main__":
    unittest.main()
