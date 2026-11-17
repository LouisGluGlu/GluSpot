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

    def test_change_follows_the_websites_cubic_bezier(self):
        # cubic-bezier(.6, 0, .3, 1): a held breath, one fast sweep, a settle.
        self.assertEqual(change_curve(0.0), 0.0)
        self.assertEqual(change_curve(1.0), 1.0)
        self.assertLess(change_curve(0.2), 0.06)
        self.assertAlmostEqual(change_curve(0.5), 0.5, delta=0.12)
        self.assertGreater(change_curve(0.8), 0.94)
        values = [change_curve(i / 50) for i in range(51)]
        self.assertEqual(values, sorted(values))
        self.assertGreater(change_curve(0.55) - change_curve(0.45), 4 * (change_curve(0.1) - change_curve(0.0)))
        self.assertAlmostEqual(CHANGE_LEAD_S + COVER_S, 2.2)

    def test_pushes_trace_the_curve_and_finish_at_the_climax(self):
        end = 120.0
        pushes = push_schedule(end - COVER_S, end)
        # Pushes are only timing anchors now; the field follows change_curve.
        self.assertAlmostEqual(sum(share for _t, share in pushes), 1.0, places=6)
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
