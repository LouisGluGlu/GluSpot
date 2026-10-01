import unittest
from unittest.mock import patch

from tests import test_audio_routes as route_tests


class ContinuousLatencyTests(unittest.TestCase):
    def setUp(self):
        fixture = route_tests.AudioRouteControllerTests(methodName="test_same_receiver_selection_does_not_restart_healthy_sender")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.controller, self.sender = fixture.controller, fixture.sender
        self.clock = 100.0
        self.now = patch("api.audio_route_controller.time.monotonic", side_effect=lambda: self.clock)
        self.now.start()
        self.addCleanup(self.now.stop)
        fixture._cast()
        self.estimate = {"seconds": 1.53, "source": "protocol_plus_input_queue"}
        self.sender.latency_estimate = lambda: self.estimate

    def test_buffer_changes_slew_without_reconnecting_or_clock_stairs(self):
        starts = tuple(self.sender.starts)
        for _ in range(10):
            before = self.controller.snapshot().cast_latency_seconds
            self.clock += 0.15
            self.controller._step()
            actual = self.controller.snapshot().cast_latency_seconds
            self.assertLessEqual(abs(actual - before), 0.012000001)
        self.assertAlmostEqual(actual, 1.53)
        self.assertEqual(tuple(self.sender.starts), starts)
        self.assertAlmostEqual(self.controller.snapshot().cast_latency_estimate_seconds, 1.53)

    def test_stale_invalid_or_absent_data_holds_compensation(self):
        self.clock += 0.5
        self.controller._step()
        previous = self.controller.snapshot().cast_latency_seconds
        for value in (None, {"seconds": float("nan")}, {"seconds": 99}, {"seconds": "invalid"}):
            self.estimate = value
            self.clock += 5
            self.controller._step()
            self.assertEqual(self.controller.snapshot().cast_latency_seconds, previous)

    def test_manual_delay_survives_telemetry_and_can_resume_automation(self):
        self.controller.set_latency(2.3)
        self.clock += 0.15
        self.controller._step()
        self.assertFalse(self.controller.snapshot().cast_latency_auto)
        self.assertEqual(self.controller.snapshot().cast_latency_seconds, 2.3)
        self.clock += 5
        self.controller._step()
        self.assertEqual(self.controller.snapshot().cast_latency_seconds, 2.3)
        self.controller.set_auto_latency(True)
        self.clock += 0.15
        self.controller._step()
        self.assertTrue(self.controller.snapshot().cast_latency_auto)
        self.assertGreater(self.controller.snapshot().cast_latency_seconds, 2.28)

    def test_stop_clears_estimate_and_new_cast_uses_its_own_session(self):
        self.clock += 0.5
        self.controller._step()
        self.controller.stop_cast()
        self.controller._step()
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot.cast_latency_seconds, 0)
        self.assertIsNone(snapshot.cast_latency_estimate_seconds)

    def test_rapid_toggle_and_repeated_slider_values_keep_final_request(self):
        self.controller.set_auto_latency(True)
        self.controller.set_auto_latency(False)
        self.controller.set_auto_latency(True)
        self.controller._step()
        self.assertTrue(self.controller.snapshot().cast_latency_auto)
        for seconds in (1.75, 2.0, 1.75):
            self.controller.set_latency(seconds)
        self.controller._step()
        self.assertEqual(self.controller.snapshot().cast_latency_seconds, 1.75)


if __name__ == "__main__":
    unittest.main()
