"""Shared native page geometry and independent search presentation regressions."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from api.online_music import AccountSnapshot, CatalogPage, OnlineSong
from local.music_library_controller import MusicLibraryController
from local.music_library_window import MusicLibraryWindow
from local.music_surface_window import MusicSurfaceWindow
from local.playback_entry_window import PlaybackEntryWindow
from tests.test_music_centered_layout import _Owner
from tests.test_music_library_controller import Entry, Island, Player, Service
from tests.test_playback_entry_controller import FakeSettings, FakeTasks


class SharedMusicPageGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = FakeSettings(completed=True, owned=True, system=False)
        self.service, self.player, self.island = Service(), Player(), Island()
        self.entry = Entry()
        self.entry.window = PlaybackEntryWindow()
        self.window = MusicLibraryWindow()
        self.tasks = FakeTasks()
        self.controller = MusicLibraryController(self.settings, self.service, self.player,
            self.island, self.entry, window=self.window, tasks=self.tasks, art_tasks=FakeTasks())
        for window in (self.window, self.entry.window):
            window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)

    def tearDown(self):
        self.controller.close()
        self.entry.window.close()
        self.entry.window.deleteLater()
        self.window.deleteLater()
        self.app.processEvents()

    def test_library_resize_and_login_qr_expansion_share_one_rectangle(self):
        self.window.show()
        self.app.processEvents()
        expected = QRect(80, 100, 680, 500)
        self.window.setGeometry(expected)
        self.assertEqual(self.entry.window._shared_music_geometry, expected)
        self.entry.window.show_sources()
        self.assertEqual(self.entry.window.geometry(), expected)
        for fraction in (0., .25, .5, .75, 1.):
            self.entry.window._set_expanded(fraction)
            self.assertEqual(self.entry.window.geometry(), expected)
        self.assertEqual(self.entry.window.minimumSize(), self.window.minimumSize())
        self.assertEqual((self.window.minimumWidth(), self.window.minimumHeight()), (330, 400))

    def test_login_resize_updates_library_and_player_normal_geometry(self):
        self.island.music_fullscreen_overlay = SimpleNamespace(_normal_geometry=QRect())
        self.entry.window.show_sources()
        self.entry.window.show()
        self.app.processEvents()
        expected = QRect(70, 85, 750, 560)
        self.entry.window.setGeometry(expected)
        self.assertEqual(self.controller._library_geometry, expected)
        self.assertEqual(self.island.music_fullscreen_overlay._normal_geometry, expected)
        self.controller._restore_library_geometry()
        self.assertEqual(self.window.geometry(), expected)

    def test_player_resize_is_used_on_next_library_or_login_navigation(self):
        expected = QRect(45, 90, 870, 590)
        self.island.music_fullscreen_overlay = SimpleNamespace(_normal_geometry=expected)
        self.entry.window._prepare_shared_music_geometry()
        self.entry.window.show_sources()
        self.controller._restore_library_geometry()
        self.assertEqual(self.entry.window.geometry(), expected)
        self.assertEqual(self.window.geometry(), expected)

    def test_minimum_pages_render_without_losing_resize_edges(self):
        self.window.set_account(AccountSnapshot("netease", True, "fixture", "Synthetic account", state="verified"))
        self.entry.window.show_sources()
        self.entry.window.selected_source = "netease"
        self.entry.window._expanded = 1.
        for window in (self.window, self.entry.window):
            for width, height in ((330, 400), (680, 500), (960, 540)):
                window.resize(width, height)
                image = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                window.render(image)
                self.assertEqual(image.size(), window.size())
                self.assertGreater(image.pixelColor(width // 2, height // 2).alpha(), 0)
                self.assertEqual(window._page_resize_edges(QPointF(width - 1, height - 1)),
                                 Qt.Edge.RightEdge | Qt.Edge.BottomEdge)

    def test_fallback_edge_drag_changes_both_dimensions_and_stops_at_minimum(self):
        self.window.setGeometry(80, 80, 680, 500)
        def event(local, global_point, buttons=Qt.MouseButton.LeftButton):
            return SimpleNamespace(position=lambda: QPointF(*local), globalPosition=lambda: QPointF(*global_point),
                button=lambda: Qt.MouseButton.LeftButton, buttons=lambda: buttons, accept=lambda: None)
        with patch.object(self.window, "windowHandle", return_value=None):
            self.assertTrue(self.window._begin_page_resize(event((679, 499), (759, 579))))
        self.assertTrue(self.window._move_page_resize(event((1, 1), (100, 100))))
        self.assertEqual((self.window.width(), self.window.height()), (330, 400))
        self.window._end_page_resize()
        self.assertIsNone(self.window._page_resize_origin)

    def test_songs_are_visible_and_acquiring_covers_before_playlist_reply(self):
        self.controller.open_source("qq")
        self.tasks.run(self.tasks.latest("open"))
        self.service.search_pages["qq", "query", "songs", 0] = CatalogPage((
            OnlineSong("qq", "one", "Immediate song", artwork_url="https://example.test/cover.png"),), 1, False)
        self.window.search.setText("query")
        self.window._search_timer.stop()
        self.controller.search("query")
        jobs = [job for job in self.tasks.jobs if job[0][0] == "search"]
        self.assertEqual(len(jobs), 2)
        self.tasks.run(jobs[0])
        self.assertEqual([row.title for row in self.window.collections], ["Immediate song"])
        self.assertTrue(self.controller._pending)
        self.window._request_visible_artwork()
        self.assertTrue(any(job[0][0] == "art" for job in self.controller.art_tasks.jobs))
        self.tasks.run(jobs[1])
        self.assertFalse(self.controller._pending)

    def test_reverse_search_completion_retains_existing_song_then_playlist_order(self):
        from api.online_music import OnlinePlaylist
        self.controller.open_source("qq")
        self.tasks.run(self.tasks.latest("open"))
        self.service.search_pages["qq", "query", "songs", 0] = CatalogPage((
            OnlineSong("qq", "one", "Song"),), 1, False)
        self.service.search_pages["qq", "query", "playlists", 0] = CatalogPage((
            OnlinePlaylist("qq", "list", "Playlist"),), 1, False)
        self.window.search.setText("query")
        self.window._search_timer.stop()
        self.controller.search("query")
        jobs = [job for job in self.tasks.jobs if job[0][0] == "search"]
        self.tasks.run(jobs[1])
        self.assertEqual([row.title for row in self.window.collections], ["Playlist"])
        self.tasks.run(jobs[0])
        self.assertEqual([row.title for row in self.window.collections], ["Song", "Playlist"])


class PlayerSharedDestinationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_player_enters_final_shared_geometry_before_first_destination_capture(self):
        owner = _Owner()
        expected = QRect(45, 80, 660, 500)
        owner._music_page_geometry = expected
        surface = MusicSurfaceWindow(owner)
        surface.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        # This test verifies handoff ordering, not the workstation's available
        # height (which becomes 540 logical pixels at DPR 2 on a 1080p screen).
        area = patch.object(surface, "_available_geometry", return_value=QRect(0, 0, 1920, 1080))
        area.start()
        try:
            surface.show_from_island(fullscreen=False, origin_geometry=QRect(), origin_snapshot=QImage())
            self.app.processEvents()
            self.assertEqual(surface._normal_geometry, expected)
            self.assertEqual(surface.geometry(), expected)
            image = QImage(surface.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            surface.render(image)
            self.assertGreater(image.pixelColor(330, 250).alpha(), 0)
        finally:
            surface.shutdown()
            owner.close()
            area.stop()
