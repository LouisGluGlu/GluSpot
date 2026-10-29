"""Persisted opt-in migration must preserve the flag-off settings contract."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from local import settings_store


class CaptureSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def manager(self, payload):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'settings.json'
        path.write_text(json.dumps(payload))
        patchers = (patch.object(settings_store, 'APP_DATA_DIR', directory.name),
                    patch.object(settings_store, 'SETTINGS_FILE', str(path)))
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        manager = settings_store.SettingsManager()
        self.addCleanup(manager._save_timer.stop)
        return manager, path

    def test_attach_transport_roundtrips_as_canonical_value(self):
        manager, path = self.manager({})
        manager.set('netease.transport', ' ATTACH ')
        manager.save_now()
        self.assertEqual(json.loads(path.read_text())['netease.transport'], 'attach')
        manager.load()
        self.assertEqual(manager.get('netease.transport'), 'attach')

    def test_flag_off_does_not_migrate_capture_envelope(self):
        legacy = {'netease': {'path': 'C:/cloudmusic.exe', 'sync_enabled': False}}
        manager, _path = self.manager({'v2_music_app_profiles': legacy})
        self.assertFalse(manager.get('v2_capture_position_enabled'))
        self.assertEqual(manager.get('v2_capture_app_profiles'), {})
        self.assertEqual(manager.get('v2_music_app_profiles'), settings_store.DEFAULT_MUSIC_APP_PROFILES)

    def test_opt_in_migrates_raw_profiles_before_fixed_defaults_and_roundtrips(self):
        manager, path = self.manager({'v2_capture_position_enabled': True,
                                     'v2_music_app_profiles': {
                                         'netease': {'path': 'C:/cloudmusic.exe', 'sync_enabled': False},
                                         'qq': {'path': '', 'sync_enabled': False}}})
        self.assertEqual(manager.get('v2_capture_app_profiles')['netease']['mode'], 'off')
        self.assertEqual(manager.get('v2_capture_app_profiles')['qq']['mode'], 'auto')
        manager.save_now()
        saved = json.loads(path.read_text())
        self.assertEqual(saved['v2_capture_app_profiles'], manager.get('v2_capture_app_profiles'))
        manager.load()
        self.assertEqual(manager.get('v2_capture_app_profiles')['netease']['mode'], 'off')

    def test_enabling_during_session_preserves_retained_legacy_manual_disable(self):
        manager, _path = self.manager({'v2_music_app_profiles': {'qq': {'path': 'qqmusic.exe', 'sync_enabled': False}}})
        manager.set('v2_capture_position_enabled', True)
        self.assertEqual(manager.get('v2_capture_app_profiles')['qq']['mode'], 'off')
        manager.set('v2_capture_app_profiles', {'qq': {'mode': 'hybrid'}})
        self.assertEqual(manager.get('v2_capture_app_profiles')['qq']['mode'], 'hybrid')
        manager.set('v2_capture_position_enabled', False)
        self.assertEqual(manager.get('v2_capture_app_profiles')['qq']['mode'], 'hybrid')

    def test_malformed_flag_cannot_enable_workers(self):
        manager, _path = self.manager({'v2_capture_position_enabled': 'true'})
        self.assertIs(manager.get('v2_capture_position_enabled'), False)
        manager.set('v2_capture_position_enabled', 1)
        self.assertIs(manager.get('v2_capture_position_enabled'), False)

    def test_netease_transport_defaults_off_and_malformed_flags_fail_closed(self):
        manager, _ = self.manager({'netease.cef.enabled': 'true', 'netease.transport': 123})
        self.assertIs(manager.get('netease.cef.enabled'), False)
        self.assertEqual(manager.get('netease.transport'), 'off')
        self.assertEqual(manager.get('netease.adapter'), 'auto')
        manager.set('netease.cef.enabled', 1)
        manager.set('netease.transport', 'unknown')
        self.assertIs(manager.get('netease.cef.enabled'), False)
        self.assertEqual(manager.get('netease.transport'), 'off')

    def test_netease_flags_roundtrip_without_enabling_master(self):
        manager, path = self.manager({})
        manager.set('netease.transport', ' RELAUNCH ')
        manager.set('netease.cef.enabled', True)
        manager.set('netease.adapter', ' LEGACY-NATIVE-V1 ')
        manager.save_now()
        saved = json.loads(path.read_text())
        self.assertEqual(saved['netease.transport'], 'relaunch')
        self.assertTrue(saved['netease.cef.enabled'])
        self.assertEqual(saved['netease.adapter'], 'legacy-native-v1')
        self.assertFalse(saved['v2_capture_position_enabled'])
