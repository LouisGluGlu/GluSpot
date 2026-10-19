import unittest

from PySide6.QtCore import QPoint, QPointF, QRectF

from local.taskbar_positioning import (
    available_taskbar_slot_rects,
    bottom_release_reaches_taskbar_threshold,
    build_standalone_position_preset_target,
    build_taskbar_drop_zones,
    build_taskbar_drag_target,
    build_taskbar_left_resize_target,
    build_standalone_free_release_target,
    build_standalone_above_taskbar_target,
    build_standalone_release_execution_plan,
    build_standalone_top_notch_target,
    build_location_mode_transition_plan,
    build_mouse_release_action_plan,
    build_mouse_release_cleanup_plan,
    build_mouse_release_standalone_settle_plan,
    build_tasker_release_detach_plan,
    build_taskbar_follow_target,
    build_taskbar_auto_hide_mode_plan,
    build_taskbar_fusion_mode_plan,
    build_taskbar_autohide_hidden_plan,
    build_taskbar_current_visibility_follow_plan,
    build_taskbar_visibility_follow_plan,
    build_taskbar_presence_plan,
    build_taskbar_above_follow_refresh_plan,
    build_taskbar_follow_refresh_plan,
    build_taskbar_autohide_interaction_plan,
    build_taskbar_preview_drag_plan,
    build_taskbar_preview_drop_acceptance_plan,
    build_taskbar_drag_action_plan,
    build_taskbar_dock_plan,
    build_drag_update_entry_plan,
    build_finder_attach_plan,
    build_finder_drag_action_plan,
    build_standalone_drag_direction_plan,
    build_standalone_stack_direction_plan,
    available_location_modes_for_state,
    canonical_location_mode,
    canonical_position_preset,
    center_for_position_ratio,
    clamp_rect_x_to_screen,
    clamp_standalone_window_pos,
    clamp_taskbar_window_pos,
    compact_music_target_width_from_content,
    decide_standalone_release_action,
    drag_main_offset_for_press,
    empty_bar_orientation,
    empty_bar_size,
    fullscreen_placement_mode,
    extend_taskbar_left_handle_visible_until,
    expanded_stack_height,
    fallback_taskbar_task_area_rect,
    hidden_autohide_follow_rect,
    intended_zone_rect,
    MainSizeFacts,
    main_size_for_kind,
    message_dot_rect,
    nudge_window_pos_outside_taskbar,
    plugin_alert_circle_size,
    plugin_alert_main_rect_screen_pressure,
    plugin_alert_rect,
    plugin_alert_row_fits,
    plugin_alert_side_for_row,
    plugin_alert_side_space,
    plugin_alert_uses_ring,
    plugin_alert_width_for_row,
    plugin_alert_x_for_side,
    plugin_alert_x_for_slot,
    plugin_active_focus,
    plugin_focus_index_for_x,
    plugin_focus_weights,
    plugin_item_rects,
    plugin_panel_height,
    plugin_panel_layout,
    plugin_panel_shell_rect,
    plugin_panel_time_layout,
    plugin_slot_widths,
    plugin_strip_layout,
    position_ratio_for_center,
    point_in_finder_frame,
    rect_x_fits_screen,
    resolve_taskbar_rect_for_screen,
    should_enter_taskbar_for_visible_taskbar_release,
    should_snap_above_taskbar_for_release,
    should_snap_top_notch_for_release,
    select_taskbar_drop_zone,
    select_taskbar_autohide_probe_rect,
    select_bottom_taskbar_rect,
    select_taskbar_drag_track_rect,
    select_taskbar_drag_reference_rect,
    select_taskbar_task_area_rect,
    select_taskbar_track_rect,
    select_windows_taskbar_occupied_rects,
    select_windows_taskbar_task_area_rect,
    should_show_taskbar_preview,
    should_show_taskbar_preview_for_rect,
    standalone_bottom_boundary_extension,
    standalone_release_probe_point,
    standalone_release_direction_for_anchor,
    standalone_main_top_for_direction,
    standalone_stack_center_x,
    standalone_stack_width_for_limit,
    stack_target_width,
    standalone_width_limit_for_global_center,
    stick_taskbar_above_gap,
    stack_rect_for_direction,
    side_taskbar_slot_rects,
    taskbar_above_gap,
    taskbar_attach_hot_zone_accepts_rect,
    taskbar_attach_hot_zone_contains,
    taskbar_attach_hot_zone_host_rect,
    taskbar_attach_hot_zone_rect,
    taskbar_autohide_cursor_in_taskbar_area,
    taskbar_autohide_cursor_reveals,
    taskbar_autohide_reveal_edge_rect,
    taskbar_autohide_visible_follow_lift,
    taskbar_anchor_main_size,
    taskbar_auto_hide_enabled_for_state,
    taskbar_bottom_boundary_extension,
    taskbar_compact_height_from_rects,
    taskbar_compact_plugin_alert_rect,
    taskbar_contacting_rect,
    taskbar_compact_main_height,
    taskbar_compact_music_size,
    taskbar_corner_radius,
    taskbar_cover_only_music_active,
    taskbar_drag_screen_ratio,
    taskbar_drag_should_detach_to_free,
    taskbar_drag_should_stay_on_track,
    taskbar_drag_track_band_rect,
    taskbar_drop_zone_rect,
    taskbar_follow_force_visible_active,
    taskbar_follow_geometry_key,
    taskbar_follow_rect_key,
    taskbar_follow_refresh_due,
    taskbar_compact_hit_test_key,
    taskbar_full_compact_height_for_rect,
    taskbar_height_from_rect,
    taskbar_message_effective_style,
    taskbar_message_rect,
    taskbar_hidden_fallback_rect,
    taskbar_left_handle_frame_rect,
    taskbar_left_handle_rect,
    taskbar_left_handle_visible,
    taskbar_left_handle_visibility_progress,
    taskbar_material_exclusion_rects,
    taskbar_manual_width_for_left_edge,
    taskbar_selected_drop_zone_local_rect,
    taskbar_shell_rect_is_hidden_autohide,
    taskbar_stack_direction_for_rect,
    taskbar_synced_corner_radius,
    taskbar_width_with_manual,
    tasker_release_point_reaches_bottom_slop,
    time_visible_module_for_width,
    StandaloneReleaseDecision,
    TaskbarFollowTarget,
)


class TaskbarPositioningTests(unittest.TestCase):
    def test_available_location_modes_follow_finder_availability(self):
        self.assertEqual(available_location_modes_for_state(mydockfinder_available=True), ["free", "finder"])
        self.assertEqual(available_location_modes_for_state(mydockfinder_available=False), ["top", "free", "tasker"])
        self.assertEqual(
            available_location_modes_for_state(mydockfinder_available=True, finder_bar_available=True),
            ["free", "finder"],
        )
        self.assertEqual(
            available_location_modes_for_state(mydockfinder_available=True, finder_bar_available=False),
            ["top", "free"],
        )
        self.assertEqual(
            available_location_modes_for_state(mydockfinder_available=False, finder_bar_available=True),
            ["top", "free", "tasker"],
        )

    def test_canonical_location_mode_maps_legacy_values(self):
        self.assertEqual(canonical_location_mode("off", mydockfinder_available=False), "free")
        self.assertEqual(canonical_location_mode("preview", mydockfinder_available=False), "free")
        self.assertEqual(canonical_location_mode("compact", mydockfinder_available=False), "tasker")
        self.assertEqual(canonical_location_mode("expanded", mydockfinder_available=False), "tasker")
        self.assertEqual(canonical_location_mode("taskbar", mydockfinder_available=False), "tasker")
        self.assertEqual(canonical_location_mode("unknown", mydockfinder_available=False), "top")

    def test_canonical_location_mode_enforces_finder_tasker_exclusivity(self):
        self.assertEqual(canonical_location_mode("finder", mydockfinder_available=False), "free")
        self.assertEqual(canonical_location_mode("top", mydockfinder_available=True), "free")
        self.assertEqual(canonical_location_mode("tasker", mydockfinder_available=True), "free")
        self.assertEqual(canonical_location_mode("stick", mydockfinder_available=True), "free")
        self.assertEqual(canonical_location_mode("finder", mydockfinder_available=True), "finder")

    def test_canonical_location_mode_allows_top_only_when_dock_has_no_finder(self):
        for mode, expected in (("top", "top"), ("free", "free"), ("finder", "free"), ("tasker", "free"), ("stick", "free")):
            with self.subTest(mode=mode):
                self.assertEqual(
                    canonical_location_mode(mode, mydockfinder_available=True, finder_bar_available=False),
                    expected,
                )

    def test_fullscreen_placement_follows_each_visible_immersive_owner(self):
        for dock, finder, expected in ((False, False, "tasker"), (False, True, "tasker"), (True, True, "finder")):
            for follow in (False, True):
                with self.subTest(dock=dock, finder=finder, follow=follow):
                    self.assertEqual(
                        fullscreen_placement_mode(
                            mydockfinder_available=dock,
                            finder_bar_available=finder,
                            owner_bar_hidden=False,
                            hide_follow_enabled=follow,
                        ),
                        (expected, False),
                    )

    def test_fullscreen_placement_follows_hidden_owner_or_uses_visible_top(self):
        for dock, finder, expected in ((False, False, "tasker"), (False, True, "tasker"), (True, True, "finder")):
            for follow in (False, True):
                with self.subTest(dock=dock, finder=finder, follow=follow):
                    self.assertEqual(
                        fullscreen_placement_mode(
                            mydockfinder_available=dock,
                            finder_bar_available=finder,
                            owner_bar_hidden=True,
                            hide_follow_enabled=follow,
                        ),
                        (expected, True) if follow else ("top", False),
                    )

    def test_fullscreen_placement_with_dock_without_finder_uses_top_and_option_visibility(self):
        for owner_hidden in (False, True):
            for follow in (False, True):
                with self.subTest(owner_hidden=owner_hidden, follow=follow):
                    self.assertEqual(
                        fullscreen_placement_mode(
                            mydockfinder_available=True,
                            finder_bar_available=False,
                            owner_bar_hidden=owner_hidden,
                            hide_follow_enabled=follow,
                        ),
                        ("top", follow),
                    )

    def test_canonical_location_mode_rejects_stick_when_autohide_disabled(self):
        self.assertEqual(
            canonical_location_mode(
                "stick",
                mydockfinder_available=False,
                taskbar_auto_hide_mode="disabled",
            ),
            "free",
        )
        self.assertEqual(
            canonical_location_mode(
                "stick",
                mydockfinder_available=False,
                taskbar_auto_hide_mode="auto",
            ),
            "stick",
        )

    def test_location_mode_transition_plan_enters_finder_with_surface_transition(self):
        plan = build_location_mode_transition_plan(
            "finder",
            "free",
            surface_transition_started=True,
        )

        self.assertTrue(plan.mode_changed)
        self.assertEqual(plan.begin_surface_transition_reason, "finder")
        self.assertTrue(plan.reset_runtime_state)
        self.assertFalse(plan.reset_clear_surface)
        self.assertTrue(plan.finder_mode)
        self.assertTrue(plan.attach_to_real_finder)
        self.assertFalse(plan.clear_mask)
        self.assertTrue(plan.refresh_finder_material)
        self.assertEqual(plan.taskbar_fusion_mode, "off")
        self.assertFalse(plan.taskbar_preview_visible)

    def test_location_mode_transition_plan_enters_tasker_and_refreshes_follow(self):
        plan = build_location_mode_transition_plan("tasker", "free")

        self.assertTrue(plan.mode_changed)
        self.assertEqual(plan.begin_surface_transition_reason, "")
        self.assertFalse(plan.finder_mode)
        self.assertFalse(plan.taskbar_above_follow_active)
        self.assertEqual(plan.taskbar_fusion_mode, "compact")
        self.assertTrue(plan.taskbar_preview_visible)
        self.assertTrue(plan.clear_scene_state)
        self.assertTrue(plan.clear_mask)
        self.assertTrue(plan.refresh_taskbar_follow_position)
        self.assertTrue(plan.sync_taskbar_color)

    def test_location_mode_transition_plan_respects_drag_candidate_refresh_guards(self):
        tasker = build_location_mode_transition_plan("tasker", "free", drag_candidate=True)
        stick = build_location_mode_transition_plan("stick", "free", drag_candidate=True)
        top = build_location_mode_transition_plan("top", "free", drag_candidate=True)

        self.assertFalse(tasker.refresh_taskbar_follow_position)
        self.assertFalse(stick.refresh_taskbar_above_follow_position)
        self.assertFalse(top.settle_top_notch)

    def test_location_mode_transition_plan_stick_and_free_clear_scene_state(self):
        stick = build_location_mode_transition_plan("stick", "free")
        free = build_location_mode_transition_plan("free", "tasker")

        self.assertTrue(stick.taskbar_above_follow_active)
        self.assertFalse(stick.taskbar_preview_visible)
        self.assertTrue(stick.refresh_taskbar_above_follow_position)
        self.assertTrue(stick.clear_scene_state)
        self.assertFalse(free.taskbar_above_follow_active)
        self.assertEqual(free.taskbar_fusion_mode, "off")
        self.assertFalse(free.taskbar_preview_visible)
        self.assertTrue(free.clear_mask)

    def test_taskbar_above_follow_refresh_plan_ignores_inactive_state(self):
        plan = build_taskbar_above_follow_refresh_plan(
            above_follow_active=False,
            location_mode="stick",
            finder_mode=False,
            taskbar_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            auto_hide_enabled=True,
            target_anchor_rect=QRectF(700.0, 980.0, 420.0, 48.0),
            current_anchor_rect=QRectF(),
        )

        self.assertEqual(plan.action, "inactive")
        self.assertFalse(plan.clear_above_taskbar_follow)
        self.assertFalse(plan.settle_above_taskbar)

    def test_taskbar_above_follow_refresh_plan_clears_invalid_mode_without_forcing_free(self):
        free = build_taskbar_above_follow_refresh_plan(
            above_follow_active=True,
            location_mode="free",
            finder_mode=False,
            taskbar_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            auto_hide_enabled=True,
            target_anchor_rect=QRectF(),
            current_anchor_rect=QRectF(),
        )
        finder = build_taskbar_above_follow_refresh_plan(
            above_follow_active=True,
            location_mode="stick",
            finder_mode=True,
            taskbar_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            auto_hide_enabled=True,
            target_anchor_rect=QRectF(),
            current_anchor_rect=QRectF(),
        )

        self.assertEqual(free.action, "clear_invalid_mode")
        self.assertTrue(free.clear_above_taskbar_follow)
        self.assertEqual(free.force_location_mode, "")
        self.assertEqual(finder.action, "clear_invalid_mode")
        self.assertTrue(finder.clear_above_taskbar_follow)

    def test_taskbar_above_follow_refresh_plan_missing_taskbar_preserves_runtime_state(self):
        plan = build_taskbar_above_follow_refresh_plan(
            above_follow_active=True,
            location_mode="stick",
            finder_mode=False,
            taskbar_rect=QRectF(),
            auto_hide_enabled=True,
            target_anchor_rect=QRectF(),
            current_anchor_rect=QRectF(),
        )

        self.assertEqual(plan.action, "missing_taskbar")
        self.assertFalse(plan.clear_above_taskbar_follow)
        self.assertFalse(plan.settle_above_taskbar)

    def test_taskbar_above_follow_refresh_plan_disables_stick_when_autohide_turns_off(self):
        plan = build_taskbar_above_follow_refresh_plan(
            above_follow_active=True,
            location_mode="stick",
            finder_mode=False,
            taskbar_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            auto_hide_enabled=False,
            target_anchor_rect=QRectF(700.0, 980.0, 420.0, 48.0),
            current_anchor_rect=QRectF(),
        )

        self.assertEqual(plan.action, "clear_autohide_disabled")
        self.assertTrue(plan.clear_above_taskbar_follow)
        self.assertEqual(plan.force_location_mode, "free")
        self.assertFalse(plan.settle_above_taskbar)

    def test_taskbar_above_follow_refresh_plan_prefers_target_anchor_and_falls_back_to_taskbar(self):
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        target_anchor = QRectF(700.0, 980.0, 420.0, 48.0)
        current_anchor = QRectF(710.0, 990.0, 400.0, 42.0)
        target = build_taskbar_above_follow_refresh_plan(
            above_follow_active=True,
            location_mode="stick",
            finder_mode=False,
            taskbar_rect=taskbar,
            auto_hide_enabled=True,
            target_anchor_rect=target_anchor,
            current_anchor_rect=current_anchor,
        )
        current = build_taskbar_above_follow_refresh_plan(
            above_follow_active=True,
            location_mode="stick",
            finder_mode=False,
            taskbar_rect=taskbar,
            auto_hide_enabled=True,
            target_anchor_rect=QRectF(),
            current_anchor_rect=current_anchor,
        )
        fallback = build_taskbar_above_follow_refresh_plan(
            above_follow_active=True,
            location_mode="stick",
            finder_mode=False,
            taskbar_rect=taskbar,
            auto_hide_enabled=True,
            target_anchor_rect=QRectF(),
            current_anchor_rect=QRectF(),
        )

        self.assertEqual(target.action, "settle")
        self.assertTrue(target.settle_above_taskbar)
        self.assertEqual(target.anchor_rect, target_anchor)
        self.assertEqual(current.anchor_rect, current_anchor)
        self.assertEqual(fallback.anchor_rect, QRectF(taskbar.center().x(), taskbar.top(), 1.0, 1.0))

    def test_location_mode_transition_plan_leaves_taskbar_color_when_mode_unchanged(self):
        plan = build_location_mode_transition_plan("tasker", "tasker")

        self.assertFalse(plan.mode_changed)
        self.assertFalse(plan.reset_runtime_state)
        self.assertFalse(plan.sync_taskbar_color)

    def test_taskbar_auto_hide_mode_plan_normalizes_invalid_values(self):
        plan = build_taskbar_auto_hide_mode_plan(
            "unexpected",
            current_location_mode="tasker",
        )

        self.assertEqual(plan.mode, "auto")
        self.assertTrue(plan.reset_follow_cache)
        self.assertFalse(plan.clear_above_taskbar_follow)
        self.assertEqual(plan.force_location_mode, "")
        self.assertFalse(plan.clear_taskbar_drop_state)

    def test_taskbar_auto_hide_mode_plan_disabling_stick_forces_free(self):
        plan = build_taskbar_auto_hide_mode_plan(
            "disabled",
            current_location_mode="stick",
        )

        self.assertEqual(plan.mode, "disabled")
        self.assertTrue(plan.clear_above_taskbar_follow)
        self.assertEqual(plan.force_location_mode, "free")
        self.assertFalse(plan.force_finder_mode)
        self.assertFalse(plan.force_attach_to_real_finder)
        self.assertEqual(plan.force_taskbar_fusion_mode, "off")
        self.assertTrue(plan.clear_taskbar_preview)
        self.assertTrue(plan.clear_taskbar_drop_state)
        self.assertTrue(plan.hide_taskbar_drop_overlay)

    def test_taskbar_auto_hide_mode_plan_disabling_tasker_clears_runtime_without_mode_change(self):
        plan = build_taskbar_auto_hide_mode_plan(
            "disabled",
            current_location_mode="tasker",
        )

        self.assertEqual(plan.force_location_mode, "")
        self.assertIsNone(plan.force_finder_mode)
        self.assertIsNone(plan.force_attach_to_real_finder)
        self.assertEqual(plan.force_taskbar_fusion_mode, "")
        self.assertTrue(plan.clear_above_taskbar_follow)
        self.assertTrue(plan.clear_taskbar_preview)
        self.assertTrue(plan.clear_taskbar_drop_state)

    def test_taskbar_auto_hide_enabled_state_respects_screen_finder_mode_and_detection(self):
        self.assertFalse(
            taskbar_auto_hide_enabled_for_state(
                screen_available=False,
                mydockfinder_available=False,
                taskbar_auto_hide_mode="enabled",
                detected_auto_hide=True,
            )
        )
        self.assertFalse(
            taskbar_auto_hide_enabled_for_state(
                screen_available=True,
                mydockfinder_available=True,
                taskbar_auto_hide_mode="enabled",
                detected_auto_hide=True,
            )
        )
        self.assertTrue(
            taskbar_auto_hide_enabled_for_state(
                screen_available=True,
                mydockfinder_available=False,
                taskbar_auto_hide_mode="enabled",
                detected_auto_hide=False,
            )
        )
        self.assertFalse(
            taskbar_auto_hide_enabled_for_state(
                screen_available=True,
                mydockfinder_available=False,
                taskbar_auto_hide_mode="disabled",
                detected_auto_hide=True,
            )
        )
        self.assertTrue(
            taskbar_auto_hide_enabled_for_state(
                screen_available=True,
                mydockfinder_available=False,
                taskbar_auto_hide_mode="auto",
                detected_auto_hide=True,
            )
        )

    def test_taskbar_fusion_mode_plan_normalizes_invalid_values(self):
        plan = build_taskbar_fusion_mode_plan("bad")

        self.assertEqual(plan.mode, "off")
        self.assertIsNone(plan.force_expanded)
        self.assertIsNone(plan.force_plugin_panel_open)
        self.assertFalse(plan.taskbar_preview_visible_before_location_change)
        self.assertEqual(plan.target_location_mode, "free")

    def test_taskbar_fusion_mode_plan_maps_preview_and_compact_modes(self):
        preview = build_taskbar_fusion_mode_plan("preview")
        compact = build_taskbar_fusion_mode_plan("compact")

        self.assertTrue(preview.taskbar_preview_visible_before_location_change)
        self.assertEqual(preview.target_location_mode, "free")
        self.assertFalse(compact.force_expanded)
        self.assertIsNone(compact.force_plugin_panel_open)
        self.assertIsNone(compact.taskbar_preview_visible_before_location_change)
        self.assertEqual(compact.target_location_mode, "tasker")

    def test_taskbar_fusion_mode_plan_maps_expanded_mode(self):
        plan = build_taskbar_fusion_mode_plan("expanded")

        self.assertTrue(plan.force_expanded)
        self.assertTrue(plan.force_plugin_panel_open)
        self.assertIsNone(plan.taskbar_preview_visible_before_location_change)
        self.assertEqual(plan.target_location_mode, "tasker")

    def test_standalone_release_execution_plan_tasker_requests_dock_transition(self):
        plan = build_standalone_release_execution_plan(StandaloneReleaseDecision("tasker"))

        self.assertEqual(plan.action, "tasker")
        self.assertFalse(plan.execute_top_notch)
        self.assertTrue(plan.execute_taskbar_dock)
        self.assertFalse(plan.execute_above_taskbar)
        self.assertFalse(plan.execute_free_placement)
        self.assertTrue(plan.has_adapter_action)
        self.assertTrue(plan.clear_above_taskbar_follow)
        self.assertEqual(plan.set_location_mode, "tasker")
        self.assertTrue(plan.request_taskbar_dock_animation)
        self.assertEqual(plan.force_location_mode, "")
        self.assertFalse(plan.clear_taskbar_preview)

    def test_standalone_release_execution_plan_top_and_stick_set_adapter_actions(self):
        top = build_standalone_release_execution_plan(StandaloneReleaseDecision("top"))
        stick = build_standalone_release_execution_plan(StandaloneReleaseDecision("stick"))

        self.assertTrue(top.execute_top_notch)
        self.assertFalse(top.execute_taskbar_dock)
        self.assertFalse(top.execute_above_taskbar)
        self.assertFalse(top.execute_free_placement)
        self.assertFalse(stick.execute_top_notch)
        self.assertFalse(stick.execute_taskbar_dock)
        self.assertTrue(stick.execute_above_taskbar)
        self.assertFalse(stick.execute_free_placement)

    def test_standalone_release_execution_plan_free_only_clears_above_follow_by_default(self):
        plan = build_standalone_release_execution_plan(StandaloneReleaseDecision("free"))

        self.assertEqual(plan.action, "free")
        self.assertFalse(plan.execute_top_notch)
        self.assertFalse(plan.execute_taskbar_dock)
        self.assertFalse(plan.execute_above_taskbar)
        self.assertTrue(plan.execute_free_placement)
        self.assertTrue(plan.clear_above_taskbar_follow)
        self.assertEqual(plan.force_location_mode, "")
        self.assertIsNone(plan.force_finder_mode)
        self.assertIsNone(plan.force_attach_to_real_finder)
        self.assertEqual(plan.force_taskbar_fusion_mode, "")
        self.assertFalse(plan.clear_taskbar_preview)

    def test_standalone_release_execution_plan_none_has_no_adapter_action(self):
        plan = build_standalone_release_execution_plan(StandaloneReleaseDecision("none"))

        self.assertEqual(plan.action, "none")
        self.assertFalse(plan.execute_top_notch)
        self.assertFalse(plan.execute_taskbar_dock)
        self.assertFalse(plan.execute_above_taskbar)
        self.assertFalse(plan.execute_free_placement)
        self.assertFalse(plan.has_adapter_action)
        self.assertFalse(plan.clear_above_taskbar_follow)

    def test_standalone_release_execution_plan_edge_notch_exit_forces_plain_free_mode(self):
        plan = build_standalone_release_execution_plan(
            StandaloneReleaseDecision("free", exiting_edge_notch_mode=True)
        )

        self.assertTrue(plan.execute_free_placement)
        self.assertTrue(plan.clear_above_taskbar_follow)
        self.assertEqual(plan.force_location_mode, "free")
        self.assertFalse(plan.force_finder_mode)
        self.assertFalse(plan.force_attach_to_real_finder)
        self.assertEqual(plan.force_taskbar_fusion_mode, "off")
        self.assertTrue(plan.clear_taskbar_preview)

    def test_mouse_release_action_plan_suppressed_only_resets_drag_state(self):
        plan = build_mouse_release_action_plan(
            scene_suppressed=True,
            drag_candidate=True,
            dragging=True,
            taskbar_preview_visible=True,
        )

        self.assertEqual(plan.action, "suppressed")
        self.assertTrue(plan.reset_drag_state)
        self.assertFalse(plan.save_placement_state)
        self.assertFalse(plan.activate_pressed_key)
        self.assertFalse(plan.capture_release_anchor)
        self.assertFalse(plan.accept_taskbar_drop)

    def test_mouse_release_action_plan_click_activates_without_drag_release(self):
        plan = build_mouse_release_action_plan(
            scene_suppressed=False,
            drag_candidate=True,
            dragging=False,
            taskbar_preview_visible=True,
        )

        self.assertEqual(plan.action, "activate")
        self.assertTrue(plan.activate_pressed_key)
        self.assertTrue(plan.update_after_activation)
        self.assertFalse(plan.capture_release_anchor)
        self.assertFalse(plan.recheck_taskbar_preview)
        self.assertFalse(plan.accept_taskbar_drop)

    def test_mouse_release_action_plan_drag_rechecks_preview_before_drop(self):
        plan = build_mouse_release_action_plan(
            scene_suppressed=False,
            drag_candidate=True,
            dragging=True,
            taskbar_preview_visible=True,
        )

        self.assertEqual(plan.action, "drag_release")
        self.assertTrue(plan.capture_release_anchor)
        self.assertTrue(plan.recheck_taskbar_preview)
        self.assertTrue(plan.accept_taskbar_drop)
        self.assertFalse(plan.activate_pressed_key)

    def test_mouse_release_action_plan_drag_without_preview_skips_preview_recheck(self):
        plan = build_mouse_release_action_plan(
            scene_suppressed=False,
            drag_candidate=True,
            dragging=True,
            taskbar_preview_visible=False,
        )

        self.assertEqual(plan.action, "drag_release")
        self.assertTrue(plan.capture_release_anchor)
        self.assertFalse(plan.recheck_taskbar_preview)
        self.assertTrue(plan.accept_taskbar_drop)

    def test_mouse_release_standalone_settle_plan_skips_taskbar_and_accepted_drop(self):
        accepted = build_mouse_release_standalone_settle_plan(
            accepted_taskbar_drop=True,
            has_release_anchor=True,
            location_mode="free",
        )
        tasker = build_mouse_release_standalone_settle_plan(
            accepted_taskbar_drop=False,
            has_release_anchor=True,
            location_mode="tasker",
        )
        normal = build_mouse_release_standalone_settle_plan(
            accepted_taskbar_drop=False,
            has_release_anchor=True,
            location_mode="free",
        )

        self.assertFalse(accepted.check_tasker_detach_above_taskbar)
        self.assertFalse(accepted.settle_standalone_release)
        self.assertFalse(tasker.check_tasker_detach_above_taskbar)
        self.assertFalse(tasker.settle_standalone_release)
        self.assertTrue(normal.check_tasker_detach_above_taskbar)
        self.assertTrue(normal.settle_standalone_release)

    def test_standalone_release_probe_point_prefers_release_point_and_falls_back_to_anchor(self):
        anchor = QRectF(100.0, 200.0, 420.0, 48.0)

        self.assertEqual(
            standalone_release_probe_point(anchor, QPointF(910.4, 1077.6)),
            QPoint(910, 1078),
        )
        self.assertEqual(
            standalone_release_probe_point(anchor, None),
            QPoint(310, 224),
        )
        self.assertIsNone(standalone_release_probe_point(QRectF(), None))

    def test_tasker_release_detach_plan_wraps_compact_bottom_slop_rule(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)

        detach = build_tasker_release_detach_plan(
            QPoint(920, 1078),
            screen,
            taskbar,
            taskbar_compact_active=True,
        )
        keep = build_tasker_release_detach_plan(
            QPoint(920, 1050),
            screen,
            taskbar,
            taskbar_compact_active=True,
        )
        non_compact = build_tasker_release_detach_plan(
            QPoint(920, 1078),
            screen,
            taskbar,
            taskbar_compact_active=False,
        )

        self.assertTrue(detach.should_detach)
        self.assertFalse(keep.should_detach)
        self.assertFalse(non_compact.should_detach)

    def test_mouse_release_cleanup_plan_for_accepted_taskbar_drop_clears_attach_only(self):
        plan = build_mouse_release_cleanup_plan(
            accepted_taskbar_drop=True,
            has_release_anchor=True,
        )

        self.assertFalse(plan.clear_taskbar_preview)
        self.assertFalse(plan.clear_taskbar_selected_drop_zone)
        self.assertTrue(plan.clear_taskbar_attach_hot_zone)
        self.assertFalse(plan.clear_taskbar_drop_zones)
        self.assertTrue(plan.hide_taskbar_drop_overlay)

    def test_mouse_release_cleanup_plan_for_normal_drag_release_clears_preview_runtime(self):
        plan = build_mouse_release_cleanup_plan(
            accepted_taskbar_drop=False,
            has_release_anchor=True,
        )

        self.assertTrue(plan.clear_taskbar_preview)
        self.assertTrue(plan.clear_taskbar_selected_drop_zone)
        self.assertTrue(plan.clear_taskbar_attach_hot_zone)
        self.assertTrue(plan.clear_taskbar_drop_zones)
        self.assertTrue(plan.hide_taskbar_drop_overlay)

    def test_mouse_release_cleanup_plan_for_click_release_does_not_clear_taskbar_preview(self):
        plan = build_mouse_release_cleanup_plan(
            accepted_taskbar_drop=False,
            has_release_anchor=False,
        )

        self.assertFalse(plan.clear_taskbar_preview)
        self.assertFalse(plan.clear_taskbar_selected_drop_zone)
        self.assertFalse(plan.clear_taskbar_attach_hot_zone)
        self.assertFalse(plan.clear_taskbar_drop_zones)
        self.assertFalse(plan.hide_taskbar_drop_overlay)

    def test_taskbar_presence_plan_inside_shows_preview_and_syncs_overlay(self):
        plan = build_taskbar_presence_plan(
            True,
            current_location_mode="free",
        )

        self.assertTrue(plan.inside)
        self.assertTrue(plan.taskbar_preview_visible)
        self.assertEqual(plan.set_location_mode, "")
        self.assertFalse(plan.clear_taskbar_attach_hot_zone)
        self.assertTrue(plan.sync_taskbar_drop_overlay)

    def test_taskbar_presence_plan_leaving_tasker_clears_preview_and_returns_free(self):
        plan = build_taskbar_presence_plan(
            False,
            current_location_mode="tasker",
        )

        self.assertFalse(plan.inside)
        self.assertFalse(plan.taskbar_preview_visible)
        self.assertEqual(plan.set_location_mode, "free")
        self.assertTrue(plan.clear_taskbar_selected_drop_zone)
        self.assertTrue(plan.clear_taskbar_attach_hot_zone)
        self.assertTrue(plan.clear_taskbar_drop_zones)
        self.assertTrue(plan.hide_taskbar_drop_overlay)
        self.assertFalse(plan.sync_taskbar_drop_overlay)

    def test_taskbar_presence_plan_leaving_non_tasker_only_clears_preview_runtime(self):
        plan = build_taskbar_presence_plan(
            False,
            current_location_mode="free",
        )

        self.assertEqual(plan.set_location_mode, "")
        self.assertTrue(plan.clear_taskbar_selected_drop_zone)
        self.assertTrue(plan.clear_taskbar_attach_hot_zone)
        self.assertTrue(plan.clear_taskbar_drop_zones)
        self.assertTrue(plan.hide_taskbar_drop_overlay)

    def test_taskbar_preview_drag_plan_blocks_prompt_when_taskbar_is_not_allowed(self):
        plan = build_taskbar_preview_drag_plan(
            taskbar_allowed=False,
            prompt_allowed=True,
            hot_zone=QRectF(760.0, 450.0, 400.0, 160.0),
            hot_zone_host_rect=QRectF(0.0, 0.0, 1920.0, 1080.0),
            taskbar_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            main_rect=QRectF(760.0, 500.0, 420.0, 48.0),
        )

        self.assertFalse(plan.prompt_visible)
        self.assertFalse(plan.active)
        self.assertTrue(plan.taskbar_attach_hot_zone.isNull())
        self.assertFalse(plan.needs_drop_zones)
        self.assertEqual(plan.taskbar_drop_zones, ())

    def test_taskbar_preview_drag_plan_shows_prompt_without_arming_until_main_rect_enters_hot_zone(self):
        plan = build_taskbar_preview_drag_plan(
            taskbar_allowed=True,
            prompt_allowed=True,
            hot_zone=QRectF(760.0, 450.0, 400.0, 160.0),
            hot_zone_host_rect=QRectF(0.0, 0.0, 1920.0, 1080.0),
            taskbar_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            main_rect=QRectF(100.0, 100.0, 420.0, 48.0),
        )

        self.assertTrue(plan.prompt_visible)
        self.assertFalse(plan.active)
        self.assertFalse(plan.taskbar_attach_hot_zone.isNull())
        self.assertFalse(plan.needs_drop_zones)
        self.assertEqual(plan.taskbar_drop_zones, ())
        self.assertTrue(plan.taskbar_selected_drop_zone.isNull())

    def test_taskbar_preview_drag_plan_requests_drop_zones_only_after_hot_zone_is_armed(self):
        plan = build_taskbar_preview_drag_plan(
            taskbar_allowed=True,
            prompt_allowed=True,
            hot_zone=QRectF(760.0, 450.0, 400.0, 160.0),
            hot_zone_host_rect=QRectF(0.0, 0.0, 1920.0, 1080.0),
            taskbar_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            main_rect=QRectF(750.0, 516.0, 420.0, 48.0),
        )

        self.assertTrue(plan.prompt_visible)
        self.assertTrue(plan.active)
        self.assertTrue(plan.needs_drop_zones)
        self.assertTrue(plan.update_taskbar_material)
        self.assertEqual(plan.last_taskbar_rect, QRectF(0.0, 1040.0, 1920.0, 40.0))

        selected = QRectF(100.0, 1044.0, 420.0, 32.0)
        with_zones = build_taskbar_preview_drag_plan(
            taskbar_allowed=True,
            prompt_allowed=True,
            hot_zone=QRectF(760.0, 450.0, 400.0, 160.0),
            hot_zone_host_rect=QRectF(0.0, 0.0, 1920.0, 1080.0),
            taskbar_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            main_rect=QRectF(750.0, 516.0, 420.0, 48.0),
            drop_zones=[selected],
            selected_drop_zone=selected,
        )

        self.assertFalse(with_zones.needs_drop_zones)
        self.assertEqual(with_zones.taskbar_drop_zones, (selected,))
        self.assertEqual(with_zones.taskbar_selected_drop_zone, selected)

    def test_taskbar_preview_drop_acceptance_plan_rejects_inactive_prompt_states(self):
        not_allowed = build_taskbar_preview_drop_acceptance_plan(
            taskbar_allowed=False,
            taskbar_preview_visible=True,
            taskbar_attach_hot_zone_active=True,
        )
        hidden_prompt = build_taskbar_preview_drop_acceptance_plan(
            taskbar_allowed=True,
            taskbar_preview_visible=False,
            taskbar_attach_hot_zone_active=True,
        )
        unarmed_prompt = build_taskbar_preview_drop_acceptance_plan(
            taskbar_allowed=True,
            taskbar_preview_visible=True,
            taskbar_attach_hot_zone_active=False,
        )

        self.assertFalse(not_allowed.accepted)
        self.assertFalse(hidden_prompt.accepted)
        self.assertFalse(unarmed_prompt.accepted)
        self.assertEqual(not_allowed.set_location_mode, "")
        self.assertFalse(hidden_prompt.request_taskbar_dock_animation)
        self.assertFalse(unarmed_prompt.clear_taskbar_attach_hot_zone)

    def test_taskbar_preview_drop_acceptance_plan_accepts_armed_prompt(self):
        plan = build_taskbar_preview_drop_acceptance_plan(
            taskbar_allowed=True,
            taskbar_preview_visible=True,
            taskbar_attach_hot_zone_active=True,
        )

        self.assertTrue(plan.accepted)
        self.assertEqual(plan.set_location_mode, "tasker")
        self.assertTrue(plan.request_taskbar_dock_animation)
        self.assertTrue(plan.clear_taskbar_attach_hot_zone)
        self.assertTrue(plan.hide_taskbar_drop_overlay)

    def test_taskbar_dock_plan_rejects_missing_screen_or_taskbar(self):
        no_screen = build_taskbar_dock_plan(
            screen_available=False,
            taskbar_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            probe_x=960.0,
        )
        no_taskbar = build_taskbar_dock_plan(
            screen_available=True,
            taskbar_rect=QRectF(),
            probe_x=960.0,
        )

        self.assertFalse(no_screen.valid)
        self.assertFalse(no_taskbar.valid)
        self.assertTrue(no_screen.visible_taskbar.isNull())
        self.assertFalse(no_taskbar.mark_follow_force_visible)

    def test_taskbar_dock_plan_forces_visible_follow_and_consumes_animation_request(self):
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        explicit = build_taskbar_dock_plan(
            screen_available=True,
            taskbar_rect=taskbar,
            probe_x=960.0,
            target_center_x=1500.0,
            dock_animation_requested=True,
        )
        fallback = build_taskbar_dock_plan(
            screen_available=True,
            taskbar_rect=taskbar,
            probe_x=960.0,
            target_center_x="bad",
            dock_animation_requested=False,
        )

        self.assertTrue(explicit.valid)
        self.assertEqual(explicit.visible_taskbar, taskbar)
        self.assertAlmostEqual(explicit.target_center_x, 1500.0)
        self.assertTrue(explicit.mark_follow_force_visible)
        self.assertTrue(explicit.force_visible)
        self.assertFalse(explicit.use_current_visibility)
        self.assertTrue(explicit.refresh_material)
        self.assertTrue(explicit.animate)
        self.assertTrue(explicit.dock)
        self.assertTrue(explicit.consume_dock_animation_request)
        self.assertAlmostEqual(fallback.target_center_x, 960.0)
        self.assertFalse(fallback.animate)
        self.assertFalse(fallback.dock)

    def test_taskbar_drag_action_plan_resizes_left_handle_on_track(self):
        plan = build_taskbar_drag_action_plan(
            location_mode="tasker",
            drag_press_key="taskbar:left-handle",
            left_handle_stays_on_track=True,
            should_detach_to_free=True,
        )

        self.assertEqual(plan.action, "resize_taskbar_left_handle")
        self.assertTrue(plan.resize_taskbar_left_handle)
        self.assertFalse(plan.move_taskbar_drag)
        self.assertFalse(plan.move_standalone_drag)

    def test_taskbar_drag_action_plan_detaches_tasker_to_free(self):
        plan = build_taskbar_drag_action_plan(
            location_mode="tasker",
            drag_press_key="music",
            left_handle_stays_on_track=False,
            should_detach_to_free=True,
        )

        self.assertEqual(plan.action, "detach_to_free")
        self.assertTrue(plan.remember_motion_global_rects)
        self.assertTrue(plan.leave_taskbar_presence)
        self.assertTrue(plan.move_standalone_drag)
        self.assertTrue(plan.update_taskbar_preview)
        self.assertFalse(plan.move_taskbar_drag)

    def test_taskbar_drag_action_plan_keeps_tasker_drag_when_not_detaching(self):
        plan = build_taskbar_drag_action_plan(
            location_mode="tasker",
            drag_press_key="music",
            left_handle_stays_on_track=False,
            should_detach_to_free=False,
        )

        self.assertEqual(plan.action, "move_taskbar_drag")
        self.assertTrue(plan.move_taskbar_drag)
        self.assertFalse(plan.leave_taskbar_presence)
        self.assertFalse(plan.move_standalone_drag)

    def test_taskbar_drag_action_plan_moves_standalone_for_non_tasker_modes(self):
        plan = build_taskbar_drag_action_plan(
            location_mode="free",
            drag_press_key="music",
            left_handle_stays_on_track=False,
            should_detach_to_free=True,
        )

        self.assertEqual(plan.action, "move_standalone_drag")
        self.assertTrue(plan.move_standalone_drag)
        self.assertTrue(plan.update_taskbar_preview)
        self.assertFalse(plan.move_taskbar_drag)

    def test_drag_update_entry_plan_waits_until_candidate_crosses_threshold(self):
        ignored = build_drag_update_entry_plan(
            drag_candidate=False,
            dragging=False,
            delta=QPoint(100, 100),
            drag_threshold=5.0,
            finder_mode=False,
        )
        waiting = build_drag_update_entry_plan(
            drag_candidate=True,
            dragging=False,
            delta=QPoint(2, 2),
            drag_threshold=5.0,
            finder_mode=False,
        )

        self.assertEqual(ignored.action, "ignore")
        self.assertFalse(ignored.pulse_stack)
        self.assertEqual(waiting.action, "await_threshold")
        self.assertFalse(waiting.start_drag)
        self.assertFalse(waiting.route_standard_drag)

    def test_drag_update_entry_plan_starts_standard_or_finder_drag(self):
        standard = build_drag_update_entry_plan(
            drag_candidate=True,
            dragging=False,
            delta=QPoint(7, 0),
            drag_threshold=5.0,
            finder_mode=False,
        )
        finder = build_drag_update_entry_plan(
            drag_candidate=True,
            dragging=True,
            delta=QPoint(0, 20),
            drag_threshold=5.0,
            finder_mode=True,
        )

        self.assertEqual(standard.action, "standard_drag")
        self.assertTrue(standard.start_drag)
        self.assertTrue(standard.collapse_expanded)
        self.assertTrue(standard.route_standard_drag)
        self.assertTrue(standard.pulse_stack)
        self.assertEqual(finder.action, "finder_drag")
        self.assertFalse(finder.start_drag)
        self.assertTrue(finder.route_finder_drag)
        self.assertTrue(finder.pulse_stack)

    def test_finder_drag_action_plan_detaches_only_past_downward_threshold(self):
        follow = build_finder_drag_action_plan(delta_y=18.0, detach_threshold=22.0)
        detach = build_finder_drag_action_plan(delta_y=24.0, detach_threshold=22.0)

        self.assertEqual(follow.action, "follow_finder")
        self.assertTrue(follow.follow_finder)
        self.assertFalse(follow.detach_to_free)
        self.assertEqual(detach.action, "detach_to_free")
        self.assertTrue(detach.detach_to_free)
        self.assertTrue(detach.move_standalone_after_detach)
        self.assertTrue(detach.pulse_stack_after_detach)

    def test_finder_attach_plan_requires_allowed_upward_drag_inside_top_threshold(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)

        disallowed = build_finder_attach_plan(
            attach_allowed=False,
            global_pos=QPoint(100, 20),
            delta_y=-10.0,
            screen_geometry=screen,
            finder_bar_height=28.0,
        )
        downward = build_finder_attach_plan(
            attach_allowed=True,
            global_pos=QPoint(100, 20),
            delta_y=1.0,
            screen_geometry=screen,
            finder_bar_height=28.0,
        )
        inside = build_finder_attach_plan(
            attach_allowed=True,
            global_pos=QPoint(100, 42),
            delta_y=-1.0,
            screen_geometry=screen,
            finder_bar_height=20.0,
        )
        outside = build_finder_attach_plan(
            attach_allowed=True,
            global_pos=QPoint(100, 43),
            delta_y=-1.0,
            screen_geometry=screen,
            finder_bar_height=20.0,
        )

        self.assertFalse(disallowed.should_attach)
        self.assertFalse(downward.should_attach)
        self.assertTrue(inside.should_attach)
        self.assertFalse(outside.should_attach)

    def test_finder_attach_plan_uses_taller_finder_bar_threshold(self):
        plan = build_finder_attach_plan(
            attach_allowed=True,
            global_pos=QPoint(100, 65),
            delta_y=-1.0,
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            finder_bar_height=48.0,
        )

        self.assertTrue(plan.should_attach)

    def test_position_ratio_clamps_to_available_track_centers(self):
        track = QRectF(100.0, 1040.0, 1000.0, 40.0)

        self.assertEqual(position_ratio_for_center(track, 200.0, -500.0), 0.0)
        self.assertEqual(position_ratio_for_center(track, 200.0, 2000.0), 1.0)
        self.assertAlmostEqual(position_ratio_for_center(track, 200.0, 600.0), 0.5)

    def test_center_for_position_ratio_uses_main_width_edges(self):
        track = QRectF(100.0, 1040.0, 1000.0, 40.0)

        self.assertAlmostEqual(center_for_position_ratio(track, 200.0, 0.0), 200.0)
        self.assertAlmostEqual(center_for_position_ratio(track, 200.0, 1.0), 1000.0)
        self.assertAlmostEqual(center_for_position_ratio(track, 200.0, 0.5), 600.0)

    def test_hidden_autohide_follow_rect_moves_outside_screen_edge(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)

        bottom = hidden_autohide_follow_rect(screen, QRectF(0.0, 1040.0, 1920.0, 40.0))
        top = hidden_autohide_follow_rect(screen, QRectF(0.0, 0.0, 1920.0, 40.0))
        right = hidden_autohide_follow_rect(screen, QRectF(1880.0, 0.0, 40.0, 1080.0))
        left = hidden_autohide_follow_rect(screen, QRectF(0.0, 0.0, 40.0, 1080.0))

        self.assertEqual(bottom, QRectF(0.0, 1080.0, 1920.0, 40.0))
        self.assertEqual(top, QRectF(0.0, -40.0, 1920.0, 40.0))
        self.assertEqual(right, QRectF(1920.0, 0.0, 40.0, 1080.0))
        self.assertEqual(left, QRectF(-40.0, 0.0, 40.0, 1080.0))

    def test_intended_zone_rect_is_centered_on_anchor(self):
        self.assertEqual(
            intended_zone_rect(500.0, 1050.0, 300.0, 40.0),
            QRectF(350.0, 1030.0, 300.0, 40.0),
        )

    def test_clamp_taskbar_window_pos_allows_bottom_extension_for_hidden_autohide(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)

        visible = clamp_taskbar_window_pos(
            QPoint(490, 1100),
            main_top=10.0,
            main_width=300.0,
            main_height=34.0,
            screen_geometry=screen,
            window_width=940.0,
            bottom_extension=0.0,
        )
        hidden = clamp_taskbar_window_pos(
            QPoint(490, 1100),
            main_top=10.0,
            main_width=300.0,
            main_height=34.0,
            screen_geometry=screen,
            window_width=940.0,
            bottom_extension=40.0,
        )

        self.assertEqual(visible.y() + 10 + 34, 1080)
        self.assertGreater(hidden.y() + 10 + 34, 1080)

    def test_clamp_standalone_window_pos_allows_bottom_extension(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)

        clamped = clamp_standalone_window_pos(
            500.0,
            9999.0,
            main_left=260.0,
            main_top=600.0,
            main_width=420.0,
            main_height=48.0,
            screen_geometry=screen,
            margin=6.0,
            bottom_extension=40.0,
        )

        self.assertAlmostEqual(clamped.y() + 600.0 + 48.0, 1080.0 + 40.0 - 6.0, delta=1.0)

    def test_nudge_window_pos_outside_taskbar_preserves_clearance(self):
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)

        nudged = nudge_window_pos_outside_taskbar(
            QPoint(490, 440),
            taskbar,
            main_top=600.2,
            main_height=39.1,
            direction=-1,
            gap=8.0,
        )

        self.assertLessEqual(nudged.y() + 600.2 + 39.1, taskbar.top() - 8.0)

    def test_stick_gap_flushes_hidden_autohide_and_clears_visible_taskbar(self):
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)

        self.assertAlmostEqual(taskbar_above_gap(taskbar), 4.0)
        self.assertAlmostEqual(stick_taskbar_above_gap(taskbar, hidden_autohide=False), 8.0)
        self.assertAlmostEqual(stick_taskbar_above_gap(taskbar, hidden_autohide=True), 0.0)

    def test_visible_autohide_lift_only_applies_to_bottom_horizontal_taskbar(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)

        self.assertAlmostEqual(
            taskbar_autohide_visible_follow_lift(QRectF(0.0, 1040.0, 1920.0, 40.0), screen),
            4.0,
        )
        self.assertAlmostEqual(
            taskbar_autohide_visible_follow_lift(QRectF(0.0, 0.0, 1920.0, 40.0), screen),
            0.0,
        )

    def test_taskbar_bottom_boundary_extension_requires_autohide_bottom_taskbar(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)

        self.assertEqual(
            taskbar_bottom_boundary_extension(
                QRectF(0.0, 1040.0, 1920.0, 40.0),
                screen,
                auto_hide_enabled=False,
            ),
            0.0,
        )
        self.assertEqual(
            taskbar_bottom_boundary_extension(
                QRectF(0.0, 1040.0, 1920.0, 40.0),
                screen,
                auto_hide_enabled=True,
            ),
            40.0,
        )
        self.assertEqual(
            taskbar_bottom_boundary_extension(
                QRectF(0.0, 0.0, 1920.0, 40.0),
                screen,
                auto_hide_enabled=True,
            ),
            0.0,
        )
        self.assertEqual(
            taskbar_bottom_boundary_extension(
                QRectF(0.0, 0.0, 40.0, 1080.0),
                screen,
                auto_hide_enabled=True,
            ),
            0.0,
        )

    def test_select_bottom_taskbar_rect_uses_bottom_taskbar_or_fallback_only(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)
        bottom = QRectF(0.0, 1040.0, 1920.0, 40.0)
        top = QRectF(0.0, 0.0, 1920.0, 40.0)
        side = QRectF(0.0, 0.0, 48.0, 1080.0)

        self.assertEqual(select_bottom_taskbar_rect(bottom, screen), bottom)
        self.assertTrue(select_bottom_taskbar_rect(top, screen).isNull())
        self.assertTrue(select_bottom_taskbar_rect(side, screen).isNull())
        self.assertEqual(select_bottom_taskbar_rect(QRectF(), screen, bottom), bottom)

    def test_standalone_bottom_boundary_extension_uses_fallback_for_hidden_bottom_taskbar(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)
        hidden_fallback = QRectF(0.0, 1032.0, 1920.0, 48.0)

        self.assertEqual(
            standalone_bottom_boundary_extension(
                QRectF(),
                screen,
                fallback_rect=hidden_fallback,
                auto_hide_enabled=True,
            ),
            48.0,
        )
        self.assertEqual(
            standalone_bottom_boundary_extension(
                QRectF(),
                screen,
                fallback_rect=hidden_fallback,
                auto_hide_enabled=False,
            ),
            0.0,
        )

    def test_taskbar_stack_direction_follows_horizontal_edge(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)

        self.assertEqual(taskbar_stack_direction_for_rect(QRectF(0.0, 1040.0, 1920.0, 40.0), screen), -1)
        self.assertEqual(taskbar_stack_direction_for_rect(QRectF(0.0, 0.0, 1920.0, 40.0), screen), 1)
        self.assertEqual(taskbar_stack_direction_for_rect(QRectF(0.0, 0.0, 48.0, 1080.0), screen), 1)

    def test_fallback_taskbar_task_area_removes_start_and_tray_regions(self):
        area = fallback_taskbar_task_area_rect(QRectF(0.0, 1040.0, 1920.0, 40.0))

        self.assertGreater(area.left(), 0.0)
        self.assertLess(area.right(), 1920.0)
        self.assertEqual(area.height(), 40.0)

    def test_fallback_taskbar_task_area_handles_vertical_taskbar(self):
        area = fallback_taskbar_task_area_rect(QRectF(0.0, 0.0, 48.0, 1080.0))

        self.assertEqual(area.width(), 48.0)
        self.assertGreater(area.top(), 0.0)
        self.assertLess(area.bottom(), 1080.0)

    def test_select_taskbar_task_area_uses_detected_area_inside_taskbar(self):
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        detected = QRectF(-20.0, 1038.0, 900.0, 80.0)

        self.assertEqual(
            select_taskbar_task_area_rect(taskbar, detected),
            QRectF(0.0, 1040.0, 880.0, 40.0),
        )

    def test_select_taskbar_task_area_falls_back_when_detection_is_missing(self):
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)

        self.assertEqual(
            select_taskbar_task_area_rect(taskbar, QRectF()),
            fallback_taskbar_task_area_rect(taskbar),
        )

    def test_select_windows_taskbar_task_area_prefers_real_task_list_class(self):
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        children = [
            ("TrayNotifyWnd", QRectF(1700.0, 1040.0, 220.0, 40.0)),
            ("MSTaskSwWClass", QRectF(160.0, 1040.0, 1300.0, 40.0)),
            ("MSTaskListWClass", QRectF(180.0, 1040.0, 1100.0, 40.0)),
        ]

        self.assertEqual(
            select_windows_taskbar_task_area_rect(taskbar, children),
            QRectF(180.0, 1040.0, 1100.0, 40.0),
        )

    def test_select_windows_taskbar_task_area_filters_tiny_candidates(self):
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        children = [
            ("MSTaskListWClass", QRectF(180.0, 1040.0, 80.0, 40.0)),
            ("MSTaskSwWClass", QRectF(180.0, 1058.0, 1000.0, 10.0)),
        ]

        self.assertIsNone(select_windows_taskbar_task_area_rect(taskbar, children))

    def test_select_windows_taskbar_occupied_rects_ignores_task_area_and_small_children(self):
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        task_area = QRectF(180.0, 1040.0, 1100.0, 40.0)
        children = [
            ("Start", QRectF(0.0, 1040.0, 64.0, 40.0)),
            ("MSTaskListWClass", task_area),
            ("Tiny", QRectF(1300.0, 1040.0, 8.0, 40.0)),
            ("TrayNotifyWnd", QRectF(1700.0, 1040.0, 220.0, 40.0)),
        ]

        self.assertEqual(
            select_windows_taskbar_occupied_rects(taskbar, children, task_area_rect=task_area),
            [
                QRectF(0.0, 1040.0, 64.0, 40.0),
                QRectF(1700.0, 1040.0, 220.0, 40.0),
            ],
        )

    def test_taskbar_preview_rule_requires_allowed_mode_and_threshold(self):
        self.assertTrue(
            should_show_taskbar_preview(
                0.72,
                taskbar_allowed=True,
                recognition_threshold=0.66,
                location_mode="free",
            )
        )
        self.assertFalse(
            should_show_taskbar_preview(
                0.60,
                taskbar_allowed=True,
                recognition_threshold=0.66,
                location_mode="free",
            )
        )
        self.assertFalse(
            should_show_taskbar_preview(
                0.90,
                taskbar_allowed=False,
                recognition_threshold=0.66,
                location_mode="free",
            )
        )
        self.assertFalse(
            should_show_taskbar_preview(
                0.90,
                taskbar_allowed=True,
                recognition_threshold=0.66,
                location_mode="finder",
            )
        )

    def test_taskbar_preview_for_rect_requires_contact_with_slop(self):
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        near_main = QRectF(760.0, 1037.0, 400.0, 64.0)
        far_main = QRectF(760.0, 1000.0, 400.0, 24.0)

        self.assertTrue(
            taskbar_contacting_rect(
                near_main,
                taskbar,
                slop=4.0,
            )
        )
        self.assertFalse(
            taskbar_contacting_rect(
                far_main,
                taskbar,
                slop=4.0,
            )
        )
        self.assertTrue(
            should_show_taskbar_preview_for_rect(
                0.90,
                near_main,
                taskbar,
                taskbar_allowed=True,
                recognition_threshold=0.66,
                location_mode="tasker",
                slop=4.0,
            )
        )
        self.assertFalse(
            should_show_taskbar_preview_for_rect(
                0.90,
                far_main,
                taskbar,
                taskbar_allowed=True,
                recognition_threshold=0.66,
                location_mode="tasker",
                slop=4.0,
            )
        )

    def test_taskbar_drag_screen_ratio_clamps_to_screen_geometry(self):
        screen = QRectF(100.0, 200.0, 1920.0, 1000.0)

        self.assertEqual(taskbar_drag_screen_ratio(QPointF(200.0, 100.0), screen), 0.0)
        self.assertEqual(taskbar_drag_screen_ratio(QPointF(200.0, 1400.0), screen), 1.0)
        self.assertAlmostEqual(taskbar_drag_screen_ratio(QPointF(200.0, 700.0), screen), 0.5)

    def test_select_taskbar_track_rect_uses_task_area_when_available(self):
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        task_area = QRectF(120.0, 1040.0, 1600.0, 40.0)

        self.assertEqual(select_taskbar_drag_track_rect(taskbar), taskbar)
        self.assertEqual(select_taskbar_track_rect(taskbar, task_area), task_area)
        self.assertEqual(select_taskbar_track_rect(taskbar, QRectF()), taskbar)
        self.assertTrue(select_taskbar_track_rect(QRectF(), task_area).isNull())

    def test_hidden_taskbar_fallback_preserves_previous_vertical_edge(self):
        rect = taskbar_hidden_fallback_rect(
            QRectF(0.0, 0.0, 1920.0, 1080.0),
            previous_taskbar_rect=QRectF(0.0, 0.0, 48.0, 1080.0),
        )

        self.assertEqual(rect, QRectF(0.0, 0.0, 48.0, 1080.0))

    def test_hidden_taskbar_fallback_uses_appbar_rect_inside_screen(self):
        rect = taskbar_hidden_fallback_rect(
            QRectF(0.0, 0.0, 1920.0, 1080.0),
            appbar_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
        )

        self.assertEqual(rect, QRectF(0.0, 1040.0, 1920.0, 40.0))

    def test_hidden_taskbar_fallback_defaults_to_bottom_when_edge_unknown(self):
        rect = taskbar_hidden_fallback_rect(QRectF(0.0, 0.0, 1920.0, 1080.0), unit=1.0)

        self.assertEqual(rect, QRectF(0.0, 1032.0, 1920.0, 48.0))

    def test_taskbar_shell_rect_hidden_autohide_uses_appbar_thickness(self):
        self.assertTrue(
            taskbar_shell_rect_is_hidden_autohide(
                QRectF(0.0, 1078.0, 1920.0, 2.0),
                QRectF(0.0, 1040.0, 1920.0, 40.0),
                auto_hide=True,
            )
        )
        self.assertFalse(
            taskbar_shell_rect_is_hidden_autohide(
                QRectF(0.0, 1040.0, 1920.0, 40.0),
                QRectF(0.0, 1040.0, 1920.0, 40.0),
                auto_hide=True,
            )
        )

    def test_translating_full_taskbar_stays_visible_until_one_pixel_at_every_monitor_edge(self):
        for unit in (1.0, 1.5, 2.0):
            for origin in (QPointF(0.0, 0.0), QPointF(-660.0, -360.0)):
                screen = QRectF(origin.x(), origin.y(), 660.0 * unit, 360.0 * unit)
                thickness = 40.0 * unit
                for edge in ("bottom", "top", "left", "right"):
                    def shell_with_visible_remainder(remainder):
                        if edge == "bottom":
                            return QRectF(screen.left(), screen.bottom() - remainder, screen.width(), thickness)
                        if edge == "top":
                            return QRectF(screen.left(), screen.top() + remainder - thickness, screen.width(), thickness)
                        if edge == "left":
                            return QRectF(screen.left() + remainder - thickness, screen.top(), thickness, screen.height())
                        return QRectF(screen.right() - remainder, screen.top(), thickness, screen.height())

                    appbar = shell_with_visible_remainder(thickness)
                    for remainder in (thickness, 20.0, 10.0, 2.0, 1.01, 1.0, 0.0, -4.0):
                        with self.subTest(unit=unit, origin=origin, edge=edge, remainder=remainder):
                            self.assertEqual(
                                taskbar_shell_rect_is_hidden_autohide(
                                    shell_with_visible_remainder(remainder),
                                    appbar,
                                    auto_hide=True,
                                    unit=unit,
                                    screen_geometry=screen,
                                ),
                                remainder <= 1.0,
                            )

    def test_screen_aware_shell_check_keeps_existing_thin_sensor_and_disabled_rules(self):
        screen = QRectF(-660.0, 0.0, 660.0, 360.0)
        appbar = QRectF(-660.0, 320.0, 660.0, 40.0)
        self.assertTrue(
            taskbar_shell_rect_is_hidden_autohide(
                QRectF(-660.0, 358.0, 660.0, 2.0),
                appbar,
                auto_hide=True,
                screen_geometry=screen,
            )
        )
        self.assertFalse(
            taskbar_shell_rect_is_hidden_autohide(
                QRectF(-660.0, 360.0, 660.0, 40.0),
                appbar,
                auto_hide=False,
                screen_geometry=screen,
            )
        )

    def test_full_taskbar_offscreen_hidden_plan_uses_owning_monitor_intersection(self):
        screen = QRectF(-660.0, 0.0, 660.0, 360.0)
        appbar = QRectF(-660.0, 320.0, 660.0, 40.0)
        for y, hidden in ((320.0, False), (350.0, False), (358.0, False), (359.0, True), (360.0, True)):
            with self.subTest(y=y):
                plan = build_taskbar_autohide_hidden_plan(
                    taskbar_rect=appbar,
                    appbar_rect=appbar,
                    shell_rect=QRectF(-660.0, y, 660.0, 40.0),
                    detected_auto_hide=True,
                    screen_geometry=screen,
                )
                self.assertTrue(plan.auto_hide)
                self.assertEqual(plan.hidden, hidden)

    def test_taskbar_resolver_returns_base_after_full_shell_moves_out_of_monitor(self):
        screen = QRectF(-660.0, 0.0, 660.0, 360.0)
        appbar = QRectF(-660.0, 320.0, 660.0, 40.0)
        for y, expected in ((350.0, QRectF(-660.0, 350.0, 660.0, 40.0)), (360.0, appbar)):
            with self.subTest(y=y):
                self.assertEqual(
                    resolve_taskbar_rect_for_screen(
                        screen_geometry=screen,
                        available_geometry=screen,
                        appbar_auto_hide=True,
                        appbar_rect=appbar,
                        shell_rect=QRectF(-660.0, y, 660.0, 40.0),
                    ),
                    expected,
                )

    def test_taskbar_autohide_probe_prefers_taskbar_then_appbar_then_shell(self):
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        appbar = QRectF(0.0, 1032.0, 1920.0, 48.0)
        shell = QRectF(0.0, 1078.0, 1920.0, 2.0)

        self.assertEqual(select_taskbar_autohide_probe_rect(taskbar, appbar, shell), taskbar)
        self.assertEqual(select_taskbar_autohide_probe_rect(QRectF(), appbar, shell), appbar)
        self.assertEqual(select_taskbar_autohide_probe_rect(QRectF(), QRectF(), shell), shell)

    def test_taskbar_autohide_reveal_edge_rect_follows_taskbar_edge(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)

        bottom = taskbar_autohide_reveal_edge_rect(QRectF(0.0, 1040.0, 1920.0, 40.0), screen)
        top = taskbar_autohide_reveal_edge_rect(QRectF(0.0, 0.0, 1920.0, 40.0), screen)
        left = taskbar_autohide_reveal_edge_rect(QRectF(0.0, 0.0, 48.0, 1080.0), screen)
        right = taskbar_autohide_reveal_edge_rect(QRectF(1872.0, 0.0, 48.0, 1080.0), screen)

        self.assertAlmostEqual(bottom.bottom(), screen.bottom() + 1.0)
        self.assertEqual(top.top(), screen.top())
        self.assertEqual(left.left(), screen.left())
        self.assertAlmostEqual(right.right(), screen.right() + 1.0)

    def test_taskbar_autohide_cursor_reveals_only_on_edge_and_not_during_drag(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)

        self.assertTrue(taskbar_autohide_cursor_reveals(taskbar, screen, QPointF(960.0, 1078.0)))
        self.assertFalse(taskbar_autohide_cursor_reveals(taskbar, screen, QPointF(960.0, 1040.0)))
        self.assertFalse(
            taskbar_autohide_cursor_reveals(
                taskbar,
                screen,
                QPointF(960.0, 1078.0),
                drag_active=True,
            )
        )
        self.assertFalse(taskbar_autohide_cursor_reveals(taskbar, screen, QPointF(960.0, 1100.0)))

    def test_taskbar_autohide_cursor_in_taskbar_area_allows_small_padding(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)

        self.assertTrue(taskbar_autohide_cursor_in_taskbar_area(taskbar, screen, QPointF(960.0, 1037.0)))
        self.assertTrue(taskbar_autohide_cursor_in_taskbar_area(taskbar, screen, QPointF(960.0, 1079.0)))
        self.assertFalse(taskbar_autohide_cursor_in_taskbar_area(taskbar, screen, QPointF(960.0, 1028.0)))
        self.assertFalse(taskbar_autohide_cursor_in_taskbar_area(taskbar, screen, QPointF(960.0, 1100.0)))

    def test_taskbar_autohide_interaction_plan_click_inside_holds_visible(self):
        plan = build_taskbar_autohide_interaction_plan(
            screen_changed=False,
            dragging=False,
            inside_taskbar=True,
            cursor_reveals=False,
            pressed=True,
            previous_mouse_pressed=False,
            interaction_visible=False,
            click_hold_visible=False,
            left_taskbar_at=0.0,
            now=10.0,
        )

        self.assertTrue(plan.keeps_visible)
        self.assertTrue(plan.interaction_visible)
        self.assertTrue(plan.click_hold_visible)
        self.assertTrue(plan.mouse_pressed)
        self.assertEqual(plan.left_taskbar_at, 0.0)

    def test_taskbar_autohide_interaction_plan_click_outside_clears_visibility(self):
        plan = build_taskbar_autohide_interaction_plan(
            screen_changed=False,
            dragging=False,
            inside_taskbar=False,
            cursor_reveals=False,
            pressed=True,
            previous_mouse_pressed=False,
            interaction_visible=True,
            click_hold_visible=True,
            left_taskbar_at=8.0,
            now=10.0,
        )

        self.assertFalse(plan.keeps_visible)
        self.assertFalse(plan.interaction_visible)
        self.assertFalse(plan.click_hold_visible)
        self.assertTrue(plan.mouse_pressed)
        self.assertEqual(plan.left_taskbar_at, 0.0)

    def test_taskbar_autohide_interaction_plan_reveal_and_leave_grace(self):
        revealed = build_taskbar_autohide_interaction_plan(
            screen_changed=False,
            dragging=False,
            inside_taskbar=False,
            cursor_reveals=True,
            pressed=False,
            previous_mouse_pressed=False,
            interaction_visible=False,
            click_hold_visible=False,
            left_taskbar_at=0.0,
            now=10.0,
        )
        leaving = build_taskbar_autohide_interaction_plan(
            screen_changed=False,
            dragging=False,
            inside_taskbar=False,
            cursor_reveals=False,
            pressed=False,
            previous_mouse_pressed=False,
            interaction_visible=True,
            click_hold_visible=False,
            left_taskbar_at=0.0,
            now=10.2,
        )
        expired = build_taskbar_autohide_interaction_plan(
            screen_changed=False,
            dragging=False,
            inside_taskbar=False,
            cursor_reveals=False,
            pressed=False,
            previous_mouse_pressed=False,
            interaction_visible=True,
            click_hold_visible=False,
            left_taskbar_at=10.2,
            now=10.8,
        )

        self.assertTrue(revealed.keeps_visible)
        self.assertTrue(revealed.interaction_visible)
        self.assertTrue(leaving.keeps_visible)
        self.assertEqual(leaving.left_taskbar_at, 10.2)
        self.assertFalse(expired.keeps_visible)
        self.assertFalse(expired.interaction_visible)

    def test_taskbar_autohide_interaction_plan_screen_change_resets_previous_state(self):
        plan = build_taskbar_autohide_interaction_plan(
            screen_changed=True,
            dragging=False,
            inside_taskbar=False,
            cursor_reveals=False,
            pressed=False,
            previous_mouse_pressed=True,
            interaction_visible=True,
            click_hold_visible=True,
            left_taskbar_at=4.0,
            now=10.0,
        )

        self.assertFalse(plan.keeps_visible)
        self.assertFalse(plan.interaction_visible)
        self.assertFalse(plan.click_hold_visible)
        self.assertFalse(plan.mouse_pressed)
        self.assertEqual(plan.left_taskbar_at, 0.0)

    def test_taskbar_autohide_hidden_plan_respects_mode_interaction_and_shell(self):
        appbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        hidden_shell = QRectF(0.0, 1078.0, 1920.0, 2.0)
        full_shell = QRectF(0.0, 1040.0, 1920.0, 40.0)

        disabled = build_taskbar_autohide_hidden_plan(
            taskbar_rect=appbar,
            appbar_rect=appbar,
            shell_rect=hidden_shell,
            taskbar_auto_hide_mode="disabled",
            detected_auto_hide=True,
        )
        held_visible = build_taskbar_autohide_hidden_plan(
            taskbar_rect=appbar,
            appbar_rect=appbar,
            shell_rect=hidden_shell,
            taskbar_auto_hide_mode="enabled",
            interaction_keeps_visible=True,
        )
        hidden = build_taskbar_autohide_hidden_plan(
            taskbar_rect=appbar,
            appbar_rect=appbar,
            shell_rect=hidden_shell,
            taskbar_auto_hide_mode="enabled",
        )
        visible_shell = build_taskbar_autohide_hidden_plan(
            taskbar_rect=appbar,
            appbar_rect=appbar,
            shell_rect=full_shell,
            taskbar_auto_hide_mode="auto",
            detected_auto_hide=True,
        )
        missing_shell = build_taskbar_autohide_hidden_plan(
            taskbar_rect=appbar,
            appbar_rect=appbar,
            shell_rect=None,
            taskbar_auto_hide_mode="enabled",
        )

        self.assertFalse(disabled.auto_hide)
        self.assertFalse(disabled.hidden)
        self.assertFalse(held_visible.hidden)
        self.assertTrue(hidden.hidden)
        self.assertFalse(visible_shell.hidden)
        self.assertTrue(missing_shell.hidden)

    def test_taskbar_current_visibility_follow_plan_handles_force_partial_and_hidden_shell(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        shell = QRectF(0.0, 1060.0, 1920.0, 20.0)

        forced = build_taskbar_current_visibility_follow_plan(
            taskbar,
            screen,
            auto_hide_enabled=True,
            force_visible=True,
            appbar_rect=taskbar,
            shell_rect=shell,
            hidden_autohide=True,
        )
        partial = build_taskbar_current_visibility_follow_plan(
            taskbar,
            screen,
            auto_hide_enabled=True,
            appbar_rect=taskbar,
            shell_rect=shell,
            hidden_autohide=False,
        )
        hidden = build_taskbar_current_visibility_follow_plan(
            taskbar,
            screen,
            auto_hide_enabled=True,
            appbar_rect=taskbar,
            shell_rect=QRectF(0.0, 1078.0, 1920.0, 2.0),
            hidden_autohide=True,
        )

        self.assertEqual(forced.follow_rect, taskbar)
        self.assertFalse(forced.hidden_autohide)
        self.assertEqual(partial.follow_rect, shell)
        self.assertFalse(partial.hidden_autohide)
        self.assertEqual(hidden.base_rect, taskbar)
        self.assertEqual(hidden.follow_rect, QRectF(0.0, 1080.0, 1920.0, 40.0))
        self.assertTrue(hidden.hidden_autohide)

    def test_taskbar_visibility_follow_plan_lifts_only_when_hidden_and_not_forced(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)

        visible = build_taskbar_visibility_follow_plan(
            taskbar,
            screen,
            hidden_autohide=False,
        )
        forced = build_taskbar_visibility_follow_plan(
            taskbar,
            screen,
            force_visible=True,
            hidden_autohide=True,
        )
        hidden = build_taskbar_visibility_follow_plan(
            taskbar,
            screen,
            hidden_autohide=True,
        )

        self.assertEqual(visible.follow_rect, taskbar)
        self.assertFalse(visible.hidden_autohide)
        self.assertEqual(forced.follow_rect, taskbar)
        self.assertFalse(forced.hidden_autohide)
        self.assertEqual(hidden.follow_rect, QRectF(0.0, 1080.0, 1920.0, 40.0))
        self.assertTrue(hidden.hidden_autohide)

    def test_resolve_taskbar_rect_uses_appbar_when_autohide_shell_is_hidden(self):
        rect = resolve_taskbar_rect_for_screen(
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            available_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            shell_rect=QRectF(0.0, 1078.0, 1920.0, 2.0),
            appbar_auto_hide=True,
            appbar_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            appbar_edge="bottom",
        )

        self.assertEqual(rect, QRectF(0.0, 1040.0, 1920.0, 40.0))

    def test_resolve_taskbar_rect_uses_appbar_when_autohide_shell_is_missing(self):
        rect = resolve_taskbar_rect_for_screen(
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            available_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            shell_rect=None,
            appbar_auto_hide=True,
            appbar_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            appbar_edge="bottom",
        )

        self.assertEqual(rect, QRectF(0.0, 1040.0, 1920.0, 40.0))

    def test_resolve_taskbar_rect_uses_available_geometry_when_shell_is_missing(self):
        rect = resolve_taskbar_rect_for_screen(
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            available_geometry=QRectF(0.0, 0.0, 1920.0, 1040.0),
            shell_rect=None,
            appbar_auto_hide=False,
            appbar_rect=QRectF(),
        )

        self.assertEqual(rect, QRectF(0.0, 1041.0, 1920.0, 40.0))

    def test_drag_reference_rect_selects_thickest_screen_candidate(self):
        selected = select_taskbar_drag_reference_rect(
            taskbar_rect=QRectF(),
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            previous_taskbar_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            fallback_rect=QRectF(0.0, 1032.0, 1920.0, 48.0),
        )

        self.assertEqual(selected, QRectF(0.0, 1032.0, 1920.0, 48.0))

    def test_drag_reference_rect_ignores_candidates_outside_screen(self):
        selected = select_taskbar_drag_reference_rect(
            taskbar_rect=QRectF(2500.0, 1040.0, 1920.0, 40.0),
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            previous_taskbar_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
        )

        self.assertEqual(selected, QRectF(0.0, 1040.0, 1920.0, 40.0))

    def test_drag_main_offset_for_press_uses_press_inside_main_rect(self):
        rect = QRectF(100.0, 50.0, 300.0, 80.0)

        self.assertEqual(
            drag_main_offset_for_press(QPointF(140.0, 70.0), rect),
            QPointF(40.0, 20.0),
        )

    def test_drag_main_offset_for_press_falls_back_to_center_for_special_or_outside_press(self):
        rect = QRectF(100.0, 50.0, 300.0, 80.0)
        center = QPointF(150.0, 40.0)

        self.assertEqual(drag_main_offset_for_press(QPointF(10.0, 10.0), rect), center)
        self.assertEqual(drag_main_offset_for_press(QPointF(140.0, 70.0), rect, "plugin-alert:due"), center)
        self.assertEqual(drag_main_offset_for_press(QPointF(140.0, 70.0), QRectF()), QPointF())

    def test_taskbar_selected_drop_zone_local_rect_translates_and_pads_zone(self):
        zone = QRectF(500.0, 1040.0, 220.0, 40.0)

        self.assertEqual(
            taskbar_selected_drop_zone_local_rect(zone, QPointF(400.0, 1000.0), unit=1.0),
            QRectF(92.0, 34.0, 236.0, 52.0),
        )
        self.assertTrue(taskbar_selected_drop_zone_local_rect(QRectF(0.0, 0.0, 1.0, 40.0)).isNull())

    def test_point_in_finder_frame_uses_mode_and_small_padding(self):
        frame = QRectF(100.0, 20.0, 300.0, 54.0)

        self.assertFalse(point_in_finder_frame(finder_mode=False, finder_hover_rect=frame, point=QPointF(120.0, 40.0)))
        self.assertTrue(point_in_finder_frame(finder_mode=True, finder_hover_rect=frame, point=QPointF(99.0, 40.0)))
        self.assertFalse(point_in_finder_frame(finder_mode=True, finder_hover_rect=frame, point=QPointF(96.0, 40.0)))

    def test_taskbar_compact_hit_test_key_prioritizes_handle_buttons_and_grab_zone(self):
        handle = QRectF(40.0, 10.0, 8.0, 38.0)
        buttons = {"music:pause": QRectF(120.0, 12.0, 24.0, 24.0)}
        grab = QRectF(90.0, 4.0, 220.0, 48.0)

        self.assertEqual(
            taskbar_compact_hit_test_key(
                point=QPointF(41.0, 20.0),
                compact_active=True,
                expanded=False,
                left_handle_rect=handle,
                left_handle_visible=True,
                music_button_rects=buttons,
                selected_drop_zone_local_rect=grab,
                main_drag_key="music",
            ),
            "taskbar:left-handle",
        )
        self.assertEqual(
            taskbar_compact_hit_test_key(
                point=QPointF(126.0, 20.0),
                compact_active=True,
                expanded=False,
                left_handle_rect=handle,
                left_handle_visible=True,
                music_button_rects=buttons,
                selected_drop_zone_local_rect=grab,
                main_drag_key="music",
            ),
            "music:pause",
        )
        self.assertEqual(
            taskbar_compact_hit_test_key(
                point=QPointF(240.0, 20.0),
                compact_active=True,
                expanded=True,
                left_handle_rect=handle,
                left_handle_visible=False,
                music_button_rects=buttons,
                selected_drop_zone_local_rect=grab,
                main_drag_key="plugin",
            ),
            "plugin",
        )
        self.assertIsNone(
            taskbar_compact_hit_test_key(
                point=QPointF(20.0, 20.0),
                compact_active=False,
                expanded=False,
                left_handle_rect=handle,
                left_handle_visible=True,
                music_button_rects=buttons,
                selected_drop_zone_local_rect=grab,
                main_drag_key="music",
            )
        )

    def test_taskbar_material_exclusion_rects_prefers_selected_slot_in_tasker(self):
        rects = taskbar_material_exclusion_rects(
            selected_drop_zone=QRectF(100.0, 1040.0, 220.0, 40.0),
            main_global_rect=QRectF(110.0, 1038.0, 300.0, 44.0),
            motion_global_rects=[QRectF(500.0, 1000.0, 140.0, 60.0)],
            taskbar_compact_active=True,
            unit=1.0,
        )

        self.assertEqual(rects, [
            QRectF(86.0, 1032.0, 248.0, 56.0),
            QRectF(490.0, 994.0, 160.0, 72.0),
        ])

    def test_taskbar_material_exclusion_rects_uses_main_when_no_authoritative_slot(self):
        rects = taskbar_material_exclusion_rects(
            selected_drop_zone=QRectF(),
            main_global_rect=QRectF(110.0, 1038.0, 300.0, 44.0),
            motion_global_rects=[],
            taskbar_compact_active=True,
            unit=1.0,
        )

        self.assertEqual(rects, [QRectF(92.0, 1028.0, 336.0, 64.0)])

    def test_taskbar_compact_height_from_rects_prefers_live_thick_candidate(self):
        height = taskbar_compact_height_from_rects(
            [
                QRectF(100.0, 1047.0, 420.0, 26.0),
                QRectF(0.0, 1032.0, 1920.0, 48.0),
                QRectF(0.0, 1078.0, 1920.0, 2.0),
            ],
            compact_min=40.0,
            min_candidate=12.0,
        )

        self.assertEqual(height, 48.0)
        self.assertEqual(taskbar_compact_height_from_rects([QRectF(0.0, 1078.0, 1920.0, 2.0)]), 0.0)

    def test_taskbar_height_from_rect_clamps_regular_but_not_compact_height(self):
        tall = QRectF(0.0, 1000.0, 1920.0, 96.0)

        self.assertEqual(taskbar_height_from_rect(tall, compact=True), 96.0)
        self.assertEqual(taskbar_height_from_rect(tall, compact=False), 72.0)
        self.assertEqual(taskbar_height_from_rect(QRectF(), compact=True), 48.0)

    def test_taskbar_corner_radius_and_synced_radius_follow_windows_style(self):
        rect = QRectF(0.0, 0.0, 18.0, 80.0)

        self.assertEqual(taskbar_corner_radius(44.0, "win10"), 0.0)
        self.assertGreater(taskbar_corner_radius(44.0, "win11"), 0.0)
        self.assertEqual(taskbar_synced_corner_radius(rect, taskbar_height=80.0, style="win11"), 9.0)
        self.assertEqual(taskbar_synced_corner_radius(QRectF(), taskbar_height=80.0, style="win11"), 0.0)

    def test_taskbar_compact_main_height_prefers_rect_then_fallback_then_current_height(self):
        self.assertEqual(
            taskbar_compact_main_height(QRectF(0.0, 1000.0, 1920.0, 52.0), fallback=40.0),
            52.0,
        )
        self.assertEqual(taskbar_compact_main_height(QRectF(), fallback=44.0), 44.0)
        self.assertEqual(taskbar_compact_main_height(QRectF(), current_taskbar_height=48.0), 48.0)
        self.assertEqual(taskbar_full_compact_height_for_rect(QRectF(), 36.0), 36.0)

    def test_taskbar_width_with_manual_clamps_only_in_compact_mode(self):
        self.assertEqual(taskbar_width_with_manual(420.0, compact_active=False, manual_width=900.0), 420.0)
        self.assertEqual(taskbar_width_with_manual(420.0, compact_active=True, manual_width=80.0), 120.0)
        self.assertEqual(taskbar_width_with_manual(420.0, compact_active=True, manual_width=900.0), 760.0)
        self.assertEqual(taskbar_width_with_manual(420.0, compact_active=True, manual_width="bad"), 420.0)

    def test_taskbar_compact_music_size_shrinks_to_cover_when_slot_is_crowded(self):
        width, height = taskbar_compact_music_size(
            taskbar_height=44.0,
            default_music_width=420.0,
            selected_zone=QRectF(0.0, 0.0, 58.0, 40.0),
            compact_active=True,
            manual_width=0.0,
        )

        self.assertEqual((width, height), (52.0, 44.0))
        manual_width, _manual_height = taskbar_compact_music_size(
            taskbar_height=44.0,
            default_music_width=420.0,
            selected_zone=QRectF(0.0, 0.0, 58.0, 40.0),
            compact_active=True,
            manual_width=500.0,
        )
        self.assertEqual(manual_width, 500.0)

    def test_taskbar_cover_only_music_active_detects_selected_slot_and_crowded_rect(self):
        self.assertFalse(
            taskbar_cover_only_music_active(
                compact_active=False,
                selected_zone=QRectF(0.0, 0.0, 58.0, 40.0),
            )
        )
        self.assertTrue(
            taskbar_cover_only_music_active(
                compact_active=True,
                selected_zone=QRectF(0.0, 0.0, 58.0, 40.0),
            )
        )
        self.assertTrue(
            taskbar_cover_only_music_active(
                compact_active=True,
                selected_zone=QRectF(),
                rect=QRectF(10.0, 10.0, 118.0, 34.0),
                default_music_width=420.0,
            )
        )

    def test_taskbar_anchor_main_size_uses_music_compact_and_non_music_taskbar_height(self):
        self.assertEqual(
            taskbar_anchor_main_size(
                "music",
                expanded=False,
                compact_music_size=(52.0, 44.0),
                taskbar_rect=QRectF(0.0, 1000.0, 1920.0, 44.0),
            ),
            (52.0, 44.0),
        )
        self.assertEqual(
            taskbar_anchor_main_size(
                "plugin",
                expanded=False,
                base_size=(420.0, 76.0),
                taskbar_rect=QRectF(0.0, 1000.0, 1920.0, 44.0),
            ),
            (420.0, 44.0),
        )
        self.assertEqual(
            taskbar_anchor_main_size(
                "plugin",
                expanded=True,
                base_size=(420.0, 76.0),
                taskbar_rect=QRectF(0.0, 1000.0, 1920.0, 44.0),
            ),
            (420.0, 76.0),
        )
        self.assertIsNone(taskbar_anchor_main_size("music", expanded=False))

    def test_empty_bar_rules_are_pure_location_layout_decisions(self):
        self.assertEqual(empty_bar_orientation("tasker"), "vertical")
        self.assertEqual(empty_bar_orientation("free"), "horizontal")
        self.assertEqual(empty_bar_orientation(""), "horizontal")
        self.assertEqual(empty_bar_size("tasker"), (7.0, 44.0))
        self.assertEqual(empty_bar_size("free"), (122.0, 12.0))
        self.assertEqual(empty_bar_size(orientation="vertical", unit=2.0), (14.0, 88.0))

    def test_compact_music_target_width_uses_measured_content_facts(self):
        self.assertEqual(
            compact_music_target_width_from_content(
                content_width=100.0,
                title_width=900.0,
                artist_width=100.0,
                lyric_width=700.0,
                current_dual_width=0.0,
                instrumental=False,
                compact_lyric_cover_enabled=False,
            ),
            610.0,
        )
        self.assertEqual(
            compact_music_target_width_from_content(
                content_width=100.0,
                title_width=200.0,
                artist_width=100.0,
                lyric_width=100.0,
                current_dual_width=520.0,
                instrumental=False,
                compact_lyric_cover_enabled=True,
            ),
            626.0,
        )
        self.assertEqual(
            compact_music_target_width_from_content(
                content_width=100.0,
                title_width=450.0,
                artist_width=300.0,
                lyric_width=80.0,
                current_dual_width=0.0,
                instrumental=True,
                compact_lyric_cover_enabled=False,
            ),
            491.0,
        )
        self.assertEqual(
            compact_music_target_width_from_content(
                content_width=20.0,
                title_width=20.0,
                artist_width=20.0,
                lyric_width=20.0,
                current_dual_width=0.0,
                instrumental=False,
                compact_lyric_cover_enabled=False,
            ),
            430.0,
        )

    def test_stack_target_width_selects_already_measured_widths(self):
        self.assertEqual(
            stack_target_width(
                "music",
                music_width=444.0,
                empty_text_width=123.0,
                empty_time_width=99.0,
                default_stack_width=560.0,
            ),
            444.0,
        )
        self.assertEqual(
            stack_target_width(
                "empty_text",
                music_width=444.0,
                empty_text_width=123.0,
                empty_time_width=99.0,
                default_stack_width=560.0,
            ),
            123.0,
        )
        self.assertEqual(
            stack_target_width(
                "time",
                music_width=444.0,
                empty_text_width=123.0,
                empty_time_width=99.0,
                default_stack_width=560.0,
            ),
            99.0,
        )
        self.assertEqual(
            stack_target_width(
                "plugin",
                music_width=444.0,
                empty_text_width=123.0,
                empty_time_width=99.0,
                default_stack_width=560.0,
                unit=1.5,
            ),
            840.0,
        )

    def test_plugin_focus_weights_interpolate_expanded_focus(self):
        self.assertEqual(
            plugin_focus_weights(
                3,
                expanded=False,
                from_index=0,
                target_index=2,
                progress=0.35,
            ),
            [1.0, 1.0, 1.0],
        )
        self.assertEqual(
            plugin_focus_weights(
                3,
                expanded=True,
                from_index=0,
                target_index=2,
                progress=0.25,
            ),
            [0.75, 0.0, 0.25],
        )
        self.assertEqual(
            plugin_focus_weights(
                3,
                expanded=True,
                from_index=9,
                target_index=9,
                progress=0.25,
            ),
            [0.0, 0.0, 1.0],
        )
        self.assertEqual(plugin_focus_weights(0, expanded=True, from_index=0, target_index=1, progress=0.5), [])

    def test_plugin_slot_widths_keep_unfocused_cells_readable(self):
        self.assertEqual(
            plugin_slot_widths(
                3,
                420.0,
                expanded=False,
                from_index=0,
                target_index=2,
                progress=1.0,
            ),
            [140.0, 140.0, 140.0],
        )
        self.assertEqual(
            plugin_slot_widths(
                3,
                420.0,
                expanded=True,
                from_index=0,
                target_index=1,
                progress=1.0,
                focus_width_cap=220.0,
            ),
            [126.0, 168.0, 126.0],
        )
        self.assertEqual(
            plugin_slot_widths(
                3,
                420.0,
                expanded=True,
                from_index=0,
                target_index=1,
                progress=0.5,
                focus_width_cap=220.0,
            ),
            [147.0, 147.0, 126.0],
        )
        self.assertEqual(
            plugin_slot_widths(
                2,
                900.0,
                expanded=True,
                from_index=1,
                target_index=1,
                progress=1.0,
                focus_width_cap=220.0,
            ),
            [450.0, 450.0],
        )

    def test_plugin_item_rects_splits_widths_and_pins_last_edge(self):
        cells = plugin_item_rects(QRectF(10.0, 20.0, 421.0, 48.0), [126.0, 168.0, 126.0])

        self.assertEqual([index for index, _rect in cells], [0, 1, 2])
        self.assertEqual(cells[0][1], QRectF(10.0, 20.0, 126.0, 48.0))
        self.assertEqual(cells[1][1], QRectF(136.0, 20.0, 168.0, 48.0))
        self.assertEqual(cells[2][1], QRectF(304.0, 20.0, 127.0, 48.0))
        self.assertEqual(plugin_item_rects(QRectF(0.0, 0.0, 100.0, 20.0), []), [])

    def test_plugin_active_focus_tracks_animation_thresholds(self):
        self.assertEqual(
            plugin_active_focus(
                3,
                expanded=False,
                current_index=9,
                from_index=0,
                target_index=2,
                progress=0.8,
            ),
            2,
        )
        self.assertEqual(
            plugin_active_focus(
                3,
                expanded=True,
                current_index=1,
                from_index=0,
                target_index=2,
                progress=0.49,
            ),
            0,
        )
        self.assertEqual(
            plugin_active_focus(
                3,
                expanded=True,
                current_index=1,
                from_index=0,
                target_index=2,
                progress=0.5,
            ),
            2,
        )
        self.assertEqual(
            plugin_active_focus(
                0,
                expanded=True,
                current_index=1,
                from_index=0,
                target_index=2,
                progress=1.0,
            ),
            0,
        )

    def test_plugin_focus_index_for_x_hits_cells_or_nearest(self):
        cells = plugin_item_rects(QRectF(10.0, 20.0, 421.0, 48.0), [126.0, 168.0, 126.0])

        self.assertEqual(plugin_focus_index_for_x(cells, 20.0), 0)
        self.assertEqual(plugin_focus_index_for_x(cells, 150.0), 1)
        self.assertEqual(plugin_focus_index_for_x(cells, 420.0), 2)
        self.assertEqual(plugin_focus_index_for_x(cells, -200.0), 0)
        self.assertEqual(plugin_focus_index_for_x(cells, 900.0), 2)
        self.assertIsNone(plugin_focus_index_for_x([], 100.0))

    def test_plugin_strip_layout_combines_focus_weights_widths_and_cells(self):
        layout = plugin_strip_layout(
            QRectF(10.0, 20.0, 421.0, 48.0),
            3,
            expanded=True,
            current_index=0,
            from_index=0,
            target_index=1,
            progress=0.5,
            focus_width_cap=220.0,
        )

        self.assertEqual(layout.active_focus, 1)
        self.assertEqual(layout.weights, [0.5, 0.5, 0.0])
        self.assertEqual(len(layout.widths), 3)
        self.assertAlmostEqual(layout.widths[0], 147.35)
        self.assertAlmostEqual(layout.widths[1], 147.35)
        self.assertAlmostEqual(layout.widths[2], 126.3)
        self.assertEqual([index for index, _rect in layout.cells], [0, 1, 2])
        self.assertAlmostEqual(layout.cells[0][1].left(), 10.0)
        self.assertAlmostEqual(layout.cells[0][1].width(), 147.35)
        self.assertAlmostEqual(layout.cells[2][1].left(), 304.7)
        self.assertAlmostEqual(layout.cells[2][1].right(), 431.0)

        empty = plugin_strip_layout(
            QRectF(10.0, 20.0, 421.0, 48.0),
            0,
            expanded=True,
            current_index=0,
            from_index=0,
            target_index=1,
            progress=0.5,
        )
        self.assertEqual(empty.active_focus, 0)
        self.assertEqual(empty.weights, [])
        self.assertEqual(empty.widths, [])
        self.assertEqual(empty.cells, [])

    def test_main_size_for_kind_keeps_music_size_priority_in_pure_rules(self):
        self.assertEqual(
            main_size_for_kind(
                "music",
                MainSizeFacts(
                    stack_width=420.0,
                    expanded=True,
                    music_expanded_layout_style="standard",
                ),
            ),
            (420.0, 176.0),
        )
        self.assertEqual(
            main_size_for_kind(
                "music",
                MainSizeFacts(
                    stack_width=420.0,
                    expanded=True,
                    music_expanded_layout_style="dual_panel",
                ),
            ),
            (420.0, 212.0),
        )
        self.assertEqual(
            main_size_for_kind(
                "music",
                MainSizeFacts(
                    stack_width=420.0,
                    taskbar_compact=True,
                    taskbar_compact_music_size=(52.0, 44.0),
                    stick_notch_active=True,
                    stick_notch_music_height=72.0,
                    top_notch_active=True,
                    top_notch_music_height=68.0,
                    compact_music_content_height=60.0,
                ),
            ),
            (52.0, 44.0),
        )
        self.assertEqual(
            main_size_for_kind(
                "music",
                MainSizeFacts(
                    stack_width=420.0,
                    stick_notch_active=True,
                    stick_notch_music_height=72.0,
                    top_notch_active=True,
                    top_notch_music_height=68.0,
                    compact_music_content_height=60.0,
                ),
            ),
            (420.0, 72.0),
        )
        self.assertEqual(
            main_size_for_kind(
                "music",
                MainSizeFacts(
                    stack_width=420.0,
                    top_notch_active=True,
                    top_notch_music_height=68.0,
                    compact_music_content_height=60.0,
                ),
            ),
            (420.0, 68.0),
        )
        self.assertEqual(
            main_size_for_kind(
                "music",
                MainSizeFacts(stack_width=420.0, compact_music_content_height=60.0),
            ),
            (420.0, 60.0),
        )

    def test_main_size_for_kind_uses_measured_non_music_facts(self):
        self.assertEqual(
            main_size_for_kind(
                "plugin",
                MainSizeFacts(stack_width=500.0, expanded=True, enabled_plugin_slot_count=3),
            ),
            (500.0, 48.0),
        )
        self.assertEqual(
            main_size_for_kind(
                "plugin",
                MainSizeFacts(
                    stack_width=500.0,
                    taskbar_compact=True,
                    enabled_plugin_slot_count=2,
                    taskbar_compact_main_height=44.0,
                    manual_width=360.0,
                ),
            ),
            (360.0, 44.0),
        )
        self.assertEqual(
            main_size_for_kind(
                "message",
                MainSizeFacts(stack_width=620.0, expanded=False),
            ),
            (430.0, 48.0),
        )
        self.assertEqual(
            main_size_for_kind(
                "message",
                MainSizeFacts(
                    stack_width=620.0,
                    expanded=True,
                    taskbar_compact=True,
                    taskbar_compact_main_height=46.0,
                    manual_width=700.0,
                ),
            ),
            (700.0, 46.0),
        )
        self.assertEqual(
            main_size_for_kind(
                "empty_text",
                MainSizeFacts(
                    stack_width=420.0,
                    empty_text_size=(188.0, 40.0),
                ),
            ),
            (188.0, 40.0),
        )
        self.assertEqual(
            main_size_for_kind(
                "time",
                MainSizeFacts(
                    stack_width=420.0,
                    taskbar_compact=True,
                    taskbar_compact_main_height=45.0,
                    empty_time_size=(126.0, 40.0),
                ),
            ),
            (126.0, 45.0),
        )
        self.assertEqual(
            main_size_for_kind(
                "thin_bar",
                MainSizeFacts(stack_width=420.0, empty_bar_size=(122.0, 12.0)),
            ),
            (122.0, 12.0),
        )

    def test_expanded_stack_height_combines_plugin_messages_and_popups(self):
        self.assertEqual(
            expanded_stack_height(
                "music",
                44.0,
                expanded=True,
                include_popups=False,
                plugin_enabled=True,
                plugin_panel_visible=True,
                plugin_panel_height=180.0,
                message_count=2,
                folded_message_count=0,
                popup_count=0,
                unit=1.0,
            ),
            44.0 + 10.0 + 48.0 + 10.0 + 180.0 + 10.0 + 48.0 * 2 + 10.0,
        )
        self.assertEqual(
            expanded_stack_height(
                "message",
                44.0,
                expanded=True,
                include_popups=False,
                plugin_enabled=True,
                plugin_panel_visible=True,
                plugin_panel_height=180.0,
                message_count=8,
                folded_message_count=3,
                popup_count=0,
                unit=1.0,
            ),
            44.0 + 10.0 + 48.0 * 3 + 20.0,
        )
        self.assertEqual(
            expanded_stack_height(
                "music",
                44.0,
                expanded=False,
                include_popups=True,
                plugin_enabled=True,
                plugin_panel_visible=True,
                plugin_panel_height=180.0,
                message_count=3,
                folded_message_count=0,
                popup_count=2,
                unit=1.0,
            ),
            100.0,
        )
        self.assertEqual(
            expanded_stack_height(
                "message",
                44.0,
                expanded=False,
                include_popups=True,
                plugin_enabled=True,
                plugin_panel_visible=True,
                plugin_panel_height=180.0,
                message_count=3,
                folded_message_count=0,
                popup_count=2,
                unit=1.0,
            ),
            44.0,
        )

    def test_time_plugin_panel_rules_are_pure_layout_decisions(self):
        self.assertEqual(time_visible_module_for_width(260.0, "alarm", breakpoint=560.0), "alarm")
        self.assertEqual(time_visible_module_for_width(560.0, "alarm", breakpoint=560.0), "all")
        self.assertEqual(time_visible_module_for_width(260.0, "unknown", breakpoint=560.0), "countdown")

        self.assertEqual(plugin_panel_height(260.0, time_mode="dashboard", breakpoint=560.0), 156.0)
        self.assertEqual(plugin_panel_height(620.0, time_mode="dashboard", breakpoint=560.0), 236.0)
        self.assertEqual(plugin_panel_height(None, time_mode="dashboard", last_width=380.0, breakpoint=560.0), 156.0)
        self.assertEqual(plugin_panel_height(420.0, time_mode="alarm_editor", breakpoint=560.0), 190.0)
        self.assertEqual(
            plugin_panel_shell_rect(QRectF(20.0, 60.0, 420.0, 188.0), QRectF(10.0, 20.0, 500.0, 48.0)),
            QRectF(10.0, 20.0, 500.0, 228.0),
        )
        self.assertEqual(
            plugin_panel_shell_rect(QRectF(20.0, 60.0, 420.0, 188.0), QRectF()),
            QRectF(20.0, 60.0, 420.0, 188.0),
        )

        panel = plugin_panel_layout(
            QRectF(0.0, 0.0, 420.0, 188.0),
            visible_module="countdown",
            breakpoint=560.0,
        )
        self.assertGreaterEqual(panel["nav"].height(), 30.0)
        self.assertGreater(panel["body"].top(), panel["nav"].bottom())
        self.assertGreater(panel["action"].top(), panel["body"].top())
        self.assertGreater(panel["reminders"].top(), panel["nav"].bottom())
        self.assertLessEqual(panel["action"].right(), 420.0)
        self.assertEqual(panel["time_layout_mode"], "single")
        self.assertGreater(panel["time_countdown"].width(), 48.0)
        self.assertEqual(panel["time_alarm"].width(), 0.0)

        alarm_panel = plugin_panel_layout(
            QRectF(0.0, 0.0, 420.0, 188.0),
            visible_module="alarm",
            breakpoint=560.0,
        )
        self.assertEqual(alarm_panel["time_layout_mode"], "single")
        self.assertGreater(alarm_panel["time_alarm"].width(), 48.0)
        self.assertEqual(alarm_panel["time_countdown"].width(), 0.0)

        narrow = plugin_panel_time_layout(
            QRectF(0.0, 0.0, 380.0, 156.0),
            visible_module="countdown",
            breakpoint=560.0,
            stretch_breakpoint=960.0,
        )
        self.assertEqual(narrow["time_layout_mode"], "double")
        self.assertEqual(narrow["time_visible_module"], "countdown")
        self.assertGreater(narrow["time_clock"].width(), 120.0)
        self.assertGreater(narrow["time_countdown"].width(), 120.0)
        self.assertEqual(narrow["time_alarm"].width(), 0.0)
        self.assertGreater(narrow["time_pager"].width(), 0.0)

        wide = plugin_panel_time_layout(
            QRectF(0.0, 0.0, 560.0, 236.0),
            visible_module="alarm",
            breakpoint=560.0,
            stretch_breakpoint=960.0,
        )
        self.assertEqual(wide["time_layout_mode"], "triple")
        self.assertEqual(wide["time_visible_module"], "all")
        self.assertGreater(wide["time_alarm"].width(), wide["time_countdown"].width())

        stretch = plugin_panel_time_layout(
            QRectF(0.0, 0.0, 980.0, 236.0),
            visible_module="countdown",
            breakpoint=560.0,
            stretch_breakpoint=960.0,
        )
        self.assertEqual(stretch["time_layout_mode"], "stretch")
        self.assertGreater(stretch["time_countdown"].width(), 260.0)
        self.assertGreater(stretch["time_alarm"].width(), 260.0)

    def test_taskbar_message_style_degrades_block_when_crowded_or_many(self):
        self.assertEqual(
            taskbar_message_effective_style(
                "block",
                2,
                selected_zone=QRectF(0.0, 0.0, 240.0, 44.0),
            ),
            "block",
        )
        self.assertEqual(
            taskbar_message_effective_style(
                "block",
                4,
                selected_zone=QRectF(0.0, 0.0, 240.0, 44.0),
            ),
            "strip",
        )
        self.assertEqual(
            taskbar_message_effective_style(
                "block",
                2,
                selected_zone=QRectF(0.0, 0.0, 120.0, 44.0),
            ),
            "strip",
        )
        self.assertEqual(taskbar_message_effective_style("rail", 2), "strip")

    def test_taskbar_message_rect_and_message_dot_rect_are_layout_rules(self):
        main = QRectF(100.0, 10.0, 220.0, 44.0)
        compact = taskbar_message_rect(
            main,
            1,
            3,
            preferred_style="block",
            selected_zone=QRectF(0.0, 0.0, 260.0, 44.0),
            compact_active=True,
            notification_outer_gap=9.0,
        )
        self.assertEqual(compact, QRectF(367.0, 15.0, 34.0, 34.0))

        left_side = taskbar_message_rect(
            main,
            1,
            3,
            preferred_style="strip",
            compact_active=False,
            notification_outer_gap=9.0,
        )
        self.assertEqual(left_side, QRectF(91.0, 16.16, 6.0, 31.68))

        below = message_dot_rect(main, 1, 3, direction=1, taskbar_compact_active=False)
        above = message_dot_rect(main, 1, 3, direction=-1, taskbar_compact_active=False)
        self.assertEqual(below, QRectF(206.5, 55.0, 7.0, 7.0))
        self.assertEqual(above, QRectF(206.5, 2.0, 7.0, 7.0))

    def test_plugin_alert_side_geometry_helpers_choose_and_clamp_rows(self):
        main = QRectF(100.0, 10.0, 220.0, 44.0)

        self.assertEqual(plugin_alert_x_for_side(main, 150.0, 10.0, "right"), 330.0)
        self.assertEqual(plugin_alert_x_for_side(main, 150.0, 10.0, "left"), -60.0)
        self.assertEqual(plugin_alert_side_space(main, 10.0, "right", 8.0, 500.0), 170.0)
        self.assertEqual(plugin_alert_side_space(main, 10.0, "left", 8.0, 500.0), 82.0)

        self.assertFalse(plugin_alert_row_fits(main, 150.0, 10.0, 7.0, 2, "right", 8.0, 500.0))
        self.assertEqual(plugin_alert_side_for_row(main, 150.0, 10.0, 7.0, 2, "left", 8.0, 500.0), "right")
        self.assertEqual(plugin_alert_width_for_row(main, 150.0, 10.0, 7.0, 2, "right", 8.0, 500.0), 42.0)
        self.assertEqual(plugin_alert_x_for_slot(main, 42.0, 10.0, 7.0, "right", 1), 379.0)

        self.assertTrue(rect_x_fits_screen(330.0, 150.0, 8.0, 500.0))
        self.assertFalse(rect_x_fits_screen(-60.0, 150.0, 8.0, 500.0))
        self.assertEqual(clamp_rect_x_to_screen(-60.0, 150.0, 8.0, 500.0), 8.0)
        self.assertTrue(plugin_alert_uses_ring(QRectF(0.0, 0.0, 42.0, 42.0)))
        self.assertFalse(plugin_alert_uses_ring(QRectF(0.0, 0.0, 150.0, 42.0)))

    def test_plugin_alert_pressure_and_taskbar_compact_rect_are_layout_rules(self):
        self.assertEqual(
            plugin_alert_main_rect_screen_pressure(
                QRectF(360.0, 0.0, 180.0, 44.0),
                target_width=220.0,
                screen_left=8.0,
                screen_right=500.0,
            ),
            "right",
        )
        self.assertEqual(
            plugin_alert_main_rect_screen_pressure(
                QRectF(-40.0, 0.0, 180.0, 44.0),
                target_width=220.0,
                screen_left=8.0,
                screen_right=500.0,
            ),
            "left",
        )
        self.assertEqual(
            plugin_alert_main_rect_screen_pressure(
                QRectF(100.0, 0.0, 220.0, 44.0),
                target_width=220.0,
                screen_left=8.0,
                screen_right=500.0,
            ),
            "",
        )
        self.assertEqual(plugin_alert_circle_size(32.0), 34.0)

        block = taskbar_compact_plugin_alert_rect(
            QRectF(100.0, 10.0, 220.0, 44.0),
            slot=1,
            total=2,
            preferred_style="block",
            notification_outer_gap=9.0,
        )
        self.assertAlmostEqual(block.left(), 372.6)
        self.assertAlmostEqual(block.top(), 12.2)
        self.assertAlmostEqual(block.width(), 39.6)
        self.assertAlmostEqual(block.height(), 39.6)

        rail = taskbar_compact_plugin_alert_rect(
            QRectF(100.0, 10.0, 220.0, 44.0),
            slot=0,
            total=2,
            preferred_style="rail",
            notification_outer_gap=9.0,
        )
        self.assertEqual(rail, QRectF(329.0, 16.16, 7.0, 31.68))

    def test_plugin_alert_rect_places_single_alerts_inside_available_side(self):
        main = QRectF(100.0, 10.0, 220.0, 44.0)
        self.assertEqual(
            plugin_alert_rect(
                main,
                slot=0,
                total=1,
                width=150.0,
                row_width=150.0,
                height=42.0,
                gap=10.0,
                row_gap=7.0,
                screen_left=8.0,
                screen_right=500.0,
                window_height=200.0,
            ),
            QRectF(330.0, 11.0, 150.0, 42.0),
        )

        crowded_right = QRectF(260.0, 10.0, 80.0, 44.0)
        self.assertEqual(
            plugin_alert_rect(
                crowded_right,
                slot=0,
                total=1,
                width=150.0,
                row_width=150.0,
                height=42.0,
                gap=10.0,
                row_gap=7.0,
                screen_left=8.0,
                screen_right=420.0,
                window_height=200.0,
            ),
            QRectF(100.0, 11.0, 150.0, 42.0),
        )

        pressured = QRectF(360.0, 10.0, 180.0, 44.0)
        self.assertEqual(
            plugin_alert_rect(
                pressured,
                slot=0,
                total=1,
                width=150.0,
                row_width=150.0,
                height=42.0,
                gap=10.0,
                row_gap=7.0,
                screen_left=8.0,
                screen_right=500.0,
                window_height=200.0,
                pressure="right",
            ),
            QRectF(308.0, 11.0, 42.0, 42.0),
        )

    def test_plugin_alert_rect_splits_or_compacts_two_alert_rows(self):
        main = QRectF(210.0, 10.0, 80.0, 44.0)
        first = plugin_alert_rect(
            main,
            slot=0,
            total=2,
            width=150.0,
            row_width=150.0,
            height=42.0,
            gap=10.0,
            row_gap=7.0,
            screen_left=8.0,
            screen_right=500.0,
            window_height=200.0,
            same_time_row=False,
        )
        second = plugin_alert_rect(
            main,
            slot=1,
            total=2,
            width=150.0,
            row_width=150.0,
            height=42.0,
            gap=10.0,
            row_gap=7.0,
            screen_left=8.0,
            screen_right=500.0,
            window_height=200.0,
            same_time_row=False,
        )
        self.assertEqual(first, QRectF(300.0, 11.0, 150.0, 42.0))
        self.assertEqual(second, QRectF(50.0, 11.0, 150.0, 42.0))

        same_time_second = plugin_alert_rect(
            main,
            slot=1,
            total=2,
            width=150.0,
            row_width=150.0,
            height=42.0,
            gap=10.0,
            row_gap=7.0,
            screen_left=8.0,
            screen_right=500.0,
            window_height=200.0,
            same_time_row=True,
        )
        self.assertEqual(same_time_second, QRectF(349.0, 11.0, 42.0, 42.0))

        pressured_second = plugin_alert_rect(
            QRectF(365.0, 80.0, 210.0, 48.0),
            slot=1,
            total=2,
            width=168.0,
            row_width=168.0,
            height=42.0,
            gap=10.0,
            row_gap=7.0,
            screen_left=8.0,
            screen_right=471.0,
            window_height=260.0,
            pressure="right",
            same_time_row=False,
        )
        self.assertEqual(pressured_second, QRectF(264.0, 83.0, 42.0, 42.0))

    def test_available_taskbar_slot_rects_subtracts_occupied_regions(self):
        area = QRectF(0.0, 1040.0, 1000.0, 40.0)
        slots = available_taskbar_slot_rects(
            area,
            [
                QRectF(120.0, 1040.0, 100.0, 40.0),
                QRectF(420.0, 1040.0, 260.0, 40.0),
            ],
        )

        self.assertEqual(slots, [
            QRectF(0.0, 1040.0, 120.0, 40.0),
            QRectF(220.0, 1040.0, 200.0, 40.0),
            QRectF(680.0, 1040.0, 320.0, 40.0),
        ])

    def test_available_taskbar_slot_rects_handles_vertical_regions(self):
        area = QRectF(0.0, 0.0, 48.0, 800.0)
        slots = available_taskbar_slot_rects(area, [QRectF(0.0, 220.0, 48.0, 160.0)])

        self.assertEqual(slots, [
            QRectF(0.0, 0.0, 48.0, 220.0),
            QRectF(0.0, 380.0, 48.0, 420.0),
        ])

    def test_side_taskbar_slot_rects_splits_empty_area_around_center(self):
        area = QRectF(0.0, 1040.0, 1920.0, 40.0)
        slots = side_taskbar_slot_rects(area, [])

        self.assertEqual(len(slots), 2)
        self.assertLess(slots[0].center().x(), area.center().x())
        self.assertGreater(slots[1].center().x(), area.center().x())
        self.assertFalse(any(abs(slot.center().x() - area.center().x()) < 80.0 for slot in slots))

    def test_side_taskbar_slot_rects_chooses_largest_slot_on_each_side(self):
        area = QRectF(0.0, 1040.0, 1920.0, 40.0)
        slots = side_taskbar_slot_rects(
            area,
            [
                QRectF(0.0, 1040.0, 160.0, 40.0),
                QRectF(220.0, 1040.0, 420.0, 40.0),
                QRectF(1300.0, 1040.0, 500.0, 40.0),
            ],
        )

        self.assertEqual(slots, [
            QRectF(220.0, 1040.0, 420.0, 40.0),
            QRectF(1300.0, 1040.0, 500.0, 40.0),
        ])

    def test_taskbar_drop_zone_rect_stays_inside_taskbar_bounds(self):
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        zone = taskbar_drop_zone_rect(taskbar, desired_width=360.0)

        self.assertGreaterEqual(zone.top(), taskbar.top())
        self.assertLessEqual(zone.bottom(), taskbar.bottom())
        self.assertAlmostEqual(zone.center().x(), taskbar.center().x(), delta=0.1)

    def test_build_taskbar_drop_zones_uses_left_and_right_side_slots(self):
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        zones = build_taskbar_drop_zones(
            taskbar,
            taskbar,
            [
                QRectF(0.0, 1040.0, 170.0, 40.0),
                QRectF(640.0, 1040.0, 710.0, 40.0),
                QRectF(1810.0, 1040.0, 110.0, 40.0),
            ],
            desired_width=360.0,
        )

        self.assertEqual(len(zones), 2)
        self.assertLess(zones[0].center().x(), taskbar.center().x())
        self.assertGreater(zones[1].center().x(), taskbar.center().x())

    def test_select_taskbar_drop_zone_uses_drag_position(self):
        left = QRectF(120.0, 1044.0, 360.0, 32.0)
        right = QRectF(1440.0, 1044.0, 360.0, 32.0)

        self.assertEqual(select_taskbar_drop_zone([left, right], QPoint(1580, 1060)), right)
        self.assertEqual(select_taskbar_drop_zone([left, right], None), left)

    def test_taskbar_attach_hot_zone_is_centered_on_screen(self):
        zone = taskbar_attach_hot_zone_rect(QRectF(0.0, 0.0, 1920.0, 1080.0))

        self.assertFalse(zone.isNull())
        self.assertTrue(taskbar_attach_hot_zone_contains(zone, QPointF(960.0, 540.0)))
        self.assertLess(zone.width(), 720.0)
        self.assertLess(zone.height(), 220.0)

    def test_taskbar_attach_hot_zone_host_rect_pads_zone(self):
        zone = QRectF(760.0, 460.0, 400.0, 160.0)
        host = taskbar_attach_hot_zone_host_rect(zone)

        self.assertLess(host.left(), zone.left())
        self.assertGreater(host.right(), zone.right())
        self.assertLess(host.top(), zone.top())
        self.assertGreater(host.bottom(), zone.bottom())

    def test_taskbar_attach_hot_zone_accepts_main_rect_center(self):
        zone = taskbar_attach_hot_zone_rect(QRectF(0.0, 0.0, 1920.0, 1080.0))
        outside_main = QRectF(100.0, 100.0, 420.0, 48.0)
        inside_main = QRectF(750.0, 516.0, 420.0, 48.0)

        self.assertFalse(taskbar_attach_hot_zone_accepts_rect(zone, outside_main))
        self.assertTrue(taskbar_attach_hot_zone_accepts_rect(zone, inside_main))

    def test_taskbar_left_handle_rect_sits_outside_main_rect(self):
        handle = taskbar_left_handle_rect(QRectF(200.0, 100.0, 300.0, 50.0), unit=1.0)

        self.assertAlmostEqual(handle.left(), 189.0)
        self.assertAlmostEqual(handle.top(), 109.5)
        self.assertAlmostEqual(handle.width(), 4.0)
        self.assertAlmostEqual(handle.height(), 31.0)

    def test_taskbar_left_handle_frame_rect_overlaps_main_rect(self):
        frame = taskbar_left_handle_frame_rect(QRectF(200.0, 100.0, 300.0, 50.0), unit=1.0)

        self.assertAlmostEqual(frame.left(), 183.4)
        self.assertAlmostEqual(frame.top(), 100.0)
        self.assertAlmostEqual(frame.width(), 25.6)
        self.assertAlmostEqual(frame.height(), 50.0)

    def test_taskbar_left_handle_visible_respects_tasker_state_hover_and_drag(self):
        self.assertFalse(
            taskbar_left_handle_visible(
                taskbar_compact_active=False,
                expanded=False,
                now=10.0,
                visible_until=20.0,
                dragging=False,
                drag_press_key="",
                hovered_key="music",
            )
        )
        self.assertFalse(
            taskbar_left_handle_visible(
                taskbar_compact_active=True,
                expanded=True,
                now=10.0,
                visible_until=20.0,
                dragging=True,
                drag_press_key="taskbar:left-handle",
                hovered_key="",
            )
        )
        self.assertTrue(
            taskbar_left_handle_visible(
                taskbar_compact_active=True,
                expanded=False,
                now=10.0,
                visible_until=10.1,
                dragging=False,
                drag_press_key="",
                hovered_key="",
            )
        )
        self.assertTrue(
            taskbar_left_handle_visible(
                taskbar_compact_active=True,
                expanded=False,
                now=10.0,
                visible_until=0.0,
                dragging=True,
                drag_press_key="taskbar:left-handle",
                hovered_key="",
            )
        )
        self.assertTrue(
            taskbar_left_handle_visible(
                taskbar_compact_active=True,
                expanded=False,
                now=10.0,
                visible_until=0.0,
                dragging=False,
                drag_press_key="",
                hovered_key="message:wechat",
            )
        )

    def test_taskbar_left_handle_visibility_progress_fades_after_hover_deadline(self):
        self.assertAlmostEqual(
            taskbar_left_handle_visibility_progress(
                taskbar_compact_active=True,
                expanded=False,
                now=9.2,
                visible_until=10.0,
                dragging=False,
                drag_press_key="",
                hovered_key="",
            ),
            0.8 / 0.9,
        )
        self.assertEqual(
            taskbar_left_handle_visibility_progress(
                taskbar_compact_active=True,
                expanded=False,
                now=10.1,
                visible_until=10.0,
                dragging=False,
                drag_press_key="",
                hovered_key="",
            ),
            0.0,
        )
        self.assertEqual(
            taskbar_left_handle_visibility_progress(
                taskbar_compact_active=True,
                expanded=False,
                now=20.0,
                visible_until=0.0,
                dragging=False,
                drag_press_key="",
                hovered_key="plugin",
            ),
            1.0,
        )

    def test_extend_taskbar_left_handle_visibility_never_shortens_existing_deadline(self):
        self.assertEqual(extend_taskbar_left_handle_visible_until(20.0, 10.0), 20.0)
        self.assertEqual(extend_taskbar_left_handle_visible_until(0.0, 10.0), 13.0)

    def test_taskbar_manual_width_for_left_edge_clamps_to_track(self):
        main_rect = QRectF(320.0, 1044.0, 260.0, 34.0)
        track = QRectF(100.0, 1040.0, 820.0, 40.0)

        expanded_width = taskbar_manual_width_for_left_edge(main_rect, track, 240.0)
        clamped_width = taskbar_manual_width_for_left_edge(main_rect, track, -200.0)

        self.assertGreater(expanded_width, main_rect.width())
        self.assertLessEqual(clamped_width, track.width())
        self.assertGreaterEqual(clamped_width, 120.0)

    def test_taskbar_drag_track_band_wraps_thin_autohide_shell(self):
        shell = QRectF(0.0, 1078.0, 1920.0, 2.0)
        band = taskbar_drag_track_band_rect(shell)

        self.assertLess(band.top(), shell.top())
        self.assertGreater(band.bottom(), shell.bottom())
        self.assertTrue(taskbar_drag_should_stay_on_track(shell, QPoint(920, 1056)))

    def test_taskbar_drag_detach_requires_away_motion(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)
        bottom_taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)

        self.assertTrue(taskbar_drag_should_detach_to_free(bottom_taskbar, screen, QPoint(8, -70)))
        self.assertFalse(taskbar_drag_should_detach_to_free(bottom_taskbar, screen, QPoint(8, 70)))
        self.assertFalse(taskbar_drag_should_detach_to_free(bottom_taskbar, screen, QPoint(160, -70)))

    def test_taskbar_drag_detach_ignores_left_handle_drag(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)
        bottom_taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)

        self.assertFalse(
            taskbar_drag_should_detach_to_free(
                bottom_taskbar,
                screen,
                QPoint(0, -100),
                left_handle_drag=True,
            )
        )

    def test_build_taskbar_drag_target_reaches_bottom_corners(self):
        target = build_taskbar_drag_target(
            global_pos=QPoint(1916, 1060),
            taskbar_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            track_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            window_width=940.0,
            main_kind="music",
            stack_width=420.0,
            main_width=240.0,
            main_height=34.0,
            main_top=10.0,
            direction=-1,
            drag_press_main_offset=QPointF(120.0, 17.0),
        )

        self.assertIsNotNone(target)
        self.assertAlmostEqual(target.position_ratio, 1.0)
        self.assertGreaterEqual(target.zone_rect.right(), 1919.0)
        self.assertLessEqual(target.window_pos.y() + target.main_top + target.main_height, 1080.0)

    def test_build_taskbar_drag_target_preserves_autohide_fallback_height(self):
        target = build_taskbar_drag_target(
            global_pos=QPoint(920, 1056),
            taskbar_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            track_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            window_width=940.0,
            main_kind="music",
            stack_width=420.0,
            main_width=420.0,
            main_height=40.0,
            main_top=600.0,
            direction=-1,
            drag_press_main_offset=QPointF(210.0, 17.0),
        )

        self.assertIsNotNone(target)
        self.assertEqual(target.zone_rect.height(), 40.0)
        self.assertEqual(target.direction, -1)

    def test_build_taskbar_left_resize_target_clamps_zone_to_track(self):
        target = build_taskbar_left_resize_target(
            global_pos=QPoint(40, 1060),
            main_rect=QRectF(320.0, 1044.0, 260.0, 34.0),
            taskbar_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            track_rect=QRectF(100.0, 1040.0, 820.0, 40.0),
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            window_width=940.0,
            main_kind="music",
            stack_width=420.0,
            main_width=240.0,
            main_height=34.0,
            main_top=10.0,
            direction=-1,
        )

        self.assertIsNotNone(target)
        self.assertGreater(target.manual_width, 240.0)
        self.assertAlmostEqual(target.zone_rect.left(), 100.0)
        self.assertLessEqual(target.zone_rect.right(), 920.0)
        self.assertAlmostEqual(target.main_width, target.zone_rect.width())

    def test_bottom_release_threshold_uses_release_point_and_taskbar_height(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        anchor = QRectF(710.0, 930.0, 420.0, 48.0)

        self.assertFalse(bottom_release_reaches_taskbar_threshold(anchor, None, screen, taskbar))
        self.assertTrue(bottom_release_reaches_taskbar_threshold(anchor, QPoint(920, 1078), screen, taskbar))

    def test_bottom_release_routes_autohide_to_stick_and_visible_taskbar_to_tasker(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)
        anchor = QRectF(710.0, 1050.0, 420.0, 48.0)

        self.assertTrue(
            should_snap_above_taskbar_for_release(
                anchor,
                QPoint(920, 1078),
                screen,
                taskbar,
                taskbar_allowed=True,
                auto_hide_enabled=True,
            )
        )
        self.assertFalse(
            should_enter_taskbar_for_visible_taskbar_release(
                anchor,
                QPoint(920, 1078),
                screen,
                taskbar,
                taskbar_allowed=True,
                auto_hide_enabled=True,
            )
        )
        self.assertTrue(
            should_enter_taskbar_for_visible_taskbar_release(
                anchor,
                QPoint(920, 1078),
                screen,
                taskbar,
                taskbar_allowed=True,
                auto_hide_enabled=False,
            )
        )
        self.assertFalse(
            should_snap_above_taskbar_for_release(
                anchor,
                QPoint(920, 1078),
                screen,
                taskbar,
                taskbar_allowed=False,
                auto_hide_enabled=True,
            )
        )

    def test_tasker_release_detach_uses_compact_bottom_slop(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)
        taskbar = QRectF(0.0, 1040.0, 1920.0, 40.0)

        self.assertTrue(
            tasker_release_point_reaches_bottom_slop(
                QPoint(920, 1078),
                screen,
                taskbar,
                taskbar_compact_active=True,
            )
        )
        self.assertFalse(
            tasker_release_point_reaches_bottom_slop(
                QPoint(920, 1050),
                screen,
                taskbar,
                taskbar_compact_active=True,
            )
        )
        self.assertFalse(
            tasker_release_point_reaches_bottom_slop(
                QPoint(920, 1078),
                screen,
                taskbar,
                taskbar_compact_active=False,
            )
        )

    def test_top_notch_release_threshold_respects_finder_guards(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)
        anchor = QRectF(400.0, 80.0, 420.0, 48.0)

        self.assertTrue(
            should_snap_top_notch_for_release(
                anchor,
                QPoint(610, 2),
                screen,
                mydockfinder_available=False,
                finder_mode=False,
            )
        )
        self.assertFalse(
            should_snap_top_notch_for_release(
                anchor,
                QPoint(610, 2),
                screen,
                mydockfinder_available=True,
                finder_mode=False,
            )
        )
        self.assertFalse(
            should_snap_top_notch_for_release(
                anchor,
                QPoint(610, 200),
                screen,
                mydockfinder_available=False,
                finder_mode=False,
            )
        )

    def test_top_release_with_dock_without_finder_uses_screen_local_edge(self):
        screen = QRectF(-1920.0, 120.0, 1920.0, 1080.0)
        for dock, finder, expected in ((False, False, True), (True, False, True), (True, True, False)):
            with self.subTest(dock=dock, finder=finder):
                self.assertEqual(
                    should_snap_top_notch_for_release(
                        QRectF(-900.0, 200.0, 420.0, 48.0),
                        QPoint(-700, 122),
                        screen,
                        mydockfinder_available=dock,
                        finder_bar_available=finder,
                        finder_mode=False,
                    ),
                    expected,
                )

    def test_standalone_release_decision_prioritizes_top_notch(self):
        decision = decide_standalone_release_action(
            QRectF(710.0, 4.0, 420.0, 48.0),
            QPoint(920, 2),
            QRectF(0.0, 0.0, 1920.0, 1080.0),
            QRectF(0.0, 1040.0, 1920.0, 40.0),
            taskbar_allowed=True,
            auto_hide_enabled=False,
            mydockfinder_available=False,
            finder_mode=False,
            taskbar_compact_active=False,
        )

        self.assertEqual(decision.action, "top")

    def test_standalone_release_decision_routes_visible_taskbar_to_tasker(self):
        decision = decide_standalone_release_action(
            QRectF(710.0, 1018.0, 420.0, 48.0),
            QPoint(920, 1078),
            QRectF(0.0, 0.0, 1920.0, 1080.0),
            QRectF(0.0, 1040.0, 1920.0, 40.0),
            taskbar_allowed=True,
            auto_hide_enabled=False,
            mydockfinder_available=False,
            finder_mode=False,
            taskbar_compact_active=False,
        )

        self.assertEqual(decision.action, "tasker")

    def test_standalone_release_decision_routes_autohide_taskbar_to_tasker(self):
        decision = decide_standalone_release_action(
            QRectF(710.0, 1018.0, 420.0, 48.0),
            QPoint(920, 1078),
            QRectF(0.0, 0.0, 1920.0, 1080.0),
            QRectF(0.0, 1040.0, 1920.0, 40.0),
            taskbar_allowed=True,
            auto_hide_enabled=True,
            mydockfinder_available=False,
            finder_mode=False,
            taskbar_compact_active=False,
        )

        self.assertEqual(decision.action, "tasker")

    def test_standalone_release_with_dock_never_attaches_to_windows_taskbar(self):
        for finder in (False, True):
            for auto_hide in (False, True):
                with self.subTest(finder=finder, auto_hide=auto_hide):
                    decision = decide_standalone_release_action(
                        QRectF(710.0, 1018.0, 420.0, 48.0),
                        QPoint(920, 1078),
                        QRectF(0.0, 0.0, 1920.0, 1080.0),
                        QRectF(0.0, 1040.0, 1920.0, 40.0),
                        taskbar_allowed=True,
                        auto_hide_enabled=auto_hide,
                        mydockfinder_available=True,
                        finder_bar_available=finder,
                        finder_mode=False,
                        taskbar_compact_active=False,
                    )
                    self.assertEqual(decision.action, "free")

    def test_standalone_release_with_dock_without_finder_attaches_only_to_top(self):
        for finder, expected in ((False, "top"), (True, "free")):
            with self.subTest(finder=finder):
                decision = decide_standalone_release_action(
                    QRectF(710.0, 4.0, 420.0, 48.0),
                    QPoint(920, 2),
                    QRectF(0.0, 0.0, 1920.0, 1080.0),
                    QRectF(0.0, 1040.0, 1920.0, 40.0),
                    taskbar_allowed=False,
                    auto_hide_enabled=False,
                    mydockfinder_available=True,
                    finder_bar_available=finder,
                    finder_mode=False,
                    taskbar_compact_active=False,
                )
                self.assertEqual(decision.action, expected)

    def test_standalone_release_decision_returns_free_with_edge_notch_exit(self):
        decision = decide_standalone_release_action(
            QRectF(710.0, 520.0, 420.0, 48.0),
            QPoint(920, 640),
            QRectF(0.0, 0.0, 1920.0, 1080.0),
            QRectF(0.0, 1040.0, 1920.0, 40.0),
            taskbar_allowed=True,
            auto_hide_enabled=True,
            mydockfinder_available=False,
            finder_mode=False,
            taskbar_compact_active=False,
            current_location_mode="stick",
        )

        self.assertEqual(decision.action, "free")
        self.assertTrue(decision.exiting_edge_notch_mode)

    def test_standalone_release_decision_blocks_finder_and_taskbar_compact(self):
        common = dict(
            anchor_rect=QRectF(710.0, 1018.0, 420.0, 48.0),
            release_global_pos=QPoint(920, 1078),
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            taskbar_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            taskbar_allowed=True,
            auto_hide_enabled=False,
            mydockfinder_available=False,
        )

        finder = decide_standalone_release_action(**common, finder_mode=True, taskbar_compact_active=False)
        compact = decide_standalone_release_action(**common, finder_mode=False, taskbar_compact_active=True)

        self.assertEqual(finder.action, "none")
        self.assertEqual(compact.action, "none")

    def test_standalone_release_direction_respects_user_mode_and_auto_bounds(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)

        self.assertEqual(
            standalone_release_direction_for_anchor(
                QRectF(710.0, 1018.0, 420.0, 48.0),
                screen,
                expand_direction="down",
            ),
            1,
        )
        self.assertEqual(
            standalone_release_direction_for_anchor(
                QRectF(710.0, 100.0, 420.0, 48.0),
                screen,
                expand_direction="up",
            ),
            -1,
        )
        self.assertEqual(
            standalone_release_direction_for_anchor(
                QRectF(710.0, 1018.0, 420.0, 48.0),
                screen,
                expand_direction="auto",
                auto_expand_bounds=True,
                expanded=True,
                expanded_down_height=120.0,
            ),
            -1,
        )
        self.assertEqual(
            standalone_release_direction_for_anchor(
                QRectF(710.0, 100.0, 420.0, 48.0),
                screen,
                expand_direction="auto",
                auto_expand_bounds=True,
                expanded=True,
                expanded_down_height=120.0,
            ),
            1,
        )

    def test_standalone_drag_direction_plan_respects_fixed_modes_and_missing_screen(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)

        down = build_standalone_drag_direction_plan(
            expand_direction="down",
            auto_expand_bounds=True,
            global_pos=QPoint(100, 1000),
            screen_geometry=screen,
            dragging=False,
            drag_candidate=True,
            current_auto_direction=0,
            current_stack_direction=-1,
        )
        up = build_standalone_drag_direction_plan(
            expand_direction="up",
            auto_expand_bounds=True,
            global_pos=QPoint(100, 100),
            screen_geometry=screen,
            dragging=False,
            drag_candidate=True,
            current_auto_direction=0,
            current_stack_direction=1,
        )
        missing_screen = build_standalone_drag_direction_plan(
            expand_direction="auto",
            auto_expand_bounds=True,
            global_pos=QPoint(100, 100),
            screen_geometry=QRectF(),
            dragging=False,
            drag_candidate=True,
            current_auto_direction=-1,
            current_stack_direction=-1,
        )

        self.assertEqual(down.direction, 1)
        self.assertTrue(down.update_auto_direction)
        self.assertEqual(down.auto_direction, 1)
        self.assertEqual(up.direction, -1)
        self.assertEqual(up.auto_direction, -1)
        self.assertEqual(missing_screen.direction, 1)
        self.assertFalse(missing_screen.update_auto_direction)

    def test_standalone_drag_direction_plan_latches_and_uses_hysteresis(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)

        latched_while_dragging = build_standalone_drag_direction_plan(
            expand_direction="auto",
            auto_expand_bounds=True,
            global_pos=QPoint(100, 1000),
            screen_geometry=screen,
            dragging=True,
            drag_candidate=True,
            current_auto_direction=1,
            current_stack_direction=1,
        )
        still_latched = build_standalone_drag_direction_plan(
            expand_direction="auto",
            auto_expand_bounds=True,
            global_pos=QPoint(100, 735),
            screen_geometry=screen,
            dragging=False,
            drag_candidate=True,
            current_auto_direction=1,
            current_stack_direction=1,
            unit=1.0,
        )
        switched_down = build_standalone_drag_direction_plan(
            expand_direction="auto",
            auto_expand_bounds=True,
            global_pos=QPoint(100, 830),
            screen_geometry=screen,
            dragging=False,
            drag_candidate=True,
            current_auto_direction=1,
            current_stack_direction=1,
            unit=1.0,
        )

        self.assertEqual(latched_while_dragging.direction, 1)
        self.assertEqual(still_latched.direction, 1)
        self.assertEqual(switched_down.direction, -1)
        self.assertEqual(switched_down.auto_direction, -1)

    def test_standalone_stack_direction_plan_respects_finder_and_fixed_modes(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)

        self.assertEqual(
            build_standalone_stack_direction_plan(
                finder_mode=True,
                taskbar_compact_active=False,
                taskbar_rect=QRectF(),
                current_stack_direction=-1,
                expand_direction="up",
                auto_expand_bounds=True,
                screen_geometry=screen,
                global_main_center_y=1000.0,
            ),
            1,
        )
        self.assertEqual(
            build_standalone_stack_direction_plan(
                finder_mode=False,
                taskbar_compact_active=False,
                taskbar_rect=QRectF(),
                current_stack_direction=1,
                expand_direction="up",
                auto_expand_bounds=True,
                screen_geometry=screen,
                global_main_center_y=100.0,
            ),
            -1,
        )

    def test_standalone_stack_direction_plan_uses_taskbar_geometry_before_auto_bounds(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)

        bottom = build_standalone_stack_direction_plan(
            finder_mode=False,
            taskbar_compact_active=True,
            taskbar_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            current_stack_direction=1,
            expand_direction="down",
            auto_expand_bounds=True,
            screen_geometry=screen,
            global_main_center_y=100.0,
        )
        missing_taskbar = build_standalone_stack_direction_plan(
            finder_mode=False,
            taskbar_compact_active=True,
            taskbar_rect=QRectF(),
            current_stack_direction=-1,
            expand_direction="down",
            auto_expand_bounds=True,
            screen_geometry=screen,
            global_main_center_y=100.0,
        )

        self.assertEqual(bottom, -1)
        self.assertEqual(missing_taskbar, -1)

    def test_standalone_stack_direction_plan_auto_bounds_uses_main_position_and_expanded_height(self):
        screen = QRectF(0.0, 0.0, 1920.0, 1080.0)

        low_main = build_standalone_stack_direction_plan(
            finder_mode=False,
            taskbar_compact_active=False,
            taskbar_rect=QRectF(),
            current_stack_direction=1,
            expand_direction="auto",
            auto_expand_bounds=True,
            screen_geometry=screen,
            global_main_center_y=820.0,
            expanded=False,
        )
        high_main = build_standalone_stack_direction_plan(
            finder_mode=False,
            taskbar_compact_active=False,
            taskbar_rect=QRectF(),
            current_stack_direction=1,
            expand_direction="auto",
            auto_expand_bounds=True,
            screen_geometry=screen,
            global_main_center_y=220.0,
            expanded=True,
            global_main_top=990.0,
            expanded_down_height=100.0,
            bottom_margin=14.0,
        )
        enough_room = build_standalone_stack_direction_plan(
            finder_mode=False,
            taskbar_compact_active=False,
            taskbar_rect=QRectF(),
            current_stack_direction=1,
            expand_direction="auto",
            auto_expand_bounds=True,
            screen_geometry=screen,
            global_main_center_y=220.0,
            expanded=True,
            global_main_top=100.0,
            expanded_down_height=100.0,
            bottom_margin=14.0,
        )

        self.assertEqual(low_main, -1)
        self.assertEqual(high_main, -1)
        self.assertEqual(enough_room, 1)

    def test_standalone_stack_width_for_limit_uses_target_when_limit_allows(self):
        self.assertEqual(standalone_stack_width_for_limit(420.0, 900.0), 420.0)

    def test_standalone_stack_width_for_limit_clamps_to_width_limit(self):
        self.assertEqual(standalone_stack_width_for_limit(420.0, 360.0), 360.0)

    def test_standalone_stack_width_for_limit_keeps_half_width_floor(self):
        self.assertEqual(standalone_stack_width_for_limit(420.0, 120.0), 210.0)

    def test_standalone_stack_width_for_limit_accepts_explicit_minimum(self):
        self.assertEqual(standalone_stack_width_for_limit(420.0, 180.0, min_width=120.0), 180.0)
        self.assertEqual(standalone_stack_width_for_limit(420.0, 80.0, min_width=120.0), 120.0)

    def test_standalone_width_limit_for_global_center_uses_nearest_screen_edge(self):
        self.assertEqual(
            standalone_width_limit_for_global_center(
                240.0,
                QRectF(0.0, 0.0, 1000.0, 800.0),
                500.0,
                min_width=120.0,
                side_margin=28.0,
            ),
            452.0,
        )

    def test_standalone_width_limit_for_global_center_keeps_minimum_when_over_edge(self):
        self.assertEqual(
            standalone_width_limit_for_global_center(
                10.0,
                QRectF(0.0, 0.0, 1000.0, 800.0),
                500.0,
                min_width=120.0,
                side_margin=28.0,
            ),
            120.0,
        )

    def test_standalone_width_limit_for_global_center_falls_back_without_screen(self):
        self.assertEqual(
            standalone_width_limit_for_global_center(
                240.0,
                None,
                500.0,
                min_width=120.0,
                side_margin=28.0,
            ),
            500.0,
        )

    def test_standalone_stack_center_x_centers_free_mode(self):
        self.assertEqual(
            standalone_stack_center_x(
                finder_mode=False,
                window_width=940.0,
                content_width=420.0,
                center_ratio=0.9,
            ),
            470.0,
        )

    def test_standalone_stack_center_x_clamps_finder_ratio_inside_margins(self):
        self.assertEqual(
            standalone_stack_center_x(
                finder_mode=True,
                window_width=940.0,
                content_width=420.0,
                center_ratio=0.01,
                unit=1.0,
            ),
            234.0,
        )
        self.assertEqual(
            standalone_stack_center_x(
                finder_mode=True,
                window_width=940.0,
                content_width=420.0,
                center_ratio=0.99,
                unit=1.0,
            ),
            706.0,
        )

    def test_standalone_stack_center_x_recenters_overwide_finder_content(self):
        self.assertEqual(
            standalone_stack_center_x(
                finder_mode=True,
                window_width=940.0,
                content_width=900.0,
                center_ratio=0.8,
                unit=1.0,
            ),
            470.0,
        )

    def test_standalone_main_top_for_direction_uses_finder_top_for_downward_stack(self):
        self.assertEqual(
            standalone_main_top_for_direction(
                finder_stack_top_y=72.0,
                window_height=900.0,
                main_height=80.0,
                direction=1,
                finder_mode=False,
            ),
            72.0,
        )

    def test_standalone_main_top_for_direction_flips_upward_stack_to_bottom(self):
        self.assertEqual(
            standalone_main_top_for_direction(
                finder_stack_top_y=72.0,
                window_height=900.0,
                main_height=80.0,
                direction=-1,
                finder_mode=False,
            ),
            748.0,
        )

    def test_standalone_main_top_for_direction_keeps_finder_downward(self):
        self.assertEqual(
            standalone_main_top_for_direction(
                finder_stack_top_y=8.0,
                window_height=900.0,
                main_height=80.0,
                direction=-1,
                finder_mode=True,
            ),
            8.0,
        )

    def test_standalone_main_top_for_direction_lifts_thin_bar(self):
        self.assertEqual(
            standalone_main_top_for_direction(
                finder_stack_top_y=72.0,
                window_height=900.0,
                main_height=12.0,
                direction=1,
                finder_mode=False,
                thin_bar=True,
                thin_bar_top_lift=4.0,
            ),
            68.0,
        )

    def test_stack_rect_for_direction_places_next_rect_below_cursor(self):
        rect, next_edge = stack_rect_for_direction(
            center_x=300.0,
            cursor_edge=120.0,
            width=200.0,
            height=48.0,
            direction=1,
            gap=10.0,
        )

        self.assertEqual(rect, QRectF(200.0, 120.0, 200.0, 48.0))
        self.assertEqual(next_edge, 178.0)

    def test_stack_rect_for_direction_places_next_rect_above_cursor(self):
        rect, next_edge = stack_rect_for_direction(
            center_x=300.0,
            cursor_edge=120.0,
            width=200.0,
            height=48.0,
            direction=-1,
            gap=10.0,
        )

        self.assertEqual(rect, QRectF(200.0, 72.0, 200.0, 48.0))
        self.assertEqual(next_edge, 62.0)

    def test_canonical_position_preset_maps_invalid_values_to_last(self):
        self.assertEqual(canonical_position_preset("custom"), "last")
        self.assertEqual(canonical_position_preset(""), "last")
        self.assertEqual(canonical_position_preset("unknown"), "last")
        self.assertEqual(canonical_position_preset("top_right"), "top_right")

    def test_build_standalone_position_preset_target_centers_top_preset(self):
        target = build_standalone_position_preset_target(
            preset="top_center",
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            window_width=940.0,
            main_kind="music",
            stack_width=420.0,
            main_width=300.0,
            main_height=62.0,
            main_top=20.0,
            margin=8.0,
        )

        self.assertIsNotNone(target)
        self.assertEqual(target.direction, 1)
        self.assertAlmostEqual(target.window_pos.x() + target.main_left + target.main_width / 2.0, 960.0)
        self.assertAlmostEqual(target.window_pos.y() + target.main_top, 8.0)

    def test_build_standalone_position_preset_target_uses_bottom_direction(self):
        target = build_standalone_position_preset_target(
            preset="bottom_right",
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            window_width=940.0,
            main_kind="music",
            stack_width=420.0,
            main_width=300.0,
            main_height=62.0,
            main_top=560.0,
            margin=8.0,
        )

        self.assertIsNotNone(target)
        self.assertEqual(target.direction, -1)
        self.assertAlmostEqual(target.window_pos.x() + target.main_left + target.main_width, 1912.0)
        self.assertAlmostEqual(target.window_pos.y() + target.main_top + target.main_height, 1072.0)

    def test_build_standalone_position_preset_target_rejects_last_and_missing_screen(self):
        common = dict(
            window_width=940.0,
            main_kind="music",
            stack_width=420.0,
            main_width=300.0,
            main_height=62.0,
            main_top=20.0,
        )

        self.assertIsNone(
            build_standalone_position_preset_target(
                preset="last",
                screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
                **common,
            )
        )
        self.assertIsNone(
            build_standalone_position_preset_target(
                preset="top_left",
                screen_geometry=QRectF(),
                **common,
            )
        )

    def test_build_standalone_top_notch_target_clamps_to_screen_top(self):
        target = build_standalone_top_notch_target(
            anchor_rect=QRectF(400.0, 6.0, 420.0, 48.0),
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            window_width=940.0,
            main_kind="music",
            stack_width=420.0,
            main_width=420.0,
            main_height=48.0,
            main_top=72.0,
        )

        self.assertIsNotNone(target)
        self.assertEqual(target.direction, 1)
        self.assertAlmostEqual(target.window_pos.y() + target.main_top, 0.0, delta=1.0)

    def test_build_standalone_top_notch_target_clamps_window_inside_screen_width(self):
        target = build_standalone_top_notch_target(
            anchor_rect=QRectF(-200.0, 6.0, 420.0, 48.0),
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            window_width=940.0,
            main_kind="music",
            stack_width=420.0,
            main_width=420.0,
            main_height=48.0,
            main_top=72.0,
        )

        self.assertIsNotNone(target)
        self.assertEqual(target.window_pos.x(), 0)

    def test_build_standalone_free_release_target_preserves_anchor_with_up_direction(self):
        target = build_standalone_free_release_target(
            anchor_rect=QRectF(710.0, 1018.0, 420.0, 48.0),
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            window_width=940.0,
            main_kind="music",
            stack_width=420.0,
            main_width=420.0,
            main_height=48.0,
            main_top=600.0,
            direction=-1,
        )

        self.assertIsNotNone(target)
        self.assertEqual(target.direction, -1)
        self.assertAlmostEqual(target.window_pos.y() + target.main_top, 1018.0, delta=1.0)

    def test_build_standalone_free_release_target_clamps_bottom_edge(self):
        target = build_standalone_free_release_target(
            anchor_rect=QRectF(710.0, 1070.0, 420.0, 48.0),
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            window_width=940.0,
            main_kind="music",
            stack_width=420.0,
            main_width=420.0,
            main_height=48.0,
            main_top=600.0,
            direction=-1,
            margin=6.0,
        )

        self.assertIsNotNone(target)
        self.assertLessEqual(target.window_pos.y() + target.main_top + target.main_height, 1080.0 - 6.0)

    def test_build_taskbar_follow_target_computes_hidden_autohide_window(self):
        target = build_taskbar_follow_target(
            screen=object(),
            visible_taskbar=QRectF(0.0, 1040.0, 1920.0, 40.0),
            follow_taskbar=QRectF(0.0, 1080.0, 1920.0, 40.0),
            track_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            window_width=940.0,
            main_kind="music",
            stack_width=420.0,
            main_width=300.0,
            main_height=34.0,
            main_top=10.0,
            direction=-1,
            auto_hide_follow=True,
            hidden_autohide=True,
            bottom_extension=40.0,
        )

        self.assertTrue(target.hidden_autohide)
        self.assertFalse(target.partial_shell_follow)
        self.assertGreater(target.window_pos.y() + target.main_top + target.main_height, 1080.0)
        self.assertEqual(target.zone_rect, QRectF(810.0, 1083.0, 300.0, 34.0))

    def test_build_taskbar_follow_target_applies_visible_autohide_lift(self):
        target = build_taskbar_follow_target(
            screen=object(),
            visible_taskbar=QRectF(0.0, 1040.0, 1920.0, 40.0),
            follow_taskbar=QRectF(0.0, 1040.0, 1920.0, 40.0),
            track_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            window_width=940.0,
            main_kind="music",
            stack_width=420.0,
            main_width=300.0,
            main_height=34.0,
            main_top=10.0,
            direction=-1,
            auto_hide_follow=True,
            hidden_autohide=False,
            visible_follow_lift=4.0,
        )

        self.assertFalse(target.hidden_autohide)
        self.assertFalse(target.partial_shell_follow)
        self.assertEqual(target.zone_rect.center().y(), 1056.0)
        self.assertEqual(target.window_pos.y() + 10.0, 1039.0)

    def test_taskbar_follow_target_keeps_owner_offset_across_hidden_boundary(self):
        screen = QRectF(-660.0, -360.0, 660.0, 360.0)
        appbar = QRectF(-660.0, -40.0, 660.0, 40.0)
        previous = None
        for offset in range(41):
            shell = appbar.translated(0.0, float(offset))
            hidden = offset >= 39
            target = build_taskbar_follow_target(
                screen=object(),
                visible_taskbar=appbar,
                follow_taskbar=shell,
                track_rect=appbar,
                screen_geometry=screen,
                window_width=500.0,
                main_kind="music",
                stack_width=300.0,
                main_width=300.0,
                main_height=30.0,
                main_top=10.0,
                direction=-1,
                auto_hide_follow=True,
                hidden_autohide=hidden,
                visible_follow_lift=4.0,
                bottom_extension=40.0,
            )
            with self.subTest(offset=offset, hidden=hidden):
                self.assertEqual(target.zone_rect.center().y() - shell.center().y(), -4.0)
                if previous is not None:
                    self.assertEqual(target.window_pos.y() - previous.window_pos.y(), 1)
                    self.assertEqual(target.zone_rect.top() - previous.zone_rect.top(), 1.0)
            previous = target

    def test_build_taskbar_follow_target_marks_partial_shell_follow(self):
        target = build_taskbar_follow_target(
            screen=object(),
            visible_taskbar=QRectF(0.0, 1040.0, 1920.0, 40.0),
            follow_taskbar=QRectF(0.0, 1060.0, 1920.0, 20.0),
            track_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            window_width=940.0,
            main_kind="music",
            stack_width=420.0,
            main_width=300.0,
            main_height=34.0,
            main_top=10.0,
            direction=-1,
            auto_hide_follow=True,
            hidden_autohide=False,
        )

        self.assertTrue(target.partial_shell_follow)
        self.assertFalse(target.hidden_autohide)

    def test_taskbar_follow_refresh_plan_rejects_invalid_target(self):
        target = build_taskbar_follow_target(
            screen=object(),
            visible_taskbar=QRectF(),
            follow_taskbar=QRectF(),
            track_rect=QRectF(),
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            window_width=940.0,
            main_kind="music",
            stack_width=420.0,
            main_width=300.0,
            main_height=34.0,
            main_top=10.0,
            direction=-1,
        )

        plan = build_taskbar_follow_refresh_plan(
            target,
            stable_geometry=False,
            previous_hidden=False,
            force_visible=False,
            taskbar_autohide_hidden=False,
            material_elapsed=1.0,
            material_interval=0.5,
        )

        self.assertFalse(plan.valid)
        self.assertFalse(plan.animate_follow)
        self.assertFalse(plan.refresh_material)

    def test_taskbar_follow_refresh_plan_follows_binary_owner_without_second_easing(self):
        target = build_taskbar_follow_target(
            screen=object(),
            visible_taskbar=QRectF(0.0, 1040.0, 1920.0, 40.0),
            follow_taskbar=QRectF(0.0, 1080.0, 1920.0, 40.0),
            track_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            window_width=940.0,
            main_kind="music",
            stack_width=420.0,
            main_width=300.0,
            main_height=34.0,
            main_top=10.0,
            direction=-1,
            auto_hide_follow=True,
            hidden_autohide=True,
            bottom_extension=40.0,
        )

        plan = build_taskbar_follow_refresh_plan(
            target,
            stable_geometry=True,
            previous_hidden=False,
            force_visible=False,
            taskbar_autohide_hidden=True,
            material_elapsed=1.0,
            material_interval=0.5,
        )

        self.assertTrue(plan.valid)
        self.assertFalse(plan.animate_follow)
        self.assertFalse(plan.refresh_material)

    def test_taskbar_follow_refresh_plan_skips_partial_shell_animation_and_throttles_material(self):
        target = build_taskbar_follow_target(
            screen=object(),
            visible_taskbar=QRectF(0.0, 1040.0, 1920.0, 40.0),
            follow_taskbar=QRectF(0.0, 1060.0, 1920.0, 20.0),
            track_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            window_width=940.0,
            main_kind="music",
            stack_width=420.0,
            main_width=300.0,
            main_height=34.0,
            main_top=10.0,
            direction=-1,
            auto_hide_follow=True,
            hidden_autohide=False,
        )

        throttled = build_taskbar_follow_refresh_plan(
            target,
            stable_geometry=True,
            previous_hidden=True,
            force_visible=False,
            taskbar_autohide_hidden=False,
            material_elapsed=0.1,
            material_interval=0.5,
        )
        due = build_taskbar_follow_refresh_plan(
            target,
            stable_geometry=True,
            previous_hidden=True,
            force_visible=False,
            taskbar_autohide_hidden=False,
            material_elapsed=0.6,
            material_interval=0.5,
        )
        geometry_changed = build_taskbar_follow_refresh_plan(
            target,
            stable_geometry=False,
            previous_hidden=True,
            force_visible=False,
            taskbar_autohide_hidden=False,
            material_elapsed=0.1,
            material_interval=0.5,
        )

        self.assertTrue(target.partial_shell_follow)
        self.assertFalse(throttled.animate_follow)
        self.assertFalse(throttled.refresh_material)
        self.assertTrue(due.refresh_material)
        self.assertTrue(geometry_changed.refresh_material)

    def test_taskbar_follow_rect_key_rounds_geometry_and_collapses_null(self):
        self.assertEqual(taskbar_follow_rect_key(QRectF()), (0, 0, 0, 0))
        self.assertEqual(
            taskbar_follow_rect_key(QRectF(0.4, 1040.49, 1919.6, 39.51)),
            (0, 1040, 1920, 40),
        )

    def test_taskbar_follow_geometry_key_includes_window_mode_and_target_geometry(self):
        screen = object()
        target = TaskbarFollowTarget(
            screen=screen,
            visible_taskbar=QRectF(0.0, 1040.0, 1920.0, 40.0),
            follow_taskbar=QRectF(0.0, 1080.0, 1920.0, 40.0),
            track_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            zone_rect=QRectF(710.0, 1044.0, 300.0, 32.0),
            window_pos=QPoint(490, 430),
            main_kind="music",
            stack_width=420.0,
            main_width=300.0,
            main_height=34.0,
            main_top=10.2,
            direction=-1,
            position_ratio=0.5,
            hidden_autohide=True,
            auto_hide_follow=True,
            partial_shell_follow=False,
        )

        collapsed = taskbar_follow_geometry_key(
            target,
            window_width=940.4,
            window_height=82.49,
            location_mode="tasker",
            expanded=False,
            main_kind="music",
        )
        expanded = taskbar_follow_geometry_key(
            target,
            window_width=940.4,
            window_height=82.49,
            location_mode="tasker",
            expanded=True,
            main_kind="music",
        )

        self.assertEqual(collapsed[0], id(screen))
        self.assertEqual(collapsed[1], (0, 1080, 1920, 40))
        self.assertEqual(collapsed[2], (0, 1040, 1920, 40))
        self.assertEqual(collapsed[3], (710, 1044, 300, 32))
        self.assertIn(940, collapsed)
        self.assertIn(82, collapsed)
        self.assertIn("tasker", collapsed)
        self.assertNotEqual(collapsed, expanded)

    def test_taskbar_follow_refresh_due_respects_interval_floor(self):
        self.assertTrue(taskbar_follow_refresh_due(now=10.0, last_refresh_at=0.0, interval=0.28))
        self.assertFalse(taskbar_follow_refresh_due(now=10.005, last_refresh_at=10.0, interval=0.0))
        self.assertFalse(taskbar_follow_refresh_due(now=10.007, last_refresh_at=10.0, interval=0.0))
        self.assertTrue(taskbar_follow_refresh_due(now=10.0081, last_refresh_at=10.0, interval=0.0))
        self.assertTrue(taskbar_follow_refresh_due(now=10.0 + 1.0 / 120.0, last_refresh_at=10.0, interval=0.008))
        self.assertFalse(taskbar_follow_refresh_due(now=10.010, last_refresh_at=10.0, interval=0.020))
        self.assertTrue(taskbar_follow_refresh_due(now=10.021, last_refresh_at=10.0, interval=0.020))

    def test_taskbar_follow_force_visible_active_checks_deadline(self):
        self.assertFalse(taskbar_follow_force_visible_active(now=10.0, force_visible_until=0.0))
        self.assertTrue(taskbar_follow_force_visible_active(now=10.0, force_visible_until=10.1))
        self.assertFalse(taskbar_follow_force_visible_active(now=10.1, force_visible_until=10.1))

    def test_build_standalone_above_taskbar_target_keeps_visible_taskbar_clearance(self):
        target = build_standalone_above_taskbar_target(
            visible_taskbar=QRectF(0.0, 1040.0, 1920.0, 40.0),
            follow_taskbar=QRectF(0.0, 1040.0, 1920.0, 40.0),
            track_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            window_width=940.0,
            main_kind="music",
            stack_width=420.0,
            main_width=420.0,
            main_height=48.0,
            main_top=600.0,
            main_left=260.0,
            target_center_x=960.0,
            gap=8.0,
            auto_hide_follow=True,
            hidden_autohide=False,
        )

        self.assertEqual(target.direction, -1)
        self.assertFalse(target.animate_follow)
        self.assertAlmostEqual(target.position_ratio, 0.5)
        self.assertAlmostEqual(target.window_pos.y() + target.main_top + target.main_height, 1032.0, delta=1.0)

    def test_build_standalone_above_taskbar_target_flushes_hidden_autohide(self):
        target = build_standalone_above_taskbar_target(
            visible_taskbar=QRectF(0.0, 1040.0, 1920.0, 40.0),
            follow_taskbar=QRectF(0.0, 1080.0, 1920.0, 40.0),
            track_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            window_width=940.0,
            main_kind="music",
            stack_width=420.0,
            main_width=420.0,
            main_height=48.0,
            main_top=600.0,
            main_left=260.0,
            target_center_x=960.0,
            gap=0.0,
            auto_hide_follow=True,
            hidden_autohide=True,
            bottom_extension=40.0,
        )

        self.assertTrue(target.hidden_autohide)
        self.assertTrue(target.animate_follow)
        self.assertFalse(target.partial_shell_follow)
        self.assertAlmostEqual(target.window_pos.y() + target.main_top + target.main_height, 1080.0, delta=1.0)

    def test_build_standalone_above_taskbar_target_does_not_animate_partial_shell_follow(self):
        target = build_standalone_above_taskbar_target(
            visible_taskbar=QRectF(0.0, 1040.0, 1920.0, 40.0),
            follow_taskbar=QRectF(0.0, 1060.0, 1920.0, 20.0),
            track_rect=QRectF(0.0, 1040.0, 1920.0, 40.0),
            screen_geometry=QRectF(0.0, 0.0, 1920.0, 1080.0),
            window_width=940.0,
            main_kind="music",
            stack_width=420.0,
            main_width=420.0,
            main_height=48.0,
            main_top=600.0,
            main_left=260.0,
            target_center_x=960.0,
            gap=8.0,
            auto_hide_follow=True,
            hidden_autohide=False,
            previous_hidden_autohide=True,
        )

        self.assertTrue(target.partial_shell_follow)
        self.assertFalse(target.animate_follow)


if __name__ == "__main__":
    unittest.main()
