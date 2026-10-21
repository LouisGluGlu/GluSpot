from __future__ import annotations

import math
import unittest

import numpy as np
from PySide6.QtGui import QColor, QImage

from local.music_surface_window import MusicSurfaceWindow


def scalar_ink_areas(image: QImage, veil: QColor, columns=32, rows=18):
    """Previous production pixelColor/QColor vote, retained as the oracle."""
    black_area = white_area = 0.0
    crossover = math.sqrt(.05 * 1.05) - .05
    for row in range(rows):
        y = min(image.height() - 1, int((row + .5) * image.height() / rows))
        for column in range(columns):
            x = min(image.width() - 1, int((column + .5) * image.width() / columns))
            color = image.pixelColor(x, y)
            color = QColor(*(round(a * (1. - veil.alphaF()) + b * veil.alphaF())
                             for a, b in zip(color.getRgb()[:3], veil.getRgb()[:3])), color.alpha())
            if MusicSurfaceWindow._relative_luminance(color) > crossover:
                black_area += color.alphaF()
            else:
                white_area += color.alphaF()
    return black_area, white_area


class SurfaceInkVoteTests(unittest.TestCase):
    def assert_same_vote(self, image, veil):
        columns, rows = min(32, image.width()), min(18, image.height())
        expected = scalar_ink_areas(image, veil, columns, rows)
        actual = MusicSurfaceWindow._scene_control_ink_areas(image, veil, columns, rows)
        for a, b in zip(expected, actual):
            self.assertAlmostEqual(a, b, places=4)
        self.assertEqual(expected[0] > expected[1], actual[0] > actual[1])

    def test_matches_scalar_for_formats_sizes_and_fractional_alpha_veils(self):
        rng = np.random.default_rng(6417)
        for width, height in ((1, 1), (7, 5), (43, 23), (101, 57)):
            data = rng.integers(0, 256, (height, width, 4), dtype=np.uint8)
            source = QImage(data.data, width, height, width * 4,
                            QImage.Format.Format_RGBA8888).copy()
            veils = [QColor(255, 255, 255, 0), QColor(0, 0, 0, 57),
                     QColor(255, 255, 255, 127), QColor(47, 91, 172, 183),
                     QColor(252, 251, 250, 255)]
            for low, high in ((0, 80), (176, 256)):
                veils.append(QColor(*(int(value) for value in rng.integers(low, high, 3)),
                                    int(rng.integers(1, 255))))
            for image_format in (QImage.Format.Format_RGB32, QImage.Format.Format_RGB888,
                                 QImage.Format.Format_RGBA8888,
                                 QImage.Format.Format_ARGB32_Premultiplied,
                                 QImage.Format.Format_RGBA8888_Premultiplied):
                image = source.convertToFormat(image_format)
                for veil in veils:
                    with self.subTest(size=(width, height), format=image_format, veil=veil.rgba()):
                        self.assert_same_vote(image, veil)

    def test_equal_area_and_transparent_scenes_preserve_white_tie_break(self):
        for alpha in (0, 1, 127, 128, 254, 255):
            image = QImage(32, 18, QImage.Format.Format_RGBA8888)
            for y in range(18):
                for x in range(32):
                    value = 0 if x < 16 else 255
                    image.setPixelColor(x, y, QColor(value, value, value, alpha))
            for image_format in (QImage.Format.Format_RGBA8888,
                                 QImage.Format.Format_ARGB32_Premultiplied):
                image = image.convertToFormat(image_format)
                with self.subTest(alpha=alpha, format=image_format):
                    self.assert_same_vote(image, QColor(0, 0, 0, 0))
                    black, white = MusicSurfaceWindow._scene_control_ink_areas(image, QColor(0, 0, 0, 0))
                    self.assertLessEqual(black, white)


if __name__ == "__main__":
    unittest.main()
