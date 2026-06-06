from __future__ import annotations

import unittest
from datetime import time as datetime_time
from unittest.mock import patch

from PySide6.QtCore import QEvent

from local import ui_theme


class UiThemeTests(unittest.TestCase):
    def test_app_dark_mode_uses_windows_system_theme_when_available(self):
        with patch.object(ui_theme, "_windows_apps_use_light_theme", return_value=0, create=True):
            self.assertTrue(ui_theme.app_dark_mode())

    def test_app_dark_mode_uses_windows_light_theme_when_available(self):
        with patch.object(ui_theme, "_windows_apps_use_light_theme", return_value=1, create=True):
            self.assertFalse(ui_theme.app_dark_mode())

    def test_theme_change_events_include_system_theme_change(self):
        self.assertIn(QEvent.Type.ThemeChange, ui_theme.theme_change_event_types())

    def test_app_dark_mode_can_be_forced_dark(self):
        settings = lambda key, default=None: {"v2_theme_mode": "dark"}.get(key, default)

        self.assertTrue(ui_theme.app_dark_mode(settings_getter=settings))

    def test_app_dark_mode_can_be_forced_light(self):
        settings = lambda key, default=None: {"v2_theme_mode": "light"}.get(key, default)

        self.assertFalse(ui_theme.app_dark_mode(settings_getter=settings))

    def test_scheduled_dark_mode_handles_cross_midnight_window(self):
        values = {
            "v2_theme_mode": "schedule",
            "v2_theme_dark_start": "22:00",
            "v2_theme_dark_end": "07:30",
        }
        settings = lambda key, default=None: values.get(key, default)

        self.assertTrue(ui_theme.app_dark_mode(settings_getter=settings, now=datetime_time(23, 15)))
        self.assertTrue(ui_theme.app_dark_mode(settings_getter=settings, now=datetime_time(6, 20)))
        self.assertFalse(ui_theme.app_dark_mode(settings_getter=settings, now=datetime_time(12, 0)))

    def test_scheduled_dark_mode_handles_same_day_window(self):
        values = {
            "v2_theme_mode": "schedule",
            "v2_theme_dark_start": "09:00",
            "v2_theme_dark_end": "18:00",
        }
        settings = lambda key, default=None: values.get(key, default)

        self.assertFalse(ui_theme.app_dark_mode(settings_getter=settings, now=datetime_time(8, 59)))
        self.assertTrue(ui_theme.app_dark_mode(settings_getter=settings, now=datetime_time(12, 0)))
        self.assertFalse(ui_theme.app_dark_mode(settings_getter=settings, now=datetime_time(18, 1)))

    def test_music_background_follow_theme_maps_between_black_and_white(self):
        self.assertEqual(ui_theme.music_ambience_style_for_theme(False, "pure_black", follow_enabled=True), "pure_white")
        self.assertEqual(ui_theme.music_ambience_style_for_theme(True, "pure_white", follow_enabled=True), "pure_black")

    def test_music_background_follow_theme_leaves_other_backgrounds_and_disabled_state_alone(self):
        self.assertEqual(ui_theme.music_ambience_style_for_theme(False, "sea_level", follow_enabled=True), "sea_level")
        self.assertEqual(ui_theme.music_ambience_style_for_theme(True, "pure_black", follow_enabled=False), "pure_black")


if __name__ == "__main__":
    unittest.main()
