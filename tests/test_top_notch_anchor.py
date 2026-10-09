"""Top placement keeps its attached edge through native and content changes."""
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, QRect, QRectF
from PySide6.QtWidgets import QApplication

from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot
from api.providers import EmptyIslandDataProvider, empty_visual_data
from local.island_stack_visual import IslandKind, IslandStackVisual, SpringRect, StackVisual


class TopNotchAnchorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.visual = IslandStackVisual(EmptyIslandDataProvider(empty_visual_data()))
        self.visual.timer.stop()
        self.visual._force_super_topmost = lambda: None
        self.visual._notify_settings_changed = lambda: None
        self.visual.finder_mode = False
        self.visual.attach_to_real_finder = False
        self.visual.location_mode = "top"
        self.visual.expanded = False
        self.visual.saved_window_pos = (505, 25)
        self.visual.last_window_position = (505, 25)
        self.visual.music_ambience_style = "pure_black"
        self.visual.scene_transition_mode = ""
        self.visual.scene_suppressed = False

    def tearDown(self):
        self.visual.music_fullscreen_overlay = None
        self.visual.close()
        self.visual.deleteLater()
        self.app.processEvents()

    def _assert_attached(self):
        visual = self.visual
        main = next(item for item in visual._build_visuals() if item.z_index == 10)
        screen = visual.screen().geometry()
        self.assertAlmostEqual(visual.y() + main.rect.top(), screen.top(), delta=0.5)
        self.assertEqual(visual.stack_direction, 1)
        self.assertEqual(visual.saved_window_pos, (visual.x(), visual.y()))
        return main

    def test_saved_platform_move_is_not_restored_as_top_gap(self):
        self.visual.show()
        self.app.processEvents()
        self._assert_attached()
        self.visual.hide()
        self.visual.saved_window_pos = (505, 25)
        self.visual.last_window_position = (505, 25)
        self.visual.show()
        self.app.processEvents()
        self._assert_attached()

    def test_native_move_is_corrected_without_persisting_vertical_drift(self):
        self.visual.show()
        self.app.processEvents()
        original = (self.visual.x(), self.visual.y())
        self.visual.move(self.visual.x(), 25)
        self.app.processEvents()
        self._assert_attached()
        self.assertEqual(self.visual.last_window_position, original)

    def test_surface_return_reapplies_top_anchor_before_raising_island(self):
        self.visual._music_surface_active = True
        self.visual._island_visible_before_music_surface = True
        self.visual.music_fullscreen_overlay = SimpleNamespace(hide=Mock())
        self.visual._restore_island_from_music_surface()
        self.app.processEvents()
        self._assert_attached()
        self.assertFalse(self.visual._music_surface_active)

    def test_monitor_origin_change_uses_physical_top_instead_of_work_area(self):
        class Screen:
            def geometry(self):
                return QRect(0, -1080, 1920, 1080)

        self.visual._restoring_position = True
        self.visual.move(505, 25)
        self.visual._restoring_position = False
        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=Screen()):
            self.assertTrue(self.visual._refresh_top_notch_position())
            main = next(item for item in self.visual._build_visuals() if item.z_index == 10)
            self.assertEqual(self.visual.y() + main.rect.top(), -1080)
            self.assertFalse(self.visual._refresh_top_notch_position())

    def test_free_finder_tasker_and_stick_placements_are_not_pinned(self):
        for mode in ("free", "finder", "tasker", "stick"):
            with self.subTest(mode=mode):
                self.visual.location_mode = mode
                self.visual.move(321, 79)
                before = self.visual.pos()
                self.assertFalse(self.visual._refresh_top_notch_position())
                self.assertEqual(self.visual.pos(), before)

    def test_drag_can_leave_edge_and_release_reestablishes_attachment(self):
        for flag in ("drag_candidate", "dragging", "_music_surface_active"):
            with self.subTest(flag=flag):
                setattr(self.visual, flag, True)
                self.visual.move(505, 79)
                self.assertFalse(self.visual._refresh_top_notch_position())
                self.assertEqual(self.visual.y(), 79)
                setattr(self.visual, flag, False)
                self.assertTrue(self.visual._refresh_top_notch_position())
                self._assert_attached()

    def test_top_expansion_cannot_flip_to_upward_canvas_anchor(self):
        self.visual.expanded = True
        self.visual.expand_direction = "up"
        self.visual.stack_direction = -1
        self.assertEqual(self.visual._vertical_stack_direction(IslandKind.MUSIC, 600, QRect(0, 0, 800, 300)), 1)

    def test_main_kind_change_and_device_notice_keep_same_attached_edge(self):
        visual = self.visual
        visual.show()
        self.app.processEvents()
        device = AudioRouteDevice("buds", "AirPods Pro", "airpods-pro", "windows", True)
        started = time.monotonic()
        with patch("local.island_stack_visual.time.monotonic", return_value=started):
            visual.on_audio_device_event(AudioRouteSnapshot(event_serial=1, event_name=device.name), device)
        for kind, offset in ((IslandKind.MUSIC, 0), (IslandKind.THIN_BAR, 0.2),
                             (IslandKind.EMPTY_TEXT, 0.6), (IslandKind.MUSIC, 6)):
            with self.subTest(kind=kind, offset=offset), patch.object(visual, "_main_kind", return_value=kind):
                visual._audio_device_motion.advance(started + offset)
                visual._refresh_top_notch_position()
                self._assert_attached()

    def test_attached_main_keeps_size_springs_and_auxiliary_motion(self):
        visual = self.visual
        main = StackVisual("music", IslandKind.MUSIC, QRectF(100, 54, 420, 60), False, None, 10)
        popup = StackVisual("message:fixture", IslandKind.MESSAGE, QRectF(100, 120, 420, 48), False, None, 5)
        moving = SpringRect(QRectF(140, 133, 340, 40))
        moving.vx, moving.vy, moving.vw, moving.vh = 25, 80, 40, 20
        visual.motions = {main.key: moving, popup.key: SpringRect(popup.rect)}
        before = (moving.x, moving.w, moving.h, moving.vx, moving.vw, moving.vh, moving.opacity)
        visual._pin_top_notch_main_motion(main)
        visual._pin_top_notch_main_motion(popup)
        self.assertEqual((moving.y, moving.vy), (54, 0))
        self.assertEqual((moving.x, moving.w, moving.h, moving.vx, moving.vw, moving.vh, moving.opacity), before)
        self.assertEqual(visual.motions[popup.key].y, 120)

    def test_native_paint_retains_top_edge_during_hover_and_spring_reversal(self):
        visual = self.visual
        visual.show()
        self.app.processEvents()
        main = self._assert_attached()
        moving = SpringRect(main.rect.translated(0, 79))
        moving.opacity = 1
        moving.vy = -90
        visual.motions[main.key] = moving
        visual.hovered_key = main.key
        visual.hover_started_at = 1
        visual.hover_bounce_until = float("inf")
        rects = []
        with patch.object(visual, "_paint_music", side_effect=lambda _p, rect, _e: rects.append(QRectF(rect))):
            visual.grab()
        self.assertTrue(rects)
        self.assertTrue(all(rect.top() + visual.y() == visual.screen().geometry().top() for rect in rects))
        self.assertEqual(moving.vy, 0)

    def test_stable_refresh_does_not_measure_content_or_repeat_native_moves(self):
        visual = self.visual
        visual._center_on_screen()
        visual._refresh_top_notch_position()
        with patch.object(visual, "move", wraps=visual.move) as move, patch.object(visual, "_main_size_for_kind", side_effect=AssertionError("unexpected measurement")):
            for _ in range(5):
                self.assertFalse(visual._refresh_top_notch_position())
        move.assert_not_called()

    def test_loading_saved_free_placement_before_location_does_not_erase_it(self):
        visual = self.visual
        # LocalIslandApp loads placement while the constructor's default mode
        # is still top; the persisted mode is applied later in the same pass.
        visual.set_saved_placement(window_pos=(321, 210), last_window_position=(321, 210), notify=False)
        visual.set_location_mode("free", notify=False)
        visual.show()
        self.app.processEvents()
        self.assertEqual(visual.pos(), QPoint(321, 210))
        self.assertEqual(visual.saved_window_pos, (321, 210))


if __name__ == "__main__":
    unittest.main()
