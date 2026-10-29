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
        self.assertEqual(start, cover, 'wide content returns only after B has split')
        self.assertEqual(target_column, self.window._content_column_geometry()[2])
        self.assertEqual(self.window._live_action_at(halfway.center()), 'content_layout')
        self.assertTrue(self.window._layout_snapshot.isNull())
        self.window._advance_width_chrome(.7)
        self.assertEqual(self.window._content_column_geometry()[1], start)
        self.window._advance_width_chrome(.85)
        self.assertNotEqual(self.window._content_column_geometry()[1], start)
        self.assertEqual(target_column, self.window._content_column_geometry()[2])
        self.window._advance_width_chrome(1.)
        self.assertNotEqual(halfway, self.window.glass_groups()[0]['content_layout'])
        self.assertNotEqual(self.window._content_column_geometry()[1], start)
        self.assertEqual(target_column, self.window._content_column_geometry()[2])

    def test_merge_phases_move_B_only_after_bottom_exit_and_reverse_A_first(self):
        entry, exit_width = self.crossover()
        self.window.resize(entry, 640)
        self.window._width_chrome_animation.stop()
        self.assertEqual(self.window._width_chrome_animation.duration(), 420)

        def at(progress):
            self.window._advance_width_chrome(progress)
            return {key: rect for group in self.window.glass_groups() for key, rect in group.items()}

        wide_B = at(0.)['content_layout']
        self.assertEqual(at(.29)['content_layout'], wide_B)
        self.assertNotIn('collection', at(.4))
        merging_B = at(.4)['content_layout']
        narrow_B = at(.55)['content_layout']
        self.assertNotEqual(merging_B, wide_B)
        self.assertNotEqual(merging_B, narrow_B)
        self.assertNotIn('collection', at(.56))
        narrow = at(1.)
        self.assertEqual(narrow['content_layout'], narrow_B)
        self.assertLess(narrow['collection'].bottom(), self.window.height() / 2)
        self.window._finish_width_chrome()

        self.window.resize(exit_width, 640)
        self.window._width_chrome_animation.stop()
        narrow_B = at(0.)['content_layout']
        self.assertEqual(at(.44)['content_layout'], narrow_B)
        self.assertNotIn('collection', at(.5))
        splitting_B = at(.575)['content_layout']
        wide_B = at(.7)['content_layout']
        self.assertNotEqual(splitting_B, narrow_B)
        self.assertNotEqual(splitting_B, wide_B)
        self.assertNotIn('collection', at(.71))
        wide = at(1.)
        self.assertEqual(wide['content_layout'], wide_B)
        self.assertGreater(wide['collection'].top(), self.window.height() / 2)

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

    def test_reversal_preserves_half_fused_outline_and_half_squeezed_A(self):
        entry, exit_width = self.crossover()
        for narrowing, progress, phase_index in ((True, .425, 0), (True, .775, 1),
                                                  (False, .225, 1), (False, .575, 0)):
            with self.subTest(narrowing=narrowing, progress=progress):
                self.window.resize(exit_width if narrowing else entry, 640)
                self.window._width_chrome_animation.stop()
                self.window._finish_width_chrome()
                self.window.resize(entry if narrowing else exit_width, 640)
                self.window._width_chrome_animation.stop()
                self.window._advance_width_chrome(progress)
                previous_phases = self.window._chrome_phases()
                self.assertAlmostEqual(previous_phases[phase_index], .5)

                self.window.resize(exit_width if narrowing else entry, 640)
                self.window._width_chrome_animation.stop()
                self.assertEqual(self.window._width_chrome_progress, 0.)
                self.assertEqual(self.window._chrome_phases(), previous_phases,
                                 'reversal must inherit the displayed glass outline')
                targets = self.window._width_control_rects(self.window.narrow)
                for key in ('audio_output', 'content_layout', 'playback_mode', 'return', 'collection'):
                    self.assertIn(key, self.window._width_chrome_from)
                    self.assertEqual(self.window.posed_rect(key, targets[key]),
                                     self.window._width_chrome_from[key],
                                     f'{key} jumped away from its recorded reversal pose')

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
