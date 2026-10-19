from __future__ import annotations

import unittest

from PySide6.QtCore import QRect, QRectF, QSize
from PySide6.QtWidgets import QApplication

from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_surface_transition import _TransitionOwner


class MusicWindowDefaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.window = MusicSurfaceWindow(self.owner)
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def geometry_on(self, available):
        self.window._available_geometry = lambda: QRect(available)
        result = self.window._default_window_geometry()
        self.assertTrue(available.contains(result), (available, result))
        offset = QRectF(result).center() - QRectF(available).center()
        self.assertLessEqual(abs(offset.x()), .5)
        self.assertLessEqual(abs(offset.y()), .5)
        return result

    def test_first_window_opens_at_960_by_540_on_ordinary_and_large_work_areas(self):
        for available in (QRect(0, 0, 1920, 1040), QRect(-3840, 40, 3840, 2120)):
            with self.subTest(available=available):
                default = self.geometry_on(available)
                self.assertEqual(default.size(), QSize(960, 540))
                self.window._normal_geometry = QRect()
                self.window.show_from_island(fullscreen=False)
                self.assertEqual(self.window.geometry(), default)
                self.window.hide()

    def test_smaller_landscape_work_area_preserves_16_by_9_and_minimum_height(self):
        default = self.geometry_on(QRect(100, 50, 800, 600))
        self.assertEqual(default.size(), QSize(752, 423))
        self.assertGreaterEqual(default.height(), 400)
        self.assertAlmostEqual(default.width() / default.height(), 16 / 9, places=2)

    def test_narrow_work_area_uses_4_by_3_before_relaxing_default_proportion(self):
        default = self.geometry_on(QRect(-600, 20, 600, 900))
        self.assertEqual(default.size(), QSize(564, 423))
        self.assertGreaterEqual(default.height(), 400)
        self.assertAlmostEqual(default.width() / default.height(), 4 / 3, places=2)

    def test_tiny_work_area_stays_visible_when_both_ratios_cannot_fit(self):
        default = self.geometry_on(QRect(50, 70, 300, 300))
        self.assertEqual(default.size(), QSize(300, 300))

    def test_manual_window_geometry_is_retained_when_reopening(self):
        self.window._available_geometry = lambda: QRect(0, 0, 1920, 1040)
        manual = QRect(250, 80, 510, 760)
        self.window._normal_geometry = QRect(manual)
        self.window.show_from_island(fullscreen=False)
        self.assertEqual(self.window.geometry(), manual)
        self.window.return_to_island()
        self.window.show_from_island(fullscreen=False)
        self.assertEqual(self.window.geometry(), manual)


if __name__ == "__main__":
    unittest.main()
