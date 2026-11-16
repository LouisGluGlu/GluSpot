"""Responsive artwork travels without changing selection or freezing lyrics."""
import unittest

from PySide6.QtCore import QAbstractAnimation
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from local.music_surface_window import MusicSurfaceWindow
from local.music_lyric_fit import cjk_unit
from tests.test_music_surface_transition import _TransitionOwner
from tests.test_owned_playback_surface import _Provider


class MusicWidthBreakpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.owner.data_provider = _Provider()
        self.saved = {}
        self.owner.settings_setter = lambda key, value: self.saved.update({key: value})
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(1300, 640)
        self.window.show()
        self.app.processEvents()
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()
        self.app.processEvents()

    def test_fit_deadband_survives_small_resize_reversals(self):
        self.assertFalse(self.window._content_layout_is_narrow())
        for width in range(1300, 640, -8):
            self.window.resize(width, 640)
            if self.window._content_layout_is_narrow():
                entry = width
                break
        else:
            self.fail("No stacked-layout threshold found")
        for width in (entry + 4, entry - 3, entry + 8, entry):
            self.window.resize(width, 640)
            self.assertTrue(self.window._content_layout_is_narrow())
        self.window._width_layout_animation.setCurrentTime(self.window._width_layout_animation.duration())
        font = self.window._surface_font(self.window.LYRIC_PIXEL_SIZE * self.window._surface_scale(), QFont.Weight.Bold)
        deadband = cjk_unit(font)
        self.window.resize(entry + round(deadband) + 10, 640)
        self.assertFalse(self.window._content_layout_is_narrow())
        for width in (entry + 16, entry + 10, entry + 18):
            self.window.resize(width, 640)
            self.assertFalse(self.window._content_layout_is_narrow())

    def _entry_and_exit(self, height):
        self.window.resize(1800, height)
        self.window._finish_width_layout()
        self.assertFalse(self.window._width_breakpoint_narrow())
        for width in range(1799, 329, -1):
            self.window.resize(width, height)
            if self.window._width_breakpoint_narrow():
                entry = width
                break
        else:
            self.fail("No content breakpoint")
        font = self.window._surface_font(self.window.LYRIC_PIXEL_SIZE * self.window._surface_scale(), QFont.Weight.Bold)
        self.window._width_layout_animation.setCurrentTime(self.window._width_layout_animation.duration())
        for width in range(entry, 1801):
            self.window.resize(width, height)
            if not self.window._width_breakpoint_narrow():
                return entry, width, cjk_unit(font)
        self.fail("No content breakpoint exit")

    def test_breakpoint_follows_font_not_pixels(self):
        short = self._entry_and_exit(640)[0]
        tall = self._entry_and_exit(900)[0]
        self.assertGreater(tall, short)

    def test_deadband_scales_with_font(self):
        gaps = []
        for height in (640, 900):
            entry, exit_width, glyph = self._entry_and_exit(height)
            self.assertAlmostEqual(exit_width - entry, glyph, delta=2.)
            gaps.append(exit_width - entry)
        self.assertGreater(gaps[1], gaps[0])

    def test_repeated_queries_do_not_restart_animation(self):
        self.window.resize(700, 640)
        animation = self.window._width_layout_animation
        animation.setCurrentTime(160)
        fraction = self.window._width_layout_fraction
        for _ in range(10):
            self.window._width_breakpoint_narrow()
            self.window._content_column_geometry()
        self.assertEqual(animation.currentTime(), 160)
        self.assertEqual(self.window._width_layout_fraction, fraction)

    def test_external_modes_move_all_geometry_and_finish_before_reversal(self):
        for layout in ("lyrics", "playlist"):
            with self.subTest(layout=layout):
                self.window.hide()
                self.window.resize(1300, 640)
                self.window._resize_content_layout()
                self.window.set_content_layout(layout, animate=False)
                self.window.show()
                self.app.processEvents()
                self.saved.clear()
                self.window.resize(700, 640)
                animation = self.window._width_layout_animation
                self.assertEqual(animation.state(), QAbstractAnimation.State.Running)
                source = self.window._content_column_geometry()
                animation.setCurrentTime(animation.duration() // 2)
                middle = self.window._content_column_geometry()
                self.assertTrue(all(first != second for first, second in zip(source, middle)))
                fraction = self.window._width_layout_fraction
                self.window.resize(1300, 640)
                self.assertEqual(animation.endValue(), 1.)
                self.assertEqual(self.window._width_layout_fraction, fraction)
                animation.setCurrentTime(animation.duration())
                self.assertEqual(animation.state(), QAbstractAnimation.State.Running)
                self.assertEqual(animation.startValue(), 1.)
                self.assertEqual(animation.endValue(), 0.)
                animation.setCurrentTime(animation.duration())
                self.assertEqual(self.window._width_layout_fraction, 0.)
                self.assertEqual(self.window._content_layout, layout)
                self.assertEqual(self.saved, {})
                self.assertTrue(self.window._layout_snapshot.isNull())
                self.assertTrue(self.window._layout_target_snapshot.isNull())

    def test_album_modes_keep_their_established_geometry(self):
        for layout in ("cover", "cover_lyrics"):
            self.window.set_content_layout(layout, animate=False)
            for width in (700, 1300):
                self.window.resize(width, 640)
                self.assertEqual(self.window._content_column_geometry(),
                                 self.window._wide_content_column_geometry())
                self.assertEqual(self.window._width_layout_animation.state(),
                                 QAbstractAnimation.State.Stopped)

    def test_hiding_finishes_width_motion(self):
        self.window.resize(700, 640)
        self.assertEqual(self.window._width_layout_animation.state(),
                         QAbstractAnimation.State.Running)
        self.window.hide()
        self.assertEqual(self.window._width_layout_animation.state(),
                         QAbstractAnimation.State.Stopped)
        self.assertEqual(self.window._width_layout_fraction, 1.)

    def test_explicit_layout_selection_finishes_width_motion_before_target_capture(self):
        self.window.resize(700, 640)
        animation = self.window._width_layout_animation
        animation.setCurrentTime(animation.duration() // 2)
        self.assertGreater(self.window._width_layout_fraction, 0.)
        self.assertLess(self.window._width_layout_fraction, 1.)
        self.window.set_content_layout("cover", animate=False)
        self.assertEqual(animation.state(), QAbstractAnimation.State.Stopped)
        self.assertEqual(self.window._width_layout_fraction, 1.)
        self.window.set_content_layout("lyrics", animate=False)
        self.assertEqual(animation.state(), QAbstractAnimation.State.Stopped)
        self.assertEqual(self.window._width_layout_fraction, 1.)

    def test_hidden_resize_adopts_current_geometry_without_a_replay(self):
        self.window.hide()
        self.window.resize(700, 640)
        self.window._resize_content_layout()
        self.assertEqual(self.window._width_layout_fraction, 1.)
        self.assertEqual(self.window._width_layout_animation.state(),
                         QAbstractAnimation.State.Stopped)

