from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MacPreferencesShellTests(unittest.TestCase):
    def _source(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_settings_window_uses_mac_preferences_shell_regions(self):
        source = self._source("local/settings_window.py")

        self.assertIn("mac_preferences_stylesheet", source)
        self.assertIn("mac_traffic_lights(self)", source)
        self.assertIn("mac_sidebar_icon(", source)
        self.assertIn("FramelessWindowHint", source)
        self.assertNotIn("WindowCloseButtonHint", source)
        self.assertNotIn("WindowMinimizeButtonHint", source)
        self.assertNotIn("WindowMaximizeButtonHint", source)
        self.assertIn('setObjectName("MacSidebar")', source)
        self.assertIn('setObjectName("MacSearch")', source)
        self.assertIn('setObjectName("MacSidebarList")', source)
        self.assertIn('setObjectName("MacTitleBar")', source)
        self.assertIn('setObjectName("MacTitle")', source)
        self.assertIn('setObjectName("MacContentScroll")', source)
        self.assertIn('setObjectName("MacCard")', source)

    def test_plugin_manager_uses_same_mac_preferences_shell_regions(self):
        source = self._source("local/plugin_manager_window.py")

        self.assertIn("mac_preferences_stylesheet", source)
        self.assertIn("mac_traffic_lights(self)", source)
        self.assertIn("mac_sidebar_icon(", source)
        self.assertIn("FramelessWindowHint", source)
        self.assertNotIn("WindowCloseButtonHint", source)
        self.assertNotIn("WindowMinimizeButtonHint", source)
        self.assertNotIn("WindowMaximizeButtonHint", source)
        self.assertIn('setObjectName("MacSidebar")', source)
        self.assertIn('setObjectName("MacSearch")', source)
        self.assertIn('setObjectName("MacSidebarList")', source)
        self.assertIn('setObjectName("MacTitleBar")', source)
        self.assertIn('setObjectName("MacTitle")', source)
        self.assertIn('setObjectName("MacContentScroll")', source)
        self.assertIn('setObjectName("MacCard")', source)

    def test_plugin_settings_are_merged_into_settings_window(self):
        source = self._source("local/settings_window.py")

        self.assertIn("def _plugins_page", source)
        self.assertIn('("插件", self._plugins_page())', source)
        self.assertIn("NoWheelComboBox", source)
        self.assertIn("NoWheelSpinBox", source)
        self.assertIn("v2_plugin_timezone", source)
        self.assertIn("v2_weather_use_location", source)
        self.assertIn("v2_schedule_json_path", source)

    def test_plugin_click_no_longer_opens_plugin_settings_window(self):
        source = self._source("local/island_stack_visual.py")

        activate_start = source.index("def _activate_key")
        activate_end = source.index("def _open_plugin_manager", activate_start)
        activate_source = source[activate_start:activate_end]
        self.assertNotIn("_open_plugin_manager", activate_source)

    def test_plugin_manager_has_real_controls_for_three_plugins(self):
        source = self._source("local/plugin_manager_window.py")

        self.assertIn("v2_weather_use_location", source)
        self.assertIn("v2_weather_manual_location", source)
        self.assertIn("v2_schedule_reminder_minutes", source)
        self.assertIn("v2_alarm_rows", source)
        self.assertIn("课表读取录入", source)

    def test_time_settings_use_adapted_alarm_rows_instead_of_raw_iso_alarm_input(self):
        settings_source = self._source("local/settings_window.py")
        manager_source = self._source("local/plugin_manager_window.py")

        self.assertIn("plugin_alarm_summary", settings_source)
        self.assertIn("v2_alarm_rows", settings_source)
        self.assertNotIn("plugin_alarm_at = QLineEdit", settings_source)
        self.assertIn("alarm_rows_summary", manager_source)
        self.assertIn("v2_alarm_rows", manager_source)
        self.assertNotIn("alarm_input = QLineEdit", manager_source)

    def test_future_island_style_placeholders_are_exposed(self):
        settings_source = self._source("local/settings_window.py")
        store_source = self._source("local/settings_store.py")
        app_source = self._source("local/app.py")
        visual_source = self._source("local/island_stack_visual.py")

        for key in (
            "v2_side_island_style",
            "v2_plugin_island_style",
            "v2_music_island_style",
            "v2_time_alarm_side_display",
        ):
            self.assertIn(key, store_source)
            self.assertIn(key, settings_source)
            self.assertIn(key, visual_source)

        self.assertIn("time_alarm_side_display", settings_source)
        self.assertIn("side_island_style", settings_source)
        self.assertIn("plugin_island_style", settings_source)
        self.assertIn("music_island_style", settings_source)
        self.assertIn("v2_time_alarm_side_display", app_source)

    def test_betterncm_entry_and_ncm_path_are_exposed(self):
        settings_source = self._source("local/settings_window.py")
        store_source = self._source("local/settings_store.py")

        self.assertIn("netease_betterncm", settings_source)
        self.assertIn("网易云音乐（BetterNCM）", settings_source)
        self.assertIn("v2_netease_betterncm_path", store_source)
        self.assertIn("v2_netease_betterncm_path", settings_source)
        self.assertIn("ncm_path_input", settings_source)

    def test_lyric_flow_settings_defaults_are_present(self):
        from local.settings_store import DEFAULT_SETTINGS

        self.assertEqual(DEFAULT_SETTINGS["v2_lyric_flow_style"], "wave_stream")
        self.assertEqual(DEFAULT_SETTINGS["v2_gelv_font_family"], "Source Han Serif SC")
        self.assertEqual(DEFAULT_SETTINGS["v2_gelv_primary_font_size"], 22)
        self.assertEqual(DEFAULT_SETTINGS["v2_gelv_secondary_font_size"], 12)
        self.assertEqual(DEFAULT_SETTINGS["v2_gelv_dual_line_font_size"], 17)
        self.assertEqual(DEFAULT_SETTINGS["v2_gelv_initial_word_spacing"], 220)
        self.assertEqual(DEFAULT_SETTINGS["v2_gelv_word_spacing"], 320)
        self.assertTrue(DEFAULT_SETTINGS["v2_gelv_size_shift_enabled"])
        self.assertFalse(DEFAULT_SETTINGS["v2_gelv_color_fill_enabled"])
        self.assertEqual(DEFAULT_SETTINGS["v2_gelv_color_fill_fallback"], "#00D7FF")
        self.assertFalse(DEFAULT_SETTINGS["v2_wave_color_fill_enabled"])
        self.assertEqual(DEFAULT_SETTINGS["v2_wave_color_fill_fallback"], "#00D7FF")
        self.assertEqual(DEFAULT_SETTINGS["v2_no_lyrics_display_mode"], "text")
        self.assertEqual(DEFAULT_SETTINGS["v2_album_island_cover_count"], 5)
        self.assertEqual(DEFAULT_SETTINGS["v2_album_island_cover_size"], 30)
        self.assertEqual(DEFAULT_SETTINGS["v2_album_island_spacing"], 112)
        self.assertEqual(DEFAULT_SETTINGS["v2_album_island_secondary_style"], "perspective_dim")
        self.assertEqual(DEFAULT_SETTINGS["v2_album_island_perspective_amount"], 100)
        self.assertTrue(DEFAULT_SETTINGS["v2_compact_lyric_show_cover"])
        self.assertIn("sea_level", DEFAULT_SETTINGS["v2_music_ambience_values"])
        self.assertIn("bilateral_diffraction", DEFAULT_SETTINGS["v2_music_ambience_values"])

    def test_settings_window_scopes_gelv_controls_and_hides_global_font_selector(self):
        source = self._source("local/settings_window.py")

        self.assertIn("LYRIC_FLOW_STYLES", source)
        self.assertIn('"album_island"', source)
        self.assertIn("格律宋散", source)
        self.assertIn("逐字染色", source)
        self.assertIn("NO_LYRICS_DISPLAY_MODES", source)
        self.assertIn("专辑岛模式", source)
        self.assertIn("_update_lyric_flow_controls", source)
        self.assertIn("GELV_FONT_CHOICES", source)
        self.assertIn("gelv_primary_font_size", source)
        self.assertIn("gelv_secondary_font_size", source)
        self.assertIn("gelv_dual_line_font_size", source)
        self.assertIn("gelv_initial_word_spacing", source)
        self.assertIn("gelv_word_spacing", source)
        self.assertIn("v2_gelv_primary_font_size", source)
        self.assertIn("v2_gelv_secondary_font_size", source)
        self.assertIn("v2_gelv_dual_line_font_size", source)
        self.assertIn("v2_gelv_initial_word_spacing", source)
        self.assertIn("v2_gelv_word_spacing", source)
        self.assertIn("wave_color_fill_checkbox", source)
        self.assertIn("v2_wave_color_fill_enabled", source)
        self.assertIn("v2_wave_color_fill_fallback", source)
        self.assertIn("纯黑", source)
        self.assertIn("双侧衍射", source)
        self.assertIn("格律字体", source)
        self.assertIn("gelv_size_shift_checkbox", source)
        self.assertIn("格律字号变化", source)
        self.assertIn("gelv_dual_line_checkbox", source)
        self.assertIn("格律双语显示", source)
        self.assertIn("同行歌词字号", source)
        self.assertIn("_detail_panel", source)
        self.assertIn("_collapse_detail_panels", source)
        self.assertIn("_update_music_ambience_controls", source)
        self.assertIn("v2_music_ambience_values", source)
        self.assertIn("album_island_cover_size", source)
        self.assertIn("album_island_spacing", source)
        self.assertIn("album_island_secondary_style", source)
        self.assertIn("album_island_perspective_amount", source)
        self.assertIn("v2_album_island_secondary_style", source)
        self.assertIn("v2_album_island_perspective_amount", source)
        self.assertIn("平排", source)
        self.assertIn("透视", source)
        self.assertNotIn("缩小变暗处理", source)
        self.assertNotIn("左右平透视效果变暗处理", source)
        self.assertIn("compact_lyric_show_cover", source)
        self.assertIn("v2_compact_lyric_show_cover", source)
        self.assertIn("_album_island_controls", source)
        self.assertIn("album_flow", source)
        self.assertIn("self.lyric_offset.parentWidget().setVisible(not album_flow)", source)
        self.assertIn("self.no_lyrics_display_mode_row.setVisible(not album_flow)", source)
        self.assertIn("v2_album_island_cover_size", source)
        self.assertIn("v2_album_island_spacing", source)
        self.assertIn("详细设置", source)
        self.assertIn("_wave_stream_controls", source)
        self.assertIn("_gelv_controls", source)
        self.assertIn('FONT_CHOICES = (\n        "PingFang SC",\n        "Source Han Serif SC",\n    )', source)
        self.assertIn("self.lyric_font_size = self._slider_row(wave_layout", source)
        self.assertNotIn("self.lyric_font_size = self._slider_row(c,", source)
        self.assertNotIn('self._row(c, "全局字体", font_combo)', source)
        self.assertNotIn("enable_dual_line_lyrics = False", source)
        self.assertIn("setVisible(gelv)", source)


if __name__ == "__main__":
    unittest.main()
