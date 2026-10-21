"""Visible queue artwork stays bounded, recoverable and outside paint I/O."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.models import ColorToken
from api.online_music import OnlineSong
from api.providers import PlaybackSourceProvider
from local.owned_music_player import OwnedMusicPlayer
from tests.test_owned_artwork_retry import picture
from tests.test_owned_playback import Player, Tasks


class OwnedQueueArtworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.artwork = Tasks()
        self.owned = OwnedMusicPlayer(SimpleNamespace(stream_url=lambda *args: None, lyrics=lambda *args: None),
            LocalLyricsStore(Path(self.folder.name)), player=Player(), dj_player=Player(), audio=object(), dj_audio=object(),
            tasks=Tasks(), artwork_tasks=self.artwork, prefetch_tasks=Tasks(), stream_cache=None)
        self.values = {"v2_owned_playback_enabled": True}
        self.provider = PlaybackSourceProvider(SimpleNamespace(suspend_live_audio=Mock(), stop_audio_cast=Mock()),
            self.owned, lambda key, default=None: self.values.get(key, default))
        self.songs = tuple(OnlineSong("netease", str(i), f"Song {i}", duration_seconds=120,
            artwork_url=f"https://fixture.invalid/{i}.png") for i in range(200))
        self.colors = ColorToken(180, 30, 40), ColorToken(80, 20, 30)
        self.notified = Mock()
        self.provider.get_owned_queue_artwork_changed_signal().connect(self.notified)
        self.owned.play_queue(self.songs)
        self.token = self.provider.get_owned_playback_queue()[0]

    def tearDown(self):
        self.provider.get_owned_queue_artwork_changed_signal().disconnect(self.notified)
        self.provider._owned_audio_bridge.close()
        self.owned.close()
        self.folder.cleanup()

    def complete(self, token, content=None, error=None, side=640):
        content = content if content is not None else f"art:{token[2][1]}".encode()
        result = None if error else (content, *self.colors, side)
        self.artwork.completed.emit(token, result, error)

    def drain(self, exclude=()):
        for _ in range(100):
            pending = [token for token, _ in self.artwork.jobs
                       if token[3] in self.owned._art_running and token[2][1] not in exclude]
            if not pending:
                return
            self.assertLessEqual(len(self.owned._art_running), 2)
            for token in pending:
                self.complete(token)
        self.fail("artwork jobs did not settle")

    def retry(self):
        self.owned._art_retry_timer.stop()
        due = max(self.owned._art_retry_at.values(), default=0)
        with patch("local.owned_music_player.time.monotonic", return_value=due + .01):
            self.owned._pump_artwork()

    def hint(self, indices):
        return self.provider.prefetch_owned_queue_artwork(tuple(indices), self.token)

    def test_visible_window_adds_only_two_rows_either_side_and_keeps_current_neighbors(self):
        self.owned.select_queue_index(50, self.token)
        self.assertTrue(self.hint(range(100, 124)))
        expected = set(range(47, 54)) | set(range(98, 126))
        self.assertEqual({int(key[1]) for key in self.owned._art_window}, expected)
        self.assertEqual(len(self.owned._art_visible_keys), 24)
        self.assertEqual(len(self.owned._art_buffer_keys), 4)
        self.assertEqual(len(self.owned._art_window), 35)
        self.assertLessEqual(len(self.owned._art_running), 2)

    def test_repeated_semantically_identical_hint_does_not_refresh_or_restart_workers(self):
        self.assertTrue(self.hint(range(50, 55)))
        before = len(self.artwork.jobs)
        with patch.object(self.owned, "_refresh_artwork_window", side_effect=AssertionError("duplicate window")):
            self.assertTrue(self.provider.prefetch_owned_queue_artwork([54, 53, 52, 51, 50], self.token))
        self.assertEqual(len(self.artwork.jobs), before)

    def test_indices_and_tokens_are_strict_and_invalid_hints_preserve_the_owned_window(self):
        self.hint(range(50, 55))
        before = dict(self.owned._art_window), self.owned._art_visible_hint, len(self.artwork.jobs)
        for indices in (None, "50", (True,), (50.,), ("50",), (-1,), (200,), tuple(range(25))):
            with self.subTest(indices=indices):
                self.assertFalse(self.provider.prefetch_owned_queue_artwork(indices, self.token))
        for token in (True, "1", self.token + 1):
            self.assertFalse(self.provider.prefetch_owned_queue_artwork((50,), token))
        self.assertEqual((self.owned._art_window, self.owned._art_visible_hint, len(self.artwork.jobs)), before)

    def test_distant_visible_rows_fetch_before_far_neighbors_and_getters_stay_memory_only(self):
        self.hint(range(50, 55))
        self.complete(self.artwork.jobs[0][0])
        self.assertEqual(self.artwork.jobs[-1][0][2][1], "50")
        self.drain()
        self.assertEqual(self.provider.get_owned_queue_artwork(self.songs[50]), b"art:50")
        jobs = len(self.artwork.jobs)
        with patch("local.owned_music_player.fetch_online_artwork", side_effect=AssertionError("paint network")):
            for _ in range(3):
                self.assertEqual(self.provider.get_owned_queue_artwork(self.songs[50]), b"art:50")
        self.assertEqual(len(self.artwork.jobs), jobs)
        self.assertTrue(self.notified.called)

    def test_empty_hint_cancels_outside_window_worker_before_fetch_and_rejects_completion(self):
        self.hint((50,))
        self.complete(self.artwork.jobs[0][0])
        token, action = self.artwork.jobs[-1]
        self.assertEqual(token[2][1], "50")
        before = self.notified.call_count
        self.assertTrue(self.hint(()))
        with patch("local.owned_music_player.fetch_online_artwork", side_effect=AssertionError("stale request")):
            self.assertIsNone(action())
        self.complete(token)
        self.assertEqual(self.provider.get_owned_queue_artwork(self.songs[50]), b"")
        self.assertEqual(self.notified.call_count, before)

    def test_queue_insertion_invalidates_visible_hint_and_old_positional_request(self):
        self.hint((50,))
        self.owned.enqueue_next(OnlineSong("netease", "added", "Added"))
        self.assertIsNone(self.owned._art_visible_hint)
        self.assertFalse(self.provider.prefetch_owned_queue_artwork((50,), self.token))
        self.assertNotIn(self.owned._art_key(self.songs[50]), self.owned._art_window)

    def test_visible_transient_failure_retries_twice_without_repaint_reset(self):
        self.hint((50,))
        self.drain(exclude=("50",))
        before = self.notified.call_count
        for attempt in range(3):
            token = next(token for token, _ in reversed(self.artwork.jobs)
                         if token[2][1] == "50" and token[3] in self.owned._art_running)
            self.complete(token, error="network")
            self.assertTrue(self.hint((50,)))
            if attempt < 2:
                self.retry()
        self.assertEqual(sum(token[2][1] == "50" for token, _ in self.artwork.jobs), 3)
        self.assertFalse(self.owned._art_retry_at)
        self.assertEqual(self.notified.call_count, before)
        self.assertEqual(self.provider.get_owned_queue_artwork(self.songs[50]), b"")

    def test_visible_failure_does_not_change_playback_and_success_updates_paused_queue(self):
        self.hint((50,))
        self.drain(exclude=("50",))
        track, source, status = self.owned.track, self.owned.player.source(), self.owned.status
        token = next(token for token, _ in self.artwork.jobs
                     if token[2][1] == "50" and token[3] in self.owned._art_running)
        self.complete(token, error="timeout")
        self.assertEqual((self.owned.track, self.owned.player.source(), self.owned.status), (track, source, status))
        self.notified.reset_mock()
        self.retry()
        token = self.artwork.jobs[-1][0]
        self.complete(token, b"recovered thumbnail", side=320)
        self.notified.assert_called_once_with()
        self.assertEqual(self.provider.get_owned_queue_artwork(self.songs[50]), b"recovered thumbnail")
        self.assertFalse(self.owned.track.is_playing)

    def test_visible_thumbnail_requests_small_known_image_and_bounds_unknown_host_bitmap_on_worker(self):
        self.hint((50,))
        self.complete(self.artwork.jobs[0][0])
        token, action = self.artwork.jobs[-1]
        content = picture(1024)
        with patch("local.owned_music_player.fetch_online_artwork", return_value=content) as fetch, \
                patch("local.owned_music_player.online_artwork_colors", return_value=self.colors):
            result = action()
        fetch.assert_called_once_with(self.songs[50].artwork_url, raise_errors=True, target_side=320)
        image = QImage.fromData(result[0])
        self.assertEqual((image.width(), image.height(), result[3]), (320, 320, 320))
        self.assertEqual(token[2], self.owned._art_key(self.songs[50]))

    def test_selected_list_thumbnail_stays_visible_until_original_upgrade_succeeds(self):
        self.hint((50,))
        self.drain()
        thumbnail = self.provider.get_owned_queue_artwork(self.songs[50])
        self.assertEqual(self.owned._art_cache_targets[self.owned._art_key(self.songs[50])], 320)
        self.owned.select_queue_index(50, self.token)
        self.assertEqual(self.owned.track.album_art_bytes, thumbnail)
        self.assertEqual(self.owned.track.album_art_source, "thumbnail")
        self.assertEqual(self.owned.track.album_art_lookup_state, "pending")
        token = next(token for token, _ in reversed(self.artwork.jobs)
                     if token[2][1] == "50" and token[3] in self.owned._art_running)
        self.complete(token, b"original", side=640)
        self.assertEqual(self.owned.track.album_art_bytes, b"original")
        self.assertEqual(self.owned.track.album_art_source, "original")

    def test_thumbnail_survives_exhausted_selected_original_upgrade_retries(self):
        self.hint((50,))
        self.drain()
        self.owned.select_queue_index(50, self.token)
        self.drain(exclude=("50",))
        thumbnail = self.owned.track.album_art_bytes
        for attempt in range(3):
            token = next(token for token, _ in reversed(self.artwork.jobs)
                         if token[2][1] == "50" and token[3] in self.owned._art_running)
            self.complete(token, error="network")
            if attempt < 2:
                self.retry()
        self.assertEqual(self.owned.track.album_art_bytes, thumbnail)
        self.assertEqual(self.owned.track.album_art_source, "thumbnail")
        self.assertEqual(self.owned.track.album_art_lookup_state, "failed")

    def test_inflight_list_thumbnail_keeps_provenance_after_leaving_window_and_becoming_current(self):
        self.hint((50,))
        self.complete(self.artwork.jobs[0][0])
        token = self.artwork.jobs[-1][0]
        self.assertEqual(token[2][1], "50")
        self.hint((100,))
        self.assertNotIn(token[2], self.owned._art_window)
        self.owned.select_queue_index(50, self.token)
        self.complete(token, b"late thumbnail", side=320)
        self.assertEqual(self.owned.track.album_art_bytes, b"late thumbnail")
        self.assertEqual(self.owned.track.album_art_source, "thumbnail")
        self.assertEqual(self.owned.track.album_art_lookup_state, "pending")
        upgrade = self.artwork.jobs[-1][0]
        self.assertEqual(upgrade[2][1], "50")
        self.assertNotEqual(upgrade[3], token[3])

    def test_byte_cap_preserves_current_and_evicted_visible_rows_do_not_refetch_forever(self):
        self.owned.ARTWORK_CACHE_MAX_BYTES = 40
        self.hint(range(50, 74))
        self.drain()
        self.assertLessEqual(sum(len(value[0]) for value in self.owned._art_cache.values()), 40)
        self.assertIn(self.owned._art_key(self.songs[0]), self.owned._art_cache)
        before = len(self.artwork.jobs)
        self.hint(range(50, 74))
        self.owned._pump_artwork()
        self.assertEqual(len(self.artwork.jobs), before)

    def test_scrolling_away_and_back_reuses_completed_same_queue_thumbnail_without_refetch(self):
        self.hint((50,))
        self.drain()
        thumbnail = self.provider.get_owned_queue_artwork(self.songs[50])
        self.hint((100,))
        self.drain()
        self.assertNotIn(self.owned._art_key(self.songs[50]), self.owned._art_window)
        self.assertEqual(self.provider.get_owned_queue_artwork(self.songs[50]), thumbnail)
        before = len(self.artwork.jobs)
        self.hint((50,))
        self.drain()
        self.assertEqual(self.provider.get_owned_queue_artwork(self.songs[50]), thumbnail)
        self.assertEqual(len(self.artwork.jobs), before)

    def test_byte_pressure_evicts_outside_window_before_new_visible_thumbnail_and_protects_current(self):
        self.hint((50,))
        self.drain()
        retained = self.owned._art_key(self.songs[50])
        current = self.owned._art_key(self.songs[0])
        self.assertIn(retained, self.owned._art_cache)
        before = sum(len(value[0]) for value in self.owned._art_cache.values())
        self.owned.ARTWORK_CACHE_MAX_BYTES = before
        self.hint((100,))
        self.drain()
        self.assertLessEqual(sum(len(value[0]) for value in self.owned._art_cache.values()), before)
        self.assertNotIn(retained, self.owned._art_cache)
        self.assertIn(current, self.owned._art_cache)
        self.assertEqual(self.provider.get_owned_queue_artwork(self.songs[100]), b"art:100")

    def test_same_queue_retained_cache_clears_on_source_replacement_and_rejects_removed_song(self):
        self.hint((50,))
        self.drain()
        self.hint(())
        self.assertTrue(self.provider.get_owned_queue_artwork(self.songs[50]))
        replacement = OnlineSong("qq", "50", "Other source", artwork_url="https://fixture.invalid/other.png")
        self.owned.play_queue((replacement,))
        self.assertFalse(self.owned._art_cache)
        self.assertFalse(self.owned._art_cache_targets)
        self.assertEqual(self.provider.get_owned_queue_artwork(self.songs[50]), b"")
        self.assertFalse(self.provider.prefetch_owned_queue_artwork((50,), self.token))

    def test_inactive_and_closed_provider_reject_hints_and_stale_success_notifications(self):
        self.values["v2_owned_playback_enabled"] = False
        self.assertFalse(self.hint((50,)))
        self.values["v2_owned_playback_enabled"] = True
        token = self.artwork.jobs[0][0]
        self.owned.stop()
        self.complete(token)
        self.assertFalse(self.notified.called)
        self.assertFalse(self.hint((50,)))


if __name__ == "__main__":
    unittest.main()
