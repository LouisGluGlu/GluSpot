from __future__ import annotations

import math
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication

from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_surface_transition import _TransitionOwner


class SurfaceEdgeAndFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def render(self):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.window.render(image)
        return image

    def test_native_mask_keeps_fractional_edge_coverage(self):
        self.window._fullscreen = False
        self.window._sync_surface_shape()
        image = self.render()
        partial = [(x, y) for y in range(25) for x in range(25)
                   if 0 < image.pixelColor(x, y).alpha() < 255]
        self.assertGreater(len(partial), 12)
        self.assertTrue(all(self.window.mask().contains(QPoint(x, y))
                            for x, y in partial))
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(image.pixelColor(40, 0).alpha(), 255)

    def _edge_images(self, dpr):
        image = QImage(round(self.window.width()*dpr), round(self.window.height()*dpr), QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.white)
        with QPainter(image) as painter:
            self.window._apply_surface_edge_alpha(painter)
        expected = QImage(image.size(), image.format())
        expected.setDevicePixelRatio(dpr)
        expected.fill(Qt.GlobalColor.transparent)
        radius = min(self.window._surface_corner_radius(), self.window.width()*0.5, self.window.height()*0.5)
        with QPainter(expected) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(Qt.GlobalColor.white)
            painter.drawRoundedRect(QRectF(self.window.rect()), radius, radius)
        return image, expected

    def test_corner_coverage_matches_antialiased_path_at_each_dpr(self):
        for size in ((960, 540), (961, 541)):
            self.window.resize(*size)
            for dpr in (1.0, 1.25, 1.5, 1.75, 2.0, 2.625):
                with self.subTest(size=size, dpr=dpr):
                    image, expected = self._edge_images(dpr)
                    side = math.ceil(self.window._surface_corner_radius()*dpr)+3
                    points = {(x, y) for cy in (0, image.height()-side)
                              for cx in (0, image.width()-side)
                              for y in range(cy, cy+side) for x in range(cx, cx+side)}
                    points.update((image.width()-1, y) for y in range(image.height()))
                    points.update((x, image.height()-1) for x in range(image.width()))
                    for x, y in points:
                        self.assertEqual(image.pixelColor(x, y).alpha(), expected.pixelColor(x, y).alpha(), (x, y))

    def test_small_transition_corner_patches_do_not_multiply_overlap_alpha(self):
        self.window.resize(61, 41)
        with patch.object(self.window, "_surface_corner_radius", return_value=25):
            for dpr in (1.0, 1.25, 1.5, 1.75):
                with self.subTest(dpr=dpr):
                    image, expected = self._edge_images(dpr)
                    for y in range(image.height()):
                        for x in range(image.width()):
                            self.assertEqual(image.pixelColor(x, y).alpha(), expected.pixelColor(x, y).alpha(), (x, y))

    def test_repeated_partial_paints_preserve_corner_coverage(self):
        self.window._fullscreen = False
        self.window._sync_surface_shape()
        image = self.render()
        before = tuple(image.pixelColor(x, y).alpha() for y in range(25) for x in range(25))
        for region in (QRect(500, 150, 200, 200), QRect(0, 0, 25, 25)):
            with self.subTest(region=region):
                for _ in range(5):
                    self.window.render(image, region.topLeft(), QRegion(region))
                after = tuple(image.pixelColor(x, y).alpha() for y in range(25) for x in range(25))
                self.assertEqual(after, before)

    def test_corner_cache_invalidates_for_resize_dpr_and_transition_radius(self):
        self._edge_images(1.0)
        previous = self.window._surface_edge_alpha_cache
        self.window.resize(961, 541)
        self._edge_images(1.0)
        self.assertIsNot(self.window._surface_edge_alpha_cache, previous)
        previous = self.window._surface_edge_alpha_cache
        self._edge_images(1.5)
        self.assertIsNot(self.window._surface_edge_alpha_cache, previous)
        previous = self.window._surface_edge_alpha_cache
        with patch.object(self.window, "_surface_corner_radius", return_value=8):
            self._edge_images(1.5)
        self.assertIsNot(self.window._surface_edge_alpha_cache, previous)

    def test_edge_mask_cache_is_small_reused_and_fullscreen_remains_opaque(self):
        self.render()
        cache = self.window._surface_edge_alpha_cache
        logical_bytes = sum(image.sizeInBytes() / image.devicePixelRatio() ** 2 for image in cache[1])
        self.assertLess(logical_bytes, 20000)
        self.render()
        self.assertIs(self.window._surface_edge_alpha_cache, cache)
        self.window._fullscreen = True
        self.window._sync_surface_shape()
        self.assertEqual(self.render().pixelColor(0, 0).alpha(), 255)

    def test_each_paint_checks_long_source_once_and_next_frame_observes_inplace_edit(self):
        self.owner.timed_lyrics = [(float(i*3), f"Row {i}") for i in range(180)]
        with patch.object(self.window, "_resolve_display_lyrics", wraps=self.window._resolve_display_lyrics) as resolve:
            self.render()
            self.assertEqual(resolve.call_count, 1)
            self.owner.timed_lyrics[1] = (3.0, "Updated between frames")
            self.render()
            self.assertEqual(resolve.call_count, 2)
        self.assertIn((3.0, "Updated between frames"), self.window._display_lyrics()[1])
        self.assertIsNone(self.window._frame_lyric_cache)

    def test_exception_cannot_leave_tick_or_paint_snapshot_active(self):
        for entry, body in (("_on_animation_tick", "_advance_surface_animation_tick"),
                            ("paintEvent", "_paint_surface_frame")):
            with self.subTest(entry=entry), patch.object(self.window, body, side_effect=RuntimeError("fixture")):
                with self.assertRaises(RuntimeError):
                    getattr(self.window, entry)(None) if entry == "paintEvent" else getattr(self.window, entry)()
                self.assertIsNone(self.window._frame_lyric_cache)


if __name__ == "__main__":
    unittest.main()
