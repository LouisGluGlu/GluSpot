from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.island_stack_visual import IslandKind, IslandStackVisual
from local.music_mode_controls import ArtworkHoverMotion, paint_presentation_glyph
from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_surface_transition import _TransitionOwner


class ArtworkHoverMotionTests(unittest.TestCase):
    def test_hover_overshoots_then_settles_at_all_refresh_rates(self):
        for rate in (30, 60, 120, 240):
            motion = ArtworkHoverMotion()
            motion.clear(10.0)
            values = []
            for tick in range(rate):
                motion.advance(True, 10.0+(tick+1)/rate)
                values.append(motion.scale)
            self.assertGreater(max(values), 1.085)
            self.assertLess(max(values), 1.095)
            self.assertAlmostEqual(motion.scale, 1.085, places=3)
            self.assertFalse(motion.active)

    def test_stall_and_reversal_preserve_current_pose(self):
        motion = ArtworkHoverMotion()
        motion.clear(10.0)
        for tick in range(6):
            motion.advance(True, 10.0+(tick+1)/60.0)
        before = motion.scale
        motion.advance(False, 12.0)
        self.assertLess(abs(motion.scale-before), 0.025)
        for tick in range(90):
            motion.advance(False, 12.0+(tick+1)/60.0)
        self.assertEqual(motion.scale, 1.0)
        motion.clear(20.0)
        self.assertFalse(motion.active)


class MusicModeRefinementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = _TransitionOwner()
        self.window = MusicSurfaceWindow(self.owner)
        self.window.setGeometry(100, 100, 960, 540)
        self.window._normal_geometry = QRect(self.window.geometry())

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _render(self):
        image = QImage(self.window.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        self.window.render(image)
        return image

    def test_window_has_native_and_painted_corners_matching_artwork(self):
        self.window._fullscreen = False
        self.window._sync_surface_shape()
        original, _ = self.window._cover_layout_endpoints()
        self.assertAlmostEqual(self.window._surface_corner_radius(), self.window._album_art_radius(original.width()))
        self.assertFalse(self.window.mask().isEmpty())
        self.assertFalse(self.window.mask().contains(self.window.rect().topLeft()))
        frame = self._render()
        self.assertEqual(frame.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(frame.pixelColor(frame.width()//2, 0).alpha(), 255)
        self.window._fullscreen = True
        self.window._sync_surface_shape()
        self.assertTrue(self.window.mask().isEmpty())
        self.assertEqual(self._render().pixelColor(0, 0).alpha(), 255)

    def test_backing_snapshot_is_opaque_at_corners_for_fullscreen_morph(self):
        self.window._sync_surface_shape()
        self.assertEqual(self._render().pixelColor(0, 0).alpha(), 0)
        snapshot = self.window._capture_transition_snapshot()
        self.assertEqual(snapshot.pixelColor(0, 0).alpha(), 255)
        self.assertFalse(self.window._capturing_transition_snapshot)

    def test_rapid_mode_reversal_keeps_corner_and_glyph_pose(self):
        self.window._fullscreen_geometry = lambda: QRect(0, 0, 1920, 1080)
        self.window.toggle_presentation_mode()
        self.window._mode_animation.setCurrentTime(110)
        corner = self.window._surface_corner_radius()
        glyph = self.window._mode_glyph_state()
        self.window.toggle_presentation_mode()
        self.assertAlmostEqual(self.window._surface_corner_radius(), corner)
        self.assertEqual(self.window._mode_glyph_state(), glyph)
        self.window._mode_animation.setCurrentTime(self.window.MODE_TRANSITION_MS)
        self.window._finish_mode_transition()
        self.assertFalse(self.window._fullscreen)
        self.assertEqual(self.window._mode_glyph_state(), (0.0, 0.0))

    def test_glyphs_distinguish_three_destinations_and_keep_painter_state(self):
        images = []
        for mode in ("fullscreen", "windowed", "island"):
            image = QImage(80, 80, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as painter:
                painter.setOpacity(0.8)
                paint_presentation_glyph(painter, QRectF(8, 8, 64, 64), mode, QColor("white"))
                self.assertEqual(painter.opacity(), 0.8)
            images.append(bytes(image.bits()))
        self.assertEqual(len(set(images)), 3)

    def test_popup_anchor_uses_visual_button_and_parent_lifecycle_is_immediate(self):
        panel = SimpleNamespace(isVisible=lambda: False, open_at=Mock(), dismiss=Mock())
        self.window._audio_output_panel = panel
        with patch.object(self.window, "_audio_output_available", return_value=True):
            self.window._toggle_audio_output_panel()
        self.assertIn("source_rect", panel.open_at.call_args.kwargs)
        self.window._dismiss_audio_output_panel()
        panel.dismiss.assert_called_with(immediate=False)
        self.window._dismiss_audio_output_panel(immediate=True)
        panel.dismiss.assert_called_with(immediate=True)
        self.window._audio_output_panel = None

    def test_compact_cover_hitbox_is_stable_and_hover_does_not_open_surface(self):
        fixture = SimpleNamespace(music_cover_rect=QRectF(10, 10, 40, 40),
                                  _main_kind=lambda: IslandKind.MUSIC,
                                  _taskbar_compact_active=lambda: False,
                                  motions={}, expanded=False)
        self.assertEqual(IslandStackVisual._hit_test(fixture, QPointF(25, 25)), "music:open_surface")
        fixture.hovered_key = "music:open_surface"
        fixture.hover_armed_key = "music:open_surface"
        fixture.hover_started_at = 10.0
        fixture._hover_auto_activation_allowed = lambda key: IslandStackVisual._hover_auto_activation_allowed(fixture, key)
        self.assertFalse(IslandStackVisual._hover_auto_activation_due(fixture, 12.0))

    def test_tasker_handles_keep_priority_over_a_cover_hitbox(self):
        fixture = SimpleNamespace(music_cover_rect=QRectF(10, 10, 40, 40),
                                  _main_kind=lambda: IslandKind.MUSIC,
                                  _taskbar_compact_active=lambda: True,
                                  _taskbar_selected_drop_zone_local_rect=lambda: QRectF(),
                                  _taskbar_left_handle_visible=lambda: True,
                                  _u=lambda value: value, music_button_rects={}, expanded=False,
                                  _taskbar_main_drag_key=lambda: "music")
        with patch("local.island_stack_visual.positioning_taskbar_compact_hit_test_key", return_value="taskbar:left-handle"):
            self.assertEqual(IslandStackVisual._hit_test(fixture, QPointF(25, 25)), "taskbar:left-handle")
        with patch("local.island_stack_visual.positioning_taskbar_compact_hit_test_key", return_value="music"):
            self.assertEqual(IslandStackVisual._hit_test(fixture, QPointF(25, 25)), "music:open_surface")

    def test_mode_handoff_cancels_hover_from_its_visible_offset(self):
        self.window._metadata_hit_rects = {"title": QRectF(10, 10, 200, 30)}
        self.window._metadata_overflows = {"title": 100.0}
        self.window._metadata_marquee_states = {"title": ("identity", "text", 10.0)}
        self.window._metadata_hover_field = "title"
        self.window._metadata_hover_started_at = 19.0
        before = self.window._metadata_marquee_pose("title", 100.0, 20.0)[0]
        snapshot = QImage(self.window.size(), QImage.Format.Format_RGB32)
        snapshot.fill(QColor("black"))
        with patch("local.music_surface_window.time.monotonic", return_value=20.0), \
                patch.object(self.window, "_capture_transition_snapshot", return_value=snapshot):
            self.window.toggle_presentation_mode()
        self.assertEqual(self.window._metadata_hover_field, "")
        self.assertAlmostEqual(self.window._metadata_marquee_pose("title", 100.0, 20.0)[0], before)
        self.assertEqual(self.window._metadata_marquee_states["title"][2], 20.45)

    def test_cover_paint_keeps_motion_and_parent_clip(self):
        motion = ArtworkHoverMotion()
        motion.value = motion.target = 1.0
        fixture = SimpleNamespace(_music_cover_hover_motion=motion, _music_windowed_preference=False)
        from PySide6.QtGui import QPainterPath
        def rounded(rect, radius):
            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            return path
        fixture._rounded_path = rounded
        fixture._paint_cover = lambda painter, rect, radius, image, key: painter.fillPath(rounded(rect, radius), QColor(240, 170, 110))
        frame = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
        frame.fill(Qt.GlobalColor.transparent)
        with QPainter(frame) as painter:
            painter.setClipRect(QRectF(0, 0, 45, 100))
            IslandStackVisual._paint_interactive_music_cover(fixture, painter, QRectF(20, 20, 60, 60), 9)
        self.assertEqual(motion.value, 1.0)
        self.assertEqual(frame.pixelColor(60, 40).alpha(), 0)
        self.assertGreater(frame.pixelColor(35, 40).alpha(), 0)

    def test_suppression_clears_hover_without_replaying_when_returned(self):
        motion = ArtworkHoverMotion()
        motion.value = motion.target = 1.0
        fixture = SimpleNamespace(_music_cover_hover_motion=motion, scene_suppressed=True,
                                  _music_surface_active=False)
        self.assertTrue(IslandStackVisual._advance_music_cover_hover(fixture, 20.0))
        self.assertEqual(motion.value, 0.0)
        self.assertFalse(motion.active)


if __name__ == "__main__":
    unittest.main()
