from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from local.music_surface_window import MusicSurfaceWindow


class _TransitionOwner(QWidget):
    def __init__(self):
        super().__init__()
        self._music_windowed_preference = False
        self.restored = False
        self.media_track_id = "transition-fixture"
        self.track_title = "Geometry transition"
        self.artist = "GluSpot"
        self.album_color = QColor(40, 90, 170)
        self.secondary_color = QColor(20, 30, 70)
        self.album_art = QImage()
        # Four displayable rows keep this transition fixture in lyric mode;
        # shorter tracks intentionally use the centered album layout.
        self.timed_lyrics = [
            (0.0, "first line"), (3.0, "second line"),
            (6.0, "third line"), (9.0, "fourth line"),
        ]
        self.lyrics = [text for _started_at, text in self.timed_lyrics]
        self.media_position_seconds = 1.0
        self.media_is_playing = True

    def _estimated_media_position(self, _now):
        return self.media_position_seconds

    def _restore_island_from_music_surface(self):
        self.restored = True


class MusicSurfaceTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.window = MusicSurfaceWindow(self.owner)
        self.window._normal_geometry = QRect(120, 90, 960, 540)
        self.window.setGeometry(0, 0, 1280, 720)

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    @staticmethod
    def _snapshot(size=(1280, 720), color=QColor(18, 42, 76)) -> QImage:
        image = QImage(size[0], size[1], QImage.Format.Format_RGB32)
        image.fill(color)
        return image

    def _render(self) -> QImage:
        frame = QImage(self.window.size(), QImage.Format.Format_RGB32)
        frame.fill(QColor(0, 0, 0))
        self.window.render(frame)
        return frame

    def test_disposing_surface_preserves_shared_application_screen_wrapper(self):
        shared = QGuiApplication.primaryScreen()
        surface = MusicSurfaceWindow(self.owner)
        surface.setGeometry(shared.availableGeometry())
        try:
            self.assertIs(surface.screen(), shared)
        finally:
            surface.shutdown()
            surface.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertTrue(isValid(shared))
        self.assertIs(QGuiApplication.primaryScreen(), shared)

    def test_screen_query_uses_surface_location_and_primary_fallback(self):
        other_screen, primary = object(), object()
        center = self.window.frameGeometry().center()
        with patch.object(QGuiApplication, "screenAt", return_value=other_screen) as screen_at, \
                patch.object(QGuiApplication, "primaryScreen", return_value=primary) as primary_screen:
            self.assertIs(self.window.screen(), other_screen)
            screen_at.assert_called_once_with(center)
            primary_screen.assert_not_called()
        with patch.object(QGuiApplication, "screenAt", return_value=None), \
                patch.object(QGuiApplication, "primaryScreen", return_value=primary):
            self.assertIs(self.window.screen(), primary)

    def test_animation_frames_only_scale_snapshot_then_layout_once_at_target(self):
        snapshot = self._snapshot()
        self.window._fullscreen = True
        with patch.object(self.window, "_capture_transition_snapshot", return_value=snapshot) as capture:
            self.window.toggle_presentation_mode()
        capture.assert_called_once_with()
        self.window._mode_animation.stop()
        self.assertFalse(self.window._pending_fullscreen)
        self.assertFalse(self.window._geometry_transition_snapshot.isNull())

        self.window._lyric_layout_cache.clear()
        for geometry in (
            QRect(40, 30, 1200, 675),
            QRect(80, 60, 1080, 608),
            QRect(self.window._normal_geometry),
        ):
            self.window.setGeometry(geometry)
            rendered = self._render()
            self.assertEqual(rendered.pixelColor(rendered.width() // 2, rendered.height() // 2), QColor(18, 42, 76))
            self.assertEqual(self.window._lyric_layout_cache, {})

        self.window._finish_mode_transition()
        self.assertTrue(self.window._geometry_transition_snapshot.isNull())
        self.assertEqual(set(self.window._button_rects), {"return", "translation", "content_layout"})
        self._render()
        final_cache_size = len(self.window._lyric_layout_cache)
        self.assertEqual(final_cache_size, len(self.owner.timed_lyrics))
        self._render()
        self.assertEqual(len(self.window._lyric_layout_cache), final_cache_size)

    def test_reverse_transition_recaptures_visible_frame_and_close_cancels_state(self):
        first = self._snapshot(color=QColor(80, 20, 30))
        second = self._snapshot((1120, 630), QColor(20, 80, 30))
        self.window._fullscreen = False
        self.window.setGeometry(120, 90, 960, 540)
        original_normal = QRect(self.window.geometry())
        self.window._normal_geometry = QRect(original_normal)
        self.window._fullscreen_geometry = lambda: QRect(0, 0, 1920, 1080)

        with patch.object(self.window, "_capture_transition_snapshot", side_effect=(first, second)) as capture:
            self.window.toggle_presentation_mode()
            self.window._mode_animation.stop()
            self.window.setGeometry(0, 0, 1120, 630)
            self.window.toggle_presentation_mode()
        self.window._mode_animation.stop()

        self.assertEqual(capture.call_count, 2)
        self.assertFalse(self.window._pending_fullscreen)
        self.assertEqual(self.window._normal_geometry, original_normal)
        self.assertEqual(self.window._geometry_transition_snapshot.cacheKey(), second.cacheKey())

        self.window.return_to_island()
        self.assertTrue(self.owner.restored)
        self.assertIsNone(self.window._pending_fullscreen)
        self.assertTrue(self.window._geometry_transition_snapshot.isNull())
        self.assertEqual(self.window._mode_animation.state(), self.window._mode_animation.State.Stopped)

    def test_only_contextual_window_button_is_painted_and_window_title_double_clicks(self):
        self.window._fullscreen = True
        self._render()
        self.assertEqual(set(self.window._button_rects), {"mode", "translation", "content_layout"})

        self.window._fullscreen = False
        self._render()
        self.assertEqual(set(self.window._button_rects), {"return", "translation", "content_layout"})

        accepted = []
        title_double_click = SimpleNamespace(
            button=lambda: Qt.MouseButton.LeftButton,
            position=lambda: QPointF(100.0, 20.0),
            accept=lambda: accepted.append(True),
        )
        with patch.object(self.window, "toggle_presentation_mode") as toggle:
            self.window.mouseDoubleClickEvent(title_double_click)
        toggle.assert_called_once_with()
        self.assertEqual(accepted, [True])

        return_button = self.window._button_rects["return"]
        button_double_click = SimpleNamespace(
            button=lambda: Qt.MouseButton.LeftButton,
            position=return_button.center,
            accept=lambda: accepted.append(True),
        )
        with patch.object(self.window, "toggle_presentation_mode") as toggle:
            self.window.mouseDoubleClickEvent(button_double_click)
        toggle.assert_not_called()

    def test_island_handoff_uses_one_snapshot_surface_and_returns_after_morph(self):
        origin = QRect(420, 12, 240, 64)
        island_frame = self._snapshot((240, 64), QColor(4, 4, 4))
        self.window.show_from_island(
            fullscreen=False,
            origin_geometry=origin,
            origin_snapshot=island_frame,
        )
        self.window._mode_animation.stop()

        self.assertFalse(self.window._geometry_transition_snapshot.isNull())
        self.assertEqual(self.window.geometry(), origin)
        self.assertEqual(self.window._island_origin_snapshot.cacheKey(), island_frame.cacheKey())
        self.assertFalse(self.window._pending_fullscreen)

        self.window._finish_mode_transition()
        self.assertEqual(self.window.geometry(), self.window._normal_geometry)
        self.assertTrue(self.window._geometry_transition_snapshot.isNull())
        self.assertFalse(self.window._fullscreen)

        self.window.return_to_island()
        self.window._mode_animation.stop()
        self.assertTrue(self.window._island_returning)
        self.assertFalse(self.owner.restored)
        self.window._finish_mode_transition()
        self.assertTrue(self.owner.restored)
        self.assertFalse(self.window._island_returning)

    def test_geometry_transition_samples_intermediate_frames_without_lyric_work(self):
        snapshot = self._snapshot()
        self.window._fullscreen = True
        self.window._normal_geometry = QRect(120, 90, 960, 540)
        with patch("local.music_surface_window.time.monotonic", return_value=100.0), patch.object(
            self.window, "_capture_transition_snapshot", return_value=snapshot,
        ):
            self.window.toggle_presentation_mode()
        screen = SimpleNamespace(refreshRate=lambda: 170.0)
        with patch.object(self.window, "screen", return_value=screen), patch.object(
            self.window, "_advance_song_layout_build",
        ) as shape, patch.object(self.window, "_advance_lyric_blur_prewarm") as blur:
            self.window._song_layout_build = {"pending": True}
            for milliseconds in range(10, 70, 10):
                with patch("local.music_surface_window.time.monotonic", return_value=100.0 + milliseconds / 1000.0):
                    self.window._on_animation_tick()
            for milliseconds in (70, 76, 82, 88):
                with patch("local.music_surface_window.time.monotonic", return_value=100.0 + milliseconds / 1000.0):
                    self.window._on_animation_tick()
                self.assertEqual(self.window._mode_animation.currentTime(), milliseconds)
                self.assertGreater(self.window.width(), 960)
                self.assertLess(self.window.width(), 1280)
            shape.assert_not_called()
            blur.assert_not_called()
        self.assertLessEqual(self.window._timer.interval(), 6)
        self.window._song_layout_build = None
        for milliseconds in range(94, 340, 6):
            with patch("local.music_surface_window.time.monotonic", return_value=100.0 + milliseconds / 1000.0):
                self.window._on_animation_tick()
        with patch("local.music_surface_window.time.monotonic", return_value=100.340):
            self.window._on_animation_tick()
        self.assertIsNone(self.window._pending_fullscreen)
        self.assertEqual(self.window.geometry(), self.window._normal_geometry)

    def test_repeated_exit_keeps_one_animation_and_uses_current_island_target(self):
        original_island = QRect(420, 12, 240, 64)
        current_island = QRect(580, 30, 300, 80)
        self.window._island_origin_geometry = original_island
        self.owner._current_main_target_global_rect = lambda: QRectF(current_island)
        self.window.show()
        start = QRect(self.window.geometry())
        with patch.object(self.window, "_capture_transition_snapshot", return_value=self._snapshot()) as capture:
            self.window.return_to_island()
            self.window.return_to_island()
        self.assertEqual(capture.call_count, 1)
        self.assertEqual(self.window._mode_animation.startValue(), start)
        self.assertEqual(self.window._mode_animation.endValue(), current_island)
        self.assertFalse(self.owner.restored)
        self.window._finish_mode_transition()
        self.assertTrue(self.owner.restored)

    def test_hiding_a_pending_exit_cancels_its_late_island_restore(self):
        self.window._island_origin_geometry = QRect(420, 12, 240, 64)
        self.window.show()
        with patch.object(self.window, "_capture_transition_snapshot", return_value=self._snapshot()):
            self.window.return_to_island()
        self.assertTrue(self.window._island_returning)
        self.window.hide()
        self.assertFalse(self.window._island_returning)
        self.assertIsNone(self.window._pending_fullscreen)
        self.assertTrue(self.window._geometry_transition_snapshot.isNull())
        self.window._finish_mode_transition()
        self.assertFalse(self.owner.restored)

    def test_exit_animation_finishes_from_timer_and_restores_island_once(self):
        self.window._island_origin_geometry = QRect(420, 12, 240, 64)
        self.window.show()
        with patch("local.music_surface_window.time.monotonic", return_value=50.0), patch.object(
            self.window, "_capture_transition_snapshot", return_value=self._snapshot(),
        ):
            self.window.return_to_island()
        self.assertFalse(self.owner.restored)
        for milliseconds in range(10, 171, 10):
            with patch("local.music_surface_window.time.monotonic", return_value=50.0 + milliseconds / 1000.0):
                self.window._on_animation_tick()
        self.assertEqual(self.window._mode_animation.currentTime(), 170)
        self.assertFalse(self.owner.restored)
        for milliseconds in range(180, 341, 10):
            with patch("local.music_surface_window.time.monotonic", return_value=50.0 + milliseconds / 1000.0):
                self.window._on_animation_tick()
        self.assertTrue(self.owner.restored)
        self.assertFalse(self.window._island_returning)

    def test_closing_surface_uses_the_exit_animation_until_shutdown(self):
        self.window._island_origin_geometry = QRect(420, 12, 240, 64)
        self.window.show()
        with patch.object(self.window, "_capture_transition_snapshot", return_value=self._snapshot()):
            self.window.close()
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window._island_returning)
        self.assertFalse(self.owner.restored)
        self.window.shutdown()
        self.assertFalse(self.window._island_returning)
        self.assertFalse(self.window.isVisible())

    def test_long_frame_stall_keeps_a_visible_exit_before_restoring_island(self):
        self.window._island_origin_geometry = QRect(420, 12, 240, 64)
        self.window.show()
        with patch("local.music_surface_window.time.monotonic", return_value=100.0), patch.object(
            self.window, "_capture_transition_snapshot", return_value=self._snapshot(),
        ):
            self.window.return_to_island()
        with patch("local.music_surface_window.time.monotonic", return_value=102.0):
            self.window._on_animation_tick()
        self.assertGreater(self.window._mode_animation.currentTime(), 0)
        self.assertLessEqual(self.window._mode_animation.currentTime(), 34)
        self.assertTrue(self.window._island_returning)
        self.assertFalse(self.owner.restored)
        previous = self.window._mode_animation.currentTime()
        for index in range(1, 50):
            with patch("local.music_surface_window.time.monotonic", return_value=102.0 + index * 0.01):
                self.window._on_animation_tick()
            if self.owner.restored:
                break
            current = self.window._mode_animation.currentTime()
            self.assertGreaterEqual(current, previous)
            self.assertLessEqual(current - previous, 34)
            previous = current
        self.assertTrue(self.owner.restored)

    def test_reversal_inherits_motion_before_braking_toward_the_latest_target(self):
        self.window._fullscreen_geometry = lambda: QRect(0, 0, 1920, 1080)
        self.window._fullscreen = False
        self.window.setGeometry(120, 90, 960, 540)
        original = QRect(self.window.geometry())
        with patch("local.music_surface_window.time.monotonic", return_value=100.0), patch.object(
            self.window, "_capture_transition_snapshot", return_value=self._snapshot(),
        ):
            self.window.toggle_presentation_mode()
        self.window._mode_animation.setCurrentTime(110)
        visible = QRect(self.window.geometry())
        incoming = self.window._current_mode_velocity()
        self.assertGreater(incoming[2], 0.0)
        with patch("local.music_surface_window.time.monotonic", return_value=100.110), patch.object(
            self.window, "_capture_transition_snapshot", return_value=self._snapshot(),
        ):
            self.window.toggle_presentation_mode()
        self.assertEqual(self.window.geometry(), visible)
        self.assertEqual(self.window._current_mode_velocity(), incoming)
        self.assertEqual(self.window._mode_animation.endValue(), original)
        self.window._mode_animation.setCurrentTime(10)
        self.assertGreater(self.window.width(), visible.width())
        self.window._mode_animation.setCurrentTime(self.window.MODE_TRANSITION_MS)
        self.assertEqual(self.window.geometry(), original)
        self.assertFalse(self.window._fullscreen)
        self.assertIsNone(self.window._pending_fullscreen)

    def test_new_mode_request_cancels_a_return_completion(self):
        self.window._island_origin_geometry = QRect(420, 12, 240, 64)
        self.window.show()
        self.window.setGeometry(120, 90, 960, 540)
        saved_normal = QRect(self.window.geometry())
        with patch.object(self.window, "_capture_transition_snapshot", return_value=self._snapshot()):
            self.window.return_to_island()
            self.window._mode_animation.setCurrentTime(100)
            self.assertNotEqual(self.window.geometry(), saved_normal)
            self.window.toggle_presentation_mode()
        self.assertFalse(self.window._island_returning)
        self.assertTrue(self.window._pending_fullscreen)
        self.assertEqual(self.window._normal_geometry, saved_normal)
        self.window._mode_animation.setCurrentTime(self.window.MODE_TRANSITION_MS)
        self.assertFalse(self.owner.restored)
        self.assertTrue(self.window._fullscreen)
        self.assertIsNone(self.window._pending_fullscreen)
        with patch.object(self.window, "_capture_transition_snapshot", return_value=self._snapshot()):
            self.window.toggle_presentation_mode()
        self.assertEqual(self.window._mode_animation.endValue(), saved_normal)
        self.window._mode_animation.setCurrentTime(self.window.MODE_TRANSITION_MS)
        self.assertEqual(self.window.geometry(), saved_normal)
        self.assertFalse(self.owner.restored)

    def test_snapshot_mode_control_accepts_a_reversal_request(self):
        self.window._fullscreen = True
        self._render()
        with patch.object(self.window, "_capture_transition_snapshot", return_value=self._snapshot()):
            self.window.toggle_presentation_mode()
            self.window._mode_animation.setCurrentTime(100)
            point = self.window._transition_control_rects()["mode"].center()
            event = SimpleNamespace(
                button=lambda: Qt.MouseButton.LeftButton,
                position=lambda: point,
                accept=lambda: None,
            )
            self.window.mousePressEvent(event)
            self.window.mouseReleaseEvent(event)
        self.assertTrue(self.window._pending_fullscreen)
        self.assertFalse(self.window._island_returning)

    def test_window_title_double_click_retargets_an_inflight_snapshot(self):
        self.window._fullscreen = False
        # At DPR 2 the fixture's 1280x720 rectangle can already equal the
        # actual fullscreen destination, leaving no transition to reverse.
        self.window.setGeometry(self.window._normal_geometry)
        self._render()
        with patch.object(self.window, "_capture_transition_snapshot", return_value=self._snapshot()):
            self.window.toggle_presentation_mode()
            self.window._mode_animation.setCurrentTime(100)
            event = SimpleNamespace(
                button=lambda: Qt.MouseButton.LeftButton,
                position=lambda: QPointF(100.0, 10.0),
                accept=lambda: None,
            )
            self.window.mouseDoubleClickEvent(event)
        self.assertFalse(self.window._pending_fullscreen)

    def test_live_lyric_positions_inherit_the_final_scaled_snapshot(self):
        self.window._fullscreen = True
        painted = {}
        native = self.window._draw_future_wrapped_layout

        def record(painter, layout, origin, text, font, width, height, line_offsets=(), **kwargs):
            if text in {"second line", "third line", "fourth line"}:
                painted.setdefault(text, origin.y() + (line_offsets[0] if line_offsets else 0.0))
            return native(painter, layout, origin, text, font, width, height, line_offsets, **kwargs)

        with patch.object(self.window, "_draw_future_wrapped_layout", side_effect=record):
            with patch("local.music_surface_window.time.monotonic", return_value=100.0):
                self._render()
        old_painted = dict(painted)
        source_height = self.window.height()
        positions = tuple(self.window._logical_line_spring.positions)
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window.toggle_presentation_mode()
        with patch("local.music_surface_window.time.monotonic", return_value=100.340):
            self.window._mode_animation.setCurrentTime(self.window.MODE_TRANSITION_MS)
        scale = self.window.height() / source_height
        for actual, old in zip(self.window._logical_line_spring.positions, positions):
            self.assertAlmostEqual(actual, old * scale)
        painted.clear()
        with patch.object(self.window, "_draw_future_wrapped_layout", side_effect=record):
            with patch("local.music_surface_window.time.monotonic", return_value=100.340):
                self._render()
        self.assertEqual(set(painted), set(old_painted))
        self.assertTrue(painted)
        for text, old_top in old_painted.items():
            self.assertAlmostEqual(painted[text], old_top * scale, delta=0.01)

    def test_snapshot_recapture_keeps_original_live_lyric_coordinate_space(self):
        self.window._fullscreen = True
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self._render()
            original_positions = tuple(self.window._logical_line_spring.positions)
            self.window.toggle_presentation_mode()
        self.window._mode_animation.setCurrentTime(120)
        with patch("local.music_surface_window.time.monotonic", return_value=100.120):
            self.window.toggle_presentation_mode()
        self.window._mode_animation.setCurrentTime(70)
        with patch("local.music_surface_window.time.monotonic", return_value=100.190):
            self.window.toggle_presentation_mode()
        self.assertEqual(self.window._mode_lyric_snapshot_size.height(), 720)
        with patch("local.music_surface_window.time.monotonic", return_value=100.530):
            self.window._mode_animation.setCurrentTime(self.window.MODE_TRANSITION_MS)
        self.assertEqual(self.window.height(), 540)
        for actual, old in zip(self.window._logical_line_spring.positions, original_positions):
            self.assertAlmostEqual(actual, old * .75)

    def test_reopen_after_interrupted_windowing_rebases_hidden_lyric_pose(self):
        origin = QRect(420, 12, 240, 64)
        self.window._fullscreen = True
        self.window.show()
        self.window.setGeometry(0, 0, 1280, 720)
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self._render()
            positions = tuple(self.window._logical_line_spring.positions)
            self.window.toggle_presentation_mode()
        self.window._mode_animation.setCurrentTime(100)
        self.window._island_origin_geometry = origin
        with patch("local.music_surface_window.time.monotonic", return_value=100.100):
            self.window.return_to_island()
        with patch("local.music_surface_window.time.monotonic", return_value=100.440):
            self.window._mode_animation.setCurrentTime(self.window.MODE_TRANSITION_MS)
        self.window.hide()
        self.assertTrue(self.owner._music_windowed_preference)
        self.assertEqual(self.window._hidden_lyric_pose_size.height(), 720)
        with patch("local.music_surface_window.time.monotonic", return_value=101.0):
            self.window.show_from_island(origin_geometry=origin)
        self.assertEqual(self.window._mode_lyric_snapshot_size.height(), 540)
        for actual, old in zip(self.window._logical_line_spring.positions, positions):
            self.assertAlmostEqual(actual, old * .75)

    def test_reopen_without_origin_rebases_a_hidden_pose_before_first_live_paint(self):
        self.window._fullscreen = True
        self.window.show()
        self.window.setGeometry(0, 0, 1280, 720)
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self._render()
            positions = tuple(self.window._logical_line_spring.positions)
        self.window.hide()
        with patch("local.music_surface_window.time.monotonic", return_value=101.0):
            self.window.show_from_island(fullscreen=False)
            self._render()
        self.assertEqual(self.window.height(), 540)
        for actual, old in zip(self.window._logical_line_spring.positions, positions):
            self.assertAlmostEqual(actual, old * .75)


if __name__ == "__main__":
    unittest.main()
