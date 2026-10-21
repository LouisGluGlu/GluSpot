"""Library cover-first I/O and deliberate playlist artwork acquisition."""
import unittest
from unittest.mock import patch

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from api.online_music import CatalogPage, OnlinePlaylist
from tests import test_music_library_controller as library_fixture
from tests import test_owned_queue_artwork as owned_fixture


class LibraryArtworkPriorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    setUp = library_fixture.MusicLibraryControllerTests.setUp
    tearDown = library_fixture.MusicLibraryControllerTests.tearDown
    open = library_fixture.MusicLibraryControllerTests.open

    def prepare_catalog(self, count=70):
        self.playlists = tuple(OnlinePlaylist("netease", str(i), f"Playlist {i}",
            artwork_url=f"https://fixture.invalid/list-{i}.png") for i in range(count))
        self.service.pages["netease", 0] = CatalogPage(self.playlists, count, False)
        self.open()

    def complete_all_covers(self):
        seen = set()
        image = QImage(32, 32, QImage.Format.Format_RGB32)
        image.fill(QColor("#789abc"))
        for _ in range(100):
            jobs = [(token, action) for token, action in self.art_tasks.jobs if token not in seen]
            if not jobs:
                return
            self.assertLessEqual(self.controller._art_inflight, 2)
            for token, _ in jobs:
                seen.add(token)
                self.art_tasks.completed.emit(token, image, None)
        self.fail("cover requests did not settle")

    def test_folded_catalog_prefetches_all_playlist_covers_without_loading_any_songs(self):
        self.prepare_catalog()
        self.window.set_library_expanded(False, animate=False)
        self.complete_all_covers()
        downloaded = {token[2] for token, _ in self.art_tasks.jobs}
        expected = {f"playlist:netease:{i}" for i in range(70)}
        self.assertEqual(downloaded, expected)
        self.assertTrue(all(not self.window._artwork[key].isNull() for key in expected))
        self.assertFalse(any(call[0] in {"tracks", "daily", "fm"} for call in self.service.calls))
        self.assertFalse(self.player.calls)
        self.controller._prefetch_catalog_artwork()
        self.assertEqual({token[2] for token, _ in self.art_tasks.jobs}, downloaded)

    def test_distant_playlist_metadata_never_blocks_the_catalog_or_adds_workers(self):
        self.prepare_catalog(180)
        self.assertFalse(self.window._loading)
        self.assertEqual(self.controller._art_inflight, 2)
        self.assertGreater(len(self.controller._catalog_art_pending), 100)
        self.assertLessEqual(len(self.controller._art_queue), 32)
        self.assertFalse(any(call[0] == "tracks" for call in self.service.calls))

    def test_source_change_cancels_unstarted_cover_fetch_and_rejects_old_completion(self):
        self.prepare_catalog(40)
        old_jobs = tuple(self.art_tasks.jobs)
        self.open("qq")
        with patch("local.music_library_controller.fetch_online_thumbnail", side_effect=AssertionError("stale fetch")):
            for token, action in old_jobs:
                self.assertTrue(action().isNull())
                self.art_tasks.completed.emit(token, QImage(8, 8, QImage.Format.Format_RGB32), None)
        self.assertFalse(self.controller._catalog_art_pending)
        self.assertFalse(self.window._artwork)

    def test_thumbnail_failure_retries_twice_without_scroll_and_keeps_audio_unchanged(self):
        self.prepare_catalog(1)
        track = self.player.track
        for attempt in range(3):
            token, _ = self.art_tasks.jobs[-1]
            self.art_tasks.completed.emit(token, QImage(), "timeout")
            self.assertIs(self.player.track, track)
            if attempt < 2:
                self.controller._art_retry_timer.stop()
                due = max(self.controller._art_retry_at.values())
                with patch("local.music_library_controller.time.monotonic", return_value=due + .01):
                    self.controller._retry_art()
        self.assertEqual(len(self.art_tasks.jobs), 3)
        self.assertFalse(self.controller._art_retry_at)

    def test_visible_skeleton_stops_when_covers_arrive_and_on_hide(self):
        self.prepare_catalog(3)
        self.window._sync_artwork_skeleton()
        self.assertTrue(self.window._artwork_skeleton_timer.isActive())
        self.complete_all_covers()
        self.assertFalse(self.window._artwork_skeleton_timer.isActive())
        row = self.window._catalog[-1]
        self.window.set_artwork(row.key, QImage())
        self.assertTrue(self.window._artwork_skeleton_timer.isActive())
        self.window.hide()
        self.assertFalse(self.window._artwork_skeleton_timer.isActive())


class PlaylistArtworkPriorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    setUp = owned_fixture.OwnedQueueArtworkTests.setUp
    tearDown = owned_fixture.OwnedQueueArtworkTests.tearDown
    complete = owned_fixture.OwnedQueueArtworkTests.complete
    drain = owned_fixture.OwnedQueueArtworkTests.drain

    def start_playlist(self, songs=None):
        songs = self.songs if songs is None else songs
        self.drain()
        self.owned.stop()
        self.artwork.jobs.clear()
        self.owned.service.playlist_tracks = lambda *args, **kwargs: CatalogPage(songs, len(songs), False)
        self.assertTrue(self.owned.play_playlist("netease", "selected"))
        token, action = self.owned.tasks.jobs[-1]
        self.owned.tasks.completed.emit(token, action(), None)
        self.token = self.provider.get_owned_playback_queue()[0]

    def test_deliberate_playlist_play_fills_all_known_artwork_with_two_workers_and_no_visible_hint(self):
        self.start_playlist(self.songs[:60])
        self.assertTrue(self.owned._art_prefetch_all)
        self.assertEqual(len(self.owned._art_window), 60)
        self.assertFalse(self.owned._art_visible_keys)
        self.drain()
        self.assertEqual({token[2][1] for token, _ in self.artwork.jobs}, {str(i) for i in range(60)})
        self.assertEqual(self.provider.get_owned_queue_artwork(self.songs[59]), b"art:59")

    def test_background_album_thumbnails_keep_playlist_order_after_current_and_neighbors(self):
        self.start_playlist(self.songs[:20])
        self.drain()
        background = [token[2][1] for token, _ in self.artwork.jobs
            if self.owned._art_request_targets.get(token[3], self.owned._art_cache_targets.get(token[2])) == 320]
        self.assertEqual(background, [str(i) for i in range(2, 20)])
        self.assertEqual(self.owned._art_cache_targets[self.owned._art_key(self.songs[10])], 320)

    def test_queue_replacement_cancels_all_playlist_artwork_and_does_not_enable_search_song_batch(self):
        self.start_playlist(self.songs[:20])
        tokens = tuple(self.artwork.jobs)
        self.owned.play_queue((self.songs[70], self.songs[71]))
        self.assertFalse(self.owned._art_prefetch_all)
        self.assertFalse(self.owned._art_sequential_keys)
        with patch("local.owned_music_player.fetch_online_artwork", side_effect=AssertionError("stale download")):
            for _, action in tokens:
                self.assertIsNone(action())

    def test_background_preload_retains_byte_cap_and_never_redownloads_evicted_covers_forever(self):
        self.owned.ARTWORK_CACHE_MAX_BYTES = 40
        self.start_playlist(self.songs[:60])
        self.drain()
        self.assertLessEqual(sum(len(value[0]) for value in self.owned._art_cache.values()), 40)
        count = len(self.artwork.jobs)
        self.owned._pump_artwork()
        self.assertEqual(len(self.artwork.jobs), count)
        self.assertIn(self.owned._art_key(self.songs[0]), self.owned._art_cache)


if __name__ == "__main__":
    unittest.main()
