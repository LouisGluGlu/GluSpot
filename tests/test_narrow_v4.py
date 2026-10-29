"""Narrow v4 uses shared samples and presentation with stable overlay geometry."""
from __future__ import annotations

from dataclasses import replace
import time
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from api.models import ColorToken, IslandVisualData, TimedLyricLine, TimedLyricUnit, TrackData
from api.providers import EmptyIslandDataProvider
from api.capture.runtime import CaptureRuntime
from local.island_stack_visual import IslandStackVisual
from local.music_surface_window import MusicSurfaceWindow
from local.music_lyric_motion import MusicLyricFrame
from local import music_lyric_presentation as presentation
from _demos.narrow3 import narrow_window as narrow
from _demos.narrow3.layout import NarrowLayout
from _demos.narrow3.motion import Visibility
from _demos.narrow3.veil import VeilOverlay
from tests.capture_async import CaptureAsyncTestCase
from tests.test_capture_cef import Clock, FakeCdp
from tests.test_capture_smtc import Session, matches, visual


def lyric_track(**changes):
    lines = tuple(TimedLyricLine(float(i * 3), f'Fixture line {i:02d}') for i in range(80))
    base = TrackData('Fixture', 'Fixture artist', (), ColorToken(40, 60, 80),
                     ColorToken(20, 30, 40), (), source_app='fixture-player',
                     is_playing=False, duration_seconds=240, position_seconds=3,
                     timed_lyrics=lines, track_id='fixture-a', position_source='capture-model')
    return replace(base, **changes)


def alpha_pixels(image):
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return np.frombuffer(image.bits(), np.uint8).reshape(image.height(), image.bytesPerLine())[:, :image.width() * 4].reshape(image.height(), image.width(), 4)[:, :, 3].copy()


class SharedPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_one_reveal_progress_sample_per_advance_including_retargets(self):
        visibility = Visibility(True)
        for now, active in ((10, True), (10.08, True), (10.16, False),
                            (11.4, False), (11.48, False), (11.5, True)):
            with patch.object(visibility.opacity, 'value', wraps=visibility.opacity.value) as value:
                visibility.advance(now, active, False, False)
                self.assertEqual(value.call_count, 1)
            self.assertAlmostEqual(visibility.progress, visibility.opacity.value(now))

    def test_surface_font_and_narrow_size_reuse_shared_tokens(self):
        self.assertAlmostEqual(presentation.surface_pixel_size(360), 31.5)
        self.assertEqual(presentation.surface_pixel_size(1129), 38)
        for width in (320, 360, 480, 1129):
            self.assertEqual(NarrowLayout(width, 640).lyric_font,
                             presentation.surface_pixel_size(width))
            font = presentation.surface_font(presentation.surface_pixel_size(width), QFont.Weight.Bold)
            self.assertEqual(tuple(font.families()), MusicSurfaceWindow.SURFACE_FONT_FAMILIES)
            self.assertEqual(font.weight(), QFont.Weight.Bold)
            self.assertEqual(MusicSurfaceWindow._intro_dot_radius(font),
                             presentation.intro_dot_radius(font.pixelSize()))

    def test_three_circles_share_radius_and_center_at_both_dprs(self):
        size = presentation.surface_font(presentation.surface_pixel_size(360)).pixelSize()
        radius = presentation.intro_dot_radius(size)
        for dpr in (1, 2):
            with self.subTest(dpr=dpr):
                image = QImage(360 * dpr, 640 * dpr, QImage.Format.Format_ARGB32_Premultiplied)
                image.setDevicePixelRatio(dpr)
                image.fill(Qt.GlobalColor.transparent)
                with QPainter(image) as painter:
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                    presentation.paint_intro_countdown(
                        painter, center=QPointF(180, 320), pixel_size=size,
                        color=QColor('white'), opacity=1.0,
                        blur_layers=((0, 1.0),), scale=1.0)
                alpha = alpha_pixels(image)
                columns = np.flatnonzero((alpha > 127).any(axis=0))
                groups = np.split(columns, np.flatnonzero(np.diff(columns) > 1) + 1)
                self.assertEqual(len(groups), 3)
                widths = [(g[-1] - g[0] + 1) / dpr for g in groups]
                self.assertLessEqual(max(widths) - min(widths), 1 / dpr)
                for width in widths:
                    self.assertLessEqual(abs(width - 2 * radius), 1 / dpr + .05)
                horizontal_center = (columns[0] + columns[-1] + 1) / (2 * dpr)
                self.assertLessEqual(abs(horizontal_center - 180), 1)
                ys, _xs = np.nonzero(alpha > 127)
                self.assertLessEqual(abs((ys.min() + ys.max() + 1) / (2 * dpr) - 320), 1)

    def test_cap_height_slot_is_half_height_independent_of_reveal(self):
        for height in (640, 900):
            for opacity in (0, .1, .5, 1):
                layout = NarrowLayout(360, height, opacity)
                self.assertEqual(layout.lyric_anchor, round(height * .5))
                self.assertEqual(layout.viewport, NarrowLayout(360, height, 0).viewport)

    def test_reveal_is_monotone_retargetable_and_has_one_curve(self):
        visibility = Visibility(True)
        visibility.advance(10, True, False, False)
        self.assertGreaterEqual(visibility.DURATION, .28)
        self.assertLessEqual(visibility.DURATION, .36)
        self.assertIs(visibility.footer, visibility.opacity)
        self.assertTrue(all(curve is visibility.opacity for curve in visibility.top))
        forward = [visibility.opacity.value(10 + i * visibility.DURATION / 32) for i in range(33)]
        self.assertEqual(forward[0], 0)
        self.assertAlmostEqual(forward[-1], 1)
        self.assertTrue(all(0 <= a <= b <= 1 for a, b in zip(forward, forward[1:])))
        visibility.advance(11, False, False, False)
        visibility.advance(11 + visibility.IDLE_DELAY, False, False, False)
        start = 11 + visibility.IDLE_DELAY
        backward = [visibility.opacity.value(start + i * visibility.DURATION / 32) for i in range(33)]
        self.assertTrue(all(0 <= b <= a <= 1 for a, b in zip(backward, backward[1:])))
        before = visibility.opacity.value(start + .08)
        visibility.advance(start + .08, True, False, False)
        self.assertAlmostEqual(visibility.opacity.value(start + .08), before)
        self.assertGreater(visibility.opacity.value(start + .16), before)


class CaptureEpochTests(CaptureAsyncTestCase):
    async def test_first_physical_sample_marker_survives_projection_until_new_sample(self):
        clock = Clock()
        cdp = FakeCdp(clock)
        runtime = CaptureRuntime(clock=clock, cef_session=cdp)
        try:
            await runtime.bind(Session(), visual().track, matches, mode='smtc')
            first = runtime.project(visual()).track
            self.assertTrue(first.capture_epoch_id)
            self.assertTrue(first.is_first_sample)
            clock.value += .1
            repeated = runtime.project(visual()).track
            self.assertEqual(repeated.capture_epoch_id, first.capture_epoch_id)
            self.assertTrue(repeated.is_first_sample)
            clock.value += .1
            cdp.emit(cdp.payload(10.2))
            second = runtime.project(visual()).track
            self.assertEqual(second.capture_epoch_id, first.capture_epoch_id)
            self.assertFalse(second.is_first_sample)
        finally:
            await runtime.close()

    async def test_recovered_capture_source_starts_a_new_settle_epoch(self):
        clock = Clock()
        cdp = FakeCdp(clock)
        runtime = CaptureRuntime(clock=clock, cef_session=cdp)
        try:
            await runtime.bind(Session(), visual().track, matches, mode='smtc')
            original = runtime.project(visual()).track
            runtime._publish_cef(None)
            unavailable = runtime.project(visual()).track
            self.assertEqual(unavailable.position_source, 'capture-unavailable')
            self.assertEqual(unavailable.position_seconds, original.position_seconds)
            clock.value += .1
            cdp.emit(cdp.payload(175))
            recovered = runtime.project(visual()).track
            self.assertGreater(recovered.capture_epoch_id, original.capture_epoch_id)
            self.assertTrue(recovered.is_first_sample)
            self.assertEqual(recovered.position_seconds, 175)
        finally:
            await runtime.close()

    async def test_track_replacement_has_new_epoch_and_cannot_reuse_native_sample(self):
        clock = Clock()
        cdp = FakeCdp(clock)
        runtime = CaptureRuntime(clock=clock, cef_session=cdp)
        try:
            await runtime.bind(Session(), visual().track, matches, mode='smtc')
            original = runtime.project(visual()).track
            replacement = replace(visual(), track=replace(visual().track, track_id='replacement'))
            await runtime.bind(Session(), replacement.track, matches, mode='smtc')
            self.assertIsNone(await runtime._cef_source.read())
            self.assertNotEqual(runtime.diagnostics()['position_route'], 'cef')
            clock.value += .1
            cdp.emit(dict(cdp.payload(1), trackId='new-native-track'))
            new_track = runtime.project(replacement).track
            self.assertNotEqual(new_track.capture_epoch_id, original.capture_epoch_id)
            self.assertTrue(new_track.is_first_sample)
            self.assertEqual(new_track.position_seconds, 1)
        finally:
            await runtime.close()


class NarrowCommitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.provider = EmptyIslandDataProvider(IslandVisualData(lyric_track(), (), ()))
        self.owner = IslandStackVisual(data_provider=self.provider)
        self.owner.timer.stop()
        self.owner.hide()
        self.window = narrow.NarrowStudyWindow(self.owner, 'B', 'left', live=True)
        self.window._timer.stop()
        self.window.study_timer.stop()
        self.window._audio_output_status_timer.stop()
        self.window.hints = False
        self.apply(lyric_track())

    def tearDown(self):
        self.window.shutdown()
        self.window.close()
        self.owner._application_quitting = True
        self.owner.close()
        self.app.processEvents()

    def apply(self, track):
        self.provider.visual_data = IslandVisualData(track, (), ())
        self.owner._apply_visual_data(self.provider.get_visual_data())
        for _ in range(100):
            lines = self.window.source_lines()
            if lines and lines[-1].text == track.timed_lyrics[-1].text:
                break
            QTest.qWait(10)
        self.assertEqual(self.window.source_lines()[-1].text, track.timed_lyrics[-1].text)

    def sample(self, now):
        with patch.object(narrow.time, 'monotonic', return_value=now):
            self.window.tick()
        self.window.study_timer.stop()

    def test_first_capture_sample_settles_large_index_without_interpolating(self):
        self.sample(100)
        before = self.window.diagnostic_snapshot()
        self.owner.media_capture_epoch_id = 'fixture-epoch-1'
        self.owner.media_is_first_sample = True
        self.owner.media_position_sampled_at = 101
        self.owner.lyric_last_sync_position = 175.14
        self.owner.lyric_index = 58
        self.owner.prev_lyric_index = 1
        self.owner.lyric_changed_at = 101
        self.sample(101)
        for instant in (101, 101.016, 101.05, 101.2):
            with patch.object(narrow.time, 'monotonic', return_value=instant):
                self.assertEqual(self.window.lyric_focus(), (58, 58.0))
        after = self.window.diagnostic_snapshot()
        self.assertEqual(after['settle_commits'] - before['settle_commits'], 1)
        self.assertEqual(after['snap_frames'] - before['snap_frames'], 0)
        self.sample(101.03)
        self.assertEqual(self.window.diagnostic_snapshot()['settle_commits'], after['settle_commits'])
        self.assertLessEqual(after['commits'], after['samples'])

    def test_snap_counter_observes_a_deliberately_interpolated_render(self):
        now = time.monotonic()
        self.owner.media_capture_epoch_id = 15
        self.owner.media_is_first_sample = True
        self.owner.lyric_index = 4
        self.owner.prev_lyric_index = 0
        self.owner.lyric_changed_at = now
        self.sample(now)
        self.window.show()
        self.app.processEvents()
        self.window.study_timer.stop()
        before = self.window.diagnostic_snapshot()['snap_frames']
        with patch.object(self.window, '_lyric_frame', return_value=MusicLyricFrame(4, 2.0, .5, 0)):
            self.window.grab()
        self.assertGreater(self.window.diagnostic_snapshot()['snap_frames'], before)

    def test_track_swap_publishes_one_fresh_commit_and_invalidates_old_hits(self):
        self.sample(100)
        self.window.content_image(NarrowLayout(360, 640))
        self.window.publish_live_lyric_hits(NarrowLayout(360, 640))
        previous_model = self.window._painted_live_model
        before = self.window.diagnostic_snapshot()
        lines = tuple(TimedLyricLine(i * 3., f'New track {i:02d}') for i in range(10))
        self.apply(lyric_track(track_id='fixture-b', timed_lyrics=lines,
                               position_seconds=0, capture_epoch_id='fixture-b-epoch',
                               is_first_sample=True))
        self.owner.lyric_index = 0
        self.owner.prev_lyric_index = 58
        self.owner.lyric_changed_at = 101
        self.sample(101)
        self.assertNotEqual(self.window.live_model_key(), previous_model)
        self.assertEqual(self.window.lyric_focus(), (0, 0.0))
        self.assertTrue(all(line.text.startswith('New track') for line in self.window.source_lines()))
        after = self.window.diagnostic_snapshot()
        self.assertEqual(after['swap_commits'] - before['swap_commits'], 1)
        self.assertEqual(after['commits'] - before['commits'], 1)
        self.sample(101.04)
        self.assertEqual(self.window.diagnostic_snapshot()['swap_commits'], after['swap_commits'])

    def test_chrome_reveal_reuses_lyric_pixels_and_blur_preparation(self):
        now = time.monotonic()
        self.owner.lyric_last_sync_position = 4
        self.owner.lyric_index = self.owner.prev_lyric_index = 1
        self.owner.lyric_changed_at = now - 1
        self.sample(now)
        layout = NarrowLayout(360, 640)
        content = self.window.content_image(layout)
        self.window.refresh_veil()
        builds = self.window.veil.builds
        with patch.object(self.window, 'paint_live_lyrics', side_effect=AssertionError('chrome rebuilt lyric layer')):
            self.window.visibility.advance(now, True, True, False)
            for offset in (.04, .08, .16, .24, .32):
                self.sample(now + offset)
                self.assertEqual(self.window.content_image(layout).cacheKey(), content.cacheKey())
        self.assertEqual(self.window.veil.builds, builds)

    def test_word_progress_changes_sharp_ink_without_rebuilding_blur(self):
        units = (TimedLyricUnit(3, 4, 'one '), TimedLyricUnit(4, 5, 'two '),
                 TimedLyricUnit(5, 6, 'three'))
        lines = (TimedLyricLine(0, 'Past line'),
                 TimedLyricLine(3, 'one two three', units, 'word'),
                 TimedLyricLine(9, 'Next line'))
        self.apply(lyric_track(timed_lyrics=lines, position_seconds=3.2,
                               is_playing=True))
        self.owner.lyric_index = self.owner.prev_lyric_index = 1
        self.owner.lyric_changed_at = time.monotonic() - 1
        self.owner.lyric_last_sync_position = 3.2
        self.sample(time.monotonic())
        layout = NarrowLayout(360, 640)
        initial_focus = self.window.lyric_focus()
        self.window.refresh_veil()
        key, builds = self.window.veil_key, self.window.veil.builds
        first = QImage(self.window.content_image(layout))
        images = []
        for position in (3.45, 3.7, 4.2, 4.7):
            self.owner.lyric_last_sync_position = position
            self.window.refresh_veil()
            self.assertEqual(self.window.lyric_focus(), initial_focus)
            self.assertEqual(self.window.veil_key, key)
            self.assertEqual(self.window.veil.builds, builds)
            images.append(QImage(self.window.content_image(layout)))
        self.assertTrue(any(image != first for image in images),
                        'sharp source-timed word ink stopped advancing')

    def test_live_intro_uses_shared_circle_surface_in_active_slot(self):
        lines = (TimedLyricLine(0, presentation.INTRO_COUNTDOWN_TEXT),
                 TimedLyricLine(8, 'First sung line'), TimedLyricLine(12, 'Next line'))
        self.apply(lyric_track(timed_lyrics=lines, position_seconds=2))
        self.owner.lyric_index = self.owner.prev_lyric_index = 0
        self.owner.lyric_last_sync_position = 2
        self.owner.lyric_changed_at = time.monotonic() - 1
        self.sample(time.monotonic())
        layout = NarrowLayout(360, 640)
        with patch.object(narrow, 'paint_intro_countdown', wraps=presentation.paint_intro_countdown) as dots:
            image = QImage(360, 640, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                self.window.paint_live_lyrics(painter, layout, current_only=True)
        self.assertTrue(dots.called)
        self.assertEqual(dots.call_args.kwargs['center'], QPointF(180, 320))
        self.assertEqual(dots.call_args.kwargs['pixel_size'], presentation.surface_font(layout.lyric_font).pixelSize())

    def test_lyric_cap_height_center_repeats_through_reveal(self):
        self.owner.lyric_index = self.owner.prev_lyric_index = 1
        self.owner.lyric_changed_at = time.monotonic() - 1
        for height in (640, 900):
            self.window.resize(360, height)
            layout = NarrowLayout(360, height)
            rows = self.window.live_lyric_rows(layout)
            _index, rect, _layers, runs = rows[1]
            _text, font, shaped, _offset, _run_height = runs[0]
            baseline = rect.top() + shaped.lineAt(0).ascent()
            self.assertAlmostEqual(baseline - QFontMetricsF(font).capHeight() / 2,
                                   height / 2, delta=1)
            for opacity in (0, .2, .8, 1):
                fade = self.window.visibility.opacity
                fade.start = fade.target = opacity
                fade.duration = 0
                self.assertEqual(self.window.live_lyric_rows(layout)[1][1], rect)

    def test_shared_owner_settles_late_first_sample_before_view_reads_it(self):
        now = time.monotonic()
        self.apply(lyric_track(capture_epoch_id=10, is_first_sample=True,
                              position_seconds=175.14, position_sampled_at=now,
                              position_observed_seconds=175.14))
        self.assertEqual(self.owner.lyric_index, 58)
        self.assertEqual(self.owner.prev_lyric_index, 58)
        self.assertAlmostEqual(self.owner.lyric_last_sync_position, 175.14)
        self.sample(now)
        self.assertEqual(self.window.lyric_focus(), (58, 58.0))
        self.assertEqual(self.window.diagnostic_snapshot()['snap_frames'], 0)
        self.assertEqual(self.window.diagnostic_snapshot()['settle_commits'], 1)

    def test_same_line_highlight_after_settle_still_follows_owner_sample(self):
        now = time.monotonic()
        self.owner.media_capture_epoch_id = 9
        self.owner.media_is_first_sample = True
        self.owner.lyric_index = 30
        self.owner.prev_lyric_index = 1
        self.owner.lyric_changed_at = now
        self.owner.lyric_last_sync_position = 90
        self.sample(now)
        self.assertEqual(self.window.lyric_focus(), (30, 30.0))
        self.owner.media_is_first_sample = False
        self.owner.lyric_index = 31
        self.owner.prev_lyric_index = 30
        self.owner.lyric_changed_at = now + 3
        self.owner.lyric_last_sync_position = 93.2
        with patch.object(narrow.time, 'monotonic', return_value=now + 3.12):
            focus, visual = self.window.lyric_focus()
        self.assertEqual(focus, 31)
        self.assertGreater(visual, 30)
        self.assertLess(visual, 31)
        self.assertEqual(self.window.playback_position(), 93.2)

    def test_veil_progress_and_glass_fill_follow_shared_owner_values(self):
        from local.liquid_glass import music_control_fill_color
        for alpha in (0, .125, .5, 1):
            dim, blur = self.window.veil.progress(alpha)
            self.assertAlmostEqual(dim, .18 * alpha)
            self.assertAlmostEqual(blur, 6 * alpha)
        for light in (False, True):
            ink = QColor(20, 22, 24) if light else QColor(245, 247, 249)
            with patch.object(self.owner, '_music_lyric_text_color', return_value=ink):
                for fill_alpha in (18, 24, 36, 206):
                    expected = music_control_fill_color(ink, fill_alpha)
                    self.assertEqual(self.owner._music_control_fill_color(fill_alpha), expected)
                    self.assertEqual(self.window._music_control_fill_color(fill_alpha), expected)

    def test_pointer_movement_threshold_and_exit_hysteresis(self):
        now = time.monotonic()
        self.window.pointer_activity(QPointF(180, 300), now)
        first_deadline = self.window._pointer_active_until
        self.window.pointer_activity(QPointF(183, 303), now + .1)
        self.assertEqual(self.window._pointer_active_until, first_deadline)
        self.window.pointer_activity(QPointF(188, 300), now + .11)
        self.assertGreater(self.window._pointer_active_until, first_deadline)
        self.window.pointer_activity(QPointF(180, 20), now + .2)
        self.sample(now + .2)
        self.assertTrue(self.window.shelf)
        self.window.pointer_activity(QPointF(180, VeilOverlay.TOP_END + 23), now + .21)
        self.sample(now + .45)
        self.assertTrue(self.window.shelf, '24px expanded control zone lost hover')
        self.window.pointer_activity(QPointF(180, VeilOverlay.TOP_END + 25), now + .46)
        self.sample(now + .659)
        self.assertTrue(self.window.shelf)
        self.sample(now + .661)
        self.assertFalse(self.window.shelf)
        self.window.pointer_activity(QPointF(), now + .7, focus=True)
        self.assertEqual(self.window.visibility.opacity.target, 1)

    def test_repeated_paused_samples_are_noops_without_losing_geometry(self):
        now = time.monotonic()
        self.owner.lyric_index = self.owner.prev_lyric_index = 1
        self.owner.lyric_changed_at = now - 1
        self.sample(now)
        self.sample(now + 1)
        before = self.window.diagnostic_snapshot()
        for i in range(1, 31):
            self.sample(now + 1 + i * .04)
        after = self.window.diagnostic_snapshot()
        self.assertEqual(after['commits'], before['commits'])
        self.assertEqual(after['noop_paints'] - before['noop_paints'], 30)
        self.assertLessEqual(after['commits'], after['samples'])

    def test_advancing_sample_paints_share_the_25hz_visual_cadence(self):
        class PaintCounter(QObject):
            paints = 0

            def eventFilter(counter, watched, event):
                if event.type() == QEvent.Type.Paint:
                    counter.paints += 1
                return False

        counter = PaintCounter(self.window)
        self.window.installEventFilter(counter)
        self.window.show()
        QTest.qWait(120)
        self.owner.timer.stop()
        counter.paints = 0
        self.assertEqual(self.window.study_timer.interval(), 40)
        started = time.monotonic()
        for _ in range(70):
            # Fast source snapshots exercise coalescing without creating a
            # second view clock: the production visual timer owns all paints.
            self.owner.lyric_last_sync_position += .01
            self.window.update()
            QTest.qWait(10)
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(counter.paints, elapsed * 15)
        self.assertLessEqual(counter.paints, elapsed * 25 + 2)

    def test_visible_surface_is_single_and_owner_has_zero_paints(self):
        self.window.show()
        self.app.processEvents()
        self.window.study_timer.stop()
        self.assertFalse(self.owner.isVisible())
        for _ in range(5):
            self.owner.update()
            self.window.tick()
            self.app.processEvents()
        diagnostics = self.window.diagnostic_snapshot()
        self.assertEqual(diagnostics['visible_windows'], 1)
        self.assertEqual(diagnostics['hidden_owner_paints'], 0)


if __name__ == '__main__':
    unittest.main()
