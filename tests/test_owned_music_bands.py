from __future__ import annotations

import unittest

import numpy as np
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat
from PySide6.QtWidgets import QApplication

from local.owned_audio_bridge import OwnedAudioBridge
from tests.test_owned_audio_bridge_handoff import Owner


class OwnedMusicBandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = Owner()
        self.position = 5
        self.owner.player.position = lambda: self.position
        self.owner.is_personal_dj_speaking = lambda: False
        self.bridge = OwnedAudioBridge(self.owner)
        self.addCleanup(self.bridge.close)

    def feed(self, hz, start=0, count=4096):
        wave = .7 * np.sin(2 * np.pi * hz * np.arange(count) / 44100) if hz else np.zeros(count)
        samples = np.repeat((wave * 32767).astype("<i2")[:, None], 2, axis=1)
        format_ = QAudioFormat()
        format_.setSampleRate(44100)
        format_.setChannelCount(2)
        format_.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self.bridge._buffer(QAudioBuffer(samples.tobytes(), format_, start))

    def test_music_frequencies_select_corresponding_bands(self):
        for hz, dominant in ((90, 0), (1000, 1), (5000, 2)):
            self.bridge._music_smooth[:] = [0.0, 0.0, 0.0]
            self.feed(hz)
            frame = self.bridge.get_music_audio_frame()
            values = (frame.low, frame.mid, frame.high)
            self.assertEqual(values.index(max(values)), dominant)
            self.assertGreater(values[dominant], .02)

    def test_decoder_ahead_does_not_drive_current_presentation(self):
        self.feed(1000, start=1000000)
        self.assertIsNone(self.bridge.get_music_audio_frame())
        self.position = 1005
        self.assertIsNotNone(self.bridge.get_music_audio_frame())
        self.position = 2000
        self.assertIsNone(self.bridge.get_music_audio_frame())

    def test_track_change_drops_the_previous_timeline(self):
        self.feed(1000)
        self.assertIsNotNone(self.bridge.get_music_audio_frame())
        self.owner._epoch = getattr(self.owner, "_epoch", 0) + 1
        self.assertIsNone(self.bridge.get_music_audio_frame())
        self.bridge._owner_changed()
        self.assertEqual(len(self.bridge._music_frames), 0)

    def test_dj_silence_does_not_block_music_bands(self):
        self.feed(90)
        self.assertIsNone(self.bridge.get_personal_dj_audio_frame())
        self.assertIsNotNone(self.bridge.get_music_audio_frame())


if __name__ == "__main__":
    unittest.main()
