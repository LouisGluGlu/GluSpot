import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from local import sea_lyric_background as sea


def _plan(**fields):
    plan = sea.SeaPlan(QColor("#1c689e"), "white", QColor("#ff2a4e"), "black")
    plan.drums = ()
    plan.syllables = ()
    plan.ready = True
    for key, value in fields.items():
        setattr(plan, key, value)
    return plan


def _redness(color: QColor) -> int:
    return color.red() - color.blue()


class SeaChangeFromBottomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_new_colour_grows_as_a_ball_from_the_bottom(self):
        end = 60.0
        plan = _plan(change_end=end, pushes=sea.push_schedule(end - sea.COVER_S, end))
        moment = next(t / 10.0 for t in range(400, 600)
                      if 0.30 <= sea.coverage_at(t / 10.0, plan.pushes) <= 0.45)
        frame = sea.render(320, 180, plan, moment, playing=False)
        bottom = frame.pixelColor(160, 176)
        top_left = frame.pixelColor(4, 4)
        top_right = frame.pixelColor(315, 4)
        self.assertGreater(_redness(bottom), 40)
        self.assertLess(_redness(top_left), 0)
        self.assertLess(_redness(top_right), 0)
        # Symmetric left and right of the centre line.
        self.assertAlmostEqual(_redness(frame.pixelColor(110, 170)), _redness(frame.pixelColor(210, 170)), delta=6)
        done = sea.render(320, 180, plan, end + 0.5, playing=False)
        self.assertGreater(_redness(done.pixelColor(4, 4)), 0)

    def test_words_change_ink_while_the_ball_crosses_the_lyric_band(self):
        low, high = sea.WORD_SWITCH_LEVELS
        self.assertLess(low, high)
        self.assertLess(sea.coverage_for_height(0.62), sea.coverage_for_height(0.28))
        self.assertTrue(0.15 < low < high < 0.7, (low, high))

    def test_light_rises_from_the_bottom_not_the_sides(self):
        plan = _plan(change_end=None, pushes=(), word_switch=None, drums=((10.0, 1.0),))
        still = sea.render(320, 180, plan, 9.0)
        lit = sea.render(320, 180, plan, 10.03)

        def gain(x, y):
            return lit.pixelColor(x, y).lightness() - still.pixelColor(x, y).lightness()

        self.assertGreater(gain(160, 176), gain(4, 90) + 20)
        self.assertGreater(gain(160, 176), gain(315, 90) + 20)
        self.assertGreater(gain(160, 176), gain(160, 4) + 20)


    def test_drums_lead_and_only_strong_long_notes_glow(self):
        plan = _plan(change_end=None, pushes=(), word_switch=None,
                     drums=((10.0, 1.0),), syllables=((10.05, 1.0, 1.0), (20.0, 1.0, 0.70), (30.0, 1.0, 0.95)))
        self.assertGreater(sea.light_level(plan, 10.02), 0.6)
        # A note right after a drum stays dark: the drum owns the light.
        self.assertEqual(sea.light_level(plan, 10.28), 0.0)
        self.assertLess(sea.light_level(plan, 10.40), 0.45)            # then only a faint glow
        self.assertEqual(sea.light_level(plan, 20.3), 0.0)          # below the raised threshold
        self.assertGreater(sea.light_level(plan, 30.3), 0.0)
        self.assertLess(sea.light_level(plan, 30.3), 0.45)            # a faint glow next to a drum
        self.assertEqual(sea.light_level(plan, 10.27), 0.0)           # a drum flash is gone in 0.26 s

    def test_the_drum_light_stays_in_the_lower_part(self):
        plan = _plan(change_end=None, pushes=(), word_switch=None, drums=((10.0, 1.0),))
        still = sea.render(320, 180, plan, 9.0)
        lit = sea.render(320, 180, plan, 10.03)
        gain = lambda x, y: lit.pixelColor(x, y).lightness() - still.pixelColor(x, y).lightness()
        self.assertGreater(gain(160, 176), 25)
        self.assertLessEqual(gain(160, 60), 2)
        self.assertLessEqual(gain(10, 120), 3)

    def test_island_changes_in_one_smooth_1_5_second_sweep(self):
        plan = _plan(change_end=60.0, pushes=sea.push_schedule(54.0, 60.0), word_switch=(50.0, 52.0))
        start, end = sea.island_change_window(plan)
        self.assertAlmostEqual(end - start, 1.5)
        self.assertAlmostEqual((start + end) / 2, 51.0)
        values = [sea.ease_in_out(i / 20) for i in range(21)]
        self.assertEqual(values, sorted(values))
        self.assertLess(values[2], 0.01)                  # slow start
        self.assertGreater(values[11] - values[9], 0.25)   # quick middle
        self.assertGreater(values[18], 0.99)               # slow end


if __name__ == "__main__":
    unittest.main()
