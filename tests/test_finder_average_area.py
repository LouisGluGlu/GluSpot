from __future__ import annotations

import math
import random
import unittest

from PySide6.QtGui import QColor, QGuiApplication, QImage

from local import mydockfinder


def _reference_average_area(image: QImage, y0: int, y1: int) -> QColor | None:
    """The original per-pixel implementation, kept as the oracle for the numpy version."""
    def distance(a, b):
        return math.sqrt((a.red() - b.red()) ** 2 * 0.24 + (a.green() - b.green()) ** 2 * 0.54
                         + (a.blue() - b.blue()) ** 2 * 0.22)

    pixels = []
    for y in range(y0, y1, 2):
        for x in range(0, image.width(), 5):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() > 0:
                pixels.append(pixel)
    if not pixels:
        return None
    middle = len(pixels) // 2
    center = QColor(sorted(p.red() for p in pixels)[middle], sorted(p.green() for p in pixels)[middle],
                    sorted(p.blue() for p in pixels)[middle])
    distances = sorted(distance(p, center) for p in pixels)
    radius = max(18.0, min(48.0, distances[min(len(distances) - 1, int(len(distances) * 0.75))] * 1.5))
    background = [p for p in pixels if distance(p, center) <= radius]
    if not background:
        return center
    n = len(background)
    return QColor(sum(p.red() for p in background) // n, sum(p.green() for p in background) // n,
                  sum(p.blue() for p in background) // n)


def _rgb(color):
    return None if color is None else color.getRgb()[:3]


class FinderAverageAreaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def assert_matches_reference(self, image, label):
        for y0, y1 in ((0, image.height()), (1, max(2, image.height() // 3)), (image.height() // 2, image.height()),
                       (0, 1), (image.height() - 1, image.height())):
            with self.subTest(label=label, y0=y0, y1=y1):
                self.assertEqual(_rgb(mydockfinder._average_area(image, y0, y1)),
                                 _rgb(_reference_average_area(image, y0, y1)))

    def test_matches_per_pixel_reference_on_bar_like_images(self):
        rng = random.Random(7)
        for case in range(40):
            width, height = rng.randint(1, 430), rng.randint(1, 40)
            fmt = rng.choice((QImage.Format.Format_ARGB32, QImage.Format.Format_RGB32,
                              QImage.Format.Format_ARGB32_Premultiplied))
            image = QImage(width, height, fmt)
            base = QColor(rng.randrange(256), rng.randrange(256), rng.randrange(256))
            for y in range(height):
                for x in range(width):
                    roll = rng.random()
                    if roll < .12:   # foreground text/icons
                        color = QColor(rng.randrange(256), rng.randrange(256), rng.randrange(256))
                    elif roll < .18 and fmt != QImage.Format.Format_RGB32:
                        color = QColor(0, 0, 0, 0)
                    else:
                        jitter = rng.randint(-6, 6)
                        color = QColor(*(max(0, min(255, c + jitter)) for c in base.getRgb()[:3]))
                    image.setPixelColor(x, y, color)
            self.assert_matches_reference(image, f"case {case} {fmt.name}")

    def test_fully_transparent_area_has_no_color(self):
        image = QImage(40, 10, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        self.assertIsNone(mydockfinder._average_area(image, 0, 10))

    def test_matches_reference_on_a_real_screen_capture(self):
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.skipTest("no screen")
        image = mydockfinder._logical_finder_capture_image(screen.grabWindow(0, 0, 0, min(420, screen.geometry().width()), 30))
        if image.isNull():
            self.skipTest("screen capture unavailable")
        self.assert_matches_reference(image, "screen")


if __name__ == "__main__":
    unittest.main()
