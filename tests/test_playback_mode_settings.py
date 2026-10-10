from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from local.settings_store import SettingsManager
from local.settings_window import SettingsWindow
from tests.test_mac_preferences_shell import FakeIslandWidget, FakeSettingsManager


class PlaybackModeSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = FakeSettingsManager()
        self.owner = FakeIslandWidget()
        self.window = SettingsWindow(self.owner, self.settings)
        self.addCleanup(self.window.close)
        self.addCleanup(self.owner.close)

    def toggle(self, control):
        QTest.mouseClick(control, Qt.MouseButton.LeftButton, pos=control.rect().center())

    def test_default_modes_are_owned_enabled_system_disabled(self):
        self.assertTrue(self.window.owned_playback_switch.isChecked())
        self.assertFalse(self.window.system_capture_switch.isChecked())
        self.assertFalse(self.settings.get("v2_playback_onboarding_completed"))

    def test_switching_off_last_owned_mode_is_rejected_and_control_restored(self):
        self.toggle(self.window.owned_playback_switch)
        self.assertTrue(self.settings.get("v2_owned_playback_enabled"))
        self.assertFalse(self.settings.get("v2_system_capture_enabled"))
        self.assertTrue(self.window.owned_playback_switch.isChecked())

    def test_dual_mode_can_be_changed_to_system_only_but_last_system_mode_stays_enabled(self):
        self.toggle(self.window.system_capture_switch)
        self.toggle(self.window.owned_playback_switch)
        self.assertFalse(self.settings.get("v2_owned_playback_enabled"))
        self.assertTrue(self.settings.get("v2_system_capture_enabled"))
        self.toggle(self.window.system_capture_switch)
        self.assertTrue(self.window.system_capture_switch.isChecked())
        self.assertTrue(self.settings.get("v2_system_capture_enabled"))
        self.assertFalse(self.settings.get("v2_owned_playback_enabled"))

    def test_dual_mode_can_return_to_owned_only_without_implicitly_completing_onboarding(self):
        self.toggle(self.window.system_capture_switch)
        self.toggle(self.window.system_capture_switch)
        self.assertTrue(self.settings.get("v2_owned_playback_enabled"))
        self.assertFalse(self.settings.get("v2_system_capture_enabled"))
        self.assertFalse(self.settings.get("v2_playback_onboarding_completed"))

    def test_sync_restores_controls_from_persisted_mode_without_changing_values(self):
        self.settings.set("v2_owned_playback_enabled", False)
        self.settings.set("v2_system_capture_enabled", True)
        self.window.sync_from_widget()
        self.assertFalse(self.window.owned_playback_switch.isChecked())
        self.assertTrue(self.window.system_capture_switch.isChecked())
        self.assertFalse(self.settings.get("v2_owned_playback_enabled"))
        self.assertTrue(self.settings.get("v2_system_capture_enabled"))

    def test_login_button_uses_controller_callback_without_changing_modes(self):
        calls = []
        self.window.online_music_login_callback = lambda: calls.append("open")
        self.window.online_login_button.click()
        self.assertEqual(calls, ["open"])
        self.assertTrue(self.settings.get("v2_owned_playback_enabled"))
        self.assertFalse(self.settings.get("v2_system_capture_enabled"))


class PlaybackModePersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_corrupt_mode_types_and_both_disabled_recover_using_only_temporary_settings(self):
        for data in (
            {"v2_owned_playback_enabled": False, "v2_system_capture_enabled": False},
            {"v2_owned_playback_enabled": "false", "v2_system_capture_enabled": "true",
             "v2_playback_onboarding_completed": "false"},
        ):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "settings.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                with patch("local.settings_store.SETTINGS_FILE", str(path)), \
                        patch("local.settings_store.APP_DATA_DIR", directory):
                    manager = SettingsManager()
                    try:
                        self.assertTrue(manager.get("v2_owned_playback_enabled"))
                        self.assertFalse(manager.get("v2_system_capture_enabled"))
                        self.assertFalse(manager.get("v2_playback_onboarding_completed"))
                    finally:
                        manager.save_now()

    def test_song_mode_persists_and_invalid_values_return_to_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with patch("local.settings_store.SETTINGS_FILE", str(path)), \
                    patch("local.settings_store.APP_DATA_DIR", directory), \
                    patch("local.settings_store.legacy_app_data_dirs", return_value=()):
                manager = SettingsManager()
                self.assertEqual(manager.get("v2_owned_playback_mode"), "sequential")
                for mode in ("shuffle", "repeat_one", "sequential"):
                    manager.set("v2_owned_playback_mode", mode)
                    manager.save_now()
                    self.assertEqual(SettingsManager().get("v2_owned_playback_mode"), mode)
                for invalid in (None, [], {}, True, "repeat_all"):
                    manager.set("v2_owned_playback_mode", invalid)
                    self.assertEqual(manager.get("v2_owned_playback_mode"), "sequential")
                path.write_text(json.dumps({"v2_owned_playback_mode": "corrupted"}), encoding="utf-8")
                repaired = SettingsManager()
                self.assertEqual(repaired.get("v2_owned_playback_mode"), "sequential")
                repaired.save_now()


if __name__ == "__main__":
    unittest.main()
