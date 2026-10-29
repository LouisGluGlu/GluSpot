"""Width handoff keeps live content and follows one posed chrome hit map."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPointF, Qt
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

    def test_chrome_deadband_preserves_mode_and_settings(self):
        for mode in ('lyrics', 'playlist'):
            self.window.resize(483, 640)
            self.window.set_content_layout(mode, animate=False)
            with patch.object(self.owner, 'settings_setter') as settings:
                self.window.resize(960, 640)
                self.assertFalse(self.window.narrow)
                self.assertTrue(self.window._width_chrome_active)
                for width in (959, 950, 937, 959, 960):
                    self.window.resize(width, 640)
                    self.assertFalse(self.window.narrow)
                self.window.resize(935, 640)
                self.assertTrue(self.window.narrow)
                self.assertEqual(self.window.mode, mode)
                settings.assert_not_called()

    def test_cover_and_hit_targets_move_through_intermediate_poses(self):
        self.window.resize(959, 640)
        before = self.window.glass_groups()[0]['content_layout']
        self.window.resize(960, 640)
        self.window._width_chrome_animation.stop()
        start = self.window._content_column_geometry()[1]
        self.window._advance_width_chrome(.5)
        self.render()
        halfway = self.window.glass_groups()[0]['content_layout']
        cover = self.window._content_column_geometry()[1]
        self.assertNotEqual(before, halfway)
        self.assertNotEqual(start, cover)
        self.assertEqual(self.window._live_action_at(halfway.center()), 'content_layout')
        self.assertTrue(self.window._layout_snapshot.isNull())
        self.window._advance_width_chrome(1.)
        self.assertNotEqual(halfway, self.window.glass_groups()[0]['content_layout'])

    def test_reversal_starts_at_displayed_chrome_and_hide_releases(self):
        self.window.resize(960, 640)
        self.window._width_chrome_animation.stop()
        self.window._advance_width_chrome(.4)
        self.window.resize(935, 640)
        self.window._width_chrome_animation.stop()
        self.assertEqual(self.window._width_chrome_progress, 0.)
        for group in self.window.glass_groups():
            for key, rect in group.items():
                if key in self.window._width_chrome_from:
                    self.assertEqual(rect, self.window._width_chrome_from[key])
        self.window.hide()
        self.assertFalse(self.window._width_chrome_active)
        self.assertEqual(self.window._width_chrome_animation.state().value, 0)
