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

    def test_separated_spectral_peaks_remain_distinct_and_scale(self):
        state = SeaWaveState()
        spectrum = [0.0] * 24
        spectrum[5] = spectrum[15] = 1.0
        for i in range(60):
            state.update((0.4, 0.4, 0.4), i / 60, spectrum=spectrum)
        for mode in ("gentle", "medium", "strong"):
            heights = [state.spectrum_displacement(i / 23, 0, mode) for i in (5, 10, 15)]
            self.assertGreater(min(heights[0], heights[2]), heights[1] + 5)
        heights = [state.spectrum_displacement(5 / 23, 0, mode)
                   for mode in ("gentle", "medium", "strong")]
        self.assertLess(heights[0], heights[1])
        self.assertLess(heights[1], heights[2])
        self.assertLess(max(heights), 48)

    def test_latest_spectrum_survives_cached_band_sampling(self):
        from local.audio_reactive_engine import AudioReactiveRuntime
        from local.island_stack_visual import IslandStackVisual
        first = self.analyze_tone(100)
        second = self.analyze_tone(5000)
        runtime = AudioReactiveRuntime()
        frame = [first]
        reads = []

        def read_frame():
            reads.append(frame[0])
            return frame[0]

        host = SimpleNamespace(
            data_provider=SimpleNamespace(get_audio_frame=read_frame),
            _audio_reactive_runtime_for_current_settings=lambda: runtime,
            _effective_music_ambience_style=lambda: "sea_level",
        )
        initial = IslandStackVisual._music_wave_input_snapshot(host, 1.0)
        frame[0] = second
        cached = IslandStackVisual._music_wave_input_snapshot(host, 1.01)
        self.assertEqual(initial.band_source, "live_frame")
        self.assertEqual(cached.band_source, "cache")
        self.assertEqual(cached.sampled_bands, initial.sampled_bands)
        self.assertEqual(reads, [first, second])
        self.assertEqual(host._sea_vocal_sample, second.vocal)
        self.assertEqual(host._sea_spectrum, second.spectrum)
        self.assertEqual(host._sea_vocal_spectrum, second.vocal_spectrum)
        self.assertNotEqual(first.spectrum, second.spectrum)
        self.assertNotEqual(first.vocal, second.vocal)
        self.assertNotEqual(first.vocal_spectrum, second.vocal_spectrum)

        # A missing/failed capture must clear all features, even on a cache hit.
        for missing in (True, False):
            with self.subTest(missing=missing):
                frame[0] = second
                IslandStackVisual._music_wave_input_snapshot(host, 1.02)

                def unavailable():
                    if missing:
                        return None
                    raise RuntimeError("capture unavailable")

                host.data_provider.get_audio_frame = unavailable
                snapshot = IslandStackVisual._music_wave_input_snapshot(host, 1.03)
                self.assertEqual(snapshot.band_source, "cache")
                self.assertEqual(host._sea_vocal_sample, 0.0)
                self.assertEqual(host._sea_spectrum, ())
                self.assertEqual(host._sea_vocal_spectrum, ())
                host.data_provider.get_audio_frame = read_frame

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


class SeaProductDefaultTests(unittest.TestCase):
    def test_product_defaults_are_spectrum_and_medium(self):
        import os
        from unittest.mock import patch
        from local import sea_wave
        self.assertEqual(sea_wave.SEA_SHAPE_MODE, "spectrum")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GLUSPOT_SEA_INTENSITY", None)
            self.assertEqual(sea_wave.sea_intensity(None), "medium")
