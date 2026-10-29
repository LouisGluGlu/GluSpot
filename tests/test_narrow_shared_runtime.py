"""Offline regressions for the shared narrow owner and isolated settings."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication
from api.providers import EmptyIslandDataProvider
from local.settings_store import SettingsManager


class FixtureSystemProvider(EmptyIslandDataProvider):
    def __init__(self, *, settings_getter, attach_only=False):
        super().__init__()
        self.settings_getter = settings_getter
        self.attach_only = attach_only
        self.lyrics_store = Mock()
        self.settings_setter = None

    def start_audio_outputs(self):
        return None

    def close_audio_outputs(self):
        pass

    def suspend_live_audio(self):
        pass


class SharedNarrowRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_explicit_settings_path_isolated_from_default_and_legacy(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            default = root / 'default' / 'settings.json'
            legacy = root / 'legacy'
            legacy.mkdir()
            (legacy / 'settings.json').write_text('{"v2_music_glass_level": 11}', encoding='utf-8')
            fixture = root / 'fixture' / 'settings.json'
            with patch('local.settings_store.SETTINGS_FILE', str(default)), \
                 patch('local.settings_store.APP_DATA_DIR', str(default.parent)), \
                 patch('local.settings_store.legacy_app_data_dirs', return_value=[str(legacy)]) as migration:
                settings = SettingsManager(settings_path=fixture)
                self.assertNotEqual(settings.get('v2_music_glass_level'), 11)
                settings.set('v2_music_glass_level', 73)
                settings.save_now()
                self.assertEqual(SettingsManager(settings_path=fixture).get('v2_music_glass_level'), 73)
                self.assertFalse(default.exists())
                migration.assert_not_called()

    def test_bootstrap_builds_narrow_around_one_shared_fixture_provider(self):
        from local.app import bootstrap
        previous_quit_policy = self.app.quitOnLastWindowClosed()
        self.addCleanup(self.app.setQuitOnLastWindowClosed, previous_quit_policy)
        with tempfile.TemporaryDirectory() as root, \
             patch('local.app.WindowsMediaProvider', FixtureSystemProvider), \
             patch('local.app.make_online_music_service', return_value=Mock()), \
             patch('local.app.LocalIslandApp._apply_startup_setting'), \
             patch('local.app.LocalIslandApp._setup_mydockfinder_listener'):
            runtime = bootstrap(view='narrow', capture='attach_only',
                                settings_path=Path(root) / 'settings.json')
            try:
                owner, view = runtime.window, runtime.view_window
                self.assertIs(view.owner, owner)
                self.assertIs(owner.data_provider, runtime.data_provider)
                self.assertIs(runtime.data_provider.system, runtime.system_provider)
                self.assertTrue(runtime.system_provider.attach_only)
                self.assertIs(owner.music_fullscreen_overlay, view)
                self.assertTrue(owner._music_surface_active)
                self.assertTrue(owner.timer.isActive())
                self.assertEqual(runtime.system_provider.settings_getter('netease.transport'), 'attach')
                runtime.settings.set('v2_music_glass_level', 71)
                self.assertEqual(runtime.system_provider.settings_getter('v2_music_glass_level'), 71)
                with patch.object(owner, '_refresh_media_data') as refresh:
                    view.tick()
                    refresh.assert_not_called()
            finally:
                runtime.window._application_quitting = True
                runtime.system_media_controls.close()
                runtime.data_provider.close_audio_outputs()
                runtime.view_window.shutdown()
                runtime.view_window.close()
                runtime.window.close()
                runtime.settings.save_now()
                runtime.watchdog.timer.stop()
                runtime.song_collection.close()
                runtime.playback_entry.close()
                runtime.music_library.close()
                runtime.online_music.close()


if __name__ == '__main__':
    unittest.main()
