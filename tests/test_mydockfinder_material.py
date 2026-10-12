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

    def test_selected_negative_origin_screen_uses_local_grab_offsets(self):
        image = QImage(800, 600, QImage.Format.Format_ARGB32)
        image.fill(QColor(160, 164, 166))
        screen = _FakeScreen(image)
        screen.geometry = lambda: QRect(-800, -600, 800, 600)
        calls = []
        original = screen.grabWindow
        def grab(window, left, top, width, height):
            calls.append((left, top, width, height))
            return original(window, left, top, width, height)
        screen.grabWindow = grab
        with patch("local.mydockfinder.QGuiApplication.primaryScreen", side_effect=AssertionError("must use selected Finder screen")):
            sample = sample_live_finder_bar(24., include_center=True, screen=screen)
        self.assertIsNotNone(sample)
        self.assertTrue(calls)
        self.assertTrue(all(left >= 0 and top == 0 for left, top, width, height in calls))
        self.assertEqual(calls[0][:3], (0, 0, 800))

    def test_native_dpr_capture_keeps_logical_bar_height_and_material(self):
        logical = QImage(800, 600, QImage.Format.Format_ARGB32)
        logical.fill(QColor(22, 210, 104))
        for y in range(24):
            for x in range(logical.width()):
                logical.setPixelColor(x, y, QColor(180 - y, 184 - y, 186 - y))
        reference = sample_live_finder_bar(24., screen=_FakeScreen(logical))
        self.assertIsNotNone(reference)
        for dpr in (1.5, 2.):
            screen = _FakeScreen(logical)
            original = screen.grabWindow
            def grab(window, left, top, width, height):
                image = original(window, left, top, width, height).toImage()
                image = image.scaled(round(image.width() * dpr), round(image.height() * dpr))
                image.setDevicePixelRatio(dpr)
                return _FakePixmap(image)
            screen.grabWindow = grab
            with self.subTest(dpr=dpr):
                sample = sample_live_finder_bar(24., screen=screen)
                self.assertIsNotNone(sample)
                self.assertEqual(sample.bar_height, reference.bar_height)
                self.assertEqual(sample.texture_image.size(), reference.texture_image.size())
                self.assertLessEqual(abs(sample.mid_color.red() - reference.mid_color.red()), 1)


if __name__ == "__main__":
    unittest.main()
