import unittest

from local.island_presentation import (
    ActiveVisualAnimationPlan,
    active_barrage_messages,
    active_visual_animation_plan,
    animation_dict_active,
    animation_duration,
    animation_speed_factor,
    animation_started_active,
    animation_tick_delta_seconds,
    barrage_alive_messages_for_clock,
    barrage_action_key_for_message,
    barrage_hover_clock_plan,
    barrage_overlay_context_active,
    barrage_overlay_state,
    barrage_preview_active,
    empty_island_can_display,
    empty_island_text_active,
    empty_island_time_active,
    folded_message_limit,
    fullscreen_plugin_alert_action_key,
    fullscreen_plugin_barrage_update_action,
    fullscreen_plugin_alert_overlay_state,
    fullscreen_plugin_alert_is_reminder,
    fullscreen_plugin_alert_key,
    fullscreen_plugin_alert_suppression_state,
    fullscreen_overlay_context_active,
    hover_auto_activation_candidate,
    left_stack_collapse_due,
    lyric_fallback_advance_plan,
    main_island_kind,
    media_visual_track_changed,
    message_hit_key,
    message_identity_key,
    message_key_from_hit,
    music_background_layer_paint_plan,
    music_surface_paint_plan,
    music_lyric_tick_plan,
    music_wave_cadence_diagnostics,
    music_wave_input_snapshot,
    music_wave_update_due,
    music_wave_update_plan,
    normalize_barrage_display_mode,
    normalize_empty_island_display_mode,
    normalize_fullscreen_plugin_alert_style,
    normalize_no_music_island_priority,
    plugin_panel_hover_open_due,
    plugin_alert_overlay_state,
    render_frame_due,
    scene_suppressed_frame_execution_plan,
    scene_suppressed_interaction_reset_plan,
    scene_suppression_probe_due,
    scene_suppression_update_plan,
    scene_suppressed_tick_plan,
    tick_delta_seconds,
    tick_frame_execution_plan,
    tick_follow_refresh_plan,
    tick_interaction_plan,
    TickRenderPlan,
    tick_render_plan,
    topmost_reinforcement_due,
)


class BMessage:
    def __init__(
        self,
        name: str,
        started_at: float,
        *,
        serial: int = 0,
        created_at: float = 0.0,
        app: str = "App",
        sender: str = "Sender",
        action_key: str = "",
    ):
        self.name = name
        self.barrage_started_at = started_at
        self.serial = serial
        self.created_at = created_at
        self.app = app
        self.sender = sender
        self.action_key = action_key


class IslandPresentationTests(unittest.TestCase):
    def test_animation_speed_factor_clamps_and_defaults_invalid_values(self):
        self.assertEqual(animation_speed_factor("bad"), 1.0)
        self.assertEqual(animation_speed_factor(0.01), 0.1)
        self.assertEqual(animation_speed_factor(9.0), 4.0)
        self.assertEqual(animation_speed_factor(2.0), 2.0)

    def test_animation_duration_scales_with_speed_and_minimum(self):
        self.assertAlmostEqual(animation_duration(0.24, speed=0.5), 0.48, delta=0.001)
        self.assertAlmostEqual(animation_duration(0.24, speed=2.0), 0.12, delta=0.001)
        self.assertAlmostEqual(animation_duration(0.01, speed=4.0, minimum=0.05), 0.05, delta=0.001)

    def test_animation_started_active_uses_scaled_duration(self):
        self.assertTrue(animation_started_active(10.0, 0.24, 10.11, speed=2.0))
        self.assertFalse(animation_started_active(10.0, 0.24, 10.13, speed=2.0))
        self.assertFalse(animation_started_active(0.0, 0.24, 10.0, speed=1.0))
        self.assertFalse(animation_started_active("bad", 0.24, 10.0, speed=1.0))

    def test_animation_dict_active_reads_started_at_only_from_dicts(self):
        self.assertTrue(animation_dict_active({"started_at": 10.0}, 10.1, 0.24, speed=1.0))
        self.assertFalse(animation_dict_active({"started_at": "bad"}, 10.1, 0.24, speed=1.0))
        self.assertFalse(animation_dict_active([], 10.1, 0.24, speed=1.0))

    def test_tick_delta_seconds_clamps_jitter_and_long_pauses(self):
        self.assertAlmostEqual(tick_delta_seconds(10.0001, 10.0), 0.001, delta=0.0001)
        self.assertAlmostEqual(tick_delta_seconds(10.12, 10.0), 0.12, delta=0.0001)
        self.assertAlmostEqual(tick_delta_seconds(11.0, 10.0), 0.2, delta=0.0001)
        self.assertAlmostEqual(tick_delta_seconds("bad", 10.0), 0.001, delta=0.0001)

    def test_animation_tick_delta_seconds_scales_clamped_tick_by_speed(self):
        self.assertAlmostEqual(animation_tick_delta_seconds(10.1, 10.0, speed=2.0), 0.2, delta=0.0001)
        self.assertAlmostEqual(animation_tick_delta_seconds(11.0, 10.0, speed=2.0), 0.4, delta=0.0001)

    def test_render_frame_due_uses_interval_and_tolerance(self):
        self.assertTrue(
            render_frame_due(now=10.0, last_render_at=0.0, interval_seconds=1 / 60, tolerance_seconds=0.0)
        )
        self.assertFalse(
            render_frame_due(now=10.014, last_render_at=10.0, interval_seconds=1 / 60, tolerance_seconds=0.0)
        )
        self.assertTrue(
            render_frame_due(now=10.0148, last_render_at=10.0, interval_seconds=1 / 60, tolerance_seconds=0.002)
        )
        self.assertTrue(
            render_frame_due(now=10.014, last_render_at="bad", interval_seconds=1 / 60, tolerance_seconds=0.0)
        )

    def test_tick_render_plan_skips_stable_frame_and_advances_phase_when_rendering(self):
        skipped = tick_render_plan(visual_data_changed=False, render_frame_due=False, speed=1.0)
        self.assertFalse(skipped.should_render)
        self.assertEqual(skipped.scroll_phase_delta, 0.0)
        self.assertEqual(skipped.ambient_phase_delta, 0.0)

        due = tick_render_plan(visual_data_changed=False, render_frame_due=True, speed=2.0)
        self.assertTrue(due.should_render)
        self.assertAlmostEqual(due.scroll_phase_delta, 0.034, delta=0.0001)
        self.assertAlmostEqual(due.ambient_phase_delta, 0.048, delta=0.0001)

        changed = tick_render_plan(visual_data_changed=True, render_frame_due=False, speed=1.0)
        self.assertTrue(changed.should_render)
        self.assertAlmostEqual(changed.scroll_phase_delta, 0.017, delta=0.0001)
        self.assertAlmostEqual(changed.ambient_phase_delta, 0.024, delta=0.0001)

    def test_tick_frame_execution_plan_separates_clock_retune_from_render_work(self):
        skipped = tick_frame_execution_plan(
            active_visual_plan=ActiveVisualAnimationPlan(active=False),
            render_plan=TickRenderPlan(should_render=False),
            update_music_wave_due=False,
        )

        self.assertFalse(skipped.active_animation)
        self.assertEqual(skipped.active_reason, "")
        self.assertTrue(skipped.retune_frame_timer)
        self.assertFalse(skipped.should_render)
        self.assertFalse(skipped.update_music_wave)
        self.assertFalse(skipped.mark_render_frame)
        self.assertFalse(skipped.update_widget)

        wave_due_without_render = tick_frame_execution_plan(
            active_visual_plan=ActiveVisualAnimationPlan(active=False),
            render_plan=TickRenderPlan(should_render=False),
            update_music_wave_due=True,
        )

        self.assertFalse(wave_due_without_render.should_render)
        self.assertTrue(wave_due_without_render.update_music_wave)
        self.assertFalse(wave_due_without_render.mark_render_frame)
        self.assertFalse(wave_due_without_render.update_widget)

        due = tick_frame_execution_plan(
            active_visual_plan=ActiveVisualAnimationPlan(active=True, reason="media"),
            render_plan=TickRenderPlan(
                should_render=True,
                scroll_phase_delta=0.25,
                ambient_phase_delta=0.5,
            ),
        )

        self.assertTrue(due.active_animation)
        self.assertEqual(due.active_reason, "media")
        self.assertTrue(due.retune_frame_timer)
        self.assertTrue(due.should_render)
        self.assertAlmostEqual(due.scroll_phase_delta, 0.25, delta=0.0001)
        self.assertAlmostEqual(due.ambient_phase_delta, 0.5, delta=0.0001)
        self.assertTrue(due.update_music_wave)
        self.assertTrue(due.mark_render_frame)
        self.assertTrue(due.update_widget)

    def test_tick_frame_execution_plan_can_render_without_advancing_music_wave(self):
        plan = tick_frame_execution_plan(
            active_visual_plan=ActiveVisualAnimationPlan(active=True, reason="media"),
            render_plan=TickRenderPlan(should_render=True),
            update_music_wave_due=False,
        )

        self.assertTrue(plan.should_render)
        self.assertFalse(plan.update_music_wave)
        self.assertTrue(plan.mark_render_frame)
        self.assertTrue(plan.update_widget)

    def test_barrage_hover_clock_plan_advances_clock_only_when_not_hovered(self):
        hovered = barrage_hover_clock_plan(overlay_hovered=True, pointer_hovered=False, anim_dt=0.2)
        self.assertTrue(hovered.hovered)
        self.assertEqual(hovered.clock_delta, 0.0)

        pointer = barrage_hover_clock_plan(overlay_hovered=False, pointer_hovered=True, anim_dt=0.2)
        self.assertTrue(pointer.hovered)
        self.assertEqual(pointer.clock_delta, 0.0)

        idle = barrage_hover_clock_plan(overlay_hovered=False, pointer_hovered=False, anim_dt=0.2)
        self.assertFalse(idle.hovered)
        self.assertAlmostEqual(idle.clock_delta, 0.2, delta=0.0001)

        invalid = barrage_hover_clock_plan(overlay_hovered=False, pointer_hovered=False, anim_dt="bad")
        self.assertFalse(invalid.hovered)
        self.assertEqual(invalid.clock_delta, 0.0)

    def test_topmost_reinforcement_due_respects_enabled_interval_and_invalid_values(self):
        self.assertFalse(
            topmost_reinforcement_due(enabled=False, now=10.0, last_reinforced_at=0.0, interval_seconds=1.0)
        )
        self.assertFalse(
            topmost_reinforcement_due(enabled=True, now=10.5, last_reinforced_at=10.0, interval_seconds=1.0)
        )
        self.assertTrue(
            topmost_reinforcement_due(enabled=True, now=11.1, last_reinforced_at=10.0, interval_seconds=1.0)
        )
        self.assertTrue(
            topmost_reinforcement_due(enabled=True, now="bad", last_reinforced_at=10.0, interval_seconds=1.0)
        )

    def test_active_visual_animation_plan_returns_first_active_reason(self):
        idle = active_visual_animation_plan()
        self.assertFalse(idle.active)
        self.assertEqual(idle.reason, "")

        drag = active_visual_animation_plan(
            dragging=True,
            lyric_index_transition_active=True,
        )
        self.assertTrue(drag.active)
        self.assertEqual(drag.reason, "drag")

        lyric = active_visual_animation_plan(
            lyric_index_transition_active=True,
            music_control_press_active=True,
        )
        self.assertTrue(lyric.active)
        self.assertEqual(lyric.reason, "lyric_index_transition")

    def test_scene_suppression_probe_due_uses_refresh_interval(self):
        self.assertFalse(scene_suppression_probe_due(now=10.39, last_checked_at=10.0))
        self.assertTrue(scene_suppression_probe_due(now=10.4, last_checked_at=10.0))
        self.assertTrue(scene_suppression_probe_due(now="bad", last_checked_at=10.0))

    def test_scene_suppressed_tick_plan_respects_frame_due(self):
        skipped = scene_suppressed_tick_plan(render_frame_due=False)
        self.assertFalse(skipped.should_render)

        due = scene_suppressed_tick_plan(render_frame_due=True)
        self.assertTrue(due.should_render)

    def test_scene_suppressed_interaction_reset_plan_is_independent_from_frame_due(self):
        plan = scene_suppressed_interaction_reset_plan()

        self.assertTrue(plan.reset_hover_state)
        self.assertTrue(plan.reset_left_stack)

    def test_scene_suppressed_frame_execution_plan_retunes_without_render_side_effects(self):
        skipped = scene_suppressed_frame_execution_plan(
            active_visual_plan=ActiveVisualAnimationPlan(active=True, reason="plugin_time_module"),
            render_plan=scene_suppressed_tick_plan(render_frame_due=False),
        )

        self.assertTrue(skipped.active_animation)
        self.assertEqual(skipped.active_reason, "plugin_time_module")
        self.assertTrue(skipped.retune_frame_timer)
        self.assertFalse(skipped.should_render)
        self.assertFalse(skipped.mark_render_frame)
        self.assertFalse(skipped.update_widget)

        due = scene_suppressed_frame_execution_plan(
            active_visual_plan=ActiveVisualAnimationPlan(active=True, reason="media"),
            render_plan=scene_suppressed_tick_plan(render_frame_due=True),
        )

        self.assertTrue(due.active_animation)
        self.assertEqual(due.active_reason, "media")
        self.assertTrue(due.retune_frame_timer)
        self.assertTrue(due.should_render)
        self.assertTrue(due.mark_render_frame)
        self.assertTrue(due.update_widget)

    def test_tick_follow_refresh_plan_requires_active_idle_and_due(self):
        self.assertFalse(
            tick_follow_refresh_plan(active=False, drag_candidate=False, refresh_due=True).should_refresh
        )
        self.assertFalse(
            tick_follow_refresh_plan(active=True, drag_candidate=True, refresh_due=True).should_refresh
        )
        self.assertFalse(
            tick_follow_refresh_plan(active=True, drag_candidate=False, refresh_due=False).should_refresh
        )
        self.assertTrue(
            tick_follow_refresh_plan(active=True, drag_candidate=False, refresh_due=True).should_refresh
        )

    def test_lyric_fallback_advance_plan_requires_playing_untimed_multi_line_delay(self):
        self.assertFalse(
            lyric_fallback_advance_plan(
                media_is_playing=False,
                has_timed_lyrics=False,
                lyric_count=2,
                current_index=0,
                lyric_changed_at=10.0,
                now=13.0,
                speed=1.0,
            ).should_advance
        )
        self.assertFalse(
            lyric_fallback_advance_plan(
                media_is_playing=True,
                has_timed_lyrics=True,
                lyric_count=2,
                current_index=0,
                lyric_changed_at=10.0,
                now=13.0,
                speed=1.0,
            ).should_advance
        )
        self.assertFalse(
            lyric_fallback_advance_plan(
                media_is_playing=True,
                has_timed_lyrics=False,
                lyric_count=1,
                current_index=0,
                lyric_changed_at=10.0,
                now=13.0,
                speed=1.0,
            ).should_advance
        )
        self.assertFalse(
            lyric_fallback_advance_plan(
                media_is_playing=True,
                has_timed_lyrics=False,
                lyric_count=2,
                current_index=0,
                lyric_changed_at=10.0,
                now=12.44,
                speed=1.0,
            ).should_advance
        )

        due = lyric_fallback_advance_plan(
            media_is_playing=True,
            has_timed_lyrics=False,
            lyric_count=3,
            current_index=2,
            lyric_changed_at=10.0,
            now=12.46,
            speed=1.0,
        )

        self.assertTrue(due.should_advance)
        self.assertEqual(due.previous_index, 2)
        self.assertEqual(due.next_index, 0)
        self.assertAlmostEqual(due.changed_at, 12.46, delta=0.0001)

    def test_lyric_fallback_advance_plan_scales_delay_by_speed(self):
        slow = lyric_fallback_advance_plan(
            media_is_playing=True,
            has_timed_lyrics=False,
            lyric_count=2,
            current_index=0,
            lyric_changed_at=10.0,
            now=13.0,
            speed=0.5,
        )
        fast = lyric_fallback_advance_plan(
            media_is_playing=True,
            has_timed_lyrics=False,
            lyric_count=2,
            current_index=0,
            lyric_changed_at=10.0,
            now=11.3,
            speed=2.0,
        )

        self.assertFalse(slow.should_advance)
        self.assertTrue(fast.should_advance)
        self.assertEqual(fast.previous_index, 0)
        self.assertEqual(fast.next_index, 1)

    def test_music_lyric_tick_plan_routes_timed_sync_or_fallback_advance(self):
        paused = music_lyric_tick_plan(
            media_is_playing=False,
            has_timed_lyrics=True,
            lyric_count=2,
            current_index=0,
            lyric_changed_at=10.0,
            now=13.0,
            speed=1.0,
        )
        self.assertFalse(paused.sync_timed_lyrics)
        self.assertFalse(paused.fallback_advance.should_advance)

        timed = music_lyric_tick_plan(
            media_is_playing=True,
            has_timed_lyrics=True,
            lyric_count=2,
            current_index=0,
            lyric_changed_at=10.0,
            now=13.0,
            speed=1.0,
        )
        self.assertTrue(timed.sync_timed_lyrics)
        self.assertFalse(timed.fallback_advance.should_advance)

        fallback = music_lyric_tick_plan(
            media_is_playing=True,
            has_timed_lyrics=False,
            lyric_count=2,
            current_index=0,
            lyric_changed_at=10.0,
            now=12.46,
            speed=1.0,
        )
        self.assertFalse(fallback.sync_timed_lyrics)
        self.assertTrue(fallback.fallback_advance.should_advance)
        self.assertEqual(fallback.fallback_advance.previous_index, 0)
        self.assertEqual(fallback.fallback_advance.next_index, 1)

    def test_scene_suppression_update_plan_enters_and_leaves_suppressed_state(self):
        hide_plan = scene_suppression_update_plan(
            current_suppressed=False,
            detected_suppressed=True,
            detected_reason="fullscreen",
            taskbar_compact_active=False,
            current_transition_mode="",
        )

        self.assertTrue(hide_plan.changed)
        self.assertTrue(hide_plan.scene_suppressed)
        self.assertEqual(hide_plan.scene_reason, "fullscreen")
        self.assertEqual(hide_plan.transition_mode, "hide")
        self.assertAlmostEqual(hide_plan.transition_duration, 0.18, delta=0.001)
        self.assertTrue(hide_plan.input_passthrough)
        self.assertTrue(hide_plan.collapse_expanded)
        self.assertTrue(hide_plan.clear_mask)
        self.assertTrue(hide_plan.update)

        show_plan = scene_suppression_update_plan(
            current_suppressed=True,
            detected_suppressed=False,
            detected_reason="",
            taskbar_compact_active=False,
            current_transition_mode="hide",
        )

        self.assertTrue(show_plan.changed)
        self.assertFalse(show_plan.scene_suppressed)
        self.assertEqual(show_plan.scene_reason, "")
        self.assertEqual(show_plan.transition_mode, "show")
        self.assertAlmostEqual(show_plan.transition_duration, 0.14, delta=0.001)
        self.assertFalse(show_plan.input_passthrough)
        self.assertFalse(show_plan.collapse_expanded)

    def test_scene_suppression_update_plan_tasker_ignores_fullscreen_probe(self):
        plan = scene_suppression_update_plan(
            current_suppressed=False,
            detected_suppressed=True,
            detected_reason="fullscreen",
            taskbar_compact_active=True,
            current_transition_mode="hide",
        )

        self.assertFalse(plan.changed)
        self.assertFalse(plan.scene_suppressed)
        self.assertEqual(plan.scene_reason, "")
        self.assertTrue(plan.clear_scene_reason)
        self.assertTrue(plan.clear_transition_mode)

    def test_hover_auto_activation_candidate_requires_matching_armed_key_and_delay(self):
        base = {
            "hovered_key": "plugin",
            "expanded": False,
            "hover_armed_key": "plugin",
            "hover_started_at": 10.0,
            "now": 11.0,
        }

        self.assertTrue(hover_auto_activation_candidate(**base))
        self.assertFalse(hover_auto_activation_candidate(**{**base, "hovered_key": ""}))
        self.assertFalse(hover_auto_activation_candidate(**{**base, "expanded": True}))
        self.assertFalse(hover_auto_activation_candidate(**{**base, "hover_armed_key": "music"}))
        self.assertFalse(hover_auto_activation_candidate(**{**base, "now": 10.99}))

    def test_plugin_panel_hover_open_due_requires_expanded_plugin_hover_and_delay(self):
        base = {
            "expanded": True,
            "hovered_key": "plugin",
            "plugin_panel_open": False,
            "hover_started_at": 10.0,
            "now": 10.5,
        }

        self.assertTrue(plugin_panel_hover_open_due(**base))
        self.assertFalse(plugin_panel_hover_open_due(**{**base, "expanded": False}))
        self.assertFalse(plugin_panel_hover_open_due(**{**base, "hovered_key": "music"}))
        self.assertFalse(plugin_panel_hover_open_due(**{**base, "plugin_panel_open": True}))
        self.assertFalse(plugin_panel_hover_open_due(**{**base, "now": 10.49}))

    def test_left_stack_collapse_due_requires_expanded_left_stack_delay(self):
        base = {
            "expanded": True,
            "left_stack_at": 10.0,
            "now": 10.5,
        }

        self.assertTrue(left_stack_collapse_due(**base))
        self.assertFalse(left_stack_collapse_due(**{**base, "expanded": False}))
        self.assertFalse(left_stack_collapse_due(**{**base, "left_stack_at": 0.0}))
        self.assertFalse(left_stack_collapse_due(**{**base, "now": 10.49}))

    def test_tick_interaction_plan_returns_no_actions_when_nothing_due(self):
        plan = tick_interaction_plan(
            hover_auto_activation_due=False,
            plugin_panel_hover_open_due=False,
            left_stack_collapse_due=False,
            alarm_row_long_press_triggered=False,
        )

        self.assertFalse(plan.activate_hover_key)
        self.assertFalse(plan.clear_hover_armed_key)
        self.assertFalse(plan.open_plugin_panel)
        self.assertFalse(plan.pulse_after_plugin_open)
        self.assertFalse(plan.collapse_left_stack)
        self.assertFalse(plan.clear_left_stack)
        self.assertFalse(plan.pulse_after_left_collapse)
        self.assertFalse(plan.render_after_alarm_long_press)
        self.assertFalse(plan.stop_after_alarm_long_press)

    def test_tick_interaction_plan_preserves_independent_tick_actions(self):
        plan = tick_interaction_plan(
            hover_auto_activation_due=True,
            plugin_panel_hover_open_due=True,
            left_stack_collapse_due=True,
            alarm_row_long_press_triggered=True,
        )

        self.assertTrue(plan.activate_hover_key)
        self.assertTrue(plan.clear_hover_armed_key)
        self.assertTrue(plan.open_plugin_panel)
        self.assertTrue(plan.pulse_after_plugin_open)
        self.assertTrue(plan.collapse_left_stack)
        self.assertTrue(plan.clear_left_stack)
        self.assertTrue(plan.pulse_after_left_collapse)
        self.assertTrue(plan.render_after_alarm_long_press)
        self.assertTrue(plan.stop_after_alarm_long_press)

    def test_music_wave_update_plan_damps_idle_non_live_bands_only(self):
        idle = music_wave_update_plan(
            media_is_playing=False,
            input_snapshot=music_wave_input_snapshot(
                sampled_bands=(0.8, 0.5, 0.25),
                live_audio_available=False,
            ),
        )

        self.assertFalse(idle.live_audio_available)
        self.assertAlmostEqual(idle.current_audio_bands[0], 0.192, delta=0.0001)
        self.assertAlmostEqual(idle.current_audio_bands[1], 0.09, delta=0.0001)
        self.assertAlmostEqual(idle.current_audio_bands[2], 0.03, delta=0.0001)

        playing = music_wave_update_plan(
            media_is_playing=True,
            input_snapshot=music_wave_input_snapshot(
                sampled_bands=(0.8, 0.5, 0.25),
                live_audio_available=False,
            ),
        )
        live = music_wave_update_plan(
            media_is_playing=False,
            input_snapshot=music_wave_input_snapshot(
                sampled_bands=(0.8, 0.5, 0.25),
                live_audio_available=True,
            ),
        )

        self.assertEqual(playing.current_audio_bands, (0.8, 0.5, 0.25))
        self.assertEqual(live.current_audio_bands, (0.8, 0.5, 0.25))
        self.assertTrue(live.live_audio_available)

    def test_music_wave_update_plan_coerces_invalid_bands_to_silence(self):
        snapshot = music_wave_input_snapshot(
            sampled_bands=("bad", 0.5, 0.25),
            live_audio_available=True,
        )
        plan = music_wave_update_plan(
            media_is_playing=True,
            input_snapshot=snapshot,
        )

        self.assertTrue(snapshot.live_audio_available)
        self.assertEqual(snapshot.sampled_bands, (0.0, 0.0, 0.0))
        self.assertEqual(plan.current_audio_bands, (0.0, 0.0, 0.0))

    def test_music_wave_update_due_uses_independent_cadence(self):
        self.assertTrue(music_wave_update_due(now=10.0, previous_updated_at=0.0))
        self.assertFalse(music_wave_update_due(now=10.010, previous_updated_at=10.0))
        self.assertTrue(music_wave_update_due(now=10.032, previous_updated_at=10.0))
        self.assertFalse(
            music_wave_update_due(
                now=10.032,
                previous_updated_at=10.0,
                tolerance_seconds=0.0,
            )
        )
        self.assertTrue(music_wave_update_due(now="bad", previous_updated_at=10.0))

    def test_music_wave_cadence_diagnostics_reports_wave_rate_against_render_rate(self):
        diagnostics = music_wave_cadence_diagnostics(
            now=13.0,
            first_updated_at=10.0,
            last_updated_at=13.0,
            update_count=91,
            visual_frame_diagnostics={"frame_count": 180, "average_fps": 60.0},
        ).as_dict()

        self.assertEqual(diagnostics["status"], "ready")
        self.assertEqual(diagnostics["update_count"], 91)
        self.assertAlmostEqual(diagnostics["estimated_update_fps"], 30.333, delta=0.001)
        self.assertAlmostEqual(diagnostics["render_average_fps"], 60.0, delta=0.001)
        self.assertAlmostEqual(diagnostics["wave_to_render_fps_ratio"], 0.506, delta=0.001)
        self.assertTrue(diagnostics["cadence_limited"])
        self.assertFalse(diagnostics["update_due"])

    def test_music_wave_cadence_diagnostics_handles_missing_wave_samples(self):
        diagnostics = music_wave_cadence_diagnostics(
            now=13.0,
            first_updated_at=0.0,
            last_updated_at=0.0,
            update_count=0,
            visual_frame_diagnostics={"frame_count": 10, "average_fps": 60.0},
        ).as_dict()

        self.assertEqual(diagnostics["status"], "missing")
        self.assertTrue(diagnostics["update_due"])
        self.assertIsNone(diagnostics["update_age_ms"])
        self.assertEqual(diagnostics["estimated_update_fps"], 0.0)

    def test_music_background_layer_paint_plan_excludes_content_transition_and_border(self):
        plan = music_background_layer_paint_plan(expanded=False, ambience_style="sea_level")

        self.assertEqual(plan.ambience_style, "sea_level")
        self.assertEqual(plan.steps, ("glass_body", "bottom_ambience", "blur_mask", "light_wave"))
        self.assertNotIn("reactive_overlay", plan.steps)
        self.assertNotIn("surface_transition", plan.steps)
        self.assertNotIn("compact_content", plan.steps)
        self.assertNotIn("border", plan.steps)

    def test_music_background_layer_paint_plan_preserves_style_specific_layers(self):
        duotone = music_background_layer_paint_plan(expanded=True, ambience_style="duotone_full")
        bilateral = music_background_layer_paint_plan(expanded=True, ambience_style="bilateral_diffraction")
        yaoguang = music_background_layer_paint_plan(expanded=True, ambience_style="yaoguang")

        self.assertEqual(duotone.steps, ("glass_body", "bottom_ambience", "light_wave"))
        self.assertEqual(bilateral.steps, ("pure_black_background", "bilateral_diffraction_ambience"))
        self.assertEqual(yaoguang.steps, ("yaoguang_expanded_background",))

    def test_music_surface_paint_plan_routes_dual_panel_as_separate_surface(self):
        plan = music_surface_paint_plan(
            expanded=True,
            ambience_style="sea_level",
            expanded_layout_style="dual_panel",
            lyric_color_fill_paints_background=False,
        )

        self.assertEqual(plan.ambience_style, "sea_level")
        self.assertEqual(plan.steps, ("dual_panel_background", "expanded_content", "border"))

    def test_music_surface_paint_plan_routes_native_layered_backgrounds(self):
        sea = music_surface_paint_plan(expanded=False, ambience_style="sea_level")
        duotone = music_surface_paint_plan(expanded=True, ambience_style="duotone_full")

        self.assertEqual(
            sea.steps,
            (
                "glass_body",
                "bottom_ambience",
                "blur_mask",
                "light_wave",
                "reactive_overlay",
                "surface_transition",
                "compact_content",
                "border",
            ),
        )
        self.assertEqual(
            duotone.steps,
            (
                "glass_body",
                "bottom_ambience",
                "light_wave",
                "reactive_overlay",
                "surface_transition",
                "expanded_content",
                "border",
            ),
        )

    def test_music_surface_paint_plan_routes_solid_and_yaoguang_backgrounds(self):
        pure_white = music_surface_paint_plan(expanded=False, ambience_style="pure_white")
        solid_album = music_surface_paint_plan(expanded=True, ambience_style="solid_album")
        yaoguang = music_surface_paint_plan(expanded=False, ambience_style="yaoguang")

        self.assertEqual(pure_white.steps[0], "pure_white_background")
        self.assertEqual(solid_album.steps[0], "solid_album_background")
        self.assertEqual(yaoguang.steps[0], "yaoguang_compact_background")
        self.assertIn("yaoguang_compact_frame_overlay", yaoguang.steps)

    def test_music_surface_paint_plan_routes_bilateral_and_color_fill_to_black_surface(self):
        bilateral = music_surface_paint_plan(expanded=False, ambience_style="bilateral_diffraction")
        color_fill = music_surface_paint_plan(
            expanded=False,
            ambience_style="sea_level",
            lyric_color_fill_paints_background=True,
        )

        self.assertEqual(
            bilateral.steps[:2],
            ("pure_black_background", "bilateral_diffraction_ambience"),
        )
        self.assertEqual(color_fill.steps[0], "pure_black_background")
        self.assertNotIn("bottom_ambience", color_fill.steps)

    def test_empty_island_display_mode_aliases_legacy_values(self):
        self.assertEqual(normalize_empty_island_display_mode("hidden_bar"), "visible_bar")
        self.assertEqual(normalize_empty_island_display_mode("show bar"), "visible_bar")
        self.assertEqual(normalize_empty_island_display_mode("specified-text"), "text")
        self.assertEqual(normalize_empty_island_display_mode("clock"), "time")
        self.assertEqual(normalize_empty_island_display_mode("unknown"), "visible_bar")

    def test_no_music_priority_aliases_legacy_values(self):
        self.assertEqual(normalize_no_music_island_priority("plugin-panel"), "plugin")
        self.assertEqual(normalize_no_music_island_priority("plugins"), "plugin")
        self.assertEqual(normalize_no_music_island_priority("empty island"), "empty")
        self.assertEqual(normalize_no_music_island_priority("none"), "empty")
        self.assertEqual(normalize_no_music_island_priority("unknown"), "plugin")

    def test_fullscreen_plugin_alert_style_aliases_legacy_values(self):
        self.assertEqual(normalize_fullscreen_plugin_alert_style("island"), "toast")
        self.assertEqual(normalize_fullscreen_plugin_alert_style("popup"), "toast")
        self.assertEqual(normalize_fullscreen_plugin_alert_style("pop"), "toast")
        self.assertEqual(normalize_fullscreen_plugin_alert_style("barrage"), "barrage")
        self.assertEqual(normalize_fullscreen_plugin_alert_style("fullscreen"), "fullscreen")
        self.assertEqual(normalize_fullscreen_plugin_alert_style("unknown"), "toast")

    def test_barrage_display_mode_aliases_legacy_values(self):
        self.assertEqual(normalize_barrage_display_mode("any"), "always")
        self.assertEqual(normalize_barrage_display_mode("anytime"), "always")
        self.assertEqual(normalize_barrage_display_mode("all"), "always")
        self.assertEqual(normalize_barrage_display_mode("on"), "always")
        self.assertEqual(normalize_barrage_display_mode("full"), "fullscreen")
        self.assertEqual(normalize_barrage_display_mode("only_fullscreen"), "fullscreen")
        self.assertEqual(normalize_barrage_display_mode("unknown"), "fullscreen")

    def test_barrage_preview_active_requires_messages_and_respects_settings_preview(self):
        self.assertFalse(
            barrage_preview_active(
                previews_present=False,
                settings_window_active=True,
                now=20.0,
                force_anytime_until=0.0,
                last_preview_at=20.0,
            )
        )
        self.assertTrue(
            barrage_preview_active(
                previews_present=True,
                settings_window_active=True,
                now=999.0,
                force_anytime_until=0.0,
                last_preview_at=0.0,
            )
        )

    def test_barrage_preview_active_uses_force_window_then_recent_preview_window(self):
        self.assertTrue(
            barrage_preview_active(
                previews_present=True,
                settings_window_active=False,
                now=20.0,
                force_anytime_until=20.5,
                last_preview_at=0.0,
            )
        )
        self.assertTrue(
            barrage_preview_active(
                previews_present=True,
                settings_window_active=False,
                now=20.0,
                force_anytime_until=0.0,
                last_preview_at=14.0,
            )
        )
        self.assertFalse(
            barrage_preview_active(
                previews_present=True,
                settings_window_active=False,
                now=20.3,
                force_anytime_until=0.0,
                last_preview_at=14.0,
            )
        )

    def test_barrage_overlay_context_active_separates_preview_always_and_fullscreen_modes(self):
        self.assertTrue(
            barrage_overlay_context_active(
                preview_active=True,
                display_mode="fullscreen",
                fullscreen_overlay_active=False,
            )
        )
        self.assertTrue(
            barrage_overlay_context_active(
                preview_active=False,
                display_mode="always",
                fullscreen_overlay_active=False,
            )
        )
        self.assertTrue(
            barrage_overlay_context_active(
                preview_active=False,
                display_mode="fullscreen",
                fullscreen_overlay_active=True,
            )
        )
        self.assertFalse(
            barrage_overlay_context_active(
                preview_active=False,
                display_mode="fullscreen",
                fullscreen_overlay_active=False,
            )
        )

    def test_active_barrage_messages_prioritizes_preview_without_dropping_fullscreen_alerts(self):
        normal = BMessage("normal", 10.0)
        preview = BMessage("preview", 10.0)
        fullscreen = BMessage("fullscreen", 10.0)

        self.assertEqual(
            active_barrage_messages(
                preview_active=True,
                messages=[normal],
                previews=[preview],
                fullscreen_messages=[fullscreen],
            ),
            [preview, fullscreen],
        )
        self.assertEqual(
            active_barrage_messages(
                preview_active=False,
                messages=[normal],
                previews=[preview],
                fullscreen_messages=[fullscreen],
            ),
            [normal, preview, fullscreen],
        )

    def test_barrage_alive_messages_for_clock_prunes_old_messages(self):
        recent = BMessage("recent", 7.0)
        borderline = BMessage("borderline", 6.0)
        expired = BMessage("expired", 5.9)

        self.assertEqual(
            barrage_alive_messages_for_clock([recent, borderline, expired], 20.0),
            [recent, borderline],
        )

    def test_barrage_overlay_state_uses_preview_key_and_message_snapshot(self):
        normal = BMessage("normal", 8.0)
        preview = BMessage("preview", 8.0)
        fullscreen = BMessage("fullscreen", 8.0)

        state = barrage_overlay_state(
            preview_only=False,
            preview_active=True,
            context_active=True,
            messages=[normal],
            previews=[preview],
            fullscreen_messages=[fullscreen],
            clock=10.0,
        )

        self.assertEqual(state["style"], "barrage")
        self.assertEqual(state["alert_key"], "preview-barrage:2")
        self.assertEqual(state["messages"], [preview, fullscreen])
        self.assertEqual(state["clock"], 10.0)

    def test_barrage_overlay_state_respects_preview_only_and_context_gates(self):
        preview = BMessage("preview", 8.0)
        normal = BMessage("normal", 8.0)

        self.assertIsNone(
            barrage_overlay_state(
                preview_only=True,
                preview_active=False,
                context_active=True,
                messages=[],
                previews=[preview],
                fullscreen_messages=[],
                clock=10.0,
            )
        )
        self.assertIsNone(
            barrage_overlay_state(
                preview_only=False,
                preview_active=False,
                context_active=False,
                messages=[normal],
                previews=[],
                fullscreen_messages=[],
                clock=10.0,
            )
        )
        state = barrage_overlay_state(
            preview_only=False,
            preview_active=False,
            context_active=True,
            messages=[normal],
            previews=[],
            fullscreen_messages=[],
            clock=10.0,
        )
        self.assertEqual(state["alert_key"], "barrage:1")
        self.assertEqual(state["messages"], [normal])

    def test_fullscreen_plugin_barrage_update_action_clears_when_context_style_or_alert_missing(self):
        base = {
            "context_active": True,
            "alert_style": "barrage",
            "has_alert": True,
            "alert_key": "time|done",
            "previous_alert_key": "",
            "has_existing_messages": False,
        }

        self.assertEqual(
            fullscreen_plugin_barrage_update_action(**{**base, "context_active": False}),
            "clear",
        )
        self.assertEqual(
            fullscreen_plugin_barrage_update_action(**{**base, "alert_style": "toast"}),
            "clear",
        )
        self.assertEqual(
            fullscreen_plugin_barrage_update_action(**{**base, "has_alert": False}),
            "clear",
        )

    def test_fullscreen_plugin_barrage_update_action_keeps_or_replaces_existing_burst(self):
        base = {
            "context_active": True,
            "alert_style": "barrage",
            "has_alert": True,
            "alert_key": "time|done",
            "previous_alert_key": "time|done",
            "has_existing_messages": True,
        }

        self.assertEqual(fullscreen_plugin_barrage_update_action(**base), "keep")
        self.assertEqual(
            fullscreen_plugin_barrage_update_action(**{**base, "previous_alert_key": "weather|rain"}),
            "replace",
        )
        self.assertEqual(
            fullscreen_plugin_barrage_update_action(**{**base, "has_existing_messages": False}),
            "replace",
        )
        self.assertEqual(
            fullscreen_plugin_barrage_update_action(**{**base, "alert_key": ""}),
            "replace",
        )

    def test_fullscreen_plugin_alert_overlay_state_builds_candidate_and_filters_suppressed(self):
        alert = (
            0,
            "TIME",
            "0:00",
            "Countdown 0:00",
            "yellow",
            "",
            "time",
            "done",
            "timer done",
            "click",
            5,
        )
        alert_key = fullscreen_plugin_alert_key(alert)

        state = fullscreen_plugin_alert_overlay_state(
            context_active=True,
            alerts=[alert],
            style="island",
        )

        self.assertEqual(state["style"], "toast")
        self.assertEqual(state["alert"], alert)
        self.assertEqual(state["action_key"], "plugin-alert:time")
        self.assertEqual(state["alert_key"], alert_key)
        self.assertIsNone(
            fullscreen_plugin_alert_overlay_state(
                context_active=True,
                alerts=[alert],
                style="toast",
                suppressed_alert_keys=[alert_key],
            )
        )

    def test_fullscreen_plugin_alert_overlay_state_requires_context_alert_and_action_key(self):
        self.assertIsNone(
            fullscreen_plugin_alert_overlay_state(
                context_active=False,
                alerts=[("short",)],
                style="toast",
            )
        )
        self.assertIsNone(
            fullscreen_plugin_alert_overlay_state(
                context_active=True,
                alerts=[],
                style="toast",
            )
        )
        self.assertIsNone(
            fullscreen_plugin_alert_overlay_state(
                context_active=True,
                alerts=[(0, "", "", "", "", "", "", "done", "", "", 5)],
                style="toast",
            )
        )

    def test_plugin_alert_overlay_state_hides_barrage_style_and_copies_toast_state(self):
        toast = {"style": "toast", "action_key": "plugin-alert:time", "alert_key": "time"}
        barrage = {"style": "barrage", "action_key": "plugin-alert:time", "alert_key": "time"}

        self.assertEqual(plugin_alert_overlay_state(toast), toast)
        self.assertIsNot(plugin_alert_overlay_state(toast), toast)
        self.assertIsNone(plugin_alert_overlay_state(barrage))
        self.assertIsNone(plugin_alert_overlay_state(None))

    def test_message_identity_and_hit_keys_are_stable_plain_rules(self):
        serial_message = BMessage("serial", 0.0, serial=7, app="WeChat", sender="Alice")
        fallback_message = BMessage("fallback", 0.0, created_at=12.345, app="QQ", sender="Bob")

        self.assertEqual(message_identity_key(serial_message), "7-WeChat-Alice")
        self.assertEqual(message_identity_key(fallback_message), "12345-QQ-Bob")
        self.assertEqual(message_key_from_hit("message:7-WeChat-Alice"), "7-WeChat-Alice")
        self.assertEqual(message_key_from_hit("message-dot:7-WeChat-Alice"), "7-WeChat-Alice")
        self.assertEqual(message_key_from_hit("plugin-alert:time"), "")
        self.assertTrue(message_hit_key("message:7-WeChat-Alice"))
        self.assertTrue(message_hit_key("message-dot:7-WeChat-Alice"))
        self.assertFalse(message_hit_key("plugin-alert:time"))

    def test_barrage_action_key_for_message_prefers_explicit_action_key(self):
        explicit = BMessage("explicit", 0.0, action_key="plugin-alert:time")
        fallback = BMessage("fallback", 0.0, serial=3, app="DingTalk", sender="Team")

        self.assertEqual(barrage_action_key_for_message(explicit), "plugin-alert:time")
        self.assertEqual(barrage_action_key_for_message(fallback), "message:3-DingTalk-Team")

    def test_fullscreen_plugin_alert_key_and_action_key_use_stable_alert_identity(self):
        alert = (
            0,
            "TIME",
            "0:00",
            "Countdown 0:00",
            "yellow",
            "",
            "time",
            "done",
            "timer done",
            "click to review or clear",
            5,
        )

        self.assertEqual(
            fullscreen_plugin_alert_key(alert),
            "time|TIME|0:00|Countdown 0:00|done|timer done|click to review or clear|5",
        )
        self.assertEqual(fullscreen_plugin_alert_action_key(alert), "plugin-alert:time")
        self.assertEqual(fullscreen_plugin_alert_key(("short",)), "")
        self.assertEqual(fullscreen_plugin_alert_action_key(("short",)), "")

    def test_fullscreen_plugin_alert_action_key_falls_back_to_label(self):
        alert = (
            0,
            "WEATHER",
            "Rain",
            "Storm",
            "blue",
            "",
            "",
            "severe",
            "Warning",
            "Heavy rain",
            3,
        )

        self.assertEqual(fullscreen_plugin_alert_action_key(alert), "plugin-alert:weather")

    def test_fullscreen_plugin_alert_reminder_filters_status_by_plugin_kind(self):
        time_done = (0, "TIME", "0:00", "Timer", "yellow", "", "time", "done", "", "", 5)
        time_running = (0, "TIME", "0:12", "Timer", "yellow", "", "time", "running", "", "", 30)
        weather_severe = (0, "WEATHER", "Rain", "Storm", "blue", "", "weather", "severe", "", "", 5)
        agenda_alert_body = (0, "AGENDA", "Soon", "Meeting", "green", "", "agenda", "normal", "", "Room 1", 5)

        self.assertTrue(fullscreen_plugin_alert_is_reminder(time_done))
        self.assertFalse(fullscreen_plugin_alert_is_reminder(time_running))
        self.assertTrue(fullscreen_plugin_alert_is_reminder(weather_severe))
        self.assertFalse(fullscreen_plugin_alert_is_reminder(agenda_alert_body))
        self.assertFalse(fullscreen_plugin_alert_is_reminder(("short",)))

    def test_fullscreen_overlay_context_uses_preview_settings_scene_and_probe_reason(self):
        base = {
            "preview_active": False,
            "settings_window_active": False,
            "scene_suppressed": False,
            "last_scene_probe_reason": "",
            "scene_reason": "",
        }

        self.assertFalse(fullscreen_overlay_context_active(**base))
        self.assertTrue(fullscreen_overlay_context_active(**{**base, "preview_active": True}))
        self.assertTrue(fullscreen_overlay_context_active(**{**base, "settings_window_active": True}))
        self.assertTrue(fullscreen_overlay_context_active(**{**base, "scene_suppressed": True}))
        self.assertTrue(fullscreen_overlay_context_active(**{**base, "last_scene_probe_reason": "fullscreen"}))
        self.assertTrue(fullscreen_overlay_context_active(**{**base, "last_scene_probe_reason": "maximized"}))
        self.assertTrue(fullscreen_overlay_context_active(**{**base, "scene_reason": "settings"}))
        self.assertFalse(fullscreen_overlay_context_active(**{**base, "last_scene_probe_reason": "windowed"}))

    def test_fullscreen_plugin_alert_suppression_state_prunes_expired_and_invalid_entries(self):
        suppressed, kept = fullscreen_plugin_alert_suppression_state(
            {
                "active": 15.0,
                "expired": 9.5,
                "bad": "not-a-time",
                42: 30.0,
            },
            "active",
            10.0,
        )

        self.assertTrue(suppressed)
        self.assertEqual(kept, {"active": 15.0, "42": 30.0})

    def test_fullscreen_plugin_alert_suppression_state_handles_missing_key_and_invalid_table(self):
        suppressed, kept = fullscreen_plugin_alert_suppression_state({"other": 15.0}, "target", 10.0)

        self.assertFalse(suppressed)
        self.assertEqual(kept, {"other": 15.0})
        self.assertEqual(fullscreen_plugin_alert_suppression_state([], "target", 10.0), (False, {}))
        self.assertEqual(fullscreen_plugin_alert_suppression_state({"target": 20.0}, "", 10.0), (False, {}))

    def test_empty_island_can_display_respects_music_and_plugin_priority(self):
        self.assertFalse(empty_island_can_display(music_enabled=True, plugin_enabled=False, no_music_priority="empty"))
        self.assertTrue(empty_island_can_display(music_enabled=False, plugin_enabled=False, no_music_priority="plugin"))
        self.assertFalse(empty_island_can_display(music_enabled=False, plugin_enabled=True, no_music_priority="plugin"))
        self.assertTrue(empty_island_can_display(music_enabled=False, plugin_enabled=True, no_music_priority="empty"))

    def test_empty_island_text_and_time_modes_require_matching_display_mode(self):
        facts = {"music_enabled": False, "plugin_enabled": True, "no_music_priority": "empty"}

        self.assertTrue(empty_island_text_active(display_mode="text", **facts))
        self.assertFalse(empty_island_text_active(display_mode="time", **facts))
        self.assertTrue(empty_island_time_active(display_mode="clock", **facts))
        self.assertFalse(empty_island_time_active(display_mode="text", **facts))
        self.assertFalse(
            empty_island_text_active(
                display_mode="text",
                music_enabled=False,
                plugin_enabled=True,
                no_music_priority="plugin",
            )
        )

    def test_main_island_kind_prioritizes_music_then_plugin_by_default(self):
        self.assertEqual(
            main_island_kind(
                music_enabled=True,
                plugin_enabled=True,
                no_music_priority="empty",
                empty_display_mode="text",
                messages_present=True,
            ),
            "music",
        )
        self.assertEqual(
            main_island_kind(
                music_enabled=False,
                plugin_enabled=True,
                no_music_priority="plugin",
                empty_display_mode="time",
                messages_present=True,
            ),
            "plugin",
        )

    def test_main_island_kind_allows_empty_modes_to_override_plugin_when_configured(self):
        self.assertEqual(
            main_island_kind(
                music_enabled=False,
                plugin_enabled=True,
                no_music_priority="empty",
                empty_display_mode="specified_text",
                messages_present=True,
            ),
            "empty_text",
        )
        self.assertEqual(
            main_island_kind(
                music_enabled=False,
                plugin_enabled=True,
                no_music_priority="empty",
                empty_display_mode="clock",
                messages_present=True,
            ),
            "time",
        )
        self.assertEqual(
            main_island_kind(
                music_enabled=False,
                plugin_enabled=True,
                no_music_priority="empty",
                empty_display_mode="visible_bar",
                messages_present=True,
            ),
            "thin_bar",
        )

    def test_main_island_kind_falls_back_to_message_then_thin_bar_without_music_or_plugins(self):
        self.assertEqual(
            main_island_kind(
                music_enabled=False,
                plugin_enabled=False,
                no_music_priority="plugin",
                empty_display_mode="visible_bar",
                messages_present=True,
            ),
            "message",
        )
        self.assertEqual(
            main_island_kind(
                music_enabled=False,
                plugin_enabled=False,
                no_music_priority="plugin",
                empty_display_mode="visible_bar",
                messages_present=False,
            ),
            "thin_bar",
        )

    def test_media_visual_track_changed_ignores_message_refresh_id_churn_for_same_visible_track(self):
        self.assertFalse(
            media_visual_track_changed(
                initial=False,
                incoming_track_id="new-id",
                previous_track_id="old-id",
                same_visible_track=True,
                message_refresh=True,
            )
        )
        self.assertTrue(
            media_visual_track_changed(
                initial=False,
                incoming_track_id="new-id",
                previous_track_id="old-id",
                same_visible_track=False,
                message_refresh=True,
            )
        )
        self.assertTrue(
            media_visual_track_changed(
                initial=True,
                incoming_track_id="new-id",
                previous_track_id="",
                same_visible_track=True,
                message_refresh=True,
            )
        )

    def test_message_refresh_cannot_hide_a_distinct_media_generation(self):
        self.assertTrue(
            media_visual_track_changed(
                initial=False,
                incoming_track_id="same-title-new-version",
                previous_track_id="same-title-old-version",
                same_visible_track=True,
                message_refresh=True,
                same_media_generation=False,
            )
        )

    def test_folded_message_limit_allows_more_when_messages_own_the_island(self):
        self.assertEqual(
            folded_message_limit(
                taskbar_compact_active=False,
                main_kind="message",
                music_enabled=False,
                plugin_enabled=False,
            ),
            5,
        )
        self.assertEqual(
            folded_message_limit(
                taskbar_compact_active=True,
                main_kind="message",
                music_enabled=False,
                plugin_enabled=False,
            ),
            3,
        )
        self.assertEqual(
            folded_message_limit(
                taskbar_compact_active=False,
                main_kind="message",
                music_enabled=True,
                plugin_enabled=False,
            ),
            3,
        )
        self.assertEqual(
            folded_message_limit(
                taskbar_compact_active=False,
                main_kind="plugin",
                music_enabled=False,
                plugin_enabled=False,
            ),
            3,
        )


if __name__ == "__main__":
    unittest.main()
