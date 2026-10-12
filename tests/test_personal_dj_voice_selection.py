"""Verified voice selection fences future batches while current music continues."""
from dataclasses import replace
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
from api.online_music import LyricPayload, OnlineSong, PersonalDjVoice, StreamInfo
from api.owned_stream_cache import PreparedAudio
from api.providers import PlaybackSourceProvider
from local.owned_music_player import OwnedMusicPlayer
from tests.test_owned_playback import Player, Tasks
from tests.test_personal_dj_playback import segments


VOICES = (PersonalDjVoice("F793-C2", "豆花", "温柔陪伴", True),
          PersonalDjVoice("M791-C2", "黄俊"), PersonalDjVoice("M792-NP", "李源"),
          PersonalDjVoice("M795-NP", "庞舸"))


class PersonalDjVoiceSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.tasks, self.prefetch, self.artwork = Tasks(), Tasks(), Tasks()
        self.qt = Player()
        self.dj_qt = Player()
        self.catalog = VOICES
        self.dj_calls = []
        self.service = SimpleNamespace(
            personal_dj_voices_cached=lambda source: self.catalog,
            personal_dj_voices=Mock(side_effect=lambda source: self.catalog),
            personal_dj=self.dj,
            personal_fm=lambda source: (segments(3)[1],),
            stream_url=lambda source, identity: StreamInfo(source, identity, "https://example.test/song.mp3", True),
            lyrics=lambda source, identity: LyricPayload(source, identity, raw_lrc="[00:01]保留歌词"))
        self.owned = OwnedMusicPlayer(self.service, LocalLyricsStore(root=Path(self.folder.name)),
            player=self.qt, audio=object(), dj_player=self.dj_qt, dj_audio=object(), tasks=self.tasks, prefetch_tasks=self.prefetch,
            artwork_tasks=self.artwork, stream_cache=None)
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(self.owned.close)

    def dj(self, source, timbre_key=""):
        self.dj_calls.append((source, timbre_key))
        return segments(7) + segments(8)

    def finish(self, tasks, kind):
        token, action = next(job for job in reversed(tasks.jobs) if job[0][0] == kind)
        tasks.completed.emit(token, action(), None)
        if kind == "stream" and self.owned._dj_overlay_voices and not self.owned._dj_source_ready:
            self.finish(tasks, "dj_stream")
        return token

    def start_music(self, *, dj=True, finish_stream=True):
        self.owned.personal_dj_enabled = dj
        self.owned._replace_queue((segments(1)[1],) + segments(2), fm_source="netease",
                                  fm_kind="dj" if dj else "fm")
        if finish_stream:
            self.finish(self.tasks, "stream")
            self.finish(self.tasks, "lyrics")
            self.qt.positionChanged.emit(37000)

    def current_state(self):
        track = self.owned.track
        return (self.owned._epoch, self.owned._stream_epoch, self.owned._lyrics_generation,
                self.qt.url, self.qt.state, track.track_id, track.position_seconds,
                track.timed_lyrics, track.album_art_bytes)

    def test_verified_selection_changes_future_requests_without_touching_playing_music(self):
        self.start_music()
        before = self.current_state()
        with patch.object(self.qt, "stop", wraps=self.qt.stop) as stopped, \
                patch.object(self.qt, "setSource", wraps=self.qt.setSource) as source, \
                patch.object(self.qt, "play", wraps=self.qt.play) as played:
            self.assertTrue(self.owned.set_personal_dj_voice("M792-NP"))
        self.assertEqual(self.current_state(), before)
        stopped.assert_not_called(); source.assert_not_called(); played.assert_not_called()
        self.service.personal_dj_voices.assert_not_called()
        self.assertEqual([song.song_id for song in self.owned.queue], ["song-1"])
        self.finish(self.prefetch, "fm_prefetch")
        self.assertEqual(self.dj_calls[-1], ("netease", "M792-NP"))
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-1")

    def test_late_old_voice_batch_cannot_append_or_clear_new_pending_request(self):
        self.start_music()
        token, action = next(job for job in reversed(self.prefetch.jobs) if job[0][0] == "fm_prefetch")
        old_result = action()
        self.assertTrue(self.owned.set_personal_dj_voice("M795-NP"))
        self.prefetch.completed.emit(token, old_result, None)
        self.assertEqual([song.song_id for song in self.owned.queue], ["song-1"])
        self.assertTrue(self.owned._fm_prefetch_pending)
        self.assertEqual(action(), ())

    def test_switch_away_then_back_still_rejects_first_generation(self):
        self.start_music()
        self.owned.set_personal_dj_voice("F793-C2")
        token, action = next(job for job in reversed(self.prefetch.jobs) if job[0][0] == "fm_prefetch")
        stale = action()
        self.owned.set_personal_dj_voice("M791-C2")
        self.owned.set_personal_dj_voice("F793-C2")
        self.prefetch.completed.emit(token, stale, None)
        self.assertEqual(len(self.owned.queue), 1)
        self.assertTrue(self.owned._fm_prefetch_pending)

    def test_unknown_unavailable_malformed_and_closed_choices_never_mutate_preference(self):
        self.start_music()
        before = (self.current_state(), self.owned._fm_generation, tuple(self.owned.queue))
        for key in ("unknown", "M792-NP?token", None, True, "x" * 129):
            self.assertFalse(self.owned.set_personal_dj_voice(key))
        self.catalog = ()
        self.assertFalse(self.owned.set_personal_dj_voice("F793-C2"))
        self.owned._closed = True
        self.assertFalse(self.owned.set_personal_dj_voice(""))
        self.owned._closed = False
        self.assertEqual((self.current_state(), self.owned._fm_generation, tuple(self.owned.queue)), before)
        self.assertEqual(self.owned.personal_dj_voice_key, "")
        self.service.personal_dj_voices.assert_not_called()

    def test_saved_preference_restores_without_catalog_and_next_request_keeps_exact_key(self):
        self.catalog = ()
        self.assertTrue(self.owned.restore_personal_dj_voice("M791-C2"))
        self.assertFalse(self.owned.restore_personal_dj_voice("M791-C2?secret"))
        self.assertEqual(self.owned.personal_dj_voice_key, "M791-C2")
        self.service.personal_dj_voices.assert_not_called()
        self.assertTrue(self.owned.play_personal_dj())
        self.finish(self.tasks, "catalog")
        self.assertEqual(self.dj_calls, [("netease", "M791-C2")])

    def test_same_voice_is_noop_and_paused_music_stays_paused(self):
        self.start_music()
        self.qt.pause()
        self.owned.set_personal_dj_voice("F793-C2")
        before = (self.current_state(), self.owned._fm_generation, len(self.prefetch.jobs))
        self.assertTrue(self.owned.set_personal_dj_voice("F793-C2"))
        self.assertEqual((self.current_state(), self.owned._fm_generation, len(self.prefetch.jobs)), before)
        self.assertEqual(self.qt.state, QMediaPlayer.PlaybackState.PausedState)

    def test_pending_current_audio_and_lyrics_are_not_cancelled_by_voice_choice(self):
        self.start_music(finish_stream=False)
        token, action = next(job for job in self.tasks.jobs if job[0][0] == "stream")
        self.assertTrue(self.owned.set_personal_dj_voice("M792-NP"))
        self.tasks.completed.emit(token, action(), None)
        self.finish(self.tasks, "lyrics")
        self.assertTrue(self.owned.track.is_playing)
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-1")
        self.assertTrue(self.owned.track.timed_lyrics)

    def test_current_intro_and_associated_song_finish_without_replay_or_visual_rollback(self):
        self.owned.personal_dj_enabled = True
        self.owned._replace_queue(segments(1) + segments(2), fm_source="netease", fm_kind="dj")
        self.finish(self.tasks, "stream")
        self.finish(self.tasks, "dj_lyrics")
        before = self.current_state()
        presentation = self.owned.get_personal_dj_presentation()
        self.assertTrue(self.owned.set_personal_dj_voice("M795-NP"))
        self.assertEqual(self.current_state(), before)
        after_presentation = self.owned.get_personal_dj_presentation()
        # The current introduction stays untouched; canceled recommendations
        # must no longer advertise the former voice's next-song preparation.
        self.assertEqual({key: value for key, value in after_presentation.items()
            if not key.startswith("next_preload_")}, {key: value for key, value in presentation.items()
            if not key.startswith("next_preload_")})
        self.assertEqual(after_presentation["next_preload_state"], "idle")
        self.assertEqual([song.song_id for song in self.owned.queue], ["dj:voice-1", "song-1"])
        self.dj_qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-1")
        self.assertFalse(self.owned.is_personal_dj_speaking())

    def test_stale_prepared_future_speech_is_released_but_current_lease_is_retained(self):
        self.start_music()
        future = self.owned.queue[1]
        prepared = PreparedAudio("future-speech", Path(self.folder.name) / "speech", future.direct_stream, 20, 1)
        current = PreparedAudio("current-song", Path(self.folder.name) / "music",
            StreamInfo("netease", "song-1", "https://example.test/song.mp3", True), 20, 1)
        cache = SimpleNamespace(release=Mock())
        self.owned.stream_cache = cache
        self.owned._prefetched_audio, self.owned._prepared_audio = prepared, current
        self.owned.set_personal_dj_voice("M791-C2")
        cache.release.assert_called_once_with(prepared)
        self.assertIs(self.owned._prepared_audio, current)
        self.assertIsNone(self.owned._prefetched_audio)
        self.owned.stream_cache = None
        self.owned._prepared_audio = None

    def test_inactive_and_traditional_fm_selection_does_not_invalidate_music_recommendations(self):
        self.start_music(dj=False)
        before = (self.current_state(), self.owned._fm_generation, tuple(self.owned.queue))
        token, action = next(job for job in reversed(self.prefetch.jobs) if job[0][0] == "fm_prefetch")
        self.owned.set_personal_dj_voice("M795-NP")
        self.assertEqual((self.current_state(), self.owned._fm_generation, tuple(self.owned.queue)), before)
        self.assertTrue(action())
        self.prefetch.completed.emit(token, action(), None)
        self.assertFalse(self.owned._fm_prefetch_pending)
        self.assertEqual(self.dj_calls, [])

    def test_old_first_entry_catalog_is_replaced_without_resurrecting_old_voice(self):
        self.owned.play_personal_dj()
        old_token, old_action = self.tasks.jobs[-1]
        old_result = old_action()
        self.assertTrue(self.owned.set_personal_dj_voice("M792-NP"))
        self.tasks.completed.emit(old_token, old_result, None)
        self.assertIsNone(self.owned.track)
        self.finish(self.tasks, "catalog")
        self.assertEqual(self.dj_calls[-1], ("netease", "M792-NP"))
        self.assertEqual(self.owned.queue[0].song_id, "dj:voice-7")

    def test_repeat_one_prepares_new_voice_batch_without_changing_current_music(self):
        self.start_music()
        self.owned.set_playback_mode("repeat_one")
        count = len(self.prefetch.jobs)
        self.owned.set_personal_dj_voice("M791-C2")
        self.assertEqual(len(self.prefetch.jobs), count + 1)
        self.assertEqual(self.owned.playback_mode, "repeat_one")
        current = self.current_state()
        self.finish(self.prefetch, "fm_prefetch")
        self.assertEqual(self.dj_calls[-1], ("netease", "M791-C2"))
        self.assertEqual(self.current_state(), current)
        self.owned.set_playback_mode("sequential")

    def test_thread_control_generation_rejects_old_voice_click(self):
        self.start_music()
        generation = (self.owned._epoch, self.owned._catalog_epoch, self.owned._queue_epoch, self.owned._mode_epoch)
        done, result = threading.Event(), {"generation": generation}
        self.owned.set_personal_dj_voice("F793-C2")
        self.owned._apply_control_request(("personal_dj_voice", "M795-NP", None, time.monotonic() + 1, done, result))
        self.assertTrue(done.is_set())
        self.assertNotIn("succeeded", result)
        self.assertEqual(self.owned.personal_dj_voice_key, "F793-C2")

    def test_provider_lists_real_catalog_and_saves_only_confirmed_program_preferences(self):
        saved = []
        values = {"v2_owned_playback_enabled": True}
        system = SimpleNamespace(suspend_live_audio=lambda: None, stop_audio_cast=lambda: None,
                                 settings_setter=lambda key, value: saved.append((key, value)))
        provider = PlaybackSourceProvider(system, self.owned, lambda key, default=None: values.get(key, default))
        self.addCleanup(provider._owned_audio_bridge.close)
        self.assertEqual(provider.list_personal_dj_voices(), VOICES)
        self.service.personal_dj_voices.assert_called_once_with("netease")
        self.assertTrue(provider.set_owned_personal_dj_voice("M792-NP"))
        self.assertEqual(provider.get_owned_personal_dj_voice_key(), "M792-NP")
        self.assertEqual(saved, [("v2_personal_dj_voice_key", "M792-NP")])
        self.assertFalse(provider.set_owned_personal_dj_voice("unknown"))
        self.catalog = ()
        self.assertFalse(provider.set_owned_personal_dj_voice("F793-C2"))
        values["v2_owned_playback_enabled"] = False
        self.assertFalse(provider.set_owned_personal_dj_voice(""))
        self.assertEqual(len(saved), 1)

    def test_voice_change_cannot_supersede_explicit_pending_playlist(self):
        self.start_music()
        self.owned.service.daily_recommendations = lambda source: (segments(6)[1],)
        self.owned.play_daily()
        token, action = self.tasks.jobs[-1]
        self.assertTrue(self.owned.set_personal_dj_voice("M792-NP"))
        self.tasks.completed.emit(token, action(), None)
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-6")
        self.assertFalse(self.owned.is_personal_fm())

    def test_failed_explicit_replacement_never_restores_obsolete_future_voice(self):
        self.start_music()
        self.owned.service.daily_recommendations = lambda source: (segments(6)[1],)
        self.owned.play_daily()
        token, _action = self.tasks.jobs[-1]
        before = self.current_state()
        jobs = len(self.prefetch.jobs)
        self.assertTrue(self.owned.set_personal_dj_voice("M792-NP"))
        self.assertEqual(self.current_state(), before)
        self.assertEqual([song.song_id for song in self.owned.queue], ["song-1"])
        self.assertEqual(self.owned._catalog_pending_epoch, token[1])
        self.assertEqual(len(self.prefetch.jobs), jobs)
        self.tasks.completed.emit(token, None, "network")
        self.assertTrue(self.owned.track.is_playing)
        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.finish(self.tasks, "catalog")
        self.assertEqual(self.dj_calls[-1], ("netease", "M792-NP"))
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-7")

    def test_pending_continuation_buffered_error_keeps_existing_fault_handling(self):
        self.start_music()
        self.qt.durationChanged.emit(120000)
        self.owned._fm_prefetch_pending = False
        self.owned._advance_fm()
        token, action = self.tasks.jobs[-1]
        stale = action()
        self.qt.errorOccurred.emit(QMediaPlayer.Error.ResourceError, "fixture secret")
        self.assertTrue(self.owned.set_personal_dj_voice("M792-NP"))
        self.assertEqual(self.owned._catalog_pending_epoch, 0)
        self.assertIsNotNone(self.owned._stream_fault)
        self.assertEqual(self.owned.failure_reason, "resource")
        self.assertTrue(self.owned.track.is_playing)
        self.tasks.completed.emit(token, stale, None)
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-1")

    def test_continuation_at_eof_uses_new_voice_and_rejects_old_catalog(self):
        self.start_music()
        self.owned.queue = self.owned.queue[:1]
        self.owned._fm_prefetch_pending = False
        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        token, action = self.tasks.jobs[-1]
        stale = action()
        self.assertTrue(self.owned.set_personal_dj_voice("M795-NP"))
        self.assertTrue(self.owned._fm_advance_pending)
        self.tasks.completed.emit(token, stale, None)
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-1")
        self.finish(self.prefetch, "fm_prefetch")
        self.assertEqual(self.dj_calls[-1], ("netease", "M795-NP"))
        self.finish(self.tasks, "stream")
        self.assertEqual(self.qt.state, QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(self.owned.queue[self.owned.index].song_id, "song-7")

    def test_paused_continuation_stays_paused_when_new_voice_arrives(self):
        self.start_music()
        self.owned.queue = self.owned.queue[:1]
        self.owned._fm_prefetch_pending = False
        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.owned.send_playback_command("toggle_play_pause")
        self.assertFalse(self.owned._fm_resume)
        self.owned.set_personal_dj_voice("M791-C2")
        self.finish(self.prefetch, "fm_prefetch")
        self.finish(self.tasks, "stream")
        self.assertEqual(self.qt.state, QMediaPlayer.PlaybackState.PausedState)
        self.assertFalse(self.owned.track.is_playing)

    def test_rejected_new_voice_prefetch_keeps_healthy_song_until_its_eof(self):
        self.start_music()
        before = self.current_state()
        self.prefetch.accepted = False
        self.assertTrue(self.owned.set_personal_dj_voice("M792-NP"))
        self.assertEqual(self.current_state(), before)
        self.assertFalse(self.owned._fm_prefetch_pending)
        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.finish(self.tasks, "catalog")
        self.assertEqual(self.dj_calls[-1], ("netease", "M792-NP"))

    def test_current_introduction_keeps_prepared_associated_song_on_voice_change(self):
        self.owned.personal_dj_enabled = True
        self.owned._replace_queue(segments(1) + segments(2), fm_source="netease", fm_kind="dj")
        self.finish(self.tasks, "stream")
        song = self.owned.queue[1]
        prepared = PreparedAudio("associated-song", Path(self.folder.name) / "music",
            StreamInfo(song.source_id, song.song_id, "https://example.test/song.mp3", True), 20, 1)
        cache = SimpleNamespace(release=Mock())
        self.owned.stream_cache = cache
        self.owned._prepared_audio = prepared
        self.owned.set_personal_dj_voice("M795-NP")
        self.assertIs(self.owned._prepared_audio, prepared)
        cache.release.assert_not_called()
        self.owned.stream_cache = None
        self.owned._prepared_audio = None


if __name__ == "__main__":
    unittest.main()
