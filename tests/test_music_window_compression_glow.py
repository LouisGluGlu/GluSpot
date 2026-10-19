"""Pressure follows the dragged edge, leaving the untouched scene intact."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_surface_transition import _TransitionOwner


class MusicWindowCompressionGlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.window = MusicSurfaceWindow(self.owner)
        self.window.setGeometry(400, 300, 330, 400)
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()
        self.window._available_geometry = lambda: QRect(0, 0, 1920, 1080)
        self.ink = QColor(30, 70, 130)
        page = QImage(330, 400, QImage.Format.Format_ARGB32_Premultiplied)
        page.fill(self.ink)
        island = QImage(160, 50, QImage.Format.Format_ARGB32_Premultiplied)
        island.fill(QColor(7, 7, 7))
        self.owner._preview_free_island_from_music_surface = lambda *_: (QRect(485, 475, 160, 50), island)
        self.capture = patch.object(self.window, '_capture_transition_snapshot', return_value=page)
        self.capture_mock = self.capture.start()

    def tearDown(self):
        self.capture.stop()
        self.window.shutdown()
        self.owner.close()

    def render(self, horizontal=0., vertical=0., edges=()):
        self.window._resize_edges = set(edges)
        self.window._update_window_compact(330. - 165. * horizontal, 400. - 200. * vertical)
        self.window._compact_animation.stop()
        frame = QImage(330, 400, QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(Qt.GlobalColor.transparent)
        self.window.render(frame)
        return frame

    def test_horizontal_pressure_preserves_center_and_opposite_edge_without_page_veil(self):
        for fraction in (.1, .5, .99):
            with self.subTest(fraction=fraction):
                frame = self.render(horizontal=fraction, edges={'right'})
                self.assertEqual(frame.pixelColor(165, 60), self.ink)
                self.assertEqual(frame.pixelColor(10, 60), self.ink)
                self.assertGreater(frame.pixelColor(325, 60).red(), self.ink.red())

    def test_pressure_front_moves_inward_and_has_a_continuous_feather(self):
        early = self.render(horizontal=.2, edges={'right'})
        later = self.render(horizontal=.8, edges={'right'})
        self.assertEqual(early.pixelColor(240, 60), self.ink)
        self.assertGreater(later.pixelColor(240, 60).red(), self.ink.red())
        ramp = [later.pixelColor(x, 60).red() for x in range(210, 327)]
        self.assertGreater(len(set(ramp)), 40)
        self.assertTrue(all(a <= b for a, b in zip(ramp, ramp[1:])))

    def test_vertical_and_corner_pressure_follow_only_the_dragged_edges(self):
        top = self.render(vertical=.7, edges={'top'})
        self.assertGreater(top.pixelColor(30, 8).red(), self.ink.red())
        self.assertEqual(top.pixelColor(30, 200), self.ink)
        self.assertEqual(top.pixelColor(30, 390), self.ink)
        self.window._reset_window_compact()
        corner = self.render(horizontal=.7, vertical=.4, edges={'left', 'bottom'})
        self.assertGreater(corner.pixelColor(8, 200).red(), self.ink.red())
        self.assertGreater(corner.pixelColor(165, 390).red(), self.ink.red())
        self.assertEqual(corner.pixelColor(165, 60), self.ink)
        self.assertEqual(corner.pixelColor(320, 60), self.ink)

    def test_rebound_retracts_the_same_front_without_recapturing(self):
        deep = self.render(horizontal=.8, edges={'right'})
        self.window._release_window_compact()
        self.window._compact_animation.stop()
        self.window._advance_window_compact(.2)
        frame = QImage(330, 400, QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(Qt.GlobalColor.transparent)
        self.window.render(frame)
        self.assertGreater(deep.pixelColor(240, 60).red(), self.ink.red())
        self.assertEqual(frame.pixelColor(240, 60), self.ink)
        self.assertEqual(frame.pixelColor(165, 60), self.ink)
        self.assertEqual(self.capture_mock.call_count, 1)


if __name__ == '__main__':
    unittest.main()
