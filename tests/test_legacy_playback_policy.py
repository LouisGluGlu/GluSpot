from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from local import runtime_platform as platform


class LegacyPlaybackPolicyTests(unittest.TestCase):
    def test_windows7_and_pre_smtc_windows_are_owned_only(self):
        for version in ((6, 1, 7601), (6, 3, 9600), (10, 0, 14393)):
            with self.subTest(version=version), patch.object(platform, "windows_version", return_value=version):
                self.assertTrue(platform.owned_playback_only())
                self.assertEqual(platform.normalize_playback_selection(False, True), (True, False))

    def test_modern_windows_retains_both_explicit_choices(self):
        with patch.object(platform, "windows_version", return_value=(10, 0, 17763)):
            self.assertFalse(platform.owned_playback_only())
            self.assertEqual(platform.normalize_playback_selection(False, True), (False, True))

    def test_legacy_build_stays_owned_only_on_newer_acceptance_host(self):
        with patch.object(platform, "windows_version", return_value=(10, 0, 26200)), \
             patch.dict(platform.sys.modules, {"PySide6": SimpleNamespace(__legacy_qt5__=True)}):
            self.assertTrue(platform.owned_playback_only())

    def test_windows7_reason_explains_unavailable_capture(self):
        with patch.object(platform, "windows_version", return_value=(6, 1, 7601)):
            self.assertIn("Windows 7", platform.system_media_unavailable_reason())

    def test_settings_cannot_enable_system_or_disable_only_owned_source(self):
        from local.settings_store import SettingsManager
        manager = SimpleNamespace(_settings={"v2_owned_playback_enabled": False, "v2_system_capture_enabled": True})
        with patch("local.settings_store.owned_playback_only", return_value=True):
            self.assertTrue(SettingsManager.get(manager, "v2_owned_playback_enabled"))
            self.assertFalse(SettingsManager.get(manager, "v2_system_capture_enabled"))

    def test_provider_boundary_rejects_stale_enabled_system_setting(self):
        from api.providers import PlaybackSourceProvider, _ensure_winsdk_loaded
        provider = PlaybackSourceProvider.__new__(PlaybackSourceProvider)
        provider.settings_getter = lambda key, default=None: True
        with patch("api.providers.owned_playback_only", return_value=True):
            self.assertFalse(provider._system_enabled())
            self.assertFalse(_ensure_winsdk_loaded())

    def test_no_system_provider_loop_or_smtc_worker_in_legacy_mode(self):
        from api.providers import WindowsMediaProvider
        with patch("api.providers.owned_playback_only", return_value=True), \
             patch.object(WindowsMediaProvider, "_start_loop") as start:
            provider = WindowsMediaProvider()
            self.assertEqual(provider._provider_status, "unsupported_os")
            self.assertIsNone(provider._loop)
            start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
