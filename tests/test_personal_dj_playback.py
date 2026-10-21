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
                       media_kind="dj_voice", direct_stream=stream, introduced_song_id=f"song-{number}"),
            OnlineSong("netease", f"song-{number}", f"Song {number}", duration_seconds=120))


class DJPlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.tasks, self.artwork, self.prefetch = Tasks(), Tasks(), Tasks()
        self.qt = Player()
        self.dj_qt = Player()
        self.calls = []
        self.batch = segments(1) + segments(2)
        self.service = SimpleNamespace(personal_dj=self.dj, personal_fm=self.fm,
            stream_url=self.stream, lyrics=self.lyrics)
        self.owned = OwnedMusicPlayer(self.service, LocalLyricsStore(root=Path(self.folder.name)),
            player=self.qt, audio=object(), dj_player=self.dj_qt, dj_audio=object(), tasks=self.tasks, artwork_tasks=self.artwork,
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
        if kind == "stream" and self.owned._dj_overlay_voices and not self.owned._dj_source_ready:
            self.run_latest(tasks, "dj_stream")
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
        self.assertIn(("dj", "netease"), self.calls)
        self.assertIn(("stream", "song-1"), self.calls)

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
        self.dj_qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
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
        self.owned._end_current_stream()
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
        self.assertEqual(self.owned._epoch, old_epoch)
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
        self.owned._end_current_stream()
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

    def test_repeat_one_prepares_new_dj_recommendations_without_repeating_intro(self):
        self.start_fm()
        self.owned.set_playback_mode("repeat_one")
        before = len(self.prefetch.jobs)
        self.owned.set_personal_dj_enabled(True)
        self.assertGreater(len(self.prefetch.jobs), before)
        self.run_latest(self.prefetch, "fm_prefetch")
        prepared_count = len(self.prefetch.jobs)
        self.owned._end_current_stream()
        self.run_latest(self.tasks, "stream")
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-3")
        self.assertEqual(len(self.prefetch.jobs), prepared_count)
        self.owned.set_playback_mode("sequential")
        self.assertGreater(len(self.prefetch.jobs), before)
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

    def test_continuation_keeps_unintroduced_leading_songs_in_actual_service_order(self):
        self.start_fm()
        self.batch = (segments(9)[1], segments(8)[1]) + segments(4) + (segments(5)[1],)
        self.owned.set_personal_dj_enabled(True)
        self.run_latest(self.prefetch, "fm_prefetch")
        self.assertEqual([s.song_id for s in self.owned.queue],
            ["song-3", "song-9", "song-8", "dj:voice-4", "song-4", "song-5"])
        self.owned._end_current_stream()
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-9")
        self.run_latest(self.tasks, "stream")
        self.assertFalse(self.owned.is_personal_dj_speaking())
        self.owned._end_current_stream()
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-8")
        self.run_latest(self.tasks, "stream")
        self.assertFalse(self.owned.is_personal_dj_speaking())
        self.owned._end_current_stream()
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-4")
        self.run_latest(self.tasks, "stream")
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.dj_qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-4")
        self.owned._end_current_stream()
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-5")

    def test_busy_song_load_after_disabling_voice_still_confirms_feature_is_disabled(self):
        self.start()
        self.tasks.accepted = False
        self.assertTrue(self.owned.set_personal_dj_enabled(False))
        self.assertFalse(self.owned.personal_dj_enabled)
        self.assertFalse(self.owned.is_personal_dj_speaking())
        self.assertEqual(self.owned.status, "playing")
        self.assertEqual(self.qt.state, QMediaPlayer.PlaybackState.PlayingState)

    def test_genuine_voice_skips_song_and_lyric_endpoints_and_retains_queue_order(self):
        self.start()
        self.assertEqual([s.media_kind for s in self.owned.queue], ["dj_voice", "song", "dj_voice", "song"])
        self.assertIn(("dj", "netease"), self.calls)
        self.assertIn(("stream", "song-1"), self.calls)
        self.assertEqual(self.owned.track.title, "Song 1")
        self.assertTrue(self.owned.track.is_playing)
        self.assertNotIn("私人DJ", self.owned.track.lyrics)
        self.assertEqual(self.owned.get_personal_dj_presentation()["next_title"], "Song 1")
        self.assertTrue(self.dj_qt.url.toString().endswith("voice-1.mp3"))
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

    def test_manual_next_previous_replay_genuine_introduction_for_target_song(self):
        self.start()
        self.assertTrue(self.owned.send_playback_command("next"))
        self.assertEqual(self.owned.index, 3)
        self.run_latest(self.tasks, "stream")
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.dj_qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertTrue(self.owned.send_playback_command("previous"))
        self.assertEqual(self.owned.index, 1)
        self.assertEqual(self.owned.get_personal_dj_presentation()["next_title"], "Song 1")

    def test_voice_displays_introduced_music_with_advancing_clock_and_preserved_lyrics_on_voice_end(self):
        self.start()
        self.run_latest(self.tasks, "dj_lyrics")
        self.qt.positionChanged.emit(91000)
        previous = self.owned.track
        self.owned.send_playback_command("next")
        self.run_latest(self.tasks, "stream")
        self.run_latest(self.tasks, "dj_lyrics")
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertTrue(self.owned.track.is_playing)
        self.qt.positionChanged.emit(7000)
        self.dj_qt.positionChanged.emit(1300)
        self.assertNotEqual(self.owned.track.track_id, previous.track_id)
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-2")
        self.assertEqual(self.owned.track.position_seconds, 7)
        self.assertEqual(self.owned.track.lyrics, ("真实歌词",))
        before = (self.owned._epoch, self.qt.source(), self.owned.track.timed_lyrics, self.owned.track.album_art_bytes)
        self.dj_qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual((self.owned._epoch, self.qt.source(), self.owned.track.timed_lyrics, self.owned.track.album_art_bytes), before)
        self.assertEqual(self.owned.track.position_seconds, 7)
        self.assertTrue(self.owned.track.is_playing)

    def test_manual_intro_switches_real_cover_and_target_lyrics_with_independent_voice_clock(self):
        self.start()
        self.run_latest(self.tasks, "dj_lyrics")
        target = replace(self.owned.queue[3], artwork_url="https://example.test/real-next-cover.png")
        self.owned.queue = self.owned.queue[:3] + (target,) + self.owned.queue[4:]
        key = self.owned._art_key(target)
        from api.models import ColorToken
        self.owned._art_cache[key] = (b"actual-next-cover", ColorToken(130, 70, 25), ColorToken(55, 24, 18))
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.track.album_art_bytes, b"actual-next-cover")
        self.assertEqual(self.owned.track.album_color, ColorToken(130, 70, 25))
        self.run_latest(self.tasks, "stream")
        self.run_latest(self.tasks, "dj_lyrics")
        self.qt.positionChanged.emit(5500)
        self.dj_qt.positionChanged.emit(1000)
        self.assertEqual(self.owned.track.title, target.title)
        self.assertEqual(self.owned.track.lyrics, ("真实歌词",))
        self.assertEqual(self.owned.track.duration_seconds, target.duration_seconds)
        self.assertEqual(self.owned.track.position_seconds, 5.5)
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertTrue(self.owned.seek_playback_position(1, self.owned.track.track_id))
        self.assertFalse(self.owned.is_personal_dj_speaking())

    def test_multipart_intro_keeps_music_clock_source_and_target_lyrics(self):
        voice, song = segments(2)
        second_stream = replace(voice.direct_stream, song_id="dj:part-two")
        second = replace(voice, song_id=second_stream.song_id, direct_stream=second_stream)
        self.batch = (voice, second, song)
        self.start()
        self.run_latest(self.tasks, "dj_lyrics")
        self.qt.positionChanged.emit(4500)
        before = (self.owned._epoch, self.qt.source(), self.owned.track.lyrics, self.owned.track.timed_lyrics)
        self.dj_qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.run_latest(self.tasks, "dj_stream")
        self.assertEqual((self.owned._epoch, self.qt.source(), self.owned.track.lyrics, self.owned.track.timed_lyrics), before)
        self.assertEqual(self.owned.track.position_seconds, 4.5)
        self.assertTrue(self.owned.track.is_playing)
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.dj_qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.owned.track.position_seconds, 4.5)
        self.assertTrue(self.owned.track.is_playing)

    def test_cancel_intro_before_target_lyric_reply_keeps_new_selected_song_and_stop_clears_state(self):
        self.start()
        old_token, old_action = next(job for job in reversed(self.tasks.jobs) if job[0][0] == "dj_lyrics")
        old_result = old_action()
        self.owned.play_queue((segments(9)[1],))
        self.run_latest(self.tasks, "stream")
        self.tasks.completed.emit(old_token, old_result, None)
        self.assertEqual(self.owned.track.title, "Song 9")
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-9")
        self.assertEqual(self.owned.track.lyrics, ("加载歌词中",))
        self.owned.stop()
        self.assertIsNone(self.owned.track)
        self.assertEqual(self.owned.get_personal_dj_presentation()["state"], "idle")

    def test_pause_during_intro_preparation_stays_ready_until_user_resumes(self):
        self.start()
        self.owned._end_current_stream()
        # Pause the selected second song's pending preparation. Another Next
        # here would skip it and wait for a new catalog, not prepare song 2.
        self.owned.send_playback_command("toggle_play_pause")
        self.run_latest(self.tasks, "stream")
        self.assertEqual(self.qt.state, QMediaPlayer.PlaybackState.PausedState)
        self.assertEqual(self.owned.get_personal_dj_presentation()["state"], "ready")
        self.assertFalse(self.owned.is_personal_dj_speaking())
        self.owned.send_playback_command("toggle_play_pause")
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertTrue(self.owned.track.is_playing)

    def test_intro_request_failure_keeps_associated_music_and_failure_expires(self):
        self.start()
        self.owned.send_playback_command("next")
        failed = next(token for token, _ in reversed(self.tasks.jobs) if token[0] == "dj_stream")
        self.tasks.completed.emit(failed, None, "network")
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-2")
        self.assertEqual(self.owned.get_personal_dj_presentation()["state"], "failed")
        self.run_latest(self.tasks, "stream")
        self.assertTrue(self.owned.track.is_playing)
        self.assertEqual(self.owned.track.title, "Song 2")
        self.assertEqual(self.owned.get_personal_dj_presentation()["state"], "failed")
        with patch("local.owned_music_player.time.monotonic", return_value=self.owned._dj_failed_until + 1):
            self.assertEqual(self.owned.get_personal_dj_presentation()["state"], "idle")

    def test_stale_intro_lyrics_stream_and_failure_cannot_resurrect_stopped_or_selected_song(self):
        self.start()
        self.owned._end_current_stream()
        self.run_latest(self.tasks, "stream")
        self.owned.send_playback_command("next")
        voice = next((token, action) for token, action in reversed(self.tasks.jobs) if token[0] == "stream")
        lyrics = next((token, action) for token, action in reversed(self.tasks.jobs) if token[0] == "dj_lyrics")
        results = voice[1](), lyrics[1]()
        self.owned.stop()
        self.tasks.completed.emit(voice[0], results[0], None)
        self.tasks.completed.emit(lyrics[0], results[1], None)
        self.tasks.completed.emit(voice[0], None, "network")
        self.assertIsNone(self.owned.track)
        self.assertEqual(self.owned.get_personal_dj_presentation()["state"], "idle")
        self.assertEqual(self.qt.state, QMediaPlayer.PlaybackState.StoppedState)

    def test_previous_without_real_intro_plays_song_directly_and_new_segment_token_changes(self):
        self.start()
        token = self.owned.get_personal_dj_presentation()["token"]
        self.owned._end_current_stream()
        self.run_latest(self.tasks, "stream")
        self.owned.send_playback_command("next")
        self.assertGreater(self.owned.get_personal_dj_presentation()["token"], token)
        self.run_latest(self.tasks, "stream")
        self.owned._end_current_stream()
        self.run_latest(self.tasks, "stream")
        self.owned.queue = (self.owned.queue[1],) + self.owned.queue[2:]
        self.owned.index = len(self.owned.queue) - 1
        self.owned.send_playback_command("previous")
        self.assertEqual(self.owned.queue[self.owned.index].media_kind, "song")
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-1")

    def test_invalid_voice_association_never_announces_unrelated_following_song(self):
        self.batch = (replace(segments(1)[0], introduced_song_id="wrong"), segments(1)[1])
        self.assertTrue(self.owned.play_personal_dj())
        self.run_latest(self.tasks, "catalog")
        self.assertFalse(self.owned.is_personal_dj_speaking())
        self.assertEqual(self.owned.status, "stream_unavailable")

    def test_same_voice_replay_creates_new_presentation_token_and_disabled_state_clears_failure(self):
        self.batch = segments(1)
        self.start()
        previous = self.owned.get_personal_dj_presentation()["token"]
        self.owned.index = self.owned._intro_start_for_song(self.owned.index)
        self.owned._load_song()
        self.assertGreater(self.owned.get_personal_dj_presentation()["token"], previous)
        self.run_latest(self.tasks, "stream")
        self.dj_qt.errorOccurred.emit(QMediaPlayer.Error.ResourceError, "fixture")
        self.assertEqual(self.owned.get_personal_dj_presentation()["state"], "failed")
        self.owned.set_personal_dj_enabled(False)
        self.assertEqual(self.owned.get_personal_dj_presentation()["state"], "idle")
        self.assertEqual(self.owned.get_personal_dj_presentation()["next_title"], "")
        self.owned.play_queue((segments(9)[1],))
        self.owned.set_personal_dj_enabled(True)
        self.assertEqual(self.owned.get_personal_dj_presentation()["state"], "idle")

    def test_translation_changed_during_intro_reparses_prefetched_payload_without_refetch(self):
        self.start()
        self.owned._end_current_stream()
        self.run_latest(self.tasks, "stream")
        preferences = {"marker": "old"}
        self.owned._lyrics_preferences_snapshot = lambda: dict(preferences)
        def parse(payload, values):
            return SimpleNamespace(lyrics=(values["marker"],), timed_lyrics=(), source="fixture")
        self.owned._parse_lyrics_payload = parse
        # The worker originally captured an earlier preference generation.
        token, _ = next(job for job in reversed(self.tasks.jobs) if job[0][0] == "dj_lyrics")
        payload = self.lyrics("netease", "song-2")
        self.tasks.completed.emit(token, (payload, parse(payload, preferences)), None)
        preferences["marker"] = "new"
        self.owned.refresh_lyrics()
        before = len([call for call in self.calls if call == ("lyrics", "song-2")])
        # The independent speech decoder finishes; music retains this song
        # and its pending preference parse throughout the overlay handoff.
        self.dj_qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.run_latest(self.tasks, "lyrics_refresh")
        self.assertEqual(self.owned.track.lyrics, ("new",))
        self.assertEqual(len([call for call in self.calls if call == ("lyrics", "song-2")]), before)

    def test_translation_changed_before_voice_stream_reparses_target_lyrics_while_audio_is_pending(self):
        settings = {"v2_enable_dual_line_lyrics": False}
        self.owned.lyrics_store.settings_getter = lambda key, default=None: settings.get(key, default)
        self.service.lyrics = lambda source, identity: LyricPayload(source, identity,
            raw_lrc="[00:01]Shine on", translation="[00:01]继续闪耀")
        self.owned.play_personal_dj()
        self.run_latest(self.tasks, "catalog")
        token, action = next(job for job in self.tasks.jobs if job[0][0] == "dj_lyrics")
        old_result = action()
        self.assertEqual(old_result[1].lyrics, ("Shine on",))
        settings["v2_enable_dual_line_lyrics"] = True
        self.owned.refresh_lyrics()
        self.tasks.completed.emit(token, old_result, None)
        self.assertEqual(self.owned._stream_epoch, 0)
        self.run_latest(self.tasks, "lyrics_refresh")
        self.assertIn("继续闪耀", self.owned.track.lyrics[0])
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-1")
        self.assertEqual(self.owned.track.position_seconds, 0)
        self.assertFalse(self.owned.track.is_playing)
        self.run_latest(self.tasks, "stream")
        self.assertIn("继续闪耀", self.owned.track.lyrics[0])
        self.assertTrue(self.owned.is_personal_dj_speaking())

    def test_multipart_intro_restarts_retained_payload_reparse_and_rejects_old_epoch_after_request_failure(self):
        voice, song = segments(2)
        second_stream = replace(voice.direct_stream, song_id="dj:part-two")
        self.batch = (voice, replace(voice, song_id=second_stream.song_id, direct_stream=second_stream), song)
        settings = {"v2_enable_dual_line_lyrics": False}
        self.owned.lyrics_store.settings_getter = lambda key, default=None: settings.get(key, default)
        self.service.lyrics = lambda source, identity: LyricPayload(source, identity,
            raw_lrc="[00:01]Shine on", translation="[00:01]继续闪耀")
        self.start()
        self.run_latest(self.tasks, "dj_lyrics")
        settings["v2_enable_dual_line_lyrics"] = True
        self.owned.refresh_lyrics()
        old_token, old_action = next(job for job in reversed(self.tasks.jobs) if job[0][0] == "lyrics_refresh")
        old_result = old_action()
        self.dj_qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.tasks.completed.emit(old_token, old_result, None)
        self.assertIn("继续闪耀", self.owned.track.lyrics[0])
        failed = next(token for token, _ in reversed(self.tasks.jobs) if token[0] == "dj_lyrics")
        self.tasks.completed.emit(failed, None, "request_failed")
        self.run_latest(self.tasks, "lyrics_refresh")
        self.assertIn("继续闪耀", self.owned.track.lyrics[0])
        self.assertEqual(self.owned._stream_epoch, self.owned._epoch)
        self.assertTrue(self.owned.track.is_playing)
        self.run_latest(self.tasks, "stream")
        self.assertIn("继续闪耀", self.owned.track.lyrics[0])

    def test_introduced_song_without_lyrics_uses_song_placeholder_and_keeps_speech_playing(self):
        self.service.lyrics = lambda source, identity: LyricPayload(source, identity)
        self.start()
        self.run_latest(self.tasks, "dj_lyrics")
        self.assertEqual(self.owned.track.lyrics, ("暂无歌词",))
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-1")
        self.assertTrue(self.owned.track.is_playing)
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.dj_qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.owned.track.lyrics, ("暂无歌词",))
        self.assertTrue(self.owned.track.is_playing)

    def test_failed_intro_lyrics_terminate_loading_and_music_retries_actual_song_lyrics(self):
        for error in ("request_failed", None):
            with self.subTest(error=error):
                self.owned.stop()
                self.start()
                failed = next(token for token, _ in reversed(self.tasks.jobs) if token[0] == "dj_lyrics")
                self.tasks.completed.emit(failed, None, error)
                self.assertEqual(self.owned.track.lyrics, ("暂无歌词",))
                self.assertEqual(self.owned.track.track_id, "owned:netease:song-1")
                self.assertTrue(self.owned.track.is_playing)
                self.assertTrue(self.owned.is_personal_dj_speaking())
                self.assertIsNone(self.owned._lyric_payload)
                self.owned._end_current_stream()
                self.run_latest(self.tasks, "stream")
                self.run_latest(self.tasks, "lyrics")
                self.assertEqual(self.owned.track.lyrics, ("真实歌词",))
                self.assertTrue(self.owned.track.is_playing)

    def test_multipart_failed_fetch_keeps_loading_until_retained_target_payload_parse_completes(self):
        voice, song = segments(2)
        second_stream = replace(voice.direct_stream, song_id="dj:part-two")
        self.batch = (voice, replace(voice, song_id=second_stream.song_id, direct_stream=second_stream), song)
        settings = {"v2_enable_dual_line_lyrics": False}
        self.owned.lyrics_store.settings_getter = lambda key, default=None: settings.get(key, default)
        self.service.lyrics = lambda source, identity: LyricPayload(source, identity,
            raw_lrc="[00:01]Shine on", translation="[00:01]继续闪耀")
        self.start()
        token, action = next(job for job in self.tasks.jobs if job[0][0] == "dj_lyrics")
        old_result = action()
        settings["v2_enable_dual_line_lyrics"] = True
        self.owned.refresh_lyrics()
        self.tasks.completed.emit(token, old_result, None)
        self.dj_qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        pending = self.owned._lyrics_parse_pending
        self.assertIsNotNone(pending)
        failed = next(token for token, _ in reversed(self.tasks.jobs) if token[0] == "dj_lyrics")
        self.tasks.completed.emit(failed, None, "request_failed")
        self.assertEqual(self.owned.track.lyrics, ("加载歌词中",))
        self.assertEqual(self.owned._lyrics_parse_pending, pending)
        self.run_latest(self.tasks, "lyrics_refresh")
        self.assertIn("继续闪耀", self.owned.track.lyrics[0])
        self.assertTrue(self.owned.track.is_playing)

    def test_failed_target_preference_parse_uses_song_placeholder_before_speech_and_later_recovers(self):
        self.owned.play_personal_dj()
        self.run_latest(self.tasks, "catalog")
        token, action = next(job for job in self.tasks.jobs if job[0][0] == "dj_lyrics")
        old_result = action()
        self.owned.refresh_lyrics()
        self.tasks.completed.emit(token, old_result, None)
        parse = self.owned._lyrics_parse_pending
        self.tasks.completed.emit(parse, None, "request_failed")
        self.assertEqual(self.owned.track.lyrics, ("暂无歌词",))
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-1")
        self.run_latest(self.tasks, "stream")
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertTrue(self.owned.track.is_playing)
        self.dj_qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.run_latest(self.tasks, "lyrics_refresh")
        self.assertEqual(self.owned.track.lyrics, ("真实歌词",))
        self.assertTrue(self.owned.track.is_playing)

    def test_rejected_intro_target_fetch_uses_song_placeholder_without_blocking_speech(self):
        submit = self.tasks.submit
        def reject_target(token, action):
            accepted = submit(token, action)
            return accepted and token[0] != "dj_lyrics"
        with patch.object(self.tasks, "submit", side_effect=reject_target):
            self.start()
        self.assertEqual(self.owned.track.lyrics, ("暂无歌词",))
        self.assertTrue(self.owned.track.is_playing)
        self.assertTrue(self.owned.is_personal_dj_speaking())

    def test_pause_during_foreground_fm_continuation_does_not_replay_old_ended_source(self):
        self.start()
        self.owned.index = len(self.owned.queue) - 1
        self.owned._load_song()
        self.run_latest(self.tasks, "stream")
        self.owned._fm_prefetch_pending = False
        self.qt.stop()
        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.owned.status, "loading_catalog")
        with patch.object(self.qt, "play", wraps=self.qt.play) as play:
            self.owned.send_playback_command("toggle_play_pause")
        play.assert_not_called()
        self.batch = segments(7)
        self.run_latest(self.tasks, "catalog")
        self.run_latest(self.tasks, "stream")
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-7")
        self.assertEqual(self.qt.state, QMediaPlayer.PlaybackState.PausedState)
        self.assertEqual(self.owned.get_personal_dj_presentation()["state"], "ready")

    def test_reentrant_failure_stop_replace_or_next_cancels_old_fallback(self):
        for action in ("stop", "replace", "next"):
            with self.subTest(action=action):
                self.start()
                observed = []
                def cancel():
                    if self.owned._dj_state != "failed" or observed:
                        return
                    observed.append(True)
                    if action == "stop":
                        self.owned.stop()
                    elif action == "replace":
                        self.owned.play_queue((segments(9)[1],))
                    else:
                        self.owned.send_playback_command("next")
                    observed.append((self.owned._epoch, self.owned.track, self.owned.queue,
                        self.owned.index, self.owned.status))
                self.owned.changed.connect(cancel)
                try:
                    self.owned._fail_dj_overlay("network")
                    self.assertEqual((self.owned._epoch, self.owned.track, self.owned.queue,
                        self.owned.index, self.owned.status), observed[1])
                finally:
                    self.owned.changed.disconnect(cancel)

    def test_dj_continuation_keeps_voice_song_pairs_including_service_repeated_song(self):
        self.start()
        self.batch = (replace(segments(3)[0], introduced_song_id="song-1"), self.batch[1])
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
        self.batch = (replace(segments(3)[0], introduced_song_id="song-2"), segments(2)[1])
        before_batch = len(self.prefetch.jobs)
        self.run_latest(self.prefetch, "fm_prefetch")
        old_count = len(self.prefetch.jobs)
        self.assertEqual(old_count, before_batch + 1)
        self.owned.index = 3
        self.owned._load_song()
        self.run_latest(self.tasks, "stream")
        self.assertEqual(len(self.prefetch.jobs), old_count)
        self.assertTrue(self.owned._fm_prefetch_pending)

    def test_manual_next_at_batch_end_preserves_new_intro_with_and_without_pending_prefetch(self):
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
        self.assertEqual([entry.title for entry in self.owned._album_queue()], ["Song 2", "Song 3", "Song 4"])
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
