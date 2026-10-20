"""Finder misses must release a visible Free island, including direction flips."""
from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPainter, QRegion
from PySide6.QtWidgets import QApplication

from local.island_stack_visual import SpringRect
from tests import test_finder_bar_surface as finder_fixture


class FinderDragVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    setUp = finder_fixture.FinderBarSurfaceTests.setUp
    tearDown = finder_fixture.FinderBarSurfaceTests.tearDown

    def _environment(self, origin=QPoint(0, 0), dpr=1., finder=True, dock=True, start_finder=False):
        visual = self.visual
        geometry = QRect(origin.x(), origin.y(), 1280, 900)
        screen = SimpleNamespace(name=lambda: "drag-monitor", geometry=lambda: geometry,
                                 devicePixelRatio=lambda: dpr)
        bar = dict(self.bar, monitor_name="drag-monitor",
                   physical_monitor_rect=[origin.x(), origin.y(), origin.x()+1280*dpr, origin.y()+900*dpr],
                   physical_rect=[origin.x(), origin.y(), origin.x()+1280*dpr, origin.y()+24*dpr],
                   physical_resting_rect=[origin.x(), origin.y(), origin.x()+1280*dpr, origin.y()+24*dpr])
        context = ExitStack()
        self.addCleanup(context.close)
        context.enter_context(patch("local.island_stack_visual.QGuiApplication.screens", return_value=[screen]))
        context.enter_context(patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=screen))
        context.enter_context(patch("local.island_stack_visual.QGuiApplication.primaryScreen", return_value=screen))
        context.enter_context(patch("local.island_stack_visual.refresh_live_finder_bar", side_effect=lambda snapshot: dict(snapshot)))
        context.enter_context(patch("local.island_stack_visual.read_finder_material", return_value=visual.finder_material))
        visual.mydockfinder_running = dock
        visual.set_finder_bar_presence([bar] if finder else [])
        visual._bottom_taskbar_rect_for_screen = Mock(return_value=QRectF(geometry.left(), geometry.bottom()-39, 1280, 40))
        visual._taskbar_rect_for_screen = visual._bottom_taskbar_rect_for_screen
        visual._taskbar_auto_hide_enabled_for_screen = Mock(return_value=False)
        visual._taskbar_autohide_hidden_for_screen = Mock(return_value=False)
        visual._standalone_bottom_boundary_extension_for_screen = Mock(return_value=0.)
        visual._save_placement_state = Mock()
        if not start_finder:
            visual.set_location_mode("free", notify=False)
        visual.expand_direction, visual.auto_expand_bounds = "auto", True
        visual.expanded = False
        visual.scene_suppressed = False
        visual._tray_hidden = False
        if not start_finder:
            visual.move(geometry.left()+120, geometry.top()+100)
        return geometry, screen

    def _drag_to(self, point, direction=1):
        visual = self.visual
        visual.stack_direction = direction
        main = next(item for item in visual._build_visuals() if item.z_index == 10)
        visual.motions = {main.key: SpringRect(main.rect)}
        visual.motions[main.key].opacity = 1.
        visual.drag_candidate, visual.dragging = True, True
        visual.drag_auto_stack_direction = direction
        visual.drag_press_key = "music"
        visual.drag_press_main_offset = QPointF(main.rect.width()/2., main.rect.height()/2.)
        if visual.finder_mode:
            visual.setMask(QRegion(main.rect.toAlignedRect()))
            visual.interaction_mask_active = True
            visual.last_mask_rect = QRectF(main.rect)
            visual._apply_finder_drag_action(point, QPoint(0, 200))
        else:
            visual._move_standalone_drag(point)
        main = next(item for item in visual._build_visuals() if item.z_index == 10)
        # During the drag the production painter snaps the visible main motion.
        visual.motions[main.key] = SpringRect(main.rect)
        visual.motions[main.key].opacity = 1.
        return QRectF(visual._current_main_target_global_rect()), main.key

    def _release(self, point):
        local = self.visual.mapFromGlobal(point)
        event = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(local), QPointF(point),
                            Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        self.visual.mouseReleaseEvent(event)

    def _assert_visible_target(self, geometry, anchor, key):
        visual = self.visual
        main = next(item for item in visual._build_visuals() if item.z_index == 10)
        origin = visual.mapToGlobal(QPoint())
        target = QRectF(main.rect).translated(QPointF(origin))
        self.assertGreater(target.intersected(QRectF(geometry)).height(), target.height()*.95)
        self.assertAlmostEqual(target.center().x(), anchor.center().x(), delta=1.)
        self.assertAlmostEqual(target.center().y(), anchor.center().y(), delta=1.)
        motion = QRectF(visual.motions[key].rect).translated(QPointF(origin))
        self.assertAlmostEqual(motion.center().x(), anchor.center().x(), delta=1.)
        self.assertAlmostEqual(motion.center().y(), anchor.center().y(), delta=1.)
        self.assertFalse(visual.scene_suppressed)
        self.assertFalse(visual.input_passthrough_enabled)
        self.assertFalse(visual.dragging)
        self.assertFalse(visual.drag_candidate)
        self.assertFalse(visual.finder_mode)
        self.assertFalse(visual.taskbar_preview_visible)
        return main

    def _assert_render_and_input(self, geometry, anchor, key, dpr=1.):
        visual = self.visual
        initial_clock = visual.media_position_seconds
        visual.last_frame_at = 500.
        for frame in range(12):
            now = 500.+frame/120.
            image = QImage(round(visual.width()*dpr), round(visual.height()*dpr), QImage.Format.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(dpr)
            image.fill(Qt.GlobalColor.transparent)
            with patch("local.island_stack_visual.time.monotonic", return_value=now):
                painter = QPainter(image)
                try:
                    visual.render(painter, QPoint())
                finally:
                    painter.end()
            self._assert_visible_target(geometry, anchor, key)
            motion = visual.motions[key]
            center = motion.rect.center()
            self.assertGreater(image.pixelColor(round(center.x()*dpr), round(center.y()*dpr)).alpha(), 200)
            # Free islands use alpha hit testing. A Finder-only rectangular
            # mask left at the previous local origin must be cleared.
            self.assertTrue(visual.mask().isEmpty())
            self.assertFalse(visual.interaction_mask_active)
            self.assertTrue(visual._hit_test(center))
        self.assertEqual(visual.media_position_seconds, initial_clock)
        self.assertTrue(visual.media_is_playing)

    def test_bottom_release_with_live_finder_preserves_free_target_and_first_motion(self):
        geometry, _screen = self._environment()
        point = QPoint(geometry.center().x(), geometry.bottom()-45)
        anchor, key = self._drag_to(point, 1)
        self._release(point)
        self.assertEqual(self.visual.location_mode, "free")
        self._assert_visible_target(geometry, anchor, key)
        self.assertEqual(self.visual.stack_direction, -1)

    def test_release_direction_flip_matrix_preserves_native_paint_and_input(self):
        for origin, dpr in ((QPoint(0, 0), 1.), (QPoint(-1280, -900), 1.5), (QPoint(160, -900), 2.)):
            for attached in (False, True):
                for direction, y in ((1, 854), (-1, 90)):
                    with self.subTest(origin=origin, dpr=dpr, attached=attached, old_direction=direction):
                        geometry, _screen = self._environment(origin, dpr, start_finder=attached)
                        point = QPoint(geometry.center().x(), geometry.top()+y)
                        anchor, key = self._drag_to(point, direction)
                        self._release(point)
                        self.assertEqual(self.visual.location_mode, "free")
                        self._assert_render_and_input(geometry, anchor, key, dpr)
                        self.assertEqual(self.visual.stack_direction, -direction)

    def test_dock_without_finder_keeps_bottom_release_visible_and_free(self):
        geometry, _screen = self._environment(finder=False)
        point = QPoint(geometry.center().x(), geometry.bottom()-45)
        anchor, key = self._drag_to(point, 1)
        self._release(point)
        self.assertEqual(self.visual.location_mode, "free")
        self._assert_render_and_input(geometry, anchor, key)

    def test_pending_presence_reconciles_then_settles_missed_bottom_drop(self):
        geometry, _screen = self._environment(QPoint(-1280, -900), 1.5)
        point = QPoint(geometry.center().x(), geometry.bottom()-45)
        anchor, key = self._drag_to(point, 1)
        # The existing poll intentionally defers geometry changes while the
        # pointer is held; release must reconcile data and still settle Free.
        self.visual._finder_presence_reconcile_pending = True
        self._release(point)
        self.assertFalse(self.visual._finder_presence_reconcile_pending)
        self.assertEqual(self.visual._finder_bar_hwnd, 1)
        self.assertEqual(self.visual.location_mode, "free")
        self._assert_render_and_input(geometry, anchor, key, 1.5)

    def test_real_finder_hit_attaches_instead_of_running_free_settle(self):
        geometry, _screen = self._environment()
        point = QPoint(geometry.center().x(), geometry.top()+12)
        self._drag_to(point, 1)
        self.visual._settle_standalone_drag_release = Mock(side_effect=AssertionError("Finder owns this drop"))
        self._release(point)
        self.assertEqual(self.visual.location_mode, "finder")
        self.assertTrue(self.visual.finder_mode)
        self.assertEqual(self.visual.pos(), geometry.topLeft())
        self.assertFalse(self.visual.dragging)

    def test_without_dock_bottom_release_still_selects_tasker(self):
        geometry, _screen = self._environment(finder=False, dock=False)
        visual = self.visual
        visual._update_taskbar_preview_for_drag = Mock()
        visual._accept_taskbar_preview_drop = Mock(return_value=False)
        visual._refresh_taskbar_follow_position = Mock(return_value=True)
        visual._dock_to_taskbar_task_area = Mock(return_value=True)
        point = QPoint(geometry.center().x(), geometry.bottom()-2)
        self._drag_to(point, 1)
        self._release(point)
        self.assertEqual(visual.location_mode, "tasker")
        self.assertFalse(visual.finder_mode)
        visual._dock_to_taskbar_task_area.assert_called_once()


if __name__ == "__main__":
    unittest.main()
