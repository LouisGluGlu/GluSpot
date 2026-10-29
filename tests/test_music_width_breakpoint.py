"""Responsive artwork travels without changing selection or freezing lyrics."""
import unittest

from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWidgets import QApplication

from local.music_surface_window import MusicSurfaceWindow
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
        self.window.resize(entry + 30, 640)
        self.assertFalse(self.window._content_layout_is_narrow())
        for width in (entry + 26, entry + 20, entry + 28):
            self.window.resize(width, 640)
            self.assertFalse(self.window._content_layout_is_narrow())

    def test_external_modes_animate_one_cover_keep_live_columns_and_reverse(self):
        for layout in ("lyrics", "playlist"):
            with self.subTest(layout=layout):
                self.window.resize(1300, 640)
                self.window._finish_width_layout()
                self.window.set_content_layout(layout, animate=False)
                self.saved.clear()
                self.window.resize(700, 640)
                animation = self.window._width_layout_animation
                self.assertEqual(animation.state(), QAbstractAnimation.State.Running)
                _, source, target_column = self.window._content_column_geometry()
                animation.setCurrentTime(animation.duration() // 2)
                _, middle, live_column = self.window._content_column_geometry()
                self.assertNotEqual(source, middle)
                self.assertEqual(target_column, live_column)
                fraction = self.window._width_layout_fraction
                self.assertGreater(fraction, 0.)
                self.assertLess(fraction, 1.)
                self.window.resize(1300, 640)
                self.assertAlmostEqual(animation.startValue(), fraction)
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

    def test_hidden_resize_adopts_current_geometry_without_a_replay(self):
        self.window.hide()
        self.window.resize(700, 640)
        self.window._resize_content_layout()
        self.assertEqual(self.window._width_layout_fraction, 1.)
        self.assertEqual(self.window._width_layout_animation.state(),
                         QAbstractAnimation.State.Stopped)

