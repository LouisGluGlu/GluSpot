"""Width handoff keeps live content and follows one posed chrome hit map."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QAbstractAnimation, Qt
from PySide6.QtGui import QImage
from tests import test_narrow_back_pc_regression as fixtures


class NarrowWidthBreakpointTests(unittest.TestCase):
    setUpClass = fixtures.NarrowBackPcRegressionTests.__dict__['setUpClass']
    setUp = fixtures.NarrowBackPcRegressionTests.setUp
    tearDown = fixtures.NarrowBackPcRegressionTests.tearDown

    def render(self):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.window.render(image)

    def crossover(self):
        self.window.resize(1300, 640)
        self.assertFalse(self.window.narrow)
        for width in range(1299, 329, -1):
            self.window.resize(width, 640)
            if self.window.narrow:
                entry = width
                break
        else:
            self.fail('No shared narrow breakpoint')
        for width in range(entry + 1, 1301):
            self.window.resize(width, 640)
            if not self.window.narrow:
                self.window._finish_width_chrome()
                return entry, width
        self.fail('No shared wide breakpoint')

    def test_chrome_deadband_preserves_mode_and_settings(self):
        for mode in ('lyrics', 'playlist'):
            self.window.set_content_layout(mode, animate=False)
            entry, exit_width = self.crossover()
            with patch.object(self.owner, 'settings_setter') as settings:
                self.window.resize(entry, 640)
                self.assertTrue(self.window.narrow)
                self.assertTrue(self.window._width_chrome_active)
                for width in (entry + 2, entry - 2, entry + 3, entry):
                    self.window.resize(width, 640)
                    self.assertTrue(self.window.narrow)
                self.window.resize(exit_width, 640)
                self.assertFalse(self.window.narrow)
                for width in (exit_width - 2, exit_width + 2, exit_width - 3):
                    self.window.resize(width, 640)
                    self.assertFalse(self.window.narrow)
                self.assertEqual(self.window.mode, mode)
                settings.assert_not_called()
                self.assertTrue(self.window._layout_snapshot.isNull())
                self.assertTrue(self.window._layout_target_snapshot.isNull())

    def test_cover_and_hit_targets_move_through_intermediate_poses(self):
        entry, exit_width = self.crossover()
        self.window.resize(entry, 640)
        self.window._finish_width_chrome()
        before = self.window.glass_groups()[0]['content_layout']
        self.window.resize(exit_width, 640)
        self.window._width_chrome_animation.stop()
        start = self.window._content_column_geometry()[1]
        target_column = self.window._content_column_geometry()[2]
        self.window._advance_width_chrome(.5)
        self.render()
        halfway = self.window.glass_groups()[0]['content_layout']
        cover = self.window._content_column_geometry()[1]
        self.assertNotEqual(before, halfway)
        self.assertNotEqual(start, cover)
        self.assertEqual(target_column, self.window._content_column_geometry()[2])
        self.assertEqual(self.window._live_action_at(halfway.center()), 'content_layout')
        self.assertTrue(self.window._layout_snapshot.isNull())
        self.window._advance_width_chrome(1.)
        self.assertNotEqual(halfway, self.window.glass_groups()[0]['content_layout'])

    def test_reversal_starts_at_displayed_chrome_and_hide_releases(self):
        entry, exit_width = self.crossover()
        self.window.resize(entry, 640)
        self.window._finish_width_chrome()
        self.window.resize(exit_width, 640)
        self.window._width_chrome_animation.stop()
        self.window._advance_width_chrome(.4)
        self.window.resize(entry, 640)
        self.window._width_chrome_animation.stop()
        self.assertEqual(self.window._width_chrome_progress, 0.)
        for group in self.window.glass_groups():
            for key, rect in group.items():
                if key in self.window._width_chrome_from:
                    self.assertEqual(rect, self.window._width_chrome_from[key])
        self.window.hide()
        self.assertFalse(self.window._width_chrome_active)
        self.assertEqual(self.window._width_chrome_animation.state().value, 0)

    def test_narrow_uses_the_shared_decision_and_only_one_animation(self):
        with patch.object(self.window, '_width_breakpoint_narrow', return_value=True) as shared:
            self.window.resize(1200, 640)
            self.assertTrue(self.window.narrow)
            shared.assert_called()
        with patch.object(self.window, '_width_breakpoint_narrow', return_value=False):
            self.window.resize(1201, 640)
            self.assertFalse(self.window.narrow)
        self.assertEqual(self.window._width_layout_animation.state(), QAbstractAnimation.State.Stopped)
        self.window._width_chrome_animation.setCurrentTime(160)
        for _ in range(10):
            self.window._sync_narrow_mode()
        self.assertEqual(self.window._width_chrome_animation.currentTime(), 160)

    def test_album_modes_do_not_trigger_width_chrome(self):
        for mode in ('cover', 'cover_lyrics'):
            self.window.set_content_layout(mode, animate=False)
            for width in (1300, 360, 1000):
                self.window.resize(width, 640)
                self.assertFalse(self.window.narrow)
                self.assertFalse(self.window._width_chrome_active)
                self.assertEqual(self.window.mode, mode)

    def test_explicit_window_mode_bypasses_the_measure_without_replay(self):
        with patch('local.music_surface_layout.lyric_column_fits', return_value=False):
            self.window.toggle_wide(True)
            for _ in range(3):
                self.window._sync_narrow_mode()
                self.window._content_column_geometry()
                self.assertFalse(self.window.narrow)
                self.assertFalse(self.window._width_breakpoint_narrow())
                self.assertFalse(self.window._width_chrome_active)
        self.window.toggle_wide(False)
        self.assertTrue(self.window.narrow)
        self.assertFalse(self.window._width_chrome_active)
