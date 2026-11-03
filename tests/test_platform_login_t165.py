"""Real account routes and UI-only upcoming platform entries."""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from PySide6.QtCore import QRect, QRectF
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from api.online_music import AccountSnapshot, MusicSource, OnlineMusicService
from local.app import LocalIslandApp
from local.playback_entry_controller import PlaybackEntryController
from local.playback_entry_window import ENTRY_SOURCES, PlaybackEntryWindow
from tests.test_playback_entry_controller import FakeIsland, FakeService, FakeSettings, FakeSystem, FakeTasks, FakeWindow


class PlatformEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = PlaybackEntryWindow()
        self.window.show_sources()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.qt.processEvents()

    def test_upcoming_platforms_cannot_dispatch_login_even_with_malformed_catalog(self):
        self.window.set_sources(tuple(SimpleNamespace(source_id=s.source_id, available=True)
                                      for s in ENTRY_SOURCES))
        spy = QSignalSpy(self.window.sourceSelected)
        for source in ENTRY_SOURCES:
            button = self.window.source_buttons[source.source_id]
            if source.login_supported:
                self.assertTrue(button.isEnabled())
            else:
                self.assertFalse(button.isEnabled())
                self.assertEqual(button.availability_status, "即将支持")
                self.assertIn("即将支持", button.accessibleName())
                button.click()
                self.window.select_source(source.source_id)
        self.assertEqual(spy.count(), 0)
        self.assertEqual(self.window.selected_source, "")
        # UI entries do not create accounts or capabilities in the service.
        self.assertEqual({s.source_id for s in ENTRY_SOURCES if s.login_supported},
                         {"netease", "kugou", "qq"})
        self.assertNotIn("spotify", dict(OnlineMusicService._names))
        self.assertNotIn("apple_music", dict(OnlineMusicService._names))
        self.assertEqual(OnlineMusicService._capabilities["qishui"], ())

    def test_all_platforms_and_login_actions_fit_narrow_and_wide_windows(self):
        self.window.set_sources(tuple(SimpleNamespace(source_id=s.source_id, available=True)
                                      for s in ENTRY_SOURCES))
        self.window.select_source("netease")
        self.window._expansion.stop()
        self.window._expanded = 1.0
        for width, height in ((330, 400), (433, 550), (960, 540), (560, 360)):
            with self.subTest(size=(width, height)):
                self.window.setMinimumSize(0, 0)
                self.window.resize(width, height)
                self.window._layout_children()
                bounds = QRectF(self.window.rect())
                buttons = tuple(self.window.source_buttons.values())
                for index, button in enumerate(buttons):
                    self.assertTrue(bounds.contains(QRectF(button.geometry())))
                    self.assertGreaterEqual(button.width(), 44)
                    self.assertGreaterEqual(button.height(), 44)
                    self.assertFalse(button.isHidden())
                    for other in buttons[index + 1:]:
                        self.assertFalse(button.geometry().intersects(other.geometry()))
                    self.assertLess(button.geometry().bottom(), self.window._source_pane_rect().top())
                qr, _detail = self.window._login_content_rects()
                title, helper, message, action = self.window._login_detail_geometry()
                for rect in (qr, title, helper, message, action):
                    self.assertTrue(bounds.contains(rect), rect)
                self.assertGreaterEqual(qr.width(), 96)
                self.assertGreaterEqual(action.width(), 44)
                self.assertFalse(helper.intersects(message))
                self.assertFalse(message.intersects(action))
                self.assertFalse(qr.intersects(action))
                avatar, name, enter = self.window._account_content_rects()
                for rect in (avatar, name, enter):
                    self.assertTrue(bounds.contains(rect), rect)
                self.assertFalse(name.intersects(enter))
                self.assertFalse(avatar.intersects(enter))


class PlatformBackRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = LocalIslandApp.__new__(LocalIslandApp)
        self.app.settings = {"v2_owned_playback_enabled": True, "v2_online_music_source": "kugou"}
        self.app.owned_player = SimpleNamespace(track=SimpleNamespace(track_id="owned:netease:123"))
        self.app.music_library = SimpleNamespace(_source="qq", _opening=None)
        self.app.online_music = SimpleNamespace(account_status=Mock(side_effect=lambda source:
            AccountSnapshot(source, logged_in=source == "qq")))
        self.app.window = SimpleNamespace(music_fullscreen_overlay=None, _music_surface_active=False,
            _music_library_active=False, hide=Mock(), _restore_island_from_music_surface=Mock())
        navigation = SimpleNamespace(origin_frame=Mock(return_value="origin"))
        self.app.playback_entry = SimpleNamespace(_navigation=navigation, show_login=Mock())

    def test_other_authenticated_platform_does_not_bypass_current_platform_login(self):
        self.assertTrue(self.app._music_login_required())
        self.app.online_music.account_status.assert_called_once_with("netease")
        self.assertTrue(self.app._open_music_login())
        self.app.playback_entry.show_login.assert_called_once_with("netease")
        self.assertEqual(self.app.playback_entry._library_origin, "origin")

    def test_pending_library_selection_precedes_old_playing_track(self):
        self.app.music_library._source = "kugou"
        self.app.music_library._opening = object()
        self.assertEqual(self.app._music_login_source(), "kugou")
        self.assertTrue(self.app._music_login_required())

    def test_signed_in_current_platform_returns_to_existing_library_callback(self):
        self.app.owned_player.track.track_id = "owned:qq:123"
        self.assertFalse(self.app._music_login_required())
        self.assertFalse(self.app._open_music_login())
        self.app.playback_entry.show_login.assert_not_called()

    def test_no_track_uses_library_then_saved_supported_source(self):
        self.app.owned_player.track = None
        self.assertEqual(self.app._music_login_source(), "qq")
        self.app.music_library._source = ""
        self.assertEqual(self.app._music_login_source(), "kugou")
        for upcoming in ("spotify", "apple_music", "qishui", "unknown"):
            self.app.settings["v2_online_music_source"] = upcoming
            self.assertEqual(self.app._music_login_source(), "netease")

    def test_owned_playback_off_does_not_request_account_status(self):
        self.app.settings["v2_owned_playback_enabled"] = False
        self.assertFalse(self.app._music_login_required())
        self.app.online_music.account_status.assert_not_called()


class PlatformControllerGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_forged_available_placeholders_never_call_account_login_or_prepare(self):
        service, tasks, window = FakeService(), FakeTasks(), FakeWindow()
        service.sources = tuple(MusicSource(source, source, True)
                                for source in ("qishui", "spotify", "apple_music"))
        controller = PlaybackEntryController(FakeSettings(), FakeSystem(), FakeIsland(), service,
            window=window, tasks=tasks, login_callback=Mock())
        try:
            controller.show_login("spotify")
            tasks.run(tasks.latest("sources"))
            tasks.run(tasks.latest("prepare"))
            self.assertEqual(controller._sources, ())
            # Exercise the direct controller boundary as well as the window.
            controller._sources = service.sources
            jobs_before = len(tasks.jobs)
            for source in ("qishui", "spotify", "apple_music"):
                controller.select_source(source)
                controller.begin_login(source)
                controller.enter_account(source)
            self.assertEqual(len(tasks.jobs), jobs_before)
            controller.login_callback.assert_not_called()
            self.assertEqual(service.prepared, [])
            self.assertEqual([call[0] for call in service.calls], ["sources"])
        finally:
            controller.close()
