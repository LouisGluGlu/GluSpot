from __future__ import annotations

import unittest

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage

from api.models import ColorToken
from api.providers import WindowsMediaProvider
from local.music_lyric_motion import ambient_color


def _png(color: QColor, *, black_and_white: bool = False, small_accent: bool = False) -> bytes:
    image = QImage(20, 20, QImage.Format.Format_RGB32)
    image.fill(color)
    if black_and_white:
        for y in range(10):
            for x in range(20):
                image.setPixelColor(x, y, QColor(255, 255, 255))
    if small_accent:
        for y in range(4):
            for x in range(4):
                image.setPixelColor(x, y, QColor(20, 55, 230))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise AssertionError("test image could not be encoded")
    return bytes(data)


class AlbumPaletteTests(unittest.TestCase):
    def setUp(self):
        self.provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        self.fallback = ColorToken(58, 68, 88)
        self.secondary = ColorToken(34, 44, 62)

    def colors(self, art: bytes) -> tuple[ColorToken, ColorToken]:
        return self.provider._colors_from_art(art, self.fallback, self.secondary)

    def assert_neutral_dark_pair(self, art: bytes):
        primary, secondary = self.colors(art)
        self.assertEqual(primary.red, primary.green)
        self.assertEqual(primary.green, primary.blue)
        self.assertEqual(secondary.red, secondary.green)
        self.assertEqual(secondary.green, secondary.blue)
        self.assertGreaterEqual(primary.red, 50)
        self.assertLessEqual(primary.red, 100)
        self.assertLess(secondary.red, primary.red)

    def test_black_white_gray_and_mixed_monochrome_covers_use_neutral_dark_gray(self):
        for art in (
            _png(QColor(0, 0, 0)),
            _png(QColor(255, 255, 255)),
            _png(QColor(128, 128, 128)),
            _png(QColor(0, 0, 0), black_and_white=True),
            _png(QColor(255, 255, 255), small_accent=True),
        ):
            with self.subTest(art=art[:20]):
                self.assert_neutral_dark_pair(art)

    def test_colorful_cover_keeps_an_album_hue(self):
        primary, secondary = self.colors(_png(QColor(215, 35, 52)))
        self.assertGreater(primary.red, primary.green + 35)
        self.assertGreater(primary.red, primary.blue + 25)
        self.assertNotEqual(primary, self.fallback)
        self.assertNotEqual(secondary, self.secondary)

    def test_missing_or_invalid_art_preserves_original_fallback(self):
        self.assertEqual(self.colors(b""), (self.fallback, self.secondary))
        self.assertEqual(self.colors(b"not an image"), (self.fallback, self.secondary))

    def test_achromatic_glow_token_never_invents_a_blue_hue(self):
        token = self.provider._correct_glow_token(0.58, 0.0, 0.5)
        self.assertEqual((token.red, token.green, token.blue), (73, 73, 73))

    def test_ambient_white_and_black_use_neutral_gray(self):
        white = ambient_color(QColor(255, 255, 255))
        black = ambient_color(QColor(0, 0, 0))
        middle = ambient_color(QColor(73, 73, 73))
        self.assertEqual(white.getRgb()[:3], (90, 90, 90))
        self.assertEqual(black.getRgb()[:3], (56, 56, 56))
        self.assertEqual(middle.getRgb()[:3], (73, 73, 73))


if __name__ == "__main__":
    unittest.main()
