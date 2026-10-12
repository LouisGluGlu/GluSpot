from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from local.mydockfinder import FinderPresence, read_finder_material, sample_live_finder_bar


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

    def _screen_with_bar(self, color, foreground=None):
        image = QImage(800, 100, QImage.Format.Format_ARGB32)
        image.fill(QColor(80, 140, 90))
        for y in range(24):
            for x in range(image.width()):
                ink = foreground if foreground is not None and 4 < y < 20 and x % 45 < 5 else color
                image.setPixelColor(x, y, ink)
        return _FakeScreen(image)

    def test_dark_and_white_materials_do_not_select_finder_letters_as_background(self):
        for background, text in ((QColor(4, 10, 10), QColor(192, 194, 194)),
                                 (QColor(250, 251, 252), QColor(54, 55, 56)),
                                 (QColor(1, 9, 10), QColor(192, 194, 194))):
            with self.subTest(background=background.getRgb()):
                sample = sample_live_finder_bar(24., screen=self._screen_with_bar(background, text))
                self.assertIsNotNone(sample)
                self.assertEqual(sample.mid_color.getRgb()[:3], background.getRgb()[:3])
                texture = sample.texture_image.pixelColor(64, sample.texture_image.height() // 2)
                self.assertEqual(texture.getRgb()[:3], background.getRgb()[:3])
                self.assertEqual(texture.alpha(), 255)

    def test_material_resamples_real_dark_light_change_without_configuration_change(self):
        screen = self._screen_with_bar(QColor(175, 177, 178))
        with patch("local.mydockfinder.detect_mydockfinder", return_value=FinderPresence(False, "fixture")):
            light = read_finder_material(force_screen_sample=True, screen=screen)
            screen._image = self._screen_with_bar(QColor(5, 8, 8))._image
            dark = read_finder_material(force_screen_sample=True, screen=screen)
        self.assertIn("screen sample", light.source)
        self.assertIn("screen sample", dark.source)
        self.assertEqual(light.mid_color.red(), 175)
        self.assertEqual(dark.mid_color.red(), 5)

    def test_global_widget_exclusion_avoids_dragged_island_and_full_bar_feedback(self):
        screen = self._screen_with_bar(QColor(152, 156, 158))
        screen.geometry = lambda: QRect(-800, -100, 800, 100)
        # Place GluSpot inside the left sample, not the old center-only gap.
        for y in range(24):
            for x in range(0, 230):
                screen._image.setPixelColor(x, y, QColor(214, 35, 40))
        calls = []
        original = screen.grabWindow
        def grab(window, left, top, width, height):
            calls.append((left, top, width, height))
            return original(window, left, top, width, height)
        screen.grabWindow = grab
        with patch("local.mydockfinder.detect_mydockfinder", return_value=FinderPresence(False, "fixture")):
            sample = read_finder_material(force_screen_sample=True, include_center=True, screen=screen,
                                          exclude_rects=(QRect(-800, -100, 230, 24),))
        self.assertIn("screen sample", sample.source)
        self.assertEqual(sample.mid_color.getRgb()[:3], (152, 156, 158))
        self.assertTrue(calls)
        self.assertTrue(all(left >= 231 for left, top, width, height in calls))
        self.assertTrue(all(top == 0 and left + width <= 800 for left, top, width, height in calls))
        self.assertEqual(sample.texture_image.pixelColor(64, 12).getRgb()[:3], (152, 156, 158))

    def test_excluded_entire_bar_does_not_sample_underlying_island(self):
        screen = self._screen_with_bar(QColor(160, 164, 166))
        with patch.object(screen, "grabWindow", wraps=screen.grabWindow) as grab:
            sample = sample_live_finder_bar(24., screen=screen, exclude_rects=(QRect(0, 0, 800, 24),))
        self.assertIsNone(sample)
        grab.assert_not_called()

    def test_other_monitor_or_lower_widget_does_not_exclude_owner_bar(self):
        screen = self._screen_with_bar(QColor(160, 164, 166))
        sample = sample_live_finder_bar(24., screen=screen,
                                       exclude_rects=(QRect(-900, 0, 100, 24), QRect(0, 90, 800, 10)))
        self.assertIsNotNone(sample)
        self.assertEqual(sample.mid_color.getRgb()[:3], (160, 164, 166))

    def test_fractional_dpr_exclusion_remains_logical_and_does_not_capture_glyphs(self):
        for dpr in (1.5, 2.):
            screen = self._screen_with_bar(QColor(6, 9, 9), QColor(190, 190, 190))
            for y in range(24):
                for x in range(270, 460):
                    screen._image.setPixelColor(x, y, QColor(218, 220, 223))
            original = screen.grabWindow
            calls = []
            def grab(window, left, top, width, height):
                calls.append((left, width))
                image = original(window, left, top, width, height).toImage()
                image = image.scaled(round(image.width() * dpr), round(image.height() * dpr))
                image.setDevicePixelRatio(dpr)
                return _FakePixmap(image)
            screen.grabWindow = grab
            with self.subTest(dpr=dpr):
                sample = sample_live_finder_bar(24., screen=screen, exclude_rects=(QRect(270, 0, 190, 24),))
                self.assertIsNotNone(sample)
                self.assertTrue(all(left + width <= 269 or left >= 461 for left, width in calls))
                self.assertLessEqual(abs(sample.mid_color.red() - 6), 1)

    def test_trusted_owner_bar_height_ignores_lower_window_edge_and_border(self):
        screen = self._screen_with_bar(QColor(6, 9, 9), QColor(190, 190, 190))
        with patch("local.mydockfinder._detect_bar_height", side_effect=AssertionError("trusted geometry")), \
                patch("local.mydockfinder._infer_edge", side_effect=AssertionError("no restored border")):
            sample = sample_live_finder_bar(56., screen=screen, trusted_bar_height=24.)
        self.assertIsNotNone(sample)
        self.assertEqual(sample.bar_height, 24.)
        self.assertFalse(sample.has_border)
        self.assertEqual(sample.border_alpha, 0)
        self.assertEqual(sample.border_width, 0.)
        self.assertEqual(sample.mid_color.getRgb()[:3], (6, 9, 9))

    def test_read_material_passes_owner_bar_height_to_sample(self):
        with patch("local.mydockfinder.detect_mydockfinder", return_value=FinderPresence(False, "fixture")), \
                patch("local.mydockfinder._detect_bar_height", side_effect=AssertionError("trusted geometry")):
            material = read_finder_material(force_screen_sample=True,
                                           screen=self._screen_with_bar(QColor(6, 9, 9)),
                                           trusted_bar_height=24.)
        self.assertEqual(material.bar_height, 24.)
        self.assertEqual(material.mid_color.getRgb()[:3], (6, 9, 9))
        self.assertFalse(material.has_border)

    def test_invalid_trusted_height_keeps_legacy_sampling_fallback(self):
        for height in (None, 0., -24., float("nan"), float("inf"), "bad", 1000.):
            with self.subTest(height=height), \
                    patch("local.mydockfinder._detect_bar_height", return_value=24) as detect:
                sample = sample_live_finder_bar(28., screen=self._screen_with_bar(QColor(160, 164, 166)),
                                               trusted_bar_height=height)
                self.assertIsNotNone(sample)
                detect.assert_called_once()

    def test_color_only_sample_uses_one_owner_height_capture_without_texture_or_color_changes(self):
        screen = self._screen_with_bar(QColor(6, 9, 9), QColor(190, 190, 190))
        reference = sample_live_finder_bar(24., screen=screen, trusted_bar_height=24.)
        calls = []
        original = screen.grabWindow
        def grab(window, left, top, width, height):
            calls.append((left, top, width, height))
            return original(window, left, top, width, height)
        screen.grabWindow = grab
        with patch("local.mydockfinder._make_material_texture", side_effect=AssertionError("color-only sample")):
            sample = sample_live_finder_bar(24., screen=screen, trusted_bar_height=24., include_texture=False)
        self.assertIsNotNone(reference)
        self.assertIsNotNone(sample)
        self.assertIsNone(sample.texture_image)
        self.assertEqual(calls, [(0, 0, 800, 24)])
        self.assertEqual(sample.bar_height, reference.bar_height)
        self.assertEqual(sample.top_color, reference.top_color)
        self.assertEqual(sample.mid_color, reference.mid_color)
        self.assertEqual(sample.bottom_color, reference.bottom_color)

    def test_read_material_passes_color_only_request_without_creating_texture(self):
        with patch("local.mydockfinder.detect_mydockfinder", return_value=FinderPresence(False, "fixture")), \
                patch("local.mydockfinder._make_material_texture", side_effect=AssertionError("color-only sample")):
            material = read_finder_material(force_screen_sample=True,
                                           screen=self._screen_with_bar(QColor(6, 9, 9)),
                                           trusted_bar_height=24., include_texture=False)
        self.assertEqual(material.bar_height, 24.)
        self.assertEqual(material.mid_color.getRgb()[:3], (6, 9, 9))
        self.assertIsNone(material.texture_image)

    def test_color_only_single_frame_excludes_own_colors_at_negative_origin_and_dpr(self):
        for dpr in (1., 1.5, 2.):
            screen = self._screen_with_bar(QColor(6, 9, 9), QColor(190, 190, 190))
            screen.geometry = lambda: QRect(-800, -100, 800, 100)
            for y in range(24):
                for x in range(0, 230):
                    screen._image.setPixelColor(x, y, QColor(218, 220, 223))
            calls = []
            original = screen.grabWindow
            def grab(window, left, top, width, height):
                calls.append((left, top, width, height))
                image = original(window, left, top, width, height).toImage()
                if dpr != 1.:
                    image = image.scaled(round(image.width() * dpr), round(image.height() * dpr))
                    image.setDevicePixelRatio(dpr)
                return _FakePixmap(image)
            screen.grabWindow = grab
            with self.subTest(dpr=dpr):
                sample = sample_live_finder_bar(24., screen=screen, trusted_bar_height=24.,
                                               include_texture=False, exclude_rects=(QRect(-800, -100, 230, 24),))
                self.assertIsNotNone(sample)
                self.assertEqual(calls, [(0, 0, 800, 24)])
                self.assertEqual(sample.bar_height, 24.)
                self.assertLessEqual(abs(sample.mid_color.red() - 6), 1)
                self.assertIsNone(sample.texture_image)

    def test_color_only_fully_excluded_owner_bar_performs_no_capture(self):
        screen = self._screen_with_bar(QColor(160, 164, 166))
        with patch.object(screen, "grabWindow", wraps=screen.grabWindow) as grab:
            sample = sample_live_finder_bar(24., screen=screen, trusted_bar_height=24.,
                                           include_texture=False, exclude_rects=(QRect(0, 0, 800, 24),))
        self.assertIsNone(sample)
        grab.assert_not_called()

    def test_color_only_widget_below_owner_bar_does_not_block_capture(self):
        screen = self._screen_with_bar(QColor(160, 164, 166))
        with patch.object(screen, "grabWindow", wraps=screen.grabWindow) as grab:
            sample = sample_live_finder_bar(24., screen=screen, trusted_bar_height=24.,
                                           include_texture=False, exclude_rects=(QRect(0, 24, 800, 40),))
        self.assertIsNotNone(sample)
        grab.assert_called_once_with(0, 0, 0, 800, 24)
        self.assertEqual(sample.mid_color.getRgb()[:3], (160, 164, 166))

    def test_color_only_without_trusted_height_keeps_safe_legacy_patch_captures(self):
        screen = self._screen_with_bar(QColor(160, 164, 166))
        calls = []
        original = screen.grabWindow
        def grab(window, left, top, width, height):
            calls.append((left, top, width, height))
            return original(window, left, top, width, height)
        screen.grabWindow = grab
        sample = sample_live_finder_bar(24., screen=screen, include_texture=False,
                                       exclude_rects=(QRect(0, 0, 230, 24),))
        self.assertIsNotNone(sample)
        self.assertGreater(len(calls), 1)
        self.assertTrue(all(left >= 231 and width < 800 for left, top, width, height in calls))


if __name__ == "__main__":
    unittest.main()
