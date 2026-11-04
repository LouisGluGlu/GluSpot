"""Artwork recovery remains autonomous, bounded and attached to the right song."""
from pathlib import Path
import threading
import time
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.models import ColorToken
from api.online_music import OnlineSong
from api.online_music_artwork import OnlineArtworkError
from local.owned_music_player import OwnedMusicPlayer
from tests.test_owned_playback import Player, Tasks


def picture(side, height=None):
    image = QImage(side, height or side, QImage.Format.Format_RGB32)
    image.fill(0xffbc2030)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(data)


class OwnedArtworkRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.artwork = Tasks()
        self.owned = OwnedMusicPlayer(SimpleNamespace(stream_url=lambda *args: None, lyrics=lambda *args: None),
            LocalLyricsStore(Path(self.temp.name)), player=Player(), dj_player=Player(), audio=object(), dj_audio=object(),
            tasks=Tasks(), artwork_tasks=self.artwork, prefetch_tasks=Tasks(), stream_cache=None)
        self.song = OnlineSong("netease", "1", "Current", artwork_url="https://p1.music.126.net/object.jpg?param=64y64")
        self.colors = ColorToken(180, 30, 40), ColorToken(80, 20, 30)

    def tearDown(self):
        self.owned.close()
        self.temp.cleanup()

    def complete(self, content=None, error=None):
        token = self.artwork.jobs[-1][0]
        image = QImage.fromData(content) if content else QImage()
        self.artwork.completed.emit(token, (content, *self.colors, min(image.width(), image.height())) if content else None, error)
        return token

    def retry(self):
        self.owned._art_retry_timer.stop()
        due = max(self.owned._art_retry_at.values(), default=0)
        with patch("local.owned_music_player.time.monotonic", return_value=due + .01):
            self.owned._pump_artwork()

    def test_one_network_failure_recovers_without_surface_poll_or_song_change(self):
        self.owned.play_queue((self.song,))
        identity, lyrics = self.owned.track.track_id, self.owned.track.lyrics
        self.complete(error="network")
        self.assertEqual(self.owned.track.album_art_lookup_state, "pending")
        self.assertTrue(self.owned._art_retry_timer.isActive())
        self.retry()
        self.assertEqual(self.owned.track.album_art_lookup_state, "pending")
        self.complete(picture(640))
        self.assertEqual(len(self.artwork.jobs), 2)
        self.assertEqual((self.owned.track.track_id, self.owned.track.lyrics), (identity, lyrics))
        self.assertEqual(self.owned.track.album_art_lookup_state, "original")
        self.assertEqual(QImage.fromData(self.owned.track.album_art_bytes).width(), 640)

    def test_corrupt_http_success_can_retry_then_publish_complete_image(self):
        self.owned.play_queue((self.song,))
        self.complete(error="invalid_image")
        self.retry()
        self.complete(picture(640))
        self.assertEqual(len(self.artwork.jobs), 2)
        self.assertEqual(self.owned.track.album_art_lookup_state, "original")

    def test_corrupt_images_and_rejected_workers_keep_two_retry_limit(self):
        for rejected in (False, True):
            with self.subTest(rejected=rejected):
                self.owned.stop()
                self.artwork.jobs.clear()
                self.artwork.accepted = not rejected
                self.owned.play_queue((self.song,))
                for _ in range(3):
                    if not rejected:
                        self.complete(error="invalid_image")
                    self.retry()
                self.assertEqual(len(self.artwork.jobs), 3)
                self.assertFalse(self.owned._art_retry_at)
                self.assertEqual(self.owned.track.album_art_lookup_state, "failed")

    def test_network_timeout_and_incomplete_get_three_bounded_backoffs_before_failure(self):
        for error in ("network", "timeout", "incomplete"):
            with self.subTest(error=error):
                self.owned.stop()
                self.artwork.jobs.clear()
                self.owned.play_queue((self.song,))
                key = self.owned._art_key(self.song)
                for attempt, delay in enumerate((.75, 1.5, 2.25), start=1):
                    with patch("local.owned_music_player.time.monotonic", return_value=100.):
                        self.complete(error=error)
                    self.assertEqual(self.owned._art_retry_at[key], 100. + delay)
                    self.assertEqual(self.owned.track.album_art_lookup_state, "pending")
                    self.retry()
                    self.assertEqual(len(self.artwork.jobs), attempt + 1)
                    self.assertEqual(self.owned.track.album_art_lookup_state, "pending")
                self.complete(error=error)
                self.assertFalse(self.owned._art_retry_at)
                self.assertEqual(self.owned.track.album_art_lookup_state, "failed")
                self.owned._pump_artwork()
                self.assertEqual(len(self.artwork.jobs), 4)

    def test_last_network_retry_can_recover_without_showing_terminal_failure(self):
        states = []
        self.owned.changed.connect(lambda: states.append(self.owned.track.album_art_lookup_state)
                                   if self.owned.track else None)
        self.owned.play_queue((self.song,))
        for error in ("timeout", "incomplete", "network"):
            self.complete(error=error)
            self.retry()
        self.complete(picture(640))
        self.assertNotIn("failed", states)
        self.assertEqual(states[-1], "original")

    def test_rejected_initial_worker_stays_pending_until_its_budget_expires(self):
        self.artwork.accepted = False
        self.owned.play_queue((self.song,))
        self.assertEqual(self.owned.track.album_art_lookup_state, "pending")
        self.retry()
        self.assertEqual(self.owned.track.album_art_lookup_state, "pending")
        self.retry()
        self.assertEqual(self.owned.track.album_art_lookup_state, "failed")

    def test_terminal_failures_do_not_retry(self):
        for error in ("invalid_response", "access_denied", "not_found", None):
            with self.subTest(error=error):
                self.owned.play_queue((self.song,))
                count = len(self.artwork.jobs)
                self.complete(error=error)
                self.assertFalse(self.owned._art_retry_at)
                self.owned._pump_artwork()
                self.assertEqual(len(self.artwork.jobs), count)

    def test_thumbnail_survives_upgrade_failure_then_becomes_original(self):
        self.owned.play_queue((self.song,))
        thumbnail = picture(128)
        self.complete(thumbnail)
        self.assertEqual(self.owned.track.album_art_bytes, thumbnail)
        self.assertEqual(self.owned.track.album_art_source, "thumbnail")
        self.retry()
        self.complete(error="network")
        self.assertEqual(self.owned.track.album_art_bytes, thumbnail)
        self.assertEqual(self.owned.track.track_id, "owned:netease:1")
        self.retry()
        original = picture(640)
        self.complete(original)
        self.assertEqual(self.owned.track.album_art_bytes, original)
        self.assertEqual(self.owned.track.album_art_lookup_state, "original")

    def test_repeated_tiny_fallback_is_bounded_and_never_downgrades_preview(self):
        self.owned.play_queue((self.song,))
        thumbnail = picture(128)
        self.complete(thumbnail)
        for _ in range(2):
            self.retry()
            self.complete(picture(64))
        self.assertEqual(self.owned.track.album_art_bytes, thumbnail)
        self.assertEqual(len(self.artwork.jobs), 3)
        self.assertFalse(self.owned._art_retry_at)
        self.assertEqual(self.owned.track.album_art_lookup_state, "failed")

    def test_revisit_preserves_thumbnail_provenance_and_exhausted_retry_state(self):
        other = OnlineSong("qq", "2", "Other")
        self.owned.play_queue((self.song, other))
        thumbnail = picture(128)
        self.complete(thumbnail)
        self.owned.send_playback_command("next")
        self.owned.send_playback_command("previous")
        self.assertEqual(self.owned.track.album_art_bytes, thumbnail)
        self.assertEqual(self.owned.track.album_art_source, "thumbnail")
        self.assertEqual(self.owned.track.album_art_lookup_state, "pending")
        for _ in range(2):
            self.retry()
            self.complete(thumbnail)
        self.owned.send_playback_command("next")
        self.owned.send_playback_command("previous")
        self.assertEqual(self.owned.track.album_art_source, "thumbnail")
        self.assertEqual(self.owned.track.album_art_lookup_state, "failed")
        self.assertEqual(len(self.artwork.jobs), 3)

    def test_revisit_during_upgrade_preserves_thumbnail_pending_state(self):
        other = OnlineSong("qq", "2", "Other")
        self.owned.play_queue((self.song, other))
        thumbnail = picture(128)
        self.complete(thumbnail)
        self.retry()
        self.assertFalse(self.owned._art_retry_at)
        self.assertTrue(self.owned._art_running)
        self.owned.send_playback_command("next")
        self.owned.send_playback_command("previous")
        self.assertEqual(self.owned.track.album_art_bytes, thumbnail)
        self.assertEqual(self.owned.track.album_art_source, "thumbnail")
        self.assertEqual(self.owned.track.album_art_lookup_state, "pending")
        self.assertEqual(len(self.artwork.jobs), 2)

    def test_rejected_upgrade_worker_preserves_preview_and_bounds_retry(self):
        self.owned.play_queue((self.song,))
        thumbnail = picture(128)
        self.complete(thumbnail)
        self.artwork.accepted = False
        self.retry()
        self.assertEqual(self.owned.track.album_art_bytes, thumbnail)
        self.assertEqual(self.owned.track.album_art_source, "thumbnail")
        self.assertFalse(self.owned.track.is_playing)

    def test_wide_but_short_result_does_not_replace_sharper_preview(self):
        self.owned.play_queue((self.song,))
        thumbnail = picture(256)
        self.complete(thumbnail)
        self.retry()
        self.complete(picture(512, 32))
        self.assertEqual(self.owned.track.album_art_bytes, thumbnail)

    def test_retry_timer_and_old_completion_cannot_publish_after_queue_replacement(self):
        self.owned.play_queue((self.song,))
        old = self.complete(error="network")
        self.owned.play_queue((OnlineSong("qq", "2", "New"),))
        self.retry()
        self.artwork.completed.emit(old, (picture(640), *self.colors), None)
        self.assertEqual(self.owned.track.track_id, "owned:qq:2")
        self.assertFalse(self.owned.track.album_art_bytes)
        self.assertFalse(self.owned._art_retry_at)

    def test_fetch_worker_preserves_sanitized_transient_evidence(self):
        self.owned.play_queue((self.song,))
        action = self.artwork.jobs[-1][1]
        with patch("local.owned_music_player.fetch_online_artwork", side_effect=OnlineArtworkError("network")), \
                self.assertRaises(OnlineArtworkError) as caught:
            action()
        self.assertEqual(caught.exception.code, "network")

    def test_unreturned_worker_retries_then_exits_pending_with_placeholder(self):
        with patch("local.owned_music_player.time.monotonic", return_value=100.):
            self.owned.play_queue((self.song,))
        expired = []
        for _ in range(4):
            expired.append(self.artwork.jobs[-1][0])
            deadline = min(self.owned._art_request_deadlines.values())
            with patch("local.owned_music_player.time.monotonic", return_value=deadline + .01):
                self.owned._pump_artwork()
            if self.owned._art_retry_at:
                self.retry()
        self.assertEqual(len(self.artwork.jobs), 4)
        self.assertFalse(self.owned._art_running)
        self.assertFalse(self.owned._art_request_deadlines)
        self.assertFalse(self.owned._art_retry_at)
        self.assertFalse(self.owned.track.album_art_bytes)
        self.assertEqual(self.owned.track.album_art_lookup_state, "failed")
        for token in reversed(expired):
            self.artwork.completed.emit(token, (picture(640), *self.colors, 640), None)
        self.assertFalse(self.owned.track.album_art_bytes)

    def test_timeout_retains_thumbnail_and_late_original_cannot_replace_retry(self):
        self.owned.play_queue((self.song,))
        thumbnail = picture(128)
        self.complete(thumbnail)
        self.retry()
        expired = self.artwork.jobs[-1][0]
        deadline = min(self.owned._art_request_deadlines.values())
        with patch("local.owned_music_player.time.monotonic", return_value=deadline + .01):
            self.owned._pump_artwork()
        self.retry()
        retry = self.artwork.jobs[-1][0]
        self.artwork.completed.emit(expired, (picture(640), *self.colors, 640), None)
        self.assertEqual(self.owned.track.album_art_bytes, thumbnail)
        self.assertIn(retry[3], self.owned._art_running)
        self.complete(picture(800))
        self.assertEqual(self.owned.track.album_art_lookup_state, "original")

    def test_new_queue_starts_current_cover_while_old_two_requests_never_return(self):
        other = OnlineSong("netease", "2", "Old next", artwork_url="https://fixture.invalid/2.png")
        self.owned.play_queue((self.song, other))
        old = [token for token, _ in self.artwork.jobs]
        self.assertEqual(len(self.owned._art_running), 2)
        replacement = OnlineSong("qq", "new", "New", artwork_url="https://fixture.invalid/new.png")
        self.owned.play_queue((replacement,))
        self.assertEqual(len(self.owned._art_running), 1)
        self.assertEqual(self.artwork.jobs[-1][0][2], self.owned._art_key(replacement))
        for token in reversed(old):
            self.artwork.completed.emit(token, (picture(640), *self.colors, 640), None)
        self.assertEqual(self.owned.track.track_id, "owned:qq:new")
        self.assertFalse(self.owned.track.album_art_bytes)
        self.complete(picture(800))
        self.assertEqual(self.owned.track.album_art_lookup_state, "original")

    def test_completion_arriving_after_deadline_loses_publication_authority(self):
        self.owned.play_queue((self.song,))
        deadline = min(self.owned._art_request_deadlines.values())
        with patch("local.owned_music_player.time.monotonic", return_value=deadline + .01):
            self.complete(picture(800))
        self.assertFalse(self.owned.track.album_art_bytes)
        self.assertTrue(self.owned._art_retry_at)

    def test_production_artwork_lane_recovers_new_queue_after_two_blocked_old_downloads(self):
        self.owned.close()
        release, started = threading.Event(), threading.Event()
        calls, image = [], picture(640)
        def fetch(url, **_options):
            calls.append(url)
            if "old" in url:
                if sum("old" in item for item in calls) == 2:
                    started.set()
                release.wait(3)
            return image
        with patch.object(OwnedMusicPlayer, "ARTWORK_REQUEST_TIMEOUT_SECONDS", .08), \
                patch("local.owned_music_player.fetch_online_artwork", side_effect=fetch), \
                patch("local.owned_music_player.online_artwork_colors", return_value=self.colors):
            self.owned = OwnedMusicPlayer(SimpleNamespace(stream_url=lambda *args: None, lyrics=lambda *args: None),
                LocalLyricsStore(Path(self.temp.name)), player=Player(), dj_player=Player(), audio=object(), dj_audio=object(),
                tasks=Tasks(), prefetch_tasks=Tasks(), stream_cache=None)
            old = tuple(OnlineSong("netease", str(i), f"Old {i}",
                artwork_url=f"https://fixture.invalid/old-{i}.png") for i in range(2))
            self.owned.play_queue(old)
            deadline = time.monotonic() + 3
            while not started.is_set() and time.monotonic() < deadline:
                self.app.processEvents()
                started.wait(.002)
            self.assertTrue(started.is_set())
            replacement = OnlineSong("qq", "new", "New", artwork_url="https://fixture.invalid/current.png")
            self.owned.play_queue((replacement,))
            try:
                while self.owned.track.album_art_lookup_state != "original" and time.monotonic() < deadline:
                    self.app.processEvents()
                    started.wait(.002)
                self.assertEqual(self.owned.track.album_art_lookup_state, "original")
                self.assertEqual(self.owned.track.track_id, "owned:qq:new")
                self.assertEqual(self.owned.track.album_art_bytes, image)
            finally:
                self.owned.close()
                release.set()

    def test_current_list_preview_deadline_includes_wait_for_busy_artwork_slots(self):
        songs = tuple(OnlineSong("netease", str(i), f"Song {i}",
            artwork_url=f"https://fixture.invalid/{i}.png") for i in range(10))
        with patch("local.owned_music_player.time.monotonic", return_value=100.):
            self.owned.play_queue(songs)
            selected = songs[8]
            key = self.owned._art_key(selected)
            self.owned._art_cache[key] = (picture(320), *self.colors)
            self.owned._art_sizes[key] = 320
            self.owned._art_targets[key] = self.owned.ARTWORK_THUMBNAIL_SIDE
            self.owned._art_cache_targets[key] = self.owned.ARTWORK_THUMBNAIL_SIDE
            self.owned.select_queue_index(8, self.owned._queue_view_serial)
        self.assertNotIn((self.owned._queue_epoch, key), self.owned._art_running.values())
        with patch("local.owned_music_player.time.monotonic", return_value=130.01):
            self.owned._pump_artwork()
        self.assertEqual(self.owned.track.album_art_source, "thumbnail")
        self.assertEqual(self.owned.track.album_art_lookup_state, "failed")
        self.assertEqual(self.owned._art_targets[key], self.owned.ARTWORK_THUMBNAIL_SIDE)


if __name__ == "__main__":
    unittest.main()
