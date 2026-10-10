from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from local.music_surface_window import MusicSurfaceWindow


class _Owner(QWidget):
    def __init__(self):
        super().__init__()
        self._music_windowed_preference = False
        self.music_intro_cleanup_enabled = False
        self.enable_dual_line_lyrics = True


class MusicJitterRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _Owner()
        self.window = MusicSurfaceWindow(self.owner)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def test_isolated_words_share_whole_line_cursor_advances(self):
        font = self.window._surface_font(48, QFont.Weight.Bold)
        for text in ("hello world", "你好 世界 你们"):
            whole, boundaries = self.window._glyph_run(text, font)
            units = self.window._isolated_glyph_units(text, font)
            self.assertEqual(len(units), len(boundaries) - 1)
            for index in range(1, len(units)):
                # The isolated images all use the same left padding. Their
                # origin deltas must match the shaped whole-line cursors.
                self.assertAlmostEqual(
                    units[index][1] - units[index - 1][1],
                    boundaries[index] - boundaries[index - 1],
                )
            self.assertFalse(whole.isNull())

    def test_finished_words_align_with_resting_line_at_both_display_scales(self):
        font = self.window._surface_font(48, QFont.Weight.Bold)
        for text in ("hello world", "你好 世界 你们"):
            layout, height = self.window._wrapped_lyric_layout(text, font, 650.0)
            _whole, boundaries = self.window._glyph_run(text, font)
            for dpr in (1.0, 2.0):
                images = []
                for active in (True, False):
                    image = QImage(round(480 * dpr), round(110 * dpr), QImage.Format.Format_ARGB32_Premultiplied)
                    image.setDevicePixelRatio(dpr)
                    image.fill(Qt.GlobalColor.transparent)
                    with QPainter(image) as painter:
                        if active:
                            self.window._paint_wrapped_lifted_lyric(
                                painter, QRectF(40.35, 15.0, 650.0, height),
                                text, font, layout, 1.0,
                            )
                        else:
                            self.window._draw_cached_wrapped_layout(
                                painter, layout, QPointF(40.35, 15.0),
                                text, font, 650.0, height, QColor("white"),
                            )
                    images.append(image)
                for left, right in zip(boundaries, boundaries[1:]):
                    start = max(0, int((40.35 + left) * dpr))
                    end = min(images[0].width(), int((40.35 + right) * dpr))
                    centers = []
                    for image in images:
                        columns = [
                            sum(image.pixelColor(x, y).alpha() for y in range(image.height()))
                            for x in range(start, end)
                        ]
                        total = sum(columns)
                        self.assertGreater(total, 0)
                        centers.append(sum((start + x) * alpha for x, alpha in enumerate(columns)) / total)
                    with self.subTest(text=text, dpr=dpr, left=left):
                        self.assertLess(abs(centers[0] - centers[1]), 0.8 * dpr)

    def test_completed_word_and_highlight_have_no_unread_tint(self):
        font = self.window._surface_font(48, QFont.Weight.Bold)
        unread = QColor(200, 50, 50)
        for mode, text in (("reading", "hello world"), ("character", "shine")):
            for progress in (0.999, 1.0):
                image = QImage(440, 110, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                with QPainter(image) as painter:
                    if mode == "reading":
                        self.window._paint_lifted_run(
                            painter, QPointF(10.35, 10.0), 90.0, text, font, progress, unread,
                        )
                    else:
                        self.window._paint_sparse_glyph_run(
                            painter, QPointF(10.35, 10.0), 90.0, text, font, progress, unread,
                        )
                colored = [
                    image.pixelColor(x, y)
                    for y in range(image.height()) for x in range(image.width())
                    if image.pixelColor(x, y).alpha() > 0
                ]
                with self.subTest(mode=mode, progress=progress):
                    self.assertTrue(colored)
                    self.assertLessEqual(max(abs(color.red() - color.green()) for color in colored), 1)

    def test_right_to_left_song_uses_whole_row_shaping(self):
        text = "لا أحبك أبدا"
        font = self.window._surface_font(48, QFont.Weight.Bold)
        self.assertTrue(self.window._has_reversed_lyric_unit_order(text))
        self.assertFalse(self.window._has_reversed_lyric_unit_order("hello world"))
        self.owner.timed_lyrics = [
            (0.0, text), (4.0, "الثانية"), (8.0, "الثالثة"), (12.0, "الرابعة"),
        ]
        self.owner.media_position_seconds = 1.0
        self.owner._estimated_media_position = lambda _now: self.owner.media_position_seconds
        self.window.resize(640, 360)
        frame = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch.object(self.window, "_paint_wrapped_lifted_lyric") as lifted, patch.object(
            self.window, "_draw_sprung_cached_wrapped_layout",
            wraps=self.window._draw_sprung_cached_wrapped_layout,
        ) as whole_row:
            self.window.render(frame)
        self.assertFalse(any(call.args[2] == text for call in lifted.call_args_list))
        self.assertTrue(any(call.args[3] == text for call in whole_row.call_args_list))


if __name__ == "__main__":
    unittest.main()
