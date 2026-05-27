from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication, QWidget

from local.mac_style import (
    MacTrafficButton,
    mac_preferences_stylesheet,
    mac_preferences_theme,
    mac_sidebar_icon,
    mac_sidebar_icon_colors,
    mac_traffic_lights,
)


class MacStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_mac_theme_has_preferences_regions(self):
        theme = mac_preferences_theme(False)

        self.assertEqual(theme["sidebar"], "#c9c9c9")
        self.assertEqual(theme["content"], "#f2f2f2")
        self.assertEqual(theme["accent"], "#0a84ff")

    def test_dark_mac_theme_has_preferences_regions(self):
        theme = mac_preferences_theme(True)

        self.assertEqual(theme["sidebar"], "#2a2a2c")
        self.assertEqual(theme["content"], "#1f1f21")
        self.assertEqual(theme["accent"], "#0a84ff")

    def test_mac_stylesheet_contains_sidebar_search_and_titlebar_selectors(self):
        qss = mac_preferences_stylesheet(mac_preferences_theme(False))

        self.assertIn("QFrame#MacSidebar", qss)
        self.assertIn("QFrame#MacTrafficLights", qss)
        self.assertIn("QLineEdit#MacSearch", qss)
        self.assertIn("QFrame#MacTitleBar", qss)
        self.assertIn("QLabel#MacTitle", qss)
        self.assertIn("QListWidget#MacSidebarList", qss)
        self.assertIn("QFrame#MacCard", qss)
        self.assertIn("QScrollArea#MacContentScroll", qss)

    def test_sidebar_icon_colors_are_stable(self):
        colors = mac_sidebar_icon_colors()

        self.assertGreaterEqual(len(colors), 6)
        self.assertIn("#0a84ff", colors)
        self.assertIn("#ff3b30", colors)

    def test_shell_widget_factories_are_shared_helpers(self):
        self.assertTrue(callable(mac_traffic_lights))
        self.assertTrue(callable(mac_sidebar_icon))

    def test_traffic_light_buttons_are_clickable_window_controls(self):
        window = QWidget()
        traffic = mac_traffic_lights(window)

        buttons = traffic.findChildren(MacTrafficButton)
        self.assertEqual([button.objectName() for button in buttons], ["MacTrafficRed", "MacTrafficYellow", "MacTrafficGreen"])
        self.assertEqual(buttons[0].toolTip(), "关闭")
        self.assertEqual(buttons[1].toolTip(), "最小化")
        self.assertEqual(buttons[2].toolTip(), "缩放")


if __name__ == "__main__":
    unittest.main()
