from __future__ import annotations

import math
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from local.music_surface_window import MusicSurfaceWindow
from tests.test_owned_playback_surface import _Provider


class MusicLibraryButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.owner = QWidget()
        self.provider = _Provider()
        self.owner.data_provider = self.provider
        self.back = Mock()
        self.owner._music_library_back_callback = self.back
        self.owner.album_color = QColor("#2870a0")
        self.owner.secondary_color = QColor("#202d48")
        self.owner.album_art = QImage()
        self.owner.music_flow_intensity = 0.0
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window = MusicSurfaceWindow(self.owner)
            self.window.resize(960, 540)
            self.window.show()
            self.window._timer.stop()
            self.window._audio_output_status_timer.stop()
            self.window._sync_audio_output_status(settle=True)
            self.window._reset_control_idle(now=100.0)
            self.window._sync_window_control_rects()

    def tearDown(self):
        self.window.shutdown()
        self.owner.close()

    def _image(self, color=Qt.GlobalColor.transparent):
        dpr = self.window.devicePixelRatioF()
        image = QImage(math.ceil(self.window.width() * dpr), math.ceil(self.window.height() * dpr),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(color)
        return image

    @staticmethod
    def _physical_rect(rect, dpr):
        return QRectF(rect.x() * dpr, rect.y() * dpr,
                      rect.width() * dpr, rect.height() * dpr).toAlignedRect()

    def _mouse(self, kind, point):
        move = kind == QEvent.Type.MouseMove
        return QMouseEvent(kind, QPointF(point), QPointF(self.window.mapToGlobal(point.toPoint())),
                           Qt.MouseButton.NoButton if move else Qt.MouseButton.LeftButton,
                           Qt.MouseButton.LeftButton if kind == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton,
                           Qt.KeyboardModifier.NoModifier)

    def _click(self, point, now):
        with patch("local.music_surface_window.time.monotonic", return_value=now), \
                patch.object(self.window, "isActiveWindow", return_value=True):
            self.window.mousePressEvent(self._mouse(QEvent.Type.MouseButtonPress, point))
            self.window.mouseReleaseEvent(self._mouse(QEvent.Type.MouseButtonRelease, point))

    def _render(self, now):
        image = self._image()
        with patch("local.music_surface_window.time.monotonic", return_value=now):
            self.window.render(image, renderFlags=QWidget.RenderFlag.DrawWindowBackground | QWidget.RenderFlag.DrawChildren | QWidget.RenderFlag.IgnoreMask)
        return image

    def test_hover_expands_from_circle_and_leave_reverses_current_pose(self):
        rect = QRectF(self.window.rect())
        circle = self.window._library_button_face(rect, 0.0)
        self.assertAlmostEqual(circle.width(), circle.height())
        with patch("local.music_surface_window.time.monotonic", return_value=100.0), \
                patch.object(self.window, "isActiveWindow", return_value=True):
            self.window.mouseMoveEvent(self._mouse(QEvent.Type.MouseMove, circle.center()))
        self.assertEqual(self.window._library_expansion_at(100.0), 0.0)
        middle_at = 100.0 + self.window.LIBRARY_EXPANSION_SECONDS / 2
        middle = self.window._library_expansion_at(middle_at)
        self.assertGreater(middle, 0.0)
        self.assertLess(middle, 1.0)
        self.assertGreater(self.window._library_button_face(rect, middle).width(), circle.width())
        with patch("local.music_surface_window.time.monotonic", return_value=middle_at):
            self.window.leaveEvent(QEvent(QEvent.Type.Leave))
        self.assertAlmostEqual(self.window._library_expansion_at(middle_at), middle)
        self.assertLess(self.window._library_expansion_at(middle_at + .08), middle)
        self.assertEqual(self.window._library_expansion_at(middle_at + self.window.LIBRARY_EXPANSION_SECONDS), 0.0)
        self.assertEqual(self.provider.calls, [])

    def test_circle_and_capsule_hit_only_their_visible_rounded_shape(self):
        rect = QRectF(self.window.rect())
        for expansion in (0.0, .5, 1.0):
            with self.subTest(expansion=expansion):
                face = self.window._library_button_face(rect, expansion)
                corner = face.topLeft() + QPointF(.5, .5)
                self.assertTrue(face.contains(corner))
                self.assertFalse(self.window._window_control_contains("library", face, corner))
                self.assertTrue(self.window._window_control_contains("library", face, face.center()))
                self.assertTrue(self.window._window_control_contains("library", face,
                    QPointF(face.left() + face.height() / 2, face.top() + 2)))
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window._sync_window_control_rects()
        face = self.window._button_rects["library"]
        self._click(face.topLeft() + QPointF(.5, .5), 100.0)
        self.back.assert_not_called()
        with patch("local.music_surface_window.time.monotonic", return_value=100.0), \
                patch.object(self.window, "isActiveWindow", return_value=True):
            self.window.mouseMoveEvent(self._mouse(QEvent.Type.MouseMove, face.topLeft() + QPointF(.5, .5)))
            self.assertNotEqual(self.window._hovered_window_button, "library")
            with patch.object(self.window, "toggle_presentation_mode") as mode:
                self.window._fullscreen = False
                self.window.mouseDoubleClickEvent(self._mouse(QEvent.Type.MouseButtonDblClick, face.topLeft() + QPointF(.5, .5)))
                mode.assert_called_once_with()
                self.window.mouseDoubleClickEvent(self._mouse(QEvent.Type.MouseButtonDblClick, face.center()))
                mode.assert_called_once_with()
        self._click(face.center(), 100.1)
        self.back.assert_called_once_with()
        self.assertEqual(self.provider.calls, [])

    def test_shared_idle_hides_library_and_first_hidden_click_only_reveals(self):
        for fullscreen in (False, True):
            with self.subTest(fullscreen=fullscreen):
                self.back.reset_mock()
                self.window._fullscreen = fullscreen
                self.window._reset_control_idle(now=100.0)
                rect = QRectF(self.window.rect())
                point = self.window._library_button_face(rect, 0.0).center()
                idle = (self.window.FULLSCREEN_CONTROL_IDLE_SECONDS if fullscreen
                        else self.window.WINDOWED_CONTROL_IDLE_SECONDS)
                hidden_at = 100.0 + idle + self.window.FULLSCREEN_CONTROL_TRANSITION_SECONDS + .1
                with patch("local.music_surface_window.time.monotonic", return_value=hidden_at):
                    self.window._sync_window_control_rects()
                    self.assertEqual(self.window._button_rects, {})
                    image = self._image()
                    with QPainter(image) as painter:
                        self.window._paint_library_back(painter, rect)
                    self.assertFalse(any(image.constBits()))
                self._click(point, hidden_at)
                self.back.assert_not_called()
                visible_at = hidden_at + self.window.FULLSCREEN_CONTROL_TRANSITION_SECONDS + .1
                with patch("local.music_surface_window.time.monotonic", return_value=visible_at):
                    self.window._sync_window_control_rects()
                    self.assertIn("library", self.window._button_rects)
                self._click(self.window._button_rects["library"].center(), visible_at)
                self.back.assert_called_once_with()
        self.assertEqual(self.provider.calls, [])

    def test_snapshot_anisotropic_circle_uses_captured_shape_and_visibility(self):
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window._sync_window_control_rects()
            self.window._remember_transition_controls(self.window.size(), False)
        self.window._geometry_transition_snapshot = self._image()
        self.window._pending_fullscreen = True
        with patch.object(self.window, "width", return_value=1440), \
                patch.object(self.window, "height", return_value=540):
            face = self.window._transition_control_rects()["library"]
            self.assertNotAlmostEqual(face.width(), face.height())
            # This point is inside a capsule with the stretched rectangle,
            # but outside the ellipse actually shown by the snapshot.
            outside = face.center() + QPointF(face.width() * .35, face.height() * .4)
            self.assertTrue(face.contains(outside))
            self.assertFalse(self.window._transition_window_control_contains("library", face, outside))
            self._click(outside, 100.0)
            self.back.assert_not_called()
            self._click(face.center(), 100.1)
            self.back.assert_called_once_with()
            self.back.reset_mock()
            self.window._mode_snapshot_controls_clickable = False
            self._click(face.center(), 100.2)
            self.back.assert_not_called()
        self.assertEqual(self.provider.calls, [])

    def test_left_partial_repaint_matches_full_image_at_actual_native_dpr(self):
        self.window._fullscreen = False
        for instant in (100.18, 102.08):
            with self.subTest(instant=instant):
                self.window._reset_control_idle(now=100.0)
                self.window._set_library_expanded(True, 100.0)
                with patch("local.music_surface_window.time.monotonic", return_value=instant):
                    partial = self._render(instant)
                    dirty = self.window._library_damage_rect(QRectF(self.window.rect()))
                    with QPainter(partial) as painter:
                        painter.fillRect(dirty, QColor("magenta"))
                    with patch.object(self.window, "_paint_library_back", wraps=self.window._paint_library_back) as library, \
                            patch.object(self.window, "_paint_window_controls", wraps=self.window._paint_window_controls) as right:
                        self.window.render(partial, dirty.topLeft(), QRegion(dirty),
                            QWidget.RenderFlag.DrawWindowBackground | QWidget.RenderFlag.DrawChildren | QWidget.RenderFlag.IgnoreMask)
                    self.assertEqual(library.call_count, 1)
                    self.assertFalse(right.call_args.kwargs["paint_ink"])
                    full = self._render(instant)
                dpr = self.window.devicePixelRatioF()
                self.assertEqual(partial.devicePixelRatio(), dpr)
                self.assertEqual(partial.width(), math.ceil(self.window.width() * dpr))
                crop = self._physical_rect(dirty, dpr)
                partial_crop, full_crop = partial.copy(crop), full.copy(crop)
                self.assertEqual(bytes(partial_crop.constBits()), bytes(full_crop.constBits()))

    def test_expansion_tick_requests_left_damage_without_full_surface_update(self):
        self.window._set_library_expanded(True, 100.0)
        with patch.object(self.window, "update") as update:
            self.window._update_control_animation_regions(100.12)
        dirty = self.window._library_damage_rect(QRectF(self.window.rect()))
        self.assertIn(((dirty,), {}), [(call.args, call.kwargs) for call in update.call_args_list])
        self.assertNotIn(((), {}), [(call.args, call.kwargs) for call in update.call_args_list])

    def test_ripple_protects_visible_shape_and_releases_hidden_library_region(self):
        rect = QRectF(self.window.rect())
        face = self.window._library_button_face(rect, 0.0)
        dpr = self.window.devicePixelRatioF()
        center = QPointF(face.center().x() * dpr, face.center().y() * dpr).toPoint()
        corner = QPointF((face.left() + .5) * dpr, (face.top() + .5) * dpr).toPoint()
        self.window._transport_feedback = {"next": (100.0, QPointF(180, 260), 1.0, 4.0)}
        results = []
        for instant in (100.0, 103.0):
            image = self._image(QColor("#223344"))
            original = image.copy()
            # A uniform wave brush isolates the actual foreground clip from
            # the independently tested radial wave intensity profile.
            with patch("local.music_surface_window.time.monotonic", return_value=instant), \
                    patch.object(self.window, "_transport_feedback_frame", return_value=(1.0, 1.0, 1.0)), \
                    patch.object(self.window, "_transport_light_gradient", return_value=QColor(40, 50, 60)), \
                    patch.object(self.window, "_transport_light_origin", return_value=face.center()), \
                    patch.object(self.window, "_transport_light_max_radius", return_value=200.0):
                with QPainter(image) as painter:
                    self.window._paint_transport_spatial_light(painter, rect, instant)
            self.assertNotEqual(bytes(image.constBits()), bytes(original.constBits()))
            results.append((image, original))
        visible, original = results[0]
        hidden, _ = results[1]
        self.assertEqual(visible.pixelColor(center), original.pixelColor(center))
        self.assertNotEqual(visible.pixelColor(corner), original.pixelColor(corner))
        self.assertNotEqual(hidden.pixelColor(center), original.pixelColor(center))

    def test_library_animation_and_hidden_paints_keep_existing_cache_budget(self):
        self.window._control_layer_next_prewarm_at = math.inf
        self.window._set_library_expanded(True, 100.0)
        rect = QRectF(self.window.rect())

        def paint_at(instant):
            with patch("local.music_surface_window.time.monotonic", return_value=instant):
                image = self._image()
                with QPainter(image) as painter:
                    self.window._paint_library_back(painter, rect)
            return image

        def direct_visible_layer(painter, bounds, reveal, shift, key, draw, **kwargs):
            # The uncached fully visible path is the visual reference. Keep
            # the production geometry, glass and glyph drawing unchanged.
            self.assertGreaterEqual(reveal, .999)
            painter.save()
            painter.translate(shift)
            draw(painter)
            painter.restore()

        def assert_budget():
            self.assertLessEqual(len(self.window._control_layer_cache), 48)
            self.assertLessEqual(self.window._control_layer_cache_bytes, self.window.CONTROL_LAYER_CACHE_MAX_BYTES)
            self.assertEqual(self.window._control_layer_cache_bytes,
                             sum(image.sizeInBytes() for image in self.window._control_layer_cache.values()))

        visible_rasters = []
        for index in range(120):
            instant = 100.0 + index / 300
            image = paint_at(instant)
            assert_budget()
            if index in (0, 30, 60, 119):
                with patch.object(self.window, "_paint_control_layer", side_effect=direct_visible_layer):
                    reference = paint_at(instant)
                self.assertEqual(image, reference)
                visible_rasters.append(bytes(image.constBits()))
        self.assertGreater(len(set(visible_rasters)), 1)
        # Fully visible controls paint directly; repeated settled paints
        # must not allocate new exit-blur layers.
        settled = paint_at(100.5)
        before_stable = {key: image.cacheKey() for key, image in self.window._control_layer_cache.items()}
        for _ in range(3):
            with patch.object(self.window, "_remember_control_layer", wraps=self.window._remember_control_layer) as remember:
                self.assertEqual(paint_at(100.5), settled)
                remember.assert_not_called()
        self.assertEqual(before_stable, {key: image.cacheKey() for key, image in self.window._control_layer_cache.items()})
        for instant in (102.02, 102.08, 102.15):
            paint_at(instant)
            assert_budget()
        before = (len(self.window._control_layer_cache), self.window._control_layer_cache_bytes)
        for instant in (103.0, 104.0, 105.0):
            image = paint_at(instant)
            self.assertFalse(any(image.constBits()))
        self.assertEqual(before, (len(self.window._control_layer_cache), self.window._control_layer_cache_bytes))

    def test_expanded_idle_pose_prewarm_matches_exact_idle_start_and_all_fade_frames(self):
        self.window._set_library_expanded(True, 100.0)
        rect = QRectF(self.window.rect())

        def paint_at(instant):
            with patch("local.music_surface_window.time.monotonic", return_value=instant):
                image = self._image()
                with QPainter(image) as painter:
                    self.window._paint_library_back(painter, rect)
            return image

        for index in range(25):
            paint_at(100.5 + index / 25)
        warmed_blurs = {key: image.cacheKey() for key, image in self.window._control_layer_cache.items()
                        if key[1] > 0.}
        self.assertGreater(len(warmed_blurs), 1)
        def direct_visible_layer(painter, bounds, reveal, shift, key, draw, **kwargs):
            self.assertGreaterEqual(reveal, .999)
            painter.save()
            painter.translate(shift)
            draw(painter)
            painter.restore()

        with patch.object(self.window, "_build_control_blur", wraps=self.window._build_control_blur) as build_blur:
            for instant in (102.0, 102.001, 102.02, 102.1, 102.3):
                image = paint_at(instant)
                # Repainting the same fade pose must produce the same image
                # without a lazily built/replaced Gaussian level.
                self.assertEqual(paint_at(instant), image)
                self.assertEqual(self.window._library_expansion_at(instant), 1.0)
                self.assertEqual(warmed_blurs, {key: item.cacheKey()
                    for key, item in self.window._control_layer_cache.items() if key[1] > 0.})
                self.assertLessEqual(len(self.window._control_layer_cache), 48)
                self.assertLessEqual(self.window._control_layer_cache_bytes, self.window.CONTROL_LAYER_CACHE_MAX_BYTES)
                self.assertEqual(self.window._control_layer_cache_bytes,
                                 sum(item.sizeInBytes() for item in self.window._control_layer_cache.values()))
                if instant == 102.0:
                    with patch.object(self.window, "_paint_control_layer", side_effect=direct_visible_layer):
                        self.assertEqual(paint_at(instant), image)
            build_blur.assert_not_called()

    def test_output_chooser_holds_library_and_inactive_pointer_cannot_reveal_it(self):
        with patch("local.music_surface_window.time.monotonic", return_value=100.0):
            self.window._toggle_audio_output_panel()
        panel = self.window._audio_output_panel
        panel._animation_timer.stop()
        panel._poll_timer.stop()
        with patch("local.music_surface_window.time.monotonic", return_value=110.0):
            self.window._sync_window_control_rects()
            self.assertIn("library", self.window._button_rects)
            self.assertEqual(self.window._control_reveal_at(110.0), 1.0)
            panel.dismiss(immediate=True)
        self.window._fullscreen = False
        self.window._reset_control_idle(now=100.0)
        with patch("local.music_surface_window.time.monotonic", return_value=110.0), \
                patch.object(self.window, "isActiveWindow", return_value=False):
            self.window._sync_window_control_rects()
            self.window._note_pointer_moved(110.0)
            self.assertEqual(self.window._control_reveal_at(110.1), 0.0)
            self.assertNotIn("library", self.window._button_rects)


if __name__ == "__main__":
    unittest.main()
