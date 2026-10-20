"""Library placeholders and recovery preserve real provider/account authority."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from api.online_music import CatalogPage, OnlinePlaylist, OnlineSong
from local.music_library_controller import MusicLibraryController
from local.music_library_window import MusicLibraryWindow
from tests.test_music_library_controller import Entry, Island, Player, Service, run_search
from tests.test_playback_entry_controller import FakeSettings, FakeTasks


class LibraryLoadingRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = FakeSettings(completed=True, owned=True, system=False)
        self.service, self.player = Service(), Player()
        self.tasks, self.art_tasks = FakeTasks(), FakeTasks()
        self.window = MusicLibraryWindow()
        self.controller = MusicLibraryController(self.settings, self.service, self.player, Island(), Entry(),
            window=self.window, tasks=self.tasks, art_tasks=self.art_tasks)

    def tearDown(self):
        self.controller.close()
        self.window.deleteLater()
        self.app.processEvents()

    def open(self, source="qq"):
        self.controller.open_source(source)
        self.tasks.run(self.tasks.latest("open"))
        self.app.processEvents()

    def due(self, kind):
        self.controller._catalog_retry_at[kind] = 0.
        self.controller._retry_catalog()

    def test_initial_catalog_uses_visible_skeleton_and_preserves_profile(self):
        self.open()
        self.assertTrue(self.window._catalog_skeleton.isVisible())
        self.assertTrue(self.window._artwork_skeleton_timer.isActive())
        self.assertFalse(self.window.empty_action.isVisible())
        self.assertFalse(self.window.recovery_action.isVisible())
        self.assertEqual(self.window._account.user_id, "qq-user")
        # Loading details do not reserve the old bottom text lane.
        height = self.window.scroll.height()
        self.window.set_loading(True, "不同长度的网络状态不会改变可见内容区域")
        self.assertEqual(self.window.scroll.height(), height)

    def test_skeleton_light_sweep_changes_pixels_and_stops_when_complete(self):
        self.open()
        frames = []
        for seconds in (.3, .6):
            image = QImage(self.window._catalog_skeleton.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with patch("local.music_library_window.time.monotonic", return_value=seconds):
                self.window._catalog_skeleton.render(image)
            frames.append(image)
        self.assertNotEqual(frames[0], frames[1])
        self.tasks.run(self.tasks.latest("initial_catalog"))
        self.tasks.run(self.tasks.latest("recommended_catalog"))
        self.assertFalse(self.window._catalog_skeleton.isVisible())
        self.assertFalse(self.window._artwork_skeleton_timer.isActive())

    def test_failed_initial_catalog_automatically_retries_same_page(self):
        self.open()
        token, _ = self.tasks.latest("initial_catalog")
        self.tasks.completed.emit(token, None, "private-network-error")
        self.assertEqual(self.window._error, "")
        self.assertTrue(self.window._catalog_skeleton.isVisible())
        self.assertFalse(self.window.empty_action.isVisible())
        self.assertFalse(self.window.recovery_action.isVisible())
        self.assertTrue(self.controller._catalog_retry_timer.isActive())
        self.service.pages["qq", 0] = CatalogPage((OnlinePlaylist("qq", "new", "Recovered"),), 1, False)
        self.due("initial_catalog")
        self.tasks.run(self.tasks.latest("initial_catalog"))
        self.assertEqual([row.title for row in self.window.collections], ["Recovered"])
        self.assertNotIn("initial_catalog", self.controller._catalog_retry_at)

    def test_repeated_failures_back_off_without_busy_loop_or_confirmation(self):
        self.open()
        for attempt, delay in enumerate((1., 2., 4., 8., 16., 30., 30.)):
            with patch("local.music_library_controller.time.monotonic", return_value=100.):
                self.tasks.completed.emit(self.tasks.latest("initial_catalog")[0], None, "network")
            self.assertEqual(self.controller._catalog_retry_at["initial_catalog"], 100. + delay)
            jobs = len(self.tasks.jobs)
            with patch("local.music_library_controller.time.monotonic", return_value=100.):
                self.controller._retry_catalog()
            self.assertEqual(len(self.tasks.jobs), jobs)
            self.due("initial_catalog")

    def test_hidden_page_pauses_retry_and_show_resumes_it(self):
        self.open()
        self.tasks.completed.emit(self.tasks.latest("initial_catalog")[0], None, "network")
        self.window.hide()
        self.app.processEvents()
        jobs = len(self.tasks.jobs)
        self.due("initial_catalog")
        self.assertEqual(len(self.tasks.jobs), jobs)
        self.assertFalse(self.controller._catalog_retry_timer.isActive())
        self.window.show()
        self.controller._arm_catalog_retry()
        self.assertTrue(self.controller._catalog_retry_timer.isActive())

    def test_source_change_clears_retry_and_rejects_prior_reply(self):
        self.open()
        old_token, old_action = self.tasks.latest("initial_catalog")
        self.tasks.completed.emit(old_token, None, "network")
        self.controller.open_source("kugou")
        self.assertFalse(self.controller._catalog_retry_at)
        self.assertIsNone(old_action())
        self.tasks.completed.emit(old_token, CatalogPage((OnlinePlaylist("qq", "stale", "Stale"),), 1, False), None)
        self.assertEqual(self.window.collections, ())

    def test_stalled_page_keeps_cursor_and_automatically_recovers(self):
        self.open()
        self.tasks.completed.emit(self.tasks.latest("initial_catalog")[0], CatalogPage((), 0, True), None)
        self.assertIn("initial_catalog", self.controller._catalog_retry_at)
        self.assertEqual(self.controller._catalog_offset, 0)
        self.service.pages["qq", 0] = CatalogPage((OnlinePlaylist("qq", "one", "One"),), 50, True)
        self.due("initial_catalog")
        self.tasks.run(self.tasks.latest("initial_catalog"))
        self.controller.load_more()
        self.tasks.completed.emit(self.tasks.latest("catalog")[0], None, "network")
        self.due("catalog")
        self.tasks.run(self.tasks.latest("catalog"))
        self.assertIn(("playlists", "qq", 50, 50), self.service.calls)
        self.assertEqual([row.title for row in self.window.collections], ["One"])

    def test_partial_recommendation_recovery_keeps_personal_catalog_and_search(self):
        self.open("netease")
        self.service.pages["netease", 0] = CatalogPage((OnlinePlaylist("netease", "personal", "Personal"),), 50, False)
        self.tasks.run(self.tasks.latest("initial_catalog"))
        self.tasks.completed.emit(self.tasks.latest("recommended_catalog")[0], None, "network")
        self.window.search.setText("Query")
        self.window._search_timer.stop()
        self.controller.search("Query")
        song = OnlineSong("netease", "song", "Result")
        self.service.search_pages["netease", "Query", "songs", 0] = CatalogPage((song,), 1, False)
        run_search(self.tasks)
        self.service.recommended_pages["netease", 0] = CatalogPage((OnlinePlaylist("netease", "recommended", "Recommended"),), 1, False)
        self.due("recommended")
        self.tasks.run(self.tasks.latest("recommended_catalog"))
        self.assertEqual([row.title for row in self.window.collections], ["Result"])
        self.assertEqual({row.title for row in self.controller._collections if row.kind == "playlist"}, {"Personal", "Recommended"})

    def test_partial_search_retries_only_failed_kind_without_repeating_good_page(self):
        self.open()
        self.tasks.run(self.tasks.latest("initial_catalog"))
        self.service.search_pages["qq", "Query", "songs", 0] = CatalogPage((OnlineSong("qq", "song", "Song"),), 30, True)
        self.service.search_pages["qq", "Query", "playlists", 0] = RuntimeError("network")
        self.window.search.setText("Query")
        self.window._search_timer.stop()
        self.controller.search("Query")
        run_search(self.tasks)
        self.assertEqual([row.title for row in self.window.collections], ["Song"])
        self.assertEqual(self.window._error, "")
        self.assertTrue(self.window._catalog_skeleton.isVisible())
        self.service.search_pages["qq", "Query", "playlists", 0] = CatalogPage((OnlinePlaylist("qq", "list", "Playlist"),), 1, False)
        previous_calls = len(self.service.calls)
        self.due("search")
        run_search(self.tasks)
        self.assertEqual(self.service.calls[previous_calls:], [("search", "qq", "Query", "playlists", 30, 0)])
        self.assertEqual([row.title for row in self.window.collections], ["Song", "Playlist"])

    def test_query_change_cancels_old_search_retry(self):
        self.open()
        self.tasks.run(self.tasks.latest("initial_catalog"))
        self.window.search.setText("old")
        self.window._search_timer.stop()
        self.controller.search("old")
        self.tasks.completed.emit(self.tasks.latest("search")[0], None, "network")
        self.window.search.setText("new")
        self.window._search_timer.stop()
        self.assertNotIn("search", self.controller._catalog_retry_at)
        self.assertNotIn("search", self.window._catalog_loading)

    def test_catalog_retry_does_not_release_inflight_search_guard(self):
        self.open()
        self.tasks.completed.emit(self.tasks.latest("initial_catalog")[0], None, "network")
        self.window.search.setText("Query")
        self.window._search_timer.stop()
        self.controller.search("Query")
        self.assertTrue(self.controller._pending)
        self.due("initial_catalog")
        self.assertTrue(self.controller._catalog_pending)
        self.assertTrue(self.controller._pending)
        self.tasks.completed.emit(self.tasks.latest("initial_catalog")[0], None, "network")
        self.assertTrue(self.controller._pending)
        jobs = len(self.tasks.jobs)
        self.controller.load_more()
        self.assertEqual(len(self.tasks.jobs), jobs)

    def test_no_url_daily_and_fm_covers_still_animate_while_resolving(self):
        self.open("netease")
        self.window.set_library_expanded(True, animate=False)
        self.tasks.run(self.tasks.latest("initial_catalog"))
        self.app.processEvents()
        covers = [cover for shelf in self.window._shelves for row, cover, _ in shelf.tiles if row.kind in {"daily", "fm"}]
        self.assertEqual(len(covers), 2)
        self.assertTrue(all(cover.loading for cover in covers))
        self.assertTrue(self.window._artwork_skeleton_timer.isActive())

    def test_artwork_fast_retries_continue_slowly_then_recover_without_a_scroll(self):
        playlist = OnlinePlaylist("qq", "art", "Artwork", artwork_url="https://example.test/art.png")
        self.service.pages["qq", 0] = CatalogPage((playlist,), 1, False)
        self.open()
        self.tasks.run(self.tasks.latest("initial_catalog"))
        key, url = "playlist:qq:art", playlist.artwork_url
        for _ in range(3):
            token, _ = self.art_tasks.latest("art")
            with patch("local.music_library_controller.time.monotonic", return_value=100.):
                self.art_tasks.completed.emit(token, QImage(), "network")
            self.controller._art_retry_at[key, url] = 0.
            self.controller._retry_art()
        token, _ = self.art_tasks.latest("art")
        image = QImage(8, 8, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.red)
        self.art_tasks.completed.emit(token, image, None)
        self.assertFalse(self.window._artwork[key].isNull())
        self.assertNotIn((key, url), self.controller._art_retry_at)

    def test_failed_cover_has_thirty_second_slow_retry_and_scroll_cannot_bypass_it(self):
        self.open("netease")
        self.tasks.run(self.tasks.latest("initial_catalog"))
        job = next(job for job in self.tasks.jobs if job[0][0] == "recommendation_cover" and job[0][2] == "daily")
        for _ in range(3):
            with patch("local.music_library_controller.time.monotonic", return_value=100.):
                self.tasks.completed.emit(job[0], "", "network")
        self.assertEqual(self.controller._recommendation_art_retry_at["daily"], 130.)
        key, url = "daily", "https://example.test/daily.png"
        with patch("local.music_library_controller.time.monotonic", return_value=100.):
            for _ in range(3):
                self.controller._schedule_art_retry(key, url)
            self.assertEqual(self.controller._art_retry_at[key, url], 130.)
            before = len(self.art_tasks.jobs)
            self.controller._queue_art(key, url)
            self.assertEqual(len(self.art_tasks.jobs), before)

    def test_backoff_releases_visible_request_slot_without_cancelling_scheduled_recovery(self):
        playlist = OnlinePlaylist("qq", "art", "Artwork", artwork_url="https://example.test/art.png")
        self.service.pages["qq", 0] = CatalogPage((playlist,), 1, False)
        self.open()
        self.tasks.run(self.tasks.latest("initial_catalog"))
        token, _ = self.art_tasks.latest("art")
        self.art_tasks.completed.emit(token, QImage(), "network")
        key, url = "playlist:qq:art", playlist.artwork_url
        self.window.set_library_expanded(True, animate=False)
        jobs = len(self.art_tasks.jobs)
        self.window._request_visible_artwork()
        self.assertEqual(len(self.art_tasks.jobs), jobs)
        self.assertNotIn(key, self.window._artwork_pending)
        self.assertIn((key, url), self.controller._art_retry_at)
        self.controller._art_retry_at[key, url] = 0.
        self.controller._retry_art()
        self.assertEqual(len(self.art_tasks.jobs), jobs + 1)

    def test_hidden_page_pauses_art_recovery_and_source_change_clears_it(self):
        self.open("netease")
        self.tasks.run(self.tasks.latest("initial_catalog"))
        self.controller._retry_recommendation_cover_later("daily")
        self.assertTrue(self.controller._art_retry_timer.isActive())
        self.window.hide()
        self.app.processEvents()
        before = len(self.tasks.jobs)
        self.controller._recommendation_art_retry_at["daily"] = 0.
        self.controller._retry_art()
        self.assertEqual(len(self.tasks.jobs), before)
        self.assertFalse(self.controller._art_retry_timer.isActive())
        self.controller.open_source("kugou")
        self.assertFalse(self.controller._recommendation_art_retry_at)
        self.assertFalse(self.controller._art_retry_at)

    def test_replaced_art_url_is_not_retried_and_decoder_capacity_stays_bounded(self):
        playlist = OnlinePlaylist("qq", "art", "Artwork", artwork_url="https://example.test/new.png")
        self.service.pages["qq", 0] = CatalogPage((playlist,), 1, False)
        self.open()
        self.tasks.run(self.tasks.latest("initial_catalog"))
        key = "playlist:qq:art"
        self.controller._art_retry_at[key, "https://example.test/old.png"] = 0.
        before = len(self.art_tasks.jobs)
        self.controller._retry_art()
        self.assertEqual(len(self.art_tasks.jobs), before)
        self.assertNotIn((key, "https://example.test/old.png"), self.controller._art_retry_at)
        self.controller._art_inflight = 2
        self.controller._art_retry_at[key, playlist.artwork_url] = 0.
        self.controller._retry_art()
        self.assertEqual(len(self.art_tasks.jobs), before)
        self.assertIn((key, playlist.artwork_url), self.controller._art_retry_at)


if __name__ == "__main__":
    unittest.main()
