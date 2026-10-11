"""DJ voice/music order uses the owned queue and actual existing controls."""
from dataclasses import replace
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.models import AudioFrame
from api.online_music import LyricPayload, OnlineMusicError, OnlineSong, StreamInfo
from api.providers import PlaybackSourceProvider
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

    def start_fm(self):
        self.assertTrue(self.owned.play_personal_fm())
        self.run_latest(self.tasks, "catalog")
        self.run_latest(self.tasks, "stream")

    def test_fm_defaults_to_traditional_music_without_dj_requests(self):
        self.start_fm()
        self.assertFalse(self.owned.personal_dj_enabled)
        self.assertEqual(self.owned._fm_kind, "fm")
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-3")
        self.assertFalse(self.owned.is_personal_dj_speaking())
        self.assertFalse(any(call[0] == "dj" for call in self.calls))

    def test_enabled_preference_enters_same_fm_context_with_real_introductions(self):
        self.assertTrue(self.owned.set_personal_dj_enabled(True))
        self.start_fm()
        self.assertTrue(self.owned.is_personal_fm())
        self.assertTrue(self.owned.personal_dj_enabled)
        self.assertEqual(self.owned._fm_kind, "dj")
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertEqual(self.calls, [("dj", "netease")])

    def test_retained_playing_voice_during_catalog_wait_drives_provider_frame_until_pause_eof_or_fault(self):
        self.start()
        system = SimpleNamespace(suspend_live_audio=lambda: None, stop_audio_cast=lambda: None)
        provider = PlaybackSourceProvider(system, self.owned, lambda key, default=None: True)
        self.addCleanup(provider._owned_audio_bridge.close)
        self.service.daily_recommendations = lambda source: (segments(7)[1],)
        self.owned.play_daily()
        frame = AudioFrame(.2, .1, .05)
        with patch.object(provider._owned_audio_bridge, "get_personal_dj_audio_frame", return_value=frame) as getter:
            self.assertTrue(self.owned.is_personal_dj_speaking())
            self.assertEqual(provider.get_owned_personal_dj_audio_frame(), frame)
            self.owned.send_playback_command("toggle_play_pause")
            self.assertFalse(self.owned.is_personal_dj_speaking())
            self.assertIsNone(provider.get_owned_personal_dj_audio_frame())
            self.assertEqual(getter.call_count, 1)
            self.owned.send_playback_command("toggle_play_pause")
            self.assertTrue(self.owned.is_personal_dj_speaking())
            self.assertEqual(self.owned.status, "loading_catalog")
            self.assertEqual(provider.get_owned_personal_dj_audio_frame(), frame)
            self.qt.errorOccurred.emit(QMediaPlayer.Error.ResourceError, "fixture")
            self.assertFalse(self.owned.is_personal_dj_speaking())
            self.assertIsNone(provider.get_owned_personal_dj_audio_frame())
            self.qt.stop()
            self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
            self.assertFalse(self.owned.is_personal_dj_speaking())
            self.assertIsNone(provider.get_owned_personal_dj_audio_frame())

    def test_enable_during_music_retains_source_clock_lyrics_and_then_introduces_next_song(self):
        self.start_fm()
        self.run_latest(self.tasks, "lyrics")
        self.qt.positionChanged.emit(27000)
        before = (self.owned._epoch, self.owned._stream_epoch, self.owned._catalog_epoch,
            self.qt.url, self.qt.state, self.owned.track.track_id,
            self.owned.track.position_seconds, self.owned.track.timed_lyrics)
        self.assertTrue(self.owned.set_personal_dj_enabled(True))
        after = (self.owned._epoch, self.owned._stream_epoch, self.owned._catalog_epoch,
            self.qt.url, self.qt.state, self.owned.track.track_id,
            self.owned.track.position_seconds, self.owned.track.timed_lyrics)
        self.assertEqual(after, before)
        self.run_latest(self.prefetch, "fm_prefetch")
        self.assertEqual([s.media_kind for s in self.owned.queue], ["song", "dj_voice", "song", "dj_voice", "song"])
        self.owned._end_current_stream()
        self.run_latest(self.tasks, "stream")
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.owned._end_current_stream()
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-1")

    def test_enable_while_current_audio_authorization_is_pending_cannot_cancel_it(self):
        self.owned.play_personal_fm()
        self.run_latest(self.tasks, "catalog")
        token, action = next(job for job in self.tasks.jobs if job[0][0] == "stream")
        self.assertTrue(self.owned.set_personal_dj_enabled(True))
        self.tasks.completed.emit(token, action(), None)
        self.assertTrue(self.owned.track.is_playing)
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-3")
        self.assertTrue(self.qt.url.toString().endswith("song.mp3"))

    def test_old_fm_prefetch_after_enable_cannot_append_or_clear_new_request(self):
        self.start_fm()
        old_token, old_action = next(job for job in self.prefetch.jobs if job[0][0] == "fm_prefetch")
        old_result = old_action()
        self.owned.set_personal_dj_enabled(True)
        self.assertEqual(old_action(), ())
        self.prefetch.completed.emit(old_token, old_result, None)
        self.assertEqual([s.song_id for s in self.owned.queue], ["song-3"])
        self.assertTrue(self.owned._fm_prefetch_pending)
        self.run_latest(self.prefetch, "fm_prefetch")
        self.assertEqual(self.owned.queue[1].song_id, "dj:voice-1")

    def test_disable_during_song_retains_current_stream_and_removes_future_speech(self):
        self.start()
        self.owned.send_playback_command("next")
        self.run_latest(self.tasks, "stream")
        before = self.owned._epoch, self.owned.track.track_id, self.qt.url, self.qt.state
        self.assertTrue(self.owned.set_personal_dj_enabled(False))
        self.assertEqual((self.owned._epoch, self.owned.track.track_id, self.qt.url, self.qt.state), before)
        self.assertTrue(all(s.media_kind == "song" for s in self.owned.queue[self.owned.index + 1:]))
        self.owned._end_current_stream()
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-2")

    def test_disable_active_voice_stops_it_and_loads_the_song_it_introduced(self):
        self.start()
        old_epoch = self.owned._epoch
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertTrue(self.owned.set_personal_dj_enabled(False))
        self.assertFalse(self.owned.is_personal_dj_speaking())
        self.assertGreater(self.owned._epoch, old_epoch)
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-1")
        self.run_latest(self.tasks, "stream")
        self.assertTrue(self.owned.track.is_playing)
        self.assertTrue(self.qt.url.toString().endswith("song.mp3"))

    def test_disable_paused_voice_keeps_introduced_song_paused(self):
        self.start()
        self.owned.send_playback_command("toggle_play_pause")
        self.assertFalse(self.owned.is_personal_dj_speaking())
        self.owned.set_personal_dj_enabled(False)
        self.run_latest(self.tasks, "stream")
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-1")
        self.assertFalse(self.owned.track.is_playing)
        self.assertEqual(self.qt.state, QMediaPlayer.PlaybackState.PausedState)
        self.owned.send_playback_command("toggle_play_pause")
        self.assertTrue(self.owned.track.is_playing)

    def test_late_dj_reply_after_disable_cannot_reintroduce_speech(self):
        self.start()
        self.owned.send_playback_command("next")
        self.run_latest(self.tasks, "stream")
        old_token, old_action = next(job for job in self.prefetch.jobs if job[0][0] == "fm_prefetch")
        old_result = old_action()
        self.owned.set_personal_dj_enabled(False)
        self.prefetch.completed.emit(old_token, old_result, None)
        self.assertTrue(all(s.media_kind != "dj_voice" for s in self.owned.queue[self.owned.index + 1:]))
        self.assertTrue(self.owned._fm_prefetch_pending)

    def test_disable_then_enable_ignores_first_generation_even_when_strategy_matches_again(self):
        self.start_fm()
        self.owned.set_personal_dj_enabled(True)
        old_token, old_action = next(job for job in reversed(self.prefetch.jobs) if job[0][0] == "fm_prefetch")
        old_result = old_action()
        self.owned.set_personal_dj_enabled(False)
        self.owned.set_personal_dj_enabled(True)
        self.prefetch.completed.emit(old_token, old_result, None)
        self.assertEqual([s.song_id for s in self.owned.queue], ["song-3"])
        self.assertTrue(self.owned._fm_prefetch_pending)

    def test_repeat_one_defers_new_dj_recommendations_and_never_repeats_intro(self):
        self.start_fm()
        self.owned.set_playback_mode("repeat_one")
        before = len(self.prefetch.jobs)
        self.owned.set_personal_dj_enabled(True)
        self.assertEqual(len(self.prefetch.jobs), before)
        self.owned._end_current_stream()
        self.run_latest(self.tasks, "stream")
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-3")
        self.assertEqual(len(self.prefetch.jobs), before)
        self.owned.set_playback_mode("sequential")
        self.assertGreater(len(self.prefetch.jobs), before)
        self.run_latest(self.prefetch, "fm_prefetch")
        self.assertEqual(self.owned.queue[self.owned.index + 1].media_kind, "dj_voice")

    def test_late_foreground_dj_catalog_after_disable_cannot_replace_current_fm(self):
        self.start()
        self.owned.index = len(self.owned.queue) - 1
        self.owned._load_song()
        self.run_latest(self.tasks, "stream")
        self.owned._fm_prefetch_pending = False
        self.owned._advance_fm()
        old_token, old_action = next(job for job in reversed(self.tasks.jobs) if job[0][0] == "catalog")
        result = old_action()
        self.owned.set_personal_dj_enabled(False)
        self.tasks.completed.emit(old_token, result, None)
        self.assertTrue(self.owned._fm_advance_pending)
        self.assertTrue(self.owned._fm_prefetch_pending)
        self.assertTrue(all(s.media_kind != "dj_voice" for s in self.owned.queue[self.owned.index + 1:]))
        self.run_latest(self.prefetch, "fm_prefetch")
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-3")

    def test_generation_cancel_and_failed_new_prefetch_keep_original_fm_clock_and_eof(self):
        self.start_fm()
        self.owned.play_personal_fm()
        old_token, old_action = next(job for job in reversed(self.tasks.jobs) if job[0][0] == "catalog")
        result = old_action()
        self.owned.set_personal_dj_enabled(True)
        self.assertEqual(self.owned._catalog_pending_epoch, 0)
        self.assertEqual(self.owned._stream_epoch, self.owned._epoch)
        current_prefetch = self.prefetch.jobs[-1][0]
        self.prefetch.completed.emit(current_prefetch, None, "network")
        self.tasks.completed.emit(old_token, result, None)
        self.qt.positionChanged.emit(39000)
        self.assertEqual(self.owned.track.position_seconds, 39)
        self.assertTrue(self.owned.track.is_playing)
        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.owned.status, "loading_catalog")
        self.assertEqual(self.tasks.jobs[-1][0][2], "fm_continue")

    def test_generation_cancel_from_normal_queue_finishes_pending_initial_fm_and_preserves_song(self):
        self.owned.play_queue((segments(5)[1], segments(6)[1]))
        self.run_latest(self.tasks, "stream")
        self.owned.play_personal_fm()
        old_token, old_action = next(job for job in reversed(self.tasks.jobs) if job[0][0] == "catalog")
        result = old_action()
        self.owned.set_personal_dj_enabled(True)
        self.assertEqual(self.owned._catalog_pending_epoch, 0)
        self.assertEqual(self.owned._stream_epoch, self.owned._epoch)
        self.tasks.completed.emit(old_token, result, None)
        self.qt.positionChanged.emit(47000)
        self.assertEqual(self.owned.track.position_seconds, 47)
        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-6")

    def test_toggle_during_replacement_catalog_never_advances_healthy_current_song(self):
        for replacement in ("fm", "daily"):
            with self.subTest(replacement=replacement):
                self.owned.stop()
                self.owned.set_personal_dj_enabled(False)
                self.start_fm()
                self.service.daily_recommendations = lambda source: (segments(7)[1],)
                if replacement == "fm":
                    self.owned.play_personal_fm()
                else:
                    self.owned.play_daily()
                pending = next(token for token, _ in reversed(self.tasks.jobs) if token[0] == "catalog")
                before = (self.owned._epoch, self.qt.source(), self.owned.track.track_id)
                self.owned.set_personal_dj_enabled(True)
                self.assertFalse(self.owned._fm_advance_pending)
                self.run_latest(self.prefetch, "fm_prefetch")
                self.assertEqual((self.owned._epoch, self.qt.source(), self.owned.track.track_id), before)
                self.assertTrue(self.owned.track.is_playing)
                if replacement == "daily":
                    self.tasks.completed.emit(pending, (segments(7)[1],), None)
                    self.assertEqual(self.owned.track.track_id, "owned:netease:song-7")

    def test_generation_cancel_replays_buffered_qt_error_into_existing_fault_handling(self):
        self.start_fm()
        self.qt.durationChanged.emit(120000)
        self.owned.play_personal_fm()
        self.qt.errorOccurred.emit(QMediaPlayer.Error.ResourceError, "fixture secret")
        self.owned.set_personal_dj_enabled(True)
        self.assertEqual(self.owned._catalog_pending_epoch, 0)
        self.assertIsNotNone(self.owned._stream_fault)
        self.assertEqual(self.owned.failure_reason, "resource")
        self.assertTrue(self.owned.track.is_playing)

    def test_toggle_after_old_eof_cannot_let_fm_prefetch_override_pending_daily_catalog(self):
        self.start_fm()
        self.service.daily_recommendations = lambda source: (segments(7)[1],)
        self.owned.play_daily()
        pending = next(token for token, _ in reversed(self.tasks.jobs) if token[0] == "catalog")
        before = (self.owned._epoch, self.qt.source(), self.owned.track.track_id)
        self.qt.stop()
        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.owned.set_personal_dj_enabled(True)
        self.assertFalse(self.owned._fm_advance_pending)
        self.run_latest(self.prefetch, "fm_prefetch")
        self.assertEqual((self.owned._epoch, self.qt.source(), self.owned.track.track_id), before)
        self.assertEqual(self.owned._catalog_pending_epoch, pending[1])
        self.tasks.completed.emit(pending, (segments(7)[1],), None)
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-7")
        self.assertFalse(self.owned.is_personal_fm())

    def test_toggle_rejects_non_boolean_and_closed_player_without_state_change(self):
        for value in (1, 0, "true", None, [], {}):
            self.assertFalse(self.owned.set_personal_dj_enabled(value))
        self.assertFalse(self.owned.personal_dj_enabled)
        self.owned.close()
        self.assertFalse(self.owned.set_personal_dj_enabled(True))
        self.assertFalse(self.owned.personal_dj_enabled)

    def test_continuation_omits_unintroduced_leading_song_and_uses_genuine_next_intro(self):
        self.start_fm()
        self.batch = (segments(9)[1],) + segments(4)
        self.owned.set_personal_dj_enabled(True)
        self.run_latest(self.prefetch, "fm_prefetch")
        self.assertEqual([s.song_id for s in self.owned.queue], ["song-3", "dj:voice-4", "song-4"])
        self.owned._end_current_stream()
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "dj:voice-4")

    def test_busy_song_load_after_disabling_voice_still_confirms_feature_is_disabled(self):
        self.start()
        self.tasks.accepted = False
        self.assertTrue(self.owned.set_personal_dj_enabled(False))
        self.assertFalse(self.owned.personal_dj_enabled)
        self.assertFalse(self.owned.is_personal_dj_speaking())
        self.assertEqual(self.owned.status, "busy")
        self.assertEqual(self.qt.state, QMediaPlayer.PlaybackState.StoppedState)

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
