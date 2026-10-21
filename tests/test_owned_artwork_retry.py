"""Artwork recovery remains autonomous, bounded and attached to the right song."""
from pathlib import Path
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
        self.assertTrue(self.owned._art_retry_timer.isActive())
        self.retry()
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

    def test_transient_failures_and_rejected_workers_have_two_retry_limit(self):
        for rejected in (False, True):
            with self.subTest(rejected=rejected):
                self.owned.stop()
                self.artwork.jobs.clear()
                self.artwork.accepted = not rejected
                self.owned.play_queue((self.song,))
                for _ in range(3):
                    if not rejected:
                        self.complete(error="timeout")
                    self.retry()
                self.assertEqual(len(self.artwork.jobs), 3)
                self.assertFalse(self.owned._art_retry_at)
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


if __name__ == "__main__":
    unittest.main()
