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

    def test_location_and_tasker_defaults_are_present(self):
        from local.settings_store import DEFAULT_SETTINGS

        self.assertEqual(DEFAULT_SETTINGS["v2_location_mode"], "free")
        self.assertEqual(DEFAULT_SETTINGS["v2_taskbar_corner_style"], "win11")
        self.assertEqual(DEFAULT_SETTINGS["v2_taskbar_left_handle_color"], "#8E8E93")
        self.assertEqual(DEFAULT_SETTINGS["v2_dual_line_font_size"], 12)
        self.assertIn("pure_white", DEFAULT_SETTINGS["v2_music_ambience_values"])
        self.assertEqual(DEFAULT_SETTINGS["v2_enabled_plugins"], ["time", "weather", "agenda"])
        self.assertNotIn("v2_taskbar_recognition_threshold", DEFAULT_SETTINGS)
        self.assertNotIn("v2_taskbar_plugin_compact_style", DEFAULT_SETTINGS)
        self.assertNotIn("v2_taskbar_message_compact_style", DEFAULT_SETTINGS)
        self.assertNotIn("v2_taskbar_hint_text", DEFAULT_SETTINGS)
        self.assertNotIn("v2_taskbar_fusion_mode", DEFAULT_SETTINGS)

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

    def test_settings_window_exposes_location_mode_instead_of_old_taskbar_fusion_controls(self):
        source = self._source("local/settings_window.py")

        self.assertIn("LOCATION_MODES", source)
        self.assertIn("location_mode", source)
        self.assertIn("v2_location_mode", source)
        self.assertIn("taskbar_corner_style", source)
        self.assertIn("dual_line_font_size", source)
        self.assertNotIn("taskbar_recognition_threshold", source)
        self.assertNotIn("taskbar_plugin_compact_style", source)
        self.assertNotIn("taskbar_message_compact_style", source)
        self.assertNotIn("TASKBAR_FUSION_MODES", source)
        self.assertNotIn("taskbar_fusion_mode", source)
        self.assertNotIn("taskbar_hint_text", source)

    def test_music_ambience_setting_is_renamed_to_background_style(self):
        source = self._source("local/settings_window.py")

        self.assertIn("音乐岛背景样式", source)
        self.assertNotIn("音乐岛光效", source)

    def test_settings_window_is_more_compact_with_rounded_shell(self):
        source = self._source("local/settings_window.py")
        style = self._source("local/mac_style.py")

        self.assertIn("COMPACT_ROW_VPAD = 8", source)
        self.assertIn("setMinimumSize(688, 496)", source)
        self.assertIn("WA_TranslucentBackground", source)
        self.assertIn("MacWindowShell", source)
        self.assertIn("border-radius: 14px", style)
        self.assertIn("border-radius: 10px", style)
        self.assertIn("min-height: 24px", style)
        self.assertIn("_apply_rounded_window_mask", source)
        self.assertIn("setMask", source)

    def test_settings_traffic_lights_show_hover_symbols(self):
        style = self._source("local/mac_style.py")

        self.assertIn("_symbol", style)
        self.assertIn("enterEvent", style)
        self.assertIn("leaveEvent", style)
        self.assertIn("drawText", style)
        self.assertIn('"×"', style)
        self.assertIn('"−"', style)
        self.assertIn('"□"', style)

    def test_empty_bar_preview_uses_selected_palette_colors(self):
        source = self._source("local/settings_window.py")

        self.assertIn("_empty_bar_preview_colors", source)
        self.assertIn('"rainbow"', source)
        self.assertIn("self.empty_palette.currentData()", source)
        self.assertNotIn('colors = self._current_custom_colors()\n            bg = f"qlineargradient', source)

    def test_tasker_theme_mode_changes_sync_music_background(self):
        source = self._source("local/settings_window.py")

        self.assertNotIn("_sync_tasker_music_background_to_theme_mode", source)
        set_theme_start = source.index("def _set_theme_mode")
        set_theme_end = source.index("def _normalize_theme_time", set_theme_start)
        set_theme_source = source[set_theme_start:set_theme_end]
        self.assertNotIn("set_music_ambience_style", set_theme_source)
        self.assertNotIn("v2_music_ambience_style", set_theme_source)

    def test_gelv_color_fill_keeps_all_music_background_options_available(self):
        source = self._source("local/settings_window.py")

        self.assertIn("_music_background_options_allowed", source)
        self.assertIn("set(DEFAULT_MUSIC_AMBIENCE_VALUES)", source)
        self.assertNotIn('{"pure_black", "bilateral_diffraction", "pure_white"} if active_fill', source)

    def test_location_mode_options_reflect_manual_finder_state(self):
        source = self._source("local/settings_window.py")

        self.assertIn('if manual_finder:', source)
        self.assertIn('("Free", "free"), ("Finder", "finder")', source)
        self.assertIn('("Free", "free"), ("Tasker", "tasker")', source)
        self.assertIn("_refresh_location_mode_options", source)

    def test_disabled_settings_options_are_visually_gray(self):
        style = self._source("local/mac_style.py")

        self.assertIn(":disabled", style)
        self.assertIn("color: {t[\"disabled_text\"]}", style)
        self.assertIn("QComboBox QAbstractItemView::item:disabled", style)

    def test_appearance_page_exposes_settings_font_selection_and_import(self):
        source = self._source("local/settings_window.py")

        self.assertIn("QFontDatabase", source)
        self.assertIn("QFileDialog", source)
        self.assertIn("settings_font_combo", source)
        self.assertIn("import_settings_font_button", source)
        self.assertIn("_import_settings_font", source)
        self.assertIn("_apply_settings_font", source)
        self.assertIn("addApplicationFont", source)
        self.assertIn("v2_settings_font_family", source)
        self.assertIn("v2_imported_settings_fonts", source)
        self.assertIn("PingFang SC", source)
        self.assertIn("Source Han Serif SC", source)

    def test_taskbar_left_handle_color_setting_is_exposed(self):
        source = self._source("local/settings_window.py")
        app_source = self._source("local/app.py")
        visual_source = self._source("local/island_stack_visual.py")

        self.assertIn("taskbar_left_handle_color", source)
        self.assertIn("v2_taskbar_left_handle_color", source)
        self.assertIn("ColorButton", source)
        self.assertIn("v2_taskbar_left_handle_color", app_source)
        self.assertIn("set_taskbar_left_handle_color", visual_source)

    def test_plugin_count_slider_is_replaced_by_three_plugin_checkboxes(self):
        source = self._source("local/settings_window.py")

        self.assertIn("plugin_time_enabled", source)
        self.assertIn("plugin_weather_enabled", source)
        self.assertIn("plugin_agenda_enabled", source)
        self.assertIn("v2_enabled_plugins", source)
        self.assertNotIn("plugin_count = self._slider_row", source)
        self.assertNotIn("插件数量", source)

    def test_settings_window_is_arranged_by_function_pages(self):
        source = self._source("local/settings_window.py")

        self.assertIn("def _placement_page", source)
        self.assertIn("def _media_page", source)
        self.assertIn("def _plugins_page", source)
        self.assertIn("def _appearance_page", source)
        self.assertIn("self._placement_page()", source)
        self.assertLess(source.index("self._placement_page()"), source.index("self._media_page()"))
        self.assertLess(source.index("self._media_page()"), source.index("self._plugins_page()"))
        self.assertLess(source.index("self._plugins_page()"), source.index("self._appearance_page()"))


if __name__ == "__main__":
    unittest.main()
