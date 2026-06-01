from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from local.mydockfinder import sample_live_finder_bar


class _FakePixmap:
    def __init__(self, image: QImage):
        self._image = QImage(image)

    def isNull(self):
        return self._image.isNull()

    def toImage(self):
        return QImage(self._image)


class _FakeScreen:
    def __init__(self, image: QImage):
        self._image = QImage(image)

    def geometry(self):
        return QRect(0, 0, self._image.width(), self._image.height())

    def grabWindow(self, _window_id, left, top, width, height):
        return _FakePixmap(self._image.copy(int(left), int(top), int(width), int(height)))


class MyDockFinderMaterialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_live_finder_texture_replicates_top_bar_material_not_wallpaper_crop(self):
        screen = QImage(800, 600, QImage.Format.Format_ARGB32)
        screen.fill(QColor(22, 210, 104, 255))
        for y in range(34):
            shade = 186 - min(42, y)
            for x in range(screen.width()):
                screen.setPixelColor(x, y, QColor(shade, shade + 3, shade + 4, 255))

        with patch("local.mydockfinder.QGuiApplication.primaryScreen", return_value=_FakeScreen(screen)):
            sample = sample_live_finder_bar(28.0, 11, include_center=True)

        self.assertIsNotNone(sample)
        self.assertIsNotNone(sample.texture_image)
        self.assertFalse(sample.texture_is_full_screen)
        self.assertLessEqual(sample.texture_image.width(), 256)
        self.assertLessEqual(sample.texture_image.height(), 64)
        center = sample.texture_image.pixelColor(sample.texture_image.width() // 2, sample.texture_image.height() // 2)
        _hue, saturation, _value, _alpha = center.getHsvF()
        self.assertLess(saturation, 0.18)
        self.assertEqual(center.alpha(), 255)


if __name__ == "__main__":
    unittest.main()
