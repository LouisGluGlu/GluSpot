import math
import unittest
from types import SimpleNamespace

import numpy as np

from api.audio_analyzer import analyze_frequency_block
from local.sea_wave import SeaWaveState


class SpectrumSeaTests(unittest.TestCase):
    def analyze_tone(self, frequency):
        time = np.arange(2205) / 44100
        tone = .5 * np.sin(2 * np.pi * frequency * time)
        return analyze_frequency_block(np.column_stack((tone, tone)), 44100, np, [0, 0, 0])

    def test_frequency_maps_left_to_right(self):
        for frequency, left in ((100, True), (5000, False)):
            frame = self.analyze_tone(frequency)
            state = SeaWaveState()
            for i in range(30):
                state.update((frame.low, frame.mid, frame.high), i / 60, spectrum=frame.spectrum)
            left_peak = max(state.spectrum_displacement(i / 48, 0, 'medium') for i in range(16))
            right_peak = max(state.spectrum_displacement(i / 48, 0, 'medium') for i in range(33, 49))
            self.assertGreater(left_peak if left else right_peak, right_peak if left else left_peak)

    def test_latest_spectrum_survives_cached_band_sampling(self):
        from local.island_stack_visual import IslandStackVisual
        first = self.analyze_tone(100)
        second = self.analyze_tone(5000)
        class Runtime:
            def input_snapshot(self, **kwargs):
                # Model a cached-band hit, where the reader is not called.
                return SimpleNamespace()
        frame = [first]
        host = SimpleNamespace(
            data_provider=SimpleNamespace(get_audio_frame=lambda: frame[0]),
            _audio_reactive_runtime_for_current_settings=lambda: Runtime(),
            _effective_music_ambience_style=lambda: "sea_level",
        )
        IslandStackVisual._music_wave_input_snapshot(host, 1.0)
        frame[0] = second
        IslandStackVisual._music_wave_input_snapshot(host, 1.01)
        self.assertEqual(host._sea_spectrum, second.spectrum)
        self.assertNotEqual(first.spectrum, second.spectrum)

    def test_render_is_idempotent_at_one_timestamp(self):
        from PySide6.QtCore import QRectF
        from local.music_background_renderer import NativeMusicBackgroundRenderer
        from tests.test_music_background_renderer import PaintHost
        state = SeaWaveState()
        frame = self.analyze_tone(100)
        state.update((frame.low, frame.mid, frame.high), 1.0, spectrum=frame.spectrum)
        host = PaintHost({})
        host._sea_wave_state = state
        renderer = NativeMusicBackgroundRenderer(host)
        context = renderer.render_context("sea_level", expanded=True)
        results = [renderer._sea_level_surface(context, QRectF(0, 0, 440, 200), True) for _ in range(3)]
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])
        self.assertEqual(state.updated_at, 1.0)

    def test_vibration_dies_after_onset(self):
        state = SeaWaveState()
        state.update((0, 0, 0), 0)
        state.update((.5, 0, 0), .05)
        initial_energy = abs(state.vibration) + abs(state.vibration_velocity) / (math.tau * 4)
        self.assertGreater(initial_energy, 0)
        # No new onset while the oscillator rings down.
        for i in range(1, 31):
            state.update((0, 0, 0), .05 + i / 60)
        self.assertLess(abs(state.vibration) + abs(state.vibration_velocity) / (math.tau * 4), initial_energy * .2)
        for i in range(31, 43):
            state.update((0, 0, 0), .05 + i / 60)
        self.assertLess(abs(state.vibration) + abs(state.vibration_velocity) / (math.tau * 4), initial_energy * .2)


if __name__ == '__main__':
    unittest.main()
