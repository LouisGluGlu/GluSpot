from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping


@dataclass(frozen=True)
class SceneSuppressionUpdatePlan:
    scene_suppressed: bool
    scene_reason: str
    changed: bool
    clear_scene_reason: bool = False
    clear_transition_mode: bool = False
    transition_mode: str = ""
    transition_duration: float = 0.0
    input_passthrough: bool = False
    reset_hover_state: bool = False
    reset_left_stack: bool = False
    collapse_expanded: bool = False
    clear_mask: bool = False
    update: bool = False


@dataclass(frozen=True)
class TickRenderPlan:
    should_render: bool
    scroll_phase_delta: float = 0.0
    ambient_phase_delta: float = 0.0


@dataclass(frozen=True)
class BarrageHoverClockPlan:
    hovered: bool
    clock_delta: float = 0.0


@dataclass(frozen=True)
class SceneSuppressedTickPlan:
    should_render: bool = False


@dataclass(frozen=True)
class SceneSuppressedInteractionResetPlan:
    reset_hover_state: bool = True
    reset_left_stack: bool = True


@dataclass(frozen=True)
class TickFollowRefreshPlan:
    should_refresh: bool = False


@dataclass(frozen=True)
class LyricFallbackAdvancePlan:
    should_advance: bool = False
    previous_index: int = 0
    next_index: int = 0
    changed_at: float = 0.0


@dataclass(frozen=True)
class MusicLyricTickPlan:
    sync_timed_lyrics: bool = False
    fallback_advance: LyricFallbackAdvancePlan = field(default_factory=LyricFallbackAdvancePlan)


@dataclass(frozen=True)
class TickInteractionPlan:
    activate_hover_key: bool = False
    clear_hover_armed_key: bool = False
    open_plugin_panel: bool = False
    pulse_after_plugin_open: bool = False
    collapse_left_stack: bool = False
    clear_left_stack: bool = False
    pulse_after_left_collapse: bool = False
    render_after_alarm_long_press: bool = False
    stop_after_alarm_long_press: bool = False


@dataclass(frozen=True)
class MusicWaveUpdatePlan:
    live_audio_available: bool = False
    current_audio_bands: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class MusicWaveCadenceDiagnostics:
    status: str = "missing"
    cadence_interval_ms: float = 0.0
    cadence_tolerance_ms: float = 0.0
    update_due: bool = False
    update_count: int = 0
    first_updated_at: float = 0.0
    last_updated_at: float = 0.0
    update_age_ms: float | None = None
    update_window_seconds: float = 0.0
    estimated_update_fps: float = 0.0
    render_frame_count: int = 0
    render_average_fps: float = 0.0
    wave_to_render_fps_ratio: float = 0.0
    render_fps_delta: float = 0.0
    cadence_limited: bool = False

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "status": self.status,
            "cadence_interval_ms": round(self.cadence_interval_ms, 3),
            "cadence_tolerance_ms": round(self.cadence_tolerance_ms, 3),
            "update_due": self.update_due,
            "update_count": self.update_count,
            "first_updated_at": round(self.first_updated_at, 3),
            "last_updated_at": round(self.last_updated_at, 3),
            "update_window_seconds": round(self.update_window_seconds, 3),
            "estimated_update_fps": round(self.estimated_update_fps, 3),
            "render_frame_count": self.render_frame_count,
            "render_average_fps": round(self.render_average_fps, 3),
            "wave_to_render_fps_ratio": round(self.wave_to_render_fps_ratio, 3),
            "render_fps_delta": round(self.render_fps_delta, 3),
            "cadence_limited": self.cadence_limited,
        }
        result["update_age_ms"] = None if self.update_age_ms is None else round(self.update_age_ms, 3)
        return result


@dataclass(frozen=True)
class MusicWaveInputSnapshot:
    sampled_bands: tuple[float, float, float] = (0.0, 0.0, 0.0)
    live_audio_available: bool = False


@dataclass(frozen=True)
class ActiveVisualAnimationPlan:
    active: bool = False
    reason: str = ""


@dataclass(frozen=True)
class TickFrameExecutionPlan:
    active_animation: bool = False
    active_reason: str = ""
    retune_frame_timer: bool = True
    should_render: bool = False
    scroll_phase_delta: float = 0.0
    ambient_phase_delta: float = 0.0
    update_music_wave: bool = False
    mark_render_frame: bool = False
    update_widget: bool = False


@dataclass(frozen=True)
class SceneSuppressedFrameExecutionPlan:
    active_animation: bool = False
    active_reason: str = ""
    retune_frame_timer: bool = True
    should_render: bool = False
    mark_render_frame: bool = False
    update_widget: bool = False


@dataclass(frozen=True)
class MusicSurfacePaintPlan:
    ambience_style: str
    steps: tuple[str, ...]


@dataclass(frozen=True)
class MusicBackgroundLayerPaintPlan:
    ambience_style: str
    steps: tuple[str, ...]


def animation_speed_factor(speed: object) -> float:
    try:
        value = float(speed)
    except (TypeError, ValueError):
        value = 1.0
    return max(0.1, min(4.0, value))


def animation_duration(seconds: object, *, speed: object = 1.0, minimum: object = 0.05) -> float:
    try:
        base = float(seconds)
    except (TypeError, ValueError):
        base = 0.0
    try:
        minimum_value = float(minimum)
    except (TypeError, ValueError):
        minimum_value = 0.05
    return max(minimum_value, base / animation_speed_factor(speed))


def animation_started_active(started_at: object, duration: object, now: object, *, speed: object = 1.0) -> bool:
    try:
        started = float(started_at or 0.0)
        now_value = float(now)
    except (TypeError, ValueError):
        return False
    if started <= 0.0:
        return False
    return now_value - started < animation_duration(duration, speed=speed)


def animation_dict_active(animation: object, now: object, duration: object, *, speed: object = 1.0) -> bool:
    if not isinstance(animation, dict) or not animation:
        return False
    return animation_started_active(animation.get("started_at", 0.0), duration, now, speed=speed)


def tick_delta_seconds(now: object, last_tick_at: object, *, lower: float = 0.001, upper: float = 0.2) -> float:
    try:
        elapsed = float(now) - float(last_tick_at)
    except (TypeError, ValueError):
        elapsed = 0.0
    return max(float(lower), min(float(upper), elapsed))


def animation_tick_delta_seconds(now: object, last_tick_at: object, *, speed: object = 1.0) -> float:
    return tick_delta_seconds(now, last_tick_at) * animation_speed_factor(speed)


def render_frame_due(
    *,
    now: object,
    last_render_at: object,
    interval_seconds: object,
    tolerance_seconds: object = 0.0,
) -> bool:
    try:
        last_render = float(last_render_at or 0.0)
    except (TypeError, ValueError):
        last_render = 0.0
    if last_render <= 0.0:
        return True
    try:
        now_value = float(now)
        interval = float(interval_seconds)
    except (TypeError, ValueError):
        return True
    try:
        tolerance = float(tolerance_seconds or 0.0)
    except (TypeError, ValueError):
        tolerance = 0.0
    return now_value - last_render + tolerance >= interval


def tick_render_plan(
    *,
    visual_data_changed: bool,
    render_frame_due: bool,
    speed: object,
) -> TickRenderPlan:
    if not bool(visual_data_changed) and not bool(render_frame_due):
        return TickRenderPlan(should_render=False)
    speed_value = animation_speed_factor(speed)
    return TickRenderPlan(
        should_render=True,
        scroll_phase_delta=0.017 * speed_value,
        ambient_phase_delta=0.024 * speed_value,
    )


def _coerce_phase_delta(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def tick_frame_execution_plan(
    *,
    active_visual_plan: ActiveVisualAnimationPlan,
    render_plan: TickRenderPlan,
    update_music_wave_due: bool = True,
) -> TickFrameExecutionPlan:
    active = bool(getattr(active_visual_plan, "active", False))
    reason = str(getattr(active_visual_plan, "reason", "") or "") if active else ""
    should_render = bool(getattr(render_plan, "should_render", False))
    if not should_render:
        return TickFrameExecutionPlan(
            active_animation=active,
            active_reason=reason,
            retune_frame_timer=True,
            update_music_wave=bool(update_music_wave_due),
        )
    return TickFrameExecutionPlan(
        active_animation=active,
        active_reason=reason,
        retune_frame_timer=True,
        should_render=True,
        scroll_phase_delta=_coerce_phase_delta(getattr(render_plan, "scroll_phase_delta", 0.0)),
        ambient_phase_delta=_coerce_phase_delta(getattr(render_plan, "ambient_phase_delta", 0.0)),
        update_music_wave=bool(update_music_wave_due),
        mark_render_frame=True,
        update_widget=True,
    )


def barrage_hover_clock_plan(
    *,
    overlay_hovered: bool,
    pointer_hovered: bool,
    anim_dt: object,
) -> BarrageHoverClockPlan:
    hovered = bool(overlay_hovered) or bool(pointer_hovered)
    if hovered:
        return BarrageHoverClockPlan(hovered=True)
    try:
        delta = float(anim_dt)
    except (TypeError, ValueError):
        delta = 0.0
    return BarrageHoverClockPlan(hovered=False, clock_delta=max(0.0, delta))


def topmost_reinforcement_due(
    *,
    enabled: bool,
    now: object,
    last_reinforced_at: object,
    interval_seconds: object,
) -> bool:
    if not bool(enabled):
        return False
    try:
        return float(now) - float(last_reinforced_at or 0.0) > float(interval_seconds)
    except (TypeError, ValueError):
        return True


def scene_suppressed_tick_plan(*, render_frame_due: bool) -> SceneSuppressedTickPlan:
    return SceneSuppressedTickPlan(should_render=bool(render_frame_due))


def scene_suppressed_interaction_reset_plan() -> SceneSuppressedInteractionResetPlan:
    return SceneSuppressedInteractionResetPlan()


def scene_suppressed_frame_execution_plan(
    *,
    active_visual_plan: ActiveVisualAnimationPlan,
    render_plan: SceneSuppressedTickPlan,
) -> SceneSuppressedFrameExecutionPlan:
    active = bool(getattr(active_visual_plan, "active", False))
    reason = str(getattr(active_visual_plan, "reason", "") or "") if active else ""
    should_render = bool(getattr(render_plan, "should_render", False))
    if not should_render:
        return SceneSuppressedFrameExecutionPlan(
            active_animation=active,
            active_reason=reason,
            retune_frame_timer=True,
        )
    return SceneSuppressedFrameExecutionPlan(
        active_animation=active,
        active_reason=reason,
        retune_frame_timer=True,
        should_render=True,
        mark_render_frame=True,
        update_widget=True,
    )


def tick_follow_refresh_plan(
    *,
    active: bool,
    drag_candidate: bool,
    refresh_due: bool,
) -> TickFollowRefreshPlan:
    return TickFollowRefreshPlan(
        should_refresh=bool(active) and not bool(drag_candidate) and bool(refresh_due),
    )


def lyric_fallback_advance_plan(
    *,
    media_is_playing: bool,
    has_timed_lyrics: bool,
    lyric_count: object,
    current_index: object,
    lyric_changed_at: object,
    now: object,
    speed: object,
    interval_seconds: object = 2.45,
) -> LyricFallbackAdvancePlan:
    if not bool(media_is_playing) or bool(has_timed_lyrics):
        return LyricFallbackAdvancePlan()
    try:
        count = int(lyric_count)
    except (TypeError, ValueError):
        count = 0
    if count <= 1:
        return LyricFallbackAdvancePlan()
    try:
        changed_at = float(lyric_changed_at or 0.0)
        now_value = float(now)
        interval = float(interval_seconds) / animation_speed_factor(speed)
    except (TypeError, ValueError):
        return LyricFallbackAdvancePlan()
    if now_value - changed_at <= interval:
        return LyricFallbackAdvancePlan()
    try:
        index = int(current_index or 0)
    except (TypeError, ValueError):
        index = 0
    return LyricFallbackAdvancePlan(
        should_advance=True,
        previous_index=index,
        next_index=(index + 1) % count,
        changed_at=now_value,
    )


def music_lyric_tick_plan(
    *,
    media_is_playing: bool,
    has_timed_lyrics: bool,
    lyric_count: object,
    current_index: object,
    lyric_changed_at: object,
    now: object,
    speed: object,
) -> MusicLyricTickPlan:
    if not bool(media_is_playing):
        return MusicLyricTickPlan()
    if bool(has_timed_lyrics):
        return MusicLyricTickPlan(sync_timed_lyrics=True)
    return MusicLyricTickPlan(
        fallback_advance=lyric_fallback_advance_plan(
            media_is_playing=True,
            has_timed_lyrics=False,
            lyric_count=lyric_count,
            current_index=current_index,
            lyric_changed_at=lyric_changed_at,
            now=now,
            speed=speed,
        ),
    )


def tick_interaction_plan(
    *,
    hover_auto_activation_due: bool,
    plugin_panel_hover_open_due: bool,
    left_stack_collapse_due: bool,
    alarm_row_long_press_triggered: bool,
) -> TickInteractionPlan:
    open_plugin = bool(plugin_panel_hover_open_due)
    collapse_left = bool(left_stack_collapse_due)
    long_press = bool(alarm_row_long_press_triggered)
    return TickInteractionPlan(
        activate_hover_key=bool(hover_auto_activation_due),
        clear_hover_armed_key=bool(hover_auto_activation_due),
        open_plugin_panel=open_plugin,
        pulse_after_plugin_open=open_plugin,
        collapse_left_stack=collapse_left,
        clear_left_stack=collapse_left,
        pulse_after_left_collapse=collapse_left,
        render_after_alarm_long_press=long_press,
        stop_after_alarm_long_press=long_press,
    )


def _coerce_audio_bands(sampled_bands: object) -> tuple[float, float, float]:
    try:
        low, mid, high = sampled_bands  # type: ignore[misc]
    except (TypeError, ValueError):
        return (0.0, 0.0, 0.0)
    try:
        return (float(low), float(mid), float(high))
    except (TypeError, ValueError):
        return (0.0, 0.0, 0.0)


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def music_wave_input_snapshot(
    *,
    sampled_bands: object,
    live_audio_available: object,
) -> MusicWaveInputSnapshot:
    return MusicWaveInputSnapshot(
        sampled_bands=_coerce_audio_bands(sampled_bands),
        live_audio_available=bool(live_audio_available),
    )


def music_wave_update_plan(
    *,
    media_is_playing: bool,
    input_snapshot: object,
) -> MusicWaveUpdatePlan:
    snapshot = music_wave_input_snapshot(
        sampled_bands=getattr(input_snapshot, "sampled_bands", ()),
        live_audio_available=getattr(input_snapshot, "live_audio_available", False),
    )
    low_band, mid_band, high_band = snapshot.sampled_bands
    live_audio = snapshot.live_audio_available
    if not bool(media_is_playing) and not live_audio:
        low_band *= 0.24
        mid_band *= 0.18
        high_band *= 0.12
    return MusicWaveUpdatePlan(
        live_audio_available=live_audio,
        current_audio_bands=(low_band, mid_band, high_band),
    )


def music_wave_update_due(
    *,
    now: object,
    previous_updated_at: object,
    interval_seconds: object = 1.0 / 30.0,
    tolerance_seconds: object = 0.002,
) -> bool:
    try:
        current = float(now)
    except (TypeError, ValueError):
        return True
    try:
        previous = float(previous_updated_at or 0.0)
    except (TypeError, ValueError):
        previous = 0.0
    if previous <= 0.0:
        return True
    try:
        interval = max(0.0, float(interval_seconds))
    except (TypeError, ValueError):
        interval = 1.0 / 30.0
    try:
        tolerance = max(0.0, float(tolerance_seconds or 0.0))
    except (TypeError, ValueError):
        tolerance = 0.002
    return current - previous + tolerance >= interval


def music_wave_cadence_diagnostics(
    *,
    now: object,
    first_updated_at: object,
    last_updated_at: object,
    update_count: object,
    visual_frame_diagnostics: Mapping[str, object] | None,
    interval_seconds: object = 1.0 / 30.0,
    tolerance_seconds: object = 0.002,
) -> MusicWaveCadenceDiagnostics:
    try:
        now_value = float(now)
    except (TypeError, ValueError):
        now_value = 0.0
    try:
        first_at = float(first_updated_at or 0.0)
    except (TypeError, ValueError):
        first_at = 0.0
    try:
        last_at = float(last_updated_at or 0.0)
    except (TypeError, ValueError):
        last_at = 0.0
    try:
        count = max(0, int(update_count or 0))
    except (TypeError, ValueError):
        count = 0
    try:
        interval = max(0.0, float(interval_seconds))
    except (TypeError, ValueError):
        interval = 1.0 / 30.0
    try:
        tolerance = max(0.0, float(tolerance_seconds or 0.0))
    except (TypeError, ValueError):
        tolerance = 0.002

    frame = visual_frame_diagnostics if isinstance(visual_frame_diagnostics, Mapping) else {}
    render_count = _coerce_int(frame.get("frame_count"))
    render_average_fps = _coerce_float(frame.get("average_fps"))
    window = max(0.0, last_at - first_at) if first_at > 0.0 and last_at >= first_at else 0.0
    estimated_fps = (count / window) if window > 0.0 and count > 1 else 0.0
    ratio = (estimated_fps / render_average_fps) if render_average_fps > 0.0 else 0.0
    delta = render_average_fps - estimated_fps if render_average_fps > 0.0 and estimated_fps > 0.0 else 0.0
    age_ms = None
    if now_value > 0.0 and last_at > 0.0:
        age_ms = max(0.0, (now_value - last_at) * 1000.0)

    return MusicWaveCadenceDiagnostics(
        status="ready" if count > 0 and last_at > 0.0 else "missing",
        cadence_interval_ms=interval * 1000.0,
        cadence_tolerance_ms=tolerance * 1000.0,
        update_due=music_wave_update_due(
            now=now_value,
            previous_updated_at=last_at,
            interval_seconds=interval,
            tolerance_seconds=tolerance,
        ),
        update_count=count,
        first_updated_at=first_at,
        last_updated_at=last_at,
        update_age_ms=age_ms,
        update_window_seconds=window,
        estimated_update_fps=estimated_fps,
        render_frame_count=render_count,
        render_average_fps=render_average_fps,
        wave_to_render_fps_ratio=ratio,
        render_fps_delta=delta,
        cadence_limited=render_average_fps > 0.0 and estimated_fps > 0.0 and estimated_fps < render_average_fps * 0.85,
    )


def _normalized_music_ambience_style(ambience_style: object) -> str:
    return str(ambience_style or "sea_level").strip().lower() or "sea_level"


def music_background_layer_paint_plan(
    *,
    expanded: bool,
    ambience_style: object,
    lyric_color_fill_paints_background: bool = False,
) -> MusicBackgroundLayerPaintPlan:
    style = _normalized_music_ambience_style(ambience_style)
    steps: list[str] = []
    black_background = bool(lyric_color_fill_paints_background) or style in {"pure_black", "bilateral_diffraction"}
    if style == "pure_white":
        steps.append("pure_white_background")
    elif style == "solid_album":
        steps.append("solid_album_background")
    elif style == "yaoguang":
        steps.append("yaoguang_expanded_background" if bool(expanded) else "yaoguang_compact_background")
    elif black_background:
        steps.append("pure_black_background")
        if style == "bilateral_diffraction":
            steps.append("bilateral_diffraction_ambience")
    else:
        steps.extend(("glass_body", "bottom_ambience"))
        if style != "duotone_full":
            steps.append("blur_mask")
        steps.append("light_wave")
    return MusicBackgroundLayerPaintPlan(ambience_style=style, steps=tuple(steps))


def music_surface_paint_plan(
    *,
    expanded: bool,
    ambience_style: object,
    expanded_layout_style: object = "standard",
    lyric_color_fill_paints_background: bool = False,
) -> MusicSurfacePaintPlan:
    style = _normalized_music_ambience_style(ambience_style)
    layout_style = str(expanded_layout_style or "standard").strip().lower()
    if bool(expanded) and layout_style == "dual_panel":
        return MusicSurfacePaintPlan(
            ambience_style=style,
            steps=("dual_panel_background", "expanded_content", "border"),
        )

    background_plan = music_background_layer_paint_plan(
        expanded=expanded,
        ambience_style=style,
        lyric_color_fill_paints_background=lyric_color_fill_paints_background,
    )
    steps: list[str] = list(background_plan.steps)

    steps.extend(("reactive_overlay", "surface_transition"))
    if bool(expanded):
        steps.append("expanded_content")
    else:
        steps.append("compact_content")
        if style == "yaoguang":
            steps.append("yaoguang_compact_frame_overlay")
    steps.append("border")
    return MusicSurfacePaintPlan(ambience_style=style, steps=tuple(steps))


def active_visual_animation_plan(
    *,
    media_is_playing: bool = False,
    finder_mode: bool = False,
    drag_candidate: bool = False,
    dragging: bool = False,
    barrage_hovered: bool = False,
    hover_bounce_active: bool = False,
    stack_bounce_active: bool = False,
    taskbar_left_handle_active: bool = False,
    scene_transition_active: bool = False,
    surface_transition_active: bool = False,
    music_surface_transition_active: bool = False,
    lyric_index_transition_active: bool = False,
    lyric_kind_transition_active: bool = False,
    album_island_transition_active: bool = False,
    plugin_time_module_active: bool = False,
    plugin_alarm_editor_page_active: bool = False,
    plugin_alarm_editor_reflow_active: bool = False,
    plugin_alarm_editor_spin_active: bool = False,
    music_control_press_active: bool = False,
) -> ActiveVisualAnimationPlan:
    checks = (
        ("media", media_is_playing),
        ("finder", finder_mode),
        ("drag", bool(drag_candidate) or bool(dragging)),
        ("barrage_hover", barrage_hovered),
        ("hover_bounce", hover_bounce_active),
        ("stack_bounce", stack_bounce_active),
        ("taskbar_left_handle", taskbar_left_handle_active),
        ("scene_transition", scene_transition_active),
        ("surface_transition", surface_transition_active),
        ("music_surface_transition", music_surface_transition_active),
        ("lyric_index_transition", lyric_index_transition_active),
        ("lyric_kind_transition", lyric_kind_transition_active),
        ("album_island_transition", album_island_transition_active),
        ("plugin_time_module", plugin_time_module_active),
        ("plugin_alarm_editor_page", plugin_alarm_editor_page_active),
        ("plugin_alarm_editor_reflow", plugin_alarm_editor_reflow_active),
        ("plugin_alarm_editor_spin", plugin_alarm_editor_spin_active),
        ("music_control_press", music_control_press_active),
    )
    for reason, active in checks:
        if bool(active):
            return ActiveVisualAnimationPlan(active=True, reason=reason)
    return ActiveVisualAnimationPlan()


def scene_suppression_probe_due(
    *,
    now: object,
    last_checked_at: object,
    interval_seconds: object = 0.4,
) -> bool:
    try:
        elapsed = float(now) - float(last_checked_at)
        interval = max(0.0, float(interval_seconds))
    except (TypeError, ValueError):
        return True
    return elapsed >= interval


def scene_suppression_update_plan(
    *,
    current_suppressed: bool,
    detected_suppressed: bool,
    detected_reason: object,
    taskbar_compact_active: bool,
    current_transition_mode: object,
) -> SceneSuppressionUpdatePlan:
    reason = str(detected_reason or "")
    suppressed = bool(detected_suppressed)
    clear_scene_reason = False
    if bool(taskbar_compact_active) and reason in {"fullscreen", "maximized"}:
        suppressed = False
        reason = ""
        clear_scene_reason = True

    if suppressed == bool(current_suppressed):
        return SceneSuppressionUpdatePlan(
            scene_suppressed=suppressed,
            scene_reason=reason if suppressed else "",
            changed=False,
            clear_scene_reason=clear_scene_reason,
            clear_transition_mode=not suppressed and str(current_transition_mode or "") == "hide",
        )

    return SceneSuppressionUpdatePlan(
        scene_suppressed=suppressed,
        scene_reason=reason if suppressed else "",
        changed=True,
        clear_scene_reason=clear_scene_reason,
        transition_mode="hide" if suppressed else "show",
        transition_duration=0.18 if suppressed else 0.14,
        input_passthrough=suppressed,
        reset_hover_state=True,
        reset_left_stack=True,
        collapse_expanded=suppressed,
        clear_mask=True,
        update=True,
    )


def _elapsed_at_least(started_at: object, now: object, delay_seconds: object) -> bool:
    try:
        started = float(started_at or 0.0)
        now_value = float(now)
        delay = max(0.0, float(delay_seconds or 0.0))
    except (TypeError, ValueError):
        return False
    return now_value - started >= delay


def hover_auto_activation_candidate(
    *,
    hovered_key: object,
    expanded: bool,
    hover_armed_key: object,
    hover_started_at: object,
    now: object,
    delay_seconds: float = 1.0,
) -> bool:
    key = str(hovered_key or "")
    if not key or bool(expanded):
        return False
    if str(hover_armed_key or "") != key:
        return False
    return _elapsed_at_least(hover_started_at, now, delay_seconds)


def plugin_panel_hover_open_due(
    *,
    expanded: bool,
    hovered_key: object,
    plugin_panel_open: bool,
    hover_started_at: object,
    now: object,
    delay_seconds: float = 0.5,
) -> bool:
    if not bool(expanded):
        return False
    if str(hovered_key or "") != "plugin":
        return False
    if bool(plugin_panel_open):
        return False
    return _elapsed_at_least(hover_started_at, now, delay_seconds)


def left_stack_collapse_due(
    *,
    expanded: bool,
    left_stack_at: object,
    now: object,
    delay_seconds: float = 0.5,
) -> bool:
    if not bool(expanded):
        return False
    if not bool(left_stack_at):
        return False
    return _elapsed_at_least(left_stack_at, now, delay_seconds)


def normalize_empty_island_display_mode(mode: str) -> str:
    value = str(mode or "visible_bar").strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "hidden": "visible_bar",
        "hide": "visible_bar",
        "hidden_bar": "visible_bar",
        "bar": "visible_bar",
        "thin_bar": "visible_bar",
        "visible": "visible_bar",
        "show_bar": "visible_bar",
        "field": "text",
        "specified": "text",
        "specified_text": "text",
        "clock": "time",
    }
    value = aliases.get(value, value)
    return value if value in {"visible_bar", "text", "time"} else "visible_bar"


def normalize_no_music_island_priority(priority: str) -> str:
    value = str(priority or "plugin").strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "plugin_island": "plugin",
        "plugins": "plugin",
        "plugin_panel": "plugin",
        "empty_island": "empty",
        "empty_mode": "empty",
        "no_island": "empty",
        "none": "empty",
    }
    value = aliases.get(value, value)
    return value if value in {"plugin", "empty"} else "plugin"


def empty_island_can_display(*, music_enabled: bool, plugin_enabled: bool, no_music_priority: str) -> bool:
    if bool(music_enabled):
        return False
    if not bool(plugin_enabled):
        return True
    return normalize_no_music_island_priority(no_music_priority) == "empty"


def empty_island_text_active(
    *,
    display_mode: str,
    music_enabled: bool,
    plugin_enabled: bool,
    no_music_priority: str,
) -> bool:
    return normalize_empty_island_display_mode(display_mode) == "text" and empty_island_can_display(
        music_enabled=music_enabled,
        plugin_enabled=plugin_enabled,
        no_music_priority=no_music_priority,
    )


def empty_island_time_active(
    *,
    display_mode: str,
    music_enabled: bool,
    plugin_enabled: bool,
    no_music_priority: str,
) -> bool:
    return normalize_empty_island_display_mode(display_mode) == "time" and empty_island_can_display(
        music_enabled=music_enabled,
        plugin_enabled=plugin_enabled,
        no_music_priority=no_music_priority,
    )


def main_island_kind(
    *,
    music_enabled: bool,
    plugin_enabled: bool,
    no_music_priority: str,
    empty_display_mode: str,
    messages_present: bool,
) -> str:
    if bool(music_enabled):
        return "music"

    priority = normalize_no_music_island_priority(no_music_priority)
    display_mode = normalize_empty_island_display_mode(empty_display_mode)

    if bool(plugin_enabled) and priority == "plugin":
        return "plugin"
    if empty_island_text_active(
        display_mode=display_mode,
        music_enabled=False,
        plugin_enabled=plugin_enabled,
        no_music_priority=priority,
    ):
        return "empty_text"
    if empty_island_time_active(
        display_mode=display_mode,
        music_enabled=False,
        plugin_enabled=plugin_enabled,
        no_music_priority=priority,
    ):
        return "time"
    if bool(plugin_enabled) and display_mode == "visible_bar" and priority == "empty":
        return "thin_bar"
    if bool(plugin_enabled):
        return "plugin"
    if bool(messages_present):
        return "message"
    return "thin_bar"


def media_visual_track_changed(
    *,
    initial: bool,
    incoming_track_id: object,
    previous_track_id: object,
    same_visible_track: bool,
    message_refresh: bool,
) -> bool:
    changed = bool(initial) or str(incoming_track_id or "") != str(previous_track_id or "")
    if not changed:
        return False
    if not bool(initial) and bool(message_refresh) and bool(same_visible_track):
        return False
    return True


def folded_message_limit(
    *,
    taskbar_compact_active: bool,
    main_kind: str,
    music_enabled: bool,
    plugin_enabled: bool,
) -> int:
    if bool(taskbar_compact_active):
        return 3
    kind = str(main_kind or "").strip().casefold()
    if kind == "message" and not bool(music_enabled) and not bool(plugin_enabled):
        return 5
    return 3


def normalize_fullscreen_plugin_alert_style(style: object) -> str:
    value = str(style or "toast").strip().casefold()
    value = {
        "island": "toast",
        "popup": "toast",
        "pop": "toast",
    }.get(value, value)
    return value if value in {"toast", "barrage", "fullscreen"} else "toast"


def normalize_barrage_display_mode(mode: object) -> str:
    value = str(mode or "fullscreen").strip().casefold()
    aliases = {
        "any": "always",
        "anytime": "always",
        "all": "always",
        "on": "always",
        "full": "fullscreen",
        "fullscreen_only": "fullscreen",
        "only_fullscreen": "fullscreen",
    }
    value = aliases.get(value, value)
    return value if value in {"always", "fullscreen"} else "fullscreen"


def barrage_preview_active(
    *,
    previews_present: bool,
    settings_window_active: bool,
    now: float,
    force_anytime_until: float,
    last_preview_at: float,
    duration_seconds: float = 6.2,
) -> bool:
    if not bool(previews_present):
        return False
    if bool(settings_window_active):
        return True
    now_value = float(now)
    if now_value <= float(force_anytime_until or 0.0):
        return True
    return now_value - float(last_preview_at or 0.0) <= max(0.0, float(duration_seconds or 0.0))


def barrage_overlay_context_active(
    *,
    preview_active: bool,
    display_mode: str,
    fullscreen_overlay_active: bool,
) -> bool:
    if bool(preview_active):
        return True
    if normalize_barrage_display_mode(display_mode) == "always":
        return True
    return bool(fullscreen_overlay_active)


def active_barrage_messages(
    *,
    preview_active: bool,
    messages: object,
    previews: object,
    fullscreen_messages: object,
) -> list[object]:
    preview_items = list(previews or [])
    fullscreen_items = list(fullscreen_messages or [])
    if bool(preview_active) and preview_items:
        return preview_items + fullscreen_items
    return list(messages or []) + preview_items + fullscreen_items


def barrage_alive_messages_for_clock(
    messages: object,
    clock: float,
    *,
    ttl_seconds: float = 14.0,
) -> list[object]:
    clock_value = float(clock)
    ttl = max(0.0, float(ttl_seconds or 0.0))
    return [
        message
        for message in list(messages or [])
        if clock_value - float(getattr(message, "barrage_started_at", 0.0) or 0.0) <= ttl
    ]


def barrage_overlay_state(
    *,
    preview_only: bool,
    preview_active: bool,
    context_active: bool,
    messages: object,
    previews: object,
    fullscreen_messages: object,
    clock: float,
) -> dict[str, object] | None:
    preview_items = list(previews or [])
    if bool(preview_only) and not bool(preview_active):
        return None
    if bool(preview_only):
        active = preview_items
    else:
        if not bool(context_active):
            return None
        active = active_barrage_messages(
            preview_active=preview_active,
            messages=messages,
            previews=preview_items,
            fullscreen_messages=fullscreen_messages,
        )

    alive = barrage_alive_messages_for_clock(active, float(clock))
    if not alive:
        return None
    preview_prefix = "preview-" if bool(preview_only) or bool(preview_active) else ""
    return {
        "style": "barrage",
        "alert": None,
        "action_key": "",
        "alert_key": f"{preview_prefix}barrage:{len(alive)}",
        "messages": list(alive),
        "clock": float(clock),
    }


def fullscreen_plugin_barrage_update_action(
    *,
    context_active: bool,
    alert_style: object,
    has_alert: bool,
    alert_key: str,
    previous_alert_key: str,
    has_existing_messages: bool,
) -> str:
    if not bool(context_active) or normalize_fullscreen_plugin_alert_style(alert_style) != "barrage":
        return "clear"
    if not bool(has_alert):
        return "clear"
    key = str(alert_key or "")
    previous = str(previous_alert_key or "")
    if key and key == previous and bool(has_existing_messages):
        return "keep"
    return "replace"


def fullscreen_plugin_alert_overlay_state(
    *,
    context_active: bool,
    alerts: object,
    style: object,
    suppressed_alert_keys: object = (),
) -> dict[str, object] | None:
    if not bool(context_active):
        return None
    alert_items = list(alerts or [])
    if not alert_items:
        return None
    alert = alert_items[0]
    action_key = fullscreen_plugin_alert_action_key(alert)
    if not action_key:
        return None
    alert_key = fullscreen_plugin_alert_key(alert)
    suppressed = {str(key) for key in list(suppressed_alert_keys or [])}
    if alert_key in suppressed:
        return None
    return {
        "style": normalize_fullscreen_plugin_alert_style(style),
        "alert": alert,
        "action_key": action_key,
        "alert_key": alert_key,
    }


def plugin_alert_overlay_state(fullscreen_state: object) -> dict[str, object] | None:
    if not isinstance(fullscreen_state, dict):
        return None
    style = str(fullscreen_state.get("style", "toast") or "toast")
    if normalize_fullscreen_plugin_alert_style(style) == "barrage":
        return None
    return dict(fullscreen_state)


def fullscreen_plugin_alert_key(alert: object) -> str:
    try:
        _index, label, detail, summary, _color, _icon, plugin_id, status, alert_title, alert_body, priority = alert
    except (TypeError, ValueError):
        return ""
    return "|".join(str(part or "") for part in (plugin_id, label, detail, summary, status, alert_title, alert_body, priority))


def fullscreen_plugin_alert_action_key(alert: object) -> str:
    try:
        _index, label, _detail, _summary, _color, _icon, plugin_id, _status, _alert_title, _alert_body, _priority = alert
    except (TypeError, ValueError):
        return ""
    target = str(plugin_id or label or "").strip().casefold()
    return f"plugin-alert:{target}" if target else ""


def fullscreen_plugin_alert_is_reminder(alert: object) -> bool:
    try:
        _index, label, _detail, _summary, _color, _icon, _plugin_id, status, alert_title, alert_body, _priority = alert
    except (TypeError, ValueError):
        return False
    status_key = str(status or "").strip().casefold()
    if status_key in {"running", "loading", "soon", "paused", "normal", ""}:
        return False
    label_key = str(label or "").strip().upper()
    if label_key == "TIME":
        return status_key in {"done", "due", "alarm"}
    return status_key in {"done", "due", "alarm", "error", "severe", "rain"} or bool(alert_title or alert_body)


def fullscreen_overlay_context_active(
    *,
    preview_active: bool,
    settings_window_active: bool,
    scene_suppressed: bool,
    last_scene_probe_reason: object,
    scene_reason: object,
) -> bool:
    if bool(preview_active):
        return True
    if bool(settings_window_active):
        return True
    if bool(scene_suppressed):
        return True
    reason = str(last_scene_probe_reason or scene_reason or "")
    return reason in {"fullscreen", "maximized", "settings"}


def fullscreen_plugin_alert_suppression_state(
    entries: object,
    alert_key: str,
    now: float,
) -> tuple[bool, dict[str, float]]:
    key = str(alert_key or "").strip()
    if not key or not isinstance(entries, dict):
        return False, {}

    kept: dict[str, float] = {}
    suppressed = False
    for item_key, until in entries.items():
        try:
            until_value = float(until)
        except (TypeError, ValueError):
            continue
        item_key_text = str(item_key)
        if until_value > float(now):
            kept[item_key_text] = until_value
            if item_key_text == key:
                suppressed = True
    return suppressed, kept


def message_identity_key(message: object) -> str:
    serial = int(getattr(message, "serial", 0) or 0)
    app = getattr(message, "app", "")
    sender = getattr(message, "sender", "")
    if serial:
        return f"{serial}-{app}-{sender}"
    created_at = float(getattr(message, "created_at", 0.0) or 0.0)
    return f"{int(created_at * 1000)}-{app}-{sender}"


def message_key_from_hit(key: str) -> str:
    text = str(key or "")
    if text.startswith("message-dot:") or text.startswith("message:"):
        return text.split(":", 1)[1]
    return ""


def message_hit_key(key: str) -> bool:
    text = str(key or "")
    return text.startswith("message:") or text.startswith("message-dot:")


def barrage_action_key_for_message(message: object) -> str:
    action_key = str(getattr(message, "action_key", "") or "").strip()
    if action_key:
        return action_key
    return f"message:{message_identity_key(message)}"
