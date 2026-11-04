"""Real FFT input and production compact-island rendering regressions."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from api.audio_analyzer import analyze_frequency_block
from api.models import AudioFrame
from api.providers import EmptyIslandDataProvider
from local.island_audio_bars import IslandAudioBars
from local.island_stack_visual import IslandStackVisual


def tone(frequency, amplitude=.5):
    clock = np.arange(4410) / 44100
    pcm = amplitude * np.sin(2 * np.pi * frequency * clock)
    return analyze_frequency_block(np.column_stack((pcm, pcm)), 44100, np, [0., 0., 0.])


class IslandAudioEnvelopeTests(unittest.TestCase):
    def test_four_frequency_regions_follow_real_fft_left_to_right(self):
        for index, frequency in enumerate((100, 400, 1400, 6000)):
            with self.subTest(frequency=frequency):
                frame = tone(frequency)
                state = IslandAudioBars()
                for tick in range(60):
                    state.update(frame.spectrum, tick / 30.)
                self.assertEqual(len(state.levels), 4)
                self.assertEqual(max(range(4), key=state.levels.__getitem__), index)
                self.assertGreater(state.levels[index], .6)

    def test_quiet_input_is_not_amplified_and_absolute_level_is_preserved(self):
        quiet = IslandAudioBars()
        frame = tone(400, .0004)
        for tick in range(600):
            quiet.update(frame.spectrum, tick / 30.)
        self.assertLess(max(quiet.levels), .005)
        quiet.update(tone(400, .05).spectrum, 20.)
        quiet_level = quiet.levels[1]
        loud = IslandAudioBars()
        loud.update(tone(400).spectrum, 20.)
        self.assertGreater(loud.levels[1], quiet_level * 2.)

    def test_attack_release_and_cadence_are_time_based(self):
        states = []
        for fps in (30, 60):
            state = IslandAudioBars()
            state.update((), 0.)
            spectrum = tone(400).spectrum
            for tick in range(1, fps + 1):
                state.update(spectrum, tick / fps)
            peak = state.levels[1]
            state.update((), 1. + 1. / fps, is_playing=False)
            self.assertGreater(state.levels[1], peak * .8)
            for tick in range(2, fps + 1):
                state.update((), 1. + tick / fps, is_playing=False)
            self.assertLess(max(state.levels), .015)
            states.append(state.levels)
        np.testing.assert_allclose(states[0], states[1], atol=1e-9)

    def test_legacy_bands_are_real_and_missing_audio_releases_to_flat(self):
        state = IslandAudioBars()
        for tick in range(60):
            state.update((), tick / 30., bands=(.8, .2, .1), vocal=.4)
        self.assertGreater(state.levels[0], state.levels[2])
        self.assertGreater(state.levels[2], state.levels[1])
        for tick in range(60, 120):
            state.update((), tick / 30.)
        self.assertLess(max(state.levels), .001)


class IslandAudioBarPaintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.visual = IslandStackVisual(EmptyIslandDataProvider())
        self.visual.timer.stop()
        self.visual.location_mode = "free"
        self.visual.finder_mode = False
        self.visual.expanded = False
        self.visual.music_ambience_style = "sea_level"
        self.visual.music_surface_transition_started_at = 0.
        self.visual.media_is_playing = True
        self.visual.track_title = "One centered white line"
        self.visual.artist = "GluSpot"
        self.visual.album_art = QImage(80, 80, QImage.Format.Format_RGB32)
        self.visual.album_art.fill(QColor("#020509"))
        self.visual.album_color = QColor("#020509")
        self.visual.secondary_color = QColor("#010203")

    def tearDown(self):
        self.visual.close()
        self.visual.deleteLater()
        self.app.processEvents()

    def test_paint_has_exactly_four_separate_columns_at_both_dprs(self):
        for dpr in (1, 2):
            with self.subTest(dpr=dpr):
                image = QImage(80 * dpr, 60 * dpr, QImage.Format.Format_RGBA8888)
                image.setDevicePixelRatio(dpr)
                image.fill(Qt.GlobalColor.transparent)
                self.visual._island_bar_state = SimpleNamespace(levels=(.2, .8, .5, 1.))
                with QPainter(image) as painter:
                    self.visual._paint_sea_level_bars(painter, QRectF(10., 10., 20., 24.))
                rgba = np.frombuffer(image.constBits(), np.uint8).reshape(60 * dpr, 80 * dpr, 4)
                columns = np.any(rgba[:, :, 3] > 0, axis=0)
                starts = np.flatnonzero(columns & ~np.roll(columns, 1))
                self.assertEqual(len(starts), 4)
                heights = [np.count_nonzero(rgba[:, x + 1, 3]) for x in starts]
                self.assertEqual(len(set(heights)), 4)
                # A round end has less coverage than the middle of its column.
                x = starts[-1]
                ys = np.flatnonzero(rgba[:, x + 1, 3])
                self.assertLess(rgba[ys[0], x:x + 5 * dpr, 3].sum(),
                                rgba[ys[len(ys) // 2], x:x + 5 * dpr, 3].sum())

    def test_dark_palette_color_is_lifted_without_losing_its_hue(self):
        for source in ("#020509", "#300410", "#031b18", "#090909"):
            with self.subTest(source=source), patch.object(self.visual, "_xiaomi_duotone_palette",
                                                          return_value=(QColor(source),)):
                color = self.visual._sea_level_bar_color()
                self.assertGreaterEqual(color.lightnessF(), .619)
                if QColor(source).saturation() > 0:
                    self.assertAlmostEqual(color.hslHueF(), QColor(source).hslHueF(), places=3)
                else:
                    self.assertEqual(color.red(), color.green())
                    self.assertEqual(color.green(), color.blue())
                self.assertEqual(color.alpha(), 255)

    def test_provider_spectrum_updates_on_audio_clock_and_paint_only_reads_it(self):
        frame = tone(1400)
        self.visual.data_provider = SimpleNamespace(get_audio_frame=lambda: frame, has_live_audio=lambda: True)
        with patch("local.island_stack_visual.time.monotonic", return_value=100.):
            self.visual._update_wave_values()
        self.assertGreater(self.visual._island_bar_state.levels[2], .2)
        previous = self.visual._island_bar_state.levels
        with patch.object(self.visual.data_provider, "get_audio_frame", side_effect=AssertionError("paint read provider")):
            image = QImage(500, 60, QImage.Format.Format_ARGB32_Premultiplied)
            with QPainter(image) as painter:
                self.visual._paint_music(painter, QRectF(4., 4., 486., 48.), False)
        self.assertEqual(self.visual._island_bar_state.levels, previous)
        self.visual.data_provider.get_audio_frame = lambda: AudioFrame(0., 0., 0., peak=.8)
        for tick in range(90):
            with patch("local.island_stack_visual.time.monotonic", return_value=101. + tick / 30.):
                self.visual._update_wave_values()
        self.assertLess(max(self.visual._island_bar_state.levels), .001)

    def test_compact_sea_has_no_wave_and_other_surface_plans_retain_layers(self):
        from local.island_presentation import music_surface_paint_plan
        for style, expanded in (("sea_level", True), ("solid_album", False), ("system", False)):
            self.assertNotEqual(music_surface_paint_plan(expanded=expanded, ambience_style=style).steps,
                                ("pure_black_background", "compact_content"))
        image = QImage(500, 60, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with patch.object(self.visual, "_paint_music_light_wave", side_effect=AssertionError("compact wave")), \
                patch.object(self.visual, "_paint_bottom_ambience", side_effect=AssertionError("compact water")):
            with QPainter(image) as painter:
                self.visual._paint_music(painter, QRectF(4., 4., 486., 48.), False)
        self.assertEqual(image.pixelColor(200, 6), QColor("black"))
        self.assertEqual(image.pixelColor(200, 49), QColor("black"))

    def test_single_white_line_preserves_width_hold_and_cover_visibility_setting(self):
        self.visual.enable_dual_line_lyrics = True
        self.visual.compact_lyric_show_cover = False
        self.visual._compact_held_lyric = (("Previous line",), {})
        self.visual._compact_shell_waiting = True
        image = QImage(500, 60, QImage.Format.Format_ARGB32_Premultiplied)
        with patch.object(self.visual, "_compact_music_text", return_value="Primary line\nTranslation line"), \
                patch.object(self.visual, "_paint_scrolling_text") as text, \
                patch.object(self.visual, "_island_display_phase", return_value={}), \
                patch.object(self.visual, "_paint_sea_level_bars") as bars:
            with QPainter(image) as painter:
                self.visual._paint_music(painter, QRectF(4., 4., 486., 48.), False)
                self.assertEqual(text.call_args.args[2], "Previous line")
                self.visual._compact_shell_waiting = False
                self.visual._paint_music(painter, QRectF(4., 4., 486., 48.), False)
            self.assertEqual(text.call_count, 2)
            self.assertEqual(text.call_args.args[2], "Primary line")
            self.assertEqual(text.call_args.args[4], QColor("white"))
            self.assertTrue(text.call_args.args[5])
            self.assertTrue(text.call_args.kwargs["centered"])
            self.assertTrue(self.visual.music_cover_rect.isEmpty())
            self.assertEqual(bars.call_count, 2)


if __name__ == "__main__":
    unittest.main()
