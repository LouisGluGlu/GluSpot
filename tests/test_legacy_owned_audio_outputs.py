"""Legacy output inventory describes observation without offering false routes."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from api.owned_audio_outputs import OwnedAudioOutputController
from tests.test_owned_audio_outputs import Backend, Bridge, Discovery, Sender


class LegacyOwnedOutputTests(unittest.TestCase):
    def setUp(self):
        legacy = patch("api.owned_audio_outputs.legacy_qt_runtime", return_value=True)
        legacy.start()
        self.addCleanup(legacy.stop)
        self.bridge, self.backend, self.discovery, self.sender = Bridge(), Backend(), Discovery(), Sender()
        self.controller = OwnedAudioOutputController(self.bridge, backend=self.backend,
            discovery=self.discovery, sender=self.sender, start_worker=False)
        self.addCleanup(self.controller.close)
        self.controller._inventory()

    def test_repeated_inventory_keeps_default_visible_and_windows_routes_disabled(self):
        for _ in range(3):
            self.assertTrue(self.controller._inventory())
            snapshot = self.controller.snapshot()
            self.assertEqual(snapshot.active_id, "one")
            self.assertEqual(snapshot.state, "ready")
            self.assertEqual(snapshot.status_message, "兼容版使用 Windows 默认输出，请在系统中切换")
            for device in snapshot.devices:
                if device.transport == "windows":
                    self.assertFalse(device.available)
                    self.assertIsNone(device.volume)
                    self.assertEqual(device.route_message, snapshot.status_message)
                else:
                    self.assertTrue(device.available)
                    self.assertEqual(device.route_message, "")
        self.assertEqual(self.bridge.calls, [])
        self.assertEqual(self.backend.writes, [])

    def test_default_changes_follow_observed_windows_default_instead_of_stale_qt_id(self):
        for identity in ("two", "three", ""):
            self.backend.devices = tuple(replace(device, is_default=device.id == identity)
                for device in self.backend.devices)
            for _ in range(2):
                self.controller._inventory()
                self.assertEqual(self.controller.snapshot().active_id, identity)
        self.assertEqual(self.bridge.current_device(), "one")
        self.assertEqual(self.bridge.calls, [])
        self.assertEqual(self.backend.writes, [])

    def test_windows_selection_and_volume_operations_are_rejected(self):
        for identity in ("one", "two"):
            self.assertFalse(self.controller.select(identity))
            self.assertFalse(self.controller.toggle(identity))
            self.assertFalse(self.controller.set_volume(identity, .8))
            self.assertFalse(self.controller.toggle_mute(identity))
        # Membership cannot bypass the compatibility gate during teardown.
        self.controller._members = ("one",)
        self.assertFalse(self.controller.toggle("one"))
        self.assertFalse(self.controller.toggle_mute("one"))
        self.controller._members = ()
        self.controller._step()
        self.assertEqual(self.bridge.calls, [])
        self.assertEqual(self.backend.writes, [])
        self.assertEqual(self.sender.calls, [])

    def test_stale_available_snapshot_and_queued_operations_cannot_write_endpoints(self):
        self.controller._publish(devices=tuple(replace(device, available=True, volume=.25)
            if device.transport == "windows" else device for device in self.controller.snapshot().devices))
        self.assertFalse(self.controller.select("two"))
        self.assertFalse(self.controller.toggle("two"))
        self.assertFalse(self.controller.set_volume("one", .8))
        self.assertFalse(self.controller.toggle_mute("one"))
        self.controller._enqueue("select", "two")
        self.controller._enqueue("volume", ("one", .8, self.controller._volume_fence(), False))
        self.controller._step()
        self.assertEqual(self.bridge.calls, [])
        self.assertEqual(self.backend.writes, [])
        self.assertEqual(self.sender.calls, [])

    def test_remote_toggle_does_not_add_the_disabled_default_windows_route(self):
        self.assertTrue(self.controller.toggle("airplay:one"))
        self.controller._step()
        self.assertEqual(self.sender.calls[0][:2], ("start", ("airplay:one",)))
        self.assertEqual(self.controller._members, ("airplay:one",))
        self.assertFalse(any(call[0] == "select" for call in self.bridge.calls))
        self.assertEqual(self.backend.writes, [])

    def test_remote_select_remains_available_and_stop_restores_default_explanation(self):
        self.assertTrue(self.controller.select("airplay:one"))
        self.controller._step()
        self.assertEqual(self.sender.calls[0][:2], ("start", ("airplay:one",)))
        self.assertTrue(self.controller.stop_cast())
        self.controller._step()
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot.active_id, "one")
        self.assertEqual(snapshot.state, "ready")
        self.assertEqual(snapshot.status_message, "兼容版使用 Windows 默认输出，请在系统中切换")
        self.assertEqual(self.backend.writes, [])


if __name__ == "__main__":
    unittest.main()
