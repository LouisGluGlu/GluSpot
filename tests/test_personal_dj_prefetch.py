"""FM lookahead survives transport timing, manual skips and voice generations."""
from dataclasses import replace
from pathlib import Path
import tempfile
import time
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


def parts(number, count):
    voice, song = segments(number)
    voices = tuple(replace(voice, song_id=f"{voice.song_id}:part-{part}",
        direct_stream=replace(voice.direct_stream, song_id=f"{voice.song_id}:part-{part}"))
        for part in range(count))
    return (*voices, song)


class PersonalDjPrefetchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.tasks, self.downloads, self.catalogs = Tasks(), Tasks(), Tasks()
        self.qt, self.dj_qt = Player(), Player()
        self.cache = FixtureCache(self.root)
        self.service = SimpleNamespace(
            stream_url=Mock(side_effect=lambda source, song: StreamInfo(source, song,
                "https://fixture.invalid/music", True)), lyrics=lambda *args: None,
            personal_dj=Mock(return_value=segments(7) + segments(8)),
            personal_dj_voices_cached=lambda source: (PersonalDjVoice("A", "A"), PersonalDjVoice("B", "B")))
        self.owned = OwnedMusicPlayer(self.service, LocalLyricsStore(self.root / "lyrics"),
            player=self.qt, audio=object(), dj_player=self.dj_qt, dj_audio=object(),
            tasks=self.tasks, artwork_tasks=Tasks(), prefetch_tasks=self.catalogs,
            stream_cache=self.cache, download_tasks=self.downloads)
        self.owned.personal_dj_enabled = True

    def tearDown(self):
        self.owned.close()
        self.folder.cleanup()

    def job(self, kind, jobs=None):
        return next(job for job in reversed((jobs or self.downloads).jobs) if job[0][0] == kind)

    def perform(self, kind, jobs=None):
        jobs = jobs or self.downloads
        token, action = self.job(kind, jobs)
        result = action()
        jobs.completed.emit(token, result, None)
        return result

    def choose(self, batch=None):
        self.owned._replace_queue(batch or ((segments(1)[1],) + segments(2) + segments(3) + segments(4)),
            fm_source="netease", fm_kind="dj")

    def music_ready(self):
        self.perform("stream", self.tasks)
        return self.perform("audio")

    def test_next_introduction_is_queued_before_current_music_authorization_or_playback(self):
        self.choose()
        self.assertEqual(len(self.downloads.jobs), 1)
        self.assertEqual(self.downloads.jobs[0][0][0], "dj_prefetch")
        speech = self.perform("dj_prefetch")
        self.assertEqual(speech.stream.song_id, "dj:voice-2")
        self.assertTrue(self.qt.source().isEmpty())
        self.assertEqual(self.owned._stream_epoch, 0)
        self.assertEqual(self.service.stream_url.call_count, 0)
        self.assertEqual(self.owned.get_personal_dj_presentation()["state"], "ready")

    def test_each_rapid_next_retargets_lookahead_before_either_current_decoder_is_ready(self):
        self.choose()
        first_token, first_action = self.job("dj_prefetch")
        first_speech = first_action()
        self.owned.send_playback_command("next")
        # The existing worker is promoted; the following speech is scheduled
        # immediately even before the promoted result reaches Qt's thread.
        second_token, second_action = self.job("dj_prefetch")
        self.assertNotEqual(second_token, first_token)
        second_speech = second_action()
        self.assertEqual(second_speech.stream.song_id, "dj:voice-3")
        self.owned.send_playback_command("next")
        third_token, third_action = self.job("dj_prefetch")
        self.assertNotEqual(third_token, second_token)
        self.assertEqual(third_action().stream.song_id, "dj:voice-4")
        self.downloads.completed.emit(first_token, first_speech, None)
        self.assertIn(first_speech.lease, self.cache.released)
        self.downloads.completed.emit(second_token, second_speech, None)
        self.assertIs(self.owned._dj_prepared_audio, second_speech)
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-3")
        self.assertEqual(self.service.stream_url.call_count, 0)

    def test_paused_repeat_one_keeps_full_next_intro_ready_and_manual_next_resumes(self):
        self.choose()
        self.music_ready()
        self.owned.send_playback_command("toggle_play_pause")
        self.owned.set_playback_mode("repeat_one")
        speech = self.perform("dj_prefetch")
        self.assertEqual(speech.stream.song_id, "dj:voice-2")
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.owned.send_playback_command("next")
        self.assertIs(self.owned._dj_prepared_audio, speech)
        self.music_ready()
        self.assertTrue(self.owned.track.is_playing)
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertTrue(any(voice.song_id == "dj:voice-3" for voice in self.owned._dj_prefetch_requests))

    def test_current_multipart_and_following_song_share_only_two_future_file_slots(self):
        self.choose(parts(1, 4) + parts(2, 3) + segments(3) + segments(4))
        self.assertEqual(tuple(voice.song_id for voice in self.owned._dj_audio_window()),
            ("dj:voice-1:part-1", "dj:voice-2:part-0"))
        second = self.perform("dj_prefetch")
        next_song = self.perform("dj_prefetch")
        self.assertEqual(len(self.owned._dj_prefetched_audio), 2)
        music = self.music_ready()
        self.perform("dj_stream", self.tasks)
        self.perform("dj_audio")
        self.assertEqual(len(self.cache.leases), 4)
        self.dj_qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertIs(self.owned._dj_prepared_audio, second)
        self.assertIs(self.owned._prepared_audio, music)
        self.assertIn(next_song.lease, self.cache.leases)
        self.perform("dj_prefetch")
        self.assertLessEqual(len(self.cache.leases), 4)
        self.assertLessEqual(len(self.owned._dj_prefetched_audio), 2)

    def test_full_next_multipart_intro_prepares_during_current_music_and_pauses(self):
        self.choose((segments(1)[1],) + parts(2, 2) + segments(3) + segments(4))
        first = self.perform("dj_prefetch")
        second = self.perform("dj_prefetch")
        self.assertEqual((first.stream.song_id, second.stream.song_id),
            ("dj:voice-2:part-0", "dj:voice-2:part-1"))
        self.music_ready()
        self.owned.send_playback_command("toggle_play_pause")
        self.owned.send_playback_command("next")
        self.assertIs(self.owned._dj_prepared_audio, first)
        self.music_ready()
        self.dj_qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertIs(self.owned._dj_prepared_audio, second)
        self.assertTrue(self.owned.track.is_playing)

    def test_catalog_reserve_counts_songs_instead_of_multipart_speech_entries(self):
        self.choose((segments(1)[1],) + parts(2, 4))
        self.assertTrue(self.owned._fm_prefetch_pending)
        self.assertEqual(len(self.catalogs.jobs), 1)
        self.assertEqual(self.owned._stream_epoch, 0)
        self.perform("fm_prefetch", self.catalogs)
        self.assertEqual(len(self.owned._upcoming_indices(include_voice=False)), 3)

    def test_song_only_recommendation_retains_order_and_warms_next_genuine_speech(self):
        self.choose((segments(1)[1], segments(2)[1]) + segments(3) + segments(4))
        speech = self.perform("dj_prefetch")
        self.assertEqual(speech.stream.song_id, "dj:voice-3")
        self.owned.send_playback_command("next")
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-2")
        self.assertFalse(self.owned._dj_overlay_voices)
        self.owned.send_playback_command("next")
        self.assertIs(self.owned._dj_prepared_audio, speech)

    def test_new_batch_completion_warms_its_intro_even_if_current_song_is_not_ready(self):
        self.choose((segments(1)[1],))
        self.assertFalse(self.downloads.jobs)
        self.perform("fm_prefetch", self.catalogs)
        speech = self.perform("dj_prefetch")
        self.assertEqual(speech.stream.song_id, "dj:voice-7")
        self.assertEqual(self.service.stream_url.call_count, 0)

    def test_voice_change_cancels_old_worker_and_releases_stale_pin(self):
        self.choose()
        token, action = self.job("dj_prefetch")
        stale = action()
        self.assertTrue(self.owned.set_personal_dj_voice("B"))
        self.downloads.completed.emit(token, stale, None)
        self.assertIn(stale.lease, self.cache.released)
        self.assertFalse(self.owned._dj_prefetched_audio)
        self.assertIsNone(action())
        self.assertTrue(self.owned._fm_prefetch_pending)

    def test_voice_change_retains_promoted_current_intro_download_and_future_parts_of_same_intro(self):
        self.choose((segments(1)[1],) + parts(2, 2) + segments(3) + segments(4))
        token, action = self.job("dj_prefetch")
        first = action()
        self.owned.send_playback_command("next")
        second_token, second_action = self.job("dj_prefetch")
        self.assertNotEqual(second_token, token)
        second = second_action()
        self.assertFalse(self.owned._dj_source_ready)
        self.owned.set_personal_dj_voice("B")
        self.downloads.completed.emit(token, first, None)
        self.assertIs(self.owned._dj_prepared_audio, first)
        self.downloads.completed.emit(second_token, second, None)
        self.music_ready()
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.dj_qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
        self.assertIs(self.owned._dj_prepared_audio, second)
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-2")
        self.assertEqual(self.owned.personal_dj_voice_key, "B")

    def test_pending_current_multipart_part_does_not_consume_next_song_prefetch_slot(self):
        self.choose((segments(1)[1],) + parts(2, 3) + segments(3) + segments(4))
        first_token, first_action = self.job("dj_prefetch")
        self.owned.send_playback_command("next")
        self.assertEqual(tuple(voice.song_id for voice in self.owned._dj_audio_window()),
            ("dj:voice-2:part-0", "dj:voice-2:part-1", "dj:voice-3"))
        second = self.perform("dj_prefetch")
        next_song = self.perform("dj_prefetch")
        self.assertEqual(next_song.stream.song_id, "dj:voice-3")
        self.assertFalse(self.owned._dj_source_ready)
        self.assertEqual(len(self.owned._dj_prefetched_audio), 2)
        first = first_action()
        self.downloads.completed.emit(first_token, first, None)
        self.music_ready()
        self.assertEqual(len(self.cache.leases), 4)
        self.assertIs(self.owned._dj_prepared_audio, first)
        self.assertIn(second.lease, self.cache.leases)
        self.assertIn(next_song.lease, self.cache.leases)

    def test_disable_close_and_replacement_cancel_background_download_and_unpin_only_unused(self):
        for operation in (lambda: self.owned.set_personal_dj_enabled(False),
                lambda: self.owned.play_queue((OnlineSong("qq", "chosen", "chosen"),)),
                self.owned.close):
            with self.subTest(operation=operation):
                if self.owned._closed:
                    break
                self.owned.personal_dj_enabled = True
                self.choose()
                token, action = self.job("dj_prefetch")
                stale = action()
                operation()
                self.downloads.completed.emit(token, stale, None)
                self.assertIn(stale.lease, self.cache.released)
                self.assertFalse(self.owned._dj_prefetched_audio)
                self.assertIsNone(action())

    def test_repeat_delivery_does_not_release_the_file_promoted_to_active_speech(self):
        self.choose()
        token, action = self.job("dj_prefetch")
        speech = action()
        self.downloads.completed.emit(token, speech, None)
        self.owned.send_playback_command("next")
        self.downloads.completed.emit(token, speech, None)
        self.assertNotIn(speech.lease, self.cache.released)
        self.assertIs(self.owned._dj_prepared_audio, speech)

    def test_prefetched_expired_url_is_not_handed_to_speech_decoder(self):
        self.choose()
        speech = self.perform("dj_prefetch")
        with patch("local.owned_music_player.time.time", return_value=speech.stream.expires_at + 1):
            self.owned.send_playback_command("next")
        self.assertIn(speech.lease, self.cache.released)
        self.assertIsNone(self.owned._dj_prepared_audio)
        self.assertEqual(self.job("dj_stream", self.tasks)[0][-1], "dj:voice-2")

    def test_promoted_download_result_expiring_before_callback_never_opens_decoder(self):
        self.choose()
        token, action = self.job("dj_prefetch")
        speech = action()
        self.owned.send_playback_command("next")
        with patch("local.owned_music_player.time.time", return_value=speech.stream.expires_at + 1):
            self.downloads.completed.emit(token, speech, None)
        self.assertIn(speech.lease, self.cache.released)
        self.assertIsNone(self.owned._dj_prepared_audio)
        self.assertTrue(self.dj_qt.source().isEmpty())
        self.music_ready()
        self.assertTrue(self.owned.track.is_playing)

    def test_failed_catalog_replacement_does_not_drop_promoted_current_intro(self):
        self.choose()
        token, action = self.job("dj_prefetch")
        speech = action()
        self.owned.send_playback_command("next")
        self.music_ready()
        self.service.daily_recommendations = lambda source: ()
        self.owned.play_daily()
        self.downloads.completed.emit(token, speech, None)
        self.assertIs(self.owned._dj_prepared_audio, speech)
        catalog_token, _ = self.job("catalog", self.tasks)
        self.tasks.completed.emit(catalog_token, None, "network")
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-2")

    def test_busy_preparation_wait_is_bounded_and_cancelled_generation_stops_immediately(self):
        stream = segments(2)[0].direct_stream
        original = self.cache.prepare
        self.cache.prepare = Mock(side_effect=[StreamDownloadError("busy"), StreamDownloadError("busy"),
            original(stream)])
        with patch("local.owned_music_player.time.sleep") as sleep:
            ready = self.owned._prepare_audio(stream, cancelled=lambda: False)
        self.assertEqual(ready.stream, stream)
        self.assertEqual(sleep.call_count, 2)
        self.cache.prepare = Mock(side_effect=StreamDownloadError("busy"))
        with patch("local.owned_music_player.time.monotonic", side_effect=(0, 4)):
            with self.assertRaises(StreamDownloadError) as caught:
                self.owned._prepare_audio(stream, cancelled=lambda: False)
        self.assertEqual(caught.exception.code, "busy")
        checks = iter((False, True))
        with patch("local.owned_music_player.time.sleep"):
            with self.assertRaises(StreamDownloadError) as caught:
                self.owned._prepare_audio(stream, cancelled=lambda: next(checks))
        self.assertEqual(caught.exception.code, "cancelled")

    def test_busy_prefetch_retries_without_failed_dj_or_reopening_current_source(self):
        self.choose()
        music = self.music_ready()
        source, epoch = self.qt.source(), self.owned._epoch
        token, _ = self.job("dj_prefetch")
        self.downloads.completed.emit(token, None, "busy")
        self.assertTrue(self.owned._dj_prefetch_timer.isActive())
        self.assertEqual(self.owned.get_personal_dj_presentation()["state"], "loading")
        self.owned._retry_dj_prefetch()
        self.assertNotEqual(self.job("dj_prefetch")[0], token)
        self.assertEqual((self.qt.source(), self.owned._epoch), (source, epoch))
        self.assertIs(self.owned._prepared_audio, music)

    def test_worker_queue_full_retries_catalog_and_dj_without_unbounded_submissions(self):
        self.catalogs.accepted = self.downloads.accepted = False
        self.choose((segments(1)[1],) + segments(2))
        self.assertFalse(self.owned._fm_prefetch_pending)
        self.assertFalse(self.owned._dj_prefetch_requests)
        self.assertTrue(self.owned._dj_prefetch_timer.isActive())
        self.catalogs.accepted = self.downloads.accepted = True
        self.owned._retry_dj_prefetch()
        self.assertTrue(self.owned._fm_prefetch_pending)
        self.assertEqual(len(self.owned._dj_prefetch_requests), 1)
        jobs = len(self.catalogs.jobs), len(self.downloads.jobs)
        for _ in range(10):
            self.owned._retry_dj_prefetch()
        self.assertEqual((len(self.catalogs.jobs), len(self.downloads.jobs)), jobs)

    def test_loading_publication_reentrant_replacement_cannot_submit_or_retry_obsolete_speech(self):
        self.downloads.accepted = False
        def changed():
            if self.owned._dj_prefetch_requests:
                self.owned.play_queue((OnlineSong("qq", "chosen", "chosen"),))
        self.owned.changed.connect(changed)
        self.choose()
        self.assertEqual(self.owned.track.track_id, "owned:qq:chosen")
        self.assertFalse(self.owned._dj_prefetch_requests)
        self.assertFalse(self.owned._dj_prefetch_timer.isActive())
        self.assertFalse(any(token[0] == "dj_prefetch" for token, _ in self.downloads.jobs))

    def test_catalog_queue_retry_survives_no_speech_or_already_ready_speech(self):
        for has_speech in (False, True):
            with self.subTest(has_speech=has_speech):
                self.catalogs.accepted = False
                self.choose((segments(1)[1],) + (segments(2) if has_speech else ()))
                if has_speech:
                    self.perform("dj_prefetch")
                self.assertTrue(self.owned._dj_prefetch_timer.isActive())
                self.catalogs.accepted = True
                self.owned._retry_dj_prefetch()
                self.assertTrue(self.owned._fm_prefetch_pending)

    def test_transient_catalog_failure_retries_without_any_surface_poll(self):
        self.choose((segments(1)[1],))
        self.music_ready()
        old_source, old_epoch = self.qt.source(), self.owned._epoch
        failed, _ = self.job("fm_prefetch", self.catalogs)
        self.catalogs.completed.emit(failed, None, "network")
        self.assertTrue(self.owned._dj_prefetch_timer.isActive())
        self.owned._retry_dj_prefetch()
        retry, action = self.job("fm_prefetch", self.catalogs)
        self.assertNotEqual(retry, failed)
        self.catalogs.completed.emit(retry, action(), None)
        self.assertEqual(self.perform("dj_prefetch").stream.song_id, "dj:voice-7")
        self.assertEqual((self.qt.source(), self.owned._epoch), (old_source, old_epoch))

    def test_catalog_transient_retries_are_bounded_and_per_new_queue_tail(self):
        self.choose((segments(1)[1],))
        for _ in range(3):
            token, _ = self.job("fm_prefetch", self.catalogs)
            self.catalogs.completed.emit(token, None, "timeout")
            self.owned._retry_dj_prefetch()
        self.assertEqual(len(self.catalogs.jobs), 3)
        self.assertFalse(self.owned._fm_prefetch_pending)
        self.choose((segments(2)[1],))
        token, _ = self.job("fm_prefetch", self.catalogs)
        self.catalogs.completed.emit(token, None, "network")
        self.owned._retry_dj_prefetch()
        self.assertEqual(len(self.catalogs.jobs), 5)

    def test_old_catalog_completion_cannot_append_or_finish_new_retry(self):
        self.choose((segments(1)[1],))
        old, old_action = self.job("fm_prefetch", self.catalogs)
        stale = old_action()
        self.catalogs.completed.emit(old, None, "network")
        self.owned._retry_dj_prefetch()
        retry, _ = self.job("fm_prefetch", self.catalogs)
        self.catalogs.completed.emit(old, stale, None)
        self.assertEqual(self.owned.queue, (segments(1)[1],))
        self.assertTrue(self.owned._fm_prefetch_pending)
        self.assertEqual(self.job("fm_prefetch", self.catalogs)[0], retry)

    def test_auth_or_schema_catalog_failure_does_not_retry(self):
        for error, result in (("auth_required", None), ("response", None), (None, ())):
            with self.subTest(error=error):
                self.choose((segments(1)[1],))
                token, _ = self.job("fm_prefetch", self.catalogs)
                count = len(self.catalogs.jobs)
                self.catalogs.completed.emit(token, result, error)
                self.owned._retry_dj_prefetch()
                self.assertEqual(len(self.catalogs.jobs), count)

    def test_manual_next_waiting_for_catalog_stops_old_music_and_introduction(self):
        self.choose(segments(1))
        self.music_ready()
        self.perform("dj_stream", self.tasks)
        self.perform("dj_audio")
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.assertTrue(self.owned.send_playback_command("next"))
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.StoppedState)
        self.assertEqual(self.dj_qt.playbackState(), QMediaPlayer.PlaybackState.StoppedState)
        self.assertTrue(self.qt.source().isEmpty())
        self.assertTrue(self.dj_qt.source().isEmpty())
        self.assertFalse(self.owned.is_personal_dj_speaking())
        self.perform("fm_prefetch", self.catalogs)
        self.music_ready()
        voice = self.owned._dj_overlay_voices[0]
        pending = self.owned._dj_prefetch_requests[voice]
        _, action = next(job for job in self.downloads.jobs if job[0] == pending)
        self.downloads.completed.emit(pending, action(), None)
        self.assertEqual(self.owned.track.track_id, "owned:netease:song-7")
        self.assertTrue(self.owned.is_personal_dj_speaking())

    def test_old_multipart_download_cannot_restart_skipped_song_during_catalog_wait(self):
        with patch.object(self.owned, "_prefetch_fm"):
            self.choose(parts(1, 2))
            self.music_ready()
            self.perform("dj_stream", self.tasks)
            self.perform("dj_audio")
            old, action = self.job("dj_prefetch")
            prepared = action()
            self.dj_qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)
            self.assertFalse(self.owned._dj_source_ready)
            self.assertTrue(self.owned.send_playback_command("next"))
        self.downloads.completed.emit(old, prepared, None)
        self.assertIn(prepared.lease, self.cache.released)
        self.assertFalse(self.owned._dj_overlay_voices)
        self.assertTrue(self.qt.source().isEmpty())
        self.assertTrue(self.dj_qt.source().isEmpty())
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.StoppedState)

    def test_next_decoder_stop_cannot_override_reentrant_stop_or_source_selection(self):
        for action in ("stop", "replace"):
            with self.subTest(action=action):
                self.choose((segments(1)[1],))
                self.music_ready()
                epoch = self.owned._epoch
                catalogs = len([job for job in self.tasks.jobs if job[0][0] == "catalog"])
                replacement = []

                def replace_owner():
                    if (self.owned._epoch == epoch or
                            self.qt.playbackState() != QMediaPlayer.PlaybackState.StoppedState):
                        return
                    self.owned.changed.disconnect(replace_owner)
                    if action == "stop":
                        self.owned.stop()
                    else:
                        self.owned.play_queue((segments(9)[1],))
                        replacement.append(self.music_ready())

                self.owned.changed.connect(replace_owner)
                self.owned.send_playback_command("next")
                self.assertEqual(len([job for job in self.tasks.jobs if job[0][0] == "catalog"]), catalogs)
                if action == "stop":
                    self.assertEqual(self.owned.status, "idle")
                    self.assertFalse(self.owned.queue)
                    self.assertTrue(self.qt.source().isEmpty())
                else:
                    self.assertEqual(self.owned.track.track_id, "owned:netease:song-9")
                    self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
                    self.assertFalse(self.qt.source().isEmpty())
                    self.assertIs(self.owned._prepared_audio, replacement[0])
                    self.assertNotIn(replacement[0].lease, self.cache.released)

    def test_promoted_busy_future_download_uses_bounded_current_preparation_retry(self):
        self.choose()
        old, _ = self.job("dj_prefetch")
        self.owned.send_playback_command("next")
        self.downloads.completed.emit(old, None, "busy")
        self.assertTrue(self.owned._dj_overlay_voices)
        self.assertTrue(self.owned._dj_prefetch_timer.isActive())
        self.owned._retry_dj_prefetch()
        self.perform("dj_stream", self.tasks)
        self.perform("dj_audio")
        self.music_ready()
        self.assertTrue(self.owned.is_personal_dj_speaking())

    def test_catalog_retry_timer_cannot_fetch_old_fm_during_replacement_catalog(self):
        self.choose((segments(1)[1],))
        failed, _ = self.job("fm_prefetch", self.catalogs)
        self.catalogs.completed.emit(failed, None, "network")
        self.service.daily_recommendations = lambda source: (segments(9)[1],)
        self.owned.play_daily()
        count = len(self.catalogs.jobs)
        self.owned._retry_dj_prefetch()
        self.assertEqual(len(self.catalogs.jobs), count)
        self.assertEqual(self.service.personal_dj.call_count, 0)

    def test_pausing_during_catalog_retry_wait_does_not_revive_skipped_music(self):
        self.choose((segments(1)[1],))
        self.music_ready()
        self.owned.send_playback_command("next")
        failed, _ = self.job("fm_prefetch", self.catalogs)
        self.catalogs.completed.emit(failed, None, "network")
        self.owned.send_playback_command("toggle_play_pause")
        self.assertTrue(self.qt.source().isEmpty())
        self.owned._retry_dj_prefetch()
        self.perform("fm_prefetch", self.catalogs)
        self.music_ready()
        voice = self.owned._dj_overlay_voices[0]
        pending = self.owned._dj_prefetch_requests[voice]
        _, action = next(job for job in self.downloads.jobs if job[0] == pending)
        self.downloads.completed.emit(pending, action(), None)
        self.assertFalse(self.owned.track.is_playing)
        self.assertFalse(self.owned.is_personal_dj_speaking())
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)


if __name__ == "__main__":
    unittest.main()
