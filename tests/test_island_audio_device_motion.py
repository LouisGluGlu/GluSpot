from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from local.island_audio_device_motion import (
    IslandAudioDeviceMotion,
    IslandDevicePayload,
    reliable_device_battery,
)


FIRST = IslandDevicePayload("buds", "AirPods", "airpods", 70)
SECOND = IslandDevicePayload("mini", "HomePod mini", "homepod-mini")
THIRD = IslandDevicePayload("speaker", "M6", "speaker", 90)


class IslandAudioDeviceMotionTests(unittest.TestCase):
    def setUp(self):
        self.engine = IslandAudioDeviceMotion()
        self.now = 10.0

    def _advance_to(self, target, hz=120):
        while self.now < target:
            self.now = min(target, self.now + 1.0 / hz)
            self.engine.advance(self.now)

    def test_entrance_grows_continuously_and_exposes_an_immutable_pose(self):
        self.assertTrue(self.engine.announce(FIRST, self.now))
        self.assertTrue(self.engine.active)
        self.assertEqual(self.engine.pose.reveal, 0.0)
        self.assertEqual(self.engine.pose.content_blend, 1.0)
        self._advance_to(10.15)
        self.assertGreater(self.engine.pose.reveal, 0.0)
        self.assertLess(self.engine.pose.reveal, 1.0)
        self._advance_to(10.40)
        self.assertEqual(self.engine.pose.reveal, 1.0)
        self.assertIsNone(self.engine.pose.previous)
        with self.assertRaises(FrozenInstanceError):
            self.engine.pose.reveal = 0.5

    def test_real_five_second_hold_then_smooth_restore(self):
        self.engine.announce(FIRST, self.now)
        self._advance_to(14.99)
        self.assertEqual(self.engine.pose.reveal, 1.0)
        self._advance_to(15.08)
        self.assertGreater(self.engine.pose.reveal, 0.0)
        self.assertLess(self.engine.pose.reveal, 1.0)
        self._advance_to(15.40)
        self.assertFalse(self.engine.active)
        self.assertIsNone(self.engine.pose)
        self.assertFalse(self.engine.advance(16.0))

    def test_stalled_gui_expires_hold_without_pose_jump_or_extra_hold(self):
        self.engine.announce(FIRST, self.now)
        self._advance_to(10.40)
        reveal = self.engine.pose.reveal
        self.now = 60.0
        self.engine.advance(self.now)
        self.assertLess(self.engine.pose.reveal, reveal)
        self.assertGreater(self.engine.pose.reveal, 0.90)
        self._advance_to(60.50)
        self.assertFalse(self.engine.active)

    def test_stall_during_entrance_returns_from_its_displayed_pose(self):
        self.engine.announce(FIRST, self.now)
        self._advance_to(10.1)
        reveal = self.engine.pose.reveal
        self.now = 100.0
        self.engine.advance(self.now)
        self.assertLess(self.engine.pose.reveal, reveal)
        self.assertGreater(self.engine.pose.reveal, 0.0)
        self._advance_to(100.5)
        self.assertFalse(self.engine.active)

    def test_same_device_battery_update_keeps_lifetime_and_reveal(self):
        self.engine.announce(FIRST, self.now)
        self._advance_to(14.5)
        before = self.engine.pose
        updated = replace(FIRST, battery_percent=0)
        self.assertFalse(self.engine.announce(updated, self.now))
        self.assertEqual(self.engine.pose.payload, updated)
        self.assertEqual(self.engine.pose.reveal, before.reveal)
        self._advance_to(15.4)
        self.assertFalse(self.engine.active)

    def test_replacement_crossfades_and_keeps_exact_displayed_reveal(self):
        self.engine.announce(FIRST, self.now)
        self._advance_to(10.12)
        reveal = self.engine.pose.reveal
        self.assertTrue(self.engine.announce(SECOND, self.now))
        self.assertEqual(self.engine.pose.reveal, reveal)
        self.assertEqual(self.engine.pose.payload, SECOND)
        self.assertEqual(self.engine.pose.previous, FIRST)
        self.assertEqual(self.engine.pose.content_blend, 0.0)
        self._advance_to(10.24)
        self.assertGreater(self.engine.pose.content_blend, 0.0)
        self.assertLess(self.engine.pose.content_blend, 1.0)
        self._advance_to(10.50)
        self.assertIsNone(self.engine.pose.previous)
        self.assertEqual(self.engine.pose.reveal, 1.0)
        self._advance_to(15.08)
        self.assertEqual(self.engine.pose.reveal, 1.0)
        self._advance_to(15.6)
        self.assertFalse(self.engine.active)

    def test_rapid_arrival_finishes_visible_crossfade_before_latest_content(self):
        self.engine.announce(FIRST, self.now)
        self._advance_to(10.4)
        self.engine.announce(SECOND, self.now)
        self._advance_to(10.48)
        before = self.engine.pose
        self.engine.announce(THIRD, self.now)
        self.assertEqual(self.engine.pose, before)
        updated = replace(THIRD, battery_percent=80)
        self.assertFalse(self.engine.announce(updated, self.now))
        self._advance_to(10.68)
        self.assertEqual(self.engine.pose.payload, updated)
        self.assertEqual(self.engine.pose.previous, SECOND)
        self.assertLess(self.engine.pose.content_blend, 1.0)
        self._advance_to(11.0)
        self.assertEqual(self.engine.pose.payload, updated)
        self.assertIsNone(self.engine.pose.previous)

    def test_later_arrivals_replace_pending_content_instead_of_stale_queue(self):
        self.engine.announce(FIRST, self.now)
        self._advance_to(10.4)
        self.engine.announce(SECOND, self.now)
        self._advance_to(10.46)
        self.engine.announce(THIRD, self.now)
        latest = IslandDevicePayload("pod", "HomePod", "homepod")
        self.engine.announce(latest, self.now)
        self._advance_to(10.7)
        self.assertEqual(self.engine.pose.payload, latest)
        self.assertEqual(self.engine.pose.previous, SECOND)

    def test_arrival_during_exit_reverses_from_current_reveal(self):
        self.engine.announce(FIRST, self.now)
        self._advance_to(15.12)
        reveal = self.engine.pose.reveal
        self.assertGreater(reveal, 0.0)
        self.assertLess(reveal, 1.0)
        self.engine.announce(SECOND, self.now)
        self.assertEqual(self.engine.pose.reveal, reveal)
        self._advance_to(15.50)
        self.assertEqual(self.engine.pose.reveal, 1.0)
        self._advance_to(20.6)
        self.assertFalse(self.engine.active)

    def test_clear_drops_pending_and_no_stale_pose_returns(self):
        self.engine.announce(FIRST, self.now)
        self.engine.announce(SECOND, self.now)
        self.engine.announce(THIRD, self.now)
        self.engine.clear(self.now)
        self.assertIsNone(self.engine.pose)
        self.assertFalse(self.engine.advance(11.0))
        self.engine.announce(SECOND, 11.0)
        self.assertEqual(self.engine.pose.payload, SECOND)
        self.assertIsNone(self.engine.pose.previous)
        self.assertEqual(self.engine.pose.reveal, 0.0)

    def test_duplicate_timestamps_do_not_change_pose(self):
        self.engine.announce(FIRST, self.now)
        before = self.engine.pose
        self.assertFalse(self.engine.advance(self.now))
        self.assertEqual(self.engine.pose, before)

    def test_backward_time_does_not_renew_real_deadline(self):
        self.engine.announce(FIRST, self.now)
        self._advance_to(14.9)
        before = self.engine.pose
        self.assertFalse(self.engine.advance(12.0))
        self.assertEqual(self.engine.pose, before)
        self._advance_to(15.5)
        self.assertFalse(self.engine.active)


class ReliableDeviceBatteryTests(unittest.TestCase):
    def _device(self, **overrides):
        values = dict(battery_percent=70, battery_source="windows-driver-cache", battery_observed_at=1000.0)
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_valid_percent_including_empty_and_full_battery(self):
        for percent in (0, 1, 70, 100):
            with self.subTest(percent=percent):
                self.assertEqual(reliable_device_battery(self._device(battery_percent=percent), 1010.0), percent)

    def test_observation_age_boundaries_and_default_unix_clock(self):
        device = self._device()
        self.assertEqual(reliable_device_battery(device, 1000.0), 70)
        self.assertEqual(reliable_device_battery(device, 1030.0), 70)
        self.assertIsNone(reliable_device_battery(device, 1030.001))
        self.assertIsNone(reliable_device_battery(device, 999.999))
        with patch("local.island_audio_device_motion.time.time", return_value=1010.0):
            self.assertEqual(reliable_device_battery(device), 70)

    def test_missing_wrong_source_or_invalid_percent_is_unknown(self):
        self.assertIsNone(reliable_device_battery(SimpleNamespace(), 1010.0))
        for percent in (None, True, False, -1, 101, 70.0, "70"):
            with self.subTest(percent=percent):
                self.assertIsNone(reliable_device_battery(self._device(battery_percent=percent), 1010.0))
        for source in ("", "apple-advertisement", None):
            with self.subTest(source=source):
                self.assertIsNone(reliable_device_battery(self._device(battery_source=source), 1010.0))

    def test_invalid_observation_and_current_timestamp_is_unknown(self):
        for observed in (None, True, False, 0, -1, "1000", float("nan"), float("inf"), 10 ** 1000):
            with self.subTest(observed=observed):
                self.assertIsNone(reliable_device_battery(self._device(battery_observed_at=observed), 1010.0))
        for current in (True, "1010", float("nan"), float("inf"), 10 ** 1000):
            with self.subTest(current=current):
                self.assertIsNone(reliable_device_battery(self._device(), current))


if __name__ == "__main__":
    unittest.main()
