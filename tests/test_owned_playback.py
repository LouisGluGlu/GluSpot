from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import CatalogPage, OnlineSong, StreamInfo
from api.providers import EmptyIslandDataProvider, PlaybackSourceProvider, WindowsMediaProvider
from api.models import ColorToken
from local.owned_music_player import OwnedMusicPlayer
from local.playback_entry_controller import startup_destination


class Tasks(QObject):
    completed = Signal(object, object, object)

    def __init__(self):
        super().__init__()
        self.jobs = []
        self.closed = False
        self.accepted = True

    def submit(self, token, action):
        self.jobs.append((token, action))
        return self.accepted

    def close(self):
        self.closed = True


class Player(QObject):
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playbackStateChanged = Signal(object)
    mediaStatusChanged = Signal(object)
    errorOccurred = Signal(object, str)

    def __init__(self):
        super().__init__()
        self.url = None
        self.state = QMediaPlayer.PlaybackState.StoppedState
        self.position = 0

    def setAudioOutput(self, audio): pass
    def stop(self):
        self.state = QMediaPlayer.PlaybackState.StoppedState
        self.playbackStateChanged.emit(self.state)
    def setSource(self, url): self.url = url
    def play(self):
        self.state = QMediaPlayer.PlaybackState.PlayingState
        self.playbackStateChanged.emit(self.state)
    def pause(self):
        self.state = QMediaPlayer.PlaybackState.PausedState
        self.playbackStateChanged.emit(self.state)
    def playbackState(self): return self.state
    def isSeekable(self): return True
    def setPosition(self, value): self.position = value


class ThreadRecordingPlayer(Player):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.seekable = True

    def _record(self, name): self.calls.append((name, threading.get_ident()))
    def stop(self):
        self._record("stop")
        super().stop()
    def setSource(self, url):
        self._record("source")
        super().setSource(url)
    def play(self):
        self._record("play")
        super().play()
    def pause(self):
        self._record("pause")
        super().pause()
    def playbackState(self):
        self._record("state")
        return super().playbackState()
    def isSeekable(self):
        self._record("seekable")
        return self.seekable
    def setPosition(self, value):
        self._record("position")
        super().setPosition(value)


class OwnedPlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tasks = Tasks()
        self.artwork_tasks = Tasks()
        self.prefetch_tasks = Tasks()
        self.qt_player = Player()
        self.service = SimpleNamespace(stream_url=lambda *args: None, lyrics=lambda *args: None)
        self.lyrics = LocalLyricsStore(root=Path(self.temp.name))
        self.player = OwnedMusicPlayer(self.service, self.lyrics, player=self.qt_player,
                                      audio=object(), tasks=self.tasks, artwork_tasks=self.artwork_tasks, prefetch_tasks=self.prefetch_tasks)
        self.songs = (OnlineSong("netease", "1", "第一首", duration_seconds=120),
                      OnlineSong("netease", "2", "第二首", duration_seconds=150))

    def tearDown(self):
        self.player.close()
        self.temp.cleanup()

    def covered_songs(self, count=10):
        return tuple(OnlineSong("netease", str(i), f"song {i}", artist=f"artist {i}",
            album=f"album {i}", duration_seconds=120 + i, artwork_url=f"https://example.test/{i}.png")
            for i in range(count))

    def finish_artwork(self, content=None):
        delivered = set()
        for _ in range(30):
            pending = [(token, action) for token, action in self.artwork_tasks.jobs if token not in delivered
                       and token[3] in self.player._art_running]
            if not pending:
                return
            for token, _ in pending:
                delivered.add(token)
                data = content if content is not None else ("art:" + token[2][1]).encode()
                self.artwork_tasks.completed.emit(token, (data, ColorToken(10, 20, 30), ColorToken(40, 50, 60)), None)
        self.fail("artwork pump did not settle")

    def authorize_current(self):
        token = next(token for token, _ in reversed(self.tasks.jobs) if token[0] == "stream")
        song = self.player.queue[self.player.index]
        self.tasks.completed.emit(token, StreamInfo(song.source_id, song.song_id, "https://example.test/audio", True), None)

    def test_sequential_list_loops_eof_and_manual_boundaries(self):
        self.assertEqual(self.player.playback_mode, "sequential")
        self.player.play_queue(self.songs, index=1)
        self.authorize_current()
        self.qt_player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.player.track.track_id, "owned:netease:1")
        self.assertEqual(self.player.status, "loading_stream")
        self.assertEqual(len([token for token, _ in self.tasks.jobs if token[0] == "catalog"]), 0)
        self.assertTrue(self.player.send_playback_command("previous"))
        self.assertEqual(self.player.index, 1)
        self.assertTrue(self.player.send_playback_command("next"))
        self.assertEqual(self.player.index, 0)

    def test_repeat_one_reauthorizes_eof_but_manual_controls_change_tracks(self):
        self.player.set_playback_mode("repeat_one")
        self.player.play_queue(self.covered_songs(2))
        self.finish_artwork()
        self.authorize_current()
        original_track, original_epoch = self.player.track.track_id, self.player._epoch
        self.qt_player.positionChanged.emit(115000)
        self.qt_player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.player.track.track_id, original_track)
        self.assertEqual(self.player.track.position_seconds, 0)
        self.assertEqual(self.player.track.album_art_bytes, b"art:0")
        self.assertEqual(self.player._epoch, original_epoch + 1)
        self.assertEqual(self.tasks.jobs[-1][0][0], "stream")
        self.assertEqual(len([token for token, _ in self.tasks.jobs if token[0] == "catalog"]), 0)
        self.assertTrue(self.player.send_playback_command("next"))
        self.assertEqual(self.player.index, 1)
        self.assertTrue(self.player.send_playback_command("previous"))
        self.assertEqual(self.player.index, 0)

    def test_pending_or_duplicate_eof_cannot_skip_unconfirmed_next_stream(self):
        self.player.play_queue(self.songs)
        self.qt_player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.player.index, 0)
        self.authorize_current()
        self.qt_player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.player.index, 1)
        count = len(self.tasks.jobs)
        self.qt_player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.player.index, 1)
        self.assertEqual(len(self.tasks.jobs), count)

    def test_shuffle_preplans_real_next_covers_cycle_and_previous_history(self):
        self.player._random.shuffle = lambda values: values.reverse()
        self.player.set_playback_mode("shuffle")
        self.player.play_queue(self.covered_songs(6))
        self.assertEqual([entry.track_id for entry in self.player.track.album_queue],
                         ["owned:netease:5", "owned:netease:4", "owned:netease:3"])
        self.assertEqual([token[2][1] for token, _ in self.artwork_tasks.jobs], ["0", "5"])
        self.finish_artwork()
        self.assertEqual({key[1] for key in self.player._art_cache}, {"0", "3", "4", "5"})
        visited = [self.player.index]
        for _ in range(5):
            advertised = self.player.track.album_queue[0].track_id
            self.assertTrue(self.player.send_playback_command("next"))
            self.assertEqual(self.player.track.track_id, advertised)
            visited.append(self.player.index)
            self.finish_artwork()
            self.assertLessEqual(len(self.player._art_cache), 7)
        self.assertEqual(len(set(visited)), 6)
        previous, current = visited[-2:]
        self.assertTrue(self.player.send_playback_command("previous"))
        self.assertEqual(self.player.index, previous)
        self.assertTrue(self.player.send_playback_command("next"))
        self.assertEqual(self.player.index, current)
        self.assertTrue(self.player.send_playback_command("next"))
        self.assertNotEqual(self.player.index, current)

    def test_shuffle_repeated_cycles_do_not_repeat_immediately_and_history_is_bounded(self):
        self.player._random.shuffle = lambda values: None
        self.player.set_playback_mode("shuffle")
        self.player.play_queue(self.songs)
        for _ in range(80):
            current = self.player.index
            self.player.send_playback_command("next")
            self.assertNotEqual(self.player.index, current)
            self.assertLessEqual(len(self.player._shuffle_history), len(self.songs) + 3)
            self.assertLessEqual(len(self.player._shuffle_pending), 3)

    def test_shuffle_duplicate_catalog_identity_cannot_repeat_immediately(self):
        self.player._random.shuffle = lambda values: None
        self.player.set_playback_mode("shuffle")
        self.player.play_queue((self.songs[0], self.songs[0], self.songs[1]), index=1)
        for _ in range(12):
            current = self.player.track.track_id
            self.player.send_playback_command("next")
            self.assertNotEqual(self.player.track.track_id, current)

    def test_single_song_modes_restart_without_impossible_shuffle_choice(self):
        for mode in ("sequential", "shuffle", "repeat_one"):
            self.player.set_playback_mode(mode)
            self.player.play_queue(self.songs[:1])
            self.authorize_current()
            self.qt_player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
            self.assertEqual(self.player.index, 0)
            self.assertEqual(self.player.status, "loading_stream")
            self.assertTrue(self.player.send_playback_command("next"))

    def test_setting_mode_preserves_current_stream_and_validates_idle_closed_values(self):
        self.assertTrue(self.player.set_playback_mode("shuffle"))
        for invalid in ("repeat", "", None, [], "SHUFFLE"):
            self.assertFalse(self.player.set_playback_mode(invalid))
        self.player.play_queue(self.songs)
        self.authorize_current()
        current, epoch, url = self.player.track.track_id, self.player._epoch, self.qt_player.url
        with patch.object(self.qt_player, "stop", wraps=self.qt_player.stop) as stop:
            self.assertTrue(self.player.set_playback_mode("repeat_one"))
        stop.assert_not_called()
        self.assertEqual((self.player.track.track_id, self.player._epoch, self.qt_player.url), (current, epoch, url))
        self.player.stop()
        self.assertEqual(self.player.playback_mode, "repeat_one")
        self.assertEqual(self.player._shuffle_history, [])
        self.assertEqual(list(self.player._shuffle_pending), [])
        self.player.close()
        self.assertFalse(self.player.set_playback_mode("sequential"))

    def test_shuffle_queue_replacement_clears_old_plan_stream_and_cover_results(self):
        self.player._random.shuffle = lambda values: values.reverse()
        self.player.set_playback_mode("shuffle")
        self.player.play_queue(self.covered_songs(6))
        stream = self.tasks.jobs[-1][0]
        art = self.artwork_tasks.jobs[0][0]
        replacement = (OnlineSong("qq", "x", "new", artwork_url="https://example.test/new.png"),)
        self.player.play_queue(replacement)
        self.tasks.completed.emit(stream, StreamInfo("netease", "0", "https://example.test/old", True), None)
        self.artwork_tasks.completed.emit(art, (b"old", ColorToken(1, 2, 3), ColorToken(4, 5, 6)), None)
        self.assertEqual(self.player.track.track_id, "owned:qq:x")
        self.assertEqual(self.player.track.album_art_bytes, b"")
        self.assertEqual(self.player._shuffle_history, [0])
        self.assertTrue(all(index == 0 for index in self.player._shuffle_pending))

    def test_fm_shuffle_only_reorders_unplayed_batch_and_continues_new_songs(self):
        songs = self.covered_songs(9)
        self.player._random.shuffle = lambda values: values.reverse()
        self.player.set_playback_mode("shuffle")
        self.service.personal_fm = lambda source: songs[:3]
        self.player.play_personal_fm()
        token, action = self.tasks.jobs[-1]
        self.tasks.completed.emit(token, action(), None)
        self.assertEqual([song.song_id for song in self.player.queue], ["0", "2", "1"])
        self.assertEqual([entry.track_id for entry in self.player.track.album_queue], ["owned:netease:2", "owned:netease:1"])
        self.authorize_current()
        self.prefetch_tasks.completed.emit(self.prefetch_tasks.jobs[-1][0], songs[3:6], None)
        self.assertEqual([song.song_id for song in self.player.queue], ["0", "2", "1", "5", "4", "3"])
        self.finish_artwork()
        played = [self.player.queue[self.player.index].song_id]
        for _ in range(5):
            advertised = self.player.track.album_queue[0].track_id
            self.player.send_playback_command("next")
            self.assertEqual(self.player.track.track_id, advertised)
            played.append(self.player.queue[self.player.index].song_id)
            self.finish_artwork()
        self.assertEqual(played, ["0", "2", "1", "5", "4", "3"])
        self.assertEqual([song.song_id for song in self.player.queue[:self.player.index]], ["1", "5", "4"])
        self.assertTrue(self.player.send_playback_command("previous"))
        self.assertEqual(self.player.track.track_id, "owned:netease:4")
        self.assertEqual(self.player.track.album_art_bytes, b"art:4")
        self.assertTrue(self.player.send_playback_command("next"))
        self.assertEqual(self.player.track.track_id, "owned:netease:3")
        self.service.personal_fm = lambda source: songs[6:9]
        self.assertTrue(self.player.send_playback_command("next"))
        token, action = self.tasks.jobs[-1]
        self.assertEqual(token[2], "fm_continue")
        self.tasks.completed.emit(token, action(), None)
        self.assertEqual(self.player.track.track_id, "owned:netease:8")
        self.assertEqual([song.song_id for song in self.player.queue[self.player.index:]], ["8", "7", "6"])

    def test_fm_switch_to_shuffle_keeps_current_and_history_then_repeat_eof_current(self):
        songs = self.covered_songs(7)
        self.service.personal_fm = lambda source: songs[:3]
        self.player.play_personal_fm()
        token, action = self.tasks.jobs[-1]
        self.tasks.completed.emit(token, action(), None)
        self.authorize_current()
        self.prefetch_tasks.completed.emit(self.prefetch_tasks.jobs[-1][0], songs[3:6], None)
        self.player.send_playback_command("next")
        self.player._random.shuffle = lambda values: values.reverse()
        self.player.set_playback_mode("shuffle")
        self.assertEqual([song.song_id for song in self.player.queue], ["0", "1", "5", "4", "3", "2"])
        self.assertEqual(self.player.track.track_id, "owned:netease:1")
        self.player.set_playback_mode("repeat_one")
        self.authorize_current()
        catalog_count = len([token for token, _ in self.tasks.jobs if token[0] == "catalog"])
        self.qt_player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.player.track.track_id, "owned:netease:1")
        self.assertEqual(len([token for token, _ in self.tasks.jobs if token[0] == "catalog"]), catalog_count)
        self.player.send_playback_command("next")
        self.assertEqual(self.player.track.track_id, "owned:netease:5")

    def test_manual_previous_cancels_waiting_fm_next_without_losing_new_batch(self):
        songs = self.covered_songs(6)
        self.service.personal_fm = lambda source: songs[:3]
        self.player.play_personal_fm()
        token, action = self.tasks.jobs[-1]
        self.tasks.completed.emit(token, action(), None)
        self.authorize_current()
        prefetch = self.prefetch_tasks.jobs[-1][0]
        self.player.send_playback_command("next")
        self.player.send_playback_command("next")
        self.player.send_playback_command("next")
        self.assertTrue(self.player._fm_advance_pending)
        self.player.set_playback_mode("shuffle")
        self.player.send_playback_command("previous")
        self.assertFalse(self.player._fm_advance_pending)
        self.assertEqual(self.player.track.track_id, "owned:netease:1")
        self.prefetch_tasks.completed.emit(prefetch, songs[3:6], None)
        self.assertEqual(self.player.track.track_id, "owned:netease:1")
        self.assertEqual({song.song_id for song in self.player.queue}, set("012345"))
        self.assertEqual(len(self.prefetch_tasks.jobs), 1)

    def test_adjacent_three_covers_are_bounded_and_next_uses_ready_cover_before_authorization(self):
        songs = self.covered_songs()
        self.player.play_queue(songs, index=3)
        self.assertEqual(len(self.artwork_tasks.jobs), 2)
        self.assertEqual([job[0][2][1] for job in self.artwork_tasks.jobs], ["3", "4"])
        self.finish_artwork()
        self.assertEqual({key[1] for key in self.player._art_cache}, set("0123456"))
        self.assertEqual(len(self.player._art_cache), 7)
        self.assertEqual(self.player.queue, songs)
        self.assertEqual([entry.track_id for entry in self.player.track.album_queue],
                         ["owned:netease:4", "owned:netease:5", "owned:netease:6"])
        self.assertEqual(self.player.track.album_queue[0].artist, "artist 4")
        self.assertEqual(self.player.track.album_queue[0].album_art_bytes, b"art:4")
        self.assertEqual([job[0][0] for job in self.tasks.jobs], ["stream"])
        self.assertEqual(self.player.status, "loading_stream")
        self.assertEqual(self.player.track.lyrics, ("加载歌词中",))
        self.player.send_playback_command("next")
        self.assertEqual(self.player.track.album_art_bytes, b"art:4")
        self.assertEqual(self.player.track.album_art_lookup_state, "original")
        self.assertEqual(self.player.track.duration_seconds, 124)
        self.assertTrue(self.qt_player.url.isEmpty())
        self.assertEqual([job[0][0] for job in self.tasks.jobs], ["stream", "stream"])
        self.assertNotIn("0", {key[1] for key in self.player._art_cache})
        self.finish_artwork()
        self.assertEqual({key[1] for key in self.player._art_cache}, set("1234567"))
        self.player.send_playback_command("previous")
        self.assertEqual(self.player.track.album_art_bytes, b"art:3")

    def test_late_previous_cover_enriches_history_without_replacing_current_cover(self):
        songs = self.covered_songs(4)
        self.player.play_queue(songs)
        old, _ = self.artwork_tasks.jobs[0]
        current, _ = self.artwork_tasks.jobs[1]
        self.player.send_playback_command("next")
        colors = ColorToken(1, 2, 3), ColorToken(4, 5, 6)
        self.artwork_tasks.completed.emit(old, (b"previous", *colors), None)
        self.assertEqual(self.player.track.track_id, "owned:netease:1")
        self.assertEqual(self.player.track.album_art_bytes, b"")
        self.assertEqual(self.player._art_cache[self.player._art_key(songs[0])][0], b"previous")
        self.assertEqual(self.player.track.album_queue[0].track_id, "owned:netease:2")
        self.artwork_tasks.completed.emit(current, (b"current", *colors), None)
        self.assertEqual(self.player.track.album_art_bytes, b"current")

    def test_artwork_byte_cap_protects_current_and_does_not_retry_evicted_neighbors_forever(self):
        self.player.ARTWORK_CACHE_MAX_BYTES = 12
        self.player.play_queue(self.covered_songs(8), index=3)
        self.finish_artwork(content=b"xxxx")
        self.assertLessEqual(sum(len(item[0]) for item in self.player._art_cache.values()), 12)
        self.assertEqual(self.player.track.album_art_bytes, b"xxxx")
        self.assertIn(self.player._art_key(self.player.queue[3]), self.player._art_cache)
        self.assertEqual(len(self.artwork_tasks.jobs), 7)
        self.player._pump_artwork()
        self.assertEqual(len(self.artwork_tasks.jobs), 7)

    def test_neighbor_failure_is_terminal_for_window_and_does_not_change_current_lyrics_or_status(self):
        self.player.play_queue(self.covered_songs(5))
        current, neighbor = [job[0] for job in self.artwork_tasks.jobs]
        self.artwork_tasks.completed.emit(current, (b"current", ColorToken(1, 2, 3), ColorToken(4, 5, 6)), None)
        before = self.player.status, self.player.track.lyrics, self.player.track.album_art_bytes
        self.artwork_tasks.completed.emit(neighbor, None, "timeout")
        self.player._pump_artwork()
        self.assertEqual((self.player.status, self.player.track.lyrics, self.player.track.album_art_bytes), before)
        self.assertEqual(sum(job[0][2] == neighbor[2] for job in self.artwork_tasks.jobs), 1)

    def test_source_replacement_waits_for_old_workers_then_rejects_their_cache_results(self):
        self.player.play_queue(self.covered_songs(6))
        old = list(self.artwork_tasks.jobs)
        songs = (OnlineSong("qq", "0", "new source", artwork_url="https://example.test/new.png"),)
        self.player.play_queue(songs)
        self.assertEqual(len(self.artwork_tasks.jobs), 2)
        for token, action in old:
            with patch("local.owned_music_player.fetch_online_artwork") as fetch:
                self.assertIsNone(action())
                fetch.assert_not_called()
            self.artwork_tasks.completed.emit(token, (b"old", ColorToken(1, 2, 3), ColorToken(4, 5, 6)), None)
        self.assertEqual(len(self.artwork_tasks.jobs), 3)
        self.assertEqual(self.player._art_cache, {})
        self.assertEqual(self.player.track.album_art_bytes, b"")
        self.assertEqual(self.player.track.track_id, "owned:qq:0")

    def test_fm_prefetch_appends_real_batch_deduplicates_and_keeps_three_previous_covers(self):
        songs = self.covered_songs(8)
        self.service.personal_fm = lambda source: songs[:3]
        self.player.play_personal_fm()
        token, action = self.tasks.jobs[-1]
        self.tasks.completed.emit(token, action(), None)
        self.tasks.completed.emit(self.tasks.jobs[-1][0], StreamInfo("netease", "0", "https://example.test/audio", True), None)
        self.assertEqual(self.tasks.jobs[-1][0][0], "lyrics")
        token, _ = self.prefetch_tasks.jobs[-1]
        before = self.player.status, self.player.track.lyrics
        self.prefetch_tasks.completed.emit(token, (songs[2], songs[3], songs[3], songs[4], songs[5]), None)
        self.assertEqual(self.player.queue, songs[:6])
        self.assertEqual((self.player.status, self.player.track.lyrics), before)
        self.finish_artwork()
        for _ in range(4):
            self.player.send_playback_command("next")
            self.finish_artwork()
        self.assertEqual([song.song_id for song in self.player.queue], ["1", "2", "3", "4", "5"])
        self.assertEqual(self.player.index, 3)
        self.assertEqual([self.player._art_cache[self.player._art_key(song)][0] for song in self.player.queue[:3]],
                         [b"art:1", b"art:2", b"art:3"])
        self.assertEqual([entry.track_id for entry in self.player.track.album_queue], ["owned:netease:5"])
        self.assertEqual(self.player.track.album_art_bytes, b"art:4")

    def test_fm_waiting_next_reuses_prefetch_and_failed_batch_does_not_loop(self):
        songs = self.covered_songs(5)
        self.service.personal_fm = lambda source: songs[:3]
        self.player.play_personal_fm()
        token, action = self.tasks.jobs[-1]
        self.tasks.completed.emit(token, action(), None)
        self.tasks.completed.emit(self.tasks.jobs[-1][0], StreamInfo("netease", "0", "https://example.test/audio", True), None)
        token, _ = self.prefetch_tasks.jobs[-1]
        self.player.index = 2
        self.assertTrue(self.player.send_playback_command("next"))
        self.assertTrue(self.player._fm_advance_pending)
        self.assertEqual(len(self.prefetch_tasks.jobs), 1)
        self.prefetch_tasks.completed.emit(token, (songs[3], songs[4]), None)
        self.assertEqual(self.player.track.track_id, "owned:netease:3")
        self.assertFalse(self.player._fm_advance_pending)
        self.tasks.completed.emit(self.tasks.jobs[-1][0], StreamInfo("netease", "3", "https://example.test/audio", True), None)
        token, _ = self.prefetch_tasks.jobs[-1]
        count = len(self.prefetch_tasks.jobs)
        self.prefetch_tasks.completed.emit(token, None, "timeout")
        self.player._prefetch_fm()
        self.assertEqual(len(self.prefetch_tasks.jobs), count)

    def test_stale_fm_prefetch_cannot_poll_or_append_to_new_queue(self):
        self.service.personal_fm = lambda source: self.songs
        self.player.play_personal_fm()
        token, action = self.tasks.jobs[-1]
        self.tasks.completed.emit(token, action(), None)
        self.tasks.completed.emit(self.tasks.jobs[-1][0], StreamInfo("netease", "1", "https://example.test/audio", True), None)
        token, action = self.prefetch_tasks.jobs[-1]
        self.player.play_queue((OnlineSong("qq", "new", "new"),))
        self.assertEqual(action(), ())
        self.prefetch_tasks.completed.emit(token, self.covered_songs(), None)
        self.assertEqual(self.player.queue[0].source_id, "qq")
        self.assertEqual(len(self.player.queue), 1)

    def test_fm_foreground_fallback_keeps_history_after_failed_preload(self):
        songs = self.covered_songs(8)
        self.service.personal_fm = lambda source: songs[:3]
        self.player.play_personal_fm()
        token, action = self.tasks.jobs[-1]
        self.tasks.completed.emit(token, action(), None)
        self.tasks.completed.emit(self.tasks.jobs[-1][0], StreamInfo("netease", "0", "https://example.test/audio", True), None)
        self.prefetch_tasks.completed.emit(self.prefetch_tasks.jobs[-1][0], None, "timeout")
        self.finish_artwork()
        self.player.index = 2
        self.service.personal_fm = lambda source: songs[3:6]
        self.player.send_playback_command("next")
        token, action = self.tasks.jobs[-1]
        self.assertEqual(token[2], "fm_continue")
        self.tasks.completed.emit(token, action(), None)
        self.assertEqual(self.player.track.track_id, "owned:netease:3")
        self.assertEqual(self.player._fm_source, "netease")
        self.assertEqual(self.player.queue, songs[:6])
        self.assertEqual([self.player._art_cache[self.player._art_key(song)][0] for song in self.player.queue[:3]],
                         [b"art:0", b"art:1", b"art:2"])

    def test_fm_prefetch_worker_cannot_hold_current_stream_or_lyric_submission(self):
        from local.music_tasks import MusicTasks
        workers = MusicTasks()
        entered, release = threading.Event(), threading.Event()
        def fetch(source):
            entered.set()
            release.wait(2)
            return ()
        self.service.personal_fm = fetch
        player = OwnedMusicPlayer(self.service, self.lyrics, player=Player(), audio=object(),
                                  tasks=self.tasks, artwork_tasks=self.artwork_tasks, prefetch_tasks=workers)
        try:
            player.play_queue(self.songs)
            player._fm_source = "netease"
            self.tasks.completed.emit(self.tasks.jobs[-1][0], StreamInfo("netease", "1", "https://example.test/audio", True), None)
            self.assertTrue(entered.wait(1))
            self.assertEqual(player.status, "playing")
            self.assertEqual(self.tasks.jobs[-1][0][0], "lyrics")
            started = time.monotonic()
            self.assertTrue(player.send_playback_command("next"))
            self.assertLess(time.monotonic() - started, .1)
            self.assertEqual(player.track.track_id, "owned:netease:2")
            self.assertEqual(self.tasks.jobs[-1][0][0], "stream")
        finally:
            release.set()
            player.close()

    def test_stop_clears_cover_bytes_and_closed_prefetch_is_inert(self):
        self.player.play_queue(self.covered_songs())
        self.finish_artwork()
        self.assertTrue(self.player._art_cache)
        self.player.stop()
        self.assertEqual(self.player._art_cache, {})
        self.assertEqual(self.player._art_window, {})
        self.assertIsNone(self.player.track)
        self.player.close()
        self.assertTrue(self.prefetch_tasks.closed)

    def test_stale_stream_cannot_replace_new_song(self):
        self.player.play_queue(self.songs)
        old_token = self.tasks.jobs[-1][0]
        self.player.send_playback_command("next")
        self.tasks.completed.emit(old_token, StreamInfo("netease", "1", "https://example.test/a", True), None)
        self.assertTrue(self.qt_player.url.isEmpty())
        self.assertEqual(self.player.track.track_id, "owned:netease:2")

    def test_stream_starts_before_lyric_request_and_restricted_stream_does_not_play(self):
        self.player.play_queue(self.songs)
        token = self.tasks.jobs[-1][0]
        self.tasks.completed.emit(token, StreamInfo("netease", "1", playable=False), None)
        self.assertEqual(self.player.status, "stream_unavailable")
        self.assertTrue(self.qt_player.url.isEmpty())
        self.tasks.completed.emit(token, StreamInfo("netease", "1", "https://example.test/a", True, True), None)
        self.assertEqual(self.qt_player.state, QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(self.player.status, "trial")
        self.assertEqual(self.tasks.jobs[-1][0][0], "lyrics")

    def test_stream_is_not_reported_playing_until_qt_confirms_state(self):
        self.player.play_queue(self.songs)
        token = self.tasks.jobs[-1][0]
        observed = []
        self.player.statusChanged.connect(lambda status: observed.append((status, bool(self.player.track and self.player.track.is_playing))))
        with patch.object(self.qt_player, "play", side_effect=lambda: None):
            self.tasks.completed.emit(token, StreamInfo("netease", "1", "https://example.test/a", True, True), None)
        self.assertEqual(self.player.status, "loading_stream")
        self.assertFalse(self.player.track.is_playing)
        self.assertEqual(observed, [])
        self.qt_player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(self.player.status, "trial")
        self.assertEqual(observed, [("trial", True)])

    def test_synchronous_source_or_play_error_cannot_be_overwritten_by_playing(self):
        for during in ("source", "play"):
            with self.subTest(during=during):
                self.player.play_queue(self.songs)
                token = self.tasks.jobs[-1][0]
                emit_error = lambda *args: self.qt_player.errorOccurred.emit(QMediaPlayer.Error.NetworkError, "synthetic signed-url-secret")
                with patch.object(self.qt_player, "setSource" if during == "source" else "play", side_effect=emit_error), \
                        patch.object(self.qt_player, "play" if during == "source" else "setSource", wraps=self.qt_player.play if during == "source" else self.qt_player.setSource) as other:
                    self.tasks.completed.emit(token, StreamInfo("netease", "1", "https://example.test/a", True), None)
                self.assertEqual(self.player.status, "playback_failed")
                self.assertEqual(self.player.failure_reason, "network")
                self.assertNotIn("secret", self.player.failure_reason)
                self.assertFalse(self.player.track.is_playing)
                self.assertEqual(self.player.track.lyrics, ("暂无歌词",))
                self.assertFalse(any(job[0][0] == "lyrics" and job[0][1] == self.player._epoch for job in self.tasks.jobs))
                if during == "source":
                    other.assert_not_called()
                self.qt_player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PlayingState)
                self.assertEqual(self.player.status, "playback_failed")
                self.assertFalse(self.player.track.is_playing)

    def test_new_catalog_cannot_be_confirmed_by_old_stream_or_playing_signal(self):
        self.service.daily_recommendations = lambda source: self.songs
        self.player.play_queue(self.songs)
        old_stream = self.tasks.jobs[-1][0]
        self.player.play_daily()
        self.qt_player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(self.player.status, "loading_catalog")
        with patch.object(self.qt_player, "setSource", wraps=self.qt_player.setSource) as source:
            self.tasks.completed.emit(old_stream, StreamInfo("netease", "1", "https://example.test/old", True), None)
        source.assert_not_called()
        self.assertEqual(self.player.status, "loading_catalog")
        catalog = next(job for job in reversed(self.tasks.jobs) if job[0][0] == "catalog")
        self.tasks.completed.emit(catalog[0], catalog[1](), None)
        self.assertEqual(self.player.status, "loading_stream")
        self.tasks.completed.emit(self.tasks.jobs[-1][0], StreamInfo("netease", "1", "https://example.test/new", True), None)
        self.assertEqual(self.player.status, "playing")

    def test_reset_and_closed_qt_errors_do_not_create_false_playback_failure(self):
        with patch.object(self.qt_player, "setSource", side_effect=lambda *args:
                self.qt_player.errorOccurred.emit(QMediaPlayer.Error.ResourceError, "synthetic old reset")):
            self.player.play_queue(self.songs)
        self.assertEqual(self.player.status, "loading_stream")
        self.assertEqual(self.player.failure_reason, "")
        self.player.close()
        self.qt_player.errorOccurred.emit(QMediaPlayer.Error.NetworkError, "synthetic closed signal")
        self.assertEqual(self.player.status, "idle")
        self.assertEqual(self.player.failure_reason, "")

    def test_async_qt_error_categories_are_sanitized_and_stop_playing_visual(self):
        for code, reason in ((QMediaPlayer.Error.NetworkError, "network"),
                (QMediaPlayer.Error.FormatError, "format"), (QMediaPlayer.Error.AccessDeniedError, "access_denied"),
                (QMediaPlayer.Error.ResourceError, "resource")):
            with self.subTest(code=code):
                self.player.play_queue(self.songs)
                self.tasks.completed.emit(self.tasks.jobs[-1][0], StreamInfo("netease", "1", "https://example.test/a", True), None)
                self.qt_player.errorOccurred.emit(code, "synthetic signed-url-secret")
                if code in {QMediaPlayer.Error.NetworkError, QMediaPlayer.Error.ResourceError}:
                    self.assertEqual(self.player.status, "playing")
                    self.assertTrue(self.player.track.is_playing)
                    self.player._stream_fault_timer.stop()
                    with patch("local.owned_music_player.time.monotonic", return_value=self.player._stream_fault[3] + 1.1):
                        self.player._check_stream_fault()
                self.assertEqual(self.player.status, "playback_failed")
                self.assertEqual(self.player.failure_reason, reason)
                self.assertFalse(self.player.track.is_playing)
                self.assertEqual(self.qt_player.state, QMediaPlayer.PlaybackState.PausedState)

    def test_safe_task_error_categories_remain_distinct_from_authorization(self):
        for error, reason in (("auth_required", "auth_required"), ("timeout", "timeout"), ("network", "network"),
                ("invalid_response", "invalid_response"), ("request_failed", "unavailable")):
            with self.subTest(error=error):
                self.player.play_queue(self.songs)
                self.tasks.completed.emit(self.tasks.jobs[-1][0], None, error)
                self.assertEqual(self.player.status, "stream_unavailable")
                self.assertEqual(self.player.failure_reason, reason)
                self.assertEqual(self.player.track.lyrics, ("暂无歌词",))

    def test_missing_artwork_has_terminal_miss_and_lyrics_begin_loading(self):
        self.player.play_queue(self.songs)
        self.assertEqual(self.player.track.album_art_lookup_state, "miss")
        self.assertEqual(self.player.track.lyrics, ("加载歌词中",))
        self.assertEqual(self.artwork_tasks.jobs, [])

    def test_artwork_updates_current_original_colors_and_preserves_upcoming_identity(self):
        songs = (OnlineSong("netease", "1", "第一首", artwork_url="https://example.test/cover.png"), self.songs[1])
        self.player.play_queue(songs)
        self.assertEqual(self.player.track.album_art_lookup_state, "pending")
        art_token, action = self.artwork_tasks.jobs[-1]
        colors = ColorToken(200, 30, 50), ColorToken(130, 40, 70)
        with patch("local.owned_music_player.fetch_online_artwork", return_value=b"source-art") as fetch, \
                patch("local.owned_music_player.online_artwork_colors", return_value=colors):
            result = action()
        fetch.assert_called_once_with(songs[0].artwork_url)
        self.artwork_tasks.completed.emit(art_token, result, None)
        self.assertEqual(self.player.track.album_art_bytes, b"source-art")
        self.assertEqual(self.player.track.album_art_source, "original")
        self.assertEqual(self.player.track.album_art_lookup_state, "original")
        self.assertEqual(self.player.track.album_color, colors[0])
        self.assertEqual(self.player.track.album_queue[0].track_id, "owned:netease:2")
        self.assertEqual(self.player.track.album_queue[0].album_art_bytes, b"")
        self.assertTrue(self.qt_player.url.isEmpty())

    def test_stale_artwork_skips_request_and_cannot_replace_new_song(self):
        songs = (OnlineSong("netease", "1", "第一首", artwork_url="https://example.test/old.png"), self.songs[1])
        self.player.play_queue(songs)
        token, action = self.artwork_tasks.jobs[-1]
        self.player.play_queue((self.songs[1],))
        with patch("local.owned_music_player.fetch_online_artwork") as fetch:
            self.assertIsNone(action())
            fetch.assert_not_called()
        self.artwork_tasks.completed.emit(token, (b"old-art", ColorToken(1, 2, 3), ColorToken(4, 5, 6)), None)
        self.assertEqual(self.player.track.track_id, "owned:netease:2")
        self.assertEqual(self.player.track.album_art_bytes, b"")
        self.assertEqual(self.player.track.album_art_lookup_state, "miss")

    def test_artwork_failure_and_full_queue_finish_loading(self):
        song = OnlineSong("netease", "1", "第一首", artwork_url="https://example.test/cover.png")
        for accepted, result, error in ((True, None, None), (True, None, "request_failed"), (False, None, None)):
            with self.subTest(accepted=accepted, error=error):
                self.artwork_tasks.accepted = accepted
                self.player.play_queue((song,))
                token, action = self.artwork_tasks.jobs[-1]
                if accepted:
                    self.artwork_tasks.completed.emit(token, result, error)
                self.assertEqual(self.player.track.album_art_lookup_state, "failed")
                self.assertEqual(self.player.track.album_art_bytes, b"")

    def test_missing_failed_and_rejected_lyrics_finish_without_interrupting_audio(self):
        for result, error, accepted in ((None, None, True), (None, "unsupported", True), (None, None, False)):
            with self.subTest(error=error, accepted=accepted):
                self.tasks.accepted = True
                self.player.play_queue(self.songs)
                stream_token = self.tasks.jobs[-1][0]
                self.tasks.accepted = accepted
                self.tasks.completed.emit(stream_token, StreamInfo("netease", "1", "https://example.test/a", True), None)
                self.assertEqual(self.qt_player.state, QMediaPlayer.PlaybackState.PlayingState)
                if accepted:
                    self.tasks.completed.emit(self.tasks.jobs[-1][0], result, error)
                self.assertEqual(self.player.track.lyrics, ("暂无歌词",))
                self.assertEqual(self.player.track.timed_lyrics, ())
                self.assertEqual(self.player.status, "playing")

    def test_stale_lyrics_cannot_replace_new_song_loading_marker(self):
        self.player.play_queue(self.songs)
        self.tasks.completed.emit(self.tasks.jobs[-1][0], StreamInfo("netease", "1", "https://example.test/a", True), None)
        lyric_token = self.tasks.jobs[-1][0]
        self.player.send_playback_command("next")
        match = self.lyrics.parse_online_lyrics(raw_lrc="[00:01.00]旧歌词")
        self.tasks.completed.emit(lyric_token, match, None)
        self.assertEqual(self.player.track.lyrics, ("加载歌词中",))

    def test_artwork_workers_do_not_hold_stream_or_gui_while_fetch_waits(self):
        from local.music_tasks import MusicTasks
        release, entered = threading.Event(), threading.Event()
        workers = MusicTasks()
        player = OwnedMusicPlayer(self.service, self.lyrics, player=Player(), audio=object(),
            tasks=self.tasks, artwork_tasks=workers)
        def fetch(url):
            entered.set()
            release.wait(2)
            return b""
        try:
            with patch("local.owned_music_player.fetch_online_artwork", side_effect=fetch):
                started = time.monotonic()
                player.play_queue((OnlineSong("netease", "1", "第一首", artwork_url="https://example.test/cover.png"),))
                self.assertLess(time.monotonic() - started, .1)
                self.assertTrue(entered.wait(1))
                self.tasks.completed.emit(self.tasks.jobs[-1][0], StreamInfo("netease", "1", "https://example.test/a", True), None)
                self.assertEqual(player.player.state, QMediaPlayer.PlaybackState.PlayingState)
                self.assertEqual(player.track.album_art_lookup_state, "pending")
                release.set()
        finally:
            release.set()
            player.close()

    def test_position_observations_retain_qt_clock_and_seek_checks_identity(self):
        self.player.play_queue(self.songs)
        self.qt_player.positionChanged.emit(12345)
        track = self.player.visual_data().track
        self.assertEqual(track.position_seconds, 12.345)
        self.assertEqual(track.position_observed_seconds, 12.345)
        self.assertEqual(track.position_source, "owned-player")
        self.assertGreater(track.position_sampled_at, 0)
        self.assertFalse(self.player.seek_playback_position(10, "owned:netease:2"))
        self.assertTrue(self.player.seek_playback_position(999, track.track_id))
        self.assertEqual(self.qt_player.position, 120000)

    def test_bad_stream_scheme_and_closed_reply_cannot_play(self):
        self.player.play_queue(self.songs)
        token = self.tasks.jobs[-1][0]
        self.tasks.completed.emit(token, StreamInfo("netease", "1", "file:///secret", True), None)
        self.assertTrue(self.qt_player.url.isEmpty())
        self.player.close()
        self.tasks.completed.emit(token, StreamInfo("netease", "1", "https://example.test/a", True), None)
        self.assertTrue(self.qt_player.url.isEmpty())

    def test_invalid_stream_authority_or_control_chars_fail_before_qt(self):
        for url in ("https:///missing-host", "https://user:password@example.test/audio", "https://[broken/audio",
                    "https://example.test/audio\r\nInjected:value"):
            with self.subTest(url=url):
                self.player.play_queue(self.songs)
                token = self.tasks.jobs[-1][0]
                self.tasks.completed.emit(token, StreamInfo("netease", "1", url, True), None)
                self.assertTrue(self.qt_player.url.isEmpty())
                self.assertEqual(self.player.status, "stream_unavailable")
                self.assertEqual(self.player.failure_reason, "invalid_stream")

    def test_end_of_queue_fetches_next_personal_fm_batch(self):
        self.service.personal_fm = lambda source: self.songs
        self.player.play_personal_fm()
        token, action = self.tasks.jobs[-1]
        self.tasks.completed.emit(token, action(), None)
        self.assertEqual(self.player._fm_source, "netease")
        self.player.index = 1
        self.player._load_song()
        self.authorize_current()
        self.prefetch_tasks.completed.emit(self.prefetch_tasks.jobs[-1][0], None, "timeout")
        self.qt_player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.tasks.jobs[-1][0][2], "fm_continue")

    def test_playlist_follows_all_pages(self):
        offsets = []
        def page(source, playlist, *, limit, offset):
            offsets.append(offset)
            return CatalogPage((self.songs[0 if offset == 0 else 1],), next_offset=100, has_more=offset == 0)
        self.service.playlist_tracks = page
        self.player.play_playlist("netease", "list")
        token, action = self.tasks.jobs[-1]
        self.tasks.completed.emit(token, action(), None)
        self.assertEqual(offsets, [0, 100])
        self.assertEqual(self.player.queue, self.songs)

    def test_stale_playlist_stops_pagination_after_current_page(self):
        offsets = []
        def page(source, playlist, *, limit, offset):
            offsets.append(offset)
            self.player.stop()
            return CatalogPage((self.songs[0],), next_offset=100, has_more=True)
        self.service.playlist_tracks = page
        self.player.play_playlist("netease", "list")
        token, action = self.tasks.jobs[-1]
        self.assertEqual(action(), ())
        self.assertEqual(offsets, [0])

    def test_queued_old_stream_skips_network_when_new_song_already_selected(self):
        self.service.stream_url = lambda *args: self.fail("stale stream requested")
        self.player.play_queue(self.songs)
        token, action = self.tasks.jobs[-1]
        self.player.send_playback_command("next")
        self.assertIsNone(action())

    def test_daily_requires_real_catalog_result_and_does_not_guess_a_queue(self):
        self.service.daily_recommendations = lambda source: ()
        self.player.play_daily()
        token, action = self.tasks.jobs[-1]
        self.tasks.completed.emit(token, action(), None)
        self.assertEqual(self.player.status, "catalog_unavailable")
        self.assertIsNone(self.player.track)

    def test_yrc_preserves_word_times_with_lrc_fallback(self):
        match = self.lyrics.parse_online_lyrics(raw_yrc="[1000,1000](1000,500,0)你好(1500,500,0)世界",
                                               raw_lrc="[00:01.00]你好世界", source="netease")
        self.assertEqual(len(match.timed_lyrics[0].units), 2)
        self.assertEqual(match.timed_lyrics[0].units[1].start_seconds, 1.5)
        fallback = self.lyrics.parse_online_lyrics(raw_yrc="invalid", raw_lrc="[00:01.00]你好世界")
        self.assertEqual(fallback.timed_lyrics[0].time_seconds, 1)
        self.assertEqual(fallback.timed_lyrics[0].units, ())

    def test_owned_provider_does_not_poll_or_route_external_capture(self):
        self.player.play_queue(self.songs)
        external = EmptyIslandDataProvider()
        external.get_visual_data = lambda: self.fail("external poll while owned")
        external.plugin_provider = SimpleNamespace(plugins=lambda: ())
        external.message_detector = SimpleNamespace(messages=lambda: ())
        provider = PlaybackSourceProvider(external, self.player, lambda key, default: default)
        self.assertEqual(provider.get_visual_data().track.track_id, "owned:netease:1")
        self.assertIsNone(provider.get_audio_frame())
        self.assertFalse(provider.has_live_audio())
        self.assertEqual(provider.get_audio_route_snapshot().capture_scope, "owned-player")
        self.assertFalse(provider.select_audio_output("external"))

    def test_capture_disable_detaches_meter_without_waiting_for_stream_shutdown(self):
        release, entered = threading.Event(), threading.Event()
        def stop():
            entered.set()
            release.wait(2)
        external = WindowsMediaProvider.__new__(WindowsMediaProvider)
        external.audio_analyzer = SimpleNamespace(stop=stop)
        started = time.monotonic()
        external.suspend_live_audio()
        self.assertLess(time.monotonic() - started, .1)
        self.assertIsNone(external.audio_analyzer)
        self.assertTrue(entered.wait(1))
        release.set()


class OwnedControlThreadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.qt_player = ThreadRecordingPlayer()
        self.tasks, self.artwork_tasks = Tasks(), Tasks()
        self.service = SimpleNamespace(stream_url=lambda *args: None, lyrics=lambda *args: None)
        self.player = OwnedMusicPlayer(self.service, LocalLyricsStore(root=Path(self.temp.name)),
            player=self.qt_player, audio=object(), tasks=self.tasks, artwork_tasks=self.artwork_tasks)
        self.songs = (OnlineSong("netease", "1", "first", duration_seconds=120),
                      OnlineSong("netease", "2", "second", duration_seconds=150))
        self.player.play_queue(self.songs)
        self.qt_player.calls.clear()
        self.main_thread = threading.get_ident()
        self.workers = []

    def tearDown(self):
        self.player.close()
        deadline = time.monotonic() + 2.5
        while any(worker.is_alive() for worker in self.workers) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        for worker in self.workers:
            worker.join(.1)
        self.temp.cleanup()

    def start_control(self, action):
        """Observe enqueue on its emitter thread; GUI delivery waits for the test."""
        emitted, finished = threading.Event(), threading.Event()
        request, result = [], []
        def observe(value):
            request.append(value)
            emitted.set()
        self.player._control_requested.connect(observe, Qt.ConnectionType.DirectConnection)
        def run():
            try:
                result.append(action())
            finally:
                finished.set()
        worker = threading.Thread(target=run, daemon=True)
        self.workers.append(worker)
        worker.start()
        self.assertTrue(emitted.wait(1), "worker did not enqueue Qt control")
        self.player._control_requested.disconnect(observe)
        return worker, finished, result, request[0]

    def finish_control(self, control):
        worker, finished, result, _ = control
        deadline = time.monotonic() + 2.5
        while not finished.is_set() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        worker.join(.1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(result), 1)
        return result[0]

    def test_background_pause_next_and_seek_touch_media_only_on_main_thread(self):
        for operation, expected in (("pause", ("state", "pause")),
                                    ("next", ("stop", "source")),
                                    ("seek", ("seekable", "position"))):
            with self.subTest(operation=operation):
                self.player.play_queue(self.songs)
                self.qt_player.state = QMediaPlayer.PlaybackState.PlayingState
                self.qt_player.calls.clear()
                action = (lambda: self.player.seek_playback_position(12.5)) if operation == "seek" else \
                    (lambda: self.player.send_playback_command("toggle_play_pause" if operation == "pause" else "next"))
                control = self.start_control(action)
                self.assertEqual(self.qt_player.calls, [])
                self.assertTrue(self.finish_control(control))
                self.assertEqual(tuple(name for name, _ in self.qt_player.calls), expected)
                self.assertTrue(all(thread == self.main_thread for _, thread in self.qt_player.calls))
                if operation == "pause":
                    self.assertEqual(self.qt_player.state, QMediaPlayer.PlaybackState.PausedState)
                elif operation == "next":
                    self.assertEqual(self.player.track.track_id, "owned:netease:2")
                else:
                    self.assertEqual(self.qt_player.position, 12500)

    def test_background_returns_actual_rejection_from_gui(self):
        self.player.set_playback_mode("shuffle")
        self.qt_player.calls.clear()
        control = self.start_control(lambda: self.player.send_playback_command("previous"))
        self.assertFalse(self.finish_control(control))
        self.assertEqual(self.qt_player.calls, [])
        self.qt_player.seekable = False
        control = self.start_control(lambda: self.player.seek_playback_position(10))
        self.assertFalse(self.finish_control(control))
        self.assertEqual(self.qt_player.calls, [("seekable", self.main_thread)])

    def test_worker_mode_applies_on_owner_thread_and_supports_idle(self):
        self.player.stop()
        self.qt_player.calls.clear()
        applied = []
        self.player.changed.connect(lambda: applied.append(threading.get_ident()))
        control = self.start_control(lambda: self.player.set_playback_mode("shuffle"))
        self.assertEqual(self.player.playback_mode, "sequential")
        self.assertTrue(self.finish_control(control))
        self.assertEqual(self.player.playback_mode, "shuffle")
        self.assertEqual(self.qt_player.calls, [])
        self.assertEqual(applied, [self.main_thread])

    def test_worker_mode_cannot_apply_to_replaced_same_track_queue_or_new_catalog(self):
        for replacement in ("same_track_queue", "catalog", "stop"):
            self.player.play_queue(self.songs)
            control = self.start_control(lambda: self.player.set_playback_mode("shuffle"))
            if replacement == "same_track_queue":
                self.player.play_queue(self.songs)
            elif replacement == "catalog":
                self.service.daily_recommendations = lambda source: self.songs
                self.player.play_daily()
            else:
                self.player.stop()
            self.qt_player.calls.clear()
            self.assertFalse(self.finish_control(control))
            self.assertEqual(self.player.playback_mode, "sequential")
            self.assertEqual(self.qt_player.calls, [])

    def test_mode_change_fences_queued_next_even_with_same_active_track(self):
        control = self.start_control(lambda: self.player.send_playback_command("next"))
        track_id = self.player.track.track_id
        self.player.set_playback_mode("repeat_one")
        self.qt_player.calls.clear()
        self.assertFalse(self.finish_control(control))
        self.assertEqual(self.player.track.track_id, track_id)
        self.assertEqual(self.qt_player.calls, [])

    def test_worker_mode_deadline_and_close_reject_without_reentry(self):
        for reason in ("deadline", "closed"):
            control = self.start_control(lambda: self.player.set_playback_mode("shuffle"))
            if reason == "deadline":
                with patch("local.owned_music_player.time.monotonic", return_value=control[3][3]):
                    self.assertFalse(self.finish_control(control))
            else:
                self.player.close()
                self.assertFalse(self.finish_control(control))
            self.assertEqual(self.player.playback_mode, "sequential")

    def test_queued_command_and_seek_cannot_apply_after_track_changes(self):
        for kind in ("command", "seek"):
            with self.subTest(kind=kind):
                self.player.play_queue(self.songs)
                action = (lambda: self.player.send_playback_command("next")) if kind == "command" else \
                    (lambda: self.player.seek_playback_position(10))
                control = self.start_control(action)
                self.player.play_queue(self.songs, index=1)
                self.qt_player.calls.clear()
                self.assertFalse(self.finish_control(control))
                self.assertEqual(self.qt_player.calls, [])
                self.assertEqual(self.player.track.track_id, "owned:netease:2")

    def test_expired_request_is_not_applied_after_worker_timeout(self):
        control = self.start_control(lambda: self.player.send_playback_command("toggle_play_pause"))
        worker, finished, result, request = control
        worker.join(2.5)  # Deliberately leave the owning Qt loop undelivered.
        self.assertTrue(finished.is_set())
        self.assertEqual(result, [False])
        # Windows' monotonic clock can equal the deadline when Event.wait ends.
        with patch("local.owned_music_player.time.monotonic", return_value=request[3]):
            self.app.processEvents()
        self.assertTrue(request[4].is_set())
        self.assertEqual(self.qt_player.calls, [])

    def test_close_rejects_already_queued_command_without_media_reentry(self):
        control = self.start_control(lambda: self.player.send_playback_command("toggle_play_pause"))
        self.player.close()
        self.qt_player.calls.clear()
        self.assertFalse(self.finish_control(control))
        self.assertEqual(self.qt_player.calls, [])
        self.assertIsNone(self.player.track)

    def test_gui_controls_return_synchronously_without_worker_event_wait(self):
        with patch.object(self.player, "_request_control", side_effect=AssertionError("GUI control queued")):
            self.assertTrue(self.player.send_playback_command("toggle_play_pause"))
            self.assertTrue(self.player.seek_playback_position(7, self.player.track.track_id))
            self.assertTrue(self.player.send_playback_command("next"))
        self.assertTrue(all(thread == self.main_thread for _, thread in self.qt_player.calls))
        self.assertEqual(self.player.track.track_id, "owned:netease:2")


class StartupRoutingTests(unittest.TestCase):
    def test_first_choice_matrix(self):
        for owned, system, playing, expected in (
            (True, False, False, "login"), (True, False, True, "login"),
            (False, True, False, "island"), (False, True, True, "island"),
            (True, True, False, "login"), (True, True, True, "island"),
        ):
            with self.subTest(owned=owned, system=system, playing=playing):
                self.assertEqual(startup_destination(first_run=True, owned=owned, system=system,
                    playing=playing, has_login=False), expected)

    def test_subsequent_launch_matrix(self):
        for owned, system in ((True, False), (False, True), (True, True)):
            for playing, saved in ((False, False), (False, True), (True, False), (True, True)):
                with self.subTest(owned=owned, system=system, playing=playing, saved=saved):
                    self.assertEqual(startup_destination(first_run=False, owned=owned, system=system,
                        playing=playing, has_login=saved), "login" if not playing and not saved else "island")
