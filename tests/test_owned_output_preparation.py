"""Receiver preparation preserves decoder positions and explicit play intent."""
import unittest
import time

from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication
from local.owned_music_player import _StreamRecovery

import tests.test_personal_dj_playback as dj_fixture


class OwnedOutputPreparationTests(unittest.TestCase):
    setUp = dj_fixture.DJPlaybackTests.setUp
    run_latest = dj_fixture.DJPlaybackTests.run_latest
    dj = dj_fixture.DJPlaybackTests.dj
    fm = dj_fixture.DJPlaybackTests.fm
    stream = dj_fixture.DJPlaybackTests.stream
    lyrics = dj_fixture.DJPlaybackTests.lyrics
    start = dj_fixture.DJPlaybackTests.start
    start_fm = dj_fixture.DJPlaybackTests.start_fm

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_loaded_song_stays_at_its_original_position_until_pcm_ready(self):
        self.start_fm()
        self.qt.position = 7300
        self.owned.set_output_preparing(True)
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.assertTrue(self.owned._output_prepare_resume)
        self.owned._play_music()
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.assertEqual(self.qt.position, 7300)
        self.owned.set_output_preparing(False)
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(self.qt.position, 7300)

    def test_async_new_song_cannot_start_its_intro_during_authentication(self):
        self.owned.play_personal_fm()
        self.run_latest(self.tasks, "catalog")
        self.owned.set_output_preparing(True)
        self.run_latest(self.tasks, "stream")
        self.assertNotEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(self.qt.position, 0)
        self.assertTrue(self.owned._output_prepare_resume)
        self.owned.set_output_preparing(False)
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        self.assertEqual(self.qt.position, 0)

    def test_user_pause_during_preparation_cancels_automatic_resume(self):
        self.start_fm()
        self.owned.set_output_preparing(True)
        self.assertTrue(self.owned.send_playback_command("toggle_play_pause"))
        self.assertFalse(self.owned._output_prepare_resume)
        self.owned.set_output_preparing(False)
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.assertFalse(self.owned._autoplay_after_load)
        self.assertTrue(self.owned.send_playback_command("toggle_play_pause"))
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PlayingState)

    def test_user_resume_during_preparation_waits_for_native_input(self):
        self.start_fm()
        self.owned.send_playback_command("toggle_play_pause")
        self.owned.set_output_preparing(True)
        self.assertFalse(self.owned._output_prepare_resume)
        self.owned.send_playback_command("toggle_play_pause")
        self.assertTrue(self.owned._output_prepare_resume)
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.owned.set_output_preparing(False)
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PlayingState)

    def test_dj_music_and_voice_resume_from_their_same_positions(self):
        self.start()
        self.qt.position, self.dj_qt.position = 2300, 2300
        self.assertTrue(self.owned.is_personal_dj_speaking())
        self.owned.set_output_preparing(True)
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.assertEqual(self.dj_qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.owned._try_start_dj_overlay()
        self.assertEqual(self.dj_qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.owned.set_output_preparing(False)
        self.assertEqual((self.qt.position, self.dj_qt.position), (2300, 2300))
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        self.assertTrue(self.owned.is_personal_dj_speaking())

    def test_prepared_dj_sources_cannot_autoplay_until_input_ready(self):
        self.owned.play_personal_dj()
        self.run_latest(self.tasks, "catalog")
        self.owned.set_output_preparing(True)
        self.run_latest(self.tasks, "stream")
        self.assertNotEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        self.assertNotEqual(self.dj_qt.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        self.owned.set_output_preparing(False)
        self.assertTrue(self.owned.is_personal_dj_speaking())

    def test_dj_user_pause_while_held_cannot_restart_speech_on_ready(self):
        self.start()
        self.owned.set_output_preparing(True)
        self.owned.send_playback_command("toggle_play_pause")
        self.owned.set_output_preparing(False)
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.assertEqual(self.dj_qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.assertFalse(self.owned.is_personal_dj_speaking())

    def test_new_paused_source_does_not_inherit_previous_resume_intent(self):
        self.start_fm()
        self.owned.set_output_preparing(True)
        self.owned._load_song(autoplay=False)
        self.owned.set_output_preparing(True)
        self.run_latest(self.tasks, "stream")
        self.owned.set_output_preparing(False)
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)

    def test_output_hold_and_stream_recovery_share_user_pause_intent(self):
        self.start_fm()
        self.qt.pause()
        self.owned._stream_recovery = _StreamRecovery(self.owned._epoch, 0, time.monotonic() + 10,
            "fixture", phase="seek", resume=True)
        self.owned.set_output_preparing(True)
        self.assertTrue(self.owned._output_prepare_resume)
        self.owned.send_playback_command("toggle_play_pause")
        self.assertFalse(self.owned._stream_recovery.resume)
        self.owned._check_stream_recovery()
        self.assertFalse(self.owned._output_prepare_resume)
        self.owned.set_output_preparing(False)
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)

    def test_ready_during_recovery_seek_waits_for_actual_target_readback(self):
        self.start_fm()
        self.qt.pause()
        self.owned._stream_recovery = _StreamRecovery(self.owned._epoch, 5000, time.monotonic() + 10,
            "fixture", phase="seek", resume=True)
        self.owned.set_output_preparing(True)
        self.owned.set_output_preparing(False)
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.owned._check_stream_recovery()
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        # This legacy fake normally stores a numeric position, while recovery
        # deliberately requires the real QMediaPlayer.position() readback.
        self.qt.position = lambda: 5000
        self.owned._check_stream_recovery()
        self.assertEqual(self.qt.playbackState(), QMediaPlayer.PlaybackState.PlayingState)


if __name__ == "__main__":
    unittest.main()
