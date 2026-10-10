"""Volume restoration uses bounded readback history and expiring intents."""
from dataclasses import replace
import unittest

from api.audio_output_volume import AudioOutputVolumeHistory
from api.audio_routes import AudioRouteDevice


class VolumeHistoryTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.history = AudioOutputVolumeHistory(max_devices=2, intent_seconds=2,
                                              clock=lambda: self.now)
        self.device = AudioRouteDevice("one", "One", volume=.62)

    def test_actual_positive_history_restores_after_zero_readback(self):
        self.history.observe(self.device, 1)
        muted = replace(self.device, volume=0, volume_muted=True)
        self.assertEqual(self.history.toggle(muted, 1), .62)

    def test_hardware_mute_preserves_positive_scalar_and_unknown_defaults_to_thirty(self):
        self.assertEqual(self.history.toggle(replace(self.device, volume_muted=True), 1), .62)
        unknown = replace(self.device, id="unknown", volume=None)
        self.assertEqual(self.history.toggle(unknown, 1), 0)
        self.assertEqual(self.history.toggle(unknown, 1), .3)
        self.assertEqual(self.history.toggle(unknown, 1), 0)

    def test_rapid_intents_alternate_before_any_readback(self):
        self.assertEqual([self.history.toggle(self.device, 1) for _ in range(4)], [0, .62, 0, .62])

    def test_request_never_becomes_positive_history(self):
        self.history.request("one", .9, 1)
        self.history.discard("one", 1)
        self.assertEqual(self.history.toggle(replace(self.device, volume=0), 1), .3)

    def test_expired_and_old_session_intents_do_not_override_actual_volume(self):
        self.assertEqual(self.history.toggle(self.device, 1), 0)
        self.now += 2
        self.assertEqual(self.history.toggle(self.device, 1), 0)
        self.assertEqual(self.history.toggle(self.device, 2), 0)

    def test_reset_retires_intents_and_preserves_confirmed_history(self):
        self.history.toggle(self.device, 1)
        self.history.reset_intents()
        self.assertEqual(self.history.toggle(replace(self.device, volume=0), 2), .62)

    def test_older_failure_cannot_discard_newer_different_intent(self):
        self.history.toggle(self.device, 1)
        self.history.request("one", .4, 1)
        self.history.discard("one", 1, value=0)
        self.assertEqual(self.history.toggle(replace(self.device, volume=0), 1), 0)

    def test_history_and_intents_have_bounded_device_capacity(self):
        for index in range(3):
            self.history.observe(replace(self.device, id=str(index)), 1)
            self.history.request(str(index), 0, 1)
        self.assertEqual(len(self.history._history), 2)
        self.assertEqual(len(self.history._intents), 2)
        self.assertEqual(self.history.toggle(replace(self.device, id="0", volume=0), 1), .3)

    def test_invalid_requested_scalars_are_rejected(self):
        for scalar in (None, True, "0.4", -1, 1.1, float("nan"), float("inf"), 10 ** 400):
            with self.subTest(scalar=scalar), self.assertRaises(ValueError):
                self.history.request("one", scalar, 1)


if __name__ == "__main__":
    unittest.main()
