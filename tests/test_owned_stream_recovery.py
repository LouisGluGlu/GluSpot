"""One authorized reopen of a genuinely stalled, seekable decoder stream."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from PySide6.QtMultimedia import QMediaPlayer

from api.online_music import StreamInfo
from tests import test_owned_stream_faults as fixtures


class OwnedStreamRecoveryTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.OwnedStreamFaultTests.setUpClass.__func__)
    setUp = fixtures.OwnedStreamFaultTests.setUp
    tearDown = fixtures.OwnedStreamFaultTests.tearDown
    start = fixtures.OwnedStreamFaultTests.start
    authorize = fixtures.OwnedStreamFaultTests.authorize
    fault = fixtures.OwnedStreamFaultTests.fault
    check = fixtures.OwnedStreamFaultTests.check

    def recover(self, position=2000):
        self.start(position=position, recovery_used=False)
        self.old_epoch = self.owned._epoch
        self.old_stream = next(token for token, _ in reversed(self.tasks.jobs) if token[0] == "stream")
        self.fault()
        self.check(101.1)
        self.owned._stream_fault_timer.stop()

    def load(self, duration=4000):
        with patch("local.owned_music_player.time.monotonic", return_value=101.2):
            self.authorize()
            self.qt.durationChanged.emit(duration)
            self.owned._check_stream_recovery()

    def test_midstream_reopens_same_song_once_with_lyrics_and_true_stop_clock(self):
        self.recover()
        self.assertEqual(self.owned.status, "recovering_stream")
        self.assertEqual(self.owned._epoch, self.old_epoch + 1)
        self.assertEqual(self.owned.index, 0)
        self.assertEqual(self.owned.track.track_id, "owned:netease:0")
        self.assertEqual(self.owned.track.position_seconds, 2)
        self.assertEqual(self.owned.track.timed_lyrics, self.timed_lyrics)
        self.assertIs(self.owned._lyric_payload, self.payload)
        self.assertFalse(self.owned.track.is_playing)
        self.load()
        self.assertEqual(self.qt.position(), 2000)
        self.assertEqual(self.owned.status, "recovering_stream")
        self.assertFalse(self.owned.track.is_playing)
        self.check(102)
        self.assertEqual(self.owned.status, "recovering_stream")
        self.qt.setPosition(2050)
        self.check(102.1)
        self.assertEqual(self.owned.status, "playing")
        self.assertTrue(self.owned.track.is_playing)
        self.assertEqual(self.owned.track.position_seconds, 2.05)
        self.assertEqual(self.owned.failure_reason, "")
        self.fault(at=103)
        self.check(104.1)
        self.assertEqual(self.owned.status, "playback_failed")
        self.assertEqual(self.owned._epoch, self.old_epoch + 1)
        self.assertEqual(len([token for token, _ in self.tasks.jobs if token[0] == "stream"]), 2)

    def test_projection_cannot_confirm_recovery_and_timeout_is_bounded(self):
        self.recover()
        self.load()
        self.owned.track = replace(self.owned.track, position_seconds=3)
        self.check(110)
        self.assertEqual(self.owned.status, "recovering_stream")
        self.check(113.11)
        self.assertEqual(self.owned.status, "playback_failed")
        self.assertEqual(self.owned.track.lyrics, self.lyrics)
        self.assertFalse(self.owned._stream_fault_timer.isActive())

    def test_authorization_timeout_and_late_completion_never_resume(self):
        self.recover()
        self.check(113.11)
        self.assertEqual(self.owned.status, "playback_failed")
        self.authorize()
        self.assertEqual(self.owned.status, "playback_failed")
        self.assertTrue(self.qt.url.isEmpty())
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)

    def test_old_authorization_cannot_replace_reopened_source(self):
        self.recover()
        self.tasks.completed.emit(self.old_stream, StreamInfo("netease", "0", "https://fixture.test/old", True), None)
        self.assertTrue(self.qt.url.isEmpty())
        self.assertEqual(self.owned.status, "recovering_stream")

    def test_nonseekable_zero_unknown_and_beyond_duration_streams_fail_without_reopen(self):
        for position, duration, seekable in ((2000, 4000, False), (0, 4000, True),
                (2000, 0, True), (4001, 4000, True)):
            with self.subTest(position=position, duration=duration, seekable=seekable):
                self.start(position=position, duration=duration, recovery_used=False)
                self.qt.seekable = seekable
                self.fault()
                self.check(101.1)
                self.assertEqual(self.owned.status, "playback_failed")
                self.assertIsNone(self.owned._stream_recovery)

    def test_seekable_loading_and_actual_seek_readback_are_required(self):
        self.recover()
        self.qt.seekable = False
        self.load()
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.StoppedState)
        self.assertEqual(self.qt.position(), 0)
        self.qt.seekable = True
        original = self.qt.setPosition
        self.qt.setPosition = lambda milliseconds: None
        self.check(101.3)
        self.assertEqual(self.owned._stream_recovery.phase, "seek")
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.StoppedState)
        self.qt.setPosition = original
        self.qt.setPosition(2000)
        self.check(101.4)
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PlayingState)

    def test_reauthorized_short_trial_cannot_seek_beyond_available_audio(self):
        self.recover()
        self.load(duration=1000)
        self.assertEqual(self.owned.status, "playback_failed")
        self.assertEqual(self.qt.position(), 0)
        self.assertEqual(self.owned.index, 0)

    def test_failed_authorization_invalid_url_and_decoder_error_are_terminal(self):
        for failure in ("auth", "url", "decoder", "invalid_media"):
            with self.subTest(failure=failure):
                self.recover()
                token = self.tasks.jobs[-1][0]
                if failure == "auth":
                    self.tasks.completed.emit(token, None, "auth_required")
                elif failure == "url":
                    self.tasks.completed.emit(token, StreamInfo("netease", "0", "file:///private", True), None)
                else:
                    self.load()
                    if failure == "decoder":
                        self.fault(at=101.3)
                    else:
                        self.qt.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.InvalidMedia)
                self.assertEqual(self.owned.status, "playback_failed")
                self.assertEqual(self.owned.track.lyrics, self.lyrics)
                self.assertIsNone(self.owned._stream_recovery)

    def test_pause_during_authorization_prevents_late_automatic_play(self):
        self.recover()
        self.assertTrue(self.owned.send_playback_command("toggle_play_pause"))
        self.load()
        self.assertIsNone(self.owned._stream_recovery)
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.assertFalse(self.owned.track.is_playing)
        self.assertEqual(self.qt.position(), 2000)
        self.assertTrue(self.owned.send_playback_command("toggle_play_pause"))
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PlayingState)

    def test_pause_after_seek_does_not_wait_for_progress_or_timeout(self):
        self.recover()
        self.load()
        self.owned.send_playback_command("toggle_play_pause")
        self.check(101.4)
        self.assertIsNone(self.owned._stream_recovery)
        self.assertEqual(self.owned.status, "playing")
        self.assertFalse(self.owned.track.is_playing)
        self.check(200)
        self.assertEqual(self.owned.status, "playing")

    def test_double_toggle_between_observer_ticks_restarts_real_player(self):
        self.recover()
        self.load()
        self.assertEqual(self.owned._stream_recovery.phase, "progress")
        self.assertTrue(self.owned.send_playback_command("toggle_play_pause"))
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.assertTrue(self.owned.send_playback_command("toggle_play_pause"))
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        self.qt.setPosition(2050)
        self.check(101.4)
        self.assertEqual(self.owned.status, "playing")
        self.assertIsNone(self.owned._stream_recovery)

    def test_seek_readback_offset_does_not_prove_subsequent_clock_progress(self):
        self.recover()
        original = self.qt.setPosition
        self.qt.setPosition = lambda value: original(value + 200 if value else value)
        self.load()
        self.assertEqual(self.qt.position(), 2200)
        self.assertEqual(self.owned.track.position_observed_seconds, 2.2)
        self.assertEqual(self.owned._stream_recovery.progress_position_ms, 2200)
        self.check(101.4)
        self.assertEqual(self.owned.status, "recovering_stream")
        self.assertFalse(self.owned.track.is_playing)
        original(2250)
        self.check(101.5)
        self.assertIsNone(self.owned._stream_recovery)
        self.assertEqual(self.owned.status, "playing")
        self.assertEqual(self.owned.track.position_observed_seconds, 2.25)

    def test_paused_recovery_records_actual_seek_readback(self):
        self.recover()
        self.owned.send_playback_command("toggle_play_pause")
        original = self.qt.setPosition
        self.qt.setPosition = lambda value: original(value + 200 if value else value)
        self.load()
        self.assertIsNone(self.owned._stream_recovery)
        self.assertEqual(self.owned.track.position_observed_seconds, 2.2)
        self.assertEqual(self.owned.track.position_seconds, 2.2)
        self.assertFalse(self.owned.track.is_playing)

    def test_synchronous_seek_error_cannot_play_invalidated_recovery(self):
        self.recover()
        operations = []
        original_seek, original_play = self.qt.setPosition, self.qt.play

        def seek_and_fail(milliseconds):
            operations.append(("seek", milliseconds))
            original_seek(milliseconds)
            if milliseconds:
                self.qt.errorOccurred.emit(QMediaPlayer.Error.FormatError, "safe-fixture")

        def record_play():
            operations.append(("play", self.qt.position()))
            original_play()

        self.qt.setPosition, self.qt.play = seek_and_fail, record_play
        self.load()
        self.assertEqual(operations, [("seek", 0), ("seek", 2000)])
        self.assertEqual(self.owned.status, "playback_failed")
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.assertEqual(self.owned._stream_epoch, 0)
        self.assertIsNone(self.owned._stream_recovery)
        self.assertFalse(self.owned.track.is_playing)
        self.check(200)
        self.assertEqual(self.owned.status, "playback_failed")

    def test_changed_signal_source_switch_cancels_old_decoder_reset(self):
        self.start(position=2000, recovery_used=False)
        current_epoch = self.owned._epoch
        switched = False

        def switch_source():
            nonlocal switched
            if self.owned.status == "recovering_stream" and not switched:
                switched = True
                self.owned.play_queue((self.songs[1],))
                self.authorize()

        self.owned.changed.connect(switch_source)
        self.fault()
        self.check(101.1)
        self.assertEqual(self.owned.track.track_id, "owned:netease:1")
        self.assertEqual(self.owned._epoch, current_epoch + 2)
        self.assertEqual(self.owned.status, "playing")
        self.assertFalse(self.qt.url.isEmpty())
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(len([token for token, _ in self.tasks.jobs if token[0] == "stream"]), 2)
        self.owned.changed.disconnect(switch_source)

    def test_explicit_recovery_seek_error_cannot_override_terminal_status(self):
        self.recover()
        self.load()
        original_seek = self.qt.setPosition

        def seek_and_fail(milliseconds):
            original_seek(milliseconds)
            self.qt.errorOccurred.emit(QMediaPlayer.Error.FormatError, "safe-fixture")

        self.qt.setPosition = seek_and_fail
        self.assertFalse(self.owned.seek_playback_position(1))
        self.assertEqual(self.owned.status, "playback_failed")
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)

    def test_explicit_seek_fences_old_automatic_target(self):
        self.recover()
        self.assertFalse(self.owned.seek_playback_position(1, "wrong-track"))
        self.assertFalse(self.owned.seek_playback_position(1))
        self.load()
        self.assertTrue(self.owned.seek_playback_position(1))
        self.assertIsNone(self.owned._stream_recovery)
        self.check(103)
        self.assertEqual(self.qt.position(), 1000)

    def test_source_next_stop_and_close_cancel_late_recovery(self):
        for action in ("catalog", "next", "stop", "close"):
            with self.subTest(action=action):
                self.owned._closed = False
                self.recover()
                token = self.tasks.jobs[-1][0]
                if action == "catalog":
                    self.service.daily_recommendations = lambda source: self.songs
                    self.owned.play_daily()
                elif action == "next":
                    self.owned.send_playback_command("next")
                else:
                    getattr(self.owned, action)()
                current = self.owned.track
                self.tasks.completed.emit(token, StreamInfo("netease", "0", "https://fixture.test/late", True), None)
                self.check(200)
                self.assertEqual(self.owned.track, current)
                self.assertIsNone(self.owned._stream_recovery)

    def test_decoder_epoch_change_is_announced_before_source_reset(self):
        seen = []
        self.owned.changed.connect(lambda: seen.append((self.owned._epoch, self.qt.position())))
        self.recover()
        self.assertIn((self.old_epoch + 1, 2000), seen)


if __name__ == "__main__":
    unittest.main()
