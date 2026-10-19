"""Transport faults must use decoder evidence without erasing valid lyrics."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import CatalogPage, LyricPayload, OnlineSong, StreamInfo
from local.owned_music_player import OwnedMusicPlayer
from tests.test_owned_playback import Player, Tasks


class DecoderClockPlayer(Player):
    def __init__(self):
        super().__init__()
        del self.position
        self._position = 0
        self.seekable = True

    def position(self):
        return self._position

    def setSource(self, url):
        super().setSource(url)
        self.setPosition(0)

    def setPosition(self, value):
        self._position = value
        self.positionChanged.emit(value)

    def isSeekable(self):
        return self.seekable

    def mediaStatus(self):
        return QMediaPlayer.MediaStatus.LoadedMedia


class OwnedStreamFaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.tasks, self.artwork, self.prefetch = Tasks(), Tasks(), Tasks()
        self.qt = DecoderClockPlayer()
        self.store = LocalLyricsStore(root=Path(self.temp.name))
        self.service = SimpleNamespace(stream_url=lambda *args: None, lyrics=lambda *args: None)
        self.owned = OwnedMusicPlayer(self.service, self.store, player=self.qt, audio=object(),
            tasks=self.tasks, artwork_tasks=self.artwork, prefetch_tasks=self.prefetch, stream_cache=None)
        self.songs = tuple(OnlineSong("netease", str(index), f"Song {index}", duration_seconds=120)
            for index in range(3))

    def tearDown(self):
        self.owned.close()
        self.temp.cleanup()

    def start(self, duration=4000, position=3800, trial=False, recovery_used=True):
        self.owned.play_queue(self.songs)
        # These observer regressions also cover terminal behavior after the
        # one recovery budget has been consumed. Fresh recovery is tested below.
        self.owned._stream_recovery_attempted = recovery_used
        self.authorize(trial)
        if duration:
            self.qt.durationChanged.emit(duration)
        self.qt.setPosition(position)
        self.payload = LyricPayload("netease", "0", raw_lrc="[00:00.00]First\n[00:02.00]Last",
            source="local-fixture")
        token = next(token for token, _action in reversed(self.tasks.jobs) if token[0] == "lyrics")
        match = self.store.parse_online_lyrics(raw_lrc=self.payload.raw_lrc, source=self.payload.source)
        self.tasks.completed.emit(token, (self.payload, match), None)
        self.lyrics = self.owned.track.lyrics
        self.timed_lyrics = self.owned.track.timed_lyrics

    def authorize(self, trial=False):
        token = next(token for token, _action in reversed(self.tasks.jobs) if token[0] == "stream")
        song = self.owned.queue[self.owned.index]
        self.tasks.completed.emit(token,
            StreamInfo(song.source_id, song.song_id, "https://fixture.test/audio", True, trial), None)

    def fault(self, code=QMediaPlayer.Error.ResourceError, at=100):
        with patch("local.owned_music_player.time.monotonic", return_value=at):
            self.qt.errorOccurred.emit(code, "signed-url-secret")
        self.owned._stream_fault_timer.stop()

    def check(self, at):
        with patch("local.owned_music_player.time.monotonic", return_value=at):
            self.owned._check_stream_fault()

    def test_no_error_keeps_stream_clock_payload_and_lyrics(self):
        self.start()
        before = self.owned.track
        self.fault(QMediaPlayer.Error.NoError)
        self.assertIs(self.owned.track, before)
        self.assertIs(self.owned._lyric_payload, self.payload)
        self.assertIsNone(self.owned._stream_fault)
        self.assertEqual(self.owned.failure_reason, "")

    def test_transport_fault_preserves_real_playback_and_duplicates_do_not_extend_stall(self):
        self.start()
        self.fault()
        before = self.owned._stream_fault
        self.fault(at=100.9)
        self.assertEqual(self.owned._stream_fault, before)
        self.assertEqual(self.owned.status, "playing")
        self.assertTrue(self.owned.track.is_playing)
        self.assertEqual(self.owned.track.timed_lyrics, self.timed_lyrics)
        self.assertIs(self.owned._lyric_payload, self.payload)
        self.assertEqual(self.owned.failure_reason, "resource")
        self.check(100.99)
        self.assertEqual(self.owned.index, 0)
        self.check(101.01)
        self.assertEqual(self.owned.index, 1)

    def test_faulty_tail_reuses_sequential_shuffle_and_repeat_one(self):
        for mode, expected_index in (("sequential", 1), ("shuffle", 2), ("repeat_one", 0)):
            with self.subTest(mode=mode):
                self.owned._random.shuffle = lambda items: items.reverse()
                self.owned.set_playback_mode(mode)
                self.start(trial=(mode == "repeat_one"))
                before_epoch = self.owned._epoch
                self.fault(QMediaPlayer.Error.NetworkError)
                self.check(101.1)
                self.assertEqual(self.owned.index, expected_index)
                self.assertEqual(self.owned._epoch, before_epoch + 1)
                self.assertEqual(self.owned.status, "loading_stream")
                self.assertIsNone(self.owned._stream_fault)
                self.assertEqual(self.owned._decoded_duration_ms, 0)
                self.check(500)
                self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
                self.assertEqual(self.owned._epoch, before_epoch + 1)

    def test_catalog_duration_or_last_lyric_never_acts_as_decoded_eof(self):
        self.start(duration=0, position=119800)
        self.fault()
        self.check(101.1)
        self.assertEqual(self.owned.index, 0)
        self.assertEqual(self.owned.status, "playback_failed")
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.assertEqual(self.owned.track.lyrics, self.lyrics)
        self.assertEqual(self.owned.track.timed_lyrics, self.timed_lyrics)
        self.assertIsNone(self.owned._lyric_payload)
        self.assertFalse(self.owned.track.is_playing)

    def test_midstream_beyond_duration_and_zero_clock_fail_without_skipping(self):
        for position in (0, 2000, 3499, 4001):
            with self.subTest(position=position):
                self.start(position=position)
                self.fault()
                self.check(101.1)
                self.assertEqual(self.owned.index, 0)
                self.assertEqual(self.owned.status, "playback_failed")
                self.assertEqual(self.owned.track.lyrics, self.lyrics)
                self.assertEqual(self.owned.track.position_seconds, position / 1000)
                self.assertIsNone(self.owned._stream_fault)

    def test_decoder_retracting_duration_makes_fault_terminal_without_skipping(self):
        self.start()
        self.qt.durationChanged.emit(0)
        self.assertEqual(self.owned._decoded_duration_ms, 0)
        self.fault()
        self.check(101.1)
        self.assertEqual(self.owned.index, 0)
        self.assertEqual(self.owned.status, "playback_failed")
        self.assertEqual(self.owned.track.lyrics, self.lyrics)

    def test_clock_must_drain_after_fault_and_position_projection_cannot_prove_eof(self):
        self.start(position=3200)
        self.fault()
        self.qt.setPosition(3600)
        self.check(101.1)
        self.assertEqual(self.owned.index, 0)
        self.owned.track = replace(self.owned.track, position_seconds=4)
        self.check(101.9)
        self.assertEqual(self.owned.index, 0)
        self.assertEqual(self.qt.position(), 3600)
        self.check(102.11)
        self.assertEqual(self.owned.index, 1)

    def test_pause_and_resume_without_watchdog_ticks_require_new_active_stall(self):
        self.start()
        self.fault()
        with patch("local.owned_music_player.time.monotonic", return_value=100.1):
            self.owned.send_playback_command("toggle_play_pause")
        self.fault(at=120)
        self.check(125)
        self.assertEqual(self.owned.index, 0)
        self.assertFalse(self.owned.track.is_playing)
        self.assertEqual(self.owned.track.lyrics, self.lyrics)
        with patch("local.owned_music_player.time.monotonic", return_value=130):
            self.owned.send_playback_command("toggle_play_pause")
        self.check(130.9)
        self.assertEqual(self.owned.index, 0)
        self.check(131.01)
        self.assertEqual(self.owned.index, 1)

    def test_backward_clock_jitter_cannot_extend_stalled_fault_forever(self):
        self.start()
        self.fault()
        original = self.owned._stream_fault
        self.qt.setPosition(3799)
        self.check(100.8)
        self.assertEqual(self.owned._stream_fault, original)
        self.qt.setPosition(3800)
        self.check(101.1)
        self.assertEqual(self.owned.index, 1)

    def test_trial_tail_uses_decoder_duration_and_keeps_trial_until_transition(self):
        self.start(duration=30000, position=29800, trial=True)
        self.fault()
        self.assertEqual(self.owned.status, "trial")
        self.assertEqual(self.owned.track.lyrics, self.lyrics)
        self.assertTrue(self.owned.track.is_playing)
        self.check(101.1)
        self.assertEqual(self.owned.index, 1)
        self.assertEqual(self.owned.status, "loading_stream")

    def test_successful_seek_clears_fault_but_rejected_seek_keeps_evidence(self):
        self.start()
        self.fault()
        self.assertFalse(self.owned.seek_playback_position(1, "wrong-track"))
        self.assertIsNotNone(self.owned._stream_fault)
        self.qt.seekable = False
        self.assertFalse(self.owned.seek_playback_position(1))
        self.assertIsNotNone(self.owned._stream_fault)
        self.qt.seekable = True
        self.assertTrue(self.owned.seek_playback_position(1))
        self.assertIsNone(self.owned._stream_fault)
        self.assertFalse(self.owned._stream_fault_timer.isActive())
        self.check(200)
        self.assertEqual(self.owned.index, 0)
        self.assertEqual(self.qt.position(), 1000)
        self.assertEqual(self.owned.status, "playing")
        self.assertEqual(self.owned.track.lyrics, self.lyrics)

    def test_song_catalog_stop_and_close_invalidate_fault_and_decoder_duration(self):
        for action in ("next", "catalog", "stop", "close"):
            with self.subTest(action=action):
                self.owned._closed = False
                self.start()
                self.fault()
                if action == "next":
                    self.owned.send_playback_command("next")
                elif action == "catalog":
                    self.service.daily_recommendations = lambda source: self.songs
                    self.owned.play_daily()
                else:
                    getattr(self.owned, action)()
                self.assertIsNone(self.owned._stream_fault)
                self.assertEqual(self.owned._decoded_duration_ms, 0)
                before = self.owned.track
                self.check(1000)
                self.qt.errorOccurred.emit(QMediaPlayer.Error.ResourceError, "old-stream-secret")
                self.assertEqual(self.owned.track, before)
                self.assertEqual(self.owned.failure_reason, "")

    def test_normal_eof_clears_fault_and_advances_once(self):
        self.start()
        self.fault()
        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.owned.index, 1)
        self.assertIsNone(self.owned._stream_fault)
        epoch = self.owned._epoch
        self.check(101.1)
        self.assertEqual(self.owned._epoch, epoch)

    def test_pending_catalog_buffered_error_returns_to_existing_tail_watchdog_on_failure(self):
        self.start()
        self.service.daily_recommendations = lambda source: self.songs
        self.owned.play_daily()
        catalog = self.tasks.jobs[-1][0]
        self.fault(at=100)
        self.qt.setPosition(3900)
        self.assertEqual(self.owned.track.position_seconds, 3.9)
        self.assertEqual(self.owned.status, "loading_catalog")
        with patch("local.owned_music_player.time.monotonic", return_value=101):
            self.tasks.completed.emit(catalog, None, "timeout")
        self.assertIsNotNone(self.owned._stream_fault)
        self.assertEqual(self.owned._decoded_duration_ms, 4000)
        self.owned._stream_fault_timer.stop()
        self.check(102.1)
        self.assertEqual(self.owned.index, 1)
        self.assertEqual(self.owned.status, "loading_stream")

    def test_pending_catalog_invalid_media_is_terminal_on_failure_and_keeps_lyrics(self):
        self.start()
        self.service.daily_recommendations = lambda source: self.songs
        self.owned.play_daily()
        catalog = self.tasks.jobs[-1][0]
        self.qt.stop()
        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.InvalidMedia)
        self.tasks.completed.emit(catalog, None, "timeout")
        self.assertEqual(self.owned.status, "playback_failed")
        self.assertFalse(self.owned.track.is_playing)
        self.assertEqual(self.owned._stream_epoch, 0)
        self.assertEqual(self.owned.track.timed_lyrics, self.timed_lyrics)

    def test_daily_playlist_and_fm_catalogs_share_tail_recovery(self):
        self.service.daily_recommendations = lambda source: self.songs
        self.service.personal_fm = lambda source: self.songs
        self.service.playlist_tracks = lambda *args, **kwargs: CatalogPage(self.songs)
        for source in ("daily", "playlist", "fm"):
            with self.subTest(source=source):
                if source == "daily":
                    self.owned.play_daily()
                elif source == "playlist":
                    self.owned.play_playlist("netease", "fixture-playlist")
                else:
                    self.owned.play_personal_fm()
                token, action = self.tasks.jobs[-1]
                self.tasks.completed.emit(token, action(), None)
                self.authorize()
                self.qt.durationChanged.emit(4000)
                self.qt.setPosition(3800)
                self.fault()
                self.check(101.1)
                self.assertEqual(self.owned.index, 1)
                self.assertEqual(self.owned.status, "loading_stream")

    def test_fm_waiting_prefetch_retires_drained_stream_and_accepts_only_one_new_batch(self):
        self.service.personal_fm = lambda source: self.songs
        self.owned.play_personal_fm()
        token, action = self.tasks.jobs[-1]
        self.tasks.completed.emit(token, action(), None)
        self.authorize()
        pending = self.prefetch.jobs[-1][0]
        self.owned.index = 2
        self.owned._load_song()
        self.authorize()
        self.qt.durationChanged.emit(4000)
        self.qt.setPosition(3800)
        self.fault()
        self.check(101.1)
        self.assertEqual(self.owned.index, 2)
        self.assertTrue(self.owned._fm_advance_pending)
        self.assertEqual(self.owned.status, "loading_catalog")
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.StoppedState)
        self.assertTrue(self.qt.source().isEmpty())
        self.assertEqual(self.owned._stream_epoch, 0)
        self.assertFalse(self.owned.track.is_playing)
        self.assertIsNone(self.owned._stream_fault)
        self.check(500)
        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        incoming = (OnlineSong("netease", "3", "Next FM fixture"),)
        self.prefetch.completed.emit(pending, incoming, None)
        self.assertEqual(self.owned.track.track_id, "owned:netease:3")
        epoch = self.owned._epoch
        self.prefetch.completed.emit(pending, incoming, None)
        self.assertEqual(self.owned._epoch, epoch)

    def test_format_access_denied_and_invalid_media_are_terminal_even_at_tail(self):
        for code in (QMediaPlayer.Error.FormatError, QMediaPlayer.Error.AccessDeniedError):
            with self.subTest(code=code):
                self.start()
                self.fault(code)
                self.assertEqual(self.owned.status, "playback_failed")
                self.assertIsNone(self.owned._stream_fault)
                self.assertEqual(self.owned.index, 0)
                self.assertEqual(self.owned.track.lyrics, self.lyrics)
        self.start()
        self.fault()
        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.InvalidMedia)
        self.assertEqual(self.owned.status, "playback_failed")
        self.assertEqual(self.owned.index, 0)
        self.assertEqual(self.owned.track.lyrics, self.lyrics)

    def test_first_parse_marker_finishes_on_terminal_failure(self):
        self.owned.play_queue(self.songs)
        self.authorize()
        self.fault(QMediaPlayer.Error.FormatError)
        self.assertEqual(self.owned.track.lyrics, ("暂无歌词",))
        self.assertEqual(self.owned.status, "playback_failed")

    def test_manual_retry_reauthorizes_current_song_with_new_epoch_and_rejects_old_results(self):
        self.start(position=2000)
        before_epoch = self.owned._epoch
        old_stream = next(token for token, _action in self.tasks.jobs if token[0] == "stream")
        self.assertTrue(self.owned.refresh_lyrics())
        old_parse = self.tasks.jobs[-1]
        old_result = old_parse[1]()
        self.fault()
        self.check(101.1)
        self.assertEqual(self.owned.status, "playback_failed")
        self.assertTrue(self.owned.send_playback_command("toggle_play_pause"))
        self.assertEqual(self.owned._epoch, before_epoch + 1)
        self.assertEqual(self.owned.index, 0)
        self.assertEqual(self.owned.track.track_id, "owned:netease:0")
        self.assertEqual(self.owned.status, "loading_stream")
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.StoppedState)
        self.assertFalse(self.owned.track.is_playing)
        current = self.owned.track
        self.tasks.completed.emit(old_stream, StreamInfo("netease", "0", "https://fixture.test/stale", True), None)
        self.tasks.completed.emit(old_parse[0], old_result, None)
        self.assertEqual(self.owned.track, current)
        self.assertTrue(self.qt.url.isEmpty())
        self.authorize()
        self.assertEqual(self.owned.status, "playing")
        self.assertTrue(self.owned.track.is_playing)

    def test_stream_unavailable_retry_reuses_current_song_and_requires_valid_queue(self):
        self.owned.play_queue(self.songs)
        token = self.tasks.jobs[-1][0]
        self.tasks.completed.emit(token, None, "auth_required")
        self.assertEqual(self.owned.status, "stream_unavailable")
        epoch = self.owned._epoch
        self.assertTrue(self.owned.send_playback_command("toggle_play_pause"))
        self.assertEqual(self.owned._epoch, epoch + 1)
        self.assertEqual(self.owned.index, 0)
        self.authorize()
        self.fault(QMediaPlayer.Error.FormatError)
        for queue, index in (((), 0), (self.songs, -1), (self.songs, 3)):
            with self.subTest(index=index, queue=bool(queue)):
                self.owned.queue, self.owned.index = queue, index
                self.assertFalse(self.owned.send_playback_command("toggle_play_pause"))
                self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.owned.queue, self.owned.index = self.songs, 0
        self.owned.close()
        self.assertFalse(self.owned.send_playback_command("toggle_play_pause"))

    def test_normal_pause_resume_keeps_authorized_stream_and_lyric_payload(self):
        self.start()
        epoch, stream_url, count = self.owned._epoch, self.qt.url, len(self.tasks.jobs)
        self.assertTrue(self.owned.send_playback_command("toggle_play_pause"))
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.assertFalse(self.owned.track.is_playing)
        self.assertTrue(self.owned.send_playback_command("toggle_play_pause"))
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        self.assertTrue(self.owned.track.is_playing)
        self.assertEqual(self.owned._epoch, epoch)
        self.assertEqual(self.qt.url, stream_url)
        self.assertEqual(len(self.tasks.jobs), count)
        self.assertIs(self.owned._lyric_payload, self.payload)
        self.assertEqual(self.owned.track.lyrics, self.lyrics)


if __name__ == "__main__":
    unittest.main()
