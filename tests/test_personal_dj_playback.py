"""DJ voice/music order uses the owned queue and actual existing controls."""
from dataclasses import replace
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import LyricPayload, OnlineMusicError, OnlineSong, StreamInfo
from local.owned_music_player import OwnedMusicPlayer
from tests.test_owned_playback import Player, Tasks


def segments(number=1):
    stream = StreamInfo("netease", f"dj:voice-{number}", f"https://example.test/voice-{number}.mp3", True,
                        quality="dj_voice", expires_at=time.time() + 3600)
    return (OnlineSong("netease", stream.song_id, "私人DJ · 语音", duration_seconds=10,
                       media_kind="dj_voice", direct_stream=stream),
            OnlineSong("netease", f"song-{number}", f"Song {number}", duration_seconds=120))


class DJPlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.tasks, self.artwork, self.prefetch = Tasks(), Tasks(), Tasks()
        self.qt = Player()
        self.calls = []
        self.batch = segments(1) + segments(2)
        self.service = SimpleNamespace(personal_dj=self.dj, personal_fm=self.fm,
            stream_url=self.stream, lyrics=self.lyrics)
        self.owned = OwnedMusicPlayer(self.service, LocalLyricsStore(root=Path(self.folder.name)),
            player=self.qt, audio=object(), tasks=self.tasks, artwork_tasks=self.artwork,
            prefetch_tasks=self.prefetch, stream_cache=None)
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(self.owned.close)

    def dj(self, source):
        self.calls.append(("dj", source)); return self.batch

    def fm(self, source):
        self.calls.append(("fm", source)); return (segments(3)[1],)

    def stream(self, source, identity):
        self.calls.append(("stream", identity)); return StreamInfo(source, identity, "https://example.test/song.mp3", True)

    def lyrics(self, source, identity):
        self.calls.append(("lyrics", identity)); return LyricPayload(source, identity, raw_lrc="[00:01]真实歌词")

    def run_latest(self, tasks, kind):
        token, action = next((t, a) for t, a in reversed(tasks.jobs) if t[0] == kind)
        try:
            result = action(); error = None
        except Exception as exc:
            result, error = None, getattr(exc, "code", "request_failed")
        tasks.completed.emit(token, result, error)
        return token

    def start(self):
        self.assertTrue(self.owned.play_personal_dj())
        self.run_latest(self.tasks, "catalog")
        self.run_latest(self.tasks, "stream")

    def test_genuine_voice_skips_song_and_lyric_endpoints_and_retains_queue_order(self):
        self.start()
        self.assertEqual([s.media_kind for s in self.owned.queue], ["dj_voice", "song", "dj_voice", "song"])
        self.assertEqual(self.calls, [("dj", "netease")])
        self.assertEqual(self.owned.track.lyrics, ("私人DJ正在介绍音乐",))
        self.assertTrue(self.qt.url.toString().endswith("voice-1.mp3"))
        self.assertTrue(self.owned.is_personal_fm())
        self.assertEqual(self.owned.available_playback_modes(), ("sequential", "repeat_one"))

    def test_single_repeat_advances_voice_once_and_repeats_only_song(self):
        self.start()
        self.owned.set_playback_mode("repeat_one")
        self.owned._end_current_stream()
        self.assertEqual(self.owned.index, 1)
        self.run_latest(self.tasks, "stream")
        self.run_latest(self.tasks, "lyrics")
        self.assertIn(("lyrics", "song-1"), self.calls)
        self.owned._end_current_stream()
        self.assertEqual(self.owned.index, 1)
        self.assertEqual(self.owned.queue[self.owned.index].media_kind, "song")

    def test_manual_next_previous_choose_music_without_repeating_introduction(self):
        self.start()
        self.assertTrue(self.owned.send_playback_command("next"))
        self.assertEqual(self.owned.index, 1)
        self.assertTrue(self.owned.send_playback_command("next"))
        self.assertEqual(self.owned.index, 3)
        self.assertTrue(self.owned.send_playback_command("previous"))
        self.assertEqual(self.owned.index, 1)

    def test_dj_continuation_keeps_voice_song_pairs_including_service_repeated_song(self):
        self.start()
        self.batch = (segments(3)[0], self.batch[1])
        self.owned._end_current_stream()
        self.run_latest(self.tasks, "stream")
        self.run_latest(self.prefetch, "fm_prefetch")
        self.assertEqual([s.song_id for s in self.owned.queue[-2:]], ["dj:voice-3", "song-1"])
        self.assertFalse(any(call[0] == "fm" for call in self.calls))

    def test_voice_expiry_never_falls_back_to_song_url(self):
        voice = segments()[0]
        expired = replace(voice, direct_stream=replace(voice.direct_stream, expires_at=time.time() - 1))
        with self.assertRaises(OnlineMusicError):
            self.owned._resolve_stream(expired)
        self.assertEqual(self.calls, [])

    def test_repeated_dj_tail_song_cannot_suppress_next_batch_prefetch(self):
        self.start()
        self.owned._end_current_stream()
        self.run_latest(self.tasks, "stream")
        self.batch = (segments(3)[0], segments(2)[1])
        self.run_latest(self.prefetch, "fm_prefetch")
        old_count = len(self.prefetch.jobs)
        self.owned.index = 3
        self.owned._load_song()
        self.run_latest(self.tasks, "stream")
        self.assertEqual(len(self.prefetch.jobs), old_count + 1)

    def test_manual_next_at_batch_end_skips_new_intro_with_and_without_pending_prefetch(self):
        for pending in (False, True):
            with self.subTest(pending=pending):
                self.batch = segments(1) + segments(2)
                self.start()
                self.owned.index = 3
                self.owned._load_song()
                self.run_latest(self.tasks, "stream")
                self.batch = segments(4)
                self.owned._fm_prefetch_pending = pending
                self.assertTrue(self.owned.send_playback_command("next"))
                self.run_latest(self.prefetch if pending else self.tasks, "fm_prefetch" if pending else "catalog")
                self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-4")
                self.assertFalse(self.owned._fm_skip_voice_pending)

    def test_artwork_neighbors_are_three_songs_and_history_keeps_three_songs(self):
        self.batch = tuple(song for i in range(1, 7) for song in segments(i))
        self.start()
        self.assertEqual([entry.title for entry in self.owned._album_queue()], ["Song 1", "Song 2", "Song 3"])
        self.owned.index = 9
        self.owned._load_song()
        previous = self.owned._previous_indices(include_voice=False)
        self.assertEqual([self.owned.queue[i].title for i in previous], ["Song 4", "Song 3", "Song 2"])

    def test_old_dj_catalog_cannot_replace_normal_queue(self):
        self.owned.play_personal_dj()
        token, action = self.tasks.jobs[-1]
        self.owned.play_queue((segments(9)[1],))
        self.tasks.completed.emit(token, self.batch, None)
        self.assertEqual(self.owned.queue[0].song_id, "song-9")
        self.assertFalse(self.owned.is_personal_fm())

    def test_normal_mode_preference_restores_after_dj(self):
        self.owned.set_playback_mode("shuffle")
        self.start()
        self.assertEqual(self.owned.playback_mode, "sequential")
        self.owned.set_playback_mode("repeat_one")
        self.owned.play_queue((segments(9)[1],))
        self.assertEqual(self.owned.playback_mode, "shuffle")
