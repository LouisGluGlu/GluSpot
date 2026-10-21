"""Automatic placement keeps physical routes, bar motion and audio independent."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, QRect, QRectF
from PySide6.QtGui import QRegion
from PySide6.QtWidgets import QApplication
from tests import test_finder_bar_surface as finder_fixture
from local.taskbar_positioning import TaskbarFollowTarget


class AutomaticIslandPlacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    setUp = finder_fixture.FinderBarSurfaceTests.setUp
    tearDown = finder_fixture.FinderBarSurfaceTests.tearDown

    def environment(self, dock, finder):
        visual = self.visual
        visual.mydockfinder_running = dock
        visual.finder_bar_enabled = finder
        visual.finder_bar_rect = QRectF(-1920, 0, 1920, 24) if finder else QRectF()
        visual.finder_bar_visible = finder
        visual._finder_bar_render_visible = finder
        visual.location_mode, visual.finder_mode = "free", False
        visual._refresh_taskbar_follow_position = Mock(return_value=True)
        visual._refresh_finder_bar_material = Mock()
        visual._center_on_screen = Mock()
        visual._owner_bar_hidden = Mock(return_value=False)
        return visual

    def test_fullscreen_matrix_keeps_route_and_visibility_separate(self):
        for dock, finder in ((False, False), (True, False), (True, True)):
            for follow in (False, True):
                for hidden in (False, True):
                    with self.subTest(dock=dock, finder=finder, follow=follow, hidden=hidden):
                        visual = self.environment(dock, finder)
                        visual._fullscreen_placement_active = False
                        visual.bar_hide_follow_enabled = follow
                        visual._owner_bar_hidden.return_value = hidden
                        suppressed, reason = visual._apply_automatic_placement_policy(100., True, True, "fullscreen")
                        expected = "finder" if finder else "top" if dock else "tasker"
                        if hidden and not follow:
                            expected = "top"
                        self.assertEqual(visual.location_mode, expected)
                        self.assertEqual(suppressed, follow and (hidden or (dock and not finder)))
                        self.assertEqual(bool(reason), suppressed)
                        self.assertTrue(visual.media_is_playing)
                        self.assertEqual(visual.media_position_seconds, 2.)
                        self.assertNotEqual(visual.location_mode, "immersive")

    def test_fullscreen_exit_keeps_auto_mode_across_owner_changes_instead_of_restoring_free(self):
        visual = self.environment(False, False)
        visual.move(QPoint(125, 175))
        visual.saved_window_pos, visual.last_window_position = (125, 175), (125, 175)
        visual.stack_center_ratio = .34
        visual.bar_hide_follow_enabled = False
        visual._apply_automatic_placement_policy(100., True, True, "fullscreen")
        self.assertEqual(visual.location_mode, "tasker")
        self.assertTrue(visual._fullscreen_placement_active)
        visual._owner_bar_hidden.return_value = True
        visual._apply_automatic_placement_policy(100.1, True, True, "fullscreen")
        self.assertEqual(visual.location_mode, "top")
        self.assertTrue(visual._fullscreen_placement_active)
        visual._owner_bar_hidden.return_value = False
        visual._apply_automatic_placement_policy(100.2, True, True, "fullscreen")
        self.assertEqual(visual.location_mode, "tasker")
        visual._apply_automatic_placement_policy(101., False, False, "")
        self.assertEqual(visual.location_mode, "tasker")
        self.assertFalse(visual._fullscreen_placement_active)

    def test_disabled_follow_temporarily_uses_top_and_returns_when_bar_reappears(self):
        for dock, finder, owner in ((False, False, "tasker"), (True, True, "finder")):
            with self.subTest(owner=owner):
                visual = self.environment(dock, finder)
                visual.location_mode, visual.finder_mode = owner, finder
                visual.bar_hide_follow_enabled = False
                visual._owner_bar_hidden.return_value = True
                self.assertEqual(visual._apply_automatic_placement_policy(100., False, True, owner+"-hidden"), (False, ""))
                self.assertEqual(visual.location_mode, "top")
                if finder:
                    self.assertNotIn("top", visual.available_location_modes())
                visual._owner_bar_hidden.return_value = False
                self.assertEqual(visual._apply_automatic_placement_policy(101., False, False, ""), (False, ""))
                self.assertEqual(visual.location_mode, owner)
                self.assertIsNone(visual._bar_fallback_restore)

    def test_dock_disappearance_during_fullscreen_reselects_tasker_and_keeps_it_on_exit(self):
        visual = self.environment(True, True)
        visual.location_mode, visual.finder_mode = "finder", True
        visual._apply_automatic_placement_policy(100., True, True, "fullscreen")
        visual.mydockfinder_running = False
        visual.finder_bar_enabled = False
        visual._apply_automatic_placement_policy(100.5, True, True, "fullscreen")
        visual._apply_automatic_placement_policy(101., False, False, "")
        self.assertEqual(visual.location_mode, "tasker")
        self.assertFalse(visual.finder_mode)

    def test_manual_legacy_finder_flag_is_not_live_dock_evidence(self):
        visual = self.environment(False, True)
        visual.manual_finder_available = True
        self.assertFalse(visual._mydockfinder_available())
        self.assertFalse(visual._finder_bar_available())
        self.assertEqual(visual.available_location_modes(), ["top", "free", "tasker"])

    def test_dock_without_finder_fullscreen_exit_reveals_and_keeps_top(self):
        visual = self.environment(True, False)
        visual.bar_hide_follow_enabled = True
        self.assertEqual(visual._apply_automatic_placement_policy(100., True, True, "fullscreen"),
                         (True, "dock-top-hidden"))
        self.assertEqual(visual.location_mode, "top")
        self.assertEqual(visual._apply_automatic_placement_policy(101., False, False, ""), (False, ""))
        self.assertEqual(visual.location_mode, "top")
        self.assertFalse(visual._fullscreen_placement_active)

    def test_fullscreen_exit_keeps_temporary_top_until_its_owner_reappears(self):
        visual = self.environment(True, True)
        visual.bar_hide_follow_enabled = False
        visual._owner_bar_hidden.return_value = True
        visual._apply_automatic_placement_policy(100., True, True, "fullscreen")
        visual._apply_automatic_placement_policy(101., False, False, "")
        self.assertEqual(visual.location_mode, "top")
        self.assertEqual(visual._bar_fallback_restore["mode"], "finder")
        visual._owner_bar_hidden.return_value = False
        visual._apply_automatic_placement_policy(102., False, False, "")
        self.assertEqual(visual.location_mode, "finder")

    def test_navigation_ownership_blocks_automatic_placement(self):
        visual = self.environment(False, False)
        visual._navigation_surface_blocks_island = Mock(return_value=True)
        visual._apply_automatic_placement_policy = Mock()
        with patch.object(visual, "_detect_scene_suppression", return_value=(True, "fullscreen")):
            visual._update_scene_state(100.)
        visual._apply_automatic_placement_policy.assert_not_called()
        self.assertEqual(visual.location_mode, "free")

    def test_top_fallback_probes_the_visible_islands_monitor(self):
        visual = self.environment(False, False)
        del visual._owner_bar_hidden
        visual.location_mode = "top"
        visual.move(-1920, -300)
        visual._current_main_target_global_rect = Mock(return_value=QRectF(-1250, 0, 400, 60))
        visual._taskbar_rect_for_screen = Mock(return_value=QRectF(-1920, 1040, 1920, 40))
        visual._taskbar_follow_rect_for_current_visibility = Mock(return_value=(QRectF(-1920,1080,1920,40), True))
        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=self.screen) as at:
            self.assertTrue(visual._owner_bar_hidden("tasker"))
        at.assert_called_once_with(QPoint(-1050, 30))
        visual._taskbar_rect_for_screen.assert_called_once_with(self.screen)
        visual._taskbar_follow_rect_for_current_visibility.assert_called_once_with(self.screen, QRectF(-1920,1040,1920,40))

    def test_finder_partial_window_hide_and_mapping_failure_do_not_reuse_visible_geometry(self):
        visual = self.visual
        finder_fixture.FinderBarSurfaceTests.presence(self)
        partial = dict(self.bar, visible=False, window_visible=True, physical_rect=[-1920,-10,0,14])
        with patch("local.island_stack_visual.QGuiApplication.screens", return_value=[self.screen]), \
                patch("local.island_stack_visual.refresh_live_finder_bar", return_value=partial):
            visual._refresh_finder_bar_follow_position(now=100.)
        self.assertFalse(visual._owner_bar_hidden("finder"))
        hidden = dict(partial, window_visible=False)
        with patch("local.island_stack_visual.QGuiApplication.screens", return_value=[self.screen]), \
                patch("local.island_stack_visual.refresh_live_finder_bar", return_value=hidden):
            visual._refresh_finder_bar_follow_position(now=101.)
        self.assertTrue(visual._owner_bar_hidden("finder"))
        for fresh in (None, dict(partial, physical_monitor_rect=[8000,0,9920,1080], monitor_name="gone")):
            with self.subTest(fresh=fresh), \
                    patch("local.island_stack_visual.QGuiApplication.screens", return_value=[self.screen]), \
                    patch("local.island_stack_visual.refresh_live_finder_bar", return_value=fresh):
                visual._finder_bar_snapshot = partial
                visual._refresh_finder_bar_follow_position(now=102.)
                visual._refresh_finder_bar_follow_position(now=102.001, probe=False)
                self.assertTrue(visual._owner_bar_hidden("finder"))
                visual._finder_bar_follow_checked_at = 0.

    def test_real_taskbar_refresh_reentry_settles_when_top_returns_to_a_still_hidden_owner(self):
        visual = self.environment(False, False)
        del visual._owner_bar_hidden
        del visual._refresh_taskbar_follow_position
        visual.bar_hide_follow_enabled = False
        visual.location_mode = "tasker"
        visual.taskbar_follow_last_hidden = True
        visual._taskbar_rect_for_screen = Mock(return_value=QRectF(-1920,1040,1920,40))
        visual._taskbar_autohide_hidden_for_screen = Mock(return_value=True)
        visual._taskbar_follow_rect_for_current_visibility = Mock(return_value=(QRectF(-1920,1080,1920,40), True))
        visual._taskbar_follow_force_visible_active = Mock(return_value=False)
        visual._settle_standalone_top_notch = Mock()
        visual._update_taskbar_material_for_screen = Mock()
        target = TaskbarFollowTarget(
            screen=self.screen, visible_taskbar=QRectF(-1920,1040,1920,40),
            follow_taskbar=QRectF(-1920,1080,1920,40), track_rect=QRectF(-1920,1040,1920,40),
            zone_rect=QRectF(-1200,1080,300,40), window_pos=QPoint(-1250,600),
            main_kind="music", stack_width=420., main_width=300., main_height=40., main_top=480.,
            direction=-1, position_ratio=.5, hidden_autohide=True,
            auto_hide_follow=True, partial_shell_follow=True)
        visual._taskbar_follow_target_for_screen = Mock(return_value=target)
        with patch("local.island_stack_visual.QGuiApplication.screenAt", return_value=self.screen), \
                patch("local.island_stack_visual.QGuiApplication.primaryScreen", return_value=self.screen), \
                patch.object(visual, "_detect_scene_suppression", return_value=(False, "")):
            visual._apply_automatic_placement_policy(100., False, True, "taskbar-hidden")
            self.assertEqual(visual.location_mode, "top")
            # The probe sees reveal, but the following native refresh sees a
            # still-hidden shell. The synchronous callback must settle once.
            visual._taskbar_follow_rect_for_current_visibility.return_value = (QRectF(-1920,1040,1920,40), False)
            visual._apply_automatic_placement_policy(101., False, False, "")
            self.assertEqual(visual.location_mode, "top")
            self.assertEqual(visual._bar_fallback_restore["mode"], "tasker")
            self.assertEqual(visual._taskbar_follow_target_for_screen.call_count, 1)
            visual._taskbar_autohide_hidden_for_screen.return_value = False
            visual._taskbar_follow_target_for_screen.return_value = replace(target, hidden_autohide=False)
            visual._apply_automatic_placement_policy(102., False, False, "")
        self.assertEqual(visual.location_mode, "tasker")
        self.assertEqual(visual._taskbar_follow_target_for_screen.call_count, 2)
        self.assertIsNone(visual._bar_fallback_restore)
        self.assertFalse(visual.input_passthrough_enabled)
        self.assertTrue(visual.media_is_playing)

    def test_taskbar_follows_live_shell_despite_hover_hold_and_stale_visible_cache(self):
        visual = self.environment(False, False)
        visual.location_mode = "tasker"
        visual.taskbar_auto_hide_mode = "auto"
        visual._taskbar_follow_force_visible_active = Mock(return_value=False)
        visual._taskbar_auto_hide_enabled_for_screen = Mock(return_value=True)
        base = QRectF(-1920,1040,1920,40)
        visual._windows_taskbar_appbar_state_for_screen = Mock(return_value={"auto_hide": True, "rect": base})
        visual._taskbar_autohide_interaction_keeps_visible = Mock(return_value=True)
        visual._taskbar_autohide_hidden_for_screen = Mock(return_value=False)
        visual._windows_taskbar_rect_for_screen = Mock(return_value=QRectF(-1920,1060,1920,40))
        follow, hidden = visual._taskbar_follow_rect_for_current_visibility(self.screen, base)
        self.assertEqual(follow, QRectF(-1920,1060,1920,40))
        self.assertFalse(hidden)
        visual._windows_taskbar_rect_for_screen.return_value = None
        follow, hidden = visual._taskbar_follow_rect_for_current_visibility(self.screen, base)
        self.assertEqual(follow, QRectF(-1920,1080,1920,40))
        self.assertTrue(hidden)
        visual._taskbar_autohide_hidden_for_screen.assert_not_called()
        visual._taskbar_autohide_interaction_keeps_visible.assert_not_called()

    def test_native_taskbar_adapter_retains_full_moving_hwnd_height(self):
        visual = self.visual
        partial = QRectF(-1920,1060,1920,40)
        visual._windows_taskbar_hwnd_for_screen = Mock(return_value=12)
        visual._window_rect_for_hwnd = Mock(return_value=partial)
        self.assertEqual(visual._windows_taskbar_rect_for_screen(self.screen), partial)

    def test_finder_hide_clears_mask_bookkeeping_and_reveal_reapplies_real_input_bounds(self):
        visual = self.environment(True, True)
        visual.location_mode, visual.finder_mode = "finder", True
        bounds = QRectF(240,0,200,24)
        visual.setMask(QRegion(bounds.toAlignedRect()))
        visual.interaction_mask_active = True
        visual.last_mask_rect = QRectF(bounds)
        visual._owner_bar_hidden.return_value = True
        with patch.object(visual, "_detect_scene_suppression", return_value=(True, "finder-hidden")):
            visual._update_scene_state(100.)
        self.assertTrue(visual.mask().isEmpty())
        self.assertFalse(visual.interaction_mask_active)
        self.assertTrue(visual.last_mask_rect.isEmpty())
        visual._owner_bar_hidden.return_value = False
        with patch.object(visual, "_detect_scene_suppression", return_value=(False, "")):
            visual._update_scene_state(100.8)
        with patch("local.island_stack_visual.time.monotonic", return_value=101.):
            visual._update_interaction_mask([bounds])
        self.assertFalse(visual.mask().isEmpty())
        self.assertTrue(visual.interaction_mask_active)
        self.assertTrue(visual.mask().contains(bounds.center().toPoint()))

    def test_hide_reversal_preserves_opacity_and_velocity_without_shrinking_or_second_slide(self):
        visual = self.visual
        visual.scene_suppressed = True
        visual._retarget_bar_visibility(False, 100.)
        frames = [visual._scene_transition_params(100.+i/120.) for i in range(7)]
        self.assertTrue(all(a[1] >= b[1] for a,b in zip(frames, frames[1:])))
        before = visual._bar_visibility_value, visual._bar_visibility_velocity
        visual.scene_suppressed = False
        visual._retarget_bar_visibility(True, 100.05)
        self.assertEqual((visual._bar_visibility_value, visual._bar_visibility_velocity), before)
        self.assertAlmostEqual(visual._scene_transition_params(100.05)[1], before[0])
        frames += [visual._scene_transition_params(100.05+i/120.) for i in range(1, 70)]
        self.assertTrue(all(scale == 1. and edge == 0. and 0. <= opacity <= 1. for scale,opacity,_,edge in frames))
        self.assertEqual(visual._bar_visibility_value, 1.)
        self.assertFalse(visual._bar_scene_transition)

    def test_follow_hidden_state_removes_input_and_reveal_restores_without_clock_changes(self):
        visual = self.environment(True, True)
        visual.location_mode, visual.finder_mode = "finder", True
        visual._owner_bar_hidden.return_value = True
        with patch.object(visual, "_detect_scene_suppression", return_value=(True, "finder-hidden")):
            visual.scene_checked_at = 0.
            visual._update_scene_state(100.)
        self.assertTrue(visual.scene_suppressed)
        self.assertTrue(visual.input_passthrough_enabled)
        visual._scene_transition_params(100.6)
        visual._owner_bar_hidden.return_value = False
        with patch.object(visual, "_detect_scene_suppression", return_value=(False, "")):
            visual._update_scene_state(100.7)
        self.assertFalse(visual.scene_suppressed)
        self.assertFalse(visual.input_passthrough_enabled)
        self.assertTrue(visual.media_is_playing)
        self.assertEqual(visual.media_position_seconds, 2.)

    def test_live_finder_slide_follows_each_120hz_sample_at_negative_origin_and_mixed_dpi(self):
        visual = self.visual
        for dpr in (1., 2.):
            with self.subTest(dpr=dpr):
                screen = SimpleNamespace(name=lambda: "fixture", geometry=lambda: QRect(-1920, 0, 1920, 1080), devicePixelRatio=lambda: dpr)
                resting = [-1920, 0, -1920+1920*dpr, 24*dpr]
                snapshot = dict(self.bar, monitor_name="fixture", physical_monitor_rect=[-1920, 0, -1920+1920*dpr,1080*dpr],
                                physical_resting_rect=resting, physical_rect=resting, visible=True, window_visible=True)
                with patch("local.island_stack_visual.QGuiApplication.screens", return_value=[screen]):
                    visual.set_finder_bar_presence([snapshot])
                    for index, offset in enumerate((0, -3, -6, -9, -12, -15, -18, -21, -24, -21, -18, -12, -6, 0)):
                        live = [resting[0], offset*dpr, resting[2], (offset+24)*dpr]
                        fresh = dict(snapshot, physical_rect=live, visible=offset == 0)
                        with patch("local.island_stack_visual.refresh_live_finder_bar", return_value=fresh) as probe:
                            visual._finder_bar_follow_checked_at = 0. if index == 0 else visual._finder_bar_follow_checked_at
                            visual._refresh_finder_bar_follow_position(now=100.+index/120.)
                        self.assertEqual(probe.call_count, 1)
                        self.assertEqual(visual.pos(), QPoint(-1920, offset))
                        self.assertEqual(visual.finder_bar_rect.height(), 24.)
                        self.assertEqual(visual._owner_bar_hidden("finder"), offset == -24)
