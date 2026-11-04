"""t156 width marks, queued handoffs and bounded shaping work."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QRect, QRectF

from local.music_lyric_fit import fits
from local.music_width_transition import WidthMarks, WidthTransition
from tests import test_narrow_back_pc_regression as fixtures


class WidthTransitionStateTests(unittest.TestCase):
    def test_marks_are_piecewise_physical_coordinates(self):
        marks = WidthMarks(330., 800., 1200., 675.)
        self.assertEqual([marks.mark(w) for w in (330, 800, 1200)], [10., 25., 50.])
        self.assertAlmostEqual(marks.mark(565), 17.5)
        self.assertAlmostEqual(marks.mark(1000), 37.5)

    def test_normal_split_finishes_then_full_merge(self):
        transition = WidthTransition(True)
        transition.request(False)
        self.assertEqual(transition.state, 'SPLITTING')
        self.assertEqual(transition.duration, 1.)
        transition.request(True)
        self.assertEqual(transition.state, 'SPLITTING')
        self.assertEqual(transition.duration, 1.)
        transition.complete()
        self.assertEqual(transition.state, 'MERGING')
        self.assertEqual(transition.duration, 1.)
        transition.complete()
        self.assertEqual(transition.state, 'NARROW')

    def test_fast_jump_has_400ms_floor_and_duration_does_not_change(self):
        transition = WidthTransition(True)
        transition.request_mark(10., 0.)
        transition.request_mark(50., .01)
        self.assertEqual(transition.duration, .4)
        transition.request_mark(18., .02)
        self.assertEqual(transition.state, 'SPLITTING')
        self.assertEqual(transition.duration, .4)
        transition.complete()
        self.assertEqual(transition.duration, .4)

    def test_duration_follows_speed_formula_and_clamps_to_400ms(self):
        for speed in (0., 15., 60., 112.5, 300., 1000.):
            with self.subTest(speed=speed):
                transition = WidthTransition(True)
                self.assertEqual(transition.duration, 1.)
                transition.drag_speed = speed
                transition.request(False)
                expected = max(.4, min(1., 1. / (1. + .8 * speed / 60.)))
                self.assertAlmostEqual(transition.duration, expected)

    def test_queued_reverse_uses_new_speed_only_after_current_handoff(self):
        transition = WidthTransition(True)
        transition.request(False)
        transition.drag_speed = 60.
        transition.request(True)
        self.assertEqual(transition.state, 'SPLITTING')
        self.assertEqual(transition.duration, 1.)
        transition.complete()
        self.assertEqual(transition.state, 'MERGING')
        self.assertAlmostEqual(transition.duration, 1. / 1.8)
        transition.complete()
        self.assertEqual(transition.state, 'NARROW')

    def test_deadband_keeps_intent(self):
        transition = WidthTransition(True)
        for now, mark in enumerate((24.5, 24.6, 24.4, 24.5)):
            transition.request_mark(mark, now)
        self.assertEqual(transition.state, 'NARROW')
        transition.request_mark(25., 10.)
        transition.complete()
        for now, mark in enumerate((24.5, 24.6, 24.4, 24.5), 11):
            transition.request_mark(mark, now)
        self.assertEqual(transition.state, 'WIDE')

    def test_unreachable_split_stays_narrow(self):
        marks = WidthMarks(330., 800., 700., 600.)
        self.assertTrue(marks.split_unreachable)
        transition = WidthTransition(True)
        transition.request_mark(50., 0., unreachable=True)
        self.assertEqual(transition.state, 'NARROW')


class WidthTransitionWindowTests(unittest.TestCase):
    setUpClass = fixtures.NarrowBackPcRegressionTests.__dict__['setUpClass']
    setUp = fixtures.NarrowBackPcRegressionTests.setUp
    tearDown = fixtures.NarrowBackPcRegressionTests.tearDown

    def begin(self):
        self.window._finish_width_chrome()
        with patch.object(self.window, '_available_geometry', return_value=QRect(0, 0, 2400, 1600)):
            self.window._begin_width_resize()
        return self.window._width_resize_marks

    def test_breakpoint_matches_t146_fit_and_is_frozen_with_height(self):
        marks = self.begin()
        self.assertAlmostEqual(marks.mark(marks.breakpoint), 25.)
        column = self.window._wide_content_column_geometry(QRectF(0., 0., marks.breakpoint, marks.height))[2]
        font = self.window._surface_font(self.window.LYRIC_PIXEL_SIZE * self.window._surface_scale(QRectF(0., 0., marks.breakpoint, marks.height)))
        self.assertTrue(fits(column.width() + .001, font, previous=None, dpr=self.window.devicePixelRatioF()))
        self.window.resize(500, 700)
        self.assertIs(self.window._width_resize_marks, marks)
        self.assertEqual(self.window._width_resize_marks.height, 560)
        self.window._end_width_resize()
        old = self.window._width_breakpoint_width()
        self.window.LYRIC_PIXEL_SIZE += 6
        self.assertGreater(self.window._width_breakpoint_width(), old)

    def test_below25_does_not_animate_and_fast_crossing_is400ms(self):
        marks = self.begin()
        self.window.resize(round(marks.minimum), round(marks.height))
        self.assertFalse(self.window._width_chrome_active)
        self.window.resize(round(marks.maximum), round(marks.height))
        self.assertEqual(self.window._width_transition.state, 'SPLITTING')
        self.assertEqual(self.window._width_chrome_animation.duration(), 400)
        self.assertEqual(self.window._width_chrome_animation.easingCurve().type(), QEasingCurve.Type.InOutCubic)

    def test_release_exactly_at25_preserves_split_intent(self):
        import math
        marks = self.begin()
        self.window.resize(math.ceil(marks.breakpoint), round(marks.height))
        self.assertEqual(self.window._width_transition.state, 'SPLITTING')
        self.window._end_width_resize()
        self.window._content_column_geometry()
        self.assertFalse(self.window._width_transition.want_narrow)
        animation = self.window._width_chrome_animation
        animation.setCurrentTime(animation.duration())
        self.assertEqual(self.window._width_transition.state, 'WIDE')
        self.assertFalse(self.window.narrow)

    def test_real_finished_callback_runs_queued_merge(self):
        self.window.resize(360, 640)
        self.window._finish_width_chrome()
        self.window.resize(1100, 640)
        animation = self.window._width_chrome_animation
        animation.setCurrentTime(150)
        fraction = self.window._width_chrome_progress
        self.window.resize(360, 640)
        self.assertEqual(self.window._width_chrome_progress, fraction)
        self.assertEqual(self.window._width_transition.state, 'SPLITTING')
        animation.setCurrentTime(animation.duration())
        self.assertEqual(self.window._width_transition.state, 'MERGING')
        self.assertEqual(animation.state(), QAbstractAnimation.State.Running)
        animation.setCurrentTime(animation.duration())
        self.assertEqual(self.window._width_transition.state, 'NARROW')
        self.assertFalse(self.window._width_chrome_active)

    def test_column_travels_with_cover_and_four_font_shaping_keys(self):
        self.window.resize(1100, 640)
        self.window._width_chrome_animation.setCurrentTime(self.window._width_chrome_animation.duration())
        self.window.resize(700, 640)
        self.window._width_chrome_animation.stop()
        keys = set()
        columns, covers = [], []
        for frame in range(31):
            self.window._advance_width_chrome(frame / 30.)
            _content, cover, column = self.window._content_column_geometry()
            columns.append(column)
            covers.append(cover)
            content, logical, font_scale, transform = self.window._width_lyric_render_pose(QRectF(self.window.rect()), column, self.window._surface_scale())
            keys.add((round(logical.width(), 5), round(font_scale, 5)))
            self.assertAlmostEqual(transform.mapRect(logical).width(), column.width(), places=4)
            # Lyrics share the full window height while header artwork
            # crosses their lane; alpha feathering supplies separation.
            self.assertAlmostEqual(column.top(), 0., places=4)
            self.assertAlmostEqual(column.bottom(), self.window.height(), places=4)
            self.assertAlmostEqual(transform.mapRect(logical).height(), column.height(), places=4)
        self.assertEqual(len(keys), 4)
        self.assertNotEqual(columns[0], columns[15])
        self.assertNotEqual(covers[0], covers[15])

    def test_animation_callback_only_updates(self):
        with patch.object(self.window, '_sync_chrome_accessibility') as accessibility, patch.object(self.window, 'refresh_controls') as controls:
            self.window._advance_width_chrome(.5)
        accessibility.assert_not_called()
        controls.assert_not_called()
