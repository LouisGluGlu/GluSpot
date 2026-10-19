"""A playlist's first authorized song does not wait for every catalog page."""
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import CatalogPage, OnlineSong, StreamInfo
from local.owned_music_player import OwnedMusicPlayer
from tests.test_owned_buffered_playback import FixtureCache
from tests.test_owned_playback import Player, Tasks


class OwnedPlaylistStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.tasks, self.artwork, self.prefetch, self.downloads = (Tasks() for _ in range(4))
        self.qt = Player()
        self.songs = tuple(OnlineSong("netease", str(index), f"Song {index}", duration_seconds=120)
                           for index in range(6))
        self.pages = {0: CatalogPage(self.songs[:2], 100, True),
                      100: CatalogPage(self.songs[2:4], 200, True),
                      200: CatalogPage(self.songs[4:], 300, False)}
        self.service = SimpleNamespace(playlist_tracks=Mock(side_effect=lambda source, playlist, *, limit, offset: self.pages[offset]),
            stream_url=Mock(side_effect=lambda source, song: StreamInfo(source, song, "https://fixture.invalid/music", True)),
            lyrics=Mock(return_value=None))
        self.owned = OwnedMusicPlayer(self.service, LocalLyricsStore(self.root / "lyrics"),
            player=self.qt, dj_player=Player(), audio=object(), dj_audio=object(), tasks=self.tasks,
            artwork_tasks=self.artwork, prefetch_tasks=self.prefetch, stream_cache=None)

    def tearDown(self):
        self.owned.close()
        self.folder.cleanup()

    def perform(self, tasks, kind):
        token, action = next(job for job in reversed(tasks.jobs) if job[0][0] == kind)
        result = action()
        tasks.completed.emit(token, result, None)
        return token, result

    def open(self, *, authorize=True):
        self.assertTrue(self.owned.play_playlist("netease", "fixture-playlist"))
        self.perform(self.tasks, "catalog")
        if authorize:
            self.perform(self.tasks, "stream")

    def ids(self):
        return tuple(song.song_id for song in self.owned.queue)

    def retry(self):
        self.owned._playlist_retry_timer.stop()
        self.owned._submit_playlist_page()

    def one_first_song(self):
        self.pages[0] = CatalogPage(self.songs[:1], 100, True)
        self.pages[100] = CatalogPage(self.songs[1:3], 200, False)

    def test_first_song_authorization_precedes_remaining_catalog_calls(self):
        self.open(authorize=False)
        self.assertEqual(self.service.playlist_tracks.call_count, 1)
        self.assertEqual(self.ids(), ("0", "1"))
        self.assertEqual(self.owned.track.track_id, "owned:netease:0")
        self.assertEqual(self.owned.status, "loading_stream")
        self.assertFalse(self.prefetch.jobs)
        self.service.stream_url.assert_not_called()
        self.perform(self.tasks, "stream")
        self.assertEqual(self.owned.status, "playing")
        self.assertEqual(self.service.playlist_tracks.call_count, 1)
        self.assertEqual(self.prefetch.jobs[-1][0][0], "playlist_page")

    def test_buffered_playback_still_waits_for_complete_authorized_audio_before_continuation(self):
        self.owned.stream_cache = FixtureCache(self.root)
        self.owned.download_tasks = self.downloads
        self.downloads.completed.connect(self.owned._completed)
        self.open()
        self.assertEqual(self.owned.status, "loading_stream")
        self.assertTrue(self.qt.source().isEmpty())
        self.assertFalse(self.prefetch.jobs)
        self.assertFalse(self.owned.stream_cache.preparations)
        self.perform(self.downloads, "audio")
        self.assertEqual(self.owned.status, "playing")
        self.assertTrue(self.qt.source().isLocalFile())
        self.assertEqual(len(self.owned.stream_cache.preparations), 1)
        self.assertEqual(self.prefetch.jobs[-1][0][0], "playlist_page")

    def test_background_pages_keep_current_decoder_clock_lyrics_and_pause(self):
        self.open()
        self.qt.setPosition(23000)
        self.qt.positionChanged.emit(23000)
        self.owned.track = replace(self.owned.track, lyrics=("Current lyric",), lyrics_source="fixture")
        self.owned.send_playback_command("toggle_play_pause")
        token = self.owned.playback_queue_token()
        before = (self.owned._epoch, self.owned._stream_epoch, self.qt.source(), self.qt.playbackState(),
                  self.owned.track.position_seconds, self.owned.track.lyrics, self.owned.status)
        with patch.object(self.qt, "stop", wraps=self.qt.stop) as stop, \
                patch.object(self.qt, "play", wraps=self.qt.play) as play:
            self.perform(self.prefetch, "playlist_page")
            self.perform(self.prefetch, "playlist_page")
        stop.assert_not_called(); play.assert_not_called()
        self.assertEqual(self.ids(), tuple(str(index) for index in range(6)))
        self.assertEqual((self.owned._epoch, self.owned._stream_epoch, self.qt.source(), self.qt.playbackState(),
            self.owned.track.position_seconds, self.owned.track.lyrics, self.owned.status), before)
        self.assertNotEqual(self.owned.playback_queue_token(), token)
        self.assertIsNone(self.owned._playlist_continuation)

    def test_listener_next_insertion_survives_background_pages_in_original_playlist_order(self):
        self.open()
        addition = OnlineSong("qq", "added", "Listener addition")
        self.assertTrue(self.owned.enqueue_next(addition))
        self.perform(self.prefetch, "playlist_page")
        self.perform(self.prefetch, "playlist_page")
        self.assertEqual(self.ids(), ("0", "added", "1", "2", "3", "4", "5"))
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.track.track_id, "owned:qq:added")
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.track.track_id, "owned:netease:1")

    def test_source_replacement_cancels_queued_page_before_network_and_rejects_late_result(self):
        self.open()
        token, action = self.prefetch.jobs[-1]
        self.owned.play_queue((OnlineSong("qq", "other", "Other source"),))
        calls = self.service.playlist_tracks.call_count
        self.assertIsNone(action())
        self.assertEqual(self.service.playlist_tracks.call_count, calls)
        self.prefetch.completed.emit(token, self.pages[100], None)
        self.assertEqual(self.ids(), ("other",))
        self.assertIsNone(self.owned._playlist_continuation)

    def test_new_pending_catalog_already_cancels_old_playlist_continuation(self):
        self.open()
        token, action = self.prefetch.jobs[-1]
        before = self.owned.queue
        self.owned._catalog("daily", "netease", lambda: self.songs[4:])
        self.assertIsNone(action())
        self.prefetch.completed.emit(token, self.pages[100], None)
        self.assertEqual(self.owned.queue, before)
        self.assertEqual(self.owned.status, "loading_catalog")

    def test_next_at_loading_boundary_waits_without_wrapping_or_replaying_old_audio(self):
        self.one_first_song(); self.open()
        pending = self.prefetch.jobs[-1][0]
        self.assertTrue(self.owned.send_playback_command("next"))
        self.assertTrue(self.owned._playlist_waiting_next)
        self.assertEqual(self.owned.track.track_id, "owned:netease:0")
        self.assertEqual(self.owned.status, "loading_catalog")
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.StoppedState)
        self.assertTrue(self.qt.source().isEmpty())
        self.assertEqual(len(self.prefetch.jobs), 1)
        self.perform(self.prefetch, "playlist_page")
        self.assertFalse(self.owned._playlist_waiting_next)
        self.assertEqual(self.owned.track.track_id, "owned:netease:1")
        self.assertEqual(self.owned.status, "loading_stream")
        epoch = self.owned._epoch
        self.prefetch.completed.emit(pending, self.pages[100], None)
        self.assertEqual(self.owned._epoch, epoch)

    def test_pause_while_waiting_for_next_page_is_retained_when_authorized_song_arrives(self):
        self.one_first_song(); self.open()
        self.owned.send_playback_command("next")
        self.owned.send_playback_command("toggle_play_pause")
        self.perform(self.prefetch, "playlist_page")
        self.perform(self.tasks, "stream")
        self.assertEqual(self.owned.track.track_id, "owned:netease:1")
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.assertFalse(self.owned.track.is_playing)

    def test_added_next_song_can_satisfy_an_already_waiting_playlist_boundary(self):
        self.one_first_song(); self.open()
        self.owned.send_playback_command("next")
        self.assertTrue(self.owned.enqueue_next(OnlineSong("qq", "added", "Addition")))
        self.assertEqual(self.owned.track.track_id, "owned:qq:added")
        self.assertFalse(self.owned._playlist_waiting_next)
        self.perform(self.prefetch, "playlist_page")
        self.assertEqual(self.ids(), ("0", "added", "1", "2"))
        self.assertEqual(self.owned.track.track_id, "owned:qq:added")

    def test_transient_tail_failure_has_two_retries_and_keeps_healthy_current_music(self):
        self.open()
        before = (self.owned.track.track_id, self.qt.source(), self.qt.playbackState(), self.owned.status)
        for attempt in range(3):
            token = self.prefetch.jobs[-1][0]
            self.prefetch.completed.emit(token, None, "network")
            if attempt < 2:
                self.assertTrue(self.owned._playlist_retry_timer.isActive())
                self.retry()
        self.assertEqual(len(self.prefetch.jobs), 3)
        self.assertIsNone(self.owned._playlist_continuation)
        self.assertFalse(self.owned._playlist_retry_timer.isActive())
        self.assertEqual((self.owned.track.track_id, self.qt.source(), self.qt.playbackState(), self.owned.status), before)

    def test_failed_tail_at_waiting_boundary_reports_failure_without_restarting_first_song(self):
        self.one_first_song(); self.open()
        self.owned.send_playback_command("next")
        self.prefetch.completed.emit(self.prefetch.jobs[-1][0], None, "auth_required")
        self.assertEqual(self.owned.status, "catalog_unavailable")
        self.assertEqual(self.owned.track.track_id, "owned:netease:0")
        self.assertTrue(self.qt.source().isEmpty())
        self.assertFalse(self.owned._playlist_waiting_next)

    def test_waiting_boundary_reentrant_new_queue_keeps_new_source_and_request(self):
        self.one_first_song(); self.open()
        replacement = (OnlineSong("qq", "replacement", "Replacement"),)
        replaced = []
        def replace_on_stop():
            if self.owned._playlist_waiting_next and not replaced:
                replaced.append(True)
                self.owned.play_queue(replacement)
                self.perform(self.tasks, "stream")
        self.owned.changed.connect(replace_on_stop)
        self.assertFalse(self.owned.send_playback_command("next"))
        self.assertEqual(self.owned.track.track_id, "owned:qq:replacement")
        self.assertEqual(self.owned.status, "playing")
        self.assertFalse(self.qt.source().isEmpty())
        self.assertIsNone(self.owned._playlist_continuation)

    def test_page_arriving_during_song_switch_defers_more_pages_until_new_audio_is_ready(self):
        self.open()
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.status, "loading_stream")
        self.perform(self.prefetch, "playlist_page")
        self.assertEqual(self.service.playlist_tracks.call_count, 2)
        self.assertEqual(len(self.prefetch.jobs), 1)
        self.assertIsNone(self.owned._playlist_continuation.request)
        self.perform(self.tasks, "stream")
        self.assertEqual(len(self.prefetch.jobs), 2)
        self.assertEqual(self.prefetch.jobs[-1][0][0], "playlist_page")

    def test_cancelled_replacement_reissues_original_playlist_tail_with_new_token(self):
        self.open()
        old_token, old_action = self.prefetch.jobs[-1]
        original_queue = self.owned.queue
        self.owned._catalog("daily", "qq", lambda: (OnlineSong("qq", "new", "New song"),))
        self.assertTrue(self.owned.cancel_pending_selection())
        self.assertIs(self.owned.queue, original_queue)
        new_token, _ = self.prefetch.jobs[-1]
        self.assertNotEqual(old_token, new_token)
        self.assertEqual(old_token[-1], new_token[-1])
        calls = self.service.playlist_tracks.call_count
        self.assertIsNone(old_action())
        self.assertEqual(self.service.playlist_tracks.call_count, calls)
        self.prefetch.completed.emit(old_token, self.pages[100], None)
        self.assertIs(self.owned.queue, original_queue)
        self.perform(self.prefetch, "playlist_page")
        self.perform(self.prefetch, "playlist_page")
        self.assertEqual(self.ids(), tuple(str(index) for index in range(6)))

    def test_failed_replacement_restores_original_playlist_pagination_without_reusing_old_reply(self):
        self.open()
        old_token, old_action = self.prefetch.jobs[-1]
        self.owned._catalog("daily", "qq", lambda: ())
        token = self.tasks.jobs[-1][0]
        self.tasks.completed.emit(token, None, "auth_required")
        self.assertEqual(self.owned.status, "catalog_unavailable")
        self.assertEqual(self.owned.failure_reason, "auth_required")
        self.assertIsNotNone(self.owned._playlist_continuation)
        self.assertNotEqual(old_token, self.prefetch.jobs[-1][0])
        self.assertIsNone(old_action())
        self.perform(self.prefetch, "playlist_page")
        self.perform(self.prefetch, "playlist_page")
        self.assertEqual(self.ids(), tuple(str(index) for index in range(6)))
        self.assertEqual(self.owned.status, "catalog_unavailable")
        self.assertEqual(self.owned.failure_reason, "auth_required")

    def test_first_request_skips_empty_filtered_pages_until_real_first_song(self):
        self.pages[0] = CatalogPage((), 100, True)
        self.pages[100] = CatalogPage(self.songs[:1], 200, False)
        self.open(authorize=False)
        self.assertEqual([call.kwargs["offset"] for call in self.service.playlist_tracks.call_args_list], [0, 100])
        self.assertEqual(self.ids(), ("0",))
        self.assertEqual(self.owned.status, "loading_stream")
        self.assertIsNone(self.owned._playlist_continuation)

    def test_empty_continuation_with_real_advancing_cursor_does_not_drop_later_songs(self):
        self.pages[100] = CatalogPage((), 200, True)
        self.open()
        self.perform(self.prefetch, "playlist_page")
        self.assertEqual(self.ids(), ("0", "1"))
        self.perform(self.prefetch, "playlist_page")
        self.assertEqual(self.ids(), ("0", "1", "4", "5"))

    def test_existing_hundred_page_budget_is_preserved_for_background_pagination(self):
        self.owned.PLAYLIST_MAX_PAGES = 3
        self.pages[200] = CatalogPage(self.songs[4:], 300, True)
        self.open()
        self.perform(self.prefetch, "playlist_page")
        self.perform(self.prefetch, "playlist_page")
        self.assertEqual(self.service.playlist_tracks.call_count, 3)
        self.assertEqual(len(self.prefetch.jobs), 2)
        self.assertIsNone(self.owned._playlist_continuation)

    def test_mismatched_source_page_cannot_append_to_the_open_playlist(self):
        self.open()
        before = self.owned.queue, self.qt.source(), self.qt.playbackState()
        token = self.prefetch.jobs[-1][0]
        self.prefetch.completed.emit(token, CatalogPage((OnlineSong("qq", "wrong", "Wrong source"),), 200, False), None)
        self.assertEqual((self.owned.queue, self.qt.source(), self.qt.playbackState()), before)
        self.assertIsNone(self.owned._playlist_continuation)

    def test_shuffle_append_preserves_planned_history_and_adds_new_unique_songs(self):
        self.owned._random.shuffle = lambda values: values.reverse()
        self.owned.set_playback_mode("shuffle")
        self.pages[100] = CatalogPage((self.songs[1], self.songs[2]), 200, False)
        self.open()
        history = tuple(self.owned._shuffle_history), tuple(self.owned._shuffle_pending)
        self.perform(self.prefetch, "playlist_page")
        self.assertEqual((tuple(self.owned._shuffle_history), tuple(self.owned._shuffle_pending)), history)
        self.assertEqual(self.ids(), ("0", "1", "1", "2"))
        self.assertEqual(list(self.owned._shuffle_bag).count(3), 1)


if __name__ == "__main__":
    unittest.main()
