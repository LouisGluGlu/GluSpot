"""Both decoder buffers enter one authenticated PCM clock without master gain."""
from types import SimpleNamespace
import unittest

import numpy as np
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat
from PySide6.QtWidgets import QApplication

from local.owned_audio_bridge import OwnedAudioBridge
from tests.test_owned_audio_bridge_handoff import Owner, Media, SignalAudio


class PersonalDjPcmMixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = Owner()
        self.owner.dj_player = Media()
        self.owner.dj_player.position = lambda: 0
        self.owner.dj_audio = SignalAudio()
        self.owner._dj_overlay_voices = ("voice",)
        self.owner._dj_music_start_seconds = 0.
        self.owner._dj_music_end_seconds = 0.
        self.owner.voice_token = (4, 1, "voice")
        self.owner.personal_dj_audio_token = lambda: self.owner.voice_token
        self.owner.is_personal_dj_speaking = lambda: bool(self.owner._dj_overlay_voices)
        self.bridge = OwnedAudioBridge(self.owner)
        self.controller = SimpleNamespace(packets=[], failures=[], changes=[], accepted=True)
        self.controller.submit_pcm = lambda data, epoch: self.controller.packets.append((data, epoch)) or True
        self.controller.accepts_pcm = lambda epoch: self.controller.accepted
        self.controller.pcm_failed = lambda reason, epoch: self.controller.failures.append((reason, epoch))
        self.controller.media_changed = lambda token, **kwargs: self.controller.changes.append((token, kwargs))
        self.bridge.attach(self.controller)
        self.token = self.bridge.media_token()
        self.bridge._owner_changed()

    def tearDown(self): self.bridge.close()

    def pcm(self, value, frames=441, start=0):
        format_ = QAudioFormat()
        format_.setSampleRate(44100); format_.setChannelCount(2)
        format_.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        return QAudioBuffer(np.full((frames, 2), value, dtype="<i2").tobytes(), format_, start)

    def feed(self): self.assertTrue(self.bridge.set_direct(True, 10, muted=False))

    def test_music_arriving_before_voice_is_held_then_mixed_once_at_twenty_percent(self):
        self.feed()
        self.bridge._buffer(self.pcm(10000))
        self.assertEqual(self.controller.packets, [])
        self.bridge._dj_buffer(self.pcm(5000))
        self.assertEqual(len(self.controller.packets), 1)
        self.assertTrue(np.all(np.frombuffer(self.controller.packets[0][0], dtype="<i2") == 7000))
        self.assertEqual(self.controller.packets[0][1], 10)
        self.assertFalse(self.owner.audio.isMuted())
        self.assertFalse(self.owner.dj_audio.isMuted())

    def test_speech_silence_holes_remain_ducked_and_pcm_clips_safely(self):
        self.feed()
        self.bridge._buffer(self.pcm(30000, frames=882))
        self.bridge._dj_buffer(self.pcm(30000, frames=441))
        self.bridge._dj_buffer(self.pcm(0, frames=220, start=15000))
        result = np.frombuffer(b"".join(data for data, _ in self.controller.packets), dtype="<i2").reshape(-1, 2)
        self.assertTrue(np.all(result[:441] == 32767))
        self.assertTrue(np.all(result[441:] == 6000))
        self.assertEqual(self.controller.failures, [])

    def test_cancel_clips_decoded_future_voice_and_restores_remaining_music_in_same_epoch(self):
        self.feed()
        self.bridge._dj_buffer(self.pcm(5000, frames=882))
        self.owner._dj_overlay_voices = ()
        self.owner._dj_music_end_seconds = .01
        self.owner.voice_token = (4, 2, "")
        self.bridge._owner_changed()
        self.bridge._buffer(self.pcm(10000, frames=882))
        result = np.frombuffer(b"".join(data for data, _ in self.controller.packets), dtype="<i2").reshape(-1, 2)
        self.assertTrue(np.all(result[:441] == 7000))
        self.assertTrue(np.all(result[441:] == 10000))
        self.assertEqual(self.controller.changes, [])

    def test_both_private_outputs_mute_together_and_restore_only_owned_changes(self):
        self.feed()
        self.assertTrue(self.bridge.commit_direct_mute(10))
        self.assertTrue(self.owner.audio.isMuted())
        self.assertTrue(self.owner.dj_audio.isMuted())
        self.assertTrue(self.bridge.set_direct(False, 10))
        self.assertFalse(self.owner.audio.isMuted())
        self.assertFalse(self.owner.dj_audio.isMuted())
        self.owner.dj_audio.setMuted(True)
        self.feed(); self.bridge.commit_direct_mute(10)
        self.bridge.set_direct(False, 10)
        self.assertTrue(self.owner.dj_audio.isMuted())

    def test_track_epoch_change_discards_both_pending_pcm_timelines(self):
        self.feed()
        self.bridge._buffer(self.pcm(10000))
        self.owner._epoch += 1
        self.owner.track.track_id = "new-song"
        self.owner.changed.emit()
        self.bridge._dj_buffer(self.pcm(5000))
        self.assertEqual(self.controller.packets, [])
        self.assertFalse(self.bridge._music_pcm)
        self.assertEqual(self.controller.changes[-1][0], self.bridge.media_token())

    def test_non_direct_frequency_sampling_never_accumulates_raw_voice_pcm(self):
        for i in range(220): self.bridge._dj_buffer(self.pcm(5000, start=i * 10000))
        self.assertFalse(self.bridge._voice_pcm)
        self.assertLessEqual(len(self.bridge._dj_frames), 96)
        self.assertEqual(self.controller.failures, [])

    def test_delayed_multipart_source_keeps_ducked_pcm_flow_beyond_two_seconds(self):
        self.feed()
        self.owner._dj_source_ready = False
        self.owner._dj_music_gain = .65
        self.owner._dj_music_end_seconds = 0.
        self.owner.voice_token = (4, 2, "next-part")
        self.owner.is_personal_dj_speaking = lambda: False
        self.bridge._owner_changed()
        for index in range(250):
            self.bridge._buffer(self.pcm(10000, start=index * 10000))
        output = np.frombuffer(b"".join(data for data, _ in self.controller.packets), dtype="<i2")
        self.assertEqual(len(output), 250 * 441 * 2)
        self.assertTrue(np.all(output == 2000))
        self.assertEqual(self.controller.failures, [])
        self.assertEqual(self.bridge._music_pcm_bytes, 0)

    def test_music_restores_over_one_second_on_the_same_pcm_clock(self):
        self.feed()
        self.owner._dj_overlay_voices = ()
        self.owner._dj_music_end_seconds = .01
        self.owner.voice_token = (4, 2, "")
        self.owner.personal_dj_music_gain_envelopes = lambda: ((.01, 1.01, 1.01),)
        self.bridge._owner_changed()
        for index in range(112):
            self.bridge._buffer(self.pcm(10000, start=index * 10000))
        output = np.frombuffer(b"".join(data for data, _ in self.controller.packets), dtype="<i2").reshape(-1,2)
        self.assertEqual(int(output[441,0]), 2000)
        self.assertEqual(int(output[round(.51*44100),0]), 6000)
        self.assertEqual(int(output[round(1.01*44100),0]), 10000)
        self.assertEqual(self.controller.failures, [])

    def test_queued_preview_preparation_uses_one_restore_envelope(self):
        self.feed()
        self.owner._dj_source_ready = False
        self.owner._dj_music_gain = .65
        self.owner._dj_restore_start_seconds = 0.
        self.owner._dj_music_end_seconds = 0.
        self.owner.voice_token = (4, 2, "preview")
        self.owner.is_personal_dj_speaking = lambda: False
        self.owner.personal_dj_music_gain_envelopes = lambda: ((0., 1., 1.),)
        self.bridge._owner_changed()
        self.bridge._buffer(self.pcm(10000, frames=44100))
        output = np.frombuffer(b"".join(data for data, _ in self.controller.packets), dtype="<i2").reshape(-1,2)
        self.assertEqual(int(output[0,0]), 2000)
        self.assertEqual(int(output[22050,0]), 6000)
        self.assertGreater(int(output[-1,0]), 9990)

    def test_paused_group_preview_never_adds_future_duck_to_resumed_music(self):
        self.feed()
        self.bridge._voice_intervals.clear()
        self.owner._dj_overlay_is_preview = True
        from PySide6.QtMultimedia import QMediaPlayer
        state = [QMediaPlayer.PlaybackState.PausedState]
        self.owner.player.playbackState = lambda: state[0]
        self.owner.voice_token = (4, 2, "preview")
        self.bridge._owner_changed()
        self.bridge._dj_buffer(self.pcm(5000))
        self.assertFalse(self.bridge._voice_intervals)
        self.owner._dj_overlay_voices = ()
        self.owner._dj_overlay_is_preview = False
        self.owner.voice_token = (4, 3, "")
        self.bridge._owner_changed()
        state[0] = QMediaPlayer.PlaybackState.PlayingState
        self.bridge._owner_changed()
        self.bridge._buffer(self.pcm(10000))
        output = np.frombuffer(self.controller.packets[-1][0], dtype="<i2")
        self.assertTrue(np.all(output == 10000))

    def test_preview_pause_holds_future_music_then_remaps_live_voice_once_on_resume(self):
        from PySide6.QtMultimedia import QMediaPlayer
        self.feed()
        self.bridge._voice_intervals.clear()
        self.owner._dj_overlay_is_preview = True
        state = [QMediaPlayer.PlaybackState.PausedState]
        self.owner.player.playbackState = lambda: state[0]
        self.owner.voice_token = (4, 2, "preview", 1)
        self.owner._dj_music_start_seconds = 0.
        self.bridge._owner_changed()
        self.bridge._buffer(self.pcm(10000, start=1000000))
        self.assertEqual(self.controller.packets, [])
        self.bridge._dj_buffer(self.pcm(5000, start=500000))
        self.assertEqual(len(self.controller.packets), 1)
        self.assertTrue(np.all(np.frombuffer(self.controller.packets[0][0], dtype="<i2") == 5000))
        state[0] = QMediaPlayer.PlaybackState.PlayingState
        self.owner._dj_music_start_seconds = .5
        self.owner._dj_music_mix_start_seconds = 1.
        self.owner.voice_token = (4, 2, "preview", 2)
        self.bridge._owner_changed()
        self.assertEqual(len(self.controller.packets), 1)
        self.bridge._dj_buffer(self.pcm(5000, start=500000))
        self.assertEqual(len(self.controller.packets), 2)
        self.assertTrue(np.all(np.frombuffer(self.controller.packets[1][0], dtype="<i2") == 7000))
        self.assertFalse(self.bridge._music_pcm)
        self.assertFalse(self.bridge._voice_pcm)
        self.assertEqual(self.controller.changes, [])

    def test_preview_ending_or_cancelling_cannot_flush_paused_future_music(self):
        from PySide6.QtMultimedia import QMediaPlayer
        self.feed()
        self.bridge._voice_intervals.clear()
        state = [QMediaPlayer.PlaybackState.PausedState]
        self.owner.player.playbackState = lambda: state[0]
        self.owner._dj_overlay_is_preview = True
        self.owner.voice_token = (4, 2, "preview")
        self.bridge._owner_changed()
        self.bridge._buffer(self.pcm(10000))
        self.bridge._dj_buffer(self.pcm(5000))
        self.assertEqual(len(self.controller.packets), 1)
        self.owner._dj_overlay_is_preview = False
        self.owner._dj_overlay_voices = ()
        self.owner.voice_token = (4, 3, "")
        self.bridge._owner_changed()
        self.bridge._buffer(self.pcm(10000, start=10000))
        self.assertEqual(len(self.controller.packets), 1)
        self.assertEqual(self.bridge._music_pcm_bytes, 2 * 441 * 4)
        state[0] = QMediaPlayer.PlaybackState.PlayingState
        self.bridge._owner_changed()
        self.assertEqual(self.bridge._music_pcm_bytes, 0)
        self.assertTrue(np.all(np.frombuffer(b"".join(data for data, _ in self.controller.packets[1:]), dtype="<i2") == 10000))


if __name__ == "__main__": unittest.main()
