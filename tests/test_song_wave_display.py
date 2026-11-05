"""Per-song wave scaling for the live island ripple and compact bars."""
from __future__ import annotations

import math
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.models import AudioFrame
from api.providers import EmptyIslandDataProvider
from local.island_stack_visual import IslandStackVisual
from local.music_background_renderer import NativeMusicBackgroundRenderer
from local.sea_wave import SeaWaveState
from local.song_wave_display import (
    DISPLAY_HIGH,
    DISPLAY_LOW,
    MIN_SPAN,
    REFERENCE_AMPLITUDE,
    SongWaveDisplay,
    map_song_wave_display,
    song_wave_loudness,
)
from tests.test_music_background_renderer import PaintHost


def _feed(display, track, samples, start=0.0):
    level = display.level
    for index, sample in enumerate(samples):
        level = display.observe(track, sample, start + index / 30.0, playing=True)
    return level


class SongWaveDisplayTests(unittest.TestCase):
    def test_loudness_matches_the_absolute_ripple_target(self):
        self.assertAlmostEqual(song_wave_loudness(0.0004), math.sqrt(0.0004))
        self.assertAlmostEqual(song_wave_loudness(1.0), 1.0)
        self.assertAlmostEqual(song_wave_loudness(0.04, 0.4), min(1.0, math.sqrt(0.04) + 1.4 * 0.38))
        self.assertIsNone(song_wave_loudness(None))
        self.assertEqual(song_wave_loudness(0.8, playing=False), 0.0)
        self.assertIsNone(song_wave_loudness(float("nan")))

    def test_flat_song_has_a_finite_minimum_and_does_not_divide_by_zero(self):
        for low, high, sample in ((0.2, 0.2, 0.2), (0.5, 0.5 + 1e-12, 0.5), (0.0, 0.0, 0.0)):
            shown = map_song_wave_display(sample, low, high)
            self.assertTrue(math.isfinite(shown))
            self.assertGreaterEqual(shown, DISPLAY_LOW)
            self.assertLessEqual(shown, DISPLAY_HIGH)
        self.assertEqual(map_song_wave_display(0.0, 0.0, 0.0), DISPLAY_LOW)
        self.assertAlmostEqual(map_song_wave_display(0.2, 0.2, 0.2), 0.575, places=3)
        silence = SongWaveDisplay()
        self.assertAlmostEqual(_feed(silence, "song", [0.0] * 40), DISPLAY_LOW, places=3)
        display = SongWaveDisplay()
        display.active = True
        display.level = 1.0
        self.assertTrue(math.isfinite(display.displacement_gain(0.0)))
        self.assertLessEqual(display.displacement_gain(1e-12), 160.0)

    def test_dynamic_song_stretches_its_own_low_and_high(self):
        display = SongWaveDisplay()
        high = _feed(display, "song", [0.1] * 40 + [0.8] * 80)
        self.assertGreater(high, 0.90)
        low = _feed(display, "song", [0.1] * 80, start=4.0)
        self.assertLess(low, 0.25)
        self.assertGreaterEqual(low, DISPLAY_LOW)

    def test_one_spike_does_not_flatten_the_rest_of_the_song(self):
        display = SongWaveDisplay()
        _feed(display, "song", [0.30] * 80)
        before = display.percentiles()
        display.observe("song", 1.0, 3.0, playing=True)
        after = display.percentiles()
        self.assertLess(after[1] - after[0], MIN_SPAN)
        self.assertAlmostEqual(after[0], before[0], places=5)
        settled = _feed(display, "song", [0.30] * 80, start=3.1)
        self.assertGreater(settled, 0.45)
        self.assertLess(settled, 0.70)

    def test_track_change_recomputes_and_eases(self):
        display = SongWaveDisplay()
        _feed(display, "loud", [0.15] * 30 + [0.85] * 30)
        quiet_on_loud_song = _feed(display, "loud", [0.15] * 80, start=3.0)
        self.assertLess(quiet_on_loud_song, 0.35)
        stepped = display.observe("quiet", 0.15, 3.0 + 80 / 30.0, playing=True)
        self.assertLess(stepped, 0.45)
        settled = _feed(display, "quiet", [0.15] * 80, start=3.0 + 81 / 30.0)
        self.assertGreater(settled, 0.45)

    def test_pause_and_missing_meter_do_not_learn_or_scale(self):
        display = SongWaveDisplay()
        _feed(display, "song", [0.6] * 40)
        total = display.total
        level = display.level
        for index in range(20):
            display.observe("song", 0.0, 2.0 + index / 30.0, playing=False)
        self.assertEqual(display.total, total)
        self.assertFalse(display.active)
        self.assertEqual(display.level, level)
        self.assertEqual(display.displacement_gain(0.4), 1.0)
        display.observe("song", None, 3.0, playing=True)
        self.assertFalse(display.active)
        self.assertEqual(display.total, total)
        display.observe("", 0.9, 3.1, playing=True)
        self.assertFalse(display.active)
        self.assertEqual(display.total, 0)

    def test_bars_keep_ratios_and_silence_stays_empty(self):
        display = SongWaveDisplay()
        display.active = False
        self.assertEqual(display.bar_levels((0.2, 0.5, 0.0, 0.1)), (0.2, 0.5, 0.0, 0.1))
        display.active = True
        display.level = 0.8
        shown = display.bar_levels((0.2, 0.5, 0.0, 0.1))
        for actual, expected in zip(shown, (0.32, 0.8, 0.0, 0.16)):
            self.assertAlmostEqual(actual, expected, places=6)
        self.assertEqual(display.bar_levels((0.0, 0.0, 0.0, 0.0)), (0.0, 0.0, 0.0, 0.0))
        self.assertTrue(all(math.isfinite(value) for value in display.bar_levels((float("nan"), -1, 2))))

    def test_full_scale_gain_uses_the_reference_height(self):
        display = SongWaveDisplay()
        display.active = True
        display.level = 1.0
        self.assertAlmostEqual(display.displacement_gain(REFERENCE_AMPLITUDE), 1.0, places=6)
        display.level = DISPLAY_LOW
        self.assertAlmostEqual(display.displacement_gain(REFERENCE_AMPLITUDE), DISPLAY_LOW, places=6)


class SongWaveSurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _warm(peak, vocal=0.0):
        state = SeaWaveState()
        for tick in range(180):
            state.update_ripple(peak, tick / 30.0, vocal=vocal)
        return state

    @staticmethod
    def _crest(host, state):
        host._sea_wave_state = state
        renderer = NativeMusicBackgroundRenderer(host)
        rect = QRectF(0.0, 0.0, 520.0, 136.0)
        points = renderer._sea_level_surface(renderer.render_context("sea_level", expanded=True), rect, True)
        waterline = rect.top() + rect.height() * 0.495
        return max(abs(point.y() - waterline) for point in points), tuple(state._ripple_history)

    def test_unadapted_vocal_crest_stays_in_its_absolute_band(self):
        crest, _history = self._crest(PaintHost({}), self._warm(0.64, vocal=0.25))
        self.assertGreater(crest, 16.5)
        self.assertLess(crest, 17.5)

    def test_active_song_makes_a_quiet_pose_obvious_without_rewriting_history(self):
        state = self._warm(0.0004)
        raw, history = self._crest(PaintHost({}), state)
        self.assertLess(raw, 1.0)
        host = PaintHost({})
        display = host._song_wave_display = SongWaveDisplay()
        display.active = True
        display.level = 1.0
        adapted, after = self._crest(host, state)
        self.assertEqual(after, history)
        self.assertGreater(adapted, 12.0)
        self.assertLess(adapted, 44.0)
        self.assertGreater(adapted, raw * 8.0)

    def test_update_wave_keeps_absolute_ripple_and_learns_the_track(self):
        from local.audio_reactive_engine import AudioReactiveRuntime
        runtime = AudioReactiveRuntime()
        frame = [AudioFrame(0.2, 0.1, 0.05, peak=0.04)]
        host = SimpleNamespace(
            data_provider=SimpleNamespace(get_audio_frame=lambda: frame[0], has_live_audio=lambda: True),
            media_is_playing=True,
            media_track_id="song-a",
            wave_values=[0.1] * 36,
            ambient_phase=1.25,
            _audio_reactive_runtime_for_current_settings=lambda: runtime,
            _effective_music_ambience_style=lambda: "sea_level",
            _invalidate_music_reactive_snapshot=Mock(),
        )
        host._music_wave_input_snapshot = lambda now: IslandStackVisual._music_wave_input_snapshot(host, now)
        host._music_wave_update_plan = lambda now: IslandStackVisual._music_wave_update_plan(host, now)
        reference = SeaWaveState()
        for tick in range(40):
            with patch("local.island_stack_visual.time.monotonic", return_value=tick / 30.0):
                IslandStackVisual._update_wave_values(host)
            reference.update_ripple(0.04, tick / 30.0)
        self.assertAlmostEqual(host._sea_wave_state.ripple_level, reference.ripple_level, places=6)
        self.assertTrue(host._song_wave_display.active)
        self.assertGreater(host._song_wave_display.level, DISPLAY_LOW)
        frame[0] = AudioFrame(0.0, 0.0, 0.0, peak=0.64)
        with patch("local.island_stack_visual.time.monotonic", return_value=2.0):
            IslandStackVisual._update_wave_values(host)
        reference.update_ripple(0.64, 2.0)
        self.assertAlmostEqual(host._sea_wave_state.ripple_level, reference.ripple_level, places=5)
        self.assertFalse(math.isnan(host._song_wave_display.level))

    def test_compact_bars_grow_to_the_song_range_without_changing_stored_levels(self):
        visual = IslandStackVisual(EmptyIslandDataProvider())
        visual.timer.stop()
        self.addCleanup(visual.close)
        visual._island_bar_state = SimpleNamespace(levels=(0.25, 0.5, 0.0, 0.1))
        stored = visual._island_bar_state.levels
        rect = QRectF(10.0, 10.0, 20.0, 24.0)

        def heights():
            image = QImage(80, 60, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                visual._paint_sea_level_bars(painter, rect)
            return image

        plain = heights()
        display = visual._song_wave_display = SongWaveDisplay()
        display.active = True
        display.level = 1.0
        adapted = heights()
        self.assertEqual(visual._island_bar_state.levels, stored)
        self.assertGreater(self._opaque_rows(adapted), self._opaque_rows(plain))

    @staticmethod
    def _opaque_rows(image):
        width = image.width()
        height = image.height()
        rows = 0
        for y in range(height):
            if any(image.pixelColor(x, y).alpha() > 0 for x in range(width)):
                rows += 1
        return rows


if __name__ == "__main__":
    unittest.main()
