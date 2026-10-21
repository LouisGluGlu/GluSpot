"""Bounded playlist covers keep song/account authority and successful retry budgets."""
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from api.online_music import OnlinePlaylist, OnlineSong
from local.song_collection_controller import SongCollectionController
from tests.test_song_collection_controller import Player, Service, Tasks


def image(color="red"):
    value = QImage(24, 24, QImage.Format.Format_ARGB32)
    value.fill(QColor(color))
    return value


class SongCollectionArtworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.songs = (OnlineSong("netease", "9", "one"), OnlineSong("netease", "10", "two"))
        self.player, self.service = Player(self.songs), Service()
        self.tasks, self.art = Tasks(), Tasks()
        self.controller = SongCollectionController(self.service, self.player, tasks=self.tasks, art_tasks=self.art)
        self.addCleanup(self.controller.close)
        self.tasks.run()
        self.track_id = "owned:netease:9"
        self.authority = self.controller.capture_authority(self.track_id)
        self.playlists = tuple(OnlinePlaylist("netease", str(i), f"List {i}", f"https://example.test/{i}.png")
                               for i in range(40))
        self.controller._set(replace(self.controller._state, playlists=self.playlists, playlists_loaded=True))

    def request(self, index=0):
        return self.controller.request_playlist_artwork(self.playlists[index], self.track_id,
                                                         expected_authority=self.authority)

    def cached(self, index=0):
        return self.controller.playlist_artwork(self.playlists[index], self.track_id,
                                                expected_authority=self.authority)

    def retry(self):
        self.controller._art_retry_timer.stop()
        with patch("local.song_collection_controller.time.monotonic", return_value=10**12):
            self.controller._retry_playlist_artwork()

    def test_only_two_workers_run_and_backlog_is_bounded(self):
        for index in range(40):
            self.request(index)
        self.assertEqual(len(self.art.items), 2)
        self.assertEqual(len(self.controller._art_queue), 32)
        with patch("local.song_collection_controller.fetch_online_thumbnail", return_value=image()):
            self.art.run()
        self.assertEqual(len(self.art.items), 2)
        self.assertEqual(len(self.controller._art_queue), 31)

    def test_successful_cover_is_shared_without_repeated_download(self):
        self.assertTrue(self.request())
        self.assertFalse(self.request())
        self.assertTrue(self.cached().isNull())
        with patch("local.song_collection_controller.fetch_online_thumbnail", return_value=image("blue")) as fetch:
            self.art.run()
        self.assertEqual(self.cached().pixelColor(0, 0), QColor("blue"))
        self.assertFalse(self.request())
        fetch.assert_called_once_with(self.playlists[0].artwork_url)

    def test_failed_cover_retries_three_times_then_stops(self):
        self.request()
        for attempt in range(3):
            with patch("local.song_collection_controller.fetch_online_thumbnail", return_value=QImage()):
                self.art.run()
            if attempt < 2:
                self.retry()
        self.assertFalse(self.request())
        self.assertFalse(self.art.items)
        self.assertFalse(self.controller._art_retry_at)

    def test_third_attempt_success_can_be_downloaded_again_after_cache_eviction(self):
        self.request()
        for _ in range(2):
            with patch("local.song_collection_controller.fetch_online_thumbnail", return_value=QImage()):
                self.art.run()
            self.retry()
        with patch("local.song_collection_controller.fetch_online_thumbnail", return_value=image()):
            self.art.run()
        for index in range(128):
            self.controller._cache_playlist_artwork(("netease", f"other{index}", "url"), image())
        self.assertTrue(self.cached().isNull())
        self.assertTrue(self.request(), "successful retry resets the consecutive failure budget")

    def test_matching_library_cache_avoids_new_work(self):
        self.controller._artwork_lookup = Mock(return_value=image("green"))
        self.assertFalse(self.request())
        self.assertEqual(self.cached().pixelColor(0, 0), QColor("green"))
        self.assertFalse(self.art.items)
        self.controller._artwork_lookup.assert_called_once_with(self.playlists[0])

    def test_song_and_account_change_reject_late_results_and_old_gestures(self):
        for changed_account in (False, True):
            with self.subTest(changed_account=changed_account):
                self.request()
                token, action = self.art.items.pop()
                if changed_account:
                    self.service.account = replace(self.service.account)
                else:
                    self.player.select(self.songs[1])
                self.art.completed.emit(token, image(), None)
                self.assertTrue(self.cached().isNull())
                self.assertFalse(self.request())
                self.player.select(self.songs[0])
                while self.tasks.items:
                    self.tasks.run()
                self.authority = self.controller.capture_authority(self.track_id)
                self.controller._set(replace(self.controller._state, playlists=self.playlists))

    def test_changed_playlist_url_rejects_older_pixels(self):
        self.request()
        token, action = self.art.items.pop()
        newer = replace(self.playlists[0], artwork_url="https://example.test/new.png")
        self.controller._set(replace(self.controller._state, playlists=(newer,)))
        self.art.completed.emit(token, image(), None)
        self.assertFalse(self.controller._artwork)

    def test_close_rejects_late_success_and_cancels_retry_timer(self):
        self.request()
        token, action = self.art.items.pop()
        self.controller.close()
        self.art.completed.emit(token, image(), None)
        self.assertFalse(self.controller._artwork)
        self.assertFalse(self.controller._art_retry_timer.isActive())
        self.assertTrue(self.art.closed)


if __name__ == "__main__":
    unittest.main()
