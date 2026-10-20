"""Login/library controllers reuse the visible handoff rather than two overlays."""
from __future__ import annotations

import unittest

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from local.music_library_controller import MusicLibraryController
from local.music_library_window import MusicLibraryWindow
from local.music_navigation_transition import MusicNavigationTransition, capture_navigation_frame
from local.playback_entry_controller import PlaybackEntryController
from local.playback_entry_window import PlaybackEntryWindow
from tests.test_music_library_controller import Island, Player, Service
from tests.test_playback_entry_controller import FakeSettings, FakeSystem, FakeTasks


class NavigationSharedRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = FakeSettings(completed=True, owned=True, system=False)
        self.island, self.service, self.player = Island(), Service(), Player()
        self.entry_window, self.library_window = PlaybackEntryWindow(), MusicLibraryWindow()
        for page in (self.entry_window, self.library_window):
            page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.entry = PlaybackEntryController(self.settings, FakeSystem(), self.island, self.service,
            window=self.entry_window, tasks=FakeTasks())
        self.entry._navigation = MusicNavigationTransition()
        self.entry._navigation.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.library = MusicLibraryController(self.settings, self.service, self.player,
            self.island, self.entry, window=self.library_window, tasks=FakeTasks(), art_tasks=FakeTasks())
        self.entry.login_callback = self.library.open_source

    def tearDown(self):
        self.library.close()
        self.entry.close()
        for page in (self.entry_window, self.library_window):
            page.deleteLater()
        self.app.processEvents()

    def test_existing_entry_overlay_is_reused_by_library(self):
        self.assertIs(self.entry._navigation, self.library._navigation)
        self.assertFalse(self.entry._navigation.isVisible())

    def test_login_to_library_midpoint_reverse_retargets_same_visible_overlay(self):
        nav = self.library._navigation
        self.library_window.show()
        self.app.processEvents()
        self.entry._library_origin = capture_navigation_frame(self.library_window)
        self.library_window.hide()
        self.entry.show_login()
        nav._prepare_pending()
        nav._animation.stop()
        nav._advance(.4)
        old_origin, old_target = nav._origin_frame, nav._target_frame
        self.entry.enter_account('netease')
        self.assertTrue(nav.isVisible())
        self.assertIs(nav._origin_frame, old_origin)
        self.assertIs(nav._target_frame, old_target)
        self.assertEqual(nav._fraction, .4)
        self.assertIs(nav._pending_start[2], self.library_window)
        nav._prepare_pending()
        nav._animation.stop()
        self.assertIs(nav._destination, self.library_window)
        self.assertIs(self.library._navigation, self.entry._navigation)
        nav.cancel()
        self.assertTrue(self.library_window.isEnabled())
        self.assertEqual(self.library_window.windowOpacity(), 1.)

    def test_rapid_library_login_library_before_target_capture_keeps_one_pending(self):
        nav = self.library._navigation
        self.library_window.show()
        self.app.processEvents()
        self.library.switch_source()
        origin = nav._origin_frame
        self.assertIs(nav._pending_start[2], self.entry_window)
        self.entry.enter_account('qq')
        self.assertIs(nav._pending_start[2], self.library_window)
        self.assertIs(nav._origin_frame, origin)
        self.assertTrue(self.entry_window.isEnabled())
        self.assertEqual(self.entry_window.windowOpacity(), 1.)
        nav._prepare_pending()
        nav._animation.stop()
        nav.cancel()
        self.assertIsNone(nav._pending_start)
        self.assertIsNone(nav._destination)


if __name__ == '__main__':
    unittest.main()
