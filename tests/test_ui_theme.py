from __future__ import annotations

import unittest
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


if __name__ == "__main__":
    unittest.main()
