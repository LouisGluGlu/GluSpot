"""Decoder-backed completion events and honest complete-introduction readiness."""
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from api.lyrics import LocalLyricsStore
from api.online_music import OnlineSong, PersonalDjVoice, StreamInfo
from api.owned_stream_cache import StreamDownloadError
from local.owned_music_player import OwnedMusicPlayer
from tests.test_owned_buffered_playback import FixtureCache
from tests.test_owned_playback import Player, Tasks
from tests.test_personal_dj_playback import segments
from tests.test_personal_dj_prefetch import parts


class PersonalDjCompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.tasks, self.downloads, self.catalogs = Tasks(), Tasks(), Tasks()
        self.music, self.speech = Player(), Player()
        self.cache = FixtureCache(self.root)
        self.service = SimpleNamespace(stream_url=lambda source, song: StreamInfo(source, song,
            f"https://fixture.invalid/{song}.mp3", True), lyrics=lambda *args: None,
            personal_dj=Mock(return_value=segments(7) + segments(8)))
        self.owned = OwnedMusicPlayer(self.service, LocalLyricsStore(self.root / "lyrics"),
            player=self.music, audio=object(), dj_player=self.speech, dj_audio=object(),
            tasks=self.tasks, artwork_tasks=Tasks(), prefetch_tasks=self.catalogs,
            stream_cache=self.cache, download_tasks=self.downloads)
        self.owned.personal_dj_enabled = True

    def tearDown(self):
        self.owned.close()
        self.folder.cleanup()

    def finish(self, kind, tasks=None):
        tasks = tasks or self.downloads
        token, action = next(job for job in reversed(tasks.jobs) if job[0][0] == kind)
        result = action()
        tasks.completed.emit(token, result, None)
        return result

    def finish_voice(self, identity):
        token = next(token for voice, token in self.owned._dj_prefetch_requests.items()
            if voice.song_id == identity)
        _, action = next(job for job in self.downloads.jobs if job[0] == token)
        result = action()
        self.downloads.completed.emit(token, result, None)
        return result

    def choose(self, batch=None):
        self.owned._replace_queue(batch or segments(1) + segments(2) + segments(3) + segments(4),
            fm_source="netease", fm_kind="dj")

    def start(self, batch=None):
        self.choose(batch)
        self.finish("dj_stream", self.tasks)
        self.finish("dj_audio")
        self.music_ready()

    def music_ready(self):
        self.finish("stream", self.tasks)
        self.finish("audio")

    def presentation(self):
        return self.owned.get_personal_dj_presentation()

    def end_speech(self):
        self.speech.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)

    def test_final_actual_intro_end_emits_once_retains_song_and_real_next_preparation(self):
        self.start()
        next_intro = self.finish("dj_prefetch")
        before = (self.owned._epoch, self.owned.track.track_id, self.music.source())
        with patch("local.owned_music_player.time.monotonic", return_value=1234.5):
            self.end_speech()
        data = self.presentation()
        self.assertEqual((data["completion_id"], data["completed_track_id"], data["completed_at"]),
            (1, "owned:netease:song-1", 1234.5))
        self.assertFalse(data["is_preview"])
        self.assertEqual(data["next_preload_state"], "ready")
        self.assertEqual(data["next_preload_title"], "Song 2")
        self.assertEqual(data["next_preload_track_id"], "owned:netease:song-2")
        self.assertIs(self.owned._dj_prefetched_audio[self.owned.queue[2]], next_intro)
        self.assertEqual((self.owned._epoch, self.owned.track.track_id, self.music.source()), before)
        self.end_speech()
        self.assertEqual(self.presentation()["completion_id"], 1)

    def test_pause_resume_and_intermediate_part_loading_are_not_completion(self):
        self.start(parts(1, 2) + segments(2) + segments(3) + segments(4))
        self.owned.send_playback_command("toggle_play_pause")
        self.assertEqual(self.presentation()["completion_id"], 0)
        self.owned.send_playback_command("toggle_play_pause")
        self.end_speech()
        self.assertEqual(self.presentation()["completion_id"], 0)
        self.assertEqual(self.presentation()["state"], "loading")
        self.finish_voice("dj:voice-1:part-1")  # The pending next part is promoted.
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.end_speech()
        self.assertEqual(self.presentation()["completion_id"], 1)

    def test_manual_skip_stop_seek_failure_disable_and_replacement_cannot_finish_intro(self):
        operations = (lambda: self.owned.send_playback_command("next"),
            self.owned.stop, lambda: self.owned.seek_playback_position(10),
            lambda: self.speech.errorOccurred.emit(QMediaPlayer.Error.ResourceError, "fixture"),
            lambda: self.owned.set_personal_dj_enabled(False),
            lambda: self.owned.play_queue((OnlineSong("qq", "other", "other"),)))
        for operation in operations:
            with self.subTest(operation=operation):
                self.owned.personal_dj_enabled = True
                self.start()
                operation()
                self.assertEqual(self.presentation()["completion_id"], 0)
                self.assertEqual(self.owned._dj_completion_serial, 0)

    def test_next_song_clears_old_completion_but_next_actual_end_advances_serial(self):
        self.start()
        self.end_speech()
        self.assertEqual(self.presentation()["completion_id"], 1)
        self.owned.send_playback_command("next")
        self.assertEqual(self.presentation()["completion_id"], 0)
        self.finish_voice("dj:voice-2")
        self.music_ready()
        self.end_speech()
        self.assertEqual(self.presentation()["completion_id"], 2)
        self.assertEqual(self.presentation()["completed_track_id"], "owned:netease:song-2")

    def test_end_status_delivered_after_user_pause_does_not_publish_completion(self):
        self.start()
        self.owned.send_playback_command("toggle_play_pause")
        self.end_speech()
        self.assertEqual(self.presentation()["completion_id"], 0)
        self.assertEqual(self.owned._dj_completion_serial, 0)

    def test_preview_cannot_emit_completion_or_resurrect_a_previous_intro_end(self):
        self.start()
        self.end_speech()
        voice = PersonalDjVoice("A", "官方试听", preview_stream=StreamInfo("netease", "dj-preview:A",
            "https://fixture.invalid/preview.mp3", True, quality="dj_preview"))
        self.service.personal_dj_voices_cached = lambda source: (voice,)
        self.owned.preview_personal_dj_voice("A")
        self.finish("dj_stream", self.tasks)
        self.finish("dj_audio")
        self.assertTrue(self.presentation()["is_preview"])
        self.assertEqual(self.presentation()["completion_id"], 0)
        self.end_speech()
        self.assertEqual(self.presentation()["completion_id"], 0)
        self.assertEqual(self.owned._dj_completion_serial, 1)

    def test_pending_catalog_intent_cannot_emit_or_revive_old_completion(self):
        self.start()
        self.service.daily_recommendations = lambda source: (segments(9)[1],)
        self.owned.play_daily()
        self.end_speech()
        self.assertEqual(self.presentation()["completion_id"], 0)
        token, _ = next(job for job in reversed(self.tasks.jobs) if job[0][0] == "catalog")
        self.tasks.completed.emit(token, None, "network")
        self.assertEqual(self.presentation()["completion_id"], 0)
        self.assertEqual(self.owned._dj_completion_serial, 0)

    def test_completed_intro_does_not_return_after_catalog_cancel_or_voice_change(self):
        self.start()
        self.end_speech()
        self.service.daily_recommendations = lambda source: ()
        self.owned.play_daily()
        self.assertEqual(self.presentation()["completion_id"], 0)
        token, _ = next(job for job in reversed(self.tasks.jobs) if job[0][0] == "catalog")
        self.tasks.completed.emit(token, None, "network")
        self.assertEqual(self.presentation()["completion_id"], 0)
        self.service.personal_dj_voices_cached = lambda source: (PersonalDjVoice("B", "B"),)
        self.owned.set_personal_dj_voice("B")
        self.assertEqual(self.presentation()["completion_id"], 0)

    def test_complete_next_multipart_only_ready_when_every_part_is_prepared(self):
        self.choose((segments(1)[1],) + parts(2, 2) + segments(3) + segments(4))
        self.assertEqual(self.presentation()["next_preload_state"], "loading")
        self.finish("dj_prefetch")
        self.assertEqual(self.presentation()["next_preload_state"], "loading")
        self.finish("dj_prefetch")
        self.assertEqual(self.presentation()["next_preload_state"], "ready")

    def test_bounded_long_multipart_never_claims_complete_next_readiness(self):
        self.choose((segments(1)[1],) + parts(2, 4) + segments(3) + segments(4))
        self.finish("dj_prefetch")
        self.finish("dj_prefetch")
        self.assertEqual(len(self.owned._dj_prefetched_audio), 2)
        self.assertEqual(self.presentation()["next_preload_state"], "loading")

    def test_song_without_intro_cannot_advertise_later_cached_speech_as_next_ready(self):
        self.choose((segments(1)[1], segments(2)[1]) + segments(3) + segments(4))
        self.finish("dj_prefetch")
        data = self.presentation()
        self.assertEqual((data["next_preload_state"], data["next_preload_title"],
            data["next_preload_track_id"]), ("idle", "", ""))

    def test_expired_invalid_or_failed_next_introduction_is_never_ready(self):
        self.choose((segments(1)[1],) + segments(2) + segments(3) + segments(4))
        prepared = self.finish("dj_prefetch")
        with patch("local.owned_music_player.time.time", return_value=prepared.stream.expires_at + 1):
            self.assertEqual(self.presentation()["next_preload_state"], "failed")
        voice = self.owned.queue[1]
        self.owned._dj_prefetched_audio[voice] = replace(prepared,
            stream=replace(prepared.stream, song_id="wrong"))
        self.assertEqual(self.presentation()["next_preload_state"], "loading")
        self.owned._dj_prefetch_failed.add(voice)
        self.assertEqual(self.presentation()["next_preload_state"], "failed")

    def test_disabled_closed_and_changed_source_hide_all_completion_and_next_state(self):
        self.start()
        self.finish("dj_prefetch")
        self.end_speech()
        self.owned.set_personal_dj_enabled(False)
        self.assertEqual(self.presentation()["completion_id"], 0)
        self.assertEqual(self.presentation()["next_preload_state"], "idle")
        self.owned.set_personal_dj_enabled(True)
        self.assertEqual(self.presentation()["completion_id"], 0)
        self.owned.play_queue((OnlineSong("qq", "other", "other"),))
        self.assertEqual(self.presentation()["next_preload_state"], "idle")
        self.owned.close()
        self.assertEqual(self.presentation()["completion_id"], 0)

    def test_natural_music_eof_uses_ready_genuine_intro_just_like_manual_next(self):
        self.start()
        next_intro = self.finish("dj_prefetch")
        self.end_speech()
        self.music.stop()
        self.music.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-2")
        self.assertIs(self.owned._dj_prepared_audio, next_intro)
        self.assertEqual(self.presentation()["completion_id"], 0)
        self.music_ready()
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertEqual(self.music.state, QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(self.speech.state, QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(self.presentation()["next_title"], "Song 2")

    def test_single_song_repeat_has_no_repeated_intro_but_manual_next_has_real_speech(self):
        self.start()
        self.finish("dj_prefetch")
        self.end_speech()
        self.owned.set_playback_mode("repeat_one")
        self.music.stop()
        self.music.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.music_ready()
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-1")
        self.assertFalse(self.owned._dj_overlay_voices)
        self.assertFalse(self.owned.is_personal_dj_speaking())
        self.owned.send_playback_command("next")
        self.music_ready()
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-2")
        self.assertTrue(self.owned.is_personal_dj_speaking())

    def test_natural_music_eof_waits_for_pending_catalog_then_starts_actual_intro(self):
        self.start(segments(1))
        self.end_speech()
        self.music.stop()
        self.music.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertTrue(self.owned._fm_advance_pending)
        self.finish("fm_prefetch", self.catalogs)
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-7")
        self.finish_voice("dj:voice-7")
        self.music_ready()
        self.assertTrue(self.owned.is_personal_dj_speaking())

    def reject_once(self, tasks, kind):
        original = tasks.submit
        rejected = []
        def submit(token, action):
            if token[0] == kind and not rejected:
                rejected.append(token)
                return False
            return original(token, action)
        tasks.submit = submit
        return rejected

    def natural_next_without_ready_intro(self):
        self.owned._clear_dj_audio_prefetch()
        self.music.stop()
        self.music.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)

    def test_auto_next_recovers_once_rejected_foreground_stream_without_silent_intro_loss(self):
        self.choose((segments(1)[1],) + segments(2) + segments(3) + segments(4))
        self.music_ready()
        rejected = self.reject_once(self.tasks, "dj_stream")
        self.natural_next_without_ready_intro()
        self.assertEqual(len(rejected), 1)
        self.assertTrue(self.presentation()["introduction_pending"])
        self.assertEqual(self.presentation()["state"], "loading")
        self.assertIsNotNone(self.owned._dj_overlay_retry)
        self.assertTrue(self.owned._dj_prefetch_timer.isActive())
        self.music_ready()
        self.owned._retry_dj_prefetch()
        self.finish("dj_stream", self.tasks)
        self.finish("dj_audio")
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-2")
        self.assertIsNone(self.owned._dj_overlay_retry)

    def test_auto_next_recovers_once_rejected_download_submission_for_verified_intro(self):
        self.choose((segments(1)[1],) + segments(2) + segments(3) + segments(4))
        self.music_ready()
        self.reject_once(self.downloads, "dj_audio")
        self.natural_next_without_ready_intro()
        self.finish("dj_stream", self.tasks)
        self.assertIsNotNone(self.owned._dj_overlay_retry)
        self.assertTrue(self.presentation()["introduction_pending"])
        self.music_ready()
        self.owned._retry_dj_prefetch()
        self.finish("dj_audio")
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-2")

    def test_foreground_retries_share_three_second_budget_across_stream_and_download(self):
        original = self.tasks.submit
        self.tasks.submit = lambda token, action: False if token[0] == "dj_stream" else original(token, action)
        with patch("local.owned_music_player.time.monotonic", return_value=10):
            self.choose()
        self.assertEqual(self.owned._dj_overlay_retry_deadline, 13)
        self.tasks.submit = original
        self.reject_once(self.downloads, "dj_audio")
        with patch("local.owned_music_player.time.monotonic", return_value=12.9):
            self.owned._retry_dj_overlay()
            self.finish("dj_stream", self.tasks)
        self.assertEqual(self.owned._dj_overlay_retry_deadline, 13)
        with patch("local.owned_music_player.time.monotonic", return_value=13):
            self.owned._retry_dj_overlay()
            self.assertTrue(self.presentation()["introduction_failed"])
        self.assertIsNone(self.owned._dj_overlay_retry)
        self.assertFalse(self.owned._dj_overlay_voices)

    def test_download_busy_budget_does_not_restart_inside_worker(self):
        stream = segments(1)[0].direct_stream
        self.cache.prepare = Mock(side_effect=StreamDownloadError("busy"))
        with patch("local.owned_music_player.time.monotonic", return_value=13), \
                patch("local.owned_music_player.time.sleep") as sleep:
            with self.assertRaises(StreamDownloadError) as caught:
                self.owned._prepare_audio(stream, cancelled=lambda: False, busy_deadline=13)
        self.assertEqual(caught.exception.code, "busy")
        sleep.assert_not_called()
        self.assertEqual(self.cache.prepare.call_count, 1)

    def test_foreground_retry_expiry_fails_without_reviving_expired_speech(self):
        self.reject_once(self.tasks, "dj_stream")
        self.choose()
        stream = self.owned._dj_overlay_voices[0].direct_stream
        before = len(self.tasks.jobs)
        with patch("local.owned_music_player.time.time", return_value=stream.expires_at + 1):
            self.owned._retry_dj_overlay()
        self.assertEqual(len(self.tasks.jobs), before)
        self.assertFalse(self.owned._dj_overlay_voices)
        self.assertTrue(self.presentation()["introduction_failed"])

    def test_source_stop_disable_and_new_song_cancel_pending_foreground_retry(self):
        operations = (self.owned.stop, lambda: self.owned.set_personal_dj_enabled(False),
            lambda: self.owned.play_queue((OnlineSong("qq", "other", "other"),)),
            lambda: self.owned.send_playback_command("next"))
        for operation in operations:
            with self.subTest(operation=operation):
                self.owned.personal_dj_enabled = True
                rejected = self.reject_once(self.tasks, "dj_stream")
                self.choose()
                self.assertTrue(rejected)
                retry = self.owned._dj_overlay_retry
                operation()
                self.assertNotEqual(self.owned._dj_overlay_retry, retry)
                before = len(self.tasks.jobs)
                self.owned._retry_dj_overlay()
                self.assertEqual(len(self.tasks.jobs), before)

    def test_pause_during_contention_prepares_intro_without_reviving_playback(self):
        self.reject_once(self.tasks, "dj_stream")
        self.choose()
        self.owned.send_playback_command("toggle_play_pause")
        self.music_ready()
        self.owned._retry_dj_overlay()
        self.finish("dj_stream", self.tasks)
        self.finish("dj_audio")
        self.assertFalse(self.owned._autoplay_after_load)
        self.assertNotEqual(self.music.state, QMediaPlayer.PlaybackState.PlayingState)
        self.assertNotEqual(self.speech.state, QMediaPlayer.PlaybackState.PlayingState)
        self.assertTrue(self.presentation()["introduction_pending"])
        self.owned.send_playback_command("toggle_play_pause")
        self.assertTrue(self.owned.is_personal_dj_speaking())

    def test_current_intro_retry_survives_voice_choice_but_future_old_voice_is_canceled(self):
        self.reject_once(self.tasks, "dj_stream")
        self.choose()
        self.service.personal_dj_voices_cached = lambda source: (PersonalDjVoice("B", "B"),)
        self.owned.set_personal_dj_voice("B")
        self.assertTrue(self.owned._dj_prefetch_timer.isActive())
        self.owned._retry_dj_overlay()
        self.finish("dj_stream", self.tasks)
        self.finish("dj_audio")
        self.music_ready()
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-1")
        self.assertEqual(self.owned.personal_dj_voice_key, "B")

    def test_network_failure_never_retries_and_current_failure_expires_without_tick_extension(self):
        self.start()
        self.speech.errorOccurred.emit(QMediaPlayer.Error.NetworkError, "fixture")
        self.assertIsNone(self.owned._dj_overlay_retry)
        self.assertFalse(self.presentation()["introduction_pending"])
        self.assertTrue(self.presentation()["introduction_failed"])
        deadline = self.owned._dj_intro_failure[1]
        with patch("local.owned_music_player.time.monotonic", return_value=deadline):
            self.assertFalse(self.presentation()["introduction_failed"])
            self.assertFalse(self.presentation()["introduction_failed"])
        self.assertEqual(self.owned._dj_intro_failure[1], deadline)

    def test_future_preload_and_preview_failures_never_claim_current_intro_failure(self):
        self.choose((segments(1)[1],) + segments(2) + segments(3) + segments(4))
        token, _ = next(job for job in reversed(self.downloads.jobs) if job[0][0] == "dj_prefetch")
        self.downloads.completed.emit(token, None, "network")
        self.assertFalse(self.presentation()["introduction_pending"])
        self.assertFalse(self.presentation()["introduction_failed"])
        self.music_ready()
        voice = PersonalDjVoice("A", "官方试听", preview_stream=StreamInfo("netease", "dj-preview:A",
            "https://fixture.invalid/preview.mp3", True, quality="dj_preview"))
        self.service.personal_dj_voices_cached = lambda source: (voice,)
        self.owned.preview_personal_dj_voice("A")
        self.finish("dj_stream", self.tasks)
        self.finish("dj_audio")
        self.speech.errorOccurred.emit(QMediaPlayer.Error.NetworkError, "fixture")
        self.assertFalse(self.presentation()["introduction_pending"])
        self.assertFalse(self.presentation()["introduction_failed"])

    def test_current_intro_failure_cannot_cross_catalog_source_or_toggle_boundaries(self):
        for operation in (lambda: self.owned.play_daily(),
                lambda: self.owned.play_queue((OnlineSong("qq", "other", "other"),)),
                lambda: self.owned.set_personal_dj_enabled(False)):
            with self.subTest(operation=operation):
                self.owned.personal_dj_enabled = True
                self.start()
                self.speech.errorOccurred.emit(QMediaPlayer.Error.NetworkError, "fixture")
                self.assertTrue(self.presentation()["introduction_failed"])
                operation()
                self.assertFalse(self.presentation()["introduction_failed"])


if __name__ == "__main__":
    unittest.main()
