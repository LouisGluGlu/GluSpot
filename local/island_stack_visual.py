# -*- coding: utf-8 -*-
"""
Local visual renderer for GluSpot.

Controls:
  M       Toggle music island
  F       Toggle Finder simulation / standalone mode
  Space   Expand / collapse island stack
  Esc     Quit
"""

from __future__ import annotations

import json
import math
import random
import re
import sys
import threading
import time
import ctypes
import hashlib
import unicodedata
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta
from ctypes import wintypes
from pathlib import Path
from dataclasses import dataclass, field, replace

ROOT_DIR = Path(__file__).resolve().parents[1]
MUSIC_CONTROL_ICON_DIR = ROOT_DIR / "assets" / "music_controls"
TIME_REMINDER_ICON_DIR = ROOT_DIR / "assets" / "logos"
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontDatabase,
    QFontInfo,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPalette,
    QRadialGradient,
    QRegion,
    QTextLayout,
)
try:
    from PySide6.QtSvg import QSvgRenderer
except Exception:
    QSvgRenderer = None
from PySide6.QtWidgets import QApplication, QWidget

from api.lyric_timeline import (
    active_index_for_position,
    active_index_near_position,
    lyric_timeline_diagnostics,
)
from api.models import IslandVisualData
from api.providers import EmptyIslandDataProvider
from local.audio_reactive_engine import (
    AudioReactiveRuntime,
    ReactiveFrame,
    ReactiveSnapshot,
    audio_band_sample_plan,
    audio_band_sampling_request_plan,
    live_audio_availability_request_plan,
)
from local.album_art_pipeline import album_art_bytes_signature, album_art_update_plan
from local.album_island_projection import album_cover_projection
from local.music_lyric_motion import GLYPH_FLOAT_LIFT, LogicalLineSpringChain, MusicLyricMotion
from local.music_lyric_reading import (
    LyricReadingRenderer,
    apply_cjk_letter_spacing,
    reading_unit_offsets,
    utf16_slice,
)
from local.music_lyric_presentation import (
    INTRO_COUNTDOWN_TEXT,
    analyze_duet_lyric_rows,
    present_lyric_text,
    present_timed_lyric_rows,
)
from local.lyric_unit_timing import (
    estimated_row_read_end,
    estimated_row_reading_progress,
    genuinely_timed_unit_row,
    real_unit_row_finished_before_next_start,
    song_has_genuine_unit_timing,
    timed_unit_reading_front,
)
from local.branding import APP_NAME, APP_VERSION
from local.liquid_glass import paint_liquid_glass
from local.control_hover import HoverMotion, advance_neighbor_spring, hover_scale, neighbor_displacement
from local.diagnostics_export import (
    build_diagnostics_export_payload,
    default_diagnostics_export_path,
    write_diagnostics_export_file,
)
from local.island_diagnostics import VisualRuntimeDiagnostics, VisibleMediaDiagnostics, build_island_provider_diagnostics
from local.runtime_diagnostics import RuntimeDiagnosticsTraceRecorder
from local.runtime_verification import build_runtime_verification_report
from local.island_presentation import (
    active_barrage_messages as presentation_active_barrage_messages,
    active_visual_animation_plan as presentation_active_visual_animation_plan,
    animation_dict_active as presentation_animation_dict_active,
    animation_duration as presentation_animation_duration,
    animation_speed_factor as presentation_animation_speed_factor,
    animation_started_active as presentation_animation_started_active,
    animation_tick_delta_seconds as presentation_animation_tick_delta_seconds,
    barrage_alive_messages_for_clock as presentation_barrage_alive_messages_for_clock,
    barrage_action_key_for_message as presentation_barrage_action_key_for_message,
    barrage_hover_clock_plan as presentation_barrage_hover_clock_plan,
    barrage_overlay_context_active as presentation_barrage_overlay_context_active,
    barrage_overlay_state as presentation_barrage_overlay_state,
    barrage_preview_active as presentation_barrage_preview_active,
    empty_island_can_display as presentation_empty_island_can_display,
    empty_island_text_active as presentation_empty_island_text_active,
    empty_island_time_active as presentation_empty_island_time_active,
    folded_message_limit as presentation_folded_message_limit,
    fullscreen_plugin_alert_action_key as presentation_fullscreen_plugin_alert_action_key,
    fullscreen_plugin_barrage_update_action as presentation_fullscreen_plugin_barrage_update_action,
    fullscreen_plugin_alert_overlay_state as presentation_fullscreen_plugin_alert_overlay_state,
    fullscreen_plugin_alert_is_reminder as presentation_fullscreen_plugin_alert_is_reminder,
    fullscreen_plugin_alert_key as presentation_fullscreen_plugin_alert_key,
    fullscreen_plugin_alert_suppression_state as presentation_fullscreen_plugin_alert_suppression_state,
    fullscreen_overlay_context_active as presentation_fullscreen_overlay_context_active,
    hover_auto_activation_candidate as presentation_hover_auto_activation_candidate,
    left_stack_collapse_due as presentation_left_stack_collapse_due,
    media_visual_track_changed as presentation_media_visual_track_changed,
    lyric_fallback_advance_plan as presentation_lyric_fallback_advance_plan,
    main_island_kind as presentation_main_island_kind,
    message_hit_key as presentation_message_hit_key,
    message_identity_key as presentation_message_identity_key,
    message_key_from_hit as presentation_message_key_from_hit,
    music_background_layer_paint_plan as presentation_music_background_layer_paint_plan,
    music_lyric_tick_plan as presentation_music_lyric_tick_plan,
    music_wave_cadence_diagnostics as presentation_music_wave_cadence_diagnostics,
    music_wave_input_snapshot as presentation_music_wave_input_snapshot,
    music_surface_paint_plan as presentation_music_surface_paint_plan,
    music_wave_update_due as presentation_music_wave_update_due,
    music_wave_update_plan as presentation_music_wave_update_plan,
    normalize_barrage_display_mode as presentation_normalize_barrage_display_mode,
    normalize_empty_island_display_mode as presentation_normalize_empty_island_display_mode,
    normalize_fullscreen_plugin_alert_style as presentation_normalize_fullscreen_plugin_alert_style,
    normalize_no_music_island_priority as presentation_normalize_no_music_island_priority,
    plugin_panel_hover_open_due as presentation_plugin_panel_hover_open_due,
    plugin_alert_overlay_state as presentation_plugin_alert_overlay_state,
    render_frame_due as presentation_render_frame_due,
    scene_suppressed_interaction_reset_plan as presentation_scene_suppressed_interaction_reset_plan,
    scene_suppression_probe_due as presentation_scene_suppression_probe_due,
    scene_suppression_update_plan as presentation_scene_suppression_update_plan,
    scene_suppressed_frame_execution_plan as presentation_scene_suppressed_frame_execution_plan,
    scene_suppressed_tick_plan as presentation_scene_suppressed_tick_plan,
    tick_delta_seconds as presentation_tick_delta_seconds,
    tick_frame_execution_plan as presentation_tick_frame_execution_plan,
    tick_follow_refresh_plan as presentation_tick_follow_refresh_plan,
    tick_interaction_plan as presentation_tick_interaction_plan,
    tick_render_plan as presentation_tick_render_plan,
    topmost_reinforcement_due as presentation_topmost_reinforcement_due,
)
from local.island_visual_stack import (
    IslandKind,
    BarrageLayoutSource,
    MotionSnapshot,
    StackVisual,
    active_motion_step_plan as stack_active_motion_step_plan,
    active_visual_motion_params as stack_active_visual_motion_params,
    barrage_background_style_plan as stack_barrage_background_style_plan,
    barrage_scene_layout_plan as stack_barrage_scene_layout_plan,
    expanded_message_rows as stack_expanded_message_rows,
    initial_motion_rect_for_visual as stack_initial_motion_rect_for_visual,
    interaction_mask_update_plan as stack_interaction_mask_update_plan,
    main_visual_identity as stack_main_visual_identity,
    message_visual_key as stack_message_visual_key,
    plugin_alert_key as stack_plugin_alert_key,
    plugin_alert_exit_rect_towards_main as stack_plugin_alert_exit_rect_towards_main,
    popup_message_rows as stack_popup_message_rows,
    stack_paint_context_plan,
    stack_frame_paint_plan,
    stale_motion_step_plan as stack_stale_motion_step_plan,
    side_notification_messages as stack_side_notification_messages,
    taskbar_should_drop_stale_motion_key as stack_taskbar_should_drop_stale_motion_key,
)
from local.lyric_paint_state import build_lyric_paint_state
from local.media_position_pipeline import (
    looks_like_seek_to_start as media_position_looks_like_seek_to_start,
    media_position_update_plan,
)
from local.music_background_renderer import NativeMusicBackgroundRenderer, SolidMusicBackgroundRenderer
from local.audio_output_icons import paint_audio_output_icon
from local.island_audio_device_motion import IslandAudioDeviceMotion, IslandDevicePayload, reliable_device_battery
from local.music_lyric_motion import SPARSE_MIN_SECONDS_PER_UNIT, normalize_sparse_seconds_per_unit
from local.music_surface_palette import (
    MusicSurfacePalette,
    artwork_surface_palette,
    cached_artwork_surface_palette,
    future_lyric_color,
    relative_luminance,
    solid_album_background_color,
    solid_album_ink_color,
)
from local.music_surface_window import MusicSurfaceWindow
from local.music_mode_controls import ArtworkHoverMotion, paint_presentation_glyph
from local.personal_dj_icon import paint_personal_dj_icon, personal_dj_lightening_limit
from local.mydockfinder import FinderGlassMaterial, read_finder_material
from local.plugin_presentation import (
    active_plugin_alerts as presentation_active_plugin_alerts,
    adjust_alarm_editor_field as presentation_adjust_alarm_editor_field,
    alarm_editor_state as presentation_alarm_editor_state,
    alarm_editor_state_from_row as presentation_alarm_editor_state_from_row,
    alarm_frequency_units as presentation_alarm_frequency_units,
    alarm_picker_display_values as presentation_alarm_picker_display_values,
    coerce_alarm_rows as presentation_coerce_alarm_rows,
    capture_time_alarm_due_pending as presentation_capture_time_alarm_due_pending,
    default_alarm_editor_state as presentation_default_alarm_editor_state,
    handle_alarm_editor_key_input as presentation_handle_alarm_editor_key_input,
    save_alarm_editor_rows as presentation_save_alarm_editor_rows,
    alarm_recurrence_label as presentation_alarm_recurrence_label,
    alarm_recurrence_value_label as presentation_alarm_recurrence_value_label,
    alarm_int as presentation_alarm_int,
    alarm_row_matches_date as presentation_alarm_row_matches_date,
    alarm_row_time_parts as presentation_alarm_row_time_parts,
    alarm_unit_from_frequency as presentation_alarm_unit_from_frequency,
    alarm_weekdays_from_value as presentation_alarm_weekdays_from_value,
    agenda_plugin_panel_sections as presentation_agenda_plugin_panel_sections,
    append_countdown_entry as presentation_append_countdown_entry,
    days_in_month as presentation_days_in_month,
    format_timer_seconds as presentation_format_timer_seconds,
    generic_plugin_panel_sections as presentation_generic_plugin_panel_sections,
    generic_plugin_alert_candidate as presentation_generic_plugin_alert_candidate,
    handle_alarm_editor_action as presentation_handle_alarm_editor_action,
    normalize_alarm_editor_recurrence as presentation_normalize_alarm_editor_recurrence,
    handle_alarm_rows_action as presentation_handle_alarm_rows_action,
    enabled_plugin_ids as presentation_enabled_plugin_ids,
    enabled_plugin_samples as presentation_enabled_plugin_samples,
    plugin_action_model as presentation_plugin_action_model,
    plugin_alert_sort_key as presentation_plugin_alert_sort_key,
    plugin_alert_status_is_active as presentation_plugin_alert_status_is_active,
    plugin_alert_status_priority as presentation_plugin_alert_status_priority,
    plugin_alert_text_parts as presentation_plugin_alert_text_parts,
    plugin_alert_display_value as presentation_plugin_alert_display_value,
    plugin_alert_width_units as presentation_plugin_alert_width_units,
    plugin_compact_value as presentation_plugin_compact_value,
    plugin_function_lines as presentation_plugin_function_lines,
    plugin_index_for_id as presentation_plugin_index_for_id,
    plugin_panel_focus_model as presentation_plugin_panel_focus_model,
    plugin_panel_reminder_models as presentation_plugin_panel_reminder_models,
    plugin_panel_sections as presentation_plugin_panel_sections,
    plugin_ring_style as presentation_plugin_ring_style,
    plugin_sample_tuple as presentation_plugin_sample_tuple,
    strip_timer_prefix as presentation_strip_timer_prefix,
    taskbar_main_overlay_alert as presentation_taskbar_main_overlay_alert,
    time_plugin_alert_width_units as presentation_time_plugin_alert_width_units,
    time_alarm_alert_text as presentation_time_alarm_alert_text,
    time_alarm_alert_text_from_parts as presentation_time_alarm_alert_text_from_parts,
    time_alarm_side_display_mode as presentation_time_alarm_side_display_mode,
    time_alarm_due_acknowledged_matches as presentation_time_alarm_due_acknowledged_matches,
    time_alarm_side_segment_text as presentation_time_alarm_side_segment_text,
    time_alert_side_suppressed as presentation_time_alert_side_suppressed,
    time_alert_kind_from_plugin_id as presentation_time_alert_kind_from_plugin_id,
    time_compact_segments as presentation_time_compact_segments,
    time_countdown_active_model as presentation_time_countdown_active_model,
    time_countdown_entries as presentation_time_countdown_entries,
    time_display_segment_text as presentation_time_display_segment_text,
    time_side_alert_candidates as presentation_time_side_alert_candidates,
    handle_timer_action as presentation_handle_timer_action,
    pause_latest_countdown_entry as presentation_pause_latest_countdown_entry,
    resume_latest_countdown_entry as presentation_resume_latest_countdown_entry,
    time_reminder_due_from_alarm as presentation_time_reminder_due_from_alarm,
    time_reminder_kind as presentation_time_reminder_kind,
    time_reminder_display_text as presentation_time_reminder_display_text,
    time_text_looks_like_countdown as presentation_time_text_looks_like_countdown,
    time_plugin_panel_sections as presentation_time_plugin_panel_sections,
    weather_plugin_panel_sections as presentation_weather_plugin_panel_sections,
    yearly_alarm_max_day as presentation_yearly_alarm_max_day,
)
from local.settings_store import (
    DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES,
    MUSIC_READING_GRADIENT_DEFAULT_STEPS,
    MUSIC_READING_LIFT_DEFAULT_LEVEL,
    MUSIC_READING_STRATEGY_DEFAULT,
    normalize_music_ambience_style,
    normalize_music_reading_gradient_steps,
    normalize_music_reading_lift_level,
    normalize_music_reading_strategy,
)
from local.taskbar_positioning import (
    StandaloneAboveTaskbarTarget,
    StandalonePlacementTarget,
    StandaloneReleaseDecision,
    TaskbarDragTarget,
    TaskbarFollowTarget,
    available_taskbar_slot_rects,
    canonical_position_preset,
    build_location_mode_transition_plan,
    build_taskbar_auto_hide_mode_plan,
    build_taskbar_fusion_mode_plan,
    build_taskbar_presence_plan,
    build_taskbar_above_follow_refresh_plan,
    build_taskbar_autohide_hidden_plan,
    build_taskbar_autohide_interaction_plan,
    build_taskbar_current_visibility_follow_plan,
    build_taskbar_visibility_follow_plan,
    build_taskbar_preview_drag_plan,
    build_taskbar_preview_drop_acceptance_plan,
    build_taskbar_drag_action_plan,
    build_taskbar_drop_zones,
    build_taskbar_drag_target,
    build_taskbar_left_resize_target,
    build_taskbar_dock_plan,
    build_taskbar_follow_refresh_plan,
    build_standalone_free_release_target,
    build_standalone_position_preset_target,
    build_standalone_release_execution_plan,
    build_standalone_stack_direction_plan,
    build_standalone_top_notch_target,
    build_standalone_above_taskbar_target,
    build_mouse_release_action_plan,
    build_mouse_release_cleanup_plan,
    build_mouse_release_standalone_settle_plan,
    build_tasker_release_detach_plan,
    build_drag_update_entry_plan,
    build_finder_attach_plan,
    build_finder_drag_action_plan,
    build_standalone_drag_direction_plan,
    build_taskbar_follow_target,
    available_location_modes_for_state,
    canonical_location_mode as positioning_canonical_location_mode,
    center_for_position_ratio,
    clamp_rect_x_to_screen as positioning_clamp_rect_x_to_screen,
    clamp_standalone_window_pos,
    clamp_taskbar_window_pos,
    compact_music_target_width_from_content as positioning_compact_music_target_width_from_content,
    decide_standalone_release_action,
    drag_main_offset_for_press as positioning_drag_main_offset_for_press,
    empty_bar_orientation as positioning_empty_bar_orientation,
    empty_bar_size as positioning_empty_bar_size,
    fallback_taskbar_task_area_rect,
    extend_taskbar_left_handle_visible_until,
    expanded_stack_height as positioning_expanded_stack_height,
    hidden_autohide_follow_rect,
    intended_zone_rect,
    MainSizeFacts,
    main_size_for_kind as positioning_main_size_for_kind,
    message_dot_rect as positioning_message_dot_rect,
    nudge_window_pos_outside_taskbar,
    plugin_panel_height as positioning_plugin_panel_height,
    plugin_panel_layout as positioning_plugin_panel_layout,
    plugin_panel_shell_rect as positioning_plugin_panel_shell_rect,
    plugin_panel_time_layout as positioning_plugin_panel_time_layout,
    plugin_active_focus as positioning_plugin_active_focus,
    plugin_focus_index_for_x as positioning_plugin_focus_index_for_x,
    plugin_focus_weights as positioning_plugin_focus_weights,
    plugin_item_rects as positioning_plugin_item_rects,
    plugin_slot_widths as positioning_plugin_slot_widths,
    plugin_strip_layout as positioning_plugin_strip_layout,
    plugin_alert_circle_size as positioning_plugin_alert_circle_size,
    plugin_alert_main_rect_screen_pressure as positioning_plugin_alert_main_rect_screen_pressure,
    plugin_alert_rect as positioning_plugin_alert_rect,
    plugin_alert_row_fits as positioning_plugin_alert_row_fits,
    plugin_alert_side_for_row as positioning_plugin_alert_side_for_row,
    plugin_alert_side_space as positioning_plugin_alert_side_space,
    plugin_alert_uses_ring as positioning_plugin_alert_uses_ring,
    plugin_alert_width_for_row as positioning_plugin_alert_width_for_row,
    plugin_alert_x_for_side as positioning_plugin_alert_x_for_side,
    plugin_alert_x_for_slot as positioning_plugin_alert_x_for_slot,
    position_ratio_for_center,
    point_in_finder_frame as positioning_point_in_finder_frame,
    rect_x_fits_screen as positioning_rect_x_fits_screen,
    should_enter_taskbar_for_visible_taskbar_release as positioning_should_enter_taskbar_for_visible_taskbar_release,
    should_snap_above_taskbar_for_release as positioning_should_snap_above_taskbar_for_release,
    should_snap_top_notch_for_release as positioning_should_snap_top_notch_for_release,
    standalone_release_direction_for_anchor,
    select_taskbar_autohide_probe_rect,
    select_bottom_taskbar_rect,
    select_taskbar_task_area_rect,
    standalone_bottom_boundary_extension,
    select_windows_taskbar_occupied_rects,
    select_windows_taskbar_task_area_rect,
    stick_taskbar_above_gap,
    side_taskbar_slot_rects,
    taskbar_above_gap,
    taskbar_attach_hot_zone_accepts_rect as positioning_taskbar_attach_hot_zone_accepts_rect,
    taskbar_attach_hot_zone_contains as positioning_taskbar_attach_hot_zone_contains,
    taskbar_attach_hot_zone_host_rect as positioning_taskbar_attach_hot_zone_host_rect,
    taskbar_attach_hot_zone_rect as positioning_taskbar_attach_hot_zone_rect,
    taskbar_autohide_visible_follow_lift,
    taskbar_autohide_cursor_in_taskbar_area as positioning_taskbar_autohide_cursor_in_taskbar_area,
    taskbar_autohide_cursor_reveals as positioning_taskbar_autohide_cursor_reveals,
    taskbar_auto_hide_enabled_for_state,
    taskbar_bottom_boundary_extension,
    taskbar_compact_height_from_rects,
    taskbar_compact_plugin_alert_rect as positioning_taskbar_compact_plugin_alert_rect,
    taskbar_compact_main_height as positioning_taskbar_compact_main_height,
    taskbar_compact_music_size as positioning_taskbar_compact_music_size,
    taskbar_contacting_rect as positioning_taskbar_contacting_rect,
    taskbar_corner_radius as positioning_taskbar_corner_radius,
    taskbar_cover_only_music_active as positioning_taskbar_cover_only_music_active,
    taskbar_drag_screen_ratio as positioning_taskbar_drag_screen_ratio,
    taskbar_selected_drop_zone_local_rect as positioning_taskbar_selected_drop_zone_local_rect,
    taskbar_left_handle_frame_rect,
    taskbar_left_handle_rect as positioning_taskbar_left_handle_rect,
    taskbar_left_handle_visible as positioning_taskbar_left_handle_visible,
    taskbar_left_handle_visibility_progress as positioning_taskbar_left_handle_visibility_progress,
    select_taskbar_drop_zone as positioning_select_taskbar_drop_zone,
    select_taskbar_drag_reference_rect,
    resolve_taskbar_rect_for_screen,
    stack_rect_for_direction,
    standalone_stack_center_x,
    standalone_stack_width_for_limit,
    standalone_main_top_for_direction,
    standalone_release_probe_point,
    standalone_width_limit_for_global_center,
    stack_target_width as positioning_stack_target_width,
    taskbar_drag_should_detach_to_free as positioning_taskbar_drag_should_detach_to_free,
    taskbar_drag_should_stay_on_track as positioning_taskbar_drag_should_stay_on_track,
    taskbar_drop_zone_rect as positioning_taskbar_drop_zone_rect,
    taskbar_follow_force_visible_active as positioning_taskbar_follow_force_visible_active,
    taskbar_follow_geometry_key as positioning_taskbar_follow_geometry_key,
    taskbar_follow_rect_key as positioning_taskbar_follow_rect_key,
    taskbar_follow_refresh_due as positioning_taskbar_follow_refresh_due,
    taskbar_compact_hit_test_key as positioning_taskbar_compact_hit_test_key,
    taskbar_full_compact_height_for_rect as positioning_taskbar_full_compact_height_for_rect,
    taskbar_height_from_rect,
    taskbar_hidden_fallback_rect as positioning_taskbar_hidden_fallback_rect,
    taskbar_material_exclusion_rects as positioning_taskbar_material_exclusion_rects,
    taskbar_anchor_main_size as positioning_taskbar_anchor_main_size,
    taskbar_message_effective_style as positioning_taskbar_message_effective_style,
    taskbar_message_rect as positioning_taskbar_message_rect,
    taskbar_manual_width_for_left_edge,
    taskbar_synced_corner_radius as positioning_taskbar_synced_corner_radius,
    taskbar_width_with_manual as positioning_taskbar_width_with_manual,
    time_visible_module_for_width as positioning_time_visible_module_for_width,
    select_taskbar_drag_track_rect,
    select_taskbar_track_rect,
    should_show_taskbar_preview as positioning_should_show_taskbar_preview,
    should_show_taskbar_preview_for_rect as positioning_should_show_taskbar_preview_for_rect,
    taskbar_shell_rect_is_hidden_autohide as positioning_taskbar_shell_rect_is_hidden_autohide,
    taskbar_stack_direction_for_rect as positioning_taskbar_stack_direction_for_rect,
)
from local.ui_theme import (
    app_dark_mode,
    system_dark_mode,
    is_theme_change_event,
    music_background_light_sync_plan,
    music_ambience_theme_category,
)
from local.visual_diagnostics import (
    VisualFrameDiagnosticsRecorder,
    VisualFrameDiagnosticsSummary,
    VisualPaintSectionDiagnosticsSummary,
)


@dataclass
class Message:
    app: str
    sender: str
    text: str
    name_color: QColor
    avatar_color: QColor
    avatar_image: QImage = field(default_factory=QImage)
    serial: int = 0
    created_at: float = field(default_factory=time.monotonic)
    popup_until: float = field(default_factory=lambda: time.monotonic() + 5.0)
    expires_at: float = 0.0
    barrage_started_at: float = 0.0
    action_key: str = ""


@dataclass(frozen=True)
class LyricLayout:
    text: str
    chunks: tuple[str, ...]
    widths: tuple[float, ...]
    total_width: float
    font_key: tuple[object, ...]


class SpringRect:
    def __init__(self, rect: QRectF):
        self.x = rect.x()
        self.y = rect.y()
        self.w = rect.width()
        self.h = rect.height()
        self.vx = 0.0
        self.vy = 0.0
        self.vw = 0.0
        self.vh = 0.0
        self.opacity = 0.0

    @property
    def rect(self) -> QRectF:
        return QRectF(self.x, self.y, self.w, self.h)

    def step(self, target: QRectF, target_opacity: float, dt: float, response: float = 0.34, damping: float = 0.76):
        dt = max(0.001, min(0.033, dt))
        omega = 2.0 * math.pi / response

        self.x, self.vx = self._spring(self.x, self.vx, target.x(), omega, damping, dt)
        self.y, self.vy = self._spring(self.y, self.vy, target.y(), omega, damping, dt)
        self.w, self.vw = self._spring(self.w, self.vw, target.width(), omega, damping, dt)
        self.h, self.vh = self._spring(self.h, self.vh, target.height(), omega, damping, dt)
        self.opacity += (target_opacity - self.opacity) * min(1.0, dt * 14.0)

    @staticmethod
    def _spring(value: float, velocity: float, target: float, omega: float, damping: float, dt: float) -> tuple[float, float]:
        acceleration = omega * omega * (target - value) - 2.0 * damping * omega * velocity
        velocity += acceleration * dt
        value += velocity * dt
        return value, velocity


class _IslandArtworkPaletteBridge(QObject):
    ready = Signal(int, object, object)


class TaskbarDropOverlay(QWidget):
    LABEL = "拖入此处粘贴到任务栏"

    def __init__(self):
        super().__init__(None)
        self.drop_rect = QRectF()
        self.drop_rects: list[QRectF] = []
        self.corner_radius = 8.0
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
        )

    def _raise_as_topmost(self):
        self.raise_()
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            if not hwnd:
                return
            ctypes.windll.user32.SetWindowPos(
                wintypes.HWND(hwnd),
                wintypes.HWND(-1),
                0,
                0,
                0,
                0,
                0x0001 | 0x0002 | 0x0010 | 0x0040,
            )
        except Exception:
            pass

    def show_zone(self, taskbar_rect: QRectF, drop_rect: QRectF, corner_radius: float):
        self.show_zones(taskbar_rect, [drop_rect], corner_radius)

    def show_zones(self, taskbar_rect: QRectF, drop_rects: list[QRectF], corner_radius: float):
        zones = [QRectF(rect) for rect in drop_rects if rect is not None and not rect.isNull()]
        if taskbar_rect.isNull() or not zones:
            self.hide()
            return
        self.corner_radius = float(corner_radius)
        self.drop_rects = []
        for rect in zones:
            local = QRectF(rect)
            local.translate(-taskbar_rect.left(), -taskbar_rect.top())
            self.drop_rects.append(local)
        self.drop_rect = QRectF(self.drop_rects[0])
        self.setGeometry(taskbar_rect.toAlignedRect())
        self.show()
        self._raise_as_topmost()
        self.update()

    def paintEvent(self, event):
        rects = list(getattr(self, "drop_rects", []))
        if not rects and not self.drop_rect.isNull():
            rects = [QRectF(self.drop_rect)]
        if not rects:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        radius = max(2.0, self.corner_radius)
        for rect in rects:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 72))
            painter.drawRoundedRect(rect.adjusted(-5.0, -5.0, 5.0, 5.0), radius + 5.0, radius + 5.0)
            painter.setBrush(QColor(38, 98, 180, 72))
            painter.drawRoundedRect(rect, radius, radius)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(125, 190, 255, 235), 1.4, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap))
            painter.drawRoundedRect(rect.adjusted(0.7, 0.7, -0.7, -0.7), max(1.0, radius - 0.7), max(1.0, radius - 0.7))
            font = painter.font()
            font.setPointSize(11)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(244, 248, 255, 236))
            painter.drawText(rect.adjusted(12.0, 0.0, -12.0, 0.0), Qt.AlignmentFlag.AlignCenter, self.LABEL)
        painter.end()


class FullscreenPluginAlertOverlay(QWidget):
    TRANSITION_SECONDS = 0.18

    def __init__(self, owner):
        super().__init__(None)
        self.owner = owner
        self.alert_state: dict[str, object] = {}
        self.active_rects: dict[str, QRectF] = {}
        self.press_key = ""
        self.barrage_hovered = False
        self.transition_mode = ""
        self.transition_started_at = time.monotonic()
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        try:
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        except AttributeError:
            pass
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setMouseTracking(True)

    def configure(self, state: dict[str, object], screen_rect: QRectF, now: float):
        previous_key = self._state_key(self.alert_state)
        next_state = dict(state or {})
        next_key = self._state_key(next_state)
        self.alert_state = next_state
        target = QRectF(screen_rect).toAlignedRect()
        if target.width() > 0 and target.height() > 0 and self.geometry() != target:
            self.setGeometry(target)
        if not self.isVisible():
            self.transition_mode = "show"
            self.transition_started_at = float(now)
            self.show()
        elif previous_key != next_key and self.transition_mode != "show":
            self.transition_mode = "show"
            self.transition_started_at = float(now)
        self.raise_()
        self._force_overlay_topmost()
        self.update()

    def _force_overlay_topmost(self):
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            if hwnd <= 0:
                return
            user32 = ctypes.windll.user32
            hwnd_topmost = ctypes.c_void_p(-1)
            flags = 0x0001 | 0x0002 | 0x0010 | 0x0040  # NOSIZE, NOMOVE, NOACTIVATE, SHOWWINDOW
            user32.SetWindowPos(ctypes.c_void_p(hwnd), hwnd_topmost, 0, 0, 0, 0, flags)
        except Exception:
            pass

    def request_hide(self, now: float):
        if not self.isVisible():
            return
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if self.transition_mode != "hide":
            self.transition_mode = "hide"
            self.transition_started_at = float(now)
        self.update()

    def _state_key(self, state: dict[str, object]) -> str:
        return f"{state.get('style', '')}:{state.get('action_key', '')}:{state.get('alert_key', '')}"

    def _u(self, value: float) -> float:
        owner = getattr(self, "owner", None)
        if owner is not None and hasattr(owner, "_u"):
            try:
                return float(owner._u(value))
            except Exception:
                pass
        return float(value)

    def _font(self, size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
        owner = getattr(self, "owner", None)
        if owner is not None and hasattr(owner, "_font"):
            try:
                return owner._font(size, weight)
            except Exception:
                pass
        font = QFont()
        font.setPointSize(max(1, int(size)))
        font.setWeight(weight)
        return font

    def _transition_opacity(self, now: float) -> tuple[float, bool]:
        mode = str(getattr(self, "transition_mode", "") or "")
        if mode not in {"show", "hide"}:
            return (1.0, False)
        owner = getattr(self, "owner", None)
        duration = self.TRANSITION_SECONDS
        if owner is not None and hasattr(owner, "_animation_duration"):
            try:
                duration = float(owner._animation_duration(self.TRANSITION_SECONDS))
            except Exception:
                duration = self.TRANSITION_SECONDS
        elapsed = max(0.0, float(now) - float(getattr(self, "transition_started_at", now)))
        if elapsed >= duration:
            if mode == "hide":
                return (0.0, True)
            self.transition_mode = ""
            return (1.0, False)
        t = max(0.0, min(1.0, elapsed / max(0.01, duration)))
        eased = 1.0 - (1.0 - t) ** 4
        return ((1.0 - eased) if mode == "hide" else eased, False)

    def _alert_tuple(self):
        alert = self.alert_state.get("alert")
        try:
            _index, label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, priority = alert
        except (TypeError, ValueError):
            return None
        return label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, priority

    def _alert_text_parts(self) -> tuple[str, str]:
        parts = self._alert_tuple()
        if parts is None:
            return ("Reminder", "Click to review")
        label, detail, summary, _color, _icon, _plugin_id, status, alert_title, alert_body, _priority = parts
        owner = getattr(self, "owner", None)
        if owner is not None and hasattr(owner, "_plugin_alert_text_parts"):
            try:
                title, body = owner._plugin_alert_text_parts(label, detail, summary, status, alert_title, alert_body)
                return (str(title or label or "Reminder"), str(body or summary or detail or "Click to review"))
            except Exception:
                pass
        return (str(alert_title or label or "Reminder"), str(alert_body or summary or detail or "Click to review"))

    def _accent_color(self) -> QColor:
        parts = self._alert_tuple()
        color = parts[3] if parts is not None else None
        if isinstance(color, QColor) and color.isValid():
            return QColor(color)
        try:
            candidate = QColor(color) if color is not None else QColor()
        except Exception:
            candidate = QColor()
        if candidate.isValid():
            return candidate
        return QColor(255, 184, 0)

    def _card_rect(self, style: str) -> QRectF:
        width = float(max(300.0, min(430.0, self.width() - self._u(48.0))))
        if style == "island":
            style = "toast"
        if style == "toast":
            height = self._u(58.0)
            x = (float(self.width()) - width) / 2.0
            y = self._u(18.0)
            rect = QRectF(x, y, width, height)
            return self._offset_card_rect(rect)
        height = self._u(58.0)
        x = (float(self.width()) - width) / 2.0
        y = self._u(28.0)
        rect = QRectF(x, y, width, height)
        return self._offset_card_rect(rect)

    def _offset_card_rect(self, rect: QRectF) -> QRectF:
        owner = getattr(self, "owner", None)
        try:
            dx = float(getattr(owner, "plugin_fullscreen_alert_x_offset", 0) or 0)
        except (TypeError, ValueError):
            dx = 0.0
        try:
            dy = float(getattr(owner, "plugin_fullscreen_alert_y_offset", 0) or 0)
        except (TypeError, ValueError):
            dy = 0.0
        moved = QRectF(rect).translated(dx, dy)
        margin = self._u(8.0)
        max_left = max(margin, float(self.width()) - moved.width() - margin)
        max_top = max(margin, float(self.height()) - moved.height() - margin)
        left = max(margin, min(max_left, moved.left()))
        top = max(margin, min(max_top, moved.top()))
        return QRectF(left, top, moved.width(), moved.height())

    def _paint_card(self, painter: QPainter, rect: QRectF, style: str, opacity: float):
        title, body = self._alert_text_parts()
        accent = self._accent_color()
        dark = app_dark_mode()
        shell = QColor(18, 20, 24, int(226 * opacity)) if dark else QColor(248, 250, 252, int(232 * opacity))
        border = QColor(255, 255, 255, int(34 * opacity)) if dark else QColor(20, 26, 36, int(28 * opacity))
        text = QColor(246, 248, 255, int(238 * opacity)) if dark else QColor(22, 28, 40, int(238 * opacity))
        muted = QColor(196, 202, 216, int(194 * opacity)) if dark else QColor(84, 96, 112, int(200 * opacity))
        radius = min(rect.height() / 2.0 if style in {"island", "toast"} else self._u(18.0), self._u(22.0))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, int(72 * opacity)))
        painter.drawRoundedRect(rect.translated(0.0, self._u(4.0)), radius, radius)
        painter.setBrush(shell)
        painter.drawRoundedRect(rect, radius, radius)
        painter.setPen(QPen(border, self._u(1.0)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(self._u(0.5), self._u(0.5), -self._u(0.5), -self._u(0.5)), radius, radius)

        icon = QRectF(rect.left() + self._u(14.0), rect.center().y() - self._u(15.0), self._u(30.0), self._u(30.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), int(220 * opacity)))
        painter.drawRoundedRect(icon, self._u(9.0), self._u(9.0))
        painter.setFont(self._font(11, QFont.Weight.Bold))
        painter.setPen(QColor(255, 255, 255, int(236 * opacity)))
        painter.drawText(icon, Qt.AlignmentFlag.AlignCenter, title[:1].upper() or "!")

        text_left = icon.right() + self._u(10.0)
        action_w = 0.0
        text_rect = QRectF(text_left, rect.top() + self._u(8.0), rect.right() - text_left - self._u(14.0) - action_w, rect.height() - self._u(16.0))
        painter.setFont(self._font(11, QFont.Weight.DemiBold))
        title_metrics = QFontMetrics(painter.font())
        painter.setPen(text)
        painter.drawText(
            QRectF(text_rect.left(), text_rect.top(), text_rect.width(), self._u(20.0)),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            title_metrics.elidedText(title, Qt.TextElideMode.ElideRight, int(text_rect.width())),
        )
        painter.setFont(self._font(9, QFont.Weight.Medium))
        body_metrics = QFontMetrics(painter.font())
        painter.setPen(muted)
        painter.drawText(
            QRectF(text_rect.left(), text_rect.top() + self._u(21.0), text_rect.width(), self._u(22.0)),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            body_metrics.elidedText(body, Qt.TextElideMode.ElideRight, int(text_rect.width())),
        )
        painter.restore()

    def _paint_fullscreen_confirmation(self, painter: QPainter, opacity: float, key: str) -> dict[str, QRectF]:
        title, body = self._alert_text_parts()
        accent = self._accent_color()
        viewport = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        backdrop = QLinearGradient(0.0, viewport.top(), 0.0, viewport.bottom())
        backdrop.setColorAt(0.00, QColor(max(0, accent.red() - 40), max(0, accent.green() - 50), max(0, accent.blue() - 80), int(236 * opacity)))
        backdrop.setColorAt(0.46, QColor(8, 10, 16, int(244 * opacity)))
        backdrop.setColorAt(1.00, QColor(0, 0, 0, int(248 * opacity)))
        painter.fillRect(viewport, backdrop)

        content_width = max(self._u(260.0), min(self._u(620.0), viewport.width() - self._u(72.0)))
        content = QRectF(
            viewport.center().x() - content_width / 2.0,
            viewport.center().y() - self._u(112.0),
            content_width,
            self._u(224.0),
        )
        icon = QRectF(content.center().x() - self._u(24.0), content.top(), self._u(48.0), self._u(48.0))
        painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), int(235 * opacity)))
        painter.drawRoundedRect(icon, self._u(15.0), self._u(15.0))
        painter.setFont(self._font(16, QFont.Weight.Bold))
        painter.setPen(QColor(255, 255, 255, int(245 * opacity)))
        painter.drawText(icon, Qt.AlignmentFlag.AlignCenter, title[:1].upper() or "!")

        painter.setFont(self._font(24, QFont.Weight.Bold))
        title_metrics = QFontMetrics(painter.font())
        title_rect = QRectF(content.left(), icon.bottom() + self._u(18.0), content.width(), self._u(38.0))
        painter.setPen(QColor(255, 255, 255, int(246 * opacity)))
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignCenter, title_metrics.elidedText(title, Qt.TextElideMode.ElideRight, int(title_rect.width())))

        painter.setFont(self._font(12, QFont.Weight.Medium))
        body_metrics = QFontMetrics(painter.font())
        body_rect = QRectF(content.left(), title_rect.bottom() + self._u(6.0), content.width(), self._u(26.0))
        painter.setPen(QColor(226, 232, 244, int(220 * opacity)))
        painter.drawText(body_rect, Qt.AlignmentFlag.AlignCenter, body_metrics.elidedText(body, Qt.TextElideMode.ElideRight, int(body_rect.width())))

        button_top = body_rect.bottom() + self._u(24.0)
        button_h = self._u(42.0)
        gap = self._u(10.0)
        primary_w = self._u(150.0)
        snooze_w = self._u(118.0)
        dismiss_w = self._u(106.0)
        total_w = primary_w + snooze_w + dismiss_w + gap * 2.0
        rects: dict[str, QRectF] = {}

        def draw_button(button: QRectF, text_value: str, fill: QColor, text_color: QColor, border: QColor | None = None):
            painter.setPen(QPen(border, self._u(1.0)) if border is not None else Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(button, button.height() / 2.0, button.height() / 2.0)
            painter.setFont(self._font(11, QFont.Weight.Bold))
            painter.setPen(text_color)
            painter.drawText(button.adjusted(self._u(10.0), 0.0, -self._u(10.0), 0.0), Qt.AlignmentFlag.AlignCenter, text_value)

        if total_w <= content.width():
            left = content.center().x() - total_w / 2.0
            detail = QRectF(left, button_top, primary_w, button_h)
            snooze = QRectF(detail.right() + gap, button_top, snooze_w, button_h)
            dismiss = QRectF(snooze.right() + gap, button_top, dismiss_w, button_h)
        else:
            stack_w = min(content.width(), self._u(230.0))
            detail = QRectF(content.center().x() - stack_w / 2.0, button_top, stack_w, button_h)
            snooze = QRectF(detail.left(), detail.bottom() + self._u(8.0), (stack_w - gap) / 2.0, button_h)
            dismiss = QRectF(snooze.right() + gap, snooze.top(), (stack_w - gap) / 2.0, button_h)

        primary_fill = QColor(255, 255, 255, int(238 * opacity))
        primary_text = QColor(8, 10, 16, int(244 * opacity))
        quiet_fill = QColor(255, 255, 255, int(34 * opacity))
        quiet_text = QColor(238, 242, 250, int(232 * opacity))
        quiet_border = QColor(255, 255, 255, int(66 * opacity))
        draw_button(detail, "查看详情", primary_fill, primary_text, QColor(255, 255, 255, int(70 * opacity)))
        draw_button(snooze, "稍后提醒", quiet_fill, quiet_text, quiet_border)
        draw_button(dismiss, "确认", quiet_fill, quiet_text, quiet_border)
        if key:
            rects[key] = detail
        rects["fullscreen-alert-snooze"] = snooze
        rects["fullscreen-alert-dismiss"] = dismiss
        painter.restore()
        return rects

    def _apply_hit_mask(self, rects: dict[str, QRectF]):
        if not rects:
            self.active_rects = {}
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.clearMask()
            return
        region = QRegion()
        normalized: dict[str, QRectF] = {}
        for key, rect in rects.items():
            if not key:
                continue
            grown = QRectF(rect).adjusted(-self._u(3.0), -self._u(3.0), self._u(3.0), self._u(3.0))
            if grown.width() <= 0 or grown.height() <= 0:
                continue
            normalized[str(key)] = grown
            region = region.united(QRegion(grown.toAlignedRect()))
        self.active_rects = normalized
        if normalized:
            self.setMask(region)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.clearMask()

    def _action_key_at(self, point: QPointF) -> str:
        for key, rect in getattr(self, "active_rects", {}).items():
            if rect.contains(point):
                return key
        return ""

    def paintEvent(self, event):
        state = dict(getattr(self, "alert_state", {}) or {})
        if not state:
            return
        now = time.monotonic()
        opacity, hide_done = self._transition_opacity(now)
        if hide_done:
            self.hide()
            self.active_rects = {}
            self.alert_state = {}
            self.barrage_hovered = False
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setOpacity(max(0.0, min(1.0, opacity)))
        style = str(state.get("style", "toast") or "toast")
        if style == "island":
            style = "toast"
        if style == "barrage":
            owner = getattr(self, "owner", None)
            if owner is not None and hasattr(owner, "_ensure_fullscreen_plugin_barrage_messages"):
                owner._ensure_fullscreen_plugin_barrage_messages(now)
            if owner is not None and hasattr(owner, "_paint_barrage_scene"):
                viewport = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
                previous_space = bool(getattr(owner, "_barrage_painting_global_space", False))
                owner._barrage_painting_global_space = True
                try:
                    rects = owner._paint_barrage_scene(painter, viewport)
                finally:
                    owner._barrage_painting_global_space = previous_space
                self._apply_hit_mask(rects)
        elif style == "fullscreen":
            key = str(state.get("action_key", "") or "")
            self._apply_hit_mask(self._paint_fullscreen_confirmation(painter, opacity, key))
        else:
            rect = self._card_rect(style)
            key = str(state.get("action_key", "") or "")
            self._paint_card(painter, rect, style, opacity)
            self._apply_hit_mask({key: rect} if key else {})
        painter.end()

    def mouseMoveEvent(self, event):
        key = self._action_key_at(event.position())
        self.barrage_hovered = bool(key and str(self.alert_state.get("style", "")) == "barrage")
        owner = getattr(self, "owner", None)
        if owner is not None:
            owner.barrage_hovered = self.barrage_hovered
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.barrage_hovered = False
        owner = getattr(self, "owner", None)
        if owner is not None:
            owner.barrage_hovered = False
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_key = self._action_key_at(event.position())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        key = self._action_key_at(event.position()) or self.press_key
        self.press_key = ""
        owner = getattr(self, "owner", None)
        if key in {"fullscreen-alert-snooze", "fullscreen-alert-dismiss"}:
            seconds = 300.0 if key.endswith("snooze") else 1800.0
            alert_key = str((getattr(self, "alert_state", {}) or {}).get("alert_key", "") or "")
            suppress = getattr(owner, "_suppress_fullscreen_plugin_alert", None) if owner is not None else None
            if callable(suppress):
                suppress(alert_key, seconds=seconds)
            self.request_hide(time.monotonic())
            event.accept()
            return
        if key and owner is not None and hasattr(owner, "_activate_key"):
            owner._activate_key(key)
            self.request_hide(time.monotonic())
            event.accept()
            return
        super().mouseReleaseEvent(event)


class BarrageOverlay(FullscreenPluginAlertOverlay):
    def __init__(self, owner):
        super().__init__(owner)
        self.barrage_messages: list[Message] = []
        self.overlay_clock = float(getattr(owner, "barrage_clock", 0.0) or 0.0)
        self.last_frame_at = time.monotonic()
        self.frame_timer = QTimer(self)
        try:
            interval = int(owner._timer_interval_ms()) if hasattr(owner, "_timer_interval_ms") else 16
        except Exception:
            interval = 16
        self.frame_timer.setInterval(max(11, min(42, interval)))
        self.frame_timer.timeout.connect(self._advance_barrage_frame)

    def configure(self, state: dict[str, object], screen_rect: QRectF, now: float):
        self._set_barrage_snapshot(state, now)
        super().configure(state, screen_rect, now)
        if not self.frame_timer.isActive():
            self.last_frame_at = float(now)
            self.frame_timer.start()

    def request_hide(self, now: float):
        super().request_hide(now)
        if self.frame_timer.isActive() and not getattr(self, "barrage_messages", []):
            self.frame_timer.stop()

    def _set_barrage_snapshot(self, state: dict[str, object], now: float):
        messages = state.get("messages", []) if isinstance(state, dict) else []
        self.barrage_messages = list(messages or [])
        owner = getattr(self, "owner", None)
        try:
            owner_clock = float(getattr(owner, "barrage_clock", 0.0) or 0.0)
        except (TypeError, ValueError):
            owner_clock = 0.0
        self.overlay_clock = max(float(getattr(self, "overlay_clock", owner_clock) or owner_clock), owner_clock)
        self.last_frame_at = float(now)

    def _advance_barrage_frame(self):
        now = time.monotonic()
        elapsed = max(0.001, min(0.2, now - float(getattr(self, "last_frame_at", now))))
        self.last_frame_at = now
        owner = getattr(self, "owner", None)
        speed = 1.0
        if owner is not None and hasattr(owner, "_animation_speed_factor"):
            try:
                speed = float(owner._animation_speed_factor())
            except Exception:
                speed = 1.0
        if not getattr(self, "barrage_hovered", False):
            self.overlay_clock = float(getattr(self, "overlay_clock", 0.0) or 0.0) + elapsed * max(0.1, min(4.0, speed))
        if owner is not None and hasattr(owner, "_barrage_alive_messages_for_clock"):
            self.barrage_messages = owner._barrage_alive_messages_for_clock(list(getattr(self, "barrage_messages", [])), self.overlay_clock)
        if not self.barrage_messages:
            self.request_hide(now)
            return
        self.update()

    def paintEvent(self, event):
        state = dict(getattr(self, "alert_state", {}) or {})
        if not state:
            return
        now = time.monotonic()
        opacity, hide_done = self._transition_opacity(now)
        if hide_done:
            self.hide()
            self.active_rects = {}
            self.alert_state = {}
            self.barrage_hovered = False
            return
        owner = getattr(self, "owner", None)
        if owner is None or not hasattr(owner, "_paint_barrage_scene"):
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setOpacity(max(0.0, min(1.0, opacity)))
        viewport = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
        previous_space = bool(getattr(owner, "_barrage_painting_global_space", False))
        owner._barrage_painting_global_space = True
        try:
            rects = owner._paint_barrage_scene(
                painter,
                viewport,
                active_messages=list(getattr(self, "barrage_messages", [])),
                clock=float(getattr(self, "overlay_clock", getattr(owner, "barrage_clock", 0.0)) or 0.0),
                prune=False,
            )
        finally:
            owner._barrage_painting_global_space = previous_space
        self._apply_hit_mask(rects)
        painter.end()


class IslandStackVisual(QWidget):
    VISUAL_SCALE = 0.75
    STACK_WIDTH = 560
    DEFAULT_FONT_FAMILY = "Alibaba PuHuiTi"
    GELV_FONT_FAMILY = "Noto Serif SC ExtraBold"
    GELV_FONT_FALLBACK_FAMILIES = ("Noto Serif SC", "Alibaba PuHuiTi")
    WAVE_FONT_FAMILY_BOLD = "Alibaba PuHuiTi"
    WAVE_FONT_FAMILY_HEAVY = "Alibaba PuHuiTi Heavy"
    WAVE_FONT_FALLBACK_FAMILIES = ("Noto Serif SC ExtraBold", "JiangChengXieSong 700W")
    EMBER_FONT_FAMILY_NORMAL = "JiangChengXieSong 700W"
    EMBER_FONT_FAMILY_BOLD = "JiangChengXieSong 900W"
    EMBER_FONT_FALLBACK_FAMILIES = ("JiangChengXieSong", "Noto Serif SC ExtraBold", "Alibaba PuHuiTi")
    _alibaba_puhuiti_loaded = False
    _noto_serif_sc_loaded = False
    _jiangcheng_xiesong_loaded = False
    CHINESE_FONT_FALLBACKS = (
        "Alibaba PuHuiTi",
        "Noto Serif SC ExtraBold",
        "JiangChengXieSong 700W",
    )
    MUSIC_AMBIENCE_STYLES = ("sea_level", "solid_album", "system")
    MUSIC_EXPANDED_LAYOUT_STYLES = ("standard", "dual_panel")
    COLOR_FILL_AMBIENCE_STYLES = ("pure_black", "bilateral_diffraction", "pure_white")
    COLOR_FILL_BLOCKED_AMBIENCE_STYLES = {"sea_level", "solid_album"}
    EMBER_COLOR_FILL_AMBIENCE_STYLES = ("pure_black", "pure_white")
    EMBER_COLOR_BACK_GRAY_AMBIENCE_STYLES = ("sea_level", "duotone_full", "solid_album")
    LYRIC_FLOW_STYLES = ("wave_stream", "gelv_songscatter", "album_island")
    ALBUM_ISLAND_SECONDARY_STYLES = ("perspective_dim",)
    # The island reuses a scaled cache, while the music window needs enough
    # source pixels for a 4K desktop. Keep the decoded source bounded, but do
    # not destroy the original cover by reducing it to an island thumbnail.
    CURRENT_ALBUM_ART_MAX_SIDE = 2560
    STORED_ALBUM_ART_MAX_SIDE = 192
    # Seven members in compact and expanded poses must coexist; a few recent
    # sizes survive the mode transition without resizing the full row again.
    SCALED_ALBUM_ART_CACHE_LIMIT = 24
    SCALED_ALBUM_ART_CACHE_PIXEL_BUDGET = 2 * 1024 * 1024
    LYRIC_LAYOUT_CACHE_LIMIT = 40
    REPRESENTATIVE_WIDTH_CACHE_LIMIT = 8
    STABLE_RENDER_FPS = 30
    FRAME_INTERVAL_TOLERANCE_SECONDS = 0.002
    TASKBAR_FOLLOW_REFRESH_INTERVAL = 0.016
    TASKBAR_FOLLOW_MATERIAL_REFRESH_INTERVAL = 0.50
    TASKBAR_QUERY_CACHE_SECONDS = 0.12

    def __init__(self, data_provider=None):
        super().__init__()
        # Every live island retires extras independently of saved settings or
        # provider output. Standalone legacy helper fixtures remain auditable.
        self._runtime_extras_retired = True
        self.data_provider = data_provider or EmptyIslandDataProvider()
        visual_data = self.data_provider.get_visual_data()
        if visual_data.messages or visual_data.plugins:
            visual_data = replace(visual_data, messages=(), plugins=())
        self.finder_material: FinderGlassMaterial = read_finder_material(include_center=True)
        self.finder_mode = bool(self.finder_material.available)
        self.attach_to_real_finder = self.finder_mode
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.resize(int(round(self._standalone_canvas_width())), int(round(self._u(650))))
        self.stack_center_ratio = 0.5
        self.saved_window_pos: tuple[int, int] | None = None
        self.last_window_position: tuple[int, int] | None = None
        self._restoring_position = False
        self.drag_press_pos = QPointF()
        self.drag_press_global = QPoint()
        self.drag_press_window_pos = QPoint()
        self.drag_press_center_ratio = 0.5
        self.drag_press_key = ""
        self.drag_press_main_offset = QPointF()
        self.drag_auto_stack_direction = 0
        self.drag_candidate = False
        self.dragging = False
        self.drag_threshold = 5.0
        self.super_topmost_enabled = True
        self.topmost_reinforced_at = 0.0
        self.scene_suppressed = False
        self.scene_reason = ""
        self.last_scene_probe_reason = ""
        self.scene_checked_at = 0.0
        self.scene_transition_mode = ""
        self.scene_transition_started_at = 0.0
        self.scene_transition_duration = 0.18
        self.settings_window_active = False
        self.last_tick_at = time.monotonic()
        self.input_passthrough_enabled = False
        self.barrage_clock = 0.0
        self.barrage_rects: dict[str, QRectF] = {}
        self.barrage_hovered = False
        self.barrage_dark_background = False
        self.barrage_style_checked_at = 0.0
        self.last_pointer_press_at = 0.0
        self.last_pointer_move_at = 0.0
        self.drag_press_main_global_rect = QRectF()
        self.last_mask_update_at = 0.0
        self.interaction_mask_active = False
        self.surface_transition_pixmap = QPixmap()
        self.surface_transition_global_rect = QRectF()
        self.surface_transition_started_at = 0.0
        self.surface_transition_duration = 0.22
        self.surface_transition_reason = ""
        self.mask_deferred_until = 0.0
        self.drag_detach_global_rects: dict[str, QRectF] = {}
        self._center_on_screen()

        self.music_playing = True
        self.music_auto_detect = False
        self.media_is_playing = True
        self.media_duration_seconds = 0.0
        self.media_position_seconds = 0.0
        self.media_track_id = ""
        self.media_source_app = ""
        self.media_lyric_offset_adapter = "system"
        self.media_position_source = "windows-media"
        self.media_position_plan_reason = "raw"
        self.media_position_confidence = 1.0
        self.listened_seconds_total = 0.0
        self.album_history: list[dict[str, object]] = []
        self.album_queue: list[dict[str, object]] = []
        self.album_queue_signature = ""
        self.album_island_transition_started_at = 0.0
        self.album_island_transition_reason = ""
        self.album_island_transition_direction = 1
        self.music_surface_transition_started_at = 0.0
        self.music_surface_transition_duration = 0.62
        self.music_surface_transition_mode = ""
        self.music_surface_transition_direction = 1
        self.music_surface_transition_from = (QColor(), QColor())
        self.music_surface_transition_to = (QColor(), QColor())
        self.lyrics_source = ""
        self.timed_lyrics: list[tuple[float, str]] = []
        # These lists share indices with the legacy (start, text) timeline.
        # Word timestamps belong to the original line, never its translation.
        self.timed_lyric_units: list[tuple[object, ...]] = []
        self.timed_lyric_timing_kinds: list[str] = []
        self.album_art = QImage()
        self._initialize_solid_album_palette_worker()
        self.album_art_signature = ""
        self.album_art_source = ""
        self.album_art_lookup_state = ""
        self.last_media_refresh_at = 0.0
        self.media_refresh_interval = 0.65
        self.music_button_rects: dict[str, QRectF] = {}
        self.music_control_press_started_at: dict[str, float] = {}
        self._island_button_ripples: list[dict[str, object]] = []
        self.tasker_music_hover_auto_expand_suppressed = False
        self.music_cover_rect = QRectF()
        self.music_fullscreen_overlay: MusicSurfaceWindow | None = None
        self._music_cover_hover_motion = ArtworkHoverMotion()
        self._audio_device_motion = None
        self._audio_device_event_serial = 0
        self._music_surface_active = False
        self._island_dj_completion_seen = 0
        self._island_dj_observed_track = ""
        self._island_dj_outro_started = 0.0
        self._island_dj_outro_until = 0.0
        self._island_dj_outro_track = ""
        self._island_visible_before_music_surface = True
        self._music_windowed_preference = False
        self.music_surface_reading_lift_level = MUSIC_READING_LIFT_DEFAULT_LEVEL
        self.music_surface_reading_gradient_steps = MUSIC_READING_GRADIENT_DEFAULT_STEPS
        self.music_surface_reading_strategy = MUSIC_READING_STRATEGY_DEFAULT
        self.music_sparse_highlight_seconds_per_unit = SPARSE_MIN_SECONDS_PER_UNIT
        self.persistent_plugin = False
        self.enabled_plugin_count = 0
        self.enabled_plugin_ids = set()
        self.show_seconds = True
        self.expanded = False
        self.plugin_panel_open = False
        self.plugin_focus = 0
        self.plugin_focus_from = 0
        self.plugin_focus_target = 0
        self.plugin_focus_changed_at = time.monotonic()
        self.plugin_alert_width = 168.0
        self.side_island_style = "standard"
        self.plugin_island_style = "standard"
        self.plugin_fullscreen_alert_style = "toast"
        self.plugin_fullscreen_alert_x_offset = 0
        self.plugin_fullscreen_alert_y_offset = 0
        self.plugin_fullscreen_alert_preview_until = 0.0
        self.fullscreen_plugin_barrage_messages: list[Message] = []
        self.fullscreen_plugin_barrage_alert_key = ""
        self.plugin_alert_overlay: FullscreenPluginAlertOverlay | None = None
        self.barrage_overlay: BarrageOverlay | None = None
        self.fullscreen_plugin_alert_overlay: FullscreenPluginAlertOverlay | None = None
        self.music_island_style = "standard"
        self.music_expanded_layout_style = "standard"
        self.time_alarm_side_display = "note"
        self.pointer_pos = QPointF(0, 0)
        self.hovered_key = ""
        self.hover_started_at = 0.0
        self.hover_bounce_until = 0.0
        self.hover_armed_key = ""
        self.left_stack_at = 0.0
        self.opened_source_app = ""
        self.opened_source_until = 0.0
        self.stack_bounce_until = 0.0
        self.settings_changed_callback = None
        self.settings_getter = None
        self.settings_setter = None
        self.plugin_manager_windows: dict[str, QWidget] = {}
        self.plugin_action_rects: dict[str, QRectF] = {}
        self.plugin_time_mode = "dashboard"
        self.plugin_time_visible_module = "countdown"
        self.plugin_time_module_animation: dict[str, object] = {}
        self.plugin_panel_last_width = 0.0
        self.plugin_timer_paused_remaining_seconds = 0
        self.plugin_timer_paused_progress = 0.0
        self.plugin_countdown_entries: list[dict[str, object]] = []
        self.plugin_alarm_rows = self._default_time_alarm_rows()
        self.plugin_alarm_list_scroll_index = 0
        self.plugin_alarm_list_bottom_spacer_rows = 0.0
        self.plugin_alarm_list_max_scroll = 0.0
        self.plugin_alarm_list_rect = QRectF()
        self.plugin_alarm_visible_range = (0, 0)
        self.plugin_alarm_delete_index: int | None = None
        self.plugin_alarm_editor = self._default_alarm_editor_state()
        self.plugin_alarm_editor_focus = ""
        self.plugin_alarm_editor_input_buffers: dict[str, str] = {}
        self.plugin_alarm_editor_spin_animations: dict[str, dict[str, float | int]] = {}
        self.plugin_alarm_editor_page_animation: dict[str, object] = {}
        self.plugin_alarm_editor_reflow_animation: dict[str, object] = {}
        self.plugin_alarm_editor_rects: dict[str, QRectF] = {}
        self.plugin_alarm_editor_note_hint = ""
        self.plugin_alarm_editor_note_hint_until = 0.0
        self.plugin_alarm_due_pending: dict[str, object] = {}
        self.plugin_alarm_due_acknowledged_key = ""
        self.show_debug_overlay = False
        self.finder_material_refreshed_at = time.monotonic()
        self.manual_finder_available = False
        self.mydockfinder_path = ""
        self.mydockfinder_running = False
        self.mydockfinder_diagnostics: dict[str, object] = {}
        self.mydockfinder_listener_process_names = tuple(DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES)
        self.position_preset = "last"
        self.location_mode = "finder" if self.finder_mode else "top"
        self.top_notch_height_mode = "standard"
        self.taskbar_fusion_mode = "off"
        self.taskbar_recognition_threshold = 0.66
        self.taskbar_corner_style = "win11"
        self.taskbar_auto_hide_mode = "auto"
        self.taskbar_plugin_compact_style = "rail"
        self.taskbar_message_compact_style = "strip"
        self.taskbar_selected_drop_zone = QRectF()
        self.taskbar_drop_zones: list[QRectF] = []
        self.taskbar_attach_hot_zone = QRectF()
        self.taskbar_attach_hot_zone_host_rect = QRectF()
        self.taskbar_attach_hot_zone_active = False
        self.taskbar_position_ratio = 0.5
        self.taskbar_manual_width = 0.0
        self.taskbar_left_handle_rect = QRectF()
        self.taskbar_left_handle_visible_until = 0.0
        self.taskbar_left_handle_color = "#8E8E93"
        self.taskbar_material_color = QColor()
        self.taskbar_material_is_light = False
        self.tasker_follow_taskbar_color = False
        self.music_background_follow_previous_style = ""
        self.taskbar_material_sampled_at = 0.0
        self.taskbar_material_sample_key = ""
        self.last_taskbar_rect = QRectF()
        self.taskbar_follow_last_at = 0.0
        self.taskbar_follow_last_key: tuple[object, ...] | str = ""
        self.taskbar_hidden_by_follow = False
        self.taskbar_follow_material_last_at = 0.0
        self.taskbar_follow_force_visible_until = 0.0
        self.taskbar_autohide_visible_until = 0.0
        self.taskbar_autohide_interaction_visible = False
        self.taskbar_autohide_click_hold_visible = False
        self.taskbar_autohide_mouse_pressed = False
        self.taskbar_autohide_left_taskbar_at = 0.0
        self.taskbar_autohide_interaction_screen_key = ()
        self.taskbar_windows_snapshot_cache = {}
        self.taskbar_above_follow_active = False
        self.taskbar_above_position_ratio = 0.5
        self.taskbar_preview_visible = False
        self.taskbar_preview_follow_taskbar = True
        self.taskbar_drop_overlay = None
        self.taskbar_dock_animation = None
        self.taskbar_follow_animation = None
        self.taskbar_dock_animation_requested = False
        self.ui_font_family = self.DEFAULT_FONT_FAMILY
        self.high_quality_font_rendering = False
        self.animation_fps = 60
        self.lyric_animation_fps = 120
        self.animation_speed = 1.0
        self.empty_island_display_mode = "visible_bar"
        self.no_music_island_priority = "plugin"
        self.empty_bar_palette = "aurora"
        self.empty_bar_width = 92
        self.empty_bar_height = 14
        self.empty_bar_y_offset = 0
        self.empty_bar_opacity = 100
        self.empty_bar_custom_colors = ("#46D6FF", "#73FFA6", "#FF5A86")
        self.barrage_y_offset = 0
        self.last_barrage_preview_at = 0.0
        self.barrage_preview_force_anytime_until = 0.0
        self.barrage_display_mode = "fullscreen"
        self.barrage_font_size = 10
        self.barrage_speed = 100
        self.barrage_use_backdrop = True
        self.barrage_show_icon = True
        self.auto_expand_bounds = True
        self.expand_direction = "auto"
        self.no_lyrics_text = "暂无歌词"
        self.lyric_offset_seconds = 0.0
        self.lyric_font_size = 18
        self.lyric_elastic_strength = 100
        self.lyric_elastic_mode = "both"
        self.lyric_word_spacing = 100
        self.wave_font_weight = "bold"
        self.wave_word_fill_enabled = False
        self.wave_color_fill_enabled = False
        self.enable_dual_line_lyrics = False
        self.music_intro_cleanup_enabled = True
        self.dual_line_font_size = 12
        self.dual_line_secondary_weight = 72
        self.dual_line_secondary_spacing = 100
        self.compact_lyric_show_cover = True
        self.lyric_flow_style = "wave_stream"
        self.gelv_font_family = self.GELV_FONT_FAMILY
        self.gelv_primary_font_size = 22
        self.gelv_secondary_font_size = 12
        self.gelv_dual_line_font_size = 17
        self.gelv_initial_word_spacing = 220
        self.gelv_word_spacing = 320
        self.gelv_color_fill_enabled = False
        self.gelv_word_fill_enabled = False
        self.ember_font_weight = "normal"
        self.ember_font_size = 18
        self.ember_line_mode = "split"
        self.ember_dual_line_enabled = False
        self.ember_italic_enabled = False
        self.ember_show_cover = True
        self.ember_word_fill_enabled = True
        self.ember_color_fill_enabled = False
        self.ember_back_gray_mix = 62
        self.ember_color_back_gray_mix = 0
        self.ember_back_opacity = 58
        self.ember_blur_panel_strength = 18
        self.ember_lyric_offset_seconds = 0.0
        self.no_lyrics_display_mode = "text"
        self.album_island_cover_count = 5
        self.album_island_cover_size = 30
        self.album_island_compact_cover_size = 30
        self.album_island_expanded_cover_size = 72
        self.album_island_spacing = 112
        self.album_island_secondary_style = "perspective_dim"
        self.album_island_perspective_amount = 100
        self.lyric_transition_kind = ""
        self.lyric_transition_distance = 0
        self.lyric_last_sync_position = 0.0
        self.lyric_last_phase_index = -1
        self.lyric_last_phase_progress = 0.0
        self.lyric_last_phase_at = 0.0
        self.lyric_last_phase_timing_kind = ""
        self._lyric_layout_cache: dict[tuple[object, ...], LyricLayout] = {}
        self._representative_lyric_width_cache: dict[tuple[object, ...], float] = {}
        self._wave_reading_renderer = LyricReadingRenderer()
        self._wave_line_spring = LogicalLineSpringChain()
        self._scaled_album_art_cache: dict[tuple[str, int, int], QImage] = {}
        self._album_image_signature_cache: dict[int, str] = {}
        self._svg_renderer_cache: dict[str, QSvgRenderer] = {}
        self.font_shrink_scale = 1.0
        self.stack_direction = 1
        self.messages: list[Message] = []
        self.barrage_preview_messages: list[Message] = []
        self._seen_provider_message_keys: set[str] = set()

        self._apply_visual_data(visual_data, initial=True)
        self.current_audio_bands = (0.44, 0.48, 0.36)
        self.live_audio_available = False
        self.wave_values = [0.0] * 36
        self.wave_targets = [random.uniform(0.12, 0.72) for _ in self.wave_values]
        self._music_wave_update_count = 0
        self._music_wave_first_updated_at = 0.0
        self._wave_values_updated_at = 0.0
        self.scroll_phase = random.random() * 10.0
        self.ambient_phase = random.random() * 10.0
        self.ambient_intensity = 0.0
        self.music_ambience_style = "sea_level"
        self.solid_album_background_fallback = "#16BCFF"
        self.yaoguang_outer_color = "#6C35F2"
        self.yaoguang_blur_strength = 58
        self.yaoguang_color_source = "custom"
        self.yaoguang_album_inverse_enabled = False
        self.yaoguang_height_mode = "regular"
        self._last_theme_follow_dark = None
        self._last_theme_follow_checked_at = 0.0
        self.music_flow_intensity = 0.74
        self.music_reactive_background_enabled = False
        self.music_reactive_background_intensity = 55
        self._music_reactive_audio_bands = self.current_audio_bands
        self._music_reactive_audio_sampled_at = -999.0
        self._audio_reactive_runtime = AudioReactiveRuntime(
            enabled=self.music_reactive_background_enabled,
            intensity_percent=self.music_reactive_background_intensity,
        )
        self._music_reactive_snapshot_cache = None
        self._music_reactive_snapshot_styles = frozenset()
        self._live_audio_available_checked_at = -999.0
        self._live_audio_available_cached = False
        self.compact_duotone_balance = 50
        self.expanded_duotone_balance = 50
        self.diffraction_frequency = 0.30

        self.message_samples = [
            (
                message.app,
                message.sender,
                message.text,
                QColor(*message.name_color.rgba()),
                QColor(*message.avatar_color.rgba()),
            )
            for message in visual_data.messages
        ]
        self.plugin_samples = [
            (
                plugin.label,
                plugin.detail,
                plugin.summary,
                QColor(*plugin.color.rgba()),
                plugin.icon,
                plugin.plugin_id,
                plugin.status,
                plugin.alert_title,
                plugin.alert_body,
                plugin.alert_priority,
                plugin.progress,
            )
            for plugin in visual_data.plugins
        ]
        self.sample_index = 0

        self.motions: dict[str, SpringRect] = {}
        self.group_frame_motion: SpringRect | None = None
        self.group_frame_mode_key = ""
        self.group_frame_target_rect = QRectF()
        self.group_frame_target_mode_key = ""
        self.group_frame_mask_path: QPainterPath | None = None
        self.taskbar_notification_frame_motion: SpringRect | None = None
        self.taskbar_notification_frame_rect = QRectF()
        self.finder_hover_rect = QRectF()
        self.last_mask_rect = QRectF()
        self.last_frame_at = time.monotonic()
        self.last_render_update_at = self.last_frame_at
        self.visual_frame_diagnostics = VisualFrameDiagnosticsRecorder()
        self.runtime_diagnostics_trace = RuntimeDiagnosticsTraceRecorder()
        self.last_runtime_diagnostics_trace_at = 0.0

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.start(self._timer_interval_ms())

        if self.message_samples:
            self.add_message()
            QTimer.singleShot(650, self.add_message)
            QTimer.singleShot(1300, self.add_message)

    def _apply_visual_data(self, visual_data: IslandVisualData, initial: bool = False) -> bool:
        if bool(getattr(self, "_runtime_extras_retired", False)):
            if visual_data.messages or visual_data.plugins:
                visual_data = replace(visual_data, messages=(), plugins=())
            self.messages = []
        now = time.monotonic()
        track = visual_data.track
        previous_track_id = getattr(self, "media_track_id", "")
        previous_is_playing = bool(getattr(self, "media_is_playing", False))
        previous_title = str(getattr(self, "track_title", "") or "")
        previous_artist = str(getattr(self, "artist", "") or "")
        previous_lyrics = tuple(getattr(self, "lyrics", ()))
        previous_album_art_key = getattr(self, "album_art", QImage()).cacheKey()
        previous_plugin_samples = tuple(getattr(self, "plugin_samples", ()))
        previous_messages = tuple(
            (
                str(getattr(message, "app", "") or ""),
                str(getattr(message, "sender", "") or ""),
                str(getattr(message, "text", "") or ""),
            )
            for message in getattr(self, "messages", ())
        )
        previous_album_island = bool(self._should_paint_album_island()) if hasattr(self, "lyrics") else False
        incoming_track_id = track.track_id or f"{track.source_app}:{track.title}:{track.artist}"
        same_visible_track = previous_title == str(track.title or "") and previous_artist == str(track.artist or "")
        message_refresh = bool(getattr(visual_data, "messages", ()))
        previous_duration = max(0.0, float(getattr(self, "media_duration_seconds", 0.0) or 0.0))
        incoming_duration = max(0.0, float(getattr(track, "duration_seconds", 0.0) or 0.0))
        same_media_generation = (
            previous_duration <= 0.0
            or incoming_duration <= 0.0
            or abs(incoming_duration - previous_duration)
            <= max(5.0, max(previous_duration, incoming_duration) * 0.04)
        )
        track_changed = presentation_media_visual_track_changed(
            initial=initial,
            incoming_track_id=incoming_track_id,
            previous_track_id=previous_track_id,
            same_visible_track=same_visible_track,
            message_refresh=message_refresh,
            same_media_generation=same_media_generation,
        )
        flow_direction = 1
        if track_changed and not initial:
            flow_direction = self._prepare_album_flow_for_track_change(incoming_track_id)
        self.track_title = track.title
        self.artist = track.artist
        self.album_color = QColor(*track.album_color.rgba())
        self.secondary_color = QColor(*track.secondary_color.rgba())
        if track_changed and str(getattr(self, "music_surface_transition_mode", "") or "") == "white_bridge":
            self._retarget_white_bridge_to_current_album()
        self.audio_frames = list(track.audio_frames)
        incoming_is_playing = bool(track.is_playing)
        incoming_position = max(0.0, float(track.position_seconds))
        incoming_position_source = str(getattr(track, "position_source", "windows-media") or "windows-media")
        incoming_is_playing, incoming_position = self._reconcile_music_playback_toggle(
            incoming_track_id, incoming_is_playing, incoming_position,
            float(getattr(track, "position_sampled_at", 0.0) or 0.0), now,
            track_changed=track_changed,
        )
        previous_position = self._estimated_media_position(now) if hasattr(self, "media_position_sync_at") else 0.0
        previous_sync_at = float(getattr(self, "media_position_sync_at", now))
        position_plan = media_position_update_plan(
            track_changed=track_changed,
            incoming_is_playing=incoming_is_playing,
            incoming_position_seconds=incoming_position,
            incoming_position_source=incoming_position_source,
            incoming_position_confidence=getattr(track, "position_confidence", 1.0),
            incoming_position_interactive=getattr(track, "position_interactive", False),
            incoming_source_foreground=getattr(track, "source_app_foreground", False),
            previous_position_seconds=previous_position,
            previous_sync_at=previous_sync_at,
            previous_raw_position_seconds=getattr(self, "media_raw_position_seconds", None),
            previous_raw_position_source=getattr(self, "media_raw_position_source", ""),
            now=now,
        )
        self.media_is_playing = incoming_is_playing
        self.media_duration_seconds = max(0.0, float(track.duration_seconds))
        self.media_position_seconds = position_plan.position_seconds
        if position_plan.listened_delta_seconds > 0.0:
            self.listened_seconds_total = float(getattr(self, "listened_seconds_total", 0.0)) + position_plan.listened_delta_seconds
        self.media_position_sync_at = now
        self.media_raw_position_seconds = position_plan.raw_position_seconds
        self.media_raw_position_source = position_plan.position_source
        self.media_track_id = incoming_track_id
        self.media_source_app = track.source_app
        self.media_position_source = position_plan.position_source
        self.media_position_sampled_at = float(getattr(track, "position_sampled_at", 0.0) or 0.0)
        self.media_position_observed_seconds = getattr(track, "position_observed_seconds", None)
        self.media_position_plan_reason = position_plan.reason
        self.media_position_confidence = position_plan.position_confidence
        self.media_position_interactive = position_plan.position_interactive
        self.media_source_foreground = position_plan.source_foreground
        self.media_app_lyric_offset_seconds = max(-12.0, min(12.0, float(getattr(track, "lyric_offset_seconds", 0.0) or 0.0)))
        self.media_lyric_offset_adapter = str(getattr(track, "lyric_offset_adapter", "system") or "system").casefold()
        self.media_refresh_interval = position_plan.refresh_interval_seconds
        if track_changed:
            self._media_strong_tracking_until = now + 10.0
        self.lyrics_source = track.lyrics_source
        had_timed_lyrics = bool(getattr(self, "timed_lyrics", ()))
        previous_timing_rows = getattr(self, "timed_lyrics", [])
        previous_timing_units = getattr(self, "timed_lyric_units", [])
        previous_timing_kinds = getattr(self, "timed_lyric_timing_kinds", [])
        previous_timing = (
            tuple(previous_timing_rows), tuple(previous_timing_units), tuple(previous_timing_kinds),
        )
        (
            self.timed_lyrics,
            self.timed_lyric_units,
            self.timed_lyric_timing_kinds,
        ) = self._coalesced_timed_lyric_data(track.timed_lyrics)
        timing_changed = previous_timing != (
            tuple(self.timed_lyrics), tuple(self.timed_lyric_units), tuple(self.timed_lyric_timing_kinds),
        )
        if not timing_changed:
            # Surface caches key immutable metadata by its container identity.
            # Equal provider refreshes should not remap every lyric unit at
            # the next visible paint; a genuine timing upgrade still replaces it.
            self.timed_lyrics = previous_timing_rows
            self.timed_lyric_units = previous_timing_units
            self.timed_lyric_timing_kinds = previous_timing_kinds
        timed_lyrics_appeared = bool(self.timed_lyrics) and not had_timed_lyrics and not track_changed
        self.plugin_samples = [
            (
                plugin.label,
                plugin.detail,
                plugin.summary,
                QColor(*plugin.color.rgba()),
                plugin.icon,
                plugin.plugin_id,
                plugin.status,
                plugin.alert_title,
                plugin.alert_body,
                plugin.alert_priority,
                plugin.progress,
            )
            for plugin in visual_data.plugins
        ]
        self._ingest_provider_messages(visual_data)
        raw_lyrics = [text for _time, text in self.timed_lyrics] if self.timed_lyrics else list(track.lyrics)
        if not self.timed_lyrics and (not raw_lyrics or raw_lyrics == ["暂无歌词"]):
            raw_lyrics = [getattr(self, "no_lyrics_text", "暂无歌词")]
        self.lyrics = raw_lyrics or [track.title or "正在播放"]
        if not self.lyrics:
            self.lyrics = ["正在播放"]
        if tuple(self.lyrics) != previous_lyrics:
            self._clear_lyric_metrics_cache()
        queue_signature = self._album_queue_signature(getattr(track, "album_queue", ()))
        queue_changed = queue_signature != str(getattr(self, "album_queue_signature", "") or "")
        if (flow_direction >= 0 or not getattr(self, "album_queue", None)) and (track_changed or queue_changed or not getattr(self, "album_queue", None)):
            self.album_queue = self._album_queue_from_track(getattr(track, "album_queue", ()))
            self.album_queue_signature = queue_signature
        current_album_island = bool(self._should_paint_album_island())
        if current_album_island and (track_changed or current_album_island != previous_album_island):
            self.album_island_transition_started_at = now
            self.album_island_transition_reason = "track" if track_changed else "mode"
            self.album_island_transition_direction = flow_direction
        image = QImage()
        art_signature = self._album_art_bytes_signature(track.album_art_bytes)
        cover_reused = bool(
            art_signature
            and art_signature == str(getattr(self, "album_art_signature", "") or "")
            and not getattr(self, "album_art", QImage()).isNull()
        )
        cover_loaded = False
        if cover_reused:
            cover_loaded = True
        elif track.album_art_bytes:
            cover_loaded = image.loadFromData(track.album_art_bytes) and not image.isNull()
        plan = album_art_update_plan(
            incoming_signature=art_signature,
            current_signature=getattr(self, "album_art_signature", ""),
            cover_loaded=cover_loaded,
            black_placeholder_loaded=cover_loaded and self._looks_like_black_album_placeholder(image),
            has_existing_cover=not getattr(self, "album_art", QImage()).isNull(),
            track_changed=track_changed,
            same_visible_track=same_visible_track,
            message_refresh=message_refresh,
        )
        if plan.should_set_loaded_cover and not cover_reused:
            self.album_art = self._bounded_album_image(image, self.CURRENT_ALBUM_ART_MAX_SIDE, smooth=True)
            self.album_art_signature = plan.next_signature or art_signature
            self.album_art_source = str(getattr(track, "album_art_source", "") or "")
            self._scaled_album_art_cache.clear()
        elif plan.action == "reuse" and cover_loaded:
            self.album_art_source = str(getattr(track, "album_art_source", "") or "")
        elif plan.should_clear_current_cover:
            self.album_art = QImage()
            self.album_art_signature = ""
            self.album_art_source = ""
            self._scaled_album_art_cache.clear()
        self.album_art_lookup_state = str(getattr(track, "album_art_lookup_state", "") or "")
        if track_changed:
            self.lyric_index = 0 if self.timed_lyrics else min(1, len(self.lyrics) - 1)
            self.prev_lyric_index = self.lyric_index
            self.lyric_changed_at = now
            self.audio_started_at = now
        if track_changed or timing_changed:
            self.lyric_last_phase_index = -1
            self.lyric_last_phase_progress = 0.0
            self.lyric_last_phase_at = now
            self.lyric_last_phase_timing_kind = ""
            self.ember_last_phase_index = -1
            self.ember_last_phase_progress = 0.0
            self.ember_last_phase_at = now
            surface = getattr(self, "music_fullscreen_overlay", None)
            if surface is not None:
                surface.update()
        self._sync_timed_lyrics(now, allow_backward=timed_lyrics_appeared or timing_changed)
        current_messages = tuple(
            (
                str(getattr(message, "app", "") or ""),
                str(getattr(message, "sender", "") or ""),
                str(getattr(message, "text", "") or ""),
            )
            for message in getattr(self, "messages", ())
        )
        return bool(
            track_changed
            or self.media_is_playing != previous_is_playing
            or self.track_title != previous_title
            or self.artist != previous_artist
            or tuple(getattr(self, "lyrics", ())) != previous_lyrics
            or timed_lyrics_appeared
            or timing_changed
            or previous_plugin_samples != tuple(getattr(self, "plugin_samples", ()))
            or previous_messages != current_messages
            or getattr(self, "album_art", QImage()).cacheKey() != previous_album_art_key
        )

    def _looks_like_black_album_placeholder(self, image: QImage) -> bool:
        if image.isNull():
            return False
        sample = image.convertToFormat(QImage.Format.Format_ARGB32)
        if sample.width() > 12 or sample.height() > 12:
            sample = sample.scaled(12, 12, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
        total_luma = 0.0
        max_channel = 0
        max_chroma = 0
        min_luma = 255
        max_luma = 0
        opaque_pixels = 0
        pixel_count = max(1, sample.width() * sample.height())
        for y in range(sample.height()):
            for x in range(sample.width()):
                color = sample.pixelColor(x, y)
                if color.alpha() < 220:
                    continue
                opaque_pixels += 1
                luma = int(color.red() * 0.2126 + color.green() * 0.7152 + color.blue() * 0.0722)
                total_luma += luma
                min_luma = min(min_luma, luma)
                max_luma = max(max_luma, luma)
                max_channel = max(max_channel, color.red(), color.green(), color.blue())
                max_chroma = max(max_chroma, max(color.red(), color.green(), color.blue()) - min(color.red(), color.green(), color.blue()))
        if opaque_pixels < pixel_count * 0.70:
            return False
        average_luma = total_luma / max(1, opaque_pixels)
        return average_luma <= 30.0 and max_channel <= 48 and max_chroma <= 12 and (max_luma - min_luma) <= 34

    def _bounded_album_image(self, image: QImage, max_side: int | float | None = None, smooth: bool = False) -> QImage:
        if not isinstance(image, QImage) or image.isNull():
            return QImage()
        try:
            limit = int(max(24, float(max_side if max_side is not None else self.STORED_ALBUM_ART_MAX_SIDE)))
        except (TypeError, ValueError):
            limit = int(self.STORED_ALBUM_ART_MAX_SIDE)
        width = int(image.width())
        height = int(image.height())
        if width <= 0 or height <= 0:
            return QImage()
        if max(width, height) <= limit:
            return QImage(image)
        mode = Qt.TransformationMode.SmoothTransformation if smooth else Qt.TransformationMode.FastTransformation
        return image.scaled(limit, limit, Qt.AspectRatioMode.KeepAspectRatio, mode)

    def _ingest_provider_messages(self, visual_data: IslandVisualData):
        if bool(getattr(self, "_runtime_extras_retired", False)):
            return
        latest_by_app: dict[str, tuple[str, object]] = {}
        app_order: list[str] = []
        for message in visual_data.messages:
            app_name = str(getattr(message, "app", "") or "").strip()
            if not app_name:
                continue
            app_key = app_name.casefold()
            if app_key in latest_by_app:
                try:
                    app_order.remove(app_key)
                except ValueError:
                    pass
            latest_by_app[app_key] = (app_name, message)
            app_order.append(app_key)

        for app_key in app_order:
            app_name, message = latest_by_app[app_key]
            key = f"{app_name}\0{message.sender}\0{message.text}"
            if key in self._seen_provider_message_keys:
                continue
            self._seen_provider_message_keys.add(key)
            avatar_image = QImage()
            avatar_bytes = getattr(message, "avatar_image_bytes", b"") or b""
            if avatar_bytes:
                avatar_image.loadFromData(avatar_bytes)
            msg = Message(
                app_name,
                message.sender,
                message.text,
                QColor(*message.name_color.rgba()),
                QColor(*message.avatar_color.rgba()),
                avatar_image=avatar_image,
                serial=len(self._seen_provider_message_keys),
            )
            if self._is_transient_new_message_summary(msg):
                msg.expires_at = time.monotonic() + 20.0
            self.messages = [
                existing for existing in self.messages
                if str(getattr(existing, "app", "") or "").strip().casefold() != app_key
            ]
            msg.barrage_started_at = float(getattr(self, "barrage_clock", 0.0)) + 0.05 * len(self.messages)
            self.messages.insert(0, msg)
        self.messages = self.messages[:8]

    def _is_transient_new_message_summary(self, message: Message) -> bool:
        text = str(getattr(message, "text", "") or "").strip()
        return bool(text in {"收到新消息", "新消息"} or re.fullmatch(r"\d{1,4}\s*(?:条|條)\s*新消息", text))

    def _prune_expired_messages(self, now: float | None = None):
        now = time.monotonic() if now is None else float(now)
        self.messages = [
            message
            for message in getattr(self, "messages", [])
            if not (float(getattr(message, "expires_at", 0.0) or 0.0) > 0.0 and now >= float(getattr(message, "expires_at", 0.0) or 0.0))
        ]

    def _coalesced_timed_lyrics(self, timed_lyrics: list[tuple[float, str]]) -> list[tuple[float, str]]:
        rows, _units, _kinds = self._coalesced_timed_lyric_data(timed_lyrics)
        return rows

    def _coalesced_timed_lyric_data(self, timed_lyrics) -> tuple[list[tuple[float, str]], list[tuple[object, ...]], list[str]]:
        if not timed_lyrics:
            return [], [], []
        merged: list[tuple[float, str]] = []
        merged_units: list[tuple[object, ...]] = []
        merged_kinds: list[str] = []
        current_time: float | None = None
        current_parts: list[str] = []
        current_units: tuple[object, ...] = ()
        current_kind = "line"

        def flush():
            if current_time is None or not current_parts:
                return
            if len(current_parts) == 1:
                text = current_parts[0]
            else:
                text = "\n".join(current_parts)
            merged.append((current_time, text))
            merged_units.append(current_units)
            merged_kinds.append(current_kind)

        for item in timed_lyrics:
            if hasattr(item, "time_seconds"):
                raw_time = item.time_seconds
                raw_text = item.text
                raw_units = tuple(getattr(item, "units", ()) or ())
                kind = str(getattr(item, "timing_kind", "line") or "line")
            else:
                raw_time, raw_text = item
                raw_units = ()
                kind = "line"
            parts = [" ".join(part.split()) for part in str(raw_text or "").splitlines()]
            parts = [part for part in parts if part]
            if not parts:
                continue
            text = "\n".join(parts)
            # Display normalization can remove or merge whitespace.  Refuse
            # timings whose concatenated text no longer fits the primary row.
            units_text = " ".join("".join(str(getattr(unit, "text", "") or "") for unit in raw_units).split())
            units = raw_units if units_text == parts[0] else ()
            if kind == "unit" and not units:
                kind = "line"
            line_time = max(0.0, float(raw_time))
            if current_time is None:
                current_time = line_time
                current_parts = [text]
                current_units = units
                current_kind = kind
                continue
            same_timestamp = abs(line_time - current_time) <= 0.0005
            separate_voice = same_timestamp and bool(analyze_duet_lyric_rows(
                ((current_time, current_parts[0]), (line_time, text)),
                singer_names=str(getattr(self, "artist", "") or ""),
            ).groups)
            # Quick sentences can be only tens of milliseconds apart. Only
            # identical timestamps are bilingual subrows. Verified duet
            # voices keep their own units even at equal source timestamps.
            if same_timestamp and not separate_voice:
                if units and not current_units and text != current_parts[0]:
                    # A local dual-line file may put its translation first.
                    # The timed original must lead the display group so its
                    # units still correspond to the primary lyric text.
                    if text in current_parts:
                        current_parts.remove(text)
                    current_parts.insert(0, text)
                    current_units = units
                    current_kind = kind
                elif text not in current_parts:
                    current_parts.append(text)
                elif text == current_parts[0] and units and not current_units:
                    current_units = units
                    current_kind = kind
                continue
            flush()
            current_time = line_time
            current_parts = [text]
            current_units = units
            current_kind = kind
        flush()
        return merged, merged_units, merged_kinds

    def _refresh_media_data(self, now: float) -> bool:
        interval = float(getattr(self, "media_refresh_interval", 0.65))
        if now < float(getattr(self, "_media_strong_tracking_until", 0.0)):
            interval = min(interval, 0.12)
        if now - getattr(self, "last_media_refresh_at", 0.0) < interval:
            return False
        self.last_media_refresh_at = now
        try:
            visual_data = self.data_provider.get_visual_data()
        except Exception:
            return False
        return bool(self._apply_visual_data(visual_data))

    def _estimated_media_position(self, now: float) -> float:
        base = max(0.0, float(getattr(self, "media_position_seconds", 0.0)))
        sync_at = float(getattr(self, "media_position_sync_at", now))
        duration = max(0.0, float(getattr(self, "media_duration_seconds", 0.0)))
        if bool(getattr(self, "media_is_playing", False)):
            base += max(0.0, now - sync_at)
        source = str(getattr(self, "media_position_source", "") or "")
        if source.endswith("-uia-lyrics"):
            # A visible lyric row provides a coarse position *inside* that row.
            # Wall-clock projection must not claim the next row before the
            # player supplies a fresh lyric or precise progress observation.
            timed = getattr(self, "timed_lyrics", ()) or ()
            if timed:
                anchor = max(0.0, float(getattr(self, "media_position_seconds", 0.0) or 0.0))
                index = active_index_for_position(timed, anchor, lead_seconds=0.0)
                if index + 1 < len(timed):
                    next_start = float(timed[index + 1][0])
                    base = min(base, max(anchor, next_start - 0.08))
        if duration > 0.0:
            return min(duration, base)
        return base

    def _effective_lyric_offset_seconds(self, include_output_latency: bool = True) -> float:
        global_offset = float(getattr(self, "lyric_offset_seconds", 0.0) or 0.0)
        app_offset = float(getattr(self, "media_app_lyric_offset_seconds", 0.0) or 0.0)
        output_delay = 0.0
        if include_output_latency:
            from local.music_output_clock import OutputLatencyClock
            generation = None
            getter = getattr(getattr(self, "data_provider", None), "get_audio_route_snapshot", None)
            if callable(getter):
                route = getter()
                if route.state in {"casting", "reconfiguring"}:
                    output_delay = max(0.0, min(5.0, float(route.cast_latency_seconds)))
                    generation = (route.group_id, route.active_id, route.cast_source_id)
            clock = getattr(self, "_lyric_output_latency_clock", None)
            if clock is None:
                clock = self._lyric_output_latency_clock = OutputLatencyClock()
            output_delay = clock.sample(output_delay, time.monotonic(), generation,
                                        playing=bool(getattr(self, "media_is_playing", False)))
            # Manual edits and route transitions re-anchor ink. Ordinary
            # estimate slewing preserves each view's in-flight focus pose.
            self._lyric_output_compensation_anchor = (global_offset, app_offset, generation)
        return max(-24.0, min(24.0, global_offset + app_offset - output_delay))

    def _stable_lyric_source_position(self, now: float) -> float:
        """Validate source observations before adding display-only latency.

        All island word effects consume this clock. Source seeking and player
        progress continue to use _estimated_media_position without output delay.
        """
        position = self._estimated_media_position(now)
        sync_at = getattr(self, "media_position_sync_at", None)
        if sync_at is None:
            return position
        source = str(getattr(self, "media_position_source", "") or "")
        observed_at = float(getattr(self, "media_position_sampled_at", 0.0) or 0.0)
        legacy_raw = float(getattr(self, "media_raw_position_seconds", getattr(self, "media_position_seconds", position)) or 0.0)
        raw = legacy_raw
        if hasattr(self, "media_position_observed_seconds"):
            observed_position = self.media_position_observed_seconds
            # No original evidence must not become an advancing observation
            # through delivery projection. NaN is only a confirmation marker;
            # the source position passed to the clock remains unchanged.
            raw = float(observed_position) if observed_position is not None else float("nan")
        app = str(getattr(self, "media_source_app", "") or "")
        sample = (observed_at if observed_at > 0.0 else sync_at,
                  0.0 if observed_at > 0.0 else legacy_raw, source, app)
        prior = getattr(self, "_island_lyric_source_sample", None)
        playing = bool(getattr(self, "media_is_playing", False))
        explicit_seek = (
            source == "local-seek" and (prior is None or prior[2] != source or sample != prior)
        ) or (
            not playing and getattr(self, "_island_lyric_source_playing", None) is False
            and source in {"windows-media", "interactive"}
            and observed_at > 0.0 and prior is not None
            and math.isfinite(raw)
            and prior[2:] == sample[2:] and sample != prior
        )
        self._island_lyric_source_sample = sample
        self._island_lyric_source_playing = playing
        clock = getattr(self, "_island_lyric_source_clock", None)
        if clock is None:
            clock = self._island_lyric_source_clock = MusicLyricMotion()
        ceiling = None
        timed = getattr(self, "timed_lyrics", ()) or ()
        if source.endswith("-uia-lyrics") and timed:
            anchor = float(getattr(self, "media_position_seconds", position) or 0.0)
            index = active_index_for_position(timed, anchor, lead_seconds=0.0)
            if index + 1 < len(timed):
                ceiling = max(anchor, float(timed[index + 1][0]) - 0.08)
        clock.frame([], position, now, str(getattr(self, "media_track_id", "") or ""),
                    sample_id=sample, sample_position=raw, playing=playing,
                    explicit_seek=explicit_seek, position_ceiling=ceiling,
                    source_generation=(app, source))
        return float(clock.last_source_position)

    def _prepare_island_lyric_offset(self, now: float, offset: float) -> bool:
        previous = getattr(self, "_island_last_lyric_offset", offset)
        self._island_last_lyric_offset = offset
        changed = not math.isclose(previous, offset, abs_tol=1e-7)
        anchor = getattr(self, "_lyric_output_compensation_anchor", None)
        previous_anchor = getattr(self, "_island_lyric_offset_anchor", anchor)
        self._island_lyric_offset_anchor = anchor
        if changed and (anchor is None or anchor != previous_anchor):
            # Compensation is presentation state, not a player seek. Drop
            # cached ink only when it would otherwise prevent re-calibration.
            self.lyric_last_phase_index = self.ember_last_phase_index = -1
            self.lyric_last_phase_progress = self.ember_last_phase_progress = 0.0
            self.lyric_last_phase_at = self.ember_last_phase_at = now
            self.lyric_last_phase_timing_kind = ""
        return changed

    def _looks_like_seek_to_start(self, raw_position: float, previous_position: float, previous_raw_position: float | None) -> bool:
        return media_position_looks_like_seek_to_start(raw_position, previous_position, previous_raw_position)

    def _sync_timed_lyrics(self, now: float, allow_backward: bool = False) -> bool:
        if not self.timed_lyrics:
            return False
        offset = self._effective_lyric_offset_seconds()
        previous_offset = getattr(self, "_island_last_sync_offset", offset)
        self._island_last_sync_offset = offset
        offset_changed = not math.isclose(previous_offset, offset, abs_tol=1e-7)
        self._prepare_island_lyric_offset(now, offset)
        position = max(0.0, self._stable_lyric_source_position(now) + offset)
        previous_index = int(getattr(self, "lyric_index", 0))
        # The normal island uses a 120 ms visual lead. A coarse UIA lyric-row
        # anchor is already bounded to that observed row, so adding the lead
        # here would advance 40 ms before the next row is actually observed.
        source = str(getattr(self, "media_position_source", "") or "")
        lead = 0.0 if source.endswith("-uia-lyrics") else 0.12
        index = active_index_near_position(
            self.timed_lyrics, position, previous_index, lead_seconds=lead,
        )
        if (index < previous_index and bool(getattr(self, "media_is_playing", False))
                and not allow_backward and not offset_changed
                and getattr(self, "_island_lyric_source_clock", None) is None):
            current_start = float(self.timed_lyrics[min(previous_index, len(self.timed_lyrics) - 1)][0])
            last_position = float(getattr(self, "lyric_last_sync_position", 0.0))
            small_backstep = position >= max(0.0, current_start - 1.05) or 0.0 < last_position - position < 2.4
            if small_backstep:
                index = previous_index
        self.lyric_last_sync_position = position
        if index != self.lyric_index and index < len(self.lyrics):
            distance = abs(index - self.lyric_index)
            self.prev_lyric_index = self.lyric_index
            self.lyric_index = index
            self.lyric_changed_at = now
            self.lyric_transition_distance = distance
            self.lyric_transition_kind = "seek" if distance > 1 else "line"
            self.lyric_last_phase_index = -1
            self.lyric_last_phase_progress = 0.0
            self.lyric_last_phase_at = now
            self.lyric_last_phase_timing_kind = ""
            return True
        return False

    def _u(self, value: float) -> float:
        return value * self.VISUAL_SCALE

    def _font_size(self, size: float, shrink: bool = True) -> int:
        shrink_scale = getattr(self, "font_shrink_scale", 1.0) if shrink else 1.0
        return max(1, int(round(size * self.VISUAL_SCALE * shrink_scale)))

    def _font(self, size: float, weight: QFont.Weight = QFont.Weight.Normal, shrink: bool = True) -> QFont:
        font = QFont()
        font.setFamilies(self._font_families())
        font.setPointSize(self._font_size(size, shrink=shrink))
        font.setWeight(weight)
        if getattr(self, "high_quality_font_rendering", False):
            font.setStyleStrategy(QFont.StyleStrategy.PreferQuality)
        else:
            font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        return font

    def _font_families(self) -> list[str]:
        family = str(getattr(self, "ui_font_family", self.DEFAULT_FONT_FAMILY)).strip() or self.DEFAULT_FONT_FAMILY
        families = [family]
        for fallback in self.CHINESE_FONT_FALLBACKS:
            if fallback not in families:
                families.append(fallback)
        return families

    def _wave_font_weight_key(self) -> str:
        value = str(getattr(self, "wave_font_weight", "bold") or "bold").strip().lower()
        return "heavy" if value == "heavy" else "bold"

    def _ember_font_weight_key(self) -> str:
        value = str(getattr(self, "ember_font_weight", "normal") or "normal").strip().lower()
        return "bold" if value in {"bold", "heavy", "900w", "900"} else "normal"

    def _wave_stream_font_families(self) -> list[str]:
        # Both reading styles share one layout and renderer. Gelv changes
        # only the embedded lyric typeface, including measurement caches.
        if self._gelv_enabled():
            return self._gelv_font_families()
        primary = self.WAVE_FONT_FAMILY_HEAVY if self._wave_font_weight_key() == "heavy" else self.WAVE_FONT_FAMILY_BOLD
        families = [primary]
        for family in (self.WAVE_FONT_FAMILY_BOLD, *self.WAVE_FONT_FALLBACK_FAMILIES):
            if family not in families:
                families.append(family)
        return families

    def _wave_stream_font(self, size: float, weight: QFont.Weight = QFont.Weight.Normal, shrink: bool = True) -> QFont:
        font = QFont()
        font.setFamilies(self._wave_stream_font_families())
        font.setPointSize(self._font_size(size, shrink=shrink))
        if self._gelv_enabled():
            font.setWeight(max(weight, QFont.Weight.ExtraBold))
        elif self._wave_font_weight_key() == "heavy":
            font.setWeight(max(weight, QFont.Weight.Black))
        else:
            font.setWeight(max(weight, QFont.Weight.Bold))
        if getattr(self, "high_quality_font_rendering", False):
            font.setStyleStrategy(QFont.StyleStrategy.PreferQuality)
        else:
            font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        return font

    def _gelv_font_families(self) -> list[str]:
        families = [self.GELV_FONT_FAMILY]
        for family in self.GELV_FONT_FALLBACK_FAMILIES:
            if family not in families:
                families.append(family)
        return families

    def _gelv_font(self, size: float, weight: QFont.Weight = QFont.Weight.Normal, shrink: bool = True) -> QFont:
        font = QFont()
        font.setFamilies(self._gelv_font_families())
        font.setPointSize(self._font_size(size, shrink=shrink))
        font.setWeight(max(weight, QFont.Weight.ExtraBold))
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        return font

    def _ember_font_families(self, back_layer: bool = False) -> list[str]:
        primary = self.EMBER_FONT_FAMILY_BOLD if self._ember_font_weight_key() == "bold" else self.EMBER_FONT_FAMILY_NORMAL
        families: list[str] = []
        for family in (primary, *self.EMBER_FONT_FALLBACK_FAMILIES):
            if family not in families:
                families.append(family)
        return families

    def _ember_font(self, size: float, back_layer: bool = False, shrink: bool = True) -> QFont:
        font = QFont()
        font.setFamilies(self._ember_font_families(back_layer=back_layer))
        point_size = self._font_size(size, shrink=shrink)
        if back_layer:
            point_size += 2
        font.setPointSize(point_size)
        font.setItalic(bool(getattr(self, "ember_italic_enabled", False)))
        font.setWeight(QFont.Weight.Bold if self._ember_font_weight_key() == "bold" else QFont.Weight.Normal)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        return font

    @classmethod
    def _alibaba_puhuiti_asset_paths(cls) -> list[Path]:
        roots = [ROOT_DIR / "assets" / "fonts" / "AlibabaPuHuiTi"]
        paths: list[Path] = []
        for root in roots:
            if not root.exists():
                continue
            paths.extend(sorted(root.rglob("AlibabaPuHuiTi-Bold.ttf")))
            paths.extend(sorted(root.rglob("AlibabaPuHuiTi-Heavy.ttf")))
        return paths

    @classmethod
    def _noto_serif_sc_asset_paths(cls) -> list[Path]:
        roots = [ROOT_DIR / "assets" / "fonts" / "NotoSerifSC"]
        paths: list[Path] = []
        for root in roots:
            if not root.exists():
                continue
            paths.extend(sorted(root.rglob("NotoSerifSC-ExtraBold.ttf")))
        return paths

    @classmethod
    def _jiangcheng_xiesong_asset_paths(cls) -> list[Path]:
        roots = [ROOT_DIR / "assets" / "fonts" / "JiangChengXieSong"]
        paths: list[Path] = []
        for root in roots:
            if not root.exists():
                continue
            paths.extend(sorted(root.rglob("JiangChengXieSong-700W.ttf")))
            paths.extend(sorted(root.rglob("JiangChengXieSong-900W.ttf")))
        return paths

    @classmethod
    def load_alibaba_puhuiti_fonts(cls):
        if cls._alibaba_puhuiti_loaded:
            return
        for path in cls._alibaba_puhuiti_asset_paths():
            try:
                QFontDatabase.addApplicationFont(str(path))
            except Exception:
                continue
        cls._alibaba_puhuiti_loaded = True

    @classmethod
    def load_noto_serif_sc_fonts(cls):
        if cls._noto_serif_sc_loaded:
            return
        for path in cls._noto_serif_sc_asset_paths():
            try:
                QFontDatabase.addApplicationFont(str(path))
            except Exception:
                continue
        cls._noto_serif_sc_loaded = True

    @classmethod
    def load_jiangcheng_xiesong_fonts(cls):
        if cls._jiangcheng_xiesong_loaded:
            return
        for path in cls._jiangcheng_xiesong_asset_paths():
            try:
                QFontDatabase.addApplicationFont(str(path))
            except Exception:
                continue
        cls._jiangcheng_xiesong_loaded = True

    @classmethod
    def load_embedded_lyric_fonts(cls):
        cls.load_alibaba_puhuiti_fonts()
        cls.load_noto_serif_sc_fonts()
        cls.load_jiangcheng_xiesong_fonts()

    def _clear_lyric_metrics_cache(self):
        cache = getattr(self, "_lyric_layout_cache", None)
        if cache is not None:
            cache.clear()
        width_cache = getattr(self, "_representative_lyric_width_cache", None)
        if width_cache is not None:
            width_cache.clear()

    @classmethod
    def best_available_font_family(cls, preferred: str | None = None) -> str:
        families = set(QFontDatabase.families())
        if preferred:
            selected = str(preferred).strip()
            if selected in families:
                return selected
        candidates = list(cls.CHINESE_FONT_FALLBACKS)
        for candidate in candidates:
            if candidate in families and cls._font_supports_simplified_chinese(candidate):
                return candidate
        for candidate in candidates:
            if candidate in families:
                return candidate
        return cls.DEFAULT_FONT_FAMILY

    @staticmethod
    def _font_supports_simplified_chinese(family: str) -> bool:
        try:
            return QFontDatabase.WritingSystem.SimplifiedChinese in QFontDatabase.writingSystems(family)
        except Exception:
            return True

    @staticmethod
    def _display_refresh_rate_fps_cap() -> int:
        try:
            rates = []
            for screen in QGuiApplication.screens() or []:
                try:
                    rate = float(screen.refreshRate())
                except Exception:
                    rate = 0.0
                if rate > 0.0:
                    rates.append(rate)
            if rates:
                return max(30, int(round(max(rates))))
        except Exception:
            pass
        return 60

    def _normalize_animation_fps(self, fps: int | float | str) -> int:
        try:
            value = int(round(float(fps)))
        except (TypeError, ValueError):
            value = 60
        return max(30, min(self._display_refresh_rate_fps_cap(), value))

    def _has_active_lyric_animation(self, now: float | None = None) -> bool:
        if not bool(getattr(self, "media_is_playing", False)):
            return False
        has_lyrics = bool(getattr(self, "timed_lyrics", None) or getattr(self, "lyrics", None))
        if not has_lyrics:
            return False
        try:
            if self._should_paint_album_island():
                return False
        except (AttributeError, RuntimeError):
            pass
        current = time.monotonic() if now is None else float(now)
        if int(getattr(self, "prev_lyric_index", 0)) != int(getattr(self, "lyric_index", 0)):
            if self._animation_started_active(getattr(self, "lyric_changed_at", 0.0), 0.52, current):
                return True
        if str(getattr(self, "lyric_transition_kind", "") or ""):
            if self._animation_started_active(getattr(self, "lyric_changed_at", 0.0), 0.52, current):
                return True
        return True

    def _effective_animation_fps(self, active_animation: bool, now: float | None = None) -> int:
        # Preserve the native island's original refresh-rate-normalized clock.
        # The separate music surface owns its own high-refresh timer.
        fps = self._normalize_animation_fps(getattr(self, "animation_fps", 60))
        if active_animation and self._has_active_lyric_animation(now=now):
            fps = max(fps, self._normalize_animation_fps(getattr(self, "lyric_animation_fps", fps)))
        return fps

    def _timer_interval_ms(self, active_animation: bool = True, now: float | None = None) -> int:
        fps = self._effective_animation_fps(active_animation, now=now)
        return max(1, int(round(1000 / fps)))

    def _stable_render_interval_ms(self) -> int:
        return self._timer_interval_ms(active_animation=False)

    def _render_interval_seconds(self, active_animation: bool, now: float | None = None) -> float:
        interval_ms = self._timer_interval_ms(active_animation=True, now=now) if active_animation else self._stable_render_interval_ms()
        return max(0.001, interval_ms / 1000.0)

    def _retune_frame_timer(self, active_animation: bool, now: float | None = None):
        timer = getattr(self, "timer", None)
        if timer is None:
            return
        target = self._timer_interval_ms(active_animation=True, now=now) if active_animation else self._stable_render_interval_ms()
        try:
            if int(timer.interval()) != int(target):
                timer.start(int(target))
        except RuntimeError:
            pass

    def _animation_started_active(self, started_at: float, duration: float, now: float) -> bool:
        return presentation_animation_started_active(
            started_at,
            duration,
            now,
            speed=getattr(self, "animation_speed", 1.0),
        )

    def _animation_dict_active(self, animation: object, now: float, duration: float) -> bool:
        return presentation_animation_dict_active(
            animation,
            now,
            duration,
            speed=getattr(self, "animation_speed", 1.0),
        )

    def _alarm_spin_animations_active(self, now: float) -> bool:
        animations = getattr(self, "plugin_alarm_editor_spin_animations", {})
        if not isinstance(animations, dict) or not animations:
            return False
        return any(self._animation_dict_active(animation, now, 0.22) for animation in animations.values())

    def _active_visual_animation_plan(self, now: float):
        if (self._personal_dj_island_presenting()
                or now < float(getattr(self, "_island_dj_outro_until", 0.0))):
            from local.island_presentation import ActiveVisualAnimationPlan
            return ActiveVisualAnimationPlan(True, "personal_dj")
        if bool(getattr(self, "_island_music_control_hover_animating", False)):
            from local.island_presentation import ActiveVisualAnimationPlan
            return ActiveVisualAnimationPlan(True, "music_control_hover")
        cover_motion = getattr(self, "_music_cover_hover_motion", None)
        if cover_motion is not None and cover_motion.active:
            from local.island_presentation import ActiveVisualAnimationPlan
            return ActiveVisualAnimationPlan(True, "album_cover_hover")
        motion = getattr(self, "_audio_device_motion", None)
        if motion is not None and motion.active:
            # Reuse the island's precise frame timer, including paused lyrics.
            from local.island_presentation import ActiveVisualAnimationPlan
            return ActiveVisualAnimationPlan(True, "audio_device")
        plan = presentation_active_visual_animation_plan(
            media_is_playing=getattr(self, "media_is_playing", False),
            finder_mode=getattr(self, "finder_mode", False),
            drag_candidate=getattr(self, "drag_candidate", False),
            dragging=getattr(self, "dragging", False),
            barrage_hovered=getattr(self, "barrage_hovered", False),
            hover_bounce_active=now < float(getattr(self, "hover_bounce_until", 0.0) or 0.0),
            stack_bounce_active=now < float(getattr(self, "stack_bounce_until", 0.0) or 0.0),
            taskbar_left_handle_active=now < float(getattr(self, "taskbar_left_handle_visible_until", 0.0) or 0.0),
        )
        if plan.active:
            return plan

        scene_transition_active = False
        if str(getattr(self, "scene_transition_mode", "") or "") in {"hide", "show"}:
            scene_transition_active = self._animation_started_active(
                getattr(self, "scene_transition_started_at", 0.0),
                float(getattr(self, "scene_transition_duration", 0.18) or 0.18),
                now,
            )
        plan = presentation_active_visual_animation_plan(scene_transition_active=scene_transition_active)
        if plan.active:
            return plan

        surface_transition_active = False
        pixmap = getattr(self, "surface_transition_pixmap", QPixmap())
        if hasattr(pixmap, "isNull") and not pixmap.isNull():
            surface_transition_active = self._animation_started_active(
                getattr(self, "surface_transition_started_at", 0.0),
                float(getattr(self, "surface_transition_duration", 0.22) or 0.22),
                now,
            )
        plan = presentation_active_visual_animation_plan(surface_transition_active=surface_transition_active)
        if plan.active:
            return plan

        music_surface_transition_active = False
        if str(getattr(self, "music_surface_transition_mode", "") or ""):
            music_surface_transition_active = self._transition_progress_for_surface(now) < 1.0
        plan = presentation_active_visual_animation_plan(
            music_surface_transition_active=music_surface_transition_active,
        )
        if plan.active:
            return plan

        lyric_index_transition_active = False
        if int(getattr(self, "prev_lyric_index", 0)) != int(getattr(self, "lyric_index", 0)):
            lyric_index_transition_active = self._animation_started_active(
                getattr(self, "lyric_changed_at", 0.0),
                0.52,
                now,
            )
        plan = presentation_active_visual_animation_plan(
            lyric_index_transition_active=lyric_index_transition_active,
        )
        if plan.active:
            return plan

        lyric_kind_transition_active = False
        if str(getattr(self, "lyric_transition_kind", "") or ""):
            lyric_kind_transition_active = self._animation_started_active(
                getattr(self, "lyric_changed_at", 0.0),
                0.52,
                now,
            )
        plan = presentation_active_visual_animation_plan(
            lyric_kind_transition_active=lyric_kind_transition_active,
        )
        if plan.active:
            return plan

        album_island_transition_active = False
        try:
            album_island_transition_active = (
                self._should_paint_album_island()
                and self._album_island_transition_progress(now) < 1.0
            )
        except Exception:
            pass
        plan = presentation_active_visual_animation_plan(
            album_island_transition_active=album_island_transition_active,
        )
        if plan.active:
            return plan

        plan = presentation_active_visual_animation_plan(
            plugin_time_module_active=self._animation_dict_active(
                getattr(self, "plugin_time_module_animation", {}),
                now,
                0.28,
            )
        )
        if plan.active:
            return plan

        plan = presentation_active_visual_animation_plan(
            plugin_alarm_editor_page_active=self._animation_dict_active(
                getattr(self, "plugin_alarm_editor_page_animation", {}),
                now,
                0.24,
            )
        )
        if plan.active:
            return plan

        plan = presentation_active_visual_animation_plan(
            plugin_alarm_editor_reflow_active=self._animation_dict_active(
                getattr(self, "plugin_alarm_editor_reflow_animation", {}),
                now,
                0.24,
            )
        )
        if plan.active:
            return plan

        plan = presentation_active_visual_animation_plan(
            plugin_alarm_editor_spin_active=self._alarm_spin_animations_active(now),
        )
        if plan.active:
            return plan

        music_control_press_active = False
        control_presses = getattr(self, "music_control_press_started_at", {})
        if isinstance(control_presses, dict):
            for started_at in control_presses.values():
                if self._animation_started_active(started_at, 0.42, now):
                    music_control_press_active = True
                    break
        ripple_active = any(
            float(ripple.get("started_at", 0.0)) <= now
            < float(ripple.get("started_at", 0.0)) + float(ripple.get("duration", 0.0))
            for ripple in getattr(self, "_island_button_ripples", ())
        )
        return presentation_active_visual_animation_plan(
            music_control_press_active=music_control_press_active or ripple_active,
        )

    def _has_active_visual_animation(self, now: float) -> bool:
        return self._active_visual_animation_plan(now).active

    def _render_frame_due(self, now: float, active_animation: bool) -> bool:
        return presentation_render_frame_due(
            now=now,
            last_render_at=getattr(self, "last_render_update_at", 0.0),
            interval_seconds=self._render_interval_seconds(active_animation, now=now),
            tolerance_seconds=getattr(self, "FRAME_INTERVAL_TOLERANCE_SECONDS", 0.0),
        )

    def _visual_frame_diagnostics_recorder(self) -> VisualFrameDiagnosticsRecorder:
        recorder = getattr(self, "visual_frame_diagnostics", None)
        if not isinstance(recorder, VisualFrameDiagnosticsRecorder):
            recorder = VisualFrameDiagnosticsRecorder()
            self.visual_frame_diagnostics = recorder
        return recorder

    def _diagnostic_frame_target_interval_seconds(self) -> float:
        timer = getattr(self, "timer", None)
        if timer is not None:
            try:
                interval_ms = float(timer.interval())
                if interval_ms > 0.0:
                    return interval_ms / 1000.0
            except RuntimeError:
                pass
        try:
            return max(0.001, float(self._stable_render_interval_ms()) / 1000.0)
        except Exception:
            return 1.0 / 60.0

    def _visual_frame_diagnostics_summary(self) -> dict[str, float | int | str]:
        recorder = getattr(self, "visual_frame_diagnostics", None)
        if isinstance(recorder, VisualFrameDiagnosticsRecorder):
            return recorder.summary().as_dict()
        return VisualFrameDiagnosticsSummary().as_dict()

    def _visual_paint_section_diagnostics_summary(self) -> dict[str, object]:
        recorder = getattr(self, "visual_frame_diagnostics", None)
        if isinstance(recorder, VisualFrameDiagnosticsRecorder):
            return recorder.section_summary().as_dict()
        return VisualPaintSectionDiagnosticsSummary().as_dict()

    def _runtime_diagnostics_trace_recorder(self) -> RuntimeDiagnosticsTraceRecorder:
        recorder = getattr(self, "runtime_diagnostics_trace", None)
        if not isinstance(recorder, RuntimeDiagnosticsTraceRecorder):
            recorder = RuntimeDiagnosticsTraceRecorder()
            self.runtime_diagnostics_trace = recorder
        return recorder

    def _runtime_diagnostics_trace_summary(self) -> dict[str, object]:
        recorder = getattr(self, "runtime_diagnostics_trace", None)
        if isinstance(recorder, RuntimeDiagnosticsTraceRecorder):
            return recorder.summary().as_dict()
        return self._runtime_diagnostics_trace_recorder().summary().as_dict()

    def _runtime_diagnostics_trace_interval_seconds(self) -> float:
        return 1.0

    def _record_runtime_diagnostics_trace_sample(self, now: float, snapshot: object) -> None:
        self._runtime_diagnostics_trace_recorder().record(float(now), snapshot if isinstance(snapshot, dict) else {})
        self.last_runtime_diagnostics_trace_at = float(now)

    def _maybe_record_runtime_diagnostics_trace(self, now: float) -> None:
        now_value = float(now)
        last_sampled_at = float(getattr(self, "last_runtime_diagnostics_trace_at", 0.0) or 0.0)
        if last_sampled_at > 0.0 and now_value - last_sampled_at < self._runtime_diagnostics_trace_interval_seconds():
            return
        try:
            diagnostics = self._build_island_runtime_diagnostics(now_value, provider_diagnostics={})
            self._record_runtime_diagnostics_trace_sample(
                now_value,
                diagnostics.get("runtime_diagnostics_snapshot"),
            )
        except Exception:
            return

    def _record_visual_paint_section(self, section: str, started_at: float, ended_at: float | None = None):
        end = time.perf_counter() if ended_at is None else float(ended_at)
        self._visual_frame_diagnostics_recorder().record_section(section, max(0.0, end - float(started_at)))

    def _mark_render_frame(self, now: float, reason: str = "render"):
        self._visual_frame_diagnostics_recorder().record_frame(
            float(now),
            target_interval_seconds=self._diagnostic_frame_target_interval_seconds(),
            reason=reason,
        )
        self.last_render_update_at = float(now)
        self._maybe_record_runtime_diagnostics_trace(float(now))

    def _animation_speed_factor(self) -> float:
        return presentation_animation_speed_factor(getattr(self, "animation_speed", 1.0))

    def _animation_duration(self, seconds: float, minimum: float = 0.05) -> float:
        return presentation_animation_duration(seconds, speed=getattr(self, "animation_speed", 1.0), minimum=minimum)

    def _tick_delta_seconds(self, now: float) -> float:
        return presentation_tick_delta_seconds(now, getattr(self, "last_tick_at", 0.0))

    def _animation_tick_delta_seconds(self, now: float) -> float:
        return presentation_animation_tick_delta_seconds(
            now,
            getattr(self, "last_tick_at", 0.0),
            speed=getattr(self, "animation_speed", 1.0),
        )

    def _tick_render_plan(self, now: float, active_animation: bool, visual_data_changed: bool):
        frame_due = True
        if not bool(visual_data_changed):
            frame_due = self._render_frame_due(now, active_animation)
        return presentation_tick_render_plan(
            visual_data_changed=visual_data_changed,
            render_frame_due=frame_due,
            speed=getattr(self, "animation_speed", 1.0),
        )

    def _barrage_hover_clock_plan(self, anim_dt: float):
        barrage_overlay = getattr(self, "barrage_overlay", None)
        overlay_hovered = bool(getattr(barrage_overlay, "barrage_hovered", False))
        pointer_hovered = False
        if not overlay_hovered and not bool(getattr(self, "scene_suppressed", False)):
            pointer_hovered = self._point_in_barrage(getattr(self, "pointer_pos", QPointF()))
        return presentation_barrage_hover_clock_plan(
            overlay_hovered=overlay_hovered,
            pointer_hovered=pointer_hovered,
            anim_dt=anim_dt,
        )

    def _topmost_reinforcement_due(self, now: float) -> bool:
        if not bool(getattr(self, "super_topmost_enabled", True)):
            return False
        return presentation_topmost_reinforcement_due(
            enabled=True,
            now=now,
            last_reinforced_at=getattr(self, "topmost_reinforced_at", 0.0),
            interval_seconds=self._topmost_reinforce_interval(),
        )

    def _scene_suppressed_tick_plan(self, now: float, active_animation: bool):
        return presentation_scene_suppressed_tick_plan(
            render_frame_due=self._render_frame_due(now, active_animation),
        )

    def _scene_suppressed_frame_execution_plan(self, now: float):
        active_visual_plan = self._active_visual_animation_plan(now)
        active_animation = active_visual_plan.active
        self._retune_frame_timer(active_animation)
        render_plan = self._scene_suppressed_tick_plan(now, active_animation)
        return presentation_scene_suppressed_frame_execution_plan(
            active_visual_plan=active_visual_plan,
            render_plan=render_plan,
        )

    def _tick_follow_refresh_plan(self, now: float, *, active: bool):
        active_value = bool(active)
        drag_candidate = bool(getattr(self, "drag_candidate", False))
        refresh_due = False
        if active_value and not drag_candidate:
            refresh_due = self._taskbar_follow_refresh_due(now)
        return presentation_tick_follow_refresh_plan(
            active=active_value,
            drag_candidate=drag_candidate,
            refresh_due=refresh_due,
        )

    def _handle_tick_follow_refresh(self, now: float):
        self._refresh_top_notch_position()
        if self._tick_follow_refresh_plan(now, active=self._taskbar_compact_active()).should_refresh:
            self._refresh_taskbar_follow_position()
        if self._tick_follow_refresh_plan(
            now,
            active=bool(getattr(self, "taskbar_above_follow_active", False)),
        ).should_refresh:
            self._refresh_taskbar_above_follow_position()

    def _handle_fullscreen_overlay_sync(self, now: float):
        if bool(getattr(self, "_runtime_extras_retired", False)):
            return
        self._ensure_fullscreen_plugin_barrage_messages(now)
        self._sync_fullscreen_plugin_alert_overlay(now)

    def _handle_tick_overlay_clock_and_topmost(self, now: float, anim_dt: float):
        if not bool(getattr(self, "_runtime_extras_retired", False)):
            self._handle_fullscreen_overlay_sync(now)
            barrage_hover_plan = self._barrage_hover_clock_plan(anim_dt)
            self.barrage_hovered = barrage_hover_plan.hovered
            self.barrage_clock += barrage_hover_plan.clock_delta
        if self._topmost_reinforcement_due(now):
            self._force_super_topmost()

    def _handle_tick_runtime_refresh(self, now: float) -> bool:
        visual_data_changed = bool(self._sync_music_background_to_theme())
        visual_data_changed = bool(self._refresh_media_data(now)) or visual_data_changed
        visual_data_changed = self._sync_personal_dj_island(now) or visual_data_changed
        self._update_scene_state(now)
        if not bool(getattr(self, "_runtime_extras_retired", False)):
            self._prune_expired_messages(now)
        return visual_data_changed

    def _lyric_fallback_advance_plan(self, now: float, speed: float):
        lyrics = getattr(self, "lyrics", ())
        try:
            lyric_count = len(lyrics or ())
        except TypeError:
            lyric_count = 0
        return presentation_lyric_fallback_advance_plan(
            media_is_playing=getattr(self, "media_is_playing", False),
            has_timed_lyrics=bool(getattr(self, "timed_lyrics", ())),
            lyric_count=lyric_count,
            current_index=getattr(self, "lyric_index", 0),
            lyric_changed_at=getattr(self, "lyric_changed_at", 0.0),
            now=now,
            speed=speed,
        )

    def _music_lyric_tick_plan(self, now: float, speed: float):
        lyrics = getattr(self, "lyrics", ())
        try:
            lyric_count = len(lyrics or ())
        except TypeError:
            lyric_count = 0
        return presentation_music_lyric_tick_plan(
            media_is_playing=getattr(self, "media_is_playing", False),
            has_timed_lyrics=bool(getattr(self, "timed_lyrics", ())),
            lyric_count=lyric_count,
            current_index=getattr(self, "lyric_index", 0),
            lyric_changed_at=getattr(self, "lyric_changed_at", 0.0),
            now=now,
            speed=speed,
        )

    def _handle_music_lyric_tick(self, now: float, speed: float) -> bool:
        music_lyric_plan = self._music_lyric_tick_plan(now, speed)
        changed = False
        if music_lyric_plan.sync_timed_lyrics:
            changed = bool(self._sync_timed_lyrics(now))
        elif music_lyric_plan.fallback_advance.should_advance:
            self.prev_lyric_index = music_lyric_plan.fallback_advance.previous_index
            self.lyric_index = music_lyric_plan.fallback_advance.next_index
            self.lyric_changed_at = music_lyric_plan.fallback_advance.changed_at
            changed = True
        spring = getattr(self, "_wave_line_spring", None)
        if (
            isinstance(spring, LogicalLineSpringChain)
            and bool(getattr(self, "expanded", False))
            and str(getattr(self, "lyric_flow_style", "wave_stream")) in {"wave_stream", "gelv_songscatter"}
            and not bool(getattr(self, "scene_suppressed", False))
        ):
            first, last = spring.visible_range or (0, 0)
            moving = any(
                abs(spring.positions[index] - spring.targets[index]) > 0.10
                or abs(spring.velocities[index]) > 0.10
                for index in range(first, last)
            )
            # The same island timer runs the physical rows even when Qt
            # combines several requested paints, including paused seeks.
            spring.advance(now)
            changed = changed or moving
        return changed

    def _hover_auto_activation_due(self, now: float) -> bool:
        if now < float(getattr(self, "_island_dj_outro_until", 0.0)):
            return False
        hovered_key = getattr(self, "hovered_key", "")
        if not presentation_hover_auto_activation_candidate(
            hovered_key=hovered_key,
            expanded=getattr(self, "expanded", False),
            hover_armed_key=getattr(self, "hover_armed_key", ""),
            hover_started_at=getattr(self, "hover_started_at", 0.0),
            now=now,
        ):
            return False
        return self._hover_auto_activation_allowed(str(hovered_key or ""))

    def _plugin_panel_hover_open_due(self, now: float) -> bool:
        return presentation_plugin_panel_hover_open_due(
            expanded=getattr(self, "expanded", False),
            hovered_key=getattr(self, "hovered_key", ""),
            plugin_panel_open=getattr(self, "plugin_panel_open", False),
            hover_started_at=getattr(self, "hover_started_at", 0.0),
            now=now,
        )

    def _left_stack_collapse_due(self, now: float) -> bool:
        if now < float(getattr(self, "_island_dj_outro_until", 0.0)):
            return False
        return presentation_left_stack_collapse_due(
            expanded=getattr(self, "expanded", False),
            left_stack_at=getattr(self, "left_stack_at", 0.0),
            now=now,
        )

    def _tick_interaction_plan(self, now: float):
        return presentation_tick_interaction_plan(
            hover_auto_activation_due=self._hover_auto_activation_due(now),
            plugin_panel_hover_open_due=self._plugin_panel_hover_open_due(now),
            left_stack_collapse_due=self._left_stack_collapse_due(now),
            alarm_row_long_press_triggered=self._maybe_trigger_alarm_row_long_press(now),
        )

    def _handle_tick_interaction(self, now: float) -> bool:
        interaction_plan = self._tick_interaction_plan(now)
        if interaction_plan.activate_hover_key:
            self._activate_key(self.hovered_key)
        if interaction_plan.clear_hover_armed_key:
            self.hover_armed_key = ""
        if interaction_plan.open_plugin_panel:
            self.plugin_panel_open = True
        if interaction_plan.pulse_after_plugin_open:
            self._pulse_stack()
        if interaction_plan.collapse_left_stack:
            self._set_expanded(False)
        if interaction_plan.clear_left_stack:
            self.left_stack_at = 0.0
        if interaction_plan.pulse_after_left_collapse:
            self._pulse_stack()
        if interaction_plan.render_after_alarm_long_press:
            self.update()
            self._mark_render_frame(now)
        return bool(interaction_plan.stop_after_alarm_long_press)

    def _handle_tick_frame_execution(self, now: float, visual_data_changed: bool) -> bool:
        active_visual_plan = self._active_visual_animation_plan(now)
        active_animation = active_visual_plan.active
        self._retune_frame_timer(active_animation)
        render_plan = self._tick_render_plan(now, active_animation, visual_data_changed)
        frame_plan = presentation_tick_frame_execution_plan(
            active_visual_plan=active_visual_plan,
            render_plan=render_plan,
            update_music_wave_due=presentation_music_wave_update_due(
                now=now,
                previous_updated_at=getattr(self, "_wave_values_updated_at", 0.0),
            ),
        )
        if frame_plan.update_music_wave:
            self._update_wave_values()
        if not frame_plan.should_render:
            return False
        self.scroll_phase += frame_plan.scroll_phase_delta
        self.ambient_phase += frame_plan.ambient_phase_delta
        if frame_plan.mark_render_frame:
            self._mark_render_frame(now)
        if frame_plan.update_widget:
            self.update()
        return True

    def set_animation_fps(self, fps: int, notify: bool = True):
        self.animation_fps = self._normalize_animation_fps(fps)
        if hasattr(self, "timer"):
            self.timer.start(self._timer_interval_ms())
        if notify:
            self._notify_settings_changed()

    def set_lyric_animation_fps(self, fps: int, notify: bool = True):
        self.lyric_animation_fps = self._normalize_animation_fps(fps)
        if hasattr(self, "timer"):
            self.timer.start(self._timer_interval_ms())
        if notify:
            self._notify_settings_changed()

    def set_animation_speed(self, speed: float, notify: bool = True):
        allowed = (0.5, 0.75, 1.0, 2.0)
        try:
            value = float(speed)
        except (TypeError, ValueError):
            value = 1.0
        self.animation_speed = min(allowed, key=lambda item: abs(item - value))
        if notify:
            self._notify_settings_changed()

    def set_empty_island_display_mode(self, mode: str, notify: bool = True):
        self.empty_island_display_mode = self._normalize_empty_island_display_mode(mode)
        self._pulse_stack()
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_no_music_island_priority(self, priority: str, notify: bool = True):
        self.no_music_island_priority = self._normalize_no_music_island_priority(priority)
        self._pulse_stack()
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_music_ambience_style(self, style: str, notify: bool = True):
        style = normalize_music_ambience_style(style)
        self.music_ambience_style = style
        if style == "system":
            self._refresh_music_system_theme(force=True)
        if not self._lyric_color_fill_background_available():
            self.wave_color_fill_enabled = False
            self.gelv_color_fill_enabled = False
        if style not in self.EMBER_COLOR_FILL_AMBIENCE_STYLES:
            self.ember_color_fill_enabled = False
        self.update()
        if notify:
            self._notify_settings_changed()

    def _normalize_music_expanded_layout_style(self, style: str) -> str:
        value = str(style or "standard").strip().lower()
        return value if value in self.MUSIC_EXPANDED_LAYOUT_STYLES else "standard"

    def set_music_expanded_layout_style(self, style: str, notify: bool = True):
        self.music_expanded_layout_style = self._normalize_music_expanded_layout_style(style)
        self._pulse_stack()
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_tasker_follow_taskbar_color(self, enabled: bool, notify: bool = True):
        self.tasker_follow_taskbar_color = bool(enabled)
        if notify:
            self._notify_settings_changed()

    def _music_background_follow_theme_enabled(self) -> bool:
        getter = getattr(self, "settings_getter", None)
        if callable(getter):
            try:
                return bool(getter("v2_music_background_follow_theme", False))
            except Exception:
                return False
        return bool(getattr(self, "music_background_follow_theme", False))

    def _tasker_follow_taskbar_color_enabled(self) -> bool:
        getter = getattr(self, "settings_getter", None)
        if callable(getter):
            try:
                return bool(getter("v2_tasker_follow_taskbar_color", getattr(self, "tasker_follow_taskbar_color", False)))
            except Exception:
                pass
        return bool(getattr(self, "tasker_follow_taskbar_color", False))

    def _music_background_follow_previous_style(self) -> str:
        getter = getattr(self, "settings_getter", None)
        if callable(getter):
            try:
                value = str(getter("v2_music_background_follow_previous_style", "") or "").strip().lower()
                if value:
                    return value
            except Exception:
                pass
        return str(getattr(self, "music_background_follow_previous_style", "") or "").strip().lower()

    def _set_music_background_follow_previous_style(self, style: str):
        normalized = str(style or "").strip().lower()
        if normalized and (
            normalized not in self.MUSIC_AMBIENCE_STYLES or music_ambience_theme_category(normalized) != "dark"
        ):
            normalized = ""
        self.music_background_follow_previous_style = normalized
        setter = getattr(self, "settings_setter", None)
        if callable(setter):
            try:
                setter("v2_music_background_follow_previous_style", normalized)
            except Exception:
                pass

    def _set_music_ambience_style_from_follow(self, style: str):
        self.set_music_ambience_style(style, notify=False)
        setter = getattr(self, "settings_setter", None)
        if callable(setter):
            try:
                setter("v2_music_ambience_style", getattr(self, "music_ambience_style", style))
            except Exception:
                pass

    def _sync_music_background_to_light_state(self, light: bool, enabled: bool, force: bool = False) -> bool:
        if (
            normalize_music_ambience_style(getattr(self, "music_ambience_style", "sea_level")) == "system"
            and not self._music_background_follow_previous_style()
        ):
            # An explicit system surface follows Windows, rather than an
            # application schedule or a sampled taskbar material.
            return False
        plan = music_background_light_sync_plan(
            light=light,
            enabled=enabled,
            current_style=getattr(self, "music_ambience_style", "sea_level"),
            previous_dark_style=self._music_background_follow_previous_style(),
            allowed_styles=self.MUSIC_AMBIENCE_STYLES,
        )
        if plan.previous_style_to_save is not None:
            self._set_music_background_follow_previous_style(plan.previous_style_to_save)
        if not plan.style_changed:
            return False
        self._set_music_ambience_style_from_follow(plan.target_style)
        if plan.notify_settings:
            self._notify_settings_changed()
        return True

    def _sync_music_background_to_theme(self, force: bool = False):
        system_changed = False
        if normalize_music_ambience_style(getattr(self, "music_ambience_style", "sea_level")) == "system":
            system_changed = self._refresh_music_system_theme(force=force)
            if not self._music_background_follow_previous_style():
                return system_changed
        getter = getattr(self, "settings_getter", None)
        enabled = self._music_background_follow_theme_enabled()
        if not enabled:
            return system_changed
        now = time.monotonic()
        if not force and now - float(getattr(self, "_last_theme_follow_checked_at", 0.0) or 0.0) < 2.0:
            return system_changed
        self._last_theme_follow_checked_at = now
        dark = app_dark_mode(getter if callable(getter) else None)
        if not force and dark == getattr(self, "_last_theme_follow_dark", None):
            return system_changed
        self._last_theme_follow_dark = dark
        return self._sync_music_background_to_light_state(light=not dark, enabled=enabled, force=force) or system_changed

    def _sync_music_background_to_taskbar_color(self, force: bool = False) -> bool:
        if not self._tasker_follow_taskbar_color_enabled():
            return False
        return self._sync_music_background_to_light_state(
            light=bool(getattr(self, "taskbar_material_is_light", False)),
            enabled=True,
            force=force,
        )

    def _coerce_music_ambience_for_color_fill(self):
        return

    def _refresh_music_system_theme(self, force: bool = False) -> bool:
        """Poll once per two seconds, or immediately on a native theme event."""
        now = time.monotonic()
        previous = getattr(self, "_music_system_dark", None)
        checked_at = getattr(self, "_music_system_theme_checked_at", None)
        if not force and previous is not None and checked_at is not None and now - float(checked_at) < 2.0:
            return False
        dark = system_dark_mode()
        self._music_system_theme_checked_at = now
        self._music_system_dark = dark
        return previous is None or bool(previous) != dark

    def _effective_music_ambience_style(self) -> str:
        style = normalize_music_ambience_style(getattr(self, "music_ambience_style", "sea_level"))
        if style == "system":
            if getattr(self, "_music_system_dark", None) is None:
                self._refresh_music_system_theme(force=True)
            return "pure_black" if bool(self._music_system_dark) else "pure_white"
        return style

    def _active_lyric_color_fill_paints_background(self, ambience_style: str | None = None) -> bool:
        if not self._active_lyric_color_fill_enabled():
            return False
        if self._gelv_enabled() or self._ember_enabled():
            return False
        style = str(ambience_style or self._effective_music_ambience_style())
        if self._taskbar_compact_active() and style not in self.COLOR_FILL_AMBIENCE_STYLES:
            return False
        return True

    def _music_lyric_text_color(self, alpha: int = 246) -> QColor:
        alpha = max(0, min(255, int(alpha)))
        if self._effective_music_ambience_style() == "solid_album":
            return solid_album_ink_color(self._solid_album_background_color(), alpha)
        if self._effective_music_ambience_style() == "pure_white":
            return QColor(0, 0, 0, alpha)
        return QColor(255, 255, 255, alpha)

    def _music_lyric_base_text_color(self, alpha: int = 132) -> QColor:
        alpha = max(0, min(255, int(alpha)))
        if self._effective_music_ambience_style() == "solid_album":
            return self._solid_album_unread_color(alpha)
        if self._effective_music_ambience_style() == "pure_white":
            return QColor(0, 0, 0, alpha)
        return QColor(255, 255, 255, alpha)

    def _pure_white_music_active(self) -> bool:
        return self._effective_music_ambience_style() == "pure_white"

    def _solid_album_music_active(self) -> bool:
        return self._effective_music_ambience_style() == "solid_album"

    def _music_meta_text_color(self, alpha: int = 240) -> QColor:
        return self._music_lyric_text_color(alpha)

    def _music_control_icon_color(self, alpha: int = 232) -> QColor:
        return self._music_lyric_text_color(alpha)

    def _music_control_fill_color(self, alpha: int = 24) -> QColor:
        alpha = max(0, min(255, int(alpha)))
        if self._music_lyric_text_color(255).lightness() < 128:
            return QColor(246, 247, 248, alpha)
        return QColor(18, 20, 24, alpha)

    def _music_control_icon_spec(self, command: str) -> dict[str, object]:
        command_key = str(command or "")
        light_icon = self._music_lyric_text_color(255).lightness() >= 128
        suffix = " (1)" if light_icon else ""
        mirrored = False
        if command_key == "music:toggle_play_pause":
            name = "暂停" if bool(getattr(self, "media_is_playing", False)) else "播放2"
        else:
            name = "上一首"
            mirrored = command_key == "music:next"
        return {
            "path": MUSIC_CONTROL_ICON_DIR / f"{name}{suffix}.svg",
            "mirrored": mirrored,
        }

    def _music_control_fallback_label(self, command: str) -> str:
        command_key = str(command or "")
        if command_key == "music:previous":
            return "\u23ee"
        if command_key == "music:next":
            return "\u23ed"
        return "\u23f8" if bool(getattr(self, "media_is_playing", False)) else "\u25b6"

    def _svg_renderer_for_path(self, path: str):
        if QSvgRenderer is None:
            return None
        key = str(path or "")
        if not key:
            return None
        cache = getattr(self, "_svg_renderer_cache", None)
        if cache is None:
            cache = {}
            self._svg_renderer_cache = cache
        renderer = cache.get(key)
        if renderer is None:
            try:
                renderer = QSvgRenderer(key)
            except Exception:
                return None
            if not renderer.isValid():
                return None
            cache[key] = renderer
            while len(cache) > 16:
                cache.pop(next(iter(cache)))
        return renderer

    def _paint_music_control_svg_icon(self, painter: QPainter, command: str, rect: QRectF) -> bool:
        spec = self._music_control_icon_spec(command)
        renderer = self._svg_renderer_for_path(str(spec["path"]))
        if renderer is None:
            return False
        painter.save()
        if bool(spec["mirrored"]):
            center = rect.center()
            painter.translate(center)
            painter.scale(-1.0, 1.0)
            renderer.render(painter, QRectF(-rect.width() / 2.0, -rect.height() / 2.0, rect.width(), rect.height()))
        else:
            renderer.render(painter, rect)
        painter.restore()
        return True

    def _music_control_elastic_scale(self, command: str, now: float | None = None) -> float:
        presses = getattr(self, "music_control_press_started_at", {}) or {}
        started = presses.get(str(command or ""))
        if started is None:
            return 1.0
        now = time.monotonic() if now is None else float(now)
        try:
            elapsed = now - float(started)
        except (TypeError, ValueError):
            return 1.0
        duration = self._animation_duration(0.34)
        if elapsed < 0.0:
            return 1.0
        if elapsed >= duration:
            try:
                presses.pop(str(command or ""), None)
            except Exception:
                pass
            return 1.0
        progress = max(0.0, min(1.0, elapsed / max(0.001, duration)))
        overshoot = math.sin(progress * math.pi) * (1.0 - progress * 0.18)
        return 1.0 + 0.105 * overshoot

    def _set_island_music_control_hover(self, key: str, now: float):
        motions = getattr(self, "_island_music_control_hover_motions", None)
        if motions is None:
            motions = self._island_music_control_hover_motions = {}
        commands = ("music:previous", "music:toggle_play_pause", "music:next")
        for command in commands:
            if command not in motions:
                motions[command] = HoverMotion()
            motions[command].set_hovered(key == command, now)

    def _advance_island_music_control_hover(self, now: float) -> bool:
        key = str(getattr(self, "hovered_key", "") or "")
        if bool(getattr(self, "scene_suppressed", False)) or bool(getattr(self, "_music_surface_active", False)):
            key = ""
        self._set_island_music_control_hover(key, now)
        motions = self._island_music_control_hover_motions
        elapsed = max(0., min(.08, now - float(getattr(self, "_island_music_control_hover_tick_at", now))))
        self._island_music_control_hover_tick_at = now
        bases = getattr(self, "_island_music_control_base_rects", {})
        hits = getattr(self, "music_button_rects", {})
        group = {command: rect for command, rect in bases.items() if command in hits}
        offsets = getattr(self, "_island_music_control_neighbor_offsets", None)
        velocities = getattr(self, "_island_music_control_neighbor_velocities", None)
        if offsets is None:
            offsets = self._island_music_control_neighbor_offsets = {}
        if velocities is None:
            velocities = self._island_music_control_neighbor_velocities = {}
        changed = any(motion.is_animating(now) for motion in motions.values())
        if (not changed and not any(motion.value(now) > .00001 for motion in motions.values())
                and not any(not offset.isNull() for offset in offsets.values())
                and not any(not velocity.isNull() for velocity in velocities.values())):
            self._island_music_control_hover_animating = False
            return False
        for command, rect in group.items():
            target = QPointF()
            for other_command, other in group.items():
                if other_command != command:
                    target += neighbor_displacement(rect, other, motions[other_command].value(now))
            old = QPointF(offsets.get(command, QPointF()))
            offset, velocity = advance_neighbor_spring(old, velocities.get(command, QPointF()), target, elapsed)
            offsets[command], velocities[command] = offset, velocity
            changed |= (abs(offset.x() - old.x()) + abs(offset.y() - old.y()) > .0001
                        or abs(velocity.x()) + abs(velocity.y()) > .01)
        self._island_music_control_hover_animating = changed
        return changed

    def _island_music_control_hover_rect(self, command: str, rect: QRectF, now: float) -> QRectF:
        motion = getattr(self, "_island_music_control_hover_motions", {}).get(command)
        scale = hover_scale(motion.value(now) if motion is not None else 0.)
        offset = getattr(self, "_island_music_control_neighbor_offsets", {}).get(command, QPointF())
        if scale == 1.0 and offset.isNull():
            return QRectF(rect)
        center = rect.center()
        result = QRectF(center.x() - rect.width() * scale / 2, center.y() - rect.height() * scale / 2,
                        rect.width() * scale, rect.height() * scale)
        result.translate(offset)
        return result

    def _paint_music_control_button(self, painter: QPainter, rect: QRectF, command: str, fill_alpha: int, radius: float | None = None):
        bases = getattr(self, "_island_music_control_base_rects", None)
        if bases is None:
            bases = self._island_music_control_base_rects = {}
        bases[command] = QRectF(rect)
        now = time.monotonic()
        self._set_island_music_control_hover(str(getattr(self, "hovered_key", "") or ""), now)
        original_height = rect.height()
        original_corner = float(radius) if radius is not None else min(rect.height() / 2.0, self._u(10.0))
        rect = self._island_music_control_hover_rect(command, rect, now)
        if painter.hasClipping():
            clip = painter.clipBoundingRect().adjusted(1, 1, -1, -1)
            if clip.width() >= rect.width() and clip.height() >= rect.height():
                rect.translate(max(0., clip.left() - rect.left()) - max(0., rect.right() - clip.right()),
                               max(0., clip.top() - rect.top()) - max(0., rect.bottom() - clip.bottom()))
        # The hover shape owns input as well as paint. The established brief
        # press overshoot remains a visual accent around that stable hit shape.
        getattr(self, "music_button_rects", {})[command] = QRectF(rect)
        hover_growth = rect.height() / max(.001, original_height)
        scale = self._music_control_elastic_scale(command)
        if scale != 1.0:
            center = rect.center()
            rect = QRectF(
                center.x() - rect.width() * scale / 2.0,
                center.y() - rect.height() * scale / 2.0,
                rect.width() * scale,
                rect.height() * scale,
            )
        corner = original_corner * hover_growth
        # Keep each island style's original tint and alpha; glass adds a fine
        # edge to that same face without sampling a stale scene or notice.
        paint_liquid_glass(
            painter, rect, radius=corner,
            base_color=self._music_control_fill_color(fill_alpha),
            hovered=str(getattr(self, "hovered_key", "")) == command,
            pressed=(bool(getattr(self, "drag_candidate", False))
                     and not bool(getattr(self, "dragging", False))
                     and str(getattr(self, "drag_press_key", "")) == command),
        )
        pad = max(self._u(3.0), min(self._u(7.0), rect.height() * 0.24))
        icon_rect = rect.adjusted(pad, pad, -pad, -pad)
        if self._paint_music_control_svg_icon(painter, command, icon_rect):
            return
        painter.setFont(QFont("Segoe UI Symbol", self._font_size(12), QFont.Weight.Medium))
        painter.setPen(self._music_control_icon_color(232))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._music_control_fallback_label(command))

    def _island_button_ripple_target(self, key: str) -> QRectF:
        """Resolve visible controls only; the surrounding card is a drag surface."""
        key = str(key or "")
        if key.startswith("music:"):
            if key == "music:open_surface":
                return QRectF()
            return QRectF(getattr(self, "music_button_rects", {}).get(key, QRectF()))
        if key.startswith("plugin-action:"):
            command = key.split(":", 1)[1]
            # Editable fields and the alarm list backdrop are not buttons.
            if command.startswith("alarm-editor-focus-") or command in {"alarm-editor-note", "alarm-list"}:
                return QRectF()
            return QRectF(getattr(self, "plugin_action_rects", {}).get(command, QRectF()))
        return QRectF()

    def _start_island_button_ripple(self, key: str, press_pos: QPointF, now: float | None = None) -> bool:
        target = self._island_button_ripple_target(key)
        if target.isEmpty():
            return False
        current = time.monotonic() if now is None else float(now)
        center = target.center() if target.width() <= self._u(100.0) else QPointF(press_pos)
        if not target.contains(center):
            center = target.center()
        album_origin = key in {"music:previous", "music:next"}
        source_radius = min(target.width(), target.height()) * 0.42
        if album_origin:
            cover = QRectF(getattr(self, "music_cover_rect", QRectF()))
            if not cover.isEmpty():
                center = cover.center()
                source_radius = 0.0
            else:
                album_origin = False
        clip = QRectF(target)
        owner_key = "music" if key.startswith("music:") else key
        enclosing = [
            (motion_key, QRectF(motion.rect))
            for motion_key, motion in getattr(self, "motions", {}).items()
            if float(getattr(motion, "opacity", 0.0)) > 0.12
            and QRectF(motion.rect).contains(center)
            and QRectF(motion.rect).width() >= target.width() * 0.85
        ]
        if enclosing:
            if key.startswith("music:"):
                chosen = next((item for item in enclosing if item[0] == "music"), None)
            elif key.startswith("plugin-action:"):
                chosen = next((item for item in enclosing if item[0] == "plugin-panel"), None)
            else:
                chosen = next((item for item in enclosing if item[0] == key), None)
            if chosen is None:
                chosen = min(enclosing, key=lambda item: item[1].width() * item[1].height())
            if key.startswith("plugin-action:"):
                owner_key = chosen[0]
            clip = QRectF(chosen[1])
        else:
            # A missing stack motion should confine the wash to the known control
            # rather than leak onto the transparent host window.
            clip = QRectF(target)
            if key.startswith("plugin-action:"):
                owner_key = "plugin-panel"
        is_light_surface = False
        if key.startswith("music"):
            try:
                is_light_surface = self._music_control_icon_color(255).lightness() < 128
            except Exception:
                pass
        elif key.startswith("plugin"):
            try:
                is_light_surface = self._plugin_theme_colors()["shell_expanded"].lightness() > 160
            except Exception:
                pass
        ripples = list(getattr(self, "_island_button_ripples", ()))
        ripples = [
            ripple for ripple in ripples
            if current - float(ripple.get("started_at", 0.0)) < float(ripple.get("duration", 0.0))
        ]
        ripples.append({
            "started_at": current,
            "duration": self._animation_duration(1.72 if album_origin else 0.38),
            "travel_duration": self._animation_duration(1.5) if album_origin else 0.0,
            "center": QPointF(center),
            "target": QRectF(target),
            "clip": QRectF(clip),
            "owner_key": owner_key,
            "source_radius_fraction": source_radius / max(1.0, min(clip.width(), clip.height())),
            "album_origin": album_origin,
            "origin_fraction": QPointF(
                (center.x() - clip.left()) / max(1.0, clip.width()),
                (center.y() - clip.top()) / max(1.0, clip.height()),
            ),
            "light_surface": is_light_surface,
        })
        self._island_button_ripples = ripples[-4:]
        self.update()
        return True

    def _paint_island_button_ripples(
        self,
        painter: QPainter,
        now: float,
        owner_key: str | None = None,
        shell_rect: QRectF | None = None,
        shell_path: QPainterPath | None = None,
    ):
        """Paint a soft expanding circular light ring behind card content."""
        ripples = getattr(self, "_island_button_ripples", ())
        if not ripples:
            return
        active: list[dict[str, object]] = []
        filtered = owner_key is not None
        for ripple in ripples:
            started = float(ripple["started_at"])
            duration = max(0.001, float(ripple["duration"]))
            progress = (now - started) / duration
            if progress < 0.0 or progress >= 1.0:
                continue
            active.append(ripple)
            if not filtered or ripple.get("owner_key") != owner_key:
                continue
            center = QPointF(ripple["center"])
            clip = QRectF(shell_rect) if shell_rect is not None else QRectF(ripple["clip"])
            if clip.isEmpty():
                continue
            if shell_rect is not None:
                origin = QPointF(ripple.get("origin_fraction", QPointF(0.5, 0.5)))
                center = QPointF(clip.left() + clip.width() * origin.x(), clip.top() + clip.height() * origin.y())
            album_origin = bool(ripple.get("album_origin", False))
            if album_origin:
                travel_duration = max(0.001, float(ripple.get("travel_duration", duration)))
                elapsed = now - started
                travel = min(1.0, elapsed / travel_duration)
                # A linearly rising speed gives the ring a gentle launch and a
                # fast finish; travel ends well before the two-second limit.
                expansion = 0.4 * travel + 0.6 * travel * travel
                tail = max(0.0, 1.0 - max(0.0, elapsed - travel_duration) / max(0.001, duration - travel_duration))
                luminance = min(1.0, expansion / 0.09) * (1.0 - 0.22 * expansion) * tail
            else:
                luminance = min(1.0, progress / 0.10) * (1.0 - progress) ** 1.05
            if luminance <= 0.001:
                continue
            light_surface = bool(ripple["light_surface"])
            pearl = QColor(225, 219, 242) if light_surface else QColor(255, 252, 248)
            chroma = QColor(145, 130, 190) if light_surface else QColor(187, 171, 238)
            short_side = min(clip.width(), clip.height())
            if album_origin:
                # Navigation light grows behind the album from radius zero and
                # reaches the music space's right edge within two seconds.
                max_radius = max(0.0, clip.right() - center.x())
                start_radius = 0.0
            else:
                edge_room = min(
                    max(0.0, center.y() - clip.top()),
                    max(0.0, clip.bottom() - center.y()),
                )
                max_radius = min(short_side * 0.60, max(short_side * 0.46, edge_room * 1.18))
                start_radius = min(max_radius * 0.83, max(self._u(4.0),
                    short_side * float(ripple.get("source_radius_fraction", 0.08))))
                expansion = 1.0 - (1.0 - progress) ** 1.7
            radius = start_radius + (max_radius - start_radius) * expansion
            band = max(self._u(11.0), short_side * (0.30 if album_origin else 0.15))
            gradient_radius = radius + band * 1.8
            inner = max(0.0, radius - band * 1.6) / gradient_radius
            gradient = QRadialGradient(center, gradient_radius)
            gradient.setColorAt(0.0, QColor(pearl.red(), pearl.green(), pearl.blue(), 0))
            gradient.setColorAt(inner, QColor(pearl.red(), pearl.green(), pearl.blue(), 0))
            gradient.setColorAt(max(inner + 0.001, (radius - band * 0.95) / gradient_radius),
                                QColor(chroma.red(), chroma.green(), chroma.blue(), int((24 if light_surface else 39) * luminance)))
            gradient.setColorAt(max(inner + 0.002, (radius - band * 0.24) / gradient_radius),
                                QColor(pearl.red(), pearl.green(), pearl.blue(), int((58 if light_surface else 103) * luminance)))
            gradient.setColorAt(radius / gradient_radius,
                                QColor(pearl.red(), pearl.green(), pearl.blue(), int((78 if light_surface else 145) * luminance)))
            gradient.setColorAt((radius + band * 0.38) / gradient_radius,
                                QColor(pearl.red(), pearl.green(), pearl.blue(), int((45 if light_surface else 83) * luminance)))
            gradient.setColorAt((radius + band * 1.15) / gradient_radius,
                                QColor(chroma.red(), chroma.green(), chroma.blue(), int((10 if light_surface else 17) * luminance)))
            gradient.setColorAt(1.0, QColor(pearl.red(), pearl.green(), pearl.blue(), 0))
            painter.save()
            painter.setClipPath(shell_path if shell_path is not None else self._rounded_path(
                clip, min(clip.height() * 0.46, clip.width() * 0.20)
            ), Qt.ClipOperation.IntersectClip)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(gradient)
            painter.drawEllipse(center, gradient_radius, gradient_radius)
            painter.restore()
        self._island_button_ripples = active

    def _paint_current_island_space_glow(
        self, painter: QPainter, rect: QRectF, radius: float, shell_path: QPainterPath | None = None,
    ):
        key = str(getattr(self, "_island_glow_current_visual_key", "") or "")
        if key and getattr(self, "_island_button_ripples", ()):
            self._paint_island_button_ripples(
                painter,
                float(getattr(self, "_island_glow_paint_now", 0.0) or time.monotonic()),
                owner_key=key,
                shell_rect=rect,
                shell_path=shell_path if shell_path is not None else self._rounded_path(rect, radius),
            )

    def set_music_flow_intensity(self, value: float, notify: bool = True):
        try:
            amount = float(value)
        except (TypeError, ValueError):
            amount = 0.74
        self.music_flow_intensity = max(0.0, min(1.0, amount))
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_music_reactive_background_enabled(self, enabled: bool, notify: bool = True):
        self.music_reactive_background_enabled = bool(enabled)
        self._invalidate_music_reactive_snapshot()
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_music_reactive_background_intensity(self, value: int, notify: bool = True):
        try:
            amount = int(value)
        except (TypeError, ValueError):
            amount = 55
        self.music_reactive_background_intensity = max(0, min(100, amount))
        self._invalidate_music_reactive_snapshot()
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_duotone_balance(self, compact: int | None = None, expanded: int | None = None, notify: bool = True):
        if compact is not None:
            self.compact_duotone_balance = max(0, min(100, int(compact)))
        if expanded is not None:
            self.expanded_duotone_balance = max(0, min(100, int(expanded)))
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_diffraction_frequency(self, value: float, notify: bool = True):
        try:
            amount = float(value)
        except (TypeError, ValueError):
            amount = 0.30
        self.diffraction_frequency = max(0.0, min(1.0, amount))
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_yaoguang_outer_color(self, color: str, notify: bool = True):
        qcolor = QColor(str(color or ""))
        if not qcolor.isValid():
            qcolor = QColor("#6C35F2")
        self.yaoguang_outer_color = qcolor.name().upper()
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_yaoguang_blur_strength(self, value: int, notify: bool = True):
        self.yaoguang_blur_strength = max(0, min(200, int(value)))
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_yaoguang_color_source(self, source: str, notify: bool = True):
        value = str(source or "custom").strip().lower()
        self.yaoguang_color_source = "album" if value in {"album", "album_color", "cover", "cover_color"} else "custom"
        if self.yaoguang_color_source != "album":
            self.yaoguang_album_inverse_enabled = False
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_yaoguang_album_inverse_enabled(self, enabled: bool, notify: bool = True):
        self.yaoguang_album_inverse_enabled = bool(enabled) and self._yaoguang_color_source_key() == "album"
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_yaoguang_height_mode(self, mode: str, notify: bool = True):
        self.yaoguang_height_mode = "spacious" if str(mode or "").strip().lower() == "spacious" else "regular"
        self._pulse_stack()
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_high_quality_font_rendering(self, enabled: bool, notify: bool = True):
        self.high_quality_font_rendering = bool(enabled)
        self._clear_lyric_metrics_cache()
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_ui_font(self, family: str, notify: bool = True):
        family = self.best_available_font_family(family or self.DEFAULT_FONT_FAMILY)
        self.ui_font_family = family
        self._clear_lyric_metrics_cache()
        self.setFont(self._font(10))
        self._pulse_stack()
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_empty_bar_palette(self, palette: str, notify: bool = True):
        self.empty_bar_palette = str(palette or "aurora")
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_empty_bar_geometry(
        self,
        width: int | None = None,
        height: int | None = None,
        y_offset: int | None = None,
        opacity: int | None = None,
        notify: bool = True,
    ):
        if width is not None:
            self.empty_bar_width = max(70, min(260, int(width)))
        if height is not None:
            self.empty_bar_height = max(8, min(28, int(height)))
        if y_offset is not None:
            self.empty_bar_y_offset = max(-80, min(160, int(y_offset)))
        if opacity is not None:
            self.empty_bar_opacity = max(10, min(100, int(opacity)))
        self._pulse_stack()
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_empty_bar_custom_colors(self, colors, notify: bool = True):
        normalized = []
        for value in list(colors or [])[:3]:
            text = str(value).strip()
            color = QColor(text)
            normalized.append(text.upper() if color.isValid() else "#46D6FF")
        while len(normalized) < 3:
            normalized.append(("#46D6FF", "#73FFA6", "#FF5A86")[len(normalized)])
        self.empty_bar_custom_colors = tuple(normalized[:3])
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_lyric_offset_seconds(self, seconds: float, notify: bool = True):
        value = max(-12.0, min(12.0, float(seconds)))
        if value == self.lyric_offset_seconds:
            return
        self.lyric_offset_seconds = value
        self._reanchor_lyric_offset()
        if notify:
            self._notify_settings_changed()

    def set_active_music_app_lyric_offset(self, service: str, seconds: float) -> bool:
        """Apply the edited profile immediately, but only to its active adapter."""
        active = str(getattr(self, "media_lyric_offset_adapter", "system") or "system").casefold()
        edited = str(service or "").casefold()
        if active == "netease_betterncm":
            active = "netease"
        if active != edited or active == "system":
            return False
        value = max(-12.0, min(12.0, float(seconds)))
        if value != float(getattr(self, "media_app_lyric_offset_seconds", 0.0) or 0.0):
            self.media_app_lyric_offset_seconds = value
            self._reanchor_lyric_offset()
        return True

    def _reanchor_lyric_offset(self):
        """A deliberate slider change is not a noisy player-clock backstep."""
        now = time.monotonic()
        self.lyric_last_phase_index = -1
        self.lyric_last_phase_progress = 0.0
        self.lyric_last_phase_at = now
        self.lyric_last_phase_timing_kind = ""
        self._sync_timed_lyrics(now, allow_backward=True)
        self.lyric_changed_at = now
        surface = getattr(self, "music_fullscreen_overlay", None)
        if surface is not None:
            reanchor = getattr(surface, "on_lyric_offset_changed", None)
            if callable(reanchor):
                reanchor()
        self.update()

    def set_lyric_font_size(self, size: int, notify: bool = True):
        self.lyric_font_size = max(14, min(24, int(size)))
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_music_surface_reading_lift_level(self, level: int, notify: bool = True):
        self.music_surface_reading_lift_level = normalize_music_reading_lift_level(level)
        surface = getattr(self, "music_fullscreen_overlay", None)
        if surface is not None:
            surface.update()
        if notify:
            self._notify_settings_changed()

    def set_music_surface_reading_gradient_steps(self, steps: int, notify: bool = True):
        self.music_surface_reading_gradient_steps = normalize_music_reading_gradient_steps(steps)
        surface = getattr(self, "music_fullscreen_overlay", None)
        if surface is not None:
            surface.update()
        if notify:
            self._notify_settings_changed()

    def set_music_surface_reading_strategy(self, strategy: str, notify: bool = True):
        self.music_surface_reading_strategy = normalize_music_reading_strategy(strategy)
        self.lyric_last_phase_index = -1
        self.lyric_last_phase_progress = 0.0
        self.ember_last_phase_index = -1
        self.ember_last_phase_progress = 0.0
        self.update()
        surface = getattr(self, "music_fullscreen_overlay", None)
        if surface is not None:
            surface.update()
        if notify:
            self._notify_settings_changed()

    def set_music_sparse_highlight_seconds_per_unit(self, seconds: float, notify: bool = True):
        self.music_sparse_highlight_seconds_per_unit = normalize_sparse_seconds_per_unit(seconds)
        surface = getattr(self, "music_fullscreen_overlay", None)
        if surface is not None:
            surface.update()
        if notify:
            self._notify_settings_changed()

    def set_lyric_elastic_strength(self, strength: int, notify: bool = True):
        self.lyric_elastic_strength = max(0, min(160, int(strength)))
        self._clear_lyric_metrics_cache()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_lyric_elastic_mode(self, mode: str, notify: bool = True):
        mode = str(mode or "both").lower()
        if mode not in ("both", "left"):
            mode = "both"
        self.lyric_elastic_mode = mode
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_lyric_word_spacing(self, spacing: int, notify: bool = True):
        self.lyric_word_spacing = max(60, min(150, int(spacing)))
        self._clear_lyric_metrics_cache()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_enable_dual_line_lyrics(self, enabled: bool, notify: bool = True):
        surface = getattr(self, "music_fullscreen_overlay", None)
        if surface is not None:
            changed = getattr(surface, "on_translation_visibility_changed", None)
            if callable(changed):
                changed(bool(enabled))
        self.enable_dual_line_lyrics = bool(enabled)
        self._pulse_stack()
        surface = getattr(self, "music_fullscreen_overlay", None)
        if surface is not None:
            surface.update()
        if notify:
            self._notify_settings_changed()
            refresh = getattr(getattr(self, "data_provider", None), "_request_immediate_media_refresh", None)
            if callable(refresh):
                refresh()
        self.update()

    def set_music_intro_cleanup_enabled(self, enabled: bool, notify: bool = True):
        self.music_intro_cleanup_enabled = bool(enabled)
        surface = getattr(self, "music_fullscreen_overlay", None)
        if surface is not None:
            surface.update()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_dual_line_font_size(self, size: int, notify: bool = True):
        self.dual_line_font_size = max(8, min(20, int(size)))
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_dual_line_secondary_weight(self, weight: int, notify: bool = True):
        self.dual_line_secondary_weight = max(40, min(100, int(weight)))
        self._clear_lyric_metrics_cache()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_dual_line_secondary_spacing(self, spacing: int, notify: bool = True):
        self.dual_line_secondary_spacing = max(60, min(150, int(spacing)))
        self._clear_lyric_metrics_cache()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_compact_lyric_show_cover(self, enabled: bool, notify: bool = True):
        self.compact_lyric_show_cover = bool(enabled)
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def _compact_lyric_cover_enabled(self) -> bool:
        return bool(getattr(self, "compact_lyric_show_cover", True))

    def set_lyric_flow_style(self, style: str, notify: bool = True):
        style = str(style or "wave_stream").strip()
        # Removed Ember Fade selections reopen with the canonical reader.
        if style not in self.LYRIC_FLOW_STYLES:
            style = "wave_stream"
        self.lyric_flow_style = style
        if self._active_lyric_color_fill_enabled():
            self._coerce_music_ambience_for_color_fill()
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_gelv_font_family(self, _family: str, notify: bool = True):
        family = self.GELV_FONT_FAMILY
        self.gelv_font_family = family
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_gelv_primary_font_size(self, size: int, notify: bool = True):
        self.gelv_primary_font_size = max(14, min(30, int(size)))
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_gelv_secondary_font_size(self, size: int, notify: bool = True):
        self.gelv_secondary_font_size = max(8, min(24, int(size)))
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_gelv_dual_line_font_size(self, size: int, notify: bool = True):
        self.gelv_dual_line_font_size = max(8, min(30, int(size)))
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_gelv_initial_word_spacing(self, spacing: int, notify: bool = True):
        self.gelv_initial_word_spacing = max(100, min(320, int(spacing)))
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_gelv_word_spacing(self, spacing: int, notify: bool = True):
        self.gelv_word_spacing = max(100, min(420, int(spacing)))
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_gelv_color_fill_enabled(self, enabled: bool, notify: bool = True):
        self.gelv_color_fill_enabled = bool(enabled)
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_gelv_word_fill_enabled(self, enabled: bool, notify: bool = True):
        self.gelv_word_fill_enabled = bool(enabled)
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_wave_color_fill_enabled(self, enabled: bool, notify: bool = True):
        self.wave_color_fill_enabled = bool(enabled)
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_wave_word_fill_enabled(self, enabled: bool, notify: bool = True):
        self.wave_word_fill_enabled = bool(enabled)
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_wave_font_weight(self, weight: str, notify: bool = True):
        value = str(weight or "bold").strip().lower()
        self.wave_font_weight = "heavy" if value == "heavy" else "bold"
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_ember_font_weight(self, weight: str, notify: bool = True):
        value = str(weight or "normal").strip().lower()
        self.ember_font_weight = "bold" if value in {"bold", "heavy", "900w", "900"} else "normal"
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_ember_font_size(self, size: int, notify: bool = True):
        self.ember_font_size = max(12, min(24, int(size)))
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_ember_line_mode(self, mode: str, notify: bool = True):
        value = str(mode or "split").strip().lower()
        self.ember_line_mode = "scroll" if value == "scroll" else "split"
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_ember_dual_line_enabled(self, enabled: bool, notify: bool = True):
        self.ember_dual_line_enabled = bool(enabled)
        if self.ember_dual_line_enabled:
            self.ember_line_mode = "scroll"
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_ember_italic_enabled(self, enabled: bool, notify: bool = True):
        self.ember_italic_enabled = bool(enabled)
        self._clear_lyric_metrics_cache()
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_ember_show_cover(self, enabled: bool, notify: bool = True):
        self.ember_show_cover = bool(enabled)
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_ember_word_fill_enabled(self, enabled: bool, notify: bool = True):
        self.ember_word_fill_enabled = bool(enabled)
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_ember_color_fill_enabled(self, enabled: bool, notify: bool = True):
        self.ember_color_fill_enabled = bool(enabled)
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_ember_back_gray_mix(self, value: int, notify: bool = True):
        self.ember_back_gray_mix = max(0, min(100, int(value)))
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_ember_color_back_gray_mix(self, value: int, notify: bool = True):
        self.ember_color_back_gray_mix = max(0, min(100, int(value)))
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_ember_back_opacity(self, value: int, notify: bool = True):
        self.ember_back_opacity = max(0, min(100, int(value)))
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_ember_blur_panel_strength(self, value: int, notify: bool = True):
        self.ember_blur_panel_strength = max(0, min(100, int(value)))
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_ember_lyric_offset_seconds(self, seconds: float, notify: bool = True):
        self.ember_lyric_offset_seconds = max(0.0, min(4.0, float(seconds)))
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_solid_album_background_fallback(self, color: str, notify: bool = True):
        qcolor = QColor(str(color or "#16BCFF"))
        self.solid_album_background_fallback = self._solid_album_fallback_color(qcolor).name().upper()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_no_lyrics_display_mode(self, mode: str, notify: bool = True):
        mode = str(mode or "text").strip()
        self.no_lyrics_display_mode = mode if mode in {"text", "album_island"} else "text"
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_album_island_cover_count(self, count: int, notify: bool = True):
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 5
        if count not in (3, 5, 7):
            count = 5
        self.album_island_cover_count = count
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_album_island_cover_size(self, size: int, notify: bool = True):
        self.album_island_cover_size = max(18, min(44, int(size)))
        self.album_island_compact_cover_size = self.album_island_cover_size
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_album_island_compact_cover_size(self, size: int, notify: bool = True):
        try:
            size = int(size)
        except (TypeError, ValueError):
            size = 30
        self.album_island_compact_cover_size = max(18, min(44, size))
        self.album_island_cover_size = self.album_island_compact_cover_size
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_album_island_expanded_cover_size(self, size: int, notify: bool = True):
        try:
            size = int(size)
        except (TypeError, ValueError):
            size = 72
        self.album_island_expanded_cover_size = max(36, min(96, size))
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_album_island_spacing(self, spacing: int, notify: bool = True):
        self.album_island_spacing = max(48, min(170, int(spacing)))
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_album_island_secondary_style(self, style: str, notify: bool = True):
        style = str(style or "perspective_dim").strip()
        if style not in self.ALBUM_ISLAND_SECONDARY_STYLES:
            style = "perspective_dim"
        self.album_island_secondary_style = style
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_album_island_perspective_amount(self, amount: int, notify: bool = True):
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            amount = 100
        self.album_island_perspective_amount = max(0, min(200, amount))
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_album_island_record(self, listened_seconds: float = 0.0, history=None, notify: bool = True):
        try:
            self.listened_seconds_total = max(0.0, float(listened_seconds))
        except (TypeError, ValueError):
            self.listened_seconds_total = 0.0
        normalized: list[dict[str, object]] = []
        for entry in list(history or [])[:6]:
            if isinstance(entry, dict):
                normalized_entry = {
                    "track_id": str(entry.get("track_id", "") or ""),
                    "title": str(entry.get("title", "") or ""),
                    "artist": str(entry.get("artist", "") or ""),
                    "album_title": str(entry.get("album_title", "") or ""),
                }
                image = self._album_art_from_entry(entry)
                if not image.isNull():
                    normalized_entry["album_art"] = image
                normalized.append(normalized_entry)
        self.album_history = normalized
        if notify:
            self._notify_settings_changed()
        self.update()

    def reset_album_island_record(self, notify: bool = True):
        self.listened_seconds_total = 0.0
        self.album_history = []
        self.album_island_transition_started_at = 0.0
        self.album_island_transition_reason = ""
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_no_lyrics_text(self, text: str, notify: bool = True):
        old_text = getattr(self, "no_lyrics_text", "暂无歌词")
        text = " ".join(str(text or "暂无歌词").split())[:16] or "暂无歌词"
        self.no_lyrics_text = text
        if self.lyrics == ["暂无歌词"] or self.lyrics == [old_text]:
            self.lyrics = [text]
            self.lyric_index = 0
            self.prev_lyric_index = 0
            self.lyric_changed_at = time.monotonic()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_barrage_settings(
        self,
        *,
        y_offset: int | None = None,
        font_size: int | None = None,
        speed: int | None = None,
        use_backdrop: bool | None = None,
        show_icon: bool | None = None,
        display_mode: str | None = None,
        notify: bool = True,
    ):
        if y_offset is not None:
            self.barrage_y_offset = max(0, min(self._barrage_max_y_offset(), int(y_offset)))
        if font_size is not None:
            self.barrage_font_size = max(8, min(20, int(font_size)))
        if speed is not None:
            self.barrage_speed = max(40, min(220, int(speed)))
        if use_backdrop is not None:
            self.barrage_use_backdrop = bool(use_backdrop)
        if show_icon is not None:
            self.barrage_show_icon = bool(show_icon)
        if display_mode is not None:
            self.barrage_display_mode = self._normalize_barrage_display_mode(display_mode)
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_auto_expand_bounds(self, enabled: bool, notify: bool = True):
        self.auto_expand_bounds = bool(enabled)
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_expand_direction(self, direction: str, notify: bool = True):
        direction = str(direction or "auto").lower()
        if direction not in ("auto", "up", "down"):
            direction = "auto"
        self.expand_direction = direction
        self.update()
        if notify:
            self._notify_settings_changed()

    def set_settings_window_active(self, active: bool):
        self.settings_window_active = bool(active)
        self.scene_checked_at = 0.0
        now = time.monotonic()
        self._update_scene_state(now)
        try:
            self._sync_fullscreen_plugin_alert_overlay(now)
        except Exception:
            pass
        self.update()

    def _center_on_screen(self):
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return
        geo = screen.geometry()
        self._restoring_position = True
        try:
            if getattr(self, "attach_to_real_finder", False):
                self.resize(geo.width(), self.height())
                self.move(geo.left(), geo.top())
                self._force_super_topmost()
            else:
                standalone_width = int(round(self._standalone_canvas_width()))
                if self.width() != standalone_width:
                    self.resize(standalone_width, self.height())
                remembered_pos = self.saved_window_pos or getattr(self, "last_window_position", None)
                if remembered_pos is not None:
                    x, y = remembered_pos
                    self.move(int(x), int(y))
                else:
                    self.move(geo.center().x() - self.width() // 2, geo.top() + int(round(self._u(14))))
        finally:
            self._restoring_position = False

    def _top_notch_anchor_locked(self) -> bool:
        return (
            str(getattr(self, "location_mode", "free") or "free") == "top"
            and not bool(getattr(self, "finder_mode", False))
            and not bool(getattr(self, "attach_to_real_finder", False))
            and not bool(getattr(self, "dragging", False))
            and not bool(getattr(self, "drag_candidate", False))
            and not bool(getattr(self, "_music_surface_active", False))
        )

    def _refresh_top_notch_position(self) -> bool:
        if not self._top_notch_anchor_locked() or bool(getattr(self, "_restoring_position", False)):
            return False
        kind = self._main_kind()
        # Downward layout has a fixed local top; it does not need the current
        # text width/height, which can be expensive to measure on every tick.
        main_top = self._main_top_for_direction(kind, 0.0, 1)
        current = QPoint(self.pos())
        probe = self.mapToGlobal(QPoint(self.width() // 2, int(round(main_top + self._u(16.0)))))
        screen = QGuiApplication.screenAt(probe) or self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return False
        target = QPoint(current.x(), int(round(screen.geometry().top() - main_top)))
        direction_changed = int(getattr(self, "stack_direction", 1) or 1) != 1
        self.stack_direction = 1
        moved = target != current
        if moved:
            # Platform moves and re-show geometry must not become a new free
            # placement. The native move can synchronously emit moveEvent.
            self._restoring_position = True
            try:
                self.move(target)
            finally:
                self._restoring_position = False
        self.save_last_position(target)
        return moved or direction_changed

    def _standalone_canvas_width(self) -> float:
        return self._u(1180.0)

    def _parse_window_position(self, value) -> tuple[int, int] | None:
        if isinstance(value, QPoint):
            return (int(value.x()), int(value.y()))
        if isinstance(value, dict):
            try:
                return (int(value.get("x")), int(value.get("y")))
            except (TypeError, ValueError):
                return None
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                return (int(value[0]), int(value[1]))
            except (TypeError, ValueError):
                return None
        return None

    def set_saved_placement(self, center_ratio=None, window_pos=None, notify: bool = True, stack_direction=None, last_window_position=None):
        try:
            ratio = float(center_ratio)
        except (TypeError, ValueError):
            ratio = 0.5
        self.stack_center_ratio = max(0.06, min(0.94, ratio))
        try:
            if stack_direction is not None:
                self.stack_direction = -1 if int(stack_direction) < 0 else 1
        except (TypeError, ValueError):
            self.stack_direction = 1

        parsed_pos = self._parse_window_position(window_pos)
        parsed_last_pos = self._parse_window_position(last_window_position)
        self.last_window_position = parsed_last_pos or parsed_pos
        self.saved_window_pos = parsed_pos
        self._center_on_screen()
        if notify:
            self._notify_settings_changed()

    def set_plugin_fullscreen_alert_style(self, style: str, notify: bool = True):
        value = str(style or "toast").strip().lower()
        value = {"island": "toast", "popup": "toast", "pop": "toast"}.get(value, value)
        if value not in {"toast", "barrage", "fullscreen"}:
            value = "toast"
        previous = str(getattr(self, "plugin_fullscreen_alert_style", "toast") or "toast")
        previous = {"island": "toast", "popup": "toast", "pop": "toast"}.get(previous, previous)
        self.plugin_fullscreen_alert_style = value
        if value != previous or value != "barrage":
            self.fullscreen_plugin_barrage_alert_key = ""
            self.fullscreen_plugin_barrage_messages = []
        try:
            self._sync_fullscreen_plugin_alert_overlay(time.monotonic())
        except Exception:
            pass
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_plugin_fullscreen_alert_position(
        self,
        *,
        x_offset: int | None = None,
        y_offset: int | None = None,
        notify: bool = True,
    ):
        if x_offset is not None:
            self.plugin_fullscreen_alert_x_offset = max(-2000, min(2000, int(x_offset)))
        if y_offset is not None:
            self.plugin_fullscreen_alert_y_offset = max(0, min(2000, int(y_offset)))
        try:
            self._sync_fullscreen_plugin_alert_overlay(time.monotonic())
        except Exception:
            pass
        if notify:
            self._notify_settings_changed()
        self.update()

    def apply_position_preset(self, preset: str, notify: bool = True) -> bool:
        value = canonical_position_preset(preset)
        self.position_preset = value
        if value == "last":
            remembered_pos = getattr(self, "last_window_position", None) or getattr(self, "saved_window_pos", None)
            parsed_pos = self._parse_window_position(remembered_pos)
            if parsed_pos is not None and str(getattr(self, "location_mode", "free") or "free") != "finder":
                new_pos = QPoint(int(parsed_pos[0]), int(parsed_pos[1]))
                self.move(new_pos)
                self.save_last_position(new_pos)
                if notify:
                    self._notify_settings_changed()
                try:
                    self.update()
                except RuntimeError:
                    pass
                return True
            if notify:
                self._notify_settings_changed()
            return False
        if str(getattr(self, "location_mode", "free") or "free") == "tasker":
            self.set_location_mode("free", notify=False)
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return False
        geo = screen.geometry()
        main_kind = self._main_kind()
        stack_w = self._stack_target_width(main_kind)
        main_w, main_h = self._main_size_for_kind(main_kind, stack_w)
        direction = -1 if value.startswith("bottom_") else 1
        main_top = self._main_top_for_direction(main_kind, main_h, direction)
        target = build_standalone_position_preset_target(
            preset=value,
            screen_geometry=geo,
            window_width=float(self.width()),
            main_kind=main_kind,
            stack_width=stack_w,
            main_width=main_w,
            main_height=main_h,
            main_top=main_top,
            margin=self._u(8.0),
        )
        if target is None:
            return False
        self.stack_direction = target.direction
        self.move(target.window_pos)
        self.save_last_position(target.window_pos)
        if notify:
            self._notify_settings_changed()
        try:
            self.update()
        except RuntimeError:
            pass
        return True

    def save_last_position(self, pos, notify: bool = False):
        parsed = self._parse_window_position(pos)
        if parsed is None:
            return
        self.last_window_position = parsed
        self.saved_window_pos = parsed
        if notify:
            self._notify_settings_changed()

    def _remember_current_window_position(self) -> bool:
        if bool(getattr(self, "_restoring_position", False)):
            return False
        if str(getattr(self, "location_mode", "free") or "free") == "finder" or bool(getattr(self, "finder_mode", False)):
            return False
        previous = getattr(self, "last_window_position", None)
        self._refresh_top_notch_position()
        try:
            pos = self.pos()
        except Exception:
            return False
        self.save_last_position(pos)
        return previous != getattr(self, "last_window_position", None)

    def moveEvent(self, event):
        super().moveEvent(event)
        if self._remember_current_window_position():
            self._notify_settings_changed()

    def set_super_topmost(self, enabled: bool, notify: bool = True):
        self.super_topmost_enabled = bool(enabled)
        if self.super_topmost_enabled:
            self._force_super_topmost()
        else:
            self._release_super_topmost()
        if notify:
            self._notify_settings_changed()
        self.update()

    def _force_super_topmost(self):
        if bool(getattr(self, "_music_surface_active", False)):
            return
        music_surface = getattr(self, "music_fullscreen_overlay", None)
        if music_surface is not None and music_surface.isVisible():
            return
        if sys.platform != "win32" or not getattr(self, "super_topmost_enabled", True):
            return
        try:
            hwnd = int(self.winId())
            if hwnd <= 0:
                return
            user32 = ctypes.windll.user32
            hwnd_topmost = ctypes.c_void_p(-1)
            flags = 0x0001 | 0x0002 | 0x0010 | 0x0040  # NOSIZE, NOMOVE, NOACTIVATE, SHOWWINDOW
            user32.SetWindowPos(ctypes.c_void_p(hwnd), hwnd_topmost, 0, 0, 0, 0, flags)
            self.topmost_reinforced_at = time.monotonic()
        except Exception:
            pass

    def _release_super_topmost(self):
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            if hwnd <= 0:
                return
            user32 = ctypes.windll.user32
            hwnd_notopmost = ctypes.c_void_p(-2)
            flags = 0x0001 | 0x0002 | 0x0010 | 0x0040  # NOSIZE, NOMOVE, NOACTIVATE, SHOWWINDOW
            user32.SetWindowPos(ctypes.c_void_p(hwnd), hwnd_notopmost, 0, 0, 0, 0, flags)
            self.topmost_reinforced_at = 0.0
        except Exception:
            pass

    def _topmost_reinforce_interval(self) -> float:
        if self._taskbar_compact_active() or bool(getattr(self, "taskbar_above_follow_active", False)):
            return 0.42
        return 1.6

    def showEvent(self, event):
        super().showEvent(event)
        self._resume_solid_album_palette_worker()
        if bool(getattr(self, "_music_surface_active", False)):
            self.hide()
            return
        self._center_on_screen()
        self._refresh_top_notch_position()
        self._force_super_topmost()

    def hideEvent(self, event):
        self._island_music_control_hover_motions = {}
        self._island_music_control_neighbor_offsets = {}
        self._island_music_control_neighbor_velocities = {}
        self._island_music_control_hover_animating = False
        cover_motion = getattr(self, "_music_cover_hover_motion", None)
        if cover_motion is not None:
            cover_motion.clear(time.monotonic())
        motion = getattr(self, "_audio_device_motion", None)
        if motion is not None:
            motion.clear(time.monotonic())
        super().hideEvent(event)

    def on_audio_device_event(self, route, device) -> None:
        if bool(getattr(self, "_runtime_extras_retired", False)):
            return
        serial = int(getattr(route, "event_serial", 0) or 0)
        if serial <= int(getattr(self, "_audio_device_event_serial", 0)):
            return
        self._audio_device_event_serial = serial
        motion = getattr(self, "_audio_device_motion", None)
        if motion is None:
            return
        now = time.monotonic()
        if (not self.isVisible() or self.isMinimized()
                or bool(getattr(self, "scene_suppressed", False))
                or bool(getattr(self, "_music_surface_active", False))):
            motion.clear(now)
            return
        name = str(getattr(device, "name", "") or getattr(route, "event_name", "") or "").strip()
        if not name:
            return
        # Synthetic group notices have no accessory identity or battery.
        identity = str(getattr(device, "id", "") or f"event:{serial}")
        motion.announce(IslandDevicePayload(identity, name,
            str(getattr(device, "kind", "device") or "device"),
            reliable_device_battery(device)), now)
        self.update()

    def _audio_device_pose(self):
        motion = getattr(self, "_audio_device_motion", None)
        return motion.pose if motion is not None and not bool(getattr(self, "expanded", False)) else None

    def _tick(self):
        now = time.monotonic()
        anim_dt = self._animation_tick_delta_seconds(now)
        self.last_tick_at = now
        speed = self._animation_speed_factor()
        cover_hover_changed = self._advance_music_cover_hover(now)
        control_hover_changed = self._advance_island_music_control_hover(now)
        notice_motion = getattr(self, "_audio_device_motion", None)
        notice_changed = False
        if notice_motion is not None:
            if bool(getattr(self, "scene_suppressed", False)) or bool(getattr(self, "_music_surface_active", False)):
                notice_motion.clear(now)
            else:
                notice_changed = notice_motion.advance(now)
        music_surface = getattr(self, "music_fullscreen_overlay", None)
        if music_surface is not None and music_surface.isVisible():
            self._refresh_media_data(now)
            self._update_scene_state(now)
            if not bool(getattr(self, "_runtime_extras_retired", False)):
                self._prune_expired_messages(now)
                self._handle_fullscreen_overlay_sync(now)
                barrage_hover_plan = self._barrage_hover_clock_plan(anim_dt)
                self.barrage_hovered = barrage_hover_plan.hovered
                self.barrage_clock += barrage_hover_plan.clock_delta
            self._handle_music_lyric_tick(now, speed)
            return
        visual_data_changed = self._handle_tick_runtime_refresh(now)
        self._handle_tick_follow_refresh(now)
        self._handle_tick_overlay_clock_and_topmost(now, anim_dt)

        lyric_visual_changed = self._handle_music_lyric_tick(now, speed)
        visual_data_changed = bool(visual_data_changed or lyric_visual_changed or notice_changed or cover_hover_changed or control_hover_changed)

        if self.scene_suppressed:
            self._handle_scene_suppressed_tick(now)
            return

        if self._handle_tick_interaction(now):
            return

        self._handle_tick_frame_execution(now, visual_data_changed)

    def _handle_scene_suppressed_tick(self, now: float):
        self._handle_fullscreen_overlay_sync(now)
        reset_plan = presentation_scene_suppressed_interaction_reset_plan()
        if reset_plan.reset_hover_state:
            self.hovered_key = ""
            self.hover_armed_key = ""
        if reset_plan.reset_left_stack:
            self.left_stack_at = 0.0
        frame_plan = self._scene_suppressed_frame_execution_plan(now)
        if not frame_plan.should_render:
            return
        if frame_plan.mark_render_frame:
            self._mark_render_frame(now)
        if frame_plan.update_widget:
            self.update()

    def _update_wave_values(self):
        now = time.monotonic()
        wave_plan = self._music_wave_update_plan(now)
        self.live_audio_available = wave_plan.live_audio_available
        self.current_audio_bands = wave_plan.current_audio_bands
        previous_at = getattr(self, "_wave_values_updated_at", None)
        advance = self._audio_reactive_runtime_for_current_settings().advance_wave_values(
            self.wave_values,
            self.current_audio_bands,
            self.ambient_phase,
            now=now,
            previous_at=previous_at,
        )
        self.wave_values = list(advance.values)
        self.wave_targets = list(advance.targets)
        self._wave_values_updated_at = advance.updated_at
        self._music_wave_update_count = int(getattr(self, "_music_wave_update_count", 0) or 0) + 1
        if not getattr(self, "_music_wave_first_updated_at", 0.0):
            self._music_wave_first_updated_at = advance.updated_at
        self._invalidate_music_reactive_snapshot()

    def _music_wave_update_plan(self, now: float):
        input_snapshot = self._music_wave_input_snapshot(now)
        return presentation_music_wave_update_plan(
            media_is_playing=getattr(self, "media_is_playing", False),
            input_snapshot=input_snapshot,
        )

    def _music_wave_input_snapshot(self, now: float):
        runtime = self._audio_reactive_runtime_for_current_settings()
        input_snapshot = getattr(runtime, "input_snapshot", None)
        if callable(input_snapshot):
            provider = getattr(self, "data_provider", None)
            return input_snapshot(
                now=now,
                reactive_enabled=bool(getattr(self, "music_reactive_background_enabled", False)),
                live_frame_reader=lambda: getattr(provider, "get_audio_frame", lambda: None)(),
                live_audio_probe=lambda: bool(getattr(provider, "has_live_audio", lambda: False)()),
                audio_frames=getattr(self, "audio_frames", []),
                audio_started_at=getattr(self, "audio_started_at", now),
                media_is_playing=bool(getattr(self, "media_is_playing", False)),
                ambient_phase=getattr(self, "ambient_phase", 0.0),
            )
        sampled_bands = self._sample_audio_bands(now)
        return presentation_music_wave_input_snapshot(
            sampled_bands=sampled_bands,
            live_audio_available=self._cached_live_audio_available(now),
        )

    def _cached_live_audio_available(self, now: float) -> bool:
        request_plan = live_audio_availability_request_plan(
            now=now,
            cached_available=getattr(self, "_live_audio_available_cached", False),
            checked_at=getattr(self, "_live_audio_available_checked_at", -999.0),
        )
        if not request_plan.request_probe:
            return request_plan.cached_available
        has_live_audio = False
        try:
            has_live_audio = bool(getattr(self.data_provider, "has_live_audio", lambda: False)())
        except Exception:
            has_live_audio = False
        self._live_audio_available_checked_at = now
        self._live_audio_available_cached = has_live_audio
        return has_live_audio

    def _sample_audio_bands(self, now: float) -> tuple[float, float, float]:
        live_frame = None
        request_plan = audio_band_sampling_request_plan(
            now=now,
            reactive_enabled=bool(getattr(self, "music_reactive_background_enabled", False)),
            cached_bands=getattr(self, "_music_reactive_audio_bands", None),
            sampled_at=getattr(self, "_music_reactive_audio_sampled_at", -999.0),
        )
        if request_plan.cached_bands is not None:
            return request_plan.cached_bands
        if request_plan.request_live_frame:
            try:
                live_frame = self.data_provider.get_audio_frame()
            except Exception:
                live_frame = None
        plan = audio_band_sample_plan(
            now=now,
            reactive_enabled=request_plan.reactive_enabled,
            live_frame=live_frame,
            audio_frames=getattr(self, "audio_frames", []),
            audio_started_at=getattr(self, "audio_started_at", now),
            media_is_playing=bool(getattr(self, "media_is_playing", False)),
            ambient_phase=getattr(self, "ambient_phase", 0.0),
        )
        self._apply_audio_band_sample_plan(plan)
        return plan.bands

    def _apply_audio_band_sample_plan(self, plan):
        if plan.cache_bands is not None:
            self._music_reactive_audio_bands = plan.cache_bands
        if plan.cache_sampled_at is not None:
            self._music_reactive_audio_sampled_at = plan.cache_sampled_at
        if plan.live_audio_available is not None:
            self._live_audio_available_cached = plan.live_audio_available
        if plan.live_audio_checked_at is not None:
            self._live_audio_available_checked_at = plan.live_audio_checked_at

    def _update_scene_state(self, now: float):
        if not presentation_scene_suppression_probe_due(
            now=now,
            last_checked_at=getattr(self, "scene_checked_at", 0.0),
        ):
            return
        self.scene_checked_at = now
        suppressed, reason = self._detect_scene_suppression()
        self.last_scene_probe_reason = str(reason or "")
        plan = presentation_scene_suppression_update_plan(
            current_suppressed=getattr(self, "scene_suppressed", False),
            detected_suppressed=suppressed,
            detected_reason=reason,
            taskbar_compact_active=self._taskbar_compact_active(),
            current_transition_mode=getattr(self, "scene_transition_mode", ""),
        )
        if plan.clear_scene_reason:
            self.scene_reason = ""
        if not plan.changed:
            if plan.clear_transition_mode:
                self.scene_transition_mode = ""
            return
        self.scene_suppressed = plan.scene_suppressed
        self.scene_reason = plan.scene_reason
        self._set_input_passthrough(plan.input_passthrough)
        self.scene_transition_mode = plan.transition_mode
        self.scene_transition_started_at = now
        self.scene_transition_duration = plan.transition_duration
        if plan.reset_hover_state:
            self.hovered_key = ""
            self.hover_armed_key = ""
        if plan.reset_left_stack:
            self.left_stack_at = 0.0
        if plan.collapse_expanded:
            self._set_expanded(False)
        if plan.clear_mask:
            self.group_frame_mask_path = None
            self.clearMask()
        if plan.update:
            self.update()

    def _set_input_passthrough(self, enabled: bool):
        enabled = bool(enabled)
        if getattr(self, "input_passthrough_enabled", False) == enabled:
            return
        self.input_passthrough_enabled = enabled
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, enabled)
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            if hwnd <= 0:
                return
            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_NOACTIVATE = 0x08000000
            style = user32.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE)
            if enabled:
                style |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
            else:
                style &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)
            user32.SetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE, style)
        except Exception:
            pass

    def _scene_transition_params(self, now: float) -> tuple[float, float, bool, float]:
        mode = getattr(self, "scene_transition_mode", "")
        if mode not in ("hide", "show"):
            return 1.0, 1.0, False, 0.0
        elapsed = max(0.0, now - self.scene_transition_started_at)
        duration = self._animation_duration(self.scene_transition_duration)
        if elapsed >= duration:
            if mode == "hide":
                return 0.0, 0.0, True, 1.0
            self.scene_transition_mode = ""
            return 1.0, 1.0, False, 0.0
        t = max(0.0, min(1.0, elapsed / duration))
        eased = 1.0 - pow(1.0 - t, 3.0)
        if mode == "hide":
            return 1.0 - 0.72 * eased, 1.0 - 0.96 * eased, False, eased
        return 0.28 + 0.72 * eased, 0.24 + 0.76 * eased, False, 1.0 - eased

    def _scene_transition_anchor(self, visuals: list[StackVisual]) -> QPointF:
        main_rect = next((visual.rect for visual in visuals if visual.z_index == 10), None)
        if main_rect:
            return main_rect.center()
        return QPointF(self.width() / 2.0, self.height() / 3.0)

    def _scene_transition_offset(self, visuals: list[StackVisual], progress: float) -> QPointF:
        progress = max(0.0, min(1.0, progress))
        bounds = self._visuals_bounds(visuals)
        if progress <= 0.001 or bounds is None:
            return QPointF(0.0, 0.0)
        edge, screen_geo, global_rect = self._nearest_scene_edge(bounds)
        margin = self._u(28.0)
        if edge == "left":
            dx = screen_geo.left() - global_rect.right() - margin if screen_geo else -bounds.right() - margin
            return QPointF(dx * progress, 0.0)
        if edge == "right":
            dx = screen_geo.right() - global_rect.left() + 1.0 + margin if screen_geo else self.width() - bounds.left() + margin
            return QPointF(dx * progress, 0.0)
        if edge == "bottom":
            dy = screen_geo.bottom() - global_rect.top() + 1.0 + margin if screen_geo else self.height() - bounds.top() + margin
            return QPointF(0.0, dy * progress)
        dy = screen_geo.top() - global_rect.bottom() - margin if screen_geo else -bounds.bottom() - margin
        return QPointF(0.0, dy * progress)

    def _visuals_bounds(self, visuals: list[StackVisual]) -> QRectF | None:
        rects = [visual.rect for visual in visuals if visual.rect.width() > 1 and visual.rect.height() > 1]
        if not rects:
            return None
        left = min(rect.left() for rect in rects)
        top = min(rect.top() for rect in rects)
        right = max(rect.right() for rect in rects)
        bottom = max(rect.bottom() for rect in rects)
        return QRectF(left, top, right - left, bottom - top)

    def _nearest_scene_edge(self, bounds: QRectF):
        top_left = self.mapToGlobal(QPoint(int(round(bounds.left())), int(round(bounds.top()))))
        bottom_right = self.mapToGlobal(QPoint(int(round(bounds.right())), int(round(bounds.bottom()))))
        global_rect = QRectF(
            float(top_left.x()),
            float(top_left.y()),
            float(bottom_right.x() - top_left.x()),
            float(bottom_right.y() - top_left.y()),
        )
        screen = QGuiApplication.screenAt(QPoint(int(round(global_rect.center().x())), int(round(global_rect.center().y())))) or QGuiApplication.primaryScreen()
        screen_geo = screen.geometry() if screen else None
        if self.finder_mode:
            return "top", screen_geo, global_rect
        if not screen_geo:
            distances = {
                "left": max(0.0, bounds.left()),
                "right": max(0.0, self.width() - bounds.right()),
                "top": max(0.0, bounds.top()),
                "bottom": max(0.0, self.height() - bounds.bottom()),
            }
            return min(distances, key=distances.get), screen_geo, global_rect
        distances = {
            "left": max(0.0, global_rect.left() - screen_geo.left()),
            "right": max(0.0, screen_geo.right() - global_rect.right()),
            "top": max(0.0, global_rect.top() - screen_geo.top()),
            "bottom": max(0.0, screen_geo.bottom() - global_rect.bottom()),
        }
        return min(distances, key=distances.get), screen_geo, global_rect

    def _detect_scene_suppression(self) -> tuple[bool, str]:
        if getattr(self, "settings_window_active", False):
            if self._barrage_preview_active():
                return False, ""
            return True, "settings"
        if sys.platform != "win32":
            return False, ""
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return False, ""
            self_hwnd = int(self.winId()) if self.winId() else 0
            if self_hwnd and hwnd == self_hwnd:
                return False, ""
            music_surface = getattr(self, "music_fullscreen_overlay", None)
            if music_surface is not None and music_surface.isVisible() and hwnd == int(music_surface.winId()):
                # Our own fullscreen/maximized song page owns the handoff,
                # not an external scene suppression. Classifying it as an
                # external fullscreen app would collapse the hidden island
                # and replay a separate scale-in when the page returns.
                return False, ""

            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return False, ""

            width = max(0, rect.right - rect.left)
            height = max(0, rect.bottom - rect.top)
            if width <= 0 or height <= 0:
                return False, ""

            title = self._window_title(hwnd, user32)
            class_name = self._window_class(hwnd, user32)
            process_name = self._window_process_name(hwnd, user32)
            if self._is_shell_desktop_window(title, class_name, process_name):
                return False, ""

            screen = QGuiApplication.screenAt(QPoint(rect.left + width // 2, rect.top + height // 2)) or QGuiApplication.primaryScreen()
            if screen:
                geo = screen.geometry()
                if width >= geo.width() - 4 and height >= geo.height() - 4 and abs(rect.left - geo.left()) <= 4 and abs(rect.top - geo.top()) <= 4:
                    return True, "fullscreen"
                available = screen.availableGeometry()
                if user32.IsZoomed(hwnd) or (
                    width >= available.width() - 10
                    and height >= available.height() - 10
                    and abs(rect.left - available.left()) <= 8
                    and abs(rect.top - available.top()) <= 8
                ):
                    return True, "maximized"

            title = self._window_title(hwnd, user32)
            class_name = self._window_class(hwnd, user32)
            process_name = self._window_process_name(hwnd, user32)
            lowered = " ".join(part for part in (title, class_name, process_name) if part).lower()
            if any(token in lowered for token in ("video", "player", "播放器", "bilibili", "youtube", "netflix", "vlc", "mpv", "potplayer")):
                if width >= 0.45 * (screen.geometry().width() if screen else width):
                    return True, "video"
        except Exception:
            return False, ""
        return False, ""

    def _is_shell_desktop_window(self, title: str, class_name: str, process_name: str) -> bool:
        cls = (class_name or "").lower()
        proc = Path(process_name or "").name.lower()
        if cls in {"progman", "workerw", "shell_traywnd", "shell_secondarytraywnd"}:
            return True
        if proc == "explorer.exe" and cls in {"progman", "workerw", "shelldll_defview"}:
            return True
        if proc == "explorer.exe" and not (title or "").strip() and cls in {"workerw", "progman"}:
            return True
        return False

    def _window_title(self, hwnd, user32) -> str:
        try:
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return ""
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            return buffer.value
        except Exception:
            return ""

    def _window_class(self, hwnd, user32) -> str:
        try:
            buffer = ctypes.create_unicode_buffer(256)
            if user32.GetClassNameW(hwnd, buffer, len(buffer)):
                return buffer.value
        except Exception:
            return ""
        return ""

    def _window_process_name(self, hwnd, user32) -> str:
        if sys.platform != "win32":
            return ""
        try:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process_id = int(pid.value)
            if not process_id:
                return ""
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
            if not handle:
                return ""
            try:
                buffer = ctypes.create_unicode_buffer(260)
                size = wintypes.DWORD(len(buffer))
                if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    return Path(buffer.value).name
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return ""
        return ""

    def _point_in_barrage(self, point: QPointF) -> bool:
        for rect in self.barrage_rects.values():
            if rect.contains(point):
                return True
        return False

    def _barrage_viewport_rect(self) -> QRectF:
        try:
            screen = QGuiApplication.screenAt(self.mapToGlobal(QPoint(max(1, self.width() // 2), max(1, self.height() // 2)))) or QGuiApplication.primaryScreen()
            if screen is not None:
                return QRectF(screen.geometry())
        except Exception:
            pass
        return QRectF(0.0, 0.0, float(max(1, self.width())), float(max(1, self.height())))

    def _barrage_max_y_offset(self) -> int:
        viewport = self._barrage_viewport_rect()
        return max(0, int(round(viewport.height())) - 1)

    def _barrage_travel_distance(self, content_width: float, viewport: QRectF | None = None) -> float:
        viewport = QRectF(viewport) if viewport is not None else self._barrage_viewport_rect()
        return max(float(self.width()), float(viewport.width())) + float(content_width) + self._u(140.0)

    def _active_barrage_messages(self) -> list[Message]:
        if bool(getattr(self, "_runtime_extras_retired", False)):
            return []
        previews = list(getattr(self, "barrage_preview_messages", []))
        fullscreen = list(getattr(self, "fullscreen_plugin_barrage_messages", []))
        return presentation_active_barrage_messages(
            preview_active=self._barrage_preview_active(previews),
            messages=list(getattr(self, "messages", [])),
            previews=previews,
            fullscreen_messages=fullscreen,
        )

    def _barrage_action_key_for_message(self, message: Message) -> str:
        return presentation_barrage_action_key_for_message(message)

    def _barrage_action_key_at(self, point: QPointF) -> str:
        for key, rect in getattr(self, "barrage_rects", {}).items():
            if rect.contains(point):
                return str(key)
        return ""

    def _fullscreen_plugin_alert_style(self) -> str:
        return presentation_normalize_fullscreen_plugin_alert_style(
            getattr(self, "plugin_fullscreen_alert_style", "toast")
        )

    def _normalize_barrage_display_mode(self, mode: str) -> str:
        return presentation_normalize_barrage_display_mode(mode)

    def _barrage_display_mode(self) -> str:
        return self._normalize_barrage_display_mode(getattr(self, "barrage_display_mode", "fullscreen"))

    def _fullscreen_plugin_alert_key(self, alert) -> str:
        return presentation_fullscreen_plugin_alert_key(alert)

    def _fullscreen_plugin_alert_action_key(self, alert) -> str:
        return presentation_fullscreen_plugin_alert_action_key(alert)

    def _fullscreen_plugin_alert_is_reminder(self, alert) -> bool:
        return presentation_fullscreen_plugin_alert_is_reminder(alert)

    def _plugin_fullscreen_alert_preview_active(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else float(now)
        return now <= float(getattr(self, "plugin_fullscreen_alert_preview_until", 0.0) or 0.0)

    def _plugin_fullscreen_alert_preview(self) -> tuple:
        return (
            0,
            "TIME",
            "提醒预览",
            "设置预览",
            QColor(255, 184, 0),
            "",
            "time",
            "done",
            "提醒预览",
            "调整提醒样式与位置",
            5,
        )

    def _fullscreen_plugin_alerts(self, limit: int = 1) -> list[tuple]:
        if bool(getattr(self, "_runtime_extras_retired", False)):
            return []
        if self._plugin_fullscreen_alert_preview_active():
            return [self._plugin_fullscreen_alert_preview()][: max(0, int(limit))]
        try:
            alerts = self._active_plugin_alerts(max(4, int(limit) * 3), include_expanded=True)
        except Exception:
            alerts = []
        out = [alert for alert in alerts if self._fullscreen_plugin_alert_is_reminder(alert)]
        return out[: max(0, int(limit))]

    def _suppress_fullscreen_plugin_alert(self, alert_key: str, seconds: float = 300.0):
        key = str(alert_key or "").strip()
        if not key:
            return
        entries = getattr(self, "fullscreen_plugin_alert_suppressed_until", {})
        if not isinstance(entries, dict):
            entries = {}
        entries[key] = time.monotonic() + max(1.0, float(seconds or 300.0))
        self.fullscreen_plugin_alert_suppressed_until = entries

    def _fullscreen_plugin_alert_suppressed(self, alert_key: str, now: float | None = None) -> bool:
        key = str(alert_key or "").strip()
        if not key:
            return False
        entries = getattr(self, "fullscreen_plugin_alert_suppressed_until", {})
        if not isinstance(entries, dict):
            return False
        now = time.monotonic() if now is None else float(now)
        suppressed, kept = presentation_fullscreen_plugin_alert_suppression_state(entries, key, now)
        if kept != entries:
            self.fullscreen_plugin_alert_suppressed_until = kept
        return suppressed

    def _fullscreen_overlay_context_active(self) -> bool:
        return presentation_fullscreen_overlay_context_active(
            preview_active=self._plugin_fullscreen_alert_preview_active(),
            settings_window_active=getattr(self, "settings_window_active", False),
            scene_suppressed=getattr(self, "scene_suppressed", False),
            last_scene_probe_reason=getattr(self, "last_scene_probe_reason", ""),
            scene_reason=getattr(self, "scene_reason", ""),
        )

    def _fullscreen_plugin_alert_overlay_state(self) -> dict[str, object] | None:
        if not self._fullscreen_overlay_context_active():
            return None
        alerts = self._fullscreen_plugin_alerts(1)
        state = presentation_fullscreen_plugin_alert_overlay_state(
            context_active=True,
            alerts=alerts,
            style=self._fullscreen_plugin_alert_style(),
        )
        if state is None:
            return None
        alert_key = str(state.get("alert_key", "") or "")
        if self._fullscreen_plugin_alert_suppressed(alert_key):
            return None
        return state

    def _plugin_alert_overlay_state(self) -> dict[str, object] | None:
        return presentation_plugin_alert_overlay_state(self._fullscreen_plugin_alert_overlay_state())

    def _barrage_alive_messages_for_clock(self, messages: list[Message], clock: float | None = None) -> list[Message]:
        clock = float(getattr(self, "barrage_clock", 0.0)) if clock is None else float(clock)
        return presentation_barrage_alive_messages_for_clock(messages, clock)

    def _barrage_alive_messages(self, messages: list[Message]) -> list[Message]:
        return self._barrage_alive_messages_for_clock(messages, float(getattr(self, "barrage_clock", 0.0)))

    def _barrage_preview_active(self, previews: list[Message] | None = None, now: float | None = None) -> bool:
        previews = list(getattr(self, "barrage_preview_messages", [])) if previews is None else list(previews)
        now = time.monotonic() if now is None else float(now)
        return presentation_barrage_preview_active(
            previews_present=bool(previews),
            settings_window_active=getattr(self, "settings_window_active", False),
            now=now,
            force_anytime_until=float(getattr(self, "barrage_preview_force_anytime_until", 0.0) or 0.0),
            last_preview_at=float(getattr(self, "last_barrage_preview_at", 0.0) or 0.0),
        )

    def _barrage_overlay_context_active(self, preview_active: bool = False) -> bool:
        display_mode = self._barrage_display_mode()
        if presentation_barrage_overlay_context_active(
            preview_active=preview_active,
            display_mode=display_mode,
            fullscreen_overlay_active=False,
        ):
            return True
        return presentation_barrage_overlay_context_active(
            preview_active=False,
            display_mode=display_mode,
            fullscreen_overlay_active=self._fullscreen_overlay_context_active(),
        )

    def _barrage_overlay_state(self, preview_only: bool = False) -> dict[str, object] | None:
        previews = list(getattr(self, "barrage_preview_messages", []))
        preview_active = self._barrage_preview_active(previews)
        try:
            messages = list(getattr(self, "messages", []))
            fullscreen = list(getattr(self, "fullscreen_plugin_barrage_messages", []))
        except Exception:
            messages = []
            fullscreen = []
        return presentation_barrage_overlay_state(
            preview_only=preview_only,
            preview_active=preview_active,
            context_active=preview_only or self._barrage_overlay_context_active(preview_active),
            messages=messages,
            previews=previews,
            fullscreen_messages=fullscreen,
            clock=float(getattr(self, "barrage_clock", 0.0)),
        )

    def _fullscreen_barrage_overlay_state(self, preview_only: bool = False) -> dict[str, object] | None:
        return self._barrage_overlay_state(preview_only=preview_only)

    def _fullscreen_overlay_state(self) -> dict[str, object] | None:
        preview = self._fullscreen_barrage_overlay_state(preview_only=True)
        if preview is not None:
            return preview
        return self._fullscreen_plugin_alert_overlay_state() or self._fullscreen_barrage_overlay_state()

    def _fullscreen_plugin_alert_screen_rect(self) -> QRectF:
        try:
            center = self.mapToGlobal(QPoint(max(1, self.width() // 2), max(1, self.height() // 2)))
            screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
            if screen is not None:
                return QRectF(screen.geometry())
        except Exception:
            pass
        return self._barrage_viewport_rect()

    def _sync_plugin_alert_overlay(self, now: float):
        if bool(getattr(self, "_runtime_extras_retired", False)):
            return
        overlay = getattr(self, "plugin_alert_overlay", None)
        state = self._plugin_alert_overlay_state()
        if state is None:
            if overlay is not None:
                overlay.request_hide(now)
            return
        if overlay is None:
            overlay = FullscreenPluginAlertOverlay(self)
            self.plugin_alert_overlay = overlay
            self.fullscreen_plugin_alert_overlay = overlay
        overlay.configure(state, self._fullscreen_plugin_alert_screen_rect(), now)

    def _sync_barrage_overlay(self, now: float):
        if bool(getattr(self, "_runtime_extras_retired", False)):
            return
        if self._fullscreen_plugin_alert_style() == "barrage":
            self._ensure_fullscreen_plugin_barrage_messages(now)
        overlay = getattr(self, "barrage_overlay", None)
        state = self._barrage_overlay_state()
        if state is None:
            if overlay is not None:
                overlay.request_hide(now)
            return
        if overlay is None:
            overlay = BarrageOverlay(self)
            self.barrage_overlay = overlay
        overlay.configure(state, self._fullscreen_plugin_alert_screen_rect(), now)

    def _sync_fullscreen_plugin_alert_overlay(self, now: float):
        self._sync_plugin_alert_overlay(now)
        self._sync_barrage_overlay(now)

    def _fullscreen_plugin_alert_barrage_messages(self, repeat_count: int = 10, now: float | None = None) -> list[Message]:
        now = time.monotonic() if now is None else float(now)
        alerts = self._fullscreen_plugin_alerts(1)
        if not alerts:
            return []
        try:
            _index, label, detail, summary, color, _icon, plugin_id, status, alert_title, alert_body, _priority = alerts[0]
        except (TypeError, ValueError):
            return []
        title, body = self._plugin_alert_text_parts(label, detail, summary, status, alert_title, alert_body)
        text = str(body or summary or detail or title or label or "提醒")
        sender = str(title or label or "提醒")
        app = str(label or plugin_id or "PLUGIN")
        action_key = f"plugin-alert:{str(plugin_id or label or '').strip().casefold()}"
        avatar = QColor(color) if isinstance(color, QColor) else QColor(96, 186, 255)
        messages: list[Message] = []
        count = max(1, min(10, int(repeat_count)))
        start_clock = float(getattr(self, "barrage_clock", 0.0))
        if self._plugin_fullscreen_alert_preview_active(now):
            start_clock -= 0.85
        for index in range(count):
            self.sample_index = int(getattr(self, "sample_index", 0)) + 1
            message = Message(app, sender, text, avatar, avatar, serial=self.sample_index, action_key=action_key)
            message.popup_until = now + 8.0
            message.barrage_started_at = start_clock + index * 0.18
            messages.append(message)
        return messages

    def _ensure_fullscreen_plugin_barrage_messages(self, now: float):
        if bool(getattr(self, "_runtime_extras_retired", False)):
            return
        context_active = self._fullscreen_overlay_context_active()
        alert_style = self._fullscreen_plugin_alert_style()
        if not context_active or alert_style != "barrage":
            self.fullscreen_plugin_barrage_alert_key = ""
            self.fullscreen_plugin_barrage_messages = []
            return
        alerts = self._fullscreen_plugin_alerts(1)
        key = self._fullscreen_plugin_alert_key(alerts[0]) if alerts else ""
        action = presentation_fullscreen_plugin_barrage_update_action(
            context_active=context_active,
            alert_style=alert_style,
            has_alert=bool(alerts),
            alert_key=key,
            previous_alert_key=getattr(self, "fullscreen_plugin_barrage_alert_key", ""),
            has_existing_messages=bool(getattr(self, "fullscreen_plugin_barrage_messages", [])),
        )
        if action == "clear":
            self.fullscreen_plugin_barrage_alert_key = ""
            self.fullscreen_plugin_barrage_messages = []
            return
        if action == "keep":
            return
        self.fullscreen_plugin_barrage_alert_key = key
        self.fullscreen_plugin_barrage_messages = self._fullscreen_plugin_alert_barrage_messages(repeat_count=10, now=now)

    def add_message(self):
        if bool(getattr(self, "_runtime_extras_retired", False)):
            return
        if not self.message_samples:
            return
        app, sender, text, name_color, avatar_color = self.message_samples[self.sample_index % len(self.message_samples)]
        self.sample_index += 1
        message = Message(app, sender, text, name_color, avatar_color, serial=self.sample_index)
        message.barrage_started_at = self.barrage_clock
        self.messages.insert(0, message)
        self._enforce_stack_limit()
        self._pulse_stack()
        self.update()

    def show_barrage_preview(self):
        if bool(getattr(self, "_runtime_extras_retired", False)):
            return
        now = time.monotonic()
        if now - getattr(self, "last_barrage_preview_at", 0.0) < 0.18:
            return
        self.last_barrage_preview_at = now
        self.barrage_preview_force_anytime_until = now + 6.2
        self.sample_index += 1
        message = Message(
            "预览",
            "弹幕",
            f"位置 {int(getattr(self, 'barrage_y_offset', 0))}px",
            QColor(96, 186, 255),
            QColor(52, 118, 240),
            serial=self.sample_index,
        )
        message.popup_until = now + 1.0
        message.barrage_started_at = float(getattr(self, "barrage_clock", 0.0)) - 0.85
        self.barrage_preview_messages.insert(0, message)
        self.barrage_preview_messages = self.barrage_preview_messages[:3]
        if getattr(self, "settings_window_active", False):
            self.scene_checked_at = 0.0
            self._update_scene_state(now)
        self._sync_barrage_overlay(now)
        self.update()

    def show_plugin_fullscreen_alert_preview(self):
        if bool(getattr(self, "_runtime_extras_retired", False)):
            return
        now = time.monotonic()
        self.plugin_fullscreen_alert_preview_until = now + 6.2
        self.fullscreen_plugin_barrage_alert_key = ""
        self.fullscreen_plugin_barrage_messages = []
        try:
            self._sync_fullscreen_plugin_alert_overlay(now)
        except Exception:
            pass
        self.update()

    def _enforce_stack_limit(self):
        self.messages = self.messages[:6]

    def _pulse_stack(self):
        self.stack_bounce_until = time.monotonic() + 0.46

    def _set_expanded(self, expanded: bool):
        expanded = bool(expanded)
        if not expanded:
            self._island_dj_outro_until = 0.0
        if self.expanded == expanded:
            if not expanded:
                self.plugin_panel_open = False
            return
        self.expanded = expanded
        if not expanded:
            self.plugin_panel_open = False
        if self._taskbar_compact_active():
            self.taskbar_follow_last_at = 0.0
            self.taskbar_follow_last_key = ""
        if self.finder_mode:
            self.clearMask()
            self.interaction_mask_active = False
            self.last_mask_rect = QRectF()
            self.mask_deferred_until = time.monotonic() + (0.34 if expanded else 0.18)

    def keyPressEvent(self, event):
        key = event.key()
        extras_retired = bool(getattr(self, "_runtime_extras_retired", False))
        if extras_retired and key in (Qt.Key.Key_P, Qt.Key.Key_N, Qt.Key.Key_C,
                Qt.Key.Key_0, Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3, Qt.Key.Key_T):
            return
        settings_changed = False
        if not extras_retired and self._handle_alarm_editor_key_event(event):
            self.update()
            return
        if key == Qt.Key.Key_Escape:
            self.close()
        elif key == Qt.Key.Key_M:
            self.music_playing = not self.music_playing
            self._enforce_stack_limit()
            self._pulse_stack()
            settings_changed = True
        elif key == Qt.Key.Key_P:
            self.persistent_plugin = not self.persistent_plugin
            self._enforce_stack_limit()
            self._pulse_stack()
            settings_changed = True
        elif key == Qt.Key.Key_N:
            self.add_message()
        elif key == Qt.Key.Key_C:
            self.messages.clear()
            self._pulse_stack()
        elif key == Qt.Key.Key_F:
            self.set_finder_mode(not self.finder_mode, notify=False)
            settings_changed = True
        elif key == Qt.Key.Key_Space:
            self._set_expanded(not self.expanded)
            self._pulse_stack()
            settings_changed = True
        elif key in (Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3):
            self._set_plugin_focus(key - Qt.Key.Key_1)
            self._set_expanded(True)
            self._pulse_stack()
            settings_changed = True
        elif key == Qt.Key.Key_0:
            known_plugins = ["time"]
            next_count = (len(self._enabled_plugin_ids()) + 1) % 2
            self.enabled_plugin_ids = set(known_plugins[:next_count])
            self.enabled_plugin_count = next_count
            self._set_plugin_focus(min(self.plugin_focus_target, max(0, next_count - 1)), immediate=True)
            self._pulse_stack()
            settings_changed = True
        elif key == Qt.Key.Key_T:
            self.show_seconds = not self.show_seconds
            self._pulse_stack()
            settings_changed = True
        if settings_changed:
            self._notify_settings_changed()
        self.update()

    def inputMethodEvent(self, event):
        if bool(getattr(self, "_runtime_extras_retired", False)):
            super().inputMethodEvent(event)
            return
        try:
            commit = event.commitString()
        except Exception:
            commit = ""
        if commit and self._handle_alarm_editor_key(commit):
            try:
                event.accept()
            except Exception:
                pass
            return
        super().inputMethodEvent(event)

    def settings_snapshot(self) -> dict[str, object]:
        return {
            "v2_music_playing": self.music_playing,
            "v2_music_auto_detect": bool(getattr(self, "music_auto_detect", False)),
            "v2_persistent_plugin": self.persistent_plugin,
            "v2_location_mode": str(getattr(self, "location_mode", "finder" if self.finder_mode else "free")),
            "v2_top_notch_height_mode": self._normalize_top_notch_height_mode(getattr(self, "top_notch_height_mode", "standard")),
            "v2_finder_mode": self.finder_mode,
            "v2_manual_mydockfinder": bool(getattr(self, "manual_finder_available", False)),
            "v2_mydockfinder_path": str(getattr(self, "mydockfinder_path", "") or ""),
            "v2_mydockfinder_listener_process_names": list(getattr(self, "mydockfinder_listener_process_names", DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES)),
            "v2_position_preset": str(getattr(self, "position_preset", "last") or "last"),
            "v2_taskbar_corner_style": str(getattr(self, "taskbar_corner_style", "win11")),
            "v2_taskbar_auto_hide_mode": str(getattr(self, "taskbar_auto_hide_mode", "auto") or "auto"),
            "v2_tasker_follow_taskbar_color": bool(getattr(self, "tasker_follow_taskbar_color", False)),
            "v2_music_background_follow_previous_style": str(getattr(self, "music_background_follow_previous_style", "") or ""),
            "v2_expanded": self.expanded,
            "v2_show_seconds": self.show_seconds,
            "v2_enabled_plugins": list(self._enabled_plugin_ids()),
            "v2_side_island_style": str(getattr(self, "side_island_style", "standard")),
            "v2_plugin_island_style": str(getattr(self, "plugin_island_style", "standard")),
            "v2_plugin_fullscreen_alert_style": str(getattr(self, "plugin_fullscreen_alert_style", "toast")),
            "v2_plugin_fullscreen_alert_x_offset": int(getattr(self, "plugin_fullscreen_alert_x_offset", 0)),
            "v2_plugin_fullscreen_alert_y_offset": int(getattr(self, "plugin_fullscreen_alert_y_offset", 0)),
            "v2_music_island_style": str(getattr(self, "music_island_style", "standard")),
            "v2_music_expanded_layout_style": self._normalize_music_expanded_layout_style(getattr(self, "music_expanded_layout_style", "standard")),
            "v2_time_alarm_side_display": str(getattr(self, "time_alarm_side_display", "note")),
            "v2_ambient_intensity": self.ambient_intensity,
            "v2_music_ambience_style": getattr(self, "music_ambience_style", "sea_level"),
            "v2_music_flow_intensity": round(float(getattr(self, "music_flow_intensity", 0.74)), 3),
            "v2_music_reactive_background_enabled": bool(getattr(self, "music_reactive_background_enabled", False)),
            "v2_music_reactive_background_intensity": int(getattr(self, "music_reactive_background_intensity", 55)),
            "v2_compact_duotone_balance": int(getattr(self, "compact_duotone_balance", 50)),
            "v2_expanded_duotone_balance": int(getattr(self, "expanded_duotone_balance", 50)),
            "v2_solid_album_background_fallback": str(getattr(self, "solid_album_background_fallback", "#16BCFF")),
            "v2_font_family": self.ui_font_family,
            "v2_high_quality_font_rendering": bool(getattr(self, "high_quality_font_rendering", False)),
            "v2_animation_fps": self.animation_fps,
            "v2_lyric_animation_fps": int(getattr(self, "lyric_animation_fps", 120)),
            "v2_animation_speed": self.animation_speed,
            "v2_empty_island_display_mode": self._empty_island_display_mode(),
            "v2_no_music_island_priority": self._no_music_island_priority(),
            "v2_empty_bar_palette": self.empty_bar_palette,
            "v2_empty_bar_width": int(getattr(self, "empty_bar_width", 92)),
            "v2_empty_bar_height": int(getattr(self, "empty_bar_height", 14)),
            "v2_empty_bar_y_offset": int(getattr(self, "empty_bar_y_offset", 0)),
            "v2_empty_bar_opacity": int(getattr(self, "empty_bar_opacity", 100)),
            "v2_empty_bar_custom_colors": list(getattr(self, "empty_bar_custom_colors", ("#46D6FF", "#73FFA6", "#FF5A86"))),
            "v2_stack_center_ratio": round(float(self.stack_center_ratio), 5),
            "v2_window_pos": list(self.saved_window_pos) if self.saved_window_pos is not None else None,
            "v2_last_window_position": (
                {"x": int(getattr(self, "last_window_position")[0]), "y": int(getattr(self, "last_window_position")[1])}
                if isinstance(getattr(self, "last_window_position", None), (list, tuple))
                and len(getattr(self, "last_window_position", ())) >= 2
                else None
            ),
            "v2_stack_direction": int(getattr(self, "stack_direction", 1)),
            "v2_barrage_y_offset": int(self.barrage_y_offset),
            "v2_barrage_font_size": int(self.barrage_font_size),
            "v2_barrage_speed": int(getattr(self, "barrage_speed", 100)),
            "v2_barrage_use_backdrop": self.barrage_use_backdrop,
            "v2_barrage_show_icon": self.barrage_show_icon,
            "v2_barrage_display_mode": self._barrage_display_mode(),
            "v2_auto_expand_bounds": self.auto_expand_bounds,
            "v2_expand_direction": self.expand_direction,
            "v2_lyric_offset_seconds": round(float(getattr(self, "lyric_offset_seconds", 0.0)), 3),
            "v2_lyric_font_size": int(getattr(self, "lyric_font_size", 18)),
            "v2_music_surface_reading_lift_level": normalize_music_reading_lift_level(
                getattr(self, "music_surface_reading_lift_level", MUSIC_READING_LIFT_DEFAULT_LEVEL)
            ),
            "v2_music_surface_reading_gradient_steps": normalize_music_reading_gradient_steps(
                getattr(self, "music_surface_reading_gradient_steps", MUSIC_READING_GRADIENT_DEFAULT_STEPS)
            ),
            "v2_music_surface_reading_strategy": normalize_music_reading_strategy(
                getattr(self, "music_surface_reading_strategy", MUSIC_READING_STRATEGY_DEFAULT)
            ),
            "v2_music_sparse_highlight_seconds_per_unit": normalize_sparse_seconds_per_unit(
                getattr(self, "music_sparse_highlight_seconds_per_unit", SPARSE_MIN_SECONDS_PER_UNIT)
            ),
            "v2_enable_dual_line_lyrics": bool(getattr(self, "enable_dual_line_lyrics", False)),
            "v2_music_intro_cleanup_enabled": bool(getattr(self, "music_intro_cleanup_enabled", True)),
            "v2_dual_line_font_size": int(getattr(self, "dual_line_font_size", 12)),
            "v2_dual_line_secondary_weight": int(getattr(self, "dual_line_secondary_weight", 72)),
            "v2_dual_line_secondary_spacing": int(getattr(self, "dual_line_secondary_spacing", 100)),
            "v2_compact_lyric_show_cover": bool(getattr(self, "compact_lyric_show_cover", True)),
            "v2_wave_font_weight": self._wave_font_weight_key(),
            "v2_lyric_flow_style": str(getattr(self, "lyric_flow_style", "wave_stream")),
            "v2_gelv_font_family": self.GELV_FONT_FAMILY,
            "v2_gelv_primary_font_size": int(getattr(self, "gelv_primary_font_size", 22)),
            "v2_gelv_secondary_font_size": int(getattr(self, "gelv_secondary_font_size", 12)),
            "v2_gelv_dual_line_font_size": int(getattr(self, "gelv_dual_line_font_size", 17)),
            "v2_gelv_initial_word_spacing": int(getattr(self, "gelv_initial_word_spacing", 220)),
            "v2_gelv_word_spacing": int(getattr(self, "gelv_word_spacing", 320)),
            "v2_gelv_color_fill_enabled": bool(getattr(self, "gelv_color_fill_enabled", False)),
            "v2_gelv_word_fill_enabled": bool(getattr(self, "gelv_word_fill_enabled", False)),
            "v2_ember_font_weight": self._ember_font_weight_key(),
            "v2_ember_font_size": int(getattr(self, "ember_font_size", 18)),
            "v2_ember_line_mode": self._ember_line_mode_key(),
            "v2_ember_dual_line_enabled": bool(getattr(self, "ember_dual_line_enabled", False)),
            "v2_ember_italic_enabled": bool(getattr(self, "ember_italic_enabled", False)),
            "v2_ember_show_cover": bool(getattr(self, "ember_show_cover", True)),
            "v2_ember_word_fill_enabled": bool(getattr(self, "ember_word_fill_enabled", True)),
            "v2_ember_color_fill_enabled": bool(getattr(self, "ember_color_fill_enabled", False)),
            "v2_ember_back_gray_mix": int(getattr(self, "ember_back_gray_mix", 62)),
            "v2_ember_color_back_gray_mix": int(getattr(self, "ember_color_back_gray_mix", 0)),
            "v2_ember_back_opacity": int(getattr(self, "ember_back_opacity", 58)),
            "v2_ember_blur_panel_strength": int(getattr(self, "ember_blur_panel_strength", 18)),
            "v2_ember_lyric_offset_seconds": round(float(getattr(self, "ember_lyric_offset_seconds", 0.0)), 3),
            "v2_no_lyrics_display_mode": str(getattr(self, "no_lyrics_display_mode", "text")),
            "v2_album_island_cover_count": int(getattr(self, "album_island_cover_count", 5)),
            "v2_album_island_cover_size": int(getattr(self, "album_island_cover_size", 30)),
            "v2_album_island_compact_cover_size": int(getattr(self, "album_island_compact_cover_size", getattr(self, "album_island_cover_size", 30))),
            "v2_album_island_expanded_cover_size": int(getattr(self, "album_island_expanded_cover_size", 72)),
            "v2_album_island_spacing": int(getattr(self, "album_island_spacing", 112)),
            "v2_album_island_secondary_style": str(getattr(self, "album_island_secondary_style", "perspective_dim")),
            "v2_album_island_perspective_amount": int(getattr(self, "album_island_perspective_amount", 100)),
            "v2_album_island_listened_seconds_total": round(float(getattr(self, "listened_seconds_total", 0.0)), 3),
            "v2_album_island_history": self._serializable_album_history(),
            "v2_no_lyrics_text": getattr(self, "no_lyrics_text", "暂无歌词"),
            "v2_super_topmost": bool(getattr(self, "super_topmost_enabled", True)),
        }

    def _begin_surface_transition(self, reason: str = "layout"):
        if not self.isVisible() or self.width() <= 2 or self.height() <= 2:
            return False
        try:
            pixmap = self.grab()
        except Exception:
            return False
        if pixmap.isNull():
            return False
        top_left = self.mapToGlobal(QPoint(0, 0))
        self.surface_transition_pixmap = pixmap
        self.surface_transition_global_rect = QRectF(
            float(top_left.x()),
            float(top_left.y()),
            float(self.width()),
            float(self.height()),
        )
        self.surface_transition_started_at = time.monotonic()
        self.surface_transition_duration = 0.24 if reason in ("finder", "standalone") else 0.20
        self.surface_transition_reason = reason
        return True

    def _paint_surface_transition(self, painter: QPainter, now: float):
        pixmap = getattr(self, "surface_transition_pixmap", QPixmap())
        if pixmap.isNull():
            return
        duration = self._animation_duration(float(getattr(self, "surface_transition_duration", 0.22)))
        elapsed = now - float(getattr(self, "surface_transition_started_at", now))
        if elapsed >= duration:
            self.surface_transition_pixmap = QPixmap()
            self.surface_transition_global_rect = QRectF()
            self.surface_transition_reason = ""
            return
        progress = max(0.0, min(1.0, elapsed / duration))
        eased = progress * progress * (3.0 - 2.0 * progress)
        current_top_left = self.mapToGlobal(QPoint(0, 0))
        old_global = getattr(self, "surface_transition_global_rect", QRectF())
        if old_global.isNull():
            target = QRectF(0.0, 0.0, float(pixmap.width()), float(pixmap.height()))
        else:
            target = QRectF(
                old_global.left() - float(current_top_left.x()),
                old_global.top() - float(current_top_left.y()),
                old_global.width(),
                old_global.height(),
            )
        reason = str(getattr(self, "surface_transition_reason", "") or "")
        drift_y = self._u(-8.0 if reason == "finder" else 8.0) * eased
        target.translate(0.0, drift_y)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setOpacity(max(0.0, min(0.92, 0.92 * (1.0 - eased))))
        painter.drawPixmap(target, pixmap, QRectF(0.0, 0.0, float(pixmap.width()), float(pixmap.height())))
        painter.restore()

    def _clear_surface_transition(self):
        self.surface_transition_pixmap = QPixmap()
        self.surface_transition_global_rect = QRectF()
        self.surface_transition_started_at = 0.0
        self.surface_transition_reason = ""

    def _reset_group_frame_state(self, clear_mask: bool = True):
        self.group_frame_motion = None
        self.group_frame_mode_key = ""
        self.group_frame_target_rect = QRectF()
        self.group_frame_target_mode_key = ""
        self.group_frame_mask_path = None
        self.taskbar_notification_frame_motion = None
        self.taskbar_notification_frame_rect = QRectF()
        self.finder_hover_rect = QRectF()
        self.interaction_mask_active = False
        self.last_mask_rect = QRectF()
        if clear_mask:
            try:
                self.clearMask()
            except Exception:
                pass

    def _clear_mode_interaction_state(self, clear_drag_detach: bool = True):
        self.hovered_key = ""
        self.hover_armed_key = ""
        self.left_stack_at = 0.0
        if clear_drag_detach:
            self.drag_detach_global_rects = {}

    def _reset_location_runtime_state(self, clear_surface: bool = False, clear_drag_detach: bool = True):
        self._reset_group_frame_state(clear_mask=True)
        self._clear_mode_interaction_state(clear_drag_detach=clear_drag_detach)
        self.taskbar_follow_last_at = 0.0
        self.taskbar_follow_last_key = ""
        self.taskbar_hidden_by_follow = False
        if clear_surface:
            self._clear_surface_transition()

    def _mydockfinder_available(self) -> bool:
        return bool(getattr(self, "mydockfinder_running", False) or getattr(self, "manual_finder_available", False))

    def _mydockfinder_root_path(self) -> Path:
        raw = str(getattr(self, "mydockfinder_path", "") or "").strip().strip('"')
        if not raw:
            return Path(r"B:\SteamLibrary\steamapps\common\MyDockFinder")
        path = Path(raw)
        if path.suffix.lower() == ".exe":
            return path.parent
        return path

    def available_location_modes(self) -> list[str]:
        return available_location_modes_for_state(mydockfinder_available=self._mydockfinder_available())

    def set_mydockfinder_path(self, path: str, notify: bool = True):
        self.mydockfinder_path = str(path or "").strip().strip('"')
        if notify:
            self._notify_settings_changed()

    def set_mydockfinder_listener_process_names(self, names, notify: bool = True):
        self.mydockfinder_listener_process_names = tuple(str(name or "").strip().casefold() for name in (names or ()) if str(name or "").strip())
        if notify:
            self._notify_settings_changed()

    def set_mydockfinder_diagnostics(self, diagnostics: dict[str, object] | None, notify: bool = True):
        self.mydockfinder_diagnostics = dict(diagnostics or {})
        if notify:
            self._notify_settings_changed()

    def _reconcile_mydockfinder_availability(self, previous_available: bool, notify: bool = True):
        current_mode = str(getattr(self, "location_mode", "free") or "free")
        desired_mode = self._canonical_location_mode(current_mode)
        if desired_mode != current_mode:
            self.set_location_mode(desired_mode, notify=False)
        elif bool(previous_available) != self._mydockfinder_available():
            self._reset_location_runtime_state(clear_surface=True)
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_mydockfinder_running(self, running: bool, notify: bool = True):
        previous_available = self._mydockfinder_available()
        self.mydockfinder_running = bool(running)
        self._reconcile_mydockfinder_availability(previous_available, notify=notify)

    def set_manual_finder_available(self, enabled: bool, notify: bool = True):
        previous_available = self._mydockfinder_available()
        self.manual_finder_available = bool(enabled)
        self._reconcile_mydockfinder_availability(previous_available, notify=notify)

    def _canonical_location_mode(self, mode: str) -> str:
        return positioning_canonical_location_mode(
            mode,
            mydockfinder_available=self._mydockfinder_available(),
            taskbar_auto_hide_mode=str(getattr(self, "taskbar_auto_hide_mode", "auto") or "auto"),
        )

    def _normalize_top_notch_height_mode(self, mode: str | None) -> str:
        value = str(mode or "standard").strip().lower()
        return value if value in {"compact", "standard", "spacious"} else "standard"

    def set_top_notch_height_mode(self, mode: str, notify: bool = True):
        self.top_notch_height_mode = self._normalize_top_notch_height_mode(mode)
        try:
            self._pulse_stack()
        except Exception:
            pass
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_location_mode(self, mode: str, notify: bool = True):
        mode = self._canonical_location_mode(mode)
        previous = str(getattr(self, "location_mode", "finder" if getattr(self, "finder_mode", False) else "free"))
        started_surface_transition = False
        initial_plan = build_location_mode_transition_plan(
            mode,
            previous,
            drag_candidate=bool(getattr(self, "drag_candidate", False)),
            surface_transition_started=False,
        )
        if initial_plan.begin_surface_transition_reason:
            try:
                started_surface_transition = bool(self._begin_surface_transition(initial_plan.begin_surface_transition_reason))
            except Exception:
                pass
        plan = build_location_mode_transition_plan(
            mode,
            previous,
            drag_candidate=bool(getattr(self, "drag_candidate", False)),
            surface_transition_started=started_surface_transition,
        )
        if plan.reset_runtime_state:
            self._reset_location_runtime_state(clear_surface=plan.reset_clear_surface)
        self.location_mode = plan.mode
        self.finder_mode = plan.finder_mode
        self.attach_to_real_finder = plan.attach_to_real_finder
        self.taskbar_above_follow_active = plan.taskbar_above_follow_active
        self.taskbar_fusion_mode = plan.taskbar_fusion_mode
        self.taskbar_preview_visible = plan.taskbar_preview_visible
        if plan.clear_scene_state:
            self.scene_suppressed = False
            self.scene_reason = ""
            self.scene_transition_mode = ""
            self.group_frame_mask_path = None
        if plan.clear_mask:
            try:
                self.clearMask()
            except Exception:
                pass
        if plan.refresh_finder_material:
            try:
                self.finder_material = read_finder_material(self._mydockfinder_root_path(), force_screen_sample=True, include_center=not self.isVisible())
                self.finder_material_refreshed_at = time.monotonic()
                self._center_on_screen()
                self._force_super_topmost()
            except Exception:
                pass
        if plan.refresh_taskbar_follow_position:
            try:
                self._refresh_taskbar_follow_position()
            except Exception:
                pass
        if plan.sync_taskbar_color:
            try:
                self._sync_music_background_to_taskbar_color(force=True)
            except Exception:
                pass
        if plan.refresh_taskbar_above_follow_position:
            try:
                self._refresh_taskbar_above_follow_position()
            except Exception:
                pass
        if plan.settle_top_notch:
            try:
                anchor = self._current_main_target_global_rect()
                if anchor.isNull():
                    anchor = self._current_main_global_rect()
                probe = QPoint(int(round(anchor.center().x())), int(round(anchor.center().y()))) if not anchor.isNull() else self.mapToGlobal(QPoint(self.width() // 2, 0))
                screen = QGuiApplication.screenAt(probe) or QGuiApplication.primaryScreen()
                if screen is not None and not anchor.isNull():
                    self._settle_standalone_top_notch(anchor, screen, save=False)
            except Exception:
                pass
        self._sync_taskbar_drop_overlay()
        try:
            self._pulse_stack()
        except Exception:
            pass
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_taskbar_fusion_mode(self, mode: str, notify: bool = True):
        plan = build_taskbar_fusion_mode_plan(mode)
        if plan.force_expanded is not None:
            self.expanded = bool(plan.force_expanded)
        if plan.force_plugin_panel_open is not None:
            self.plugin_panel_open = bool(plan.force_plugin_panel_open)
        if plan.taskbar_preview_visible_before_location_change is not None:
            self.taskbar_preview_visible = bool(plan.taskbar_preview_visible_before_location_change)
        self.set_location_mode(plan.target_location_mode, notify=notify)

    def set_taskbar_recognition_threshold(self, threshold: float, notify: bool = True):
        try:
            value = float(threshold)
        except (TypeError, ValueError):
            value = 0.66
        self.taskbar_recognition_threshold = max(0.34, min(0.95, value))
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_taskbar_corner_style(self, style: str, notify: bool = True):
        style = str(style or "win11").strip().lower()
        if style not in {"win10", "win11"}:
            style = "win11"
        self.taskbar_corner_style = style
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_taskbar_auto_hide_mode(self, mode: str, notify: bool = True):
        plan = build_taskbar_auto_hide_mode_plan(
            mode,
            current_location_mode=str(getattr(self, "location_mode", "free") or "free"),
        )
        self.taskbar_auto_hide_mode = plan.mode
        if plan.reset_follow_cache:
            self.taskbar_follow_last_at = 0.0
            self.taskbar_windows_snapshot_cache = {}
        if plan.clear_above_taskbar_follow:
            self.taskbar_above_follow_active = False
        if plan.force_location_mode:
            self.location_mode = plan.force_location_mode
        if plan.force_finder_mode is not None:
            self.finder_mode = bool(plan.force_finder_mode)
        if plan.force_attach_to_real_finder is not None:
            self.attach_to_real_finder = bool(plan.force_attach_to_real_finder)
        if plan.force_taskbar_fusion_mode:
            self.taskbar_fusion_mode = plan.force_taskbar_fusion_mode
        if plan.clear_taskbar_preview:
            self.taskbar_preview_visible = False
        if plan.clear_taskbar_drop_state:
            self._clear_taskbar_drop_state()
        if plan.hide_taskbar_drop_overlay:
            self._hide_taskbar_drop_overlay()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_taskbar_plugin_compact_style(self, style: str, notify: bool = True):
        style = str(style or "rail").strip().lower()
        if style not in {"rail", "block"}:
            style = "rail"
        self.taskbar_plugin_compact_style = style
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_taskbar_message_compact_style(self, style: str, notify: bool = True):
        style = str(style or "strip").strip().lower()
        if style not in {"strip", "block"}:
            style = "strip"
        self.taskbar_message_compact_style = style
        self._pulse_stack()
        if notify:
            self._notify_settings_changed()
        self.update()

    def set_taskbar_left_handle_color(self, color: str, notify: bool = True):
        qcolor = QColor(str(color or "#8E8E93"))
        self.taskbar_left_handle_color = qcolor.name().upper() if qcolor.isValid() else "#8E8E93"
        if notify:
            self._notify_settings_changed()
        self.update()

    def _taskbar_allowed(self) -> bool:
        return (
            not bool(getattr(self, "finder_mode", False))
            and str(getattr(self, "location_mode", "free")) != "finder"
            and not self._mydockfinder_available()
        )

    def _should_show_taskbar_preview(self, lower_third_ratio: float) -> bool:
        return positioning_should_show_taskbar_preview(
            lower_third_ratio,
            taskbar_allowed=self._taskbar_allowed(),
            recognition_threshold=float(getattr(self, "taskbar_recognition_threshold", 0.66)),
            location_mode=str(getattr(self, "location_mode", "free") or "free"),
        )

    def _taskbar_contacting_rect(self, main_rect: QRectF, taskbar_rect: QRectF) -> bool:
        return positioning_taskbar_contacting_rect(
            main_rect,
            taskbar_rect,
            slop=self._u(4.0) if hasattr(self, "_u") else 4.0,
        )

    def _should_show_taskbar_preview_for_rect(self, lower_third_ratio: float, main_rect: QRectF, taskbar_rect: QRectF) -> bool:
        return positioning_should_show_taskbar_preview_for_rect(
            lower_third_ratio,
            main_rect,
            taskbar_rect,
            taskbar_allowed=self._taskbar_allowed(),
            recognition_threshold=float(getattr(self, "taskbar_recognition_threshold", 0.66)),
            location_mode=str(getattr(self, "location_mode", "free") or "free"),
            slop=self._u(4.0) if hasattr(self, "_u") else 4.0,
        )

    def _taskbar_visible_for_screen(self, screen) -> bool:
        rect = self._taskbar_rect_for_screen(screen)
        return rect is not None and not rect.isNull() and rect.width() > 1.0 and rect.height() > 1.0

    def _taskbar_drag_ratio(self, global_pos: QPoint) -> tuple[float, object | None]:
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        if screen is None:
            return 0.0, None
        return positioning_taskbar_drag_screen_ratio(global_pos, screen.geometry()), screen

    def _taskbar_attach_hot_zone_for_screen(self, screen) -> QRectF:
        if screen is None:
            return QRectF()
        return positioning_taskbar_attach_hot_zone_rect(screen.geometry(), unit=self._u(1.0))

    def _taskbar_attach_hot_zone_host_rect(self, hot_zone: QRectF) -> QRectF:
        return positioning_taskbar_attach_hot_zone_host_rect(hot_zone, unit=self._u(1.0))

    def _taskbar_attach_hot_zone_contains(self, screen, global_pos: QPoint | QPointF) -> tuple[bool, QRectF]:
        zone = self._taskbar_attach_hot_zone_for_screen(screen)
        return positioning_taskbar_attach_hot_zone_contains(zone, global_pos), zone

    def _taskbar_attach_hot_zone_accepts_rect(self, hot_zone: QRectF, main_rect: QRectF) -> bool:
        return positioning_taskbar_attach_hot_zone_accepts_rect(hot_zone, main_rect)

    def _current_main_global_rect(self) -> QRectF:
        try:
            visuals = self._build_visuals()
            main_visual = next((visual for visual in visuals if visual.z_index == 10), None)
            if main_visual is None:
                return QRectF()
            rect = QRectF(main_visual.rect)
            motion = getattr(self, "motions", {}).get(main_visual.key)
            if motion is not None and getattr(motion, "opacity", 0.0) > 0.08:
                rect = QRectF(motion.x, motion.y, motion.w, motion.h)
            origin = self.mapToGlobal(QPoint(0, 0))
            rect.translate(float(origin.x()), float(origin.y()))
            return rect
        except Exception:
            return QRectF()

    def _current_main_target_global_rect(self) -> QRectF:
        try:
            visuals = self._build_visuals()
            main_visual = next((visual for visual in visuals if visual.z_index == 10), None)
            if main_visual is None:
                return QRectF()
            rect = QRectF(main_visual.rect)
            origin = self.mapToGlobal(QPoint(0, 0))
            rect.translate(float(origin.x()), float(origin.y()))
            return rect
        except Exception:
            return QRectF()

    def _taskbar_material_is_light(self, color: QColor) -> bool:
        if color is None or not color.isValid():
            return False
        luminance = 0.2126 * color.redF() + 0.7152 * color.greenF() + 0.0722 * color.blueF()
        return luminance >= 0.72

    def _taskbar_material_exclusion_rects(self) -> list[QRectF]:
        selected = QRectF(getattr(self, "taskbar_selected_drop_zone", QRectF()))
        main_rect = QRectF()
        selected_is_authoritative = (
            self._taskbar_compact_active()
            and not selected.isNull()
            and selected.width() > 1.0
            and selected.height() > 1.0
        )
        if not selected_is_authoritative:
            try:
                main_rect = self._current_main_global_rect()
            except Exception:
                main_rect = QRectF()
        motion_rects: list[QRectF] = []
        try:
            origin = self.mapToGlobal(QPoint(0, 0))
            for motion in getattr(self, "motions", {}).values():
                if getattr(motion, "opacity", 0.0) <= 0.08:
                    continue
                rect = QRectF(motion.rect)
                if rect.isNull() or rect.width() <= 1.0 or rect.height() <= 1.0:
                    continue
                rect.translate(float(origin.x()), float(origin.y()))
                motion_rects.append(rect)
        except Exception:
            pass
        return positioning_taskbar_material_exclusion_rects(
            selected_drop_zone=selected,
            main_global_rect=main_rect,
            motion_global_rects=motion_rects,
            taskbar_compact_active=self._taskbar_compact_active(),
            unit=self._u(1.0),
        )

    def _sample_taskbar_material_color(self, screen, taskbar_rect: QRectF) -> QColor:
        if screen is None or taskbar_rect is None or taskbar_rect.isNull():
            return QColor()
        try:
            sample_size = int(max(6, min(18, min(taskbar_rect.width(), taskbar_rect.height()) * 0.45)))
            horizontal = taskbar_rect.width() >= taskbar_rect.height()
            ratios = (0.18, 0.32, 0.50, 0.68, 0.82)
            exclusions = []
            try:
                exclusions = [
                    QRectF(rect).adjusted(-sample_size, -sample_size, sample_size, sample_size)
                    for rect in self._taskbar_material_exclusion_rects()
                    if rect is not None and not QRectF(rect).isNull()
                ]
            except Exception:
                exclusions = []

            candidates: list[QRectF] = []
            for ratio in ratios:
                if horizontal:
                    cx = taskbar_rect.left() + taskbar_rect.width() * ratio
                    cy = taskbar_rect.center().y()
                else:
                    cx = taskbar_rect.center().x()
                    cy = taskbar_rect.top() + taskbar_rect.height() * ratio
                sample = QRectF(cx - sample_size / 2.0, cy - sample_size / 2.0, sample_size, sample_size)
                candidates.append(sample)
            safe_candidates = [
                sample for sample in candidates
                if not any(sample.intersects(exclusion) for exclusion in exclusions)
            ]
            if not safe_candidates:
                safe_candidates = [QRectF(taskbar_rect.center().x() - sample_size / 2.0, taskbar_rect.center().y() - sample_size / 2.0, sample_size, sample_size)]

            colors: list[QColor] = []
            for sample in safe_candidates:
                x = int(round(sample.left()))
                y = int(round(sample.top()))
                image = screen.grabWindow(0, x, y, sample_size, sample_size).toImage()
                if image.isNull():
                    continue
                red = green = blue = count = 0
                step = max(1, sample_size // 6)
                for py in range(0, image.height(), step):
                    for px in range(0, image.width(), step):
                        pixel = image.pixelColor(px, py)
                        red += pixel.red()
                        green += pixel.green()
                        blue += pixel.blue()
                        count += 1
                if count > 0:
                    colors.append(QColor(red // count, green // count, blue // count))
            if not colors:
                return QColor()
            colors.sort(key=lambda color: 0.2126 * color.redF() + 0.7152 * color.greenF() + 0.0722 * color.blueF())
            return QColor(colors[len(colors) // 2])
        except Exception:
            return QColor()

    def _update_taskbar_material_for_screen(self, screen, taskbar_rect: QRectF):
        if screen is None or taskbar_rect is None or taskbar_rect.isNull():
            return
        now = time.monotonic()
        screen_key = self._taskbar_screen_cache_key(screen)
        key = (
            f"{screen_key!r}:"
            f"{int(round(taskbar_rect.left()))},"
            f"{int(round(taskbar_rect.top()))},"
            f"{int(round(taskbar_rect.width()))},"
            f"{int(round(taskbar_rect.height()))}"
        )
        current = QColor(getattr(self, "taskbar_material_color", QColor()))
        sampled_at = float(getattr(self, "taskbar_material_sampled_at", 0.0) or 0.0)
        same_key = key == str(getattr(self, "taskbar_material_sample_key", "") or "")
        if (
            same_key
            and sampled_at > 0.0
            and now - sampled_at < 0.45
        ):
            return
        color = self._sample_taskbar_material_color(screen, taskbar_rect)
        self.taskbar_material_sampled_at = now
        self.taskbar_material_sample_key = key
        if color.isValid():
            self.taskbar_material_color = QColor(color)
            self.taskbar_material_is_light = self._taskbar_material_is_light(color)

    def _taskbar_material_frame_tokens(self) -> dict[str, QColor]:
        base = QColor(getattr(self, "taskbar_material_color", QColor()))
        if not base.isValid():
            base = QColor(34, 38, 46)
        light = bool(getattr(self, "taskbar_material_is_light", False)) or self._taskbar_material_is_light(base)
        if light:
            return {
                "fill_top": QColor(min(255, base.red() + 14), min(255, base.green() + 14), min(255, base.blue() + 14), 232),
                "fill_mid": QColor(max(0, base.red() - 2), max(0, base.green() - 2), max(0, base.blue() - 2), 236),
                "fill_bottom": QColor(max(0, base.red() - 18), max(0, base.green() - 18), max(0, base.blue() - 18), 240),
                "stroke": QColor(0, 0, 0, 64),
                "shadow": QColor(0, 0, 0, 54),
            }
        return {
            "fill_top": QColor(min(255, base.red() + 18), min(255, base.green() + 18), min(255, base.blue() + 18), 226),
            "fill_mid": QColor(base.red(), base.green(), base.blue(), 234),
            "fill_bottom": QColor(max(0, base.red() - 16), max(0, base.green() - 16), max(0, base.blue() - 16), 240),
            "stroke": QColor(255, 255, 255, 46),
            "shadow": QColor(0, 0, 0, 86),
        }

    def _windows_taskbar_hwnds(self) -> list[int]:
        if sys.platform != "win32":
            return []
        try:
            user32 = ctypes.windll.user32
            hwnds: list[int] = []
            primary = user32.FindWindowW("Shell_TrayWnd", None)
            if primary:
                hwnds.append(int(primary))

            enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

            def enum_proc(hwnd, _lparam):
                buffer = ctypes.create_unicode_buffer(128)
                user32.GetClassNameW(hwnd, buffer, 128)
                if buffer.value == "Shell_SecondaryTrayWnd":
                    hwnds.append(int(hwnd))
                return True

            user32.EnumWindows(enum_proc_type(enum_proc), 0)
            return list(dict.fromkeys(hwnds))
        except Exception:
            return []

    def _window_class_name(self, hwnd: int) -> str:
        if sys.platform != "win32" or not hwnd:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(128)
            ctypes.windll.user32.GetClassNameW(wintypes.HWND(int(hwnd)), buffer, 128)
            return str(buffer.value or "")
        except Exception:
            return ""

    def _window_rect_for_hwnd(self, hwnd: int) -> QRectF | None:
        if sys.platform != "win32":
            return None
        try:
            user32 = ctypes.windll.user32
            rect = wintypes.RECT()
            if not user32.GetWindowRect(wintypes.HWND(int(hwnd)), ctypes.byref(rect)):
                return None
            return QRectF(
                float(rect.left),
                float(rect.top),
                float(rect.right - rect.left),
                float(rect.bottom - rect.top),
            )
        except Exception:
            return None

    def _taskbar_screen_cache_key(self, screen) -> tuple[object, ...]:
        if screen is None:
            return ("none",)

        def rect_key(rect) -> tuple[int, int, int, int]:
            value = QRectF(rect)
            return (
                int(round(value.left())),
                int(round(value.top())),
                int(round(value.width())),
                int(round(value.height())),
            )

        try:
            geometry = rect_key(screen.geometry())
        except Exception:
            geometry = (0, 0, 0, 0)
        try:
            available = rect_key(screen.availableGeometry())
        except Exception:
            available = (0, 0, 0, 0)
        name = ""
        try:
            name_fn = getattr(screen, "name", None)
            if callable(name_fn):
                name = str(name_fn() or "")
        except Exception:
            name = ""
        return (name, geometry, available)

    def _taskbar_windows_snapshot_for_screen(self, screen) -> dict[str, object]:
        key = self._taskbar_screen_cache_key(screen)
        now = time.monotonic()
        cache = getattr(self, "taskbar_windows_snapshot_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self.taskbar_windows_snapshot_cache = cache
        cached = cache.get(key)
        try:
            ttl = max(0.04, float(getattr(self, "TASKBAR_QUERY_CACHE_SECONDS", 0.12)))
        except (TypeError, ValueError):
            ttl = 0.12
        if isinstance(cached, dict):
            try:
                if now - float(cached.get("sampled_at", 0.0) or 0.0) <= ttl:
                    return cached
            except (TypeError, ValueError):
                pass
        snapshot = {
            "sampled_at": now,
            "appbar_state": self._windows_taskbar_appbar_state_for_screen(screen),
            "shell_rect": self._windows_taskbar_rect_for_screen(screen),
        }
        cache[key] = snapshot
        if len(cache) > 4:
            oldest_key = min(cache, key=lambda item: float(cache[item].get("sampled_at", 0.0) or 0.0))
            cache.pop(oldest_key, None)
        return snapshot

    def _windows_taskbar_appbar_state_for_screen(self, screen) -> dict[str, object]:
        state: dict[str, object] = {"auto_hide": False, "rect": QRectF(), "edge": ""}
        if sys.platform != "win32" or screen is None:
            return state
        try:
            class APPBARDATA(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("hWnd", wintypes.HWND),
                    ("uCallbackMessage", wintypes.UINT),
                    ("uEdge", wintypes.UINT),
                    ("rc", wintypes.RECT),
                    ("lParam", wintypes.LPARAM),
                ]

            shell32 = ctypes.windll.shell32
            hwnd = self._windows_taskbar_hwnd_for_screen(screen) or 0
            data = APPBARDATA()
            data.cbSize = ctypes.sizeof(APPBARDATA)
            data.hWnd = wintypes.HWND(int(hwnd))
            ABM_GETSTATE = 0x00000004
            ABM_GETTASKBARPOS = 0x00000005
            ABS_AUTOHIDE = 0x00000001
            edge_names = {0: "left", 1: "top", 2: "right", 3: "bottom"}

            raw_state = int(shell32.SHAppBarMessage(ABM_GETSTATE, ctypes.byref(data)))
            state["auto_hide"] = bool(raw_state & ABS_AUTOHIDE)

            pos = APPBARDATA()
            pos.cbSize = ctypes.sizeof(APPBARDATA)
            pos.hWnd = wintypes.HWND(int(hwnd))
            if shell32.SHAppBarMessage(ABM_GETTASKBARPOS, ctypes.byref(pos)):
                rect = QRectF(
                    float(pos.rc.left),
                    float(pos.rc.top),
                    float(pos.rc.right - pos.rc.left),
                    float(pos.rc.bottom - pos.rc.top),
                )
                screen_rect = QRectF(screen.geometry())
                if not rect.isNull() and rect.intersects(screen_rect):
                    state["rect"] = rect.intersected(screen_rect)
                    state["edge"] = edge_names.get(int(pos.uEdge), "")
        except Exception:
            return state
        return state

    def _windows_taskbar_hwnd_for_screen(self, screen) -> int | None:
        if sys.platform != "win32" or screen is None:
            return None
        screen_geo = QRectF(screen.geometry())
        for hwnd in self._windows_taskbar_hwnds():
            taskbar = self._window_rect_for_hwnd(hwnd)
            if taskbar is None or taskbar.isNull() or not taskbar.intersects(screen_geo):
                continue
            return int(hwnd)
        return None

    def _windows_taskbar_rect_for_screen(self, screen) -> QRectF | None:
        if sys.platform != "win32" or screen is None:
            return None
        hwnd = self._windows_taskbar_hwnd_for_screen(screen)
        if not hwnd:
            return None
        taskbar = self._window_rect_for_hwnd(hwnd)
        if taskbar is None or taskbar.isNull():
            return None
        return taskbar.intersected(QRectF(screen.geometry()))

    def _taskbar_shell_rect_is_hidden_autohide(self, screen, shell_rect: QRectF, appbar_state: dict[str, object] | None = None) -> bool:
        state = appbar_state if isinstance(appbar_state, dict) else self._windows_taskbar_appbar_state_for_screen(screen)
        return positioning_taskbar_shell_rect_is_hidden_autohide(
            shell_rect,
            QRectF(state.get("rect", QRectF())) if isinstance(state, dict) else QRectF(),
            auto_hide=bool(state.get("auto_hide", False)) if isinstance(state, dict) else False,
            unit=self._u(1.0),
        )

    def _taskbar_autohide_cursor_reveals(self, screen, taskbar_rect: QRectF) -> bool:
        taskbar = QRectF(taskbar_rect)
        if screen is None or taskbar.isNull():
            return False
        try:
            geo = QRectF(screen.geometry())
            cursor = QPointF(QCursor.pos())
        except Exception:
            return False
        return positioning_taskbar_autohide_cursor_reveals(
            taskbar,
            geo,
            cursor,
            drag_active=self._taskbar_autohide_drag_active(),
            unit=self._u(1.0),
        )

    def _taskbar_autohide_drag_active(self) -> bool:
        return bool(getattr(self, "dragging", False) or getattr(self, "drag_candidate", False))

    def _taskbar_autohide_mouse_pressed(self) -> bool:
        try:
            buttons = QGuiApplication.mouseButtons()
        except Exception:
            return False
        try:
            return bool(buttons != Qt.MouseButton.NoButton)
        except Exception:
            try:
                return bool(int(buttons))
            except Exception:
                return bool(buttons)

    def _taskbar_autohide_cursor_in_taskbar_area(self, screen, taskbar_rect: QRectF, cursor: QPointF | None = None) -> bool:
        taskbar = QRectF(taskbar_rect)
        if screen is None or taskbar.isNull():
            return False
        try:
            geo = QRectF(screen.geometry())
            point = QPointF(QCursor.pos()) if cursor is None else QPointF(cursor)
        except Exception:
            return False
        return positioning_taskbar_autohide_cursor_in_taskbar_area(
            taskbar,
            geo,
            point,
            unit=self._u(1.0),
        )

    def _taskbar_autohide_interaction_keeps_visible(self, screen, taskbar_rect: QRectF) -> bool:
        taskbar = QRectF(taskbar_rect)
        if screen is None or taskbar.isNull():
            self.taskbar_autohide_interaction_visible = False
            self.taskbar_autohide_click_hold_visible = False
            self.taskbar_autohide_left_taskbar_at = 0.0
            return False
        try:
            geo = QRectF(screen.geometry())
            name = ""
            name_fn = getattr(screen, "name", None)
            if callable(name_fn):
                name = str(name_fn() or "")
            screen_key = (
                name,
                int(round(geo.left())),
                int(round(geo.top())),
                int(round(geo.width())),
                int(round(geo.height())),
            )
        except Exception:
            geo = QRectF()
            screen_key = (id(screen),)
        screen_changed = tuple(getattr(self, "taskbar_autohide_interaction_screen_key", ()) or ()) != tuple(screen_key)
        if screen_changed:
            self.taskbar_autohide_interaction_screen_key = tuple(screen_key)
        dragging = self._taskbar_autohide_drag_active()
        try:
            cursor = QPointF(QCursor.pos())
        except Exception:
            cursor = QPointF()
        inside_taskbar = (not dragging) and positioning_taskbar_autohide_cursor_in_taskbar_area(
            taskbar,
            geo,
            cursor,
            unit=self._u(1.0),
        )
        cursor_reveals = positioning_taskbar_autohide_cursor_reveals(
            taskbar,
            geo,
            cursor,
            drag_active=dragging,
            unit=self._u(1.0),
        )
        try:
            now = float(time.monotonic())
        except Exception:
            now = 0.0
        pressed = self._taskbar_autohide_mouse_pressed()
        plan = build_taskbar_autohide_interaction_plan(
            screen_changed=screen_changed,
            dragging=dragging,
            inside_taskbar=inside_taskbar,
            cursor_reveals=cursor_reveals,
            pressed=pressed,
            previous_mouse_pressed=bool(getattr(self, "taskbar_autohide_mouse_pressed", False)),
            interaction_visible=bool(getattr(self, "taskbar_autohide_interaction_visible", False)),
            click_hold_visible=bool(getattr(self, "taskbar_autohide_click_hold_visible", False)),
            left_taskbar_at=getattr(self, "taskbar_autohide_left_taskbar_at", 0.0),
            now=now,
        )
        self.taskbar_autohide_interaction_visible = bool(plan.interaction_visible)
        self.taskbar_autohide_click_hold_visible = bool(plan.click_hold_visible)
        self.taskbar_autohide_mouse_pressed = bool(plan.mouse_pressed)
        self.taskbar_autohide_left_taskbar_at = float(plan.left_taskbar_at)
        return bool(plan.keeps_visible)

    def _taskbar_autohide_recent_visible_active(self, now: float | None = None) -> bool:
        try:
            until = float(getattr(self, "taskbar_autohide_visible_until", 0.0) or 0.0)
        except (TypeError, ValueError):
            until = 0.0
        if until <= 0.0:
            return False
        current = time.monotonic() if now is None else float(now)
        return current < until

    def _mark_taskbar_autohide_visible(self, duration: float = 0.0):
        try:
            current = float(time.monotonic())
        except Exception:
            current = 0.0
        try:
            existing = float(getattr(self, "taskbar_autohide_visible_until", 0.0) or 0.0)
        except (TypeError, ValueError):
            existing = 0.0
        self.taskbar_autohide_visible_until = max(existing, current + max(0.0, float(duration)))

    def _hidden_autohide_follow_rect(self, screen, taskbar_rect: QRectF) -> QRectF:
        try:
            screen_geo = screen.geometry() if screen is not None else QRectF()
        except Exception:
            screen_geo = QRectF()
        return hidden_autohide_follow_rect(screen_geo, taskbar_rect)

    def _taskbar_auto_hide_enabled_for_screen(self, screen) -> bool:
        mode = str(getattr(self, "taskbar_auto_hide_mode", "auto") or "auto")
        mydockfinder = self._mydockfinder_available()
        detected = False
        if (
            screen is not None
            and not mydockfinder
            and mode.strip().lower() not in {"enabled", "disabled"}
        ):
            try:
                state = self._taskbar_windows_snapshot_for_screen(screen).get("appbar_state", {})
                detected = bool(state.get("auto_hide", False)) if isinstance(state, dict) else False
            except Exception:
                detected = False
        return taskbar_auto_hide_enabled_for_state(
            screen_available=screen is not None,
            mydockfinder_available=mydockfinder,
            taskbar_auto_hide_mode=mode,
            detected_auto_hide=detected,
        )

    def _taskbar_autohide_hidden_for_screen(self, screen, taskbar_rect: QRectF | None = None) -> bool:
        if screen is None:
            return False
        if self._mydockfinder_available():
            return False
        try:
            mode = str(getattr(self, "taskbar_auto_hide_mode", "auto") or "auto").strip().lower()
            snapshot = self._taskbar_windows_snapshot_for_screen(screen)
            state = snapshot.get("appbar_state", {})
            if isinstance(state, dict):
                state = dict(state)
                if mode == "enabled":
                    state["auto_hide"] = True
                elif mode == "disabled":
                    state["auto_hide"] = False
            appbar_rect = QRectF(state.get("rect", QRectF())) if isinstance(state, dict) else QRectF()
            shell_rect = snapshot.get("shell_rect", None)
            preliminary = build_taskbar_autohide_hidden_plan(
                taskbar_rect=taskbar_rect,
                appbar_rect=appbar_rect,
                shell_rect=shell_rect,
                taskbar_auto_hide_mode=mode,
                detected_auto_hide=bool(state.get("auto_hide", False)) if isinstance(state, dict) else False,
                unit=self._u(1.0),
            )
            if not preliminary.auto_hide:
                return False
            interaction = (
                not preliminary.probe_taskbar.isNull()
                and self._taskbar_autohide_interaction_keeps_visible(screen, preliminary.probe_taskbar)
            )
            plan = build_taskbar_autohide_hidden_plan(
                taskbar_rect=taskbar_rect,
                appbar_rect=appbar_rect,
                shell_rect=shell_rect,
                taskbar_auto_hide_mode=mode,
                detected_auto_hide=bool(state.get("auto_hide", False)) if isinstance(state, dict) else False,
                interaction_keeps_visible=interaction,
                unit=self._u(1.0),
            )
            return plan.hidden
        except Exception:
            return False

    def _taskbar_follow_rect_for_current_visibility(
        self,
        screen,
        taskbar_rect: QRectF,
        force_visible: bool = False,
    ) -> tuple[QRectF, bool]:
        taskbar = QRectF(taskbar_rect)
        self.taskbar_follow_base_rect = QRectF(taskbar)
        if screen is None or taskbar.isNull():
            return taskbar, False
        if bool(force_visible) or self._taskbar_follow_force_visible_active():
            return taskbar, False
        if not self._taskbar_auto_hide_enabled_for_screen(screen):
            return taskbar, False
        try:
            state = self._windows_taskbar_appbar_state_for_screen(screen)
            if isinstance(state, dict):
                state = dict(state)
                mode = str(getattr(self, "taskbar_auto_hide_mode", "auto") or "auto").strip().lower()
                if mode == "enabled":
                    state["auto_hide"] = True
                elif mode == "disabled":
                    state["auto_hide"] = False
            else:
                state = {}
            appbar_rect = QRectF(state.get("rect", QRectF())) if isinstance(state, dict) else QRectF()
            shell_rect = self._windows_taskbar_rect_for_screen(screen)
            base_probe = appbar_rect if not appbar_rect.isNull() else taskbar
            probe = select_taskbar_autohide_probe_rect(base_probe, appbar_rect, shell_rect)
            interaction = (
                not probe.isNull()
                and self._taskbar_autohide_interaction_keeps_visible(screen, probe)
            )
            hidden_plan = build_taskbar_autohide_hidden_plan(
                taskbar_rect=base_probe,
                appbar_rect=appbar_rect,
                shell_rect=shell_rect,
                taskbar_auto_hide_mode=str(getattr(self, "taskbar_auto_hide_mode", "auto") or "auto"),
                detected_auto_hide=bool(state.get("auto_hide", False)) if isinstance(state, dict) else False,
                interaction_keeps_visible=interaction,
                unit=self._u(1.0),
            )
            hidden_autohide = bool(hidden_plan.hidden)
            if shell_rect is None or QRectF(shell_rect).isNull():
                hidden_autohide = self._taskbar_autohide_hidden_for_screen(screen, base_probe)
            try:
                screen_geo = QRectF(screen.geometry())
            except Exception:
                screen_geo = QRectF()
            follow_plan = build_taskbar_current_visibility_follow_plan(
                taskbar,
                screen_geo,
                auto_hide_enabled=True,
                appbar_rect=appbar_rect,
                shell_rect=shell_rect,
                interaction_keeps_visible=interaction,
                hidden_autohide=hidden_autohide,
            )
            self.taskbar_follow_base_rect = QRectF(follow_plan.base_rect)
            return follow_plan.follow_rect, follow_plan.hidden_autohide
        except Exception:
            if self._taskbar_autohide_hidden_for_screen(screen, taskbar):
                return self._hidden_autohide_follow_rect(screen, taskbar), True
            return taskbar, False

    def _taskbar_bottom_boundary_extension_for_screen(self, screen, taskbar_rect: QRectF) -> float:
        taskbar = QRectF(taskbar_rect)
        if screen is None or taskbar.isNull():
            return 0.0
        try:
            geo = QRectF(screen.geometry())
        except Exception:
            return 0.0
        return taskbar_bottom_boundary_extension(
            taskbar,
            geo,
            auto_hide_enabled=self._taskbar_auto_hide_enabled_for_screen(screen),
        )

    def _taskbar_follow_rect_for_visibility(self, screen, taskbar_rect: QRectF, force_visible: bool = False) -> QRectF:
        taskbar = QRectF(taskbar_rect)
        if screen is None or taskbar.isNull():
            return taskbar
        try:
            screen_geo = QRectF(screen.geometry())
        except Exception:
            screen_geo = QRectF()
        plan = build_taskbar_visibility_follow_plan(
            taskbar,
            screen_geo,
            force_visible=force_visible,
            force_visible_active=self._taskbar_follow_force_visible_active(),
            hidden_autohide=self._taskbar_autohide_hidden_for_screen(screen, taskbar),
        )
        return plan.follow_rect

    def _standalone_bottom_boundary_extension_for_screen(self, screen) -> float:
        if screen is None:
            return 0.0
        try:
            taskbar = self._taskbar_rect_for_screen(screen)
            fallback = self._taskbar_hidden_fallback_rect_for_screen(screen) if taskbar is None or taskbar.isNull() else QRectF()
            return standalone_bottom_boundary_extension(
                taskbar,
                screen.geometry(),
                fallback_rect=fallback,
                auto_hide_enabled=self._taskbar_auto_hide_enabled_for_screen(screen),
            )
        except Exception:
            return 0.0

    def _windows_taskbar_child_rects(self, taskbar_hwnd: int) -> list[tuple[str, QRectF]]:
        if sys.platform != "win32" or not taskbar_hwnd:
            return []
        try:
            user32 = ctypes.windll.user32
            children: list[tuple[str, QRectF]] = []
            enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

            def enum_proc(hwnd, _lparam):
                class_name = self._window_class_name(int(hwnd))
                rect = self._window_rect_for_hwnd(int(hwnd))
                if class_name and rect is not None and not rect.isNull():
                    children.append((class_name, rect))
                return True

            user32.EnumChildWindows(wintypes.HWND(int(taskbar_hwnd)), enum_proc_type(enum_proc), 0)
            return children
        except Exception:
            return []

    def _windows_taskbar_task_area_rect_for_screen(self, screen, taskbar_rect: QRectF) -> QRectF | None:
        hwnd = self._windows_taskbar_hwnd_for_screen(screen)
        if not hwnd:
            return None
        taskbar = QRectF(taskbar_rect)
        if taskbar.isNull():
            return None
        return select_windows_taskbar_task_area_rect(
            taskbar,
            self._windows_taskbar_child_rects(hwnd),
            unit=self._u(1.0),
        )

    def _fallback_taskbar_task_area_rect(self, taskbar_rect: QRectF) -> QRectF:
        return fallback_taskbar_task_area_rect(taskbar_rect, unit=self._u(1.0))

    def _taskbar_task_area_rect_for_screen(self, screen, taskbar_rect: QRectF) -> QRectF:
        detected = self._windows_taskbar_task_area_rect_for_screen(screen, taskbar_rect)
        return select_taskbar_task_area_rect(taskbar_rect, detected, unit=self._u(1.0))

    def _taskbar_occupied_rects_for_screen(self, screen, taskbar_rect: QRectF) -> list[QRectF]:
        taskbar = QRectF(taskbar_rect)
        if taskbar.isNull() or screen is None or not hasattr(screen, "geometry"):
            return []
        hwnd = self._windows_taskbar_hwnd_for_screen(screen)
        if not hwnd:
            return []
        task_area = self._windows_taskbar_task_area_rect_for_screen(screen, taskbar)
        return select_windows_taskbar_occupied_rects(
            taskbar,
            self._windows_taskbar_child_rects(hwnd),
            task_area_rect=task_area,
            unit=self._u(1.0),
        )

    def _taskbar_rect_for_screen(self, screen) -> QRectF | None:
        if screen is None:
            return None
        snapshot = self._taskbar_windows_snapshot_for_screen(screen)
        appbar_state = snapshot.get("appbar_state", {})
        if isinstance(appbar_state, dict):
            appbar_state = dict(appbar_state)
            mode = str(getattr(self, "taskbar_auto_hide_mode", "auto") or "auto").strip().lower()
            if mode == "enabled":
                appbar_state["auto_hide"] = True
            elif mode == "disabled":
                appbar_state["auto_hide"] = False
        appbar_rect = QRectF(appbar_state.get("rect", QRectF())) if isinstance(appbar_state, dict) else QRectF()
        appbar_edge = str(appbar_state.get("edge", "") or "") if isinstance(appbar_state, dict) else ""
        try:
            screen_geo = QRectF(screen.geometry())
        except Exception:
            screen_geo = QRectF()
        try:
            available_geo = QRectF(screen.availableGeometry())
        except Exception:
            available_geo = QRectF()
        return resolve_taskbar_rect_for_screen(
            screen_geometry=screen_geo,
            available_geometry=available_geo,
            shell_rect=snapshot.get("shell_rect", None),
            appbar_auto_hide=bool(appbar_state.get("auto_hide", False)) if isinstance(appbar_state, dict) else False,
            appbar_rect=appbar_rect,
            appbar_edge=appbar_edge,
            taskbar_auto_hide_mode=str(getattr(self, "taskbar_auto_hide_mode", "auto") or "auto"),
            previous_taskbar_rect=QRectF(getattr(self, "last_taskbar_rect", QRectF())),
            unit=self._u(1.0),
        )

    def _taskbar_available_slot_rects(self, task_area: QRectF, occupied_rects: list[QRectF]) -> list[QRectF]:
        return available_taskbar_slot_rects(task_area, occupied_rects, min_size=self._u(72.0))

    def _taskbar_side_slot_rects(self, task_area: QRectF, available_slots: list[QRectF]) -> list[QRectF]:
        return side_taskbar_slot_rects(
            task_area,
            available_slots,
            min_size=self._u(72.0),
            max_center_gap=self._u(96.0),
        )

    def _taskbar_drop_zone_rect(self, taskbar_rect: QRectF, desired_width: float | None = None) -> QRectF:
        return positioning_taskbar_drop_zone_rect(taskbar_rect, desired_width=desired_width, unit=self._u(1.0))

    def _taskbar_drop_zones_for_rect(self, screen, taskbar_rect: QRectF, desired_width: float | None = None) -> list[QRectF]:
        taskbar = QRectF(taskbar_rect)
        if taskbar.isNull():
            return []
        try:
            task_area = self._taskbar_task_area_rect_for_screen(screen, taskbar)
        except Exception:
            task_area = QRectF()
        base = task_area if not task_area.isNull() else taskbar
        try:
            occupied = self._taskbar_occupied_rects_for_screen(screen, taskbar)
        except Exception:
            occupied = []
        return build_taskbar_drop_zones(
            taskbar,
            base,
            occupied,
            desired_width=desired_width,
            unit=self._u(1.0),
        )

    def _taskbar_drop_zones_for_screen(self, screen, desired_width: float | None = None) -> list[QRectF]:
        taskbar = self._taskbar_rect_for_screen(screen)
        if taskbar is None or taskbar.isNull():
            return []
        return self._taskbar_drop_zones_for_rect(screen, taskbar, desired_width=desired_width)

    def _select_taskbar_drop_zone(self, zones: list[QRectF], global_pos: QPoint | QPointF | None) -> QRectF:
        return positioning_select_taskbar_drop_zone(zones, global_pos)

    def _taskbar_position_ratio_for_center(self, track_rect: QRectF, main_width: float, center_x: float) -> float:
        return position_ratio_for_center(track_rect, main_width, center_x)

    def _taskbar_center_for_position_ratio(self, track_rect: QRectF, main_width: float, ratio: float) -> float:
        return center_for_position_ratio(track_rect, main_width, ratio)

    def _taskbar_left_handle_rect(self, main_rect: QRectF) -> QRectF:
        return positioning_taskbar_left_handle_rect(main_rect, unit=self._u(1.0))

    def _taskbar_left_handle_frame_rect(self, main_rect: QRectF) -> QRectF:
        return taskbar_left_handle_frame_rect(main_rect, unit=self._u(1.0))

    def _taskbar_left_handle_visible(self) -> bool:
        try:
            now = float(time.monotonic())
        except Exception:
            now = 0.0
        return positioning_taskbar_left_handle_visible(
            taskbar_compact_active=self._taskbar_compact_active(),
            expanded=bool(getattr(self, "expanded", False)),
            now=now,
            visible_until=float(getattr(self, "taskbar_left_handle_visible_until", 0.0) or 0.0),
            dragging=bool(getattr(self, "dragging", False)),
            drag_press_key=str(getattr(self, "drag_press_key", "") or ""),
            hovered_key=str(getattr(self, "hovered_key", "") or ""),
        )

    def _taskbar_left_handle_visibility_progress(self, now: float | None = None) -> float:
        current = time.monotonic() if now is None else float(now)
        return positioning_taskbar_left_handle_visibility_progress(
            taskbar_compact_active=self._taskbar_compact_active(),
            expanded=bool(getattr(self, "expanded", False)),
            now=float(current),
            visible_until=float(getattr(self, "taskbar_left_handle_visible_until", 0.0) or 0.0),
            dragging=bool(getattr(self, "dragging", False)),
            drag_press_key=str(getattr(self, "drag_press_key", "") or ""),
            hovered_key=str(getattr(self, "hovered_key", "") or ""),
        )

    def _taskbar_left_handle_slide_progress(self, now: float | None = None) -> float:
        return self._taskbar_left_handle_visibility_progress(now=now)

    def _extend_taskbar_left_handle_visibility(self, now: float | None = None):
        if not self._taskbar_compact_active():
            return
        now = time.monotonic() if now is None else float(now)
        self.taskbar_left_handle_visible_until = extend_taskbar_left_handle_visible_until(
            float(getattr(self, "taskbar_left_handle_visible_until", 0.0) or 0.0),
            now,
        )

    def _taskbar_manual_width_for_left_edge(self, main_rect: QRectF, track_rect: QRectF, left_x: float) -> float:
        return taskbar_manual_width_for_left_edge(main_rect, track_rect, left_x, unit=self._u(1.0))

    def _taskbar_drop_zone_for_screen(self, screen, desired_width: float | None = None) -> QRectF:
        selected = getattr(self, "taskbar_selected_drop_zone", QRectF())
        if selected is not None and not selected.isNull():
            return QRectF(selected)
        zones = self._taskbar_drop_zones_for_screen(screen, desired_width=desired_width)
        return QRectF(zones[0]) if zones else QRectF()

    def _taskbar_drag_track_rect_for_screen(self, screen, taskbar_rect: QRectF) -> QRectF:
        return select_taskbar_drag_track_rect(taskbar_rect)

    def _taskbar_track_rect_for_screen(self, screen, taskbar_rect: QRectF) -> QRectF:
        taskbar = QRectF(taskbar_rect)
        task_area = self._taskbar_task_area_rect_for_screen(screen, taskbar)
        return select_taskbar_track_rect(taskbar_rect, task_area)

    def _taskbar_hidden_fallback_rect_for_screen(self, screen, edge: str = "", appbar_rect: QRectF | None = None) -> QRectF:
        if screen is None:
            return QRectF()
        try:
            screen_geo = QRectF(screen.geometry())
        except Exception:
            return QRectF()
        return positioning_taskbar_hidden_fallback_rect(
            screen_geo,
            previous_taskbar_rect=QRectF(getattr(self, "last_taskbar_rect", QRectF())),
            edge=edge,
            appbar_rect=appbar_rect,
            unit=self._u(1.0),
        )

    def _taskbar_intended_zone_rect(self, center_x: float, center_y: float, main_w: float, main_h: float) -> QRectF:
        return intended_zone_rect(center_x, center_y, main_w, main_h)

    def _taskbar_follow_refresh_due(self, now: float) -> bool:
        try:
            last = float(getattr(self, "taskbar_follow_last_at", 0.0) or 0.0)
        except (TypeError, ValueError):
            last = 0.0
        interval = float(getattr(self, "TASKBAR_FOLLOW_REFRESH_INTERVAL", 0.28))
        return positioning_taskbar_follow_refresh_due(now=now, last_refresh_at=last, interval=interval)

    def _taskbar_follow_force_visible_active(self, now: float | None = None) -> bool:
        try:
            until = float(getattr(self, "taskbar_follow_force_visible_until", 0.0) or 0.0)
        except (TypeError, ValueError):
            until = 0.0
        if until <= 0.0:
            return False
        current = time.monotonic() if now is None else float(now)
        return positioning_taskbar_follow_force_visible_active(now=current, force_visible_until=until)

    def _mark_taskbar_follow_force_visible(self, duration: float = 0.16):
        try:
            current = float(time.monotonic())
        except Exception:
            current = 0.0
        try:
            existing = float(getattr(self, "taskbar_follow_force_visible_until", 0.0) or 0.0)
        except (TypeError, ValueError):
            existing = 0.0
        self.taskbar_follow_force_visible_until = max(existing, current + max(0.0, float(duration)))
        self.taskbar_follow_last_at = 0.0

    def _taskbar_follow_rect_key(self, rect: QRectF) -> tuple[int, int, int, int]:
        return positioning_taskbar_follow_rect_key(rect)

    def _taskbar_follow_geometry_key(
        self,
        screen,
        taskbar_rect: QRectF,
        track_rect: QRectF,
        zone_rect: QRectF,
        window_pos: QPoint,
        main_top: float,
        direction: int,
    ) -> tuple[object, ...]:
        return positioning_taskbar_follow_geometry_key(
            TaskbarFollowTarget(
                screen=screen,
                visible_taskbar=QRectF(taskbar_rect),
                follow_taskbar=QRectF(taskbar_rect),
                track_rect=QRectF(track_rect),
                zone_rect=QRectF(zone_rect),
                window_pos=QPoint(window_pos),
                main_kind=self._main_kind(),
                stack_width=0.0,
                main_width=0.0,
                main_height=0.0,
                main_top=float(main_top),
                direction=int(direction),
                position_ratio=0.5,
                hidden_autohide=False,
                auto_hide_follow=False,
                partial_shell_follow=False,
            ),
            window_width=float(self.width()),
            window_height=float(self.height()),
            location_mode=str(getattr(self, "location_mode", "free") or "free"),
            expanded=bool(getattr(self, "expanded", False)),
            main_kind=self._main_kind(),
        )

    def _move_taskbar_window_if_needed(self, new_pos: QPoint) -> bool:
        target = QPoint(new_pos)
        def close_enough(x: int, y: int) -> bool:
            return abs(int(x) - int(target.x())) <= 1 and abs(int(y) - int(target.y())) <= 1

        current = None
        try:
            current = QPoint(self.pos())
        except Exception:
            current = None
        if current is not None and close_enough(current.x(), current.y()):
            return False
        saved = getattr(self, "saved_window_pos", None)
        if current is None and isinstance(saved, (list, tuple)) and len(saved) >= 2:
            try:
                if close_enough(int(saved[0]), int(saved[1])):
                    return False
            except (TypeError, ValueError):
                pass
        self.move(target)
        return True

    def _stop_taskbar_position_animation(self):
        for attr in ("taskbar_dock_animation", "taskbar_follow_animation"):
            animation = getattr(self, attr, None)
            if animation is None:
                continue
            try:
                animation.stop()
            except Exception:
                pass
            setattr(self, attr, None)

    def _taskbar_follow_animation_duration_ms(self, start_pos: QPoint, target_pos: QPoint) -> int:
        try:
            dx = float(target_pos.x() - start_pos.x())
            dy = float(target_pos.y() - start_pos.y())
        except Exception:
            dx = 0.0
            dy = 0.0
        distance = math.hypot(dx, dy)
        base_seconds = 0.056 + min(0.026, distance / 900.0 * 0.046)
        try:
            scaled = self._animation_duration(base_seconds, minimum=0.045)
        except Exception:
            scaled = base_seconds
        return int(round(max(45.0, min(86.0, scaled * 1000.0))))

    def _animate_taskbar_follow_to_pos(self, new_pos: QPoint) -> bool:
        target = QPoint(new_pos)
        try:
            start = QPoint(self.pos())
        except Exception:
            return self._move_taskbar_window_if_needed(target)
        if abs(start.x() - target.x()) <= 1 and abs(start.y() - target.y()) <= 1:
            return False
        try:
            if not self.isVisible():
                return self._move_taskbar_window_if_needed(target)
            existing = getattr(self, "taskbar_follow_animation", None)
            if existing is not None:
                try:
                    current = existing.currentValue()
                    if isinstance(current, QPoint):
                        start = QPoint(current)
                except Exception:
                    pass
                try:
                    existing.stop()
                except Exception:
                    pass
            animation = QPropertyAnimation(self, b"pos", self)
            animation.setStartValue(start)
            animation.setEndValue(target)
            animation.setDuration(self._taskbar_follow_animation_duration_ms(start, target))
            animation.setEasingCurve(QEasingCurve.Type.OutQuad)
            self.taskbar_follow_animation = animation

            def clear_animation():
                if getattr(self, "taskbar_follow_animation", None) is animation:
                    self.taskbar_follow_animation = None

            animation.finished.connect(clear_animation)
            animation.start()
            return True
        except Exception:
            return self._move_taskbar_window_if_needed(target)

    def _taskbar_dock_animation_duration_ms(self) -> int:
        try:
            scaled = self._animation_duration(0.14, minimum=0.045)
        except Exception:
            scaled = 0.14
        return int(round(max(45.0, min(80.0, scaled * 1000.0))))

    def _animate_taskbar_dock_to_pos(self, new_pos: QPoint) -> bool:
        target = QPoint(new_pos)
        try:
            start = QPoint(self.pos())
        except Exception:
            return self._move_taskbar_window_if_needed(target)
        if abs(start.x() - target.x()) <= 1 and abs(start.y() - target.y()) <= 1:
            return False
        try:
            if not self.isVisible():
                return self._move_taskbar_window_if_needed(target)
            existing = getattr(self, "taskbar_dock_animation", None)
            if existing is not None:
                try:
                    existing.stop()
                except Exception:
                    pass
            animation = QPropertyAnimation(self, b"pos", self)
            animation.setStartValue(start)
            animation.setEndValue(target)
            animation.setDuration(self._taskbar_dock_animation_duration_ms())
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            self.taskbar_dock_animation = animation

            def clear_animation():
                if getattr(self, "taskbar_dock_animation", None) is animation:
                    self.taskbar_dock_animation = None

            animation.finished.connect(clear_animation)
            animation.start()
            return True
        except Exception:
            return self._move_taskbar_window_if_needed(target)

    def _set_taskbar_follow_hidden(self, hidden: bool):
        hidden = bool(hidden)
        if hidden:
            if not bool(getattr(self, "taskbar_hidden_by_follow", False)):
                try:
                    self.hide()
                except Exception:
                    pass
            self.taskbar_hidden_by_follow = True
            return
        if bool(getattr(self, "taskbar_hidden_by_follow", False)):
            self.taskbar_hidden_by_follow = False
            if bool(getattr(self, "_music_surface_active", False)):
                return
            try:
                self.show()
            except Exception:
                pass
            return
        self.taskbar_hidden_by_follow = False

    def _taskbar_follow_target_for_screen(
        self,
        screen,
        visible_taskbar: QRectF,
        *,
        target_center_x: float | None = None,
        position_ratio: float | None = None,
        force_visible: bool = False,
        use_current_visibility: bool = True,
    ) -> TaskbarFollowTarget:
        taskbar = QRectF(visible_taskbar)
        if taskbar.isNull():
            return TaskbarFollowTarget(
                screen=screen,
                visible_taskbar=QRectF(),
                follow_taskbar=QRectF(),
                track_rect=QRectF(),
                zone_rect=QRectF(),
                window_pos=QPoint(),
                main_kind=self._main_kind(),
                stack_width=0.0,
                main_width=0.0,
                main_height=0.0,
                main_top=0.0,
                direction=1,
                position_ratio=0.5,
                hidden_autohide=False,
                auto_hide_follow=False,
                partial_shell_follow=False,
            )
        if use_current_visibility:
            follow_taskbar, hidden_autohide = self._taskbar_follow_rect_for_current_visibility(
                screen,
                taskbar,
                force_visible=force_visible,
            )
        else:
            follow_taskbar = self._taskbar_follow_rect_for_visibility(screen, taskbar, force_visible=force_visible)
            hidden_autohide = (
                bool(self._taskbar_auto_hide_enabled_for_screen(screen))
                and self._taskbar_follow_rect_key(follow_taskbar) != self._taskbar_follow_rect_key(taskbar)
            )
        base_taskbar = QRectF(getattr(self, "taskbar_follow_base_rect", QRectF()))
        visible = base_taskbar if not base_taskbar.isNull() else taskbar
        track = self._taskbar_drag_track_rect_for_screen(screen, visible)
        if track.isNull():
            track = QRectF(visible)
        try:
            screen_geo = QRectF(screen.geometry())
        except Exception:
            screen_geo = QRectF()
        auto_hide_follow = bool(self._taskbar_auto_hide_enabled_for_screen(screen)) and not bool(force_visible)
        direction = self._taskbar_stack_direction_for_rect(follow_taskbar, screen_geo)
        main_kind = self._main_kind()
        stack_w = self._stack_target_width(main_kind)
        main_w, main_h = self._main_size_for_taskbar_anchor(main_kind, stack_w, visible)
        main_top = self._main_top_for_direction(main_kind, main_h, direction)
        visible_follow_lift = 0.0
        if auto_hide_follow and not hidden_autohide:
            visible_follow_lift = self._taskbar_autohide_visible_follow_lift(follow_taskbar, screen_geo)
        bottom_extension = self._taskbar_bottom_boundary_extension_for_screen(screen, visible)
        return build_taskbar_follow_target(
            screen=screen,
            visible_taskbar=visible,
            follow_taskbar=follow_taskbar,
            track_rect=track,
            screen_geometry=screen_geo,
            window_width=float(self.width()),
            main_kind=main_kind,
            stack_width=float(stack_w),
            main_width=float(main_w),
            main_height=float(main_h),
            main_top=float(main_top),
            direction=int(direction),
            auto_hide_follow=bool(auto_hide_follow),
            hidden_autohide=bool(hidden_autohide),
            visible_follow_lift=float(visible_follow_lift),
            bottom_extension=float(bottom_extension),
            current_position_ratio=float(getattr(self, "taskbar_position_ratio", 0.5) or 0.5),
            target_center_x=target_center_x,
            position_ratio=position_ratio,
        )

    def _taskbar_follow_geometry_key_for_target(self, target: TaskbarFollowTarget) -> tuple[object, ...]:
        return positioning_taskbar_follow_geometry_key(
            target,
            window_width=float(self.width()),
            window_height=float(self.height()),
            location_mode=str(getattr(self, "location_mode", "free") or "free"),
            expanded=bool(getattr(self, "expanded", False)),
            main_kind=self._main_kind(),
        )

    def _apply_taskbar_follow_target(self, target: TaskbarFollowTarget, *, animate: bool = False, dock: bool = False):
        self.last_taskbar_rect = QRectF(target.visible_taskbar)
        self.stack_direction = int(target.direction)
        self.taskbar_position_ratio = float(target.position_ratio)
        self.taskbar_selected_drop_zone = QRectF(target.zone_rect)
        if animate and dock:
            moved = self._animate_taskbar_dock_to_pos(target.window_pos)
        elif animate:
            moved = self._animate_taskbar_follow_to_pos(target.window_pos)
        else:
            moved = self._move_taskbar_window_if_needed(target.window_pos)
        self._force_super_topmost()
        self.save_last_position(target.window_pos)
        return moved

    def _refresh_taskbar_follow_position(self) -> bool:
        if not self._taskbar_compact_active():
            return False
        now = time.monotonic()
        probe = self.mapToGlobal(QPoint(max(1, self.width() // 2), max(1, self.height())))
        screen = QGuiApplication.screenAt(probe) or QGuiApplication.primaryScreen()
        if screen is None:
            return False
        taskbar = self._taskbar_rect_for_screen(screen)
        if taskbar is None or taskbar.isNull():
            fallback = self._taskbar_hidden_fallback_rect_for_screen(screen)
            if fallback.isNull():
                return False
            taskbar = fallback
        visible_taskbar = QRectF(taskbar)
        force_visible = self._taskbar_follow_force_visible_active(now)
        self._set_taskbar_follow_hidden(False)
        target = self._taskbar_follow_target_for_screen(
            screen,
            visible_taskbar,
            force_visible=force_visible,
            use_current_visibility=True,
        )
        geometry_key = self._taskbar_follow_geometry_key_for_target(target)
        stable_geometry = geometry_key == getattr(self, "taskbar_follow_last_key", "")
        previous_hidden = bool(getattr(self, "taskbar_follow_last_hidden", False))
        try:
            material_last = float(getattr(self, "taskbar_follow_material_last_at", 0.0) or 0.0)
        except (TypeError, ValueError):
            material_last = 0.0
        material_interval = float(getattr(self, "TASKBAR_FOLLOW_MATERIAL_REFRESH_INTERVAL", 0.50))
        follow_plan = build_taskbar_follow_refresh_plan(
            target,
            stable_geometry=stable_geometry,
            previous_hidden=previous_hidden,
            force_visible=force_visible,
            taskbar_autohide_hidden=self._taskbar_autohide_hidden_for_screen(screen, target.visible_taskbar),
            material_elapsed=now - material_last,
            material_interval=material_interval,
        )
        if not follow_plan.valid:
            return False
        self._apply_taskbar_follow_target(target, animate=follow_plan.animate_follow, dock=False)
        self.taskbar_follow_last_at = now
        self.taskbar_follow_last_key = geometry_key
        self.taskbar_follow_last_hidden = bool(target.hidden_autohide)
        if follow_plan.refresh_material:
            self._update_taskbar_material_for_screen(screen, target.visible_taskbar)
            self.taskbar_follow_material_last_at = now
        return True

    def _hide_taskbar_drop_overlay(self):
        overlay = getattr(self, "taskbar_drop_overlay", None)
        if overlay is not None:
            try:
                overlay.hide()
            except Exception:
                pass

    def _clear_taskbar_drop_state(self):
        self.taskbar_selected_drop_zone = QRectF()
        self.taskbar_attach_hot_zone = QRectF()
        self.taskbar_attach_hot_zone_host_rect = QRectF()
        self.taskbar_attach_hot_zone_active = False
        self.taskbar_drop_zones = []

    def _clear_taskbar_preview_runtime(self):
        self.taskbar_preview_visible = False
        self._clear_taskbar_drop_state()
        self._hide_taskbar_drop_overlay()

    def _sync_taskbar_drop_overlay(self):
        if not bool(getattr(self, "taskbar_preview_visible", False)) or not self._taskbar_allowed():
            self._hide_taskbar_drop_overlay()
            return
        hot_zone = QRectF(getattr(self, "taskbar_attach_hot_zone", QRectF()))
        if hot_zone.isNull():
            self._hide_taskbar_drop_overlay()
            return
        host_rect = QRectF(getattr(self, "taskbar_attach_hot_zone_host_rect", QRectF()))
        if host_rect.isNull():
            host_rect = self._taskbar_attach_hot_zone_host_rect(hot_zone)
        if host_rect.isNull():
            self._hide_taskbar_drop_overlay()
            return
        overlay = getattr(self, "taskbar_drop_overlay", None)
        if overlay is None:
            overlay = TaskbarDropOverlay()
            self.taskbar_drop_overlay = overlay
        overlay.show_zones(host_rect, [hot_zone], self._u(14.0))

    def _set_taskbar_presence(self, inside: bool, notify: bool = True):
        plan = build_taskbar_presence_plan(
            inside,
            current_location_mode=str(getattr(self, "location_mode", "free") or "free"),
        )
        self.taskbar_preview_visible = plan.taskbar_preview_visible
        if plan.sync_taskbar_drop_overlay:
            self._sync_taskbar_drop_overlay()
            return
        if plan.clear_taskbar_selected_drop_zone:
            self.taskbar_selected_drop_zone = QRectF()
        if plan.clear_taskbar_attach_hot_zone:
            self.taskbar_attach_hot_zone = QRectF()
            self.taskbar_attach_hot_zone_host_rect = QRectF()
            self.taskbar_attach_hot_zone_active = False
        if plan.clear_taskbar_drop_zones:
            self.taskbar_drop_zones = []
        if plan.hide_taskbar_drop_overlay:
            self._hide_taskbar_drop_overlay()
        if plan.set_location_mode:
            self.set_location_mode(plan.set_location_mode, notify=notify)

    def _update_taskbar_preview_for_drag(self, global_pos: QPoint):
        _ratio, screen = self._taskbar_drag_ratio(global_pos)
        taskbar = self._taskbar_rect_for_screen(screen) if screen is not None else None
        hot_zone = QRectF()
        host_rect = QRectF()
        main_rect = QRectF()
        taskbar_allowed = bool(screen is not None and self._taskbar_allowed())
        prompt_allowed = False
        if screen is not None and taskbar_allowed:
            prompt_allowed = self._taskbar_auto_hide_enabled_for_screen(screen)
            if prompt_allowed:
                hot_zone = self._taskbar_attach_hot_zone_for_screen(screen)
                try:
                    host_rect = QRectF(screen.geometry())
                except Exception:
                    host_rect = self._taskbar_attach_hot_zone_host_rect(hot_zone)
            if taskbar is None or taskbar.isNull():
                taskbar = self._taskbar_hidden_fallback_rect_for_screen(screen)
            main_rect = self._current_main_target_global_rect()
            if main_rect.isNull():
                main_rect = self._current_main_global_rect()
        taskbar_rect = QRectF(taskbar) if taskbar is not None else QRectF()
        plan = build_taskbar_preview_drag_plan(
            taskbar_allowed=taskbar_allowed,
            prompt_allowed=prompt_allowed,
            hot_zone=hot_zone,
            hot_zone_host_rect=host_rect,
            taskbar_rect=taskbar_rect,
            main_rect=main_rect,
        )
        if plan.needs_drop_zones and screen is not None:
            zones = self._taskbar_drop_zones_for_rect(screen, taskbar_rect, desired_width=self._u(420.0))
            selected = self._select_taskbar_drop_zone(zones, main_rect.center())
            plan = build_taskbar_preview_drag_plan(
                taskbar_allowed=taskbar_allowed,
                prompt_allowed=prompt_allowed,
                hot_zone=hot_zone,
                hot_zone_host_rect=host_rect,
                taskbar_rect=taskbar_rect,
                main_rect=main_rect,
                drop_zones=zones,
                selected_drop_zone=selected,
            )
        if plan.active:
            self.last_taskbar_rect = QRectF(plan.last_taskbar_rect)
            if plan.update_taskbar_material:
                self._update_taskbar_material_for_screen(screen, taskbar_rect)
        self.taskbar_drop_zones = list(plan.taskbar_drop_zones)
        self.taskbar_selected_drop_zone = QRectF(plan.taskbar_selected_drop_zone)
        self.taskbar_attach_hot_zone = QRectF(plan.taskbar_attach_hot_zone)
        self.taskbar_attach_hot_zone_host_rect = QRectF(plan.taskbar_attach_hot_zone_host_rect)
        self.taskbar_attach_hot_zone_active = bool(plan.taskbar_attach_hot_zone_active)
        self._set_taskbar_presence(bool(plan.prompt_visible), notify=False)

    def _accept_taskbar_preview_drop(self) -> bool:
        plan = build_taskbar_preview_drop_acceptance_plan(
            taskbar_allowed=self._taskbar_allowed(),
            taskbar_preview_visible=bool(getattr(self, "taskbar_preview_visible", False)),
            taskbar_attach_hot_zone_active=bool(getattr(self, "taskbar_attach_hot_zone_active", False)),
        )
        if not plan.accepted:
            return False
        self.taskbar_dock_animation_requested = bool(plan.request_taskbar_dock_animation)
        if plan.set_location_mode:
            self.set_location_mode(plan.set_location_mode, notify=False)
        try:
            self._dock_to_taskbar_task_area()
        except Exception:
            pass
        finally:
            self.taskbar_dock_animation_requested = False
            if plan.clear_taskbar_attach_hot_zone:
                self.taskbar_attach_hot_zone = QRectF()
                self.taskbar_attach_hot_zone_host_rect = QRectF()
                self.taskbar_attach_hot_zone_active = False
            if plan.hide_taskbar_drop_overlay:
                self._hide_taskbar_drop_overlay()
        return True

    def _taskbar_stack_direction_for_rect(self, taskbar_rect: QRectF, screen_geo) -> int:
        return positioning_taskbar_stack_direction_for_rect(taskbar_rect, screen_geo)

    def _dock_to_taskbar_task_area(
        self,
        target_center_x: float | None = None,
        target_screen=None,
        target_taskbar_rect: QRectF | None = None,
    ) -> bool:
        probe = self.mapToGlobal(QPoint(max(1, self.width() // 2), max(1, self.height())))
        screen = target_screen or QGuiApplication.screenAt(probe) or QGuiApplication.primaryScreen()
        if screen is None:
            return False
        taskbar = QRectF(target_taskbar_rect) if target_taskbar_rect is not None else self._taskbar_rect_for_screen(screen)
        if taskbar is None or taskbar.isNull():
            taskbar = self._taskbar_hidden_fallback_rect_for_screen(screen)
        plan = build_taskbar_dock_plan(
            screen_available=screen is not None,
            taskbar_rect=QRectF(taskbar) if taskbar is not None else QRectF(),
            probe_x=float(probe.x()),
            target_center_x=target_center_x,
            dock_animation_requested=bool(getattr(self, "taskbar_dock_animation_requested", False)),
        )
        if not plan.valid:
            return False
        if plan.mark_follow_force_visible:
            self._mark_taskbar_follow_force_visible()
        target = self._taskbar_follow_target_for_screen(
            screen,
            plan.visible_taskbar,
            target_center_x=plan.target_center_x,
            force_visible=plan.force_visible,
            use_current_visibility=plan.use_current_visibility,
        )
        if target.track_rect.isNull():
            return False
        if plan.refresh_material:
            self._update_taskbar_material_for_screen(screen, plan.visible_taskbar)
        if plan.consume_dock_animation_request:
            self.taskbar_dock_animation_requested = False
        self._apply_taskbar_follow_target(target, animate=plan.animate, dock=plan.dock)
        return True

    def set_finder_mode(self, enabled: bool, notify: bool = True):
        self.set_location_mode("finder" if enabled else "free", notify=notify)

    def _notify_settings_changed(self):
        if self.settings_changed_callback:
            self.settings_changed_callback(self.settings_snapshot())

    @Slot(str, object)
    def on_music_cache_cleared(self, kind: str, result=None):
        """Invalidate GUI caches after the provider's background cleanup."""
        if result is not None and (not isinstance(result, dict) or not result.get("refresh_requested")):
            return
        if kind == "artwork":
            # Preserve a softened preview, but force freshly returned bytes
            # through decoding even if they equal the old cached download.
            self.album_art_signature = ""
            self.album_art_source = "thumbnail" if not self.album_art.isNull() else ""
            self.album_art_lookup_state = "pending"
            self._scaled_album_art_cache.clear()
            surface = getattr(self, "music_fullscreen_overlay", None)
            if surface is not None:
                surface.invalidate_artwork_cache()
        elif kind == "lyrics":
            self._clear_lyric_metrics_cache()
        else:
            return
        self.last_media_refresh_at = -999.0
        self.update()

    def provider_diagnostics(self) -> dict[str, object]:
        provider_diagnostics = {}
        try:
            provider_diag = getattr(self.data_provider, "diagnostics", None)
            if callable(provider_diag):
                provider_diagnostics.update(provider_diag())
        except Exception as exc:
            provider_diagnostics["provider_error"] = str(exc)
        image = getattr(self, "album_art", QImage())
        surface = getattr(self, "music_fullscreen_overlay", None)
        provider_diagnostics["artwork_handoff"] = {
            "source": str(getattr(self, "album_art_source", "") or ""),
            "lookup_state": str(getattr(self, "album_art_lookup_state", "") or ""),
            "decoded_width": image.width(),
            "decoded_height": image.height(),
            "bytes_signature": str(getattr(self, "album_art_signature", "") or ""),
            "display_cache_key": image.cacheKey(),
            "surface_waiting": bool(getattr(surface, "_art_loading_waiting", False)),
            "surface_preview_key": getattr(surface, "_art_loading_preview_key", None),
        }
        now = time.monotonic()
        diagnostics = self._build_island_runtime_diagnostics(
            now,
            provider_diagnostics=provider_diagnostics,
        )
        self._record_runtime_diagnostics_trace_sample(
            now,
            diagnostics.get("runtime_diagnostics_snapshot"),
        )
        diagnostics["runtime_diagnostics_trace"] = self._runtime_diagnostics_trace_summary()
        diagnostics["runtime_verification_report"] = build_runtime_verification_report(diagnostics).as_dict()
        return diagnostics

    def diagnostics_export_payload(self, *, source: str = "island") -> dict[str, object]:
        generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        return build_diagnostics_export_payload(
            self.provider_diagnostics(),
            generated_at=generated_at,
            app_name=APP_NAME,
            app_version=APP_VERSION,
            source=source,
        )

    def export_runtime_diagnostics(self, path: str | Path | None = None) -> str:
        payload = self.diagnostics_export_payload(source="island")
        target = Path(path) if path is not None else default_diagnostics_export_path(
            ROOT_DIR / "logs",
            generated_at=str(payload.get("generated_at") or ""),
            app_name=APP_NAME,
        )
        return str(write_diagnostics_export_file(payload, target))

    def _build_island_runtime_diagnostics(
        self,
        now: float,
        *,
        provider_diagnostics: dict[str, object] | None = None,
    ) -> dict[str, object]:
        lyric_diagnostics = self._lyric_timeline_diagnostics(now)
        return build_island_provider_diagnostics(
            provider_diagnostics=provider_diagnostics or {},
            visible=VisibleMediaDiagnostics(
                title=getattr(self, "track_title", ""),
                artist=getattr(self, "artist", ""),
                track_id=getattr(self, "media_track_id", ""),
                source_app=getattr(self, "media_source_app", ""),
                position_source=getattr(self, "media_position_source", ""),
                position_reason=getattr(self, "media_position_plan_reason", ""),
                position_confidence=getattr(self, "media_position_confidence", 0.0),
                position_interactive=bool(getattr(self, "media_position_interactive", False)),
                source_foreground=bool(getattr(self, "media_source_foreground", False)),
                mydockfinder_running=bool(getattr(self, "mydockfinder_running", False)),
                mydockfinder_diagnostics=dict(getattr(self, "mydockfinder_diagnostics", {}) or {}),
            ),
            visual_context=self._visual_runtime_diagnostics(),
            lyric_diagnostics=lyric_diagnostics,
            visual_frame_diagnostics=self._visual_frame_diagnostics_summary(),
            visual_paint_section_diagnostics=self._visual_paint_section_diagnostics_summary(),
            music_reactive_input_diagnostics=self._music_reactive_input_diagnostics(now),
            music_wave_cadence_diagnostics=self._music_wave_cadence_diagnostics(now),
        )

    def _visual_runtime_diagnostics(self) -> VisualRuntimeDiagnostics:
        try:
            effective_style = self._effective_music_ambience_style()
        except Exception:
            effective_style = str(getattr(self, "music_ambience_style", "sea_level") or "sea_level")
        try:
            taskbar_compact = self._taskbar_compact_active()
        except Exception:
            taskbar_compact = False
        return VisualRuntimeDiagnostics(
            ambience_style=str(getattr(self, "music_ambience_style", "sea_level") or "sea_level"),
            effective_ambience_style=str(effective_style or "sea_level"),
            lyric_flow_style=str(getattr(self, "lyric_flow_style", "wave_stream") or "wave_stream"),
            expanded=bool(getattr(self, "expanded", False)),
            location_mode=str(getattr(self, "location_mode", "finder" if getattr(self, "finder_mode", False) else "free") or "free"),
            taskbar_compact_active=bool(taskbar_compact),
            reactive_background_enabled=bool(getattr(self, "music_reactive_background_enabled", False)),
            reactive_background_intensity=int(getattr(self, "music_reactive_background_intensity", 55) or 0),
            animation_fps=int(getattr(self, "animation_fps", 60) or 0),
            lyric_animation_fps=int(getattr(self, "lyric_animation_fps", 120) or 0),
            animation_speed=float(getattr(self, "animation_speed", 1.0) or 0.0),
            compact_duotone_balance=int(getattr(self, "compact_duotone_balance", 50) or 0),
            expanded_duotone_balance=int(getattr(self, "expanded_duotone_balance", 50) or 0),
            diffraction_frequency=float(getattr(self, "diffraction_frequency", 0.30) or 0.0),
            yaoguang_color_source=str(getattr(self, "yaoguang_color_source", "custom") or "custom"),
            yaoguang_album_inverse_enabled=bool(getattr(self, "yaoguang_album_inverse_enabled", False)),
            yaoguang_blur_strength=int(getattr(self, "yaoguang_blur_strength", 58) or 0),
            yaoguang_height_mode=str(getattr(self, "yaoguang_height_mode", "regular") or "regular"),
        )

    def _lyric_timeline_diagnostics(self, now: float) -> dict[str, object]:
        estimated_position = self._estimated_media_position(now) if hasattr(self, "media_position_sync_at") else 0.0
        try:
            diagnostics = lyric_timeline_diagnostics(
                timed_lyrics=getattr(self, "timed_lyrics", ()) or (),
                lyrics=getattr(self, "lyrics", ()) or (),
                lyric_index=int(getattr(self, "lyric_index", 0)),
                estimated_position_seconds=estimated_position,
                lyric_offset_seconds=self._effective_lyric_offset_seconds() if hasattr(self, "lyric_offset_seconds") else 0.0,
                changed_elapsed_seconds=now - getattr(self, "lyric_changed_at", now),
                previous_phase_index=int(getattr(self, "lyric_last_phase_index", -1)),
                previous_phase_progress=float(getattr(self, "lyric_last_phase_progress", 0.0)),
                previous_phase_at=float(getattr(self, "lyric_last_phase_at", now)),
                now=now,
                media_is_playing=bool(getattr(self, "media_is_playing", False)),
                transition_kind=getattr(self, "lyric_transition_kind", ""),
            )
            result = diagnostics.as_dict()
            clock = getattr(self, "_island_lyric_source_clock", None)
            pending_at = getattr(clock, "pending_clock_at", None)
            result["source_clock"] = {
                "observed_seconds": getattr(self, "media_position_observed_seconds", None),
                "observed_at": getattr(self, "media_position_sampled_at", 0.0),
                "validated_seconds": getattr(clock, "last_source_position", None),
                "pending_seconds": getattr(clock, "pending_clock_position", None),
                "pending_age_seconds": max(0.0, now - pending_at) if pending_at is not None else None,
            }
            return result
        except Exception as exc:
            return {
                "estimated_position_seconds": estimated_position,
                "lyric_offset_seconds": getattr(self, "lyric_offset_seconds", 0.0),
                "visible_lyric_index": int(getattr(self, "lyric_index", 0)),
                "visible_current_lyric": "",
                "visible_lyrics_count": len(getattr(self, "lyrics", ()) or ()),
                "phase_progress": 0.0,
                "phase_span": 0.0,
                "phase_source": "",
                "error": str(exc),
            }

    def _music_reactive_input_diagnostics(self, now: float | None = None) -> dict[str, object]:
        runtime = self._audio_reactive_runtime_for_current_settings()
        input_diagnostics = getattr(runtime, "input_diagnostics", None)
        if callable(input_diagnostics):
            try:
                return dict(input_diagnostics(now=now))
            except Exception as exc:
                return {"error": str(exc)}
        return {}

    def _music_wave_cadence_diagnostics(self, now: float) -> dict[str, object]:
        return presentation_music_wave_cadence_diagnostics(
            now=now,
            first_updated_at=getattr(self, "_music_wave_first_updated_at", 0.0),
            last_updated_at=getattr(self, "_wave_values_updated_at", 0.0),
            update_count=getattr(self, "_music_wave_update_count", 0),
            visual_frame_diagnostics=self._visual_frame_diagnostics_summary(),
        ).as_dict()

    def mouseMoveEvent(self, event):
        now = time.monotonic()
        move_interval = 0.012 if getattr(self, "finder_mode", False) else 0.025
        if now - getattr(self, "last_pointer_move_at", 0.0) < move_interval and not self.drag_candidate:
            return
        self.last_pointer_move_at = now
        self.pointer_pos = event.position()
        if self.scene_suppressed:
            self.update()
            return
        if self.drag_candidate and event.buttons() & Qt.MouseButton.LeftButton:
            self._update_drag(event)
            self.update()
            return
        key = self._hit_test(event.position())
        if key in {"music", "plugin", "message", "taskbar:left-handle"} or str(key or "").startswith("message:"):
            self._extend_taskbar_left_handle_visibility(now)
        in_finder_frame = self._point_in_finder_frame(event.position())
        if key or in_finder_frame:
            self.left_stack_at = 0.0
        elif self.expanded and not self.left_stack_at:
            self.left_stack_at = time.monotonic()
        if self.expanded and key == "plugin":
            self._focus_plugin_at(event.position().x())
        if self._should_close_plugin_panel_for_hover(key):
            self.plugin_panel_open = False
            self._pulse_stack()
        if self._main_kind() == IslandKind.MESSAGE and key and self._is_message_hit(key) and not self.expanded:
            self._set_expanded(True)
            self.hover_armed_key = ""
            self._pulse_stack()
        if key != self.hovered_key:
            self.hovered_key = key
            self._set_island_music_control_hover(key, now)
            self.hover_started_at = now
            self.hover_armed_key = key if key and key != "music:open_surface" and not self.expanded and not (self._main_kind() == IslandKind.MESSAGE and self._is_message_hit(key)) else ""
            if key:
                self.hover_bounce_until = now + 1.0
            self.update()

    def _open_message_source(self, msg_key: str):
        msg = next((m for m in self.messages if self._message_key(m) == msg_key), None)
        if not msg:
            return
        self._focus_app_window_async(msg.app)
        self.opened_source_app = f"Opened {msg.app} · {msg.sender}"
        self.opened_source_until = time.monotonic() + 1.4
        self._clear_messages_for_app(msg.app)
        if self.expanded:
            self._set_expanded(False)

    def _message_key_from_hit(self, key: str) -> str:
        return presentation_message_key_from_hit(key)

    def _is_message_hit(self, key: str) -> bool:
        return presentation_message_hit_key(key)

    def _should_close_plugin_panel_for_hover(self, key: str) -> bool:
        if not getattr(self, "plugin_panel_open", False):
            return False
        if not self.expanded or self._main_kind() != IslandKind.MUSIC:
            return False
        return key == "music" or key.startswith("music:")

    def _clear_messages_for_app(self, app_name: str):
        target = str(app_name or "").strip().casefold()
        if not target:
            return
        self.messages = [message for message in self.messages if str(getattr(message, "app", "") or "").strip().casefold() != target]
        self.barrage_preview_messages = [
            message for message in self.barrage_preview_messages if str(getattr(message, "app", "") or "").strip().casefold() != target
        ]
        self.update()

    def _focus_app_window(self, app_name: str) -> bool:
        if sys.platform != "win32" or not app_name:
            return False
        try:
            user32 = ctypes.windll.user32
            matches: list[int] = []
            needle = app_name.lower()
            needles = [needle]
            aliases = {
                "网易云音乐": ["cloudmusic", "orpheus", "netease"],
                "qq 音乐": ["qqmusic", "qq 音乐"],
                "spotify": ["spotify"],
                "微信": ["wechat", "weixin", "微信"],
                "telegram": ["telegram"],
                "飞书": ["feishu", "lark", "飞书"],
                "钉钉": ["dingtalk", "钉钉"],
            }
            for key, values in aliases.items():
                if key.lower() in needle or needle in key.lower():
                    needles.extend(values)
            enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def enum_proc(hwnd, _lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                info = buffer.value.lower()
                try:
                    class_buf = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, class_buf, 256)
                    info += " " + class_buf.value.lower()
                except Exception:
                    pass
                if any(item and item in info for item in needles):
                    matches.append(hwnd)
                    return False
                return True

            user32.EnumWindows(enum_proc_type(enum_proc), 0)
            if not matches:
                return False
            hwnd = matches[0]
            user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
            return True
        except Exception:
            return False

    def _run_background(self, name: str, callback):
        def runner():
            try:
                callback()
            except Exception:
                pass

        thread = threading.Thread(target=runner, name=f"Island{name}", daemon=True)
        thread.start()

    def _focus_app_window_async(self, app_name: str):
        if app_name:
            self._run_background("FocusApp", lambda: self._focus_app_window(app_name))

    def _send_playback_command_async(self, command: str):
        now = time.monotonic()
        pending = None
        if command == "toggle_play_pause":
            pending = self._begin_music_playback_toggle(now)
        else:
            self._pending_music_playback_toggle = None
            if command in {"previous", "next"}:
                self._media_strong_tracking_until = now + 10.0

        def send():
            try:
                succeeded = bool(self.data_provider.send_playback_command(command))
            except Exception:
                succeeded = False
            if pending is not None:
                # Only a plain result is published by the worker. Qt state
                # and the current command generation are reconciled on the UI.
                pending["command_succeeded"] = succeeded

        self._run_background("MediaCommand", send)

    def _begin_music_playback_toggle(self, now: float) -> dict[str, object]:
        playing = bool(getattr(self, "media_is_playing", False))
        position = self._estimated_media_position(now)
        self.media_position_seconds = position
        self.media_position_sync_at = float(now)
        self.media_is_playing = not playing
        pending = {
            "track_id": str(getattr(self, "media_track_id", "") or ""),
            "started_at": float(now),
            "playing": not playing,
            "previous_playing": playing,
            "command_succeeded": None,
        }
        self._pending_music_playback_toggle = pending
        self._media_strong_tracking_until = max(
            float(getattr(self, "_media_strong_tracking_until", 0.0)), float(now) + 2.5,
        )
        self.last_media_refresh_at = 0.0
        return pending

    def _reconcile_music_playback_toggle(
        self, track_id: str, playing: bool, position: float,
        sampled_at: float, now: float, *, track_changed: bool = False,
    ) -> tuple[bool, float]:
        pending = getattr(self, "_pending_music_playback_toggle", None)
        if not isinstance(pending, dict):
            return playing, position
        if track_changed or str(pending["track_id"]) != str(track_id):
            self._pending_music_playback_toggle = None
            return playing, position
        if pending.get("command_succeeded") is False:
            # A failed command restores the actual source state on the next
            # fast refresh instead of leaving the optimistic icon or clock.
            self._pending_music_playback_toggle = None
            return playing, position
        started_at = float(pending["started_at"])
        expected = bool(pending["playing"])
        if now - started_at >= 1.8:
            self._pending_music_playback_toggle = None
            return playing, position
        if sampled_at > started_at and playing == expected:
            self._pending_music_playback_toggle = None
            return playing, position
        # An in-flight pre-click read, or a cached provider frame while the
        # player settles, must not rewind a pause or count paused time on resume.
        return expected, self._estimated_media_position(now)

    def _show_music_fullscreen(self, *, fullscreen=None, origin_geometry=None, origin_snapshot=None):
        overlay = getattr(self, "music_fullscreen_overlay", None)
        if bool(getattr(self, "_music_surface_active", False)):
            self._focus_active_music_surface()
            return
        if overlay is None:
            overlay = MusicSurfaceWindow(self)
            self.music_fullscreen_overlay = overlay
        self._island_visible_before_music_surface = bool(self.isVisible())
        external_origin = origin_geometry is not None
        origin_geometry = QRect(origin_geometry) if external_origin else self._current_main_global_rect().toAlignedRect()
        origin_snapshot = QImage(origin_snapshot) if origin_snapshot is not None else QImage()
        if not external_origin and origin_geometry.isValid() and self.isVisible():
            local_origin = self.mapFromGlobal(origin_geometry.topLeft())
            local_rect = QRect(local_origin, origin_geometry.size()).intersected(self.rect())
            if local_rect.isValid():
                origin_geometry = QRect(self.mapToGlobal(local_rect.topLeft()), local_rect.size())
                origin_snapshot = self.grab(local_rect).toImage()
            else:
                origin_geometry = QRect()
        self._music_surface_active = True
        try:
            self._release_super_topmost()
            self.hide()
            overlay.show_from_island(
                fullscreen=fullscreen,
                origin_geometry=origin_geometry,
                origin_snapshot=origin_snapshot,
            )
        except Exception:
            try:
                overlay.hide()
            except Exception:
                pass
            self._music_surface_active = False
            if bool(getattr(self, "_island_visible_before_music_surface", True)):
                self.show()
                self.raise_()
                self.activateWindow()

    def _focus_active_music_surface(self) -> bool:
        if not bool(getattr(self, "_music_surface_active", False)):
            return False
        overlay = getattr(self, "music_fullscreen_overlay", None)
        if overlay is not None and overlay.isVisible():
            try:
                minimized = bool(overlay.isMinimized())
            except Exception:
                minimized = False
            if minimized:
                try:
                    if bool(getattr(overlay, "_fullscreen", False)):
                        overlay.showFullScreen()
                    else:
                        overlay.showNormal()
                except Exception:
                    pass
            overlay.raise_()
            overlay.activateWindow()
        return True

    def _restore_island_from_music_surface(self):
        overlay = getattr(self, "music_fullscreen_overlay", None)
        if overlay is not None:
            try:
                overlay.hide()
            except Exception:
                pass
        self._music_surface_active = False
        if bool(getattr(self, "_island_visible_before_music_surface", True)):
            self._settle_island_return_motion(time.monotonic())
            self.show()
            self.raise_()
            self.activateWindow()

    def _settle_island_return_motion(self, now: float):
        """Present the destination already reached by the song-page morph."""
        self.hovered_key = ""
        self.hover_armed_key = ""
        self.hover_started_at = 0.0
        self.hover_bounce_until = 0.0
        self.stack_bounce_until = 0.0
        self.left_stack_at = 0.0
        self.pointer_pos = QPointF(-1.0, -1.0)
        self.last_frame_at = float(now)
        # The hidden QWidget does not paint, so its shell retains the old
        # dimensions and velocity even though the lyric row has advanced.
        # Compact ink is fitted to the live shell height; resuming that old
        # spring after the return morph turns overshoot into repeated integer
        # font-size jumps. Settle the destination once without resetting any
        # reading, source-clock or logical-line motion.
        self._sync_timed_lyrics(float(now))
        for visual in self._build_visuals():
            if visual.z_index == 10:
                self._apply_motion_snap_to_target(visual.key, visual.rect, 1.0)

    def _close_music_fullscreen(self):
        overlay = getattr(self, "music_fullscreen_overlay", None)
        return_to_island = getattr(overlay, "return_to_island", None)
        if bool(getattr(self, "_music_surface_active", False)) and callable(return_to_island):
            return_to_island()
        else:
            self._restore_island_from_music_surface()

    def _activate_key(self, key: str):
        # Explicit interaction takes ownership of the presentation immediately.
        self._island_dj_outro_until = 0.0
        if key.startswith("music:"):
            command = key.split(":", 1)[1]
            if command == "open_surface":
                self._show_music_fullscreen()
            elif command == "open_source":
                self._open_music_source()
            else:
                self.tasker_music_hover_auto_expand_suppressed = True
                self.hover_armed_key = ""
                self._send_playback_command_async(command)
                if command == "toggle_play_pause":
                    presses = getattr(self, "music_control_press_started_at", None)
                    if presses is None:
                        presses = {}
                        self.music_control_press_started_at = presses
                    presses[key] = time.monotonic()
                self.last_media_refresh_at = 0.0
        elif key.startswith("plugin-alert:"):
            plugin_id = key.split(":", 1)[1]
            index = self._plugin_index_for_id(plugin_id)
            if index >= 0:
                self.persistent_plugin = True
                self.enabled_plugin_count = max(self.enabled_plugin_count, index + 1)
                self._set_plugin_focus(index, immediate=False)
            self._set_expanded(True)
            self.plugin_panel_open = True
        elif key.startswith("plugin-action:"):
            self._activate_plugin_action(key.split(":", 1)[1])
        elif key == "plugin":
            self.plugin_panel_open = True
            if self.expanded:
                self._focus_plugin_at(self.pointer_pos.x(), immediate=True)
            else:
                self._set_expanded(True)
        elif key == "plugin-panel":
            if not self.expanded:
                self._set_expanded(True)
            self.plugin_panel_open = True
        elif self._is_message_hit(key):
            self._open_message_source(self._message_key_from_hit(key))
        elif key and not self.expanded:
            self._set_expanded(True)
        elif key and self.expanded:
                self._set_expanded(False)
        self._pulse_stack()

    def _hover_auto_activation_allowed(self, key: str) -> bool:
        key = str(key or "")
        if not key:
            return False
        if key.startswith("music:"):
            return False
        if key == "music" and bool(getattr(self, "tasker_music_hover_auto_expand_suppressed", False)):
            return False
        if key == "taskbar:left-handle":
            return False
        return True

    def _open_plugin_manager(self):
        samples = self._enabled_plugin_samples()
        if not samples:
            return
        index = max(0, min(self._active_plugin_focus(time.monotonic()), len(samples) - 1))
        label, detail, summary, _color, _icon, plugin_id, status, _alert_title, _alert_body, _alert_priority, _progress = self._plugin_tuple(samples[index])
        try:
            from local.plugin_manager_window import PluginManagerWindow
            existing = self.plugin_manager_windows.get(label)
            if existing is not None and existing.isVisible():
                existing.raise_()
                existing.activateWindow()
                return
            window = PluginManagerWindow(
                label,
                detail,
                summary,
                self,
                settings_getter=getattr(self, "settings_getter", None),
                settings_setter=getattr(self, "settings_setter", None),
                refresh_callback=self.refresh_plugin_data,
                diagnostics_getter=self.provider_diagnostics,
            )
            self.plugin_manager_windows[label] = window
            window.show()
            window.raise_()
            window.activateWindow()
        except Exception:
            self.opened_source_app = f"{label} 管理面板暂不可用"
            self.opened_source_until = time.monotonic() + 1.4

    def _set_plugin_setting(self, key: str, value):
        setter = getattr(self, "settings_setter", None)
        if callable(setter):
            setter(key, value)

    def _plugin_setting(self, key: str, default=None):
        getter = getattr(self, "settings_getter", None)
        if callable(getter):
            try:
                return getter(key, default)
            except Exception:
                return default
        return default

    def _format_timer_seconds(self, seconds: int) -> str:
        return presentation_format_timer_seconds(seconds)

    def _timer_paused_remaining_seconds(self) -> int:
        try:
            return max(0, int(getattr(self, "plugin_timer_paused_remaining_seconds", 0) or 0))
        except (TypeError, ValueError):
            return 0

    def _set_timer_paused_state(self, remaining_seconds: int, progress: float = 0.0):
        self.plugin_timer_paused_remaining_seconds = max(0, int(remaining_seconds))
        self.plugin_timer_paused_progress = max(0.0, min(1.0, float(progress or 0.0)))

    def _clear_timer_paused_state(self):
        self.plugin_timer_paused_remaining_seconds = 0
        self.plugin_timer_paused_progress = 0.0

    def _append_countdown_entry(self, minutes: int, started_at: datetime, end_at: datetime):
        self.plugin_countdown_entries = presentation_append_countdown_entry(
            getattr(self, "plugin_countdown_entries", None),
            minutes,
            started_at,
            end_at,
        )

    def _pause_latest_countdown_entry(self, remaining_seconds: int, progress: float):
        self.plugin_countdown_entries = presentation_pause_latest_countdown_entry(
            getattr(self, "plugin_countdown_entries", None),
            remaining_seconds,
            progress,
        )

    def _resume_latest_countdown_entry(self, remaining_seconds: int, started_at: datetime, end_at: datetime):
        self.plugin_countdown_entries = presentation_resume_latest_countdown_entry(
            getattr(self, "plugin_countdown_entries", None),
            remaining_seconds,
            started_at,
            end_at,
        )

    def _timer_setting_datetime(self, key: str, now: datetime) -> datetime | None:
        raw = str(self._plugin_setting(key, "") or "").strip()
        if not raw:
            return None
        try:
            value = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=now.tzinfo)
        return value.astimezone(now.tzinfo)

    def _timer_remaining_seconds_from_settings(self, now: datetime | None = None) -> int:
        now = now or datetime.now().astimezone()
        target = self._timer_setting_datetime("v2_timer_end_at", now)
        if target is None:
            return 0
        return max(0, int((target - now).total_seconds()))

    def _timer_progress_from_settings(self, now: datetime | None = None) -> float:
        now = now or datetime.now().astimezone()
        started = self._timer_setting_datetime("v2_timer_started_at", now)
        target = self._timer_setting_datetime("v2_timer_end_at", now)
        if started is None or target is None or started >= target:
            return 0.0
        total = max(1.0, (target - started).total_seconds())
        elapsed = max(0.0, (now - started).total_seconds())
        return max(0.0, min(1.0, elapsed / total))

    def _request_visual_update(self):
        try:
            self.update()
        except RuntimeError:
            pass

    def _clear_alarm_delete_mode(self):
        self.plugin_alarm_delete_index = None

    def _handle_alarm_row_long_press(self, command: str) -> bool:
        match = re.fullmatch(r"alarm-edit-(\d+)", str(command or "").strip().lower())
        if not match:
            return False
        rows = self._time_alarm_rows()
        index = int(match.group(1))
        if not (0 <= index < len(rows)):
            return False
        self.plugin_alarm_delete_index = index
        self._request_visual_update()
        return True

    def _maybe_trigger_alarm_row_long_press(self, now: float) -> bool:
        if not getattr(self, "drag_candidate", False) or getattr(self, "dragging", False):
            return False
        command = str(getattr(self, "drag_press_key", "") or "")
        if command.startswith("plugin-action:"):
            command = command.split(":", 1)[1]
        if not re.fullmatch(r"alarm-edit-\d+", command):
            return False
        if float(now) - float(getattr(self, "last_pointer_press_at", 0.0) or 0.0) < 0.48:
            return False
        if self._handle_alarm_row_long_press(command):
            self.drag_candidate = False
            self.drag_press_key = ""
            return True
        return False

    def _begin_time_module_animation(self, from_module: str, to_module: str, steps: int):
        if from_module == to_module:
            self.plugin_time_module_animation = {}
            return
        direction = 1 if int(steps or 0) >= 0 else -1
        self.plugin_time_module_animation = {
            "from": str(from_module or "countdown"),
            "to": str(to_module or "countdown"),
            "direction": direction,
            "started_at": time.monotonic(),
        }

    def _time_module_transition_offsets(self, visible: str) -> tuple[str, float, float] | None:
        animation = getattr(self, "plugin_time_module_animation", {})
        if not isinstance(animation, dict) or not animation:
            return None
        if str(animation.get("to") or "") != str(visible or ""):
            return None
        try:
            started_at = float(animation.get("started_at", 0.0) or 0.0)
            direction = 1 if int(animation.get("direction", 1) or 1) >= 0 else -1
        except (TypeError, ValueError):
            return None
        elapsed = max(0.0, time.monotonic() - started_at)
        duration = self._animation_duration(0.24)
        if elapsed >= duration:
            self.plugin_time_module_animation = {}
            return None
        progress = max(0.0, min(1.0, elapsed / duration))
        eased = 1.0 - pow(1.0 - progress, 3)
        from_offset = -direction * eased
        to_offset = direction * (1.0 - eased)
        return str(animation.get("from") or ""), from_offset, to_offset

    def _activate_plugin_action(self, command: str):
        if bool(getattr(self, "_runtime_extras_retired", False)):
            return
        command = str(command or "").strip().lower()
        if command.startswith("alarm-editor-") and command not in {"alarm-editor-save", "alarm-editor-cancel"}:
            result = presentation_handle_alarm_editor_action(
                self._time_alarm_editor_state(),
                command,
                boundary_year=self._alarm_year_boundary_year(),
            )
            if bool(result.get("handled", False)):
                self.plugin_alarm_editor = dict(result.get("editor") or {})
                if "focus" in result:
                    self.plugin_alarm_editor_focus = str(result.get("focus") or "")
                if bool(result.get("clear_input_buffers", False)):
                    self.plugin_alarm_editor_input_buffers = {}
                page_animation = result.get("page_animation")
                if isinstance(page_animation, dict):
                    self._begin_alarm_editor_page_animation(
                        str(page_animation.get("from") or "main"),
                        str(page_animation.get("to") or "main"),
                        int(page_animation.get("steps") or 1),
                    )
                spin_field = str(result.get("spin_field") or "")
                if spin_field:
                    self._begin_alarm_editor_spin(spin_field, int(result.get("spin_steps") or 1))
                self._request_visual_update()
                return
        if command.startswith("timer-"):
            now = datetime.now().astimezone()
            result = presentation_handle_timer_action(
                command,
                now=now,
                entries=getattr(self, "plugin_countdown_entries", None),
                paused_remaining=self._timer_paused_remaining_seconds(),
                settings_remaining=self._timer_remaining_seconds_from_settings(now),
                settings_progress=self._timer_progress_from_settings(now),
            )
            if bool(result.get("handled", False)):
                if "entries" in result:
                    self.plugin_countdown_entries = [dict(entry) for entry in result.get("entries") or [] if isinstance(entry, dict)]
                paused = result.get("paused")
                if isinstance(paused, dict):
                    self._set_timer_paused_state(
                        int(paused.get("remaining") or 0),
                        float(paused.get("progress") or 0.0),
                    )
                if bool(result.get("clear_paused", False)):
                    self._clear_timer_paused_state()
                settings = result.get("settings")
                if isinstance(settings, dict):
                    for key, value in settings.items():
                        self._set_plugin_setting(str(key), str(value))
                mode = str(result.get("mode") or "")
                if mode:
                    self.plugin_time_mode = mode
                if bool(result.get("refresh", False)):
                    self.refresh_plugin_data()
                return
        if command == "alarm-due-confirm":
            pending = self._time_alarm_due_pending()
            if pending:
                label = str(pending.get("label") or "闹钟").strip() or "闹钟"
                time_text = str(pending.get("time") or "").strip()
                self.plugin_alarm_due_acknowledged_key = str(pending.get("key") or f"{time_text}|{label}")
            self.plugin_alarm_due_pending = {}
            self._request_visual_update()
            return
        if command.startswith(("alarm-toggle-", "alarm-delete-")):
            result = presentation_handle_alarm_rows_action([dict(row) for row in self._time_alarm_rows()], command)
            if bool(result.get("handled", False)):
                if bool(result.get("changed", False)):
                    self._persist_alarm_rows(result.get("rows"))
                if bool(result.get("clear_delete_mode", False)):
                    self._clear_alarm_delete_mode()
                if bool(result.get("request_update", False)):
                    self._request_visual_update()
                return
        alarm_edit_match = re.fullmatch(r"alarm-edit-(\d+)", command)
        if alarm_edit_match:
            self._clear_alarm_delete_mode()
            rows = [dict(row) for row in self._time_alarm_rows()]
            index = int(alarm_edit_match.group(1))
            if 0 <= index < len(rows):
                self.plugin_time_mode = "alarm_editor"
                self.plugin_alarm_editor = self._alarm_editor_state_from_row(index, rows[index])
                self.plugin_alarm_editor_focus = "note"
                self._request_visual_update()
            return
        if command == "alarm-add-open":
            self.plugin_time_mode = "alarm_editor"
            self.plugin_alarm_editor = self._default_alarm_editor_state()
            self.plugin_alarm_editor_focus = "note"
            self._request_visual_update()
            return
        if command == "alarm-editor-cancel":
            self.plugin_time_mode = "dashboard"
            self.plugin_alarm_editor_focus = ""
            self._request_visual_update()
            return
        if command == "alarm-editor-save":
            editor = self._time_alarm_editor_state()
            rows = [dict(row) for row in self._time_alarm_rows()]
            rows = presentation_save_alarm_editor_rows(
                editor,
                rows,
                boundary_year=self._alarm_year_boundary_year(),
            )
            self._persist_alarm_rows(rows)
            self.plugin_time_mode = "dashboard"
            self.plugin_alarm_editor_focus = ""
            self._request_visual_update()
            return
        if command == "weather-refresh":
            self._set_plugin_setting("v2_weather_refresh_nonce", str(time.monotonic()))
            self.refresh_plugin_data()
            return
        if command == "agenda-open":
            self._open_plugin_manager()
            return
        if command == "timer-open":
            self._open_plugin_manager()

    def refresh_plugin_data(self):
        if bool(getattr(self, "_runtime_extras_retired", False)):
            return
        self.last_media_refresh_at = -999.0
        self._refresh_media_data(time.monotonic())
        self.update()

    def _plugin_tuple(self, plugin):
        return presentation_plugin_sample_tuple(plugin)

    def _normalize_empty_island_display_mode(self, mode: str) -> str:
        return presentation_normalize_empty_island_display_mode(mode)

    def _empty_island_display_mode(self) -> str:
        return self._normalize_empty_island_display_mode(getattr(self, "empty_island_display_mode", "visible_bar"))

    def _normalize_no_music_island_priority(self, priority: str) -> str:
        return presentation_normalize_no_music_island_priority(priority)

    def _no_music_island_priority(self) -> str:
        return self._normalize_no_music_island_priority(getattr(self, "no_music_island_priority", "plugin"))

    def _empty_island_can_display(self) -> bool:
        return presentation_empty_island_can_display(
            music_enabled=self._music_enabled_for_stack(),
            plugin_enabled=self._plugin_enabled(),
            no_music_priority=self._no_music_island_priority(),
        )

    def _empty_music_text_active(self) -> bool:
        return presentation_empty_island_text_active(
            display_mode=self._empty_island_display_mode(),
            music_enabled=self._music_enabled_for_stack(),
            plugin_enabled=self._plugin_enabled(),
            no_music_priority=self._no_music_island_priority(),
        )

    def _empty_time_mode_active(self) -> bool:
        return presentation_empty_island_time_active(
            display_mode=self._empty_island_display_mode(),
            music_enabled=self._music_enabled_for_stack(),
            plugin_enabled=self._plugin_enabled(),
            no_music_priority=self._no_music_island_priority(),
        )

    def _empty_text_display_text(self) -> str:
        return str(getattr(self, "no_lyrics_text", "暂无歌词") or "暂无歌词").strip() or "暂无歌词"

    def _empty_time_text(self) -> str:
        now = datetime.now().astimezone()
        return now.strftime("%H:%M:%S" if bool(getattr(self, "show_seconds", True)) else "%H:%M")

    def _enabled_plugin_ids(self) -> list[str]:
        if bool(getattr(self, "_runtime_extras_retired", False)):
            return []
        return presentation_enabled_plugin_ids(
            getattr(self, "enabled_plugin_ids", None),
            int(getattr(self, "enabled_plugin_count", 3) or 0),
        )

    def _enabled_plugin_slot_count(self) -> int:
        return len(self._enabled_plugin_ids())

    def _enabled_plugin_samples(self) -> list[tuple]:
        enabled = self._enabled_plugin_ids()
        if not enabled:
            self.enabled_plugin_count = 0
            return []
        samples = presentation_enabled_plugin_samples(getattr(self, "plugin_samples", []), enabled)
        self.enabled_plugin_count = len(samples) if samples else len(enabled)
        return samples

    def _plugin_index_for_id(self, plugin_id: str) -> int:
        return presentation_plugin_index_for_id(self._enabled_plugin_samples(), plugin_id)

    def _time_alert_side_suppressed(self, include_expanded: bool) -> bool:
        expanded = bool(getattr(self, "expanded", False))
        plugin_panel_open = bool(getattr(self, "plugin_panel_open", False))
        main_kind_is_plugin = False
        if not include_expanded and expanded and plugin_panel_open:
            main_kind_is_plugin = self._main_kind() == IslandKind.PLUGIN
        return presentation_time_alert_side_suppressed(
            include_expanded=include_expanded,
            expanded=expanded,
            plugin_panel_open=plugin_panel_open,
            main_kind_is_plugin=main_kind_is_plugin,
        )

    def _time_alarm_side_display_mode(self) -> str:
        value = str(getattr(self, "time_alarm_side_display", "note") or "note").strip().lower()
        setting = self._plugin_setting("v2_time_alarm_side_display", value)
        return presentation_time_alarm_side_display_mode(value, setting)

    def _time_alarm_side_segment_text(self, segment: dict[str, object]) -> str:
        return presentation_time_alarm_side_segment_text(segment, self._time_alarm_side_display_mode())

    def _time_alarm_due_pending(self) -> dict[str, object]:
        pending = getattr(self, "plugin_alarm_due_pending", {})
        return dict(pending) if isinstance(pending, dict) and pending else {}

    def _time_alarm_due_acknowledged_matches(self, time_text: str, label: str) -> bool:
        return presentation_time_alarm_due_acknowledged_matches(
            time_text,
            label,
            str(getattr(self, "plugin_alarm_due_acknowledged_key", "") or ""),
        )

    def _capture_time_alarm_due_pending(self, model: dict[str, object]) -> dict[str, object]:
        result = presentation_capture_time_alarm_due_pending(
            model,
            existing_pending=self._time_alarm_due_pending(),
            acknowledged_key=str(getattr(self, "plugin_alarm_due_acknowledged_key", "") or ""),
            captured_at=time.monotonic(),
        )
        if result.get("clear_acknowledged"):
            self.plugin_alarm_due_acknowledged_key = ""
        pending = dict(result.get("pending") or {})
        if result.get("set_pending"):
            self.plugin_alarm_due_pending = pending
        return pending

    def _time_side_alert_candidates(
        self,
        index: int,
        label: str,
        detail: str,
        summary: str,
        color,
        icon: str,
        plugin_id: str,
        status: str,
        alert_title: str,
        alert_body: str,
        alert_priority: int,
    ) -> list[tuple[int, int, int, str, str, str, object, str, str, str, str, int]]:
        return presentation_time_side_alert_candidates(
            index=index,
            label=label,
            detail=detail,
            summary=summary,
            color=color,
            icon=icon,
            plugin_id=plugin_id,
            status=status,
            alert_title=alert_title,
            alert_body=alert_body,
            alert_priority=alert_priority,
            pending_alarm=self._time_alarm_due_pending(),
            acknowledged_key=str(getattr(self, "plugin_alarm_due_acknowledged_key", "") or ""),
            compact_segments=self._time_compact_segments(detail, summary, status, alert_body),
            alarm_side_display_mode=self._time_alarm_side_display_mode(),
        )

    def _active_plugin_alerts(self, limit: int = 2, include_expanded: bool = False):
        if not self._plugin_enabled() or (self.expanded and not include_expanded):
            return []
        candidates = []
        for index, plugin in enumerate(self._enabled_plugin_samples()):
            label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority, _progress = self._plugin_tuple(plugin)
            is_time = str(plugin_id or "").strip().casefold() == "time" or str(label or "").strip().upper() == "TIME"
            if is_time and self._time_alert_side_suppressed(include_expanded):
                continue
            if is_time:
                self._capture_time_alarm_due_pending(
                    {
                        "label": label,
                        "detail": detail,
                        "summary": summary,
                        "status": status,
                        "alert_title": alert_title,
                        "alert_body": alert_body,
                    }
                )
                candidates.extend(self._time_side_alert_candidates(index, label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority))
                continue
            candidate = presentation_generic_plugin_alert_candidate(
                index=index,
                label=label,
                detail=detail,
                summary=summary,
                color=color,
                icon=icon,
                plugin_id=plugin_id,
                status=status,
                alert_title=alert_title,
                alert_body=alert_body,
                alert_priority=alert_priority,
            )
            if candidate is not None:
                candidates.append(candidate)
        return presentation_active_plugin_alerts(candidates, limit)

    def _active_plugin_alert(self):
        alerts = self._active_plugin_alerts(1)
        return alerts[0] if alerts else None

    def _taskbar_main_overlay_alert(self):
        return presentation_taskbar_main_overlay_alert(
            self._active_plugin_alerts(4, include_expanded=True),
            taskbar_compact_active=self._taskbar_compact_active(),
        )

    def _taskbar_main_overlay_rect(self, main_rect: QRectF, alert) -> QRectF:
        return QRectF(main_rect)

    def _open_music_source(self):
        app_name = str(getattr(self, "media_source_app", "") or "").strip()
        self._focus_app_window_async(app_name)
        self.opened_source_app = f"Opening {app_name or 'music app'}"
        self.opened_source_until = time.monotonic() + 1.4

    def _update_drag(self, event):
        global_pos = event.globalPosition().toPoint()
        delta = global_pos - self.drag_press_global
        entry_plan = build_drag_update_entry_plan(
            drag_candidate=bool(getattr(self, "drag_candidate", False)),
            dragging=bool(getattr(self, "dragging", False)),
            delta=delta,
            drag_threshold=float(getattr(self, "drag_threshold", 0.0) or 0.0),
            finder_mode=bool(getattr(self, "finder_mode", False)),
        )
        if entry_plan.action in {"ignore", "await_threshold"}:
            return
        if entry_plan.start_drag:
            self.dragging = True
        if entry_plan.collapse_expanded:
            self._collapse_expanded_for_drag()
        if entry_plan.route_finder_drag:
            if self._apply_finder_drag_action(global_pos, delta):
                return
        elif entry_plan.route_standard_drag:
            self._apply_standard_drag_action(global_pos, delta)
        if entry_plan.pulse_stack:
            self._pulse_stack()

    def _apply_finder_drag_action(self, global_pos: QPoint, delta: QPoint) -> bool:
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        if screen is None:
            return False
        geo = screen.geometry()
        plan = build_finder_drag_action_plan(
            delta_y=float(delta.y()),
            detach_threshold=max(self._u(22.0), 18.0),
        )
        if plan.detach_to_free:
            self._detach_from_finder_for_drag(global_pos)
            if plan.move_standalone_after_detach:
                self._move_standalone_drag(global_pos)
            if plan.pulse_stack_after_detach:
                self._pulse_stack()
            return True
        if plan.follow_finder:
            self.resize(geo.width(), self.height())
            self.move(geo.left(), geo.top())
            usable = max(1.0, float(geo.width()))
            ratio = (global_pos.x() - geo.left()) / usable
            self.stack_center_ratio = max(0.06, min(0.94, ratio))
            self._force_super_topmost()
        return False

    def _apply_standard_drag_action(self, global_pos: QPoint, delta: QPoint) -> None:
        location_mode = str(getattr(self, "location_mode", "free") or "free")
        drag_press_key = str(getattr(self, "drag_press_key", "") or "")
        if self._should_attach_to_finder(global_pos, delta):
            self._attach_to_finder_for_drag(global_pos)
            return
        left_handle_stays_on_track = (
            location_mode == "tasker"
            and drag_press_key == "taskbar:left-handle"
            and self._taskbar_drag_should_stay_on_track(global_pos)
        )
        should_detach_to_free = (
            location_mode == "tasker"
            and self._taskbar_drag_should_detach_to_free(global_pos, delta)
        )
        plan = build_taskbar_drag_action_plan(
            location_mode=location_mode,
            drag_press_key=drag_press_key,
            left_handle_stays_on_track=left_handle_stays_on_track,
            should_detach_to_free=should_detach_to_free,
        )
        if plan.resize_taskbar_left_handle:
            self._resize_taskbar_from_left_handle(global_pos)
        elif plan.move_taskbar_drag:
            self._move_taskbar_drag(global_pos)
        else:
            if plan.remember_motion_global_rects:
                try:
                    self._remember_motion_global_rects()
                except Exception:
                    pass
            if plan.leave_taskbar_presence:
                self._set_taskbar_presence(False, notify=False)
            elif plan.set_location_mode:
                self.set_location_mode(plan.set_location_mode, notify=False)
            if plan.move_standalone_drag:
                self._move_standalone_drag(global_pos)
            if plan.update_taskbar_preview:
                self._update_taskbar_preview_for_drag(global_pos)

    def _finder_attach_allowed(self) -> bool:
        return self._mydockfinder_available()

    def _should_attach_to_finder(self, global_pos: QPoint, delta: QPoint) -> bool:
        attach_allowed = self._finder_attach_allowed()
        delta_y = float(delta.y())
        if not attach_allowed or delta_y >= 0.0:
            return False
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        if not screen:
            return False
        plan = build_finder_attach_plan(
            attach_allowed=attach_allowed,
            global_pos=global_pos,
            delta_y=delta_y,
            screen_geometry=screen.geometry(),
            finder_bar_height=float(getattr(self.finder_material, "bar_height", 28.0)),
            unit=self._u(1.0),
        )
        return plan.should_attach

    def _attach_to_finder_for_drag(self, global_pos: QPoint):
        if not self._finder_attach_allowed():
            return
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        if not screen:
            return
        geo = screen.geometry()
        started_surface_transition = bool(self._begin_surface_transition("finder"))
        self._reset_location_runtime_state(clear_surface=not started_surface_transition)
        self.location_mode = "finder"
        self.finder_mode = True
        self.attach_to_real_finder = True
        self.taskbar_fusion_mode = "off"
        self.taskbar_preview_visible = False
        self._hide_taskbar_drop_overlay()
        self.finder_material = read_finder_material(self._mydockfinder_root_path(), force_screen_sample=True, include_center=True)
        self.finder_material_refreshed_at = time.monotonic()
        self.resize(geo.width(), self.height())
        self.move(geo.left(), geo.top())
        self.saved_window_pos = None
        self.stack_center_ratio = max(0.06, min(0.94, (global_pos.x() - geo.left()) / max(1.0, float(geo.width()))))
        self._force_super_topmost()
        self._notify_settings_changed()

    def _remember_motion_global_rects(self):
        origin = self.mapToGlobal(QPoint(0, 0))
        ox = float(origin.x())
        oy = float(origin.y())
        self.drag_detach_global_rects = {
            key: QRectF(motion.x + ox, motion.y + oy, motion.w, motion.h)
            for key, motion in self.motions.items()
            if motion.opacity > 0.025
        }

    def _restore_motion_global_rects_after_move(self):
        saved = getattr(self, "drag_detach_global_rects", {})
        if not saved:
            return
        origin = self.mapToGlobal(QPoint(0, 0))
        ox = float(origin.x())
        oy = float(origin.y())
        for key, global_rect in saved.items():
            motion = self.motions.get(key)
            if motion is None:
                continue
            motion.x = global_rect.left() - ox
            motion.y = global_rect.top() - oy
            motion.w = global_rect.width()
            motion.h = global_rect.height()
            motion.vx = motion.vy = motion.vw = motion.vh = 0.0
        self.drag_detach_global_rects = {}

    def _detach_from_finder_for_drag(self, global_pos: QPoint):
        self._remember_motion_global_rects()
        self.location_mode = "free"
        self.finder_mode = False
        self.attach_to_real_finder = False
        self._reset_location_runtime_state(clear_surface=True, clear_drag_detach=False)
        standalone_width = int(round(self._u(940)))
        if self.width() != standalone_width:
            self.resize(standalone_width, self.height())
        self._notify_settings_changed()

    def _move_standalone_drag(self, global_pos: QPoint):
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        screen_geo = screen.geometry() if screen else None
        main_kind = self._main_kind()
        target_stack_w = self._stack_target_width(main_kind)
        target_main_w, _target_main_h = self._main_size_for_kind(main_kind, target_stack_w)
        desired_center_x = float(global_pos.x()) + target_main_w / 2.0 - float(self.drag_press_main_offset.x())
        width_limit = self._standalone_width_limit_for_global_center(desired_center_x, screen_geo, target_stack_w)
        stack_w = standalone_stack_width_for_limit(target_stack_w, width_limit)
        main_w, main_h = self._main_size_for_kind(main_kind, stack_w)
        old_direction = getattr(self, "stack_direction", 1)
        old_main_top = self._main_top_for_direction(main_kind, main_h, old_direction)
        direction = self._standalone_drag_direction(global_pos)
        self.stack_direction = direction
        main_left = self.width() / 2.0 - main_w / 2.0
        main_top = self._main_top_for_direction(main_kind, main_h, direction)
        if direction != old_direction:
            local_delta_y = main_top - old_main_top
            for motion in self.motions.values():
                motion.y += local_delta_y
                motion.vy = 0.0
        new_x = int(round(global_pos.x() - main_left - self.drag_press_main_offset.x()))
        new_y = int(round(global_pos.y() - main_top - self.drag_press_main_offset.y()))
        if screen_geo:
            bottom_extension = self._standalone_bottom_boundary_extension_for_screen(screen)
            new_pos = self._clamp_standalone_window_pos(new_x, new_y, main_left, main_top, main_w, main_h, screen_geo, bottom_extension=bottom_extension)
        else:
            new_pos = QPoint(new_x, new_y)
        self.move(new_pos)
        self._restore_motion_global_rects_after_move()
        self.save_last_position(new_pos)

    def _collapse_expanded_for_drag(self) -> bool:
        if not bool(getattr(self, "expanded", False)):
            return False
        if str(getattr(self, "drag_press_key", "") or "") == "taskbar:left-handle":
            return False
        old_rect = QRectF(getattr(self, "drag_press_main_global_rect", QRectF()))
        old_offset = QPointF(getattr(self, "drag_press_main_offset", QPointF()))
        self._set_expanded(False)
        if old_rect.isNull() or old_rect.width() <= 1.0 or old_rect.height() <= 1.0:
            return True
        try:
            main_kind = self._main_kind()
            stack_w = self._stack_target_width(main_kind)
            main_w, main_h = self._main_size_for_kind(main_kind, stack_w)
        except Exception:
            main_w = old_rect.width()
            main_h = old_rect.height()
        if main_w <= 1.0 or main_h <= 1.0:
            return True
        ratio_x = max(0.0, min(1.0, float(old_offset.x()) / float(old_rect.width())))
        ratio_y = max(0.0, min(1.0, float(old_offset.y()) / float(old_rect.height())))
        self.drag_press_main_offset = QPointF(ratio_x * float(main_w), ratio_y * float(main_h))
        self.drag_press_main_global_rect = QRectF(old_rect.left(), old_rect.top(), float(main_w), float(main_h))
        return True

    def _taskbar_drag_reference_rect_for_screen(self, screen) -> QRectF:
        taskbar = self._taskbar_rect_for_screen(screen)
        if taskbar is None or taskbar.isNull():
            taskbar = QRectF()
        else:
            taskbar = QRectF(taskbar)
        try:
            screen_geo = QRectF(screen.geometry()) if screen is not None else QRectF()
        except Exception:
            screen_geo = QRectF()
        try:
            previous = QRectF(getattr(self, "last_taskbar_rect", QRectF()))
        except Exception:
            previous = QRectF()
        try:
            fallback = self._taskbar_hidden_fallback_rect_for_screen(screen)
        except Exception:
            fallback = QRectF()
        return select_taskbar_drag_reference_rect(
            taskbar_rect=taskbar,
            screen_geometry=screen_geo,
            previous_taskbar_rect=previous,
            fallback_rect=fallback,
        )

    def _taskbar_drag_should_stay_on_track(self, global_pos: QPoint) -> bool:
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        taskbar = self._taskbar_drag_reference_rect_for_screen(screen)
        return positioning_taskbar_drag_should_stay_on_track(taskbar, global_pos, unit=self._u(1.0))

    def _taskbar_drag_should_detach_to_free(self, global_pos: QPoint, delta: QPoint) -> bool:
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        taskbar = self._taskbar_drag_reference_rect_for_screen(screen)
        try:
            geo = QRectF(screen.geometry()) if screen is not None else QRectF()
        except Exception:
            geo = QRectF()
        return positioning_taskbar_drag_should_detach_to_free(
            taskbar,
            geo,
            delta,
            left_handle_drag=str(getattr(self, "drag_press_key", "") or "") == "taskbar:left-handle",
            unit=self._u(1.0),
        )

    def _apply_taskbar_drag_target(self, target: TaskbarDragTarget, screen):
        self.last_taskbar_rect = QRectF(target.taskbar_rect)
        self.stack_direction = int(target.direction)
        self.taskbar_position_ratio = float(target.position_ratio)
        if float(target.manual_width) > 0.0:
            self.taskbar_manual_width = float(target.manual_width)
        self.taskbar_selected_drop_zone = QRectF(target.zone_rect)
        self._move_taskbar_window_if_needed(QPoint(target.window_pos))
        self._force_super_topmost()
        self.save_last_position(QPoint(target.window_pos))
        self._update_taskbar_material_for_screen(screen, target.taskbar_rect)

    def _move_taskbar_drag(self, global_pos: QPoint):
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        taskbar = self._taskbar_drag_reference_rect_for_screen(screen)
        if taskbar.isNull():
            return
        track = self._taskbar_drag_track_rect_for_screen(screen, taskbar)
        if track.isNull():
            return
        direction = self._taskbar_stack_direction_for_rect(taskbar, screen.geometry())
        main_kind = self._main_kind()
        stack_w = self._stack_target_width(main_kind)
        main_w, main_h = self._main_size_for_taskbar_anchor(main_kind, stack_w, taskbar)
        main_top = self._main_top_for_direction(main_kind, main_h, direction)
        target = build_taskbar_drag_target(
            global_pos=global_pos,
            taskbar_rect=taskbar,
            track_rect=track,
            screen_geometry=screen.geometry(),
            window_width=float(self.width()),
            main_kind=main_kind,
            stack_width=stack_w,
            main_width=main_w,
            main_height=main_h,
            main_top=main_top,
            direction=direction,
            drag_press_main_offset=getattr(self, "drag_press_main_offset", QPointF()),
        )
        if target is None:
            return
        self._apply_taskbar_drag_target(target, screen)

    def _resize_taskbar_from_left_handle(self, global_pos: QPoint):
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        taskbar = self._taskbar_drag_reference_rect_for_screen(screen)
        if taskbar.isNull():
            return
        track = self._taskbar_drag_track_rect_for_screen(screen, taskbar)
        if track.isNull():
            return
        main_rect = QRectF(getattr(self, "drag_press_main_global_rect", QRectF()))
        if main_rect.isNull():
            main_rect = self._current_main_global_rect()
        if main_rect.isNull():
            return
        direction = self._taskbar_stack_direction_for_rect(taskbar, screen.geometry())
        main_kind = self._main_kind()
        stack_w = self._stack_target_width(main_kind)
        main_w, main_h = self._main_size_for_taskbar_anchor(main_kind, stack_w, taskbar)
        main_top = self._main_top_for_direction(main_kind, main_h, direction)
        target = build_taskbar_left_resize_target(
            global_pos=global_pos,
            main_rect=main_rect,
            taskbar_rect=taskbar,
            track_rect=track,
            screen_geometry=screen.geometry(),
            window_width=float(self.width()),
            main_kind=main_kind,
            stack_width=stack_w,
            main_width=main_w,
            main_height=main_h,
            main_top=main_top,
            direction=direction,
            unit=self._u(1.0),
        )
        if target is None:
            return
        self._apply_taskbar_drag_target(target, screen)

    def _clamp_taskbar_window_pos(
        self,
        new_pos: QPoint,
        main_top: float,
        main_w: float,
        main_h: float,
        screen_geo,
        bottom_extension: float = 0.0,
    ) -> QPoint:
        try:
            window_w = float(self.width())
        except Exception:
            window_w = float(main_w)
        return clamp_taskbar_window_pos(new_pos, main_top, main_w, main_h, screen_geo, window_w, bottom_extension)

    def _clamp_standalone_window_pos(
        self,
        new_x: float,
        new_y: float,
        main_left: float,
        main_top: float,
        main_w: float,
        main_h: float,
        screen_geo,
        bottom_extension: float = 0.0,
    ) -> QPoint:
        return clamp_standalone_window_pos(
            new_x,
            new_y,
            main_left,
            main_top,
            main_w,
            main_h,
            screen_geo,
            margin=self._u(6.0),
            bottom_extension=bottom_extension,
        )

    def _nudge_window_pos_outside_taskbar(
        self,
        new_pos: QPoint,
        taskbar_rect: QRectF,
        main_top: float,
        main_h: float,
        direction: int,
        gap: float = 0.0,
    ) -> QPoint:
        return nudge_window_pos_outside_taskbar(new_pos, taskbar_rect, main_top, main_h, direction, gap)

    def _standalone_release_direction_for_anchor(self, anchor_rect: QRectF, main_kind: IslandKind, main_h: float, screen_geo) -> int:
        expanded_down_height = None
        mode = str(getattr(self, "expand_direction", "auto") or "auto")
        should_measure_expanded_height = False
        if (
            mode.lower() == "auto"
            and bool(getattr(self, "auto_expand_bounds", True))
            and bool(getattr(self, "expanded", False))
            and screen_geo
        ):
            try:
                anchor = QRectF(anchor_rect)
                screen = QRectF(screen_geo)
                cutoff = float(screen.top()) + float(screen.height()) * (2.0 / 3.0)
                should_measure_expanded_height = not anchor.isNull() and anchor.center().y() < cutoff
            except Exception:
                should_measure_expanded_height = False
        if should_measure_expanded_height:
            expanded_down_height = self._expanded_stack_height(main_kind, main_h, include_popups=False)
        return standalone_release_direction_for_anchor(
            anchor_rect,
            screen_geo,
            expand_direction=mode,
            auto_expand_bounds=bool(getattr(self, "auto_expand_bounds", True)),
            expanded=bool(getattr(self, "expanded", False)),
            expanded_down_height=expanded_down_height,
            bottom_margin=self._u(14.0),
        )

    def _taskbar_above_gap(self, taskbar_rect: QRectF | None = None) -> float:
        return taskbar_above_gap(taskbar_rect)

    def _stick_taskbar_above_gap(self, taskbar_rect: QRectF | None = None, hidden_autohide: bool = False) -> float:
        return stick_taskbar_above_gap(taskbar_rect, hidden_autohide=hidden_autohide)

    def _taskbar_autohide_visible_follow_lift(self, taskbar_rect: QRectF, screen_geo) -> float:
        return taskbar_autohide_visible_follow_lift(taskbar_rect, screen_geo)

    def _bottom_taskbar_rect_for_screen(self, screen) -> QRectF:
        if screen is None:
            return QRectF()
        try:
            taskbar = self._taskbar_rect_for_screen(screen)
            geo = QRectF(screen.geometry())
            fallback = self._taskbar_hidden_fallback_rect_for_screen(screen) if taskbar is None or taskbar.isNull() else QRectF()
            return select_bottom_taskbar_rect(taskbar, geo, fallback)
        except Exception:
            return QRectF()

    def _should_snap_above_taskbar_for_release(
        self,
        anchor_rect: QRectF,
        release_global_pos: QPoint | QPointF | None,
        screen,
        taskbar_rect: QRectF,
    ) -> bool:
        try:
            screen_geo = screen.geometry() if screen is not None else None
        except Exception:
            screen_geo = None
        auto_hide = self._taskbar_auto_hide_enabled_for_screen(screen) if screen is not None else False
        return positioning_should_snap_above_taskbar_for_release(
            anchor_rect,
            release_global_pos,
            screen_geo,
            taskbar_rect,
            taskbar_allowed=self._taskbar_allowed(),
            auto_hide_enabled=auto_hide,
            min_threshold=self._u(24.0),
            max_threshold=self._u(96.0),
        )

    def _should_enter_taskbar_for_visible_taskbar_release(
        self,
        anchor_rect: QRectF,
        release_global_pos: QPoint | QPointF | None,
        screen,
        taskbar_rect: QRectF,
    ) -> bool:
        try:
            screen_geo = screen.geometry() if screen is not None else None
        except Exception:
            screen_geo = None
        auto_hide = self._taskbar_auto_hide_enabled_for_screen(screen) if screen is not None else False
        return positioning_should_enter_taskbar_for_visible_taskbar_release(
            anchor_rect,
            release_global_pos,
            screen_geo,
            taskbar_rect,
            taskbar_allowed=self._taskbar_allowed(),
            auto_hide_enabled=auto_hide,
            min_threshold=self._u(24.0),
            max_threshold=self._u(96.0),
        )

    def _tasker_release_should_detach_above_taskbar(self, release_global_pos: QPoint | QPointF | None) -> bool:
        point = standalone_release_probe_point(QRectF(), release_global_pos)
        if point is None:
            return False
        screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
        taskbar = self._bottom_taskbar_rect_for_screen(screen)
        try:
            screen_geo = screen.geometry() if screen is not None else None
        except Exception:
            screen_geo = None
        plan = build_tasker_release_detach_plan(
            point,
            screen_geo,
            taskbar,
            taskbar_compact_active=self._taskbar_compact_active(),
            min_slop=self._u(8.0),
            max_slop=self._u(18.0),
        )
        return bool(plan.should_detach)

    def _should_snap_top_notch_for_release(
        self,
        anchor_rect: QRectF,
        release_global_pos: QPoint | QPointF | None,
        screen,
    ) -> bool:
        try:
            screen_geo = screen.geometry() if screen is not None else None
        except Exception:
            screen_geo = None
        return positioning_should_snap_top_notch_for_release(
            anchor_rect,
            release_global_pos,
            screen_geo,
            mydockfinder_available=self._mydockfinder_available(),
            finder_mode=bool(getattr(self, "finder_mode", False)),
            min_threshold=self._u(18.0),
            max_threshold=self._u(84.0),
        )

    def _standalone_release_decision(
        self,
        anchor_rect: QRectF,
        release_global_pos: QPoint | QPointF | None,
        screen,
        taskbar_rect: QRectF,
    ) -> StandaloneReleaseDecision:
        try:
            screen_geo = screen.geometry() if screen is not None else None
        except Exception:
            screen_geo = None
        auto_hide = self._taskbar_auto_hide_enabled_for_screen(screen) if screen is not None else False
        return decide_standalone_release_action(
            anchor_rect,
            release_global_pos,
            screen_geo,
            taskbar_rect,
            taskbar_allowed=self._taskbar_allowed(),
            auto_hide_enabled=auto_hide,
            mydockfinder_available=self._mydockfinder_available(),
            finder_mode=bool(getattr(self, "finder_mode", False)),
            taskbar_compact_active=self._taskbar_compact_active(),
            current_location_mode=str(getattr(self, "location_mode", "free") or "free"),
            bottom_min_threshold=self._u(24.0),
            bottom_max_threshold=self._u(96.0),
            top_min_threshold=self._u(18.0),
            top_max_threshold=self._u(84.0),
        )

    def _standalone_top_notch_target_for_screen(self, anchor_rect: QRectF, screen) -> StandalonePlacementTarget | None:
        anchor = QRectF(anchor_rect)
        if self._mydockfinder_available() or screen is None or anchor.isNull():
            return None
        try:
            screen_geo = QRectF(screen.geometry())
        except Exception:
            return None
        if screen_geo.isNull():
            return None
        main_kind = self._main_kind()
        target_stack_w = self._stack_target_width(main_kind)
        width_limit = self._standalone_width_limit_for_global_center(anchor.center().x(), screen_geo, target_stack_w)
        stack_w = standalone_stack_width_for_limit(target_stack_w, width_limit)
        main_w, main_h = self._main_size_for_kind(main_kind, stack_w)
        main_top = self._main_top_for_direction(main_kind, main_h, 1)
        return build_standalone_top_notch_target(
            anchor_rect=anchor,
            screen_geometry=screen_geo,
            window_width=float(self.width()),
            main_kind=main_kind,
            stack_width=stack_w,
            main_width=main_w,
            main_height=main_h,
            main_top=main_top,
        )

    def _settle_standalone_top_notch(self, anchor_rect: QRectF, screen, save: bool = True) -> bool:
        target = self._standalone_top_notch_target_for_screen(anchor_rect, screen)
        if target is None:
            return False
        self.location_mode = "top"
        self.finder_mode = False
        self.attach_to_real_finder = False
        self.taskbar_above_follow_active = False
        self.taskbar_fusion_mode = "off"
        self._clear_taskbar_preview_runtime()
        self.stack_direction = target.direction
        self.move(target.window_pos)
        self._force_super_topmost()
        self._restore_motion_global_rects_after_move()
        if save:
            self.save_last_position(target.window_pos)
        return True

    def _standalone_above_taskbar_target_for_screen(
        self,
        anchor_rect: QRectF,
        screen,
        taskbar_rect: QRectF,
        animate: bool = False,
    ) -> StandaloneAboveTaskbarTarget | None:
        taskbar = QRectF(taskbar_rect)
        anchor = QRectF(anchor_rect)
        if screen is None or taskbar.isNull() or anchor.isNull():
            return None
        screen_geo = screen.geometry()
        follow_taskbar, hidden_autohide = self._taskbar_follow_rect_for_current_visibility(screen, taskbar)
        base_taskbar = QRectF(getattr(self, "taskbar_follow_base_rect", QRectF()))
        if not base_taskbar.isNull():
            taskbar = base_taskbar
        track = self._taskbar_track_rect_for_screen(screen, taskbar)
        if track.isNull():
            track = QRectF(taskbar)
        main_kind = self._main_kind()
        target_stack_w = self._stack_target_width(main_kind)
        width_limit = self._standalone_width_limit_for_global_center(anchor.center().x(), screen_geo, target_stack_w)
        stack_w = standalone_stack_width_for_limit(target_stack_w, width_limit)
        main_w, main_h = self._main_size_for_kind(main_kind, stack_w)
        main_left = self.width() / 2.0 - main_w / 2.0
        gap = self._stick_taskbar_above_gap(taskbar, hidden_autohide=hidden_autohide)
        direction = -1 if follow_taskbar.center().y() >= QRectF(screen_geo).center().y() else 1
        main_top = self._main_top_for_direction(main_kind, main_h, direction)
        bottom_extension = self._taskbar_bottom_boundary_extension_for_screen(screen, taskbar)
        return build_standalone_above_taskbar_target(
            visible_taskbar=taskbar,
            follow_taskbar=follow_taskbar,
            track_rect=track,
            screen_geometry=screen_geo,
            window_width=float(self.width()),
            main_kind=main_kind,
            stack_width=stack_w,
            main_width=main_w,
            main_height=main_h,
            main_top=main_top,
            target_center_x=anchor.center().x(),
            current_position_ratio=float(getattr(self, "taskbar_above_position_ratio", 0.5) or 0.5),
            main_left=main_left,
            gap=gap,
            auto_hide_follow=self._taskbar_auto_hide_enabled_for_screen(screen),
            hidden_autohide=hidden_autohide,
            previous_hidden_autohide=bool(getattr(self, "taskbar_above_follow_last_hidden", False)),
            animate_requested=animate,
            bottom_extension=bottom_extension,
            margin=self._u(6.0),
        )

    def _settle_standalone_above_taskbar(self, anchor_rect: QRectF, screen, taskbar_rect: QRectF, animate: bool = False) -> bool:
        target = self._standalone_above_taskbar_target_for_screen(anchor_rect, screen, taskbar_rect, animate=animate)
        if target is None:
            return False
        self.stack_direction = target.direction
        self.taskbar_above_position_ratio = target.position_ratio
        self.taskbar_above_follow_active = True
        self.location_mode = "stick"
        self.finder_mode = False
        self.attach_to_real_finder = False
        self.taskbar_fusion_mode = "off"
        self._clear_taskbar_preview_runtime()
        self.last_taskbar_rect = QRectF(target.visible_taskbar)
        if target.animate_follow:
            self._animate_taskbar_follow_to_pos(target.window_pos)
        else:
            self.move(target.window_pos)
        self.taskbar_above_follow_last_hidden = bool(target.hidden_autohide)
        self._force_super_topmost()
        self._restore_motion_global_rects_after_move()
        self.save_last_position(target.window_pos)
        return True

    def _refresh_taskbar_above_follow_position(self) -> bool:
        initial_plan = build_taskbar_above_follow_refresh_plan(
            above_follow_active=bool(getattr(self, "taskbar_above_follow_active", False)),
            location_mode=str(getattr(self, "location_mode", "free") or "free"),
            finder_mode=bool(getattr(self, "finder_mode", False)),
            taskbar_rect=QRectF(),
            auto_hide_enabled=False,
            target_anchor_rect=QRectF(),
            current_anchor_rect=QRectF(),
        )
        if initial_plan.action in {"inactive", "clear_invalid_mode"}:
            if initial_plan.clear_above_taskbar_follow:
                self.taskbar_above_follow_active = False
            if initial_plan.force_location_mode:
                self.location_mode = initial_plan.force_location_mode
            return False
        try:
            probe = self.mapToGlobal(QPoint(max(1, self.width() // 2), max(1, self.height())))
        except Exception:
            probe = QPoint(0, 0)
        screen = QGuiApplication.screenAt(probe) or QGuiApplication.primaryScreen()
        taskbar = self._bottom_taskbar_rect_for_screen(screen)
        auto_hide_enabled = self._taskbar_auto_hide_enabled_for_screen(screen) if screen is not None else False
        target_anchor = self._current_main_target_global_rect()
        current_anchor = QRectF()
        if target_anchor.isNull():
            current_anchor = self._current_main_global_rect()
        plan = build_taskbar_above_follow_refresh_plan(
            above_follow_active=bool(getattr(self, "taskbar_above_follow_active", False)),
            location_mode=str(getattr(self, "location_mode", "free") or "free"),
            finder_mode=bool(getattr(self, "finder_mode", False)),
            taskbar_rect=taskbar,
            auto_hide_enabled=auto_hide_enabled,
            target_anchor_rect=target_anchor,
            current_anchor_rect=current_anchor,
        )
        if plan.clear_above_taskbar_follow:
            self.taskbar_above_follow_active = False
        if plan.force_location_mode:
            self.location_mode = plan.force_location_mode
        if not plan.settle_above_taskbar:
            return False
        return self._settle_standalone_above_taskbar(plan.anchor_rect, screen, taskbar, animate=False)

    def _standalone_free_release_target_for_screen(self, anchor_rect: QRectF, screen) -> StandalonePlacementTarget | None:
        anchor = QRectF(anchor_rect)
        if screen is None or anchor.isNull():
            return None
        try:
            screen_geo = screen.geometry()
        except Exception:
            return None
        main_kind = self._main_kind()
        target_stack_w = self._stack_target_width(main_kind)
        width_limit = self._standalone_width_limit_for_global_center(anchor.center().x(), screen_geo, target_stack_w)
        stack_w = standalone_stack_width_for_limit(target_stack_w, width_limit)
        main_w, main_h = self._main_size_for_kind(main_kind, stack_w)
        direction = self._standalone_release_direction_for_anchor(anchor, main_kind, main_h, screen_geo)
        main_top = self._main_top_for_direction(main_kind, main_h, direction)
        return build_standalone_free_release_target(
            anchor_rect=anchor,
            screen_geometry=screen_geo,
            window_width=float(self.width()),
            main_kind=main_kind,
            stack_width=stack_w,
            main_width=main_w,
            main_height=main_h,
            main_top=main_top,
            direction=direction,
            bottom_extension=self._standalone_bottom_boundary_extension_for_screen(screen),
            margin=self._u(6.0),
        )

    def _apply_standalone_release_runtime_plan(self, plan) -> None:
        if plan.clear_above_taskbar_follow:
            self.taskbar_above_follow_active = False
        if plan.force_location_mode:
            self.location_mode = plan.force_location_mode
        if plan.force_finder_mode is not None:
            self.finder_mode = bool(plan.force_finder_mode)
        if plan.force_attach_to_real_finder is not None:
            self.attach_to_real_finder = bool(plan.force_attach_to_real_finder)
        if plan.force_taskbar_fusion_mode:
            self.taskbar_fusion_mode = plan.force_taskbar_fusion_mode
        if plan.clear_taskbar_preview:
            self.taskbar_preview_visible = False

    def _execute_standalone_release_taskbar_dock(self, anchor: QRectF, screen, taskbar: QRectF, plan) -> bool:
        self._apply_standalone_release_runtime_plan(plan)
        self.taskbar_dock_animation_requested = bool(plan.request_taskbar_dock_animation)
        if plan.set_location_mode:
            self.set_location_mode(plan.set_location_mode, notify=False)
        try:
            self._dock_to_taskbar_task_area(
                target_center_x=anchor.center().x(),
                target_screen=screen,
                target_taskbar_rect=taskbar,
            )
        except Exception:
            pass
        finally:
            self.taskbar_dock_animation_requested = False
        return True

    def _execute_standalone_release_free_placement(self, anchor: QRectF, screen, plan) -> bool:
        self._apply_standalone_release_runtime_plan(plan)
        target = self._standalone_free_release_target_for_screen(anchor, screen)
        if target is None:
            return False
        self.stack_direction = target.direction
        self.move(target.window_pos)
        self._restore_motion_global_rects_after_move()
        self.save_last_position(target.window_pos)
        return True

    def _screen_for_standalone_release(self, anchor: QRectF, release_global_pos: QPoint | QPointF | None = None):
        probe = standalone_release_probe_point(anchor, release_global_pos)
        if probe is None:
            return None
        fallback = QPoint(int(round(anchor.center().x())), int(round(anchor.center().y())))
        return QGuiApplication.screenAt(probe) or QGuiApplication.screenAt(fallback) or QGuiApplication.primaryScreen()

    def _standalone_release_execution_plan_for_screen(
        self,
        anchor: QRectF,
        release_global_pos: QPoint | QPointF | None,
        screen,
    ):
        taskbar = self._bottom_taskbar_rect_for_screen(screen)
        decision = self._standalone_release_decision(anchor, release_global_pos, screen, taskbar)
        return taskbar, build_standalone_release_execution_plan(decision)

    def _execute_standalone_release_plan(self, anchor: QRectF, screen, taskbar: QRectF, plan) -> bool:
        if not plan.has_adapter_action:
            return False
        if plan.execute_top_notch:
            return self._settle_standalone_top_notch(anchor, screen)
        if plan.execute_taskbar_dock:
            return self._execute_standalone_release_taskbar_dock(anchor, screen, taskbar, plan)
        if plan.execute_above_taskbar:
            return self._settle_standalone_above_taskbar(anchor, screen, taskbar)
        if plan.execute_free_placement:
            return self._execute_standalone_release_free_placement(anchor, screen, plan)
        return False

    def _settle_standalone_drag_release(self, anchor_rect: QRectF, release_global_pos: QPoint | QPointF | None = None) -> bool:
        anchor = QRectF(anchor_rect)
        if anchor.isNull():
            return False
        screen = self._screen_for_standalone_release(anchor, release_global_pos)
        if screen is None:
            return False
        taskbar, plan = self._standalone_release_execution_plan_for_screen(anchor, release_global_pos, screen)
        return self._execute_standalone_release_plan(anchor, screen, taskbar, plan)

    def _drag_main_offset_for_press(self, press_pos: QPointF, main_rect: QRectF, key: str = "") -> QPointF:
        return positioning_drag_main_offset_for_press(press_pos, main_rect, key)

    def _save_placement_state(self):
        if self.finder_mode:
            self.saved_window_pos = None
        elif self.saved_window_pos is None:
            self.save_last_position(QPoint(int(self.x()), int(self.y())))
        self._notify_settings_changed()

    def _apply_mouse_release_cleanup_plan(self, plan):
        if (
            bool(getattr(plan, "clear_taskbar_preview", False))
            and bool(getattr(plan, "clear_taskbar_selected_drop_zone", False))
            and bool(getattr(plan, "clear_taskbar_attach_hot_zone", False))
            and bool(getattr(plan, "clear_taskbar_drop_zones", False))
            and bool(getattr(plan, "hide_taskbar_drop_overlay", False))
        ):
            self._clear_taskbar_preview_runtime()
            return
        if bool(getattr(plan, "clear_taskbar_preview", False)):
            self.taskbar_preview_visible = False
        if bool(getattr(plan, "clear_taskbar_selected_drop_zone", False)):
            self.taskbar_selected_drop_zone = QRectF()
        if bool(getattr(plan, "clear_taskbar_attach_hot_zone", False)):
            self.taskbar_attach_hot_zone = QRectF()
            self.taskbar_attach_hot_zone_host_rect = QRectF()
            self.taskbar_attach_hot_zone_active = False
        if bool(getattr(plan, "clear_taskbar_drop_zones", False)):
            self.taskbar_drop_zones = []
        if bool(getattr(plan, "hide_taskbar_drop_overlay", False)):
            self._hide_taskbar_drop_overlay()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.last_pointer_press_at = time.monotonic()
        self.pointer_pos = event.position()
        if self.scene_suppressed:
            self.drag_candidate = False
            self.dragging = False
            return
        if self._taskbar_compact_active():
            self._stop_taskbar_position_animation()
            self._force_super_topmost()
        self.drag_press_pos = QPointF(event.position())
        self.drag_press_global = event.globalPosition().toPoint()
        self.drag_press_window_pos = self.pos()
        self.drag_press_center_ratio = self.stack_center_ratio
        self.drag_press_key = self._hit_test(event.position())
        self.drag_press_main_offset = QPointF()
        self.drag_press_main_global_rect = QRectF()
        self.drag_auto_stack_direction = 0
        try:
            visuals = self._build_visuals()
            main_visual = next((visual for visual in visuals if visual.z_index == 10), None)
            if main_visual:
                motion = self.motions.get(main_visual.key)
                main_rect = QRectF(motion.rect) if motion and motion.opacity > 0.08 else QRectF(main_visual.rect)
                self.drag_press_main_offset = self._drag_main_offset_for_press(event.position(), main_rect, self.drag_press_key)
                self.drag_press_main_global_rect = QRectF(main_rect)
                origin = self.mapToGlobal(QPoint(0, 0))
                self.drag_press_main_global_rect.translate(float(origin.x()), float(origin.y()))
            direction = int(getattr(self, "stack_direction", 1) or 1)
            self.drag_auto_stack_direction = direction if direction in (-1, 1) else 1
        except Exception:
            self.drag_press_main_offset = QPointF()
            self.drag_press_main_global_rect = QRectF()
            direction = int(getattr(self, "stack_direction", 1) or 1)
            self.drag_auto_stack_direction = direction if direction in (-1, 1) else 1
        self.drag_candidate = True
        self.dragging = False
        self.hover_armed_key = ""
        if str(self.drag_press_key or "").startswith("music:"):
            self.hovered_key = self.drag_press_key
        else:
            self.hovered_key = ""
        self.update()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.scene_suppressed:
                return
            if self._main_kind() != IslandKind.MUSIC:
                return
            self._set_expanded(False)
            self._pulse_stack()
            self.update()

    def _release_global_pos_from_event(self, event) -> QPoint:
        try:
            return event.globalPosition().toPoint()
        except Exception:
            try:
                return self.mapToGlobal(event.position().toPoint())
            except Exception:
                return QPoint(getattr(self, "drag_press_global", QPoint()))

    def _apply_mouse_release_action_plan(self, plan, event, release_global_pos: QPoint) -> tuple[QRectF, bool]:
        release_anchor_rect = QRectF()
        accepted_taskbar_drop = False
        if plan.activate_pressed_key:
            key = getattr(self, "drag_press_key", "") or self._hit_test(event.position())
            if key:
                if self._island_button_ripple_target(key).contains(event.position()):
                    self._start_island_button_ripple(key, getattr(self, "drag_press_pos", event.position()))
                self._activate_key(key)
            if plan.update_after_activation:
                self.update()
        elif plan.capture_release_anchor:
            release_anchor_rect = self._current_main_target_global_rect()
            if plan.recheck_taskbar_preview:
                try:
                    self._update_taskbar_preview_for_drag(release_global_pos)
                except Exception:
                    pass
            if plan.accept_taskbar_drop:
                accepted_taskbar_drop = self._accept_taskbar_preview_drop()
        return release_anchor_rect, accepted_taskbar_drop

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        release_global_pos = self._release_global_pos_from_event(event)
        action_plan = build_mouse_release_action_plan(
            scene_suppressed=bool(getattr(self, "scene_suppressed", False)),
            drag_candidate=bool(getattr(self, "drag_candidate", False)),
            dragging=bool(getattr(self, "dragging", False)),
            taskbar_preview_visible=bool(getattr(self, "taskbar_preview_visible", False)),
        )
        release_anchor_rect, accepted_taskbar_drop = self._apply_mouse_release_action_plan(
            action_plan,
            event,
            release_global_pos,
        )
        if action_plan.reset_drag_state:
            self.drag_candidate = False
            self.dragging = False
            self.drag_auto_stack_direction = 0
        settle_plan = build_mouse_release_standalone_settle_plan(
            accepted_taskbar_drop=accepted_taskbar_drop,
            has_release_anchor=not release_anchor_rect.isNull(),
            location_mode=str(getattr(self, "location_mode", "free") or "free"),
        )
        if settle_plan.settle_standalone_release:
            if settle_plan.check_tasker_detach_above_taskbar and self._tasker_release_should_detach_above_taskbar(release_global_pos):
                self.set_location_mode("free", notify=False)
            self._settle_standalone_drag_release(release_anchor_rect, release_global_pos)
        cleanup_plan = build_mouse_release_cleanup_plan(
            accepted_taskbar_drop=accepted_taskbar_drop,
            has_release_anchor=not release_anchor_rect.isNull(),
        )
        self._apply_mouse_release_cleanup_plan(cleanup_plan)
        if action_plan.save_placement_state:
            self._save_placement_state()

    def wheelEvent(self, event):
        if self._handle_time_alarm_editor_wheel(event):
            return
        if self._handle_time_panel_module_wheel(event):
            return
        super().wheelEvent(event)

    def leaveEvent(self, event):
        self.hovered_key = ""
        self._set_island_music_control_hover("", time.monotonic())
        self.hover_armed_key = ""
        self.tasker_music_hover_auto_expand_suppressed = False
        self.barrage_hovered = False
        self.pointer_pos = QPointF(-1, -1)
        if self.expanded:
            self.left_stack_at = time.monotonic()
        super().leaveEvent(event)

    def closeEvent(self, event):
        self._close_solid_album_palette_worker()
        music_surface = getattr(self, "music_fullscreen_overlay", None)
        if music_surface is not None:
            try:
                music_surface.shutdown()
            except Exception:
                pass
        self.music_fullscreen_overlay = None
        overlays = [
            getattr(self, "plugin_alert_overlay", None),
            getattr(self, "barrage_overlay", None),
            getattr(self, "fullscreen_plugin_alert_overlay", None),
        ]
        seen: set[int] = set()
        for overlay in overlays:
            if overlay is None:
                continue
            try:
                identity = int(overlay.winId())
            except Exception:
                identity = id(overlay)
            if identity in seen:
                continue
            seen.add(identity)
            try:
                overlay.hide()
                overlay.deleteLater()
            except Exception:
                pass
        self.plugin_alert_overlay = None
        self.barrage_overlay = None
        self.fullscreen_plugin_alert_overlay = None
        super().closeEvent(event)

    def changeEvent(self, event):
        if is_theme_change_event(event):
            self._sync_music_background_to_theme(force=True)
            self.update()
        super().changeEvent(event)

    def _point_in_finder_frame(self, point: QPointF) -> bool:
        return positioning_point_in_finder_frame(
            finder_mode=bool(getattr(self, "finder_mode", False)),
            finder_hover_rect=QRectF(getattr(self, "finder_hover_rect", QRectF())),
            point=point,
            unit=self._u(1.0),
        )

    def _taskbar_selected_drop_zone_local_rect(self) -> QRectF:
        zone = QRectF(getattr(self, "taskbar_selected_drop_zone", QRectF()))
        origin = None
        try:
            origin = self.mapToGlobal(QPoint(0, 0))
        except Exception:
            pass
        return positioning_taskbar_selected_drop_zone_local_rect(
            zone,
            origin,
            unit=self._u(1.0),
        )

    def _taskbar_main_drag_key(self) -> str:
        try:
            main_visual = next((visual for visual in self._build_visuals() if visual.z_index == 10), None)
            if main_visual is not None and str(main_visual.key or ""):
                return str(main_visual.key)
        except Exception:
            pass
        return "music"

    def _hit_test(self, point: QPointF) -> str:
        if self._taskbar_compact_active():
            selected_grab_rect = self._taskbar_selected_drop_zone_local_rect()
            main_drag_key = "music"
            try:
                if not selected_grab_rect.isNull() and selected_grab_rect.contains(point):
                    main_drag_key = self._taskbar_main_drag_key()
            except Exception:
                main_drag_key = "music"
            taskbar_key = positioning_taskbar_compact_hit_test_key(
                point=point,
                compact_active=True,
                expanded=bool(getattr(self, "expanded", False)),
                left_handle_rect=QRectF(getattr(self, "taskbar_left_handle_rect", QRectF())),
                left_handle_visible=self._taskbar_left_handle_visible(),
                music_button_rects=getattr(self, "music_button_rects", {}),
                selected_drop_zone_local_rect=selected_grab_rect,
                main_drag_key=main_drag_key,
                unit=self._u(1.0),
            )
            if (
                taskbar_key in (None, "music")
                and getattr(self, "music_cover_rect", QRectF()).contains(point)
                and self._main_kind() == IslandKind.MUSIC
            ):
                return "music:open_surface"
            if taskbar_key is not None:
                return taskbar_key
        if (self._main_kind() == IslandKind.MUSIC
                and getattr(self, "music_cover_rect", QRectF()).contains(point)):
            return "music:open_surface"
        if self.expanded and self._main_kind() == IslandKind.MUSIC:
            if getattr(self, "music_cover_rect", QRectF()).contains(point):
                return "music:open_surface"
            for key, rect in self.music_button_rects.items():
                if rect.contains(point):
                    return key
        if self.expanded:
            if str(getattr(self, "plugin_time_mode", "dashboard")) == "alarm_editor":
                action_rects = getattr(self, "plugin_action_rects", {})
                for command, rect in action_rects.items():
                    if str(command).startswith("alarm-editor-open-") and rect.contains(point):
                        return f"plugin-action:{command}"
                for command, rect in action_rects.items():
                    if rect.contains(point):
                        return f"plugin-action:{command}"
                for field, rect in getattr(self, "plugin_alarm_editor_rects", {}).items():
                    if rect.contains(point):
                        return f"plugin-action:alarm-editor-focus-{field}"
            else:
                for command, rect in getattr(self, "plugin_action_rects", {}).items():
                    if rect.contains(point):
                        return f"plugin-action:{command}"
        for key, motion in sorted(self.motions.items(), key=lambda item: item[1].rect.y(), reverse=True):
            if motion.opacity > 0.22 and motion.rect.contains(point):
                return key
        try:
            for visual in sorted(self._build_visuals(), key=lambda item: item.z_index, reverse=True):
                if visual.rect.contains(point):
                    return visual.key
        except Exception:
            pass
        return ""

    def _set_plugin_focus(self, index: int, immediate: bool = False):
        count = self._enabled_plugin_slot_count()
        if count <= 0:
            self.plugin_focus = 0
            self.plugin_focus_from = 0
            self.plugin_focus_target = 0
            return
        index = max(0, min(index, count - 1))
        now = time.monotonic()
        if immediate:
            self.plugin_focus = index
            self.plugin_focus_from = index
            self.plugin_focus_target = index
            self.plugin_focus_changed_at = now - 0.26
            return
        if index == self.plugin_focus_target:
            return
        self.plugin_focus_from = self._active_plugin_focus(now)
        self.plugin_focus = index
        self.plugin_focus_target = index
        self.plugin_focus_changed_at = now

    def _focus_plugin_at(self, x: float, immediate: bool = False):
        if self._enabled_plugin_slot_count() <= 0:
            return
        motion = self.motions.get("plugin")
        if not motion:
            return
        now = time.monotonic()
        cells = self._plugin_item_rects(motion.rect, now, expand_active=False)
        index = positioning_plugin_focus_index_for_x(cells, x)
        if index is not None:
            self._set_plugin_focus(index, immediate=immediate)

    def _plugin_focus_progress(self, now: float) -> float:
        elapsed = now - self.plugin_focus_changed_at
        progress = max(0.0, min(1.0, elapsed / self._animation_duration(0.26)))
        return progress * progress * (3.0 - 2.0 * progress)

    def _active_plugin_focus(self, now: float) -> int:
        count = self._enabled_plugin_slot_count()
        return positioning_plugin_active_focus(
            count,
            expanded=bool(getattr(self, "expanded", False)),
            current_index=int(getattr(self, "plugin_focus", 0)),
            from_index=int(getattr(self, "plugin_focus_from", 0)),
            target_index=int(getattr(self, "plugin_focus_target", 0)),
            progress=self._plugin_focus_progress(now),
        )

    def _plugin_focus_weights(self, count: int, now: float, expand_active: bool = True) -> list[float]:
        return positioning_plugin_focus_weights(
            count,
            expanded=bool(getattr(self, "expanded", False)),
            from_index=int(getattr(self, "plugin_focus_from", 0)),
            target_index=int(getattr(self, "plugin_focus_target", 0)),
            progress=self._plugin_focus_progress(now),
            expand_active=expand_active,
        )

    def _plugin_widths(self, count: int, total_width: float, now: float, expand_active: bool = True) -> list[float]:
        return positioning_plugin_slot_widths(
            count,
            total_width,
            expanded=bool(getattr(self, "expanded", False)),
            from_index=int(getattr(self, "plugin_focus_from", 0)),
            target_index=int(getattr(self, "plugin_focus_target", 0)),
            progress=self._plugin_focus_progress(now),
            expand_active=expand_active,
            focus_width_cap=self._u(220.0),
        )

    def _plugin_item_rects(self, rect: QRectF, now: float, expand_active: bool = True) -> list[tuple[int, QRectF]]:
        count = max(0, min(self._enabled_plugin_slot_count(), 3))
        widths = self._plugin_widths(count, rect.width(), now, expand_active=expand_active)
        return positioning_plugin_item_rects(rect, widths)

    def _plugin_strip_layout(self, count: int, rect: QRectF, now: float, expand_active: bool = True):
        return positioning_plugin_strip_layout(
            rect,
            max(0, min(int(count), 3)),
            expanded=bool(getattr(self, "expanded", False)),
            current_index=int(getattr(self, "plugin_focus", 0)),
            from_index=int(getattr(self, "plugin_focus_from", 0)),
            target_index=int(getattr(self, "plugin_focus_target", 0)),
            progress=self._plugin_focus_progress(now),
            expand_active=expand_active,
            focus_width_cap=self._u(220.0),
        )

    def _music_target_width(self, expanded: bool | None = None) -> float:
        if self._personal_dj_island_presenting():
            font = self._font(20, QFont.Weight.DemiBold)
            title = getattr(self, "_island_dj_title", "") or getattr(self, "track_title", "")
            return min(self._u(760), max(self._u(380), QFontMetrics(font).horizontalAdvance(title) + self._u(152)))
        base = self._base_music_target_width(expanded)
        if bool(self.expanded if expanded is None else expanded):
            return base
        return self._audio_device_target_width(base)

    def _audio_device_target_width(self, base: float) -> float:
        pose = self._audio_device_pose()
        if pose is None:
            return base
        metric = QFontMetrics(self._font(self._compact_primary_lyric_font_size(), QFont.Weight.Bold, shrink=False))
        payloads = (pose.payload,) if pose.previous is None else (pose.previous, pose.payload)
        desired = max(min(self._u(680), metric.horizontalAdvance(p.name)) + self._u(104) for p in payloads)
        if any(p.battery_percent is not None for p in payloads):
            desired = max(desired, base + self._u(80))
        return base + (max(base, desired) - base) * pose.reveal

    def _base_music_target_width(self, expanded: bool | None = None) -> float:
        expanded = self.expanded if expanded is None else expanded
        instrumental = self._is_instrumental_track()
        lyric_lines = self._music_lyric_measurement_lines(self.lyrics, expanded=expanded)
        active_lyric_size = max(14, min(24, int(getattr(self, "lyric_font_size", 18))))
        compact_lyric_size = self._compact_primary_lyric_font_size()

        if expanded:
            title_w = min(
                float(QFontMetrics(self._font(13, QFont.Weight.Bold, shrink=False)).horizontalAdvance(self.track_title)),
                self._music_meta_measurement_width_cap(expanded=True),
            )
            artist_w = min(
                float(QFontMetrics(self._font(9, shrink=False)).horizontalAdvance(self.artist)),
                self._music_meta_measurement_width_cap(expanded=True),
            )
            lyric_w = min(
                self._representative_lyric_width(lyric_lines, active_lyric_size, True),
                self._music_lyric_measurement_width_cap(expanded=True),
            )
            identity_w = max(title_w, artist_w * 0.88)
            content_w = identity_w if instrumental else max(identity_w, lyric_w * 0.82)
            required = self._u(58 + 16 + 15 + 132 + 48) + content_w
            max_w = self._u(940.0)
            if self._should_paint_album_island():
                stage_w = self._album_island_preferred_stage_width(expanded=True)
                required = max(required, stage_w + self._u(36.0))
                max_w = self._u(1280.0)
            return max(self._u(470.0), min(max_w, required))

        if self._should_paint_album_island():
            stage_w = self._album_island_preferred_stage_width(expanded=False)
            required = self._u(9 + 30 + 12 + 25) + stage_w
            return max(self._u(300.0), min(self._u(760.0), required))

        compact_text = self._compact_music_measurement_text()
        compact_metric = QFontMetrics(self._wave_stream_font(compact_lyric_size, QFont.Weight.Bold, shrink=False))
        title_metric = QFontMetrics(self._font(11, QFont.Weight.Bold, shrink=False))
        content_w = min(
            float(compact_metric.horizontalAdvance(compact_text)),
            self._music_lyric_measurement_width_cap(expanded=False),
        )
        title_w = min(float(title_metric.horizontalAdvance(self.track_title)), self._music_meta_measurement_width_cap(expanded=False))
        artist_w = min(float(title_metric.horizontalAdvance(self.artist)), self._music_meta_measurement_width_cap(expanded=False))
        lyric_w = min(
            self._representative_lyric_width(lyric_lines, compact_lyric_size, True),
            self._music_lyric_measurement_width_cap(expanded=False),
        )
        current_dual_w = self._current_compact_dual_line_width(compact_lyric_size)
        return self._compact_music_target_width_from_content(content_w, title_w, artist_w, lyric_w, current_dual_w, instrumental)

    def _music_lyric_measurement_width_cap(self, expanded: bool) -> float:
        return self._u(680.0 if expanded else 640.0)

    def _music_meta_measurement_width_cap(self, expanded: bool) -> float:
        return self._u(560.0 if expanded else 420.0)

    def _music_lyric_measurement_chunk_limit(self, expanded: bool) -> int:
        return 36 if expanded else 30

    def _music_lyric_measurement_text(self, text: str, expanded: bool) -> str:
        text = " ".join(str(text or "").split())
        if not text:
            return ""
        chunks = [str(chunk).strip() for chunk in self._lyric_chunks(text) if str(chunk).strip()]
        limit = self._music_lyric_measurement_chunk_limit(expanded)
        if len(chunks) <= limit:
            return text
        return self._gelv_join_chunks(chunks[:limit])

    def _music_lyric_measurement_lines(self, lyrics: list[str], expanded: bool) -> list[str]:
        lines: list[str] = []
        for lyric in lyrics or []:
            cleaned = self._clean_lyric_for_width(str(lyric or ""))
            if not cleaned:
                continue
            display_lines = self._lyric_display_lines(cleaned) or [cleaned]
            for line in display_lines:
                measured = self._music_lyric_measurement_text(line, expanded)
                if measured:
                    lines.append(measured)
        return lines

    def _compact_music_measurement_text(self) -> str:
        text = self._compact_music_text()
        display_lines = self._lyric_display_lines(text)
        if display_lines:
            text = display_lines[0]
        return self._music_lyric_measurement_text(text, expanded=False)

    def _compact_music_target_width_from_content(
        self,
        content_w: float,
        title_w: float,
        artist_w: float,
        lyric_w: float,
        current_dual_w: float,
        instrumental: bool,
    ) -> float:
        return positioning_compact_music_target_width_from_content(
            content_width=content_w,
            title_width=title_w,
            artist_width=artist_w,
            lyric_width=lyric_w,
            current_dual_width=current_dual_w,
            instrumental=instrumental,
            compact_lyric_cover_enabled=self._compact_lyric_cover_enabled(),
            unit=self._u(1.0),
        )

    def _current_compact_dual_line_width(self, compact_size: float) -> float:
        lines = self._current_lyric_display_lines()
        if len(lines) <= 1:
            return 0.0
        secondary_bold = int(getattr(self, "dual_line_secondary_weight", 72)) >= 78
        secondary_spacing = int(getattr(self, "dual_line_secondary_spacing", 100))
        cap = self._music_lyric_measurement_width_cap(expanded=False)
        primary_text = self._music_lyric_measurement_text(lines[0], expanded=False)
        secondary_text = self._music_lyric_measurement_text(lines[1], expanded=False)
        primary_size, secondary_size = self._compact_dual_line_font_sizes(int(compact_size), secondary_bold)
        primary_w = self._lyric_layout(primary_text, primary_size, True).total_width
        secondary_w = self._lyric_layout(secondary_text, secondary_size, secondary_bold, secondary_spacing).total_width
        return min(cap, max(primary_w, secondary_w))

    def _top_notch_height_mode_active(self, mode: str) -> bool:
        return self._edge_notch_height_mode_active(mode)

    def _edge_notch_height_mode_active(self, mode: str) -> bool:
        return (
            str(getattr(self, "location_mode", "free") or "free") in {"top", "stick"}
            and self._normalize_top_notch_height_mode(getattr(self, "top_notch_height_mode", "standard")) == mode
        )

    def _compact_primary_lyric_font_size(self) -> int:
        size = max(12, min(20, int(getattr(self, "lyric_font_size", 18)) - 2))
        if self._top_notch_height_mode_active("compact"):
            size = min(size, 16)
        return size

    def _dual_line_secondary_font_size(self) -> int:
        size = max(8, min(20, int(getattr(self, "dual_line_font_size", 12))))
        if self._top_notch_height_mode_active("standard"):
            primary = max(12, min(20, int(getattr(self, "lyric_font_size", 18)) - 2))
            size = min(size, max(8, min(12, primary - 5)))
        return size

    def _compact_dual_line_font_sizes(self, compact_size: int, secondary_bold: bool = False) -> tuple[int, int]:
        primary_size = max(12, int(compact_size))
        secondary_size = max(self._dual_line_secondary_font_size(), min(primary_size, max(8, primary_size - 3)))
        return primary_size, secondary_size

    def _representative_text_width(self, lines: list[str], metrics: QFontMetrics) -> float:
        if not lines:
            return 0.0
        widths = sorted(metrics.horizontalAdvance(line) for line in lines if line)
        if not widths:
            return 0.0
        index = min(len(widths) - 1, max(0, int(round((len(widths) - 1) * 0.72))))
        return float(widths[index])

    def _representative_lyric_width(self, lines: list[str], size: float, bold: bool) -> float:
        if not lines:
            return 0.0
        key = (
            tuple(lines),
            self._font_size(size),
            bool(bold),
            tuple(self._wave_stream_font_families()),
            bool(getattr(self, "high_quality_font_rendering", False)),
            int(getattr(self, "lyric_elastic_strength", 100)),
            int(getattr(self, "lyric_word_spacing", 100)),
        )
        cache = getattr(self, "_representative_lyric_width_cache", None)
        if cache is None:
            cache = {}
            self._representative_lyric_width_cache = cache
        cached = cache.get(key)
        if cached is not None:
            return cached
        widths = sorted(self._lyric_layout(line, size, bold).total_width for line in lines if line)
        if not widths:
            return 0.0
        index = min(len(widths) - 1, max(0, int(round((len(widths) - 1) * 0.72))))
        value = float(widths[index])
        cache[key] = value
        while len(cache) > int(getattr(self, "REPRESENTATIVE_WIDTH_CACHE_LIMIT", 12)):
            cache.pop(next(iter(cache)))
        return value

    def _is_instrumental_track(self) -> bool:
        if self._empty_music_text_active():
            return False
        if len(self.lyrics) > 1:
            return False
        lines = [self._clean_lyric_for_width(line).lower() for line in self.lyrics]
        lines = [line for line in lines if line]
        if not lines:
            return True
        marker_text = " ".join(lines[:4])
        markers = ("纯音乐", "instrumental", "伴奏", "无人声", "无歌词", "暂无歌词", "暂无")
        return any(marker in marker_text for marker in markers)

    def _compact_music_text(self) -> str:
        if self._empty_music_text_active():
            return str(getattr(self, "no_lyrics_text", "暂无歌词") or "暂无歌词").strip() or "暂无歌词"
        if self._is_instrumental_track():
            title = self.track_title.strip()
            artist = self.artist.strip()
            if title and artist:
                return f"{title} · {artist}"
            return title or artist or "纯音乐"
        current = self._clean_lyric_for_width(self.lyrics[self._island_display_row_index()]) if self.lyrics else ""
        return current or self.track_title or self.artist or "正在播放"

    def _lyric_display_lines(self, lyric: str) -> list[str]:
        text = str(lyric or "").strip()
        if not text:
            return []
        allow_dual_line = self._dual_line_lyrics_allowed()
        if "\n" in text:
            lines = [" ".join(part.split()) for part in text.splitlines()]
            lines = [line for line in lines if line]
            return lines[:2] if allow_dual_line else lines[:1]
        if not allow_dual_line:
            return [" ".join(text.split())]
        for pattern in (r"\s[|｜]\s", r"\s/{1,2}\s"):
            parts = [" ".join(part.split()) for part in re.split(pattern, text, maxsplit=1)]
            if len(parts) == 2 and self._looks_like_two_line_lyric(parts[0], parts[1]):
                return parts
        return [" ".join(text.split())]

    def _looks_like_two_line_lyric(self, first: str, second: str) -> bool:
        if len(first) < 4 or len(second) < 4:
            return False
        if len(first) > 48 or len(second) > 48:
            return False
        has_cjk = any(self._is_cjk_lyric_char(char) for char in first + second)
        if has_cjk:
            return True
        return len(first.split()) >= 3 and len(second.split()) >= 3

    def _gelv_enabled(self) -> bool:
        return str(getattr(self, "lyric_flow_style", "wave_stream")) == "gelv_songscatter"

    def _ember_enabled(self) -> bool:
        return str(getattr(self, "lyric_flow_style", "wave_stream")) == "ember_fade"

    def _ember_line_mode_key(self) -> str:
        value = str(getattr(self, "ember_line_mode", "split") or "split").strip().lower()
        return "scroll" if value == "scroll" else "split"

    def _ember_dual_line_enabled(self) -> bool:
        return bool(getattr(self, "ember_dual_line_enabled", False))

    def _ember_edge_scroll_forced(self, expanded: bool = False) -> bool:
        return str(getattr(self, "location_mode", "free") or "free") in {"tasker", "stick"}

    def _ember_effective_line_mode(self, expanded: bool = False) -> str:
        if self._ember_edge_scroll_forced(expanded=expanded) or self._ember_dual_line_enabled():
            return "scroll"
        return self._ember_line_mode_key()

    def _yaoguang_colorful_unlocked(self) -> bool:
        return (
            str(getattr(self, "music_ambience_style", "sea_level") or "sea_level") == "yaoguang"
            and self._yaoguang_color_source_key() == "album"
            and bool(getattr(self, "yaoguang_album_inverse_enabled", False))
        )

    def _lyric_color_fill_background_available(self) -> bool:
        style = self._effective_music_ambience_style()
        return style not in self.COLOR_FILL_BLOCKED_AMBIENCE_STYLES or self._yaoguang_colorful_unlocked()

    def _gelv_color_fill_available(self) -> bool:
        return self._lyric_color_fill_background_available()

    def _ember_color_fill_available(self) -> bool:
        style = str(getattr(self, "music_ambience_style", "sea_level") or "sea_level")
        return style in self.EMBER_COLOR_FILL_AMBIENCE_STYLES

    def _ember_color_back_gray_available(self) -> bool:
        style = str(getattr(self, "music_ambience_style", "sea_level") or "sea_level")
        return style in self.EMBER_COLOR_BACK_GRAY_AMBIENCE_STYLES

    def _gelv_color_theme_enabled(self) -> bool:
        return bool(getattr(self, "gelv_color_fill_enabled", False)) and self._gelv_color_fill_available() and self._gelv_fill_color().isValid()

    def _gelv_word_fill_enabled(self) -> bool:
        return bool(getattr(self, "gelv_word_fill_enabled", False)) and not self._island_whole_row_reading()

    def _ember_color_theme_enabled(self) -> bool:
        return bool(getattr(self, "ember_color_fill_enabled", False)) and self._ember_color_fill_available() and self._ember_fill_color().isValid()

    def _ember_word_fill_enabled(self) -> bool:
        return bool(getattr(self, "ember_word_fill_enabled", True)) and not self._island_whole_row_reading()

    def _wave_color_theme_enabled(self) -> bool:
        return bool(getattr(self, "wave_color_fill_enabled", False)) and self._lyric_color_fill_background_available() and self._wave_fill_color().isValid()

    def _wave_word_fill_enabled(self) -> bool:
        return bool(getattr(self, "wave_word_fill_enabled", False))

    def _active_lyric_color_fill_enabled(self) -> bool:
        # The shared continuous reading front ignores historical effect
        # switches. Keeping their saved values does not change live paint.
        return False

    def _active_lyric_fill_color(self) -> QColor:
        return self._wave_fill_color()

    def _dual_line_lyrics_allowed(self) -> bool:
        if self._taskbar_compact_active():
            return False
        if self._edge_notch_height_mode_active("compact"):
            return False
        return bool(getattr(self, "enable_dual_line_lyrics", False))

    def _gelv_line_sizes(self, taskbar: bool = False) -> tuple[int, int]:
        active = max(14, min(30, int(getattr(self, "gelv_primary_font_size", 22))))
        secondary = max(8, min(24, int(getattr(self, "gelv_secondary_font_size", 12))))
        secondary = min(active, secondary)
        if taskbar:
            active = max(12, active - 4)
            secondary = max(8, min(active, secondary - 3))
        elif self._edge_notch_height_mode_active("compact"):
            active = min(active, 18)
            secondary = max(8, min(secondary, active - 7))
        elif self._edge_notch_height_mode_active("standard"):
            secondary = max(8, min(secondary, active - 8))
        return active, secondary

    def _gelv_dual_line_size(self, taskbar: bool = False) -> int:
        active, _secondary = self._gelv_line_sizes(taskbar=taskbar)
        size = max(8, min(30, int(getattr(self, "gelv_dual_line_font_size", 17))))
        if taskbar:
            size = max(8, size - 3)
        elif self._edge_notch_height_mode_active("standard"):
            size = min(size, max(8, active - 8))
        return min(active, size)

    def _gelv_line_pixel_height(self, size: float, expanded: bool = False) -> float:
        metrics = QFontMetrics(self._gelv_font(size, QFont.Weight.Normal))
        padding = self._u(3.0 if expanded else 2.0)
        fallback = self._u(max(float(size) + (10.0 if expanded else 4.0), 14.0))
        return max(fallback, float(metrics.height()) + padding)

    def _gelv_compact_music_height(self) -> float:
        active_size, secondary_size = self._gelv_line_sizes(taskbar=False)
        line_count = len(self._gelv_display_lines())
        if line_count <= 1:
            content_px = self._gelv_line_pixel_height(active_size, expanded=False)
            return max(46.0, min(64.0, content_px / self.VISUAL_SCALE + 9.0))
        explicit_dual = self._gelv_current_dual_line() or self._gelv_current_mixed_language_line()
        second_size = self._gelv_dual_line_size(taskbar=False) if explicit_dual else secondary_size
        line_heights = [
            self._gelv_line_pixel_height(size, expanded=False)
            for size in (active_size, second_size)[:line_count]
        ]
        content_px = sum(line_heights) + self._u(1.0) * max(0, line_count - 1)
        height = max(46.0, min(82.0, content_px / self.VISUAL_SCALE + 9.0))
        if not explicit_dual:
            height = min(height, 68.0)
        return height

    def _ember_current_text(self, visual_index: int | None = None) -> str:
        if self._empty_music_text_active():
            return str(getattr(self, "no_lyrics_text", "暂无歌词") or "暂无歌词").strip() or "暂无歌词"
        lines = self._compact_lyric_lines()
        if not lines:
            return str(getattr(self, "track_title", "") or getattr(self, "artist", "") or "正在播放")
        index = int(getattr(self, "lyric_index", 0) if visual_index is None else visual_index) % len(lines)
        return self._clean_lyric_for_width(lines[index]) or str(lines[index] or "")

    def _ember_current_display_lines(self, expanded: bool = False, visual_index: int | None = None) -> list[str]:
        return self._ember_display_lines_for_mode(self._ember_current_text(visual_index=visual_index), expanded=expanded)

    def _ember_base_font_size(self, expanded: bool = False, line_count: int = 1) -> int:
        configured_size = max(12, min(24, int(getattr(self, "ember_font_size", getattr(self, "lyric_font_size", 18)))))
        if expanded:
            size = max(14, configured_size)
            return max(13, size - (1 if line_count >= 3 else 0))
        size = configured_size
        if line_count >= 3:
            return max(11, size - 3)
        return size

    def _ember_compact_music_height(self) -> float:
        lines = self._ember_current_display_lines(expanded=False)
        line_count = max(1, len(lines))
        size = self._ember_base_font_size(expanded=False, line_count=line_count)
        metrics = QFontMetrics(self._ember_font(size, back_layer=True))
        content_px = float(metrics.height()) * line_count + self._u(1.5) * max(0, line_count - 1)
        if self._ember_effective_line_mode(expanded=False) == "scroll":
            if line_count > 1 and not self._ember_edge_scroll_forced(expanded=False):
                height = max(54.0, min(76.0, content_px / self.VISUAL_SCALE + 14.0))
            else:
                height = max(40.0, min(58.0, content_px / self.VISUAL_SCALE + 10.0))
        elif line_count >= 3:
            height = max(92.0, min(112.0, content_px / self.VISUAL_SCALE + 38.0))
        else:
            height = max(46.0, min(84.0, content_px / self.VISUAL_SCALE + 12.0))
        try:
            yaoguang_compact = self._effective_music_ambience_style() == "yaoguang" and not self._taskbar_compact_active()
        except Exception:
            yaoguang_compact = False
        if yaoguang_compact:
            height += 12.0 if line_count > 1 else 8.0
        return height

    def _album_island_compact_music_height(self) -> float:
        cover = max(18, min(44, int(getattr(self, "album_island_cover_size", 30))))
        return max(46.0, min(72.0, float(cover) + 16.0))

    def _gelv_display_lines(self) -> list[str]:
        lyrics = getattr(self, "lyrics", None) or []
        if not lyrics:
            return []
        index = int(getattr(self, "lyric_index", 0)) % len(lyrics)
        active = str(lyrics[index] or "").strip()
        active_lines = [line.strip() for line in active.splitlines() if line.strip()]
        dual_enabled = self._dual_line_lyrics_allowed()
        active_primary = active_lines[0] if active_lines else active
        if dual_enabled and len(active_lines) > 1:
            return active_lines[:2]
        mixed = self._gelv_split_mixed_language_line(active) if dual_enabled else None
        if mixed is not None:
            return [mixed[0], mixed[1]]
        if self._edge_notch_height_mode_active("compact"):
            return [active_primary] if active_primary else []
        if len(lyrics) <= 1:
            return [active_primary] if active_primary else []
        next_line = str(lyrics[(index + 1) % len(lyrics)] or "").strip()
        next_primary = next((line.strip() for line in next_line.splitlines() if line.strip()), next_line)
        return [line for line in (active_primary, next_primary) if line]

    def _gelv_current_dual_line(self) -> bool:
        lyrics = getattr(self, "lyrics", None) or []
        if not lyrics or not self._dual_line_lyrics_allowed():
            return False
        index = int(getattr(self, "lyric_index", 0)) % len(lyrics)
        active = str(lyrics[index] or "")
        return len([line for line in active.splitlines() if line.strip()]) > 1

    def _gelv_current_mixed_language_line(self) -> bool:
        lyrics = getattr(self, "lyrics", None) or []
        if not lyrics or not self._dual_line_lyrics_allowed():
            return False
        index = int(getattr(self, "lyric_index", 0)) % len(lyrics)
        return self._gelv_split_mixed_language_line(str(lyrics[index] or "")) is not None

    def _gelv_split_mixed_language_line(self, text: str) -> tuple[str, str] | None:
        text = " ".join(str(text or "").split())
        if not text:
            return None
        has_cjk = any(self._is_cjk_lyric_char(char) for char in text)
        has_latin = any(("A" <= char <= "Z") or ("a" <= char <= "z") for char in text)
        if not has_cjk or not has_latin:
            return None
        matches = list(re.finditer(r"\s+", text))
        for match in reversed(matches):
            left = text[: match.start()].strip()
            right = text[match.end() :].strip()
            if not left or not right:
                continue
            left_cjk = any(self._is_cjk_lyric_char(char) for char in left)
            right_cjk = any(self._is_cjk_lyric_char(char) for char in right)
            left_latin = any(("A" <= char <= "Z") or ("a" <= char <= "z") for char in left)
            right_latin = any(("A" <= char <= "Z") or ("a" <= char <= "z") for char in right)
            if left_latin and not left_cjk and right_cjk:
                return left, right
            if left_cjk and right_latin and not right_cjk:
                return left, right
        return None

    def _gelv_spacing_percent(self, progress: float = 1.0) -> int:
        initial = max(100, min(320, int(getattr(self, "gelv_initial_word_spacing", 220))))
        if self._island_whole_row_reading():
            return initial
        target = max(initial, min(420, int(getattr(self, "gelv_word_spacing", 320))))
        progress = max(0.0, min(1.0, float(progress)))
        return int(round(initial + (target - initial) * progress))

    def _gelv_layout_width(self, text: str, size: float, spacing_progress: float = 1.0) -> float:
        spacing = self._gelv_spacing_percent(spacing_progress)
        layout = self._gelv_layout(text, size, spacing_percent=spacing)
        return float(layout.total_width)

    def _gelv_transition_state(self, now: float | None = None) -> dict[str, float]:
        now = time.monotonic() if now is None else float(now)
        current_index = int(getattr(self, "lyric_index", 0))
        previous_index = int(getattr(self, "prev_lyric_index", current_index))
        changed_at = float(getattr(self, "lyric_changed_at", now))
        duration = self._animation_duration(0.42)
        elapsed = max(0.0, min(1.0, (now - changed_at) / duration))
        transitioning = previous_index != current_index and elapsed < 1.0
        if transitioning:
            eased = 1.0 - (1.0 - elapsed) ** 4
            return {
                "progress": elapsed,
                "eased": eased,
                "out_alpha": 1.0 - eased,
                "in_alpha": max(0.18, eased),
                "out_y_offset": 0.0,
                "current_y_offset": 0.0,
                "current_size_mix": 1.0,
            }
        return {
            "progress": 1.0,
            "eased": 1.0,
            "out_alpha": 0.0,
            "in_alpha": 1.0,
            "out_y_offset": 0.0,
            "current_y_offset": 0.0,
            "current_size_mix": 1.0,
        }

    def _gelv_line_x(self, rect: QRectF, text: str, size: float, progress: float, spacing_percent: int | None = None) -> float:
        spacing = int(spacing_percent) if spacing_percent is not None else self._gelv_spacing_percent(progress)
        layout = self._gelv_layout(text, size, spacing_percent=spacing)
        text_w = float(layout.total_width)
        if text_w <= rect.width() - self._u(8.0):
            return rect.center().x() - text_w / 2.0
        travel = max(0.0, text_w - rect.width() + self._u(18.0))
        eased = 1.0 - (1.0 - max(0.0, min(1.0, float(progress)))) ** 3
        return rect.left() - travel * eased

    def _gelv_primary_text_for_index(self, index: int) -> str:
        lyrics = getattr(self, "lyrics", None) or []
        if not lyrics:
            return ""
        text = str(lyrics[index % len(lyrics)] or "").strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            return lines[0]
        mixed = self._gelv_split_mixed_language_line(text) if self._dual_line_lyrics_allowed() else None
        return mixed[0] if mixed is not None else text

    def _gelv_line_rects(self, rect: QRectF, expanded: bool = False, now: float | None = None) -> list[dict[str, object]]:
        lines = self._gelv_display_lines()
        if not lines:
            return []
        taskbar = self._taskbar_compact_active() and not expanded
        active_size, secondary_size = self._gelv_line_sizes(taskbar=taskbar)
        dual = self._gelv_current_dual_line() or self._gelv_current_mixed_language_line()
        dual_line_size = self._gelv_dual_line_size(taskbar=taskbar)
        state = self._gelv_transition_state(now)
        size_mix = max(0.0, min(1.0, float(state.get("current_size_mix", 1.0))))
        incoming_size = secondary_size + (active_size - secondary_size) * size_mix
        if dual:
            sizes = [incoming_size, dual_line_size]
            actives = [True, True]
            stable_sizes = [active_size, dual_line_size]
        else:
            sizes = [incoming_size, secondary_size]
            actives = [True, False]
            stable_sizes = [active_size, secondary_size]
        gap = self._u(8.0 if expanded else (0.0 if taskbar else 1.0))
        line_heights = [self._gelv_line_pixel_height(size, expanded=expanded) for size in stable_sizes[: len(lines)]]
        total_h = sum(line_heights) + gap * max(0, len(line_heights) - 1)
        if taskbar and len(line_heights) > 1 and rect.height() > 0.0 and total_h > rect.height():
            count = len(line_heights)
            available_h = max(1.0, rect.height() - gap * max(0, count - 1))
            scale = max(0.50, min(1.0, available_h / max(1.0, sum(line_heights))))
            sizes = [max(8.0, float(size) * scale) for size in sizes]
            stable_sizes = [max(8.0, float(size) * scale) for size in stable_sizes]
            line_heights = [self._gelv_line_pixel_height(size, expanded=False) for size in stable_sizes[: len(lines)]]
            total_h = sum(line_heights) + gap * max(0, len(line_heights) - 1)
            if total_h > rect.height():
                fit_scale = max(0.1, rect.height() / max(1.0, total_h))
                line_heights = [max(1.0, height * fit_scale) for height in line_heights]
                total_h = sum(line_heights) + gap * max(0, len(line_heights) - 1)
            if len(sizes) > 1:
                secondary_limit = max(5.0, float(sizes[0]) - 7.0)
                sizes[1] = min(float(sizes[1]), secondary_limit)
        y = rect.center().y() - total_h / 2.0
        if taskbar:
            y = max(rect.top(), min(y, rect.bottom() - total_h))
        rows: list[dict[str, object]] = []
        for index, text in enumerate(lines[:2]):
            row = QRectF(rect.left(), y, rect.width(), line_heights[index])
            rows.append({
                "text": text,
                "rect": row,
                "size": sizes[index],
                "active": actives[index],
                "opacity": 0.58 if taskbar and index > 0 else 1.0,
                "color_fill": index == 0,
                "progress_fill": False,
                "allow_word_fill": index == 0,
                "spacing_percent": None if index == 0 or (dual and index == 1) else 100,
                "transition_out": False,
            })
            y = row.bottom() + gap
        out_alpha = max(0.0, min(1.0, float(state.get("out_alpha", 0.0))))
        previous_index = int(getattr(self, "prev_lyric_index", int(getattr(self, "lyric_index", 0))))
        current_index = int(getattr(self, "lyric_index", 0))
        previous_text = self._gelv_primary_text_for_index(previous_index)
        moving_switch = out_alpha > 0.001 and previous_index != current_index and not dual and len(rows) >= 2
        if moving_switch:
            eased = max(0.0, min(1.0, float(state.get("eased", 1.0))))
            upper_rect = QRectF(rows[0]["rect"])
            lower_rect = QRectF(rows[1]["rect"])
            incoming = QRectF(upper_rect)
            incoming.moveTop(lower_rect.top() + (upper_rect.top() - lower_rect.top()) * eased)
            rows[0]["rect"] = incoming
            enter_offset = (lower_rect.height() + gap) * (1.0 - eased)
            entering_next = QRectF(lower_rect)
            entering_next.moveTop(lower_rect.top() + enter_offset)
            rows[1]["rect"] = entering_next
            rows[1]["opacity"] = max(0.42, eased)
        if out_alpha > 0.001 and previous_index != current_index and previous_text and rows:
            out_rect = QRectF(rows[0]["rect"])
            if moving_switch:
                eased = max(0.0, min(1.0, float(state.get("eased", 1.0))))
                upper_rect = QRectF(rect.left(), rect.center().y() - total_h / 2.0, rect.width(), line_heights[0])
                out_rect = QRectF(upper_rect)
                out_rect.moveTop(upper_rect.top() - (upper_rect.height() + gap) * eased)
            out_row = dict(rows[0])
            out_row.update({
                "text": previous_text,
                "rect": out_rect,
                "size": active_size,
                "opacity": out_alpha,
                "color_fill": True,
                "spacing_percent": None,
                "scroll_progress": 1.0,
                "transition_out": True,
            })
            rows.insert(0, out_row)
        return rows

    def _gelv_spacing_progress(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else float(now)
        phase = self._lyric_phase(now)
        if "progress" in phase:
            return max(0.0, min(1.0, float(phase.get("progress", 0.0))))
        timed = getattr(self, "timed_lyrics", []) or []
        index = int(getattr(self, "lyric_index", 0))
        if timed and 0 <= index < len(timed):
            position = max(0.0, self._stable_lyric_source_position(now) + self._effective_lyric_offset_seconds())
            start = float(timed[index][0])
            if index + 1 < len(timed):
                end = float(timed[index + 1][0])
            else:
                end = max(start + 1.0, float(getattr(self, "media_duration_seconds", 0.0) or 0.0))
            if end > start:
                return max(0.0, min(1.0, (position - start) / (end - start)))
        changed_at = float(getattr(self, "lyric_changed_at", now))
        return max(0.0, min(1.0, (now - changed_at) / 3.0))

    def _ember_lyric_phase(self, now: float) -> dict[str, float]:
        extra_offset = max(0.0, min(4.0, float(getattr(self, "ember_lyric_offset_seconds", 0.0) or 0.0)))
        if abs(extra_offset) <= 0.0001:
            return self._lyric_phase(now)
        estimated_position = self._stable_lyric_source_position(now)
        self._prepare_island_lyric_offset(now, self._effective_lyric_offset_seconds())
        paint_state = build_lyric_paint_state(
            timed_lyrics=getattr(self, "timed_lyrics", ()) or (),
            lyric_index=getattr(self, "lyric_index", 0),
            estimated_position_seconds=estimated_position,
            lyric_offset_seconds=self._effective_lyric_offset_seconds() + extra_offset,
            lyric_changed_at=getattr(self, "lyric_changed_at", now),
            previous_phase_index=getattr(self, "ember_last_phase_index", -1),
            previous_phase_progress=getattr(self, "ember_last_phase_progress", 0.0),
            previous_phase_at=getattr(self, "ember_last_phase_at", now),
            now=now,
            media_is_playing=getattr(self, "media_is_playing", False),
            transition_kind=getattr(self, "lyric_transition_kind", ""),
            position_selects_active_index=extra_offset > 0.0001,
            lead_seconds=0.0,
        )
        phase = dict(paint_state.phase)
        timed = getattr(self, "timed_lyrics", ()) or ()
        kinds = getattr(self, "timed_lyric_timing_kinds", ()) or ()
        units = getattr(self, "timed_lyric_units", ()) or ()
        index = paint_state.cache.active_index
        if self._island_song_has_genuine_unit_timing() and 0 <= index < len(timed):
            kind = kinds[index] if index < len(kinds) else "line"
            row_units = units[index] if index < len(units) else ()
            if kind == "line" or (kind == "unit" and not genuinely_timed_unit_row(row_units)):
                next_start = float(timed[index + 1][0]) if index + 1 < len(timed) else None
                estimated = estimated_row_reading_progress(
                    float(timed[index][0]), next_start,
                    estimated_position + self._effective_lyric_offset_seconds() + extra_offset,
                )
                if estimated is not None:
                    phase["progress"] = estimated
        self.ember_last_phase_index = paint_state.cache.active_index
        self.ember_last_phase_progress = float(phase.get("progress", paint_state.cache.progress))
        self.ember_last_phase_at = paint_state.cache.updated_at
        return phase

    def _lyric_fill_color(self) -> QColor:
        album = QColor(getattr(self, "album_color", QColor()))
        if album.saturation() < 28 or album.value() < 28:
            return QColor()
        hsv = album.toHsv()
        hsv.setHsv(hsv.hue(), max(190, hsv.saturation()), max(210, hsv.value()), 255)
        return hsv.toRgb()

    def _solid_album_color_usable(self, color: QColor) -> bool:
        if not color.isValid():
            return False
        hue, saturation, value, _alpha = color.getHsvF()
        if hue < 0 or saturation < 0.12:
            return False
        return 0.14 < value < 0.93

    def _saturated_solid_album_color(self, color: QColor) -> QColor:
        hue, saturation, value, _alpha = color.getHsvF()
        if hue < 0:
            hue = 0.56
        return QColor.fromHsvF(
            hue,
            max(0.78, min(1.0, saturation + 0.16)),
            max(0.52, min(0.88, value)),
            1.0,
        )

    def _solid_album_fallback_color(self, color: QColor | str | None = None) -> QColor:
        candidate = QColor(color) if color is not None else QColor(str(getattr(self, "solid_album_background_fallback", "#16BCFF")))
        if not candidate.isValid():
            candidate = QColor("#16BCFF")
        candidate.setAlpha(255)
        return candidate

    def _solid_album_palette_request(self):
        image = QImage(getattr(self, "album_art", QImage()))
        primary = QColor(getattr(self, "album_color", QColor()))
        secondary = QColor(getattr(self, "secondary_color", QColor()))
        fallback_setting = str(getattr(self, "solid_album_background_fallback", "#16BCFF") or "#16BCFF")
        # The legacy cyan default is not a claim about a missing cover's hue.
        # Keep an explicitly selected fallback, but make the untouched default
        # neutral when there is no artwork or usable provider color.
        fallback = QColor(56, 56, 56) if fallback_setting.upper() == "#16BCFF" else QColor(fallback_setting)
        if not fallback.isValid():
            fallback = QColor(56, 56, 56)
        if not primary.isValid():
            primary = QColor(fallback)
        if not secondary.isValid():
            secondary = QColor(primary)
        key = (image.cacheKey(), primary.rgba(), secondary.rgba(), fallback.rgba())
        return key, image, primary, secondary

    def _solid_album_surface_palette(self) -> MusicSurfacePalette:
        """Read prepared cover roles without cold extraction on the GUI thread."""
        key, image, primary, secondary = self._solid_album_palette_request()
        cached_key = getattr(self, "_solid_album_palette_cache_key", None)
        cached = getattr(self, "_solid_album_palette_cache", None)
        # Value-only legacy renderer fixtures do not construct a QWidget or
        # its queued signal bridge. They can retain exact steady-state role
        # checks; every normally constructed application island is async.
        if not bool(getattr(self, "_solid_album_palette_async_enabled", False)):
            if key != cached_key or cached is None:
                cached = (MusicSurfacePalette(primary, QColor(primary), QColor(primary))
                          if image.isNull() else artwork_surface_palette(image, primary, secondary))
                self._solid_album_palette_cache_key = key
                self._solid_album_palette_cache = cached
            return cached
        if key != cached_key or cached is None:
            if key != self._solid_album_palette_requested_key:
                self._solid_album_palette_generation += 1
                self._solid_album_palette_requested_key = key
                self._solid_album_palette_pending = None
                self._solid_album_palette_failed_key = None
            waiting_for_cover = image.isNull() and str(getattr(self, "album_art_lookup_state", "") or "") == "pending"
            if waiting_for_cover and self._solid_album_palette_cache is not None:
                return self._displayed_solid_album_palette()
            prepared = cached_artwork_surface_palette(image, primary, secondary)
            if prepared is not None:
                self._solid_album_palette_pending = None
                self._accept_solid_album_palette(key, prepared)
            elif image.isNull():
                self._accept_solid_album_palette(
                    key, MusicSurfacePalette(primary, QColor(primary), QColor(primary)),
                )
            elif not self._solid_album_palette_closed and key != self._solid_album_palette_failed_key:
                surface = getattr(self, "music_fullscreen_overlay", None)
                # The visible large surface uses the same shared cache and
                # already queues this cover. Avoid a second extraction while
                # it owns presentation; return-to-island will hit its result.
                large_surface_visible = surface is not None and surface.isVisible()
                if not large_surface_visible:
                    if (self._solid_album_palette_worker_key != key
                            or self._solid_album_palette_active_token != self._solid_album_palette_generation):
                        self._solid_album_palette_pending = (key, image, primary, secondary)
                    self._start_solid_album_palette_worker()
            if self._solid_album_palette_cache is None:
                self._solid_album_palette_cache = MusicSurfacePalette(
                    primary, QColor(primary), QColor(primary),
                )
        return self._displayed_solid_album_palette()

    def _initialize_solid_album_palette_worker(self) -> None:
        self._solid_album_palette_async_enabled = True
        self._solid_album_palette_generation = 0
        self._solid_album_palette_active_token = None
        self._solid_album_palette_worker_key = None
        self._solid_album_palette_pending = None
        self._solid_album_palette_requested_key = None
        self._solid_album_palette_failed_key = None
        self._solid_album_palette_closed = False
        self._solid_album_palette_cache_key = None
        self._solid_album_palette_cache = None
        self._solid_album_palette_blend_from = None
        self._solid_album_palette_blend_started_at = 0.0
        self._solid_album_palette_bridge = _IslandArtworkPaletteBridge(self)
        self._solid_album_palette_bridge.ready.connect(self._on_solid_album_palette_ready, Qt.ConnectionType.QueuedConnection)
        self._solid_album_palette_blend_timer = QTimer(self)
        self._solid_album_palette_blend_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._solid_album_palette_blend_timer.setInterval(16)
        self._solid_album_palette_blend_timer.timeout.connect(self._advance_solid_album_palette_blend)

    def _close_solid_album_palette_worker(self) -> None:
        if not bool(getattr(self, "_solid_album_palette_async_enabled", False)):
            return
        self._solid_album_palette_closed = True
        self._solid_album_palette_generation += 1
        self._solid_album_palette_pending = None
        self._solid_album_palette_blend_timer.stop()
        # Keep the one outstanding job owned until its queued completion.
        # A close/show on the same QWidget must not launch a second worker.
        # QObject automatically releases this bridge connection on deletion.

    def _resume_solid_album_palette_worker(self) -> None:
        if bool(getattr(self, "_solid_album_palette_async_enabled", False)) and self._solid_album_palette_closed:
            self._solid_album_palette_closed = False
            self._solid_album_palette_requested_key = None
            self._solid_album_palette_failed_key = None

    def _start_solid_album_palette_worker(self) -> None:
        if (self._solid_album_palette_closed or self._solid_album_palette_active_token is not None
                or self._solid_album_palette_pending is None):
            return
        key, image, primary, secondary = self._solid_album_palette_pending
        self._solid_album_palette_pending = None
        prepared = cached_artwork_surface_palette(image, primary, secondary)
        if prepared is not None:
            self._accept_solid_album_palette(key, prepared)
            return
        token = self._solid_album_palette_generation
        self._solid_album_palette_active_token = token
        self._solid_album_palette_worker_key = key
        bridge = self._solid_album_palette_bridge

        def prepare():
            # Only immutable value payloads and a signal bridge survive in
            # this closure. No QWidget, owner, geometry or painter is touched.
            try:
                result = artwork_surface_palette(image, primary, secondary)
            except Exception:
                result = None
            try:
                bridge.ready.emit(token, key, result)
            except RuntimeError:
                pass

        try:
            self._run_background("SolidArtworkPalette", prepare)
        except Exception:
            self._solid_album_palette_active_token = None
            self._solid_album_palette_worker_key = None
            self._solid_album_palette_failed_key = key

    @Slot(int, object, object)
    def _on_solid_album_palette_ready(self, token: int, key, result) -> None:
        if token != self._solid_album_palette_active_token:
            return
        self._solid_album_palette_active_token = None
        self._solid_album_palette_worker_key = None
        if self._solid_album_palette_closed:
            return
        if token == self._solid_album_palette_generation and key == self._solid_album_palette_requested_key:
            # Observe actual current values too: a provider can replace art
            # between the last paint and queued GUI delivery.
            current, _image, _primary, _secondary = self._solid_album_palette_request()
            if current == key:
                if result is not None:
                    self._accept_solid_album_palette(key, result)
                    self._solid_album_palette_failed_key = None
                else:
                    self._solid_album_palette_failed_key = key
        if self._solid_album_palette_pending is not None:
            if self._solid_album_palette_pending[0] == self._solid_album_palette_requested_key:
                self._start_solid_album_palette_worker()
            else:
                self._solid_album_palette_pending = None

    def _accept_solid_album_palette(self, key, palette: MusicSurfacePalette) -> None:
        previous = self._displayed_solid_album_palette()
        self._solid_album_palette_cache_key = key
        self._solid_album_palette_cache = palette
        if previous is not None and solid_album_background_color(previous) != solid_album_background_color(palette):
            self._solid_album_palette_blend_from = previous
            self._solid_album_palette_blend_started_at = time.monotonic()
            self._solid_album_palette_blend_timer.start()
        else:
            self._solid_album_palette_blend_from = None
            self._solid_album_palette_blend_timer.stop()
        self.update()

    def _displayed_solid_album_palette(self) -> MusicSurfacePalette | None:
        target = getattr(self, "_solid_album_palette_cache", None)
        previous = getattr(self, "_solid_album_palette_blend_from", None)
        if target is None or previous is None:
            return target
        elapsed = max(0.0, time.monotonic() - self._solid_album_palette_blend_started_at)
        fraction = min(1.0, elapsed / 0.42)
        if fraction >= 1.0:
            self._solid_album_palette_blend_from = None
            return target
        fraction = fraction * fraction * (3.0 - 2.0 * fraction)
        return MusicSurfacePalette(
            self._mix_color(previous.primary, target.primary, fraction),
            self._mix_color(previous.secondary, target.secondary, fraction),
            self._mix_color(previous.characteristic, target.characteristic, fraction),
            target.ambient_colors, target.ambient_weights,
            self._mix_color(solid_album_background_color(previous), solid_album_background_color(target), fraction),
        )

    def _advance_solid_album_palette_blend(self) -> None:
        if self._solid_album_palette_closed or self._solid_album_palette_blend_from is None:
            self._solid_album_palette_blend_timer.stop()
            return
        self._displayed_solid_album_palette()
        self.update()

    def _solid_album_palette_colors(self) -> tuple[QColor, QColor, QColor]:
        palette = self._solid_album_surface_palette()
        return (QColor(palette.primary), QColor(palette.secondary), QColor(palette.characteristic))

    def _solid_album_background_color(self) -> QColor:
        return solid_album_background_color(self._solid_album_surface_palette())

    def _solid_album_unread_color(self, alpha: int) -> QColor:
        return solid_album_ink_color(self._solid_album_background_color(), alpha)

    def _yaoguang_color_usable(self, color: QColor) -> bool:
        if not color.isValid():
            return False
        hue, saturation, value, _alpha = color.getHsvF()
        if hue < 0 or saturation < 0.16:
            return False
        return 0.18 < value < 0.98

    def _yaoguang_color_source_key(self) -> str:
        value = str(getattr(self, "yaoguang_color_source", "custom") or "custom").strip().lower()
        return "album" if value in {"album", "album_color", "cover", "cover_color"} else "custom"

    def _yaoguang_height_mode_key(self) -> str:
        value = str(getattr(self, "yaoguang_height_mode", "regular") or "regular").strip().lower()
        return "spacious" if value == "spacious" else "regular"

    def _yaoguang_album_color_candidate(self) -> QColor:
        album = QColor(getattr(self, "album_color", QColor()))
        if not self._yaoguang_color_usable(album):
            return QColor(str(getattr(self, "yaoguang_outer_color", "#6C35F2")))
        hue, saturation, value, _alpha = album.getHsvF()
        if hue < 0:
            return QColor(str(getattr(self, "yaoguang_outer_color", "#6C35F2")))
        if bool(getattr(self, "yaoguang_album_inverse_enabled", False)):
            hue = (hue + 0.5) % 1.0
        return QColor.fromHsvF(hue, saturation, value, 1.0)

    def _yaoguang_outer_color(self, color: QColor | str | None = None) -> QColor:
        if color is not None:
            candidate = QColor(color)
        elif self._yaoguang_color_source_key() == "album":
            candidate = self._yaoguang_album_color_candidate()
        else:
            candidate = QColor(str(getattr(self, "yaoguang_outer_color", "#6C35F2")))
        if not self._yaoguang_color_usable(candidate):
            candidate = QColor("#6C35F2")
        hue, saturation, value, _alpha = candidate.getHsvF()
        if hue < 0:
            hue = 0.72
        return QColor.fromHsvF(
            hue,
            max(0.68, min(0.96, saturation)),
            max(0.54, min(0.96, value)),
            1.0,
        )

    def _gelv_fill_color(self) -> QColor:
        return self._lyric_fill_color()

    def _wave_fill_color(self) -> QColor:
        return self._lyric_fill_color()

    def _ember_fill_color(self) -> QColor:
        return self._lyric_fill_color()

    def _ember_front_neutral_color(self, alpha: int = 255) -> QColor:
        return self._music_lyric_text_color(alpha)

    def _ember_front_read_color(self, alpha: int = 255) -> QColor:
        alpha = max(0, min(255, int(alpha)))
        if self._ember_color_theme_enabled():
            fill = self._ember_fill_color()
            return QColor(fill.red(), fill.green(), fill.blue(), alpha)
        return self._ember_front_neutral_color(alpha)

    def _ember_back_layer_alpha(self) -> int:
        opacity = max(0, min(100, int(getattr(self, "ember_back_opacity", 58))))
        return max(0, min(255, int(round(255.0 * opacity / 100.0))))

    def _ember_back_layer_color(self, read_color: QColor | None = None, alpha: int | None = None) -> QColor:
        alpha = self._ember_back_layer_alpha() if alpha is None else max(0, min(255, int(alpha)))
        front = QColor(read_color) if read_color is not None else self._ember_front_read_color()
        if not front.isValid():
            front = self._ember_front_neutral_color()
        inverse = QColor(255 - front.red(), 255 - front.green(), 255 - front.blue())
        gray_mix = max(0.0, min(1.0, float(getattr(self, "ember_back_gray_mix", 62)) / 100.0))
        front_is_dark = front.value() < 128
        gray_target = QColor(24, 24, 24) if front_is_dark else QColor(238, 238, 238)
        color = self._mix_color(inverse, gray_target, gray_mix)
        if self._ember_color_back_gray_available():
            color_gray_mix = max(0.0, min(1.0, float(getattr(self, "ember_color_back_gray_mix", 0)) / 100.0))
            color = self._mix_color(color, gray_target, color_gray_mix)
        color.setAlpha(alpha)
        return color

    def _ember_line_progress(self, progress: float, line_count: int) -> float:
        return max(0.0, min(1.0, float(progress)))

    def _should_paint_album_island(self) -> bool:
        if self._empty_music_text_active():
            return False
        if str(getattr(self, "lyric_flow_style", "wave_stream")) == "album_island":
            return True
        if str(getattr(self, "no_lyrics_display_mode", "text")) != "album_island":
            return False
        if self._is_instrumental_track():
            return True
        lyrics = [str(line).strip() for line in getattr(self, "lyrics", []) if str(line).strip()]
        if not lyrics:
            return True
        no_text = str(getattr(self, "no_lyrics_text", "暂无歌词")).strip()
        return len(lyrics) == 1 and lyrics[0] == no_text

    def _album_island_slots(self, rect: QRectF, reserve_text: bool = False) -> list[dict[str, object]]:
        count = int(getattr(self, "album_island_cover_count", 5))
        if count not in (3, 5, 7):
            count = 5
        center = count // 2
        stage = QRectF(rect)
        transition = self._album_island_transition_progress()
        eased_transition = 1.0 - (1.0 - transition) ** 4
        history_entries = self._album_island_entries_with_art(getattr(self, "album_history", []) or [])
        queue_entries = self._album_island_queue_slot_sources(
            getattr(self, "album_queue", []) or getattr(self, "album_recommendations", []) or []
        )
        left_count = min(center, len(history_entries))
        right_count = min(center, len(queue_entries))
        if reserve_text and right_count <= 0:
            text_reserve = min(stage.width() * 0.42, self._u(126.0 if stage.height() < self._u(82.0) else 184.0))
            stage.setRight(max(stage.left() + self._u(76.0), stage.right() - text_reserve))
        cover = self._album_island_cover_px(stage)
        step = self._album_island_step_px(cover, stage, center)
        center_x = stage.center().x()
        center_y = stage.center().y()
        perspective_amount = max(0, min(200, int(getattr(self, "album_island_perspective_amount", 100)))) / 100.0
        sides = list(range(-left_count, 0)) + [0] + list(range(1, right_count + 1))
        slots: list[dict[str, object]] = []
        for side in sides:
            distance = abs(side)
            perspective = None
            scale = max(0.78, 1.0 - 0.08 * distance)
            alpha = max(92, 255 - 48 * distance)
            squeeze = min(0.42, (0.10 + 0.055 * distance) * perspective_amount)
            if side < 0:
                perspective = {"left_height": 1.0, "right_height": 1.0 - squeeze}
            elif side > 0:
                perspective = {"left_height": 1.0 - squeeze, "right_height": 1.0}
            tilt = 0.0
            size = cover * scale
            animated_side = float(side)
            if eased_transition < 1.0:
                animated_side += float(getattr(self, "album_island_transition_direction", 1) or 1) * (1.0 - eased_transition)
            slot_center_x = center_x + animated_side * step
            x = slot_center_x - size / 2.0
            y = center_y - size / 2.0 + distance * stage.height() * 0.020
            if side == 0:
                album_art = QImage(getattr(self, "album_art", QImage()))
                placeholder = False
            elif side < 0:
                album_art = history_entries[abs(side) - 1]
                placeholder = False
            else:
                queue_source = queue_entries[side - 1]
                album_art = QImage(queue_source.get("album_art", QImage()))
                placeholder = bool(queue_source.get("placeholder", False))
            slots.append(
                {
                    "index": side + center,
                    "side": side,
                    "rect": QRectF(x, y, size, size),
                    "alpha": alpha,
                    "center": side == 0,
                    "z": 100 - distance * 12,
                    "tilt": tilt,
                    "perspective": perspective,
                    "album_art": album_art,
                    "placeholder": placeholder,
                }
            )
        if slots:
            leftmost = min(QRectF(slot["rect"]).left() for slot in slots)
            if leftmost < stage.left():
                center_slot = next((slot for slot in slots if bool(slot.get("center"))), None)
                current_center_x = QRectF(center_slot["rect"]).center().x() if center_slot is not None else center_x
                max_shift = max(0.0, rect.center().x() - current_center_x)
                shift = min(stage.left() - leftmost, max_shift)
                if shift > 0.0:
                    for slot in slots:
                        moved = QRectF(slot["rect"])
                        moved.translate(shift, 0.0)
                        slot["rect"] = moved
        return slots

    def _album_island_entries_with_art(self, entries) -> list[QImage]:
        images: list[QImage] = []
        current_signatures = self._current_album_art_signatures()
        seen_signatures: set[str] = set()
        for entry in list(entries or [])[:6]:
            image = self._album_art_from_entry(entry)
            if not image.isNull():
                signatures = self._entry_album_art_signatures(entry, image)
                if signatures & current_signatures:
                    continue
                if signatures:
                    if signatures & seen_signatures:
                        continue
                    seen_signatures.update(signatures)
                images.append(image)
        return images

    def _album_island_queue_entries_with_art(self, entries) -> list[QImage]:
        return [
            QImage(source["album_art"])
            for source in self._album_island_queue_slot_sources(entries)
            if not bool(source.get("placeholder", False))
        ]

    def _album_queue_entry_has_identity(self, entry) -> bool:
        if isinstance(entry, dict):
            values = (
                entry.get("track_id"),
                entry.get("title"),
                entry.get("artist"),
                entry.get("album_title"),
            )
        else:
            values = (
                getattr(entry, "track_id", ""),
                getattr(entry, "title", ""),
                getattr(entry, "artist", ""),
                getattr(entry, "album_title", ""),
            )
        return any(str(value or "").strip() for value in values)

    def _album_island_queue_slot_sources(self, entries) -> list[dict[str, object]]:
        sources: list[dict[str, object]] = []
        current_signatures = self._current_album_art_signatures()
        seen_signatures: set[str] = set()
        for entry in list(entries or [])[:6]:
            if self._entry_matches_current_track(entry):
                continue
            image = self._album_art_from_entry(entry)
            placeholder = image.isNull() and self._album_queue_entry_has_identity(entry)
            if image.isNull() and not placeholder:
                continue
            signatures = self._entry_album_art_signatures(entry, image)
            if signatures & current_signatures:
                continue
            if not placeholder and signatures:
                if signatures & seen_signatures:
                    continue
                seen_signatures.update(signatures)
            sources.append({"album_art": image, "placeholder": placeholder})
        return sources

    def _album_island_cover_px(self, stage: QRectF) -> float:
        expanded_stage = bool(getattr(self, "expanded", False)) or float(stage.height()) >= self._u(86.0)
        if expanded_stage:
            fallback = int(getattr(self, "album_island_cover_size", 30))
            configured = max(36, min(96, int(getattr(self, "album_island_expanded_cover_size", max(72, fallback)))))
        else:
            configured = max(18, min(44, int(getattr(self, "album_island_compact_cover_size", getattr(self, "album_island_cover_size", 30)))))
        return min(self._u(float(configured)), stage.height() * 0.84 if not expanded_stage else stage.height() * 0.92)

    def _album_island_step_px(self, cover: float, stage: QRectF, side_capacity: int) -> float:
        spacing = max(48, min(170, int(getattr(self, "album_island_spacing", 112))))
        desired = cover * (spacing / 100.0)
        max_step = stage.width() / max(1.0, side_capacity * 2.0 + 1.0)
        if self._taskbar_compact_active() and not bool(getattr(self, "expanded", False)):
            return max(cover * 0.58, min(desired, max_step * 1.04))
        return max(cover * 0.72, min(desired, max_step * 1.18))

    def _album_island_transition_progress(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else float(now)
        started_at = float(getattr(self, "album_island_transition_started_at", 0.0) or 0.0)
        if started_at <= 0.0:
            started_at = float(getattr(self, "audio_started_at", 0.0) or 0.0)
        if started_at <= 0.0:
            return 1.0
        return max(0.0, min(1.0, (now - started_at) / 0.62))

    def _album_island_configured_cover_px(self, expanded: bool = False) -> float:
        if expanded:
            fallback = int(getattr(self, "album_island_cover_size", 30))
            configured = max(36, min(96, int(getattr(self, "album_island_expanded_cover_size", max(72, fallback)))))
        else:
            configured = max(18, min(44, int(getattr(self, "album_island_compact_cover_size", getattr(self, "album_island_cover_size", 30)))))
        return self._u(float(configured))

    def _album_island_preferred_stage_width(self, expanded: bool = False) -> float:
        count = int(getattr(self, "album_island_cover_count", 5))
        if count not in (3, 5, 7):
            count = 5
        side_capacity = count // 2
        cover = self._album_island_configured_cover_px(expanded=expanded)
        step = cover * (max(48, min(170, int(getattr(self, "album_island_spacing", 112)))) / 100.0)
        history_count = min(side_capacity, len(self._album_island_entries_with_art(getattr(self, "album_history", []) or [])))
        queue_count = min(side_capacity, len(self._album_island_queue_slot_sources(getattr(self, "album_queue", []) or [])))
        text_w = self._u(184.0 if expanded else 112.0) if queue_count <= 0 else 0.0
        span = cover + step * (history_count + queue_count) + text_w + self._u(24.0)
        minimum = self._u((300.0 if queue_count <= 0 else 240.0) if expanded else (164.0 if queue_count <= 0 else 132.0))
        return max(minimum, span)

    def _album_island_art_for_side(self, side: int, center: int) -> QImage:
        if side == 0:
            return QImage(getattr(self, "album_art", QImage()))
        history = list(getattr(self, "album_history", []) or [])
        queue = list(getattr(self, "album_queue", []) or getattr(self, "album_recommendations", []) or [])
        if side < 0:
            return self._album_art_from_entry(history[abs(side) - 1] if abs(side) - 1 < len(history) else None)
        return self._album_art_from_entry(queue[side - 1] if side - 1 < len(queue) else None)

    def _album_queue_from_track(self, entries) -> list[dict[str, object]]:
        queue: list[dict[str, object]] = []
        current_signatures = self._current_album_art_signatures()
        seen_signatures: set[str] = set()
        for entry in list(entries or [])[:6]:
            if self._entry_matches_current_track(entry):
                continue
            image = self._album_art_from_entry(entry)
            placeholder = image.isNull() and self._album_queue_entry_has_identity(entry)
            if image.isNull() and not placeholder:
                continue
            signatures = self._entry_album_art_signatures(entry, image)
            if signatures & current_signatures:
                continue
            if not placeholder and signatures:
                if signatures & seen_signatures:
                    continue
                seen_signatures.update(signatures)
            if isinstance(entry, dict):
                track_id = str(entry.get("track_id", "") or "")
                title = str(entry.get("title", "") or "")
                artist = str(entry.get("artist", "") or "")
                album_title = str(entry.get("album_title", "") or "")
            else:
                track_id = str(getattr(entry, "track_id", "") or "")
                title = str(getattr(entry, "title", "") or "")
                artist = str(getattr(entry, "artist", "") or "")
                album_title = str(getattr(entry, "album_title", "") or "")
            album_color = self._entry_color_token(entry, "album_color")
            secondary_color = self._entry_color_token(entry, "secondary_color")
            queue.append(
                {
                    "track_id": track_id,
                    "title": title,
                    "artist": artist,
                    "album_title": album_title,
                    "album_art": image,
                    "placeholder": placeholder,
                    "album_art_signature": self._preferred_album_art_signature(signatures),
                    "album_color": album_color,
                    "secondary_color": secondary_color,
                }
            )
        return queue

    def _entry_color_token(self, entry, key: str) -> QColor:
        value = entry.get(key) if isinstance(entry, dict) else getattr(entry, key, None)
        if isinstance(value, QColor):
            return QColor(value)
        if hasattr(value, "rgba"):
            try:
                return QColor(*value.rgba())
            except Exception:
                return QColor()
        if isinstance(value, (tuple, list)) and len(value) >= 3:
            try:
                alpha = int(value[3]) if len(value) >= 4 else 255
                return QColor(int(value[0]), int(value[1]), int(value[2]), alpha)
            except Exception:
                return QColor()
        if isinstance(value, str):
            color = QColor(value)
            return color if color.isValid() else QColor()
        return QColor()

    def _album_queue_signature(self, entries) -> str:
        parts: list[str] = []
        for entry in list(entries or [])[:6]:
            if isinstance(entry, dict):
                values = (
                    entry.get("track_id", ""),
                    entry.get("title", ""),
                    entry.get("artist", ""),
                    entry.get("album_title", ""),
                    self._album_entry_art_signature(entry.get("album_art_bytes") or entry.get("image_bytes")),
                    str(getattr(entry.get("album_art") or entry.get("image"), "cacheKey", lambda: "")()),
                )
            else:
                values = (
                    getattr(entry, "track_id", ""),
                    getattr(entry, "title", ""),
                    getattr(entry, "artist", ""),
                    getattr(entry, "album_title", ""),
                    self._album_entry_art_signature(getattr(entry, "album_art_bytes", b"") or getattr(entry, "image_bytes", b"")),
                    str(getattr(getattr(entry, "album_art", None), "cacheKey", lambda: "")()),
                )
            parts.append("\0".join(str(value or "") for value in values))
        return "\1".join(parts)

    def _album_entry_art_signature(self, data) -> str:
        if isinstance(data, bytes) and data:
            return self._album_art_bytes_signature(data)
        return ""

    def _album_art_bytes_signature(self, data) -> str:
        return album_art_bytes_signature(data)

    def _preferred_album_art_signature(self, signatures: set[str]) -> str:
        return next(iter(sorted(signature for signature in signatures if signature)), "")

    def _current_album_art_signatures(self) -> set[str]:
        signatures: set[str] = set()
        stored = str(getattr(self, "album_art_signature", "") or "")
        if stored:
            signatures.add(stored)
        image_signature = self._album_image_content_signature(QImage(getattr(self, "album_art", QImage())))
        if image_signature:
            signatures.add(image_signature)
        return signatures

    def _entry_album_art_signatures(self, entry, image: QImage | None = None) -> set[str]:
        signatures: set[str] = set()
        stored = ""
        data = b""
        if isinstance(entry, dict):
            stored = str(entry.get("album_art_signature", "") or entry.get("image_signature", "") or "")
            data = entry.get("album_art_bytes") or entry.get("image_bytes") or b""
        else:
            stored = str(getattr(entry, "album_art_signature", "") or getattr(entry, "image_signature", "") or "")
            data = getattr(entry, "album_art_bytes", b"") or getattr(entry, "image_bytes", b"") or b""
        if stored:
            signatures.add(stored)
        byte_signature = self._album_entry_art_signature(data)
        if byte_signature:
            signatures.add(byte_signature)
        if isinstance(image, QImage) and not image.isNull():
            image_signature = self._album_image_content_signature(image)
            if image_signature:
                signatures.add(image_signature)
        return signatures

    def _album_image_content_signature(self, image: QImage) -> str:
        if not isinstance(image, QImage) or image.isNull():
            return ""
        cache_key = int(image.cacheKey())
        cache = getattr(self, "_album_image_signature_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._album_image_signature_cache = cache
        cached = cache.get(cache_key)
        if isinstance(cached, str) and cached:
            return cached
        scaled = image.convertToFormat(QImage.Format.Format_ARGB32).scaled(
            16,
            16,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        digest = hashlib.blake2b(digest_size=8)
        digest.update(bytes((scaled.width() & 0xFF, scaled.height() & 0xFF)))
        for y in range(scaled.height()):
            for x in range(scaled.width()):
                color = QColor(scaled.pixel(x, y))
                digest.update(bytes((color.red(), color.green(), color.blue(), color.alpha())))
        signature = digest.hexdigest()
        cache[cache_key] = signature
        while len(cache) > 48:
            cache.pop(next(iter(cache)))
        return signature

    def _album_art_from_entry(self, entry) -> QImage:
        if isinstance(entry, QImage):
            return self._bounded_album_image(entry, self.STORED_ALBUM_ART_MAX_SIDE)
        if isinstance(entry, dict):
            image = entry.get("album_art") or entry.get("image")
            if isinstance(image, QImage):
                return self._bounded_album_image(image, self.STORED_ALBUM_ART_MAX_SIDE)
            data = entry.get("album_art_bytes") or entry.get("image_bytes")
            if isinstance(data, bytes):
                loaded = QImage()
                if loaded.loadFromData(data) and not loaded.isNull():
                    return self._bounded_album_image(loaded, self.STORED_ALBUM_ART_MAX_SIDE)
        image = getattr(entry, "album_art", None)
        if isinstance(image, QImage):
            return self._bounded_album_image(image, self.STORED_ALBUM_ART_MAX_SIDE)
        data = getattr(entry, "album_art_bytes", b"") or getattr(entry, "image_bytes", b"") or b""
        if isinstance(data, bytes) and data:
            loaded = QImage()
            if loaded.loadFromData(data) and not loaded.isNull():
                return self._bounded_album_image(loaded, self.STORED_ALBUM_ART_MAX_SIDE)
        return QImage()

    def _album_island_listened_text(self) -> str:
        seconds = max(0.0, float(getattr(self, "listened_seconds_total", 0.0)))
        hours = int(seconds / 3600.0)
        return f"您已听歌 {hours} 小时"

    def _serializable_album_history(self) -> list[dict[str, str]]:
        history: list[dict[str, str]] = []
        for entry in list(getattr(self, "album_history", []) or [])[:6]:
            if isinstance(entry, dict):
                history.append(
                    {
                        "track_id": str(entry.get("track_id", "") or ""),
                        "title": str(entry.get("title", "") or ""),
                        "artist": str(entry.get("artist", "") or ""),
                        "album_title": str(entry.get("album_title", "") or ""),
                    }
                )
            else:
                history.append(
                    {
                        "track_id": str(getattr(entry, "track_id", "") or ""),
                        "title": str(getattr(entry, "title", "") or ""),
                        "artist": str(getattr(entry, "artist", "") or ""),
                        "album_title": str(getattr(entry, "album_title", "") or ""),
                    }
                )
        return history

    def _current_album_history_entry(self) -> dict[str, object]:
        return {
            "track_id": str(getattr(self, "media_track_id", "") or ""),
            "title": str(getattr(self, "track_title", "") or ""),
            "artist": str(getattr(self, "artist", "") or ""),
            "album_art": self._bounded_album_image(getattr(self, "album_art", QImage()), self.STORED_ALBUM_ART_MAX_SIDE),
            "album_color": QColor(getattr(self, "album_color", QColor())),
            "secondary_color": QColor(getattr(self, "secondary_color", QColor())),
        }

    def _entry_matches_track_id(self, entry, track_id: str) -> bool:
        candidate = ""
        if isinstance(entry, dict):
            candidate = str(entry.get("track_id", "") or "")
        else:
            candidate = str(getattr(entry, "track_id", "") or "")
        return bool(candidate) and candidate == str(track_id or "")

    def _entry_matches_current_track(self, entry) -> bool:
        current_track_id = str(getattr(self, "media_track_id", "") or "")
        if current_track_id and self._entry_matches_track_id(entry, current_track_id):
            return True
        if isinstance(entry, dict):
            title = str(entry.get("title", "") or "")
            artist = str(entry.get("artist", "") or "")
        else:
            title = str(getattr(entry, "title", "") or "")
            artist = str(getattr(entry, "artist", "") or "")
        current_title = str(getattr(self, "track_title", "") or "")
        current_artist = str(getattr(self, "artist", "") or "")
        if not title or not current_title:
            return False
        return (
            self._normalized_track_identity(title) == self._normalized_track_identity(current_title)
            and self._normalized_track_identity(artist) == self._normalized_track_identity(current_artist)
        )

    def _normalized_track_identity(self, value: str) -> str:
        text = unicodedata.normalize("NFKC", str(value or "")).casefold()
        return re.sub(r"[\s\-_./\\|·・•:：;；,，'\"“”‘’()（）\[\]【】]+", "", text)

    def _prepare_album_flow_for_track_change(self, incoming_track_id: str) -> int:
        incoming_track_id = str(incoming_track_id or "")
        history = list(getattr(self, "album_history", []) or [])
        queue = list(getattr(self, "album_queue", []) or [])
        previous_id = str(getattr(self, "media_track_id", "") or "")
        if previous_id and incoming_track_id and previous_id != incoming_track_id:
            queue_index = next(
                (index for index, entry in enumerate(queue) if self._entry_matches_track_id(entry, incoming_track_id)),
                -1,
            )
            history_index = next(
                (index for index, entry in enumerate(history) if self._entry_matches_track_id(entry, incoming_track_id)),
                -1,
            )
            current_entry = self._current_album_history_entry()
            if queue_index >= 0:
                self._begin_music_surface_transition(queue[queue_index], direction=1, mode="queued")
            if history_index >= 0:
                self._begin_music_surface_transition(history[history_index], direction=-1, mode="history")
                history.pop(history_index)
                queue = [current_entry] + [entry for entry in queue if not self._entry_matches_track_id(entry, previous_id)]
                self.album_history = history[:6]
                self.album_queue = queue[:6]
                return -1
            if queue_index < 0:
                self._begin_music_surface_transition(None, direction=1, mode="white_bridge")
            history.insert(0, current_entry)
        self.album_history = history[:6]
        return 1

    def _remember_album_history(self, incoming_track_id: str):
        history = list(getattr(self, "album_history", []))
        previous_id = str(getattr(self, "media_track_id", "") or "")
        if previous_id and previous_id != str(incoming_track_id or ""):
            history.insert(
                0,
                {
                    "track_id": previous_id,
                    "title": str(getattr(self, "track_title", "") or ""),
                    "album_art": self._bounded_album_image(getattr(self, "album_art", QImage()), self.STORED_ALBUM_ART_MAX_SIDE),
                },
            )
        self.album_history = history[:6]

    def _island_display_row_index(self) -> int:
        lyrics = getattr(self, "lyrics", ()) or ()
        index = int(getattr(self, "lyric_index", 0))
        if getattr(self, "_island_lyric_frame_at", None) is not None:
            phase = self._island_display_phase()
            index = int(phase.get("visual_focus_index", phase.get("active_index", index)))
        return index % len(lyrics) if lyrics else 0

    def _current_lyric_display_lines(self) -> list[str]:
        if not self.lyrics:
            return []
        return self._lyric_display_lines(self.lyrics[self._island_display_row_index()])

    def _has_current_two_line_lyric(self) -> bool:
        return len(self._current_lyric_display_lines()) > 1

    def _clean_lyric_for_width(self, lyric: str) -> str:
        noise_tokens = ["作词", "作曲", "编曲", "制作", "混音", "母带", "吉他", "贝斯", "鼓", "小提琴", "和声", "录音"]
        for token in noise_tokens:
            if token in lyric:
                lyric = lyric.split(token, 1)[0]
        return "\n".join(" ".join(part.strip(" -—·:：[]【】()（）").split()) for part in str(lyric).splitlines()).strip()

    def _stack_target_width(self, main_kind: IslandKind) -> float:
        base = positioning_stack_target_width(
            main_kind,
            music_width=self._music_target_width(self.expanded) if main_kind == IslandKind.MUSIC else 0.0,
            empty_text_width=self._empty_text_island_size()[0] if main_kind == IslandKind.EMPTY_TEXT else 0.0,
            empty_time_width=self._empty_time_island_size()[0] if main_kind == IslandKind.TIME else 0.0,
            default_stack_width=float(self.STACK_WIDTH),
            unit=self._u(1.0),
        )
        if main_kind in {IslandKind.EMPTY_TEXT, IslandKind.TIME, IslandKind.THIN_BAR, IslandKind.PLUGIN}:
            return self._audio_device_target_width(base)
        return base

    def _taskbar_compact_active(self) -> bool:
        return str(getattr(self, "location_mode", "free") or "free") == "tasker"

    def _taskbar_edge_compact_active(self) -> bool:
        return str(getattr(self, "location_mode", "free") or "free") in {"tasker", "stick"}

    def _taskbar_uses_expanded_material_frame(self) -> bool:
        return (
            str(getattr(self, "location_mode", "free") or "free") == "tasker"
            and bool(getattr(self, "expanded", False))
            and not bool(getattr(self, "finder_mode", False))
        )

    def _taskbar_shell_remains_opaque(self) -> bool:
        return True

    def _taskbar_panel_corner_policy(self) -> str:
        return "square" if str(getattr(self, "taskbar_corner_style", "win11") or "win11") == "win10" else "small-round"

    def _taskbar_height(self) -> float:
        compact = self._taskbar_edge_compact_active()
        if compact:
            candidates = [
                QRectF(getattr(self, "taskbar_follow_base_rect", QRectF())),
                QRectF(getattr(self, "last_taskbar_rect", QRectF())),
                QRectF(getattr(self, "taskbar_selected_drop_zone", QRectF())),
            ]
            try:
                screen = QGuiApplication.screenAt(self.mapToGlobal(QPoint(self.width() // 2, self.height()))) or QGuiApplication.primaryScreen()
                candidates.append(self._taskbar_drag_reference_rect_for_screen(screen))
            except Exception:
                pass
            height = taskbar_compact_height_from_rects(
                candidates,
                compact_min=self._u(40.0),
                min_candidate=self._u(12.0),
            )
            if height > 0.0:
                return height
        screen = QGuiApplication.screenAt(self.mapToGlobal(QPoint(self.width() // 2, self.height()))) or QGuiApplication.primaryScreen()
        if screen is None:
            return self._u(48.0)
        taskbar = self._taskbar_drag_reference_rect_for_screen(screen) if compact else self._taskbar_rect_for_screen(screen)
        return taskbar_height_from_rect(taskbar, compact=compact, unit=self._u(1.0))

    def _taskbar_corner_radius(self, taskbar_height: float, style: str | None = None) -> float:
        style = str(style or getattr(self, "taskbar_corner_style", "win11") or "win11").strip().lower()
        return positioning_taskbar_corner_radius(taskbar_height, style, unit=self._u(1.0))

    def _taskbar_default_music_width(self) -> float:
        return self._u(420.0)

    def _taskbar_compact_main_height(self, taskbar_rect: QRectF | None = None, fallback: float | None = None) -> float:
        if fallback is not None:
            return positioning_taskbar_compact_main_height(
                taskbar_rect,
                fallback=fallback,
                unit=self._u(1.0),
            )
        current_height = None
        try:
            current_height = float(self._taskbar_height())
        except Exception:
            current_height = None
        return positioning_taskbar_compact_main_height(
            taskbar_rect,
            current_taskbar_height=current_height,
            unit=self._u(1.0),
        )

    def _taskbar_full_compact_height_for_rect(self, taskbar_rect: QRectF, fallback: float) -> float:
        return positioning_taskbar_full_compact_height_for_rect(taskbar_rect, fallback)

    def _taskbar_width_with_manual(self, default_width: float) -> float:
        return positioning_taskbar_width_with_manual(
            default_width,
            compact_active=self._taskbar_compact_active(),
            manual_width=getattr(self, "taskbar_manual_width", 0.0),
            unit=self._u(1.0),
        )

    def _taskbar_compact_music_size(
        self,
        base_width: float,
        taskbar_height: float,
        selected_zone: QRectF | None = None,
        fill_height: bool = False,
    ) -> tuple[float, float]:
        selected = QRectF(getattr(self, "taskbar_selected_drop_zone", QRectF()) if selected_zone is None else selected_zone)
        return positioning_taskbar_compact_music_size(
            taskbar_height=taskbar_height,
            default_music_width=self._taskbar_default_music_width(),
            selected_zone=selected,
            compact_active=self._taskbar_compact_active(),
            manual_width=getattr(self, "taskbar_manual_width", 0.0),
            unit=self._u(1.0),
        )

    def _main_size_for_taskbar_anchor(self, main_kind: IslandKind, stack_w: float, taskbar_rect: QRectF, fill_height: bool = False) -> tuple[float, float]:
        compact_music_size = None
        base_size = None
        expanded = bool(getattr(self, "expanded", False))
        if main_kind == IslandKind.MUSIC and not expanded:
            taskbar = QRectF(taskbar_rect)
            taskbar_height = min(float(taskbar.width()), float(taskbar.height())) if not taskbar.isNull() else self._u(48.0)
            compact_music_size = self._taskbar_compact_music_size(stack_w, taskbar_height, selected_zone=QRectF(), fill_height=fill_height)
        else:
            base_size = self._main_size_for_kind(main_kind, stack_w)
        selected = positioning_taskbar_anchor_main_size(
            main_kind,
            expanded=expanded,
            base_size=base_size,
            compact_music_size=compact_music_size,
            taskbar_rect=taskbar_rect,
            unit=self._u(1.0),
        )
        if selected is not None:
            return selected
        return self._main_size_for_kind(main_kind, stack_w)

    def _taskbar_cover_only_music_active(self, rect: QRectF | None = None) -> bool:
        return positioning_taskbar_cover_only_music_active(
            compact_active=self._taskbar_compact_active(),
            selected_zone=QRectF(getattr(self, "taskbar_selected_drop_zone", QRectF())),
            rect=rect,
            default_music_width=self._taskbar_default_music_width(),
            unit=self._u(1.0),
        )

    def _music_shell_radius(self, rect: QRectF, expanded: bool) -> float:
        if self._taskbar_compact_active():
            try:
                taskbar_height = self._taskbar_height()
            except Exception:
                taskbar_height = rect.height()
            return self._taskbar_corner_radius(min(float(rect.height()), float(taskbar_height)))
        return self._u(30.0) if expanded else rect.height() / 2.0

    def _taskbar_synced_corner_radius(self, rect: QRectF) -> float:
        try:
            taskbar_height = self._taskbar_height()
        except Exception:
            taskbar_height = rect.height()
        return positioning_taskbar_synced_corner_radius(
            rect,
            taskbar_height=taskbar_height,
            style=getattr(self, "taskbar_corner_style", "win11"),
            unit=self._u(1.0),
        )

    def _plugin_panel_shell_radius(self, rect: QRectF) -> float:
        if self._taskbar_compact_active():
            return self._taskbar_synced_corner_radius(rect)
        return min(self._u(26.0), rect.height() / 4.0)

    def _plugin_panel_module_radius(self, rect: QRectF, fallback: float) -> float:
        if self._taskbar_compact_active():
            return self._taskbar_synced_corner_radius(rect)
        return float(fallback)

    def _current_screen_geometry(self):
        screen = QGuiApplication.screenAt(self.mapToGlobal(QPoint(self.width() // 2, self.height() // 2))) or QGuiApplication.primaryScreen()
        return screen.geometry() if screen else None

    def _expanded_stack_height(self, main_kind: IslandKind, main_h: float, include_popups: bool) -> float:
        panel_visible = self._plugin_panel_visible() if bool(getattr(self, "expanded", False)) and self._plugin_enabled() else False
        panel_height = 0.0
        if panel_visible and main_kind in (IslandKind.MUSIC, IslandKind.PLUGIN):
            panel_height = self._plugin_panel_height(self._stack_target_width(main_kind))
        folded_message_count = 0
        if main_kind == IslandKind.MESSAGE:
            folded_message_count = max(0, len(self.messages[1 : 1 + self._folded_message_limit(main_kind)]))
        popup_count = 0
        if include_popups and main_kind != IslandKind.MESSAGE:
            now = time.monotonic()
            popup_count = min(1, len([m for m in self.messages if now < m.popup_until]))
        return positioning_expanded_stack_height(
            main_kind,
            main_h,
            expanded=bool(getattr(self, "expanded", False)),
            include_popups=include_popups,
            plugin_enabled=self._plugin_enabled(),
            plugin_panel_visible=panel_visible,
            plugin_panel_height=panel_height,
            message_count=len(self.messages),
            folded_message_count=folded_message_count,
            popup_count=popup_count,
            unit=self._u(1.0),
        )

    def _standalone_width_limit(self, center_x: float, screen_geo) -> float:
        target_width = self._stack_target_width(self._main_kind())
        if not screen_geo:
            return target_width
        global_center_x = self.mapToGlobal(QPoint(int(round(center_x)), 0)).x()
        return self._standalone_width_limit_for_global_center(global_center_x, screen_geo, target_width)

    def _standalone_width_limit_for_global_center(self, global_center_x: float, screen_geo, target_width: float | None = None) -> float:
        if target_width is None:
            target_width = self._stack_target_width(self._main_kind())
        return standalone_width_limit_for_global_center(
            global_center_x,
            screen_geo,
            target_width,
            min_width=self._u(120.0),
            side_margin=self._u(28.0),
        )

    def _empty_bar_orientation(self) -> str:
        return positioning_empty_bar_orientation(str(getattr(self, "location_mode", "") or ""))

    def _empty_bar_size(self) -> tuple[float, float]:
        return positioning_empty_bar_size(orientation=self._empty_bar_orientation(), unit=self._u(1.0))

    def _color_luminance(self, color: QColor) -> float:
        if color is None or not color.isValid():
            return 0.0
        return 0.2126 * color.redF() + 0.7152 * color.greenF() + 0.0722 * color.blueF()

    def _empty_bar_surrounding_is_light(self) -> bool:
        base = QColor()
        if self._empty_bar_orientation() == "vertical":
            base = QColor(getattr(self, "taskbar_material_color", QColor()))
            if base.isValid():
                return bool(getattr(self, "taskbar_material_is_light", False)) or self._color_luminance(base) >= 0.62
        finder_material = getattr(self, "finder_material", None)
        if bool(getattr(self, "finder_mode", False)) and finder_material is not None:
            candidates = [
                QColor(getattr(finder_material, "mid_color", QColor())),
                QColor(getattr(finder_material, "top_color", QColor())),
                QColor(getattr(finder_material, "bottom_color", QColor())),
            ]
            valid = [color for color in candidates if color.isValid()]
            if valid:
                return sum(self._color_luminance(color) for color in valid) / len(valid) >= 0.62
        return not self._system_dark_mode()

    def _empty_bar_color(self) -> QColor:
        return QColor(30, 32, 36) if self._empty_bar_surrounding_is_light() else QColor(246, 248, 252)

    def _empty_bar_alpha(self) -> int:
        return 214

    def _empty_text_island_font(self) -> QFont:
        return self._font(14, QFont.Weight.Bold, shrink=False)

    def _empty_time_island_font(self) -> QFont:
        return self._font(15, QFont.Weight.Bold, shrink=False)

    def _empty_text_island_colors(self) -> tuple[QColor, QColor]:
        return QColor(8, 9, 11), QColor(246, 248, 252)

    def _empty_time_island_colors(self) -> tuple[QColor, QColor]:
        return QColor(8, 9, 11), QColor(246, 248, 252)

    def _empty_text_island_size(self, max_width: float | None = None) -> tuple[float, float]:
        text = self._empty_text_display_text()
        metrics = QFontMetrics(self._empty_text_island_font())
        width = float(metrics.horizontalAdvance(text)) + self._u(36.0)
        return max(self._u(56.0), width), self._u(40.0)

    def _empty_time_island_size(self, max_width: float | None = None) -> tuple[float, float]:
        text = self._empty_time_text()
        metrics = QFontMetrics(self._empty_time_island_font())
        width = float(metrics.horizontalAdvance(text)) + self._u(34.0)
        return max(self._u(74.0), width), self._u(40.0)

    def _compact_music_content_height_raw(self) -> float:
        if self._personal_dj_island_presenting():
            return 112.0
        if self._should_paint_album_island():
            return self._album_island_compact_music_height()
        two_line = bool(getattr(self, "lyrics", None)) and self._has_current_two_line_lyric()
        height = 80.0 if two_line else 46.0
        lines = self._current_lyric_display_lines() if getattr(self, "lyrics", None) else ()
        if lines:
            size = self._compact_primary_lyric_font_size()
            if two_line:
                secondary_bold = int(getattr(self, "dual_line_secondary_weight", 72)) >= 78
                primary_size, secondary_size = self._compact_dual_line_font_sizes(size, secondary_bold)
                content_height = (
                    self._island_lyric_row_content_height(lines[0], primary_size, True)
                    + self._u(1.0)
                    + self._island_lyric_row_content_height(lines[1], secondary_size, secondary_bold)
                    + self._u(4.0)
                )
            else:
                content_height = self._island_lyric_row_content_height(lines[0], size, True)
            top, bottom = self._compact_lyric_vertical_padding(two_line)
            height = max(height, (content_height + top + bottom) / self._u(1.0))
        if (
            self._effective_music_ambience_style() == "yaoguang"
            and self._yaoguang_height_mode_key() == "spacious"
            and not self._taskbar_compact_active()
        ):
            height += 10.0
        return height

    def _notch_music_needs_content_height(self) -> bool:
        if self._personal_dj_island_presenting():
            return True
        if self._should_paint_album_island():
            return True
        return bool(getattr(self, "lyrics", None)) and (
            self._has_current_two_line_lyric() or self._compact_music_content_height_raw() > 46.0
        )

    def _edge_notch_music_height_raw(self) -> float:
        height = self._compact_music_content_height_raw()
        mode = self._normalize_top_notch_height_mode(getattr(self, "top_notch_height_mode", "standard"))
        if mode == "compact":
            return max(36.0, height - 10.0)
        if mode == "standard":
            return height if self._notch_music_needs_content_height() else max(40.0, height - 6.0)
        if mode == "spacious":
            return height + 8.0
        return height

    def _top_notch_music_height_raw(self) -> float:
        return self._edge_notch_music_height_raw()

    def _stick_notch_music_height(self) -> float:
        return self._u(self._edge_notch_music_height_raw())

    def _main_size_for_kind(self, main_kind: IslandKind, stack_w: float) -> tuple[float, float]:
        width, height = self._base_main_size_for_kind(main_kind, stack_w)
        pose = self._audio_device_pose()
        if pose is not None and main_kind in {IslandKind.EMPTY_TEXT, IslandKind.TIME, IslandKind.THIN_BAR, IslandKind.PLUGIN}:
            # Keep the existing main identity and SpringRect; reminders retain
            # their priority. Tasker never grows beyond its selected zone.
            if self._taskbar_compact_active():
                width, _unused = self._taskbar_compact_music_size(stack_w, self._taskbar_height())
            else:
                width = min(stack_w, self._audio_device_target_width(width))
                height += (max(height, self._u(34)) - height) * pose.reveal
        return width, height

    def _base_main_size_for_kind(self, main_kind: IslandKind, stack_w: float) -> tuple[float, float]:
        taskbar_compact = self._taskbar_compact_active()
        if main_kind == IslandKind.MUSIC and self._personal_dj_island_presenting() and not taskbar_compact:
            # The existing SpringRect grows the island itself; DJ never adds
            # a nested card. Tasker retains its physical taskbar bounds.
            return stack_w, self._u(112.0)
        expanded = bool(getattr(self, "expanded", False)) and not self._drag_hides_auxiliary_visuals()
        unit = self._u(1.0)
        compact_main_height = self._taskbar_compact_main_height() if taskbar_compact else self._u(48.0)
        compact_music_size = None
        music_style = "standard"
        stick_notch_height = 0.0
        top_notch_height = 0.0
        compact_music_height = 0.0
        empty_text_size = (0.0, 0.0)
        empty_time_size = (0.0, 0.0)
        empty_bar_size = (0.0, 0.0)

        if main_kind == IslandKind.MUSIC:
            if expanded:
                music_style = self._normalize_music_expanded_layout_style(getattr(self, "music_expanded_layout_style", "standard"))
            elif taskbar_compact:
                compact_music_size = self._taskbar_compact_music_size(stack_w, self._taskbar_height())
            elif self._stick_notch_active():
                stick_notch_height = self._stick_notch_music_height()
            elif self._top_notch_active():
                top_notch_height = self._u(self._top_notch_music_height_raw())
            else:
                compact_music_height = self._u(self._compact_music_content_height_raw())
        elif main_kind == IslandKind.EMPTY_TEXT:
            empty_text_size = self._empty_text_island_size(stack_w)
        elif main_kind == IslandKind.TIME:
            empty_time_size = self._empty_time_island_size(stack_w)
        elif main_kind not in {IslandKind.PLUGIN, IslandKind.MESSAGE}:
            empty_bar_size = self._empty_bar_size()

        return positioning_main_size_for_kind(
            main_kind,
            MainSizeFacts(
                stack_width=stack_w,
                expanded=expanded,
                taskbar_compact=taskbar_compact,
                music_expanded_layout_style=music_style,
                taskbar_compact_music_size=compact_music_size,
                stick_notch_active=self._stick_notch_active() if main_kind == IslandKind.MUSIC else False,
                stick_notch_music_height=stick_notch_height,
                top_notch_active=self._top_notch_active() if main_kind == IslandKind.MUSIC else False,
                top_notch_music_height=top_notch_height,
                compact_music_content_height=compact_music_height,
                enabled_plugin_slot_count=self._enabled_plugin_slot_count() if main_kind == IslandKind.PLUGIN else 1,
                taskbar_compact_main_height=compact_main_height,
                empty_text_size=empty_text_size,
                empty_time_size=empty_time_size,
                empty_bar_size=empty_bar_size,
                manual_width=getattr(self, "taskbar_manual_width", 0.0),
                unit=unit,
            ),
        )

    def _time_panel_breakpoint(self) -> float:
        return self._u(560.0)

    def _time_panel_stretch_breakpoint(self) -> float:
        return self._u(960.0)

    def _time_panel_modules(self) -> list[str]:
        return ["countdown", "alarm"]

    def _time_visible_module_for_width(self, width: float) -> str:
        return positioning_time_visible_module_for_width(
            width,
            str(getattr(self, "plugin_time_visible_module", "countdown") or "countdown"),
            breakpoint=self._time_panel_breakpoint(),
            modules=tuple(self._time_panel_modules()),
        )

    def _plugin_panel_height(self, width: float | None = None) -> float:
        return positioning_plugin_panel_height(
            width,
            time_mode=str(getattr(self, "plugin_time_mode", "dashboard") or "dashboard"),
            last_width=float(getattr(self, "plugin_panel_last_width", 0.0) or 0.0),
            breakpoint=self._time_panel_breakpoint(),
            unit=self._u(1.0),
        )

    def _plugin_panel_visible(self) -> bool:
        if self._empty_time_mode_active():
            return False
        return bool(self.expanded and self._plugin_enabled() and getattr(self, "plugin_panel_open", False))

    def _main_top_for_direction(self, main_kind: IslandKind, main_h: float, direction: int) -> float:
        return standalone_main_top_for_direction(
            finder_stack_top_y=self._finder_stack_top_y(),
            window_height=float(self.height()),
            main_height=float(main_h),
            direction=int(direction),
            finder_mode=bool(getattr(self, "finder_mode", False)),
            thin_bar=main_kind == IslandKind.THIN_BAR,
            thin_bar_top_lift=self._u(2.0) + 2.0,
        )

    def _standalone_drag_direction(self, global_pos: QPoint) -> int:
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        try:
            screen_geo = screen.geometry() if screen is not None else QRectF()
        except Exception:
            screen_geo = QRectF()
        plan = build_standalone_drag_direction_plan(
            expand_direction=str(getattr(self, "expand_direction", "auto") or "auto"),
            auto_expand_bounds=bool(getattr(self, "auto_expand_bounds", True)),
            global_pos=global_pos,
            screen_geometry=screen_geo,
            dragging=bool(getattr(self, "dragging", False)),
            drag_candidate=bool(getattr(self, "drag_candidate", False)),
            current_auto_direction=int(getattr(self, "drag_auto_stack_direction", 0) or 0),
            current_stack_direction=int(getattr(self, "stack_direction", 1) or 1),
            unit=self._u(1.0),
        )
        if plan.update_auto_direction:
            self.drag_auto_stack_direction = int(plan.auto_direction)
        return int(plan.direction)

    def _drag_hides_auxiliary_visuals(self) -> bool:
        if not bool(getattr(self, "dragging", False)):
            return False
        return str(getattr(self, "drag_press_key", "") or "") != "taskbar:left-handle"

    def _vertical_stack_direction(self, main_kind: IslandKind, main_h: float, screen_geo) -> int:
        if self._top_notch_anchor_locked():
            return 1
        taskbar_compact = self._taskbar_compact_active()
        taskbar = QRectF(getattr(self, "last_taskbar_rect", QRectF())) if taskbar_compact else QRectF()
        mode = str(getattr(self, "expand_direction", "auto") or "auto")
        auto_bounds = bool(getattr(self, "auto_expand_bounds", True))
        global_main_center_y = None
        global_main_top = None
        expanded_down_height = None
        try:
            screen = QRectF(screen_geo)
        except Exception:
            screen = QRectF()
        if (
            not bool(getattr(self, "finder_mode", False))
            and not taskbar_compact
            and mode.strip().lower() not in {"down", "up"}
            and auto_bounds
            and not screen.isNull()
        ):
            origin_y = self.mapToGlobal(QPoint(0, 0)).y()
            local_top = self._main_top_for_direction(main_kind, main_h, getattr(self, "stack_direction", 1))
            global_main_center_y = origin_y + local_top + main_h / 2.0
            if bool(getattr(self, "expanded", False)):
                expanded_down_height = self._expanded_stack_height(main_kind, main_h, include_popups=False)
                global_main_top = origin_y + self._main_top_for_direction(main_kind, main_h, 1)
        return build_standalone_stack_direction_plan(
            finder_mode=bool(getattr(self, "finder_mode", False)),
            taskbar_compact_active=taskbar_compact,
            taskbar_rect=taskbar,
            current_stack_direction=int(getattr(self, "stack_direction", 1) or 1),
            expand_direction=mode,
            auto_expand_bounds=auto_bounds,
            screen_geometry=screen_geo,
            global_main_center_y=global_main_center_y,
            expanded=bool(getattr(self, "expanded", False)),
            global_main_top=global_main_top,
            expanded_down_height=expanded_down_height,
            bottom_margin=self._u(14.0),
        )

    def _stack_rect_for_direction(self, center_x: float, cursor_edge: float, width: float, height: float, direction: int, gap: float) -> tuple[QRectF, float]:
        return stack_rect_for_direction(
            center_x=center_x,
            cursor_edge=cursor_edge,
            width=width,
            height=height,
            direction=direction,
            gap=gap,
        )

    def _finder_content_gap(self) -> float:
        return max(self._u(6.0), min(self._u(8.0), self.finder_material.bar_height * 0.18 * self.VISUAL_SCALE))

    def _finder_stack_top_y(self) -> float:
        if not self.finder_mode:
            return self._u(72.0)
        return self._finder_content_gap()

    def _stack_center_x(self, content_width: float) -> float:
        return standalone_stack_center_x(
            finder_mode=bool(getattr(self, "finder_mode", False)),
            window_width=float(self.width()),
            content_width=float(content_width),
            center_ratio=float(getattr(self, "stack_center_ratio", 0.5)),
            unit=self._u(1.0),
        )

    def _build_visuals(self) -> list[StackVisual]:
        now = time.monotonic()
        main_kind = self._main_kind()
        screen_geo = self._current_screen_geometry()
        target_stack_w = self._stack_target_width(main_kind)
        stack_w = target_stack_w
        center_x = self._stack_center_x(stack_w)
        if not self._taskbar_compact_active():
            for _pass in range(2):
                stack_w = standalone_stack_width_for_limit(
                    stack_w,
                    self._standalone_width_limit(center_x, screen_geo),
                    min_width=self._u(120.0),
                )
                center_x = self._stack_center_x(stack_w)
        self.font_shrink_scale = 1.0
        gap = self._u(10)
        visuals: list[StackVisual] = []
        _, direction_probe_h = self._main_size_for_kind(main_kind, stack_w)
        direction = self._vertical_stack_direction(main_kind, direction_probe_h, screen_geo)
        if self._drag_hides_auxiliary_visuals():
            locked_direction = int(getattr(self, "drag_auto_stack_direction", 0) or getattr(self, "stack_direction", direction) or direction)
            if locked_direction in (-1, 1):
                direction = locked_direction
        self.stack_direction = direction
        main_w, main_h = self._main_size_for_kind(main_kind, stack_w)

        main_key, message = stack_main_visual_identity(main_kind, self.messages, self._message_key)

        main_top = self._main_top_for_direction(main_kind, main_h, direction)
        main_rect = QRectF(center_x - main_w / 2, main_top, main_w, main_h)
        visuals.append(StackVisual(main_key, main_kind, main_rect, False, message, 10))
        cursor_edge = main_rect.bottom() + gap if direction >= 0 else main_rect.top() - gap
        if self._drag_hides_auxiliary_visuals():
            return visuals

        taskbar_side_notifications = self._taskbar_compact_active() and not self.expanded
        taskbar_overlay_alert = None if taskbar_side_notifications else self._taskbar_main_overlay_alert()
        overlay_plugin_id = ""
        if taskbar_overlay_alert is not None:
            try:
                overlay_plugin_id = str(taskbar_overlay_alert[6] or "time")
            except (TypeError, IndexError):
                overlay_plugin_id = "time"
            visuals.append(StackVisual(f"taskbar-overlay:{overlay_plugin_id}", IslandKind.PLUGIN_ALERT, self._taskbar_main_overlay_rect(main_rect, taskbar_overlay_alert), False, None, 12))

        notification_limit = 3 if taskbar_side_notifications else 2
        plugin_alerts = self._active_plugin_alerts(notification_limit)
        if overlay_plugin_id:
            overlay_base = overlay_plugin_id.split(":", 1)[0].casefold()
            plugin_alerts = [
                alert for alert in plugin_alerts
                if str(alert[6] or "").split(":", 1)[0].casefold() != overlay_base
            ]

        if taskbar_side_notifications:
            dot_messages, side_notification_total = stack_side_notification_messages(
                main_kind=main_kind,
                messages=self.messages,
                folded_limit=self._folded_message_limit(main_kind),
                taskbar_side_notifications=True,
                plugin_alert_count=len(plugin_alerts),
                notification_limit=notification_limit,
            )
        else:
            dot_messages, side_notification_total = stack_side_notification_messages(
                main_kind=main_kind,
                messages=self.messages,
                folded_limit=self._folded_message_limit(main_kind),
                taskbar_side_notifications=False,
                plugin_alert_count=len(plugin_alerts),
                notification_limit=notification_limit,
            )

        if plugin_alerts:
            for alert_slot, plugin_alert in enumerate(plugin_alerts):
                _index, _label, _detail, _summary, _color, _icon, plugin_id, _status, _alert_title, _alert_body, _alert_priority = plugin_alert
                alert_rect = self._plugin_alert_rect(main_rect, alert_slot, side_notification_total)
                visuals.append(StackVisual(stack_plugin_alert_key(plugin_alert), IslandKind.PLUGIN_ALERT, alert_rect, False, None, 8))

        panel_visible = self._plugin_panel_visible()
        if self.expanded and self._plugin_enabled() and main_kind == IslandKind.MUSIC:
            rect, cursor_edge = self._stack_rect_for_direction(center_x, cursor_edge, stack_w, self._u(48), direction, gap)
            visuals.append(StackVisual("plugin", IslandKind.PLUGIN, rect, False, None, 6))
            if panel_visible:
                panel_edge = rect.bottom() if direction >= 0 else rect.top()
                rect, cursor_edge = self._stack_rect_for_direction(center_x, panel_edge, stack_w, self._plugin_panel_height(stack_w), direction, gap)
                visuals.append(StackVisual("plugin-panel", IslandKind.PLUGIN_PANEL, rect, False, None, 5))
        elif self.expanded and self._plugin_enabled() and main_kind == IslandKind.PLUGIN and panel_visible:
            strip_rect = main_rect
            panel_edge = strip_rect.bottom() if direction >= 0 else strip_rect.top()
            rect, cursor_edge = self._stack_rect_for_direction(center_x, panel_edge, stack_w, self._plugin_panel_height(stack_w), direction, gap)
            visuals.append(StackVisual("plugin-panel", IslandKind.PLUGIN_PANEL, rect, False, None, 5))

        if self.expanded:
            expanded_messages = stack_expanded_message_rows(
                main_kind=main_kind,
                messages=self.messages,
                folded_limit=self._folded_message_limit(main_kind),
            )
            for index, msg in enumerate(expanded_messages):
                key = stack_message_visual_key(msg, self._message_key)
                rect, cursor_edge = self._stack_rect_for_direction(center_x, cursor_edge, stack_w, self._u(48), direction, gap)
                visuals.append(StackVisual(key, IslandKind.MESSAGE, rect, False, msg, max(1, 6 - index)))
            return visuals

        for dot_index, msg in enumerate(dot_messages):
            key = stack_message_visual_key(msg, self._message_key, folded=True)
            slot_index = len(plugin_alerts) + dot_index if taskbar_side_notifications else dot_index
            slot_count = side_notification_total if taskbar_side_notifications else len(dot_messages)
            rect = self._message_dot_rect(main_rect, slot_index, slot_count, direction)
            z_index = 14 - dot_index if self._taskbar_compact_active() else 5 - dot_index
            visuals.append(StackVisual(key, IslandKind.MESSAGE, rect, True, msg, z_index))

        dot_tray_edge = main_rect.bottom() if direction >= 0 else main_rect.top()
        if dot_messages:
            dot_rects = [self._message_dot_rect(main_rect, i, len(dot_messages), direction) for i in range(len(dot_messages))]
            dot_tray = self._dot_tray_rect(dot_rects)
            if dot_tray:
                dot_tray_edge = dot_tray.bottom() if direction >= 0 else dot_tray.top()

        if not self.expanded:
            popup_start_edge = cursor_edge
            if dot_messages:
                popup_start_edge = max(popup_start_edge, dot_tray_edge + self._u(12.0)) if direction >= 0 else min(popup_start_edge, dot_tray_edge - self._u(12.0))
            popup_messages = stack_popup_message_rows(
                main_kind=main_kind,
                messages=self.messages,
                now=now,
                taskbar_side_notifications=taskbar_side_notifications,
                expanded=bool(self.expanded),
            )
            for index, msg in enumerate(popup_messages):
                key = stack_message_visual_key(msg, self._message_key)
                pop_width = main_w if main_kind != IslandKind.THIN_BAR else self._u(430)
                if direction >= 0:
                    y = popup_start_edge + index * self._u(54.0)
                else:
                    y = popup_start_edge - self._u(46.0) - index * self._u(54.0)
                rect = QRectF(center_x - pop_width / 2, y, pop_width, self._u(46))
                visuals.append(StackVisual(key, IslandKind.MESSAGE, rect, False, msg, 3 - index))

        return visuals

    def _plugin_alert_rect(self, main_rect: QRectF, slot: int = 0, total: int = 1) -> QRectF:
        if self._taskbar_compact_active():
            return positioning_taskbar_compact_plugin_alert_rect(
                main_rect,
                slot=slot,
                total=total,
                preferred_style=getattr(self, "taskbar_plugin_compact_style", "rail"),
                notification_outer_gap=self._taskbar_notification_outer_gap(),
                unit=self._u(1.0),
            )

        width = self._u(float(getattr(self, "plugin_alert_width", 168.0)))
        try:
            alerts = self._active_plugin_alerts(total)
            if 0 <= int(slot) < len(alerts):
                _index, label, detail, summary, _color, _icon, _plugin_id, _status, _alert_title, alert_body, _alert_priority = alerts[int(slot)]
                content_width = self._plugin_alert_width_for(label, detail, alert_body or summary)
                if str(label or "").strip().upper() == "TIME":
                    width = content_width
                else:
                    width = max(width, content_width)
        except Exception:
            pass
        height = self._u(42.0)
        gap = self._u(10.0)
        screen_left, screen_right = self._screen_bounds_in_local()
        pressure = self._main_rect_screen_pressure(main_rect, screen_left, screen_right)
        total = max(1, int(total))
        slot = max(0, min(int(slot), total - 1))
        row_gap = self._u(7.0)
        same_time_row = False
        row_width = width
        if total > 1:
            try:
                alerts_for_row = self._active_plugin_alerts(total)
            except Exception:
                alerts_for_row = []
            same_time_row = len(alerts_for_row) >= total and all(
                str(alert[1] or "").strip().upper() == "TIME" or str(alert[6] or "").strip().casefold().startswith("time")
                for alert in alerts_for_row[:total]
            )
            row_width = self._plugin_alert_row_desired_width(total, width)
        return positioning_plugin_alert_rect(
            main_rect,
            slot=slot,
            total=total,
            width=width,
            row_width=row_width,
            height=height,
            gap=gap,
            row_gap=row_gap,
            screen_left=screen_left,
            screen_right=screen_right,
            window_height=float(self.height()),
            pressure=pressure,
            same_time_row=same_time_row,
            min_width=self._u(128.0),
            vertical_margin=self._u(8.0),
            unit=self._u(1.0),
        )

    def _screen_bounds_in_local(self) -> tuple[float, float]:
        margin = self._u(8.0)
        fallback = (margin, max(margin, float(self.width()) - margin))
        try:
            screen_geo = self._current_screen_geometry()
        except Exception:
            screen_geo = None
        if not screen_geo:
            return fallback
        try:
            origin = self.mapToGlobal(QPoint(0, 0))
            left = float(screen_geo.left() - origin.x()) + margin
            right = float(screen_geo.right() - origin.x()) - margin
        except Exception:
            return fallback
        left = max(left, margin)
        right = min(right, float(self.width()) - margin)
        if right - left < self._u(80.0):
            return fallback
        return left, right

    def _main_rect_screen_pressure(self, main_rect: QRectF, screen_left: float, screen_right: float) -> str:
        try:
            main_kind = self._main_kind()
            target_width = self._main_size_for_kind(main_kind, self._stack_target_width(main_kind))[0]
        except Exception:
            try:
                target_width = self._stack_target_width(self._main_kind())
            except Exception:
                target_width = main_rect.width()
        return positioning_plugin_alert_main_rect_screen_pressure(
            main_rect,
            target_width=target_width,
            screen_left=screen_left,
            screen_right=screen_right,
            unit=self._u(1.0),
        )

    def _plugin_alert_circle_size(self, height: float) -> float:
        return positioning_plugin_alert_circle_size(height, unit=self._u(1.0))

    def _plugin_alert_x_for_side(self, main_rect: QRectF, width: float, gap: float, side: str) -> float:
        return positioning_plugin_alert_x_for_side(main_rect, width, gap, side)

    def _plugin_alert_side_for_row(
        self,
        main_rect: QRectF,
        width: float,
        gap: float,
        row_gap: float,
        total: int,
        preferred_side: str,
        screen_left: float,
        screen_right: float,
    ) -> str:
        return positioning_plugin_alert_side_for_row(
            main_rect,
            width,
            gap,
            row_gap,
            total,
            preferred_side,
            screen_left,
            screen_right,
        )

    def _plugin_alert_row_desired_width(self, total: int, fallback_width: float) -> float:
        row_width = float(fallback_width)
        try:
            alerts = self._active_plugin_alerts(max(1, int(total)))
        except Exception:
            alerts = []
        base_width = self._u(float(getattr(self, "plugin_alert_width", 168.0)))
        for alert in alerts[: max(1, int(total))]:
            try:
                _index, label, detail, summary, _color, _icon, _plugin_id, _status, _alert_title, alert_body, _alert_priority = alert
            except (TypeError, ValueError):
                continue
            content_width = self._plugin_alert_width_for(label, detail, alert_body or summary)
            if str(label or "").strip().upper() == "TIME":
                desired = content_width
            else:
                desired = max(base_width, content_width)
            row_width = max(row_width, desired)
        return row_width

    def _plugin_alert_width_for_row(
        self,
        main_rect: QRectF,
        width: float,
        gap: float,
        row_gap: float,
        total: int,
        side: str,
        screen_left: float,
        screen_right: float,
    ) -> float:
        return positioning_plugin_alert_width_for_row(
            main_rect,
            width,
            gap,
            row_gap,
            total,
            side,
            screen_left,
            screen_right,
            height=self._u(42.0),
            unit=self._u(1.0),
        )

    def _plugin_alert_x_for_slot(self, main_rect: QRectF, width: float, gap: float, row_gap: float, side: str, slot: int) -> float:
        return positioning_plugin_alert_x_for_slot(main_rect, width, gap, row_gap, side, slot)

    def _plugin_alert_row_fits(
        self,
        main_rect: QRectF,
        width: float,
        gap: float,
        row_gap: float,
        total: int,
        side: str,
        screen_left: float,
        screen_right: float,
    ) -> bool:
        return positioning_plugin_alert_row_fits(
            main_rect,
            width,
            gap,
            row_gap,
            total,
            side,
            screen_left,
            screen_right,
        )

    def _plugin_alert_side_space(self, main_rect: QRectF, gap: float, side: str, screen_left: float, screen_right: float) -> float:
        return positioning_plugin_alert_side_space(main_rect, gap, side, screen_left, screen_right)

    def _plugin_alert_uses_ring(self, rect: QRectF) -> bool:
        return positioning_plugin_alert_uses_ring(rect, unit=self._u(1.0))

    def _rect_x_fits_screen(self, x: float, width: float, screen_left: float, screen_right: float) -> bool:
        return positioning_rect_x_fits_screen(x, width, screen_left, screen_right)

    def _clamp_rect_x_to_screen(self, x: float, width: float, screen_left: float, screen_right: float) -> float:
        return positioning_clamp_rect_x_to_screen(x, width, screen_left, screen_right)

    def _plugin_alert_width_for(self, label: str, detail: str, summary: str) -> float:
        return self._u(presentation_plugin_alert_width_units(label, detail, summary))

    def _time_plugin_alert_width_for(self, detail: str, summary: str) -> float:
        return self._u(presentation_time_plugin_alert_width_units(detail, summary))

    def _plugin_alert_text_parts(self, label: str, detail: str, summary: str, status: str, alert_title: str = "", alert_body: str = "") -> tuple[str, str]:
        return presentation_plugin_alert_text_parts(label, detail, summary, status, alert_title, alert_body)

    def _main_kind(self) -> IslandKind:
        kind = presentation_main_island_kind(
            music_enabled=self._music_enabled_for_stack(),
            plugin_enabled=self._plugin_enabled(),
            no_music_priority=self._no_music_island_priority(),
            empty_display_mode=self._empty_island_display_mode(),
            messages_present=bool(self.messages),
        )
        return {
            "music": IslandKind.MUSIC,
            "plugin": IslandKind.PLUGIN,
            "empty_text": IslandKind.EMPTY_TEXT,
            "time": IslandKind.TIME,
            "message": IslandKind.MESSAGE,
            "thin_bar": IslandKind.THIN_BAR,
        }.get(kind, IslandKind.THIN_BAR)

    def _live_audio_detected_for_stack(self) -> bool:
        has_audio = bool(getattr(self, "live_audio_available", False))
        if not has_audio:
            try:
                provider = getattr(self, "data_provider", None)
                has_audio = bool(getattr(provider, "has_live_audio", lambda: False)())
            except Exception:
                has_audio = False
        return has_audio

    def _paused_media_available_for_stack(self) -> bool:
        if bool(getattr(self, "media_is_playing", True)):
            return False
        if str(getattr(self, "track_title", "") or "").strip():
            return True
        if str(getattr(self, "artist", "") or "").strip():
            return True
        return bool(getattr(self, "lyrics", None) or [])

    def _playing_media_metadata_available_for_stack(self) -> bool:
        if not bool(getattr(self, "media_is_playing", True)):
            return False
        has_metadata = bool(
            str(getattr(self, "track_title", "") or "").strip()
            or str(getattr(self, "artist", "") or "").strip()
            or (getattr(self, "lyrics", None) or [])
        )
        if not has_metadata:
            return False
        try:
            if float(getattr(self, "media_duration_seconds", 0.0) or 0.0) > 0.0:
                return True
        except (TypeError, ValueError):
            pass
        try:
            estimated = float(self._estimated_media_position(time.monotonic()))
            if estimated > 0.5:
                return True
        except Exception:
            pass
        source = str(getattr(self, "media_source_app", "") or "").strip()
        return bool(source)

    def _music_enabled_for_stack(self) -> bool:
        if not bool(getattr(self, "music_playing", False)):
            return False
        if self._personal_dj_island_presenting():
            return True
        if not bool(getattr(self, "music_auto_detect", False)):
            return True
        return (
            self._live_audio_detected_for_stack()
            or self._playing_media_metadata_available_for_stack()
            or self._paused_media_available_for_stack()
        )

    def _plugin_enabled(self) -> bool:
        return bool(getattr(self, "persistent_plugin", False)) and len(self._enabled_plugin_ids()) > 0

    def _folded_message_limit(self, main_kind: IslandKind) -> int:
        return presentation_folded_message_limit(
            taskbar_compact_active=self._taskbar_compact_active(),
            main_kind=main_kind.name.lower(),
            music_enabled=self._music_enabled_for_stack(),
            plugin_enabled=self._plugin_enabled(),
        )

    def _taskbar_message_effective_style(self, count: int) -> str:
        return positioning_taskbar_message_effective_style(
            getattr(self, "taskbar_message_compact_style", "strip"),
            count,
            selected_zone=QRectF(getattr(self, "taskbar_selected_drop_zone", QRectF())),
            unit=self._u(1.0),
        )

    def _taskbar_message_rect(self, main_rect: QRectF, index: int, count: int, direction: int = 1) -> QRectF:
        return positioning_taskbar_message_rect(
            main_rect,
            index,
            count,
            preferred_style=getattr(self, "taskbar_message_compact_style", "strip"),
            selected_zone=QRectF(getattr(self, "taskbar_selected_drop_zone", QRectF())),
            compact_active=self._taskbar_compact_active(),
            notification_outer_gap=self._taskbar_notification_outer_gap(),
            unit=self._u(1.0),
        )

    def _taskbar_notification_outer_gap(self) -> float:
        return self._u(9.0)

    def _message_dot_rect(self, main_rect: QRectF, index: int, count: int, direction: int = 1) -> QRectF:
        return positioning_message_dot_rect(
            main_rect,
            index,
            count,
            direction=direction,
            taskbar_compact_active=self._taskbar_compact_active(),
            preferred_taskbar_style=getattr(self, "taskbar_message_compact_style", "strip"),
            selected_taskbar_zone=QRectF(getattr(self, "taskbar_selected_drop_zone", QRectF())),
            taskbar_notification_outer_gap=self._taskbar_notification_outer_gap(),
            unit=self._u(1.0),
        )

    def _message_card_progress(self, rect: QRectF) -> float:
        return max(0.0, min(1.0, (rect.width() - self._u(10.0)) / self._u(180.0)))

    def _message_key(self, msg: Message) -> str:
        return presentation_message_identity_key(msg)

    def _initial_motion_rect_for_visual(self, visual: StackVisual, visuals: list[StackVisual]) -> QRectF:
        motion_snapshots = {
            key: MotionSnapshot(QRectF(motion.rect), float(getattr(motion, "opacity", 0.0) or 0.0))
            for key, motion in getattr(self, "motions", {}).items()
        }
        return stack_initial_motion_rect_for_visual(
            visual,
            visuals,
            motion_snapshots,
            stack_direction=int(getattr(self, "stack_direction", 1) or 1),
            expanded=bool(getattr(self, "expanded", False)),
            now=time.monotonic(),
            taskbar_compact_active=self._taskbar_compact_active(),
            unit=self._u(1.0),
        )

    def _plugin_alert_exit_rect_towards_main(self, alert_rect: QRectF, visuals: list[StackVisual]) -> QRectF:
        return stack_plugin_alert_exit_rect_towards_main(alert_rect, visuals, unit=self._u(1.0))

    def _taskbar_should_drop_stale_motion_key(self, key: str) -> bool:
        return stack_taskbar_should_drop_stale_motion_key(
            key,
            taskbar_compact_active=self._taskbar_compact_active(),
        )

    def _visual_motion_params(self, now: float) -> tuple[float, float]:
        return stack_active_visual_motion_params(
            finder_mode=bool(getattr(self, "finder_mode", False)),
            taskbar_compact_active=self._taskbar_compact_active(),
            stack_bounce_active=now < float(getattr(self, "stack_bounce_until", 0.0) or 0.0),
        )

    def _apply_motion_snap_to_target(self, key: str, target: QRectF, opacity: float = 1.0) -> bool:
        target = QRectF(target)
        if target.isNull():
            return False
        motion = self.motions.get(key)
        if motion is None:
            motion = SpringRect(target)
            self.motions[key] = motion
        motion.x = target.x()
        motion.y = target.y()
        motion.w = target.width()
        motion.h = target.height()
        motion.vx = 0.0
        motion.vy = 0.0
        motion.vw = 0.0
        motion.vh = 0.0
        motion.opacity = max(0.0, min(1.0, float(opacity)))
        return True

    def _snap_drag_main_motion_to_target(self, visual: StackVisual) -> bool:
        plan = stack_active_motion_step_plan(
            visual,
            motion_exists=visual.key in self.motions,
            drag_hides_auxiliary_visuals=self._drag_hides_auxiliary_visuals(),
            finder_mode=bool(getattr(self, "finder_mode", False)),
            taskbar_compact_active=self._taskbar_compact_active(),
            stack_bounce_active=False,
        )
        if not plan.snap_to_target:
            return False
        return self._apply_motion_snap_to_target(visual.key, plan.target_rect, plan.target_opacity)

    def _pin_top_notch_main_motion(self, visual: StackVisual) -> None:
        if visual.z_index != 10 or not self._top_notch_anchor_locked():
            return
        motion = self.motions.get(visual.key)
        if motion is not None:
            # Only the attached edge is fixed. Width, height and opacity keep
            # their existing springs when lyrics, devices or main kinds change.
            motion.y = visual.rect.top()
            motion.vy = 0.0

    def paintEvent(self, event):
        # Layout and ink must consume one verified source snapshot. A queued
        # timer can still hold the old row when this first visible frame is
        # painted; measuring that old row and then painting a new bilingual
        # row would leave its translation outside the old one-line shell.
        self._island_lyric_frame_at = time.monotonic()
        self._island_lyric_frame_phase = None
        try:
            self._paint_island_frame(event)
        finally:
            self._island_lyric_frame_at = None
            self._island_lyric_frame_phase = None

    def _paint_island_frame(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        if getattr(self, "high_quality_font_rendering", False):
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        now = self._island_lyric_frame_at
        dt = (now - self.last_frame_at) * self._animation_speed_factor()
        self.last_frame_at = now

        self._paint_stage(painter)
        self._paint_taskbar_drop_hint(painter)
        visuals = self._build_visuals()
        transition_scale, transition_opacity, scene_hidden, transition_edge_progress = self._scene_transition_params(now)
        if scene_hidden:
            self.group_frame_mask_path = None
            self._sync_fullscreen_plugin_alert_overlay(now)
            overlay = getattr(self, "barrage_overlay", None)
            if overlay is None or not overlay.isVisible():
                barrage_rects = self._paint_barrage_scene(painter)
                self.barrage_rects = barrage_rects
            else:
                self.barrage_rects = {}
            self.clearMask()
            if self.show_debug_overlay:
                self._paint_help(painter)
            painter.end()
            return

        targets = {visual.key for visual in visuals}
        painter_transform_applied = False
        if transition_scale != 1.0 or transition_opacity != 1.0:
            painter.save()
            anchor = self._scene_transition_anchor(visuals)
            offset = self._scene_transition_offset(visuals, transition_edge_progress)
            painter.translate(offset)
            painter.translate(anchor.x(), anchor.y())
            painter.scale(transition_scale, transition_scale)
            painter.translate(-anchor.x(), -anchor.y())
            painter.setOpacity(transition_opacity)
            painter_transform_applied = True

        drag_hides_auxiliary_visuals = self._drag_hides_auxiliary_visuals()
        taskbar_compact_active = self._taskbar_compact_active()
        stack_bounce_active = now < float(getattr(self, "stack_bounce_until", 0.0) or 0.0)
        for visual in visuals:
            motion_plan = stack_active_motion_step_plan(
                visual,
                motion_exists=visual.key in self.motions,
                drag_hides_auxiliary_visuals=drag_hides_auxiliary_visuals,
                finder_mode=bool(getattr(self, "finder_mode", False)),
                taskbar_compact_active=taskbar_compact_active,
                stack_bounce_active=stack_bounce_active,
            )
            if motion_plan.create_missing_motion:
                start = self._initial_motion_rect_for_visual(visual, visuals)
                self.motions[visual.key] = SpringRect(start)
            if motion_plan.snap_to_target:
                self._apply_motion_snap_to_target(visual.key, motion_plan.target_rect, motion_plan.target_opacity)
                continue
            self.motions[visual.key].step(
                motion_plan.target_rect,
                motion_plan.target_opacity,
                dt,
                response=motion_plan.response,
                damping=motion_plan.damping,
            )
            self._pin_top_notch_main_motion(visual)

        for key in list(self.motions):
            if key not in targets:
                motion = self.motions[key]
                stale_plan = stack_stale_motion_step_plan(
                    key,
                    MotionSnapshot(QRectF(motion.rect), float(getattr(motion, "opacity", 0.0) or 0.0)),
                    visuals,
                    expanded=bool(getattr(self, "expanded", False)),
                    finder_mode=bool(getattr(self, "finder_mode", False)),
                    drop_immediately=self._taskbar_should_drop_stale_motion_key(key),
                    unit=self._u(1.0),
                )
                if stale_plan.drop_immediately:
                    del self.motions[key]
                    continue
                if stale_plan.target_rect is None:
                    del self.motions[key]
                    continue
                motion.step(
                    stale_plan.target_rect,
                    stale_plan.target_opacity,
                    dt,
                    response=stale_plan.response,
                    damping=stale_plan.damping,
                )
                if motion.opacity < stale_plan.delete_below_opacity:
                    del self.motions[key]

        paint_plan = stack_frame_paint_plan(visuals, self.motions, minimum_opacity=0.06)
        items = list(paint_plan.visible_items)
        self._paint_myfinder_group_frame(painter, items, dt)

        self._paint_dot_group_backing(painter, items)
        self.plugin_action_rects = {}
        self._island_glow_paint_now = now
        for paint_item in paint_plan.paint_items:
            visual = paint_item.visual
            motion = paint_item.motion
            self._island_glow_current_visual_key = visual.key
            if paint_item.isolate_painter_state:
                painter.save()
            try:
                context_plan = stack_paint_context_plan(
                    visual,
                    motion,
                    now=now,
                    hovered_key=str(getattr(self, "hovered_key", "") or ""),
                    hover_started_at=float(getattr(self, "hover_started_at", 0.0) or 0.0),
                    hover_bounce_until=float(getattr(self, "hover_bounce_until", 0.0) or 0.0),
                    expanded=bool(getattr(self, "expanded", False)),
                    taskbar_compact_active=self._taskbar_compact_active(),
                    edge_notch_active=self._edge_notch_active(),
                )
                painter.setOpacity(context_plan.opacity)
                rect = QRectF(context_plan.rect)
                if visual.z_index == 10 and self._top_notch_anchor_locked():
                    rect.moveTop(visual.rect.top())
                if context_plan.paint_taskbar_left_handle:
                    self._paint_taskbar_left_handle(painter, rect)
                if context_plan.paint_edge_notch_backing:
                    self._paint_edge_notch_backing(painter, rect)
                if context_plan.clip_edge_notch:
                    painter.setClipPath(self._edge_notch_path(rect, self._edge_notch_edge()))
                if visual.kind == IslandKind.MUSIC:
                    self._paint_music(painter, rect, self.expanded)
                elif (self._audio_device_pose() is not None and not visual.folded
                      and visual.kind in {IslandKind.PLUGIN, IslandKind.EMPTY_TEXT, IslandKind.TIME, IslandKind.THIN_BAR}):
                    self._paint_audio_device_idle_island(painter, rect, visual.kind)
                elif visual.kind == IslandKind.PLUGIN:
                    self._paint_plugin(painter, rect, visual.folded)
                elif visual.kind == IslandKind.PLUGIN_PANEL:
                    self._paint_plugin_panel(painter, rect)
                elif visual.kind == IslandKind.PLUGIN_ALERT:
                    self._paint_plugin_alert(painter, rect, visual.key)
                elif visual.kind == IslandKind.MESSAGE and visual.message:
                    self._paint_message(painter, rect, visual.message, visual.folded)
                elif visual.kind == IslandKind.EMPTY_TEXT:
                    self._paint_empty_text_island(painter, rect)
                elif visual.kind == IslandKind.TIME:
                    self._paint_empty_time_island(painter, rect)
                else:
                    self._paint_thin_bar(painter, rect)
            finally:
                if paint_item.isolate_painter_state:
                    painter.restore()
                self._island_glow_current_visual_key = ""

        self.barrage_rects = {}
        self._update_interaction_mask(list(paint_plan.mask_rects))
        if painter_transform_applied:
            painter.restore()
        self._paint_surface_transition(painter, now)
        if self.show_debug_overlay:
            self._paint_help(painter)
        painter.end()

    def _hover_rect(self, rect: QRectF, now: float) -> QRectF:
        if self.expanded or not self.hover_started_at:
            return rect
        progress = max(0.0, min(1.0, now - self.hover_started_at))
        scale = 1.0 + 0.026 * (1.0 - math.cos(progress * math.pi)) / 2.0
        out = QRectF(rect)
        center = out.center()
        out.setWidth(rect.width() * scale)
        out.setHeight(rect.height() * scale)
        out.moveCenter(center)
        return out

    def _paint_stage(self, painter: QPainter):
        return
        painter.fillRect(QRectF(0, 0, self.width(), self.height()), QColor(3, 3, 4, 238))

        if self.finder_mode:
            finder = QRectF(44, 24, self.width() - 88, 34)
            path = self._rounded_path(finder, 14)
            painter.fillPath(path, QColor(22, 24, 28, 224))
            painter.setPen(QPen(QColor(255, 255, 255, 24), 1.0))
            painter.drawPath(path)
            painter.setPen(QPen(QColor(255, 255, 255, 18), 1.0))
            painter.drawLine(QPointF(finder.left() + 22, finder.top() + 1), QPointF(finder.right() - 22, finder.top() + 1))
            painter.setFont(self._font(9, QFont.Weight.Medium))
            painter.setPen(QColor(255, 255, 255, 138))
            painter.drawText(finder.adjusted(18, 0, -18, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "MyFinder simulation · V2 shell")
            painter.setPen(QColor(255, 255, 255, 92))
            painter.drawText(finder.adjusted(18, 0, -18, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, "upper edge reserved for embedded fusion")
        else:
            painter.setFont(self._font(9, QFont.Weight.Medium))
            painter.setPen(QColor(255, 255, 255, 105))
            painter.drawText(QRectF(42, 29, self.width() - 84, 24), Qt.AlignmentFlag.AlignLeft, "Standalone floating mode · V2 island stack")

    def _paint_taskbar_drop_hint(self, painter: QPainter):
        self._sync_taskbar_drop_overlay()

    def _paint_barrage_scene(
        self,
        painter: QPainter,
        viewport: QRectF | None = None,
        *,
        active_messages: list[Message] | None = None,
        clock: float | None = None,
        prune: bool = True,
    ) -> dict[str, QRectF]:
        viewport = QRectF(viewport) if viewport is not None else self._barrage_viewport_rect()
        active = list(self._active_barrage_messages() if active_messages is None else active_messages)
        if not active:
            return {}
        paint_clock = float(getattr(self, "barrage_clock", 0.0)) if clock is None else float(clock)

        font_size = max(8, min(20, int(getattr(self, "barrage_font_size", 10))))
        font = self._font(font_size, QFont.Weight.Bold)
        painter.setFont(font)
        metrics = QFontMetrics(font)

        def source_for_message(message: Message) -> BarrageLayoutSource:
            text = self._barrage_text(message)
            return BarrageLayoutSource(
                message=message,
                text=text,
                action_key=self._barrage_action_key_for_message(message),
                started_at=float(getattr(message, "barrage_started_at", 0.0) or 0.0),
                serial=int(getattr(message, "serial", 0) or 0),
                text_width=float(metrics.horizontalAdvance(text)),
            )

        try:
            window_width = float(self.width())
        except Exception:
            window_width = float(viewport.width())
        plan = stack_barrage_scene_layout_plan(
            [source_for_message(message) for message in active],
            preview_sources=[source_for_message(message) for message in getattr(self, "barrage_preview_messages", [])] if prune else (),
            fullscreen_sources=[source_for_message(message) for message in getattr(self, "fullscreen_plugin_barrage_messages", [])] if prune else (),
            viewport=viewport,
            clock=paint_clock,
            font_height=float(metrics.height()),
            y_offset=float(getattr(self, "barrage_y_offset", 0)),
            speed=float(getattr(self, "barrage_speed", 100)),
            show_icon=bool(getattr(self, "barrage_show_icon", True)),
            travel_base_width=max(window_width, float(viewport.width())),
            unit=self._u(1.0),
        )

        for item in plan.paint_items:
            self._paint_barrage_text(painter, item.text_rect, item.text, item.message, font, metrics)

        if prune:
            self.barrage_preview_messages = list(plan.alive_preview_messages)[:3]
            self.fullscreen_plugin_barrage_messages = list(plan.alive_fullscreen_messages)[:10]
        return dict(plan.hit_rects)

    def _barrage_text(self, message: Message) -> str:
        sender = message.sender.strip()
        text = message.text.strip()
        if sender and text:
            return f"{sender}: {text}"
        return sender or text or message.app

    def _barrage_background_is_dark(self, rect: QRectF) -> bool:
        now = time.monotonic()
        plan = stack_barrage_background_style_plan(
            current_dark_background=bool(getattr(self, "barrage_dark_background", False)),
            now=now,
            drag_candidate=bool(getattr(self, "drag_candidate", False)),
            last_pointer_press_at=float(getattr(self, "last_pointer_press_at", 0.0) or 0.0),
            last_checked_at=float(getattr(self, "barrage_style_checked_at", 0.0) or 0.0),
        )
        if plan.sample_luma:
            plan = stack_barrage_background_style_plan(
                current_dark_background=bool(getattr(self, "barrage_dark_background", False)),
                now=now,
                drag_candidate=bool(getattr(self, "drag_candidate", False)),
                last_pointer_press_at=float(getattr(self, "last_pointer_press_at", 0.0) or 0.0),
                last_checked_at=float(getattr(self, "barrage_style_checked_at", 0.0) or 0.0),
                sampled_luma=self._sample_background_luma(rect),
            )
        self.barrage_dark_background = plan.dark_background
        if plan.record_checked_at:
            self.barrage_style_checked_at = now
        return self.barrage_dark_background

    def _paint_barrage_text(self, painter: QPainter, rect: QRectF, text: str, message: Message, font: QFont, metrics: QFontMetrics):
        dark_bg = self._barrage_background_is_dark(rect)
        ink = QColor(255, 255, 255, 232) if dark_bg else QColor(22, 28, 40, 232)
        glow = QColor(0, 0, 0, 86) if dark_bg else QColor(255, 255, 255, 122)
        accent = QColor(message.name_color.red(), message.name_color.green(), message.name_color.blue(), 198 if dark_bg else 216)

        painter.save()
        painter.setFont(font)
        painter.setOpacity(1.0)
        radius = min(rect.height() / 2.0, self._u(12.0))
        if getattr(self, "barrage_use_backdrop", True):
            backdrop = QColor(12, 16, 24, 104) if dark_bg else QColor(255, 255, 255, 116)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(backdrop)
            painter.drawRoundedRect(rect, radius, radius)
            edge = QColor(255, 255, 255, 22) if dark_bg else QColor(0, 0, 0, 18)
            painter.setPen(QPen(edge, self._u(1.0)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(self._u(0.5), self._u(0.5), -self._u(0.5), -self._u(0.5)), max(1.0, radius - self._u(0.5)), max(1.0, radius - self._u(0.5)))

        icon_x = rect.left() + self._u(9.0)
        text_x = icon_x
        if getattr(self, "barrage_show_icon", True):
            icon_size = max(self._u(14.0), min(self._u(18.0), rect.height() - self._u(8.0)))
            icon_rect = QRectF(rect.left() + self._u(7.0), rect.center().y() - icon_size / 2.0, icon_size, icon_size)
            painter.setPen(QPen(accent, self._u(0.9)))
            painter.setBrush(QColor(message.avatar_color.red(), message.avatar_color.green(), message.avatar_color.blue(), 220))
            painter.drawEllipse(icon_rect)
            painter.setFont(self._font(max(8, min(20, int(getattr(self, "barrage_font_size", 10)))), QFont.Weight.Bold))
            painter.setPen(QColor(255, 255, 255, 228))
            painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, (message.sender[:1] or message.app[:1] or "·"))
            text_x = icon_rect.right() + self._u(8.0)
        else:
            text_x = rect.left() + self._u(12.0)

        text_rect = QRectF(text_x, rect.top(), rect.right() - text_x - self._u(10.0), rect.height())
        painter.setFont(font)
        painter.setPen(QPen(glow, self._u(1.35)))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            painter.drawText(text_rect.translated(dx, dy), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        painter.setPen(ink)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        painter.restore()

    def _sample_background_luma(self, rect: QRectF) -> float:
        try:
            local_center = QPoint(int(round(rect.center().x())), int(round(rect.center().y())))
            if getattr(self, "_barrage_painting_global_space", False):
                center = local_center
            else:
                center = self.mapToGlobal(local_center)
            screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
            if not screen:
                return 0.5
            sample_w = max(8, min(96, int(round(rect.width()))))
            sample_h = max(8, min(36, int(round(rect.height()))))
            left = center.x() - sample_w // 2
            top = center.y() - sample_h // 2
            geo = screen.geometry()
            left = max(geo.left(), min(left, geo.right() - sample_w + 1))
            top = max(geo.top(), min(top, geo.bottom() - sample_h + 1))
            pixmap = screen.grabWindow(0, left, top, sample_w, sample_h)
            if pixmap.isNull():
                return 0.5
            image = pixmap.toImage()
            if image.isNull():
                return 0.5
        except Exception:
            return 0.5
        total = 0.0
        count = 0
        step_x = max(1, image.width() // 10)
        step_y = max(1, image.height() // 6)
        for y in range(0, image.height(), step_y):
            for x in range(0, image.width(), step_x):
                color = image.pixelColor(x, y)
                total += color.red() * 0.2126 + color.green() * 0.7152 + color.blue() * 0.0722
                count += 1
        if not count:
            return 0.5
        return max(0.0, min(1.0, (total / count) / 255.0))

    def _update_interaction_mask(self, rects: list[QRectF]):
        now = time.monotonic()
        plan = stack_interaction_mask_update_plan(
            rects,
            platform_win32=sys.platform == "win32",
            finder_mode=bool(getattr(self, "finder_mode", False)),
            scene_suppressed=bool(getattr(self, "scene_suppressed", False)),
            interaction_mask_active=bool(getattr(self, "interaction_mask_active", False)),
            finder_hover_rect=QRectF(getattr(self, "finder_hover_rect", QRectF())),
            last_mask_rect=QRectF(getattr(self, "last_mask_rect", QRectF())),
            now=now,
            mask_deferred_until=float(getattr(self, "mask_deferred_until", 0.0) or 0.0),
            last_pointer_move_at=float(getattr(self, "last_pointer_move_at", 0.0) or 0.0),
            last_mask_update_at=float(getattr(self, "last_mask_update_at", 0.0) or 0.0),
            unit=self._u(1.0),
        )
        if plan.record_update_at:
            self.last_mask_update_at = now
        if plan.action == "clear":
            self.clearMask()
            self.interaction_mask_active = False
            self.last_mask_rect = QRectF()
            return
        if plan.action != "apply":
            return
        self.setMask(QRegion(plan.mask_rect.toAlignedRect()))
        self.last_mask_rect = QRectF(plan.mask_rect)
        self.interaction_mask_active = True

    def _paint_help(self, painter: QPainter):
        painter.setFont(self._font(9))
        painter.setPen(QColor(255, 255, 255, 118))
        mode = "Finder" if self.finder_mode else "Standalone"
        music = "Music" if self.music_playing else "NoMusic"
        plugin = "Plugin" if self.persistent_plugin else "NoPlugin"
        stack = "Expanded" if self.expanded else "Folded"
        width = int(self._stack_target_width(self._main_kind())) if self.expanded else int(self._u(float(self.STACK_WIDTH)))
        text = f"M music  P plugin  N message  C clear  F finder  Space expand  0 plugins  T seconds  1/2/3 focus  Esc quit    [{mode} | {music} | {plugin}:{self.enabled_plugin_count} | {stack} | width={width}px]"
        painter.drawText(QRectF(self._u(34), self.height() - self._u(36), self.width() - self._u(68), self._u(24)), Qt.AlignmentFlag.AlignCenter, text)
        if self.opened_source_app and time.monotonic() < self.opened_source_until:
            painter.setFont(self._font(9, QFont.Weight.Bold))
            painter.setPen(QColor(132, 255, 176, 210))
            painter.drawText(QRectF(self._u(34), self.height() - self._u(62), self.width() - self._u(68), self._u(22)), Qt.AlignmentFlag.AlignCenter, self.opened_source_app)

    def _rounded_path(self, rect: QRectF, radius: float) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        return path

    def _myfinder_cradle_path(self, rect: QRectF, top_radius: float, bottom_radius: float) -> QPainterPath:
        bottom_r = max(1.0, min(bottom_radius, rect.height() * 0.5, rect.width() * 0.5))
        path = QPainterPath()
        path.moveTo(rect.left(), rect.top())
        path.lineTo(rect.right(), rect.top())
        path.lineTo(rect.right(), rect.bottom() - bottom_r)
        path.quadTo(rect.right(), rect.bottom(), rect.right() - bottom_r, rect.bottom())
        path.lineTo(rect.left() + bottom_r, rect.bottom())
        path.quadTo(rect.left(), rect.bottom(), rect.left(), rect.bottom() - bottom_r)
        path.lineTo(rect.left(), rect.top())
        path.closeSubpath()
        return path

    def _finder_texture_source_rect(self, target: QRectF, texture: QImage) -> QRectF:
        glass = self.finder_material
        if getattr(glass, "texture_is_full_screen", False):
            global_top_left = self.mapToGlobal(QPoint(int(round(target.left())), int(round(target.top()))))
            source_left = float(global_top_left.x() - int(getattr(glass, "texture_screen_left", 0)))
            source_top = float(global_top_left.y() - int(getattr(glass, "texture_screen_top", 0)))
            source_left = max(0.0, min(float(texture.width() - 1), source_left))
            source_top = max(0.0, min(float(texture.height() - 1), source_top))
            source_width = max(1.0, min(float(texture.width()) - source_left, target.width()))
            source_height = max(1.0, min(float(texture.height()) - source_top, target.height()))
            return QRectF(source_left, source_top, source_width, source_height)
        if texture.width() > 512:
            global_left = self.mapToGlobal(QPoint(int(round(target.left())), 0)).x()
            source_left = max(0.0, min(float(texture.width() - 1), float(global_left - int(getattr(glass, "texture_screen_left", 0)))))
            source_width = max(1.0, min(float(texture.width()) - source_left, target.width()))
            return QRectF(source_left, 0.0, source_width, float(texture.height()))
        return QRectF(0.0, 0.0, float(texture.width()), float(texture.height()))

    def _group_frame_target_is_discontinuous(self, current: QRectF, target: QRectF) -> bool:
        if current.isNull() or target.isNull():
            return False
        width_delta = abs(current.width() - target.width())
        height_delta = abs(current.height() - target.height())
        center_delta = math.hypot(current.center().x() - target.center().x(), current.center().y() - target.center().y())
        large_width_shift = width_delta > max(self._u(140.0), target.width() * 0.72)
        large_height_shift = height_delta > max(self._u(90.0), target.height() * 1.25)
        large_center_shift = center_delta > max(self._u(180.0), target.width() * 0.64)
        return large_width_shift or large_height_shift or large_center_shift

    def _group_frame_should_snap_to_target(self, target: QRectF, mode_key: str) -> bool:
        motion = getattr(self, "group_frame_motion", None)
        if motion is None:
            return False
        previous_key = str(getattr(self, "group_frame_mode_key", "") or "")
        if previous_key and previous_key != mode_key:
            return True
        return self._group_frame_target_is_discontinuous(motion.rect, target)

    def _coalesced_group_frame_target(self, target: QRectF, mode_key: str) -> QRectF:
        previous_key = str(getattr(self, "group_frame_target_mode_key", "") or "")
        previous = QRectF(getattr(self, "group_frame_target_rect", QRectF()))
        if previous_key == mode_key and not previous.isNull():
            edge_delta = max(
                abs(previous.left() - target.left()),
                abs(previous.top() - target.top()),
                abs(previous.right() - target.right()),
                abs(previous.bottom() - target.bottom()),
            )
            threshold = self._u(6.0 if mode_key == "finder" else 2.5)
            if edge_delta <= threshold:
                return previous
        self.group_frame_target_rect = QRectF(target)
        self.group_frame_target_mode_key = mode_key
        return target

    def _taskbar_material_frame_radius(self, frame: QRectF) -> float:
        if self._taskbar_panel_corner_policy() == "square":
            return 0.0
        return min(self._u(10.0), frame.height() * 0.12)

    def _paint_taskbar_material_frame(self, painter: QPainter, frame: QRectF, shell: QPainterPath | None = None) -> QPainterPath:
        radius = self._taskbar_material_frame_radius(frame)
        shell = QPainterPath(shell) if shell is not None else self._rounded_path(frame, radius)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        shadow = QRectF(frame).adjusted(-self._u(8.0), -self._u(8.0), self._u(8.0), self._u(10.0))
        shadow_gradient = QRadialGradient(shadow.center(), max(shadow.width(), shadow.height()) * 0.58)
        tokens = self._taskbar_material_frame_tokens()
        shadow_color = tokens["shadow"]
        shadow_gradient.setColorAt(0.0, shadow_color)
        shadow_gradient.setColorAt(0.70, QColor(0, 0, 0, 28))
        shadow_gradient.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(shadow_gradient)
        painter.drawRoundedRect(shadow, radius + self._u(10.0), radius + self._u(10.0))

        material = QLinearGradient(frame.topLeft(), frame.bottomLeft())
        material.setColorAt(0.0, tokens["fill_top"])
        material.setColorAt(0.42, tokens["fill_mid"])
        material.setColorAt(1.0, tokens["fill_bottom"])
        painter.fillPath(shell, material)
        painter.setPen(QPen(tokens["stroke"], self._u(1.0)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(shell)
        painter.restore()
        return shell

    def _taskbar_right_extension_path(self, rect: QRectF, radius: float) -> QPainterPath:
        corner = max(0.0, min(float(radius), rect.width() * 0.5, rect.height() * 0.5))
        path = QPainterPath()
        path.moveTo(rect.left(), rect.top())
        path.lineTo(rect.right() - corner, rect.top())
        if corner > 0.0:
            path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + corner)
        else:
            path.lineTo(rect.right(), rect.top())
        path.lineTo(rect.right(), rect.bottom() - corner)
        if corner > 0.0:
            path.quadTo(rect.right(), rect.bottom(), rect.right() - corner, rect.bottom())
        else:
            path.lineTo(rect.right(), rect.bottom())
        path.lineTo(rect.left(), rect.bottom())
        path.closeSubpath()
        return path

    def _stick_notch_active(self) -> bool:
        return str(getattr(self, "location_mode", "free") or "free") == "stick"

    def _top_notch_active(self) -> bool:
        return str(getattr(self, "location_mode", "free") or "free") == "top"

    def _edge_notch_active(self) -> bool:
        return str(getattr(self, "location_mode", "free") or "free") in {"stick", "top"}

    def _edge_notch_edge(self) -> str:
        return "top" if self._top_notch_active() else "bottom"

    def _edge_notch_path(self, rect: QRectF, edge: str = "bottom") -> QPainterPath:
        shell = QRectF(rect)
        radius = max(self._u(10.0), min(self._u(24.0), shell.height() * 0.50, shell.width() * 0.16))
        edge = str(edge or "bottom").strip().casefold()
        path = QPainterPath()
        if edge == "top":
            path.moveTo(shell.left(), shell.top())
            path.lineTo(shell.right(), shell.top())
            path.lineTo(shell.right(), shell.bottom() - radius)
            path.quadTo(shell.right(), shell.bottom(), shell.right() - radius, shell.bottom())
            path.lineTo(shell.left() + radius, shell.bottom())
            path.quadTo(shell.left(), shell.bottom(), shell.left(), shell.bottom() - radius)
            path.lineTo(shell.left(), shell.top())
            path.closeSubpath()
            return path
        path.moveTo(shell.left(), shell.bottom())
        path.lineTo(shell.right(), shell.bottom())
        path.lineTo(shell.right(), shell.top() + radius)
        path.quadTo(shell.right(), shell.top(), shell.right() - radius, shell.top())
        path.lineTo(shell.left() + radius, shell.top())
        path.quadTo(shell.left(), shell.top(), shell.left(), shell.top() + radius)
        path.lineTo(shell.left(), shell.bottom())
        path.closeSubpath()
        return path

    def _music_shell_path(self, rect: QRectF, radius: float, expanded: bool = False) -> QPainterPath:
        if self._edge_notch_active():
            return self._edge_notch_path(rect, self._edge_notch_edge())
        return self._rounded_path(rect, radius)

    def _paint_edge_notch_backing(self, painter: QPainter, rect: QRectF):
        edge = self._edge_notch_edge()
        shell = self._edge_notch_path(rect, edge)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        fill = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
        if edge == "top":
            fill.setColorAt(0.0, QColor(0, 0, 0, 255))
            fill.setColorAt(0.45, QColor(0, 0, 0, 255))
            fill.setColorAt(1.0, QColor(12, 12, 14, 252))
        else:
            fill.setColorAt(0.0, QColor(12, 12, 14, 252))
            fill.setColorAt(0.55, QColor(0, 0, 0, 255))
            fill.setColorAt(1.0, QColor(0, 0, 0, 255))
        painter.fillPath(shell, QBrush(fill))
        painter.setPen(QPen(QColor(255, 255, 255, 28), self._u(1.0)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if edge == "top":
            inset = rect.adjusted(self._u(0.8), self._u(0.2), -self._u(0.8), -self._u(0.8))
        else:
            inset = rect.adjusted(self._u(0.8), self._u(0.8), -self._u(0.8), -self._u(0.2))
        painter.drawPath(self._edge_notch_path(inset, edge))
        painter.restore()

    def _paint_stick_notch_backing(self, painter: QPainter, rect: QRectF):
        self._paint_edge_notch_backing(painter, rect)

    def _reset_taskbar_notification_frame_state(self):
        self.taskbar_notification_frame_motion = None
        self.taskbar_notification_frame_rect = QRectF()

    def _is_taskbar_notification_visual(self, visual: StackVisual) -> bool:
        if not self._taskbar_compact_active() or bool(getattr(self, "expanded", False)):
            return False
        if visual.kind == IslandKind.PLUGIN_ALERT and str(visual.key).startswith("plugin-alert:"):
            return True
        return visual.kind == IslandKind.MESSAGE and visual.folded and str(visual.key).startswith("message-dot:")

    def _taskbar_notification_frame_target(self, items: list[tuple[StackVisual, SpringRect]]) -> QRectF:
        main_rect = QRectF()
        notification_rects: list[QRectF] = []
        for visual, motion in items:
            rect = QRectF(visual.rect)
            if motion.opacity <= 0.08 or rect.width() <= 2 or rect.height() <= 2:
                continue
            if visual.z_index == 10:
                main_rect = rect
            elif self._is_taskbar_notification_visual(visual):
                notification_rects.append(rect)
        if main_rect.isNull() or not notification_rects:
            return QRectF()
        right = max(rect.right() for rect in notification_rects) + self._u(3.0)
        left = main_rect.right() - self._u(18.0)
        return QRectF(left, main_rect.top(), max(self._u(4.0), right - left), main_rect.height())

    def _paint_taskbar_notification_frame(self, painter: QPainter, items: list[tuple[StackVisual, SpringRect]], dt: float):
        target = self._taskbar_notification_frame_target(items)
        if target.isNull():
            self._reset_taskbar_notification_frame_state()
            return
        motion = getattr(self, "taskbar_notification_frame_motion", None)
        if motion is None:
            motion = SpringRect(target)
            motion.opacity = 1.0
            self.taskbar_notification_frame_motion = motion
        else:
            motion.step(target, 1.0, dt, response=0.30, damping=0.84)
        frame = QRectF(motion.rect)
        self.taskbar_notification_frame_rect = frame
        shell = self._taskbar_right_extension_path(frame, self._taskbar_material_frame_radius(frame))
        self._paint_taskbar_material_frame(painter, frame, shell)

    def _paint_taskbar_group_frame(self, painter: QPainter, items: list[tuple[StackVisual, SpringRect]], dt: float):
        visible_rects = []
        for visual, motion in items:
            rect = QRectF(visual.rect)
            if motion.opacity <= 0.16 or rect.width() <= 4 or rect.height() <= 4:
                continue
            if visual.folded:
                continue
            if visual.kind == IslandKind.PLUGIN_ALERT:
                continue
            visible_rects.append(rect)
        if not visible_rects:
            self.group_frame_motion = None
            self.group_frame_mode_key = ""
            self.group_frame_target_rect = QRectF()
            self.group_frame_target_mode_key = ""
            self.group_frame_mask_path = None
            self.finder_hover_rect = QRectF()
            return

        pad_x = self._u(10.0)
        pad_y = self._u(8.0)
        left = min(rect.left() for rect in visible_rects) - pad_x
        right = max(rect.right() for rect in visible_rects) + pad_x
        top = min(rect.top() for rect in visible_rects) - pad_y
        bottom = max(rect.bottom() for rect in visible_rects) + pad_y
        target = QRectF(left, top, right - left, bottom - top)
        mode_key = "tasker"
        target = self._coalesced_group_frame_target(target, mode_key)
        if self.group_frame_motion is None:
            start = QRectF(target)
            start.setHeight(self._u(34.0))
            start.moveBottom(target.bottom())
            self.group_frame_motion = SpringRect(start)
        if self._group_frame_should_snap_to_target(target, mode_key):
            self.group_frame_motion = SpringRect(target)
            self.group_frame_motion.opacity = 1.0
        else:
            self.group_frame_motion.step(target, 1.0, dt, response=0.32, damping=0.84)
        self.group_frame_mode_key = mode_key

        frame = self.group_frame_motion.rect
        shell = self._rounded_path(frame, self._taskbar_material_frame_radius(frame))
        self.group_frame_mask_path = QPainterPath(shell)
        self.finder_hover_rect = frame.adjusted(-self._u(8.0), -self._u(6.0), self._u(8.0), self._u(8.0))
        self._paint_taskbar_material_frame(painter, frame)

    def _paint_myfinder_group_frame(self, painter: QPainter, items: list[tuple[StackVisual, SpringRect]], dt: float):
        if not self.finder_mode:
            if self._taskbar_compact_active() and not bool(getattr(self, "expanded", False)):
                self._paint_taskbar_notification_frame(painter, items, dt)
            else:
                self._reset_taskbar_notification_frame_state()
            if self._taskbar_uses_expanded_material_frame():
                self._paint_taskbar_group_frame(painter, items, dt)
            else:
                self.group_frame_motion = None
                self.group_frame_mode_key = ""
                self.group_frame_target_rect = QRectF()
                self.group_frame_target_mode_key = ""
                self.group_frame_mask_path = None
                self.finder_hover_rect = QRectF()
            return
        visible_rects = []
        main_rect = None
        main_kind = None
        for visual, motion in items:
            rect = QRectF(visual.rect)
            if motion.opacity <= 0.16 or rect.width() <= 4 or rect.height() <= 4:
                continue
            if visual.z_index == 10:
                main_rect = QRectF(rect)
                main_kind = visual.kind
            if self.expanded:
                if visual.folded:
                    continue
            elif (
                visual.z_index != 10
                and visual.kind != IslandKind.PLUGIN_ALERT
                and not (visual.kind == IslandKind.MESSAGE and not visual.folded)
            ):
                continue
            visible_rects.append(rect)
        if not visible_rects:
            self.group_frame_motion = None
            self.group_frame_mode_key = ""
            self.group_frame_target_rect = QRectF()
            self.group_frame_target_mode_key = ""
            self.group_frame_mask_path = None
            self.finder_hover_rect = QRectF()
            return
        if main_kind == IslandKind.THIN_BAR:
            self.group_frame_motion = None
            self.group_frame_mode_key = ""
            self.group_frame_target_rect = QRectF()
            self.group_frame_target_mode_key = ""
            self.group_frame_mask_path = None
            self.finder_hover_rect = QRectF()
            return

        left = min(rect.left() for rect in visible_rects)
        right = max(rect.right() for rect in visible_rects)
        top = min(rect.top() for rect in visible_rects)
        bottom = max(rect.bottom() for rect in visible_rects)
        dot_rects = [visual.rect for visual, motion in items if visual.kind == IslandKind.MESSAGE and visual.folded and motion.opacity > 0.08]
        dot_tray = self._dot_tray_rect(dot_rects)
        if dot_tray and not self.expanded:
            bottom = max(bottom, dot_tray.bottom())
            if main_rect:
                bottom = max(bottom, main_rect.bottom() + self._u(15.0))

        finder_bar = QRectF(0.0, 0.0, self.width(), self.finder_material.bar_height)
        glass = self.finder_material
        effective_border_width = 0.0
        pad_x = (
            max(self._u(6.0), min(self._u(12.0), self._u(5.6) + effective_border_width * self._u(1.8)))
            if not self.expanded
            else max(self._u(9.0), min(self._u(15.0), self._u(8.2) + effective_border_width * self._u(2.0)))
        )
        content_top = top - self._u(6.0 if not self.expanded else 8.0)
        if main_kind == IslandKind.MUSIC and not self.expanded:
            music_top = main_rect.top() if main_rect is not None and not main_rect.isNull() else top
            music_h = main_rect.height() if main_rect is not None and not main_rect.isNull() else max(1.0, bottom - top)
            frame_top = music_top + music_h * 0.42
        else:
            frame_top = content_top
        frame_top = max(0.0, min(bottom - self._u(2.0), frame_top))
        if dot_tray and not self.expanded:
            bottom = max(bottom, dot_tray.bottom())
        bottom_padding = self._u(6.0 if main_kind == IslandKind.MUSIC and not self.expanded else 8.0)
        target_bottom = bottom + bottom_padding
        target = QRectF(left - pad_x, frame_top, right - left + pad_x * 2.0, target_bottom - frame_top)
        stable_dual_compact_music = main_kind == IslandKind.MUSIC and not self.expanded and self._has_current_two_line_lyric()
        mode_key = "finder"
        target = self._coalesced_group_frame_target(target, mode_key)
        if self.group_frame_motion is None:
            start = QRectF(target)
            start.setHeight(min(target.height(), self._u(22.0)))
            start.moveTop(target.top())
            self.group_frame_motion = SpringRect(start)
        if stable_dual_compact_music:
            self.group_frame_motion = SpringRect(target)
            self.group_frame_motion.opacity = 1.0
        elif self._group_frame_should_snap_to_target(target, mode_key):
            self.group_frame_motion = SpringRect(target)
            self.group_frame_motion.opacity = 1.0
        else:
            self.group_frame_motion.step(target, 1.0, dt, response=0.16, damping=0.9)
        self.group_frame_mode_key = mode_key

        frame = self.group_frame_motion.rect
        side_flare = max(
            self._u(18.0),
            min(self._u(32.0), glass.corner_radius * self.VISUAL_SCALE + effective_border_width * self._u(5.0)),
        )
        cradle = frame.adjusted(-side_flare, 0.0, side_flare, 0.0)
        cradle.setTop(frame.top())

        finder_bottom = frame.top()
        body_bottom = cradle.bottom()
        body_h = max(1.0, body_bottom - finder_bottom)
        base_corner = max(self._u(18.0), glass.corner_radius * self.VISUAL_SCALE + effective_border_width * self._u(2.2))
        bottom_r = max(self._u(14.0), min(base_corner, body_h * 0.48, cradle.width() * 0.12))
        top_r = max(bottom_r, min(self._u(42.0), body_h * 0.52, side_flare * 1.55))
        shell_rect = QRectF(cradle.left(), finder_bottom - self._u(1.0), cradle.width(), body_h + self._u(1.0))
        shell = self._myfinder_cradle_path(shell_rect, top_r, bottom_r)
        self.group_frame_mask_path = QPainterPath(shell)
        hover_pad_x = max(self._u(10.0), side_flare * 0.36)
        self.finder_hover_rect = shell_rect.adjusted(-hover_pad_x, -self._u(7.0), hover_pad_x, self._u(12.0))
        self.finder_hover_rect.setTop(min(self.finder_hover_rect.top(), top - self._u(6.0)))
        left_join = shell_rect.left()
        right_join = shell_rect.right()

        painter.setPen(Qt.PenStyle.NoPen)
        used_texture = False
        if glass.texture_image and not glass.texture_image.isNull():
            painter.save()
            painter.setClipPath(shell)
            target = QRectF(shell_rect)
            painter.drawImage(target, glass.texture_image, self._finder_texture_source_rect(target, glass.texture_image))
            painter.restore()
            used_texture = True
        else:
            material = QLinearGradient(finder_bar.left(), finder_bottom, finder_bar.right(), cradle.bottom())
            material.setColorAt(0.00, glass.bottom_color)
            material.setColorAt(0.12, glass.bottom_color)
            material.setColorAt(0.42, glass.bottom_color)
            material.setColorAt(1.00, glass.mid_color)
            painter.fillPath(shell, material)

        if not used_texture:
            shade = QLinearGradient(0, finder_bottom, 0, cradle.bottom())
            shade.setColorAt(0.00, QColor(255, 255, 255, 0))
            shade.setColorAt(0.34, QColor(255, 255, 255, 0))
            shade.setColorAt(0.72, QColor(0, 0, 0, max(0, int(glass.shadow_alpha * 0.10))))
            shade.setColorAt(1.00, QColor(0, 0, 0, max(8, int(glass.shadow_alpha * 0.28))))
            painter.fillPath(shell, shade)

        join_wash = QLinearGradient(0, finder_bottom, 0, finder_bottom + self._u(8.0))
        join_wash.setColorAt(0.00, QColor(glass.bottom_color.red(), glass.bottom_color.green(), glass.bottom_color.blue(), 0))
        join_wash.setColorAt(0.45, QColor(glass.bottom_color.red(), glass.bottom_color.green(), glass.bottom_color.blue(), max(10, int(glass.bottom_color.alpha() * 0.18))))
        join_wash.setColorAt(1.00, QColor(glass.bottom_color.red(), glass.bottom_color.green(), glass.bottom_color.blue(), 0))
        painter.fillPath(shell, join_wash)

    def _dot_tray_rect(self, dot_rects: list[QRectF]) -> QRectF | None:
        if not dot_rects:
            return None
        left = min(rect.left() for rect in dot_rects)
        right = max(rect.right() for rect in dot_rects)
        top = min(rect.top() for rect in dot_rects)
        bottom = max(rect.bottom() for rect in dot_rects)
        return QRectF(left - self._u(8.0), top - self._u(5.0), right - left + self._u(16.0), bottom - top + self._u(9.0))

    def _paint_dot_group_backing(self, painter: QPainter, items: list[tuple[StackVisual, SpringRect]]):
        if self._taskbar_compact_active():
            return
        dots = [(visual, motion.rect) for visual, motion in items if visual.kind == IslandKind.MESSAGE and visual.folded and motion.opacity > 0.08]
        if not dots:
            return
        bg = self._dot_tray_rect([rect for _, rect in dots])
        if not bg:
            return
        main_rect = next((motion.rect for visual, motion in items if visual.z_index == 10 and motion.opacity > 0.12), None)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 218))
        if main_rect:
            bg = QRectF(bg)
            direction = -1 if bg.center().y() < main_rect.center().y() else 1
            if direction >= 0:
                bg.setTop(main_rect.bottom() - self._u(2.0))
                bg.setBottom(max(bg.bottom(), main_rect.bottom() + self._u(15.0)))
            else:
                bg.setBottom(main_rect.top() + self._u(2.0))
                bg.setTop(min(bg.top(), main_rect.top() - self._u(15.0)))
            corner_radius = min(self._u(8.0), bg.height() * 0.45, bg.width() * 0.26)
            path = QPainterPath()
            if direction >= 0:
                path.moveTo(bg.left(), bg.top())
                path.lineTo(bg.right(), bg.top())
                path.lineTo(bg.right(), bg.bottom() - corner_radius)
                path.quadTo(bg.right(), bg.bottom(), bg.right() - corner_radius, bg.bottom())
                path.lineTo(bg.left() + corner_radius, bg.bottom())
                path.quadTo(bg.left(), bg.bottom(), bg.left(), bg.bottom() - corner_radius)
                path.lineTo(bg.left(), bg.top())
            else:
                path.moveTo(bg.left(), bg.bottom())
                path.lineTo(bg.right(), bg.bottom())
                path.lineTo(bg.right(), bg.top() + corner_radius)
                path.quadTo(bg.right(), bg.top(), bg.right() - corner_radius, bg.top())
                path.lineTo(bg.left() + corner_radius, bg.top())
                path.quadTo(bg.left(), bg.top(), bg.left(), bg.top() + corner_radius)
                path.lineTo(bg.left(), bg.bottom())
            path.closeSubpath()
            tray_gradient = QLinearGradient(0, bg.top(), 0, bg.bottom())
            if direction >= 0:
                tray_gradient.setColorAt(0.00, QColor(0, 0, 0, 210))
                tray_gradient.setColorAt(0.35, QColor(0, 0, 0, 228))
                tray_gradient.setColorAt(1.00, QColor(0, 0, 0, 238))
            else:
                tray_gradient.setColorAt(0.00, QColor(0, 0, 0, 238))
                tray_gradient.setColorAt(0.65, QColor(0, 0, 0, 228))
                tray_gradient.setColorAt(1.00, QColor(0, 0, 0, 210))
            painter.fillPath(path, tray_gradient)
            return

        radius = min(self._u(8.0), bg.height() * 0.45, bg.width() * 0.26)
        path = QPainterPath()
        path.moveTo(bg.left(), bg.top())
        path.lineTo(bg.right(), bg.top())
        path.lineTo(bg.right(), bg.bottom() - radius)
        path.quadTo(bg.right(), bg.bottom(), bg.right() - radius, bg.bottom())
        path.lineTo(bg.left() + radius, bg.bottom())
        path.quadTo(bg.left(), bg.bottom(), bg.left(), bg.bottom() - radius)
        path.lineTo(bg.left(), bg.top())
        path.closeSubpath()
        painter.fillPath(path, QColor(0, 0, 0, 226))

    def _paint_black_glass(self, painter: QPainter, rect: QRectF, radius: float, folded: bool = False):
        path = self._rounded_path(rect, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(path, QColor(0, 0, 0, 255))

        stroke = self._u(1.0)
        inner = rect.adjusted(stroke, stroke, -stroke, -stroke)
        painter.setPen(QPen(QColor(255, 255, 255, 28 if not folded else 15), stroke))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(inner, max(1.0, radius - stroke), max(1.0, radius - stroke))

        if rect.height() > self._u(26) and not folded:
            painter.setPen(QPen(QColor(255, 255, 255, 16), stroke))
            painter.drawLine(QPointF(rect.left() + radius * 0.85, rect.top() + self._u(1.2)), QPointF(rect.right() - radius * 0.85, rect.top() + self._u(1.2)))

    def _paint_connected_backing(self, painter: QPainter, items: list[tuple[StackVisual, SpringRect]]):
        visible = [(v, m.rect) for v, m in items if m.opacity > 0.2 and not v.folded and m.rect.height() > self._u(22)]
        visible.sort(key=lambda item: item[1].top())
        painter.setPen(Qt.PenStyle.NoPen)
        for (_, upper), (_, lower) in zip(visible, visible[1:]):
            if lower.top() - upper.bottom() > self._u(22):
                continue
            cx = (upper.center().x() + lower.center().x()) / 2.0
            width = min(upper.width(), lower.width()) - self._u(28)
            bridge = QRectF(cx - width / 2.0, upper.bottom() - self._u(1.0), width, lower.top() - upper.bottom() + self._u(2.0))
            path = self._rounded_path(bridge.adjusted(0, -self._u(7), 0, self._u(7)), self._u(19))
            painter.fillPath(path, QColor(0, 0, 0, 178))
            painter.setPen(QPen(QColor(255, 255, 255, 16), self._u(1.0)))
            painter.drawPath(path)
            painter.setPen(Qt.PenStyle.NoPen)

    def _paint_music(self, painter: QPainter, rect: QRectF, expanded: bool):
        total_started_at = time.perf_counter()
        dj_speaking = self._personal_dj_island_presenting()
        if dj_speaking:
            # Reuse the actual island ambience and shell, including top-notch
            # fusion, rather than drawing a second rectangle over them.
            expanded = False
        radius = self._music_shell_radius(rect, expanded)
        ambience_style = self._effective_music_ambience_style()
        plan = presentation_music_surface_paint_plan(
            expanded=expanded,
            ambience_style=ambience_style,
            expanded_layout_style=self._normalize_music_expanded_layout_style(getattr(self, "music_expanded_layout_style", "standard")),
            lyric_color_fill_paints_background=self._active_lyric_color_fill_paints_background(ambience_style),
        )
        if not dj_speaking and not expanded and self._audio_device_pose() is not None:
            # Render the same background once, without content. Sampling this
            # raster includes album/white-bridge transitions and never reads
            # the desktop, a previous icon, or an audio provider from paint.
            self._paint_audio_device_background(painter, rect, radius, plan)
            self._paint_music_compact(painter, rect)
            for step in plan.steps:
                if step in {"border", "yaoguang_compact_frame_overlay"}:
                    self._execute_music_surface_paint_step(step, painter, rect, radius, False, plan.ambience_style)
            self._record_visual_paint_section("music.total", total_started_at)
            return
        try:
            for step in plan.steps:
                step_started_at = time.perf_counter()
                try:
                    if dj_speaking and step in {"expanded_content", "compact_content"}:
                        self._paint_personal_dj_island(painter, rect)
                        continue
                    if step in {"expanded_content", "compact_content"} and getattr(self, "_island_button_ripples", ()):
                        self._paint_island_button_ripples(
                            painter,
                            float(getattr(self, "_island_glow_paint_now", 0.0) or time.monotonic()),
                            owner_key="music",
                            shell_rect=rect,
                            shell_path=self._music_reactive_overlay_clip_path(rect, radius, expanded, plan.ambience_style),
                        )
                    self._execute_music_surface_paint_step(step, painter, rect, radius, expanded, plan.ambience_style)
                finally:
                    self._record_visual_paint_section(f"music.{step}", step_started_at)
        finally:
            self._record_visual_paint_section("music.total", total_started_at)

    def _sync_personal_dj_island(self, now: float) -> bool:
        """Read immutable provider state on the existing tick, never during paint."""
        provider = getattr(self, "data_provider", None)
        getter = getattr(provider, "get_personal_dj_presentation", None)
        frame_getter = getattr(provider, "get_owned_personal_dj_audio_frame", None)
        try:
            state = getter() if callable(getter) else None
            speaking = isinstance(state, dict) and state.get("speaking") is True
            pending = isinstance(state, dict) and state.get("introduction_pending") is True
            failed = isinstance(state, dict) and state.get("introduction_failed") is True
            pending = pending and not state.get("is_preview", False)
            failed = failed and not state.get("is_preview", False)
            presenting = speaking or pending or failed
            title = str(state.get("next_title", "")) if presenting else ""
            token = state.get("token") if presenting else None
        except Exception:
            state = None
            pending = failed = presenting = False
            speaking, title, token = False, "", None
        try:
            frame = frame_getter() if speaking and callable(frame_getter) else None
        except Exception:
            # FFT is optional presentation data, never authority for whether
            # the independent DJ decoder is currently speaking.
            frame = None
        artist = str(getattr(self, "artist", "") or "") if presenting else ""
        status = ("failed" if failed else str(state.get("state", "loading"))
            if pending and not speaking else "")
        changed = (speaking, title, token, artist, status) != (getattr(self, "_island_dj_speaking", False),
            getattr(self, "_island_dj_title", ""), getattr(self, "_island_dj_token", None),
            getattr(self, "_island_dj_artist", ""), getattr(self, "_island_dj_status", ""))
        snapshot = state if isinstance(state, dict) else {}
        completion = snapshot.get("completion_id", 0)
        completion = completion if isinstance(completion, int) else 0
        seen = int(getattr(self, "_island_dj_completion_seen", 0))
        current_track = str(getattr(self, "media_track_id", "") or "")
        island_active = (not bool(getattr(self, "_music_surface_active", False))
            and not bool(getattr(self, "scene_suppressed", False)))
        try:
            island_active = island_active and self.isVisible()
        except RuntimeError:
            island_active = False
        if speaking and not snapshot.get("is_preview", False) and island_active:
            self._island_dj_observed_track = current_track
        if completion > seen:
            # Consume completions even while hidden. A later mode return must
            # not replay an old narration's expansion.
            self._island_dj_completion_seen = completion
            completed_at = snapshot.get("completed_at", 0.0)
            fresh = isinstance(completed_at, (int, float)) and 0.0 <= now - completed_at <= .5
            if (fresh and island_active and not speaking and not snapshot.get("is_preview", False)
                    and current_track and snapshot.get("completed_track_id") == current_track
                    and getattr(self, "_island_dj_observed_track", "") == current_track):
                self._island_dj_outro_started = now
                self._island_dj_outro_until = now + .32 + 2.0
                self._island_dj_outro_track = current_track
                self.left_stack_at = 0.0
                self.hover_armed_key = ""
                self._set_expanded(True)
                changed = True
        until = float(getattr(self, "_island_dj_outro_until", 0.0))
        if until:
            canceled = (presenting or not island_active or completion == 0
                or current_track != getattr(self, "_island_dj_outro_track", ""))
            if canceled or now >= until:
                self._island_dj_outro_until = 0.0
                # A new narration owns the shell; hidden surfaces should not
                # keep the preceding automatic expansion when they return.
                self._set_expanded(False)
                changed = True
        if token != getattr(self, "_island_dj_token", None):
            self._island_dj_started = now
        self._island_dj_speaking, self._island_dj_title = speaking, title
        self._island_dj_artist = artist
        self._island_dj_status = status
        self._island_dj_token, self._island_dj_frame = token, frame
        return changed

    def _personal_dj_island_presenting(self) -> bool:
        return bool(getattr(self, "_island_dj_speaking", False)
            or getattr(self, "_island_dj_status", ""))

    def _personal_dj_island_outro_progress(self) -> float:
        if not float(getattr(self, "_island_dj_outro_until", 0.0)):
            return 1.0
        elapsed = (time.monotonic() - float(getattr(self, "_island_dj_outro_started", 0.0))) / .32
        progress = max(0.0, min(1.0, elapsed))
        return progress * progress * (3.0 - 2.0 * progress)

    def _paint_personal_dj_island(self, painter: QPainter, rect: QRectF):
        """Large independent hollow rings and title on the existing ambience."""
        painter.save()
        path = self._music_reactive_overlay_clip_path(rect, self._music_shell_radius(rect, False), False,
            self._effective_music_ambience_style())
        painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
        self.music_button_rects = {}
        status = str(getattr(self, "_island_dj_status", ""))
        compact_status = bool(status) and rect.height() < self._u(72)
        icon_area = rect.adjusted(0., 0., 0., -self._u(20)) if status and not compact_status else rect
        side = min(icon_area.height() * .90, self._u(100))
        icon = QRectF(rect.left() + self._u(13), icon_area.center().y() - side / 2, side, side)
        ink = self._personal_dj_island_color()
        paint_personal_dj_icon(painter, icon,
            ink, neutral=QColor(242, 242, 245), bands=getattr(self, "_island_dj_frame", None),
            max_lightening=float(getattr(self, "_island_dj_lightening", .60)),
            elapsed=max(0., time.monotonic() - getattr(self, "_island_dj_started", 0.)))
        status_text = {"failed": "播报加载失败", "ready": "播报已准备好"}.get(status, "播报加载中")
        if status and not compact_status:
            # Loading/fault evidence is independent of idle controls, and is
            # never represented as measured speech or a completed intro.
            painter.setFont(self._font(8, QFont.Weight.Medium))
            painter.setPen(self._music_meta_text_color(190))
            painter.drawText(QRectF(icon.left(), rect.bottom() - self._u(19), icon.width(), self._u(15)),
                Qt.AlignmentFlag.AlignCenter, status_text)
        title_rect = QRectF(icon.right() + self._u(16), rect.top(),
                            max(0., rect.right() - icon.right() - self._u(32)), rect.height())
        artist = str(getattr(self, "_island_dj_artist", "") or getattr(self, "artist", "") or "")
        if artist or compact_status:
            block_height = min(rect.height() - self._u(4), self._u(64))
            title_rect.setTop(rect.center().y() - block_height / 2)
            title_rect.setHeight(block_height * .58)
        font = self._font(min(22., max(12., rect.height() / self.VISUAL_SCALE * .32)), QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(self._music_lyric_text_color())
        title = getattr(self, "_island_dj_title", "") or getattr(self, "track_title", "")
        title = QFontMetrics(font).elidedText(title, Qt.TextElideMode.ElideRight, max(1, int(title_rect.width())))
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter, title)
        if artist or compact_status:
            gap = min(self._u(4), block_height * .05)
            artist_rect = QRectF(title_rect.left(), title_rect.bottom() + gap,
                title_rect.width(), block_height - title_rect.height() - gap)
            artist_size = min(11., max(7., artist_rect.height() / self._u(1) * .52))
            artist_font = self._font(artist_size, QFont.Weight.Medium)
            painter.setFont(artist_font)
            painter.setPen(self._music_meta_text_color(176))
            if compact_status:
                # A taskbar-height shell cannot fit a full caption beneath its
                # rings. Reserve actual text width beside the artist instead.
                status_width = min(artist_rect.width(), float(QFontMetrics(artist_font).horizontalAdvance(status_text)) + self._u(2))
                status_rect = QRectF(artist_rect.right() - status_width, artist_rect.top(), status_width, artist_rect.height())
                painter.drawText(status_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, status_text)
                artist_rect.setRight(max(artist_rect.left(), status_rect.left() - self._u(8)))
            artist = QFontMetrics(artist_font).elidedText(artist, Qt.TextElideMode.ElideRight, max(1, int(artist_rect.width())))
            painter.drawText(artist_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter, artist)
        painter.restore()

    def _personal_dj_island_color(self) -> QColor:
        """Use the shared characteristic role, never invalid gray lyric fill."""
        palette = self._solid_album_surface_palette()
        style = self._effective_music_ambience_style()
        backdrop = (solid_album_background_color(palette) if style == "solid_album"
            else QColor(255, 255, 255) if style == "pure_white" else QColor(28, 28, 30))
        key = (palette.characteristic.rgba(), palette.primary.rgba(), backdrop.rgba())
        if key != getattr(self, "_island_dj_color_key", None):
            self._island_dj_color_key = key
            self._island_dj_color = future_lyric_color(palette.characteristic, backdrop,
                use_light=relative_luminance(backdrop) < .11, primary=palette.primary)
            self._island_dj_lightening = personal_dj_lightening_limit(self._island_dj_color, backdrop)
        return QColor(self._island_dj_color)

    def _execute_music_surface_paint_step(
        self,
        step: str,
        painter: QPainter,
        rect: QRectF,
        radius: float,
        expanded: bool,
        ambience_style: str,
    ):
        if step == "dual_panel_background":
            self._paint_dual_panel_music_background(painter, rect, radius, ambience_style)
        elif step == "pure_white_background":
            self._paint_pure_white_music_background(painter, rect, radius)
        elif step == "solid_album_background":
            self._paint_solid_album_music_background(painter, rect, radius)
        elif step == "yaoguang_expanded_background":
            self._paint_yaoguang_music_background(painter, rect, radius, expanded)
        elif step == "yaoguang_compact_background":
            self._paint_yaoguang_compact_music_background(painter, rect, radius)
        elif step == "pure_black_background":
            self._paint_pure_black_music_background(painter, rect, radius)
        elif step == "bilateral_diffraction_ambience":
            self._paint_bilateral_diffraction_ambience(painter, rect, radius, expanded)
        elif step == "glass_body":
            self._paint_music_glass_body(painter, rect, radius)
        elif step == "bottom_ambience":
            self._paint_bottom_ambience(painter, rect, radius, expanded)
        elif step == "blur_mask":
            self._paint_music_blur_mask(painter, rect, radius, expanded)
        elif step == "light_wave":
            self._paint_music_light_wave(painter, rect, radius, expanded)
        elif step == "reactive_overlay":
            self._paint_music_reactive_background_overlay(painter, rect, radius, expanded, ambience_style)
        elif step == "surface_transition":
            self._paint_music_surface_transition_wash(painter, rect, radius, expanded)
        elif step == "expanded_content":
            self._paint_music_expanded(painter, rect)
        elif step == "compact_content":
            self._paint_music_compact(painter, rect)
        elif step == "yaoguang_compact_frame_overlay":
            self._paint_yaoguang_compact_frame_overlay(painter, rect)
        elif step == "border":
            self._paint_music_border(painter, rect, radius)
        else:
            raise ValueError(f"unknown music surface paint step: {step}")

    def _dual_panel_music_rects(self, rect: QRectF) -> tuple[QRectF, QRectF]:
        top_h = max(self._u(82.0), min(rect.height() - self._u(74.0), rect.height() * 0.52))
        top_h = max(self._u(58.0), min(rect.height() - self._u(48.0), top_h))
        lyric_panel = QRectF(rect.left(), rect.top(), rect.width(), top_h)
        info_panel = QRectF(rect.left(), lyric_panel.bottom(), rect.width(), rect.bottom() - lyric_panel.bottom())
        return lyric_panel, info_panel

    def _dual_panel_upper_path(self, rect: QRectF, radius: float) -> QPainterPath:
        if self._top_notch_active():
            path = QPainterPath()
            path.addRect(QRectF(rect))
            return path
        r = max(0.0, min(float(radius), rect.width() / 2.0, rect.height()))
        path = QPainterPath()
        path.moveTo(rect.left(), rect.bottom())
        path.lineTo(rect.left(), rect.top() + r)
        if r > 0.0:
            path.quadTo(rect.left(), rect.top(), rect.left() + r, rect.top())
        path.lineTo(rect.right() - r, rect.top())
        if r > 0.0:
            path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + r)
        path.lineTo(rect.right(), rect.bottom())
        path.lineTo(rect.left(), rect.bottom())
        path.closeSubpath()
        return path

    def _dual_panel_lower_path(self, rect: QRectF, radius: float) -> QPainterPath:
        if self._stick_notch_active():
            path = QPainterPath()
            path.addRect(QRectF(rect))
            return path
        r = max(0.0, min(float(radius), rect.width() / 2.0, rect.height()))
        path = QPainterPath()
        path.moveTo(rect.left(), rect.top())
        path.lineTo(rect.right(), rect.top())
        path.lineTo(rect.right(), rect.bottom() - r)
        if r > 0.0:
            path.quadTo(rect.right(), rect.bottom(), rect.right() - r, rect.bottom())
        path.lineTo(rect.left() + r, rect.bottom())
        if r > 0.0:
            path.quadTo(rect.left(), rect.bottom(), rect.left(), rect.bottom() - r)
        path.lineTo(rect.left(), rect.top())
        path.closeSubpath()
        return path

    def _paint_dual_panel_music_background(self, painter: QPainter, rect: QRectF, radius: float, ambience_style: str):
        if ambience_style == "solid_album":
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.fillPath(self._music_shell_path(rect, radius, True), self._solid_album_background_color())
            painter.restore()
            return
        lyric_panel, info_panel = self._dual_panel_music_rects(rect)
        lower_light = ambience_style == "pure_white"
        lower_fill = QColor(255, 255, 255, 255) if lower_light else QColor(0, 0, 0, 255)
        upper_path = self._dual_panel_upper_path(lyric_panel, radius)
        lower_path = self._dual_panel_lower_path(info_panel, radius)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(lower_path, lower_fill)

        if ambience_style == "pure_white":
            painter.fillPath(upper_path, QColor(224, 225, 226, 255))
        elif ambience_style == "pure_black":
            painter.fillPath(upper_path, QColor(34, 35, 37, 255))
        else:
            self._paint_dual_panel_dynamic_upper_background(painter, rect, radius, ambience_style, lyric_panel)

        divider = QColor(0, 0, 0, 10) if lower_light else QColor(255, 255, 255, 18)
        painter.fillRect(QRectF(rect.left(), info_panel.top() - self._u(0.6), rect.width(), self._u(1.2)), divider)
        painter.restore()

    def _paint_dual_panel_dynamic_upper_background(self, painter: QPainter, rect: QRectF, radius: float, ambience_style: str, lyric_panel: QRectF):
        panel = QRectF(lyric_panel)
        layer_w = max(1, int(math.ceil(panel.width())))
        layer_h = max(1, int(math.ceil(panel.height())))
        layer = QImage(layer_w, layer_h, QImage.Format.Format_ARGB32_Premultiplied)
        if ambience_style == "solid_album":
            layer.fill(self._solid_album_background_color())
        else:
            layer.fill(QColor(0, 0, 0, 255))
        layer_painter = QPainter(layer)
        layer_painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        layer_rect = QRectF(0.0, 0.0, float(layer_w), float(layer_h))
        layer_radius = min(radius, layer_rect.width() / 2.0, layer_rect.height())
        had_instance_shell_path = "_music_shell_path" in getattr(self, "__dict__", {})
        original_shell_path = getattr(self, "_music_shell_path")

        def upper_panel_shell_path(path_rect: QRectF, path_radius: float, expanded: bool = False) -> QPainterPath:
            return self._dual_panel_upper_path(QRectF(path_rect), float(path_radius))

        try:
            self._music_shell_path = upper_panel_shell_path
            plan = presentation_music_background_layer_paint_plan(
                expanded=True,
                ambience_style=ambience_style,
                lyric_color_fill_paints_background=False,
            )
            for step in plan.steps:
                self._execute_music_surface_paint_step(step, layer_painter, layer_rect, layer_radius, True, plan.ambience_style)
        finally:
            if had_instance_shell_path:
                self._music_shell_path = original_shell_path
            else:
                self.__dict__.pop("_music_shell_path", None)
            layer_painter.end()

        painter.save()
        painter.setClipPath(self._dual_panel_upper_path(panel, radius))
        painter.drawImage(panel, layer, QRectF(0.0, 0.0, float(layer_w), float(layer_h)))
        painter.restore()

    def _solid_music_background_renderer(self) -> SolidMusicBackgroundRenderer:
        renderer = getattr(self, "_solid_music_background_renderer_cache", None)
        if not isinstance(renderer, SolidMusicBackgroundRenderer):
            renderer = SolidMusicBackgroundRenderer(self)
            self._solid_music_background_renderer_cache = renderer
        return renderer

    def _native_music_background_renderer(self) -> NativeMusicBackgroundRenderer:
        renderer = getattr(self, "_native_music_background_renderer_cache", None)
        if not isinstance(renderer, NativeMusicBackgroundRenderer):
            renderer = NativeMusicBackgroundRenderer(self)
            self._native_music_background_renderer_cache = renderer
        return renderer

    def _paint_pure_black_music_background(self, painter: QPainter, rect: QRectF, radius: float):
        self._solid_music_background_renderer().paint_pure_black(painter, rect, radius)

    def _paint_pure_white_music_background(self, painter: QPainter, rect: QRectF, radius: float):
        self._solid_music_background_renderer().paint_pure_white(painter, rect, radius)

    def _paint_solid_album_music_background(self, painter: QPainter, rect: QRectF, radius: float):
        self._solid_music_background_renderer().paint_solid_album(painter, rect, radius)

    def _reactive_album_accent_pair(self, light_surface: bool) -> tuple[QColor, QColor]:
        return self._solid_music_background_renderer().reactive_album_accent_pair(light_surface)

    def _paint_solid_surface_reactive_accent(self, painter: QPainter, rect: QRectF, radius: float, ambience_style: str, light_surface: bool):
        self._solid_music_background_renderer().paint_reactive_accent(painter, rect, radius, ambience_style, light_surface)

    def _music_reactive_overlay_clip_path(self, rect: QRectF, radius: float, expanded: bool, ambience_style: str) -> QPainterPath:
        style = str(ambience_style or "")
        if style == "yaoguang":
            if not expanded:
                return self._yaoguang_compact_inner_path(rect)
            frame = self._yaoguang_frame_width(rect, expanded=True)
            inner = rect.adjusted(frame, frame, -frame, -frame)
            inner_radius = self._yaoguang_layer_radius(inner, radius, frame * 0.35)
            return self._rounded_path(inner, inner_radius)
        return self._music_shell_path(rect, radius, expanded)

    def _paint_music_reactive_background_overlay(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool, ambience_style: str):
        # Music-reactive visuals are handled inside each native background style.
        # A generic overlay made light styles flash and washed dark styles gray.
        return

    def _yaoguang_frame_width(self, rect: QRectF, expanded: bool) -> float:
        frame = max(4.0, min(rect.width(), rect.height()) * (0.12 if expanded else 0.17))
        return min(frame, max(2.0, min(rect.width(), rect.height()) * 0.34))

    def _yaoguang_layer_radius(self, rect: QRectF, radius: float, inset: float = 0.0) -> float:
        if rect.isNull():
            return 0.0
        base = max(0.0, float(radius) - float(inset))
        if self._taskbar_compact_active():
            base = min(base, self._taskbar_synced_corner_radius(rect))
        return min(float(rect.width()) / 2.0, float(rect.height()) / 2.0, base)

    def _paint_yaoguang_soft_rect(self, painter: QPainter, rect: QRectF, radius: float, color: QColor, softness: float):
        if rect.isNull() or rect.width() <= 0.0 or rect.height() <= 0.0:
            return
        strength = max(0.0, min(2.0, float(softness)))
        if strength > 0.0:
            normalized = min(1.0, strength)
            extra = max(0.0, strength - 1.0)
            spread = min(rect.width(), rect.height()) * (0.10 + 0.28 * normalized + 0.20 * extra)
            passes = max(4, min(26, int(round(4 + normalized * 14 + extra * 8))))
            for step in range(passes, 0, -1):
                t = step / passes
                grow = spread * t
                glow = QColor(color)
                glow.setAlpha(max(2, int(color.alpha() * (0.08 + 0.13 * normalized + 0.05 * extra) * (1.0 - t * 0.68))))
                glow_rect = rect.adjusted(-grow, -grow, grow, grow)
                painter.fillPath(self._rounded_path(glow_rect, radius + grow), glow)
        painter.fillPath(self._rounded_path(rect, radius), color)

    def _paint_yaoguang_music_background(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool = False):
        outer = self._yaoguang_outer_color()
        hue, saturation, value, _alpha = outer.getHsvF()
        if hue < 0:
            hue = 0.72
        mid = QColor.fromHsvF(hue, max(0.40, saturation * 0.62), min(0.98, max(0.74, value + 0.16)), 1.0)
        pale = QColor.fromHsvF(hue, max(0.16, saturation * 0.28), 0.93, 1.0)
        black = QColor(2, 1, 5, 255)
        frame = self._yaoguang_frame_width(rect, expanded)
        inner_rect = rect.adjusted(frame, frame, -frame, -frame)
        if inner_rect.width() <= 1.0 or inner_rect.height() <= 1.0:
            inner_rect = rect.adjusted(1.0, 1.0, -1.0, -1.0)
        inner_radius = self._yaoguang_layer_radius(inner_rect, radius, frame * 0.35)

        if expanded:
            color_rect = QRectF(inner_rect)
            mid_rect = inner_rect.adjusted(inner_rect.width() * 0.04, inner_rect.height() * 0.18, -inner_rect.width() * 0.04, -inner_rect.height() * 0.18)
            pale_rect = inner_rect.adjusted(inner_rect.width() * 0.07, inner_rect.height() * 0.34, -inner_rect.width() * 0.07, -inner_rect.height() * 0.34)
        else:
            color_rect = QRectF(inner_rect)
            mid_rect = inner_rect.adjusted(inner_rect.width() * 0.085, 0.0, -inner_rect.width() * 0.085, 0.0)
            pale_rect = inner_rect.adjusted(inner_rect.width() * 0.165, 0.0, -inner_rect.width() * 0.165, 0.0)

        color_radius = min(color_rect.width() / 2.0, color_rect.height() / 2.0, inner_radius)
        if expanded:
            mid_radius = min(mid_rect.width() / 2.0, mid_rect.height() / 2.0, max(2.0, inner_radius * 0.76))
            pale_radius = min(pale_rect.width() / 2.0, pale_rect.height() / 2.0, max(2.0, mid_radius * 0.64))
        else:
            mid_radius = min(mid_rect.width() / 2.0, mid_rect.height() / 2.0, inner_radius)
            pale_radius = min(pale_rect.width() / 2.0, pale_rect.height() / 2.0, inner_radius)

        strength = max(0, min(200, int(getattr(self, "yaoguang_blur_strength", 58))))
        softness = strength / 100.0

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        shell = self._music_shell_path(rect, radius, expanded)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(shell, black)
        inner_path = self._rounded_path(inner_rect, inner_radius)
        painter.setClipPath(inner_path)
        self._paint_yaoguang_soft_rect(painter, color_rect, color_radius, outer, softness * 0.72)
        self._paint_yaoguang_soft_rect(painter, mid_rect, mid_radius, mid, softness)
        self._paint_yaoguang_soft_rect(painter, pale_rect, pale_radius, pale, min(1.0, softness * 1.12))

        sheen = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
        sheen.setColorAt(0.00, QColor(255, 255, 255, 10))
        sheen.setColorAt(0.46, QColor(255, 255, 255, 30))
        sheen.setColorAt(1.00, QColor(0, 0, 0, 18))
        painter.fillPath(self._rounded_path(mid_rect, mid_radius), QBrush(sheen))
        painter.restore()

    def _paint_music_surface_transition_wash(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        if self._solid_album_music_active():
            return
        mode = str(getattr(self, "music_surface_transition_mode", "") or "")
        started_at = float(getattr(self, "music_surface_transition_started_at", 0.0) or 0.0)
        if not mode or started_at <= 0.0:
            return
        progress = self._transition_progress_for_surface()
        if progress >= 1.0 and not str(getattr(self, "music_surface_transition_mode", "") or ""):
            return
        primary, secondary = self._current_music_surface_colors()
        bridge = self._mix_color(primary, secondary, 0.50)
        white_mode = mode in {"white_bridge", "white_to_album"}
        base_alpha = 238 if white_mode else (178 if expanded else 158)
        direction = -1 if int(getattr(self, "music_surface_transition_direction", 1) or 1) < 0 else 1
        start_x = rect.left() if direction >= 0 else rect.right()
        end_x = rect.right() if direction >= 0 else rect.left()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setClipPath(self._music_shell_path(rect, radius, expanded))
        fill = QLinearGradient(start_x, rect.top(), end_x, rect.bottom())
        fill.setColorAt(0.00, QColor(primary.red(), primary.green(), primary.blue(), base_alpha))
        fill.setColorAt(0.52, QColor(bridge.red(), bridge.green(), bridge.blue(), max(72, int(base_alpha * 0.86))))
        fill.setColorAt(1.00, QColor(secondary.red(), secondary.green(), secondary.blue(), max(58, int(base_alpha * 0.74))))
        painter.fillRect(rect, fill)

        band_progress = max(0.0, min(1.0, progress))
        band_center = rect.left() + rect.width() * band_progress if direction >= 0 else rect.right() - rect.width() * band_progress
        band_width = rect.width() * (0.34 if white_mode else 0.24)
        band = QLinearGradient(band_center - band_width, 0, band_center + band_width, 0)
        band.setColorAt(0.00, QColor(255, 255, 255, 0))
        band.setColorAt(0.48, QColor(255, 255, 255, 76 if white_mode else 34))
        band.setColorAt(1.00, QColor(255, 255, 255, 0))
        painter.fillRect(rect, band)
        painter.restore()

    def _compact_lyric_vertical_padding(self, two_line: bool, rect: QRectF | None = None) -> tuple[float, float]:
        if self._taskbar_edge_compact_active():
            height = float(QRectF(rect).height()) if rect is not None and not QRectF(rect).isNull() else self._u(34.0)
            pad = max(self._u(3.0), min(self._u(5.0), height * 0.11))
            return pad, pad
        return self._u(7 if two_line else 3), self._u(6.0)

    def _yaoguang_compact_cover_rect(self, rect: QRectF) -> QRectF:
        if self._taskbar_edge_compact_active():
            return self._compact_cover_rect(rect)
        try:
            two_line = bool(self._has_current_two_line_lyric())
        except Exception:
            two_line = False
        cover_size = min(float(rect.height()) - self._u(18.0), self._u(40.0 if two_line else 38.0))
        cover_size = max(self._u(28.0), cover_size)
        return QRectF(rect.left() + self._u(8.0), rect.center().y() - cover_size / 2.0, cover_size, cover_size)

    def _yaoguang_compact_background_rect(self, rect: QRectF) -> QRectF:
        if not self._compact_lyric_cover_enabled():
            return QRectF(rect)
        cover = self._yaoguang_compact_cover_rect(rect)
        gap = max(self._u(16.0), min(self._u(24.0), rect.width() * 0.052))
        min_width = min(rect.width(), max(self._u(88.0), rect.height() * 2.25))
        left = min(cover.right() + gap, rect.right() - min_width)
        left = max(rect.left(), left)
        return QRectF(left, rect.top(), max(1.0, rect.right() - left), rect.height())

    def _yaoguang_compact_inner_rect(self, rect: QRectF) -> QRectF:
        capsule = self._yaoguang_compact_background_rect(rect)
        if capsule.isNull() or capsule.width() <= 1.0 or capsule.height() <= 1.0:
            return QRectF()
        frame = self._yaoguang_frame_width(capsule, expanded=False)
        inner = capsule.adjusted(frame, frame, -frame, -frame)
        if inner.width() <= 1.0 or inner.height() <= 1.0:
            return QRectF()
        return inner

    def _yaoguang_compact_inner_path(self, rect: QRectF) -> QPainterPath:
        capsule = self._yaoguang_compact_background_rect(rect)
        inner = self._yaoguang_compact_inner_rect(rect)
        if capsule.isNull() or inner.isNull() or inner.width() <= 1.0 or inner.height() <= 1.0:
            return QPainterPath()
        radius = self._music_shell_radius(capsule, expanded=False)
        frame = self._yaoguang_frame_width(capsule, expanded=False)
        inner_radius = self._yaoguang_layer_radius(inner, radius, frame * 0.35)
        return self._rounded_path(inner, inner_radius)

    def _yaoguang_compact_text_rect(self, rect: QRectF, top_padding: float, bottom_padding: float) -> QRectF:
        capsule = self._yaoguang_compact_background_rect(rect)
        frame = self._yaoguang_frame_width(capsule, expanded=False)
        inner = self._yaoguang_compact_inner_rect(rect)
        if inner.isNull():
            inner = capsule.adjusted(frame, frame, -frame, -frame)
        left = inner.left() + self._u(8.0)
        right = inner.right() - self._u(10.0)
        top = max(rect.top() + top_padding, inner.top())
        bottom = min(rect.bottom() - bottom_padding, inner.bottom())
        if bottom <= top:
            top = inner.top()
            bottom = inner.bottom()
        return QRectF(left, top, max(1.0, right - left), max(1.0, bottom - top))

    def _compact_cover_rect(self, rect: QRectF) -> QRectF:
        two_line = self._has_current_two_line_lyric()
        if self._taskbar_edge_compact_active():
            top_padding, bottom_padding = self._compact_lyric_vertical_padding(two_line, rect)
            cover_size = min(rect.height() - top_padding - bottom_padding, self._u(38.0 if two_line else 30.0))
            cover_size = max(self._u(20.0), cover_size)
            return QRectF(rect.left() + self._u(9), rect.center().y() - cover_size / 2.0, cover_size, cover_size)
        if two_line:
            cover_size = min(rect.height() - self._u(14.0), self._u(50.0))
            y_offset = self._u(1.0)
        else:
            cover_size = self._u(30.0)
            y_offset = 0.0
        return QRectF(rect.left() + self._u(9), rect.center().y() - cover_size / 2.0 + y_offset, cover_size, cover_size)

    def _compact_cover_radius(self, rect: QRectF, two_line: bool) -> float:
        if self._taskbar_edge_compact_active():
            return self._music_shell_radius(rect, expanded=False)
        return self._u(10.0 if two_line else 8.0)

    def _paint_music_glass_body(self, painter: QPainter, rect: QRectF, radius: float):
        path = self._music_shell_path(rect, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        body = QLinearGradient(0, rect.top(), 0, rect.bottom())
        body.setColorAt(0.00, QColor(4, 8, 18, 255))
        body.setColorAt(0.30, QColor(6, 11, 24, 255))
        body.setColorAt(0.66, QColor(8, 16, 32, 255))
        body.setColorAt(1.00, QColor(12, 24, 44, 255))
        painter.fillPath(path, body)

        shade = QLinearGradient(0, rect.top(), 0, rect.bottom())
        shade.setColorAt(0.0, QColor(255, 255, 255, 18))
        shade.setColorAt(0.26, QColor(255, 255, 255, 6))
        shade.setColorAt(0.70, QColor(0, 0, 0, 0))
        shade.setColorAt(1.0, QColor(0, 0, 0, 30))
        painter.fillPath(path, shade)

    def _paint_music_border(self, painter: QPainter, rect: QRectF, radius: float):
        stroke = self._u(1.0)
        inner = rect.adjusted(stroke, stroke, -stroke, -stroke)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 30), stroke))
        painter.drawPath(self._music_shell_path(inner, max(1.0, radius - stroke)))

        painter.setPen(QPen(QColor(255, 255, 255, 13), stroke))
        if not self._edge_notch_active():
            painter.drawLine(QPointF(rect.left() + radius * 0.9, rect.top() + self._u(1.4)), QPointF(rect.right() - radius * 0.9, rect.top() + self._u(1.4)))

    def _paint_music_blur_mask(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        painter.save()
        stroke = self._u(1.0)
        bleed = self._u(1.0)
        paint_rect = rect.adjusted(-bleed, -bleed, bleed, bleed)
        painter.setClipPath(self._music_shell_path(rect.adjusted(stroke - bleed, stroke - bleed, -stroke + bleed, -stroke + bleed), max(1.0, radius - stroke + bleed), expanded))

        mask = QLinearGradient(0, paint_rect.top(), 0, paint_rect.bottom())
        mask.setColorAt(0.00, QColor(0, 0, 0, 232 if expanded else 218))
        mask.setColorAt(0.24, QColor(0, 0, 0, 218 if expanded else 198))
        mask.setColorAt(0.40, QColor(0, 0, 0, 164 if expanded else 142))
        mask.setColorAt(0.55, QColor(0, 0, 0, 76 if expanded else 62))
        mask.setColorAt(0.72, QColor(0, 0, 0, 18 if expanded else 14))
        mask.setColorAt(0.88, QColor(0, 0, 0, 8))
        mask.setColorAt(1.00, QColor(0, 0, 0, 0))
        painter.fillRect(paint_rect, mask)
        painter.restore()

    def _dominant_album_color(self) -> QColor:
        primary, _secondary = self._current_music_surface_colors()
        return self._safe_wave_color(primary)

    def _safe_wave_color(self, color: QColor) -> QColor:
        hue, sat, val, _alpha = color.getHsvF()
        if hue < 0 or sat < 0.10:
            gray = max(40, min(224, int(val * 255)))
            return QColor(gray, gray, gray)
        sat = max(0.42, min(0.94, sat))
        val = max(0.42, min(0.90, val))
        return QColor.fromHsvF(hue, sat, val, 1.0)

    def _pure_plugin_color(self, color: QColor) -> QColor:
        hue, sat, val, _alpha = color.getHsvF()
        if hue < 0:
            return color
        sat = max(0.92, min(1.0, sat + 0.22))
        val = max(0.88, min(1.0, val + 0.12))
        return QColor.fromHsvF(hue, sat, val, 1.0)

    def _plugin_display_color(self, label: str, color: QColor) -> QColor:
        if str(label or "").strip().upper() == "TIME":
            return QColor(255, 204, 0)
        return QColor(color)

    def _system_dark_mode(self) -> bool:
        return app_dark_mode()

    def _plugin_theme_colors(self) -> dict[str, QColor]:
        if self._system_dark_mode():
            return {
                "shell": QColor(30, 30, 32, 255),
                "shell_expanded": QColor(28, 28, 30, 255),
                "cell": QColor(46, 46, 49, 255),
                "cell_active": QColor(58, 58, 62, 255),
                "stroke": QColor(255, 255, 255, 42),
                "inner_stroke": QColor(0, 0, 0, 45),
                "text": QColor(245, 245, 247, 238),
                "muted": QColor(188, 188, 194, 224),
                "alert": QColor(38, 38, 41, 255),
            }
        return {
            "shell": QColor(248, 248, 252, 255),
            "shell_expanded": QColor(248, 248, 252, 255),
            "cell": QColor(255, 255, 255, 255),
            "cell_active": QColor(255, 255, 255, 255),
            "stroke": QColor(255, 255, 255, 166),
            "inner_stroke": QColor(0, 0, 0, 18),
            "text": QColor(28, 28, 30, 238),
            "muted": QColor(72, 72, 74, 226),
            "alert": QColor(248, 248, 252, 255),
        }

    def _time_reference_palette(self, theme: dict[str, QColor], color: QColor) -> dict[str, QColor]:
        if not self._system_dark_mode():
            return {
                "surface": QColor(241, 242, 246, 255),
                "surface_lowest": QColor(232, 234, 240, 255),
                "module": QColor(255, 255, 252, 255),
                "module_active": QColor(255, 248, 226, 255),
                "module_stroke": QColor(184, 188, 198, 230),
                "handle": QColor(211, 216, 226, 255),
                "divider": QColor(178, 184, 196, 200),
                "accent": QColor(222, 142, 0, 255),
                "accent_soft": QColor(255, 184, 0, 34),
                "accent_track": QColor(222, 142, 0, 76),
                "control_stroke": QColor(184, 116, 0, 210),
                "active": QColor(2, 170, 74, 255),
                "text": QColor(22, 24, 28, 244),
                "muted": QColor(66, 70, 78, 224),
                "muted_dim": QColor(104, 110, 122, 184),
                "thumb": QColor(255, 255, 255, 246),
                "off_track": QColor(224, 228, 236, 236),
                "note_stroke": QColor(124, 134, 150, 195),
                "compact_cell": QColor(255, 255, 252, 248),
                "compact_cell_active": QColor(255, 248, 226, 255),
                "compact_stroke": QColor(178, 184, 196, 210),
                "compact_time_chip": QColor(34, 36, 40, 238),
                "compact_time_text": QColor(255, 184, 0, 255),
                "compact_chip": QColor(246, 248, 252, 245),
                "compact_chip_done": QColor(206, 132, 0, 255),
                "compact_chip_text": QColor(206, 132, 0, 245),
                "compact_done_text": QColor(255, 255, 248, 246),
            }
        return {
            "surface": QColor(19, 19, 19, 255),
            "surface_lowest": QColor(14, 14, 14, 255),
            "module": QColor(28, 27, 27, 255),
            "module_active": QColor(32, 32, 31, 255),
            "module_stroke": QColor(255, 184, 0, 255),
            "handle": QColor(42, 42, 42, 255),
            "divider": QColor(68, 68, 68, 190),
            "accent": QColor(255, 184, 0, 255),
            "accent_soft": QColor(255, 184, 0, 16),
            "accent_track": QColor(255, 184, 0, 46),
            "control_stroke": QColor(255, 184, 0, 255),
            "active": QColor(2, 201, 83, 255),
            "text": QColor(229, 226, 225, 242),
            "muted": QColor(213, 196, 171, 225),
            "muted_dim": QColor(158, 143, 120, 170),
            "thumb": QColor(245, 244, 241, 246),
            "off_track": QColor(19, 19, 19, 230),
            "note_stroke": QColor(142, 150, 164, 185),
            "compact_cell": QColor(42, 42, 42, 226),
            "compact_cell_active": QColor(54, 54, 54, 242),
            "compact_stroke": QColor(170, 170, 170, 150),
            "compact_time_chip": QColor(20, 20, 20, 235),
            "compact_time_text": QColor(255, 184, 0, 255),
            "compact_chip": QColor(26, 26, 26, 235),
            "compact_chip_done": QColor(255, 184, 0, 255),
            "compact_chip_text": QColor(255, 184, 0, 245),
            "compact_done_text": QColor(34, 24, 0, 245),
        }

    def _accent_pair(self) -> tuple[QColor, QColor]:
        base = self._dominant_album_color()
        _primary, secondary = self._current_music_surface_colors()
        companion = self._safe_wave_color(secondary)
        base_hue, base_sat, _base_val, _base_alpha = base.getHsvF()
        companion_hue, companion_sat, _companion_val, _companion_alpha = companion.getHsvF()
        if base_hue < 0 or base_sat < 0.12:
            return self._gray_pair(base)
        if companion_hue < 0 or companion_sat < 0.12 or self._hue_distance(base_hue, companion_hue) < 0.08:
            companion = QColor.fromHsvF((base_hue + 0.16) % 1.0, 0.72, 0.58, 1.0)
        return base, companion

    def _gray_pair(self, color: QColor) -> tuple[QColor, QColor]:
        gray = color.red() if color.red() == color.green() == color.blue() else int(color.valueF() * 255)
        gray = max(56, min(220, gray))
        base = QColor(gray, gray, gray)
        companion = QColor(max(28, gray - 40), max(28, gray - 40), max(28, gray - 40))
        return base, companion

    def _hue_distance(self, a: float, b: float) -> float:
        delta = abs(a - b) % 1.0
        return min(delta, 1.0 - delta)

    def _gray_step(self, color: QColor, amount: float) -> QColor:
        amount = max(0.0, min(1.0, amount))
        base = color.redF() * 255.0
        value = max(28.0, min(234.0, base + amount * 84.0))
        gray = int(value)
        return QColor(gray, gray, gray)

    def _reactive_color(self, color: QColor, hue_shift: float, sat_boost: float, val_boost: float) -> QColor:
        hue, sat, val, _alpha = color.getHsvF()
        if hue < 0:
            hue = 0.58
        return QColor.fromHsvF(
            (hue + hue_shift) % 1.0,
            max(0.0, min(1.0, sat + sat_boost)),
            max(0.0, min(1.0, val + val_boost)),
            1.0,
        )

    def _mix_color(self, a: QColor, b: QColor, amount: float) -> QColor:
        amount = max(0.0, min(1.0, amount))
        return QColor(
            int(a.red() + (b.red() - a.red()) * amount),
            int(a.green() + (b.green() - a.green()) * amount),
            int(a.blue() + (b.blue() - a.blue()) * amount),
        )

    def _duotone_balance_amount(self, expanded: bool) -> float:
        key = "expanded_duotone_balance" if expanded else "compact_duotone_balance"
        return max(0.0, min(1.0, float(getattr(self, key, 50)) / 100.0))

    def _balanced_pair(self, primary: QColor, secondary: QColor, expanded: bool) -> tuple[QColor, QColor]:
        balance = self._duotone_balance_amount(expanded)
        delta = balance - 0.5
        if abs(delta) <= 0.001:
            return primary, secondary
        primary_mix = max(0.0, delta) * 0.44
        secondary_mix = max(0.0, -delta) * 0.44
        return self._mix_color(primary, secondary, primary_mix), self._mix_color(secondary, primary, secondary_mix)

    def _audio_bands(self) -> tuple[float, float, float]:
        return getattr(self, "current_audio_bands", (0.0, 0.0, 0.0))

    def _ambient_mist_color(self, color: QColor, saturation_boost: float = 0.0, lightness_boost: float = 0.0) -> QColor:
        hue, saturation, lightness, _alpha = color.getHslF()
        if hue < 0:
            hue = 0.58
            saturation = 0.52
            lightness = 0.47
        saturation = max(0.44, min(0.86, saturation + saturation_boost))
        lightness = max(0.30, min(0.66, lightness + lightness_boost))
        return QColor.fromHslF(hue, saturation, lightness, 1.0)

    def _transition_progress_for_surface(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else float(now)
        started_at = float(getattr(self, "music_surface_transition_started_at", 0.0) or 0.0)
        if started_at <= 0.0:
            return 1.0
        duration = max(0.18, float(getattr(self, "music_surface_transition_duration", 0.62) or 0.62))
        elapsed = max(0.0, min(1.0, (now - started_at) / duration))
        eased = 1.0 - (1.0 - elapsed) ** 4
        if elapsed >= 1.0:
            self.music_surface_transition_started_at = 0.0
            self.music_surface_transition_mode = ""
        return eased

    def _music_surface_waiting_for_loaded_lyrics(self) -> bool:
        if getattr(self, "timed_lyrics", None):
            return False
        lyrics = [str(line or "").strip() for line in getattr(self, "lyrics", []) or [] if str(line or "").strip()]
        if not lyrics:
            return True
        loading_markers = {"查找歌词中", "寻找歌词中", "加载歌词中"}
        return len(lyrics) == 1 and lyrics[0] in loading_markers

    def _current_music_surface_colors(self, now: float | None = None) -> tuple[QColor, QColor]:
        current = (
            QColor(getattr(self, "album_color", QColor(58, 68, 88))),
            QColor(getattr(self, "secondary_color", QColor(34, 44, 62))),
        )
        started_at = float(getattr(self, "music_surface_transition_started_at", 0.0) or 0.0)
        if started_at <= 0.0:
            return current
        start_primary, start_secondary = getattr(self, "music_surface_transition_from", (QColor(), QColor()))
        target_primary, target_secondary = getattr(self, "music_surface_transition_to", current)
        start_primary = QColor(start_primary) if isinstance(start_primary, QColor) and start_primary.isValid() else current[0]
        start_secondary = QColor(start_secondary) if isinstance(start_secondary, QColor) and start_secondary.isValid() else current[1]
        target_primary = QColor(target_primary) if isinstance(target_primary, QColor) and target_primary.isValid() else current[0]
        target_secondary = QColor(target_secondary) if isinstance(target_secondary, QColor) and target_secondary.isValid() else current[1]
        progress = self._transition_progress_for_surface(now)
        return (
            self._mix_color(start_primary, target_primary, progress),
            self._mix_color(start_secondary, target_secondary, progress),
        )

    def _begin_music_surface_transition(self, target_entry, direction: int = 1, mode: str = "queued"):
        start_primary = QColor(getattr(self, "album_color", QColor(58, 68, 88)))
        start_secondary = QColor(getattr(self, "secondary_color", QColor(34, 44, 62)))
        target_primary = self._entry_color_token(target_entry, "album_color") if target_entry is not None else QColor()
        target_secondary = self._entry_color_token(target_entry, "secondary_color") if target_entry is not None else QColor()
        if not target_primary.isValid() and target_entry is not None:
            image = self._album_art_from_entry(target_entry)
            if not image.isNull():
                target_primary = self._safe_wave_color(self._dominant_color_from_image(image))
                target_secondary = self._related_color_from_qcolor(target_primary, 0.12)
        bridge = str(mode or "") == "white_bridge" or not target_primary.isValid()
        if bridge:
            target_primary = QColor(245, 247, 250)
            target_secondary = QColor(228, 233, 241)
            mode = "white_bridge"
        elif not target_secondary.isValid():
            target_secondary = self._related_color_from_qcolor(target_primary, 0.12)
        self.music_surface_transition_from = (start_primary, start_secondary)
        self.music_surface_transition_to = (target_primary, target_secondary)
        self.music_surface_transition_started_at = time.monotonic()
        self.music_surface_transition_duration = max(0.42, min(0.82, float(getattr(self, "album_island_transition_duration", 0.62) or 0.62)))
        self.music_surface_transition_direction = -1 if direction < 0 else 1
        self.music_surface_transition_mode = str(mode or "queued")

    def _retarget_white_bridge_to_current_album(self):
        target_primary = QColor(getattr(self, "album_color", QColor(58, 68, 88)))
        target_secondary = QColor(getattr(self, "secondary_color", QColor(34, 44, 62)))
        self.music_surface_transition_from = (QColor(245, 247, 250), QColor(228, 233, 241))
        self.music_surface_transition_to = (target_primary, target_secondary)
        self.music_surface_transition_started_at = time.monotonic()
        self.music_surface_transition_duration = max(0.42, min(0.72, float(getattr(self, "album_island_transition_duration", 0.62) or 0.62)))
        self.music_surface_transition_mode = "white_to_album"

    def _related_color_from_qcolor(self, color: QColor, lightness_delta: float = 0.10) -> QColor:
        hue, saturation, lightness, _alpha = color.getHslF()
        if hue < 0:
            hue = 0.58
        related_color = QColor.fromHslF(hue, max(0.28, min(0.78, saturation + 0.08)), max(0.28, min(0.72, lightness + lightness_delta)), 1.0)
        return related_color

    def _dominant_color_from_image(self, image: QImage) -> QColor:
        if image.isNull():
            return QColor()
        scaled = image.scaled(16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
        buckets: dict[tuple[int, int, int], tuple[int, int, int, int]] = {}
        for y in range(scaled.height()):
            for x in range(scaled.width()):
                color = QColor(scaled.pixel(x, y))
                if color.alpha() < 32:
                    continue
                hue, saturation, value, _alpha = color.getHsvF()
                if hue < 0 or value < 0.12:
                    continue
                key = (int(hue * 12), int(saturation * 4), int(value * 4))
                r, g, b, count = buckets.get(key, (0, 0, 0, 0))
                buckets[key] = (r + color.red(), g + color.green(), b + color.blue(), count + 1)
        if not buckets:
            return QColor()
        r, g, b, count = max(buckets.values(), key=lambda item: item[3])
        return QColor(r // max(1, count), g // max(1, count), b // max(1, count))

    def _album_mist_palette(self) -> tuple[QColor, QColor, QColor]:
        album_color, secondary_color = self._current_music_surface_colors()
        primary = self._ambient_mist_color(album_color, 0.18, 0.03)
        secondary = self._ambient_mist_color(secondary_color, 0.20, 0.04)
        bridge = self._mix_color(primary, secondary, 0.50)
        return primary, self._ambient_mist_color(bridge, 0.16, 0.03), secondary

    def _album_mist_palette_for_state(self, expanded: bool) -> tuple[QColor, QColor, QColor]:
        primary, bridge, secondary = self._album_mist_palette()
        primary, secondary = self._balanced_pair(primary, secondary, expanded)
        balance = self._duotone_balance_amount(expanded)
        bridge = self._mix_color(primary, secondary, balance)
        return primary, self._ambient_mist_color(bridge, 0.16, 0.03), secondary

    def _music_flow_amount(self) -> float:
        base = max(0.0, min(1.0, float(getattr(self, "music_flow_intensity", 0.74))))
        return max(0.0, min(1.0, base))

    def _xiaomi_duotone_palette(self) -> tuple[QColor, QColor, QColor]:
        album_color, _secondary_color = self._current_music_surface_colors()
        base = self._safe_wave_color(album_color)
        hue, sat, val, _alpha = base.getHsvF()
        if hue < 0:
            hue = 0.78
            sat = 0.72
            val = 0.62
        main = QColor.fromHsvF(hue, max(0.78, min(1.0, sat + 0.26)), max(0.72, min(0.98, val + 0.18)), 1.0)
        reverse_hue = (hue + 0.50) % 1.0
        accent = QColor.fromHsvF(reverse_hue, max(0.58, min(0.90, sat + 0.08)), max(0.30, min(0.48, val * 0.58)), 1.0)
        bridge = self._mix_color(main, accent, 0.42)
        bridge_h, bridge_s, bridge_v, _ = bridge.getHsvF()
        if bridge_h >= 0:
            bridge = QColor.fromHsvF(bridge_h, max(0.64, bridge_s), max(0.54, bridge_v), 1.0)
        return main, accent, bridge

    def _full_duotone_palette(self) -> tuple[QColor, QColor, QColor]:
        return self._full_duotone_palette_for_state(bool(getattr(self, "expanded", False)))

    def _full_duotone_palette_for_state(self, expanded: bool) -> tuple[QColor, QColor, QColor]:
        album_color, secondary_color = self._current_music_surface_colors()
        base = self._safe_wave_color(album_color)
        companion = self._safe_wave_color(secondary_color)
        hue, sat, val, _alpha = base.getHsvF()
        comp_hue, comp_sat, comp_val, _comp_alpha = companion.getHsvF()
        if hue < 0:
            hue = 0.78
            sat = 0.72
            val = 0.62
        if comp_hue < 0 or comp_sat < 0.14 or self._hue_distance(hue, comp_hue) < 0.06:
            comp_hue = (hue + 0.50) % 1.0
            comp_sat = max(0.48, min(0.78, sat + 0.02))
            comp_val = max(0.30, min(0.58, val * 0.72))
        main = QColor.fromHsvF(hue, max(0.84, min(1.0, sat + 0.28)), max(0.72, min(0.98, val + 0.18)), 1.0)
        accent = QColor.fromHsvF(comp_hue, max(0.48, min(0.80, comp_sat + 0.06)), max(0.22, min(0.42, comp_val * 0.62)), 1.0)
        main, accent = self._balanced_pair(main, accent, expanded)
        bridge = self._mix_color(main, accent, 0.40)
        bridge_h, bridge_s, bridge_v, _ = bridge.getHsvF()
        if bridge_h >= 0:
            bridge = QColor.fromHsvF(bridge_h, max(0.70, bridge_s), max(0.46, min(0.72, bridge_v + 0.08)), 1.0)
        return main, accent, bridge

    def _mist_alpha(self, opacity: float, intensity: float) -> int:
        return int(max(0, min(196, opacity * 255.0 * intensity)))

    def _ambient_boost(self) -> tuple[float, float, float, float]:
        value = max(0.0, min(1.0, float(getattr(self, "ambient_intensity", 0.0))))
        eased = value * value * (3.0 - 2.0 * value)
        return (
            1.04 + eased * 1.04,
            eased,
            1.0 + eased * 0.32,
            1.0 + eased * 0.56,
        )

    def _audio_reactive_runtime_for_current_settings(self) -> AudioReactiveRuntime:
        runtime = getattr(self, "_audio_reactive_runtime", None)
        if not isinstance(runtime, AudioReactiveRuntime):
            runtime = AudioReactiveRuntime()
            self._audio_reactive_runtime = runtime
        intensity_percent = getattr(self, "music_reactive_background_intensity", 55)
        runtime.configure(
            enabled=bool(getattr(self, "music_reactive_background_enabled", False)),
            intensity_percent=intensity_percent,
        )
        return runtime

    def _invalidate_music_reactive_snapshot(self) -> None:
        self._music_reactive_snapshot_cache = None
        self._music_reactive_snapshot_styles = frozenset()

    def _music_reactive_snapshot(self, *ambience_styles: str) -> ReactiveSnapshot:
        runtime = self._audio_reactive_runtime_for_current_settings()
        requested_styles = frozenset(str(style or "") for style in ambience_styles if str(style or ""))
        snapshot_func = getattr(runtime, "snapshot", None)
        read_frame = getattr(runtime, "read_frame", None)
        if callable(snapshot_func) and callable(read_frame):
            frame = read_frame()
            cached = getattr(self, "_music_reactive_snapshot_cache", None)
            cached_styles = getattr(self, "_music_reactive_snapshot_styles", frozenset())
            if (
                isinstance(cached, ReactiveSnapshot)
                and cached.frame == frame
                and requested_styles.issubset(cached_styles)
            ):
                return cached
            styles = tuple(sorted(frozenset(cached_styles) | requested_styles))
            snapshot = snapshot_func(styles)
            self._music_reactive_snapshot_cache = snapshot
            self._music_reactive_snapshot_styles = frozenset(styles)
            return snapshot

        responses = {}
        style_response = getattr(runtime, "style_response", None)
        if callable(style_response):
            responses = {style: style_response(style) for style in sorted(requested_styles)}
        frame = read_frame() if callable(read_frame) else ReactiveFrame()
        return ReactiveSnapshot(frame=frame, style_responses=responses)

    def _music_reactive_level(self) -> float:
        return float(self._music_reactive_state()["energy"])

    def _music_reactive_frame(self, now: float | None = None) -> ReactiveFrame:
        runtime = self._audio_reactive_runtime_for_current_settings()
        if now is not None:
            return runtime.advance_frame(self._audio_bands(), now)
        return runtime.read_frame()

    def _advance_music_reactive_frame(self, now: float | None = None) -> ReactiveFrame:
        frame = self._audio_reactive_runtime_for_current_settings().advance_frame(self._audio_bands(), now)
        self._invalidate_music_reactive_snapshot()
        return frame

    def _music_reactive_state(self) -> dict[str, float | bool]:
        return self._music_reactive_snapshot().state()

    def _music_reactive_style_response(self, ambience_style: str) -> dict[str, float]:
        return self._music_reactive_snapshot(ambience_style).style_response(ambience_style)

    def _boost_mist_color(self, color: QColor, boost: float) -> QColor:
        hue, saturation, lightness, _alpha = color.getHslF()
        if hue < 0:
            return color
        saturation = max(0.0, min(0.92, saturation + boost * 0.36))
        lightness = max(0.0, min(0.84, lightness + boost * 0.15))
        return QColor.fromHslF(hue, saturation, lightness, 1.0)

    def _paint_mist_ellipse(self, painter: QPainter, center: QPointF, radius_x: float, radius_y: float, color: QColor, alpha: int):
        if alpha <= 0:
            return
        painter.save()
        painter.translate(center)
        painter.scale(max(1.0, radius_x), max(1.0, radius_y))
        glow = QRadialGradient(QPointF(0.0, 0.0), 1.0)
        glow.setColorAt(0.00, QColor(min(255, int(color.red() * 1.05 + 14)), min(255, int(color.green() * 1.05 + 14)), min(255, int(color.blue() * 1.05 + 14)), int(alpha * 0.78)))
        glow.setColorAt(0.20, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.58)))
        glow.setColorAt(0.46, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.28)))
        glow.setColorAt(0.70, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.10)))
        glow.setColorAt(0.88, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.025)))
        glow.setColorAt(1.00, QColor(color.red(), color.green(), color.blue(), 0))
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(-1.0, -1.0, 2.0, 2.0))
        painter.restore()

    def _diffraction_frequency_amount(self) -> float:
        return max(0.0, min(1.0, float(getattr(self, "diffraction_frequency", 0.30))))

    def _bilateral_diffraction_ring_count(self) -> int:
        return max(5, min(12, int(round(5 + self._diffraction_frequency_amount() * 7))))

    def _paint_bilateral_diffraction_ambience(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        self._native_music_background_renderer().paint_bilateral_diffraction_ambience(painter, rect, radius, expanded)

    def _paint_bottom_ambience(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        style = self._effective_music_ambience_style()
        if style in {"pure_black", "bilateral_diffraction"}:
            return
        if style == "duotone_full":
            self._paint_full_duotone_ambience(painter, rect, radius, expanded)
            return
        if style == "sea_level":
            self._paint_xiaomi_duotone_ambience(painter, rect, radius, expanded)
            return

        painter.save()
        inset = self._u(1.4)
        bleed = self._u(1.0)
        paint_rect = rect.adjusted(-bleed, -bleed, bleed, bleed)
        clip_rect = rect.adjusted(inset - bleed, inset - bleed, -inset + bleed, -inset + bleed)
        painter.setClipPath(self._music_shell_path(clip_rect, max(1.0, radius - inset + bleed), expanded))
        painter.setPen(Qt.PenStyle.NoPen)

        primary, bridge, secondary = self._album_mist_palette()
        low, mid, high = self._audio_bands()
        now = time.monotonic()
        intensity, boost, size_boost, opacity_boost = self._ambient_boost()
        if not self.media_is_playing:
            intensity *= 0.80
        primary = self._boost_mist_color(primary, boost)
        bridge = self._boost_mist_color(bridge, boost)
        secondary = self._boost_mist_color(secondary, boost)

        layer_top = paint_rect.top() + paint_rect.height() * (0.24 if expanded else 0.30)
        layer_rect = QRectF(paint_rect.left(), layer_top, paint_rect.width(), paint_rect.bottom() - layer_top)
        field = QLinearGradient(0, layer_rect.top(), 0, layer_rect.bottom())
        field.setColorAt(0.00, QColor(bridge.red(), bridge.green(), bridge.blue(), 0))
        field.setColorAt(0.16, QColor(bridge.red(), bridge.green(), bridge.blue(), self._mist_alpha(0.060 + mid * 0.022, intensity)))
        field.setColorAt(0.39, QColor(primary.red(), primary.green(), primary.blue(), self._mist_alpha(0.125 + low * 0.034, intensity)))
        field.setColorAt(0.66, QColor(secondary.red(), secondary.green(), secondary.blue(), self._mist_alpha(0.165 + high * 0.038, intensity)))
        field.setColorAt(1.00, QColor(bridge.red(), bridge.green(), bridge.blue(), self._mist_alpha(0.145 + mid * 0.032, intensity)))
        painter.fillRect(layer_rect, field)

        def wave(duration: float, delay: float, offset: float = 0.0) -> float:
            return math.sin(((now + delay) / duration) * math.tau + offset)

        def breathe(duration: float, delay: float, offset: float = 0.0) -> float:
            return 0.5 + 0.5 * wave(duration, delay, offset)

        color_blocks = (
            (primary, 14.0, 0.0, 0.18, 0.56, 28.0, 11.0, 0.96 * size_boost, 0.38 * size_boost, 0.24 * opacity_boost, 0.118 * opacity_boost, low, 0.0),
            (bridge, 17.0, -4.0, 0.50, 0.60, 32.0, 13.0, 1.18 * size_boost, 0.47 * size_boost, 0.23 * opacity_boost, 0.110 * opacity_boost, mid, 1.3),
            (secondary, 20.0, -8.0, 0.82, 0.55, 26.0, 12.0, 0.94 * size_boost, 0.38 * size_boost, 0.24 * opacity_boost, 0.118 * opacity_boost, high, 2.4),
            (self._mix_color(primary, bridge, 0.38), 23.0, -11.0, 0.35, 0.38, 20.0, 9.0, 0.78 * size_boost, 0.28 * size_boost, 0.16 * opacity_boost, 0.074 * opacity_boost, mid, 3.1),
            (self._mix_color(secondary, bridge, 0.42), 26.0, -15.0, 0.66, 0.41, 22.0, 9.0, 0.74 * size_boost, 0.27 * size_boost, 0.15 * opacity_boost, 0.070 * opacity_boost, high, 4.2),
        )
        for glow_color, duration, delay, x_ratio, y_ratio, drift_x, drift_y, width_ratio, height_ratio, base_opacity, opacity_span, band, offset in color_blocks:
            breath = breathe(duration, delay, offset)
            shimmer = breathe(duration * 1.73, delay * 0.61, offset + 2.2)
            center = QPointF(
                rect.left() + rect.width() * x_ratio + self._u(drift_x) * wave(duration, delay, offset + 0.4),
                layer_rect.top() + layer_rect.height() * y_ratio + self._u(drift_y) * wave(duration, delay, offset + 1.8),
            )
            scale = 0.96 + breath * 0.14
            alpha = self._mist_alpha(base_opacity + opacity_span * shimmer + band * 0.034, intensity)
            rx = rect.width() * (width_ratio + breath * 0.10 + band * 0.018)
            ry = rect.height() * (height_ratio + breath * 0.055 + band * 0.014)
            self._paint_mist_ellipse(painter, center, rx * scale * 1.40, ry * scale * 1.34, glow_color, int(alpha * 0.38))
            self._paint_mist_ellipse(painter, center, rx * scale, ry * scale, glow_color, alpha)

        highlight_breath = breathe(18.0, -5.0, 2.8)
        highlight_center = QPointF(rect.left() + rect.width() * 0.50 + self._u(15.0) * wave(18.0, -5.0, 2.4), layer_rect.top() + layer_rect.height() * 0.46 + self._u(6.0) * wave(18.0, -5.0, 0.8))
        highlight = self._mix_color(QColor(255, 255, 255), bridge, 0.76)
        self._paint_mist_ellipse(
            painter,
            highlight_center,
            rect.width() * (0.20 + highlight_breath * 0.035),
            rect.height() * (0.12 + highlight_breath * 0.024),
            highlight,
            self._mist_alpha((0.045 + highlight_breath * 0.030) * opacity_boost, intensity),
        )

        black_blocks = (
            (0.30, 0.46, 19.0, -2.0, 16.0, 8.0, 0.34 * size_boost, 0.16 * size_boost, 0.055, 0.050, 0.0),
            (0.58, 0.54, 24.0, -9.0, 22.0, 9.0, 0.42 * size_boost, 0.18 * size_boost, 0.060, 0.055, 1.6),
            (0.74, 0.40, 28.0, -14.0, 18.0, 7.0, 0.30 * size_boost, 0.14 * size_boost, 0.045, 0.040, 3.0),
        )
        for x_ratio, y_ratio, duration, delay, drift_x, drift_y, width_ratio, height_ratio, base_opacity, opacity_span, offset in black_blocks:
            breath = breathe(duration, delay, offset)
            center = QPointF(
                rect.left() + rect.width() * x_ratio + self._u(drift_x) * wave(duration, delay, offset + 0.7),
                layer_rect.top() + layer_rect.height() * y_ratio + self._u(drift_y) * wave(duration, delay, offset + 2.1),
            )
            alpha = int(max(0, min(70, (base_opacity + opacity_span * breath) * 255.0 * intensity)))
            self._paint_mist_ellipse(
                painter,
                center,
                rect.width() * (width_ratio + breath * 0.055),
                rect.height() * (height_ratio + breath * 0.035),
                QColor(0, 0, 0),
                alpha,
            )

        top_fade = QLinearGradient(0, paint_rect.top(), 0, paint_rect.bottom())
        top_fade.setColorAt(0.00, QColor(0, 0, 0, 236 if expanded else 226))
        top_fade.setColorAt(0.18, QColor(0, 0, 0, 192 if expanded else 178))
        top_fade.setColorAt(0.30, QColor(0, 0, 0, 50 if expanded else 54))
        top_fade.setColorAt(0.42, QColor(0, 0, 0, 6))
        top_fade.setColorAt(0.56, QColor(0, 0, 0, 0))
        top_fade.setColorAt(1.00, QColor(0, 0, 0, 0))
        painter.fillRect(paint_rect, top_fade)

        painter.restore()

    def _paint_full_duotone_ambience(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        self._native_music_background_renderer().paint_full_duotone_ambience(painter, rect, radius, expanded)

    def _paint_xiaomi_duotone_ambience(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        self._native_music_background_renderer().paint_sea_level_ambience(painter, rect, radius, expanded)

    def _music_wave_path(self, rect: QRectF, center_y: float, amplitude: float, thickness: float, phase: float) -> QPainterPath:
        return self._native_music_background_renderer().music_wave_path(rect, center_y, amplitude, thickness, phase)

    def _sea_level_wave_path(self, rect: QRectF, center_y: float, amplitude: float, thickness: float, phase: float, layer: float = 0.0) -> QPainterPath:
        return self._native_music_background_renderer().sea_level_wave_path(rect, center_y, amplitude, thickness, phase, layer)

    def _paint_music_light_wave(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        style = self._effective_music_ambience_style()
        if style in {"pure_black", "bilateral_diffraction"}:
            return
        if style == "duotone_full":
            return
        if style == "sea_level":
            self._paint_xiaomi_duotone_wave(painter, rect, radius, expanded)
            return

        painter.save()
        inset = self._u(1.4)
        painter.setClipPath(self._music_shell_path(rect.adjusted(inset, inset, -inset, -inset), max(1.0, radius - inset), expanded))
        painter.setPen(Qt.PenStyle.NoPen)

        primary, bridge, secondary = self._album_mist_palette_for_state(expanded)
        low, mid, high = self._audio_bands()
        intensity, boost, _size_boost, opacity_boost = self._ambient_boost()
        if not self.media_is_playing:
            intensity *= 0.72
        primary = self._boost_mist_color(primary, boost)
        bridge = self._boost_mist_color(bridge, boost)
        secondary = self._boost_mist_color(secondary, boost)

        now = time.monotonic()
        phase = self.ambient_phase * 1.8 + now * 0.55
        center_y = rect.top() + rect.height() * (0.69 if expanded else 0.63)
        center_y += self._u(4.5) * math.sin(phase * 0.65) + self._u(3.0) * (low - high)
        glow_h = rect.height() * (0.22 if expanded else 0.30) + self._u(12.0) * mid
        glow_rect = QRectF(rect.left(), center_y - glow_h, rect.width(), glow_h * 2.05)

        vertical = QLinearGradient(0, glow_rect.top(), 0, glow_rect.bottom())
        vertical.setColorAt(0.00, QColor(bridge.red(), bridge.green(), bridge.blue(), 0))
        vertical.setColorAt(0.22, QColor(primary.red(), primary.green(), primary.blue(), self._mist_alpha(0.038 * opacity_boost + low * 0.012, intensity)))
        vertical.setColorAt(0.50, QColor(bridge.red(), bridge.green(), bridge.blue(), self._mist_alpha(0.112 * opacity_boost + mid * 0.024, intensity)))
        vertical.setColorAt(0.74, QColor(secondary.red(), secondary.green(), secondary.blue(), self._mist_alpha(0.082 * opacity_boost + high * 0.018, intensity)))
        vertical.setColorAt(1.00, QColor(primary.red(), primary.green(), primary.blue(), 0))
        painter.fillRect(glow_rect, vertical)

        horizontal = QLinearGradient(rect.left(), 0, rect.right(), 0)
        horizontal.setColorAt(0.00, QColor(primary.red(), primary.green(), primary.blue(), 0))
        horizontal.setColorAt(0.18, QColor(primary.red(), primary.green(), primary.blue(), self._mist_alpha(0.092 * opacity_boost + low * 0.024, intensity)))
        horizontal.setColorAt(0.50, QColor(bridge.red(), bridge.green(), bridge.blue(), self._mist_alpha(0.132 * opacity_boost + mid * 0.032, intensity)))
        horizontal.setColorAt(0.82, QColor(secondary.red(), secondary.green(), secondary.blue(), self._mist_alpha(0.100 * opacity_boost + high * 0.026, intensity)))
        horizontal.setColorAt(1.00, QColor(secondary.red(), secondary.green(), secondary.blue(), 0))

        amplitude = self._u(9.0 if expanded else 4.0) + self._u(9.0 if expanded else 5.0) * (low * 0.58 + mid * 0.34 + high * 0.20)
        thickness = self._u(13.0 if expanded else 6.0) + self._u(8.0 if expanded else 4.5) * mid
        main_path = self._music_wave_path(rect.adjusted(self._u(5.0), 0, -self._u(5.0), 0), center_y, amplitude, thickness, phase)
        painter.fillPath(main_path, QBrush(horizontal))

        upper_path = self._music_wave_path(rect.adjusted(self._u(18.0), 0, -self._u(18.0), 0), center_y - self._u(10.0), amplitude * 0.56, thickness * 0.42, phase + 1.7)
        upper_color = self._mix_color(QColor(255, 255, 255), bridge, 0.55)
        painter.fillPath(upper_path, QColor(upper_color.red(), upper_color.green(), upper_color.blue(), self._mist_alpha(0.024 * opacity_boost + mid * 0.010, intensity)))

        crest = QPainterPath()
        values = self.wave_values or [0.25] * 36
        count = max(2, len(values))
        for i, value in enumerate(values):
            pos = i / (count - 1)
            x = rect.left() + self._u(10.0) + (rect.width() - self._u(20.0)) * pos
            soft_peak = math.sin(pos * math.pi)
            ripple = math.sin(pos * math.tau * 2.0 + phase) * 0.28 + math.sin(pos * math.tau * 3.6 - phase * 0.68) * 0.12
            y = center_y - amplitude * ((value - 0.40) * 0.72 + ripple) * (0.20 + soft_peak * 0.80)
            if i == 0:
                crest.moveTo(x, y)
            else:
                crest.lineTo(x, y)

        crest_gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
        crest_gradient.setColorAt(0.00, QColor(primary.red(), primary.green(), primary.blue(), 0))
        crest_gradient.setColorAt(0.22, QColor(255, 255, 255, self._mist_alpha(0.030 * opacity_boost + low * 0.008, intensity)))
        crest_gradient.setColorAt(0.50, QColor(255, 255, 255, self._mist_alpha(0.058 * opacity_boost + mid * 0.012, intensity)))
        crest_gradient.setColorAt(0.78, QColor(255, 255, 255, self._mist_alpha(0.034 * opacity_boost + high * 0.010, intensity)))
        crest_gradient.setColorAt(1.00, QColor(secondary.red(), secondary.green(), secondary.blue(), 0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QBrush(crest_gradient), self._u(1.6 if expanded else 1.1), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(crest)

        painter.restore()

    def _paint_xiaomi_duotone_wave(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        self._native_music_background_renderer().paint_sea_level_wave(painter, rect, radius, expanded)

    def _advance_music_cover_hover(self, now: float) -> bool:
        motion = getattr(self, "_music_cover_hover_motion", None)
        if motion is None:
            return False
        if (bool(getattr(self, "scene_suppressed", False))
                or bool(getattr(self, "_music_surface_active", False))):
            changed = motion.value != 0.0
            motion.clear(now)
            return changed
        cover = getattr(self, "music_cover_rect", QRectF())
        hovered = (not cover.isEmpty() and cover.contains(getattr(self, "pointer_pos", QPointF(-1, -1)))
                   and self._main_kind() == IslandKind.MUSIC)
        return motion.advance(hovered, now)

    def _paint_interactive_music_cover(self, painter: QPainter, rect: QRectF, radius: float,
                                       image: QImage | None = None, cache_key: str | None = None):
        motion = getattr(self, "_music_cover_hover_motion", None)
        reveal = max(0.0, min(1.0, motion.value)) if motion is not None else 0.0
        painter.save()
        if motion is not None and abs(motion.scale-1.0) > 0.0001:
            painter.translate(rect.center())
            painter.scale(motion.scale, motion.scale)
            painter.translate(-rect.center())
        self._paint_cover(painter, rect, radius, image, cache_key)
        if reveal > 0.001:
            path = self._rounded_path(rect, radius)
            painter.fillPath(path, QColor(0, 0, 0, round(112*reveal)))
            side = min(rect.width(), rect.height())*0.57
            glyph = QRectF(rect.center().x()-side/2.0, rect.center().y()-side/2.0, side, side)
            painter.save()
            painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
            painter.setOpacity(painter.opacity()*reveal)
            mode = "windowed" if bool(getattr(self, "_music_windowed_preference", False)) else "fullscreen"
            paint_presentation_glyph(painter, glyph, mode, QColor(255, 255, 255, 242))
            painter.restore()
        painter.restore()

    def _paint_cover(self, painter: QPainter, rect: QRectF, radius: float, image: QImage | None = None, cache_key: str | None = None):
        path = self._rounded_path(rect, radius)
        painter.save()
        painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
        album_art = image if isinstance(image, QImage) else getattr(self, "album_art", QImage())
        if not album_art.isNull():
            scaled_width = int(max(1, rect.width()))
            scaled_height = int(max(1, rect.height()))
            cache_id = cache_key or str(album_art.cacheKey() or getattr(self, "media_track_id", ""))
            scaled_key = (cache_id, scaled_width, scaled_height)
            scaled_cache = getattr(self, "_scaled_album_art_cache", None)
            if scaled_cache is None:
                scaled_cache = {}
                self._scaled_album_art_cache = scaled_cache
            scaled = scaled_cache.get(scaled_key)
            if scaled is None or scaled.isNull():
                scaled = album_art.scaled(
                    scaled_width,
                    scaled_height,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                # Aspect-fill can produce a tall or wide raster from unusual
                # artwork. Bound retained pixels as well as item count; hits
                # skip this accounting, and an oversized item stays transient.
                pixel_budget = int(getattr(self, "SCALED_ALBUM_ART_CACHE_PIXEL_BUDGET", 2 * 1024 * 1024))
                scaled_pixels = scaled.width() * scaled.height() * scaled.depth() // 8
                if scaled_pixels <= pixel_budget:
                    scaled_cache[scaled_key] = scaled
                    retained_pixels = sum(
                        cached.width() * cached.height() * cached.depth() // 8
                        for cached in scaled_cache.values()
                    )
                    while len(scaled_cache) > int(getattr(self, "SCALED_ALBUM_ART_CACHE_LIMIT", 24)) or retained_pixels > pixel_budget:
                        discarded = scaled_cache.pop(next(iter(scaled_cache)))
                        retained_pixels -= discarded.width() * discarded.height() * discarded.depth() // 8
            source = QRectF(
                (scaled.width() - rect.width()) / 2.0,
                (scaled.height() - rect.height()) / 2.0,
                rect.width(),
                rect.height(),
            )
            painter.drawImage(rect, scaled, source)
            shade = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
            shade.setColorAt(0.00, QColor(255, 255, 255, 42))
            shade.setColorAt(0.36, QColor(255, 255, 255, 6))
            shade.setColorAt(1.00, QColor(0, 0, 0, 42))
            painter.fillPath(path, QBrush(shade))
            painter.setPen(QPen(QColor(255, 255, 255, 48), self._u(1.0)))
            painter.drawPath(path)
            painter.restore()
            return
        album = self._dominant_album_color().lighter(156)
        secondary = self.secondary_color.lighter(148)
        focus = QColor(240, 250, 255)
        fallback = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
        fallback.setColorAt(0.0, QColor(224, 238, 255, 255))
        fallback.setColorAt(0.52, QColor(album.red(), album.green(), album.blue(), 255))
        fallback.setColorAt(1.0, QColor(218, 190, 238, 255))
        painter.fillPath(path, QBrush(fallback))
        gradient = QRadialGradient(rect.center() + QPointF(rect.width() * 0.10, -rect.height() * 0.16), rect.width() * 0.86)
        gradient.setColorAt(0.0, QColor(focus.red(), focus.green(), focus.blue(), 255))
        gradient.setColorAt(0.24, QColor(album.red(), album.green(), album.blue(), 255))
        gradient.setColorAt(0.66, QColor(secondary.red(), secondary.green(), secondary.blue(), 255))
        gradient.setColorAt(1.0, QColor(max(18, album.red() // 4), max(18, album.green() // 4), max(18, album.blue() // 4), 255))
        painter.fillPath(path, QBrush(gradient))
        sheen = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
        sheen.setColorAt(0.00, QColor(255, 255, 255, 58))
        sheen.setColorAt(0.34, QColor(255, 255, 255, 10))
        sheen.setColorAt(1.00, QColor(0, 0, 0, 26))
        painter.fillPath(path, QBrush(sheen))
        highlight = QRadialGradient(rect.center() + QPointF(-rect.width() * 0.12, -rect.height() * 0.18), rect.width() * 0.48)
        highlight.setColorAt(0.0, QColor(234, 250, 255, 232))
        highlight.setColorAt(0.62, QColor(148, 210, 255, 82))
        highlight.setColorAt(1.0, QColor(148, 210, 255, 0))
        painter.fillPath(path, QBrush(highlight))
        painter.setPen(QPen(QColor(255, 255, 255, 48), self._u(1.0)))
        painter.drawPath(path)
        painter.restore()

    def _paint_yaoguang_compact_music_background(self, painter: QPainter, rect: QRectF, radius: float):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(self._music_shell_path(rect, radius, False), QColor(2, 1, 5, 255))
        painter.restore()

        capsule = self._yaoguang_compact_background_rect(rect)
        capsule_radius = self._music_shell_radius(capsule, expanded=False)
        self._paint_yaoguang_music_background(painter, capsule, capsule_radius, expanded=False)

    def _paint_yaoguang_compact_frame_overlay(self, painter: QPainter, rect: QRectF):
        capsule = self._yaoguang_compact_background_rect(rect)
        if capsule.isNull() or capsule.width() <= 1.0 or capsule.height() <= 1.0:
            return
        radius = self._music_shell_radius(capsule, expanded=False)
        frame = self._yaoguang_frame_width(capsule, expanded=False)
        inner = self._yaoguang_compact_inner_rect(rect)
        if inner.width() <= 1.0 or inner.height() <= 1.0:
            return
        inner_radius = self._yaoguang_layer_radius(inner, radius, frame * 0.35)
        frame_path = QPainterPath()
        frame_path.setFillRule(Qt.FillRule.OddEvenFill)
        frame_path.addPath(self._music_shell_path(capsule, radius, False))
        frame_path.addPath(self._rounded_path(inner, inner_radius))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(frame_path, QColor(2, 1, 5, 255))
        painter.restore()

    def _paint_music_compact(self, painter: QPainter, rect: QRectF):
        pose = self._audio_device_pose()
        if pose is None:
            self._paint_music_compact_base(painter, rect)
            return
        painter.save()
        painter.setClipPath(self._music_shell_path(rect, self._music_shell_radius(rect, False), False), Qt.ClipOperation.IntersectClip)
        opacity = painter.opacity()
        if pose.reveal < 1.0:
            self._paint_audio_device_faded_layer(painter, rect, 1.0-pose.reveal,
                lambda layer: self._paint_music_compact_base(layer, rect))
        self.music_button_rects = {}
        self.music_cover_rect = QRectF()
        painter.setOpacity(opacity * pose.reveal)
        content_rect = self._yaoguang_compact_background_rect(rect) if self._effective_music_ambience_style() == "yaoguang" else rect
        self._paint_audio_device_content(painter, content_rect, pose, keep_lyrics=True)
        painter.restore()

    def _paint_audio_device_faded_layer(self, painter, rect, opacity, paint):
        combined = painter.opacity()*opacity
        if combined <= 0:
            return
        if combined >= .999:
            paint(painter)
            return
        # Existing lyric styles own per-line opacity. Composite their live
        # render as one layer so those values cannot override a notice fade.
        dpr = max(1.0, float(painter.device().devicePixelRatioF()))
        image = QImage(max(1, math.ceil(rect.width()*dpr)), max(1, math.ceil(rect.height()*dpr)), QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as layer:
            layer.setRenderHint(QPainter.RenderHint.Antialiasing)
            layer.translate(-rect.left(), -rect.top())
            paint(layer)
        painter.save()
        painter.setOpacity(combined)
        painter.drawImage(rect.topLeft(), image)
        painter.restore()

    def _paint_audio_device_background(self, painter, rect, radius, plan):
        dpr = max(1.0, float(painter.device().devicePixelRatioF()))
        size = (max(1, math.ceil(rect.width() * dpr)), max(1, math.ceil(rect.height() * dpr)))
        image = getattr(self, "_audio_device_background_image", None)
        if image is None or (image.width(), image.height()) != size or image.devicePixelRatioF() != dpr:
            image = QImage(*size, QImage.Format.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(dpr)
        image.fill(Qt.GlobalColor.transparent)
        with QPainter(image) as background:
            background.setRenderHint(QPainter.RenderHint.Antialiasing)
            background.translate(-rect.left(), -rect.top())
            for step in plan.steps:
                if step not in {"compact_content", "expanded_content", "border", "yaoguang_compact_frame_overlay"}:
                    self._execute_music_surface_paint_step(step, background, rect, radius, False, plan.ambience_style)
        self._audio_device_background_image = image
        self._audio_device_background_rect = QRectF(rect)
        painter.drawImage(rect.topLeft(), image)

    def _audio_device_ink(self, rect: QRectF) -> QColor:
        image = getattr(self, "_audio_device_background_image", None)
        bounds = getattr(self, "_audio_device_background_rect", QRectF())
        if image is None or image.isNull() or bounds.isEmpty():
            return self._music_control_icon_color(246)
        dpr = image.devicePixelRatioF()
        luminances = []
        for fx in (.30, .50, .70):
            for fy in (.35, .50, .65):
                x = min(image.width()-1, max(0, round((rect.left() + rect.width()*fx - bounds.left())*dpr)))
                y = min(image.height()-1, max(0, round((rect.top() + rect.height()*fy - bounds.top())*dpr)))
                color = image.pixelColor(x, y)
                if color.alpha() >= 180:
                    luminances.append(relative_luminance(color))
        if not luminances:
            return self._music_control_icon_color(246)
        # Black/white contrast crossover, using the actual background only.
        return QColor(24, 26, 29) if sum(luminances)/len(luminances) > .179 else QColor(244, 246, 248)

    def _paint_audio_device_content(self, painter, rect, pose, *, keep_lyrics):
        pad = min(self._u(12), rect.width()*.06)
        glyph_size = max(1.0, min(self._u(39), rect.height()*.86, rect.width()*.22))
        ring_size = max(1.0, min(self._u(29), rect.height()*.72, rect.width()*.19))
        glyph = QRectF(rect.left()+pad, rect.center().y()-glyph_size/2, glyph_size, glyph_size)
        ring = QRectF(rect.right()-pad-ring_size, rect.center().y()-ring_size/2, ring_size, ring_size)
        center = QRectF(glyph.right()+pad, rect.top()+self._u(2), max(0, ring.left()-glyph.right()-pad*2), max(1, rect.height()-self._u(4)))
        name_inset = pad * 2 + glyph_size
        name_rect = rect.adjusted(name_inset, self._u(2), -name_inset, -self._u(2))
        payloads = [(pose.payload, 1.0)] if pose.previous is None else [(pose.previous, 1-pose.content_blend), (pose.payload, pose.content_blend)]
        base_opacity = painter.opacity()
        battery_weight = 0.0
        font = self._font(min(self._compact_primary_lyric_font_size(), max(9, int(rect.height()*.46))), QFont.Weight.Bold, shrink=False)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for payload, weight in payloads:
            if weight <= 0:
                continue
            painter.setOpacity(base_opacity*weight)
            moved = glyph.translated(-self._u(6)*(1-pose.reveal), 0)
            paint_audio_output_icon(painter, moved, payload.kind, self._audio_device_ink(glyph))
            percent = payload.battery_percent
            if percent is not None:
                battery_weight += weight
                ink = self._audio_device_ink(ring)
                track = QColor(ink)
                track.setAlpha(54)
                stroke = min(self._u(4.5), ring_size*.17)
                arc = ring.adjusted(stroke/2, stroke/2, -stroke/2, -stroke/2)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(track, stroke))
                painter.drawEllipse(arc)
                green = QColor(23, 135, 60) if ink.red() < 128 else QColor(80, 222, 119)
                painter.setPen(QPen(green, stroke, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                if percent > 0:
                    painter.drawArc(arc, 90*16, -round(360*16*percent/100))
            if percent is None or not keep_lyrics:
                painter.setFont(font)
                painter.setPen(self._audio_device_ink(name_rect))
                text_bounds = name_rect if percent is None else center
                label = QFontMetrics(font).elidedText(payload.name, Qt.TextElideMode.ElideRight, max(0, int(text_bounds.width())))
                painter.drawText(text_bounds, Qt.AlignmentFlag.AlignCenter, label)
        if keep_lyrics and battery_weight > 0 and center.width() > 2:
            painter.setOpacity(base_opacity)
            self._paint_audio_device_faded_layer(painter, center, battery_weight,
                lambda layer: self._paint_compact_lyric_viewport(layer, center))
        painter.restore()

    def _paint_audio_device_idle_island(self, painter, rect, kind):
        pose = self._audio_device_pose()
        if pose is None:
            return
        opacity = painter.opacity()
        painter.save()
        if pose.reveal < 1:
            def original(layer):
                if kind == IslandKind.PLUGIN:
                    self._paint_plugin(layer, rect, False)
                elif kind == IslandKind.EMPTY_TEXT:
                    self._paint_empty_text_island(layer, rect)
                elif kind == IslandKind.TIME:
                    self._paint_empty_time_island(layer, rect)
                else:
                    self._paint_thin_bar(layer, rect)
            self._paint_audio_device_faded_layer(painter, rect, 1-pose.reveal, original)
        painter.setOpacity(opacity*pose.reveal)
        style = self._effective_music_ambience_style()
        plan = presentation_music_surface_paint_plan(expanded=False, ambience_style=style,
            expanded_layout_style="standard", lyric_color_fill_paints_background=self._active_lyric_color_fill_paints_background(style))
        radius = self._music_shell_radius(rect, False)
        self._paint_audio_device_background(painter, rect, radius, plan)
        painter.setClipPath(self._music_shell_path(rect, radius, False), Qt.ClipOperation.IntersectClip)
        self._paint_audio_device_content(painter, rect, pose, keep_lyrics=False)
        painter.restore()

    def _paint_music_compact_base(self, painter: QPainter, rect: QRectF):
        self.music_button_rects = {}
        if self._taskbar_cover_only_music_active(rect):
            self._paint_taskbar_cover_only_music(painter, rect)
            return
        if self._empty_music_text_active():
            self.music_cover_rect = QRectF()
            top_padding, bottom_padding = self._compact_lyric_vertical_padding(False, rect)
            text_rect = rect.adjusted(self._u(12.0), top_padding, -self._u(12.0), -bottom_padding)
            self._paint_compact_lyric_viewport(painter, text_rect)
            return
        if self._should_paint_album_island():
            self.music_cover_rect = QRectF()
            self._paint_album_island_compact(painter, rect)
            self._paint_taskbar_music_hover_overlay(painter, rect)
            return
        two_line = self._has_current_two_line_lyric()
        top_padding, bottom_padding = self._compact_lyric_vertical_padding(two_line, rect)
        yaoguang_compact = self._effective_music_ambience_style() == "yaoguang"
        if self._compact_lyric_cover_enabled():
            cover = self._yaoguang_compact_cover_rect(rect) if yaoguang_compact else self._compact_cover_rect(rect)
            self.music_cover_rect = QRectF(cover)
            self._paint_interactive_music_cover(painter, cover, self._compact_cover_radius(rect, two_line))
            text_left = cover.right() + self._u(15 if two_line else 12)
        else:
            self.music_cover_rect = QRectF()
            text_left = rect.left() + self._u(10.0)
        if yaoguang_compact:
            text_rect = self._yaoguang_compact_text_rect(rect, top_padding, bottom_padding)
        else:
            text_rect = QRectF(text_left, rect.top() + top_padding, rect.right() - text_left - self._u(25), rect.height() - top_padding - bottom_padding)
        previous_clip_path = getattr(self, "_compact_lyric_clip_path", None)
        had_previous_clip_path = hasattr(self, "_compact_lyric_clip_path")
        if yaoguang_compact:
            self._compact_lyric_clip_path = self._yaoguang_compact_inner_path(rect)
        try:
            self._paint_compact_lyric_viewport(painter, text_rect)
        finally:
            if had_previous_clip_path:
                self._compact_lyric_clip_path = previous_clip_path
            elif hasattr(self, "_compact_lyric_clip_path"):
                delattr(self, "_compact_lyric_clip_path")
        self._paint_taskbar_music_hover_overlay(painter, rect)

    def _paint_taskbar_cover_only_music(self, painter: QPainter, rect: QRectF):
        pad = max(self._u(2.0), rect.height() * 0.08)
        button_size = max(self._u(20.0), min(rect.height() - pad * 2.0, rect.width() * 0.40))
        cover_size = max(self._u(22.0), min(rect.height() - pad * 2.0, rect.width() - button_size - pad * 3.0))
        cover = QRectF(rect.left() + pad, rect.center().y() - cover_size / 2.0, cover_size, cover_size)
        button = QRectF(rect.right() - pad - button_size, rect.center().y() - button_size / 2.0, button_size, button_size)
        if button.left() <= cover.right() + pad:
            cover_size = max(self._u(18.0), button.left() - pad - cover.left())
            cover = QRectF(cover.left(), rect.center().y() - cover_size / 2.0, cover_size, cover_size)
        self.music_cover_rect = QRectF(cover)
        self.music_button_rects = {"music:toggle_play_pause": QRectF(button)}
        self._paint_interactive_music_cover(painter, cover, self._compact_cover_radius(rect, False))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        radius = min(button.height() / 2.0, self._u(9.0))
        self._paint_music_control_button(painter, button, "music:toggle_play_pause", 36, radius=radius)
        painter.restore()

    def _taskbar_music_hover_overlay_active(self) -> bool:
        if not self._taskbar_compact_active() or bool(getattr(self, "expanded", False)):
            return False
        key = str(getattr(self, "hovered_key", "") or "")
        return key == "music" or key.startswith("music:")

    def _paint_taskbar_music_hover_overlay(self, painter: QPainter, rect: QRectF):
        if not self._taskbar_music_hover_overlay_active() or rect.isNull():
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        button_size = max(self._u(32.0), min(self._u(38.0), rect.height() - self._u(2.0)))
        gap = max(self._u(5.0), min(self._u(8.0), rect.width() * 0.03))
        group_w = button_size * 3.0 + gap * 2.0
        side_pad = max(self._u(5.0), min(self._u(8.0), rect.width() * 0.028))
        x = rect.right() - side_pad - group_w
        commands = ["music:previous", "music:toggle_play_pause", "music:next"]
        self.music_button_rects = {}
        for index, command in enumerate(commands):
            br = QRectF(x + index * (button_size + gap), rect.center().y() - button_size / 2.0, button_size, button_size)
            self.music_button_rects[command] = QRectF(br)
            radius = min(br.height() / 2.0, self._u(12.0))
            self._paint_music_control_button(painter, br, command, 206 if index != 1 else 226, radius=radius)
        painter.restore()

    def _island_lyric_row_content_height(self, text: str, size: float, bold: bool) -> float:
        """Reserve the shaped line and its full reading travel before clipping.

        Island fonts use point sizes. Raw settings units therefore cannot
        stand in for actual Qt line height, especially for the serif face or
        fallback glyphs. The same bounded shaped-line cache serves the block
        renderer, so measuring does not rasterize every word on every frame.
        """
        font = self._wave_stream_font(size, QFont.Weight.Bold if bold else QFont.Weight.Normal)
        renderer = getattr(self, "_wave_reading_renderer", None)
        if not isinstance(renderer, LyricReadingRenderer):
            renderer = self._wave_reading_renderer = LyricReadingRenderer()
        layout = renderer.whole_line(text or "Mg国", font)
        line_height = layout.lineAt(0).height() if layout.lineCount() else float(QFontMetrics(font).height())
        pixel_size = max(1.0, float(QFontInfo(font).pixelSize()))
        lift = GLYPH_FLOAT_LIFT * pixel_size / 38.0
        return line_height + 2.0 * lift + self._u(2.0)

    def _fit_island_lyric_row_size(self, text: str, size: int, bold: bool, height: float) -> int:
        # A taskbar or a spring that is still opening owns its shell height.
        # Fit ink inside that live geometry instead of clipping it while the
        # next full-height target is being reached.
        size = int(size)
        while size > 8 and self._island_lyric_row_content_height(text, size, bold) > height:
            size -= 1
        return size

    def _compact_dual_line_lyric_rows(
        self,
        rect: QRectF,
        compact_size: int,
        secondary_bold: bool,
        texts: tuple[str, str] = ("Mg国", "Mg国"),
    ) -> tuple[QRectF, QRectF, int, int]:
        primary_size, secondary_size = self._compact_dual_line_font_sizes(compact_size, secondary_bold)
        try:
            yaoguang_compact = self._effective_music_ambience_style() == "yaoguang"
        except Exception:
            yaoguang_compact = False
        min_primary_size = 8
        min_secondary_size = 8
        top_inset = self._u(4.0 if yaoguang_compact else 2.0)
        bottom_inset = self._u(4.0 if yaoguang_compact else 2.0)
        gap = self._u(1.0)

        def measured_rows() -> tuple[float, float, float]:
            primary_h = self._island_lyric_row_content_height(texts[0], primary_size, True)
            secondary_h = self._island_lyric_row_content_height(texts[1], secondary_size, secondary_bold)
            return primary_h, secondary_h, primary_h + gap + secondary_h

        primary_h, secondary_h, total_h = measured_rows()
        available_h = max(1.0, rect.height() - top_inset - bottom_inset)
        for _ in range(max(primary_size, secondary_size)):
            if total_h <= available_h or (primary_size <= min_primary_size and secondary_size <= min_secondary_size):
                break
            if primary_size > min_primary_size:
                primary_size -= 1
            if total_h - available_h > self._u(2.0) and secondary_size > min_secondary_size:
                secondary_size -= 1
            primary_h, secondary_h, total_h = measured_rows()

        if total_h > available_h:
            relief = (total_h - available_h) / 2.0
            top_inset = max(self._u(2.0 if yaoguang_compact else 1.0), top_inset - relief)
            bottom_inset = max(self._u(2.0 if yaoguang_compact else 1.0), bottom_inset - relief)

        y = rect.center().y() - total_h / 2.0
        min_y = rect.top() + top_inset
        max_y = rect.bottom() - bottom_inset - total_h
        if max_y >= min_y:
            y = max(min_y, min(y, max_y))
        else:
            y = min_y
        first = QRectF(rect.left(), y, rect.width(), primary_h)
        second = QRectF(rect.left(), first.bottom() + gap, rect.width(), secondary_h)
        return first, second, primary_size, secondary_size

    def _paint_compact_lyric_viewport(self, painter: QPainter, rect: QRectF):
        painter.save()
        painter.setClipRect(rect)
        clip_path = getattr(self, "_compact_lyric_clip_path", None)
        if isinstance(clip_path, QPainterPath) and not clip_path.isEmpty():
            painter.setClipPath(clip_path, Qt.ClipOperation.IntersectClip)
        if self._should_paint_album_island():
            self._paint_album_island(painter, rect)
            painter.restore()
            return
        lines = self._compact_lyric_lines()
        if not lines:
            painter.restore()
            return
        phase = self._island_display_phase()
        source_index = int(phase.get("active_index", getattr(self, "lyric_index", 0)))
        index = int(phase.get("visual_focus_index", source_index)) % len(lines)
        if index != source_index:
            phase = dict(phase)
            phase["progress"] = 0.0
            phase["whole_row"] = False
            for key in ("timed_primary_text", "timed_units", "timed_position",
                        "timed_line_end", "timed_character_front", "whole_block_progress"):
                phase.pop(key, None)
        compact_size = self._compact_primary_lyric_font_size()
        display_lines = self._lyric_display_lines(lines[index])
        base_text = self._music_lyric_base_text_color(132)
        live_text = self._music_lyric_text_color(246)
        if len(display_lines) > 1 and rect.height() >= self._u(48.0):
            secondary_bold = int(getattr(self, "dual_line_secondary_weight", 72)) >= 78
            secondary_spacing = int(getattr(self, "dual_line_secondary_spacing", 100))
            first, second, primary_size, secondary_size = self._compact_dual_line_lyric_rows(
                rect, compact_size, secondary_bold, (display_lines[0], display_lines[1]),
            )
            if "whole_block_progress" in phase:
                self._paint_wave_stream_whole_block(painter, (
                    (first, display_lines[0], primary_size, True, base_text, live_text, None),
                    (second, display_lines[1], secondary_size, secondary_bold,
                     self._music_lyric_base_text_color(96), self._music_lyric_text_color(204), secondary_spacing),
                ), phase)
            else:
                self._paint_live_lyric_text(painter, first, display_lines[0], primary_size, base_text, live_text, True, phase, centered=True, centered_scroll=True)
                self._paint_live_lyric_text(painter, second, display_lines[1], secondary_size, self._music_lyric_base_text_color(96), self._music_lyric_text_color(204), secondary_bold, phase, centered=True, centered_scroll=True, spacing_percent=secondary_spacing)
        else:
            text = display_lines[0] if display_lines else lines[index]
            compact_size = self._fit_island_lyric_row_size(text, compact_size, True, rect.height())
            self._paint_live_lyric_text(painter, rect, text, compact_size, base_text, live_text, True, phase, centered=True, centered_scroll=True)
        painter.restore()

    def _paint_gelv_compact_lyrics(self, painter: QPainter, rect: QRectF):
        now = time.monotonic()
        progress = self._gelv_spacing_progress(now)
        for row in self._gelv_line_rects(rect, expanded=False, now=now):
            opacity = max(0.0, min(1.0, float(row.get("opacity", 1.0))))
            if opacity <= 0.01:
                continue
            painter.save()
            painter.setOpacity(opacity)
            self._paint_gelv_line(
                painter,
                row["rect"],
                str(row["text"]),
                float(row["size"]),
                float(row.get("scroll_progress", progress)),
                active=bool(row["active"]),
                color_fill=bool(row.get("color_fill", False)),
                progress_fill=bool(row.get("progress_fill", False)),
                spacing_percent=row.get("spacing_percent"),
                allow_word_fill=bool(row.get("allow_word_fill", bool(row["active"]))),
                word_backplate=bool(row["active"]),
            )
            painter.restore()

    def _paint_gelv_expanded_lyrics(self, painter: QPainter, rect: QRectF):
        now = time.monotonic()
        progress = self._gelv_spacing_progress(now)
        inner = rect.adjusted(self._u(18.0), self._u(16.0), -self._u(18.0), -self._u(16.0))
        for row_index, row in enumerate(self._gelv_line_rects(inner, expanded=True, now=now)):
            opacity = max(0.0, min(1.0, float(row.get("opacity", 1.0))))
            if opacity <= 0.01:
                continue
            painter.save()
            painter.setOpacity(opacity)
            self._paint_gelv_line(
                painter,
                row["rect"],
                str(row["text"]),
                float(row["size"]),
                float(row.get("scroll_progress", progress)),
                active=bool(row["active"]),
                color_fill=bool(row.get("color_fill", False)),
                progress_fill=bool(row.get("progress_fill", False)),
                spacing_percent=row.get("spacing_percent"),
                allow_word_fill=bool(row.get("allow_word_fill", row_index == 0)),
                word_backplate=False,
            )
            painter.restore()

    def _paint_ember_lyrics(self, painter: QPainter, rect: QRectF, expanded: bool = False):
        now = time.monotonic()
        phase = self._ember_lyric_phase(now)
        visual_index = int(phase.get("active_index", getattr(self, "lyric_index", 0)))
        lines = self._ember_current_display_lines(expanded=expanded, visual_index=visual_index)
        if not lines:
            return
        scroll_mode = self._ember_effective_line_mode(expanded=expanded) == "scroll"
        progress = max(0.0, min(1.0, float(phase.get("progress", 0.0))))
        confidence = max(0.0, min(1.0, float(phase.get("confidence", 1.0))))
        size = self._ember_base_font_size(expanded=expanded, line_count=len(lines))
        gap = self._u(4.0 if expanded else (1.5 if len(lines) > 1 else 2.0))
        inner = rect.adjusted(
            self._u(30.0 if expanded else 18.0),
            self._u(10.0 if expanded else 3.0),
            -self._u(30.0 if expanded else 18.0),
            -self._u(10.0 if expanded else 3.0),
        )

        def measured_line_height(font_size: int) -> float:
            metrics = QFontMetrics(self._ember_font(font_size, back_layer=True))
            return max(float(metrics.height()) + self._u(1.0), self._u(float(font_size) + (10.0 if expanded else 5.0)))

        line_h = measured_line_height(size)
        total_h = line_h * len(lines) + gap * max(0, len(lines) - 1)
        min_size = 11 if expanded else 10
        while total_h > inner.height() and size > min_size:
            size -= 1
            line_h = measured_line_height(size)
            total_h = line_h * len(lines) + gap * max(0, len(lines) - 1)
        if total_h > inner.height() and lines:
            line_h = max(self._u(14.0), (inner.height() - gap * max(0, len(lines) - 1)) / len(lines))
            total_h = line_h * len(lines) + gap * max(0, len(lines) - 1)
        y = inner.center().y() - total_h / 2.0
        lyric_line_count = len([line for line in lines if line != "..."])
        line_progress = self._ember_line_progress(progress, lyric_line_count)
        for line in lines:
            row = QRectF(inner.left(), y, inner.width(), line_h)
            self._paint_ember_line(
                painter,
                row,
                line,
                size,
                1.0 if line == "..." else line_progress,
                confidence,
                scroll=scroll_mode,
            )
            y += line_h + gap

    def _paint_ember_line(
        self,
        painter: QPainter,
        rect: QRectF,
        text: str,
        size: float,
        progress: float,
        confidence: float,
        scroll: bool = False,
    ):
        if not text:
            return
        back_layout = self._ember_layout(text, size, back_layer=True)
        front_layout = self._ember_layout(text, size, back_layer=False)
        back_x = self._ember_layout_x(rect, back_layout, progress=progress) if scroll else None
        front_x = self._ember_layout_x(rect, front_layout, progress=progress) if scroll else None
        front_base = (
            self._music_lyric_base_text_color(132)
            if self._solid_album_music_active()
            else self._ember_front_neutral_color(255)
        )
        front_live = self._ember_front_read_color(255)
        back_color = self._ember_back_layer_color(front_live)
        word_fill = self._ember_word_fill_enabled()
        reveal_progress = progress if word_fill else None
        self._paint_ember_layout(painter, rect, back_layout, size, True, back_color, progress=reveal_progress, confidence=confidence, x_start=back_x)
        self._paint_ember_blur_panel(
            painter,
            rect,
            back_layout,
            size,
            back_color,
            progress=reveal_progress,
            confidence=confidence,
            x_start=back_x,
        )
        self._paint_ember_layout(
            painter,
            rect,
            front_layout,
            size,
            False,
            front_base if word_fill else front_live,
            progress=reveal_progress,
            confidence=confidence,
            live_color=front_live,
            word_fill=word_fill,
            x_start=front_x,
        )

    def _ember_layout_x(self, rect: QRectF, layout: LyricLayout, progress: float = 0.0) -> float:
        text_w = float(layout.total_width)
        if text_w <= rect.width() - self._u(10.0):
            return rect.center().x() - text_w / 2.0
        travel = max(0.0, text_w - rect.width())
        eased = 1.0 - (1.0 - max(0.0, min(1.0, float(progress)))) ** 3
        return rect.left() - travel * eased

    def _paint_ember_blur_panel(
        self,
        painter: QPainter,
        rect: QRectF,
        layout: LyricLayout,
        size: float,
        color: QColor,
        progress: float | None = None,
        confidence: float = 1.0,
        x_start: float | None = None,
    ):
        strength = max(0, min(100, int(getattr(self, "ember_blur_panel_strength", 18))))
        if strength <= 0 or painter is None:
            return
        if not getattr(layout, "chunks", ()):
            return
        if not all(hasattr(painter, name) for name in ("drawImage", "save", "restore", "setOpacity")):
            return
        confidence = max(0.0, min(1.0, float(confidence)))
        if confidence <= 0.01:
            return
        blur_radius = max(self._u(1.2), min(self._u(10.0), self._u(1.35 + strength * 0.085)))
        margin = max(3, int(math.ceil(blur_radius * 2.4 + self._u(4.0))))
        layer_rect = rect.adjusted(-margin, -margin, margin, margin)
        layer_w = max(1, int(math.ceil(layer_rect.width())))
        layer_h = max(1, int(math.ceil(layer_rect.height())))
        if layer_w <= 1 or layer_h <= 1:
            return

        layer = QImage(layer_w, layer_h, QImage.Format.Format_ARGB32_Premultiplied)
        layer.fill(Qt.GlobalColor.transparent)
        layer_painter = QPainter(layer)
        layer_painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        mask_color = QColor(color)
        mask_alpha = int(mask_color.alpha() * max(0.42, min(0.86, 0.42 + strength / 180.0)))
        mask_color.setAlpha(max(0, min(255, mask_alpha)))
        local_rect = QRectF(
            rect.left() - layer_rect.left(),
            rect.top() - layer_rect.top(),
            rect.width(),
            rect.height(),
        )
        self._paint_ember_layout(
            layer_painter,
            local_rect,
            layout,
            size,
            True,
            mask_color,
            progress=progress,
            confidence=confidence,
            x_start=(x_start - layer_rect.left()) if x_start is not None else None,
        )
        layer_painter.end()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rings = max(2, min(5, 1 + strength // 22))
        directions = (
            (1.0, 0.0),
            (-1.0, 0.0),
            (0.0, 1.0),
            (0.0, -1.0),
            (0.72, 0.72),
            (-0.72, 0.72),
            (0.72, -0.72),
            (-0.72, -0.72),
        )
        base_opacity = max(0.05, min(0.34, 0.08 + strength / 340.0)) * confidence
        for ring in range(rings, 0, -1):
            distance = blur_radius * ring / rings
            painter.setOpacity(base_opacity / (ring + 1.2))
            for dx, dy in directions:
                painter.drawImage(layer_rect.translated(dx * distance, dy * distance), layer)
        painter.setOpacity(max(0.04, min(0.18, base_opacity * 0.48)))
        painter.drawImage(layer_rect, layer)
        painter.restore()

    def _paint_ember_layout(
        self,
        painter: QPainter,
        rect: QRectF,
        layout: LyricLayout,
        size: float,
        back_layer: bool,
        color: QColor,
        progress: float | None = None,
        confidence: float = 1.0,
        live_color: QColor | None = None,
        word_fill: bool = False,
        x_start: float | None = None,
    ):
        if not layout.chunks:
            return
        painter.save()
        painter.setFont(self._ember_font(size, back_layer=back_layer))
        x = float(x_start) if x_start is not None else rect.center().x() - layout.total_width / 2.0
        chunk_count = len(layout.chunks)
        focus = max(0.0, min(1.0, float(progress if progress is not None else 1.0))) * chunk_count
        for index, chunk in enumerate(layout.chunks):
            width = layout.widths[index]
            token_rect = QRectF(x, rect.top(), width, rect.height())
            if progress is None:
                chunk_color = QColor(color)
                painter.setPen(chunk_color)
                painter.drawText(token_rect, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, chunk)
            else:
                reveal = max(0.0, min(1.0, focus - float(index)))
                if reveal <= 0.0 and not word_fill:
                    x += width
                    continue
                alpha_floor = int(color.alpha() * 0.46) if word_fill else 0
                alpha = int((alpha_floor + (color.alpha() - alpha_floor) * reveal) * max(0.35, confidence))
                blur = self._u(1.35 * (1.0 - reveal)) if reveal > 0.0 else 0.0
                if blur > 0.18:
                    blur_color = QColor(color)
                    blur_color.setAlpha(max(0, min(255, int(alpha * 0.26))))
                    painter.setPen(blur_color)
                    for dx, dy in ((-blur, 0.0), (blur, 0.0), (0.0, -blur), (0.0, blur)):
                        painter.drawText(token_rect.translated(dx, dy), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, chunk)
                chunk_color = QColor(color)
                if word_fill and live_color is not None:
                    mixed = self._mix_alpha_color(color, live_color, reveal, alpha=color.alpha())
                    chunk_color = QColor(mixed)
                chunk_color.setAlpha(max(0, min(255, alpha)))
                painter.setPen(chunk_color)
                painter.drawText(token_rect, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, chunk)
            x += width
        painter.restore()

    def _paint_gelv_line(
        self,
        painter: QPainter,
        rect: QRectF,
        text: str,
        size: float,
        progress: float,
        active: bool,
        color_fill: bool = True,
        progress_fill: bool = False,
        spacing_percent: int | None = None,
        allow_word_fill: bool = True,
        word_backplate: bool = False,
    ):
        if not text:
            return
        spacing = int(spacing_percent) if spacing_percent is not None else self._gelv_spacing_percent(progress)
        layout = self._gelv_layout(text, size, spacing_percent=spacing)
        x = self._gelv_line_x(rect, text, size, progress, spacing_percent=spacing)
        gelv_color_theme_active = color_fill and self._gelv_color_theme_enabled()
        gelv_word_fill_active = (
            bool(allow_word_fill) and bool(progress_fill or self._gelv_word_fill_enabled())
            and not self._island_whole_row_reading()
        )
        if self._pure_white_music_active() or self._solid_album_music_active():
            neutral_base = self._music_lyric_base_text_color(128 if active else 82)
            neutral_live = self._music_lyric_text_color(245 if active else 156)
            if gelv_color_theme_active:
                live = self._gelv_fill_color()
                live.setAlpha(250 if active else 178)
                base = neutral_base if gelv_word_fill_active else QColor(live)
            elif gelv_word_fill_active:
                base = neutral_base
                live = neutral_live
            else:
                base = QColor(neutral_live)
                live = QColor(neutral_live)
        else:
            neutral_base = QColor(255, 255, 255, 128 if active else 82)
            neutral_live = QColor(255, 255, 255, 245 if active else 156)
            if gelv_color_theme_active:
                live = self._gelv_fill_color()
                live.setAlpha(250 if active else 178)
                base = neutral_base if gelv_word_fill_active else QColor(live)
            elif gelv_word_fill_active:
                base = neutral_base
                live = neutral_live
            else:
                base = QColor(neutral_live)
                live = QColor(neutral_live)
        previous_backplate = getattr(self, "_current_word_backplate_enabled", False)
        previous_word_fill = getattr(self, "_current_gelv_word_fill_enabled", True)
        self._current_word_backplate_enabled = bool(word_backplate)
        self._current_gelv_word_fill_enabled = bool(gelv_word_fill_active)
        try:
            self._paint_gelv_songscatter_chunks(painter, rect, x, layout, size, base, live, progress, active)
        finally:
            self._current_word_backplate_enabled = previous_backplate
            self._current_gelv_word_fill_enabled = previous_word_fill

    def _paint_gelv_songscatter_chunks(
        self,
        painter: QPainter,
        rect: QRectF,
        x: float,
        layout: LyricLayout,
        size: float,
        base_color: QColor,
        live_color: QColor,
        progress: float,
        active: bool,
    ):
        chunks = layout.chunks
        if not chunks:
            return
        progress = max(0.0, min(1.0, float(progress)))
        live_width = max(0.0, min(layout.total_width, self._lyric_progress_width(layout, progress)))
        word_fill = bool(getattr(self, "_current_gelv_word_fill_enabled", True))
        font = self._gelv_font(size, QFont.Weight.Normal)
        painter.setFont(font)
        cursor = x
        for index, chunk in enumerate(chunks):
            width = layout.widths[index]
            if width <= 0.0:
                cursor += width
                continue
            sung_amount = 1.0
            if word_fill:
                sung_amount = max(0.0, min(1.0, (live_width - (cursor - x)) / max(1.0, width)))
            chunk_color = self._mix_alpha_color(base_color, live_color, sung_amount)
            token_rect = QRectF(cursor, rect.top(), width, rect.height())
            painter.setPen(chunk_color)
            painter.drawText(
                token_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter,
                chunk,
            )
            cursor += width

    def _paint_album_island(self, painter: QPainter, rect: QRectF):
        painter.save()
        painter.setClipRect(rect)
        raw_slots = self._album_island_slots(rect)
        self._paint_album_island_slots(painter, raw_slots)
        painter.setOpacity(1.0)
        if not any(int(slot.get("side", 0)) > 0 for slot in raw_slots):
            self._paint_album_island_listened_text(painter, rect, raw_slots)
        painter.restore()

    def _paint_album_island_slots(self, painter: QPainter, raw_slots: list[dict[str, object]]):
        slots = sorted(raw_slots, key=lambda slot: int(slot.get("z", 0)))
        for slot in slots:
            if bool(slot["center"]):
                self.music_cover_rect = QRectF(slot["rect"])
            painter.setOpacity(float(slot["alpha"]) / 255.0)
            radius = self._u(8.0 if bool(slot["center"]) else 6.0)
            self._paint_album_island_slot(painter, slot, radius)

    def _paint_album_island_compact(self, painter: QPainter, rect: QRectF):
        painter.save()
        painter.setClipRect(rect)
        stage = rect.adjusted(self._u(8.0), self._u(4.0), -self._u(8.0), -self._u(4.0))
        raw_slots = self._album_island_slots(stage, reserve_text=True)
        self._paint_album_island_slots(painter, raw_slots)
        painter.setOpacity(1.0)
        if not any(int(slot.get("side", 0)) > 0 for slot in raw_slots):
            self._paint_album_island_listened_text(painter, rect, raw_slots)
        painter.restore()

    def _paint_album_island_slot(self, painter: QPainter, slot: dict[str, object], radius: float):
        rect = QRectF(slot["rect"])
        image = slot.get("album_art")
        if not bool(slot.get("center")) and not bool(slot.get("placeholder", False)) and (not isinstance(image, QImage) or image.isNull()):
            return
        tilt = float(slot.get("tilt", 0.0) or 0.0)
        perspective = slot.get("perspective")
        painter.save()
        if isinstance(perspective, dict) and not bool(slot.get("center")):
            painter.setWorldTransform(album_cover_projection(rect, perspective), combine=True)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.setOpacity(float(slot["alpha"]) / 255.0)
            cache_key = f"album-island:{slot.get('side', 0)}:{image.cacheKey() if isinstance(image, QImage) else 'empty'}"
            self._paint_cover(painter, rect, radius, image if isinstance(image, QImage) else None, cache_key)
            painter.restore()
            return
        if abs(tilt) > 0.01:
            center = rect.center()
            painter.translate(center)
            painter.shear(math.tan(math.radians(tilt)) * 0.34, 0.0)
            painter.translate(-center)
        cache_key = f"album-island:{slot.get('side', 0)}:{image.cacheKey() if isinstance(image, QImage) else 'empty'}"
        cover_painter = self._paint_interactive_music_cover if bool(slot.get("center")) else self._paint_cover
        cover_painter(painter, rect, radius, image if isinstance(image, QImage) else None, cache_key)
        painter.restore()

    def _paint_album_island_listened_text(self, painter: QPainter, rect: QRectF, slots: list[dict[str, object]]):
        center_slot = next((slot for slot in slots if bool(slot.get("center"))), None)
        if center_slot is None:
            return
        cover_rect = QRectF(center_slot["rect"])
        text_rect = QRectF(
            cover_rect.right() + self._u(10.0),
            rect.top(),
            max(0.0, rect.right() - cover_rect.right() - self._u(12.0)),
            rect.height(),
        )
        if text_rect.width() < self._u(46.0):
            return
        painter.save()
        progress = self._album_island_transition_progress()
        painter.setOpacity(max(0.0, min(1.0, progress)))
        expanded = rect.height() >= self._u(92.0)
        painter.setFont(self._font(17 if expanded else 9, QFont.Weight.Bold if expanded else QFont.Weight.DemiBold, shrink=False))
        painter.setPen(self._music_meta_text_color(218 if expanded else 152))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._album_island_listened_text())
        painter.restore()

    def _compact_lyric_lines(self) -> list[str]:
        if self._empty_music_text_active():
            text = str(getattr(self, "no_lyrics_text", "暂无歌词") or "暂无歌词").strip() or "暂无歌词"
            return [text]
        if self._is_instrumental_track():
            return [self._compact_music_text()]
        return [self._clean_lyric_for_width(line) or line for line in self.lyrics] or [self.track_title or self.artist or "正在播放"]

    def _paint_music_expanded(self, painter: QPainter, rect: QRectF):
        if self._empty_music_text_active():
            self.music_cover_rect = QRectF()
            self.music_button_rects = {}
            text = str(getattr(self, "no_lyrics_text", "暂无歌词") or "暂无歌词").strip() or "暂无歌词"
            row = QRectF(rect.left() + self._u(22.0), rect.center().y() - self._u(18.0), rect.width() - self._u(44.0), self._u(36.0))
            self._paint_scrolling_text(painter, row, text, 18, self._music_lyric_text_color(226), True, centered=True, centered_scroll=True)
            return
        if self._should_paint_album_island():
            self.music_cover_rect = QRectF()
            self._paint_album_island_expanded(painter, rect)
            return
        if self._normalize_music_expanded_layout_style(getattr(self, "music_expanded_layout_style", "standard")) == "dual_panel":
            self._paint_music_expanded_dual_panel(painter, rect)
            return
        margin = self._u(16)
        cover_size = self._u(58)
        cover = QRectF(rect.left() + margin, rect.top() + self._u(14), cover_size, cover_size)
        controls_w = self._u(132)
        info = QRectF(cover.right() + self._u(15), cover.top() + self._u(2), rect.right() - cover.right() - controls_w - self._u(48), self._u(68))
        progress = self._personal_dj_island_outro_progress()
        if progress < 1.0:
            # Move the same rings/title into the established cover/title slots;
            # the shell and ambience keep their existing physical animation.
            side = min(rect.height() * .90, self._u(100))
            origin = QRectF(rect.left() + self._u(13), rect.center().y() - side / 2, side, side)
            moving_cover = QRectF(*(a + (b - a) * progress for a, b in zip(
                (origin.x(), origin.y(), origin.width(), origin.height()),
                (cover.x(), cover.y(), cover.width(), cover.height()))))
            self.music_cover_rect = QRectF(moving_cover)
            painter.save()
            opacity = painter.opacity()
            painter.setOpacity(opacity * progress)
            self._paint_interactive_music_cover(painter, moving_cover, self._u(17))
            painter.setOpacity(opacity * (1.0 - progress))
            paint_personal_dj_icon(painter, moving_cover, self._personal_dj_island_color(),
                neutral=QColor(242, 242, 245), elapsed=max(0., time.monotonic()
                    - float(getattr(self, "_island_dj_started", 0.0))),
                max_lightening=float(getattr(self, "_island_dj_lightening", .60)))
            painter.restore()
            origin_title = QRectF(origin.right() + self._u(16), rect.top(),
                max(0., rect.right() - origin.right() - self._u(32)), rect.height())
            if getattr(self, "artist", ""):
                block_height = min(rect.height() - self._u(4), self._u(64))
                origin_title.setTop(rect.center().y() - block_height / 2)
                origin_title.setHeight(block_height * .58)
            title_target = QRectF(info.left(), info.top(), info.width(), self._u(34))
            moving_title = QRectF(*(a + (b - a) * progress for a, b in zip(
                (origin_title.x(), origin_title.y(), origin_title.width(), origin_title.height()),
                (title_target.x(), title_target.y(), title_target.width(), title_target.height()))))
            initial_size = min(22., max(12., rect.height() / self.VISUAL_SCALE * .32))
            font = self._font(initial_size + (13. - initial_size) * progress, QFont.Weight.DemiBold)
            metrics = QFontMetrics(font)
            title = metrics.elidedText(self.track_title, Qt.TextElideMode.ElideRight, max(1, int(moving_title.width())))
            inset = max(0., moving_title.width() - metrics.horizontalAdvance(title)) * .5 * (1. - progress)
            painter.setFont(font)
            painter.setPen(self._music_meta_text_color(240))
            painter.drawText(moving_title.adjusted(inset, 0., 0., 0.),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)
            if self.artist:
                gap = min(self._u(4), block_height * .05)
                origin_artist = QRectF(origin_title.left(), origin_title.bottom() + gap,
                    origin_title.width(), block_height - origin_title.height() - gap)
                target_artist = QRectF(info.left(), info.top() + self._u(35), info.width(), self._u(24))
                moving_artist = QRectF(*(a + (b - a) * progress for a, b in zip(
                    (origin_artist.x(), origin_artist.y(), origin_artist.width(), origin_artist.height()),
                    (target_artist.x(), target_artist.y(), target_artist.width(), target_artist.height()))))
                artist_font = self._font(11. - 2. * progress, QFont.Weight.Medium)
                artist_metrics = QFontMetrics(artist_font)
                artist = artist_metrics.elidedText(self.artist, Qt.TextElideMode.ElideRight, max(1, int(moving_artist.width())))
                inset = max(0., moving_artist.width() - artist_metrics.horizontalAdvance(artist)) * .5 * (1. - progress)
                painter.setFont(artist_font)
                painter.setPen(self._music_meta_text_color(round(176 - 41 * progress)))
                painter.drawText(moving_artist.adjusted(inset, 0., 0., 0.),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, artist)
        else:
            self.music_cover_rect = QRectF(cover)
            self._paint_interactive_music_cover(painter, cover, self._u(17))
            self._paint_scrolling_text(painter, QRectF(info.left(), info.top(), info.width(), self._u(34)), self.track_title, 13, self._music_meta_text_color(240), True)
            self._paint_scrolling_text(painter, QRectF(info.left(), info.top() + self._u(35), info.width(), self._u(24)), self.artist, 9, self._music_meta_text_color(135), False)
        painter.save()
        painter.setOpacity(painter.opacity() * progress)

        controls_x = rect.right() - margin - controls_w
        commands = ["music:previous", "music:toggle_play_pause", "music:next"]
        self.music_button_rects = {}
        for i, command in enumerate(commands):
            button_size = self._u(36)
            br = QRectF(controls_x + i * self._u(45), cover.center().y() - button_size / 2.0, button_size, button_size)
            self.music_button_rects[command] = QRectF(br)
            self._paint_music_control_button(painter, br, command, 24 if i != 1 else 36, radius=self._u(13))

        viewport = QRectF(rect.left() + self._u(22), cover.bottom() + self._u(10), rect.width() - self._u(44), rect.bottom() - cover.bottom() - self._u(28))
        self._paint_lyric_viewport(painter, viewport)
        painter.restore()

    def _paint_music_expanded_dual_panel(self, painter: QPainter, rect: QRectF):
        lyric_panel, info_panel = self._dual_panel_music_rects(rect)
        self.music_button_rects = {}
        lyric_viewport = lyric_panel.adjusted(self._u(30.0), self._u(16.0), -self._u(30.0), -self._u(14.0))
        self._paint_lyric_viewport(painter, lyric_viewport)

        lower_light = self._music_lyric_text_color(255).lightness() < 128
        title_color = QColor(0, 0, 0, 242) if lower_light else QColor(255, 255, 255, 242)
        artist_color = QColor(0, 0, 0, 184) if lower_light else QColor(255, 255, 255, 172)
        margin = self._u(22.0)
        cover_size = max(self._u(48.0), min(self._u(66.0), info_panel.height() - self._u(26.0)))
        cover = QRectF(info_panel.left() + margin, info_panel.center().y() - cover_size / 2.0, cover_size, cover_size)
        self.music_cover_rect = QRectF(cover)
        self._paint_interactive_music_cover(painter, cover, self._u(9.0))

        button_size = self._u(42.0)
        gap = self._u(14.0 if rect.width() >= self._u(600.0) else 12.0)
        commands = ["music:previous", "music:toggle_play_pause", "music:next"] if rect.width() >= self._u(600.0) else ["music:toggle_play_pause", "music:next"]
        controls_w = len(commands) * button_size + max(0, len(commands) - 1) * gap
        controls_x = info_panel.right() - margin - controls_w
        controls_y = info_panel.center().y() - button_size / 2.0

        info_left = cover.right() + self._u(16.0)
        info_right = controls_x - self._u(18.0)
        info = QRectF(info_left, info_panel.center().y() - self._u(28.0), max(self._u(42.0), info_right - info_left), self._u(58.0))
        self._paint_scrolling_text(painter, QRectF(info.left(), info.top(), info.width(), self._u(28.0)), self.track_title, 14, title_color, True)
        self._paint_scrolling_text(painter, QRectF(info.left(), info.top() + self._u(31.0), info.width(), self._u(24.0)), self.artist, 10, artist_color, False)

        for i, command in enumerate(commands):
            br = QRectF(controls_x + i * (button_size + gap), controls_y, button_size, button_size)
            self.music_button_rects[command] = QRectF(br)
            self._paint_music_control_button(painter, br, command, 18 if lower_light else 26, radius=self._u(13.0))

    def _paint_album_island_expanded(self, painter: QPainter, rect: QRectF):
        painter.save()
        painter.setClipRect(rect)
        margin = self._u(18.0)
        controls_w = self._u(132.0)
        self.music_button_rects = {}

        info = QRectF(rect.left() + margin, rect.top() + self._u(13.0), rect.width() - controls_w - margin * 2.0 - self._u(18.0), self._u(42.0))
        self._paint_scrolling_text(painter, QRectF(info.left(), info.top(), info.width(), self._u(24.0)), self.track_title, 12, self._music_meta_text_color(238), True)
        self._paint_scrolling_text(painter, QRectF(info.left(), info.top() + self._u(24.0), info.width(), self._u(18.0)), self.artist, 8, self._music_meta_text_color(132), False)

        controls_x = rect.right() - margin - controls_w
        commands = ["music:previous", "music:toggle_play_pause", "music:next"]
        for i, command in enumerate(commands):
            button_size = self._u(34.0)
            br = QRectF(controls_x + i * self._u(44.0), rect.top() + self._u(19.0), button_size, button_size)
            self.music_button_rects[command] = QRectF(br)
            self._paint_music_control_button(painter, br, command, 22 if i != 1 else 34, radius=self._u(12.0))

        stage = QRectF(rect.left() + margin, rect.top() + self._u(58.0), rect.width() - margin * 2.0, rect.height() - self._u(76.0))
        raw_slots = self._album_island_slots(stage, reserve_text=True)
        self._paint_album_island_slots(painter, raw_slots)
        painter.setOpacity(1.0)
        if not any(int(slot.get("side", 0)) > 0 for slot in raw_slots):
            self._paint_album_island_listened_text(painter, stage, raw_slots)
        painter.restore()

    def _lyric_transition_progress(self, duration: float = 0.52) -> tuple[float, float]:
        if getattr(self, "lyric_transition_kind", "") == "seek":
            duration = max(duration, 0.46)
        duration = self._animation_duration(duration)
        elapsed = max(0.0, min(1.0, (time.monotonic() - self.lyric_changed_at) / max(0.001, duration)))
        eased = elapsed * elapsed * (3.0 - 2.0 * elapsed)
        if elapsed >= 1.0 and getattr(self, "lyric_transition_kind", ""):
            self.lyric_transition_kind = ""
            self.lyric_transition_distance = 0
        return elapsed, eased

    def _island_song_has_genuine_unit_timing(self) -> bool:
        """Classify the displayed song, not metadata rows kept in the source timeline.

        The island keeps its original rows and indexes for playback. Match
        unit evidence to the existing large-page presentation only for the
        song-wide reading decision; the match is cached across paint frames.
        """
        timed = getattr(self, "timed_lyrics", ()) or ()
        kinds = getattr(self, "timed_lyric_timing_kinds", ()) or ()
        units = getattr(self, "timed_lyric_units", ()) or ()
        show_translation = bool(getattr(self, "enable_dual_line_lyrics", False))
        clean_intro = bool(getattr(self, "music_intro_cleanup_enabled", True))
        artist = str(getattr(self, "artist", "") or "")
        key = (
            str(getattr(self, "media_track_id", "") or ""),
            str(getattr(self, "track_title", "") or ""), artist,
            id(timed), len(timed), id(kinds), len(kinds), id(units), len(units),
            show_translation, clean_intro,
        )
        if key == getattr(self, "_island_genuine_timing_cache_key", None):
            return bool(getattr(self, "_island_genuine_timing_cache_value", False))
        has_timing = False
        if timed and song_has_genuine_unit_timing(kinds, units):
            displayed = present_timed_lyric_rows(
                timed, show_translation=show_translation, clean_intro=clean_intro,
                intro_countdown=False, singer_names=artist,
            )
            surviving = tuple(
                (float(start), str(text).replace("\u2028", "\n").splitlines()[0])
                for start, text in displayed if text != INTRO_COUNTDOWN_TEXT
            )
            for index, (start, raw_text) in enumerate(timed):
                if index >= len(kinds) or kinds[index] != "unit" or index >= len(units):
                    continue
                row_units = units[index]
                if not genuinely_timed_unit_row(row_units):
                    continue
                prepared = present_lyric_text(
                    raw_text, show_translation=show_translation, clean_intro=clean_intro,
                    clean_credits=False,
                )
                primary = prepared.splitlines()[0] if prepared else ""
                if not primary or not any(
                    abs(float(start) - shown_start) <= 0.02 and primary == shown_primary
                    for shown_start, shown_primary in surviving
                ):
                    continue
                if timed_unit_reading_front(
                    primary, row_units, float(start), range(len(primary) + 1),
                ) is not None:
                    has_timing = True
                    break
        self._island_genuine_timing_cache_key = key
        self._island_genuine_timing_cache_value = has_timing
        return has_timing

    def _island_whole_row_reading(self, index: int | None = None) -> bool:
        """Keep low-perception in one mode for an entire timed song."""
        index = int(getattr(self, "lyric_index", 0)) if index is None else int(index)
        timed = getattr(self, "timed_lyrics", ()) or ()
        kinds = getattr(self, "timed_lyric_timing_kinds", ()) or ()
        kind = kinds[index] if 0 <= index < len(kinds) else "line"
        if kind == "synthetic":
            return True
        strategy = normalize_music_reading_strategy(getattr(
            self, "music_surface_reading_strategy", MUSIC_READING_STRATEGY_DEFAULT,
        ))
        if strategy != "low_perception":
            return False
        return not self._island_song_has_genuine_unit_timing()

    def _island_display_phase(self, now: float | None = None) -> dict[str, float]:
        frame_at = getattr(self, "_island_lyric_frame_at", None)
        if frame_at is not None:
            phase = getattr(self, "_island_lyric_frame_phase", None)
            if phase is None:
                phase = self._lyric_phase(frame_at)
                self._island_lyric_frame_phase = phase
            return phase
        return self._lyric_phase(time.monotonic() if now is None else now)

    def _lyric_phase(self, now: float) -> dict[str, float]:
        estimated_position = self._stable_lyric_source_position(now)
        lyric_offset = self._effective_lyric_offset_seconds()
        self._prepare_island_lyric_offset(now, lyric_offset)
        paint_state = build_lyric_paint_state(
            timed_lyrics=getattr(self, "timed_lyrics", ()) or (),
            lyric_index=getattr(self, "lyric_index", 0),
            estimated_position_seconds=estimated_position,
            lyric_offset_seconds=lyric_offset,
            lyric_changed_at=getattr(self, "lyric_changed_at", now),
            previous_phase_index=getattr(self, "lyric_last_phase_index", -1),
            previous_phase_progress=getattr(self, "lyric_last_phase_progress", 0.0),
            previous_phase_at=getattr(self, "lyric_last_phase_at", now),
            now=now,
            media_is_playing=getattr(self, "media_is_playing", False),
            transition_kind=getattr(self, "lyric_transition_kind", ""),
            position_selects_active_index=True,
            lead_seconds=0.0 if str(getattr(self, "media_position_source", "") or "").endswith("-uia-lyrics") else 0.12,
        )
        phase = dict(paint_state.phase)
        index = paint_state.cache.active_index
        phase["active_index"] = index
        timed = getattr(self, "timed_lyrics", ()) or ()
        if timed and index == len(timed) - 1:
            # Tail rows have no following timestamp. Their existing fallback
            # span remains, but an old owner's changed_at is not a new song
            # clock when a first visible paint already belongs to the tail.
            elapsed = max(0.0, estimated_position + lyric_offset - float(timed[index][0]))
            span = max(0.35, float(phase.get("span", 2.45)))
            phase.update(progress=min(1.0, elapsed / span), elapsed=elapsed,
                         remaining=max(0.0, span - elapsed))
        kinds = getattr(self, "timed_lyric_timing_kinds", ()) or ()
        unit_rows = getattr(self, "timed_lyric_units", ()) or ()
        song_has_real_units = self._island_song_has_genuine_unit_timing()
        timing_kind = "line"
        if 0 <= index < len(timed) and index < len(kinds):
            kind = kinds[index]
            timing_kind = kind
            if kind == "synthetic":
                # A plain-text source has no actual intra-line time evidence.
                phase["progress"] = 1.0
            elif kind == "unit":
                timing_kind = "line"
                units = unit_rows[index] if index < len(unit_rows) else ()
                primary_text = str(timed[index][1]).splitlines()[0]
                if primary_text and genuinely_timed_unit_row(units):
                    line_end = float(timed[index + 1][0]) if index + 1 < len(timed) else None
                    front = timed_unit_reading_front(
                        primary_text,
                        units,
                        max(0.0, estimated_position + lyric_offset),
                        tuple(range(len(primary_text) + 1)),
                        line_end=line_end,
                    )
                    if front is not None:
                        timing_kind = "unit"
                        unit_progress = max(0.0, min(1.0, front / len(primary_text)))
                        reanchoring = (
                            not bool(getattr(self, "media_is_playing", False))
                            or str(getattr(self, "lyric_transition_kind", "") or "") == "seek"
                            or str(getattr(self, "media_position_source", "") or "") == "local-seek"
                        )
                        if (
                            not reanchoring
                            and index == int(getattr(self, "lyric_last_phase_index", -1))
                            and str(getattr(self, "lyric_last_phase_timing_kind", "") or "") == "unit"
                        ):
                            unit_progress = max(
                                unit_progress,
                                float(getattr(self, "lyric_last_phase_progress", 0.0) or 0.0),
                            )
                        phase["progress"] = unit_progress
                        phase["timed_primary_text"] = primary_text
                        phase["timed_units"] = units
                        phase["timed_position"] = max(0.0, estimated_position + lyric_offset)
                        phase["timed_line_end"] = line_end
                        phase["timed_character_front"] = unit_progress * len(primary_text)
        if song_has_real_units and timing_kind == "line" and 0 <= index < len(timed):
            next_start = float(timed[index + 1][0]) if index + 1 < len(timed) else None
            estimated = estimated_row_reading_progress(
                float(timed[index][0]), next_start, estimated_position + lyric_offset,
            )
            if estimated is not None:
                phase["progress"] = estimated
        phase["whole_row"] = self._island_whole_row_reading(index)
        if phase["whole_row"] and str(getattr(self, "lyric_flow_style", "wave_stream")) in {"wave_stream", "gelv_songscatter"} and normalize_music_reading_strategy(getattr(
            self, "music_surface_reading_strategy", MUSIC_READING_STRATEGY_DEFAULT,
        )) == "low_perception":
            if 0 <= index < len(timed):
                elapsed = max(0.0, estimated_position + lyric_offset - float(timed[index][0]))
            else:
                elapsed = max(0.0, now - float(getattr(self, "lyric_changed_at", now)))
            phase["whole_block_progress"] = max(0.0, min(1.0, elapsed / 0.5))
        # The source row remains ``lyric_index``. Only the Wave Stream view
        # may focus an unread next sentence after the current one has been
        # fully read; reusing the source index here would seek/retime audio.
        visual_index = index
        focus_key = (
            str(getattr(self, "media_track_id", "") or ""),
            id(timed), len(timed), round(float(lyric_offset), 3), song_has_real_units,
        )
        explicit_seek = (
            str(getattr(self, "lyric_transition_kind", "") or "") == "seek"
            or str(getattr(self, "media_position_source", "") or "") == "local-seek"
        )
        if focus_key != getattr(self, "_island_visual_focus_key", None) or explicit_seek:
            self._island_visual_focus_key = focus_key
            self._island_advanced_focus_index = None
        latched = getattr(self, "_island_advanced_focus_index", None)
        if latched is not None and latched != index + 1:
            self._island_advanced_focus_index = None
            latched = None
        if song_has_real_units and not explicit_seek and 0 <= index < len(timed) - 1:
            start = float(timed[index][0])
            next_start = float(timed[index + 1][0])
            effective_position = max(0.0, estimated_position + lyric_offset)
            if start <= effective_position < next_start:
                if timing_kind == "unit":
                    primary = str(timed[index][1]).splitlines()[0]
                    finished = real_unit_row_finished_before_next_start(
                        primary, unit_rows[index] if index < len(unit_rows) else (),
                        effective_position, next_start,
                    )
                elif timing_kind == "line":
                    end = estimated_row_read_end(start, next_start)
                    finished = bool(end is not None and end < next_start and effective_position >= end)
                else:
                    finished = False
                if finished:
                    self._island_advanced_focus_index = index + 1
                    latched = index + 1
        if latched == index + 1 and 0 <= latched < len(timed):
            visual_index = latched
        phase["visual_focus_index"] = visual_index
        self.lyric_last_phase_index = index
        self.lyric_last_phase_progress = float(phase.get("progress", paint_state.cache.progress))
        self.lyric_last_phase_at = paint_state.cache.updated_at
        self.lyric_last_phase_timing_kind = timing_kind
        return phase

    def _paint_wave_stream_whole_block(self, painter: QPainter, rows: tuple, phase: dict) -> None:
        """Share the half-second reading ramp across original and translation."""
        renderer = getattr(self, "_wave_reading_renderer", None)
        if not isinstance(renderer, LyricReadingRenderer):
            renderer = LyricReadingRenderer()
            self._wave_reading_renderer = renderer
        prepared = []
        for rect, text, size, bold, unread, read, spacing in rows:
            font = self._wave_stream_font(size, QFont.Weight.Bold if bold else QFont.Weight.Normal)
            layout = self._lyric_layout(text, size, bold, spacing)
            x = self._island_lyric_row_x(rect, layout.total_width, True, phase)
            prepared.append((rect, x, text, font, unread, read))
        renderer.paint_whole_block(
            painter, tuple(prepared), float(phase.get("whole_block_progress", 1.0)),
        )

    def _paint_expanded_lyric_row(
        self,
        painter: QPainter,
        row: QRectF,
        text: str,
        size: float,
        alpha: int,
        bold: bool,
        active: bool,
        phase: dict[str, float],
    ):
        lines = self._lyric_display_lines(text)
        if not lines:
            return
        if active and len(lines) > 1:
            secondary_bold = int(getattr(self, "dual_line_secondary_weight", 72)) >= 78
            secondary_spacing = int(getattr(self, "dual_line_secondary_spacing", 100))
            primary_size = max(13, size - 1)
            secondary_size = self._dual_line_secondary_font_size()
            primary_h = self._island_lyric_row_content_height(lines[0], primary_size, True)
            secondary_h = self._island_lyric_row_content_height(lines[1], secondary_size, secondary_bold)
            first_h = max(primary_h, row.height() - secondary_h - self._u(1.0))
            first = QRectF(row.left(), row.top(), row.width(), first_h)
            second = QRectF(row.left(), first.bottom() + self._u(1.0), row.width(), secondary_h)
            if "whole_block_progress" in phase:
                self._paint_wave_stream_whole_block(painter, (
                    (first, lines[0], primary_size, True,
                     self._music_lyric_base_text_color(126), self._music_lyric_text_color(alpha), None),
                    (second, lines[1], secondary_size, secondary_bold,
                     self._music_lyric_base_text_color(92), self._music_lyric_text_color(max(84, int(alpha * 0.74))), secondary_spacing),
                ), phase)
            else:
                self._paint_live_lyric_text(painter, first, lines[0], primary_size, self._music_lyric_base_text_color(126), self._music_lyric_text_color(alpha), True, phase, centered=True, centered_scroll=True)
                self._paint_live_lyric_text(painter, second, lines[1], secondary_size, self._music_lyric_base_text_color(92), self._music_lyric_text_color(max(84, int(alpha * 0.74))), secondary_bold, phase, centered=True, centered_scroll=True, spacing_percent=secondary_spacing)
            return
        text = lines[0] if len(lines) == 1 else "  ".join(lines[:2])
        if active:
            self._paint_live_lyric_text(painter, row, text, size, self._music_lyric_base_text_color(126), self._music_lyric_text_color(alpha), bold, phase, centered=True, centered_scroll=True)
        else:
            self._paint_scrolling_text(
                painter, row, text, size, self._music_lyric_text_color(max(0, min(255, alpha))),
                bold, centered=True, centered_scroll=True,
                font=self._wave_stream_font(size, QFont.Weight.Bold if bold else QFont.Weight.Normal),
            )

    def _paint_lyric_viewport(self, painter: QPainter, rect: QRectF):
        painter.save()
        painter.setClipRect(rect)
        if not self.lyrics:
            painter.restore()
            return
        if self._should_paint_album_island():
            self._paint_album_island(painter, rect)
            painter.restore()
            return
        if len(self.lyrics) <= 1 and self._is_instrumental_track():
            now = time.monotonic()
            phase = self._island_display_phase(now)
            row = QRectF(rect.left(), rect.center().y() - self._u(17.0), rect.width(), self._u(34.0))
            text = self.lyrics[0] if self.lyrics else getattr(self, "no_lyrics_text", "暂无歌词")
            self._paint_live_lyric_text(
                painter,
                row,
                text,
                max(14, min(24, int(getattr(self, "lyric_font_size", 18)))),
                self._music_lyric_base_text_color(110),
                self._music_lyric_text_color(218),
                True,
                phase,
                centered=True,
                centered_scroll=True,
            )
            painter.restore()
            return
        active_size = max(14, min(24, int(getattr(self, "lyric_font_size", 18))))
        side_size = max(10, active_size - 7)
        now = getattr(self, "_island_lyric_frame_at", None)
        now = time.monotonic() if now is None else now
        active_phase = self._island_display_phase(now)
        source_index = max(0, min(len(self.lyrics) - 1, int(active_phase.get("active_index", self.lyric_index))))
        active_index = max(0, min(
            len(self.lyrics) - 1,
            int(active_phase.get("visual_focus_index", source_index)),
        ))
        active_lines = self._lyric_display_lines(self.lyrics[active_index])
        active_line_h = max(
            self._u(max(30.0, active_size + 12.0)),
            self._island_lyric_row_content_height(active_lines[0] if active_lines else "", active_size, True),
        )
        if len(active_lines) > 1:
            secondary_bold = int(getattr(self, "dual_line_secondary_weight", 72)) >= 78
            active_h = max(
                active_line_h * 1.55,
                self._island_lyric_row_content_height(active_lines[0], max(13, active_size - 1), True)
                + self._u(1.0)
                + self._island_lyric_row_content_height(active_lines[1], self._dual_line_secondary_font_size(), secondary_bold),
            )
        else:
            active_h = active_line_h
        side_h = max(
            self._u(max(22.0, side_size + 11.0)),
            self._island_lyric_row_content_height("Mg国", side_size, False),
        )
        gap = self._u(7.0 if len(active_lines) > 1 else 9.0)
        center_row = QRectF(rect.left(), rect.center().y() - active_h / 2.0, rect.width(), active_h)
        is_seek_transition = (
            getattr(self, "lyric_transition_kind", "") == "seek"
            and self.prev_lyric_index != self.lyric_index
            and active_index == source_index
        )
        if active_index != source_index:
            active_phase = dict(active_phase)
            active_phase["progress"] = 0.0
            active_phase["whole_row"] = False
            for key in ("timed_primary_text", "timed_units", "timed_position",
                        "timed_line_end", "timed_character_front", "whole_block_progress"):
                active_phase.pop(key, None)
        target_tops = tuple(
            center_row.top() - side_h - gap - (active_index - 1 - index) * (side_h + gap)
            if index < active_index else (
                center_row.top() + active_h + gap + (index - active_index - 1) * (side_h + gap)
                if index > active_index else center_row.top()
            )
            for index in range(len(self.lyrics))
        )
        spring = getattr(self, "_wave_line_spring", None)
        if not isinstance(spring, LogicalLineSpringChain):
            spring = LogicalLineSpringChain()
            self._wave_line_spring = spring
        track_identity = str(getattr(self, "media_track_id", "") or "") or (
            str(getattr(self, "track_title", "") or ""),
            str(getattr(self, "artist", "") or ""),
        )
        identity_source = (track_identity, tuple(self.lyrics))
        if identity_source != getattr(self, "_wave_row_identity_source", None):
            occurrences: dict[str, int] = {}
            identities = []
            for text in self.lyrics:
                lines = self._lyric_display_lines(text)
                primary = lines[0] if lines else str(text)
                occurrence = occurrences.get(primary, 0)
                occurrences[primary] = occurrence + 1
                identities.append((track_identity, primary, occurrence))
            self._wave_row_identity_source = identity_source
            self._wave_row_identities = tuple(identities)
        row_ids = self._wave_row_identities
        spring_key = (track_identity, active_size, round(rect.width(), 2))
        if spring.row_ids is not None and row_ids != spring.row_ids:
            previous_ids = {identity: index for index, identity in enumerate(spring.row_ids)}
            first, last = spring.visible_range or (0, 0)
            offsets = sorted(
                spring.position_for(previous_ids[identity]) - target_tops[index]
                for index, identity in enumerate(row_ids)
                if identity in previous_ids and first <= previous_ids[identity] < last
            )
            viewport_offset = offsets[len(offsets) // 2] if offsets else 0.0
        elif spring.row_ids == row_ids:
            viewport_offset = spring.viewport_pose(target_tops)[0]
        else:
            viewport_offset = 0.0
        # Find the lyrics physically crossing the island, including skipped
        # sentences. The swept old-to-new interval can span an entire song;
        # only this small moving window needs independent spring steps.
        visible_first = max(0, bisect_left(
            target_tops, rect.top() - max(active_h, side_h) - viewport_offset,
        ) - 2)
        visible_last = min(len(self.lyrics), bisect_right(
            target_tops, rect.bottom() - viewport_offset,
        ) + 2)
        spring_frame = spring.frame(
            spring_key,
            target_tops,
            active_index,
            now,
            animate=True,
            seek=is_seek_transition,
            visible_range=(visible_first, visible_last),
            row_ids=row_ids,
        )

        for idx in range(visible_first, visible_last):
            is_active = idx == active_index
            row_h = active_h if is_active else side_h
            size = active_size if is_active else side_size
            alpha = 246 if is_active else (70 if idx < active_index else 76)
            row = QRectF(center_row)
            row.moveTop(spring_frame.positions[idx])
            row.setHeight(row_h)
            if row.bottom() < rect.top() + self._u(2) or row.top() > rect.bottom() - self._u(2):
                continue
            painter.save()
            painter.setOpacity(max(0.0, min(1.0, alpha / 255.0)))
            self._paint_expanded_lyric_row(painter, row, self.lyrics[idx], size, alpha, is_active, is_active, active_phase)
            painter.restore()
        painter.restore()

    def _paint_plugin_solid_shell(self, painter: QPainter, rect: QRectF, radius: float, folded: bool):
        theme = self._plugin_theme_colors()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme["shell"] if folded else theme["shell_expanded"])
        painter.drawRoundedRect(rect, radius, radius)
        inset = self._u(0.8)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        stroke = QColor(theme["stroke"])
        stroke.setAlpha(min(stroke.alpha(), 166 if folded else 118))
        painter.setPen(QPen(stroke, self._u(1.0)))
        painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), max(1.0, radius - inset), max(1.0, radius - inset))
        inner_stroke = QColor(theme["inner_stroke"])
        inner_stroke.setAlpha(min(inner_stroke.alpha(), 18 if folded else 14) if not self._system_dark_mode() else inner_stroke.alpha())
        painter.setPen(QPen(inner_stroke, self._u(0.8)))
        painter.drawRoundedRect(rect.adjusted(inset * 1.8, inset * 1.8, -inset * 1.8, -inset * 1.8), max(1.0, radius - inset * 1.8), max(1.0, radius - inset * 1.8))
        painter.restore()

    def _paint_plugin(self, painter: QPainter, rect: QRectF, folded: bool):
        radius = rect.height() / 2 if folded else min(self._u(24), rect.height() / 2)
        joined_panel = bool(not folded and self._plugin_panel_visible())
        if not joined_panel:
            self._paint_plugin_solid_shell(painter, rect, radius, folded=folded)
            self._paint_current_island_space_glow(painter, rect, radius)
        if folded or rect.height() < self._u(22):
            return

        plugins = [
            (
                label,
                detail,
                summary,
                color,
                icon,
                plugin_id,
                status,
                alert_title,
                alert_body,
                alert_priority,
                progress,
            )
            for label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority, progress in (self._plugin_tuple(plugin) for plugin in self._enabled_plugin_samples())
        ]
        if not plugins:
            return

        now = time.monotonic()
        count = len(plugins)
        strip_expand_active = bool(self.hovered_key == "plugin" or self._plugin_panel_visible())
        strip_layout = self._plugin_strip_layout(count, rect, now, expand_active=strip_expand_active)
        active_focus = strip_layout.active_focus
        weights = strip_layout.weights
        cells = strip_layout.cells

        for i, (label, detail, summary, color, icon, _plugin_id, status, _alert_title, _alert_body, _alert_priority, progress) in enumerate(plugins):
            cell = cells[i][1]
            sr = cell.adjusted(self._u(5), self._u(6), -self._u(5), -self._u(6))
            focus_weight = weights[i] if i < len(weights) else 0.0
            if self.expanded and strip_expand_active and i == active_focus:
                self._paint_plugin_focus_cell(painter, sr, label, detail, color, icon, focus_weight, False, status, summary=summary, progress=progress)
            else:
                self._paint_plugin_compact_cell(painter, sr, label, detail, summary, color, i == active_focus, status, progress)

    def _plugin_panel_shell_rect(self, panel_rect: QRectF, strip_rect: QRectF | None = None) -> QRectF:
        if strip_rect is None or strip_rect.isNull():
            motion = self.motions.get("plugin") if hasattr(self, "motions") else None
            strip_rect = QRectF(motion.rect) if motion and motion.opacity > 0.02 else QRectF()
        return positioning_plugin_panel_shell_rect(panel_rect, strip_rect)

    def _paint_plugin_panel(self, painter: QPainter, rect: QRectF):
        shell_rect = self._plugin_panel_shell_rect(rect)
        radius = self._plugin_panel_shell_radius(shell_rect)
        self._paint_plugin_solid_shell(painter, shell_rect, radius, folded=False)
        self._paint_current_island_space_glow(painter, shell_rect, radius)
        # The expanded strip and panel share one opaque shell; a strip click's
        # light belongs to that backing, not the strip's transparent host area.
        if str(getattr(self, "_island_glow_current_visual_key", "") or "") == "plugin-panel":
            self._paint_island_button_ripples(
                painter,
                float(getattr(self, "_island_glow_paint_now", 0.0) or time.monotonic()),
                owner_key="plugin",
                shell_rect=shell_rect,
                shell_path=self._rounded_path(shell_rect, radius),
            )
        plugins = [
            (
                label,
                detail,
                summary,
                color,
                icon,
                plugin_id,
                status,
                alert_title,
                alert_body,
                alert_priority,
                progress,
            )
            for label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority, progress in (self._plugin_tuple(plugin) for plugin in self._enabled_plugin_samples())
        ]
        if not plugins:
            theme = self._plugin_theme_colors()
            muted = QColor(theme["muted"])
            muted.setAlpha(170)
            self._paint_scrolling_text(painter, rect.adjusted(self._u(18.0), 0.0, -self._u(18.0), 0.0), "功能待定", 14, muted, False)
            return
        self._paint_plugin_function_panel(painter, rect, self._plugin_panel_focus_model() or {})

    def _plugin_panel_focus_model(self) -> dict[str, object] | None:
        plugins = [
            (
                label,
                detail,
                summary,
                color,
                icon,
                plugin_id,
                status,
                alert_title,
                alert_body,
                alert_priority,
                progress,
            )
            for label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority, progress in (self._plugin_tuple(plugin) for plugin in self._enabled_plugin_samples())
        ]
        if not plugins:
            return None
        index = max(0, min(self._active_plugin_focus(time.monotonic()), len(plugins) - 1))
        model = presentation_plugin_panel_focus_model(plugins, index)
        if model is None:
            return None
        model["action"] = self._plugin_action_model(
            str(model.get("label", "")),
            str(model.get("detail", "")),
            str(model.get("summary", "")),
            str(model.get("status", "")),
        )
        return model

    def _plugin_compact_value(self, label: str, detail: str, summary: str, status: str = "normal") -> str:
        return presentation_plugin_compact_value(label, detail, summary, status)

    def _strip_timer_prefix(self, text: str) -> str:
        return presentation_strip_timer_prefix(text)

    def _time_reminder_kind(self, status: str = "", text: str = "") -> str:
        return presentation_time_reminder_kind(status, text)

    def _time_alert_kind_from_plugin_id(self, plugin_id: str) -> str:
        return presentation_time_alert_kind_from_plugin_id(plugin_id)

    def _time_reminder_icon_path(self, status: str = "running", kind: str = "countdown") -> str:
        status_key = str(status or "").strip().lower()
        kind_key = str(kind or "").strip().lower()
        base = "\u95f9\u949f" if kind_key == "alarm" else "\u5012\u8ba1\u65f6"
        tone = "\u9ed1" if status_key in {"done", "due", "alarm"} else "\u9ec4"
        return str(TIME_REMINDER_ICON_DIR / f"{base}{tone}.svg")

    def _time_reminder_display_text(self, text: str, max_chars: int = 18) -> str:
        return presentation_time_reminder_display_text(text, max_chars)

    def _time_alarm_note_display_text(self, text: str) -> str:
        return str(text or "").strip()

    def _time_alarm_alert_text(self, note: str) -> str:
        return presentation_time_alarm_alert_text(note)

    def _time_alarm_alert_text_from_parts(self, *parts: str) -> str:
        return presentation_time_alarm_alert_text_from_parts(*parts)

    def _time_text_looks_like_countdown(self, text: str) -> bool:
        return presentation_time_text_looks_like_countdown(text)

    def _time_reminder_due_from_alarm(self, alarm: dict[str, object], now: datetime) -> datetime | None:
        return presentation_time_reminder_due_from_alarm(alarm, now)

    def _time_display_segment_text(self, detail: str, now: datetime) -> str:
        return presentation_time_display_segment_text(
            detail,
            now,
            show_seconds=bool(getattr(self, "show_seconds", True)),
        )

    def _time_compact_segments(self, detail: str, summary: str, status: str, alert_body: str = "", now: datetime | None = None) -> list[dict[str, object]]:
        now = now or datetime.now().astimezone()
        return presentation_time_compact_segments(
            detail,
            summary,
            status,
            alert_body,
            now=now,
            show_seconds=bool(getattr(self, "show_seconds", True)),
            countdown_entries=getattr(self, "plugin_countdown_entries", None),
            alarm_rows=self._time_alarm_rows(),
        )

    def _time_compact_cell_layout(self, rect: QRectF, segments: list[dict[str, object]]) -> list[dict[str, object]]:
        if not segments:
            return []
        gap = self._u(4.0)
        pad_x = self._u(5.0)
        inner_top = rect.top() + self._u(3.0)
        inner_h = max(self._u(24.0), rect.height() - self._u(6.0))
        cursor = rect.left() + pad_x
        right_limit = rect.right() - pad_x
        time_text = str(segments[0].get("text") or "")
        has_seconds = bool(re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", time_text))
        time_floor = self._u(86.0 if has_seconds else 66.0)
        time_target = max(time_floor, min(self._u(96.0), len(time_text) * self._u(7.4) + self._u(17.0)))
        reminders = list(segments[1:3])
        min_reminder_w = 0.0
        if reminders:
            circle = inner_h
            min_reminder_w = circle * len(reminders) + gap * max(0, len(reminders) - 1)
        max_time_w = max(self._u(48.0), right_limit - cursor - (gap if reminders else 0.0) - min_reminder_w)
        time_w = max(self._u(48.0), min(time_target, max_time_w))
        time_rect = QRectF(cursor, inner_top, max(0.0, time_w), inner_h)
        layout = [{**segments[0], "kind": "time", "rect": time_rect, "icon_only": False}]
        cursor = time_rect.right() + gap
        available = right_limit - cursor
        if available <= self._u(20.0):
            return layout

        full_widths = []
        for segment in reminders:
            kind = str(segment.get("kind") or "countdown")
            if kind == "countdown":
                base_width = self._u(66.0)
                display_text = self._time_reminder_display_text(str(segment.get("text") or ""), 10)
                if len(display_text) <= 5:
                    full_widths.append(base_width)
                    continue
                text_width = float(QFontMetrics(self._font(10, QFont.Weight.Bold)).horizontalAdvance(display_text))
                icon_size = min(self._u(20.0), inner_h - self._u(5.0))
                required_width = self._u(6.0) + icon_size + self._u(4.0) + text_width + self._u(7.0) + self._u(8.0)
                full_widths.append(max(base_width, min(self._u(148.0), required_width)))
            else:
                full_widths.append(self._u(74.0))
        full_total = sum(full_widths) + gap * max(0, len(full_widths) - 1)
        use_circles = bool(reminders) and full_total > available
        for index, segment in enumerate(reminders):
            target_w = inner_h if use_circles else full_widths[index]
            if cursor + target_w > right_limit + self._u(0.5):
                break
            layout.append({**segment, "rect": QRectF(cursor, inner_top, target_w, inner_h), "icon_only": use_circles})
            cursor += target_w + gap
        return layout

    def _paint_svg_icon(self, painter: QPainter, path: str, rect: QRectF) -> bool:
        renderer = self._svg_renderer_for_path(str(path))
        if renderer is None:
            return False
        try:
            renderer.render(painter, rect)
            return True
        except Exception:
            return False

    def _plugin_alert_display_value(self, label: str, detail: str, summary: str, status: str, alert_title: str = "", alert_body: str = "") -> str:
        return presentation_plugin_alert_display_value(label, detail, summary, status, alert_title, alert_body)

    def _default_time_alarm_rows(self) -> list[dict[str, object]]:
        return []

    def _default_alarm_editor_state(self) -> dict[str, object]:
        return presentation_default_alarm_editor_state(datetime.now().astimezone())

    def _alarm_editor_state_from_row(self, index: int, row: dict[str, object]) -> dict[str, object]:
        return presentation_alarm_editor_state_from_row(index, row, now=datetime.now().astimezone())

    def _alarm_row_time_parts(self, row: dict[str, object]) -> tuple[int, int] | None:
        return presentation_alarm_row_time_parts(row)

    def _alarm_int(self, value: object, lower: int, upper: int, default: int) -> int:
        return presentation_alarm_int(value, lower, upper, default)

    def _alarm_row_matches_date(self, row: dict[str, object], due: datetime) -> bool:
        return presentation_alarm_row_matches_date(row, due)

    def _coerce_alarm_rows(self, rows: object) -> list[dict[str, object]]:
        return presentation_coerce_alarm_rows(rows, boundary_year=self._alarm_year_boundary_year())

    def _settings_alarm_rows(self) -> list[dict[str, object]] | None:
        getter = getattr(self, "settings_getter", None)
        if not callable(getter):
            return None
        raw = self._plugin_setting("v2_alarm_rows", None)
        if raw is None:
            return None
        if raw == "":
            return []
        payload = raw
        if isinstance(raw, str):
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                return []
        return self._coerce_alarm_rows(payload)

    def _persist_alarm_rows(self, rows: object) -> list[dict[str, object]]:
        normalized = self._coerce_alarm_rows(rows)
        self.plugin_alarm_rows = normalized
        self._set_plugin_setting("v2_alarm_rows", json.dumps(normalized, ensure_ascii=False, separators=(",", ":")))
        return normalized

    def _time_alarm_rows(self) -> list[dict[str, object]]:
        settings_rows = self._settings_alarm_rows()
        if settings_rows is not None:
            self.plugin_alarm_rows = settings_rows[:8]
            return self.plugin_alarm_rows
        rows = getattr(self, "plugin_alarm_rows", None)
        if not isinstance(rows, list):
            rows = self._default_time_alarm_rows()
        capped = [dict(row) for row in rows[:8] if isinstance(row, dict)]
        self.plugin_alarm_rows = capped
        return capped

    def _time_alarm_editor_state(self) -> dict[str, object]:
        editor = getattr(self, "plugin_alarm_editor", None)
        state = presentation_alarm_editor_state(editor, now=datetime.now().astimezone())
        if isinstance(editor, dict):
            editor.clear()
            editor.update(state)
            state = editor
        self.plugin_alarm_editor = state
        return state

    def _alarm_frequency_units(self) -> list[tuple[str, str]]:
        return presentation_alarm_frequency_units()

    def _alarm_unit_from_frequency(self, label: str) -> str:
        return presentation_alarm_unit_from_frequency(label)

    def _alarm_weekdays_from_value(self, value) -> list[int]:
        return presentation_alarm_weekdays_from_value(value)

    def _days_in_month(self, year: int, month: int) -> int:
        return presentation_days_in_month(year, month)

    def _is_leap_year(self, year: int) -> bool:
        year = int(year)
        return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)

    def _alarm_year_boundary_year(self) -> int:
        return datetime.now().astimezone().year + 1

    def _yearly_alarm_max_day(self, month: int) -> int:
        return presentation_yearly_alarm_max_day(month, self._alarm_year_boundary_year())

    def _normalize_alarm_editor_recurrence(self, editor: dict[str, object]) -> dict[str, object]:
        normalized = presentation_normalize_alarm_editor_recurrence(editor, self._alarm_year_boundary_year())
        editor.clear()
        editor.update(normalized)
        return editor

    def _alarm_recurrence_label(self, editor: dict[str, object]) -> str:
        return presentation_alarm_recurrence_label(editor)

    def _alarm_recurrence_value_label(self, editor: dict[str, object]) -> str:
        return presentation_alarm_recurrence_value_label(editor)

    def _time_countdown_active_model(self, model: dict[str, object]) -> dict[str, object]:
        return presentation_time_countdown_active_model(
            model,
            paused_remaining=self._timer_paused_remaining_seconds(),
            paused_progress=getattr(self, "plugin_timer_paused_progress", 0.0),
        )

    def _time_countdown_entries(self, model: dict[str, object]) -> list[dict[str, object]]:
        return presentation_time_countdown_entries(
            getattr(self, "plugin_countdown_entries", None),
            dict(model.get("countdown_active") or {}),
            now=datetime.now().astimezone(),
            paused_remaining=self._timer_paused_remaining_seconds(),
        )

    def _time_plugin_panel_model(self, model: dict[str, object]) -> dict[str, object]:
        mode = str(getattr(self, "plugin_time_mode", "dashboard") or "dashboard")
        clock_text = str(model.get("detail", "") or "").strip() or datetime.now().astimezone().strftime("%H:%M")
        dashboard = {
            "mode": mode if mode == "alarm_editor" else "dashboard",
            "clock": {
                "time": clock_text,
                "date": str(model.get("summary", "") or "").strip(),
                "location": "中国 北京",
            },
            "countdown_presets": [
                {"minutes": 5, "label": "5"},
                {"minutes": 15, "label": "15"},
                {"minutes": 10, "label": "10"},
                {"minutes": 20, "label": "20"},
            ],
            "countdown_active": self._time_countdown_active_model(model),
            "alarms": [dict(row) for row in self._time_alarm_rows()[:8]],
        }
        dashboard["countdown_entries"] = self._time_countdown_entries(dashboard)
        pending_alarm = self._capture_time_alarm_due_pending(model) or self._time_alarm_due_pending()
        if pending_alarm:
            dashboard["alarm_due_pending"] = pending_alarm
        if dashboard["mode"] == "alarm_editor":
            dashboard["editor"] = dict(self._time_alarm_editor_state())
        return dashboard

    def _adjust_alarm_editor_field(self, field: str, steps: int):
        editor = self._time_alarm_editor_state()
        steps = int(steps)
        result = presentation_adjust_alarm_editor_field(
            editor,
            field,
            steps,
            boundary_year=self._alarm_year_boundary_year(),
        )
        editor.clear()
        editor.update(dict(result.get("editor") or {}))
        self.plugin_alarm_editor = editor
        reflow = result.get("reflow")
        if isinstance(reflow, dict):
            self._begin_alarm_editor_reflow_animation(
                str(reflow.get("from") or ""),
                str(reflow.get("to") or ""),
                int(reflow.get("steps") or steps),
            )
        adjusted = bool(result.get("adjusted", False))
        if adjusted:
            self._begin_alarm_editor_spin(field, steps)
        self._request_visual_update()

    def _begin_alarm_editor_spin(self, field: str, steps: int):
        animations = getattr(self, "plugin_alarm_editor_spin_animations", None)
        if not isinstance(animations, dict):
            animations = {}
        direction = 1 if int(steps or 0) >= 0 else -1
        animations[str(field)] = {"direction": direction, "started_at": time.monotonic()}
        self.plugin_alarm_editor_spin_animations = animations

    def _begin_alarm_editor_page_animation(self, from_page: str, to_page: str, steps: int):
        direction = 1 if int(steps or 0) >= 0 else -1
        self.plugin_alarm_editor_page_animation = {
            "from": str(from_page or "main"),
            "to": str(to_page or "main"),
            "direction": direction,
            "started_at": time.monotonic(),
        }

    def _begin_alarm_editor_reflow_animation(self, from_unit: str, to_unit: str, steps: int):
        direction = 1 if int(steps or 0) >= 0 else -1
        self.plugin_alarm_editor_reflow_animation = {
            "from": str(from_unit or ""),
            "to": str(to_unit or ""),
            "direction": direction,
            "started_at": time.monotonic(),
        }

    def _alarm_editor_reflow_offset(self, unit: str) -> float:
        animation = getattr(self, "plugin_alarm_editor_reflow_animation", {})
        if not isinstance(animation, dict) or not animation:
            return 0.0
        if str(animation.get("to") or "") != str(unit or ""):
            return 0.0
        try:
            started_at = float(animation.get("started_at", 0.0) or 0.0)
            direction = 1 if int(animation.get("direction", 1) or 1) >= 0 else -1
        except (TypeError, ValueError):
            return 0.0
        elapsed = max(0.0, time.monotonic() - started_at)
        duration = 0.24
        if elapsed >= duration:
            self.plugin_alarm_editor_reflow_animation = {}
            return 0.0
        progress = max(0.0, min(1.0, elapsed / duration))
        eased = 1.0 - pow(1.0 - progress, 3)
        return -direction * (1.0 - eased)

    def _alarm_editor_page_transition_offset(self, page: str) -> float:
        animation = getattr(self, "plugin_alarm_editor_page_animation", {})
        if not isinstance(animation, dict) or not animation:
            return 0.0
        if str(animation.get("to") or "") != str(page or ""):
            return 0.0
        try:
            started_at = float(animation.get("started_at", 0.0) or 0.0)
            direction = 1 if int(animation.get("direction", 1) or 1) >= 0 else -1
        except (TypeError, ValueError):
            return 0.0
        elapsed = max(0.0, time.monotonic() - started_at)
        duration = self._animation_duration(0.26)
        if elapsed >= duration:
            self.plugin_alarm_editor_page_animation = {}
            return 0.0
        progress = max(0.0, min(1.0, elapsed / duration))
        eased = 1.0 - pow(1.0 - progress, 3)
        return direction * (1.0 - eased)

    def _alarm_editor_spin_offset(self, field: str) -> float:
        animations = getattr(self, "plugin_alarm_editor_spin_animations", {})
        if not isinstance(animations, dict):
            return 0.0
        animation = animations.get(str(field))
        if not isinstance(animation, dict):
            return 0.0
        try:
            started_at = float(animation.get("started_at", 0.0) or 0.0)
            direction = 1 if int(animation.get("direction", 1) or 1) >= 0 else -1
        except (TypeError, ValueError):
            return 0.0
        elapsed = max(0.0, time.monotonic() - started_at)
        duration = self._animation_duration(0.22)
        if elapsed >= duration:
            animations.pop(str(field), None)
            return 0.0
        progress = max(0.0, min(1.0, elapsed / duration))
        eased = 1.0 - pow(1.0 - progress, 3)
        return direction * (1.0 - eased)

    def _with_alarm_picker_offsets(self, field: str, values: list[dict[str, object]]) -> list[dict[str, object]]:
        offset = self._alarm_editor_spin_offset(field)
        if abs(offset) <= 0.001:
            return values
        adjusted = []
        for item in values:
            clone = dict(item)
            clone["offset"] = offset
            adjusted.append(clone)
        return adjusted

    def _alarm_picker_window_values(self, field: str, options: list[object], current: object, formatter=None, visible_count: int = 7) -> list[dict[str, object]]:
        if not options:
            return self._with_alarm_picker_offsets(field, [{"text": str(current), "current": True, "opacity": 1.0}])
        formatter = formatter or (lambda value: str(value))
        visible_count = max(3, int(visible_count))
        if visible_count % 2 == 0:
            visible_count += 1
        center = visible_count // 2
        try:
            current_index = options.index(current)
        except ValueError:
            current_index = 0
        values = []
        for slot in range(visible_count):
            distance = abs(slot - center)
            if slot == center:
                value = current
                current_row = True
            else:
                value = options[(current_index + slot - center) % len(options)]
                current_row = False
            values.append(
                {
                    "text": formatter(value),
                    "current": current_row,
                    "opacity": 1.0 if current_row else max(0.18, 0.54 - distance * 0.1),
                }
            )
        return self._with_alarm_picker_offsets(field, values)

    def _alarm_picker_display_values(self, field: str) -> list[dict[str, object]]:
        field = str(field or "")
        return presentation_alarm_picker_display_values(
            self._time_alarm_editor_state(),
            field,
            boundary_year=self._alarm_year_boundary_year(),
            offset=self._alarm_editor_spin_offset(field),
        )

    def _handle_alarm_editor_key(self, text: str, key: int | None = None) -> bool:
        if str(getattr(self, "plugin_time_mode", "dashboard")) != "alarm_editor":
            return False
        focus = str(getattr(self, "plugin_alarm_editor_focus", "") or "")
        key_action = ""
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) or text == "\b":
            key_action = "backspace"
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            key_action = "enter"
        buffers = getattr(self, "plugin_alarm_editor_input_buffers", None)
        result = presentation_handle_alarm_editor_key_input(
            self._time_alarm_editor_state(),
            focus,
            text,
            key_action,
            input_buffers=buffers if isinstance(buffers, dict) else {},
            boundary_year=self._alarm_year_boundary_year(),
        )
        if not bool(result.get("handled", False)):
            return False
        if bool(result.get("save", False)):
            self._activate_plugin_action("alarm-editor-save")
            return True
        self.plugin_alarm_editor = dict(result.get("editor") or {})
        self.plugin_alarm_editor_input_buffers = dict(result.get("input_buffers") or {})
        if "note_hint" in result:
            self.plugin_alarm_editor_note_hint = str(result.get("note_hint") or "")
            try:
                hint_seconds = float(result.get("note_hint_seconds", 0.0) or 0.0)
            except (TypeError, ValueError):
                hint_seconds = 0.0
            self.plugin_alarm_editor_note_hint_until = time.monotonic() + hint_seconds if hint_seconds > 0.0 else 0.0
        spin_field = str(result.get("spin_field") or "")
        if spin_field:
            self._begin_alarm_editor_spin(spin_field, int(result.get("spin_steps") or 1))
        self._request_visual_update()
        return True

    def _handle_alarm_editor_key_event(self, event) -> bool:
        if str(getattr(self, "plugin_time_mode", "dashboard")) != "alarm_editor":
            return False
        try:
            key = event.key()
        except Exception:
            key = None
        if key == Qt.Key.Key_Escape:
            self.plugin_time_mode = "dashboard"
            self._request_visual_update()
            return True
        try:
            text = event.text()
        except Exception:
            text = ""
        return self._handle_alarm_editor_key(text, key)

    def _handle_time_alarm_editor_wheel(self, event) -> bool:
        if str(getattr(self, "plugin_time_mode", "dashboard")) != "alarm_editor":
            return False
        rects = getattr(self, "plugin_alarm_editor_rects", {})
        try:
            pos = event.position()
        except Exception:
            return False
        try:
            delta = event.angleDelta().y()
        except Exception:
            delta = 0
        field = next((name for name, rect in rects.items() if rect.contains(pos)), "") if isinstance(rects, dict) else ""
        if not field:
            editor = self._time_alarm_editor_state()
            detail_mode = str(editor.get("detail_mode") or "").strip()
            width = float(getattr(self, "plugin_panel_last_width", 0.0) or 0.0)
            if detail_mode or width <= 0.0 or width >= self._u(600.0):
                return False
            pages = ["main", "note"]
            current = str(editor.get("editor_page") or "main")
            index = pages.index(current) if current in pages else 0
            step = 1 if delta < 0 else -1
            target = pages[(index + step) % len(pages)]
            editor["editor_page"] = target
            editor["detail_mode"] = ""
            self.plugin_alarm_editor = editor
            self._begin_alarm_editor_page_animation(current, target, step)
            try:
                event.accept()
            except Exception:
                pass
            self._request_visual_update()
            return True
        steps = -1 if delta > 0 else 1
        self._adjust_alarm_editor_field(field, steps)
        try:
            event.accept()
        except Exception:
            pass
        return True

    def _handle_time_panel_module_wheel(self, event) -> bool:
        if str(getattr(self, "plugin_time_mode", "dashboard")) == "alarm_editor":
            return False
        width = float(getattr(self, "plugin_panel_last_width", 0.0) or 0.0)
        if self._handle_time_alarm_list_wheel(event):
            return True
        if width <= 0.0 or width >= self._time_panel_breakpoint():
            return False
        modules = self._time_panel_modules()
        current = self._time_visible_module_for_width(width)
        index = modules.index(current) if current in modules else 0
        try:
            delta = event.angleDelta().y()
        except Exception:
            delta = 0
        step = 1 if delta < 0 else -1
        target = modules[(index + step) % len(modules)]
        self.plugin_time_visible_module = target
        self._begin_time_module_animation(current, target, step)
        try:
            event.accept()
        except Exception:
            pass
        self._request_visual_update()
        return True

    def _handle_time_alarm_list_wheel(self, event) -> bool:
        visible = self._time_visible_module_for_width(float(getattr(self, "plugin_panel_last_width", 0.0) or 0.0))
        if visible not in {"alarm", "all"}:
            return False
        list_rect = getattr(self, "plugin_alarm_list_rect", QRectF())
        if not isinstance(list_rect, QRectF) or list_rect.isNull():
            return False
        try:
            pos = event.position()
        except Exception:
            return False
        if not list_rect.contains(pos):
            return False
        alarms = self._time_alarm_rows()
        start, count = getattr(self, "plugin_alarm_visible_range", (0, 0))
        try:
            start = int(start)
            count = int(count)
        except (TypeError, ValueError):
            start, count = 0, 0
        if count <= 0:
            return False
        try:
            spacer_rows = float(getattr(self, "plugin_alarm_list_bottom_spacer_rows", 0.0) or 0.0)
        except (TypeError, ValueError):
            spacer_rows = 0.0
        max_start = float(max(0.0, len(alarms) - count + max(0.0, spacer_rows)))
        if max_start <= 0.0:
            return False
        try:
            delta = event.angleDelta().y()
        except Exception:
            delta = 0
        if delta == 0:
            return False
        step = -float(delta) / 120.0
        try:
            current = float(getattr(self, "plugin_alarm_list_scroll_index", start) or 0.0)
        except (TypeError, ValueError):
            current = float(start)
        current = max(0.0, min(max_start, current))
        target = max(0.0, min(max_start, current + step))
        if math.isclose(target, current, abs_tol=0.001):
            return False
        self.plugin_alarm_list_scroll_index = target
        self._clear_alarm_delete_mode()
        try:
            event.accept()
        except Exception:
            pass
        self._request_visual_update()
        return True

    def _plugin_panel_alert_progress(self, index: int) -> float:
        alerts = self._active_plugin_alerts(3, include_expanded=True)
        if not alerts:
            return 0.0
        for alert in alerts:
            alert_index = int(alert[0])
            if alert_index == int(index):
                return 1.0
        return 0.0

    def _plugin_ring_style(self, status: str = "normal") -> dict[str, float]:
        style = presentation_plugin_ring_style(status)
        return {
            "width": self._u(float(style["width"])),
            "track_width": self._u(float(style["track_width"])),
            "pulse": float(style["pulse"]),
        }

    def _plugin_action_model(self, label: str, detail: str, summary: str, status: str) -> dict[str, str]:
        return presentation_plugin_action_model(label, detail, summary, status)

    def _plugin_panel_layout(self, rect: QRectF) -> dict[str, QRectF]:
        return positioning_plugin_panel_layout(
            rect,
            visible_module=self._time_visible_module_for_width(rect.width()),
            breakpoint=self._time_panel_breakpoint(),
            unit=self._u(1.0),
        )

    def _time_plugin_panel_layout(self, rect: QRectF) -> dict[str, QRectF]:
        return positioning_plugin_panel_time_layout(
            rect,
            visible_module=self._time_visible_module_for_width(rect.width()),
            breakpoint=self._time_panel_breakpoint(),
            stretch_breakpoint=self._time_panel_stretch_breakpoint(),
            unit=self._u(1.0),
        )

    def _plugin_panel_reminder_models(self, limit: int = 3) -> list[dict[str, object]]:
        samples = self._enabled_plugin_samples()
        sample_tuples = [self._plugin_tuple(sample) for sample in samples]
        return presentation_plugin_panel_reminder_models(
            self._active_plugin_alerts(limit, include_expanded=True),
            sample_tuples,
        )

    def _plugin_function_lines(self, label: str, detail: str, summary: str, status: str, alert_title: str = "", alert_body: str = "") -> tuple[str, tuple[str, ...]]:
        return presentation_plugin_function_lines(label, detail, summary, status, alert_title, alert_body)

    def _time_plugin_panel_sections(self, model: dict[str, object]) -> list[dict[str, str]]:
        return presentation_time_plugin_panel_sections(model)

    def _weather_plugin_panel_sections(self, model: dict[str, object]) -> list[dict[str, str]]:
        return presentation_weather_plugin_panel_sections(model)

    def _agenda_plugin_panel_sections(self, model: dict[str, object]) -> list[dict[str, str]]:
        return presentation_agenda_plugin_panel_sections(model)

    def _generic_plugin_panel_sections(self, model: dict[str, object]) -> list[dict[str, str]]:
        return presentation_generic_plugin_panel_sections(model)

    def _plugin_panel_sections(self, model: dict[str, object]) -> list[dict[str, str]]:
        return presentation_plugin_panel_sections(model)

    def _paint_plugin_panel_module(
        self,
        painter: QPainter,
        rect: QRectF,
        title: str,
        body: str,
        theme: dict[str, QColor],
        color: QColor,
        emphasized: bool = False,
        reserve_action: bool = False,
    ):
        radius = self._plugin_panel_module_radius(rect, min(self._u(15.0), rect.height() * 0.28))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme["cell_active"] if emphasized else theme["cell"])
        painter.drawRoundedRect(rect, radius, radius)
        painter.setPen(QPen(theme["stroke"], self._u(1.0)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(self._u(0.8), self._u(0.8), -self._u(0.8), -self._u(0.8)), radius, radius)

        accent = QRectF(rect.left() + self._u(10.0), rect.top() + self._u(9.0), self._u(22.0), self._u(3.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 235))
        painter.drawRoundedRect(accent, self._u(1.5), self._u(1.5))

        title_rect = QRectF(rect.left() + self._u(10.0), rect.top() + self._u(14.0), rect.width() - self._u(20.0), self._u(18.0))
        painter.setFont(self._font(8, QFont.Weight.DemiBold))
        painter.setPen(theme["muted"])
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)

        body_bottom = rect.bottom() - (self._u(28.0) if reserve_action else self._u(8.0))
        body_rect = QRectF(rect.left() + self._u(10.0), title_rect.bottom() + self._u(2.0), rect.width() - self._u(20.0), max(self._u(18.0), body_bottom - title_rect.bottom()))
        self._paint_scrolling_text(painter, body_rect, body, 12 if emphasized else 10, theme["text"], emphasized)

    def _paint_plugin_panel_action_button(self, painter: QPainter, rect: QRectF, action: dict[str, str], color: QColor):
        command = str(action.get("command", ""))
        if not command:
            return
        button_h = min(self._u(24.0), max(self._u(20.0), rect.height() * 0.26))
        button = QRectF(rect.left() + self._u(10.0), rect.bottom() - button_h - self._u(8.0), rect.width() - self._u(20.0), button_h)
        self.plugin_action_rects[command] = QRectF(button)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 232))
        painter.drawRoundedRect(button, self._u(8.0), self._u(8.0))
        painter.setFont(self._font(8, QFont.Weight.DemiBold))
        painter.setPen(QColor(255, 255, 255, 246))
        painter.drawText(button.adjusted(self._u(8.0), 0.0, -self._u(8.0), 0.0), Qt.AlignmentFlag.AlignCenter, str(action.get("label") or "操作"))

    def _paint_time_clock_module(self, painter: QPainter, rect: QRectF, section: dict[str, str], theme: dict[str, QColor], color: QColor, progress: float, status: str):
        self._paint_plugin_panel_module(painter, rect, section["title"], "", theme, color, emphasized=True)
        face_size = min(rect.width() - self._u(22.0), rect.height() - self._u(34.0))
        face = QRectF(rect.center().x() - face_size / 2.0, rect.top() + self._u(26.0), face_size, face_size)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 238), self._u(3.2), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawEllipse(face)
        self._paint_plugin_progress_ring(painter, face.adjusted(self._u(4.0), self._u(4.0), -self._u(4.0), -self._u(4.0)), color, progress, status)
        center = face.center()
        painter.setPen(QPen(theme["text"], self._u(2.5), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(center, QPointF(center.x() + face.width() * 0.22, center.y() + face.height() * 0.13))
        painter.setPen(QPen(theme["muted"], self._u(1.8), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(center, QPointF(center.x() - face.width() * 0.16, center.y() - face.height() * 0.18))

    def _paint_time_module_shell(self, painter: QPainter, rect: QRectF, theme: dict[str, QColor], active: bool = False):
        ref = self._time_reference_palette(theme, QColor(255, 184, 0))
        radius = self._plugin_panel_module_radius(rect, min(self._u(16.0), rect.height() * 0.26))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ref["module"])
        painter.drawRoundedRect(rect, radius, radius)
        painter.setPen(QPen(ref.get("module_stroke", ref["accent"]), self._u(1.2 if not self._system_dark_mode() else 1.4)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(self._u(0.8), self._u(0.8), -self._u(0.8), -self._u(0.8)), radius, radius)

    def _time_clock_hand_angles(self, clock_text: str = "") -> dict[str, float]:
        match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", str(clock_text or ""))
        if match:
            hour = int(match.group(1)) % 24
            minute = max(0, min(59, int(match.group(2))))
            second = max(0, min(59, int(match.group(3) or 0)))
        else:
            now = datetime.now().astimezone()
            hour, minute, second = now.hour, now.minute, now.second
        return {
            "hour": (hour % 12) * 30.0 + minute * 0.5 + second / 120.0,
            "minute": minute * 6.0 + second * 0.1,
            "second": second * 6.0,
        }

    def _clock_hand_end(self, center: QPointF, length: float, angle_degrees: float) -> QPointF:
        radians = math.radians(float(angle_degrees) - 90.0)
        return QPointF(center.x() + math.cos(radians) * length, center.y() + math.sin(radians) * length)

    def _draw_time_clock_face(self, painter: QPainter, face: QRectF, color: QColor, theme: dict[str, QColor], clock_text: str = ""):
        ref = self._time_reference_palette(theme, color)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(ref["accent_track"], self._u(5.0), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawEllipse(face.adjusted(-self._u(1.0), -self._u(1.0), self._u(1.0), self._u(1.0)))
        painter.setPen(QPen(ref["accent"], self._u(1.5), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawEllipse(face)
        center = face.center()
        angles = self._time_clock_hand_angles(clock_text)
        radius = face.width() / 2.0
        painter.setPen(QPen(ref["accent"], self._u(2.4), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(center, self._clock_hand_end(center, radius * 0.48, angles["hour"]))
        painter.setPen(QPen(ref["accent"], self._u(1.7), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(center, self._clock_hand_end(center, radius * 0.70, angles["minute"]))
        second_pen = QColor(ref["accent"])
        second_pen.setAlpha(155)
        painter.setPen(QPen(second_pen, self._u(0.9), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(center, self._clock_hand_end(center, radius * 0.76, angles["second"]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ref["accent"])
        painter.drawEllipse(QRectF(center.x() - self._u(2.3), center.y() - self._u(2.3), self._u(4.6), self._u(4.6)))

    def _paint_time_dashboard_clock(self, painter: QPainter, rect: QRectF, model: dict[str, object], color: QColor, theme: dict[str, QColor]):
        ref = self._time_reference_palette(theme, color)
        self._paint_time_module_shell(painter, rect, theme, active=True)
        clock = model["clock"]
        face_size = min(rect.width() - self._u(24.0), rect.height() - self._u(35.0))
        face = QRectF(rect.center().x() - face_size / 2.0, rect.top() + self._u(10.0), face_size, face_size)
        self._draw_time_clock_face(painter, face, color, theme, str(clock.get("time") or ""))
        painter.setFont(self._font(8, QFont.Weight.DemiBold))
        painter.setPen(ref["accent"])
        painter.drawText(QRectF(rect.left() + self._u(8.0), face.bottom() + self._u(2.0), rect.width() - self._u(16.0), self._u(14.0)), Qt.AlignmentFlag.AlignCenter, str(clock["location"]))

    def _time_countdown_ring_rect(self, rect: QRectF) -> QRectF:
        ring_size = min(rect.width() - self._u(28.0), rect.height() - self._u(18.0))
        ring_size = max(self._u(42.0), ring_size)
        center = rect.center() + QPointF(0.0, self._u(4.0))
        return QRectF(center.x() - ring_size / 2.0, center.y() - ring_size / 2.0, ring_size, ring_size)

    def _time_countdown_ring_widths(self) -> dict[str, float]:
        return {"width": self._u(7.8), "track_width": self._u(6.6)}

    def _paint_time_countdown_text_button(self, painter: QPainter, rect: QRectF, label: str, fill: QColor, stroke: QColor, text: QColor):
        painter.setPen(QPen(stroke, self._u(1.3)))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
        painter.setFont(self._font(12, QFont.Weight.Bold))
        painter.setPen(text)
        painter.drawText(rect.adjusted(self._u(5.0), 0.0, -self._u(5.0), 0.0), Qt.AlignmentFlag.AlignCenter, label)

    def _paint_time_countdown_icon_button(self, painter: QPainter, rect: QRectF, kind: str, fill: QColor, stroke: QColor, icon: QColor):
        painter.setPen(QPen(stroke, self._u(1.0)))
        painter.setBrush(fill)
        painter.drawEllipse(rect)
        painter.setPen(QPen(icon, self._u(2.2), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if kind == "pause":
            bar_w = rect.width() * 0.13
            gap = rect.width() * 0.12
            top = rect.top() + rect.height() * 0.25
            bottom = rect.bottom() - rect.height() * 0.25
            x1 = rect.center().x() - gap - bar_w / 2.0
            x2 = rect.center().x() + gap + bar_w / 2.0
            painter.drawLine(QPointF(x1, top), QPointF(x1, bottom))
            painter.drawLine(QPointF(x2, top), QPointF(x2, bottom))
            return
        if kind == "play":
            path = QPainterPath()
            path.moveTo(rect.left() + rect.width() * 0.38, rect.top() + rect.height() * 0.28)
            path.lineTo(rect.left() + rect.width() * 0.38, rect.bottom() - rect.height() * 0.28)
            path.lineTo(rect.right() - rect.width() * 0.27, rect.center().y())
            path.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(icon)
            painter.drawPath(path)
            return
        inset = rect.width() * 0.30
        painter.drawLine(QPointF(rect.left() + inset, rect.top() + inset), QPointF(rect.right() - inset, rect.bottom() - inset))
        painter.drawLine(QPointF(rect.right() - inset, rect.top() + inset), QPointF(rect.left() + inset, rect.bottom() - inset))

    def _paint_static_elided_text(
        self,
        painter: QPainter,
        rect: QRectF,
        text: str,
        size: int,
        color: QColor,
        bold: bool,
        alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft,
    ):
        font = self._font(size, QFont.Weight.Bold if bold else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(color)
        fm = QFontMetrics(font)
        available = max(1, int(rect.width() - self._u(4.0)))
        clipped = fm.elidedText(str(text or ""), Qt.TextElideMode.ElideRight, available)
        painter.save()
        painter.setClipRect(rect)
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | alignment, clipped)
        painter.restore()

    def _time_alarm_note_should_scroll(self, text: str, rect: QRectF, size: int, bold: bool, max_chars: int) -> bool:
        value = str(text or "").strip()
        threshold = 5 if int(max_chars) <= 5 else max(4, int(max_chars))
        if len(value) > threshold:
            return True
        font = self._font(size, QFont.Weight.Bold if bold else QFont.Weight.Normal)
        return QFontMetrics(font).horizontalAdvance(value) > rect.width() - self._u(8.0)

    def _paint_time_symbol(self, painter: QPainter, rect: QRectF, kind: str, color: QColor, stroke_width: float | None = None):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(color, stroke_width if stroke_width is not None else self._u(1.8), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if str(kind or "").lower() == "alarm":
            painter.drawEllipse(rect.adjusted(self._u(1.0), self._u(1.0), -self._u(1.0), -self._u(1.0)))
            center = rect.center()
            painter.drawLine(center, QPointF(center.x(), rect.top() + rect.height() * 0.28))
            painter.drawLine(center, QPointF(rect.right() - rect.width() * 0.28, rect.bottom() - rect.height() * 0.30))
        else:
            top = rect.top() + rect.height() * 0.20
            bottom = rect.bottom() - rect.height() * 0.20
            left = rect.left() + rect.width() * 0.30
            right = rect.right() - rect.width() * 0.30
            mid = rect.center().y()
            painter.drawLine(QPointF(left, top), QPointF(right, top))
            painter.drawLine(QPointF(left, bottom), QPointF(right, bottom))
            painter.drawLine(QPointF(left, top), QPointF(rect.center().x(), mid))
            painter.drawLine(QPointF(right, top), QPointF(rect.center().x(), mid))
            painter.drawLine(QPointF(rect.center().x(), mid), QPointF(left, bottom))
            painter.drawLine(QPointF(rect.center().x(), mid), QPointF(right, bottom))
        painter.restore()

    def _paint_time_countdown_ring(self, painter: QPainter, ring: QRectF, entry: dict[str, object], ref: dict[str, QColor]):
        widths = self._time_countdown_ring_widths()
        status = str(entry.get("status") or "running").lower()
        progress = 1.0 if status in {"done", "due", "alarm"} else max(0.0, min(1.0, float(entry.get("progress", 0.0) or 0.0)))
        ring_line = ring.adjusted(self._u(4.0), self._u(4.0), -self._u(4.0), -self._u(4.0))
        painter.setPen(QPen(ref["accent_track"], widths["track_width"], Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(ring_line)
        painter.setPen(QPen(ref["accent"], widths["width"], Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(ring_line, 90 * 16, int(-360 * 16 * progress))
        painter.setFont(self._font(16, QFont.Weight.Bold))
        painter.setPen(ref["text"])
        painter.drawText(ring, Qt.AlignmentFlag.AlignCenter, str(entry.get("text") or ""))

    def _paint_time_countdown_pause_overlay(self, painter: QPainter, rect: QRectF, ring: QRectF, ref: dict[str, QColor]):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 116))
        painter.drawRoundedRect(rect.adjusted(self._u(2.0), self._u(2.0), -self._u(2.0), -self._u(2.0)), min(self._u(15.0), rect.height() * 0.24), min(self._u(15.0), rect.height() * 0.24))
        icon_size = min(self._u(38.0), ring.height() * 0.34)
        play = QRectF(ring.center().x() - icon_size / 2.0, ring.center().y() - icon_size * 1.22, icon_size, icon_size)
        clear = QRectF(ring.center().x() - icon_size / 2.0, ring.center().y() + icon_size * 0.38, icon_size, icon_size)
        red = QColor(223, 0, 0, 245)
        self._paint_time_countdown_icon_button(painter, play, "play", ref["active"], ref["active"], QColor(255, 255, 255, 246))
        self._paint_time_countdown_icon_button(painter, clear, "clear", red, red, QColor(255, 255, 255, 246))
        self.plugin_action_rects["timer-resume"] = QRectF(play.adjusted(-self._u(2.0), -self._u(2.0), self._u(2.0), self._u(2.0)))
        self.plugin_action_rects["timer-clear"] = QRectF(clear.adjusted(-self._u(2.0), -self._u(2.0), self._u(2.0), self._u(2.0)))

    def _paint_time_countdown_module(self, painter: QPainter, rect: QRectF, model: dict[str, object], color: QColor, theme: dict[str, QColor]):
        active = model["countdown_active"]
        ref = self._time_reference_palette(theme, color)
        self._paint_time_module_shell(painter, rect, theme, active=bool(active.get("active")))
        title_rect = QRectF(rect.left() + self._u(12.0), rect.top() + self._u(8.0), rect.width() - self._u(24.0), self._u(18.0))
        painter.setFont(self._font(9, QFont.Weight.Bold))
        painter.setPen(ref["accent"])
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "倒计时")
        entries = self._time_countdown_entries(model)
        if entries:
            layout_mode = str(model.get("time_layout_mode") or "").lower()
            stretch_controls = layout_mode == "stretch" or (not layout_mode and rect.width() >= self._u(250.0))
            show_two = len(entries) > 1 and stretch_controls
            terminal_statuses = {"done", "due", "alarm"}
            if show_two:
                ring_size = min(rect.height() - self._u(16.0), (rect.width() - self._u(34.0)) / 2.0)
                ring_size = max(self._u(52.0), ring_size)
                total_w = ring_size * 2.0 + self._u(8.0)
                x = rect.center().x() - total_w / 2.0
                y = rect.center().y() - ring_size / 2.0 + self._u(4.0)
                for index, entry in enumerate(entries[:2]):
                    ring = QRectF(x + index * (ring_size + self._u(8.0)), y, ring_size, ring_size)
                    self._paint_time_countdown_ring(painter, ring, entry, ref)
                    entry_status = str(entry.get("status") or "").lower()
                    if entry_status == "paused":
                        self._paint_time_countdown_pause_overlay(painter, QRectF(ring.adjusted(-self._u(4.0), -self._u(4.0), self._u(4.0), self._u(4.0))), ring, ref)
                    elif index == 0 and entry_status in terminal_statuses:
                        self.plugin_action_rects["timer-clear"] = QRectF(ring.adjusted(-self._u(5.0), -self._u(5.0), self._u(5.0), self._u(5.0)))
                    elif index == 0:
                        self.plugin_action_rects["timer-pause"] = QRectF(ring.adjusted(-self._u(5.0), -self._u(5.0), self._u(5.0), self._u(5.0)))
                return

            entry = entries[0]
            status = str(entry.get("status") or "running").lower()
            paused = status == "paused"
            terminal = status in terminal_statuses
            if terminal:
                ring = self._time_countdown_ring_rect(rect)
                self._paint_time_countdown_ring(painter, ring, entry, ref)
                self.plugin_action_rects["timer-clear"] = QRectF(ring.adjusted(-self._u(5.0), -self._u(5.0), self._u(5.0), self._u(5.0)))
                return
            if stretch_controls and not paused:
                ring_size = min(rect.height() - self._u(24.0), rect.width() * 0.36)
                ring_size = max(self._u(48.0), ring_size)
                ring = QRectF(rect.left() + self._u(18.0), rect.center().y() - ring_size / 2.0 + self._u(5.0), ring_size, ring_size)
                self._paint_time_countdown_ring(painter, ring, entry, ref)
                ring_hit = QRectF(ring.adjusted(-self._u(5.0), -self._u(5.0), self._u(5.0), self._u(5.0)))
                button_w = min(self._u(82.0), max(self._u(56.0), rect.right() - ring.right() - self._u(24.0)))
                button_h = self._u(28.0)
                button_x = rect.right() - button_w - self._u(14.0)
                pause = QRectF(button_x, rect.center().y() - button_h - self._u(3.0), button_w, button_h)
                clear = QRectF(button_x, rect.center().y() + self._u(5.0), button_w, button_h)
                red = QColor(223, 0, 0, 245)
                self._paint_time_countdown_text_button(painter, pause, "暂停", QColor(0, 0, 0, 0), ref.get("control_stroke", ref["accent"]), ref["text"])
                self._paint_time_countdown_text_button(painter, clear, "取消", red, ref.get("control_stroke", ref["accent"]), ref["text"])
                self.plugin_action_rects["timer-pause"] = ring_hit.united(pause.adjusted(-self._u(2.0), -self._u(2.0), self._u(2.0), self._u(2.0)))
                self.plugin_action_rects["timer-clear"] = QRectF(clear.adjusted(-self._u(2.0), -self._u(2.0), self._u(2.0), self._u(2.0)))
                return

            ring = self._time_countdown_ring_rect(rect)
            self._paint_time_countdown_ring(painter, ring, entry, ref)
            if paused:
                self._paint_time_countdown_pause_overlay(painter, rect, ring, ref)
            else:
                self.plugin_action_rects["timer-pause"] = QRectF(ring.adjusted(-self._u(5.0), -self._u(5.0), self._u(5.0), self._u(5.0)))
            return

        presets = model["countdown_presets"]
        grid_top = title_rect.bottom() + self._u(3.0)
        grid_h = max(self._u(46.0), rect.bottom() - grid_top - self._u(10.0))
        layout_mode = str(model.get("time_layout_mode") or "").lower()
        if layout_mode == "stretch":
            side_pad = self._u(15.0)
            gap = self._u(10.0)
            available_w = max(self._u(120.0), rect.width() - side_pad * 2.0)
            ring_size = min(grid_h - self._u(4.0), (available_w - gap * 3.0) / 4.0)
            ring_size = max(self._u(60.0), ring_size)
            total_w = ring_size * 4.0 + gap * 3.0
            start_x = rect.left() + (rect.width() - total_w) / 2.0
            y = grid_top + (grid_h - ring_size) / 2.0
            for index, item in enumerate(presets[:4]):
                ring = QRectF(start_x + index * (ring_size + gap), y, ring_size, ring_size)
                command = f"timer-start-{int(item['minutes'])}"
                self.plugin_action_rects[command] = QRectF(ring.adjusted(-self._u(3.0), -self._u(3.0), self._u(3.0), self._u(3.0)))
                ring_line = ring.adjusted(self._u(5.0), self._u(5.0), -self._u(5.0), -self._u(5.0))
                painter.setPen(QPen(ref["accent_track"], self._u(5.8), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(ring_line)
                painter.setPen(QPen(ref["accent"], self._u(7.2), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawArc(ring_line, 90 * 16, -360 * 16)
                self._paint_static_elided_text(
                    painter,
                    ring.adjusted(self._u(5.0), 0.0, -self._u(5.0), 0.0),
                    f"{int(item['minutes'])}分钟",
                    12,
                    ref["accent"],
                    True,
                    Qt.AlignmentFlag.AlignCenter,
                )
            return
        use_pills = True
        gap_x = self._u(10.0 if layout_mode == "double" else 12.0)
        gap_y = self._u(10.0)
        button_h = min(self._u(30.0), max(self._u(22.0), (grid_h - gap_y) / 2.0))
        button_w = min(self._u(108.0), max(button_h * 2.15, (rect.width() - self._u(30.0) - gap_x) / 2.0))
        grid_w = button_w * 2.0 + gap_x
        grid_h_used = button_h * 2.0 + gap_y
        grid_x = rect.left() + (rect.width() - grid_w) / 2.0
        grid_y = grid_top + (grid_h - grid_h_used) / 2.0
        for index, item in enumerate(presets):
            col = index % 2
            row = index // 2
            button = QRectF(grid_x + col * (button_w + gap_x), grid_y + row * (button_h + gap_y), button_w, button_h)
            command = f"timer-start-{int(item['minutes'])}"
            self.plugin_action_rects[command] = QRectF(button.adjusted(-self._u(3.0), -self._u(3.0), self._u(3.0), self._u(3.0)))
            painter.setPen(QPen(ref.get("control_stroke", ref["accent"]), self._u(1.2)))
            painter.setBrush(ref["module"])
            painter.drawRoundedRect(button, button.height() / 2.0, button.height() / 2.0)
            self._paint_static_elided_text(painter, button.adjusted(self._u(5.0), 0.0, -self._u(5.0), 0.0), f"{int(item['minutes'])}分钟", 10, ref["accent"], True, Qt.AlignmentFlag.AlignCenter)

    def _paint_time_alarm_switch(self, painter: QPainter, rect: QRectF, enabled: bool, color: QColor, theme: dict[str, QColor]):
        ref = self._time_reference_palette(theme, color)
        radius = rect.height() / 2.0
        painter.setPen(QPen(ref["active"] if enabled else ref["muted_dim"], self._u(1.0)))
        painter.setBrush(ref["active"] if enabled else ref["off_track"])
        painter.drawRoundedRect(rect, radius, radius)
        knob = rect.height() - self._u(4.0)
        knob_x = rect.right() - knob - self._u(2.0) if enabled else rect.left() + self._u(2.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ref["thumb"])
        painter.drawEllipse(QRectF(knob_x, rect.top() + self._u(2.0), knob, knob))

    def _paint_time_page_dots(self, painter: QPainter, rect: QRectF, visible: str, theme: dict[str, QColor], color: QColor):
        if rect.isNull() or rect.width() <= 0.0:
            return
        ref = self._time_reference_palette(theme, color)
        dot = self._u(9.0)
        gap = self._u(7.0)
        total_h = dot * 2.0 + gap
        x = rect.center().x() - dot / 2.0
        y = rect.center().y() - total_h / 2.0
        modules = self._time_panel_modules()
        for index, module in enumerate(modules):
            active = module == visible
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ref["text"] if active else ref["muted_dim"])
            painter.drawEllipse(QRectF(x, y + index * (dot + gap), dot, dot))

    def _paint_time_alarm_module(self, painter: QPainter, rect: QRectF, model: dict[str, object], color: QColor, theme: dict[str, QColor]):
        ref = self._time_reference_palette(theme, color)
        self._paint_time_module_shell(painter, rect, theme, active=False)
        alarms = [dict(row) for row in list(model.get("alarms", []))[:8]]
        pending_alarm = dict(model.get("alarm_due_pending") or self._time_alarm_due_pending())
        if pending_alarm:
            confirm_rect = QRectF(rect)
            self.plugin_alarm_list_rect = QRectF()
            self.plugin_alarm_visible_range = (0, 0)
            self.plugin_alarm_list_scroll_index = 0.0
            self.plugin_alarm_list_bottom_spacer_rows = 0.0
            self.plugin_alarm_list_max_scroll = 0.0
            self.plugin_action_rects["alarm-due-confirm"] = QRectF(rect.adjusted(-self._u(4.0), -self._u(4.0), self._u(4.0), self._u(4.0)))
            radius = min(self._u(16.0), rect.height() * 0.26)
            fill = QLinearGradient(confirm_rect.topLeft(), confirm_rect.bottomLeft())
            fill.setColorAt(0.0, QColor(255, 211, 46, 255))
            fill.setColorAt(1.0, ref["accent"])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(fill))
            painter.drawRoundedRect(confirm_rect, radius, radius)
            painter.setPen(QPen(QColor(255, 236, 142, 120), self._u(1.0)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(confirm_rect.adjusted(self._u(0.7), self._u(0.7), -self._u(0.7), -self._u(0.7)), radius, radius)
            text_color = QColor(32, 23, 0, 248)
            label_text = str(pending_alarm.get("label") or "闹钟").strip() or "闹钟"
            time_text = str(pending_alarm.get("time") or "").strip()
            content = confirm_rect.adjusted(self._u(14.0), self._u(10.0), -self._u(14.0), -self._u(10.0))
            painter.setFont(self._font(8, QFont.Weight.DemiBold))
            painter.setPen(QColor(68, 47, 0, 210))
            painter.drawText(QRectF(content.left(), content.top(), content.width(), self._u(15.0)), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "闹钟提醒")
            time_w = self._u(66.0) if time_text else 0.0
            label_rect = QRectF(content.left(), content.center().y() - self._u(17.0), max(self._u(70.0), content.width() - time_w - self._u(8.0)), self._u(34.0))
            self._paint_static_elided_text(painter, label_rect, label_text, 16, text_color, True, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            if time_text:
                time_rect = QRectF(content.right() - time_w, label_rect.top(), time_w, label_rect.height())
                self._paint_static_elided_text(painter, time_rect, time_text, 14, text_color, True, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            hint_rect = QRectF(content.left(), content.bottom() - self._u(16.0), content.width(), self._u(16.0))
            self._paint_static_elided_text(painter, hint_rect, "点击确认", 9, QColor(77, 54, 0, 215), True, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            return
        painter.setFont(self._font(9, QFont.Weight.Bold))
        painter.setPen(ref["accent"])
        painter.drawText(QRectF(rect.left() + self._u(10.0), rect.top() + self._u(8.0), rect.width() - self._u(20.0), self._u(16.0)), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "闹钟")
        row_top = rect.top() + self._u(27.0)
        add_h = self._u(27.0)
        add_rect = QRectF(rect.left() + self._u(8.0), rect.bottom() - self._u(5.0) - add_h, rect.width() - self._u(16.0), add_h)
        list_bottom = add_rect.top() - self._u(6.0)
        list_rect = QRectF(rect.left() + self._u(8.0), row_top, rect.width() - self._u(16.0), max(0.0, list_bottom - row_top))
        slot_count = 4
        row_h = max(self._u(18.0), list_rect.height() / float(slot_count))
        visible_count = min(slot_count, len(alarms)) if alarms else 0
        bottom_spacer_rows = max(1.0, add_h / max(1.0, row_h))
        max_start = max(0.0, float(len(alarms) - visible_count) + (bottom_spacer_rows if alarms else 0.0))
        try:
            scroll_index = float(getattr(self, "plugin_alarm_list_scroll_index", 0.0) or 0.0)
        except (TypeError, ValueError):
            scroll_index = 0.0
        scroll_index = max(0.0, min(max_start, scroll_index)) if visible_count > 0 else 0.0
        start = int(math.floor(scroll_index))
        fraction = scroll_index - float(start)
        self.plugin_alarm_list_scroll_index = scroll_index
        self.plugin_alarm_list_bottom_spacer_rows = bottom_spacer_rows if alarms else 0.0
        self.plugin_alarm_list_max_scroll = max_start
        self.plugin_alarm_list_rect = QRectF(list_rect)
        self.plugin_alarm_visible_range = (start, visible_count)
        self.plugin_action_rects["alarm-add-open"] = QRectF(add_rect.adjusted(-self._u(2.0), -self._u(2.0), self._u(2.0), self._u(2.0)))
        painter.save()
        painter.setClipRect(list_rect)
        extra_row = 1 if fraction > 0.001 else 0
        end = min(len(alarms), start + visible_count + extra_row)
        for index in range(start, end):
            local_index = index - start
            alarm = alarms[index]
            row = QRectF(list_rect.left(), list_rect.top() + (local_index - fraction) * row_h, list_rect.width(), row_h)
            if row.bottom() < list_rect.top() or row.top() > list_rect.bottom():
                continue
            switch = QRectF(row.right() - self._u(26.0), row.center().y() - self._u(7.0), self._u(25.0), self._u(14.0))
            delete_active = int(getattr(self, "plugin_alarm_delete_index", -1) if getattr(self, "plugin_alarm_delete_index", None) is not None else -1) == index
            delete_rect = QRectF(row.right() - self._u(57.0), row.center().y() - self._u(8.5), self._u(54.0), self._u(17.0))
            control_rect = delete_rect if delete_active else switch
            edit_hit = QRectF(row.left(), row.top(), max(self._u(32.0), control_rect.left() - row.left() - self._u(5.0)), row.height())
            self.plugin_action_rects[f"alarm-edit-{index}"] = QRectF(edit_hit)
            if delete_active:
                self.plugin_action_rects[f"alarm-delete-{index}"] = QRectF(delete_rect.adjusted(-self._u(3.0), -self._u(4.0), self._u(3.0), self._u(4.0)))
            else:
                self.plugin_action_rects[f"alarm-toggle-{index}"] = QRectF(switch.adjusted(-self._u(4.0), -self._u(5.0), self._u(4.0), self._u(5.0)))
            time_rect = QRectF(row.left(), row.top(), self._u(42.0), row.height())
            label_rect = QRectF(time_rect.right() + self._u(3.0), row.top(), max(self._u(20.0), control_rect.left() - time_rect.right() - self._u(8.0)), row.height())
            enabled = bool(alarm.get("enabled"))
            time_text = str(alarm.get("time", "") or "")
            label_text = str(alarm.get("label", "") or "")
            time_color = ref["text"] if enabled else ref["muted_dim"]
            label_color = ref["text"] if enabled else ref["muted_dim"]
            self._paint_static_elided_text(painter, time_rect, time_text, 10, time_color, False, Qt.AlignmentFlag.AlignLeft)
            if label_text:
                if self._time_alarm_note_should_scroll(label_text, label_rect, 10, True, 5):
                    self._paint_scrolling_text(painter, label_rect, self._time_alarm_note_display_text(label_text), 10, label_color, True)
                else:
                    self._paint_static_elided_text(painter, label_rect, label_text, 10, label_color, True, Qt.AlignmentFlag.AlignLeft)
            if delete_active:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(210, 35, 39, 245))
                painter.drawRoundedRect(delete_rect, delete_rect.height() / 2.0, delete_rect.height() / 2.0)
                self._paint_static_elided_text(painter, delete_rect.adjusted(self._u(5.0), 0.0, -self._u(5.0), 0.0), "删除", 8, QColor(255, 248, 245, 248), True, Qt.AlignmentFlag.AlignCenter)
            else:
                self._paint_time_alarm_switch(painter, switch, enabled, color, theme)
            if index < end - 1 and row.bottom() < list_rect.bottom():
                painter.setPen(QPen(ref["divider"], self._u(0.8)))
                painter.drawLine(QPointF(row.left(), row.bottom()), QPointF(row.right(), row.bottom()))
        painter.restore()
        self.plugin_action_rects["alarm-list"] = QRectF(list_rect)
        if visible_count > 0 and len(alarms) > visible_count:
            track = QRectF(rect.right() - self._u(5.0), list_rect.top() + self._u(2.0), self._u(2.0), list_rect.height() - self._u(4.0))
            total_rows = max(1.0, len(alarms) + bottom_spacer_rows)
            thumb_h = max(self._u(12.0), track.height() * visible_count / total_rows)
            travel = max(0.0, track.height() - thumb_h)
            thumb_y = track.top() + travel * (scroll_index / max(1.0, max_start))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ref["muted_dim"])
            painter.drawRoundedRect(QRectF(track.left(), thumb_y, track.width(), thumb_h), track.width() / 2.0, track.width() / 2.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ref["accent"])
        painter.drawRoundedRect(add_rect, add_rect.height() / 2.0, add_rect.height() / 2.0)
        self._paint_static_elided_text(painter, add_rect.adjusted(self._u(8.0), 0.0, -self._u(8.0), 0.0), "添 加 闹 钟", 10, QColor(26, 21, 0, 245), True, Qt.AlignmentFlag.AlignCenter)

    def _paint_alarm_picker_field(self, painter: QPainter, rect: QRectF, field: str, value: str, title: str, theme: dict[str, QColor], color: QColor):
        ref = self._time_reference_palette(theme, color)
        self.plugin_alarm_editor_rects[field] = QRectF(rect)
        painter.setPen(QPen(ref["accent"], self._u(1.2)))
        painter.setBrush(ref["module_active"])
        painter.drawRoundedRect(rect, self._u(11.0), self._u(11.0))
        painter.setFont(self._font(10, QFont.Weight.Bold))
        painter.setPen(ref["accent"])
        painter.drawText(QRectF(rect.left(), rect.top() + self._u(4.0), rect.width(), self._u(15.0)), Qt.AlignmentFlag.AlignCenter, title)
        painter.setPen(QPen(ref["accent_track"], self._u(1.0)))
        painter.drawLine(QPointF(rect.left() + self._u(9.0), rect.center().y()), QPointF(rect.right() - self._u(9.0), rect.center().y()))
        values = self._alarm_picker_display_values(field)
        row_h = max(self._u(14.0), (rect.height() - self._u(19.0)) / 3.0)
        value_clip = rect.adjusted(self._u(3.0), self._u(18.0), -self._u(3.0), -self._u(3.0))
        center_index = next((index for index, item in enumerate(values) if bool(item.get("current"))), len(values) // 2)
        center_y = value_clip.center().y() - row_h / 2.0
        for index, item in enumerate(values):
            current = bool(item.get("current"))
            text = str(item.get("text") or value)
            painter.save()
            painter.setClipRect(value_clip)
            painter.setOpacity(float(item.get("opacity", 1.0)))
            value_size = 20 if current and field in {"hour", "minute"} else (14 if current else 11)
            painter.setFont(self._font(value_size, QFont.Weight.Bold if current else QFont.Weight.DemiBold))
            painter.setPen(ref["text"] if current else ref["muted_dim"])
            offset = float(item.get("offset", 0.0) or 0.0)
            row = QRectF(rect.left() + self._u(4.0), center_y + (index - center_index + offset) * row_h, rect.width() - self._u(8.0), row_h)
            painter.drawText(row, Qt.AlignmentFlag.AlignCenter, text)
            painter.restore()

    def _time_alarm_editor_layout(self, rect: QRectF) -> dict[str, QRectF]:
        if rect.width() < self._u(600.0):
            shell = QRectF(rect)
            title = QRectF(rect.left() + self._u(18.0), rect.top() + self._u(7.0), rect.width() - self._u(36.0), self._u(19.0))
            label_gap = self._u(10.0)
            label_h = self._u(20.0)
            top_floor = self._u(38.0)
            action_h = self._u(32.0)
            action_gap = self._u(8.0)
            bottom_pad = self._u(10.0) + action_h + action_gap
            max_field_h = max(self._u(44.0), rect.height() - top_floor - label_gap - label_h - bottom_pad)
            field_h = min(self._u(82.0), max(self._u(68.0), rect.height() * 0.38), max_field_h)
            field_y = rect.top() + max(top_floor, rect.height() * 0.30)
            label_y = field_y + field_h + label_gap
            label_bottom = label_y + label_h
            max_label_bottom = rect.bottom() - bottom_pad
            if label_bottom > max_label_bottom:
                field_y = max(rect.top() + self._u(32.0), max_label_bottom - label_h - label_gap - field_h)
                label_y = field_y + field_h + label_gap
            gap = self._u(16.0)
            dot_w = self._u(18.0)
            left_pad = self._u(20.0)
            right_pad = self._u(30.0)
            usable_w = max(self._u(210.0), rect.width() - left_pad - right_pad - dot_w)
            time_w = min(self._u(138.0), max(self._u(104.0), usable_w * 0.28))
            value_w = min(self._u(112.0), max(self._u(72.0), usable_w * 0.22))
            freq_w = usable_w - time_w - value_w - gap * 2.0
            if freq_w < self._u(88.0):
                value_w = 0.0
                freq_w = usable_w - time_w - gap
            time_rect = QRectF(rect.left() + left_pad, field_y, time_w, field_h)
            frequency_rect = QRectF(time_rect.right() + gap, field_y, max(self._u(88.0), freq_w), field_h)
            value_rect = QRectF(frequency_rect.right() + gap, field_y, value_w, field_h) if value_w > 0.0 else QRectF()
            if not value_rect.isNull() and value_rect.right() > rect.right() - dot_w - self._u(10.0):
                value_rect = QRectF()
            note_right = rect.right() - dot_w - right_pad
            note_rect = QRectF(rect.left() + left_pad, field_y, max(self._u(170.0), note_right - rect.left() - left_pad), field_h)
            action_y = rect.bottom() - self._u(10.0) - action_h
            action_gap_x = self._u(14.0)
            action_w = min(self._u(156.0), max(self._u(118.0), (rect.width() - self._u(44.0) - action_gap_x) / 2.0))
            actions_total = action_w * 2.0 + action_gap_x
            action_x = rect.center().x() - actions_total / 2.0
            cancel = QRectF(action_x, action_y, action_w, action_h)
            save = QRectF(cancel.right() + action_gap_x, action_y, action_w, action_h)
            main_dot = QRectF(rect.right() - self._u(26.0), rect.center().y() - self._u(13.0), self._u(18.0), self._u(26.0))
            note_dot = QRectF(main_dot)
            return {
                "mode": "narrow",
                "shell": shell,
                "title": title,
                "main_page": QRectF(rect),
                "note_page": QRectF(rect),
                "time": time_rect,
                "main_time": time_rect,
                "main_frequency": frequency_rect,
                "main_frequency_value": value_rect,
                "frequency": frequency_rect,
                "frequency_value": value_rect,
                "note": note_rect,
                "time_label": QRectF(time_rect.left(), label_y, time_rect.width(), label_h),
                "frequency_label": QRectF(frequency_rect.left(), label_y, max(frequency_rect.width(), value_rect.right() - frequency_rect.left() if not value_rect.isNull() else frequency_rect.width()), label_h),
                "note_label": QRectF(note_rect.left(), label_y, note_rect.width(), label_h),
                "main_note_dot": main_dot,
                "note_main_dot": note_dot,
                "save": save,
                "cancel": cancel,
            }
        shell = QRectF(rect)
        title = QRectF(rect.left() + self._u(16.0), rect.top() + self._u(7.0), rect.width() - self._u(32.0), self._u(19.0))
        field_h = min(self._u(43.0), max(self._u(34.0), rect.height() * 0.31))
        label_h = self._u(15.0)
        row_gap = self._u(16.0)
        action_h = self._u(32.0)
        action_gap = self._u(8.0)
        action_y = rect.bottom() - self._u(10.0) - action_h
        field_y = title.bottom() + self._u(12.0)
        max_label_bottom = action_y - action_gap
        field_y = min(field_y, max(rect.top() + self._u(28.0), max_label_bottom - label_h - self._u(6.0) - field_h))
        label_y = field_y + field_h + self._u(6.0)
        time_w = self._u(112.0)
        note_w = self._u(106.0)
        frequency_w = self._u(58.0)
        weekdays_w = self._u(78.0)
        total_w = time_w + note_w + frequency_w + weekdays_w + row_gap * 3.0
        if total_w > rect.width() - self._u(24.0):
            scale = max(0.72, (rect.width() - self._u(24.0) - row_gap * 3.0) / max(1.0, total_w - row_gap * 3.0))
            time_w *= scale
            note_w *= scale
            frequency_w *= scale
            weekdays_w *= scale
            total_w = time_w + note_w + frequency_w + weekdays_w + row_gap * 3.0
        x = rect.center().x() - total_w / 2.0
        time_rect = QRectF(x, field_y, time_w, field_h)
        note_rect = QRectF(time_rect.right() + row_gap, field_y, note_w, field_h)
        frequency_rect = QRectF(note_rect.right() + row_gap, field_y, frequency_w, field_h)
        weekdays_rect = QRectF(frequency_rect.right() + row_gap, field_y, weekdays_w, field_h)
        action_gap_x = self._u(16.0)
        action_w = min(self._u(150.0), max(self._u(118.0), (rect.width() - self._u(64.0) - action_gap_x) / 2.0))
        actions_total = action_w * 2.0 + action_gap_x
        action_x = rect.center().x() - actions_total / 2.0
        cancel = QRectF(action_x, action_y, action_w, action_h)
        save = QRectF(cancel.right() + action_gap_x, action_y, action_w, action_h)
        return {
            "shell": shell,
            "title": title,
            "time": time_rect,
            "note": note_rect,
            "frequency": frequency_rect,
            "weekdays": weekdays_rect,
            "time_label": QRectF(time_rect.left(), label_y, time_rect.width(), label_h),
            "note_label": QRectF(note_rect.left(), label_y, note_rect.width(), label_h),
            "frequency_label": QRectF(frequency_rect.left(), label_y, frequency_rect.width(), label_h),
            "weekdays_label": QRectF(weekdays_rect.left(), label_y, weekdays_rect.width(), label_h),
            "save": save,
            "cancel": cancel,
        }

    def _paint_alarm_editor_caret(self, painter: QPainter, rect: QRectF, field: str, ref: dict[str, QColor]):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(ref["text"], self._u(1.6), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        x = rect.center().x() + rect.width() * 0.22
        top = rect.top() + rect.height() * 0.26
        bottom = rect.bottom() - rect.height() * 0.26
        painter.drawLine(QPointF(x, top), QPointF(x, bottom))
        painter.restore()

    def _paint_time_alarm_editor_box(self, painter: QPainter, rect: QRectF, text: str, placeholder: bool, ref: dict[str, QColor], size: int = 14, bold: bool = True, focused: bool = False, field: str = ""):
        painter.setPen(QPen(ref["accent"], self._u(1.9 if focused else 1.5)))
        painter.setBrush(ref["module_active"])
        painter.drawRoundedRect(rect, self._u(7.0), self._u(7.0))
        color = ref["muted_dim"] if placeholder else ref["text"]
        text_rect = rect.adjusted(self._u(7.0), 0.0, -self._u(7.0), 0.0)
        if field == "note" and not placeholder and self._time_alarm_note_should_scroll(text, text_rect, size, bold, 5):
            self._paint_scrolling_text(painter, text_rect, self._time_alarm_note_display_text(text), size, color, bold, centered=True)
        else:
            self._paint_static_elided_text(painter, text_rect, text, size, color, bold, Qt.AlignmentFlag.AlignCenter)
        if focused and field:
            self._paint_alarm_editor_caret(painter, rect, field, ref)

    def _paint_alarm_detail_input_box(self, painter: QPainter, rect: QRectF, field: str, text: str, ref: dict[str, QColor], focused: bool):
        self.plugin_alarm_editor_rects[field] = QRectF(rect)
        self.plugin_action_rects[f"alarm-editor-focus-{field}"] = QRectF(rect)
        painter.setPen(QPen(ref["accent"], self._u(2.0)))
        painter.setBrush(QColor(28, 28, 27, 238))
        painter.drawRoundedRect(rect, self._u(10.0), self._u(10.0))
        values = self._alarm_picker_display_values(field)
        if len(values) <= 1:
            painter.setFont(self._font(30, QFont.Weight.Bold))
            painter.setPen(ref["accent"])
            painter.drawText(rect.adjusted(self._u(8.0), 0.0, -self._u(8.0), 0.0), Qt.AlignmentFlag.AlignCenter, text)
        else:
            clip = rect.adjusted(self._u(4.0), self._u(4.0), -self._u(4.0), -self._u(4.0))
            row_h = max(self._u(23.0), rect.height() * 0.44)
            center_index = next((index for index, item in enumerate(values) if bool(item.get("current"))), len(values) // 2)
            center_y = clip.center().y() - row_h / 2.0
            for index, item in enumerate(values):
                current = bool(item.get("current"))
                painter.save()
                painter.setClipRect(clip)
                painter.setOpacity(float(item.get("opacity", 1.0)))
                painter.setFont(self._font(30 if current else 18, QFont.Weight.Bold if current else QFont.Weight.DemiBold))
                painter.setPen(ref["accent"] if current else ref["muted_dim"])
                offset = float(item.get("offset", 0.0) or 0.0)
                row = QRectF(rect.left() + self._u(8.0), center_y + (index - center_index + offset) * row_h, rect.width() - self._u(16.0), row_h)
                painter.drawText(row, Qt.AlignmentFlag.AlignCenter, str(item.get("text") or text))
                painter.restore()
        if focused:
            self._paint_alarm_editor_caret(painter, rect, field, ref)

    def _paint_time_alarm_editor_action(self, painter: QPainter, rect: QRectF, text: str, ref: dict[str, QColor], primary: bool):
        fill = QColor(255, 184, 0, 255) if primary else QColor(24, 24, 23, 245)
        painter.setPen(QPen(ref["accent"], self._u(1.1)))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
        painter.setFont(self._font(12 if rect.height() >= self._u(30.0) else 10, QFont.Weight.Bold))
        painter.setPen(QColor(35, 24, 0, 245) if primary else ref["accent"])
        painter.drawText(rect.adjusted(self._u(5.0), 0.0, -self._u(5.0), 0.0), Qt.AlignmentFlag.AlignCenter, text)

    def _paint_time_alarm_editor_label(self, painter: QPainter, rect: QRectF, text: str, ref: dict[str, QColor]):
        painter.setFont(self._font(10, QFont.Weight.Bold))
        painter.setPen(ref["accent"])
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_alarm_editor_page_dots(self, painter: QPainter, rect: QRectF, active_page: str, ref: dict[str, QColor]):
        if rect.isNull() or rect.width() <= 0.0:
            return
        dot = self._u(7.0)
        gap = self._u(7.0)
        total_h = dot * 2.0 + gap
        x = rect.center().x() - dot / 2.0
        y = rect.center().y() - total_h / 2.0
        for index, page in enumerate(("main", "note")):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ref["text"] if page == active_page else ref["muted_dim"])
            painter.drawEllipse(QRectF(x, y + index * (dot + gap), dot, dot))

    def _alarm_editor_frequency_value(self, editor: dict[str, object]) -> tuple[str, str, str]:
        unit = str(editor.get("frequency_unit") or self._alarm_unit_from_frequency(str(editor.get("frequency") or "每周"))).lower()
        if unit == "weekly":
            return "weekdays", self._alarm_recurrence_value_label(editor) or "1", "alarm-editor-open-weekdays"
        if unit == "monthly":
            return "month_day", self._alarm_recurrence_value_label(editor), "alarm-editor-open-month-day"
        if unit == "yearly":
            return "year_date", self._alarm_recurrence_value_label(editor), "alarm-editor-open-year-date"
        return "", "", ""

    def _alarm_editor_narrow_frequency_rect(self, layout: dict[str, QRectF], value_field: str) -> QRectF:
        frequency = QRectF(layout["main_frequency"])
        if value_field:
            return frequency
        slot_left = layout["main_time"].right() + self._u(16.0)
        slot_right = layout["main_note_dot"].left() - self._u(8.0)
        slot_w = max(frequency.width(), slot_right - slot_left)
        target_w = min(slot_w, max(frequency.width() * 1.24, self._u(132.0)))
        rect = QRectF(slot_left + (slot_w - target_w) / 2.0, frequency.top(), target_w, frequency.height())
        unit = str(self._time_alarm_editor_state().get("frequency_unit") or "")
        offset = self._alarm_editor_reflow_offset(unit)
        if abs(offset) > 0.001:
            rect.translate(offset * self._u(18.0), 0.0)
        return rect

    def _taskbar_left_handle_frame_tokens(self) -> dict[str, QColor]:
        tokens = self._taskbar_material_frame_tokens()
        base = QColor(getattr(self, "taskbar_material_color", QColor()))
        light = bool(getattr(self, "taskbar_material_is_light", False))
        if base.isValid():
            light = light or self._taskbar_material_is_light(base)
        bias_top = -10 if light else 8
        bias_mid = -18 if light else 2
        bias_bottom = -28 if light else -10

        def shifted(color: QColor, bias: int, alpha: int) -> QColor:
            return QColor(
                max(0, min(255, color.red() + bias)),
                max(0, min(255, color.green() + bias)),
                max(0, min(255, color.blue() + bias)),
                alpha,
            )

        return {
            "fill_top": shifted(tokens["fill_top"], bias_top, 226),
            "fill_mid": shifted(tokens["fill_mid"], bias_mid, 232),
            "fill_bottom": shifted(tokens["fill_bottom"], bias_bottom, 238),
            "stroke": QColor(tokens["stroke"].red(), tokens["stroke"].green(), tokens["stroke"].blue(), 42 if light else 34),
        }

    def _paint_taskbar_left_handle(self, painter: QPainter, main_rect: QRectF):
        handle = self._taskbar_left_handle_rect(main_rect)
        self.taskbar_left_handle_rect = QRectF(handle)
        progress = self._taskbar_left_handle_slide_progress()
        if handle.isNull() or progress <= 0.01:
            return
        frame = self._taskbar_left_handle_frame_rect(main_rect)
        rect = QRectF(main_rect)
        slide_target_x = rect.left() - frame.width() * 0.18 if not frame.isNull() else handle.right()
        slide_delta = max(0.0, slide_target_x - (frame.left() if not frame.isNull() else handle.left()))
        if slide_delta > 0.0 and progress < 1.0:
            shift = slide_delta * (1.0 - progress)
            handle.translate(shift, 0.0)
            frame.translate(shift, 0.0)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setOpacity(max(0.28, min(1.0, progress * 1.35)))
        painter.setPen(Qt.PenStyle.NoPen)
        if not frame.isNull():
            tokens = self._taskbar_left_handle_frame_tokens()
            material = QLinearGradient(frame.topLeft(), frame.bottomLeft())
            material.setColorAt(0.0, tokens["fill_top"])
            material.setColorAt(0.44, tokens["fill_mid"])
            material.setColorAt(1.0, tokens["fill_bottom"])
            radius = min(frame.width() / 2.0, self._taskbar_corner_radius(frame.height()))
            painter.setBrush(material)
            painter.drawRoundedRect(frame, radius, radius)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(tokens["stroke"], self._u(1.0)))
            painter.drawRoundedRect(frame.adjusted(self._u(0.5), self._u(0.5), -self._u(0.5), -self._u(0.5)), max(0.0, radius - self._u(0.5)), max(0.0, radius - self._u(0.5)))
            painter.setPen(Qt.PenStyle.NoPen)
        color = self._taskbar_left_handle_color()
        painter.setBrush(color)
        painter.drawRoundedRect(handle, handle.width() / 2.0, handle.width() / 2.0)
        painter.restore()

    def _taskbar_left_handle_color(self) -> QColor:
        color = QColor(str(getattr(self, "taskbar_left_handle_color", "#8E8E93") or "#8E8E93"))
        if not color.isValid():
            color = QColor("#8E8E93")
        color.setAlpha(max(72, color.alpha()))
        return color

    def _paint_time_alarm_editor_detail(self, painter: QPainter, rect: QRectF, editor: dict[str, object], color: QColor, theme: dict[str, QColor], ref: dict[str, QColor]):
        mode = str(editor.get("detail_mode") or "").strip().lower()
        confirm = QRectF(rect.right() - self._u(40.0), rect.top() + self._u(10.0), self._u(30.0), self._u(24.0))
        self.plugin_action_rects["alarm-editor-detail-confirm"] = QRectF(confirm.adjusted(-self._u(4.0), -self._u(4.0), self._u(4.0), self._u(4.0)))
        painter.setPen(QPen(ref["accent"], self._u(1.2)))
        painter.setBrush(QColor(ref["accent"].red(), ref["accent"].green(), ref["accent"].blue(), 34))
        painter.drawRoundedRect(confirm, self._u(8.0), self._u(8.0))
        painter.setPen(QPen(ref["accent"], self._u(2.0), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(QPointF(confirm.left() + confirm.width() * 0.28, confirm.center().y()), QPointF(confirm.left() + confirm.width() * 0.44, confirm.bottom() - confirm.height() * 0.30))
        painter.drawLine(QPointF(confirm.left() + confirm.width() * 0.44, confirm.bottom() - confirm.height() * 0.30), QPointF(confirm.right() - confirm.width() * 0.24, confirm.top() + confirm.height() * 0.30))
        title_rect = QRectF(rect.left() + self._u(40.0), rect.top() + self._u(24.0), rect.width() - self._u(100.0), self._u(46.0))
        content = QRectF(rect.left() + self._u(40.0), title_rect.bottom() + self._u(18.0), rect.width() - self._u(80.0), max(self._u(54.0), rect.bottom() - title_rect.bottom() - self._u(32.0)))
        painter.setFont(self._font(30, QFont.Weight.Bold))
        painter.setPen(ref["accent"])
        title = {"weekly": "每周", "monthly": self._alarm_recurrence_label(editor), "yearly": self._alarm_recurrence_label(editor)}.get(mode, "频次")
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        if mode == "weekly":
            weekdays = set(self._alarm_weekdays_from_value(editor.get("weekdays", [])))
            gap = self._u(12.0)
            chip_w = min(self._u(48.0), max(self._u(34.0), (content.width() - gap * 6.0) / 7.0))
            x = content.left()
            y = content.top() + self._u(2.0)
            for day in range(1, 8):
                chip = QRectF(x + (day - 1) * (chip_w + gap), y, chip_w, self._u(58.0))
                selected = day in weekdays
                painter.setFont(self._font(34, QFont.Weight.Bold))
                painter.setPen(ref["accent"] if selected else QColor(118, 118, 118, 235))
                painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, str(day))
                self.plugin_action_rects[f"alarm-editor-toggle-weekday-{day}"] = QRectF(chip.adjusted(-self._u(2.0), -self._u(2.0), self._u(2.0), self._u(2.0)))
            return
        if mode == "monthly":
            field_w = min(self._u(142.0), max(self._u(104.0), content.width() * 0.34))
            field_h = self._u(74.0)
            field = QRectF(content.center().x() - field_w / 2.0 - self._u(18.0), content.top(), field_w, field_h)
            self._paint_alarm_detail_input_box(painter, field, "month_day", str(editor.get("month_day", 28)), ref, str(getattr(self, "plugin_alarm_editor_focus", "") or "") == "month_day")
            painter.setFont(self._font(30, QFont.Weight.Bold))
            painter.setPen(ref["text"])
            painter.drawText(QRectF(field.right() + self._u(18.0), field.top(), self._u(58.0), field.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "号")
            return
        if mode == "yearly":
            gap = self._u(26.0)
            field_w = min(self._u(96.0), max(self._u(80.0), (content.width() - gap - self._u(92.0)) / 2.0))
            field_h = self._u(74.0)
            total_w = field_w * 2.0 + gap + self._u(84.0)
            x = content.center().x() - total_w / 2.0
            y = content.top()
            month = QRectF(x, y, field_w, field_h)
            day = QRectF(month.right() + gap + self._u(42.0), y, field_w, field_h)
            focus = str(getattr(self, "plugin_alarm_editor_focus", "") or "")
            self._paint_alarm_detail_input_box(painter, month, "year_month", str(editor.get("year_month", 4)), ref, focus == "year_month")
            self._paint_alarm_detail_input_box(painter, day, "year_day", str(editor.get("year_day", 28)), ref, focus == "year_day")
            painter.setFont(self._font(30, QFont.Weight.Bold))
            painter.setPen(ref["text"])
            painter.drawText(QRectF(month.right() + self._u(12.0), month.top(), self._u(48.0), month.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "月")
            painter.drawText(QRectF(day.right() + self._u(12.0), day.top(), self._u(48.0), day.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "日")

    def _paint_time_alarm_editor_narrow(self, painter: QPainter, layout: dict[str, QRectF], editor: dict[str, object], color: QColor, theme: dict[str, QColor], ref: dict[str, QColor]):
        self._normalize_alarm_editor_recurrence(editor)
        focus = str(getattr(self, "plugin_alarm_editor_focus", "") or "")
        if str(editor.get("detail_mode") or "").strip():
            self._paint_time_alarm_editor_detail(painter, layout["shell"], editor, color, theme, ref)
            return

        page = str(editor.get("editor_page") or "main")
        self._paint_alarm_editor_page_dots(painter, layout["main_note_dot"], "note" if page == "note" else "main", ref)
        page_offset = self._alarm_editor_page_transition_offset("note" if page == "note" else "main")
        page_translated = abs(page_offset) > 0.001
        if page_translated:
            painter.save()
            painter.translate(0.0, page_offset * layout["shell"].height() * 0.18)
        if page == "note":
            note_text = str(editor.get("note") or "")
            self._paint_time_alarm_editor_box(painter, layout["note"], note_text or "备注...", not bool(note_text), ref, size=15, bold=True, focused=focus == "note", field="note")
            hint = str(getattr(self, "plugin_alarm_editor_note_hint", "") or "")
            if hint and time.monotonic() < float(getattr(self, "plugin_alarm_editor_note_hint_until", 0.0) or 0.0):
                self._paint_time_alarm_editor_label(painter, layout["note_label"], hint, ref)
            else:
                self._paint_time_alarm_editor_label(painter, layout["note_label"], "备注（最多10字）", ref)
            self.plugin_action_rects["alarm-editor-note"] = QRectF(layout["note"])
            self.plugin_action_rects["alarm-editor-focus-note"] = QRectF(layout["note"])
            self.plugin_action_rects["alarm-editor-page-main"] = QRectF(layout["note_main_dot"])
            if page_translated:
                painter.restore()
            return

        time_rect = layout["main_time"]
        hour_rect = QRectF(time_rect.left(), time_rect.top(), time_rect.width() * 0.46, time_rect.height())
        minute_rect = QRectF(time_rect.left() + time_rect.width() * 0.54, time_rect.top(), time_rect.width() * 0.46, time_rect.height())
        self.plugin_alarm_editor_rects["hour"] = hour_rect
        self.plugin_alarm_editor_rects["minute"] = minute_rect
        self.plugin_action_rects["alarm-editor-focus-hour"] = QRectF(hour_rect)
        self.plugin_action_rects["alarm-editor-focus-minute"] = QRectF(minute_rect)
        time_text = f"{int(editor.get('hour', 0)):02d}:{int(editor.get('minute', 0)):02d}"
        self._paint_time_alarm_editor_box(painter, time_rect, time_text, False, ref, size=24, bold=True, focused=focus in {"hour", "minute"})
        unit_label = dict(self._alarm_frequency_units()).get(str(editor.get("frequency_unit") or "weekly"), self._alarm_recurrence_label(editor))
        value_field, value_text, open_command = self._alarm_editor_frequency_value(editor)
        frequency_rect = self._alarm_editor_narrow_frequency_rect(layout, value_field)
        self._paint_alarm_picker_field(painter, frequency_rect, "frequency_unit", unit_label, "频次", theme, color)
        self.plugin_action_rects["alarm-editor-focus-frequency_unit"] = QRectF(frequency_rect)
        if value_field:
            open_rect = QRectF(layout["main_frequency_value"]) if not layout["main_frequency_value"].isNull() else QRectF(
                frequency_rect.center().x(),
                frequency_rect.top(),
                frequency_rect.width() * 0.5,
                frequency_rect.height(),
            )
            self.plugin_action_rects[open_command] = QRectF(open_rect)
        if value_field and not layout["main_frequency_value"].isNull():
            value_rect = layout["main_frequency_value"]
            self._paint_time_alarm_editor_box(painter, value_rect, value_text, False, ref, size=13, bold=True, focused=focus in {value_field, "weekdays"})
            if value_field == "year_date":
                self.plugin_alarm_editor_rects["year_month"] = QRectF(value_rect.left(), value_rect.top(), value_rect.width() * 0.48, value_rect.height())
                self.plugin_alarm_editor_rects["year_day"] = QRectF(value_rect.left() + value_rect.width() * 0.52, value_rect.top(), value_rect.width() * 0.48, value_rect.height())
            else:
                self.plugin_alarm_editor_rects[value_field] = QRectF(value_rect)
        self._paint_time_alarm_editor_label(painter, layout["time_label"], "时间", ref)
        frequency_label = QRectF(frequency_rect.left(), layout["frequency_label"].top(), frequency_rect.width(), layout["frequency_label"].height())
        if value_field and not layout["main_frequency_value"].isNull():
            frequency_label = QRectF(frequency_rect.left(), layout["frequency_label"].top(), layout["main_frequency_value"].right() - frequency_rect.left(), layout["frequency_label"].height())
        self._paint_time_alarm_editor_label(painter, frequency_label, "频次", ref)
        self.plugin_action_rects["alarm-editor-page-note"] = QRectF(layout["main_note_dot"])
        if page_translated:
            painter.restore()

    def _paint_time_alarm_editor(self, painter: QPainter, rect: QRectF, model: dict[str, object], color: QColor, theme: dict[str, QColor]):
        ref = self._time_reference_palette(theme, color)
        self.plugin_alarm_editor_rects = {}
        editor = model["editor"]
        self._normalize_alarm_editor_recurrence(editor)
        layout = self._time_alarm_editor_layout(rect)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(35, 36, 38, 248))
        painter.drawRoundedRect(layout["shell"], self._u(14.0), self._u(14.0))
        painter.setPen(QPen(ref["divider"], self._u(0.8)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(layout["shell"].adjusted(self._u(0.8), self._u(0.8), -self._u(0.8), -self._u(0.8)), self._u(13.0), self._u(13.0))
        if str(editor.get("detail_mode") or "").strip():
            self._paint_time_alarm_editor_detail(painter, layout["shell"], editor, color, theme, ref)
            return
        painter.setFont(self._font(13, QFont.Weight.Bold))
        painter.setPen(ref["accent"])
        painter.drawText(layout["title"], Qt.AlignmentFlag.AlignCenter, "闹钟设置")

        if layout.get("mode") == "narrow":
            self._paint_time_alarm_editor_narrow(painter, layout, editor, color, theme, ref)
            self.plugin_action_rects["alarm-editor-save"] = QRectF(layout["save"])
            self.plugin_action_rects["alarm-editor-cancel"] = QRectF(layout["cancel"])
            self._paint_time_alarm_editor_action(painter, layout["cancel"], "取消", ref, False)
            self._paint_time_alarm_editor_action(painter, layout["save"], "确定", ref, True)
            return

        time_rect = layout["time"]
        hour_rect = QRectF(time_rect.left(), time_rect.top(), time_rect.width() * 0.46, time_rect.height())
        minute_rect = QRectF(time_rect.left() + time_rect.width() * 0.54, time_rect.top(), time_rect.width() * 0.46, time_rect.height())
        self.plugin_alarm_editor_rects["hour"] = hour_rect
        self.plugin_alarm_editor_rects["minute"] = minute_rect

        time_text = f"{int(editor['hour']):02d}:{int(editor['minute']):02d}"
        focus = str(getattr(self, "plugin_alarm_editor_focus", "") or "")
        self._paint_time_alarm_editor_box(painter, time_rect, time_text, False, ref, size=24, bold=True, focused=focus in {"hour", "minute"})
        note_text = str(editor.get("note") or "")
        self._paint_time_alarm_editor_box(painter, layout["note"], note_text or "备注...", not bool(note_text), ref, size=12, bold=True, focused=focus == "note", field="note")
        unit_label = dict(self._alarm_frequency_units()).get(
            str(editor.get("frequency_unit") or self._alarm_unit_from_frequency(str(editor.get("frequency") or "每周"))),
            self._alarm_recurrence_label(editor),
        )
        value_field, value_text, open_command = self._alarm_editor_frequency_value(editor)
        frequency_rect = QRectF(layout["frequency"])
        value_rect = QRectF(layout["weekdays"])
        self._paint_alarm_picker_field(painter, frequency_rect, "frequency_unit", unit_label, "频次", theme, color)
        self.plugin_alarm_editor_rects["frequency"] = QRectF(frequency_rect)
        if value_field:
            self._paint_time_alarm_editor_box(painter, value_rect, value_text, False, ref, size=12, bold=True, focused=focus in {value_field, "weekdays", "year_month", "year_day"})
            if value_field == "year_date":
                self.plugin_alarm_editor_rects["year_month"] = QRectF(value_rect.left(), value_rect.top(), value_rect.width() * 0.48, value_rect.height())
                self.plugin_alarm_editor_rects["year_day"] = QRectF(value_rect.left() + value_rect.width() * 0.52, value_rect.top(), value_rect.width() * 0.48, value_rect.height())
                self.plugin_action_rects["alarm-editor-focus-year_month"] = QRectF(self.plugin_alarm_editor_rects["year_month"])
                self.plugin_action_rects["alarm-editor-focus-year_day"] = QRectF(self.plugin_alarm_editor_rects["year_day"])
            else:
                self.plugin_alarm_editor_rects[value_field] = QRectF(value_rect)
                self.plugin_action_rects[f"alarm-editor-focus-{value_field}"] = QRectF(value_rect)
            self.plugin_alarm_editor_rects["weekdays"] = QRectF(value_rect)
        else:
            self._paint_time_alarm_editor_box(painter, value_rect, "默认", False, ref, size=12, bold=True, focused=False)
            self.plugin_alarm_editor_rects["weekdays"] = QRectF(value_rect)

        self._paint_time_alarm_editor_label(painter, layout["time_label"], "时间", ref)
        hint = str(getattr(self, "plugin_alarm_editor_note_hint", "") or "")
        if hint and time.monotonic() < float(getattr(self, "plugin_alarm_editor_note_hint_until", 0.0) or 0.0):
            self._paint_time_alarm_editor_label(painter, layout["note_label"], hint, ref)
        else:
            self._paint_time_alarm_editor_label(painter, layout["note_label"], "备注", ref)
        self._paint_time_alarm_editor_label(painter, layout["frequency_label"], "频次", ref)
        self._paint_time_alarm_editor_label(painter, layout["weekdays_label"], "日期", ref)

        self.plugin_action_rects["alarm-editor-note"] = QRectF(layout["note"])
        self.plugin_action_rects["alarm-editor-focus-hour"] = QRectF(hour_rect)
        self.plugin_action_rects["alarm-editor-focus-minute"] = QRectF(minute_rect)
        self.plugin_action_rects["alarm-editor-focus-frequency_unit"] = QRectF(frequency_rect)
        self.plugin_action_rects["alarm-editor-focus-frequency"] = QRectF(frequency_rect)
        self.plugin_action_rects["alarm-editor-focus-weekdays"] = QRectF(value_rect)
        if open_command:
            self.plugin_action_rects[open_command] = QRectF(value_rect)
        self.plugin_action_rects["alarm-editor-save"] = QRectF(layout["save"])
        self.plugin_action_rects["alarm-editor-cancel"] = QRectF(layout["cancel"])
        self._paint_time_alarm_editor_action(painter, layout["cancel"], "取消", ref, False)
        self._paint_time_alarm_editor_action(painter, layout["save"], "确定", ref, True)

    def _paint_time_module_by_name(self, painter: QPainter, module: str, rect: QRectF, panel: dict[str, object], color: QColor, theme: dict[str, QColor]):
        if module == "alarm":
            self._paint_time_alarm_module(painter, rect, panel, color, theme)
        else:
            self._paint_time_countdown_module(painter, rect, panel, color, theme)

    def _paint_time_plugin_panel(self, painter: QPainter, rect: QRectF, model: dict[str, object], color: QColor, theme: dict[str, QColor]):
        self.plugin_panel_last_width = float(rect.width())
        panel = self._time_plugin_panel_model(model)
        if panel["mode"] == "alarm_editor":
            self._paint_time_alarm_editor(painter, rect, panel, color, theme)
            return
        ref = self._time_reference_palette(theme, color)
        layout = self._time_plugin_panel_layout(rect)
        panel_for_paint = dict(panel)
        panel_for_paint["time_layout_mode"] = layout["time_layout_mode"]
        title = layout["time_title"]
        painter.setFont(self._font(10, QFont.Weight.Bold))
        painter.setPen(ref["accent"])
        painter.drawText(title, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "TIME")
        if layout["time_layout_mode"] == "double":
            visible = str(layout.get("time_visible_module") or "countdown")
            self._paint_time_dashboard_clock(painter, layout["time_clock"], panel, color, theme)
            slot_rect = QRectF(layout["time_alarm"] if visible == "alarm" else layout["time_countdown"])
            transition = self._time_module_transition_offsets(visible)
            if transition and not slot_rect.isNull():
                from_module, from_offset, to_offset = transition
                painter.save()
                painter.setClipRect(slot_rect)
                action_snapshot = dict(self.plugin_action_rects)
                if from_module in self._time_panel_modules():
                    painter.save()
                    painter.translate(0.0, from_offset * slot_rect.height())
                    self._paint_time_module_by_name(painter, from_module, slot_rect, panel_for_paint, color, theme)
                    painter.restore()
                self.plugin_action_rects = action_snapshot
                painter.save()
                painter.translate(0.0, to_offset * slot_rect.height())
                self._paint_time_module_by_name(painter, visible, slot_rect, panel_for_paint, color, theme)
                painter.restore()
                painter.restore()
            else:
                self._paint_time_module_by_name(painter, visible, slot_rect, panel_for_paint, color, theme)
            self._paint_time_page_dots(painter, layout["time_pager"], visible, theme, color)
            return
        self._paint_time_dashboard_clock(painter, layout["time_clock"], panel, color, theme)
        self._paint_time_countdown_module(painter, layout["time_countdown"], panel_for_paint, color, theme)
        self._paint_time_alarm_module(painter, layout["time_alarm"], panel, color, theme)

    def _paint_structured_plugin_panel(self, painter: QPainter, rect: QRectF, model: dict[str, object], color: QColor, theme: dict[str, QColor]):
        label = str(model.get("label", ""))
        if label.strip().upper() == "TIME":
            self._paint_time_plugin_panel(painter, rect, model, color, theme)
            return
        detail = str(model.get("detail", ""))
        summary = str(model.get("summary", ""))
        status = str(model.get("status", "normal"))
        alert_title = str(model.get("alert_title", ""))
        alert_body = str(model.get("alert_body", ""))
        progress = float(model.get("progress", 0.0) or 0.0)
        layout = self._plugin_panel_layout(rect)
        title, _lines = self._plugin_function_lines(label, detail, summary, status, alert_title, alert_body)
        nav = layout["nav"]
        painter.setFont(self._font(10, QFont.Weight.Bold))
        painter.setPen(theme["text"])
        painter.drawText(nav.adjusted(self._u(4.0), 0.0, 0.0, 0.0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        status_text = {"running": "运行中", "done": "完成", "alarm": "提醒", "loading": "刷新中", "soon": "即将", "due": "现在", "rain": "降雨", "severe": "预警"}.get(status.lower(), "就绪")
        painter.setFont(self._font(8, QFont.Weight.DemiBold))
        painter.setPen(theme["muted"])
        painter.drawText(nav.adjusted(0.0, 0.0, -self._u(4.0), 0.0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, status_text)

        sections = self._plugin_panel_sections(model)
        module_rects = (layout["time_clock"], layout["time_countdown"], layout["time_alarm"])
        action = self._plugin_action_model(label, detail, summary, status)
        for index, section in enumerate(sections[:3]):
            module = module_rects[index]
            reserve_action = index == 2
            if str(section["id"]) == "clock":
                self._paint_time_clock_module(painter, module, section, theme, color, progress, status)
            else:
                self._paint_plugin_panel_module(
                    painter,
                    module,
                    section["title"],
                    section["body"],
                    theme,
                    color,
                    emphasized=index == 0 and str(label).upper() != "TIME",
                    reserve_action=reserve_action,
                )
            if reserve_action:
                self._paint_plugin_panel_action_button(painter, module, action, color)
        self._paint_plugin_panel_reminders(painter, layout["reminders"])

    def _paint_plugin_compact_cell(self, painter: QPainter, rect: QRectF, label: str, detail: str, summary: str, color: QColor, active: bool, status: str = "normal", progress: float = 0.0):
        if str(label or "").strip().upper() == "TIME":
            self._paint_time_compact_cell(painter, rect, detail, summary, color, active, status, progress)
            return
        theme = self._plugin_theme_colors()
        color = self._pure_plugin_color(self._plugin_display_color(label, color))
        radius = min(self._u(15), rect.height() / 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme["cell_active"] if active else theme["cell"])
        painter.drawRoundedRect(rect, radius, radius)

        accent_size = self._u(20)
        accent = QRectF(rect.left() + self._u(8), rect.center().y() - accent_size / 2.0, accent_size, accent_size)
        label_key = str(label or "").strip().upper()
        status_key = str(status or "").strip().lower()
        is_time = label_key == "TIME"
        is_time_done = is_time and status_key in {"done", "due", "alarm"}
        accent_fill = QColor(color.red(), color.green(), color.blue(), 238 if active else 218)
        if is_time:
            accent_fill = QColor(255, 184, 0, 238) if is_time_done else QColor(30, 26, 18, 232)
        painter.setBrush(accent_fill)
        painter.drawRoundedRect(accent, self._u(6), self._u(6))
        painted_icon = False
        if is_time:
            painted_icon = self._paint_svg_icon(painter, self._time_reminder_icon_path(status, self._time_reminder_kind(status, summary)), accent.adjusted(self._u(3.0), self._u(3.0), -self._u(3.0), -self._u(3.0)))
        if not painted_icon:
            painter.setPen(QColor(39, 25, 0, 245) if is_time_done else QColor(255, 255, 255, 242))
            painter.setFont(self._font(7, QFont.Weight.Bold))
            painter.drawText(accent, Qt.AlignmentFlag.AlignCenter, label[:1])
        if not is_time_done:
            self._paint_plugin_progress_ring(painter, accent.adjusted(-self._u(3.0), -self._u(3.0), self._u(3.0), self._u(3.0)), color, progress, status)

        painter.setPen(QPen(theme["stroke"], self._u(1.0)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        inset = self._u(0.8)
        painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), max(1.0, radius - inset), max(1.0, radius - inset))
        painter.setPen(QPen(theme["inner_stroke"], self._u(0.8)))
        painter.drawRoundedRect(rect.adjusted(inset * 1.7, inset * 1.7, -inset * 1.7, -inset * 1.7), max(1.0, radius - inset * 1.7), max(1.0, radius - inset * 1.7))
        detail_rect = QRectF(accent.right() + self._u(8), rect.top(), rect.right() - accent.right() - self._u(16), rect.height())
        detail_color = QColor(theme["text"])
        detail_color.setAlpha(232 if active else 188)
        self._paint_scrolling_text(painter, detail_rect, self._plugin_compact_value(label, detail, summary, status), 10, detail_color, False)
        self._paint_plugin_status_dot(painter, rect, status)

    def _paint_plugin_progress_ring(self, painter: QPainter, rect: QRectF, color: QColor, progress: float, status: str = "normal"):
        status = str(status or "").lower()
        if status == "loading":
            progress = (time.monotonic() * 0.82) % 1.0
        elif status == "error":
            progress = 1.0
        if status not in {"running", "done", "due", "soon", "loading", "error", "alarm", "severe", "rain"} and float(progress or 0.0) <= 0.0:
            return
        progress = 1.0 if status in {"done", "due", "alarm", "severe"} else max(0.0, min(1.0, float(progress or 0.0)))
        style = self._plugin_ring_style(status)
        base = QColor(255, 255, 255, 54)
        accent = QColor(color.red(), color.green(), color.blue(), 245)
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(base, style["track_width"], Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawEllipse(rect)
        painter.setPen(QPen(accent, style["width"], Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, 90 * 16, int(-360 * 16 * progress))
        painter.restore()

    def _paint_plugin_panel_reminders(self, painter: QPainter, rect: QRectF):
        reminders = self._plugin_panel_reminder_models(3)
        if not reminders:
            theme = self._plugin_theme_colors()
            idle = QRectF(rect.left(), rect.top(), min(rect.width(), self._u(132.0)), rect.height())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme["cell"])
            painter.drawRoundedRect(idle, self._u(12.0), self._u(12.0))
            muted = QColor(theme["muted"])
            muted.setAlpha(160)
            self._paint_scrolling_text(painter, idle.adjusted(self._u(10.0), 0.0, -self._u(10.0), 0.0), "暂无提醒", 9, muted, False)
            return

        gap = self._u(7.0)
        count = len(reminders)
        chip_w = max(self._u(78.0), min(self._u(132.0), (rect.width() - gap * max(0, count - 1)) / max(1, count)))
        theme = self._plugin_theme_colors()
        for i, item in enumerate(reminders):
            chip = QRectF(rect.left() + i * (chip_w + gap), rect.top(), chip_w, rect.height())
            label = str(item["label"])
            plugin_id = str(item.get("plugin_id") or "")
            color = self._pure_plugin_color(self._plugin_display_color(label, item["color"]))
            status = str(item["status"] or "normal")
            status_key = status.lower()
            is_time = label.strip().upper() == "TIME" or plugin_id.strip().casefold() == "time"
            is_time_done = is_time and status_key in {"done", "due", "alarm"}
            text_color = QColor(39, 25, 0, 245) if is_time_done else theme["text"]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 184, 0, 240) if is_time_done else (theme["cell_active"] if i == 0 else theme["cell"]))
            painter.drawRoundedRect(chip, self._u(12.0), self._u(12.0))
            icon_size = self._u(18.0)
            icon_rect = QRectF(chip.left() + self._u(7.0), chip.center().y() - icon_size / 2.0, icon_size, icon_size)
            painter.setBrush(QColor(30, 26, 18, 226) if is_time and not is_time_done else QColor(color.red(), color.green(), color.blue(), 226 if not is_time_done else 0))
            painter.drawRoundedRect(icon_rect, self._u(6.0), self._u(6.0))
            progress = float(item.get("progress", 0.0) or 0.0)
            if not is_time_done:
                self._paint_plugin_progress_ring(painter, icon_rect.adjusted(-self._u(2.6), -self._u(2.6), self._u(2.6), self._u(2.6)), color, progress, status)
            painted_icon = False
            if is_time:
                kind = self._time_reminder_kind(status, f"{item.get('body')} {item.get('detail')}")
                painted_icon = self._paint_svg_icon(painter, self._time_reminder_icon_path(status, kind), icon_rect.adjusted(self._u(2.8), self._u(2.8), -self._u(2.8), -self._u(2.8)))
            if not painted_icon:
                painter.setFont(QFont("Segoe UI Symbol", self._font_size(8), QFont.Weight.Bold))
                painter.setPen(text_color if is_time_done else QColor(255, 255, 255, 244))
                painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, str(item["icon"] or item["label"])[:1])
            text_rect = QRectF(icon_rect.right() + self._u(6.0), chip.top(), chip.right() - icon_rect.right() - self._u(10.0), chip.height())
            self._paint_scrolling_text(painter, text_rect, str(item["body"] or item["detail"] or item["label"]), 8, text_color, False)

    def _paint_time_compact_cell(self, painter: QPainter, rect: QRectF, detail: str, summary: str, color: QColor, active: bool, status: str = "normal", progress: float = 0.0):
        theme = self._plugin_theme_colors()
        ref = self._time_reference_palette(theme, color)
        radius = min(self._u(16.0), rect.height() / 2.0)
        painter.setPen(QPen(ref["compact_stroke"], self._u(1.1)))
        painter.setBrush(ref["compact_cell_active"] if active else ref["compact_cell"])
        painter.drawRoundedRect(rect, radius, radius)
        segments = self._time_compact_segments(detail, summary, status)
        if not segments:
            return
        layout = self._time_compact_cell_layout(rect, segments)
        if not layout:
            return
        time_text = str(layout[0].get("text") or "")
        time_rect = QRectF(layout[0]["rect"])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ref["compact_time_chip"])
        painter.drawRoundedRect(time_rect, time_rect.height() / 2.0, time_rect.height() / 2.0)
        painter.setFont(self._font(12 if len(time_text) <= 5 else 9, QFont.Weight.Bold))
        painter.setPen(ref["compact_time_text"])
        painter.drawText(time_rect.adjusted(self._u(4.0), 0.0, -self._u(4.0), 0.0), Qt.AlignmentFlag.AlignCenter, time_text)
        for segment in layout[1:]:
            chip = QRectF(segment["rect"])
            seg_status = str(segment.get("status") or status).lower()
            done = seg_status in {"done", "due", "alarm-done"} or str(segment.get("text") or "") == "\u65f6\u95f4\u5230"
            fill = ref["compact_chip_done"] if done else ref["compact_chip"]
            text_color = ref["compact_done_text"] if done else ref["compact_chip_text"]
            painter.setPen(QPen(ref["accent"], self._u(1.0)))
            painter.setBrush(fill)
            painter.drawRoundedRect(chip, chip.height() / 2.0, chip.height() / 2.0)
            icon_size = min(self._u(20.0), chip.height() - self._u(5.0))
            segment_kind = str(segment.get("kind") or "countdown")
            if bool(segment.get("icon_only")):
                icon_size = min(chip.height() - self._u(7.0), self._u(24.0))
                icon_rect = QRectF(chip.center().x() - icon_size / 2.0, chip.center().y() - icon_size / 2.0, icon_size, icon_size)
                self._paint_time_symbol(painter, icon_rect, segment_kind, text_color, self._u(1.8))
                continue
            icon_rect = QRectF(chip.left() + self._u(6.0), chip.center().y() - icon_size / 2.0, icon_size, icon_size)
            self._paint_time_symbol(painter, icon_rect, segment_kind, text_color, self._u(1.6))
            text_rect = QRectF(icon_rect.right() + self._u(4.0), chip.top(), chip.right() - icon_rect.right() - self._u(7.0), chip.height())
            segment_text = str(segment.get("text") or "")
            display_text = self._time_alarm_note_display_text(segment_text) if segment_kind == "alarm" else self._time_reminder_display_text(segment_text, 10)
            if segment_kind == "alarm" and self._time_alarm_note_should_scroll(segment_text, text_rect, 10, True, 5):
                self._paint_scrolling_text(painter, text_rect, display_text, 10, text_color, True)
            else:
                self._paint_static_elided_text(painter, text_rect, display_text, 10, text_color, True)

    def _paint_plugin_function_panel(self, painter: QPainter, rect: QRectF, model: dict[str, object]):
        if not model:
            return
        index = int(model.get("index", 0))
        label = str(model.get("label", ""))
        detail = str(model.get("detail", ""))
        summary = str(model.get("summary", ""))
        color = model.get("color")
        icon = str(model.get("icon", ""))
        status = str(model.get("status", "normal"))
        alert_title = str(model.get("alert_title", ""))
        alert_body = str(model.get("alert_body", ""))
        progress = float(model.get("progress", 0.0) or 0.0)
        theme = self._plugin_theme_colors()
        color = self._pure_plugin_color(self._plugin_display_color(label, color))

        layout = self._plugin_panel_layout(rect)
        if str(label).upper() in {"TIME", "WEATHER", "AGENDA"}:
            self._paint_structured_plugin_panel(painter, rect, model, color, theme)
            return

        body = layout["body"]
        title, lines = self._plugin_function_lines(label, detail, summary, status, alert_title, alert_body)
        action = self._plugin_action_model(label, detail, summary, status)
        accent = layout["icon"]
        pulse = 0.5 + 0.5 * math.sin(time.monotonic() * 3.0)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), int(172 + pulse * 46)))
        painter.drawRoundedRect(accent, self._u(22.0), self._u(22.0))
        self._paint_plugin_progress_ring(painter, accent.adjusted(self._u(5.0), self._u(5.0), -self._u(5.0), -self._u(5.0)), color, progress, status)
        if str(label).upper() == "TIME":
            center = accent.center()
            painter.setPen(QPen(QColor(255, 255, 255, 236), self._u(3.0), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(center, QPointF(center.x() + accent.width() * 0.24, center.y() + accent.height() * 0.16))
            painter.setPen(QPen(QColor(255, 255, 255, 215), self._u(2.0), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(center, QPointF(center.x() - accent.width() * 0.18, center.y() - accent.height() * 0.12))
        else:
            painter.setFont(QFont("Segoe UI Symbol", self._font_size(22), QFont.Weight.Bold))
            painter.setPen(QColor(255, 255, 255, 244))
            painter.drawText(accent, Qt.AlignmentFlag.AlignCenter, icon or str(label)[:1])

        text_rect = layout["text"]
        text_left = text_rect.left()
        title_rect = QRectF(text_left, text_rect.top(), text_rect.width(), self._u(22.0))
        painter.setFont(self._font(12, QFont.Weight.Bold))
        painter.setPen(theme["muted"])
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        if lines:
            self._paint_scrolling_text(painter, QRectF(text_left, title_rect.bottom() + self._u(3.0), text_rect.width(), self._u(30.0)), lines[0], 18, theme["text"], False)
        if len(lines) > 1:
            self._paint_scrolling_text(painter, QRectF(text_left, title_rect.bottom() + self._u(34.0), text_rect.width(), self._u(18.0)), lines[1], 10, theme["muted"], False)
        if len(lines) > 2:
            third_color = QColor(theme["muted"])
            third_color.setAlpha(170)
            self._paint_scrolling_text(painter, QRectF(text_left, title_rect.bottom() + self._u(52.0), text_rect.width(), self._u(16.0)), lines[2], 9, third_color, False)

        action_rect = layout["action"]
        command = str(action.get("command", ""))
        if command:
            self.plugin_action_rects[command] = QRectF(action_rect)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 218))
        painter.drawRoundedRect(action_rect, self._u(9.0), self._u(9.0))
        painter.setFont(self._font(9, QFont.Weight.DemiBold))
        painter.setPen(QColor(255, 255, 255, 244))
        painter.drawText(action_rect.adjusted(self._u(8.0), 0.0, -self._u(8.0), 0.0), Qt.AlignmentFlag.AlignCenter, str(action.get("label") or "操作"))
        self._paint_plugin_panel_reminders(painter, layout["reminders"])

    def _paint_plugin_focus_cell(
        self,
        painter: QPainter,
        rect: QRectF,
        label: str,
        detail: str,
        color: QColor,
        icon: str,
        focus_weight: float,
        icon_mode: bool,
        status: str = "normal",
        summary: str = "",
        progress: float = 0.0,
    ):
        if str(label or "").strip().upper() == "TIME":
            self._paint_time_compact_cell(painter, rect, detail, summary or detail, color, True, status, progress)
            return
        theme = self._plugin_theme_colors()
        color = self._pure_plugin_color(self._plugin_display_color(label, color))
        if icon_mode or rect.width() < self._u(138):
            circle_size = min(self._u(32.0), max(self._u(28.0), rect.height() - self._u(4.0)))
            circle = QRectF(rect.center().x() - circle_size / 2.0, rect.center().y() - circle_size / 2.0, circle_size, circle_size)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme["cell_active"])
            painter.drawRoundedRect(circle, self._u(12), self._u(12))
            inner = circle.adjusted(self._u(4), self._u(4), -self._u(4), -self._u(4))
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), 232))
            painter.drawRoundedRect(inner, self._u(8), self._u(8))
            painter.setFont(QFont("Segoe UI Symbol", self._font_size(13), QFont.Weight.Bold))
            painter.setPen(QColor(255, 255, 255, 242))
            painter.drawText(inner, Qt.AlignmentFlag.AlignCenter, icon)
            self._paint_plugin_status_dot(painter, circle, status)
            return

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._mix_color(theme["cell"], theme["cell_active"], focus_weight))
        radius = min(self._u(16), rect.height() / 2)
        painter.drawRoundedRect(rect, radius, radius)
        stroke = self._u(1.0)
        painter.setPen(QPen(theme["stroke"], stroke))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        inset = self._u(0.8)
        painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), max(1.0, radius - inset), max(1.0, radius - inset))
        painter.setPen(QPen(theme["inner_stroke"], self._u(0.8)))
        painter.drawRoundedRect(rect.adjusted(inset * 1.8, inset * 1.8, -inset * 1.8, -inset * 1.8), max(1.0, radius - inset * 1.8), max(1.0, radius - inset * 1.8))

        icon_size = self._u(26)
        icon_box = QRectF(rect.left() + self._u(11), rect.center().y() - icon_size / 2.0, icon_size, icon_size)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 212))
        painter.drawRoundedRect(icon_box, self._u(9), self._u(9))
        painter.setFont(QFont("Segoe UI Symbol", self._font_size(11), QFont.Weight.Bold))
        painter.setPen(QColor(255, 255, 255, 242))
        painter.drawText(icon_box, Qt.AlignmentFlag.AlignCenter, icon)

        label_rect = QRectF(icon_box.right() + self._u(9), rect.top(), self._u(46), rect.height())
        painter.setFont(self._font(8, QFont.Weight.Bold))
        painter.setPen(theme["text"])
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
        detail_rect = QRectF(label_rect.right() + self._u(5), rect.top(), rect.right() - label_rect.right() - self._u(16), rect.height())
        self._paint_scrolling_text(painter, detail_rect, detail, 10, theme["muted"], False)
        self._paint_plugin_status_dot(painter, rect, status)

    def _paint_time_plugin_alert(
        self,
        painter: QPainter,
        rect: QRectF,
        label: str,
        detail: str,
        summary: str,
        color: QColor,
        status: str,
        alert_title: str,
        alert_body: str,
        progress: float,
        reminder_kind: str = "",
    ):
        ref = self._time_reference_palette(self._plugin_theme_colors(), color)
        status_key = str(status or "").strip().lower()
        done = status_key in {"done", "due", "alarm"}
        kind = str(reminder_kind or "").strip().lower()
        if kind not in {"alarm", "countdown"}:
            kind = self._time_reminder_kind(status, f"{summary} {alert_title} {alert_body}")
        alert_note = self._time_alarm_alert_text(alert_body)
        if status_key in {"done", "due"} and kind == "countdown" and alert_note != "闹钟" and not self._time_text_looks_like_countdown(alert_body):
            kind = "alarm"
        radius = rect.height() / 2.0
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ref["accent"] if done else QColor(21, 21, 20, 244))
        painter.drawRoundedRect(rect, radius, radius)
        self._paint_current_island_space_glow(painter, rect, radius)
        painter.setPen(QPen(QColor(46, 46, 44, 210) if not done else QColor(255, 184, 0, 245), self._u(1.0)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(self._u(0.8), self._u(0.8), -self._u(0.8), -self._u(0.8)), radius - self._u(0.8), radius - self._u(0.8))
        text_color = QColor(35, 24, 0, 245) if done else ref["accent"]
        icon_size = min(rect.height() - self._u(5.0), max(self._u(30.0), rect.height() * 0.84))
        icon_rect = QRectF(rect.left() + self._u(8.0), rect.center().y() - icon_size / 2.0, icon_size, icon_size)
        use_ring = self._plugin_alert_uses_ring(rect)
        if done:
            if use_ring:
                icon_color = QColor(28, 21, 0, 248)
                icon_inner = rect.adjusted(self._u(8.0), self._u(8.0), -self._u(8.0), -self._u(8.0))
                self._paint_time_symbol(painter, icon_inner, kind, icon_color, self._u(2.2))
                painter.restore()
                return
            body_source = self._time_alarm_alert_text_from_parts(alert_body, alert_title, summary)
            if kind == "alarm" and body_source:
                body_text = self._time_alarm_note_display_text(body_source)
            else:
                body_source = "\u65f6\u95f4\u5230"
                body_text = "\u65f6\u95f4\u5230"
            self._paint_time_symbol(painter, icon_rect.adjusted(self._u(3.6), self._u(3.6), -self._u(3.6), -self._u(3.6)), kind, text_color, self._u(2.1))
            text_left = icon_rect.right() + self._u(8.0)
            text_rect = QRectF(text_left, rect.top(), max(self._u(20.0), rect.right() - text_left - self._u(12.0)), rect.height())
            self._paint_static_elided_text(painter, text_rect, body_text, 20, text_color, True, Qt.AlignmentFlag.AlignLeft)
            painter.restore()
            return

        if use_ring:
            icon_inner = rect.adjusted(self._u(8.0), self._u(8.0), -self._u(8.0), -self._u(8.0))
            self._paint_time_symbol(painter, icon_inner, kind, ref["accent"], self._u(2.1))
            self._paint_plugin_progress_ring(painter, rect.adjusted(self._u(4.0), self._u(4.0), -self._u(4.0), -self._u(4.0)), ref["accent"], progress, status)
            painter.restore()
            return

        painter.setPen(QPen(ref["accent"], self._u(2.0)))
        painter.setBrush(QColor(255, 184, 0, 18) if kind == "alarm" else Qt.BrushStyle.NoBrush)
        if kind == "alarm":
            painter.drawEllipse(icon_rect)
        self._paint_time_symbol(painter, icon_rect.adjusted(self._u(3.6), self._u(3.6), -self._u(3.6), -self._u(3.6)), kind, ref["accent"], self._u(2.1))
        if use_ring:
            self._paint_plugin_progress_ring(painter, icon_rect.adjusted(-self._u(2.4), -self._u(2.4), self._u(2.4), self._u(2.4)), ref["accent"], progress, status)

        text_left = icon_rect.right() + self._u(8.0)
        text_rect = QRectF(text_left, rect.top(), max(self._u(20.0), rect.right() - text_left - self._u(12.0)), rect.height())
        value = self._strip_timer_prefix(str(alert_body or summary or detail)) or self._plugin_compact_value(label, detail, summary, status)
        if kind == "countdown":
            self._paint_static_elided_text(painter, text_rect, self._time_reminder_display_text(value, 8), 20, text_color, True, Qt.AlignmentFlag.AlignLeft)
        else:
            note_source = self._time_alarm_alert_text_from_parts(alert_body, alert_title)
            note = self._time_alarm_note_display_text(note_source)
            self._paint_static_elided_text(painter, text_rect, note, 20, text_color, True, Qt.AlignmentFlag.AlignLeft)
        painter.restore()

    def _paint_plugin_alert(self, painter: QPainter, rect: QRectF, key: str):
        if str(key or "").startswith("taskbar-overlay:"):
            self._paint_taskbar_time_overlay_alert(painter, rect, key)
            return
        plugin_id = key.split(":", 1)[-1] if ":" in key else ""
        alert = None
        samples = self._enabled_plugin_samples()
        if hasattr(self, "persistent_plugin"):
            alert = next((item for item in self._active_plugin_alerts(4, include_expanded=True) if str(item[6] or "").strip().casefold() == str(plugin_id or "").strip().casefold()), None)
        if alert is not None:
            index, label, detail, summary, color, icon, alert_plugin_id, status, alert_title, alert_body, _alert_priority = alert
            progress = 0.0
            if 0 <= int(index) < len(samples):
                _label, _detail, _summary, _color, _icon, _source_id, _status, _title, _body, _priority, progress = self._plugin_tuple(samples[int(index)])
        else:
            index = self._plugin_index_for_id(plugin_id)
            if index < 0:
                return
            if index >= len(samples):
                return
            label, detail, summary, color, icon, alert_plugin_id, status, alert_title, alert_body, _alert_priority, progress = self._plugin_tuple(samples[index])
        theme = self._plugin_theme_colors()
        color = self._pure_plugin_color(self._plugin_display_color(label, color))
        label_key = str(label or "").strip().upper()
        is_time = label_key == "TIME" or str(plugin_id or "").strip().casefold() == "time"
        if self._taskbar_compact_active():
            if str(getattr(self, "taskbar_plugin_compact_style", "rail") or "rail") == "block":
                self._paint_taskbar_plugin_block(painter, rect, label, detail, summary, color, icon, status, alert_title, alert_body, progress)
            else:
                self._paint_taskbar_plugin_rail(painter, rect, color, status, progress)
            return
        if is_time:
            time_kind = self._time_alert_kind_from_plugin_id(str(plugin_id))
            if time_kind != "alarm":
                time_kind = self._time_alert_kind_from_plugin_id(str(alert_plugin_id))
            self._paint_time_plugin_alert(painter, rect, label, detail, summary, color, status, alert_title, alert_body, progress, time_kind)
            return
        icon_only = rect.width() <= rect.height() * 1.28
        radius = rect.height() / 2.0 if icon_only else min(self._u(17.0), rect.height() / 2.0)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        text_color = theme["text"]
        painter.setBrush(theme["alert"])
        painter.drawRoundedRect(rect, radius, radius)
        self._paint_current_island_space_glow(painter, rect, radius)
        painter.setPen(QPen(theme["stroke"], self._u(1.0)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(self._u(0.8), self._u(0.8), -self._u(0.8), -self._u(0.8)), radius - self._u(0.8), radius - self._u(0.8))

        pulse = 0.5 + 0.5 * math.sin(time.monotonic() * 4.0)
        icon_size = self._u(28.0)
        icon_rect = QRectF(rect.left() + self._u(8.0), rect.center().y() - icon_size / 2.0, icon_size, icon_size)
        painter.setPen(Qt.PenStyle.NoPen)
        icon_fill = QColor(color.red(), color.green(), color.blue(), int(214 + pulse * 32))
        painter.setBrush(icon_fill)
        painter.drawRoundedRect(icon_rect, self._u(10.0), self._u(10.0))
        glow_rect = icon_rect.adjusted(-self._u(4.0), -self._u(4.0), self._u(4.0), self._u(4.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        glow_color = QColor(color.red(), color.green(), color.blue(), int(44 + pulse * 48))
        painter.setPen(QPen(glow_color, self._u(1.2), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawRoundedRect(glow_rect, self._u(12.0), self._u(12.0))
        if self._plugin_alert_uses_ring(rect):
            self._paint_plugin_progress_ring(painter, icon_rect.adjusted(-self._u(3.0), -self._u(3.0), self._u(3.0), self._u(3.0)), color, progress, status)
        painter.setFont(QFont("Segoe UI Symbol", self._font_size(11), QFont.Weight.Bold))
        painter.setPen(QColor(255, 255, 255, 245))
        painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, icon or label[:1])
        if icon_only:
            self._paint_plugin_status_dot(painter, rect, status)
            painter.restore()
            return

        value = self._plugin_alert_display_value(label, detail, summary, status, alert_title, alert_body)
        text_left = icon_rect.right() + self._u(8.0)
        value_rect = QRectF(text_left, rect.top(), rect.right() - text_left - self._u(12.0), rect.height())
        painter.setFont(self._font(12, QFont.Weight.DemiBold))
        painter.setPen(text_color)
        self._paint_scrolling_text(painter, value_rect, value, 12, text_color, False)
        self._paint_plugin_status_dot(painter, rect, status)
        painter.restore()

    def _paint_taskbar_time_overlay_alert(self, painter: QPainter, rect: QRectF, key: str):
        alert = self._taskbar_main_overlay_alert()
        if alert is None:
            return
        try:
            _index, label, detail, summary, color, _icon, _plugin_id, status, alert_title, alert_body, _priority = alert
        except (TypeError, ValueError):
            return
        radius = self._taskbar_corner_radius(rect.height())
        done = str(status or "").strip().lower() in {"done", "due", "alarm"}
        accent = QColor(color)
        if not accent.isValid():
            accent = QColor(255, 184, 0)
        text_color = QColor(38, 24, 0, 245) if done else QColor(255, 239, 188, 245)
        bg = QColor(accent.red(), accent.green(), accent.blue(), 244) if done else QColor(24, 21, 14, 244)
        title = str(alert_title or label or "TIME")
        body = str(alert_body or summary or detail or "时间到")
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, radius, radius)
        self._paint_current_island_space_glow(painter, rect, radius)
        painter.setPen(QPen(QColor(255, 255, 255, 70), self._u(1.0)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(self._u(0.8), self._u(0.8), -self._u(0.8), -self._u(0.8)), max(1.0, radius - self._u(0.8)), max(1.0, radius - self._u(0.8)))
        icon_size = min(rect.height() - self._u(10.0), self._u(28.0))
        icon_rect = QRectF(rect.left() + self._u(10.0), rect.center().y() - icon_size / 2.0, icon_size, icon_size)
        painter.setPen(text_color)
        painter.setFont(QFont("Segoe UI Symbol", self._font_size(13), QFont.Weight.Bold))
        painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, "⏱")
        text_rect = QRectF(icon_rect.right() + self._u(8.0), rect.top(), rect.right() - icon_rect.right() - self._u(18.0), rect.height())
        painter.setFont(self._font(10, QFont.Weight.Bold))
        self._paint_static_elided_text(painter, text_rect, f"{title}  {body}", 10, text_color, True, Qt.AlignmentFlag.AlignLeft)
        painter.restore()

    def _taskbar_plugin_rail_segments(self, rect: QRectF, color: QColor, status: str, progress: float = 0.0) -> list[dict[str, object]]:
        status_key = str(status or "").strip().lower()
        urgent = status_key in {"done", "due", "alarm", "severe", "error"}
        rail = QColor(color)
        if urgent:
            rail = QColor(255, 184, 0)
        try:
            ratio = float(progress)
        except (TypeError, ValueError):
            ratio = 0.0
        if ratio <= 0.0:
            ratio = 1.0 if urgent else 0.36
        ratio = max(0.08, min(1.0, ratio))
        color_h = rect.height() * ratio
        black_rect = QRectF(rect)
        color_rect = QRectF(rect.left(), rect.bottom() - color_h, rect.width(), color_h)
        return [
            {"rect": black_rect, "color": QColor(0, 0, 0, 0)},
            {"rect": color_rect, "color": QColor(rail.red(), rail.green(), rail.blue(), 235)},
        ]

    def _paint_taskbar_plugin_rail(self, painter: QPainter, rect: QRectF, color: QColor, status: str, progress: float = 0.0):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        radius = min(rect.width() / 2.0, rect.height() / 2.0)
        painter.setPen(Qt.PenStyle.NoPen)
        path = self._rounded_path(rect, radius)
        painter.setClipPath(path)
        for segment in self._taskbar_plugin_rail_segments(rect, color, status, progress):
            painter.setBrush(segment["color"])
            painter.drawRect(segment["rect"])
        self._paint_current_island_space_glow(painter, rect, radius)
        painter.restore()

    def _paint_taskbar_plugin_block(
        self,
        painter: QPainter,
        rect: QRectF,
        label: str,
        detail: str,
        summary: str,
        color: QColor,
        icon: str,
        status: str,
        alert_title: str,
        alert_body: str,
        progress: float,
    ):
        radius = self._taskbar_corner_radius(rect.height())
        value = self._plugin_alert_display_value(label, detail, summary, status, alert_title, alert_body)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 238))
        painter.drawRoundedRect(rect, radius, radius)
        self._paint_current_island_space_glow(painter, rect, radius)
        accent = QColor(color)
        painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 218))
        icon_size = min(rect.width(), rect.height()) * 0.48
        icon_rect = QRectF(rect.center().x() - icon_size / 2.0, rect.top() + self._u(4.0), icon_size, icon_size)
        painter.drawRoundedRect(icon_rect, max(2.0, radius * 0.55), max(2.0, radius * 0.55))
        painter.setFont(QFont("Segoe UI Symbol", self._font_size(9), QFont.Weight.Bold))
        painter.setPen(QColor(255, 255, 255, 245))
        painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, icon or str(label or "?")[:1])
        text_rect = QRectF(rect.left() + self._u(2.0), icon_rect.bottom() - self._u(1.0), rect.width() - self._u(4.0), rect.bottom() - icon_rect.bottom())
        painter.setFont(self._font(7, QFont.Weight.Bold))
        painter.setPen(QColor(235, 242, 255, 226))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, str(value or summary or detail)[:6])
        if progress > 0.0:
            ring = rect.adjusted(self._u(2.2), self._u(2.2), -self._u(2.2), -self._u(2.2))
            self._paint_plugin_progress_ring(painter, ring, accent, progress, status)
        painter.restore()

    def _paint_plugin_status_dot(self, painter: QPainter, rect: QRectF, status: str):
        status = str(status or "normal").lower()
        colors = {
            "running": QColor(52, 199, 89, 235),
            "done": QColor(255, 59, 48, 235),
            "due": QColor(255, 59, 48, 235),
            "soon": QColor(255, 149, 0, 235),
            "loading": QColor(0, 122, 255, 225),
            "error": QColor(255, 149, 0, 235),
            "alarm": QColor(255, 59, 48, 235),
            "severe": QColor(255, 59, 48, 235),
            "rain": QColor(90, 200, 250, 235),
        }
        color = colors.get(status)
        if color is None:
            return
        size = self._u(6.0)
        dot = QRectF(rect.right() - self._u(9.0), rect.top() + self._u(7.0), size, size)
        painter.setPen(QPen(QColor(255, 255, 255, 215), self._u(1.0)))
        painter.setBrush(color)
        painter.drawEllipse(dot)

    def _message_app_glyph(self, msg: Message) -> str:
        app = str(getattr(msg, "app", "") or "").strip().casefold()
        if "wechat" in app or "微信" in app:
            return "微"
        if "qq" in app:
            return "Q"
        if "dingtalk" in app or "钉" in app:
            return "钉"
        if "feishu" in app or "飞书" in app:
            return "飞"
        return (str(getattr(msg, "app", "") or "").strip()[:1] or "•")

    def _taskbar_message_block_model(self, msg: Message, rect: QRectF) -> dict[str, object]:
        radius = self._taskbar_corner_radius(rect.height())
        fill = QColor(msg.name_color)
        return {
            "fill": QColor(fill),
            "outer": QColor(fill),
            "glyph": self._message_app_glyph(msg),
            "radius": radius,
        }

    def _paint_message(self, painter: QPainter, rect: QRectF, msg: Message, folded: bool):
        if folded and self._taskbar_compact_active():
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            accent = QColor(msg.name_color)
            if rect.width() < rect.height():
                painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 232))
                painter.drawRoundedRect(rect, rect.width() / 2.0, rect.width() / 2.0)
                self._paint_current_island_space_glow(painter, rect, rect.width() / 2.0)
            else:
                model = self._taskbar_message_block_model(msg, rect)
                radius = float(model["radius"])
                painter.setBrush(model["fill"])
                painter.drawRoundedRect(rect, radius, radius)
                self._paint_current_island_space_glow(painter, rect, radius)
                inset = self._u(4.0)
                icon_rect = rect.adjusted(inset, inset, -inset, -inset)
                if not msg.avatar_image.isNull():
                    clip = QPainterPath()
                    clip.addRoundedRect(icon_rect, max(2.0, radius * 0.62), max(2.0, radius * 0.62))
                    painter.save()
                    painter.setClipPath(clip)
                    painter.drawImage(icon_rect, msg.avatar_image)
                    painter.restore()
                else:
                    painter.setFont(self._font(8, QFont.Weight.Bold))
                    painter.setPen(QColor(255, 255, 255, 242))
                    painter.drawText(icon_rect, Qt.AlignmentFlag.AlignCenter, str(model["glyph"]))
            painter.restore()
            return

        progress = self._message_card_progress(rect)
        if progress < 0.08:
            color = msg.name_color
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), 225))
            painter.drawEllipse(rect)
            ellipse_path = QPainterPath()
            ellipse_path.addEllipse(rect)
            self._paint_current_island_space_glow(painter, rect, min(rect.width(), rect.height()) / 2.0, ellipse_path)
            return

        radius = rect.height() / 2 if progress < 0.55 else min(self._u(23), rect.height() / 2)
        if progress < 0.92:
            color = msg.name_color
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), int(80 + progress * 78)))
            painter.drawRoundedRect(rect, radius, radius)
        else:
            self._paint_black_glass(painter, rect, radius, folded=False)

        self._paint_current_island_space_glow(painter, rect, radius)

        painter.save()
        painter.setOpacity(progress)
        avatar_size = self._u(28)
        avatar = QRectF(rect.left() + self._u(10), rect.center().y() - avatar_size / 2.0, avatar_size, avatar_size)
        painter.setPen(Qt.PenStyle.NoPen)
        if not msg.avatar_image.isNull():
            painter.save()
            clip = QPainterPath()
            clip.addEllipse(avatar)
            painter.setClipPath(clip)
            painter.drawImage(avatar, msg.avatar_image)
            painter.restore()
        else:
            painter.setBrush(msg.avatar_color)
            painter.drawEllipse(avatar)
            painter.setFont(self._font(9, QFont.Weight.Bold))
            painter.setPen(QColor(255, 255, 255, 235))
            painter.drawText(avatar, Qt.AlignmentFlag.AlignCenter, msg.sender[:1])

        x = avatar.right() + self._u(11)
        painter.setFont(self._font(10, QFont.Weight.Bold))
        name_w = QFontMetrics(painter.font()).horizontalAdvance(msg.sender) + self._u(7)
        painter.setPen(msg.name_color)
        painter.drawText(QRectF(x, rect.top() + self._u(6), name_w, rect.height() - self._u(12)), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, msg.sender)
        x += name_w + self._u(9)
        text_rect = QRectF(x, rect.top() + self._u(6), rect.right() - x - self._u(16), rect.height() - self._u(12))
        self._paint_scrolling_text(painter, text_rect, msg.text, 10, QColor(255, 255, 255, 218), False)
        painter.restore()

    def _paint_thin_bar(self, painter: QPainter, rect: QRectF):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bar = rect.adjusted(self._u(0.5), self._u(0.5), -self._u(0.5), -self._u(0.5))
        radius = min(bar.width(), bar.height()) / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        path = self._rounded_path(bar, radius)
        fill = self._empty_bar_color()
        fill.setAlpha(self._empty_bar_alpha())
        glow = QColor(fill)
        glow.setAlpha(40)
        glow_rect = bar.adjusted(-self._u(1.4), -self._u(1.4), self._u(1.4), self._u(1.4))
        painter.fillPath(self._rounded_path(glow_rect, min(glow_rect.width(), glow_rect.height()) / 2.0), glow)
        painter.fillPath(path, fill)
        self._paint_current_island_space_glow(painter, bar, radius)

        stroke = QColor(255, 255, 255, 52) if fill.lightness() < 128 else QColor(0, 0, 0, 40)
        painter.setPen(QPen(stroke, self._u(1.0)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.restore()

    def _paint_black_empty_island(self, painter: QPainter, rect: QRectF, text: str, font: QFont, colors: tuple[QColor, QColor]):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        shell = rect.adjusted(self._u(0.5), self._u(0.5), -self._u(0.5), -self._u(0.5))
        radius = shell.height() / 2.0
        background, text_color = colors
        shadow = QColor(background)
        shadow.setAlpha(72)
        shadow_rect = shell.adjusted(-self._u(1.2), -self._u(1.2), self._u(1.2), self._u(1.2))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(self._rounded_path(shadow_rect, shadow_rect.height() / 2.0), shadow)
        painter.fillPath(self._rounded_path(shell, radius), background)
        self._paint_current_island_space_glow(painter, shell, radius)
        painter.setPen(QPen(QColor(255, 255, 255, 36), self._u(1.0)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._rounded_path(shell, radius))

        text_rect = shell.adjusted(self._u(18.0), 0.0, -self._u(18.0), 0.0)
        painter.setFont(font)
        painter.setPen(text_color)
        painter.save()
        painter.setClipRect(text_rect)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()
        painter.restore()

    def _paint_empty_text_island(self, painter: QPainter, rect: QRectF):
        self._paint_black_empty_island(
            painter,
            rect,
            self._empty_text_display_text(),
            self._empty_text_island_font(),
            self._empty_text_island_colors(),
        )

    def _paint_empty_time_island(self, painter: QPainter, rect: QRectF):
        self._paint_black_empty_island(
            painter,
            rect,
            self._empty_time_text(),
            self._empty_time_island_font(),
            self._empty_time_island_colors(),
        )

    def _scrolling_text_x(self, rect: QRectF, text_w: float, centered: bool, centered_scroll: bool, focus_px: float | None = None) -> float:
        if text_w <= rect.width() - self._u(10):
            return rect.center().x() - text_w / 2.0 if centered else rect.left()
        if focus_px is not None:
            min_x = rect.right() - text_w
            max_x = rect.left()
            safe_left = rect.left() + rect.width() * 0.40
            safe_right = rect.left() + rect.width() * 0.68
            focus_x = max(0.0, min(text_w, focus_px))
            desired = rect.left() if focus_x <= rect.width() * 0.42 else safe_right - focus_x
            if desired + focus_x < safe_left:
                desired = safe_left - focus_x
            return max(min_x, min(max_x, desired))
        travel = text_w - rect.width() + self._u(42)
        if centered_scroll:
            offset = math.sin(self.scroll_phase * 0.55) * travel / 2.0
            return rect.center().x() - text_w / 2.0 - offset
        offset = (math.sin(self.scroll_phase * 0.55) + 1.0) / 2.0 * travel
        return rect.left() - offset

    def _island_lyric_row_x(self, rect: QRectF, text_w: float, centered: bool, phase: dict) -> float:
        """A lyric's horizontal pose belongs to its own playback clock.

        Fully fitting text stays centered, including the old ten-pixel margin
        boundary. Whole-row reading scrolls once and holds the tail; it cannot
        inherit the continuously reversing metadata marquee's global phase.
        """
        if text_w <= rect.width():
            return rect.center().x() - text_w / 2 if centered else rect.left()
        if phase.get("whole_row") or "whole_block_progress" in phase:
            elapsed = max(0., float(phase.get("elapsed", 0.)))
            span = max(.5, float(phase.get("span", 2.45)))
            progress = max(0., min(1., (elapsed - .5) / max(1., min(6., span - .5))))
            eased = progress * progress * (3 - 2 * progress)
            return rect.left() - (text_w - rect.width()) * eased
        return self._scrolling_text_x(rect, text_w, centered, False,
                                      focus_px=max(0., min(1., float(phase.get("progress", 0.)))) * text_w)

    def _is_combining_lyric_char(self, char: str) -> bool:
        codepoint = ord(char)
        return unicodedata.combining(char) != 0 or 0xFE00 <= codepoint <= 0xFE0F or codepoint == 0x200D

    def _is_cjk_lyric_char(self, char: str) -> bool:
        codepoint = ord(char)
        return (
            0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
            or 0x3040 <= codepoint <= 0x30FF
            or 0xAC00 <= codepoint <= 0xD7AF
        )

    def _lyric_chunks(self, text: str) -> list[str]:
        chunks: list[str] = []
        buffer = ""
        buffer_kind = ""
        for char in text:
            if self._is_combining_lyric_char(char):
                if chunks and not buffer:
                    chunks[-1] += char
                else:
                    buffer += char
                continue
            if char.isspace():
                if buffer:
                    chunks.append(buffer + char)
                    buffer = ""
                    buffer_kind = ""
                elif chunks:
                    chunks[-1] += char
                else:
                    chunks.append(char)
                continue
            if self._is_cjk_lyric_char(char):
                if buffer:
                    chunks.append(buffer)
                    buffer = ""
                    buffer_kind = ""
                chunks.append(char)
                continue
            kind = "word" if char.isalnum() else "punct"
            if kind == "punct":
                if buffer:
                    chunks.append(buffer + char)
                    buffer = ""
                    buffer_kind = ""
                elif chunks:
                    chunks[-1] += char
                else:
                    chunks.append(char)
                continue
            if buffer_kind == kind:
                buffer += char
            else:
                if buffer:
                    chunks.append(buffer)
                buffer = char
                buffer_kind = kind
        if buffer:
            chunks.append(buffer)
        return chunks or ([text] if text else [])

    def _gelv_chunks(self, text: str) -> list[str]:
        return [str(chunk).strip() for chunk in self._lyric_chunks(text) if str(chunk).strip()]

    def _gelv_join_chunks(self, chunks: list[str]) -> str:
        output = ""
        for chunk in chunks:
            if not output:
                output = chunk
                continue
            joins_as_word = (
                output[-1:].isascii()
                and output[-1:].isalnum()
                and chunk[:1].isascii()
                and chunk[:1].isalnum()
            )
            output += (" " if joins_as_word else "") + chunk
        return output.strip()

    def _ember_chunk_counts(self, chunk: str) -> tuple[int, int]:
        cjk_count = sum(1 for char in chunk if self._is_cjk_lyric_char(char))
        word_count = 1 if any(char.isascii() and char.isalnum() for char in chunk) else 0
        return cjk_count, word_count

    def _ember_limits(self, expanded: bool) -> tuple[int, int, int, int]:
        if expanded:
            return 20, 12, 40, 24
        return 12, 8, 24, 16

    def _ember_dual_display_lines(self, text: str) -> list[str]:
        raw = str(text or "").strip()
        if not raw:
            return []
        if "\n" in raw:
            lines = [" ".join(part.split()) for part in raw.splitlines()]
            lines = [line for line in lines if line]
            if lines:
                return lines[:2]
        flattened = " ".join(raw.split())
        for pattern in (r"\s[|/／]{1,2}\s", r"\s-{2,}\s"):
            parts = [" ".join(part.split()) for part in re.split(pattern, flattened, maxsplit=1)]
            if len(parts) == 2 and all(parts):
                return parts
        return [flattened] if flattened else []

    def _ember_primary_line_text(self, text: str) -> str:
        lines = self._ember_dual_display_lines(text)
        if lines:
            return lines[0]
        return " ".join(str(text or "").split())

    def _ember_scroll_display_lines(self, text: str, expanded: bool = False) -> list[str]:
        source = str(text or "")
        if not self._ember_dual_line_enabled():
            source = self._ember_primary_line_text(source)
        flattened = " ".join(source.split())
        if not flattened:
            return []
        if self._ember_dual_line_enabled():
            lines = self._ember_dual_display_lines(text)
            if self._ember_edge_scroll_forced(expanded=expanded) and len(lines) > 1:
                return ["  /  ".join(lines[:2])]
            return lines[:2] if lines else [flattened]
        return [flattened]

    def _ember_display_lines_for_mode(self, text: str, expanded: bool = False) -> list[str]:
        if not self._ember_dual_line_enabled():
            text = self._ember_primary_line_text(text)
        if self._ember_effective_line_mode(expanded=expanded) == "scroll":
            return self._ember_scroll_display_lines(text, expanded=expanded)
        return self._ember_display_lines(text, expanded=expanded)

    def _ember_display_lines(self, text: str, expanded: bool = False) -> list[str]:
        text = " ".join(str(text or "").split())
        if not text:
            return []
        line_char_limit, line_word_limit, total_char_limit, total_word_limit = self._ember_limits(expanded)
        chunks = self._gelv_chunks(text)
        if not chunks:
            return []
        total_chars = 0
        total_words = 0
        kept: list[str] = []
        overflow = False

        for chunk in chunks:
            char_count, word_count = self._ember_chunk_counts(chunk)
            if (
                total_chars + char_count > total_char_limit
                or total_words + word_count > total_word_limit
            ):
                overflow = True
                break
            kept.append(chunk)
            total_chars += char_count
            total_words += word_count

        if not kept:
            return []
        needs_two_rows = total_chars > line_char_limit or total_words > line_word_limit
        if needs_two_rows and len(kept) > 1:
            upper_count = (len(kept) + 1) // 2
            lines = [
                self._gelv_join_chunks(kept[:upper_count]),
                self._gelv_join_chunks(kept[upper_count:]),
            ]
            lines = [line for line in lines if line]
        else:
            lines = [self._gelv_join_chunks(kept)]
        if overflow:
            lines = lines[:2]
            lines.append("...")
        return lines[:3]

    def _gelv_split_line(self, text: str, threshold: int | None = None) -> tuple[str, str] | None:
        chunks = self._gelv_chunks(text)
        limit = max(1, int(threshold if threshold is not None else 18))
        if len(chunks) <= limit:
            return None
        upper_count = (len(chunks) + 1) // 2
        upper = self._gelv_join_chunks(chunks[:upper_count])
        lower = self._gelv_join_chunks(chunks[upper_count:])
        if not upper or not lower:
            return None
        return upper, lower

    def _lyric_layout_key(self, text: str, size: float, bold: bool, spacing_percent: int | None = None) -> tuple[object, ...]:
        font = self._wave_stream_font(size, QFont.Weight.Bold if bold else QFont.Weight.Normal)
        return text, font.toString()

    def _lyric_chunk_spacing(self, chunk: str, base_width: float, reference_width: float | None = None, spacing_percent: int | None = None) -> float:
        if not chunk or not chunk.strip():
            return 0.0
        strength = max(0.0, min(1.6, float(getattr(self, "lyric_elastic_strength", 100)) / 100.0))
        spacing = max(0.6, min(1.5, float(spacing_percent if spacing_percent is not None else getattr(self, "lyric_word_spacing", 100)) / 100.0))
        if strength <= 0.01:
            return 0.0
        has_cjk = any(self._is_cjk_lyric_char(char) for char in chunk)
        minimum = self._u(1.2 if has_cjk else 1.8)
        selected_width = max(0.0, float(base_width))
        original_width = max(0.0, float(reference_width if reference_width is not None else base_width))
        effective_width = (selected_width + original_width) * 0.5
        proportional = effective_width * (0.046 + 0.038 * strength)
        return max(minimum, proportional) * spacing

    def _lyric_layout(self, text: str, size: float, bold: bool, spacing_percent: int | None = None) -> LyricLayout:
        key = self._lyric_layout_key(text, size, bold, spacing_percent)
        cache = getattr(self, "_lyric_layout_cache", None)
        if cache is None:
            cache = {}
            self._lyric_layout_cache = cache
        cached = cache.get(key)
        if cached is not None:
            return cached
        font = self._wave_stream_font(size, QFont.Weight.Bold if bold else QFont.Weight.Normal)
        shaped = QTextLayout(text, font)
        apply_cjk_letter_spacing(shaped, text, font, resolve_point_size=True)
        shaped.beginLayout()
        line = shaped.createLine()
        shaped.endLayout()
        offsets = reading_unit_offsets(text)
        chunks = tuple(utf16_slice(text, start, end - start) for start, end in zip(offsets, offsets[1:]))
        if line.isValid():
            positions = tuple(float(line.cursorToX(offset)[0]) for offset in offsets)
            widths = tuple(max(0.0, right - left) for left, right in zip(positions, positions[1:]))
            total_width = float(line.naturalTextWidth())
        else:
            widths = tuple(0.0 for _chunk in chunks)
            total_width = 0.0
        layout = LyricLayout(text=text, chunks=chunks, widths=widths, total_width=total_width, font_key=key)
        cache[key] = layout
        while len(cache) > int(getattr(self, "LYRIC_LAYOUT_CACHE_LIMIT", 48)):
            cache.pop(next(iter(cache)))
        return layout

    def _gelv_layout_key(self, text: str, size: float, spacing_percent: int | None = None) -> tuple[object, ...]:
        return (
            "gelv",
            text,
            self._font_size(size),
            tuple(self._gelv_font_families()),
            int(spacing_percent if spacing_percent is not None else self._gelv_spacing_percent(1.0)),
        )

    def _gelv_chunk_spacing(self, chunk: str, base_width: float, reference_width: float | None = None, spacing_percent: int | None = None) -> float:
        if not chunk or not chunk.strip():
            return 0.0
        spacing = max(100, min(420, int(spacing_percent if spacing_percent is not None else self._gelv_spacing_percent(1.0))))
        spread = max(1.0, min(4.2, spacing / 100.0))
        has_cjk = any(self._is_cjk_lyric_char(char) for char in chunk)
        minimum = self._u(8.0 if has_cjk else 10.0)
        selected_width = max(0.0, float(base_width))
        original_width = max(0.0, float(reference_width if reference_width is not None else base_width))
        effective_width = (selected_width + original_width) * 0.5
        proportional = effective_width * (0.30 if has_cjk else 0.28)
        return max(minimum, proportional) * spread

    def _gelv_layout(self, text: str, size: float, spacing_percent: int | None = None) -> LyricLayout:
        key = self._gelv_layout_key(text, size, spacing_percent)
        cache = getattr(self, "_lyric_layout_cache", None)
        if cache is None:
            cache = {}
            self._lyric_layout_cache = cache
        cached = cache.get(key)
        if cached is not None:
            return cached
        chunks = tuple(self._gelv_chunks(text))
        font = self._gelv_font(size, QFont.Weight.Normal)
        metrics = QFontMetrics(font)
        reference_font = self._gelv_font(18 if size >= 16 else 15, QFont.Weight.Normal)
        reference_metrics = QFontMetrics(reference_font)
        widths = []
        for chunk in chunks:
            width = float(metrics.horizontalAdvance(chunk))
            reference_width = float(reference_metrics.horizontalAdvance(chunk))
            widths.append(width + self._gelv_chunk_spacing(chunk, width, reference_width, spacing_percent))
        layout = LyricLayout(text=text, chunks=chunks, widths=tuple(widths), total_width=sum(widths), font_key=key)
        cache[key] = layout
        while len(cache) > int(getattr(self, "LYRIC_LAYOUT_CACHE_LIMIT", 48)):
            cache.pop(next(iter(cache)))
        return layout

    def _ember_layout_key(self, text: str, size: float, back_layer: bool = False) -> tuple[object, ...]:
        return (
            "ember",
            text,
            self._font_size(size) + (2 if back_layer else 0),
            tuple(self._ember_font_families(back_layer=back_layer)),
            bool(back_layer),
            bool(getattr(self, "ember_italic_enabled", False)),
        )

    def _ember_chunk_spacing(self, chunk: str) -> float:
        if not chunk or not chunk.strip():
            return 0.0
        has_cjk = any(self._is_cjk_lyric_char(char) for char in chunk)
        return self._u(7.5 if has_cjk else 10.0)

    def _ember_layout(self, text: str, size: float, back_layer: bool = False) -> LyricLayout:
        key = self._ember_layout_key(text, size, back_layer=back_layer)
        cache = getattr(self, "_lyric_layout_cache", None)
        if cache is None:
            cache = {}
            self._lyric_layout_cache = cache
        cached = cache.get(key)
        if cached is not None:
            return cached
        chunks = tuple(self._gelv_chunks(text))
        metrics = QFontMetrics(self._ember_font(size, back_layer=back_layer))
        widths = tuple(float(metrics.horizontalAdvance(chunk)) + self._ember_chunk_spacing(chunk) for chunk in chunks)
        layout = LyricLayout(text=text, chunks=chunks, widths=widths, total_width=sum(widths), font_key=key)
        cache[key] = layout
        while len(cache) > int(getattr(self, "LYRIC_LAYOUT_CACHE_LIMIT", 48)):
            cache.pop(next(iter(cache)))
        return layout

    def _lyric_progress_width(self, layout: LyricLayout, progress: float) -> float:
        chunks = layout.chunks
        if not chunks:
            return 0.0
        target = max(0.0, min(1.0, progress)) * len(chunks)
        full_count = max(0, min(len(chunks), int(target)))
        partial = max(0.0, min(1.0, target - full_count))
        width = sum(layout.widths[:full_count])
        if full_count < len(chunks):
            width += layout.widths[full_count] * partial
        return width

    def _lyric_elastic_scales(self, chunk_count: int, progress: float, confidence: float) -> list[float]:
        if chunk_count <= 0:
            return []
        strength = max(0.0, min(1.6, float(getattr(self, "lyric_elastic_strength", 100)) / 100.0))
        if strength <= 0.01 or confidence <= 0.01:
            return [1.0] * chunk_count
        mode = str(getattr(self, "lyric_elastic_mode", "both") or "both").lower()
        if chunk_count == 1:
            bloom = math.sin(max(0.0, min(1.0, progress)) * math.pi)
            return [1.0 + 0.055 * bloom * confidence * strength]
        focus = max(0.0, min(1.0, progress)) * (chunk_count - 1)
        now = time.monotonic()
        scales: list[float] = []
        for index in range(chunk_count):
            distance = abs(index - focus)
            core = math.exp(-(distance * distance) / 0.18)
            near = math.exp(-(distance * distance) / 1.35)
            sung_side = max(0.0, min(1.0, focus - index + 0.65))
            leading = 0.60 + 0.40 * sung_side
            spring = 0.94 + 0.06 * math.sin(now * 5.8 - index * 0.72)
            if mode == "left":
                right_fade = max(0.0, min(1.0, 1.0 - max(0.0, index - focus) / 0.55))
                left_tail = math.exp(-(max(0.0, focus - index) ** 2) / 2.1)
                amount = 0.078 * core * right_fade + 0.030 * left_tail * leading
            else:
                amount = 0.100 * core + 0.044 * near * leading
            scale = 1.0 + confidence * strength * spring * amount
            scales.append(max(1.0, min(1.24, scale)))
        return scales

    def _mix_alpha_color(self, a: QColor, b: QColor, amount: float, alpha: int | None = None) -> QColor:
        amount = max(0.0, min(1.0, amount))
        return QColor(
            int(a.red() + (b.red() - a.red()) * amount),
            int(a.green() + (b.green() - a.green()) * amount),
            int(a.blue() + (b.blue() - a.blue()) * amount),
            max(0, min(255, int(a.alpha() + (b.alpha() - a.alpha()) * amount) if alpha is None else alpha)),
        )

    def _expanded_word_backplate_enabled(self) -> bool:
        return False

    def _paint_wave_stream_lyric_chunks(
        self,
        painter: QPainter,
        rect: QRectF,
        x: float,
        layout: LyricLayout,
        size: float,
        bold: bool,
        base_color: QColor,
        live_color: QColor,
        progress: float,
        confidence: float,
        glow: bool = False,
        *,
        timed_units: tuple[object, ...] = (),
        timed_position: float | None = None,
        timed_line_end: float | None = None,
        timed_character_front: float | None = None,
        whole_row: bool = False,
    ):
        if not layout.chunks or layout.total_width <= 0.0:
            return
        font = self._wave_stream_font(size, QFont.Weight.Bold if bold else QFont.Weight.Normal)
        renderer = getattr(self, "_wave_reading_renderer", None)
        if not isinstance(renderer, LyricReadingRenderer):
            renderer = LyricReadingRenderer()
            self._wave_reading_renderer = renderer
        # Wave Stream shares the full-screen read wipe and one-way lift.  The
        # historical ``glow`` pass is accepted for call-site compatibility,
        # but never scales or redraws the whole word.
        paint_kwargs = {}
        if timed_units and timed_position is not None:
            paint_kwargs = {
                "timed_units": timed_units,
                "timed_position": timed_position,
                "timed_line_end": timed_line_end,
                "timed_character_front": timed_character_front,
            }
        if whole_row:
            paint_kwargs = {"whole_row": True}
        renderer.paint(
            painter,
            rect,
            x,
            layout.text,
            font,
            max(0.0, min(1.0, float(progress))),
            QColor(base_color),
            QColor(live_color),
            **paint_kwargs,
        )

    def _paint_live_lyric_text(
        self,
        painter: QPainter,
        rect: QRectF,
        text: str,
        size: float,
        base_color: QColor,
        live_color: QColor,
        bold: bool,
        phase: dict[str, float],
        centered: bool = False,
        centered_scroll: bool = False,
        spacing_percent: int | None = None,
    ):
        if "whole_block_progress" in phase:
            self._paint_wave_stream_whole_block(painter, (
                (rect, text, size, bold, QColor(base_color), QColor(live_color), spacing_percent),
            ), phase)
            return
        progress = max(0.0, min(1.0, float(phase.get("progress", 0.0))))
        whole_row = bool(phase.get("whole_row", False))
        layout = self._lyric_layout(text, size, bold, spacing_percent)
        text_w = layout.total_width
        x = self._island_lyric_row_x(rect, text_w, centered, phase)
        painter.save()
        renderer = getattr(self, "_wave_reading_renderer", None)
        if not isinstance(renderer, LyricReadingRenderer):
            renderer = LyricReadingRenderer()
            self._wave_reading_renderer = renderer
        font = self._wave_stream_font(size, QFont.Weight.Bold if bold else QFont.Weight.Normal)
        device_getter = getattr(painter, "device", None)
        device = device_getter() if callable(device_getter) else None
        dpr = device.devicePixelRatioF() if device is not None else 1.0
        vertical_margin = renderer.vertical_clip_margin(text, font, rect.height(), dpr)
        # The slot still bounds horizontal marquee travel; its upper/lower
        # edges must not shave off a word as its baseline rises. The caller's
        # shell/viewport clip remains in force through IntersectClip.
        painter.setClipRect(
            rect.adjusted(0.0, -vertical_margin, 0.0, vertical_margin),
            Qt.ClipOperation.IntersectClip,
        )
        # Wave Stream always uses the continuous read front.  Legacy detailed
        # fill/color and elastic knobs are intentionally ignored here.
        timed_kwargs = {}
        if text == phase.get("timed_primary_text") and phase.get("timed_units"):
            timed_kwargs = {
                "timed_units": tuple(phase["timed_units"]),
                "timed_position": phase.get("timed_position"),
                "timed_line_end": phase.get("timed_line_end"),
                "timed_character_front": phase.get("timed_character_front"),
            }
        if whole_row:
            timed_kwargs = {"whole_row": True}
        self._paint_wave_stream_lyric_chunks(
            painter,
            rect,
            x,
            layout,
            size,
            bold,
            QColor(base_color),
            QColor(live_color),
            progress,
            1.0,
            **timed_kwargs,
        )
        painter.restore()

    def _paint_scrolling_text(
        self,
        painter: QPainter,
        rect: QRectF,
        text: str,
        size: int,
        color: QColor,
        bold: bool,
        centered: bool = False,
        centered_scroll: bool = False,
        *,
        font: QFont | None = None,
    ):
        font = font if font is not None else self._font(size, QFont.Weight.Bold if bold else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(color)
        fm = QFontMetrics(font)
        text_w = fm.horizontalAdvance(text)
        painter.save()
        painter.setClipRect(rect)
        if text_w <= rect.width() - self._u(10):
            flags = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignHCenter if centered else Qt.AlignmentFlag.AlignLeft)
            painter.drawText(rect, flags, text)
        else:
            x = self._scrolling_text_x(rect, text_w, centered, centered_scroll)
            painter.drawText(QRectF(x, rect.top(), text_w + self._u(20), rect.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        painter.restore()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    IslandStackVisual.load_embedded_lyric_fonts()
    app.setFont(QFont(IslandStackVisual.best_available_font_family(IslandStackVisual.DEFAULT_FONT_FAMILY), 9))
    from api.fake_provider import FakeIslandDataProvider

    widget = IslandStackVisual(data_provider=FakeIslandDataProvider())
    widget.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
