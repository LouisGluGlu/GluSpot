"""Memory-only owned queue views and guarded direct song selection."""
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.models import ColorToken
from api.online_music import OnlineSong, StreamInfo
from api.providers import PlaybackSourceProvider
from local.owned_music_player import OwnedMusicPlayer
from tests.test_owned_playback import Player, Tasks
from tests.test_personal_dj_playback import segments


class OwnedQueuePresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.tasks, self.artwork, self.prefetch = Tasks(), Tasks(), Tasks()
        self.qt, self.dj_qt = Player(), Player()
        self.service = SimpleNamespace(stream_url=Mock(side_effect=lambda source, song:
            StreamInfo(source, song, "https://fixture.invalid/music", True)),
            lyrics=Mock(return_value=None), personal_fm=Mock(return_value=()),
            personal_dj=Mock(return_value=()))
        self.owned = OwnedMusicPlayer(self.service, LocalLyricsStore(Path(self.folder.name)),
            player=self.qt, audio=object(), dj_player=self.dj_qt, dj_audio=object(),
            tasks=self.tasks, artwork_tasks=self.artwork, prefetch_tasks=self.prefetch,
            stream_cache=None)
        self.values = {"v2_owned_playback_enabled": True, "v2_system_capture_enabled": True}
        self.system = SimpleNamespace(suspend_live_audio=Mock(), stop_audio_cast=Mock())
        self.provider = PlaybackSourceProvider(self.system, self.owned,
            lambda key, default=None: self.values.get(key, default))
        self.songs = tuple(OnlineSong("netease", str(i), f"Song {i}", artist=f"Artist {i}",
            duration_seconds=120) for i in range(8))
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(self.owned.close)
        self.addCleanup(self.provider._owned_audio_bridge.close)

    def perform(self, kind):
        token, action = next(job for job in reversed(self.tasks.jobs) if job[0][0] == kind)
        result = action()
        self.tasks.completed.emit(token, result, None)
        return token, result

    def start(self, index=0):
        self.owned.play_queue(self.songs, index)
        self.perform("stream")

    def test_queue_view_is_complete_immutable_memory_snapshot_with_current_row(self):
        self.start(2)
        self.service.stream_url.reset_mock()
        self.service.lyrics.reset_mock()
        token, index, songs = self.provider.get_owned_playback_queue()
        self.assertIsInstance(token, int)
        self.assertEqual(index, 2)
        self.assertEqual(songs, self.songs)
        self.assertIsInstance(songs, tuple)
        self.assertEqual(len(self.owned.track.album_queue), 3)
        for _ in range(5):
            observed = self.provider.get_owned_playback_queue()
            self.assertEqual(observed, (token, index, songs))
            self.assertIs(observed[2], songs)
        with self.assertRaises(FrozenInstanceError):
            songs[0].title = "Changed"
        self.service.stream_url.assert_not_called()
        self.service.lyrics.assert_not_called()

    def test_current_row_click_keeps_decoder_position_pause_and_clock(self):
        self.start(3)
        self.qt.positionChanged.emit(27000)
        self.owned.send_playback_command("toggle_play_pause")
        token, index, _ = self.provider.get_owned_playback_queue()
        before = (self.owned.track, self.owned._epoch, self.qt.source(), self.qt.playbackState())
        with patch.object(self.qt, "stop", wraps=self.qt.stop) as stop:
            self.assertTrue(self.provider.select_owned_queue_index(index, token))
        stop.assert_not_called()
        self.assertEqual((self.owned.track, self.owned._epoch, self.qt.source(), self.qt.playbackState()), before)

    def test_queue_thumbnail_reads_existing_memory_only_and_preserves_cache(self):
        covered = tuple(replace(song, artwork_url=f"https://fixture.invalid/{song.song_id}.png")
            for song in self.songs)
        self.owned.play_queue(covered)
        song = covered[1]
        key = self.owned._art_key(song)
        content = b"already-decoded-thumbnail-fixture"
        self.owned._art_cache[key] = (content, ColorToken(1, 2, 3), ColorToken(4, 5, 6))
        jobs = len(self.tasks.jobs), len(self.artwork.jobs), len(self.prefetch.jobs)
        before = dict(self.owned._art_cache)
        with patch("local.owned_music_player.fetch_online_artwork", side_effect=AssertionError("network read")):
            for _ in range(3):
                self.assertIs(self.provider.get_owned_queue_artwork(song), content)
        self.assertEqual(self.owned._art_cache, before)
        self.assertEqual((len(self.tasks.jobs), len(self.artwork.jobs), len(self.prefetch.jobs)), jobs)

    def test_queue_thumbnail_missing_stale_invalid_and_inactive_returns_empty_without_fetch(self):
        self.start()
        for song in (self.songs[1], None, "song", replace(self.songs[1], artwork_url="https://unowned.invalid/other"),
                replace(self.songs[1], media_kind="dj_voice")):
            self.assertEqual(self.provider.get_owned_queue_artwork(song), b"")
        self.values["v2_owned_playback_enabled"] = False
        self.assertEqual(self.provider.get_owned_queue_artwork(self.songs[0]), b"")
        self.values["v2_owned_playback_enabled"] = True
        self.owned.stop()
        self.assertEqual(self.provider.get_owned_queue_artwork(self.songs[0]), b"")

    def test_different_row_reuses_queue_and_mode_and_loads_selected_song(self):
        self.start()
        self.owned.set_playback_mode("repeat_one")
        token, _, songs = self.provider.get_owned_playback_queue()
        self.assertTrue(self.provider.select_owned_queue_index(4, token))
        self.assertEqual(self.owned.queue, songs)
        self.assertEqual(self.owned.track.track_id, "owned:netease:4")
        self.assertEqual(self.owned.playback_mode, "repeat_one")
        self.assertEqual(self.provider.get_owned_playback_queue()[1], 4)
        self.perform("stream")
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PlayingState)

    def test_stale_row_after_insertion_or_same_tuple_replacement_is_rejected(self):
        self.start()
        token, _, songs = self.provider.get_owned_playback_queue()
        self.owned.enqueue_next(OnlineSong("netease", "added", "Added"))
        self.assertFalse(self.provider.select_owned_queue_index(1, token))
        self.assertEqual(self.owned.track.track_id, "owned:netease:0")
        current = self.provider.get_owned_playback_queue()[0]
        self.assertNotEqual(current, token)
        self.owned.play_queue(songs)
        another = self.provider.get_owned_playback_queue()[0]
        self.owned.play_queue(songs)
        self.assertNotEqual(self.provider.get_owned_playback_queue()[0], another)
        self.assertFalse(self.provider.select_owned_queue_index(1, another))

    def test_inactive_system_idle_closed_and_disabled_sources_cannot_select_owned_queue(self):
        self.assertEqual(self.provider.get_owned_playback_queue(), (None, -1, ()))
        self.assertFalse(self.provider.select_owned_queue_index(0))
        self.start()
        token = self.provider.get_owned_playback_queue()[0]
        self.values["v2_owned_playback_enabled"] = False
        self.assertEqual(self.provider.get_owned_playback_queue(), (None, -1, ()))
        self.assertFalse(self.provider.select_owned_queue_index(1, token))
        self.values["v2_owned_playback_enabled"] = True
        self.owned._closed = True
        self.assertEqual(self.provider.get_owned_playback_queue(), (None, -1, ()))
        self.assertFalse(self.provider.select_owned_queue_index(1, token))

    def test_invalid_row_types_and_bounds_never_mutate_the_player(self):
        self.start()
        before = (self.owned.track, self.owned.queue, self.owned._epoch)
        token = self.provider.get_owned_playback_queue()[0]
        for index in (None, True, False, "1", 1.0, -1, len(self.songs)):
            with self.subTest(index=index):
                self.assertFalse(self.provider.select_owned_queue_index(index, token))
                self.assertEqual((self.owned.track, self.owned.queue, self.owned._epoch), before)

    def test_fm_snapshot_excludes_speech_and_selection_retains_true_introduction(self):
        self.owned.personal_dj_enabled = True
        batch = segments(1) + segments(2) + segments(3)
        self.owned._replace_queue(batch, fm_source="netease", fm_kind="dj")
        token, index, songs = self.provider.get_owned_playback_queue()
        self.assertEqual(tuple(song.song_id for song in songs), ("song-1", "song-2", "song-3"))
        self.assertEqual(index, 0)
        self.assertTrue(self.provider.select_owned_queue_index(2, token))
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-3")
        self.assertTrue(self.owned.is_personal_fm())
        self.assertTrue(self.owned.personal_dj_enabled)
        self.assertEqual(self.owned._dj_overlay_voices, batch[-2:-1])
        self.assertEqual(self.provider.get_owned_playback_queue()[1], 2)

    def test_fm_prune_changes_token_and_old_rows_cannot_target_shifted_songs(self):
        self.owned._replace_queue(self.songs, fm_source="netease")
        token, _, _ = self.provider.get_owned_playback_queue()
        self.assertTrue(self.provider.select_owned_queue_index(6, token))
        new_token, index, songs = self.provider.get_owned_playback_queue()
        self.assertNotEqual(new_token, token)
        self.assertEqual(index, 3)
        self.assertEqual(songs[index].song_id, "6")
        self.assertFalse(self.provider.select_owned_queue_index(3, token))
        self.assertEqual(self.owned.track.track_id, "owned:netease:6")

    def test_shuffle_direct_selection_keeps_previous_and_planned_future(self):
        self.owned._random.shuffle = lambda values: values.reverse()
        self.owned.set_playback_mode("shuffle")
        self.start()
        before = tuple(self.owned.queue[i].song_id for i in self.owned._upcoming_indices())
        token = self.provider.get_owned_playback_queue()[0]
        self.assertTrue(self.provider.select_owned_queue_index(4, token))
        self.assertEqual(self.owned.track.track_id, "owned:netease:4")
        self.owned.send_playback_command("previous")
        self.assertEqual(self.owned.track.track_id, "owned:netease:0")
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.track.track_id, "owned:netease:4")
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.track.track_id, f"owned:netease:{before[0]}")

    def test_selecting_another_song_keeps_next_priority_and_selecting_that_next_consumes_it(self):
        self.start()
        addition = OnlineSong("netease", "added", "Added")
        self.owned.enqueue_next(addition)
        token, _, songs = self.provider.get_owned_playback_queue()
        self.provider.select_owned_queue_index(3, token)
        self.assertEqual(self.owned.track.track_id, "owned:netease:2")
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.track.track_id, "owned:netease:added")
        self.assertFalse(self.owned._play_next_indices)
        self.assertEqual(self.owned.queue, songs)

    def test_selecting_queue_row_cancels_pending_catalog_and_stale_result(self):
        self.start()
        token = self.provider.get_owned_playback_queue()[0]
        self.owned._catalog("daily", "netease", lambda: (OnlineSong("netease", "late", "Late"),))
        pending, action = self.tasks.jobs[-1]
        self.assertTrue(self.provider.select_owned_queue_index(2, token))
        self.tasks.completed.emit(pending, action(), None)
        self.assertEqual(self.owned.track.track_id, "owned:netease:2")
        self.assertFalse(self.owned._catalog_pending_epoch)

    def test_far_later_fm_selection_preserves_unplayed_listener_addition_during_trim(self):
        self.owned._replace_queue(self.songs, fm_source="netease")
        addition = OnlineSong("netease", "added", "Added")
        self.owned.enqueue_next(addition)
        token = self.provider.get_owned_playback_queue()[0]
        self.assertTrue(self.provider.select_owned_queue_index(7, token))
        self.assertEqual(self.owned.track.track_id, "owned:netease:6")
        self.assertIn(addition, self.owned.queue)
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.track.track_id, "owned:netease:added")

    def test_stop_retains_an_immutable_old_snapshot_but_invalidates_selection_token(self):
        self.start()
        token, _, songs = self.provider.get_owned_playback_queue()
        self.owned.stop()
        self.assertEqual(songs, self.songs)
        self.assertEqual(self.provider.get_owned_playback_queue(), (None, -1, ()))
        self.owned.play_queue(self.songs)
        self.assertFalse(self.provider.select_owned_queue_index(1, token))

    def test_worker_selection_marshals_and_uses_real_guarded_result(self):
        self.start()
        token = self.provider.get_owned_playback_queue()[0]
        result = []
        worker = threading.Thread(target=lambda: result.append(self.provider.select_owned_queue_index(2, token)))
        worker.start()
        deadline = time.monotonic() + 1.5
        while worker.is_alive() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        worker.join(timeout=.5)
        self.assertEqual(result, [True])
        self.assertEqual(self.owned.track.track_id, "owned:netease:2")


if __name__ == "__main__":
    unittest.main()
