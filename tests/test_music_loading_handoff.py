from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from tests.test_music_centered_layout import _Owner
from local.music_surface_window import MusicSurfaceWindow


class MusicLoadingHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _Owner()
        self.owner.media_is_playing = False
        self.owner.timed_lyrics = [(float(i * 4), f"Old verse {i}") for i in range(5)]
        self.owner.lyrics = [text for _at, text in self.owner.timed_lyrics]
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.column = QRectF(420, 30, 480, 480)
        self.render(100.0)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def render(self, now):
        image = QImage(self.window.size(), QImage.Format.Format_RGB32)
        with patch("local.music_surface_window.time.monotonic", return_value=now):
            self.window.render(image)
        return image

    def begin(self, now=101.0, key="new-song"):
        self.owner.media_track_id = key
        self.owner.track_title = key
        self.owner.timed_lyrics = []
        self.owner.lyrics = ["查找歌词中"]
        self.render(now)

    def resolve(self, now, *, instrumental=False):
        self.owner.timed_lyrics = [] if instrumental else [
            (float(i * 4), f"New verse {i}") for i in range(5)
        ]
        self.owner.lyrics = ["暂无歌词"] if instrumental else [
            text for _at, text in self.owner.timed_lyrics
        ]
        self.render(now)

    def skeleton(self, now):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with patch("local.music_surface_window.time.monotonic", return_value=now), QPainter(image) as painter:
            self.window._paint_lyric_loading(painter, self.column, now, 1.0)
        return image

    def test_fast_result_skips_skeleton_and_does_not_wait_for_a_cycle(self):
        self.begin()
        for now in (101.0, 101.5, 101.99):
            self.assertFalse(self.window._lyric_loading_visible(now))
        self.resolve(101.8)
        self.assertEqual(self.window._lyric_loading_release_at, 0.0)
        self.assertTrue(self.window._has_timed_lyrics)
        self.assertTrue(self.window._lyric_hit_rects)

    def test_online_original_provenance_finishes_preview_only_when_image_changes(self):
        for source in ("netease-original", "cover-art-archive-original"):
            with self.subTest(source=source):
                self.owner.media_track_id = source
                self.owner.album_art = QImage(160, 160, QImage.Format.Format_RGB32)
                self.owner.album_art.fill(QColor(80, 50, 70))
                self.owner.album_art_source = "thumbnail"
                self.render(101.0)
                self.assertTrue(self.window._art_loading_waiting)
                self.owner.album_art_source = source
                self.render(101.1)
                self.assertTrue(self.window._art_loading_waiting)
                self.owner.album_art = QImage(1200, 1200, QImage.Format.Format_RGB32)
                self.owner.album_art.fill(QColor(80, 50, 70))
                self.render(101.2)
                self.assertFalse(self.window._art_loading_waiting)
                self.assertFalse(self.window._should_blur_preview(self.owner.album_art))

    def test_loaded_lyrics_wait_for_current_cycle_and_keep_current_media_time(self):
        self.begin()
        self.render(102.2)
        self.assertTrue(self.window._lyric_loading_visible(102.2))
        self.resolve(102.6)
        self.assertAlmostEqual(self.window._lyric_loading_release_at, 105.0)
        self.assertFalse(self.window._has_timed_lyrics)
        self.assertEqual(self.window._lyric_hit_rects, {})
        self.owner.media_position_seconds = 8.5
        self.render(105.01)
        self.assertTrue(self.window._has_timed_lyrics)
        self.assertEqual(self.window._lyric_motion.active_index, 2)

    def test_instrumental_centers_only_after_visible_cycle_disappears(self):
        self.begin()
        self.render(102.2)
        self.resolve(102.7, instrumental=True)
        self.assertEqual(self.window._cover_layout_to, 0.0)
        self.assertTrue(self.window._lyric_loading_visible(104.9))
        self.render(105.01)
        self.assertEqual(self.window._cover_layout_to, 1.0)
        self.assertFalse(self.window._has_timed_lyrics)

    def test_resolved_long_song_prewarms_during_cycle_without_painting_or_seek_targets(self):
        self.begin()
        self.render(102.2)
        self.owner.timed_lyrics = [(float(i * 4), f"Loaded verse {i} continues softly") for i in range(120)]
        self.owner.lyrics = [text for _at, text in self.owner.timed_lyrics]
        with patch.object(self.window, "_paint_wrapped_lifted_lyric") as word_paint:
            self.render(102.6)
            for tick in range(24):
                now = 102.7 + tick * 0.01
                with patch("local.music_surface_window.time.monotonic", return_value=now):
                    self.window._on_animation_tick()
                self.render(now)
        word_paint.assert_not_called()
        self.assertIsNone(self.window._song_layout_build)
        self.assertEqual(len(self.window._song_layouts), 120)
        prepared_key = self.window._song_layout_key
        self.assertFalse(self.window._has_timed_lyrics)
        self.assertEqual(self.window._lyric_hit_rects, {})
        self.assertIsNone(self.window._last_visible_lyric_snapshot)
        self.render(105.01)
        self.assertEqual(self.window._song_layout_key, prepared_key)
        self.assertTrue(self.window._has_timed_lyrics)
        self.assertTrue(self.window._lyric_hit_rects)

    def test_result_at_cycle_end_retains_full_fade_without_starting_another_cycle(self):
        for result_at in (104.95, 105.0):
            with self.subTest(result_at=result_at):
                self.begin(101.0, f"late-{result_at}")
                self.render(102.2)
                self.resolve(result_at)
                self.assertAlmostEqual(self.window._lyric_loading_exit_started_at, result_at)
                self.assertAlmostEqual(self.window._lyric_loading_release_at, result_at + 0.5)
                with patch("local.music_surface_window.time.monotonic", return_value=105.1):
                    cycle, phase = self.window._lyric_loading_phase(105.1)
                self.assertEqual(cycle, 0)
                self.assertAlmostEqual(phase, 1.0, places=4)
                self.assertTrue(self.window._lyric_loading_visible(result_at + 0.25))
                self.assertAlmostEqual(self.window._lyric_loading_reveal(result_at + 0.25)[1], 0.5)
                self.render(result_at + 0.51)
                self.assertFalse(self.window._lyric_loading_visible(result_at + 0.51))
                self.assertTrue(self.window._has_timed_lyrics)

    def test_centered_previous_track_never_shows_skeleton_during_next_lookup(self):
        self.owner.timed_lyrics = []
        self.owner.lyrics = ["暂无歌词"]
        self.render(100.1)
        self.render(100.8)
        self.begin()
        with patch.object(self.window, "_paint_lyric_loading") as loading:
            self.render(104.4)
        loading.assert_not_called()
        self.assertEqual(self.window._cover_layout_to, 1.0)
        self.resolve(104.5)
        self.assertEqual(self.window._cover_layout_to, 0.0)
        self.assertAlmostEqual(self.window._cover_layout_fraction_at(104.5), 1.0)

    def test_rapid_track_change_cancels_previous_cycle_hold(self):
        self.begin()
        self.render(102.2)
        self.resolve(102.6)
        self.begin(102.8, "third-song")
        self.resolve(103.1)
        self.assertEqual(self.window._lyric_loading_release_at, 0.0)
        self.assertTrue(self.window._has_timed_lyrics)
        self.assertEqual(self.window._lyric_loading_track_key, "third-song")

    def test_paused_cycle_release_requests_a_repaint(self):
        self.begin()
        self.render(102.2)
        self.resolve(102.6)
        with patch("local.music_surface_window.time.monotonic", return_value=105.01), patch.object(
            self.window, "update",
        ) as repaint:
            self.window._on_animation_tick()
        repaint.assert_called()
        self.render(105.02)
        self.assertTrue(self.window._has_timed_lyrics)

    def test_loading_diagonal_reveal_and_exit_start_from_bottom_right(self):
        self.begin()
        initial, entering, full = (self.skeleton(at) for at in (101.9, 102.25, 102.6))
        scale = self.window._surface_scale()
        height = min(42 * scale, max(17 * scale, self.column.height() * 0.067))
        step = min(height * 1.75, self.column.height() / 7.45)
        top = self.column.center().y() - 3 * step - height * 0.5
        first = (round(self.column.left() + 16), round(top + height * 0.5))
        last = (round(self.column.left() + self.column.width() * 0.78), round(top + 6 * step + height * 0.5))
        self.assertEqual(initial.pixelColor(*last).alpha(), 0)
        self.assertGreater(entering.pixelColor(*last).alpha(), entering.pixelColor(*first).alpha())
        self.assertGreater(full.pixelColor(*first).alpha(), 0)
        self.resolve(102.7)
        exiting = self.skeleton(104.7)
        _cycle, phase = self.window._lyric_loading_phase(104.7)
        exit_first = (first[0], round(top + (1 - phase) * step + height * 0.5))
        exit_last = (last[0], round(top + (6 - phase) * step + height * 0.5))
        # Compare each position with its own tint, since rows have different
        # base opacity and the last row moves in the final half-second.
        self.assertGreater(exiting.pixelColor(*exit_first).alpha(), 0)
        self.assertEqual(exiting.pixelColor(*exit_last).alpha(), 0)

    def test_ripple_cannot_paint_inside_transparent_cover_slot(self):
        self.owner.album_art = QImage()
        self.window._transport_feedback = {
            "art_loading": (100.0, self.window._transport_light_origin(100.0), 1.0, 1.72),
        }
        frame = QImage(self.window.size(), QImage.Format.Format_RGB32)
        frame.fill(QColor(30, 30, 30))
        at = 100.8
        with patch("local.music_surface_window.time.monotonic", return_value=at), QPainter(frame) as painter:
            self.window._paint_transport_spatial_light(painter, QRectF(self.window.rect()), at)
        cover = self.window._scaled_cover_rect(self.window._current_cover_rect(at), self.window._cover_scale_at(at))
        for x in range(round(cover.left() + 20), round(cover.right() - 20), 9):
            for y in range(round(cover.top() + 20), round(cover.bottom() - 20), 9):
                self.assertEqual(frame.pixelColor(x, y), QColor(30, 30, 30))
        outside = sum(frame.pixelColor(x, y).red() > 30 for x in range(0, frame.width(), 7)
                      for y in range(0, frame.height(), 7))
        self.assertGreater(outside, 0)


if __name__ == "__main__":
    unittest.main()
