from __future__ import annotations

import unittest

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.album_island_projection import album_cover_projection
from local.island_stack_visual import IslandStackVisual


class AlbumIslandProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def visual(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual._u = lambda value: float(value)
        return visual

    def test_projection_maps_whole_cover_to_convex_quad_inside_slot(self):
        rect = QRectF(20.0, 30.0, 80.0, 80.0)
        for strength in (0.035, 0.155, 0.42):
            with self.subTest(strength=strength):
                transform = album_cover_projection(rect, {"left_height": 1.0, "right_height": 1.0-strength})
                corners = [transform.map(point) for point in (rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft())]
                for point in corners:
                    self.assertTrue(rect.adjusted(-0.00001, -0.00001, 0.00001, 0.00001).contains(point))
                self.assertLess(corners[0].x(), corners[1].x())
                self.assertLess(corners[1].y(), corners[2].y())
                self.assertAlmostEqual(corners[0].y(), rect.top())
                self.assertAlmostEqual(corners[1].y(), rect.top()+rect.height()*strength/2.0)
                self.assertFalse(transform.isAffine())

    def test_left_and_right_projection_mirror_without_changing_height_or_center(self):
        rect = QRectF(35.0, 12.0, 64.0, 64.0)
        left = album_cover_projection(rect, {"left_height": 1.0, "right_height": 0.73})
        right = album_cover_projection(rect, {"left_height": 0.73, "right_height": 1.0})
        for point in (rect.topLeft(), rect.bottomLeft(), QPointF(47.0, 39.0), rect.center()):
            reflected = QPointF(2*rect.center().x()-point.x(), point.y())
            left_point = left.map(point)
            right_point = right.map(reflected)
            self.assertAlmostEqual(left_point.x()+right_point.x(), 2*rect.center().x())
            self.assertAlmostEqual(left_point.y(), right_point.y())

    def test_zero_amount_and_degenerate_rect_are_identity(self):
        self.assertTrue(album_cover_projection(QRectF(17, 23, 70, 70), {"left_height": 1.0, "right_height": 1.0}).isIdentity())
        self.assertTrue(album_cover_projection(QRectF(17, 23, 0, 70), {"left_height": 1.0, "right_height": 0.6}).isIdentity())

    def test_scaled_cover_cache_stays_bounded_after_many_track_changes(self):
        visual = self.visual()
        image = QImage(120, 120, QImage.Format.Format_ARGB32)
        with QPainter(image) as painter:
            for index in range(60):
                art = QImage(128, 128, QImage.Format.Format_ARGB32)
                art.fill(QColor(index*4, 110, 180))
                visual._paint_cover(painter, QRectF(15, 15, 72, 72), 6, art, f"cover-{index}")
                self.assertLessEqual(len(visual._scaled_album_art_cache), 24)
        self.assertEqual(len(visual._scaled_album_art_cache), 24)
        self.assertLess(sum(cover.sizeInBytes() for cover in visual._scaled_album_art_cache.values()), 600*1024)

    def test_oversized_aspect_fill_result_does_not_enter_scaled_cache(self):
        visual = self.visual()
        image = QImage(120, 120, QImage.Format.Format_ARGB32)
        ordinary = QImage(128, 128, QImage.Format.Format_ARGB32)
        ordinary.fill(QColor(90, 140, 210))
        unusually_tall = QImage(16, 2048, QImage.Format.Format_ARGB32)
        unusually_tall.fill(QColor(180, 140, 110))
        with QPainter(image) as painter:
            visual._paint_cover(painter, QRectF(15, 15, 72, 72), 6, ordinary, "ordinary")
            before = set(visual._scaled_album_art_cache)
            visual._paint_cover(painter, QRectF(15, 15, 72, 72), 6, unusually_tall, "tall")
            visual._paint_cover(painter, QRectF(15, 15, 72, 72), 6, unusually_tall, "tall")
        self.assertEqual(set(visual._scaled_album_art_cache), before)
        self.assertLess(sum(cover.sizeInBytes() for cover in visual._scaled_album_art_cache.values()), visual.SCALED_ALBUM_ART_CACHE_PIXEL_BUDGET)

    def test_pixel_budget_evicts_old_entries_before_count_limit(self):
        visual = self.visual()
        image = QImage(120, 120, QImage.Format.Format_ARGB32)
        with QPainter(image) as painter:
            for index in range(3):
                tall = QImage(16, 800, QImage.Format.Format_ARGB32)
                tall.fill(QColor(30+index*20, 140, 190))
                visual._paint_cover(painter, QRectF(15, 15, 72, 72), 6, tall, f"tall-{index}")
                self.assertLessEqual(sum(cover.sizeInBytes() for cover in visual._scaled_album_art_cache.values()), visual.SCALED_ALBUM_ART_CACHE_PIXEL_BUDGET)
        self.assertEqual(len(visual._scaled_album_art_cache), 2)
        self.assertNotIn(("tall-0", 72, 72), visual._scaled_album_art_cache)


if __name__ == "__main__":
    unittest.main()
