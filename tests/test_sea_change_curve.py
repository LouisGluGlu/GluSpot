import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from local.sea_lyric_background import (CHANGE_LEAD_S, COVER_S, LANDING_S, SeaPlan, change_curve,
                                        coverage_at, is_moving, landing_level, push_schedule)


class SeaChangeCurveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_slow_build_then_fast_even_sweep_then_short_settle(self):
        self.assertAlmostEqual(change_curve(0.30), 0.12, places=3)
        self.assertAlmostEqual(change_curve(0.95), 0.97, places=3)
        self.assertEqual(change_curve(1.0), 1.0)
        early = change_curve(0.15) - change_curve(0.0)
        middle = change_curve(0.65) - change_curve(0.50)
        self.assertGreater(middle, 3 * early)          # the sweep is far faster than the build
        self.assertAlmostEqual(change_curve(0.6) - change_curve(0.5), change_curve(0.8) - change_curve(0.7), places=6)

    def test_pushes_trace_the_curve_and_finish_at_the_climax(self):
        end = 120.0
        pushes = push_schedule(end - COVER_S, end)
        begin = end - COVER_S - CHANGE_LEAD_S
        for fraction in (0.3, 0.6, 0.9):
            self.assertAlmostEqual(coverage_at(begin + fraction * (end - begin), pushes),
                                   change_curve(fraction), delta=0.03)
        self.assertAlmostEqual(coverage_at(end + 0.2, pushes), 1.0, places=3)

    def test_landing_bloom_peaks_right_after_the_change_and_keeps_the_field_moving(self):
        plan = SeaPlan(QColor("navy"), "white", QColor("orange"), "black", change_end=50.0)
        self.assertEqual(landing_level(plan, 49.9), 0.0)
        self.assertGreater(landing_level(plan, 50.06), 0.8)
        self.assertLess(landing_level(plan, 50.6), 0.1)
        self.assertEqual(landing_level(plan, 50.0 + LANDING_S), 0.0)
        self.assertTrue(is_moving(plan, 50.3, True))
        plan.solid = True
        self.assertEqual(landing_level(plan, 50.08), 0.0)


class TintedUnreadInkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_unread_words_take_the_field_hue(self):
        from local.music_surface_window import MusicSurfaceWindow
        colors = MusicSurfaceWindow._sea_ink_colors
        read, unread = colors(None, "white", QColor(20, 60, 160))
        self.assertEqual(read, QColor(255, 255, 255))
        self.assertAlmostEqual(unread.hsvHueF(), QColor(20, 60, 160).hsvHueF(), delta=0.02)
        self.assertGreater(unread.valueF(), 0.9)
        read, unread = colors(None, "black", QColor(250, 200, 120))
        self.assertEqual(read, QColor(0, 0, 0))
        self.assertAlmostEqual(unread.hsvHueF(), QColor(250, 200, 120).hsvHueF(), delta=0.02)
        self.assertLess(unread.valueF(), 0.35)
        # A grey field keeps the neutral ink.
        self.assertEqual(colors(None, "white", QColor(128, 128, 128))[1], QColor(255, 255, 255, 140))


if __name__ == "__main__":
    unittest.main()
