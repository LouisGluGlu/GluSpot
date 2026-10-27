import unittest
import numpy as np

from api.audio_analyzer import analyze_frequency_block
from api.models import AudioFrame


class VocalAudioTests(unittest.TestCase):
    def test_centered_speech_band_and_rejected_bass_and_side(self):
        rate = 44100
        time = np.arange(2205) / rate
        def frame(freq, sign):
            wave = .5 * np.sin(2 * np.pi * freq * time)
            return analyze_frequency_block(np.column_stack((wave, wave * sign)), rate, np, [0, 0, 0])
        centered = frame(1000, 1)
        bass = frame(60, 1)
        side = frame(1000, -1)
        self.assertGreater(centered.vocal, .05)
        self.assertLess(bass.vocal, centered.vocal * .05)
        self.assertLess(side.vocal if side is not None else 0.0, centered.vocal * .05)
        mono = analyze_frequency_block(.5 * np.sin(2 * np.pi * 1000 * time), rate, np, [0, 0, 0])
        self.assertAlmostEqual(mono.vocal, centered.vocal, places=6)
        self.assertEqual(AudioFrame(0, 0, 0).vocal, 0)


if __name__ == '__main__':
    unittest.main()
