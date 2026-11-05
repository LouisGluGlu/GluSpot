"""J title styling and the shared K marquee for the window and the island."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRect, QRectF
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.island_stack_visual import IslandStackVisual
from local.metadata_marquee import (
    FRAME_SECONDS,
    brightened_cover_accent,
    cycle_seconds,
    marquee_pose,
)
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_surface_transition import _TransitionOwner


class MetadataMarqueeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(1000, 700)
        self.window._timer.stop()
        self.window._audio_output_status_timer.stop()

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def test_cycle_waits_scrolls_linearly_holds_the_tail_and_restarts(self):
        text_width, view = 420.0, 180.0
        duration = cycle_seconds(text_width)
        self.assertEqual(duration, 420.0 / 28.0)
        start = 0.14 * duration
        travel = 0.72 * duration
        overflow = text_width - view
        self.assertEqual(marquee_pose(text_width, view, 0.0).offset, 0.0)
        self.assertFalse(marquee_pose(text_width, view, start - 0.01).fade_left)
        self.assertTrue(marquee_pose(text_width, view, start - 0.01).fade_right)
        early = marquee_pose(text_width, view, start + travel * 0.25)
        late = marquee_pose(text_width, view, start + travel * 0.75)
        self.assertTrue(early.moving and late.moving)
        self.assertTrue(early.fade_left and early.fade_right)
        self.assertAlmostEqual(late.offset - early.offset, overflow * 0.5, delta=0.05)
        self.assertLessEqual(early.delay, FRAME_SECONDS)
        tail = marquee_pose(text_width, view, start + travel + 0.2)
        self.assertFalse(tail.moving)
        self.assertAlmostEqual(tail.offset, overflow)
        self.assertTrue(tail.fade_left)
        self.assertFalse(tail.fade_right)
        self.assertEqual(marquee_pose(text_width, view, duration).offset, 0.0)
        self.assertFalse(marquee_pose(80.0, 200.0, 3.0).moving)
        self.assertEqual(marquee_pose(80.0, 200.0, 3.0).delay, float("inf"))

    def test_short_text_uses_an_eight_second_cycle(self):
        self.assertEqual(cycle_seconds(28.0), 8.0)
        self.assertAlmostEqual(marquee_pose(100.0, 40.0, 0.14 * 8.0).offset, 0.0, delta=0.02)
        self.assertTrue(marquee_pose(100.0, 40.0, 0.14 * 8.0 + 0.05).moving)

    def test_neutral_cover_keeps_the_previous_artist_ink(self):
        self.assertIsNone(brightened_cover_accent(QColor()))
        self.assertIsNone(brightened_cover_accent(QColor(40, 40, 40)))
        self.assertEqual(self.window._metadata_artist_color(), QColor(255, 255, 255, 142))
        image = QImage(48, 48, QImage.Format.Format_RGB32)
        image.fill(QColor(20, 70, 180))
        self.owner.album_art = image
        accent = self.window._metadata_artist_color()
        self.assertGreater(accent.lightnessF(), 0.65)
        self.assertGreater(accent.blue(), accent.red())
        self.assertNotEqual(accent, QColor(255, 255, 255, 142))

    def test_wide_title_is_larger_and_semibold_without_growing_the_slot(self):
        self.window.resize(1000, 700)
        content, cover, _lyric = self.window._wide_content_column_geometry()
        unit = self.window._control_unit()
        title_font, title_rect, artist_font, _artist_rect = self.window._metadata_layout(
            cover, content, self.window._surface_scale(), 1.0,
        )
        self.assertEqual(title_font.weight(), QFont.Weight.DemiBold)
        self.assertLess(title_font.letterSpacing(), 0.0)
        self.assertAlmostEqual(title_font.pixelSize(), 3.1 * 1.15 * unit, delta=1.5)
        self.assertEqual(artist_font.weight(), QFont.Weight.Medium)
        base = self.window._surface_font(self.window.TITLE_PIXEL_SIZE * self.window._surface_scale(), QFont.Weight.Bold)
        layout_size = 3.1 * unit
        title_height = self.window._font_height(base) * layout_size / base.pixelSize()
        self.assertAlmostEqual(title_rect.height(), max(3.4 * unit, title_height + 2.0 * unit), delta=0.8)
        self.assertAlmostEqual(title_rect.left(), cover.left(), delta=0.6)

    def test_hidden_panel_does_not_refresh_and_motion_stays_at_thirty_fps(self):
        self.window._metadata_marquee_moving = True
        self.window._metadata_animation_rect = QRect(4, 4, 40, 16)
        with patch.object(self.window, "update") as update:
            self.assertFalse(self.window._refresh_metadata_marquee(10.0))
            update.assert_not_called()
        with (
            patch.object(self.window, "isVisible", return_value=True),
            patch.object(self.window, "isMinimized", return_value=False),
            patch.object(self.window, "update") as update,
        ):
            self.assertTrue(self.window._refresh_metadata_marquee(10.0))
            self.assertFalse(self.window._refresh_metadata_marquee(10.0 + FRAME_SECONDS * 0.4))
            self.assertTrue(self.window._refresh_metadata_marquee(10.0 + FRAME_SECONDS + 0.001))
            self.assertEqual(update.call_count, 2)
        self.window._metadata_marquee_moving = False
        self.window._metadata_animation_rect = QRect()
        self.window._metadata_next_motion_at = float("inf")
        with patch.object(self.window, "isVisible", return_value=True), patch.object(self.window, "update") as update:
            self.assertFalse(self.window._refresh_metadata_marquee(30.0))
            update.assert_not_called()

    def test_track_change_restarts_title_and_artist_separately(self):
        font = self.window._surface_font(22.0)
        slot = QRectF(8, 8, 80, 28)
        image = QImage(120, 80, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        title = "A very long title that cannot stay inside the slot"
        artist = "A"
        with QPainter(image) as painter, patch.object(self.window, "_control_reveal_at", return_value=1.0):
            self.window._paint_scrolling_metadata(painter, slot, title, font, 50.0, "song-a", field="title")
            self.window._paint_scrolling_metadata(painter, slot, artist, font, 50.0, "song-a", field="artist")
        self.assertGreater(self.window._metadata_overflows["title"], 1.0)
        self.assertEqual(self.window._metadata_overflows["artist"], 0.0)
        self.assertEqual(self.window._metadata_marquee_states["title"][2], 50.0)
        self.assertEqual(self.window._metadata_marquee_states["artist"][2], 50.0)
        with QPainter(image) as painter, patch.object(self.window, "_control_reveal_at", return_value=1.0):
            self.window._paint_scrolling_metadata(painter, slot, title, font, 80.0, "song-b", field="title")
        self.assertEqual(self.window._metadata_marquee_states["title"][2], 80.0)
        self.assertEqual(self.window._metadata_marquee_states["artist"][2], 50.0)

    def test_island_header_scrolls_independently_of_the_old_phase_and_resets(self):
        visual = IslandStackVisual()
        try:
            visual.timer.stop()
            visual.media_track_id = "song-a"
            visual.track_title = "Gold Up In My Teeth (Remastered 2024 Deluxe Edition)"
            visual.artist = "Haley"
            visual.scroll_phase = 4.2
            rect = QRectF(0, 0, 90, 24)
            image = QImage(140, 40, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(QColor(0, 0, 0, 0))
            with patch("local.island_stack_visual.time.monotonic", return_value=10.0), QPainter(image) as painter:
                visual._paint_island_metadata_line(
                    painter, rect, visual.track_title, 13, QColor("white"), True, "title",
                )
                visual._paint_island_metadata_line(
                    painter, QRectF(0, 26, 90, 16), visual.artist, 9, QColor(180, 180, 180), False, "artist",
                )
            self.assertEqual(visual._island_metadata_clocks["title"][1], 10.0)
            self.assertEqual(visual._island_metadata_clocks["artist"][1], 10.0)
            visual.scroll_phase = 80.0
            with patch("local.island_stack_visual.time.monotonic", return_value=10.0), QPainter(image) as painter:
                visual._paint_island_metadata_line(
                    painter, rect, visual.track_title, 13, QColor("white"), True, "title",
                )
            self.assertEqual(visual._island_metadata_clocks["title"][1], 10.0)
            visual.media_track_id = "song-b"
            with patch("local.island_stack_visual.time.monotonic", return_value=22.0), QPainter(image) as painter:
                visual._paint_island_metadata_line(
                    painter, rect, visual.track_title, 13, QColor("white"), True, "title",
                )
            self.assertEqual(visual._island_metadata_clocks["title"][1], 22.0)
            self.assertLess(visual._metadata_marquee_deadline, float("inf"))
        finally:
            visual.close()


if __name__ == "__main__":
    unittest.main()
