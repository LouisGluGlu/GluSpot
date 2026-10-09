from __future__ import annotations

import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QFont, QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication

from local.music_surface_window import MusicSurfaceWindow
from tests.test_audio_output_panel import _RouteProvider
from tests.test_music_surface_transition import _TransitionOwner


class MusicMetadataControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.window = MusicSurfaceWindow(self.owner)
        self.window.resize(960, 540)
        self.window._reset_control_idle(100.0)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _field(self, field, text, slot_width):
        self.window._metadata_marquee_states[field] = ("song", text, 100.0)
        self.window._metadata_slot_widths[field] = slot_width
        self.window._metadata_overflows[field] = self.window._metadata_control_overflow(field, 0.0)
        self.window._metadata_hit_rects[field] = QRectF(10, 10, slot_width, 30)

    def _revealing(self, now=102.0):
        window = self.window
        window._control_reveal_from = 0.0
        window._control_reveal_target = 0.0
        window._control_transition_started_at = 100.0
        with patch.object(window, "isActiveWindow", return_value=True):
            window._note_pointer_moved(now)

    def test_mouse_reveal_starts_each_overflow_field_once_after_controls_settle(self):
        for fullscreen in (False, True):
            with self.subTest(fullscreen=fullscreen):
                window = self.window
                window._fullscreen = fullscreen
                self._field("title", "A title that stays far wider than its slot", 50.0)
                self._field("artist", "An artist that stays far wider than its slot", 50.0)
                self._revealing()
                window._update_control_animation_regions(102.2)
                self.assertEqual(window._metadata_marquee_states["title"][2], 100.0)
                self.assertEqual(window._metadata_marquee_states["artist"][2], 100.0)
                window._update_control_animation_regions(102.6)
                for field in ("title", "artist"):
                    self.assertAlmostEqual(window._metadata_marquee_states[field][2], 87.6)
                    self.assertGreater(window._metadata_marquee_pose(
                        field, window._metadata_control_overflow(field, 1.0), 102.9,
                    )[0], 0.0)
                original = dict(window._metadata_marquee_states)
                with patch.object(window, "isActiveWindow", return_value=True):
                    window._note_pointer_moved(102.9)
                    window._note_pointer_moved(103.0)
                window._update_control_animation_regions(103.0)
                self.assertEqual(window._metadata_marquee_states, original)

    def test_name_fitting_visible_controls_waits_for_hide_then_scrolls_once(self):
        window = self.window
        text = "This title fits only when controls are visible"
        self._field("title", text, 1.0)
        shown_width = window._metadata_control_overflow("title", 1.0) + 1.0
        hidden_width = window._metadata_control_overflow("title", 0.0) + 1.0
        window._metadata_slot_widths["title"] = (shown_width + hidden_width) * 0.5
        self._field("artist", "Short", 900.0)
        self._revealing()
        window._update_control_animation_regions(102.6)
        self.assertEqual(window._metadata_marquee_states["title"][2], 100.0)
        self.assertEqual(window._metadata_scroll_after_hide, {"title": ("song", text)})
        self.assertEqual(window._metadata_marquee_pose("title", 0.0, 102.8), (0.0, False, float("inf")))
        window._update_control_animation_regions(104.2)
        self.assertEqual(window._metadata_marquee_states["title"][2], 100.0)
        window._update_control_animation_regions(104.6)
        self.assertAlmostEqual(window._metadata_marquee_states["title"][2], 89.6)
        self.assertGreater(window._metadata_marquee_pose(
            "title", window._metadata_control_overflow("title", 0.0), 104.9,
        )[0], 0.0)
        self.assertEqual(window._metadata_marquee_states["artist"][2], 100.0)
        self.assertEqual(window._metadata_scroll_after_hide, {})
        window._update_control_animation_regions(105.0)
        self.assertAlmostEqual(window._metadata_marquee_states["title"][2], 89.6)

    def test_names_fitting_both_views_have_no_active_or_deferred_trip(self):
        self._field("title", "Short", 900.0)
        self._field("artist", "Singer", 900.0)
        self._revealing()
        self.window._update_control_animation_regions(102.6)
        self.window._update_control_animation_regions(104.6)
        self.assertEqual(self.window._metadata_reveal_pending, {})
        self.assertEqual(self.window._metadata_scroll_after_hide, {})
        self.assertEqual(self.window._metadata_marquee_states["title"][2], 100.0)

    def test_track_change_during_reveal_does_not_scroll_old_or_new_song(self):
        self._field("title", "A very long title", 20.0)
        self._revealing()
        self.window._metadata_marquee_states["title"] = ("new-song", "Another long title", 102.3)
        self.window._update_control_animation_regions(102.6)
        self.assertEqual(self.window._metadata_marquee_states["title"][2], 102.3)
        self.assertEqual(self.window._metadata_reveal_pending, {})

    def test_fully_wrapped_centered_layout_does_not_trigger_hidden_normal_fields(self):
        self._field("title", "A previously overflowing normal title", 20.0)
        self.window._metadata_hit_rects.clear()
        self.window._metadata_slot_widths.clear()
        self._revealing()
        self.window._update_control_animation_regions(102.6)
        self.assertEqual(self.window._metadata_reveal_pending, {})
        self.assertEqual(self.window._metadata_scroll_after_hide, {})
        self.assertEqual(self.window._metadata_marquee_states["title"][2], 100.0)

    def test_controls_reveal_during_existing_trip_preserves_phase(self):
        self._field("title", "A very long title", 20.0)
        self.window._metadata_marquee_states["title"] = ("song", "A very long title", 86.2)
        self._revealing()
        self.window._update_control_animation_regions(102.6)
        self.assertEqual(self.window._metadata_marquee_states["title"][2], 86.2)

    def test_fit_current_viewport_never_moves_during_hover_or_return(self):
        self._field("title", "A long title", 100.0)
        self.window._metadata_returns["title"] = (102.0, 0.8)
        self.window._metadata_hover_field = "title"
        self.window._metadata_hover_started_at = 101.0
        self.assertEqual(self.window._metadata_marquee_pose("title", 0.0, 102.1),
                         (0.0, False, float("inf")))

    def test_viewport_change_does_not_restart_fifteen_second_auto_wait(self):
        image = QImage(320, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        text = "A title longer than either changing text viewport"
        font = self.window._surface_font(24.0, QFont.Weight.Bold)
        with patch.object(self.window, "_control_reveal_at", return_value=1.0):
            with QPainter(image) as painter:
                self.window._paint_scrolling_metadata(painter, QRectF(10, 10, 90, 30), text,
                                                      font, 100.0, "song", field="title")
                self.window._paint_scrolling_metadata(painter, QRectF(10, 10, 120, 30), text,
                                                      font, 110.0, "song", field="title")
                self.window._paint_scrolling_metadata(painter, QRectF(10, 10, 100, 30), text,
                                                      font, 115.2, "song", field="title")
        self.assertEqual(self.window._metadata_marquee_states["title"][2], 100.0)
        self.assertTrue(self.window._metadata_marquee_moving)

    def test_lyric_only_paint_keeps_metadata_hover_and_control_measurements(self):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window.render(image)
        before_hits = dict(self.window._metadata_hit_rects)
        before_widths = dict(self.window._metadata_slot_widths)
        self.assertEqual(set(before_hits), {"title", "artist"})
        with patch("local.music_surface_window.time.monotonic", return_value=100.1):
            self.window.render(image, QPoint(0, 0), QRegion(self.window._active_lyric_animation_rect))
        self.assertEqual(self.window._metadata_hit_rects, before_hits)
        self.assertEqual(self.window._metadata_slot_widths, before_widths)

    def test_fused_chooser_holds_controls_while_visible_and_closing_then_rearms_idle(self):
        window = self.window
        self.owner.data_provider = _RouteProvider()
        window.show()
        for fullscreen in (False, True):
            with self.subTest(fullscreen=fullscreen):
                window._fullscreen = fullscreen
                with patch("local.music_surface_window.time.monotonic", return_value=100.0), \
                        patch("local.audio_output_panel.time.monotonic", return_value=100.0):
                    window._toggle_audio_output_panel()
                panel = window._audio_output_panel
                panel._animation_timer.stop()
                panel._poll_timer.stop()
                panel._reveal = panel._reveal_target = 1.0
                panel._apply_reveal_pose()
                self.assertEqual(window._control_reveal_at(120.0), 1.0)
                self.assertEqual(window._control_reveal_target, 1.0)
                panel.dismiss()
                panel._animation_timer.stop()
                self.assertTrue(panel.is_closing)
                self.assertEqual(window._control_reveal_at(125.0), 1.0)
                with patch("local.music_surface_window.time.monotonic", return_value=126.0), \
                        patch.object(window, "isActiveWindow", return_value=True):
                    panel.dismiss(immediate=True)
                self.assertEqual(window._control_last_motion_at, 126.0)
                idle = (window.FULLSCREEN_CONTROL_IDLE_SECONDS if fullscreen
                        else window.WINDOWED_CONTROL_IDLE_SECONDS)
                self.assertEqual(window._control_reveal_at(126.0 + idle - 0.01), 1.0)
                self.assertEqual(window._control_reveal_at(126.0 + idle + 0.6), 0.0)

    def test_open_chooser_keeps_visible_metadata_measurement_and_auto_cycle(self):
        window = self.window
        self.owner.data_provider = _RouteProvider()
        window.show()
        with patch("local.music_surface_window.time.monotonic", return_value=100.0), \
                patch("local.audio_output_panel.time.monotonic", return_value=100.0):
            window._toggle_audio_output_panel()
        panel = window._audio_output_panel
        panel._animation_timer.stop()
        panel._poll_timer.stop()
        self._field("title", "A name that fits while controls show", 1.0)
        visible_width = window._metadata_control_overflow("title", 1.0) + 1.0
        hidden_width = window._metadata_control_overflow("title", 0.0) + 1.0
        window._metadata_slot_widths["title"] = (visible_width + hidden_width) / 2
        self._field("artist", "An artist too long for its tiny slot", 20.0)
        image = QImage(400, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as painter:
            for field, text in (("title", "A name that fits while controls show"),
                                ("artist", "An artist too long for its tiny slot")):
                weight = QFont.Weight.Bold if field == "title" else QFont.Weight.Medium
                pixels = window.TITLE_PIXEL_SIZE if field == "title" else window.ARTIST_PIXEL_SIZE
                font = window._surface_font(pixels * window._surface_scale(), weight)
                width = window._metadata_slot_widths[field]
                window._paint_scrolling_metadata(painter, QRectF(10, 10, width, 30),
                                                text, font, 115.2, "song", field=field)
        self.assertEqual(window._metadata_overflows["title"], 0.0)
        self.assertGreater(window._metadata_overflows["artist"], 0.0)
        self.assertGreater(window._metadata_marquee_pose(
            "artist", window._metadata_overflows["artist"], 115.2,
        )[0], 0.0)
        self.assertEqual(window._metadata_marquee_states["artist"][2], 100.0)


if __name__ == "__main__":
    unittest.main()
