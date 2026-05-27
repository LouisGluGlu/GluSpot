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


if __name__ == "__main__":
    unittest.main()
