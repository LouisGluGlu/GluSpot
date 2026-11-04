"""Moderate absolute sea response and a thin, smoothed palette crest."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.music_background_renderer import NativeMusicBackgroundRenderer
from local.sea_wave import SeaWaveState
from tests.test_music_background_renderer import PaintHost


class RisingSeaStateTests(unittest.TestCase):
    @staticmethod
    def warm(*, peak=.64, vocal=0., low=0.):
        state = SeaWaveState()
        for tick in range(180):
            state.update_ripple(peak, tick / 30., vocal=vocal, low=low)
        return state

    @staticmethod
    def peak(state):
        return max(abs(state.ripple_displacement(index / 1000.)) for index in range(1001))

    def test_quiet_floor_is_retained_and_vocal_swell_is_about_seventeen_pixels(self):
        quiet = self.warm(peak=.0004)
        self.assertAlmostEqual(self.peak(quiet), .3395681076259124, places=8)
        self.assertLess(self.peak(self.warm(peak=0.)), .25)
        vocal = self.warm(vocal=.25, low=.03)
        self.assertGreater(self.peak(vocal), 16.5)
        self.assertLess(self.peak(vocal), 17.5)
        self.assertGreater(self.peak(vocal), self.peak(self.warm(low=.64)))

    def test_six_frame_low_onset_is_about_nineteen_pixels_and_releases(self):
        beat = self.warm(peak=.0004, low=.0004)
        peaks = []
        for tick in range(30):
            level = .64 if tick < 6 else .0004
            beat.update_ripple(level, 6. + tick / 30., low=level)
            peaks.append(self.peak(beat))
        self.assertGreater(max(peaks), 18.5)
        self.assertLess(max(peaks), 19.5)
        self.assertTrue(all(a < b for a, b in zip(peaks[:5], peaks[1:6])))
        self.assertLess(peaks[-1], 1.)
        self.assertLess(beat.ripple_beat, .001)

    def test_vocal_envelope_retains_smooth_attack_and_pause_clears_low_punch(self):
        state = self.warm()
        peaks = []
        for tick in range(1, 7):
            state.update_ripple(.64, (179 + tick) / 30., vocal=.25)
            peaks.append(self.peak(state))
        self.assertTrue(all(a < b for a, b in zip(peaks, peaks[1:])))
        self.assertLess(max(peaks), 18.)
        state.update_ripple(.64, 6.2, low=.64)
        self.assertGreater(state.ripple_beat, .5)
        for tick in range(120):
            state.update_ripple(.64, 6.3 + tick / 30., low=.64, is_playing=False)
        self.assertLess(state.ripple_beat, 1e-12)
        self.assertLess(self.peak(state), .25)

    @staticmethod
    def controlled_history(height):
        """Feed known water heights without coupling edge tests to wave phases."""
        state = SeaWaveState()
        now = [0.]
        with patch.object(state, "ripple_displacement", side_effect=lambda pos: height(pos, now[0])):
            for tick in range(66):
                now[0] = tick * .033
                state.update_ripple(.64, now[0])
        return state

    def test_extra_edge_is_subtle_and_tapers_at_rising_segment_ends(self):
        state = self.controlled_history(lambda pos, now: 24. * (2. * pos - 1.) * now)
        widths = state.ripple_edge_widths()
        self.assertEqual(len(widths), 49)
        self.assertTrue(all(width == 0. for width in widths[:25]))
        rising = widths[25:]
        self.assertTrue(all(width > 0. for width in rising))
        self.assertLessEqual(max(rising), 1.2)
        self.assertGreater(max(rising), 1.)
        self.assertGreater(max(rising), min(rising) * 10.)
        # The first rising samples enter with a soft ramp, without a fixed
        # minimum stroke suddenly appearing at the segment boundary.
        self.assertLess(widths[25], .04)
        self.assertLess(widths[25], widths[26])

    def test_extra_thickness_has_audio_clock_ema_and_duplicate_ticks_are_pure(self):
        # A sudden rise after a flat one-second history must approach its
        # thickness over time rather than jumping straight to the 1.2 limit.
        state = SeaWaveState()
        with patch.object(state, "ripple_displacement", return_value=0.):
            for tick in range(40):
                state.update_ripple(.64, tick / 30.)
        widths = []
        with patch.object(state, "ripple_displacement", return_value=8.):
            for tick in range(40, 46):
                state.update_ripple(.64, tick / 30.)
                widths.append(max(state.ripple_edge_widths()))
            before = state.ripple_edge_widths()
            state.update_ripple(.64, 45 / 30.)
            self.assertEqual(state.ripple_edge_widths(), before)
        self.assertGreater(widths[0], 0.)
        self.assertLess(widths[0], .3)
        self.assertTrue(all(a < b < 1.2 for a, b in zip(widths, widths[1:])))

    def test_falling_and_unchanged_water_have_no_thick_line(self):
        for speed in (-20., 0.):
            with self.subTest(speed=speed):
                state = self.controlled_history(lambda pos, now: pos * 12. + speed * now)
                self.assertEqual(state.ripple_edge_widths(), (0.,) * 49)

    def test_clamped_visible_plateaus_never_receive_a_thick_edge(self):
        for offset in (-200., 100.):
            with self.subTest(offset=offset):
                state = self.controlled_history(lambda _pos, now: offset + 20. * now)
                self.assertGreater(max(state.ripple_edge_widths()), 0.)
                self.assertEqual(state.ripple_edge_widths(minimum=-65.68, maximum=10.2), (0.,) * 49)

    def test_one_second_history_is_bounded_and_reading_cannot_advance_it(self):
        state = SeaWaveState()
        for tick in range(600):
            state.update_ripple(.64, tick * .033, vocal=.25)
        history = tuple(state._ripple_history)
        self.assertLessEqual(len(history), 34)
        self.assertGreaterEqual(history[-1][0] - history[0][0], 1.)
        self.assertLess(history[-1][0] - history[0][0], 1.1)
        pose = (state.ripple_time, state.ripple_level, state.ripple_updated_at)
        widths = state.ripple_edge_widths()
        self.assertGreater(max(widths), 0.)
        for _ in range(20):
            self.assertEqual(state.ripple_edge_widths(), widths)
            self.peak(state)
        self.assertEqual(tuple(state._ripple_history), history)
        self.assertEqual((state.ripple_time, state.ripple_level, state.ripple_updated_at), pose)
        # Duplicate timer observations do not consume history slots; a clock
        # reset cannot compare the new pose against a stale future sample.
        state.update_ripple(.64, history[-1][0])
        self.assertEqual(len(state._ripple_history), len(history))
        state.update_ripple(.64, 0.)
        self.assertEqual(len(state._ripple_history), 1)
        self.assertEqual(state.ripple_edge_widths(), (0.,) * 49)


class RisingSeaRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def renderer(state):
        host = PaintHost({})
        host._sea_wave_state = state
        tint = QColor("#c01a91")
        host._xiaomi_duotone_palette = lambda: (tint, tint, tint)
        return NativeMusicBackgroundRenderer(host)

    def test_moderate_vocal_surface_is_unclamped_and_inside_damage_strip(self):
        state = RisingSeaStateTests.warm(vocal=.25, low=.03)
        renderer = self.renderer(state)
        for rect in (QRectF(0., 0., 520., 136.), QRectF(20., 13., 520., 136.)):
            with self.subTest(origin=rect.topLeft()):
                context = renderer.render_context("sea_level", expanded=True)
                points = renderer._sea_level_surface(context, rect, True)
                waterline = rect.top() + rect.height() * .495
                measured = max(abs(point.y() - waterline) for point in points)
                self.assertGreater(measured, 16.5)
                self.assertLess(measured, 17.5)
                self.assertTrue(all(renderer.sea_level_wave_strip(rect).contains(point) for point in points))
                self.assertTrue(all(point.y() <= rect.bottom() - 3. for point in points))
                self.assertTrue(all(rect.left() <= point.x() <= rect.right() for point in points))
                self.assertEqual(renderer.sea_level_wave_strip(rect),
                                 QRectF(rect.left(), waterline - 44., rect.width(), 104.).intersected(rect))

    def test_every_legal_drive_keeps_crest_glow_and_flip_reflection_inside_damage(self):
        state = SeaWaveState()
        renderer = self.renderer(state)
        rect = QRectF(0., 0., 520., 136.)
        strip = renderer.sea_level_wave_strip(rect)
        for tick in range(360):
            peak = 1. if tick % 18 < 6 else .0004
            state.update_ripple(peak, tick / 30., vocal=1., low=peak)
            points = renderer._sea_level_surface(renderer.render_context("sea_level", expanded=True), rect, True)
            self.assertGreaterEqual(min(point.y() for point in points) - 11., strip.top())
            self.assertLessEqual(max(point.y() for point in points) + 26., strip.bottom())

    def test_tight_dpr_raster_retains_the_full_strip_pixels(self):
        rect = QRectF(0., 0., 520., 136.)
        for moment in ("quiet", "vocal", "beat"):
            state = RisingSeaStateTests.warm(peak=.0004 if moment != "vocal" else .64,
                                             vocal=.25 if moment == "vocal" else 0.)
            if moment == "beat":
                for tick in range(6):
                    state.update_ripple(.64, 6. + tick / 30., low=.64)
            for progress in (1., .25, .75):
                renderer = self.renderer(state)
                renderer.host._island_track_transition_progress = lambda _now: progress
                images = []
                history = state._ripple_history.copy()
                for tight in (True, False):
                    if not tight:
                        state._ripple_history.clear()
                    image = QImage(1040, 272, QImage.Format.Format_ARGB32_Premultiplied)
                    image.setDevicePixelRatio(2.)
                    image.fill(Qt.GlobalColor.transparent)
                    # Discard the per-renderer raster to compare independent
                    # tight and complete allocations of the same water pose.
                    renderer._sea_ripple_raster = None
                    with QPainter(image) as painter:
                        renderer.paint_sea_level_wave(painter, rect, 24., True)
                    images.append(image)
                state._ripple_history = history
                with self.subTest(moment=moment, progress=progress):
                    self.assertEqual(images[0], images[1])

    def test_repeated_paint_preserves_water_history_and_returns_identical_rasters(self):
        state = RisingSeaStateTests.warm(vocal=.25, low=.03)
        renderer = self.renderer(state)
        pose = (tuple(state._ripple_history), state.ripple_time, state.ripple_updated_at)
        for dpr in (1, 2):
            images = []
            for _ in range(2):
                image = QImage(520 * dpr, 136 * dpr, QImage.Format.Format_ARGB32_Premultiplied)
                image.setDevicePixelRatio(dpr)
                image.fill(Qt.GlobalColor.transparent)
                with QPainter(image) as painter:
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                    renderer.paint_sea_level_ambience(painter, QRectF(0., 0., 520., 136.), 24., True)
                    renderer.paint_sea_level_wave(painter, QRectF(0., 0., 520., 136.), 24., True)
                images.append(image)
            self.assertEqual(images[0], images[1])
            self.assertEqual((tuple(state._ripple_history), state.ripple_time, state.ripple_updated_at), pose)

    def test_renderer_uses_original_heights_without_amplitude_clamps(self):
        state = RisingSeaStateTests.warm(vocal=.25, low=.03)
        renderer = self.renderer(state)
        image = QImage(520, 136, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with patch.object(state, "ripple_edge_widths", wraps=state.ripple_edge_widths) as widths:
            with QPainter(image) as painter:
                renderer.paint_sea_level_wave(painter, QRectF(0., 0., 520., 136.), 24., True)
        widths.assert_called_once()
        self.assertEqual(widths.call_args.kwargs, {"include_rises": True})

    def test_base_hairline_remains_visible_on_falling_and_flat_water(self):
        rect = QRectF(0., 0., 520., 136.)
        strokes = []
        class CrestPainter(QPainter):
            def drawPath(self, path):
                strokes.append((self.pen().widthF(), self.pen().color().alpha()))
                super().drawPath(path)
        for speed in (-4., 0., 4.):
            with self.subTest(speed=speed):
                state = RisingSeaStateTests.controlled_history(lambda _pos, now: speed * now)
                renderer = self.renderer(state)
                image = QImage(520, 136, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                strokes.clear()
                with CrestPainter(image) as painter:
                    renderer.paint_sea_level_wave(painter, rect, 24., True)
                self.assertEqual(len(strokes), 1)
                self.assertEqual(strokes[0][0], 1.)
                self.assertTrue(154 <= strokes[0][1] <= 182)
                self.assertTrue(any(bytes(image.constBits())))

    def test_track_flip_reflection_never_adds_a_sharp_edge_to_falling_or_flat_water(self):
        rect = QRectF(0., 0., 520., 136.)
        points = [QPointF(index * 520. / 48., 80.) for index in range(49)]
        for speed in (-4., 0., 4.):
            with self.subTest(speed=speed):
                state = RisingSeaStateTests.controlled_history(lambda _pos, now: speed * now)
                renderer = self.renderer(state)
                context = renderer.render_context("sea_level", expanded=True)
                sea, line = renderer._sea_level_colors(context)
                image = QImage(520, 136, QImage.Format.Format_ARGB32_Premultiplied)
                image.fill(Qt.GlobalColor.transparent)
                # The soft reflection remains allowed. Isolate sharp ink to
                # catch an unconditional track-switch crest stroke.
                with patch.object(renderer, "_paint_background_blob") as soft_glow:
                    with QPainter(image) as painter:
                        renderer._paint_sea_track_light(painter, rect, points, sea, line, .5, True)
                soft_glow.assert_called_once()
                self.assertEqual(any(bytes(image.constBits())), speed > 0.)

    def test_ribbon_stops_at_the_signed_rise_zero_inside_a_sample_segment(self):
        rect = QRectF(0., 0., 520., 136.)
        points = [QPointF(index * 520. / 48., 80.) for index in range(49)]
        step = rect.width() / 48.
        for mirror in (False, True):
            with self.subTest(mirror=mirror):
                def height(pos, now):
                    index = (1. - pos if mirror else pos) * 48.
                    return (4. - 6. * index) * now
                state = RisingSeaStateTests.controlled_history(height)
                renderer = self.renderer(state)
                path = renderer._sea_rising_ribbon(rect, points)
                bounds = path.boundingRect()
                self.assertFalse(path.isEmpty())
                self.assertGreater(bounds.height(), 0.)
                if mirror:
                    boundary = rect.right() - step * (2. / 3.)
                    self.assertAlmostEqual(bounds.left(), boundary, places=6)
                    self.assertAlmostEqual(bounds.right(), rect.right(), places=6)
                    self.assertFalse(path.contains(QPointF(boundary - step * .1, 80.001)))
                else:
                    boundary = step * (2. / 3.)
                    self.assertAlmostEqual(bounds.left(), rect.left(), places=6)
                    self.assertAlmostEqual(bounds.right(), boundary, places=6)
                    self.assertFalse(path.contains(QPointF(boundary + step * .1, 80.001)))


if __name__ == "__main__":
    unittest.main()
