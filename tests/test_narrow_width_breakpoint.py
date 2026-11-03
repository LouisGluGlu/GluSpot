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
        self.window._width_chrome_animation.setCurrentTime(self.window._width_chrome_animation.duration())
        self.assertFalse(self.window.narrow)
        for width in range(1299, 329, -1):
            self.window.resize(width, 640)
            if self.window.narrow:
                entry = width
                break
        else:
            self.fail('No shared narrow breakpoint')
        self.window._width_chrome_animation.setCurrentTime(self.window._width_chrome_animation.duration())
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
                self.window._width_chrome_animation.setCurrentTime(self.window._width_chrome_animation.duration())
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
        self.assertNotEqual(target_column, self.window._content_column_geometry()[2])
        self.assertEqual(self.window._live_action_at(halfway.center()), 'content_layout')
        self.assertTrue(self.window._layout_snapshot.isNull())
        self.window._advance_width_chrome(.7)
        self.assertNotEqual(self.window._content_column_geometry()[1], start)
        self.window._advance_width_chrome(.85)
        self.assertNotEqual(self.window._content_column_geometry()[1], start)
        self.assertNotEqual(target_column, self.window._content_column_geometry()[2])
        self.window._advance_width_chrome(1.)
        self.assertNotEqual(halfway, self.window.glass_groups()[0]['content_layout'])
        self.assertNotEqual(self.window._content_column_geometry()[1], start)
        self.assertNotEqual(target_column, self.window._content_column_geometry()[2])

    def test_merge_phases_move_B_only_after_bottom_exit_and_reverse_A_first(self):
        entry, exit_width = self.crossover()
        self.window.resize(entry, 640)
        self.window._width_chrome_animation.stop()
        self.assertEqual(self.window._width_chrome_animation.duration(), 1000)

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

    def test_reversal_queues_until_split_finishes_and_hide_releases(self):
        entry, exit_width = self.crossover()
        self.window.resize(entry, 640)
        self.window._finish_width_chrome()
        self.window.resize(exit_width, 640)
        animation = self.window._width_chrome_animation
        animation.setCurrentTime(200)
        pose = self.window._width_chrome_progress
        self.window.resize(entry, 640)
        self.assertEqual(self.window._width_chrome_progress, pose)
        self.assertEqual(self.window._width_transition.state, 'SPLITTING')
        self.assertTrue(self.window._width_transition.want_narrow)
        animation.setCurrentTime(animation.duration())
        self.assertEqual(self.window._width_transition.state, 'MERGING')
        self.assertTrue(self.window.narrow)
        self.assertEqual(animation.state(), QAbstractAnimation.State.Running)
        self.window.hide()
        self.assertFalse(self.window._width_chrome_active)
        self.assertEqual(animation.state(), QAbstractAnimation.State.Stopped)

    def test_return_intent_never_changes_running_chrome_duration_or_phase(self):
        entry, exit_width = self.crossover()
        for progress in (.225, .425, .575, .775):
            self.window.resize(entry, 640)
            self.window._finish_width_chrome()
            self.window.resize(exit_width, 640)
            animation = self.window._width_chrome_animation
            animation.setCurrentTime(round(progress * animation.duration()))
            previous = self.window._chrome_phases()
            duration, phase = animation.duration(), self.window._width_chrome_progress
            self.window.resize(entry, 640)
            self.assertEqual(self.window._width_chrome_progress, phase)
            self.assertEqual(animation.duration(), duration)
            self.assertEqual(self.window._chrome_phases(), previous)
            animation.setCurrentTime(animation.duration())
            animation.setCurrentTime(animation.duration())

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

    def test_album_modes_share_responsive_chrome_without_content_handoff(self):
        for mode in ('cover', 'cover_lyrics'):
            self.window.set_content_layout(mode, animate=False)
            for width in (1300, 360, 1000):
                self.window.resize(width, 640)
                self.assertEqual(self.window.narrow, self.window._width_breakpoint_narrow())
                if width == 360:
                    self.assertTrue(self.window.narrow)
                    self.assertEqual(set(self.window.glass_groups()[2]),
                                     {'media:previous', 'media:toggle_play_pause', 'media:next'})
                elif width == 1300:
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
