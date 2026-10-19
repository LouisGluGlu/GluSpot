"""First use follows the same verified Top/Finder edge geometry as the V2 island."""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QApplication

from api.providers import EmptyIslandDataProvider, empty_visual_data
from local.island_stack_visual import IslandStackVisual
from local.playback_entry_controller import PlaybackEntryController
from local.playback_entry_window import PlaybackEntryWindow
from tests.test_playback_entry_controller import FakeService, FakeSettings, FakeSystem, FakeTasks


class EntryPlacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.island = IslandStackVisual(EmptyIslandDataProvider(empty_visual_data()))
        self.island.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.island.timer.stop()
        self.island._force_super_topmost = Mock()
        self.island._pulse_stack = Mock()
        self.island._begin_surface_transition = lambda reason: False
        self.island._refresh_finder_bar_material = Mock()
        self.window = PlaybackEntryWindow()
        self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.settings = FakeSettings(system=True)
        self.controller = PlaybackEntryController(self.settings, FakeSystem(), self.island,
                                                  FakeService(), window=self.window, tasks=FakeTasks())
        self.screen = SimpleNamespace(name=lambda: "entry-fixture", geometry=lambda: QRect(-1920, 0, 1920, 1080),
                                      availableGeometry=lambda: QRect(-1920, 24, 1920, 1056), devicePixelRatio=lambda: 2.)

    def tearDown(self):
        self.controller.close()
        self.island.close()
        self.window.deleteLater()
        self.island.deleteLater()
        self.app.processEvents()

    def start(self):
        with patch.object(QGuiApplication, "screenAt", return_value=self.screen):
            self.controller.start(installer_handoff=QRect(-1100, 0, 340, 52))
        self.window._page_morph.setCurrentTime(self.window._page_morph.duration())

    def presence(self, *, enabled=True):
        # Physical 48 px on this 2x monitor is the real logical 24 px bar.
        snapshot = {"hwnd": 1, "pid": 2, "enabled": True, "visible": True,
                    "physical_rect": [-1920, 0, 1920, 48], "physical_resting_rect": [-1920, 0, 1920, 48],
                    "physical_monitor_rect": [-1920, 0, 1920, 2160], "monitor_name": "entry-fixture"}
        self.island.mydockfinder_running = True
        with patch("local.island_stack_visual.QGuiApplication.screens", return_value=[self.screen]):
            self.island.set_finder_bar_presence([snapshot] if enabled else [])

    def refresh(self):
        with patch.object(QGuiApplication, "screenAt", return_value=self.screen):
            self.controller._refresh_choices_placement()
        self.window._page_morph.setCurrentTime(self.window._page_morph.duration())

    def test_choices_touch_display_top_instead_of_reserved_work_area(self):
        self.start()
        placement = self.window._choices_placement
        self.assertEqual(placement.mode, "top")
        self.assertEqual(self.window.y(), self.screen.geometry().top())
        self.assertNotEqual(self.window.y(), self.screen.availableGeometry().top())
        self.assertEqual(self.window.size(), QRect(0, 0, 370, 176).size())
        self.assertTrue(placement.shell.contains(QPointF(1, 1)))
        self.assertFalse(placement.shell.contains(QPoint(0, 175)))
        self.assertEqual(self.window.selection, (True, True))
        self.assertTrue(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(self.controller._placement_timer.isActive())

    def test_running_dock_without_verified_bar_keeps_top_choice_presentation(self):
        self.island.mydockfinder_running = True
        self.start()
        self.assertEqual(self.window._choices_placement.mode, "top")
        self.assertEqual(self.window.y(), 0)

    def test_verified_finder_uses_mapped_bounds_and_shared_extended_cradle(self):
        self.presence()
        self.start()
        placement = self.window._choices_placement
        self.assertEqual(placement.mode, "finder")
        self.assertEqual(self.island.finder_bar_rect, QRectF(-1920, 0, 1920, 24))
        self.assertEqual(placement.content.size(), QRectF(0, 0, 370, 176).size())
        unit = self.island._u
        self.assertEqual(placement.geometry.y() + placement.content.y(), 24 + unit(4))
        self.assertGreater(placement.geometry.width(), placement.content.width() + unit(36))
        self.assertLessEqual(placement.geometry.top(), 24)
        content_global = placement.content.translated(placement.geometry.x(), placement.geometry.y())
        expected = self.island._myfinder_expanded_shell_path(content_global.adjusted(-unit(9), -unit(8), unit(9), unit(8)))
        self.assertEqual(expected.boundingRect().toAlignedRect(), placement.geometry)
        for button in (self.window.owned_button, self.window.system_button, self.window.continue_button):
            self.assertTrue(placement.content.contains(QRectF(button.geometry())))
        image = self.window.grab().toImage()
        dpr = image.devicePixelRatio()
        # The outer extension is painted independently of the black choices.
        outer = image.pixelColor(round(5 * dpr), round(12 * dpr))
        self.assertGreater(outer.alpha(), 0)
        self.assertNotEqual(outer, QColor("#000000"))

    def test_open_choices_follow_new_bar_and_removal_without_confirm(self):
        self.start()
        self.presence()
        self.refresh()
        self.assertEqual(self.window._choices_placement.mode, "finder")
        self.assertEqual(self.window.page, "modes")
        self.assertFalse(self.settings.get("v2_playback_onboarding_completed"))
        self.island.finder_bar_center_ratio = .75
        self.refresh()
        self.assertAlmostEqual(self.window.geometry().center().x(), -480, delta=1)
        self.presence(enabled=False)
        self.refresh()
        self.assertEqual(self.window._choices_placement.mode, "top")
        self.assertEqual(self.window.y(), 0)
        self.assertEqual(self.window.selection, (True, True))

    def test_unchanged_bar_repairs_native_centering_of_top_and_finder_choices(self):
        self.start()
        for finder in (False, True):
            with self.subTest(finder=finder):
                if finder:
                    self.presence()
                    self.refresh()
                expected = QRect(self.window._choices_placement.geometry)
                self.window.move(-1100, 420)
                self.assertNotEqual(self.window.geometry(), expected)
                self.refresh()
                self.assertEqual(self.window.geometry(), expected)
                self.assertTrue(self.window._page_morph_to.isNull())
                self.assertEqual(self.window.selection, (True, True))

    def test_native_remap_reapplies_attachment_after_show(self):
        self.presence()
        self.start()
        expected = QRect(self.window._choices_placement.geometry)
        self.window.hide()
        self.window.move(-1100, 420)
        self.window.show()
        self.app.processEvents()
        self.assertEqual(self.window.geometry(), expected)

    def test_same_placement_does_not_interrupt_initial_expansion(self):
        with patch.object(QGuiApplication, "screenAt", return_value=self.screen):
            self.controller.start(installer_handoff=QRect(-1100, 0, 340, 52))
            self.window._page_morph.setCurrentTime(120)
            current = QRect(self.window.geometry())
            target = QRect(self.window._page_morph_target)
            self.controller._refresh_choices_placement()
        self.assertEqual(self.window.geometry(), current)
        self.assertEqual(self.window._page_morph_target, target)
        self.assertFalse(self.window._page_morph_to.isNull())

    def test_login_keeps_existing_handoff_and_stops_first_use_following(self):
        self.start()
        self.window.continue_button.click()
        self.assertEqual(self.window.page, "sources")
        self.assertFalse(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.controller._placement_timer.isActive())
        self.assertTrue(self.settings.get("v2_playback_onboarding_completed"))
        self.assertFalse(self.window._page_morph_to.isNull())
        target = QRect(self.window._page_morph_target)
        self.presence()
        self.controller._refresh_choices_placement()
        self.assertEqual(self.window._page_morph_target, target)


if __name__ == "__main__":
    unittest.main()
