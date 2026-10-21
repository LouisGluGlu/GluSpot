from __future__ import annotations

import unittest

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.music_lyric_reading import LyricReadingRenderer


class MusicLyricReadingCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_long_bilingual_row_reuses_tints_across_unchanged_frames(self):
        # Two different font sizes are the ordinary primary/translation case.
        # A 128-entry LRU discarded its own visible working set on every paint.
        text = "漫长的夜晚每一颗星星都照亮了你我心中的远方" * 4
        fonts = [QFont("Arial"), QFont("Arial")]
        fonts[0].setPixelSize(38)
        fonts[1].setPixelSize(24)
        for dpr in (1.0, 2.0):
            with self.subTest(dpr=dpr):
                renderer = LyricReadingRenderer()
                frame = QImage(int(1600 * dpr), int(220 * dpr),
                               QImage.Format.Format_ARGB32_Premultiplied)
                frame.setDevicePixelRatio(dpr)

                def paint():
                    frame.fill(0)
                    with QPainter(frame) as painter:
                        for index, font in enumerate(fonts):
                            renderer.paint(
                                painter, QRectF(0, index * 100, 1600, 100), 12.37,
                                text if index == 0 else text[::-1], font, 0.5,
                                QColor(124, 86, 122, 220), QColor(255, 255, 255, 240),
                            )

                paint()
                expected_pixels = bytes(frame.constBits())
                expected_tints = {key: image.cacheKey() for key, image in renderer.tints.items()}
                self.assertGreater(len(expected_tints), 128)
                for _ in range(3):
                    paint()
                    self.assertEqual(expected_pixels, bytes(frame.constBits()))
                    self.assertEqual(expected_tints, {
                        key: image.cacheKey() for key, image in renderer.tints.items()
                    })
                self.assertLessEqual(renderer.tint_bytes, renderer.CACHE_MAX_BYTES)

    def test_tints_keep_byte_and_entry_bounds_and_preserve_source(self):
        renderer = LyricReadingRenderer()
        renderer.CACHE_MAX_BYTES = 16 * 1024
        renderer.TINT_CACHE_MAX_ENTRIES = 3
        sources = []
        for index in range(10):
            source = QImage(32, 32, QImage.Format.Format_ARGB32_Premultiplied)
            source.fill(QColor(255, 255, 255, 150 + index))
            before = bytes(source.constBits())
            sources.append(source)
            renderer.tinted(source, QColor(index * 20, 40, 70))
            self.assertEqual(before, bytes(source.constBits()))
            self.assertLessEqual(len(renderer.tints), renderer.TINT_CACHE_MAX_ENTRIES)
            self.assertLessEqual(renderer.tint_bytes, renderer.CACHE_MAX_BYTES)
            self.assertEqual(renderer.tint_bytes, sum(
                image.sizeInBytes() for image in renderer.tints.values()
            ))
        self.assertEqual(len(renderer.tints), 3)
        # Larger layers exhaust bytes before the entry bound is reached.
        renderer.CACHE_MAX_BYTES = 5 * 1024
        extra = QImage(32, 32, QImage.Format.Format_ARGB32_Premultiplied)
        extra.fill(QColor(255, 255, 255))
        renderer.tinted(extra, QColor(30, 50, 80))
        self.assertEqual(len(renderer.tints), 1)
        self.assertEqual(renderer.tint_bytes, extra.sizeInBytes())
        # An individual layer over the pixel budget must never be retained.
        oversized = QImage(80, 80, QImage.Format.Format_ARGB32_Premultiplied)
        oversized.fill(QColor(255, 255, 255))
        previous = dict(renderer.tints)
        renderer.tinted(oversized, QColor(80, 90, 100))
        self.assertEqual(previous.keys(), renderer.tints.keys())


if __name__ == "__main__":
    unittest.main()
