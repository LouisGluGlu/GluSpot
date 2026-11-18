from __future__ import annotations

from pathlib import Path
from unittest import mock
import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from tests.test_mac_preferences_shell import FakeIslandWidget, FakeSettingsManager


def _ancestor_named(widget: QWidget, name: str):
    current = widget.parentWidget()
    while current is not None:
        if current.objectName() == name:
            return current
        current = current.parentWidget()
    return None


class AppleSettingsLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _window(self, **kwargs):
        from local.settings_window import SettingsWindow

        window = SettingsWindow(FakeIslandWidget(), FakeSettingsManager(), **kwargs)
        self.addCleanup(self._close, window)
        return window

    def _close(self, window):
        window.close()
        window.deleteLater()
        self.app.processEvents()

    def test_new_layout_is_default_and_environment_can_pick_the_old_one(self):
        from local.settings_apple import apple_layout_from_env

        self.assertTrue(apple_layout_from_env({}))
        self.assertTrue(apple_layout_from_env({"GLUSPOT_SETTINGS_LAYOUT": "apple"}))
        self.assertFalse(apple_layout_from_env({"GLUSPOT_SETTINGS_LAYOUT": "classic"}))
        with mock.patch.dict("os.environ", {"GLUSPOT_SETTINGS_LAYOUT": "classic"}):
            self.assertFalse(self._window()._apple_layout)
        with mock.patch.dict("os.environ", {"GLUSPOT_SETTINGS_LAYOUT": ""}):
            self.assertTrue(self._window()._apple_layout)

    def test_groups_put_title_above_rows_in_a_card_and_hints_below(self):
        window = self._window(layout_style="apple")
        card = _ancestor_named(window.owned_playback_switch, "MacCard")
        self.assertIsNotNone(card)
        for switch in (window.system_capture_switch, window.playback_interruption_switch):
            self.assertIs(_ancestor_named(switch, "MacCard"), card)
        group = card.parentWidget()
        self.assertEqual(group.objectName(), "AppleGroup")
        labels = [group.layout().itemAt(i).widget() for i in range(group.layout().count())]
        header = labels[0]
        self.assertIsInstance(header, QLabel)
        self.assertEqual(header.text(), "播放来源")
        self.assertIsNone(_ancestor_named(header, "MacCard"))
        footers = [w for w in labels if isinstance(w, QLabel) and w.property("appleFooter")]
        self.assertTrue(any("后开始的音乐优先播放" in w.text() for w in footers))
        self.assertTrue(all(_ancestor_named(w, "MacCard") is None for w in footers))
        # Every page has a large title.
        for page in window._settings_pages:
            self.assertEqual(page["page"]._apple_heading.text(), page["title"])

    def test_row_controls_share_the_card_right_edge_and_titles_are_not_clipped(self):
        window = self._window(layout_style="apple")
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        window.nav.setCurrentRow(4)
        window.show()
        for width, height in ((688, 496), (980, 700)):
            window.resize(width, height)
            self.app.processEvents()
            card = _ancestor_named(window.placement_mode, "MacCard")
            right = card.mapTo(window, QPoint(card.width(), 0)).x()
            for control in (window.placement_mode, window.finder_bar_style):
                edge = control.mapTo(window, QPoint(control.width(), 0)).x()
                self.assertEqual(right - edge, 16)
                title = control.parentWidget().findChild(QLabel, "RowTitle")
                self.assertGreaterEqual(title.width(), title.fontMetrics().horizontalAdvance(title.text()))
            column = window._settings_pages[4]["page"]._apple_column
            self.assertLessEqual(column.width(), 640)

    def test_bare_buttons_become_link_rows_and_keep_their_callbacks(self):
        window = self._window(layout_style="apple")
        login = mock.Mock()
        window.online_music_login_callback = login
        for button in (window.online_login_button, window.personal_dj_voice_button):
            self.assertTrue(button.property("appleLinkRow"))
            self.assertTrue(button.property("glassFlat"))
            self.assertIsNotNone(button._apple_chevron)
        window.online_login_button.click()
        login.assert_called_once_with()

    def test_detail_row_pushes_a_second_level_view_inside_the_page(self):
        window = self._window(layout_style="apple")
        page = window._settings_pages[5]["page"]
        toggle, body = window._detail_panels[0]
        self.assertEqual(toggle.text(), "软件诊断")
        toggle.click()
        self.assertFalse(body.isHidden())
        self.assertTrue(page._apple_column.isHidden())
        self.assertFalse(page._apple_detail_host.isHidden())
        self.assertTrue(page.isAncestorOf(window._diagnostic_export_status))
        back = page._apple_detail_host.findChild(QPushButton, "AppleBackButton")
        self.assertIsNotNone(back)
        back.click()
        self.assertFalse(toggle.isChecked())
        self.assertTrue(body.isHidden())
        self.assertFalse(page._apple_column.isHidden())

    def test_runtime_switch_rebuilds_the_other_layout_on_the_same_page(self):
        settings = FakeSettingsManager()
        from local.settings_window import SettingsWindow

        window = SettingsWindow(FakeIslandWidget(), settings, layout_style="apple")
        self.addCleanup(self._close, window)
        window.nav.setCurrentRow(3)
        window._toggle_settings_layout()
        self.assertFalse(window._apple_layout)
        self.assertEqual(window.stack.currentIndex(), 3)
        self.assertIsNone(_ancestor_named(window.music_glass_level, "AppleGroup"))
        window.music_glass_level.setValue(72)
        window._toggle_settings_layout()
        self.assertTrue(window._apple_layout)
        self.assertEqual(window.stack.currentIndex(), 3)
        self.assertEqual(window.music_glass_level.value(), 72)
        self.assertEqual(settings.get("v2_music_glass_level"), 72)
        self.assertEqual(len(window._settings_pages), 6)
        self.assertEqual(window._settings_search_matches("动画倍速")[0]["page_index"], 5)

    def test_sidebar_tiles_use_licensed_vector_glyphs(self):
        window = self._window(layout_style="apple")
        paths = [Path(page["icon_path"]) for page in window._settings_pages]
        self.assertEqual([path.name for path in paths], ["musical-notes.svg", "play-circle.svg", "chatbox-ellipses.svg",
                                                         "color-palette.svg", "location.svg", "settings.svg"])
        self.assertTrue(all(path.exists() and path.parent.name == "ionicons" for path in paths))
        self.assertIn("MIT", (paths[0].parent / "LICENSE").read_text(encoding="utf-8"))

    def test_sidebar_corner_is_concentric_with_the_window_corner(self):
        from local.settings_apple import AppleSidebarPanel, AppleWindowShell

        window_centre = 0.5 + AppleWindowShell.RADIUS
        sidebar_centre = AppleSidebarPanel.INSET + AppleSidebarPanel.RADIUS
        self.assertAlmostEqual(window_centre, sidebar_centre)

    def test_traffic_lights_are_small_dots_equally_spaced_from_the_sidebar_corner(self):
        from local.mac_style import MacTrafficCapsule

        window = self._window(layout_style="apple")
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        window.resize(860, 640)
        window.show()
        self.app.processEvents()
        traffic = window.findChild(MacTrafficCapsule)
        side = window.findChild(QWidget, "MacSidebar")
        face = side.glass_rect()
        self.assertEqual([button.size().width() for button in traffic.buttons], [12, 12, 12])
        first = traffic.buttons[0].geometry().translated(traffic.pos())
        self.assertEqual(first.left() - face.left(), 10)
        self.assertEqual(first.top() - face.top(), 10)
        self.assertEqual(window.search_input.mapTo(side, QPoint()).x(), first.left())
        self.assertEqual(traffic.buttons[1].x() - traffic.buttons[0].geometry().right() - 1, 8)
        centre = traffic.buttons[0].geometry().center()
        search = window.search_input.mapTo(side, QPoint())
        traffic._set_expanded(True)
        traffic._animation.setCurrentTime(traffic._animation.duration())
        self.assertEqual([button.size().width() for button in traffic.buttons], [16, 16, 16])
        self.assertEqual([button._reveal for button in traffic.buttons], [1.0] * 3)
        self.assertEqual(traffic.buttons[0].geometry().center(), centre)
        self.assertTrue(all(traffic.rect().contains(button.geometry()) for button in traffic.buttons))
        self.assertEqual(window.search_input.mapTo(side, QPoint()), search)

    def test_switches_and_lamps_use_the_apple_style_and_animate(self):
        from local.ios_widgets import IosSwitch
        from local.settings_window import StatusLamp

        window = self._window(layout_style="apple")
        switches = window.findChildren(IosSwitch)
        self.assertTrue(switches and all(switch.apple_style() for switch in switches))
        lamps = window.findChildren(StatusLamp)
        self.assertTrue(lamps and all(lamp._apple_style for lamp in lamps))
        switch = window.motion_cover_switch
        self.assertEqual(switch.minimumHeight(), IosSwitch.APPLE_CONTROL_HEIGHT)
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        window.nav.setCurrentRow(3)
        window.show()
        self.app.processEvents()
        before = switch.isChecked()
        switch.setChecked(not before)
        self.assertEqual(switch._slide.state(), switch._slide.State.Running)
        switch._slide.setCurrentTime(switch._slide.duration())
        self.assertAlmostEqual(switch._position, 1.0 if not before else 0.0)
        classic = self._window(layout_style="classic")
        self.assertFalse(any(s.apple_style() for s in classic.findChildren(IosSwitch)))

    def test_cards_follow_the_shared_glass_level(self):
        from PySide6.QtGui import QImage

        window = self._window(layout_style="apple")
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        window.resize(860, 640)
        window.show()
        self.app.processEvents()
        card = _ancestor_named(window.owned_playback_switch, "MacCard")

        def centre_alpha():
            image = QImage(card.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with mock.patch.object(type(window), "_settings_dark_mode", return_value=True):
                card.render(image, QPoint(), card.rect(), QWidget.RenderFlag(0))
            return image.pixelColor(card.width() // 2, 3).alpha()

        window.music_glass_level.setValue(0)
        clear = centre_alpha()
        window.music_glass_level.setValue(100)
        solid = centre_alpha()
        self.assertGreater(solid, clear)


if __name__ == "__main__":
    unittest.main()
