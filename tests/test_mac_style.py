from __future__ import annotations

import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QFrame, QLineEdit, QWidget

from local.liquid_glass import GlassPushButton

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

        self.assertEqual(theme["sidebar"], "#e1e1e5")
        self.assertEqual(theme["content"], "#f4f4f7")
        self.assertEqual(theme["sidebar_text"], "#1d1d1f")
        self.assertEqual(theme["section_text"], "#111113")
        self.assertEqual(theme["detail_body"], "#ececf1")
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

    def test_transparent_card_containers_preserve_control_faces_and_primary_contrast(self):
        for dark in (False, True):
            for detail in (False, True):
                with self.subTest(dark=dark, detail=detail):
                    theme = mac_preferences_theme(dark)
                    owner = QWidget()
                    owner.setProperty("controlHoverDisabled", True)
                    owner.setStyleSheet(mac_preferences_stylesheet(theme))
                    owner.resize(360, 140)
                    card = QFrame(owner)
                    card.setObjectName("MacCard")
                    card.setGeometry(5, 5, 350, 130)
                    surface = card
                    if detail:
                        surface = QFrame(card)
                        surface.setObjectName("DetailBody")
                        surface.setGeometry(8, 8, 334, 114)
                    row = QWidget(surface)
                    row.setGeometry(15, 20, 300, 70)
                    button = GlassPushButton("上传图标", row)
                    button.setObjectName("Primary")
                    button.setGeometry(0, 10, 110, 34)
                    field = QLineEdit(row)
                    field.setGeometry(130, 10, 140, 34)
                    owner.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
                    try:
                        owner.show()
                        self.app.processEvents()
                        dpr = owner.devicePixelRatioF()
                        image = QImage(round(owner.width() * dpr), round(owner.height() * dpr),
                                       QImage.Format.Format_ARGB32_Premultiplied)
                        image.setDevicePixelRatio(dpr)
                        owner.render(image)

                        def sample(widget, point):
                            point = widget.mapTo(owner, point)
                            return image.pixelColor(round(point.x() * dpr), round(point.y() * dpr))

                        accent = sample(button, QPoint(10, 17))
                        self.assertGreater(accent.blue() - accent.red(), 100)
                        self.assertEqual(sample(field, QPoint(20, 17)), QColor(theme["control"]))
                        self.assertEqual(sample(row, QPoint(285, 55)),
                                         QColor(theme["detail_body" if detail else "card"]))
                    finally:
                        owner.close()
                        owner.deleteLater()
                        self.app.processEvents()

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
