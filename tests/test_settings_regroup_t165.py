"""Settings regroup keeps existing values, actions and search destinations."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QLabel

from local.settings_store import SettingsManager
from local.settings_window import SettingsWindow
from tests.test_mac_preferences_shell import FakeIslandWidget, FakeSettingsManager


class SettingsRegroupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_controls_and_search_belong_to_the_requested_groups(self):
        island = FakeIslandWidget()
        window = SettingsWindow(island, FakeSettingsManager())
        try:
            groups = (
                ("账号与平台", (window.online_login_button, window.personal_dj_voice_button)),
                ("播放与采集", (window.owned_playback_switch, window.system_capture_switch,
                                window.netease_capture_settings, window.system_lyric_offset,
                                window.music_surface_preload_count)),
                ("歌词与显示", (window.music_surface_reading_gradient_steps,
                                window.dual_line_secondary_source, window.cantonese_secondary_priority,
                                window.lyric_chinese_conversion)),
                ("窗口与布局", (window.theme_mode, window.music_glass_level,
                                window.mydockfinder_monitor_switch, window.placement_mode,
                                window.finder_bar_preview, window.music_preview, window.lyric_flow_style)),
                ("高级", (window.startup_mode, window.animation_speed, window.fps_slider,
                          window.lyric_fps_slider, window.app_icon_preview,
                          window.clear_artwork_cache_button, window._diagnostic_export_status)),
            )
            for index, (title, controls) in enumerate(groups):
                with self.subTest(title=title):
                    page = window._settings_pages[index]
                    self.assertEqual(page['title'], title)
                    self.assertTrue(all(page['page'].isAncestorOf(control) for control in controls))
            for query, target in (("音乐页预载歌曲", 1), ("歌词繁简转换", 2),
                                  ("背景样式", 3), ("动画倍速", 4)):
                with self.subTest(query=query):
                    matches = window._settings_search_matches(query)
                    self.assertTrue(matches)
                    self.assertEqual(matches[0]['page_index'], target)
            window.online_music_login_callback = Mock()
            window.personal_dj_voice_callback = Mock()
            window.online_login_button.click()
            window.personal_dj_voice_button.click()
            window.online_music_login_callback.assert_called_once_with()
            window.personal_dj_voice_callback.assert_called_once_with()
            labels = ' '.join(label.text() for label in window.findChildren(QLabel))
            self.assertNotIn('金标', labels)
            self.assertNotIn('金色涟漪', labels)
            self.assertIn('低感知方案', labels)
        finally:
            window.close()
            window.deleteLater()
            island.deleteLater()
            self.app.processEvents()

    def test_settings_glass_stays_dark_without_overwriting_saved_app_theme(self):
        settings = FakeSettingsManager()
        settings.values['v2_theme_mode'] = 'light'
        island = FakeIslandWidget()
        window = SettingsWindow(island, settings)
        try:
            self.assertTrue(window._settings_dark_mode())
            self.assertLess(QColor(window._theme()['window']).lightness(), 60)
            self.assertIn('rgba(', window._theme()['card'])
            self.assertEqual(window.theme_mode.currentData(), 'light')
            self.assertEqual(settings.get('v2_theme_mode'), 'light')
            switch = window.music_background_follow_theme_checkbox
            self.assertIs(switch.property('darkMode'), True)
            switch.resize(360, 26)
            image = QImage(switch.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with patch('local.ios_widgets.app_dark_mode', return_value=False):
                switch.render(image)
            label_ink = [image.pixelColor(x, y)
                         for x in range(250) for y in range(21)
                         if image.pixelColor(x, y).alpha() > 200]
            self.assertTrue(label_ink)
            self.assertTrue(any(color.lightness() > 220 for color in label_ink))
        finally:
            window.close()
            window.deleteLater()
            island.deleteLater()
            self.app.processEvents()

    def test_old_json_loses_no_additional_values_through_open_sync_and_save(self):
        # The store already retires gold/lift keys. Compare with its existing
        # normalized values so this UI change cannot introduce a new migration.
        old = {
            'v2_theme_mode': 'light', 'v2_owned_playback_mode': 'shuffle',
            'v2_music_content_layout': 'playlist', 'v2_music_glass_level': 73,
            'v2_music_surface_preload_count': 3,
            'v2_system_lyric_offset_seconds': -1.25,
            'v2_dual_line_secondary_source': 'transliteration',
            'v2_cantonese_secondary_priority': 'translation_first',
            'v2_lyric_chinese_conversion': 'traditional',
            'v2_music_surface_reading_strategy': 'gold',
            'v2_music_surface_reading_lift_level': 4,
            'v2_mydockfinder_path': 'C:/Legacy/MyDockFinder.exe',
            'extension.fixture': {'schema': 1, 'values': [False, 0, '保留']},
        }
        with tempfile.TemporaryDirectory(prefix='gluspot-t165-settings-') as temporary:
            path = Path(temporary) / 'settings.json'
            path.write_text(json.dumps(old, ensure_ascii=False), encoding='utf-8')
            settings = SettingsManager(settings_path=path)
            baseline = copy.deepcopy(settings._settings)
            island = FakeIslandWidget()
            window = SettingsWindow(island, settings)
            try:
                window.sync_from_widget()
                for index in range(5):
                    window.nav.setCurrentRow(index)
                    window.sync_from_widget()
                self.assertEqual(settings._settings, baseline)
                self.assertEqual(window.music_surface_preload_count.value(), 3)
                self.assertEqual(window.system_lyric_offset.value(), -1250)
                self.assertEqual(window.lyric_chinese_conversion.currentData(), 'traditional')
                settings.save()
                window.close()
                stored = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(stored, baseline)
                self.assertEqual(stored['extension.fixture'], old['extension.fixture'])
                self.assertEqual(stored['v2_mydockfinder_path'], old['v2_mydockfinder_path'])
                restored = SettingsManager(settings_path=path)
                try:
                    self.assertEqual(restored._settings, baseline)
                finally:
                    restored._save_timer.stop()
            finally:
                settings._save_timer.stop()
                window.close()
                window.deleteLater()
                island.deleteLater()
                self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
