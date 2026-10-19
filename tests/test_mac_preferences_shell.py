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

    def test_settings_controls_and_sidebar_keep_native_hover_geometry(self):
        from local.settings_window import SettingsWindow
        window = SettingsWindow(FakeIslandWidget(), FakeSettingsManager())
        try:
            self.assertTrue(window.property("controlHoverDisabled"))
            self.assertIsNone(getattr(window.nav, "_control_hover_delegate", None))
            for control in window.findChildren(QWidget):
                registration = getattr(control, "_control_hover_registration", None)
                if registration is not None and registration.group is not None:
                    self.assertFalse(registration.group._valid(registration.record))
            toggle, body = window._detail_panels[0]
            self.assertFalse(toggle.isChecked())
            toggle.click()
            self.assertTrue(toggle.isChecked())
            self.assertFalse(body.isHidden())
            toggle.click()
            self.assertTrue(body.isHidden())
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_settings_sidebar_uses_asset_icons(self):
        from local.settings_window import SettingsWindow

        window = SettingsWindow(FakeIslandWidget(), FakeSettingsManager())
        try:
            self.assertGreaterEqual(window.nav.iconSize().width(), 22)
            self.assertGreaterEqual(window.nav.iconSize().height(), 22)
            icon_paths = [page.get("icon_path", "") for page in window._settings_pages]
            self.assertEqual(len(icon_paths), 4)
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

    def test_music_background_preview_uses_three_current_styles_and_system_theme(self):
        from local.settings_window import MusicAmbiencePreview, SettingsWindow

        self.assertEqual(
            [value for _label, value in SettingsWindow.MUSIC_AMBIENCE_STYLES],
            ["sea_level", "solid_album", "system"],
        )
        preview = MusicAmbiencePreview()
        preview.resize(320, 64)
        def render(style, dark):
            preview.set_preview_state(style, {"ambient_intensity": 0.62, "music_flow_intensity": 0.74})
            image = QImage(preview.size(), QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            with mock.patch("local.settings_window.system_dark_mode", return_value=dark):
                with QPainter(image) as painter:
                    preview.render(painter, QPoint())
            return image
        try:
            for style in ("sea_level", "solid_album", "system"):
                image = render(style, True)
                self.assertGreater(image.pixelColor(160, 8).alpha(), 0, style)
            dark = render("system", True)
            light = render("system", False)
            self.assertNotEqual(dark, light)
            self.assertGreater(light.pixelColor(160, 8).lightness(), dark.pixelColor(160, 8).lightness() + 100)
        finally:
            preview.close()

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

    def test_removed_visual_settings_migrate_to_fixed_defaults(self):
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
                    self.assertEqual(settings.get("v2_wave_font_weight"), "bold")
                    self.assertEqual(settings.get("v2_lyric_font_size"), 18)
                    self.assertFalse(settings.get("v2_enable_dual_line_lyrics"))
                    self.assertTrue(settings._dirty)
                    self.assertNotIn(retired, settings.get("v2_music_ambience_values"))
                    self.assertEqual(settings.get("v2_music_ambience_values")["sea_level"]["ambient_intensity"], 0.0)
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
                    self.assertEqual(saved["v2_wave_font_weight"], "bold")
                    self.assertNotIn(retired, saved["v2_music_ambience_values"])
                    self.assertTrue(settings_store._REMOVED_VISUAL_SETTING_KEYS.isdisjoint(saved))

    def test_startup_ignores_removed_visual_options_and_uses_fixed_wave_font(self):
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
        controller.window.set_wave_font_weight.assert_called_once_with("bold", notify=False)
        controller.window.set_lyric_font_size.assert_called_once_with(18, notify=False)
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
                self.assertEqual(settings.get("v2_lyric_font_size"), 18)
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

    def test_sparse_highlight_threshold_is_read_only_at_point_eight_seconds(self):
        from local.settings_store import DEFAULT_SETTINGS
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        settings = FakeSettingsManager()
        self.assertEqual(DEFAULT_SETTINGS["v2_music_sparse_highlight_seconds_per_unit"], 0.8)
        window = SettingsWindow(island, settings)
        try:
            self.assertFalse(hasattr(window, "music_sparse_highlight_seconds_per_unit"))
            self.assertEqual(window.music_sparse_highlight_label.text(), "0.8 秒 / 字词")
            window.sync_from_widget()
        finally:
            window.close()

    def test_sparse_highlight_threshold_rejects_malformed_loaded_settings(self):
        from local.settings_window import SettingsWindow

        settings = FakeSettingsManager()
        settings.values["v2_music_sparse_highlight_seconds_per_unit"] = "not-a-number"
        window = SettingsWindow(FakeIslandWidget(), settings)
        try:
            self.assertEqual(window.music_sparse_highlight_label.text(), "0.8 秒 / 字词")
        finally:
            window.close()

    def test_sparse_highlight_threshold_is_normalized_on_disk_load_and_set(self):
        from local import settings_store

        with tempfile.TemporaryDirectory() as temporary:
            settings_file = Path(temporary) / "settings.json"
            with mock.patch.object(settings_store, "SETTINGS_FILE", str(settings_file)), \
                 mock.patch.object(settings_store, "APP_DATA_DIR", temporary):
                for raw, expected in (("bad", 0.8), (None, 0.8), (0.5, 0.8), (2.5, 0.8)):
                    with self.subTest(raw=raw):
                        settings_file.write_text(json.dumps({
                            "v2_music_sparse_highlight_seconds_per_unit": raw,
                        }), encoding="utf-8")
                        settings = settings_store.SettingsManager()
                        self.assertEqual(settings.get("v2_music_sparse_highlight_seconds_per_unit"), expected)
                        settings.set("v2_music_sparse_highlight_seconds_per_unit", "1.8")
                        self.assertEqual(settings.get("v2_music_sparse_highlight_seconds_per_unit"), 0.8)
                        settings.save_now()
                        self.assertEqual(json.loads(settings_file.read_text(encoding="utf-8"))[
                            "v2_music_sparse_highlight_seconds_per_unit"
                        ], 0.8)

    def test_malformed_settings_document_uses_defaults_without_crashing(self):
        from local import settings_store

        with tempfile.TemporaryDirectory() as temporary:
            settings_file = Path(temporary) / "settings.json"
            settings_file.write_text("[]", encoding="utf-8")
            with mock.patch.object(settings_store, "SETTINGS_FILE", str(settings_file)), \
                 mock.patch.object(settings_store, "APP_DATA_DIR", temporary):
                settings = settings_store.SettingsManager()
            self.assertEqual(settings.get("v2_music_sparse_highlight_seconds_per_unit"), 0.8)

    def test_music_intro_cleanup_option_is_absent_with_translation_still_session_controlled(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        settings = FakeSettingsManager()
        window = SettingsWindow(island, settings)
        try:
            self.assertFalse(hasattr(window, "music_intro_cleanup_checkbox"))
            self.assertFalse(hasattr(window, "_set_music_intro_cleanup_enabled"))
            self.assertNotIn("片头制作信息改为", " ".join(
                label.text() for label in window.findChildren(QLabel)))
            self.assertNotIn("片头制作信息改为", " ".join(
                " ".join(entry["terms"]) for entry in window._settings_search_entries))
            for name in ("music_surface_translation_checkbox", "dual_line_lyrics_checkbox", "gelv_dual_line_checkbox"):
                self.assertFalse(hasattr(window, name), name)
            window._set_dual_line_lyrics(True)
            self.assertTrue(island.enable_dual_line_lyrics)
            window.sync_from_widget()
        finally:
            window.close()

    def test_music_intro_cleanup_is_always_enabled_for_fresh_and_legacy_settings(self):
        from local import settings_store

        key = "v2_music_intro_cleanup_enabled"
        self.assertIs(settings_store.DEFAULT_SETTINGS[key], True)
        with tempfile.TemporaryDirectory() as temporary:
            settings_file = Path(temporary) / "settings.json"
            with mock.patch.object(settings_store, "SETTINGS_FILE", str(settings_file)), \
                 mock.patch.object(settings_store, "APP_DATA_DIR", temporary), \
                 mock.patch.object(settings_store, "legacy_app_data_dirs", return_value=()):
                for raw in (None, False, True, "false", 0):
                    with self.subTest(raw=raw):
                        document = {} if raw is None else {key: raw}
                        settings_file.write_text(json.dumps(document), encoding="utf-8")
                        settings = settings_store.SettingsManager()
                        self.assertIs(settings.get(key), True)
                        settings.set(key, False)
                        self.assertIs(settings.get(key), True)
                        # A normal subsequent save must retain the canonical
                        # true value instead of the old disabled preference.
                        settings.set("v2_music_glass_level", 61)
                        settings.save_now()
                        self.assertIs(json.loads(settings_file.read_text(encoding="utf-8"))[key], True)

    def test_location_and_tasker_defaults_are_present(self):
        from local.settings_store import DEFAULT_SETTINGS

        self.assertEqual(DEFAULT_SETTINGS["v2_location_mode"], "top")
        self.assertEqual(DEFAULT_SETTINGS["v2_top_notch_height_mode"], "standard")
        self.assertEqual(DEFAULT_SETTINGS["v2_taskbar_corner_style"], "win11")
        self.assertEqual(DEFAULT_SETTINGS["v2_taskbar_left_handle_color"], "#8E8E93")
        self.assertFalse(DEFAULT_SETTINGS["v2_tasker_follow_taskbar_color"])
        self.assertEqual(DEFAULT_SETTINGS["v2_music_background_follow_previous_style"], "")
        self.assertEqual(DEFAULT_SETTINGS["v2_dual_line_font_size"], 12)
        self.assertEqual(set(DEFAULT_SETTINGS["v2_music_ambience_values"]), {"sea_level", "solid_album", "system"})
        self.assertEqual(DEFAULT_SETTINGS["v2_enabled_plugins"], [])
        self.assertIn("v2_mydockfinder_path", DEFAULT_SETTINGS)
        from local.settings_store import MYDOCKFINDER_LISTEN_PROCESS_CHOICES
        self.assertEqual(DEFAULT_SETTINGS["v2_mydockfinder_listener_process_names"], [name for name, _ in MYDOCKFINDER_LISTEN_PROCESS_CHOICES])
        self.assertIn("v2_last_window_position", DEFAULT_SETTINGS)
        self.assertIn("v2_custom_icon_path", DEFAULT_SETTINGS)
        self.assertEqual(DEFAULT_SETTINGS["v2_custom_icon_path"], "")
        self.assertNotIn("v2_taskbar_recognition_threshold", DEFAULT_SETTINGS)
        self.assertNotIn("v2_taskbar_plugin_compact_style", DEFAULT_SETTINGS)
        self.assertNotIn("v2_taskbar_message_compact_style", DEFAULT_SETTINGS)
        self.assertNotIn("v2_taskbar_hint_text", DEFAULT_SETTINGS)
        self.assertNotIn("v2_taskbar_fusion_mode", DEFAULT_SETTINGS)

    def test_media_page_has_one_system_lyric_offset_without_player_profiles(self):
        from local.settings_window import SettingsWindow

        source = self._source("local/settings_window.py")
        media_start = source.index("def _media_page")
        media_end = source.index("def _music_surface_lyrics_page", media_start)
        media_source = source[media_start:media_end]
        self.assertNotIn('self.lyric_offset = self._slider_row(c, "歌词偏移"', media_source)
        self.assertIn("_build_music_app_profile_panel", source)
        self.assertIn('"offset": offset_slider', source)
        self.assertIn("_set_music_app_profile_lyric_offset", source)

        window = SettingsWindow(FakeIslandWidget(), FakeSettingsManager())
        try:
            self.assertFalse(hasattr(window, "lyric_offset"))
            self.assertFalse(window.music_app_profile_controls)
            self.assertEqual((window.system_lyric_offset.minimum(), window.system_lyric_offset.maximum()), (-12000, 12000))
        finally:
            window.close()

    def test_old_saved_profiles_do_not_recreate_listener_controls(self):
        from local.settings_window import SettingsWindow, StatusLamp

        settings = FakeSettingsManager()
        profiles = dict(settings.values["v2_music_app_profiles"])
        profiles["qq"] = dict(profiles["qq"], sync_enabled=True)
        settings.values["v2_music_app_profiles"] = profiles
        window = SettingsWindow(FakeIslandWidget(), settings)
        try:
            self.assertEqual(window.music_app_profile_controls, {})
            window.sync_from_widget()
            self.assertEqual(window.music_app_profile_controls, {})
        finally:
            window.close()

    def test_system_lyric_offset_applies_immediately_after_save(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        island.set_active_music_app_lyric_offset = mock.Mock()
        settings = FakeSettingsManager()
        window = SettingsWindow(island, settings)
        try:
            window.system_lyric_offset.setValue(750)
            self.assertEqual(settings.get("v2_system_lyric_offset_seconds"), 0.75)
            island.set_active_music_app_lyric_offset.assert_called_once_with("system", 0.75)
        finally:
            window.close()

    def test_app_loads_persistent_album_island_record_and_split_sizes(self):
        app_source = self._source("local/app.py")

        self.assertIn("load_embedded_lyric_fonts", app_source)
        self.assertIn("v2_album_island_listened_seconds_total", app_source)
        self.assertIn("v2_album_island_history", app_source)
        self.assertIn("v2_album_island_compact_cover_size", app_source)
        self.assertIn("v2_album_island_expanded_cover_size", app_source)

    def test_music_ambience_setting_is_renamed_to_background_style(self):
        source = self._source("local/settings_window.py")

        self.assertIn('self._row(c, "背景样式", self.music_ambience_style)', source)
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
        self.assertNotIn("self.empty_island_display_mode = self._combo", appearance_source)
        self.assertNotIn("无岛显示", appearance_source)
        self.assertNotIn("self._preview_frame(22)", appearance_source)
        media_start = source.index("def _media_page")
        media_end = source.index("def _music_surface_lyrics_page", media_start)
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
            self.assertFalse(any(key.startswith(("message_", "wechat_", "qq_notification_", "dingtalk_notification_"))
                                 for key in window._diagnostic_labels))
            self.assertTrue(all(label.objectName() == "DiagnosticValue" for label in labels))
            self.assertTrue(all(label.wordWrap() for label in labels))
            self.assertTrue(
                all(label.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored for label in labels)
            )
        finally:
            window.close()

    def test_settings_traffic_lights_show_hover_symbols(self):
        from local.mac_style import MacTrafficCapsule
        from local.settings_window import SettingsWindow

        window = SettingsWindow(FakeIslandWidget(), FakeSettingsManager())
        try:
            traffic = window.findChild(MacTrafficCapsule)
            self.assertEqual([button._icon_key for button in traffic.buttons], ["close", "minimize", "maximize"])
            traffic._set_progress(1.)
            self.assertTrue(all(button._reveal == 1. for button in traffic.buttons))
        finally:
            window.close()

    def test_settings_traffic_lights_use_capsule_backplate(self):
        from local.mac_style import MacTrafficCapsule, mac_traffic_lights

        traffic = mac_traffic_lights()
        try:
            self.assertIsInstance(traffic, MacTrafficCapsule)
            reserved = traffic.size()
            compact = traffic._capsule_rect()
            traffic._set_progress(1.)
            self.assertEqual(traffic.size(), reserved)
            self.assertGreater(traffic._capsule_rect().width(), compact.width())
            self.assertTrue(all(traffic.rect().contains(button.geometry()) for button in traffic.buttons))
        finally:
            traffic.close()

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
        self.assertIn('self._indicator_row(c, "检测状态"', source)

    def test_media_status_values_align_with_neighboring_controls_without_clipping_titles(self):
        from local.mac_style import mac_preferences_theme
        from local.settings_window import SettingsWindow

        for dark in (False, True):
            with self.subTest(dark=dark), mock.patch(
                "local.settings_window.mac_preferences_theme", return_value=mac_preferences_theme(dark),
            ):
                window = SettingsWindow(FakeIslandWidget(), FakeSettingsManager())
                try:
                    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
                    window.nav.setCurrentRow(1)
                    window.show()
                    for width, height in ((688, 496), (860, 620)):
                        window.resize(width, height)
                        self.app.processEvents()
                        control_x = window.placement_mode.mapTo(window, QPoint()).x()
                        for value in (window.mydockfinder_status_label, window.myfinder_status_label):
                            self.assertEqual(value.mapTo(window, QPoint()).x(), control_x)
                            title = value.parentWidget().findChild(QLabel, "RowTitle")
                            self.assertGreaterEqual(title.width(), title.fontMetrics().horizontalAdvance(title.text()))
                        self.assertEqual(window.finder_bar_style.mapTo(window, QPoint()).x(), control_x)
                finally:
                    window.close()
                    window.deleteLater()
                    self.app.processEvents()

    def test_path_picker_buttons_share_input_top_and_bottom_at_supported_window_sizes(self):
        from PySide6.QtWidgets import QLineEdit, QPushButton
        from local.mac_style import mac_preferences_theme
        from local.settings_window import SettingsWindow

        for dark in (False, True):
            with self.subTest(dark=dark), mock.patch(
                "local.settings_window.mac_preferences_theme", return_value=mac_preferences_theme(dark),
            ):
                window = SettingsWindow(FakeIslandWidget(), FakeSettingsManager())
                try:
                    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
                    window.nav.setCurrentRow(1)
                    media = window._settings_pages[1]["page"]
                    path_input = QLineEdit("C:/Fixture/example.json")
                    window._path_picker_row(media._content_layout, "测试文件", path_input, lambda: None, stacked=True)
                    window.show()
                    paths = [path_input]
                    for width, height in ((688, 496), (860, 620)):
                        window.resize(width, height)
                        self.app.processEvents()
                        for path in paths:
                            button = path.parentWidget().findChild(QPushButton, "PathBrowseButton")
                            self.assertEqual(button.geometry().top(), path.geometry().top())
                            self.assertEqual(button.geometry().bottom(), path.geometry().bottom())
                            self.assertGreater(path.width(), 0)
                            self.assertTrue(path.parentWidget().rect().contains(button.geometry()))
                finally:
                    window.close()
                    window.deleteLater()
                    self.app.processEvents()

    def test_plugin_timer_start_button_aligns_with_its_minutes_input(self):
        from PySide6.QtWidgets import QPushButton
        from local.mac_style import mac_preferences_theme
        from local.plugin_manager_window import PluginManagerWindow

        for dark in (False, True):
            with self.subTest(dark=dark), mock.patch(
                "local.plugin_manager_window.mac_preferences_theme", return_value=mac_preferences_theme(dark),
            ):
                window = PluginManagerWindow("TIME")
                try:
                    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
                    window.show()
                    start = next(button for button in window.findChildren(QPushButton) if button.text() == "开始")
                    for width, height in ((680, 560), (860, 620)):
                        window.resize(width, height)
                        self.app.processEvents()
                        self.assertEqual(start.geometry().top(), window.minutes_input.geometry().top())
                        self.assertEqual(start.geometry().bottom(), window.minutes_input.geometry().bottom())
                finally:
                    window.close()
                    window.deleteLater()
                    self.app.processEvents()

    def test_retired_effects_have_no_settings_search_entry(self):
        from local.settings_window import SettingsWindow

        window = SettingsWindow(FakeIslandWidget(), FakeSettingsManager())
        try:
            indexed = " ".join(" ".join(entry["terms"]) for entry in window._settings_search_entries)
            for label in ("明灭余烬", "歌词炫彩", "双色占比", "初始字词间距", "平排"):
                self.assertNotIn(label, indexed, label)
            for label in ("格律宋散", "波光逐流", "系统"):
                self.assertIn(label, indexed, label)
        finally:
            window.close()

    def test_retired_ember_selection_reopens_as_usable_shared_reader(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        island.lyric_flow_style = "ember_fade"
        settings = FakeSettingsManager()
        settings.values["v2_lyric_flow_style"] = "ember_fade"
        window = SettingsWindow(island, settings)
        try:
            self.assertEqual(window.lyric_flow_style.currentData(), "wave_stream")
            window.sync_from_widget()
            self.assertEqual(window.lyric_flow_style.findData("ember_fade"), -1)
            self.assertFalse(hasattr(window, "_ember_controls"))
        finally:
            window.close()

    def test_retired_back_layer_controls_are_absent_on_all_backgrounds(self):
        from local.settings_window import SettingsWindow

        for background in ("sea_level", "solid_album", "system"):
            island = FakeIslandWidget()
            island.music_ambience_style = background
            window = SettingsWindow(island, FakeSettingsManager())
            try:
                for name in ("ember_color_back_gray_mix", "ember_back_gray_mix", "ember_back_opacity",
                             "ember_color_fill_checkbox", "gelv_color_fill_checkbox"):
                    self.assertFalse(hasattr(window, name), name)
                titles = {entry["title"] for entry in window._settings_search_entries}
                self.assertNotIn("背景灰度融合", titles)
                self.assertNotIn("背层透明度", titles)
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

    def test_settings_search_displays_ranked_retained_results_and_predicts_terms(self):
        from local.settings_window import SettingsWindow

        window = SettingsWindow(FakeIslandWidget(), FakeSettingsManager())
        try:
            normal_nav = [window.nav.item(i).text() for i in range(window.nav.count())]
            window.search_input.setText("背景样式")
            self.assertIs(window.stack.currentWidget(), window.settings_search_page)
            self.assertEqual([window.nav.item(i).text() for i in range(window.nav.count())], normal_nav)
            titles = [label.text() for label in window.settings_search_page.findChildren(QLabel)
                      if label.objectName() == "SearchResultTitle"]
            self.assertEqual(titles[0], "背景样式")
            model = window.search_input.completer().model()
            self.assertIn("背景样式", [model.index(row, 0).data() for row in range(model.rowCount())])
            window.search_input.setText("背样式")
            titles = [label.text() for label in window.settings_search_page.findChildren(QLabel)
                      if label.objectName() == "SearchResultTitle"]
            self.assertIn("背景样式", titles)
            window.search_input.clear()
            self.assertIsNot(window.stack.currentWidget(), window.settings_search_page)
            self.assertEqual(normal_nav, ["基础设置", "媒体控制", "全屏歌词", "关于我"])
        finally:
            window.close()

    def test_theme_following_restores_sea_level_and_explicit_system_stays_system(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        island.music_ambience_style = "sea_level"
        settings = FakeSettingsManager()
        settings.values["v2_music_background_follow_theme"] = True
        settings.values["v2_theme_mode"] = "dark"
        settings.values["v2_music_ambience_style"] = "sea_level"
        window = SettingsWindow(island, settings)
        try:
            settings.values["v2_theme_mode"] = "light"
            window._sync_music_background_to_theme()
            self.assertEqual(island.music_ambience_style, "system")
            self.assertEqual(settings.get("v2_music_background_follow_previous_style"), "sea_level")
            settings.values["v2_theme_mode"] = "dark"
            window._sync_music_background_to_theme()
            self.assertEqual(island.music_ambience_style, "sea_level")
            self.assertEqual(settings.get("v2_music_background_follow_previous_style"), "")
            window._set_combo_data(window.music_ambience_style, "system")
            window._set_music_ambience_style()
            for theme in ("light", "dark"):
                settings.values["v2_theme_mode"] = theme
                window._sync_music_background_to_theme()
                self.assertEqual(island.music_ambience_style, "system")
                self.assertEqual(settings.get("v2_music_ambience_style"), "system")
        finally:
            window.close()

    def test_music_background_follow_theme_does_not_change_retired_tasker_setting(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        settings = FakeSettingsManager()
        window = SettingsWindow(island, settings)
        try:
            window._set_music_background_follow_theme(True)
            self.assertTrue(settings.get("v2_music_background_follow_theme"))
            self.assertFalse(settings.get("v2_tasker_follow_taskbar_color"))
            self.assertFalse(island.tasker_follow_taskbar_color)
            self.assertFalse(hasattr(window, "tasker_follow_taskbar_color_checkbox"))
            window._set_music_background_follow_theme(False)
            self.assertFalse(settings.get("v2_music_background_follow_theme"))
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

    def test_app_position_uses_fixed_top_default_after_old_manual_choices(self):
        from local.app import LocalIslandApp

        controller = LocalIslandApp.__new__(LocalIslandApp)
        controller.settings = FakeSettingsManager()
        for old_mode in ("finder", "free", "top", "tasker", "stick"):
            controller.settings.values["v2_location_mode"] = old_mode
            self.assertEqual(controller._location_mode_setting(), "top")

    def test_settings_exposes_only_mydockfinder_monitor_permission_and_readonly_status(self):
        source = self._source("local/settings_window.py")
        app_source = self._source("local/app.py")

        self.assertIn("MyDockFinder 监听", source)
        self.assertIn("mydockfinder_monitor_switch", source)
        self.assertNotIn("MyDockFinder 监听进程", source)
        self.assertNotIn("mydockfinder_process_checkboxes", source)
        self.assertNotIn("_set_mydockfinder_listener_process", source)
        self.assertIn("_status_indicator", source)
        self.assertNotIn("mydockfinder_path_input", source)
        self.assertNotIn("_set_mydockfinder_path", source)
        self.assertIn("_setup_mydockfinder_listener", app_source)
        self.assertIn("_poll_mydockfinder_presence", app_source)
        self.assertIn("set_mydockfinder_running", app_source)
        self.assertIn("mydockfinder_status_label", source)
        self.assertIn("_mydockfinder_status_text", source)
        self.assertIn("_mydockfinder_process_diagnostics", app_source)

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
        self.assertNotIn("_browse_mydockfinder_path", source)
        self.assertIn("_browse_music_app_profile_path", source)
        self.assertIn("_browse_betterncm_path", source)
        self.assertIn("_browse_schedule_json_path", source)
        self.assertIn("_browse_schedule_ics_path", source)
        self.assertIn("getOpenFileName", source)
        self.assertIn("getExistingDirectory", source)

    def test_disabled_settings_options_are_visually_gray(self):
        style = self._source("local/mac_style.py")

        self.assertIn(":disabled", style)
        self.assertIn("color: {t[\"disabled_text\"]}", style)
        self.assertIn("QComboBox QAbstractItemView::item:disabled", style)


    def test_media_page_explains_smtc_before_island_appearance(self):
        source = self._source("local/settings_window.py")
        start = source.index("def _media_page")
        media = source[start:source.index("def _music_surface_lyrics_page", start)]
        self.assertNotIn("音乐软件监听", media)
        self.assertNotIn("_build_music_app_profile_panel", media)
        self.assertLess(media.index("SMTC"), media.index("音乐岛样式"))
        self.assertLess(media.index("音乐岛样式"), media.index("歌词与预载"))

    def test_settings_window_is_arranged_by_four_function_pages(self):
        from local.settings_window import SettingsWindow

        window = SettingsWindow(FakeIslandWidget(), FakeSettingsManager())
        try:
            titles = [page["title"] for page in window._settings_pages]
            self.assertEqual(titles, ["基础设置", "媒体控制", "全屏歌词", "关于我"])
            self.assertEqual([window.nav.item(i).text() for i in range(window.nav.count())], titles)
            window.sync_from_widget()
            window.nav.setCurrentRow(3)
            self.assertEqual(window._diagnostics_page_index, 3)
            self.assertIs(window.stack.currentWidget(), window._settings_pages[3]["page"])
        finally:
            window.close()

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

    def test_mydockfinder_media_page_has_one_permission_with_readonly_status_and_display_controls(self):
        from PySide6.QtTest import QTest
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        settings = FakeSettingsManager()
        settings.values.update(v2_mydockfinder_path="C:/Old/MyDockFinder.exe",
                               v2_mydockfinder_listener_process_names=["dock.exe"])
        window = SettingsWindow(island, settings)
        try:
            basic, media = (window._settings_pages[i]["page"] for i in (0, 1))
            self.assertFalse(basic.isAncestorOf(window.mydockfinder_monitor_switch))
            self.assertTrue(media.isAncestorOf(window.mydockfinder_monitor_switch))
            for removed in ("mydockfinder_path_input", "mydockfinder_path_row", "mydockfinder_process_checkboxes",
                            "mydockfinder_listener_detail_toggle", "mydockfinder_listener_detail_body"):
                self.assertFalse(hasattr(window, removed), removed)
            for display in (window.placement_mode, window.finder_bar_style, window.finder_bar_preview):
                self.assertTrue(media.isAncestorOf(display))
            self.assertIn("仅检测 dock.exe", window.mydockfinder_status_label.text())
            self.assertIn("Dock_64.exe", window.mydockfinder_status_label.text())
            island.mydockfinder_running = True
            island.mydockfinder_diagnostics = {"matched_name": "dock.exe"}
            window.sync_from_widget()
            self.assertTrue(window.mydockfinder_status_indicator._active)
            self.assertIn("dock.exe", window.mydockfinder_status_label.text())
            self.assertNotIn("位置模式", window.mydockfinder_status_label.text())
            QTest.mouseClick(window.mydockfinder_monitor_switch, Qt.MouseButton.LeftButton)
            self.assertFalse(settings.get("v2_mydockfinder_monitor_enabled"))
            self.assertFalse(window.mydockfinder_status_indicator._monitor_enabled)
            self.assertFalse(window.myfinder_status_indicator._monitor_enabled)
            self.assertIn("监听已关闭", window.mydockfinder_status_label.text())
            self.assertIn("监听已关闭", window.myfinder_status_label.text())
            QTest.mouseClick(window.mydockfinder_monitor_switch, Qt.MouseButton.LeftButton)
            self.assertTrue(settings.get("v2_mydockfinder_monitor_enabled"))
            self.assertTrue(window.mydockfinder_status_indicator._monitor_enabled)
            self.assertEqual(settings.get("v2_mydockfinder_path"), "C:/Old/MyDockFinder.exe")
            self.assertEqual(settings.get("v2_mydockfinder_listener_process_names"), ["dock.exe"])
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

    def test_finder_controls_use_media_page_without_restoring_retired_position_controls(self):
        from local.settings_window import SettingsWindow

        window = SettingsWindow(FakeIslandWidget(), FakeSettingsManager())
        try:
            media = window._settings_pages[1]["page"]
            self.assertTrue(media.isAncestorOf(window.placement_mode))
            self.assertTrue(media.isAncestorOf(window.finder_bar_style))
            self.assertEqual(window.finder_bar_style.currentData(), "transparent")
            self.assertEqual({window.finder_bar_style.itemData(i) for i in range(window.finder_bar_style.count())},
                             {"transparent", "capsule", "fade"})
            self.assertFalse(hasattr(window, "location_mode"))
            self.assertFalse(hasattr(window, "position_preset"))
            indexed = " ".join(" ".join(entry["terms"]) for entry in window._settings_search_entries)
            self.assertIn("沉浸栏样式", indexed)
            self.assertNotIn("Finder Bar 样式", indexed)
            self.assertEqual(window.finder_bar_preview.accessibleName(), "沉浸栏样式预览")
            self.assertIn("Finder Bar 与 Tasker Bar 共用沉浸栏样式", window.finder_bar_hint.text())
            self.assertEqual([window.finder_bar_style.itemText(i) for i in range(window.finder_bar_style.count())],
                             ["专辑与无背景", "专辑与渐变色背景", "专辑与背景"])
            self.assertIn("音乐岛模式", indexed)
            self.assertIn("沉浸栏位置", indexed)
        finally:
            window.close()

    def test_immersive_style_selection_updates_one_shared_runtime_and_saved_key(self):
        from local.settings_window import SettingsWindow

        island, settings = FakeIslandWidget(), FakeSettingsManager()
        calls = []

        def set_style(style, notify=True):
            calls.append((style, notify))
            island.finder_bar_style = style

        island.set_finder_bar_style = set_style
        window = SettingsWindow(island, settings)
        try:
            for mode, style in (("finder", "capsule"), ("tasker", "fade"), ("finder", "transparent")):
                island.location_mode = mode
                window.finder_bar_style.setCurrentIndex(window.finder_bar_style.findData(style))
                self.assertEqual(island.finder_bar_style, style)
                self.assertEqual(settings.get("v2_finder_bar_style"), style)
                self.assertEqual(window.finder_bar_preview.style, style)
                self.assertFalse(hasattr(island, "tasker_bar_style"))
            self.assertEqual(calls, [(style, False) for style in ("capsule", "fade", "transparent")])
            self.assertEqual(settings.get("v2_location_mode"), "top")
            self.assertEqual(settings.get("v2_music_island_style"), "standard")
        finally:
            window.close()

    def test_existing_shared_style_survives_placement_changes_and_settings_reopen(self):
        from local.settings_window import SettingsWindow

        settings = FakeSettingsManager()
        settings.set("v2_finder_bar_style", "fade")
        island = FakeIslandWidget()
        window = SettingsWindow(island, settings)
        try:
            self.assertEqual(window.finder_bar_style.currentData(), "fade")
            for mode in ("tasker", "top", "free", "tasker"):
                family = "immersive" if mode == "tasker" else mode
                window.placement_mode.setCurrentIndex(window.placement_mode.findData(family))
                self.assertEqual(island.location_mode, mode)
                self.assertEqual(window.finder_bar_style.currentData(), "fade")
                self.assertEqual(window.finder_bar_preview.style, "fade")
                self.assertEqual(settings.get("v2_finder_bar_style"), "fade")
            self.assertNotIn("v2_tasker_bar_style", settings.values)
        finally:
            window.close()
        reopened = SettingsWindow(FakeIslandWidget(), settings)
        try:
            self.assertEqual(reopened.finder_bar_style.currentData(), "fade")
            self.assertEqual(reopened.finder_bar_preview.style, "fade")
        finally:
            reopened.close()

    def test_placement_choices_follow_dock_and_real_finder_bar_presence(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        window = SettingsWindow(island, FakeSettingsManager())
        try:
            def choices():
                return [(window.placement_mode.itemText(i), window.placement_mode.itemData(i))
                        for i in range(window.placement_mode.count())]

            self.assertEqual(choices(), [("Free", "free"), ("Top", "top"), ("沉浸栏", "immersive")])
            self.assertEqual(window.immersive_placement.currentData(), "tasker")
            self.assertTrue(window.immersive_placement_row.isHidden())
            self.assertEqual(window.finder_bar_preview.mode, "tasker")
            self.assertFalse(window.finder_bar_preview.isHidden())
            self.assertTrue(window.finder_bar_style.isEnabled())
            island.mydockfinder_running = True
            island.finder_bar_enabled = False
            island.finder_bar_visible = False
            window.sync_from_widget()
            self.assertEqual(choices(), [("Free", "free"), ("Top", "top")])
            self.assertIn("Top 或 Free", window.myfinder_status_label.text())
            self.assertTrue(window.finder_bar_preview.isHidden())
            self.assertFalse(window.finder_bar_style.isEnabled())
            island.finder_bar_enabled = True
            island.finder_bar_visible = True
            island.location_mode = "finder"
            window.sync_from_widget()
            self.assertEqual(choices(), [("Free", "free"), ("沉浸栏", "immersive")])
            self.assertEqual(window.placement_mode.currentData(), "immersive")
            self.assertEqual(window.immersive_placement.currentData(), "finder")
            self.assertFalse(window.immersive_placement_row.isHidden())
            self.assertFalse(window.immersive_placement.isEnabled())
            self.assertIn("并显示", window.myfinder_status_label.text())
            self.assertEqual(window.finder_bar_preview.mode, "finder")
            self.assertFalse(window.finder_bar_preview.isHidden())
            self.assertTrue(window.finder_bar_style.isEnabled())
            island.finder_bar_visible = False
            window.sync_from_widget()
            self.assertEqual(choices(), [("Free", "free"), ("沉浸栏", "immersive")])
            self.assertIn("自动隐藏", window.myfinder_status_label.text())
            self.assertTrue(window.myfinder_status_indicator._active)
            self.assertFalse(window.finder_bar_preview.isHidden())
            self.assertEqual(window.finder_bar_preview.mode, "finder")
        finally:
            window.close()

    def test_placement_choices_reuse_runtime_available_modes_and_do_not_save_global_position(self):
        from local.settings_window import SettingsWindow

        island, settings = FakeIslandWidget(), FakeSettingsManager()
        calls = []
        island.available_location_modes = lambda: ["top", "free", "tasker"]

        def set_location(mode, notify=True):
            calls.append((mode, notify))
            island.location_mode = mode

        island.set_location_mode = set_location
        window = SettingsWindow(island, settings)
        try:
            window.placement_mode.setCurrentIndex(window.placement_mode.findData("immersive"))
            self.assertEqual(calls, [("tasker", False)])
            self.assertEqual(island.location_mode, "tasker")
            self.assertEqual(settings.get("v2_location_mode"), "top")
            island.available_location_modes = lambda: ["free", "finder"]
            island.mydockfinder_running = True
            island.finder_bar_enabled = True
            island.finder_bar_visible = False
            island.location_mode = "free"
            window.sync_from_widget()
            self.assertEqual([window.placement_mode.itemData(i) for i in range(window.placement_mode.count())],
                             ["free", "immersive"])
            self.assertEqual(window.immersive_placement.currentData(), "finder")
            self.assertTrue(window.immersive_placement_row.isHidden())
            self.assertEqual(calls, [("tasker", False)])
        finally:
            window.close()

    def test_immersive_family_retains_current_physical_route_and_rejects_stale_choices(self):
        from local.settings_window import SettingsWindow

        island, settings = FakeIslandWidget(), FakeSettingsManager()
        island.location_mode = "finder"
        island.available_location_modes = lambda: ["free", "finder", "tasker"]
        calls = []

        def set_location(mode, notify=True):
            calls.append(mode)
            island.location_mode = mode

        island.set_location_mode = set_location
        window = SettingsWindow(island, settings)
        try:
            self.assertEqual(window.placement_mode.currentData(), "immersive")
            self.assertEqual(window.immersive_placement.currentData(), "finder")
            self.assertTrue(window.immersive_placement.isEnabled())
            window._set_placement_mode()
            self.assertEqual(calls, ["finder"])
            window.immersive_placement.setCurrentIndex(window.immersive_placement.findData("tasker"))
            self.assertEqual(calls, ["finder", "tasker"])
            self.assertEqual(island.location_mode, "tasker")
            island.available_location_modes = lambda: ["free"]
            window._set_placement_mode()
            self.assertEqual(calls, ["finder", "tasker"])
            self.assertEqual(window.placement_mode.currentData(), "free")
            self.assertTrue(window.immersive_placement_row.isHidden())
            self.assertEqual(settings.get("v2_location_mode"), "top")
            self.assertNotIn("immersive", settings.values.values())
        finally:
            window.close()

    def test_finder_status_prefers_runtime_presence_and_handles_malformed_diagnostics(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        island.mydockfinder_running = True
        island.mydockfinder_diagnostics = {"finder_enabled": True, "finder_bars": 42}
        window = SettingsWindow(island, FakeSettingsManager())
        try:
            self.assertIn("自动隐藏", window.myfinder_status_label.text())
            island.finder_bar_enabled = False
            island.finder_bar_visible = True
            window.sync_from_widget()
            self.assertIn("尚未检测", window.myfinder_status_label.text())
            self.assertFalse(window.myfinder_status_indicator._active)
            self.assertEqual([window.placement_mode.itemData(i) for i in range(window.placement_mode.count())], ["free", "top"])
        finally:
            window.close()

    def test_immersive_preview_tracks_environment_and_preserves_disabled_style(self):
        from PySide6.QtTest import QTest
        from local.settings_window import SettingsWindow

        island, settings = FakeIslandWidget(), FakeSettingsManager()
        settings.set("v2_finder_bar_style", "fade")
        window = SettingsWindow(island, settings)
        try:
            for dock_running, finder_enabled, expected in (
                (False, False, "tasker"), (True, True, "finder"),
                (True, False, None), (False, False, "tasker"),
            ):
                island.mydockfinder_running = dock_running
                island.finder_bar_enabled = finder_enabled
                island.finder_bar_visible = False
                window.sync_from_widget()
                self.assertEqual(window.finder_bar_preview.isHidden(), expected is None)
                self.assertEqual(window.finder_bar_style.isEnabled(), expected is not None)
                if expected is not None:
                    self.assertEqual(window.finder_bar_preview.mode, expected)
                    self.assertIn("Finder Bar" if expected == "finder" else "Tasker Bar",
                                  window.finder_bar_hint.text())
                self.assertEqual(window.finder_bar_style.currentData(), "fade")
                self.assertEqual(settings.get("v2_finder_bar_style"), "fade")
            # Disabling monitoring immediately stops presenting a stale Dock hit.
            island.mydockfinder_running = True
            island.finder_bar_enabled = False
            QTest.mouseClick(window.mydockfinder_monitor_switch, Qt.MouseButton.LeftButton)
            self.assertTrue(window.finder_bar_style.isEnabled())
            self.assertEqual(window.finder_bar_preview.mode, "tasker")
            self.assertFalse(window.finder_bar_preview.isHidden())
        finally:
            window.close()

    def test_bar_hide_follow_switch_applies_runtime_and_persists_for_reopen(self):
        from PySide6.QtTest import QTest
        from local.settings_window import SettingsWindow

        island, settings = FakeIslandWidget(), FakeSettingsManager()
        calls = []

        def set_follow(enabled, notify=True):
            calls.append((enabled, notify))
            island.bar_hide_follow_enabled = enabled

        island.set_bar_hide_follow_enabled = set_follow
        window = SettingsWindow(island, settings)
        try:
            self.assertTrue(window.bar_hide_follow_switch.isChecked())
            QTest.mouseClick(window.bar_hide_follow_switch, Qt.MouseButton.LeftButton)
            self.assertEqual(calls, [(False, False)])
            self.assertFalse(island.bar_hide_follow_enabled)
            self.assertFalse(settings.get("v2_bar_hide_follow_enabled"))
            indexed = " ".join(" ".join(entry["terms"]) for entry in window._settings_search_entries)
            self.assertIn("隐藏时跟随", indexed)
        finally:
            window.close()
        reopened = SettingsWindow(FakeIslandWidget(), settings)
        try:
            self.assertFalse(reopened.bar_hide_follow_switch.isChecked())
            settings.set("v2_bar_hide_follow_enabled", True)
            reopened.sync_from_widget()
            self.assertTrue(reopened.bar_hide_follow_switch.isChecked())
        finally:
            reopened.close()

    def test_internal_stick_placement_uses_free_public_name(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        island.location_mode = "stick"
        window = SettingsWindow(island, FakeSettingsManager())
        try:
            window.sync_from_widget()
            self.assertEqual(window.placement_mode.currentData(), "free")
            self.assertEqual(window._location_mode_label(), "Free")
            self.assertEqual(island.location_mode, "stick")
            self.assertNotIn("stick", [window.placement_mode.itemData(i) for i in range(window.placement_mode.count())])
        finally:
            window.close()

    def test_retired_settings_have_no_widgets_or_search_results(self):
        from local.settings_window import SettingsWindow

        window = SettingsWindow(FakeIslandWidget(), FakeSettingsManager())
        try:
            for name in (
                "music_checkbox", "plugin_checkbox", "music_auto_checkbox", "empty_island_display_mode",
                "no_music_island_priority", "location_mode", "top_notch_height_mode", "position_preset",
                "taskbar_corner_style", "taskbar_auto_hide_mode", "taskbar_left_handle_color",
                "settings_font_combo", "import_settings_font_button", "font_combo",
                "super_topmost_checkbox", "expanded_checkbox",
                "auto_bounds_checkbox", "expand_direction", "message_detection_checkbox",
                "plugin_time_enabled", "plugin_weather_enabled", "plugin_agenda_enabled",
                "barrage_display_mode", "barrage_y_offset", "barrage_font_size", "plugin_fullscreen_alert_style",
                "music_reactive_background_checkbox", "music_reactive_background_intensity",
                "music_expanded_layout_style", "wave_slider", "music_flow_slider", "solid_album_background_fallback",
                "lyric_font_size", "wave_font_weight", "dual_line_lyrics_checkbox", "music_surface_translation_checkbox",
                "dual_line_font_size", "dual_line_secondary_weight", "dual_line_secondary_spacing",
                "wave_compact_cover_checkbox", "album_island_cover_count", "album_island_cover_size",
                "album_island_spacing", "album_island_perspective_amount", "no_lyrics_input", "no_lyrics_display_mode",
            ):
                self.assertFalse(hasattr(window, name), name)
            indexed = " ".join(" ".join(entry["terms"]) for entry in window._settings_search_entries)
            for label in ("插件设置", "通知设置", "字体设置", "字体导入", "岛屿启用", "无岛显示", "位置与监听",
                          "Tasker 圆角", "律动强度", "双语字号", "专辑间距", "透视程度", "时间显秒", "展开态界面"):
                self.assertNotIn(label, indexed, label)
                window.search_input.setText(label)
                titles = [entry["title"] for entry in window._settings_search_entries
                          if entry["title"] == label]
                self.assertEqual(titles, [], label)
            window.search_input.clear()
            window.sync_from_widget()
        finally:
            window.close()

    def test_background_choices_use_fixed_style_defaults_after_legacy_custom_values(self):
        from local.settings_store import DEFAULT_MUSIC_AMBIENCE_VALUES
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        settings = FakeSettingsManager()
        settings.values["v2_music_ambience_values"] = {"sea_level": {"ambient_intensity": .99, "music_flow_intensity": .01}}
        window = SettingsWindow(island, settings)
        try:
            self.assertEqual([window.music_ambience_style.itemData(i) for i in range(window.music_ambience_style.count())],
                             ["sea_level", "solid_album", "system"])
            for style, values in DEFAULT_MUSIC_AMBIENCE_VALUES.items():
                window._set_combo_data(window.music_ambience_style, style)
                window._set_music_ambience_style()
                self.assertEqual(island.music_ambience_style, style)
                self.assertEqual(settings.get("v2_music_ambience_style"), style)
                self.assertEqual(island.ambient_intensity, values["ambient_intensity"])
                self.assertEqual(island.music_flow_intensity, values["music_flow_intensity"])
                self.assertEqual(window.music_preview._style, style)
                window.sync_from_widget()
        finally:
            window.close()

    def test_simple_lyric_styles_and_preload_remain_usable_without_fine_controls(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        settings = FakeSettingsManager()
        window = SettingsWindow(island, settings)
        try:
            self.assertEqual([window.lyric_flow_style.itemData(i) for i in range(window.lyric_flow_style.count())],
                             ["wave_stream", "gelv_songscatter", "album_island"])
            for style in ("wave_stream", "gelv_songscatter", "album_island"):
                window._set_combo_data(window.lyric_flow_style, style)
                window._set_lyric_flow_style()
                self.assertEqual(island.lyric_flow_style, style)
                self.assertEqual(settings.get("v2_lyric_flow_style"), style)
                self.assertEqual(window.reset_album_record_button.isHidden(), style != "album_island")
                window.sync_from_widget()
            window.music_surface_preload_count.setValue(3)
            self.assertEqual(settings.get("v2_music_surface_preload_count"), 3)
            settings.set("v2_music_surface_preload_count", 5)
            window.sync_from_widget()
            self.assertEqual(window.music_surface_preload_count.value(), 5)
        finally:
            window.close()

    def test_settings_uses_default_embedded_font_despite_legacy_selection(self):
        from local.settings_store import DEFAULT_SETTINGS
        from local.settings_window import SettingsWindow

        settings = FakeSettingsManager()
        settings.values["v2_settings_font_family"] = "JiangChengXieSong 900W"
        window = SettingsWindow(FakeIslandWidget(), settings)
        try:
            requested = DEFAULT_SETTINGS["v2_settings_font_family"]
            from PySide6.QtGui import QFont
            self.assertEqual(window.font().weight(), window.SETTINGS_FONT_WEIGHTS.get(requested, QFont.Weight.Normal))
            self.assertEqual(window.font().families(), list(window.SETTINGS_FONT_FALLBACKS[requested]))
            self.assertFalse(hasattr(window, "settings_font_combo"))
            self.assertTrue(hasattr(window, "app_icon_preview"))
        finally:
            window.close()

    def test_secondary_content_choice_remains_on_fullscreen_page_without_lookup(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        settings = FakeSettingsManager()
        window = SettingsWindow(island, settings)
        try:
            fullscreen = window._settings_pages[2]["page"]
            self.assertTrue(fullscreen.isAncestorOf(window.dual_line_secondary_source))
            self.assertEqual(window.dual_line_secondary_source.currentData(), "translation")
            island.data_provider._request_immediate_media_refresh = mock.Mock()
            window.dual_line_secondary_source.setCurrentIndex(window.dual_line_secondary_source.findData("transliteration"))
            self.assertEqual(settings.get("v2_dual_line_secondary_source"), "transliteration")
            self.assertFalse(settings.get("v2_enable_dual_line_lyrics"))
            self.assertFalse(hasattr(island, "enable_dual_line_lyrics"))
            # Changing the display choice requests a follow-up media refresh;
            # the UI never performs a synchronous lyric/network lookup.
            island.data_provider._request_immediate_media_refresh.assert_called_once_with()
            settings.set("v2_dual_line_secondary_source", "translation")
            window.sync_from_widget()
            self.assertEqual(window.dual_line_secondary_source.currentData(), "translation")
        finally:
            window.close()

    def test_general_animation_controls_remain_visible_and_persist(self):
        from local.settings_window import SettingsWindow

        island = FakeIslandWidget()
        settings = FakeSettingsManager()
        with mock.patch.object(SettingsWindow, "_display_refresh_rate_fps_cap", return_value=144):
            window = SettingsWindow(island, settings)
            try:
                basic = window._settings_pages[0]["page"]
                for control in (window.animation_speed, window.fps_slider, window.lyric_fps_slider):
                    self.assertTrue(basic.isAncestorOf(control))
                    self.assertFalse(control.isHidden())
                self.assertEqual(window.fps_slider.maximum(), 144)
                self.assertEqual(window.lyric_fps_slider.maximum(), 144)
                island.set_animation_speed = mock.Mock(side_effect=lambda value, **_kwargs: setattr(island, "animation_speed", value))
                window.animation_speed.setCurrentIndex(window.animation_speed.findData(2.0))
                self.assertEqual(settings.get("v2_animation_speed"), 2.0)
                window.fps_slider.setValue(90)
                window.lyric_fps_slider.setValue(120)
                self.assertEqual(settings.get("v2_animation_fps"), 90)
                self.assertEqual(settings.get("v2_lyric_animation_fps"), 120)
                island.animation_speed, island.animation_fps, island.lyric_animation_fps = 1.0, 60, 90
                window.sync_from_widget()
                self.assertEqual(window.animation_speed.currentData(), 1.0)
                self.assertEqual(window.fps_slider.value(), 60)
                self.assertEqual(window.lyric_fps_slider.value(), 90)
                self.assertIn("高性能消耗", window.lyric_fps_slider._value_label.text())
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
