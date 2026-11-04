"""Owned lyric fetches retry within one track budget and cannot hold skeletons."""
from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import LyricPayload, OnlineMusicError, OnlineSong, StreamInfo
from local.music_tasks import MusicTasks
from local.owned_music_player import OwnedMusicPlayer
from tests.test_owned_playback import Tasks, ThreadRecordingPlayer


class OwnedLyricRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = LocalLyricsStore(Path(folder.name))
        self.tasks = Tasks()
        self.player = OwnedMusicPlayer(SimpleNamespace(lyrics=Mock()), self.store,
            player=ThreadRecordingPlayer(), audio=object(), tasks=self.tasks,
            artwork_tasks=Tasks(), prefetch_tasks=Tasks(), stream_cache=None)
        self.addCleanup(self.player.close)
        self.songs = tuple(OnlineSong("netease", str(i), f"Song {i}", "Artist") for i in range(3))

    def start(self, index=0):
        self.player.play_queue(self.songs, index=index)
        token = self.tasks.jobs[-1][0]
        self.tasks.completed.emit(token, StreamInfo("netease", str(index), "https://example.test/audio", True), None)
        return self.tasks.jobs[-1]

    def result(self, index, words):
        payload = LyricPayload("netease", str(index), raw_lrc=f"[00:01.00]{words}")
        return payload, self.store.parse_online_lyrics(raw_lrc=payload.raw_lrc)

    def test_timeout_retries_once_and_out_of_order_result_cannot_replace_retry(self):
        first = self.start()
        self.tasks.completed.emit(first[0], None, "timeout")
        retry = self.tasks.jobs[-1]
        self.assertNotEqual(first[0], retry[0])
        self.assertEqual(self.player.track.lyrics, ("加载歌词中",))
        self.tasks.completed.emit(first[0], self.result(0, "expired words"), None)
        self.assertEqual(self.player.track.lyrics, ("加载歌词中",))
        self.tasks.completed.emit(retry[0], self.result(0, "current words"), None)
        self.assertEqual(self.player.track.lyrics, ("current words",))
        self.assertFalse(self.player._lyrics_guard_timer.isActive())

    def test_second_timeout_is_terminal_and_does_not_interrupt_audio(self):
        first = self.start()
        self.player.player.calls.clear()
        self.tasks.completed.emit(first[0], None, "timeout")
        retry = self.tasks.jobs[-1]
        self.tasks.completed.emit(retry[0], None, "timeout")
        self.assertEqual(self.player.track.lyrics, ("暂无歌词",))
        self.assertEqual(self.player.player.calls, [])
        self.assertIsNone(self.player._lyrics_request_pending)
        self.assertFalse(self.player._lyrics_guard_timer.isActive())

    def test_real_official_timeout_retries_before_exact_id_unavailable_fallback(self):
        initial = self.start()
        self.player.service.lyrics.side_effect = [OnlineMusicError("timeout", "netease"), self.result(0, "recovered")[0]]
        with self.assertRaises(OnlineMusicError) as failed:
            initial[1]()
        self.tasks.completed.emit(initial[0], None, failed.exception.code)
        retry = self.tasks.jobs[-1]
        self.tasks.completed.emit(retry[0], retry[1](), None)
        self.assertEqual(self.player.track.lyrics, ("recovered",))
        self.assertEqual(self.player.service.lyrics.call_count, 2)

    def test_deadline_includes_stream_queue_wait_and_late_fetch_cannot_resurrect(self):
        with patch("local.owned_music_player.time.monotonic", return_value=100.):
            self.player.play_queue(self.songs)
        stream = self.tasks.jobs[-1][0]
        with patch("local.owned_music_player.time.monotonic", return_value=130.):
            self.player._check_lyrics_deadline()
            self.tasks.completed.emit(stream, StreamInfo("netease", "0", "https://example.test/audio", True), None)
        self.assertEqual(self.player.track.lyrics, ("暂无歌词",))
        # An authorized audio stream may now fetch a late recovery, but this
        # cannot reintroduce a loading marker or extend its original deadline.
        self.assertFalse(self.player._lyrics_guard_timer.isActive())
        self.assertEqual(self.player._lyrics_wait_started_at, 100.)

    def test_rapid_switches_reject_all_old_track_results_and_stop_clears_guard(self):
        first = self.start(0)
        second = self.start(1)
        current = self.start(2)
        self.tasks.completed.emit(second[0], self.result(1, "wrong second"), None)
        self.tasks.completed.emit(first[0], self.result(0, "wrong first"), None)
        self.assertEqual(self.player.track.track_id, "owned:netease:2")
        self.assertEqual(self.player.track.lyrics, ("加载歌词中",))
        self.tasks.completed.emit(current[0], self.result(2, "right current"), None)
        self.assertEqual(self.player.track.lyrics, ("right current",))
        self.player.stop()
        self.assertFalse(self.player._lyrics_guard_timer.isActive())
        self.assertIsNone(self.player._lyrics_request_pending)

    def test_real_hung_lyric_lane_retries_then_resolves_without_stream_worker(self):
        lane = MusicTasks(task_timeout_seconds=.04)
        self.addCleanup(lane.close)
        lane.completed.connect(self.player._completed)
        self.player.lyric_tasks = lane
        release = threading.Event()
        calls = []
        def fetch(_song, _preferences, **_options):
            calls.append(threading.get_ident())
            release.wait(2.)
            return self.result(0, "late")
        with patch.object(self.player, "_fetch_lyrics", side_effect=fetch):
            try:
                self.start()
                deadline = time.monotonic() + 2.
                while self.player.track.lyrics == ("加载歌词中",) and time.monotonic() < deadline:
                    QTest.qWait(5)
                self.assertEqual(self.player.track.lyrics, ("暂无歌词",))
                self.assertEqual(len(calls), 2)
                self.assertEqual(sum(job[0][0] == "stream" for job in self.tasks.jobs), 1)
                self.assertFalse(any(job[0][0] == "lyrics" for job in self.tasks.jobs))
            finally:
                release.set()
                QTest.qWait(20)
        self.assertEqual(self.player.track.lyrics, ("暂无歌词",))


if __name__ == "__main__":
    unittest.main()
