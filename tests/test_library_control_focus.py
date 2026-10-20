"""Mouse clicks keep glass clean; keyboard users retain a quiet focus cue."""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from local.music_library_search import LibraryProgress, LibrarySearch
from local.music_library_window import _ExpandSwitch, _LibraryActionButton
from local.playback_entry_window import EntryNavigationButton, MusicSourceButton, ENTRY_SOURCES


class LibraryControlFocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.host = QWidget()
        self.host.resize(700, 220)
        self.host.setProperty("controlHoverDisabled", True)
        self.host.settings_getter = lambda key, default=None: 73 if key == "v2_music_glass_level" else default
        self.host._now_playing = SimpleNamespace(position_seconds=161, duration_seconds=200)
        self.host._background_cache = QImage(700, 220, QImage.Format.Format_RGB32)
        self.host._background_cache.fill(QColor("#343a48"))
        self.start = QPushButton("Tab 起点", self.host)
        self.start.setGeometry(10, 10, 80, 44)
        self.expand = _ExpandSwitch(self.host)
        self.expand.setText("全部收拢")
        self.expand.setGeometry(110, 10, 140, 44)
        self.search = LibrarySearch(self.host)
        self.search.setGeometry(550, 70, 44, 44)
        self.progress = LibraryProgress(self.search, self.host)
        self.progress.setGeometry(10, 70, 520, 44)
        self.action = _LibraryActionButton("logout", "退出登录", self.host)
        self.action.setGeometry(270, 10, 44, 44)
        self.host.show()
        self.app.processEvents()

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        self.app.processEvents()

    @staticmethod
    def render(widget):
        image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        widget.render(image)
        return image

    def test_mouse_focus_does_not_add_the_reported_dotted_rectangle(self):
        for control in (self.expand, self.progress, self.action):
            with self.subTest(control=type(control).__name__):
                QTest.mouseClick(control, Qt.MouseButton.LeftButton)
                self.app.processEvents()
                self.assertTrue(control.hasFocus())
                self.assertFalse(self.host.testAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange))
                focused = self.render(control)
                control.clearFocus()
                self.assertEqual(focused, self.render(control))

    def test_tab_navigation_keeps_a_visible_solid_focus_cue(self):
        for control in (self.expand, self.progress, self.action):
            with self.subTest(control=type(control).__name__):
                QWidget.setTabOrder(self.start, control)
                QTest.mouseClick(self.start, Qt.MouseButton.LeftButton)
                QTest.keyClick(self.start, Qt.Key.Key_Tab)
                self.app.processEvents()
                self.assertTrue(control.hasFocus())
                self.assertTrue(self.host.testAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange))
                focused = self.render(control)
                control.clearFocus()
                self.assertNotEqual(focused, self.render(control))

    def test_library_controls_reuse_neutral_glass_and_saved_material_strength(self):
        self.search._set_phase(1.)
        navigation = EntryNavigationButton("close", "关闭", self.host)
        source = MusicSourceButton(ENTRY_SOURCES[0], self.host)
        for control, module in ((self.expand, "local.music_library_window"),
                                (self.action, "local.music_library_window"),
                                (self.search, "local.music_library_search"),
                                (self.progress, "local.music_library_search"),
                                (navigation, "local.playback_entry_window"),
                                (source, "local.playback_entry_window")):
            with self.subTest(control=type(control).__name__), patch(module + ".paint_liquid_glass") as glass:
                self.render(control)
                self.assertTrue(glass.called)
                material = glass.call_args.kwargs
                self.assertTrue(material["refractive"])
                self.assertEqual(material["material_level"], 73)
                self.assertIsNone(material.get("base_color"))
                self.assertIsNone(material.get("tint"))
                self.assertIs(material["backdrop"], self.host._background_cache)

    def test_library_background_settings_context_supplies_the_same_glass_strength(self):
        getter = self.host.settings_getter
        del self.host.settings_getter
        self.host._background_settings_getter = getter
        with patch("local.music_library_window.paint_liquid_glass") as glass:
            self.render(self.expand)
        self.assertEqual(glass.call_args.kwargs["material_level"], 73)


if __name__ == "__main__":
    unittest.main()
