"""Physical spring, shared ticker, and edge flick."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWidgets import QApplication

from local.spring_motion import (
    EDGE_SPRING, FRAME_LOG, SCROLL_SPRING, WIDE_NARROW_SPRING, FlickScroll, Spring,
    SpringConfig, SpringProgressAnimation, SpringTicker, background_follow, rubber_band,
)


class SpringSolverTests(unittest.TestCase):
    def test_critical_release_from_rest_does_not_cross_the_target(self):
        spring = Spring(WIDE_NARROW_SPRING, 0.0, 0.0, 1.0)
        peak = 0.0
        for _ in range(80):
            spring.step(0.016)
            peak = max(peak, spring.value)
        self.assertLessEqual(peak, 1.0 + 1e-6)
        self.assertTrue(spring.settled)
        self.assertEqual(spring.value, 1.0)
        self.assertEqual(spring.velocity, 0.0)
        self.assertEqual(WIDE_NARROW_SPRING.damping_ratio, 1.0)

    def test_dismiss_damping_overshoots_once(self):
        config = SpringConfig(mass=1.0, stiffness=220.0, damping_ratio=0.8)
        spring = Spring(config, 0.0, 0.0, 1.0)
        peak = 0.0
        for _ in range(120):
            spring.step(0.016)
            peak = max(peak, spring.value)
        self.assertGreater(peak, 1.005)
        self.assertTrue(spring.settled)
        self.assertEqual(spring.value, 1.0)
        self.assertAlmostEqual(config.damping, 2.0 * 0.8 * (220.0 ** 0.5), places=6)

    def test_release_velocity_changes_the_path(self):
        still = Spring(WIDE_NARROW_SPRING, 0.0, 0.0, 1.0)
        flicked = Spring(WIDE_NARROW_SPRING, 0.0, 8.0, 1.0)
        still.step(0.05)
        flicked.step(0.05)
        self.assertGreater(flicked.value, still.value + 0.05)

    def test_retarget_keeps_position_and_velocity(self):
        spring = Spring(WIDE_NARROW_SPRING, 0.2, 3.5, 1.0)
        spring.retarget(0.0)
        self.assertEqual(spring.value, 0.2)
        self.assertEqual(spring.velocity, 3.5)
        self.assertEqual(spring.target, 0.0)
        spring.retarget(-1.0, velocity=-2.0)
        self.assertEqual(spring.value, 0.2)
        self.assertEqual(spring.velocity, -2.0)

    def test_one_analytical_step_matches_many_small_steps(self):
        whole = Spring(WIDE_NARROW_SPRING, 0.0, 1.5, 1.0)
        pieces = Spring(WIDE_NARROW_SPRING, 0.0, 1.5, 1.0)
        whole.step(0.2)
        for _ in range(200):
            pieces.step(0.001)
        self.assertAlmostEqual(whole.value, pieces.value, places=3)
        self.assertAlmostEqual(whole.velocity, pieces.velocity, places=3)

    def test_overdamped_stays_finite_and_approaches(self):
        config = SpringConfig(mass=1.2, stiffness=180.0, damping_ratio=1.4)
        spring = Spring(config, 0.0, 0.0, 1.0)
        previous = -1.0
        for _ in range(40):
            spring.step(0.02)
            self.assertGreaterEqual(spring.value, previous - 1e-6)
            previous = spring.value
        self.assertLess(spring.value, 1.0 + 1e-6)
        self.assertGreater(spring.value, 0.5)

    def test_rubber_band_is_odd_bounded_and_grows(self):
        self.assertEqual(rubber_band(0.0, 200.0), 0.0)
        self.assertLess(rubber_band(-40.0, 200.0), 0.0)
        self.assertAlmostEqual(rubber_band(-40.0, 200.0), -rubber_band(40.0, 200.0))
        self.assertLess(rubber_band(40.0, 200.0), rubber_band(400.0, 200.0))
        self.assertLess(rubber_band(10000.0, 200.0), 200.0)

    def test_background_follow_uses_the_whole_handoff(self):
        self.assertEqual(background_follow(0.0, 0.34), 0.0)
        early = background_follow(0.2, 0.34)
        middle = background_follow(0.5, 0.34)
        done = background_follow(1.0, 0.34)
        self.assertGreater(early, 0.0)
        self.assertGreater(middle, early)
        self.assertGreater(done, 0.9)
        self.assertLessEqual(done, 1.0)


class FlickScrollTests(unittest.TestCase):
    def test_a_jump_onto_the_edge_does_not_stretch(self):
        flick = FlickScroll()
        committed, overscroll = flick.absorb(0.0, 5000.0, 0.0, 400.0, 240.0)
        self.assertEqual(committed, 400.0)
        self.assertEqual(overscroll, 0.0)

    def test_pushing_again_at_the_edge_rubber_bands(self):
        flick = FlickScroll()
        committed, overscroll = flick.absorb(400.0, 480.0, 0.0, 400.0, 240.0)
        self.assertEqual(committed, 400.0)
        self.assertGreater(overscroll, 0.0)
        self.assertLess(overscroll, 240.0)
        flick.cancel()

    def test_inward_flick_from_the_edge_coasts_inside(self):
        flick = FlickScroll()
        flick.velocity = 400.0
        self.assertTrue(flick.arm_coast(0.0, 0.0, 800.0, span=300.0))
        self.assertEqual(flick._mode, "coast")
        moved = False
        for _ in range(8):
            result = flick.step(0.016)
            self.assertIsNotNone(result)
            if result[0] == "position" and result[1] > 0.0:
                moved = True
                break
        self.assertTrue(moved)
        flick.cancel()

    def test_outward_flick_at_the_edge_returns_to_zero(self):
        flick = FlickScroll()
        flick.velocity = -500.0
        self.assertTrue(flick.arm_coast(0.0, 0.0, 800.0, span=300.0))
        self.assertEqual(flick._mode, "edge")
        self.assertNotEqual(flick.overscroll, 0.0)
        for _ in range(90):
            flick.step(0.016)
            if flick._spring is None:
                break
        self.assertEqual(flick.overscroll, 0.0)
        self.assertEqual(EDGE_SPRING.damping_ratio, 0.8)
        self.assertEqual(SCROLL_SPRING.damping_ratio, 1.0)


class SharedTickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        SpringTicker.reset_for_tests()

    def test_one_ticker_stops_when_the_last_spring_settles(self):
        first = SpringProgressAnimation(config=WIDE_NARROW_SPRING)
        second = SpringProgressAnimation(config=WIDE_NARROW_SPRING)
        finished = []
        first.finished.connect(lambda: finished.append("first"))
        first.start()
        second.start()
        ticker = SpringTicker.instance()
        self.assertEqual(ticker.subscriber_count, 2)
        self.assertTrue(ticker.active)
        self.assertIs(SpringTicker.instance(), ticker)
        for _ in range(80):
            if not ticker.active or ticker._last is None:
                break
            ticker._last -= 0.016
            ticker._tick()
        self.assertEqual(finished, ["first"])
        self.assertEqual(first.state(), QAbstractAnimation.State.Stopped)
        self.assertEqual(second.state(), QAbstractAnimation.State.Stopped)
        self.assertFalse(ticker.active)
        self.assertEqual(ticker.subscriber_count, 0)

    def test_scrub_to_the_end_finishes_once_and_stop_does_not(self):
        animation = SpringProgressAnimation()
        animation.setDuration(400)
        finished = []
        animation.finished.connect(lambda: finished.append(1))
        animation.start()
        animation.setCurrentTime(150)
        self.assertGreater(animation.spring.value, 0.0)
        self.assertLess(animation.spring.value, 1.0)
        self.assertEqual(animation.currentTime(), 150)
        animation.setCurrentTime(400)
        animation.setCurrentTime(400)
        self.assertEqual(finished, [1])
        self.assertEqual(animation.state(), QAbstractAnimation.State.Stopped)
        animation.stop()
        self.assertEqual(finished, [1])

    def test_retarget_continues_from_the_current_velocity(self):
        animation = SpringProgressAnimation()
        animation.release_velocity = 4.0
        animation.start()
        SpringTicker.instance()._tick()
        pose = animation.spring.value
        speed = animation.spring.velocity
        animation.retarget(0.2)
        self.assertEqual(animation.spring.value, pose)
        self.assertEqual(animation.spring.velocity, speed)
        self.assertEqual(animation.spring.target, 0.2)
        animation.stop()

    def test_dropped_frame_is_recorded(self):
        animation = SpringProgressAnimation()
        animation.start()
        ticker = SpringTicker.instance()
        FRAME_LOG.frames.clear()
        ticker._last -= 1.0
        ticker._tick()
        self.assertTrue(any(frame["dropped"] for frame in FRAME_LOG.frames))
        animation.stop()


if __name__ == "__main__":
    unittest.main()
