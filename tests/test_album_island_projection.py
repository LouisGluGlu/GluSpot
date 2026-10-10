from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QTransform
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

    def test_legacy_flat_style_normalizes_at_existing_setter(self):
        visual = self.visual()
        visual._pulse_stack = lambda: None
        visual.update = lambda: None
        visual.set_album_island_secondary_style("shrink_dim", notify=False)
        self.assertEqual(visual.album_island_secondary_style, "perspective_dim")
        self.assertEqual(visual.ALBUM_ISLAND_SECONDARY_STYLES, ("perspective_dim",))

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

    def test_native_raster_preserves_marker_that_old_trapezoid_clipped_off(self):
        visual = self.visual()
        art = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
        art.fill(QColor(18, 35, 60))
        with QPainter(art) as painter:
            painter.fillRect(QRectF(72, 8, 20, 12), QColor(230, 15, 20))
        rect = QRectF(20, 20, 100, 100)
        perspective = {"left_height": 1.0, "right_height": 0.58}
        landmark = album_cover_projection(rect, perspective).map(QPointF(102, 34))
        for dpr in (1.0, 1.5, 2.0):
            with self.subTest(dpr=dpr):
                image = QImage(round(150*dpr), round(150*dpr), QImage.Format.Format_ARGB32_Premultiplied)
                image.setDevicePixelRatio(dpr)
                image.fill(Qt.GlobalColor.transparent)
                with QPainter(image) as painter:
                    visual._paint_album_island_slot(painter, {"rect": rect, "album_art": art, "alpha": 255, "center": False, "side": -1, "perspective": perspective}, 6)
                color = image.pixelColor(round(landmark.x()*dpr), round(landmark.y()*dpr))
                self.assertGreater(color.red(), 150)
                self.assertLess(color.green(), 65)
                # The old square-space location is above the projected surface.
                self.assertEqual(image.pixelColor(round(102*dpr), round(28*dpr)).alpha(), 0)

    def test_projection_preserves_parent_transform_clip_and_center_cover_handler(self):
        visual = self.visual()
        calls = []
        visual._paint_cover = lambda painter, *_args: calls.append(("side", painter.worldTransform(), painter.hasClipping()))
        visual._paint_interactive_music_cover = lambda painter, *_args: calls.append(("center", painter.worldTransform(), painter.hasClipping()))
        art = QImage(8, 8, QImage.Format.Format_ARGB32)
        art.fill(QColor(40, 90, 150))
        image = QImage(150, 150, QImage.Format.Format_ARGB32)
        with QPainter(image) as painter:
            painter.translate(7, 11)
            painter.setClipRect(QRectF(0, 0, 120, 120))
            parent = QTransform(painter.worldTransform())
            slot = {"rect": QRectF(20, 20, 60, 60), "album_art": art, "alpha": 200, "side": -1, "center": False, "perspective": {"left_height": 1.0, "right_height": 0.7}}
            visual._paint_album_island_slot(painter, slot, 6)
            self.assertEqual(painter.worldTransform(), parent)
            slot.update({"center": True, "side": 0})
            visual._paint_album_island_slot(painter, slot, 8)
            self.assertEqual(painter.worldTransform(), parent)
        self.assertEqual([call[0] for call in calls], ["side", "center"])
        self.assertFalse(calls[0][1].isAffine())
        self.assertEqual(calls[1][1], parent)
        self.assertTrue(all(call[2] for call in calls))

    def test_seven_members_reuse_scaled_covers_across_frames_and_both_sizes(self):
        visual = self.visual()
        visual.album_island_cover_count = 7
        visual.album_island_spacing = 112
        visual.album_island_expanded_cover_size = 96
        visual.album_island_compact_cover_size = 40
        visual.album_art = QImage(1200, 1200, QImage.Format.Format_ARGB32)
        visual.album_art.fill(QColor(90, 140, 210))
        visual.album_history = []
        visual.album_queue = []
        for index in range(3):
            for entries, offset in ((visual.album_history, 0), (visual.album_queue, 80)):
                art = QImage(128, 128, QImage.Format.Format_ARGB32)
                art.fill(QColor(30+index*20+offset, 90, 150))
                entries.append({"track_id": f"{offset}-{index}", "album_art": art})
        visual.expanded = True
        expanded = visual._album_island_slots(QRectF(0, 0, 800, 130))
        visual.expanded = False
        compact = visual._album_island_slots(QRectF(0, 0, 400, 60))
        image = QImage(800, 150, QImage.Format.Format_ARGB32)
        scaled = QImage.scaled
        calls = []

        def record_resize(source, *args, **kwargs):
            calls.append(source.cacheKey())
            return scaled(source, *args, **kwargs)

        with patch.object(QImage, "scaled", new=record_resize):
            with QPainter(image) as painter:
                visual._paint_album_island_slots(painter, expanded)
                self.assertEqual(len(calls), 7)
                visual._paint_album_island_slots(painter, expanded)
                self.assertEqual(len(calls), 7)
                visual._paint_album_island_slots(painter, compact)
                self.assertEqual(len(calls), 14)
                visual._paint_album_island_slots(painter, expanded)
                visual._paint_album_island_slots(painter, compact)
                self.assertEqual(len(calls), 14)
        self.assertEqual(len(visual._scaled_album_art_cache), 14)

    def test_scaled_cover_cache_stays_bounded_after_many_track_changes(self):
        visual = self.visual()
        image = QImage(120, 120, QImage.Format.Format_ARGB32)
        with QPainter(image) as painter:
            for index in range(60):
                art = QImage(128, 128, QImage.Format.Format_ARGB32)
                art.fill(QColor(index*4, 110, 180))
                visual._paint_album_island_slot(painter, {
                    "rect": QRectF(15, 15, 72, 72), "album_art": art,
                    "alpha": 207, "center": False, "side": -1,
                    "perspective": {"left_height": 1.0, "right_height": 0.79},
                }, 6)
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
