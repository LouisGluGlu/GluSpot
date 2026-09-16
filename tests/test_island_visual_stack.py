from __future__ import annotations

import unittest
from dataclasses import dataclass

from PySide6.QtCore import QRectF

from local.island_visual_stack import (
    BarrageLayoutSource,
    IslandKind,
    MotionSnapshot,
    StackVisual,
    active_motion_step_plan,
    active_visual_motion_params,
    barrage_background_style_plan,
    barrage_scene_layout_plan,
    candidate_dot_messages,
    expanded_message_rows,
    initial_motion_rect_for_visual,
    interaction_mask_update_plan,
    main_visual_identity,
    message_visual_key,
    plugin_alert_key,
    plugin_alert_exit_rect_towards_main,
    popup_message_rows,
    side_notification_messages,
    stack_paint_context_plan,
    stack_frame_paint_plan,
    stack_paint_items,
    stale_motion_step_plan,
    taskbar_should_drop_stale_motion_key,
)


@dataclass
class VisualMessage:
    key: str
    popup_until: float = 0.0


def message_key(message: object) -> str:
    return getattr(message, "key")


class IslandVisualStackTests(unittest.TestCase):
    def test_stack_visual_model_is_qt_free_except_target_rect(self):
        rect = QRectF(10.0, 20.0, 30.0, 40.0)
        visual = StackVisual("music", IslandKind.MUSIC, rect, z_index=10)

        self.assertEqual(visual.key, "music")
        self.assertEqual(visual.kind, IslandKind.MUSIC)
        self.assertEqual(visual.rect, rect)
        self.assertEqual(visual.z_index, 10)

    def test_main_visual_identity_uses_first_message_only_for_message_island(self):
        messages = [VisualMessage("first"), VisualMessage("second")]

        self.assertEqual(main_visual_identity(IslandKind.MUSIC, messages, message_key), ("music", None))
        self.assertEqual(main_visual_identity(IslandKind.PLUGIN, messages, message_key), ("plugin", None))
        self.assertEqual(main_visual_identity(IslandKind.EMPTY_TEXT, messages, message_key), ("empty-text", None))
        self.assertEqual(main_visual_identity(IslandKind.TIME, messages, message_key), ("time", None))
        self.assertEqual(main_visual_identity(IslandKind.THIN_BAR, messages, message_key), ("thin", None))
        key, message = main_visual_identity(IslandKind.MESSAGE, messages, message_key)
        self.assertEqual(key, "message:first")
        self.assertIs(message, messages[0])

    def test_message_dot_candidates_skip_main_message_when_message_owns_island(self):
        messages = [VisualMessage("main"), VisualMessage("dot-a"), VisualMessage("dot-b")]

        self.assertEqual(
            [message.key for message in candidate_dot_messages(IslandKind.MESSAGE, messages, 2)],
            ["dot-a", "dot-b"],
        )
        self.assertEqual(
            [message.key for message in candidate_dot_messages(IslandKind.MUSIC, messages, 2)],
            ["main", "dot-a"],
        )

    def test_taskbar_side_notifications_share_slots_between_plugin_alerts_and_message_dots(self):
        messages = [VisualMessage("a"), VisualMessage("b"), VisualMessage("c")]

        dot_messages, total = side_notification_messages(
            main_kind=IslandKind.MUSIC,
            messages=messages,
            folded_limit=3,
            taskbar_side_notifications=True,
            plugin_alert_count=2,
            notification_limit=3,
        )

        self.assertEqual([message.key for message in dot_messages], ["a"])
        self.assertEqual(total, 3)

    def test_non_taskbar_side_notifications_do_not_let_plugin_alerts_eat_message_dots(self):
        messages = [VisualMessage("a"), VisualMessage("b")]

        dot_messages, total = side_notification_messages(
            main_kind=IslandKind.MUSIC,
            messages=messages,
            folded_limit=2,
            taskbar_side_notifications=False,
            plugin_alert_count=2,
            notification_limit=3,
        )

        self.assertEqual([message.key for message in dot_messages], ["a", "b"])
        self.assertEqual(total, 2)

    def test_expanded_message_rows_do_not_duplicate_main_message_island(self):
        messages = [VisualMessage("main"), VisualMessage("row-a"), VisualMessage("row-b")]

        self.assertEqual(
            [message.key for message in expanded_message_rows(main_kind=IslandKind.MESSAGE, messages=messages, folded_limit=1)],
            ["row-a"],
        )
        self.assertEqual(
            [message.key for message in expanded_message_rows(main_kind=IslandKind.MUSIC, messages=messages, folded_limit=1)],
            ["main", "row-a", "row-b"],
        )

    def test_popup_rows_are_hidden_for_expanded_message_owned_or_taskbar_side_layouts(self):
        messages = [VisualMessage("main", popup_until=20.0), VisualMessage("second", popup_until=20.0)]

        self.assertEqual(
            [message.key for message in popup_message_rows(
                main_kind=IslandKind.MUSIC,
                messages=messages,
                now=10.0,
                taskbar_side_notifications=False,
                expanded=False,
            )],
            ["main"],
        )
        self.assertEqual(
            popup_message_rows(
                main_kind=IslandKind.MUSIC,
                messages=messages,
                now=10.0,
                taskbar_side_notifications=True,
                expanded=False,
            ),
            [],
        )
        self.assertEqual(
            popup_message_rows(
                main_kind=IslandKind.MESSAGE,
                messages=messages,
                now=10.0,
                taskbar_side_notifications=False,
                expanded=False,
            ),
            [],
        )
        self.assertEqual(
            popup_message_rows(
                main_kind=IslandKind.MUSIC,
                messages=messages,
                now=10.0,
                taskbar_side_notifications=False,
                expanded=True,
            ),
            [],
        )

    def test_visual_key_helpers_are_stable(self):
        message = VisualMessage("wechat")

        self.assertEqual(message_visual_key(message, message_key), "message:wechat")
        self.assertEqual(message_visual_key(message, message_key, folded=True), "message-dot:wechat")
        self.assertEqual(plugin_alert_key((0, "TIME", "", "", "", "", "time:alarm")), "plugin-alert:time:alarm")
        self.assertEqual(plugin_alert_key(()), "plugin-alert:")

    def test_stack_paint_items_sort_by_z_index_and_isolate_every_visual(self):
        main = StackVisual("music", IslandKind.MUSIC, QRectF(100.0, 80.0, 420.0, 48.0), z_index=10)
        popup = StackVisual("message:a", IslandKind.MESSAGE, QRectF(100.0, 24.0, 420.0, 46.0), z_index=3)
        dot = StackVisual("message-dot:b", IslandKind.MESSAGE, QRectF(530.0, 92.0, 28.0, 28.0), z_index=14)

        plan = stack_paint_items(
            [
                (main, MotionSnapshot(main.rect, 1.0)),
                (dot, MotionSnapshot(dot.rect, 1.0)),
                (popup, MotionSnapshot(popup.rect, 1.0)),
            ]
        )

        self.assertEqual([item.visual.key for item in plan], ["message:a", "music", "message-dot:b"])
        self.assertTrue(all(item.isolate_painter_state for item in plan))
        self.assertEqual([item.motion.rect for item in plan], [popup.rect, main.rect, dot.rect])

    def test_stack_frame_paint_plan_filters_visible_motions_and_exports_mask_rects(self):
        main = StackVisual("music", IslandKind.MUSIC, QRectF(100.0, 80.0, 420.0, 48.0), z_index=10)
        faded = StackVisual("message:faded", IslandKind.MESSAGE, QRectF(100.0, 24.0, 420.0, 46.0), z_index=3)
        dot = StackVisual("message-dot:b", IslandKind.MESSAGE, QRectF(530.0, 92.0, 28.0, 28.0), z_index=14)
        missing = StackVisual("message:missing", IslandKind.MESSAGE, QRectF(0.0, 0.0, 10.0, 10.0), z_index=1)
        motions = {
            main.key: MotionSnapshot(QRectF(main.rect), 1.0),
            faded.key: MotionSnapshot(QRectF(faded.rect), 0.06),
            dot.key: MotionSnapshot(QRectF(dot.rect), 0.5),
        }

        plan = stack_frame_paint_plan([missing, main, faded, dot], motions, minimum_opacity=0.06)

        self.assertEqual([visual.key for visual, _motion in plan.visible_items], ["music", "message-dot:b"])
        self.assertEqual([item.visual.key for item in plan.paint_items], ["music", "message-dot:b"])
        self.assertEqual(list(plan.mask_rects), [main.rect, dot.rect])

    def test_stack_paint_context_plan_clamps_opacity_and_marks_main_decorations(self):
        main = StackVisual("music", IslandKind.MUSIC, QRectF(100.0, 80.0, 420.0, 48.0), z_index=10)
        motion = MotionSnapshot(main.rect, 1.4)

        plan = stack_paint_context_plan(
            main,
            motion,
            now=10.0,
            hovered_key="",
            hover_started_at=0.0,
            hover_bounce_until=0.0,
            expanded=False,
            taskbar_compact_active=True,
            edge_notch_active=True,
        )

        self.assertEqual(plan.rect, main.rect)
        self.assertEqual(plan.opacity, 1.0)
        self.assertTrue(plan.paint_taskbar_left_handle)
        self.assertTrue(plan.paint_edge_notch_backing)
        self.assertTrue(plan.clip_edge_notch)

    def test_stack_paint_context_plan_applies_hover_scale_only_for_active_non_expanded_item(self):
        rect = QRectF(100.0, 80.0, 420.0, 48.0)
        visual = StackVisual("music", IslandKind.MUSIC, rect, z_index=10)
        motion = MotionSnapshot(rect, 0.8)

        plan = stack_paint_context_plan(
            visual,
            motion,
            now=10.5,
            hovered_key="music",
            hover_started_at=10.0,
            hover_bounce_until=11.0,
            expanded=False,
            taskbar_compact_active=False,
            edge_notch_active=False,
        )
        expanded_plan = stack_paint_context_plan(
            visual,
            motion,
            now=10.5,
            hovered_key="music",
            hover_started_at=10.0,
            hover_bounce_until=11.0,
            expanded=True,
            taskbar_compact_active=False,
            edge_notch_active=False,
        )

        self.assertGreater(plan.rect.width(), rect.width())
        self.assertGreater(plan.rect.height(), rect.height())
        self.assertEqual(plan.rect.center(), rect.center())
        self.assertEqual(plan.opacity, 0.8)
        self.assertEqual(expanded_plan.rect, rect)

    def test_stack_paint_context_plan_keeps_auxiliary_visuals_free_of_main_decorations(self):
        message = StackVisual("message:a", IslandKind.MESSAGE, QRectF(10.0, 20.0, 220.0, 46.0), z_index=3)

        plan = stack_paint_context_plan(
            message,
            MotionSnapshot(message.rect, -0.5),
            now=10.0,
            hovered_key="",
            hover_started_at=0.0,
            hover_bounce_until=0.0,
            expanded=False,
            taskbar_compact_active=True,
            edge_notch_active=True,
        )

        self.assertEqual(plan.opacity, 0.0)
        self.assertFalse(plan.paint_taskbar_left_handle)
        self.assertFalse(plan.paint_edge_notch_backing)
        self.assertFalse(plan.clip_edge_notch)

    def test_interaction_mask_plan_clears_active_mask_outside_finder_and_suppressed_scene(self):
        plan = interaction_mask_update_plan(
            [],
            platform_win32=True,
            finder_mode=False,
            scene_suppressed=False,
            interaction_mask_active=True,
            finder_hover_rect=QRectF(),
            last_mask_rect=QRectF(),
            now=10.0,
            mask_deferred_until=0.0,
            last_pointer_move_at=0.0,
            last_mask_update_at=0.0,
        )

        self.assertEqual(plan.action, "clear")
        self.assertFalse(plan.record_update_at)

    def test_interaction_mask_plan_respects_deferred_pointer_and_interval_gates(self):
        base = dict(
            rects=[QRectF(100.0, 40.0, 80.0, 30.0)],
            platform_win32=True,
            finder_mode=True,
            scene_suppressed=False,
            interaction_mask_active=False,
            finder_hover_rect=QRectF(),
            last_mask_rect=QRectF(),
        )

        deferred = interaction_mask_update_plan(
            **base,
            now=10.0,
            mask_deferred_until=11.0,
            last_pointer_move_at=0.0,
            last_mask_update_at=0.0,
        )
        pointer_busy = interaction_mask_update_plan(
            **base,
            now=10.0,
            mask_deferred_until=0.0,
            last_pointer_move_at=9.98,
            last_mask_update_at=0.0,
        )
        interval_busy = interaction_mask_update_plan(
            **base,
            now=10.0,
            mask_deferred_until=0.0,
            last_pointer_move_at=0.0,
            last_mask_update_at=9.9,
        )

        self.assertEqual(deferred.action, "skip")
        self.assertFalse(deferred.record_update_at)
        self.assertEqual(pointer_busy.action, "skip")
        self.assertFalse(pointer_busy.record_update_at)
        self.assertEqual(interval_busy.action, "skip")
        self.assertFalse(interval_busy.record_update_at)

    def test_interaction_mask_plan_applies_united_finder_hover_and_visual_rects(self):
        plan = interaction_mask_update_plan(
            [QRectF(100.0, 50.0, 20.0, 10.0)],
            platform_win32=True,
            finder_mode=True,
            scene_suppressed=False,
            interaction_mask_active=False,
            finder_hover_rect=QRectF(10.0, 10.0, 30.0, 10.0),
            last_mask_rect=QRectF(),
            now=10.0,
            mask_deferred_until=0.0,
            last_pointer_move_at=0.0,
            last_mask_update_at=0.0,
            unit=1.0,
        )

        self.assertEqual(plan.action, "apply")
        self.assertTrue(plan.record_update_at)
        self.assertEqual(plan.mask_rect, QRectF(6.0, 8.0, 126.0, 62.0))

    def test_interaction_mask_plan_skips_tiny_delta_but_records_update_gate(self):
        plan = interaction_mask_update_plan(
            [QRectF(100.0, 50.0, 20.0, 10.0)],
            platform_win32=True,
            finder_mode=False,
            scene_suppressed=True,
            interaction_mask_active=True,
            finder_hover_rect=QRectF(),
            last_mask_rect=QRectF(92.4, 44.2, 35.8, 21.7),
            now=10.0,
            mask_deferred_until=0.0,
            last_pointer_move_at=0.0,
            last_mask_update_at=0.0,
            unit=1.0,
        )

        self.assertEqual(plan.action, "skip")
        self.assertTrue(plan.record_update_at)

    def test_barrage_background_style_plan_skips_sampling_during_drag_or_recent_press(self):
        drag_plan = barrage_background_style_plan(
            current_dark_background=True,
            now=10.0,
            drag_candidate=True,
            last_pointer_press_at=0.0,
            last_checked_at=0.0,
        )
        press_plan = barrage_background_style_plan(
            current_dark_background=False,
            now=10.0,
            drag_candidate=False,
            last_pointer_press_at=9.4,
            last_checked_at=0.0,
        )

        self.assertTrue(drag_plan.dark_background)
        self.assertFalse(drag_plan.sample_luma)
        self.assertFalse(drag_plan.record_checked_at)
        self.assertFalse(press_plan.dark_background)
        self.assertFalse(press_plan.sample_luma)
        self.assertFalse(press_plan.record_checked_at)

    def test_barrage_background_style_plan_uses_interval_gate_before_sampling(self):
        plan = barrage_background_style_plan(
            current_dark_background=True,
            now=10.0,
            drag_candidate=False,
            last_pointer_press_at=0.0,
            last_checked_at=9.6,
        )

        self.assertTrue(plan.dark_background)
        self.assertFalse(plan.sample_luma)
        self.assertFalse(plan.record_checked_at)

    def test_barrage_background_style_plan_requests_luma_then_applies_hysteresis(self):
        request = barrage_background_style_plan(
            current_dark_background=False,
            now=10.0,
            drag_candidate=False,
            last_pointer_press_at=0.0,
            last_checked_at=9.0,
        )
        dark = barrage_background_style_plan(
            current_dark_background=False,
            now=10.0,
            drag_candidate=False,
            last_pointer_press_at=0.0,
            last_checked_at=9.0,
            sampled_luma=0.40,
        )
        light = barrage_background_style_plan(
            current_dark_background=True,
            now=10.0,
            drag_candidate=False,
            last_pointer_press_at=0.0,
            last_checked_at=9.0,
            sampled_luma=0.70,
        )
        stable = barrage_background_style_plan(
            current_dark_background=True,
            now=10.0,
            drag_candidate=False,
            last_pointer_press_at=0.0,
            last_checked_at=9.0,
            sampled_luma=0.52,
        )

        self.assertTrue(request.sample_luma)
        self.assertFalse(request.record_checked_at)
        self.assertTrue(dark.dark_background)
        self.assertTrue(dark.record_checked_at)
        self.assertFalse(light.dark_background)
        self.assertTrue(light.record_checked_at)
        self.assertTrue(stable.dark_background)
        self.assertTrue(stable.record_checked_at)

    def test_barrage_scene_layout_plan_places_visible_item_and_hit_rect(self):
        message = VisualMessage("preview")
        source = BarrageLayoutSource(
            message=message,
            text="Barrage: visible",
            action_key="preview-barrage:1",
            started_at=19.15,
            serial=1,
            text_width=82.0,
        )

        plan = barrage_scene_layout_plan(
            [source],
            viewport=QRectF(0.0, 0.0, 640.0, 220.0),
            clock=20.0,
            font_height=14.0,
            y_offset=40.0,
            speed=100.0,
            show_icon=True,
            travel_base_width=640.0,
            unit=1.0,
        )

        self.assertEqual(len(plan.paint_items), 1)
        item = plan.paint_items[0]
        self.assertIs(item.message, message)
        self.assertEqual(item.text, "Barrage: visible")
        self.assertEqual(item.action_key, "preview-barrage:1")
        self.assertEqual(item.lane, 0)
        self.assertEqual(item.text_rect.top(), 40.0)
        self.assertLess(item.text_rect.left(), 640.0)
        self.assertGreater(item.text_rect.right(), 0.0)
        self.assertEqual(plan.hit_rects["preview-barrage:1"], item.text_rect.adjusted(-10.0, -8.0, 12.0, 8.0))

    def test_barrage_scene_layout_plan_sorts_sources_by_start_and_serial(self):
        early = BarrageLayoutSource(
            message=VisualMessage("early"),
            text="early",
            action_key="message:early",
            started_at=8.0,
            serial=9,
            text_width=40.0,
        )
        same_time_first = BarrageLayoutSource(
            message=VisualMessage("same-a"),
            text="same-a",
            action_key="message:same-a",
            started_at=9.0,
            serial=1,
            text_width=40.0,
        )
        same_time_second = BarrageLayoutSource(
            message=VisualMessage("same-b"),
            text="same-b",
            action_key="message:same-b",
            started_at=9.0,
            serial=2,
            text_width=40.0,
        )

        plan = barrage_scene_layout_plan(
            [same_time_second, early, same_time_first],
            viewport=QRectF(0.0, 0.0, 640.0, 220.0),
            clock=9.2,
            font_height=14.0,
            y_offset=0.0,
            speed=100.0,
            show_icon=False,
            travel_base_width=640.0,
            unit=1.0,
        )

        self.assertEqual(
            [item.action_key for item in plan.paint_items],
            ["message:early", "message:same-a", "message:same-b"],
        )

    def test_barrage_scene_layout_plan_prunes_preview_and_fullscreen_sources_by_visibility(self):
        active = BarrageLayoutSource(
            message=VisualMessage("active"),
            text="active",
            action_key="message:active",
            started_at=20.0,
            serial=1,
            text_width=40.0,
        )
        preview_alive = BarrageLayoutSource(
            message=VisualMessage("preview-alive"),
            text="alive",
            action_key="preview:alive",
            started_at=19.7,
            serial=2,
            text_width=40.0,
        )
        preview_expired = BarrageLayoutSource(
            message=VisualMessage("preview-expired"),
            text="expired",
            action_key="preview:expired",
            started_at=0.0,
            serial=3,
            text_width=40.0,
        )
        fullscreen_alive = BarrageLayoutSource(
            message=VisualMessage("fullscreen-alive"),
            text="fullscreen",
            action_key="fullscreen:alive",
            started_at=19.4,
            serial=4,
            text_width=60.0,
        )

        plan = barrage_scene_layout_plan(
            [active],
            preview_sources=[preview_alive, preview_expired],
            fullscreen_sources=[fullscreen_alive],
            viewport=QRectF(0.0, 0.0, 640.0, 220.0),
            clock=20.0,
            font_height=14.0,
            y_offset=0.0,
            speed=100.0,
            show_icon=True,
            travel_base_width=640.0,
            unit=1.0,
        )

        self.assertEqual([message.key for message in plan.alive_preview_messages], ["preview-alive"])
        self.assertEqual([message.key for message in plan.alive_fullscreen_messages], ["fullscreen-alive"])

    def test_taskbar_main_initial_motion_starts_at_target_without_vertical_pop(self):
        main = StackVisual("music", IslandKind.MUSIC, QRectF(100.0, 40.0, 420.0, 34.0), z_index=10)

        start = initial_motion_rect_for_visual(
            main,
            [main],
            {},
            stack_direction=-1,
            expanded=False,
            now=10.0,
            taskbar_compact_active=True,
        )

        self.assertEqual(start, main.rect)

    def test_plugin_panel_initial_motion_starts_from_strip_edge(self):
        strip = StackVisual("plugin", IslandKind.PLUGIN, QRectF(10.0, 20.0, 420.0, 48.0), z_index=6)
        panel = StackVisual("plugin-panel", IslandKind.PLUGIN_PANEL, QRectF(10.0, 78.0, 420.0, 188.0), z_index=5)

        start_down = initial_motion_rect_for_visual(
            panel,
            [strip, panel],
            {},
            stack_direction=1,
            expanded=True,
            now=10.0,
            taskbar_compact_active=False,
        )
        start_up = initial_motion_rect_for_visual(
            panel,
            [strip, panel],
            {},
            stack_direction=-1,
            expanded=True,
            now=10.0,
            taskbar_compact_active=False,
        )

        self.assertEqual(start_down.top(), strip.rect.bottom())
        self.assertLessEqual(start_down.height(), 10.0)
        self.assertEqual(start_up.bottom(), strip.rect.top())
        self.assertLessEqual(start_up.height(), 10.0)

    def test_message_initial_motion_prefers_existing_popup_motion(self):
        old_message_rect = QRectF(50.0, 20.0, 200.0, 46.0)
        message = VisualMessage("new", popup_until=20.0)
        visual = StackVisual("message:new", IslandKind.MESSAGE, QRectF(80.0, 110.0, 200.0, 46.0), message=message)

        start = initial_motion_rect_for_visual(
            visual,
            [visual],
            {"message:old": MotionSnapshot(old_message_rect, 1.0)},
            stack_direction=1,
            expanded=False,
            now=10.0,
            taskbar_compact_active=False,
        )

        self.assertEqual(start.width(), 16.0)
        self.assertEqual(start.height(), 10.0)
        self.assertEqual(start.center().x(), old_message_rect.center().x())
        self.assertEqual(start.top(), old_message_rect.bottom() + 4.0)

    def test_message_initial_motion_falls_back_to_main_motion_then_expanded_dot(self):
        message = VisualMessage("row", popup_until=5.0)
        main_rect = QRectF(100.0, 80.0, 420.0, 48.0)
        row = StackVisual("message:row", IslandKind.MESSAGE, QRectF(120.0, 150.0, 260.0, 46.0), message=message)
        main = StackVisual("music", IslandKind.MUSIC, main_rect, z_index=10)

        from_main = initial_motion_rect_for_visual(
            row,
            [main, row],
            {"music": MotionSnapshot(main_rect, 1.0)},
            stack_direction=1,
            expanded=False,
            now=10.0,
            taskbar_compact_active=False,
        )
        expanded_dot = initial_motion_rect_for_visual(
            row,
            [row],
            {},
            stack_direction=-1,
            expanded=True,
            now=10.0,
            taskbar_compact_active=False,
        )

        self.assertEqual(from_main.left(), main_rect.center().x() - 3.0)
        self.assertEqual(from_main.top(), main_rect.bottom() + 10.0)
        self.assertEqual(expanded_dot.width(), 7.0)
        self.assertEqual(expanded_dot.top(), row.rect.bottom() + 7.0)

    def test_initial_motion_default_offsets_with_stack_direction(self):
        visual = StackVisual("thin", IslandKind.THIN_BAR, QRectF(30.0, 60.0, 120.0, 20.0))

        down = initial_motion_rect_for_visual(
            visual,
            [visual],
            {},
            stack_direction=1,
            expanded=False,
            now=10.0,
            taskbar_compact_active=False,
        )
        up = initial_motion_rect_for_visual(
            visual,
            [visual],
            {},
            stack_direction=-1,
            expanded=False,
            now=10.0,
            taskbar_compact_active=False,
        )

        self.assertEqual(down.top(), visual.rect.top() - 12.0)
        self.assertEqual(up.top(), visual.rect.top() + 12.0)

    def test_plugin_alert_exit_rect_shrinks_towards_main_island(self):
        main = StackVisual("music", IslandKind.MUSIC, QRectF(160.0, 82.0, 420.0, 48.0), z_index=10)
        right_alert = QRectF(600.0, 85.0, 156.0, 42.0)
        left_alert = QRectF(0.0, 85.0, 126.0, 42.0)

        right_target = plugin_alert_exit_rect_towards_main(right_alert, [main])
        left_target = plugin_alert_exit_rect_towards_main(left_alert, [main])

        self.assertLessEqual(right_target.width(), 10.0)
        self.assertLess(right_target.center().x(), right_alert.center().x())
        self.assertAlmostEqual(right_target.left(), main.rect.right() + 4.0, delta=1.0)
        self.assertLessEqual(left_target.width(), 10.0)
        self.assertGreater(left_target.center().x(), left_alert.center().x())
        self.assertAlmostEqual(left_target.right(), main.rect.left() - 4.0, delta=1.0)

    def test_plugin_alert_exit_rect_without_main_collapses_in_place(self):
        alert = QRectF(60.0, 40.0, 120.0, 30.0)

        target = plugin_alert_exit_rect_towards_main(alert, [])

        self.assertEqual(target.center(), alert.center())
        self.assertEqual(target.width(), 10.0)

    def test_taskbar_should_drop_stale_motion_key_is_limited_to_side_visuals(self):
        self.assertTrue(taskbar_should_drop_stale_motion_key("plugin-alert:time", taskbar_compact_active=True))
        self.assertTrue(taskbar_should_drop_stale_motion_key("taskbar-overlay:time", taskbar_compact_active=True))
        self.assertTrue(taskbar_should_drop_stale_motion_key("message-dot:wechat", taskbar_compact_active=True))
        self.assertFalse(taskbar_should_drop_stale_motion_key("music", taskbar_compact_active=True))
        self.assertFalse(taskbar_should_drop_stale_motion_key("message-dot:wechat", taskbar_compact_active=False))

    def test_active_visual_motion_params_keep_mode_specific_spring_feel(self):
        self.assertEqual(
            active_visual_motion_params(
                finder_mode=True,
                taskbar_compact_active=False,
                stack_bounce_active=True,
            ),
            (0.19, 0.84),
        )
        self.assertEqual(
            active_visual_motion_params(
                finder_mode=False,
                taskbar_compact_active=True,
                stack_bounce_active=False,
            ),
            (0.22, 0.86),
        )
        self.assertEqual(
            active_visual_motion_params(
                finder_mode=False,
                taskbar_compact_active=False,
                stack_bounce_active=False,
            ),
            (0.34, 0.76),
        )

    def test_active_motion_step_plan_snaps_dragged_main_visual_to_target(self):
        main = StackVisual("music", IslandKind.MUSIC, QRectF(100.0, 40.0, 420.0, 48.0), z_index=10)

        plan = active_motion_step_plan(
            main,
            motion_exists=False,
            drag_hides_auxiliary_visuals=True,
            finder_mode=False,
            taskbar_compact_active=False,
            stack_bounce_active=True,
        )

        self.assertTrue(plan.create_missing_motion)
        self.assertTrue(plan.snap_to_target)
        self.assertEqual(plan.target_rect, main.rect)
        self.assertEqual(plan.target_opacity, 1.0)

    def test_active_motion_step_plan_steps_auxiliary_visuals_even_while_dragging(self):
        alert = StackVisual("plugin-alert:time", IslandKind.PLUGIN_ALERT, QRectF(560.0, 44.0, 150.0, 42.0), z_index=8)

        plan = active_motion_step_plan(
            alert,
            motion_exists=True,
            drag_hides_auxiliary_visuals=True,
            finder_mode=False,
            taskbar_compact_active=False,
            stack_bounce_active=True,
        )

        self.assertFalse(plan.create_missing_motion)
        self.assertFalse(plan.snap_to_target)
        self.assertEqual(plan.target_rect, alert.rect)
        self.assertEqual((plan.response, plan.damping), (0.28, 0.76))

    def test_stale_motion_step_plan_drops_immediately_when_taskbar_side_visual_is_stale(self):
        plan = stale_motion_step_plan(
            "message-dot:wechat",
            MotionSnapshot(QRectF(20.0, 40.0, 24.0, 24.0), 1.0),
            [],
            expanded=False,
            finder_mode=False,
            drop_immediately=True,
        )

        self.assertTrue(plan.drop_immediately)
        self.assertIsNone(plan.target_rect)

    def test_stale_motion_step_plan_moves_generic_visual_up_into_short_exit_strip(self):
        rect = QRectF(20.0, 40.0, 120.0, 36.0)

        plan = stale_motion_step_plan(
            "message:old",
            MotionSnapshot(rect, 1.0),
            [],
            expanded=False,
            finder_mode=False,
            unit=2.0,
        )

        self.assertFalse(plan.drop_immediately)
        self.assertIsNotNone(plan.target_rect)
        self.assertEqual(plan.target_rect, QRectF(20.0, 24.0, 120.0, 32.0))
        self.assertEqual(plan.target_opacity, 0.0)
        self.assertEqual(plan.response, 0.24)
        self.assertEqual(plan.damping, 0.76)
        self.assertEqual(plan.delete_below_opacity, 0.025)

    def test_stale_motion_step_plan_shrinks_expanded_plugin_alert_towards_main(self):
        main = StackVisual("music", IslandKind.MUSIC, QRectF(160.0, 82.0, 420.0, 48.0), z_index=10)
        alert_rect = QRectF(600.0, 85.0, 156.0, 42.0)

        plan = stale_motion_step_plan(
            "plugin-alert:time",
            MotionSnapshot(alert_rect, 1.0),
            [main],
            expanded=True,
            finder_mode=True,
        )

        self.assertFalse(plan.drop_immediately)
        self.assertIsNotNone(plan.target_rect)
        self.assertLess(plan.target_rect.center().x(), alert_rect.center().x())
        self.assertAlmostEqual(plan.target_rect.left(), main.rect.right() + 4.0, delta=1.0)
        self.assertEqual(plan.response, 0.18)
        self.assertEqual(plan.damping, 0.68)


if __name__ == "__main__":
    unittest.main()
