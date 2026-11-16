"""Magnet dwell, playlist duplicate guard, and scaled cover corners."""
from __future__ import annotations

import unittest

from PySide6.QtCore import QRectF

from local.spring_motion import (
    COVER_CORNER_RATIO, CriticalSpring, MagnetLatch, RectSpring,
    docked_island_rect, magnet_fade_opacity, magnet_tile_rect, rect_width_overlap,
    scaled_cover_corner_radius, song_already_in_playlist, squircle_path,
)


class MagnetLatchTests(unittest.TestCase):
    def test_snap_requires_half_width_inside_two_seconds(self):
        latch = MagnetLatch()
        latch.observe(0, 0, 0.2, 0.0)
        latch.observe(2, 1, 0.62, 0.4)
        self.assertEqual(latch.decision(), "snap")
        self.assertEqual(latch.caption(), "松手吸附")
        self.assertGreaterEqual(rect_width_overlap(QRectF(0, 0, 100, 40), QRectF(40, 0, 100, 40)), 0.5)

    def test_an_island_pulled_off_the_bar_does_not_count_as_over_the_slot(self):
        from local.spring_motion import magnet_overlap
        tile = QRectF(1000, 0, 400, 36)                       # the Finder bar slot
        over = QRectF(1050, 4, 300, 30)
        self.assertGreater(magnet_overlap(over, tile), 0.5)
        pulled_down = QRectF(1050, 120, 300, 30)              # same x, dragged out below
        self.assertEqual(magnet_overlap(pulled_down, tile), 0.0)
        half_off = QRectF(1050, 18, 300, 30)                  # 18 of its 30 px still on the bar
        self.assertAlmostEqual(magnet_overlap(half_off, tile), 0.6, places=2)
        sideways = QRectF(1250, 4, 300, 30)                   # half its width past the slot
        self.assertAlmostEqual(magnet_overlap(sideways, tile), 0.5, places=2)

    def test_resting_past_two_seconds_stays_even_at_near_full_overlap(self):
        latch = MagnetLatch()
        latch.observe(0, 0, 0.99, 0.0)
        latch.observe(1, 0, 0.99, 2.01)
        self.assertTrue(latch.pinned)
        self.assertEqual(latch.decision(), "stay")
        self.assertEqual(latch.caption(), "")

    def test_finished_dwell_fades_the_same_blue_tile_away(self):
        self.assertEqual(magnet_fade_opacity(0.0), 1.0)
        self.assertGreater(magnet_fade_opacity(0.05), magnet_fade_opacity(0.16))
        self.assertEqual(magnet_fade_opacity(1.0), 0.0)
        early = MagnetLatch()
        early.observe(0, 0, 0.8, 0.0)
        early.observe(0, 0, 0.8, 1.0)
        self.assertEqual(early.decision(), "snap")
        self.assertEqual(early.caption(), "松手吸附")

    def test_faded_tile_stays_blue_and_then_disappears(self):
        from PySide6.QtGui import QColor, QImage
        from PySide6.QtWidgets import QApplication
        from local.island_stack_visual import TaskbarDropOverlay
        QApplication.instance() or QApplication([])
        overlay = TaskbarDropOverlay()
        overlay.magnet_local = QRectF(16, 16, 220, 72)
        overlay.magnet_caption = ""
        overlay.magnet_pinned = True
        overlay.resize(260, 110)

        def sample(opacity: float) -> QColor:
            overlay.magnet_opacity = opacity
            image = QImage(overlay.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(QColor(0, 0, 0, 0))
            overlay.render(image)
            return image.pixelColor(120, 52)

        full = sample(1.0)
        mid = sample(0.35)
        gone = sample(0.0)
        self.assertGreater(full.blue(), full.red() + 30)
        self.assertGreater(full.alpha(), mid.alpha())
        self.assertGreater(mid.blue(), mid.red())
        self.assertLess(gone.alpha(), 8)

    def test_jitter_inside_eight_pixels_does_not_reset_or_flicker_the_hint(self):
        latch = MagnetLatch()
        latch.observe(0, 0, 0.52, 0.0)
        latch.observe(1, 0, 0.47, 0.2)
        latch.observe(0, 1, 0.44, 0.4)
        self.assertTrue(latch.armed)
        self.assertEqual(latch.caption(), "松手吸附")
        latch.observe(0, 1, 0.30, 0.5)
        self.assertFalse(latch.armed)
        self.assertEqual(latch.caption(), "吸附")
        held = MagnetLatch()
        held.observe(10, 10, 0.9, 0.0)
        held.observe(16, 14, 0.9, 2.4)
        self.assertTrue(held.pinned)
        self.assertEqual(held.caption(), "")
        held.observe(12, 12, 0.2, 2.5)
        self.assertTrue(held.pinned)
        self.assertEqual(held.caption(), "")

    def test_a_real_move_restarts_the_dwell(self):
        latch = MagnetLatch()
        latch.observe(0, 0, 0.9, 0.0)
        latch.observe(1, 0, 0.9, 2.4)
        self.assertTrue(latch.pinned)
        latch.observe(40, 0, 0.9, 2.5)
        self.assertFalse(latch.pinned)
        self.assertEqual(latch.elapsed, 0.0)
        self.assertEqual(latch.decision(), "snap")

    def test_spring_settles_without_overshoot(self):
        spring = RectSpring()
        spring.start(QRectF(0, 0, 10, 10), QRectF(40, 0, 10, 10))
        peak = 0.0
        for _ in range(80):
            rect = spring.step(0.016)
            peak = max(peak, rect.x())
        self.assertLessEqual(peak, 40.5)
        self.assertFalse(spring.active)
        icon = CriticalSpring()
        icon.set_target(True)
        icon_peak = 0.0
        for _ in range(80):
            icon.step(0.016)
            icon_peak = max(icon_peak, icon.value)
        self.assertLessEqual(icon_peak, 1.02)
        mid = icon.value
        icon.set_target(False)
        icon.step(0.016)
        self.assertLess(icon.value, mid)

    def test_tiles_sit_on_the_top_and_bottom_centers(self):
        screen = QRectF(0, 0, 1920, 1080)
        finder = magnet_tile_rect(screen, mode="finder", bar=QRectF(0, 0, 1920, 48))
        taskbar = magnet_tile_rect(screen, mode="taskbar", bar=QRectF(0, 1040, 1920, 40))
        self.assertAlmostEqual(finder.center().x(), screen.center().x(), delta=1)
        self.assertLess(finder.center().y(), screen.height() * 0.2)
        self.assertAlmostEqual(taskbar.center().x(), screen.center().x(), delta=1)
        self.assertGreater(taskbar.center().y(), screen.height() * 0.8)
        island = QRectF(100, 100, 248, 64)
        docked = docked_island_rect(island, finder)
        self.assertEqual(docked.size(), island.size())
        self.assertAlmostEqual(docked.center().x(), finder.center().x(), delta=0.1)


class CoverRadiusTests(unittest.TestCase):
    def test_radius_scales_with_the_cover_and_never_becomes_a_circle(self):
        large = scaled_cover_corner_radius(92)
        small = scaled_cover_corner_radius(36)
        self.assertAlmostEqual(large / 92, COVER_CORNER_RATIO)
        self.assertAlmostEqual(small / 36, COVER_CORNER_RATIO)
        self.assertAlmostEqual(large / 92, small / 36)
        self.assertLess(small, 36 * 0.25)
        self.assertLess(large, 92 * 0.5)
        squircle = squircle_path(QRectF(0, 0, 36, 36))
        self.assertFalse(squircle.isEmpty())
        self.assertGreater(squircle.boundingRect().width(), 30)


class TranslationTurnTests(unittest.TestCase):
    def test_letters_orbit_clockwise_and_stay_apart(self):
        import math
        from local.translation_icon import AYE_CENTER, ICON_CENTER, ZHONG_CENTER, letter_pose
        gap0 = math.hypot(AYE_CENTER.x() - ZHONG_CENTER.x(), AYE_CENTER.y() - ZHONG_CENTER.y())
        for step in range(9):
            zhong, aye = letter_pose(step / 8.0)
            gap = math.hypot(aye.x() - zhong.x(), aye.y() - zhong.y())
            self.assertAlmostEqual(gap, gap0, delta=0.05)
        off_zhong, off_aye = letter_pose(0.0)
        quarter_zhong, _quarter_aye = letter_pose(0.5)
        on_zhong, on_aye = letter_pose(1.0)
        back_zhong, back_aye = letter_pose(2.0)
        self.assertAlmostEqual(off_zhong.x(), ZHONG_CENTER.x(), places=4)
        self.assertAlmostEqual(off_aye.y(), AYE_CENTER.y(), places=4)
        # Ninety degrees clockwise carries 中 across to the right.
        self.assertGreater(quarter_zhong.x(), off_zhong.x() + 200)
        # Half a turn parks 中 at the lower right and A at the upper left.
        self.assertGreater(on_zhong.x(), ICON_CENTER.x())
        self.assertGreater(on_zhong.y(), ICON_CENTER.y())
        self.assertLess(on_aye.x(), ICON_CENTER.x())
        self.assertLess(on_aye.y(), ICON_CENTER.y())
        self.assertAlmostEqual(on_zhong.x(), 1024.0 - ZHONG_CENTER.x(), places=3)
        self.assertAlmostEqual(on_zhong.y(), 1024.0 - ZHONG_CENTER.y(), places=3)
        self.assertAlmostEqual(back_zhong.x(), ZHONG_CENTER.x(), places=3)
        self.assertAlmostEqual(back_aye.y(), AYE_CENTER.y(), places=3)

    def test_letters_stay_upright_while_arcs_trade_corners(self):
        from PySide6.QtGui import QPainterPath
        from local.translation_icon import ARC_LOWER, ARC_UPPER, ZHONG, spin
        turned = spin(180.0).map(ARC_LOWER)
        self.assertAlmostEqual(turned.boundingRect().x(), ARC_UPPER.boundingRect().x(), delta=1.5)
        self.assertAlmostEqual(turned.boundingRect().y(), ARC_UPPER.boundingRect().y(), delta=1.5)
        self.assertAlmostEqual(turned.boundingRect().width(), ARC_UPPER.boundingRect().width(), delta=1.5)
        home = ZHONG.boundingRect()
        sample = QPainterPath()
        sample.addRect(home.adjusted(home.width() * 0.2, home.height() * 0.2,
                                     -home.width() * 0.2, -home.height() * 0.2))
        # Translating the glyph onto its half-turn center does not spin that sample.
        from local.translation_icon import letter_pose
        start, _aye = letter_pose(0.0)
        landed, _aye = letter_pose(1.0)
        shift = landed - start
        moved = QPainterPath(ZHONG)
        moved.translate(shift.x(), shift.y())
        self.assertAlmostEqual(moved.boundingRect().center().x(), landed.x(), delta=0.2)
        self.assertAlmostEqual(moved.boundingRect().width(), ZHONG.boundingRect().width(), delta=0.01)
        self.assertTrue(sample.boundingRect().width() > 10)

    def test_rapid_clicks_keep_turning_clockwise(self):
        spring = CriticalSpring()
        spring.advance()
        self.assertEqual(spring.target, 1.0)
        spring.value = 0.35
        spring.velocity = 0.4
        spring.advance()
        self.assertEqual(spring.target, 2.0)
        self.assertAlmostEqual(spring.value, 0.35)
        self.assertAlmostEqual(spring.velocity, 0.4)
        spring.step(0.016)
        self.assertGreater(spring.value, 0.35)


class PlaylistDuplicateTests(unittest.TestCase):
    def test_contains_flag_and_remembered_id_both_block_another_add(self):
        self.assertFalse(song_already_in_playlist("12"))
        self.assertTrue(song_already_in_playlist("12", contains_track=True))
        self.assertTrue(song_already_in_playlist("12", remembered={"12"}))
        self.assertFalse(song_already_in_playlist("13", remembered={"12"}))


if __name__ == "__main__":
    unittest.main()
