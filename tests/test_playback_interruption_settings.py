"""t260 opt-in persistence, existing settings style, and runtime wiring."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from local.ios_widgets import IosSwitch
from local.settings_store import SettingsManager
from local.settings_window import SettingsWindow
from tests.test_mac_preferences_shell import FakeIslandWidget, FakeSettingsManager


class InterruptionSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_missing_or_corrupt_values_are_off_and_set_requires_bool(self):
        for persisted in ({}, {'v2_playback_interruption_enabled': 'true'},
                          {'v2_playback_interruption_enabled': 1},
                          {'v2_playback_interruption_enabled': None}):
            with self.subTest(persisted=persisted), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'settings.json'
                path.write_text(json.dumps(persisted))
                settings = SettingsManager(settings_path=path)
                self.assertIs(settings.get('v2_playback_interruption_enabled'), False)
                calls = []
                settings.setting_changed.connect(lambda *args: calls.append(args))
                settings.set('v2_playback_interruption_enabled', True)
                for invalid in ('true', 1, None, [], {}):
                    settings.set('v2_playback_interruption_enabled', invalid)
                self.assertIs(settings.get('v2_playback_interruption_enabled'), True)
                self.assertEqual(calls, [('v2_playback_interruption_enabled', True)])
                settings.save_now()
                restored = SettingsManager(settings_path=path)
                self.assertIs(restored.get('v2_playback_interruption_enabled'), True)
                restored.save_now()

    def test_existing_switch_style_search_and_sync_do_not_save(self):
        settings, island = FakeSettingsManager(), FakeIslandWidget()
        settings.set = Mock(wraps=settings.set)
        window = SettingsWindow(island, settings)
        try:
            switch = window.playback_interruption_switch
            self.assertIsInstance(switch, IosSwitch)
            self.assertFalse(switch.isChecked())
            self.assertTrue(window._settings_pages[1]['page'].isAncestorOf(switch))
            matches = window._settings_search_matches('内外播放打断')
            self.assertTrue(matches)
            self.assertEqual(matches[0]['page_index'], 1)
            window.sync_from_widget()
            settings.set.assert_not_called()
            QTest.mouseClick(switch, Qt.MouseButton.LeftButton, pos=switch.rect().center())
            settings.set.assert_called_once_with('v2_playback_interruption_enabled', True)
            settings.values['v2_playback_interruption_enabled'] = False
            settings.set.reset_mock()
            window.sync_from_widget()
            self.assertFalse(switch.isChecked())
            settings.set.assert_not_called()
        finally:
            window.close()
            window.deleteLater()
            island.deleteLater()
            self.app.processEvents()

    def test_external_setting_signal_sync_is_blocked_and_unsupported_switch_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = SettingsManager(settings_path=Path(directory) / 'settings.json')
            island = FakeIslandWidget()
            with patch('local.settings_window.owned_playback_only', return_value=True):
                window = SettingsWindow(island, settings)
            try:
                switch = window.playback_interruption_switch
                self.assertFalse(switch.isEnabled())
                self.assertTrue(switch.toolTip())
                toggled = Mock()
                switch.toggled.connect(toggled)
                settings.set('v2_playback_interruption_enabled', True)
                self.assertTrue(switch.isChecked())
                toggled.assert_not_called()
            finally:
                window.close()
                window.deleteLater()
                island.deleteLater()
                settings.save_now()
                self.app.processEvents()

    def test_runtime_routes_toggle_and_source_intent_to_provider(self):
        from local.app import LocalIslandApp
        app = SimpleNamespace(data_provider=Mock(), window=SimpleNamespace(),
                              owned_player=Mock(), system_provider=Mock(), system_media_controls=Mock())
        LocalIslandApp._playback_mode_changed(app, 'v2_playback_interruption_enabled', True)
        app.data_provider.set_playback_interruption_enabled.assert_called_once_with(True)
        app.data_provider.playback_source_changed.assert_not_called()
        LocalIslandApp._playback_mode_changed(app, 'v2_system_capture_enabled', False)
        app.data_provider.playback_source_changed.assert_called_once_with()
