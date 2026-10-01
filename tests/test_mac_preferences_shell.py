from __future__ import annotations

from pathlib import Path
from unittest import mock
import json
import tempfile
import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QSizePolicy, QWidget


ROOT = Path(__file__).resolve().parents[1]


class FakeSettingsManager:
    def __init__(self):
        from local.settings_store import DEFAULT_SETTINGS

        self.values = dict(DEFAULT_SETTINGS)

    def get(self, key: str, default=None):
        return self.values.get(key, default)

    def set(self, key: str, value):
        self.values[key] = value


class FakeIslandWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.show_seconds = True
        self.enabled_plugin_ids = {"time"}
        self.enabled_plugin_count = 1
        self.persistent_plugin = True
        self.music_playing = True
        self.music_auto_detect = False
        self.empty_island_display_mode = "visible_bar"
        self.no_music_island_priority = "plugin"
        self.location_mode = "free"
        self.top_notch_height_mode = "standard"
        self.tasker_follow_taskbar_color = False
        self.music_ambience_style = "sea_level"
        self.yaoguang_outer_color = "#6C35F2"
        self.yaoguang_blur_strength = 58
        self.yaoguang_color_source = "custom"
        self.yaoguang_album_inverse_enabled = False
        self.yaoguang_height_mode = "regular"
        self.media_refresh_calls: list[str] = []
        self.diagnostics_export_calls = 0
        self.last_media_refresh_at = 123.0
        self.data_provider = type(
            "FakeProvider",
            (),
            {"_request_immediate_media_refresh": lambda provider_self: self.media_refresh_calls.append("provider")},
        )()

    def _enforce_stack_limit(self):
        pass

    def _pulse_stack(self):
        pass

    def set_top_notch_height_mode(self, mode: str, notify: bool = True):
        self.top_notch_height_mode = str(mode)

    def set_tasker_follow_taskbar_color(self, enabled: bool, notify: bool = True):
        self.tasker_follow_taskbar_color = bool(enabled)

    def set_music_ambience_style(self, style: str, notify: bool = True):
        self.music_ambience_style = str(style)

    def set_yaoguang_outer_color(self, color: str, notify: bool = True):
        self.yaoguang_outer_color = str(color)

    def set_yaoguang_blur_strength(self, value: int, notify: bool = True):
        self.yaoguang_blur_strength = int(value)

    def set_yaoguang_color_source(self, source: str, notify: bool = True):
        self.yaoguang_color_source = str(source)

    def set_yaoguang_album_inverse_enabled(self, enabled: bool, notify: bool = True):
        self.yaoguang_album_inverse_enabled = bool(enabled)

    def set_yaoguang_height_mode(self, mode: str, notify: bool = True):
        self.yaoguang_height_mode = str(mode)

    def export_runtime_diagnostics(self):
        self.diagnostics_export_calls += 1
        return r"C:\Temp\gluspot-runtime-diagnostics.json"


class MacPreferencesShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls._default_app_font = cls.app.font()

    def tearDown(self):
        self.app.setFont(self._default_app_font)

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

    def test_settings_sidebar_uses_asset_icons(self):
        from local.settings_window import SettingsWindow

        window = SettingsWindow(FakeIslandWidget(), FakeSettingsManager())
        try:
            self.assertGreaterEqual(window.nav.iconSize().width(), 22)
            self.assertGreaterEqual(window.nav.iconSize().height(), 22)
            icon_paths = [page.get("icon_path", "") for page in window._settings_pages]
            self.assertEqual(len(icon_paths), 6)
            self.assertTrue(all("assets" in str(path) and "seeting" in str(path) for path in icon_paths))
            self.assertTrue(all(Path(str(path)).exists() for path in icon_paths))
        finally:
            window.close()

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
        self.assertIn('("插件设置", self._plugins_page())', source)
        self.assertIn("NoWheelComboBox", source)
        self.assertIn("NoWheelSpinBox", source)
        self.assertIn("v2_plugin_timezone", source)
        self.assertIn("v2_weather_use_location", source)
        self.assertIn("v2_schedule_json_path", source)

    def test_animation_speed_control_is_exposed(self):
        source = self._source("local/settings_window.py")

        self.assertIn("self.animation_speed = self._combo", source)
        self.assertNotIn("self.animation_speed.parentWidget().hide()", source)

    def test_animation_fps_controls_are_visible_and_refresh_rate_limited(self):
        source = self._source("local/settings_window.py")
        from local.settings_store import DEFAULT_SETTINGS

        self.assertEqual(DEFAULT_SETTINGS["v2_animation_fps"], 60)
        self.assertEqual(DEFAULT_SETTINGS["v2_lyric_animation_fps"], 120)
        self.assertIn("_display_refresh_rate_fps_cap", source)
        self.assertIn("self.fps_slider = self._slider_row", source)
        self.assertIn("self.lyric_fps_slider = self._slider_row", source)
        self.assertNotIn("self.fps_slider.parentWidget().hide()", source)
        self.assertIn("高性能消耗", source)

    def test_about_diagnostics_can_export_support_payload(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        window = SettingsWindow(island, FakeSettingsManager())
        try:
            window._export_diagnostics()

            self.assertEqual(island.diagnostics_export_calls, 1)
            self.assertIn("gluspot-runtime-diagnostics.json", window._diagnostic_export_status.text())
        finally:
            window.close()

    def test_empty_bar_no_longer_paints_streaming_palette(self):
        source = self._source("local/island_stack_visual.py")
        start = source.index("def _paint_thin_bar")
        end = source.index("def _scrolling_text_x", start)
        thin_bar_source = source[start:end]

        self.assertIn("_empty_bar_color", thin_bar_source)
        self.assertNotIn("self._animation_speed_factor()", thin_bar_source)
        self.assertNotIn("_empty_bar_colors", thin_bar_source)

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
            "v2_time_alarm_side_display",
        ):
            self.assertIn(key, store_source)
            self.assertIn(key, settings_source)
            self.assertIn(key, visual_source)

        self.assertIn("time_alarm_side_display", settings_source)
        self.assertIn("side_island_style", settings_source)
        self.assertIn("plugin_island_style", settings_source)
        self.assertIn("v2_music_island_style", store_source)
        self.assertIn("v2_music_island_style", visual_source)
        self.assertNotIn("self.music_island_style", settings_source)
        self.assertIn("v2_time_alarm_side_display", app_source)

    def test_expanded_music_layout_style_exposes_regular_and_dual_panel_choices(self):
        from local.settings_store import DEFAULT_SETTINGS
        from local.settings_window import SettingsWindow

        settings_source = self._source("local/settings_window.py")
        app_source = self._source("local/app.py")
        visual_source = self._source("local/island_stack_visual.py")

        self.assertEqual(DEFAULT_SETTINGS["v2_music_expanded_layout_style"], "standard")
        self.assertIn("MUSIC_EXPANDED_LAYOUT_STYLES", settings_source)
        self.assertIn('("常规", "standard")', settings_source)
        self.assertIn('("双板", "dual_panel")', settings_source)
        self.assertIn("v2_music_expanded_layout_style", app_source)
        self.assertIn("v2_music_expanded_layout_style", visual_source)

        island = FakeIslandWidget()
        settings = FakeSettingsManager()
        window = SettingsWindow(island, settings)
        try:
            self.assertTrue(hasattr(window, "music_expanded_layout_style"))
            window._set_combo_data(window.music_expanded_layout_style, "dual_panel")
            window._set_music_expanded_layout_style()
            self.assertEqual(island.music_expanded_layout_style, "dual_panel")
            self.assertEqual(settings.values["v2_music_expanded_layout_style"], "dual_panel")
        finally:
            window.close()

    def test_music_background_style_owns_contextual_controls(self):
        settings_source = self._source("local/settings_window.py")

        self.assertNotIn("MUSIC_ISLAND_STYLES", settings_source)
        self.assertNotIn("岛体样式", settings_source)
        self.assertNotIn("_music_island_style_detail_toggle", settings_source)
        self.assertNotIn("_update_music_island_style_controls", settings_source)
        self.assertIn("MusicAmbiencePreview", settings_source)
        self.assertIn("_paint_bilateral_diffraction", settings_source)
        self.assertIn("饱和光强度", settings_source)
        self.assertIn("流光浮动强度", settings_source)
        self.assertIn("双色占比", settings_source)
        self.assertNotIn("氛围光增强", settings_source)
        self.assertNotIn("diffraction_frequency_slider", settings_source)
        self.assertIn("music_ambience_theme_category", settings_source)
        self.assertIn("solid_album_background_fallback", settings_source)
        self.assertIn("yaoguang_outer_color", settings_source)

    def test_music_background_preview_uses_rendered_ordered_styles(self):
        from local.settings_window import MusicAmbiencePreview, SettingsWindow

        expected_styles = [
            "sea_level",
            "duotone_full",
            "solid_album",
            "pure_black",
            "pure_white",
        ]
        self.assertEqual([value for _label, value in SettingsWindow.MUSIC_AMBIENCE_STYLES], expected_styles)

        source = self._source("local/settings_window.py")
        self.assertIn("class MusicAmbiencePreview", source)
        self.assertIn("def _paint_sea_level", source)
        self.assertIn("def _paint_full_duotone", source)
        self.assertIn("def _paint_bilateral_diffraction", source)
        self.assertIn("def _paint_yaoguang", source)
        self.assertNotIn("_music_preview_stylesheet", source)

        preview = MusicAmbiencePreview()
        preview.resize(320, 64)
        values = {
            "ambient_intensity": 0.62,
            "music_flow_intensity": 0.74,
            "compact_duotone_balance": 58,
            "expanded_duotone_balance": 58,
            "diffraction_frequency": 0.72,
        }
        for style in expected_styles:
            preview.set_preview_state(style, values)
            image = QImage(preview.size(), QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            preview.render(painter, QPoint())
            painter.end()
            colors = {
                image.pixelColor(x, y).rgba()
                for x in range(12, image.width() - 12, 31)
                for y in range(10, image.height() - 10, 13)
            }
            self.assertGreater(len(colors), 8, style)

    def test_removed_backgrounds_are_not_selectable_or_configurable(self):
        from local.settings_window import MusicAmbiencePreview, SettingsWindow

        for retired in ("yaoguang", "bilateral_diffraction"):
            with self.subTest(style=retired):
                island = FakeIslandWidget()
                island.music_ambience_style = retired
                settings = FakeSettingsManager()
                settings.values["v2_music_ambience_style"] = retired
                window = SettingsWindow(island, settings)
                try:
                    self.assertEqual(window.music_ambience_style.findData(retired), -1)
                    self.assertEqual(island.music_ambience_style, "sea_level")
                    self.assertEqual(settings.values["v2_music_ambience_style"], "sea_level")
                    for name in (
                        "yaoguang_outer_color", "yaoguang_blur_strength", "yaoguang_color_source",
                        "yaoguang_album_inverse_checkbox", "yaoguang_height_mode", "diffraction_frequency_slider",
                    ):
                        self.assertFalse(hasattr(window, name), name)
                    preview = MusicAmbiencePreview()
                    preview.set_preview_state(retired)
                    self.assertEqual(preview._style, "sea_level")
                    window.sync_from_widget()
                finally:
                    window.close()

    def test_removed_visual_settings_migrate_without_changing_wave_font(self):
        from local import settings_store

        for retired in ("yaoguang", "bilateral_diffraction"):
            with self.subTest(style=retired), tempfile.TemporaryDirectory() as directory:
                settings_file = Path(directory) / "settings.json"
                settings_file.write_text(json.dumps({
                    "v2_music_ambience_style": retired,
                    "v2_music_background_follow_previous_style": retired,
                    "v2_wave_font_weight": "heavy",
                    "v2_lyric_font_size": 21,
                    "v2_enable_dual_line_lyrics": True,
                    "v2_wave_word_fill_enabled": False,
                    "v2_wave_color_fill_enabled": True,
                    "v2_lyric_elastic_strength": 160,
                    "v2_lyric_word_spacing": 150,
                    "v2_yaoguang_outer_color": "#AA33CC",
                    "v2_music_ambience_values": {
                        "sea_level": {"ambient_intensity": 0.33},
                        retired: {"ambient_intensity": 0.99},
                    },
                }), encoding="utf-8")
                with mock.patch.object(settings_store, "APP_DATA_DIR", directory), mock.patch.object(settings_store, "SETTINGS_FILE", str(settings_file)):
                    settings = settings_store.SettingsManager()
                    self.assertEqual(settings.get("v2_music_ambience_style"), "sea_level")
                    self.assertEqual(settings.get("v2_music_background_follow_previous_style"), "sea_level")
                    self.assertEqual(settings.get("v2_wave_font_weight"), "heavy")
                    self.assertEqual(settings.get("v2_lyric_font_size"), 21)
                    self.assertTrue(settings.get("v2_enable_dual_line_lyrics"))
                    self.assertTrue(settings._dirty)
                    self.assertNotIn(retired, settings.get("v2_music_ambience_values"))
                    self.assertEqual(settings.get("v2_music_ambience_values")["sea_level"]["ambient_intensity"], 0.33)
                    settings.save_now()
                    migrated = json.loads(settings_file.read_text(encoding="utf-8"))
                    self.assertEqual(migrated["v2_music_ambience_style"], "sea_level")
                    self.assertTrue(settings_store._REMOVED_VISUAL_SETTING_KEYS.isdisjoint(migrated))
                    for key in settings_store._REMOVED_VISUAL_SETTING_KEYS:
                        self.assertNotIn(key, settings._settings)
                        settings.set(key, "ignored")
                        self.assertNotIn(key, settings._settings)
                    settings.set("v2_music_ambience_style", retired)
                    self.assertEqual(settings.get("v2_music_ambience_style"), "sea_level")
                    settings.set("v2_music_ambience_values", {retired: {}, "sea_level": {"ambient_intensity": 0.4}})
                    self.assertNotIn(retired, settings.get("v2_music_ambience_values"))
                    settings.save_now()
                    saved = json.loads(settings_file.read_text(encoding="utf-8"))
                    self.assertEqual(saved["v2_wave_font_weight"], "heavy")
                    self.assertNotIn(retired, saved["v2_music_ambience_values"])
                    self.assertTrue(settings_store._REMOVED_VISUAL_SETTING_KEYS.isdisjoint(saved))

    def test_startup_ignores_removed_visual_options_and_preserves_wave_font_choice(self):
        from local.app import LocalIslandApp

        controller = LocalIslandApp.__new__(LocalIslandApp)
        controller.settings = FakeSettingsManager()
        controller.settings.values.update({
            "v2_music_ambience_style": "yaoguang",
            "v2_wave_font_weight": "heavy",
            "v2_lyric_font_size": 21,
            "v2_wave_word_fill_enabled": True,
            "v2_wave_color_fill_enabled": True,
            "v2_lyric_elastic_strength": 160,
            "v2_lyric_word_spacing": 150,
            "v2_yaoguang_outer_color": "#AA33CC",
        })
        controller.window = mock.Mock()
        controller.window.plugin_focus_target = 0
        controller.window.music_ambience_style = "sea_level"
        controller.window.set_music_ambience_style.side_effect = lambda style, **kwargs: setattr(
            controller.window, "music_ambience_style", style
        )
        with mock.patch("local.app._mydockfinder_process_diagnostics", return_value={"running": False}):
            controller._apply_saved_settings()
        controller.window.set_music_ambience_style.assert_called_once_with("sea_level", notify=False)
        controller.window.set_wave_font_weight.assert_called_once_with("heavy", notify=False)
        controller.window.set_lyric_font_size.assert_called_once_with(21, notify=False)
        for method in (
            "set_wave_word_fill_enabled", "set_wave_color_fill_enabled", "set_lyric_elastic_strength",
            "set_lyric_elastic_mode", "set_lyric_word_spacing", "set_yaoguang_outer_color",
            "set_yaoguang_blur_strength", "set_yaoguang_color_source", "set_yaoguang_album_inverse_enabled",
            "set_yaoguang_height_mode", "set_diffraction_frequency",
        ):
            getattr(controller.window, method).assert_not_called()

    def test_betterncm_entry_and_ncm_path_are_exposed(self):
        settings_source = self._source("local/settings_window.py")
        store_source = self._source("local/settings_store.py")

        self.assertIn("netease_betterncm", settings_source)
        self.assertIn("网易云音乐（BetterNCM）", settings_source)
        self.assertIn("v2_netease_betterncm_path", store_source)
        self.assertIn("v2_netease_betterncm_path", settings_source)
        self.assertIn("betterncm_path", settings_source)
        self.assertNotIn("ncm_path_input", settings_source)

    def test_music_app_profiles_defaults_are_present(self):
        from local.settings_store import DEFAULT_SETTINGS

        profiles = DEFAULT_SETTINGS["v2_music_app_profiles"]
        for service in ("netease", "qq", "qishui", "kugou"):
            self.assertIn(service, profiles)
            self.assertIn("path", profiles[service])
            self.assertIn("sync_enabled", profiles[service])
            self.assertIn("lyric_offset_seconds", profiles[service])
            self.assertNotIn("progress_poll_interval_seconds", profiles[service])
            self.assertFalse(profiles[service]["sync_enabled"])
        self.assertIn("betterncm_enabled", profiles["netease"])
        self.assertIn("betterncm_path", profiles["netease"])

    def test_music_app_profile_controls_are_exposed(self):
        settings_source = self._source("local/settings_window.py")

        self.assertIn("music_app_profile_controls", settings_source)
        self.assertIn("_music_app_profile", settings_source)
        self.assertIn("_set_music_app_profile_path", settings_source)
        self.assertIn("_set_music_app_profile_sync_enabled", settings_source)
        self.assertIn("_set_music_app_profile_lyric_offset", settings_source)
        self.assertNotIn("_set_music_app_profile_progress_poll_interval", settings_source)
        self.assertNotIn("progress_poll_interval_seconds", settings_source)
        self.assertIn("_set_music_app_profile_betterncm_enabled", settings_source)
        self.assertIn("_set_music_app_profile_betterncm_path", settings_source)
        self.assertIn("v2_music_app_profiles", settings_source)

    def test_lightweight_lyrics_follow_has_no_forced_anchor_settings_surface(self):
        settings_source = self._source("local/settings_window.py")
        store_source = self._source("local/settings_store.py")
        provider_source = self._source("api/providers.py")
        reader_source = self._source("api/netease_position.py")

        self.assertNotIn("progress_poll_interval_seconds", store_source)
        self.assertNotIn("progress_poll", settings_source)
        self.assertNotIn("lyric_click_debug", settings_source)
        self.assertNotIn("request_progress_anchor_refresh", provider_source)
        self.assertNotIn("_progress_poll_", provider_source)
        self.assertNotIn("_lyric_click_debug_snapshot", provider_source)
        self.assertNotIn("_ensure_global_pointer_sampler_started", provider_source)
        self.assertNotIn("read_immediate", reader_source)
        self.assertNotIn("read_progress_immediate", reader_source)
        self.assertNotIn("lyric_click_point_hits_visible_row", reader_source)

    def test_lyric_flow_settings_defaults_are_present(self):
        from local.settings_store import DEFAULT_SETTINGS

        self.assertEqual(DEFAULT_SETTINGS["v2_lyric_flow_style"], "wave_stream")
        self.assertNotIn("v2_lyric_glow_style", DEFAULT_SETTINGS)
        self.assertEqual(DEFAULT_SETTINGS["v2_font_family"], "Alibaba PuHuiTi")
        self.assertEqual(DEFAULT_SETTINGS["v2_settings_font_family"], "Alibaba PuHuiTi")
        self.assertEqual(DEFAULT_SETTINGS["v2_gelv_font_family"], "Noto Serif SC ExtraBold")
        self.assertEqual(DEFAULT_SETTINGS["v2_gelv_primary_font_size"], 22)
        self.assertEqual(DEFAULT_SETTINGS["v2_gelv_secondary_font_size"], 12)
        self.assertEqual(DEFAULT_SETTINGS["v2_gelv_dual_line_font_size"], 17)
        self.assertEqual(DEFAULT_SETTINGS["v2_gelv_initial_word_spacing"], 220)
        self.assertEqual(DEFAULT_SETTINGS["v2_gelv_word_spacing"], 320)
        self.assertNotIn("v2_gelv_size_shift_enabled", DEFAULT_SETTINGS)
        self.assertFalse(DEFAULT_SETTINGS["v2_gelv_color_fill_enabled"])
        self.assertFalse(DEFAULT_SETTINGS["v2_gelv_word_fill_enabled"])
        self.assertNotIn("v2_gelv_color_fill_fallback", DEFAULT_SETTINGS)
        self.assertEqual(DEFAULT_SETTINGS["v2_wave_font_weight"], "bold")
        self.assertNotIn("v2_wave_word_fill_enabled", DEFAULT_SETTINGS)
        self.assertNotIn("v2_wave_color_fill_enabled", DEFAULT_SETTINGS)
        self.assertEqual(DEFAULT_SETTINGS["v2_music_surface_preload_count"], 6)

        self.assertNotIn("v2_wave_color_fill_fallback", DEFAULT_SETTINGS)
        self.assertEqual(DEFAULT_SETTINGS["v2_ember_font_weight"], "normal")
        self.assertEqual(DEFAULT_SETTINGS["v2_ember_line_mode"], "split")
        self.assertFalse(DEFAULT_SETTINGS["v2_ember_dual_line_enabled"])
        self.assertFalse(DEFAULT_SETTINGS["v2_ember_italic_enabled"])
        self.assertTrue(DEFAULT_SETTINGS["v2_ember_show_cover"])
        self.assertTrue(DEFAULT_SETTINGS["v2_ember_word_fill_enabled"])
        self.assertFalse(DEFAULT_SETTINGS["v2_ember_color_fill_enabled"])
        self.assertNotIn("v2_ember_color_fill_fallback", DEFAULT_SETTINGS)
        self.assertEqual(DEFAULT_SETTINGS["v2_ember_font_size"], 18)
        self.assertEqual(DEFAULT_SETTINGS["v2_ember_back_gray_mix"], 62)
        self.assertEqual(DEFAULT_SETTINGS["v2_ember_color_back_gray_mix"], 0)
        self.assertEqual(DEFAULT_SETTINGS["v2_ember_back_opacity"], 58)
        self.assertEqual(DEFAULT_SETTINGS["v2_ember_blur_panel_strength"], 18)
        self.assertEqual(DEFAULT_SETTINGS["v2_ember_lyric_offset_seconds"], 0.0)
        self.assertEqual(DEFAULT_SETTINGS["v2_no_lyrics_display_mode"], "text")
        self.assertEqual(DEFAULT_SETTINGS["v2_album_island_cover_count"], 5)
        self.assertEqual(DEFAULT_SETTINGS["v2_album_island_cover_size"], 30)
        self.assertEqual(DEFAULT_SETTINGS["v2_album_island_compact_cover_size"], 30)
        self.assertEqual(DEFAULT_SETTINGS["v2_album_island_expanded_cover_size"], 72)
        self.assertEqual(DEFAULT_SETTINGS["v2_album_island_spacing"], 112)
        self.assertEqual(DEFAULT_SETTINGS["v2_album_island_secondary_style"], "perspective_dim")
        self.assertEqual(DEFAULT_SETTINGS["v2_album_island_perspective_amount"], 100)
        self.assertEqual(DEFAULT_SETTINGS["v2_album_island_listened_seconds_total"], 0.0)
        self.assertEqual(DEFAULT_SETTINGS["v2_album_island_history"], [])
        self.assertTrue(DEFAULT_SETTINGS["v2_compact_lyric_show_cover"])
        self.assertEqual(DEFAULT_SETTINGS["v2_dual_line_secondary_source"], "translation")
        self.assertEqual(DEFAULT_SETTINGS["v2_lyric_chinese_conversion"], "off")
        self.assertIn("sea_level", DEFAULT_SETTINGS["v2_music_ambience_values"])
        self.assertNotIn("bilateral_diffraction", DEFAULT_SETTINGS["v2_music_ambience_values"])
        self.assertNotIn("yaoguang", DEFAULT_SETTINGS["v2_music_ambience_values"])
        self.assertIn("solid_album", DEFAULT_SETTINGS["v2_music_ambience_values"])
        self.assertEqual(DEFAULT_SETTINGS["v2_solid_album_background_fallback"], "#16BCFF")
        self.assertFalse(DEFAULT_SETTINGS["v2_music_reactive_background_enabled"])
        self.assertEqual(DEFAULT_SETTINGS["v2_music_reactive_background_intensity"], 55)

    def test_music_surface_reading_lift_level_has_four_saved_choices(self):
        from local.settings_store import DEFAULT_SETTINGS
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        settings = FakeSettingsManager()
        self.assertEqual(DEFAULT_SETTINGS["v2_music_surface_reading_lift_level"], 2)
        self.assertNotIn("v2_fullscreen_reading_effect", DEFAULT_SETTINGS)
        window = SettingsWindow(island, settings)
        try:
            combo = window.music_surface_reading_lift_level
            self.assertEqual([combo.itemData(index) for index in range(combo.count())], [0, 1, 2, 4])
            self.assertEqual(combo.currentData(), 2)
            self.assertNotIn("渐进高亮", [combo.itemText(index) for index in range(combo.count())])
            for index, level in enumerate((0, 1, 2, 4)):
                with self.subTest(level=level):
                    combo.setCurrentIndex(index)
                    self.assertEqual(settings.get("v2_music_surface_reading_lift_level"), level)
                    self.assertEqual(island.music_surface_reading_lift_level, level)
        finally:
            window.close()

    def test_music_surface_reading_strategy_is_saved_and_defaults_to_gold(self):
        from local.settings_store import DEFAULT_SETTINGS
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        settings = FakeSettingsManager()
        self.assertEqual(DEFAULT_SETTINGS["v2_music_surface_reading_strategy"], "gold")
        window = SettingsWindow(island, settings)
        try:
            combo = window.music_surface_reading_strategy
            self.assertEqual(
                [combo.itemData(index) for index in range(combo.count())],
                ["gold", "low_perception"],
            )
            self.assertEqual(combo.currentData(), "gold")
            combo.setCurrentIndex(1)
            self.assertEqual(settings.get("v2_music_surface_reading_strategy"), "low_perception")
            self.assertEqual(island.music_surface_reading_strategy, "low_perception")
        finally:
            window.close()

    def test_music_surface_reading_strategy_normalizes_invalid_saved_values(self):
        from local import settings_store

        with tempfile.TemporaryDirectory() as temporary:
            settings_file = Path(temporary) / "settings.json"
            with mock.patch.object(settings_store, "SETTINGS_FILE", str(settings_file)), \
                 mock.patch.object(settings_store, "APP_DATA_DIR", temporary):
                settings_file.write_text(json.dumps({
                    "v2_music_surface_reading_strategy": "unexpected",
                }), encoding="utf-8")
                settings = settings_store.SettingsManager()
                self.assertEqual(settings.get("v2_music_surface_reading_strategy"), "gold")
                settings.set("v2_music_surface_reading_strategy", "low_perception")
                self.assertEqual(settings.get("v2_music_surface_reading_strategy"), "low_perception")
                settings.set("v2_music_surface_reading_strategy", "unexpected")
                self.assertEqual(settings.get("v2_music_surface_reading_strategy"), "gold")

    def test_music_surface_lift_level_normalizes_disk_values_and_removes_old_effect(self):
        from local import settings_store

        with tempfile.TemporaryDirectory() as temporary:
            settings_file = Path(temporary) / "settings.json"
            with mock.patch.object(settings_store, "SETTINGS_FILE", str(settings_file)), \
                 mock.patch.object(settings_store, "APP_DATA_DIR", temporary):
                for raw, expected in (("bad", 2), (None, 2), (-1, 2), (1.5, 2), (3, 2), (True, 2), ("4", 4), (0, 0)):
                    with self.subTest(raw=raw):
                        settings_file.write_text(json.dumps({
                            "v2_fullscreen_reading_effect": "highlight",
                            "v2_music_surface_reading_lift_level": raw,
                        }), encoding="utf-8")
                        settings = settings_store.SettingsManager()
                        self.assertEqual(settings.get("v2_music_surface_reading_lift_level"), expected)
                        self.assertIsNone(settings.get("v2_fullscreen_reading_effect"))
                        settings.set("v2_music_surface_reading_lift_level", 1)
                        self.assertEqual(settings.get("v2_music_surface_reading_lift_level"), 1)
                        settings.set("v2_fullscreen_reading_effect", "highlight")
                        settings.save_now()
                        saved = json.loads(settings_file.read_text(encoding="utf-8"))
                        self.assertEqual(saved["v2_music_surface_reading_lift_level"], 1)
                        self.assertNotIn("v2_fullscreen_reading_effect", saved)
                settings_file.write_text(json.dumps({
                    "v2_fullscreen_reading_effect": "highlight",
                    "v2_lyric_font_size": 21,
                }), encoding="utf-8")
                settings = settings_store.SettingsManager()
                self.assertEqual(settings.get("v2_music_surface_reading_lift_level"), 2)
                self.assertEqual(settings.get("v2_lyric_font_size"), 21)
                settings.save_now()
                saved = json.loads(settings_file.read_text(encoding="utf-8"))
                self.assertEqual(saved["v2_music_surface_reading_lift_level"], 2)
                self.assertNotIn("v2_fullscreen_reading_effect", saved)

    def test_music_surface_reading_gradient_has_its_own_sidebar_page_and_saves(self):
        from local.settings_store import DEFAULT_SETTINGS
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        settings = FakeSettingsManager()
        window = SettingsWindow(island, settings)
        try:
            page_names = [page["title"] for page in window._settings_pages]
            self.assertEqual(page_names[2], "全屏歌词")
            self.assertEqual(DEFAULT_SETTINGS["v2_music_surface_reading_gradient_steps"], 30)
            slider = window.music_surface_reading_gradient_steps
            self.assertEqual((slider.minimum(), slider.maximum(), slider.value()), (0, 30, 30))
            self.assertIn("中文 2.0", slider._value_label.text())
            slider.setValue(0)
            self.assertEqual(settings.get("v2_music_surface_reading_gradient_steps"), 0)
            self.assertEqual(island.music_surface_reading_gradient_steps, 0)
            self.assertEqual(slider._value_label.text(), "无渐变")
            slider.setValue(15)
            self.assertEqual(settings.get("v2_music_surface_reading_gradient_steps"), 15)
            self.assertIn("中文 1.0", slider._value_label.text())
            slider.setValue(30)
            self.assertEqual(settings.get("v2_music_surface_reading_gradient_steps"), 30)
        finally:
            window.close()

    def test_music_surface_reading_gradient_normalizes_disk_and_set_values(self):
        from local import settings_store

        with tempfile.TemporaryDirectory() as temporary:
            settings_file = Path(temporary) / "settings.json"
            with mock.patch.object(settings_store, "SETTINGS_FILE", str(settings_file)), \
                 mock.patch.object(settings_store, "APP_DATA_DIR", temporary):
                for raw, expected in (("bad", 30), (None, 30), (-5, 0), (31, 30), ("12", 12)):
                    with self.subTest(raw=raw):
                        settings_file.write_text(json.dumps({
                            "v2_music_surface_reading_gradient_steps": raw,
                        }), encoding="utf-8")
                        settings = settings_store.SettingsManager()
                        self.assertEqual(settings.get("v2_music_surface_reading_gradient_steps"), expected)
                        settings.set("v2_music_surface_reading_gradient_steps", 0)
                        self.assertEqual(settings.get("v2_music_surface_reading_gradient_steps"), 0)
                        settings.save_now()
                        self.assertEqual(json.loads(settings_file.read_text(encoding="utf-8"))[
                            "v2_music_surface_reading_gradient_steps"
                        ], 0)

    def test_sparse_highlight_threshold_setting_uses_tenths_and_updates_preview(self):
        from local.settings_store import DEFAULT_SETTINGS
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        settings = FakeSettingsManager()
        self.assertEqual(DEFAULT_SETTINGS["v2_music_sparse_highlight_seconds_per_unit"], 1.5)
        window = SettingsWindow(island, settings)
        try:
            slider = window.music_sparse_highlight_seconds_per_unit
            self.assertEqual((slider.minimum(), slider.maximum(), slider.value()), (8, 20, 15))
            self.assertEqual(slider._value_label.text(), "1.5 秒")
            slider.setValue(8)
            self.assertEqual(settings.get("v2_music_sparse_highlight_seconds_per_unit"), 0.8)
            self.assertEqual(island.music_sparse_highlight_seconds_per_unit, 0.8)
            self.assertEqual(slider._value_label.text(), "0.8 秒")
            slider.setValue(20)
            self.assertEqual(settings.get("v2_music_sparse_highlight_seconds_per_unit"), 2.0)
            self.assertEqual(slider._value_label.text(), "2.0 秒")
        finally:
            window.close()

    def test_sparse_highlight_threshold_rejects_malformed_loaded_settings(self):
        from local.settings_window import SettingsWindow

        settings = FakeSettingsManager()
        settings.values["v2_music_sparse_highlight_seconds_per_unit"] = "not-a-number"
        window = SettingsWindow(FakeIslandWidget(), settings)
        try:
            self.assertEqual(window.music_sparse_highlight_seconds_per_unit.value(), 15)
            self.assertEqual(window.music_sparse_highlight_seconds_per_unit._value_label.text(), "1.5 秒")
        finally:
            window.close()

    def test_sparse_highlight_threshold_is_normalized_on_disk_load_and_set(self):
        from local import settings_store

        with tempfile.TemporaryDirectory() as temporary:
            settings_file = Path(temporary) / "settings.json"
            with mock.patch.object(settings_store, "SETTINGS_FILE", str(settings_file)), \
                 mock.patch.object(settings_store, "APP_DATA_DIR", temporary):
                for raw, expected in (("bad", 1.5), (None, 1.5), (0.5, 0.8), (2.5, 2.0)):
                    with self.subTest(raw=raw):
                        settings_file.write_text(json.dumps({
                            "v2_music_sparse_highlight_seconds_per_unit": raw,
                        }), encoding="utf-8")
                        settings = settings_store.SettingsManager()
                        self.assertEqual(settings.get("v2_music_sparse_highlight_seconds_per_unit"), expected)
                        settings.set("v2_music_sparse_highlight_seconds_per_unit", "1.8")
                        self.assertEqual(settings.get("v2_music_sparse_highlight_seconds_per_unit"), 1.8)
                        settings.save_now()
                        self.assertEqual(json.loads(settings_file.read_text(encoding="utf-8"))[
                            "v2_music_sparse_highlight_seconds_per_unit"
                        ], 1.8)

    def test_malformed_settings_document_uses_defaults_without_crashing(self):
        from local import settings_store

        with tempfile.TemporaryDirectory() as temporary:
            settings_file = Path(temporary) / "settings.json"
            settings_file.write_text("[]", encoding="utf-8")
            with mock.patch.object(settings_store, "SETTINGS_FILE", str(settings_file)), \
                 mock.patch.object(settings_store, "APP_DATA_DIR", temporary):
                settings = settings_store.SettingsManager()
            self.assertEqual(settings.get("v2_music_sparse_highlight_seconds_per_unit"), 1.5)

    def test_music_intro_cleanup_and_embedded_translation_share_existing_setting(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        settings = FakeSettingsManager()
        window = SettingsWindow(island, settings)
        try:
            self.assertTrue(window.music_intro_cleanup_checkbox.isChecked())
            window.music_intro_cleanup_checkbox.setChecked(False)
            window.music_intro_cleanup_checkbox.toggled.emit(False)
            self.assertFalse(settings.get("v2_music_intro_cleanup_enabled"))
            self.assertFalse(island.music_intro_cleanup_enabled)

            self.assertFalse(window.music_surface_translation_checkbox.isChecked())
            window.music_surface_translation_checkbox.setChecked(True)
            window.music_surface_translation_checkbox.toggled.emit(True)
            self.assertTrue(settings.get("v2_enable_dual_line_lyrics"))
            self.assertTrue(island.enable_dual_line_lyrics)
            self.assertTrue(window.dual_line_lyrics_checkbox.isChecked())
            self.assertTrue(window.gelv_dual_line_checkbox.isChecked())
        finally:
            window.close()

    def test_location_and_tasker_defaults_are_present(self):
        from local.settings_store import DEFAULT_SETTINGS

        self.assertEqual(DEFAULT_SETTINGS["v2_location_mode"], "top")
        self.assertEqual(DEFAULT_SETTINGS["v2_top_notch_height_mode"], "standard")
        self.assertEqual(DEFAULT_SETTINGS["v2_taskbar_corner_style"], "win11")
        self.assertEqual(DEFAULT_SETTINGS["v2_taskbar_left_handle_color"], "#8E8E93")
        self.assertFalse(DEFAULT_SETTINGS["v2_tasker_follow_taskbar_color"])
        self.assertEqual(DEFAULT_SETTINGS["v2_music_background_follow_previous_style"], "")
        self.assertEqual(DEFAULT_SETTINGS["v2_dual_line_font_size"], 12)
        self.assertIn("pure_white", DEFAULT_SETTINGS["v2_music_ambience_values"])
        self.assertEqual(DEFAULT_SETTINGS["v2_enabled_plugins"], ["time"])
        self.assertIn("v2_mydockfinder_path", DEFAULT_SETTINGS)
        self.assertEqual(DEFAULT_SETTINGS["v2_mydockfinder_listener_process_names"], ["dock.exe"])
        self.assertIn("v2_last_window_position", DEFAULT_SETTINGS)
        self.assertIn("v2_custom_icon_path", DEFAULT_SETTINGS)
        self.assertEqual(DEFAULT_SETTINGS["v2_custom_icon_path"], "")
        self.assertNotIn("v2_taskbar_recognition_threshold", DEFAULT_SETTINGS)
        self.assertNotIn("v2_taskbar_plugin_compact_style", DEFAULT_SETTINGS)
        self.assertNotIn("v2_taskbar_message_compact_style", DEFAULT_SETTINGS)
        self.assertNotIn("v2_taskbar_hint_text", DEFAULT_SETTINGS)
        self.assertNotIn("v2_taskbar_fusion_mode", DEFAULT_SETTINGS)

    def test_settings_window_scopes_gelv_controls_and_hides_global_font_selector(self):
        source = self._source("local/settings_window.py")

        self.assertIn("LYRIC_FLOW_STYLES", source)
        self.assertIn('"album_island"', source)
        self.assertIn('"ember_fade"', source)
        self.assertIn("明灭余烬", source)
        self.assertIn("格律宋散", source)
        self.assertIn("歌词读字", source)
        self.assertIn("NO_LYRICS_DISPLAY_MODES", source)
        self.assertIn("专辑岛模式", source)
        self.assertIn("_update_lyric_flow_controls", source)
        self.assertNotIn("GELV_FONT_CHOICES", source)
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
        self.assertNotIn("wave_color_fill_checkbox", source)
        self.assertNotIn("wave_word_fill_checkbox", source)
        self.assertIn("wave_font_weight", source)
        self.assertIn("v2_wave_font_weight", source)
        self.assertNotIn("v2_wave_word_fill_enabled", source)
        self.assertNotIn("v2_wave_color_fill_enabled", source)
        self.assertNotIn("v2_wave_color_fill_fallback", source)
        self.assertIn("dual_line_secondary_source", source)
        self.assertIn("v2_dual_line_secondary_source", source)
        self.assertIn("lyric_chinese_conversion", source)
        self.assertIn("v2_lyric_chinese_conversion", source)
        self.assertIn("双语显示/双行显示", source)
        self.assertNotIn("文字辉光", source)
        self.assertNotIn("LYRIC_GLOW_STYLES", source)
        self.assertIn("gelv_word_fill_checkbox", source)
        self.assertIn("v2_gelv_word_fill_enabled", source)
        self.assertIn("_ember_controls", source)
        self.assertIn("ember_word_fill_checkbox", source)
        self.assertIn("ember_color_fill_checkbox", source)
        self.assertIn("ember_color_fill_indicator", source)
        self.assertIn("ember_font_weight", source)
        self.assertIn("ember_font_size", source)
        self.assertIn("ember_line_mode", source)
        self.assertIn("ember_dual_line_checkbox", source)
        self.assertIn("ember_dual_line_indicator", source)
        self.assertIn("ember_italic_checkbox", source)
        self.assertIn("ember_show_cover_checkbox", source)
        self.assertIn("ember_back_gray_mix", source)
        self.assertIn("ember_color_back_gray_mix", source)
        self.assertIn("ember_back_opacity", source)
        self.assertIn("ember_blur_panel_strength", source)
        self.assertIn("ember_lyric_offset", source)
        self.assertIn("v2_ember_word_fill_enabled", source)
        self.assertIn("v2_ember_color_fill_enabled", source)
        self.assertNotIn("v2_ember_color_fill_fallback", source)
        self.assertIn("v2_ember_font_weight", source)
        self.assertIn("v2_ember_font_size", source)
        self.assertIn("v2_ember_line_mode", source)
        self.assertIn("v2_ember_dual_line_enabled", source)
        self.assertIn("v2_ember_italic_enabled", source)
        self.assertIn("v2_ember_show_cover", source)
        self.assertIn("v2_ember_back_gray_mix", source)
        self.assertIn("v2_ember_color_back_gray_mix", source)
        self.assertIn("v2_ember_back_opacity", source)
        self.assertIn("v2_ember_blur_panel_strength", source)
        self.assertIn("v2_ember_lyric_offset_seconds", source)
        self.assertIn("歌词炫彩", source)
        self.assertIn("黑色", source)
        self.assertIn("白色", source)
        self.assertNotIn("双侧衍射", source)
        self.assertNotIn("格律字体", source)
        self.assertNotIn("gelv_font_family = self._combo", source)
        self.assertNotIn("gelv_size_shift_checkbox", source)
        self.assertNotIn("格律字号变化", source)
        self.assertNotIn("v2_gelv_size_shift_enabled", source)
        self.assertNotIn("set_gelv_size_shift_enabled", source)
        self.assertIn("gelv_dual_line_checkbox", source)
        self.assertIn("gelv_dual_line_indicator", source)
        self.assertIn("wave_dual_line_indicator", source)
        self.assertIn("IslandStackVisual.GELV_FONT_FAMILY", source)
        self.assertNotIn('family = "Source Han Serif SC"', source)
        self.assertIn("格律双语显示", source)
        self.assertIn("同行歌词字号", source)
        self.assertIn("_detail_panel", source)
        self.assertIn("_collapse_detail_panels", source)
        self.assertIn("_update_music_ambience_controls", source)
        self.assertIn("v2_music_ambience_values", source)
        self.assertIn("v2_music_reactive_background_enabled", source)
        self.assertIn("music_reactive_background_checkbox", source)
        self.assertIn("music_reactive_background_intensity", source)
        self.assertIn("album_island_cover_size", source)
        self.assertIn("album_island_compact_cover_size", source)
        self.assertIn("album_island_expanded_cover_size", source)
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
        self.assertNotIn('self.lyric_offset = self._slider_row(c, "歌词偏移"', source)
        self.assertNotIn("self.lyric_offset.parentWidget().setVisible(not album_flow)", source)
        self.assertIn("self.no_lyrics_display_mode_row.setVisible(not album_flow)", source)
        self.assertIn("v2_album_island_cover_size", source)
        self.assertIn("v2_album_island_compact_cover_size", source)
        self.assertIn("v2_album_island_expanded_cover_size", source)
        self.assertIn("v2_album_island_spacing", source)
        self.assertIn("reset_album_island_record", source)
        self.assertIn("详细设置", source)
        self.assertIn("_wave_stream_controls", source)
        self.assertIn("_gelv_controls", source)
        self.assertIn('FONT_CHOICES = (\n        "Alibaba PuHuiTi",', source)
        self.assertIn('"JiangChengXieSong 900W"', source)
        self.assertIn("self.lyric_font_size = self._slider_row(wave_layout", source)
        self.assertNotIn("self.lyric_font_size = self._slider_row(c,", source)
        self.assertNotIn('self._row(c, "全局字体", font_combo)', source)
        self.assertNotIn("enable_dual_line_lyrics = False", source)
        self.assertIn("setVisible(gelv)", source)

    def test_media_page_uses_per_app_lyric_offsets_only(self):
        from local.settings_window import SettingsWindow

        source = self._source("local/settings_window.py")
        media_start = source.index("def _media_page")
        media_end = source.index("def _appearance_page", media_start)
        media_source = source[media_start:media_end]
        self.assertNotIn('self.lyric_offset = self._slider_row(c, "歌词偏移"', media_source)
        self.assertIn("_build_music_app_profile_panel", source)
        self.assertIn('"offset": offset_slider', source)
        self.assertIn("_set_music_app_profile_lyric_offset", source)

        window = SettingsWindow(FakeIslandWidget(), FakeSettingsManager())
        try:
            self.assertFalse(hasattr(window, "lyric_offset"))
            self.assertTrue(window.music_app_profile_controls)
            self.assertTrue(all("offset" in controls for controls in window.music_app_profile_controls.values()))
        finally:
            window.close()

    def test_music_app_profile_collapsed_headers_show_sync_status_indicator(self):
        from local.settings_window import SettingsWindow, StatusLamp

        settings = FakeSettingsManager()
        profiles = dict(settings.values["v2_music_app_profiles"])
        profiles["qq"] = dict(profiles["qq"], sync_enabled=True)
        settings.values["v2_music_app_profiles"] = profiles
        window = SettingsWindow(FakeIslandWidget(), settings)
        try:
            controls = window.music_app_profile_controls
            self.assertTrue(all("indicator" in row for row in controls.values()))
            self.assertTrue(all(isinstance(row["indicator"], StatusLamp) for row in controls.values()))
            self.assertTrue(controls["qq"]["indicator"]._active)
            self.assertFalse(controls["netease"]["indicator"]._active)
        finally:
            window.close()

    def test_active_app_profile_offset_applies_immediately_after_save(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        island.set_active_music_app_lyric_offset = mock.Mock()
        settings = FakeSettingsManager()
        window = SettingsWindow(island, settings)
        try:
            window._set_music_app_profile_lyric_offset("qq", 750)
            self.assertEqual(settings.get("v2_music_app_profiles")["qq"]["lyric_offset_seconds"], 0.75)
            island.set_active_music_app_lyric_offset.assert_called_once_with("qq", 0.75)
        finally:
            window.close()

    def test_app_loads_persistent_album_island_record_and_split_sizes(self):
        app_source = self._source("local/app.py")

        self.assertIn("load_embedded_lyric_fonts", app_source)
        self.assertIn("v2_album_island_listened_seconds_total", app_source)
        self.assertIn("v2_album_island_history", app_source)
        self.assertIn("v2_album_island_compact_cover_size", app_source)
        self.assertIn("v2_album_island_expanded_cover_size", app_source)

    def test_settings_window_exposes_location_mode_instead_of_old_taskbar_fusion_controls(self):
        source = self._source("local/settings_window.py")

        self.assertIn("LOCATION_MODES", source)
        self.assertIn("location_mode", source)
        self.assertIn("v2_location_mode", source)
        self.assertIn("taskbar_corner_style", source)
        self.assertIn("taskbar_auto_hide_mode", source)
        self.assertIn("v2_taskbar_auto_hide_mode", source)
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

        self.assertIn("COMPACT_ROW_VPAD = 10", source)
        self.assertIn("setMinimumSize(688, 496)", source)
        self.assertIn("WA_TranslucentBackground", source)
        self.assertIn("MacWindowShell", source)
        self.assertIn("border-radius: 14px", style)
        self.assertIn("border-radius: 10px", style)
        self.assertIn("min-height: 24px", style)
        self.assertIn("_apply_rounded_window_mask", source)
        self.assertIn("setMask", source)

    def test_basic_settings_page_uses_denser_local_layout(self):
        source = self._source("local/settings_window.py")

        self.assertIn("APPEARANCE_ROW_VPAD = 6", source)
        self.assertIn("_begin_compact_appearance_page", source)
        self.assertIn("_end_compact_appearance_page", source)
        self.assertIn("layout.setContentsMargins(20, 14, 24, 22)", source)
        self.assertIn("layout.setSpacing(14)", source)
        self.assertIn("layout.setContentsMargins(12, 7, 12, 7)", source)
        self.assertIn("layout.setContentsMargins(16, 10, 16, 10)", source)
        appearance_start = source.index("def _basic_settings_page")
        appearance_end = source.index("def _media_page", appearance_start)
        appearance_source = source[appearance_start:appearance_end]
        self.assertIn("self._begin_compact_appearance_page(page)", appearance_source)
        self.assertIn("self._end_compact_appearance_page()", appearance_source)
        self.assertIn("self.empty_island_display_mode = self._combo", appearance_source)
        self.assertIn("无岛显示", appearance_source)
        self.assertNotIn("self._preview_frame(22)", appearance_source)
        media_start = source.index("def _media_page")
        media_end = source.index("def _appearance_page", media_start)
        media_source = source[media_start:media_end]
        self.assertIn("MusicAmbiencePreview", media_source)

    def test_about_diagnostics_wrap_values_without_widening_panel(self):
        from local.settings_window import SettingsWindow

        window = SettingsWindow(FakeIslandWidget(), FakeSettingsManager())
        try:
            labels = list(window._diagnostic_labels.values())
            self.assertGreater(len(labels), 10)
            self.assertIn("visual_paint_hot_groups", window._diagnostic_labels)
            self.assertIn("visual_paint_hot_sections", window._diagnostic_labels)
            self.assertIn("music_reactive_input", window._diagnostic_labels)
            self.assertIn("runtime_health", window._diagnostic_labels)
            self.assertIn("runtime_bottleneck", window._diagnostic_labels)
            self.assertIn("runtime_trace", window._diagnostic_labels)
            self.assertIn("lyric_timeline_lag", window._diagnostic_labels)
            self.assertTrue(all(label.objectName() == "DiagnosticValue" for label in labels))
            self.assertTrue(all(label.wordWrap() for label in labels))
            self.assertTrue(
                all(label.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored for label in labels)
            )
        finally:
            window.close()

    def test_settings_traffic_lights_show_hover_symbols(self):
        style = self._source("local/mac_style.py")

        self.assertIn("_symbol", style)
        self.assertIn("enterEvent", style)
        self.assertIn("leaveEvent", style)
        self.assertIn("drawText", style)
        self.assertIn('"×"', style)
        self.assertIn('"−"', style)
        self.assertIn('"□"', style)

    def test_settings_traffic_lights_use_capsule_backplate(self):
        style = self._source("local/mac_style.py")

        self.assertIn("MacTrafficCapsule", style)
        self.assertIn("setFixedSize(10, 10)", style)
        self.assertIn("setFixedSize(48, 18)", style)
        self.assertIn("drawRoundedRect", style)
        self.assertIn("drawEllipse", style)
        self.assertIn("layout.setContentsMargins(5, 4, 5, 4)", style)
        self.assertIn("layout.setSpacing(4)", style)
        self.assertNotIn("QLinearGradient", style)

    def test_settings_status_indicators_are_lamp_dots(self):
        source = self._source("local/settings_window.py")

        self.assertIn("StatusLamp", source)
        self.assertIn("setFixedSize(8, 8)", source)
        self.assertIn("_paint_lamp", source)
        self.assertIn("radial", source)
        self.assertIn("gleam", source)
        self.assertIn("_status_indicator", source)
        self.assertIn("_indicator_row", source)
        self.assertIn("grid = QGridLayout(row_widget)", source)
        self.assertIn("grid.addWidget(indicator, 0, 0", source)
        self.assertIn("grid.setColumnMinimumWidth(0, 12)", source)
        self.assertIn('self._indicator_row(p, "MyDockFinder 监听"', source)
        self.assertIn('self._indicator_row(p, "Tasker 圆角"', source)
        self.assertIn('self._indicator_row(p, "Tasker 左侧竖线"', source)

    def test_gelv_color_fill_uses_status_lamp_and_duotone_unavailable_state(self):
        source = self._source("local/settings_window.py")

        self.assertIn("gelv_color_fill_indicator", source)
        self.assertIn('self._indicator_row(gelv_layout, "歌词炫彩"', source)
        self.assertIn("gelv_word_fill_checkbox", source)
        self.assertIn("_set_gelv_word_fill_enabled", source)
        self.assertIn("_gelv_color_fill_available_for_settings", source)
        self.assertIn('COLOR_FILL_BLOCKED_BACKGROUND_STYLES = {"sea_level", "duotone_full", "solid_album"}', source)
        self.assertIn("self._set_status_indicator(", source)
        self.assertIn("self.gelv_color_fill_indicator,", source)
        self.assertIn("gelv_color_fill_available", source)

    def test_lyric_read_word_and_colorful_labels_are_consistent(self):
        from local.settings_window import SettingsWindow

        window = SettingsWindow(FakeIslandWidget(), FakeSettingsManager())
        try:
            self.assertEqual(window.gelv_word_fill_checkbox.text(), "歌词读字")
            self.assertEqual(window.ember_word_fill_checkbox.text(), "歌词读字")
            self.assertFalse(hasattr(window, "wave_word_fill_checkbox"))
            self.assertFalse(hasattr(window, "wave_color_fill_checkbox"))
            self.assertFalse(hasattr(window, "gelv_color_fill_fallback"))
            self.assertFalse(hasattr(window, "ember_color_fill_fallback"))
            self.assertFalse(hasattr(window, "wave_color_fill_fallback"))

            gelv_labels = [label.text() for label in window.gelv_color_fill_checkbox.parentWidget().findChildren(QLabel)]
            ember_labels = [label.text() for label in window.ember_color_fill_checkbox.parentWidget().findChildren(QLabel)]
            wave_labels = [label.text() for label in window._wave_stream_controls.findChildren(QLabel)]
            self.assertIn("歌词炫彩", gelv_labels)
            self.assertIn("歌词炫彩", ember_labels)
            self.assertNotIn("歌词炫彩", wave_labels)
            self.assertNotIn("歌词读字", wave_labels)
        finally:
            window.close()

    def test_gelv_colorful_is_blocked_on_colored_backgrounds_but_allowed_on_pure_black(self):
        from local.settings_window import SettingsWindow

        for background in ("sea_level", "duotone_full"):
            island = FakeIslandWidget()
            island.lyric_flow_style = "gelv_songscatter"
            island.music_ambience_style = background
            island.gelv_color_fill_enabled = True
            settings = FakeSettingsManager()
            settings.values["v2_gelv_color_fill_enabled"] = True
            window = SettingsWindow(island, settings)
            try:
                self.assertFalse(window.gelv_color_fill_indicator._active)
                self.assertTrue(window.gelv_color_fill_checkbox.isEnabled())
                self.assertTrue(window.gelv_color_fill_checkbox.isChecked())
                self.assertTrue(island.gelv_color_fill_enabled)
                self.assertTrue(settings.values["v2_gelv_color_fill_enabled"])
                self.assertEqual(window.gelv_color_fill_checkbox.unavailable_reason(), "")
                self.assertIn("海平面", window.gelv_color_fill_indicator._text)
                self.assertIn("整岛双色", window.gelv_color_fill_indicator._text)
            finally:
                window.close()

        island = FakeIslandWidget()
        island.lyric_flow_style = "gelv_songscatter"
        island.music_ambience_style = "pure_black"
        window = SettingsWindow(island, FakeSettingsManager())
        try:
            self.assertTrue(window.gelv_color_fill_indicator._active)
            self.assertTrue(window.gelv_color_fill_checkbox.isEnabled())
            self.assertEqual(window.gelv_color_fill_checkbox.unavailable_reason(), "")
        finally:
            window.close()

    def test_wave_has_no_legacy_animation_details_and_keeps_font_and_bilingual_choices(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        island.lyric_flow_style = "wave_stream"
        island.wave_font_weight = "heavy"
        island.enable_dual_line_lyrics = True
        settings = FakeSettingsManager()
        settings.values["v2_wave_font_weight"] = "heavy"
        window = SettingsWindow(island, settings)
        try:
            for name in (
                "_wave_stream_detail_toggle", "wave_word_fill_checkbox", "wave_color_fill_checkbox",
                "wave_word_fill_indicator", "wave_color_fill_indicator", "lyric_elastic_strength",
                "lyric_elastic_mode", "lyric_word_spacing",
            ):
                self.assertFalse(hasattr(window, name), name)
            self.assertEqual(window.wave_font_weight.currentData(), "heavy")
            self.assertTrue(window.dual_line_lyrics_checkbox.isChecked())
            self.assertTrue(window.dual_line_secondary_source.isEnabled())
            self.assertTrue(window.dual_line_font_size.isEnabled())
            self.assertTrue(hasattr(window, "wave_compact_cover_checkbox"))
            window._set_combo_data(window.wave_font_weight, "bold")
            window._set_wave_font_weight()
            self.assertEqual(island.wave_font_weight, "bold")
            self.assertEqual(settings.values["v2_wave_font_weight"], "bold")
            window.sync_from_widget()
            self.assertEqual(window.wave_font_weight.currentData(), "bold")
        finally:
            window.close()

    def test_ember_color_fill_blocks_colorful_backgrounds_but_allows_neutral_backgrounds(self):
        from local.settings_window import SettingsWindow

        for background in ("sea_level", "duotone_full", "solid_album"):
            island = FakeIslandWidget()
            island.lyric_flow_style = "ember_fade"
            island.music_ambience_style = background
            island.ember_color_fill_enabled = True
            settings = FakeSettingsManager()
            settings.values["v2_ember_color_fill_enabled"] = True
            window = SettingsWindow(island, settings)
            try:
                self.assertFalse(window.ember_color_fill_indicator._active)
                self.assertTrue(window.ember_color_fill_checkbox.isEnabled())
                self.assertTrue(window.ember_color_fill_checkbox.isChecked())
                self.assertTrue(island.ember_color_fill_enabled)
                self.assertTrue(settings.values["v2_ember_color_fill_enabled"])
                self.assertEqual(window.ember_color_fill_checkbox.unavailable_reason(), "")
                self.assertTrue(window.ember_word_fill_checkbox.isEnabled())
            finally:
                window.close()

        for background in ("pure_black", "pure_white"):
            island = FakeIslandWidget()
            island.lyric_flow_style = "ember_fade"
            island.music_ambience_style = background
            window = SettingsWindow(island, FakeSettingsManager())
            try:
                self.assertTrue(window.ember_color_fill_indicator._active)
                self.assertTrue(window.ember_color_fill_checkbox.isEnabled())
                self.assertEqual(window.ember_color_fill_checkbox.unavailable_reason(), "")
            finally:
                window.close()

    def test_ember_detail_sliders_include_opacity_blur_and_positive_offset_range(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        island.lyric_flow_style = "ember_fade"
        settings = FakeSettingsManager()
        window = SettingsWindow(island, settings)
        try:
            self.assertEqual(window.ember_back_opacity.minimum(), 0)
            self.assertEqual(window.ember_back_opacity.maximum(), 100)
            self.assertEqual(window.ember_blur_panel_strength.minimum(), 0)
            self.assertEqual(window.ember_blur_panel_strength.maximum(), 100)
            self.assertEqual(window.ember_font_size.minimum(), 12)
            self.assertEqual(window.ember_font_size.maximum(), 24)
            self.assertEqual(window.ember_lyric_offset.minimum(), 0)
            self.assertEqual(window.ember_lyric_offset.maximum(), 4000)

            window._set_ember_font_size(22)
            self.assertEqual(settings.values["v2_ember_font_size"], 22)
            self.assertEqual(island.ember_font_size, 22)

            window._set_ember_lyric_offset(-250)
            self.assertEqual(settings.values["v2_ember_lyric_offset_seconds"], 0.0)
            self.assertEqual(island.ember_lyric_offset_seconds, 0.0)

            window._set_ember_lyric_offset(5000)
            self.assertEqual(settings.values["v2_ember_lyric_offset_seconds"], 4.0)
            self.assertEqual(island.ember_lyric_offset_seconds, 4.0)
        finally:
            window.close()

    def test_ember_detail_modes_dual_line_italic_and_cover_settings(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        island.lyric_flow_style = "ember_fade"
        settings = FakeSettingsManager()
        window = SettingsWindow(island, settings)
        try:
            self.assertEqual(window.ember_line_mode.currentData(), "split")
            self.assertTrue(window.ember_line_mode.isEnabled())
            self.assertTrue(window.ember_show_cover_checkbox.isChecked())

            window.ember_line_mode.blockSignals(True)
            window.ember_line_mode.setCurrentIndex(window.ember_line_mode.findData("scroll"))
            window.ember_line_mode.blockSignals(False)
            window._set_ember_line_mode()
            self.assertEqual(settings.values["v2_ember_line_mode"], "scroll")
            self.assertEqual(island.ember_line_mode, "scroll")

            window._set_ember_italic_enabled(True)
            self.assertTrue(settings.values["v2_ember_italic_enabled"])
            self.assertTrue(island.ember_italic_enabled)

            window._set_ember_show_cover(False)
            self.assertFalse(settings.values["v2_ember_show_cover"])
            self.assertFalse(island.ember_show_cover)

            window._set_ember_dual_line_enabled(True)
            self.assertTrue(settings.values["v2_ember_dual_line_enabled"])
            self.assertEqual(settings.values["v2_ember_line_mode"], "scroll")
            self.assertFalse(window.ember_line_mode.isEnabled())
            self.assertIn("双语", window.ember_dual_line_indicator.toolTip())
        finally:
            window.close()

    def test_ember_colored_background_gray_control_is_available_on_color_backgrounds(self):
        from local.settings_window import SettingsWindow

        for background in ("sea_level", "duotone_full", "solid_album"):
            island = FakeIslandWidget()
            island.lyric_flow_style = "ember_fade"
            island.music_ambience_style = background
            window = SettingsWindow(island, FakeSettingsManager())
            try:
                self.assertTrue(window.ember_color_back_gray_mix.isEnabled())
            finally:
                window.close()

        island = FakeIslandWidget()
        island.lyric_flow_style = "ember_fade"
        island.music_ambience_style = "pure_black"
        window = SettingsWindow(island, FakeSettingsManager())
        try:
            self.assertFalse(window.ember_color_back_gray_mix.isEnabled())
        finally:
            window.close()

    def test_lyric_display_choices_request_media_refresh_once(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        settings = FakeSettingsManager()
        window = SettingsWindow(island, settings)
        try:
            island.media_refresh_calls.clear()
            window._set_dual_line_lyrics(True)
            self.assertEqual(island.media_refresh_calls, ["provider"])

            island.media_refresh_calls.clear()
            window.dual_line_secondary_source.blockSignals(True)
            window.dual_line_secondary_source.setCurrentIndex(1)
            window.dual_line_secondary_source.blockSignals(False)
            window._set_dual_line_secondary_source()
            self.assertEqual(settings.values["v2_dual_line_secondary_source"], "transliteration")
            self.assertEqual(island.media_refresh_calls, ["provider"])

            island.media_refresh_calls.clear()
            window.lyric_chinese_conversion.blockSignals(True)
            window.lyric_chinese_conversion.setCurrentIndex(1)
            window.lyric_chinese_conversion.blockSignals(False)
            window._set_lyric_chinese_conversion()
            self.assertEqual(settings.values["v2_lyric_chinese_conversion"], "simplified")
            self.assertEqual(island.media_refresh_calls, ["provider"])
        finally:
            window.close()

    def test_settings_page_uses_smooth_capsule_scroll_area(self):
        source = self._source("local/settings_window.py")
        style = self._source("local/mac_style.py")

        self.assertIn("SmoothScrollArea", source)
        self.assertIn("QEasingCurve.Type.OutQuint", source)
        self.assertIn("ScrollPerPixel", source)
        self.assertIn("ScrollBarAlwaysOn", source)
        self.assertIn("QScrollBar:vertical", style)
        self.assertIn("width: 12px", style)
        self.assertIn("border-radius: 5px", style)
        self.assertIn("QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical", style)

    def test_detail_panels_have_distinct_nested_surface(self):
        from local.settings_window import SettingsWindow

        style = self._source("local/mac_style.py")
        self.assertIn("QFrame#DetailBody", style)
        self.assertIn("detail_body", style)
        self.assertIn("section_text", style)
        self.assertIn("QPushButton#DetailToggle:checked", style)

        window = SettingsWindow(FakeIslandWidget(), FakeSettingsManager())
        try:
            bodies = window.findChildren(QFrame, "DetailBody")
            self.assertGreater(len(bodies), 0)
            first_toggle, first_body = window._detail_panels[0]
            self.assertFalse(first_body.isVisible())
            first_toggle.setChecked(True)
            self.assertFalse(first_body.isHidden())
        finally:
            window.close()

    def test_settings_value_controls_ignore_wheel_changes(self):
        source = self._source("local/settings_window.py")
        widgets = self._source("local/no_wheel_widgets.py")

        self.assertIn("NoWheelSlider", widgets)
        self.assertIn("from local.no_wheel_widgets import NoWheelComboBox, NoWheelSlider, NoWheelSpinBox", source)
        self.assertIn("slider = NoWheelSlider(Qt.Orientation.Horizontal)", source)

    def test_settings_exposes_empty_display_modes_instead_of_stream_palette(self):
        source = self._source("local/settings_window.py")
        defaults = self._source("local/settings_store.py")

        self.assertIn("v2_empty_island_display_mode", defaults)
        self.assertIn("v2_empty_island_display_mode", source)
        self.assertIn("v2_no_music_island_priority", defaults)
        self.assertIn("v2_no_music_island_priority", source)
        self.assertIn("no_music_island_priority", source)
        self.assertIn("无音乐岛时优先", source)
        self.assertIn("无岛显示", source)
        self.assertIn("visible_bar", source)
        self.assertIn("text", source)
        self.assertIn("time", source)
        self.assertNotIn("hidden_bar", source)
        self.assertNotIn('"v2_empty_island_display_mode": "hidden_bar"', defaults)
        self.assertIn("empty_time_seconds_checkbox", source)
        self.assertIn("empty_time_seconds_row", source)
        self.assertIn("_update_empty_island_time_seconds_visibility", source)
        self.assertIn('self._row(ep, "时间显秒"', source)
        self.assertIn('mode == "time"', source)
        self.assertNotIn("空态流光设置", source)
        self.assertNotIn("EMPTY_BAR_PALETTES", source)
        self.assertNotIn("_empty_bar_preview_colors", source)
        self.assertNotIn("self.empty_palette", source)

    def test_time_plugin_exposes_seconds_toggle_shared_with_empty_time(self):
        source = self._source("local/settings_window.py")

        self.assertIn("time_seconds_checkbox", source)
        self.assertIn('self._row(t, "时间显秒"', source)
        self.assertIn("empty_time_seconds_checkbox", source)
        self.assertIn('("seconds_checkbox", "time_seconds_checkbox", "empty_time_seconds_checkbox")', source)

    def test_settings_search_displays_ranked_content_results_and_predicts_terms(self):
        from local.settings_window import SettingsWindow

        window = SettingsWindow(FakeIslandWidget(), FakeSettingsManager())
        try:
            normal_nav = [window.nav.item(index).text() for index in range(window.nav.count())]

            window.search_input.setText("时间显秒")
            self.assertIs(window.stack.currentWidget(), window.settings_search_page)
            self.assertEqual([window.nav.item(index).text() for index in range(window.nav.count())], normal_nav)
            result_titles = [
                label.text()
                for label in window.settings_search_page.findChildren(QLabel)
                if label.objectName() == "SearchResultTitle"
            ]
            self.assertGreater(len(result_titles), 0)
            self.assertEqual(result_titles[0], "时间显秒")

            completer = window.search_input.completer()
            suggestions = [
                completer.model().index(row, 0).data()
                for row in range(completer.model().rowCount())
            ]
            self.assertIn("时间显秒", suggestions)

            window.search_input.setText("时秒")
            fuzzy_titles = [
                label.text()
                for label in window.settings_search_page.findChildren(QLabel)
                if label.objectName() == "SearchResultTitle"
            ]
            self.assertIn("时间显秒", fuzzy_titles)

            window.search_input.setText("无音频隐藏")
            hide_titles = [
                label.text()
                for label in window.settings_search_page.findChildren(QLabel)
                if label.objectName() == "SearchResultTitle"
            ]
            self.assertGreater(len(hide_titles), 0)
            self.assertEqual(hide_titles[0], "音乐岛自动识别：无音频时隐藏")

            window.search_input.clear()
            self.assertIsNot(window.stack.currentWidget(), window.settings_search_page)
            self.assertEqual([window.nav.item(index).text() for index in range(window.nav.count())][:4], ["基础设置", "媒体控制", "全屏歌词", "插件设置"])
        finally:
            window.close()

    def test_tasker_mode_marks_dual_line_lyrics_unavailable_in_settings(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        island.location_mode = "tasker"
        island.enable_dual_line_lyrics = True
        window = SettingsWindow(island, FakeSettingsManager())
        try:
            self.assertFalse(window.wave_dual_line_indicator._active)
            self.assertFalse(window.gelv_dual_line_indicator._active)
            self.assertFalse(window.dual_line_lyrics_checkbox.isEnabled())
            self.assertFalse(window.gelv_dual_line_checkbox.isEnabled())
            self.assertIn("Tasker", window.wave_dual_line_indicator.toolTip())
            self.assertIn("Tasker", window.gelv_dual_line_indicator.toolTip())
        finally:
            window.close()

    def test_tasker_theme_mode_changes_sync_music_background(self):
        from local.settings_window import SettingsWindow
        from local.settings_store import DEFAULT_SETTINGS

        self.assertIn("v2_music_background_follow_theme", DEFAULT_SETTINGS)
        self.assertIn("v2_music_background_follow_previous_style", DEFAULT_SETTINGS)
        island = FakeIslandWidget()
        island.music_ambience_style = "pure_black"
        settings = FakeSettingsManager()
        settings.values["v2_music_background_follow_theme"] = True
        settings.values["v2_theme_mode"] = "light"
        settings.values["v2_music_ambience_style"] = "pure_black"
        window = SettingsWindow(island, settings)
        try:
            self.assertTrue(hasattr(window, "music_background_follow_theme_checkbox"))
            window._sync_music_background_to_theme()
            self.assertEqual(island.music_ambience_style, "pure_white")
            self.assertEqual(settings.values["v2_music_ambience_style"], "pure_white")
            self.assertEqual(settings.values["v2_music_background_follow_previous_style"], "pure_black")

            settings.values["v2_theme_mode"] = "dark"
            window._sync_music_background_to_theme()
            self.assertEqual(island.music_ambience_style, "pure_black")
            self.assertEqual(settings.values["v2_music_background_follow_previous_style"], "")

            island.music_ambience_style = "sea_level"
            settings.values["v2_music_ambience_style"] = "sea_level"
            settings.values["v2_theme_mode"] = "light"
            window._sync_music_background_to_theme()
            self.assertEqual(island.music_ambience_style, "pure_white")
            self.assertEqual(settings.values["v2_music_background_follow_previous_style"], "sea_level")
            settings.values["v2_theme_mode"] = "dark"
            window._sync_music_background_to_theme()
            self.assertEqual(island.music_ambience_style, "sea_level")
        finally:
            window.close()

    def test_music_background_follow_theme_defaults_tasker_follow_and_warns_when_disabled(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        settings = FakeSettingsManager()
        settings.values["v2_music_background_follow_theme"] = False
        settings.values["v2_tasker_follow_taskbar_color"] = False
        window = SettingsWindow(island, settings)
        try:
            window._set_music_background_follow_theme(True)
            self.assertTrue(settings.values["v2_music_background_follow_theme"])
            self.assertTrue(settings.values["v2_tasker_follow_taskbar_color"])
            self.assertTrue(island.tasker_follow_taskbar_color)

            window._set_tasker_follow_taskbar_color(False)
            self.assertFalse(settings.values["v2_tasker_follow_taskbar_color"])
            hints = getattr(window, "tasker_follow_taskbar_color_hint_labels", [])
            self.assertGreater(len(hints), 0)
            self.assertTrue(any(not hint.isHidden() for hint in hints))

            window._set_music_background_follow_theme(False)
            self.assertFalse(settings.values["v2_music_background_follow_theme"])
            self.assertTrue(all(hint.isHidden() for hint in hints))
        finally:
            window.close()

    def test_theme_schedule_time_rows_only_show_for_scheduled_theme(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        settings = FakeSettingsManager()
        settings.values["v2_theme_mode"] = "light"
        window = SettingsWindow(island, settings)
        try:
            rows = getattr(window, "theme_dark_time_rows", [])
            self.assertGreaterEqual(len(rows), 2)
            self.assertTrue(all(row.isHidden() for row in rows))

            window._set_combo_data(window.theme_mode, "schedule")
            window._set_theme_mode()
            self.assertTrue(all(not row.isHidden() for row in rows))

            window._set_combo_data(window.theme_mode, "dark")
            window._set_theme_mode()
            self.assertTrue(all(row.isHidden() for row in rows))
        finally:
            window.close()

    def test_gelv_color_fill_keeps_all_music_background_options_available(self):
        source = self._source("local/settings_window.py")

        self.assertIn("_music_background_options_allowed", source)
        self.assertIn("set(DEFAULT_MUSIC_AMBIENCE_VALUES)", source)
        self.assertNotIn('{"pure_black", "bilateral_diffraction", "pure_white"} if active_fill', source)

    def test_location_mode_options_reflect_manual_finder_state(self):
        source = self._source("local/settings_window.py")

        self.assertIn("mydockfinder_running", source)
        self.assertIn('("Free", "free"), ("Finder", "finder")', source)
        self.assertIn('("Top", "top"), ("Free", "free"), ("Tasker", "tasker")', source)
        self.assertIn("_refresh_location_mode_options", source)

    def test_taskbar_auto_hide_mode_setting_is_persisted_and_applied(self):
        source = self._source("local/settings_window.py")
        app_source = self._source("local/app.py")
        store_source = self._source("local/settings_store.py")

        self.assertIn("TASKBAR_AUTO_HIDE_MODES", source)
        self.assertIn("_set_taskbar_auto_hide_mode", source)
        self.assertIn("v2_taskbar_auto_hide_mode", source)
        self.assertIn("set_taskbar_auto_hide_mode", app_source)
        self.assertIn('"v2_taskbar_auto_hide_mode": "auto"', store_source)

    def test_stick_location_mode_is_preserved_as_internal_drag_state(self):
        settings_source = self._source("local/settings_window.py")
        app_source = self._source("local/app.py")

        self.assertIn('{"finder", "free", "top", "tasker", "stick"}', app_source)
        self.assertIn('current == "stick"', settings_source)
        self.assertIn('("Stick", "stick")', settings_source)
        self.assertIn('("Top", "top")', settings_source)

    def test_top_notch_height_mode_setting_is_persisted_and_applied(self):
        from local.settings_window import SettingsWindow

        source = self._source("local/settings_window.py")
        self.assertIn("Top/Stick 高度", source)
        island = FakeIslandWidget()
        settings = FakeSettingsManager()
        window = SettingsWindow(island, settings)
        try:
            self.assertTrue(hasattr(window, "top_notch_height_mode"))
            window._set_combo_data(window.top_notch_height_mode, "spacious")
            window._set_top_notch_height_mode()

            self.assertEqual(island.top_notch_height_mode, "spacious")
            self.assertEqual(settings.values["v2_top_notch_height_mode"], "spacious")
        finally:
            window.close()

    def test_weather_and_agenda_plugins_are_enabled_interfaces(self):
        source = self._source("local/settings_window.py")
        app_source = self._source("local/app.py")
        provider_source = self._source("api/plugin_provider.py")

        self.assertIn('known = ["time", "weather", "agenda"]', source)
        self.assertIn('known = ["time", "weather", "agenda"]', app_source)
        self.assertIn('enabled.append("weather")', source)
        self.assertIn('enabled.append("agenda")', source)
        self.assertIn('if "weather" in enabled:', provider_source)
        self.assertIn('if "agenda" in enabled:', provider_source)
        self.assertNotIn("天气插件（待开发）", source)
        self.assertNotIn("日程插件（待开发）", source)
        self.assertNotIn("weather_card.setEnabled(False)", source)
        self.assertNotIn("agenda_card.setEnabled(False)", source)

    def test_settings_exposes_mydockfinder_listener_path(self):
        source = self._source("local/settings_window.py")
        app_source = self._source("local/app.py")

        self.assertIn("MyDockFinder 监听", source)
        self.assertIn("MyDockFinder 监听进程", source)
        self.assertIn("mydockfinder_process_checkboxes", source)
        self.assertIn("_set_mydockfinder_listener_process", source)
        self.assertIn("v2_mydockfinder_listener_process_names", source)
        self.assertIn("v2_mydockfinder_listener_process_names", app_source)
        self.assertIn("_status_indicator", source)
        self.assertIn("mydockfinder_path_input", source)
        self.assertIn("_set_mydockfinder_path", source)
        self.assertIn("v2_mydockfinder_path", source)
        self.assertIn("_setup_mydockfinder_listener", app_source)
        self.assertIn("_poll_mydockfinder_presence", app_source)
        self.assertIn("set_mydockfinder_running", app_source)
        self.assertIn("mydockfinder_status_label", source)
        self.assertIn("_mydockfinder_status_text", source)
        self.assertIn("_mydockfinder_process_diagnostics", app_source)

    def test_tasker_controls_are_gated_by_mydockfinder_indicator(self):
        source = self._source("local/settings_window.py")

        self.assertIn("taskbar_corner_status", source)
        self.assertIn("taskbar_left_handle_status", source)
        self.assertIn("mydockfinder_running", source)
        self.assertIn("not mydockfinder_running", source)

    def test_position_page_exposes_explicit_position_presets_without_auto_snap(self):
        source = self._source("local/settings_window.py")
        visual_source = self._source("local/island_stack_visual.py")

        self.assertIn("POSITION_PRESETS", source)
        self.assertIn("position_preset", source)
        self.assertIn("_set_position_preset", source)
        self.assertIn("apply_position_preset", visual_source)
        self.assertNotIn("_snap_top_center_window_pos", visual_source)

    def test_app_flushes_visual_position_snapshot_before_quit(self):
        from local.app import LocalIslandApp

        class Settings:
            def __init__(self):
                self.values = {}
                self.saved = False

            def set(self, key, value):
                self.values[key] = value

            def save_now(self):
                self.saved = True

        app = LocalIslandApp.__new__(LocalIslandApp)
        app.settings = Settings()
        app.window = type(
            "Window",
            (),
            {"settings_snapshot": lambda self: {"v2_last_window_position": {"x": 432, "y": 210}}},
        )()

        app._flush_visual_state_now()

        self.assertEqual(app.settings.values["v2_last_window_position"], {"x": 432, "y": 210})
        self.assertTrue(app.settings.saved)

    def test_path_settings_expose_file_picker_buttons(self):
        source = self._source("local/settings_window.py")

        self.assertIn("_path_picker_row", source)
        self.assertIn("_browse_mydockfinder_path", source)
        self.assertIn("_browse_music_app_profile_path", source)
        self.assertIn("_browse_betterncm_path", source)
        self.assertIn("_browse_schedule_json_path", source)
        self.assertIn("_browse_schedule_ics_path", source)
        self.assertIn("getOpenFileName", source)
        self.assertIn("getExistingDirectory", source)

    def test_barrage_page_uses_screen_height_range_and_fullscreen_notification_style(self):
        source = self._source("local/settings_window.py")
        store_source = self._source("local/settings_store.py")
        app_source = self._source("local/app.py")
        visual_source = self._source("local/island_stack_visual.py")

        self.assertIn("_barrage_position_max", source)
        self.assertIn("self._barrage_position_max()", source)
        self.assertNotIn('"弹幕位置", 0, 240', source)
        self.assertIn("PLUGIN_FULLSCREEN_ALERT_STYLES", source)
        self.assertIn("plugin_fullscreen_alert_style", source)
        self.assertIn("v2_plugin_fullscreen_alert_style", store_source)
        self.assertIn("v2_plugin_fullscreen_alert_style", app_source)
        self.assertIn("set_plugin_fullscreen_alert_style", visual_source)
        self.assertIn("v2_plugin_fullscreen_alert_x_offset", store_source)
        self.assertIn("v2_plugin_fullscreen_alert_y_offset", store_source)
        self.assertIn("set_plugin_fullscreen_alert_position", visual_source)
        self.assertIn("plugin_fullscreen_alert_x_offset", source)
        self.assertIn("plugin_fullscreen_alert_y_offset", source)
        self.assertIn("v2_barrage_display_mode", store_source)
        self.assertIn("v2_barrage_display_mode", app_source)
        self.assertIn("BARRAGE_DISPLAY_MODES", source)
        self.assertIn("_set_barrage_display_mode", source)

    def test_barrage_adjustments_trigger_preview_without_hiding_island(self):
        source = self._source("local/settings_window.py")
        preview_start = source.index("def _begin_barrage_adjust_preview")
        preview_end = source.index("def _touch", preview_start)
        preview_source = source[preview_start:preview_end]
        self.assertIn("show_barrage_preview", preview_source)
        self.assertNotIn("set_settings_window_active(True)", preview_source)

        for name in ("_set_barrage_y_offset", "_set_barrage_font_size", "_set_barrage_speed", "_set_barrage_backdrop", "_set_barrage_icon"):
            start = source.index(f"def {name}")
            end = source.index("\n    def ", start + 1)
            method_source = source[start:end]
            self.assertIn("_begin_barrage_adjust_preview()", method_source, name)

    def test_plugin_alert_adjustments_trigger_preview(self):
        source = self._source("local/settings_window.py")
        preview_start = source.index("def _begin_plugin_alert_adjust_preview")
        preview_end = source.index("\n    def ", preview_start + 1)
        preview_source = source[preview_start:preview_end]
        self.assertIn("show_plugin_fullscreen_alert_preview", preview_source)

        for name in ("_set_plugin_fullscreen_alert_style", "_set_plugin_fullscreen_alert_x_offset", "_set_plugin_fullscreen_alert_y_offset"):
            start = source.index(f"def {name}")
            end = source.index("\n    def ", start + 1)
            method_source = source[start:end]
            self.assertIn("_begin_plugin_alert_adjust_preview()", method_source, name)

    def test_plugin_fullscreen_alert_choices_use_fullscreen_when_wording(self):
        from local.settings_window import SettingsWindow

        self.assertEqual(
            tuple(SettingsWindow.PLUGIN_FULLSCREEN_ALERT_STYLES),
            (
                ("弹幕提醒", "barrage"),
                ("弹窗提醒", "toast"),
                ("全屏提醒", "fullscreen"),
            ),
        )
        source = self._source("local/settings_window.py")
        self.assertIn('self._row(g, "全屏时提醒"', source)
        self.assertNotIn('self._row(g, "全屏提醒"', source)

    def test_disabled_settings_options_are_visually_gray(self):
        style = self._source("local/mac_style.py")

        self.assertIn(":disabled", style)
        self.assertIn("color: {t[\"disabled_text\"]}", style)
        self.assertIn("QComboBox QAbstractItemView::item:disabled", style)

    def test_weather_and_agenda_plugin_options_are_available_indicator_rows(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        island.enabled_plugin_ids = {"time", "weather", "agenda"}
        island.enabled_plugin_count = 3
        window = SettingsWindow(island, FakeSettingsManager())
        try:
            self.assertTrue(window.plugin_weather_indicator._active)
            self.assertTrue(window.plugin_agenda_indicator._active)
            self.assertTrue(window.plugin_weather_enabled.isEnabled())
            self.assertTrue(window.plugin_agenda_enabled.isEnabled())
            self.assertTrue(window.plugin_weather_enabled.isChecked())
            self.assertTrue(window.plugin_agenda_enabled.isChecked())
            self.assertEqual(window.plugin_weather_indicator.toolTip(), "可用")
            self.assertEqual(window.plugin_agenda_indicator.toolTip(), "可用")
            self.assertEqual(window.plugin_weather_enabled.unavailable_reason(), "")
            self.assertEqual(window.plugin_agenda_enabled.unavailable_reason(), "")
        finally:
            window.close()


    def test_appearance_page_exposes_settings_font_selection_and_import(self):
        from local.settings_window import SettingsWindow

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
        self.assertIn("load_embedded_lyric_fonts", source)
        self.assertEqual(
            SettingsWindow.FONT_CHOICES,
            (
                "Alibaba PuHuiTi",
                "Alibaba PuHuiTi Heavy",
                "Noto Serif SC ExtraBold",
                "JiangChengXieSong 700W",
                "JiangChengXieSong 900W",
            ),
        )
        self.assertNotIn("PingFang SC", SettingsWindow.FONT_CHOICES)
        self.assertNotIn("Source Han Serif SC", SettingsWindow.FONT_CHOICES)

    def test_settings_font_reports_actual_applied_family(self):
        source = self._source("local/settings_window.py")

        self.assertIn("settings_font_status", source)
        self.assertIn("_resolve_settings_font_family", source)
        self.assertIn("_update_settings_font_status", source)
        self.assertIn("QApplication.instance()", source)

    def test_media_page_prioritizes_per_app_sync_profiles(self):
        source = self._source("local/settings_window.py")

        self.assertIn("music_sync_status", source)
        self.assertIn("_music_app_profile_status_text", source)
        self.assertIn("_update_music_app_profile_statuses", source)
        self.assertNotIn("ncm_path_input", source)
        self.assertLess(source.index("app_profile_title"), source.index("lyric_title"))
        self.assertLess(source.index("app_profile_title"), source.index("background_title"))
        self.assertLess(source.index("background_title"), source.index("lyric_title"))

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

        self.assertIn("def _basic_settings_page", source)
        self.assertIn("def _media_page", source)
        self.assertIn("def _music_surface_lyrics_page", source)
        self.assertIn("def _plugins_page", source)
        self.assertIn("def _notification_page", source)
        self.assertIn("def _about_page", source)
        for name in ("基础设置", "媒体控制", "全屏歌词", "插件设置", "通知设置", "关于我"):
            self.assertIn(name, source)
        self.assertIn("self._basic_settings_page()", source)
        self.assertLess(source.index("self._basic_settings_page()"), source.index("self._media_page()"))
        self.assertLess(source.index("self._media_page()"), source.index("self._plugins_page()"))
        self.assertLess(source.index("self._media_page()"), source.index("self._music_surface_lyrics_page()"))
        self.assertLess(source.index("self._music_surface_lyrics_page()"), source.index("self._plugins_page()"))
        self.assertLess(source.index("self._plugins_page()"), source.index("self._notification_page()"))
        self.assertLess(source.index("self._notification_page()"), source.index("self._about_page()"))

    def test_about_page_exposes_custom_global_icon_picker(self):
        source = self._source("local/settings_window.py")

        self.assertIn("icon_changed_callback", source)
        self.assertIn("install_custom_icon", source)
        self.assertIn("v2_custom_icon_path", source)
        self.assertIn("_choose_custom_icon", source)
        self.assertIn("_reset_custom_icon", source)
        self.assertIn("Image Files (*.png *.jpg *.jpeg *.bmp *.webp *.ico)", source)
        self.assertIn("全局图标", source)
        self.assertIn("上传图标", source)

    def test_about_page_custom_icon_picker_saves_and_notifies(self):
        from local import settings_window
        from local.settings_window import SettingsWindow

        manager = FakeSettingsManager()
        window = SettingsWindow(FakeIslandWidget(), manager)
        callbacks: list[str] = []
        window.icon_changed_callback = callbacks.append
        try:
            with mock.patch.object(settings_window.QFileDialog, "getOpenFileName", return_value=("D:/picked/icon.png", "")):
                with mock.patch.object(settings_window, "install_custom_icon", return_value={"ico_path": "D:/installed/custom-icon.ico"}):
                    window._choose_custom_icon()

            self.assertEqual(manager.values["v2_custom_icon_path"], "D:/installed/custom-icon.ico")
            self.assertEqual(callbacks, ["D:/installed/custom-icon.ico"])

            window._reset_custom_icon()
            self.assertEqual(manager.values["v2_custom_icon_path"], "")
            self.assertEqual(callbacks[-1], "")
        finally:
            window.close()

    def test_basic_settings_exposes_startup_mode_selector(self):
        source = self._source("local/settings_window.py")
        store_source = self._source("local/settings_store.py")
        from local.settings_window import SettingsWindow

        self.assertIn("STARTUP_MODES", source)
        self.assertIn(("Windows 启动项", "registry"), SettingsWindow.STARTUP_MODES)
        self.assertNotIn(("注册表", "registry"), SettingsWindow.STARTUP_MODES)
        self.assertIn("startup_mode", source)
        self.assertIn("_set_startup_mode", source)
        self.assertIn("v2_startup_mode", source)
        self.assertIn("v2_startup_mode", store_source)

    def test_basic_settings_groups_mydockfinder_and_taskbar_controls(self):
        from local.settings_window import SettingsWindow

        window = SettingsWindow(FakeIslandWidget(), FakeSettingsManager())
        try:
            self.assertEqual(window.mydockfinder_path_row.objectName(), "PathPickerRowStacked")
            self.assertTrue(hasattr(window, "mydockfinder_listener_detail_toggle"))
            self.assertTrue(hasattr(window, "mydockfinder_listener_detail_body"))
            self.assertFalse(window.mydockfinder_listener_detail_toggle.isChecked())
            self.assertFalse(window.mydockfinder_listener_detail_body.isVisible())

            section_titles = [
                label.text()
                for label in window.findChildren(QLabel)
                if label.text() in {"位置与监听", "任务栏设置"}
            ]
            self.assertIn("位置与监听", section_titles)
            self.assertIn("任务栏设置", section_titles)
        finally:
            window.close()

    def test_ios_switches_use_compact_height(self):
        from local.ios_widgets import IosSwitch

        switch = IosSwitch("测试", True)

        self.assertEqual(switch.minimumHeight(), 21)

    def test_disabled_ios_switch_shows_unavailable_reason_chip(self):
        from local.ios_widgets import IosSwitch

        switch = IosSwitch("测试", True)
        switch.set_unavailable_reason("当前组合不可用")
        switch.setEnabled(False)

        self.assertEqual(switch.unavailable_reason(), "当前组合不可用")
        self.assertGreater(switch.minimumHeight(), switch.CONTROL_HEIGHT)

        switch.setEnabled(True)
        self.assertEqual(switch.minimumHeight(), switch.CONTROL_HEIGHT)


if __name__ == "__main__":
    unittest.main()
