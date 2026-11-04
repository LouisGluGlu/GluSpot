"""Chrome slides at full ink strength; content geometry stays put."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from tests import test_narrow_chrome as fixtures
from _demos.narrow3.layout import NarrowLayout
from _demos.narrow3.veil import VeilOverlay


class NarrowChromeSlideTests(unittest.TestCase):
    setUpClass = fixtures.NarrowChromeTests.__dict__['setUpClass']
    setUp = fixtures.NarrowChromeTests.setUp
    tearDown = fixtures.NarrowChromeTests.tearDown

    def test_edge_positions_and_hit_targets_use_the_same_pose(self):
        window = self.window
        window.visibility.progress = .8
        layout = NarrowLayout(window.width(), window.height())
        groups = window.glass_groups()
        self.assertAlmostEqual(groups[0]['content_layout'].left(), layout.top[0].left())
        self.assertAlmostEqual(groups[0]['content_layout'].top(), layout.top[0].top() - VeilOverlay.TOP_END * .2)
        self.assertAlmostEqual(groups[2]['media:next'].top(), layout.next.top() + VeilOverlay.BOTTOM_HEIGHT * .2)
        self.assertEqual(window._live_action_at(groups[0]['return'].center()), 'return')
        self.assertNotEqual(window._live_action_at(layout.top[3].center()), 'return')
        window.visibility.progress = 0.
        self.assertTrue(all(not group for group in window.glass_groups()))

    def test_transport_and_glass_keep_full_opacity_while_moving(self):
        window = self.window
        window.visibility.progress = .5
        image = QImage(window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        alphas = []
        def inspect_ink(painter, *args, **kwargs):
            alphas.append(painter.opacity())
        with patch.object(window, '_paint_surface_glass', side_effect=inspect_ink), \
             patch.object(window, '_paint_transport_icon', side_effect=inspect_ink):
            with QPainter(image) as painter:
                window.paint_controls(painter, NarrowLayout(window.width(), window.height()), .5)
        self.assertTrue(alphas)
        self.assertEqual(set(alphas), {1.})

    def test_reveal_does_not_reflow_mounted_lyric_column(self):
        poses = []
        for progress in (0., .25, .5, .75, 1.):
            self.window.visibility.progress = progress
            poses.append(self.window._content_column_geometry()[2])
        self.assertTrue(all(pose == poses[0] for pose in poses))

    def test_cached_chrome_is_captured_unshifted_and_only_translated_on_present(self):
        window = self.window
        window.visibility.progress = .5
        window.refresh_controls(100.)
        self.assertFalse(window._capturing_chrome)
        class Recorder:
            def __init__(self): self.points = []
            def save(self): pass
            def restore(self): pass
            def setOpacity(self, opacity): pass
            def drawImage(self, point, image): self.points.append(point)
        recorder = Recorder()
        window._paint_chrome_bands(recorder)
        self.assertEqual(recorder.points[0].x(), 0.)
        self.assertEqual(recorder.points[0].y(), -VeilOverlay.TOP_END * .5)
        self.assertEqual(recorder.points[1].y(), window.height() - VeilOverlay.BOTTOM_HEIGHT * .5)

    def test_moving_controls_have_no_hits_before_shared_reveal_gate(self):
        self.window.visibility.progress = .74
        self.assertTrue(all(not group for group in self.window.glass_groups()))
