from __future__ import annotations

from dataclasses import dataclass, field
import math

from PySide6.QtCore import QPoint, QPointF, QRectF


@dataclass(frozen=True)
class TaskbarFollowTarget:
    screen: object
    visible_taskbar: QRectF
    follow_taskbar: QRectF
    track_rect: QRectF
    zone_rect: QRectF
    window_pos: QPoint
    main_kind: object
    stack_width: float
    main_width: float
    main_height: float
    main_top: float
    direction: int
    position_ratio: float
    hidden_autohide: bool
    auto_hide_follow: bool
    partial_shell_follow: bool


@dataclass(frozen=True)
class TaskbarFollowRefreshPlan:
    valid: bool
    animate_follow: bool = False
    refresh_material: bool = False


@dataclass(frozen=True)
class TaskbarAboveFollowRefreshPlan:
    action: str
    clear_above_taskbar_follow: bool = False
    force_location_mode: str = ""
    settle_above_taskbar: bool = False
    anchor_rect: QRectF = field(default_factory=QRectF)


@dataclass(frozen=True)
class TaskbarDockPlan:
    valid: bool
    visible_taskbar: QRectF
    target_center_x: float = 0.0
    mark_follow_force_visible: bool = False
    force_visible: bool = False
    use_current_visibility: bool = False
    refresh_material: bool = False
    animate: bool = False
    dock: bool = False
    consume_dock_animation_request: bool = False


@dataclass(frozen=True)
class TaskbarDragTarget:
    taskbar_rect: QRectF
    track_rect: QRectF
    zone_rect: QRectF
    window_pos: QPoint
    main_kind: object
    stack_width: float
    main_width: float
    main_height: float
    main_top: float
    direction: int
    position_ratio: float
    manual_width: float = 0.0


@dataclass(frozen=True)
class StandaloneAboveTaskbarTarget:
    visible_taskbar: QRectF
    follow_taskbar: QRectF
    track_rect: QRectF
    window_pos: QPoint
    main_kind: object
    stack_width: float
    main_width: float
    main_height: float
    main_top: float
    main_left: float
    direction: int
    position_ratio: float
    gap: float
    hidden_autohide: bool
    auto_hide_follow: bool
    partial_shell_follow: bool
    animate_follow: bool


@dataclass(frozen=True)
class StandalonePlacementTarget:
    window_pos: QPoint
    main_kind: object
    stack_width: float
    main_width: float
    main_height: float
    main_top: float
    main_left: float
    direction: int


@dataclass(frozen=True)
class StandaloneReleaseDecision:
    action: str
    exiting_edge_notch_mode: bool = False


@dataclass(frozen=True)
class StandaloneReleaseExecutionPlan:
    action: str
    execute_top_notch: bool = False
    execute_taskbar_dock: bool = False
    execute_above_taskbar: bool = False
    execute_free_placement: bool = False
    clear_above_taskbar_follow: bool = False
    set_location_mode: str = ""
    request_taskbar_dock_animation: bool = False
    force_location_mode: str = ""
    force_finder_mode: bool | None = None
    force_attach_to_real_finder: bool | None = None
    force_taskbar_fusion_mode: str = ""
    clear_taskbar_preview: bool = False

    @property
    def has_adapter_action(self) -> bool:
        return (
            self.execute_top_notch
            or self.execute_taskbar_dock
            or self.execute_above_taskbar
            or self.execute_free_placement
        )


@dataclass(frozen=True)
class MouseReleaseCleanupPlan:
    clear_taskbar_preview: bool = False
    clear_taskbar_selected_drop_zone: bool = False
    clear_taskbar_attach_hot_zone: bool = False
    clear_taskbar_drop_zones: bool = False
    hide_taskbar_drop_overlay: bool = False


@dataclass(frozen=True)
class MouseReleaseActionPlan:
    action: str
    reset_drag_state: bool = True
    save_placement_state: bool = True
    activate_pressed_key: bool = False
    update_after_activation: bool = False
    capture_release_anchor: bool = False
    recheck_taskbar_preview: bool = False
    accept_taskbar_drop: bool = False


@dataclass(frozen=True)
class MouseReleaseStandaloneSettlePlan:
    check_tasker_detach_above_taskbar: bool = False
    settle_standalone_release: bool = False


@dataclass(frozen=True)
class TaskerReleaseDetachPlan:
    should_detach: bool = False


@dataclass(frozen=True)
class TaskbarPresencePlan:
    inside: bool
    taskbar_preview_visible: bool
    set_location_mode: str = ""
    clear_taskbar_selected_drop_zone: bool = False
    clear_taskbar_attach_hot_zone: bool = False
    clear_taskbar_drop_zones: bool = False
    hide_taskbar_drop_overlay: bool = False
    sync_taskbar_drop_overlay: bool = False


@dataclass(frozen=True)
class TaskbarPreviewDragPlan:
    prompt_visible: bool
    active: bool
    taskbar_attach_hot_zone: QRectF
    taskbar_attach_hot_zone_host_rect: QRectF
    taskbar_attach_hot_zone_active: bool
    needs_drop_zones: bool
    taskbar_drop_zones: tuple[QRectF, ...]
    taskbar_selected_drop_zone: QRectF
    last_taskbar_rect: QRectF
    update_taskbar_material: bool


@dataclass(frozen=True)
class TaskbarPreviewDropAcceptancePlan:
    accepted: bool
    set_location_mode: str = ""
    request_taskbar_dock_animation: bool = False
    clear_taskbar_attach_hot_zone: bool = False
    hide_taskbar_drop_overlay: bool = False


@dataclass(frozen=True)
class TaskbarDragActionPlan:
    action: str
    remember_motion_global_rects: bool = False
    leave_taskbar_presence: bool = False
    set_location_mode: str = ""
    move_standalone_drag: bool = False
    update_taskbar_preview: bool = False
    move_taskbar_drag: bool = False
    resize_taskbar_left_handle: bool = False


@dataclass(frozen=True)
class DragUpdateEntryPlan:
    action: str
    start_drag: bool = False
    collapse_expanded: bool = False
    route_finder_drag: bool = False
    route_standard_drag: bool = False
    pulse_stack: bool = False


@dataclass(frozen=True)
class FinderDragActionPlan:
    action: str
    detach_to_free: bool = False
    follow_finder: bool = False
    move_standalone_after_detach: bool = False
    pulse_stack_after_detach: bool = False


@dataclass(frozen=True)
class FinderAttachPlan:
    should_attach: bool = False


@dataclass(frozen=True)
class StandaloneDragDirectionPlan:
    direction: int
    update_auto_direction: bool = False
    auto_direction: int = 0


@dataclass(frozen=True)
class LocationModeTransitionPlan:
    mode: str
    previous_mode: str
    mode_changed: bool
    begin_surface_transition_reason: str
    reset_runtime_state: bool
    reset_clear_surface: bool
    finder_mode: bool
    attach_to_real_finder: bool
    taskbar_above_follow_active: bool
    taskbar_fusion_mode: str
    taskbar_preview_visible: bool
    clear_scene_state: bool
    clear_mask: bool
    refresh_finder_material: bool
    refresh_taskbar_follow_position: bool
    refresh_taskbar_above_follow_position: bool
    settle_top_notch: bool
    sync_taskbar_color: bool


@dataclass(frozen=True)
class TaskbarAutoHideModePlan:
    mode: str
    reset_follow_cache: bool
    clear_above_taskbar_follow: bool
    force_location_mode: str
    force_finder_mode: bool | None
    force_attach_to_real_finder: bool | None
    force_taskbar_fusion_mode: str
    clear_taskbar_preview: bool
    clear_taskbar_drop_state: bool
    hide_taskbar_drop_overlay: bool


@dataclass(frozen=True)
class TaskbarFusionModePlan:
    mode: str
    force_expanded: bool | None
    force_plugin_panel_open: bool | None
    taskbar_preview_visible_before_location_change: bool | None
    target_location_mode: str


@dataclass(frozen=True)
class TaskbarAutohideHiddenPlan:
    auto_hide: bool
    hidden: bool
    probe_taskbar: QRectF


@dataclass(frozen=True)
class TaskbarCurrentVisibilityFollowPlan:
    follow_rect: QRectF
    base_rect: QRectF
    hidden_autohide: bool


@dataclass(frozen=True)
class TaskbarVisibilityFollowPlan:
    follow_rect: QRectF
    hidden_autohide: bool


@dataclass(frozen=True)
class TaskbarAutohideInteractionPlan:
    keeps_visible: bool
    interaction_visible: bool
    click_hold_visible: bool
    mouse_pressed: bool
    left_taskbar_at: float


def position_ratio_for_center(track_rect: QRectF, main_width: float, center_x: float) -> float:
    track = QRectF(track_rect)
    if track.isNull():
        return 0.5
    half = max(0.0, float(main_width) / 2.0)
    left_center = track.left() + half
    right_center = track.right() - half
    if right_center <= left_center:
        return 0.5
    clamped = max(left_center, min(right_center, float(center_x)))
    return max(0.0, min(1.0, (clamped - left_center) / (right_center - left_center)))


def center_for_position_ratio(track_rect: QRectF, main_width: float, ratio: float) -> float:
    track = QRectF(track_rect)
    if track.isNull():
        return 0.0
    half = max(0.0, float(main_width) / 2.0)
    left_center = track.left() + half
    right_center = track.right() - half
    if right_center <= left_center:
        return track.center().x()
    value = max(0.0, min(1.0, float(ratio)))
    return left_center + (right_center - left_center) * value


def intended_zone_rect(center_x: float, center_y: float, main_width: float, main_height: float) -> QRectF:
    return QRectF(
        float(center_x) - float(main_width) / 2.0,
        float(center_y) - float(main_height) / 2.0,
        float(main_width),
        float(main_height),
    )


def taskbar_drag_screen_ratio(global_pos: QPoint | QPointF, screen_geometry) -> float:
    try:
        geo = QRectF(screen_geometry)
    except Exception:
        return 0.0
    if geo.isNull():
        return 0.0
    height = max(1.0, float(geo.height()))
    try:
        y = float(global_pos.y())
    except Exception:
        return 0.0
    return max(0.0, min(1.0, (y - float(geo.top())) / height))


def taskbar_contacting_rect(main_rect: QRectF, taskbar_rect: QRectF, *, slop: float = 4.0) -> bool:
    main = QRectF(main_rect)
    taskbar = QRectF(taskbar_rect)
    if main.isNull() or taskbar.isNull():
        return False
    margin = max(0.0, float(slop))
    return taskbar.adjusted(-margin, -margin, margin, margin).intersects(main)


def should_show_taskbar_preview(
    lower_third_ratio: float,
    *,
    taskbar_allowed: bool,
    recognition_threshold: float,
    location_mode: str,
) -> bool:
    if not bool(taskbar_allowed):
        return False
    if str(location_mode or "free") not in {"free", "tasker"}:
        return False
    try:
        ratio = float(lower_third_ratio)
    except (TypeError, ValueError):
        ratio = 0.0
    try:
        threshold = float(recognition_threshold)
    except (TypeError, ValueError):
        threshold = 0.66
    return ratio >= threshold


def should_show_taskbar_preview_for_rect(
    lower_third_ratio: float,
    main_rect: QRectF,
    taskbar_rect: QRectF,
    *,
    taskbar_allowed: bool,
    recognition_threshold: float,
    location_mode: str,
    slop: float = 4.0,
) -> bool:
    return should_show_taskbar_preview(
        lower_third_ratio,
        taskbar_allowed=taskbar_allowed,
        recognition_threshold=recognition_threshold,
        location_mode=location_mode,
    ) and taskbar_contacting_rect(main_rect, taskbar_rect, slop=slop)


def select_taskbar_drag_track_rect(taskbar_rect: QRectF) -> QRectF:
    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull():
        return QRectF()
    return taskbar


def select_taskbar_track_rect(taskbar_rect: QRectF, task_area_rect: QRectF | None = None) -> QRectF:
    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull():
        return QRectF()
    task_area = QRectF(task_area_rect) if task_area_rect is not None else QRectF()
    return task_area if not task_area.isNull() else taskbar


def coerce_release_point(release_global_pos) -> QPoint | None:
    try:
        return QPoint(
            int(round(float(release_global_pos.x()))),
            int(round(float(release_global_pos.y()))),
        )
    except Exception:
        return None


def standalone_release_probe_point(anchor_rect: QRectF, release_global_pos=None) -> QPoint | None:
    release_point = coerce_release_point(release_global_pos)
    if release_point is not None:
        return release_point
    anchor = QRectF(anchor_rect)
    if anchor.isNull():
        return None
    return QPoint(int(round(anchor.center().x())), int(round(anchor.center().y())))


def hidden_autohide_follow_rect(screen_geometry, taskbar_rect: QRectF) -> QRectF:
    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull():
        return taskbar
    try:
        geo = QRectF(screen_geometry)
    except Exception:
        geo = QRectF()
    if taskbar.width() >= taskbar.height():
        dy = taskbar.height() if geo.isNull() or taskbar.center().y() >= geo.center().y() else -taskbar.height()
        taskbar.translate(0.0, dy)
    else:
        dx = taskbar.width() if geo.isNull() or taskbar.center().x() >= geo.center().x() else -taskbar.width()
        taskbar.translate(dx, 0.0)
    return taskbar


def taskbar_follow_rect_key(rect: QRectF) -> tuple[int, int, int, int]:
    value = QRectF(rect)
    if value.isNull():
        return (0, 0, 0, 0)
    return (
        int(round(value.left())),
        int(round(value.top())),
        int(round(value.width())),
        int(round(value.height())),
    )


def taskbar_follow_geometry_key(
    target: TaskbarFollowTarget,
    *,
    window_width: float,
    window_height: float,
    location_mode: str,
    expanded: bool,
    main_kind: object,
) -> tuple[object, ...]:
    return (
        id(target.screen),
        taskbar_follow_rect_key(target.follow_taskbar),
        taskbar_follow_rect_key(target.track_rect),
        taskbar_follow_rect_key(target.zone_rect),
        int(round(float(target.main_top))),
        int(target.direction),
        int(target.window_pos.x()),
        int(target.window_pos.y()),
        int(round(float(window_width))),
        int(round(float(window_height))),
        str(location_mode or "free"),
        bool(expanded),
        str(main_kind),
    )


def clamp_taskbar_window_pos(
    new_pos: QPoint,
    main_top: float,
    main_width: float,
    main_height: float,
    screen_geometry,
    window_width: float,
    bottom_extension: float = 0.0,
) -> QPoint:
    main_left = float(window_width) / 2.0 - float(main_width) / 2.0
    screen = QRectF(screen_geometry)
    left = float(screen.left())
    top = float(screen.top())
    right = left + float(screen.width())
    bottom = top + float(screen.height()) + max(0.0, float(bottom_extension))
    x = float(new_pos.x())
    y = float(new_pos.y())
    min_x = left - main_left
    max_x = right - main_left - float(main_width)
    min_y = top - float(main_top)
    max_y = bottom - float(main_top) - float(main_height)
    if min_x <= max_x:
        x = max(min_x, min(max_x, x))
    if min_y <= max_y:
        y = max(min_y, min(max_y, y))
    return QPoint(int(round(x)), int(round(y)))


def standalone_stack_width_for_limit(
    target_stack_width: float,
    width_limit: float,
    min_factor: float = 0.5,
    min_width: float | None = None,
) -> float:
    target = float(target_stack_width)
    minimum = max(0.0, float(min_width)) if min_width is not None else target * max(0.0, float(min_factor))
    return max(minimum, min(target, float(width_limit)))


def standalone_width_limit_for_global_center(
    global_center_x: float,
    screen_geometry,
    target_width: float,
    *,
    min_width: float = 120.0,
    side_margin: float = 28.0,
) -> float:
    try:
        left = float(screen_geometry.left())
        right = float(screen_geometry.right())
    except Exception:
        return float(target_width)
    center = float(global_center_x)
    left_space = max(0.0, center - left)
    right_space = max(0.0, right - center)
    limit = min(left_space, right_space) * 2.0 - float(side_margin)
    minimum = max(0.0, float(min_width))
    return max(minimum, min(float(target_width), limit if limit > 0.0 else minimum))


def empty_bar_orientation(location_mode: str | None) -> str:
    return "vertical" if str(location_mode or "").strip().casefold() == "tasker" else "horizontal"


def empty_bar_size(
    location_mode: str | None = None,
    *,
    orientation: str | None = None,
    unit: float = 1.0,
) -> tuple[float, float]:
    u = max(0.0, float(unit))
    direction = str(orientation or empty_bar_orientation(location_mode)).strip().casefold()
    if direction == "vertical":
        return 7.0 * u, 44.0 * u
    return 122.0 * u, 12.0 * u


def standalone_stack_center_x(
    *,
    finder_mode: bool,
    window_width: float,
    content_width: float,
    center_ratio: float = 0.5,
    unit: float = 1.0,
) -> float:
    width = float(window_width)
    if not bool(finder_mode):
        return width / 2.0
    content = float(content_width)
    u = max(0.0, float(unit))
    if content >= width - 48.0 * u:
        return width / 2.0
    margin = max(content / 2.0 + 24.0 * u, 68.0 * u)
    ratio = max(0.06, min(0.94, float(center_ratio)))
    return max(margin, min(width - margin, width * ratio))


def standalone_main_top_for_direction(
    *,
    finder_stack_top_y: float,
    window_height: float,
    main_height: float,
    direction: int,
    finder_mode: bool,
    thin_bar: bool = False,
    thin_bar_top_lift: float = 4.0,
) -> float:
    top_y = float(finder_stack_top_y)
    if bool(thin_bar):
        top_y = max(0.0, top_y - float(thin_bar_top_lift))
    if bool(finder_mode) or int(direction) >= 0:
        return top_y
    return max(0.0, float(window_height) - top_y - float(main_height))


def stack_rect_for_direction(
    *,
    center_x: float,
    cursor_edge: float,
    width: float,
    height: float,
    direction: int,
    gap: float,
) -> tuple[QRectF, float]:
    left = float(center_x) - float(width) / 2.0
    if int(direction) >= 0:
        rect = QRectF(left, float(cursor_edge), float(width), float(height))
        return rect, rect.bottom() + float(gap)
    rect = QRectF(left, float(cursor_edge) - float(height), float(width), float(height))
    return rect, rect.top() - float(gap)


def canonical_position_preset(preset: str) -> str:
    value = str(preset or "last").strip().lower()
    if value in {"", "custom"}:
        value = "last"
    if value not in {"last", "top_center", "top_left", "top_right", "bottom_left", "bottom_right"}:
        value = "last"
    return value


def clamp_standalone_window_pos(
    new_x: float,
    new_y: float,
    main_left: float,
    main_top: float,
    main_width: float,
    main_height: float,
    screen_geometry,
    margin: float = 6.0,
    bottom_extension: float = 0.0,
) -> QPoint:
    try:
        left_edge = float(screen_geometry.left())
        right_edge = float(screen_geometry.right())
        top_edge = float(screen_geometry.top())
        bottom_edge = float(screen_geometry.bottom())
    except Exception:
        screen = QRectF(screen_geometry)
        left_edge = float(screen.left())
        right_edge = float(screen.right())
        top_edge = float(screen.top())
        bottom_edge = float(screen.bottom())
    x = float(new_x)
    y = float(new_y)
    min_x = left_edge + float(margin) - float(main_left)
    max_x = right_edge - float(margin) - float(main_left) - float(main_width)
    min_y = top_edge + float(margin) - float(main_top)
    max_y = (
        bottom_edge
        + max(0.0, float(bottom_extension))
        - float(margin)
        - float(main_top)
        - float(main_height)
    )
    if min_x <= max_x:
        x = max(min_x, min(max_x, x))
    if min_y <= max_y:
        y = max(min_y, min(max_y, y))
    return QPoint(int(round(x)), int(round(y)))


def nudge_window_pos_outside_taskbar(
    new_pos: QPoint,
    taskbar_rect: QRectF,
    main_top: float,
    main_height: float,
    direction: int,
    gap: float = 0.0,
) -> QPoint:
    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull():
        return QPoint(new_pos)
    y = int(new_pos.y())
    seam = max(0.0, float(gap))
    if int(direction) < 0:
        allowed_bottom = float(taskbar.top()) - seam
        if float(y) + float(main_top) + float(main_height) > allowed_bottom:
            y = int(math.floor(allowed_bottom - float(main_top) - float(main_height)))
    else:
        allowed_top = float(taskbar.bottom()) + seam
        if float(y) + float(main_top) < allowed_top:
            y = int(math.ceil(allowed_top - float(main_top)))
    return QPoint(int(new_pos.x()), y)


def taskbar_above_gap(taskbar_rect: QRectF | None = None) -> float:
    base = 4.0
    if taskbar_rect is None:
        return base
    try:
        taskbar = QRectF(taskbar_rect)
        thickness = min(float(taskbar.width()), float(taskbar.height()))
    except Exception:
        thickness = 0.0
    if thickness <= 0.0:
        return base
    return max(base, min(8.0, thickness * 0.08))


def stick_taskbar_above_gap(taskbar_rect: QRectF | None = None, hidden_autohide: bool = False) -> float:
    if bool(hidden_autohide):
        return 0.0
    return taskbar_above_gap(taskbar_rect) + 4.0


def taskbar_autohide_visible_follow_lift(taskbar_rect: QRectF, screen_geometry) -> float:
    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull() or screen_geometry is None:
        return 0.0
    try:
        geo = QRectF(screen_geometry)
    except Exception:
        geo = QRectF()
    if taskbar.width() < taskbar.height():
        return 0.0
    if not geo.isNull() and taskbar.center().y() < geo.center().y():
        return 0.0
    return taskbar_above_gap(taskbar)


def taskbar_bottom_boundary_extension(
    taskbar_rect: QRectF,
    screen_geometry,
    *,
    auto_hide_enabled: bool,
) -> float:
    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull() or not bool(auto_hide_enabled):
        return 0.0
    try:
        geo = QRectF(screen_geometry)
    except Exception:
        return 0.0
    if geo.isNull():
        return 0.0
    if taskbar.width() < taskbar.height():
        return 0.0
    if taskbar.center().y() < geo.center().y():
        return 0.0
    return max(0.0, float(taskbar.height()))


def select_bottom_taskbar_rect(
    taskbar_rect: QRectF | None,
    screen_geometry,
    fallback_rect: QRectF | None = None,
) -> QRectF:
    taskbar = QRectF(taskbar_rect) if taskbar_rect is not None else QRectF()
    if taskbar.isNull():
        taskbar = QRectF(fallback_rect) if fallback_rect is not None else QRectF()
    if taskbar.isNull():
        return QRectF()
    if taskbar.width() < taskbar.height():
        return QRectF()
    try:
        geo = QRectF(screen_geometry) if screen_geometry is not None else QRectF()
    except Exception:
        geo = QRectF()
    if not geo.isNull() and taskbar.center().y() < geo.center().y():
        return QRectF()
    return taskbar


def standalone_bottom_boundary_extension(
    taskbar_rect: QRectF | None,
    screen_geometry,
    *,
    fallback_rect: QRectF | None = None,
    auto_hide_enabled: bool,
) -> float:
    bottom_taskbar = select_bottom_taskbar_rect(taskbar_rect, screen_geometry, fallback_rect)
    if bottom_taskbar.isNull():
        return 0.0
    return taskbar_bottom_boundary_extension(
        bottom_taskbar,
        screen_geometry,
        auto_hide_enabled=auto_hide_enabled,
    )


def taskbar_stack_direction_for_rect(taskbar_rect: QRectF, screen_geometry) -> int:
    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull():
        return 1
    if taskbar.width() >= taskbar.height() and screen_geometry is not None:
        try:
            screen = QRectF(screen_geometry)
        except Exception:
            screen = QRectF()
        if not screen.isNull():
            return -1 if taskbar.center().y() >= screen.center().y() else 1
    return 1


def available_location_modes_for_state(*, mydockfinder_available: bool) -> list[str]:
    if bool(mydockfinder_available):
        return ["free", "finder"]
    return ["top", "free", "tasker"]


def canonical_location_mode(
    mode: str,
    *,
    mydockfinder_available: bool,
    taskbar_auto_hide_mode: str = "auto",
) -> str:
    value = str(mode or "free").strip().lower()
    legacy = {
        "off": "free",
        "preview": "free",
        "compact": "tasker",
        "expanded": "tasker",
        "taskbar": "tasker",
    }
    value = legacy.get(value, value)
    if value not in {"finder", "free", "top", "tasker", "stick"}:
        value = "top"
    if value == "finder" and not bool(mydockfinder_available):
        value = "free"
    if value in {"top", "tasker", "stick"} and bool(mydockfinder_available):
        value = "free"
    auto_hide_mode = str(taskbar_auto_hide_mode or "auto").strip().lower()
    if value == "stick" and auto_hide_mode == "disabled":
        value = "free"
    return value


def normalize_taskbar_auto_hide_mode(mode: str) -> str:
    value = str(mode or "auto").strip().lower()
    return value if value in {"auto", "enabled", "disabled"} else "auto"


def taskbar_auto_hide_enabled_for_state(
    *,
    screen_available: bool,
    mydockfinder_available: bool,
    taskbar_auto_hide_mode: str,
    detected_auto_hide: bool = False,
) -> bool:
    if not bool(screen_available) or bool(mydockfinder_available):
        return False
    mode = normalize_taskbar_auto_hide_mode(taskbar_auto_hide_mode)
    if mode == "enabled":
        return True
    if mode == "disabled":
        return False
    return bool(detected_auto_hide)


def build_taskbar_auto_hide_mode_plan(
    mode: str,
    *,
    current_location_mode: str,
) -> TaskbarAutoHideModePlan:
    normalized = normalize_taskbar_auto_hide_mode(mode)
    disabled = normalized == "disabled"
    current_location = str(current_location_mode or "free").strip().lower()
    force_free = disabled and current_location == "stick"
    return TaskbarAutoHideModePlan(
        mode=normalized,
        reset_follow_cache=True,
        clear_above_taskbar_follow=disabled,
        force_location_mode="free" if force_free else "",
        force_finder_mode=False if force_free else None,
        force_attach_to_real_finder=False if force_free else None,
        force_taskbar_fusion_mode="off" if force_free else "",
        clear_taskbar_preview=disabled,
        clear_taskbar_drop_state=disabled,
        hide_taskbar_drop_overlay=disabled,
    )


def normalize_taskbar_fusion_mode(mode: str) -> str:
    value = str(mode or "off").strip()
    return value if value in {"off", "preview", "compact", "expanded"} else "off"


def build_taskbar_fusion_mode_plan(mode: str) -> TaskbarFusionModePlan:
    normalized = normalize_taskbar_fusion_mode(mode)
    return TaskbarFusionModePlan(
        mode=normalized,
        force_expanded=True if normalized == "expanded" else False if normalized == "compact" else None,
        force_plugin_panel_open=True if normalized == "expanded" else None,
        taskbar_preview_visible_before_location_change=normalized == "preview" if normalized in {"off", "preview"} else None,
        target_location_mode="tasker" if normalized in {"compact", "expanded"} else "free",
    )


def build_standalone_release_execution_plan(
    decision: StandaloneReleaseDecision,
) -> StandaloneReleaseExecutionPlan:
    action = str(getattr(decision, "action", "") or "none")
    if action == "top":
        return StandaloneReleaseExecutionPlan(
            action=action,
            execute_top_notch=True,
        )
    if action == "tasker":
        return StandaloneReleaseExecutionPlan(
            action=action,
            execute_taskbar_dock=True,
            clear_above_taskbar_follow=True,
            set_location_mode="tasker",
            request_taskbar_dock_animation=True,
        )
    if action == "stick":
        return StandaloneReleaseExecutionPlan(
            action=action,
            execute_above_taskbar=True,
        )
    if action == "free":
        exiting_edge = bool(getattr(decision, "exiting_edge_notch_mode", False))
        return StandaloneReleaseExecutionPlan(
            action=action,
            execute_free_placement=True,
            clear_above_taskbar_follow=True,
            force_location_mode="free" if exiting_edge else "",
            force_finder_mode=False if exiting_edge else None,
            force_attach_to_real_finder=False if exiting_edge else None,
            force_taskbar_fusion_mode="off" if exiting_edge else "",
            clear_taskbar_preview=exiting_edge,
        )
    return StandaloneReleaseExecutionPlan(action=action)


def build_mouse_release_cleanup_plan(
    *,
    accepted_taskbar_drop: bool,
    has_release_anchor: bool,
) -> MouseReleaseCleanupPlan:
    if bool(accepted_taskbar_drop):
        return MouseReleaseCleanupPlan(
            clear_taskbar_attach_hot_zone=True,
            hide_taskbar_drop_overlay=True,
        )
    if bool(has_release_anchor):
        return MouseReleaseCleanupPlan(
            clear_taskbar_preview=True,
            clear_taskbar_selected_drop_zone=True,
            clear_taskbar_attach_hot_zone=True,
            clear_taskbar_drop_zones=True,
            hide_taskbar_drop_overlay=True,
        )
    return MouseReleaseCleanupPlan()


def build_mouse_release_action_plan(
    *,
    scene_suppressed: bool,
    drag_candidate: bool,
    dragging: bool,
    taskbar_preview_visible: bool,
) -> MouseReleaseActionPlan:
    if bool(scene_suppressed):
        return MouseReleaseActionPlan(
            action="suppressed",
            save_placement_state=False,
        )
    if bool(drag_candidate) and not bool(dragging):
        return MouseReleaseActionPlan(
            action="activate",
            activate_pressed_key=True,
            update_after_activation=True,
        )
    if bool(dragging):
        return MouseReleaseActionPlan(
            action="drag_release",
            capture_release_anchor=True,
            recheck_taskbar_preview=bool(taskbar_preview_visible),
            accept_taskbar_drop=True,
        )
    return MouseReleaseActionPlan(action="idle")


def build_mouse_release_standalone_settle_plan(
    *,
    accepted_taskbar_drop: bool,
    has_release_anchor: bool,
    location_mode: str,
) -> MouseReleaseStandaloneSettlePlan:
    should_settle = (
        not bool(accepted_taskbar_drop)
        and bool(has_release_anchor)
        and str(location_mode or "free").strip().lower() != "tasker"
    )
    return MouseReleaseStandaloneSettlePlan(
        check_tasker_detach_above_taskbar=should_settle,
        settle_standalone_release=should_settle,
    )


def build_tasker_release_detach_plan(
    release_global_pos,
    screen_geometry,
    taskbar_rect: QRectF,
    *,
    taskbar_compact_active: bool,
    min_slop: float = 8.0,
    max_slop: float = 18.0,
) -> TaskerReleaseDetachPlan:
    return TaskerReleaseDetachPlan(
        should_detach=tasker_release_point_reaches_bottom_slop(
            release_global_pos,
            screen_geometry,
            taskbar_rect,
            taskbar_compact_active=taskbar_compact_active,
            min_slop=min_slop,
            max_slop=max_slop,
        )
    )


def build_taskbar_presence_plan(
    inside: bool,
    *,
    current_location_mode: str,
) -> TaskbarPresencePlan:
    active = bool(inside)
    current = str(current_location_mode or "free").strip().lower()
    if active:
        return TaskbarPresencePlan(
            inside=True,
            taskbar_preview_visible=True,
            sync_taskbar_drop_overlay=True,
        )
    return TaskbarPresencePlan(
        inside=False,
        taskbar_preview_visible=False,
        set_location_mode="free" if current == "tasker" else "",
        clear_taskbar_selected_drop_zone=True,
        clear_taskbar_attach_hot_zone=True,
        clear_taskbar_drop_zones=True,
        hide_taskbar_drop_overlay=True,
    )


def build_taskbar_preview_drag_plan(
    *,
    taskbar_allowed: bool,
    prompt_allowed: bool,
    hot_zone: QRectF,
    hot_zone_host_rect: QRectF,
    taskbar_rect: QRectF,
    main_rect: QRectF,
    drop_zones: list[QRectF] | tuple[QRectF, ...] | None = None,
    selected_drop_zone: QRectF | None = None,
) -> TaskbarPreviewDragPlan:
    prompt_visible = bool(taskbar_allowed) and bool(prompt_allowed) and not QRectF(hot_zone).isNull()
    visible_hot_zone = QRectF(hot_zone) if prompt_visible else QRectF()
    visible_host_rect = QRectF(hot_zone_host_rect) if prompt_visible else QRectF()
    taskbar = QRectF(taskbar_rect)
    active = (
        prompt_visible
        and not taskbar.isNull()
        and taskbar_attach_hot_zone_accepts_rect(visible_hot_zone, main_rect)
    )
    clean_zones = tuple(QRectF(zone) for zone in (drop_zones or ()) if zone is not None and not QRectF(zone).isNull())
    selected = QRectF(selected_drop_zone) if selected_drop_zone is not None and not QRectF(selected_drop_zone).isNull() else QRectF()
    if not active:
        clean_zones = ()
        selected = QRectF()
    return TaskbarPreviewDragPlan(
        prompt_visible=prompt_visible,
        active=active,
        taskbar_attach_hot_zone=visible_hot_zone,
        taskbar_attach_hot_zone_host_rect=visible_host_rect,
        taskbar_attach_hot_zone_active=active,
        needs_drop_zones=active and not clean_zones,
        taskbar_drop_zones=clean_zones,
        taskbar_selected_drop_zone=selected,
        last_taskbar_rect=QRectF(taskbar) if active else QRectF(),
        update_taskbar_material=active,
    )


def build_taskbar_preview_drop_acceptance_plan(
    *,
    taskbar_allowed: bool,
    taskbar_preview_visible: bool,
    taskbar_attach_hot_zone_active: bool,
) -> TaskbarPreviewDropAcceptancePlan:
    accepted = (
        bool(taskbar_allowed)
        and bool(taskbar_preview_visible)
        and bool(taskbar_attach_hot_zone_active)
    )
    if not accepted:
        return TaskbarPreviewDropAcceptancePlan(accepted=False)
    return TaskbarPreviewDropAcceptancePlan(
        accepted=True,
        set_location_mode="tasker",
        request_taskbar_dock_animation=True,
        clear_taskbar_attach_hot_zone=True,
        hide_taskbar_drop_overlay=True,
    )


def build_taskbar_drag_action_plan(
    *,
    location_mode: str,
    drag_press_key: str,
    left_handle_stays_on_track: bool,
    should_detach_to_free: bool,
) -> TaskbarDragActionPlan:
    mode = str(location_mode or "free").strip().lower()
    key = str(drag_press_key or "")
    if mode == "tasker":
        if key == "taskbar:left-handle" and bool(left_handle_stays_on_track):
            return TaskbarDragActionPlan(
                action="resize_taskbar_left_handle",
                resize_taskbar_left_handle=True,
            )
        if bool(should_detach_to_free):
            return TaskbarDragActionPlan(
                action="detach_to_free",
                remember_motion_global_rects=True,
                leave_taskbar_presence=True,
                move_standalone_drag=True,
                update_taskbar_preview=True,
            )
        return TaskbarDragActionPlan(
            action="move_taskbar_drag",
            move_taskbar_drag=True,
        )
    return TaskbarDragActionPlan(
        action="move_standalone_drag",
        move_standalone_drag=True,
        update_taskbar_preview=True,
        set_location_mode="free" if mode == "tasker" else "",
    )


def build_drag_update_entry_plan(
    *,
    drag_candidate: bool,
    dragging: bool,
    delta,
    drag_threshold: float,
    finder_mode: bool,
) -> DragUpdateEntryPlan:
    if not bool(drag_candidate):
        return DragUpdateEntryPlan(action="ignore")
    try:
        distance = abs(float(delta.x())) + abs(float(delta.y()))
    except Exception:
        distance = 0.0
    start_drag = False
    if not bool(dragging):
        if distance < max(0.0, float(drag_threshold)):
            return DragUpdateEntryPlan(action="await_threshold")
        start_drag = True
    if bool(finder_mode):
        return DragUpdateEntryPlan(
            action="finder_drag",
            start_drag=start_drag,
            collapse_expanded=start_drag,
            route_finder_drag=True,
            pulse_stack=True,
        )
    return DragUpdateEntryPlan(
        action="standard_drag",
        start_drag=start_drag,
        collapse_expanded=start_drag,
        route_standard_drag=True,
        pulse_stack=True,
    )


def build_finder_drag_action_plan(
    *,
    delta_y: float,
    detach_threshold: float,
) -> FinderDragActionPlan:
    if float(delta_y) > max(0.0, float(detach_threshold)):
        return FinderDragActionPlan(
            action="detach_to_free",
            detach_to_free=True,
            move_standalone_after_detach=True,
            pulse_stack_after_detach=True,
        )
    return FinderDragActionPlan(
        action="follow_finder",
        follow_finder=True,
    )


def build_finder_attach_plan(
    *,
    attach_allowed: bool,
    global_pos: QPoint | QPointF,
    delta_y: float,
    screen_geometry,
    finder_bar_height: float,
    unit: float = 1.0,
) -> FinderAttachPlan:
    if not bool(attach_allowed) or float(delta_y) >= 0.0:
        return FinderAttachPlan()
    try:
        geo = QRectF(screen_geometry)
    except Exception:
        return FinderAttachPlan()
    if geo.isNull():
        return FinderAttachPlan()
    try:
        y = float(global_pos.y())
    except Exception:
        return FinderAttachPlan()
    u = max(0.0, float(unit))
    threshold = max(42.0 * u, float(finder_bar_height) + 18.0 * u)
    return FinderAttachPlan(should_attach=y <= geo.top() + threshold)


def build_standalone_drag_direction_plan(
    *,
    expand_direction: str,
    auto_expand_bounds: bool,
    global_pos,
    screen_geometry,
    dragging: bool,
    drag_candidate: bool,
    current_auto_direction: int,
    current_stack_direction: int,
    unit: float = 1.0,
) -> StandaloneDragDirectionPlan:
    mode = str(expand_direction or "auto").strip().lower()
    if mode == "down":
        return StandaloneDragDirectionPlan(direction=1, update_auto_direction=True, auto_direction=1)
    if mode == "up":
        return StandaloneDragDirectionPlan(direction=-1, update_auto_direction=True, auto_direction=-1)
    if not bool(auto_expand_bounds):
        return StandaloneDragDirectionPlan(direction=1, update_auto_direction=True, auto_direction=1)
    try:
        geo = QRectF(screen_geometry)
    except Exception:
        geo = QRectF()
    if geo.isNull():
        return StandaloneDragDirectionPlan(direction=1)
    try:
        y = float(global_pos.y())
    except Exception:
        y = geo.top()
    cutoff = geo.top() + geo.height() * (2.0 / 3.0)
    raw_direction = -1 if y >= cutoff else 1

    def valid_direction(value: int, fallback: int) -> int:
        return value if value in (-1, 1) else fallback

    if bool(dragging):
        latched = valid_direction(
            int(current_auto_direction or current_stack_direction or raw_direction),
            raw_direction,
        )
        return StandaloneDragDirectionPlan(
            direction=latched,
            update_auto_direction=True,
            auto_direction=latched,
        )
    if not (bool(drag_candidate) or bool(dragging)):
        return StandaloneDragDirectionPlan(
            direction=raw_direction,
            update_auto_direction=True,
            auto_direction=raw_direction,
        )

    latched = valid_direction(
        int(current_auto_direction or current_stack_direction or raw_direction),
        raw_direction,
    )
    scale = max(0.0, float(unit))
    margin = max(56.0 * scale, min(96.0 * scale, float(geo.height()) * 0.08))
    if latched >= 0 and y >= cutoff + margin:
        latched = -1
    elif latched < 0 and y <= cutoff - margin:
        latched = 1
    return StandaloneDragDirectionPlan(
        direction=latched,
        update_auto_direction=True,
        auto_direction=latched,
    )


def build_standalone_stack_direction_plan(
    *,
    finder_mode: bool,
    taskbar_compact_active: bool,
    taskbar_rect: QRectF,
    current_stack_direction: int,
    expand_direction: str,
    auto_expand_bounds: bool,
    screen_geometry,
    global_main_center_y: float | None = None,
    expanded: bool = False,
    global_main_top: float | None = None,
    expanded_down_height: float | None = None,
    bottom_margin: float = 14.0,
) -> int:
    if bool(finder_mode):
        return 1

    try:
        screen = QRectF(screen_geometry)
    except Exception:
        screen = QRectF()

    if bool(taskbar_compact_active):
        taskbar = QRectF(taskbar_rect)
        if not taskbar.isNull() and not screen.isNull():
            return taskbar_stack_direction_for_rect(taskbar, screen)
        try:
            return -1 if int(current_stack_direction) < 0 else 1
        except (TypeError, ValueError):
            return 1

    mode = str(expand_direction or "auto").strip().lower()
    if mode == "down":
        return 1
    if mode == "up":
        return -1
    if not bool(auto_expand_bounds) or screen.isNull():
        return 1

    try:
        center_y = float(global_main_center_y)
    except (TypeError, ValueError):
        return 1
    cutoff = float(screen.top()) + float(screen.height()) * (2.0 / 3.0)
    if center_y >= cutoff:
        return -1
    if not bool(expanded):
        return 1
    try:
        top = float(global_main_top)
        down_height = float(expanded_down_height)
    except (TypeError, ValueError):
        return 1
    if top + down_height > float(screen.bottom()) - float(bottom_margin):
        return -1
    return 1


def build_location_mode_transition_plan(
    mode: str,
    previous_mode: str,
    *,
    drag_candidate: bool = False,
    surface_transition_started: bool = False,
) -> LocationModeTransitionPlan:
    current = str(mode or "free").strip().lower()
    previous = str(previous_mode or "free").strip().lower()
    mode_changed = current != previous
    needs_surface_transition = mode_changed and (current == "finder" or previous == "finder")
    transition_reason = "finder" if needs_surface_transition and current == "finder" else "standalone" if needs_surface_transition else ""
    standalone_mode = current in {"tasker", "stick", "top", "free"}
    clear_scene_state = current in {"tasker", "stick", "top", "free"}
    no_drag = not bool(drag_candidate)
    return LocationModeTransitionPlan(
        mode=current,
        previous_mode=previous,
        mode_changed=mode_changed,
        begin_surface_transition_reason=transition_reason,
        reset_runtime_state=mode_changed,
        reset_clear_surface=not bool(surface_transition_started),
        finder_mode=current == "finder",
        attach_to_real_finder=current == "finder",
        taskbar_above_follow_active=current == "stick",
        taskbar_fusion_mode="compact" if current == "tasker" else "off",
        taskbar_preview_visible=current == "tasker",
        clear_scene_state=clear_scene_state,
        clear_mask=standalone_mode,
        refresh_finder_material=current == "finder",
        refresh_taskbar_follow_position=current == "tasker" and no_drag,
        refresh_taskbar_above_follow_position=current == "stick" and no_drag,
        settle_top_notch=current == "top" and no_drag,
        sync_taskbar_color=current == "tasker" and mode_changed,
    )


def fallback_taskbar_task_area_rect(
    taskbar_rect: QRectF,
    *,
    unit: float = 1.0,
) -> QRectF:
    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull():
        return QRectF()
    u = max(0.0, float(unit))
    if taskbar.width() >= taskbar.height():
        lead = min(max(56.0 * u, taskbar.height() * 1.75), taskbar.width() * 0.22)
        trail = min(max(150.0 * u, taskbar.height() * 4.0), taskbar.width() * 0.32)
        area = taskbar.adjusted(lead, 0.0, -trail, 0.0)
        return area if area.width() >= 180.0 * u else taskbar
    lead = min(max(56.0 * u, taskbar.width() * 1.75), taskbar.height() * 0.22)
    trail = min(max(150.0 * u, taskbar.width() * 4.0), taskbar.height() * 0.32)
    area = taskbar.adjusted(0.0, lead, 0.0, -trail)
    return area if area.height() >= 180.0 * u else taskbar


def select_windows_taskbar_task_area_rect(
    taskbar_rect: QRectF,
    child_rects: list[tuple[str, QRectF]] | tuple[tuple[str, QRectF], ...],
    *,
    unit: float = 1.0,
) -> QRectF | None:
    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull():
        return None
    class_priority = {
        "MSTaskListWClass": 0,
        "MSTaskSwWClass": 1,
    }
    horizontal = taskbar.width() >= taskbar.height()
    u = max(0.0, float(unit))
    candidates: list[tuple[int, float, QRectF]] = []
    for class_name, rect in child_rects or ():
        if class_name not in class_priority:
            continue
        candidate = QRectF(rect).intersected(taskbar)
        if candidate.isNull():
            continue
        if horizontal:
            if candidate.height() < taskbar.height() * 0.42 or candidate.width() < 120.0 * u:
                continue
        elif candidate.width() < taskbar.width() * 0.42 or candidate.height() < 120.0 * u:
            continue
        candidates.append((class_priority[class_name], -candidate.width() * candidate.height(), candidate))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return QRectF(candidates[0][2])


def select_taskbar_task_area_rect(
    taskbar_rect: QRectF,
    detected_task_area: QRectF | None = None,
    *,
    unit: float = 1.0,
) -> QRectF:
    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull():
        return QRectF()
    detected = QRectF(detected_task_area) if detected_task_area is not None else QRectF()
    if not detected.isNull():
        return detected.intersected(taskbar)
    return fallback_taskbar_task_area_rect(taskbar, unit=unit)


def select_windows_taskbar_occupied_rects(
    taskbar_rect: QRectF,
    child_rects: list[tuple[str, QRectF]] | tuple[tuple[str, QRectF], ...],
    *,
    task_area_rect: QRectF | None = None,
    unit: float = 1.0,
) -> list[QRectF]:
    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull():
        return []
    task_area = QRectF(task_area_rect) if task_area_rect is not None else QRectF()
    ignored = {"MSTaskListWClass", "MSTaskSwWClass"}
    horizontal = taskbar.width() >= taskbar.height()
    u = max(0.0, float(unit))
    occupied: list[QRectF] = []
    for class_name, rect in child_rects or ():
        candidate = QRectF(rect).intersected(taskbar)
        if candidate.isNull():
            continue
        if class_name in ignored:
            continue
        if not task_area.isNull() and candidate.contains(task_area.center()):
            continue
        if horizontal:
            if candidate.height() < taskbar.height() * 0.28 or candidate.width() < 14.0 * u:
                continue
        elif candidate.width() < taskbar.width() * 0.28 or candidate.height() < 14.0 * u:
            continue
        occupied.append(candidate)
    return occupied


def taskbar_shell_rect_is_hidden_autohide(
    shell_rect: QRectF,
    appbar_rect: QRectF | None = None,
    *,
    auto_hide: bool,
    unit: float = 1.0,
) -> bool:
    taskbar = QRectF(shell_rect)
    if taskbar.isNull() or not bool(auto_hide):
        return False
    appbar = QRectF(appbar_rect) if appbar_rect is not None else QRectF()
    visible_thickness = min(float(taskbar.width()), float(taskbar.height()))
    expected_thickness = min(float(appbar.width()), float(appbar.height())) if not appbar.isNull() else 0.0
    u = max(0.0, float(unit))
    hidden_threshold = max(6.0 * u, 4.0)
    if expected_thickness > 0.0:
        hidden_threshold = max(hidden_threshold, min(expected_thickness * 0.35, 18.0 * u))
    return visible_thickness <= hidden_threshold


def taskbar_autohide_reveal_edge_rect(
    taskbar_rect: QRectF,
    screen_geometry,
    *,
    unit: float = 1.0,
) -> QRectF:
    taskbar = QRectF(taskbar_rect)
    try:
        geo = QRectF(screen_geometry)
    except Exception:
        geo = QRectF()
    if taskbar.isNull() or geo.isNull():
        return QRectF()
    u = max(0.0, float(unit))
    edge_thickness = max(2.0, min(6.0 * u, min(float(taskbar.width()), float(taskbar.height())) * 0.16))
    if taskbar.width() >= taskbar.height():
        if taskbar.center().y() >= geo.center().y():
            return QRectF(float(geo.left()), float(geo.bottom()) - edge_thickness, float(geo.width()), edge_thickness + 1.0)
        return QRectF(float(geo.left()), float(geo.top()), float(geo.width()), edge_thickness + 1.0)
    if taskbar.center().x() >= geo.center().x():
        return QRectF(float(geo.right()) - edge_thickness, float(geo.top()), edge_thickness + 1.0, float(geo.height()))
    return QRectF(float(geo.left()), float(geo.top()), edge_thickness + 1.0, float(geo.height()))


def taskbar_autohide_cursor_reveals(
    taskbar_rect: QRectF,
    screen_geometry,
    cursor: QPoint | QPointF,
    *,
    drag_active: bool = False,
    unit: float = 1.0,
) -> bool:
    if bool(drag_active):
        return False
    try:
        geo = QRectF(screen_geometry)
        point = QPointF(cursor)
    except Exception:
        return False
    if geo.isNull() or not geo.adjusted(-1.0, -1.0, 1.0, 1.0).contains(point):
        return False
    reveal = taskbar_autohide_reveal_edge_rect(taskbar_rect, geo, unit=unit)
    if reveal.isNull():
        return False
    return reveal.adjusted(-1.0, -1.0, 1.0, 1.0).contains(point)


def taskbar_autohide_cursor_in_taskbar_area(
    taskbar_rect: QRectF,
    screen_geometry,
    cursor: QPoint | QPointF,
    *,
    unit: float = 1.0,
) -> bool:
    taskbar = QRectF(taskbar_rect)
    try:
        geo = QRectF(screen_geometry)
        point = QPointF(cursor)
    except Exception:
        return False
    if taskbar.isNull() or geo.isNull() or not geo.adjusted(-1.0, -1.0, 1.0, 1.0).contains(point):
        return False
    u = max(0.0, float(unit))
    pad = max(1.0, min(4.0 * u, min(float(taskbar.width()), float(taskbar.height())) * 0.10))
    return taskbar.adjusted(-pad, -pad, pad, pad).contains(point)


def build_taskbar_autohide_interaction_plan(
    *,
    screen_changed: bool,
    dragging: bool,
    inside_taskbar: bool,
    cursor_reveals: bool,
    pressed: bool,
    previous_mouse_pressed: bool,
    interaction_visible: bool,
    click_hold_visible: bool,
    left_taskbar_at: float,
    now: float,
    leave_grace_seconds: float = 0.5,
) -> TaskbarAutohideInteractionPlan:
    if bool(screen_changed):
        interaction = False
        click_hold = False
        was_pressed = False
        left_at = 0.0
    else:
        interaction = bool(interaction_visible)
        click_hold = bool(click_hold_visible)
        was_pressed = bool(previous_mouse_pressed)
        try:
            left_at = float(left_taskbar_at or 0.0)
        except (TypeError, ValueError):
            left_at = 0.0

    is_pressed = bool(pressed)
    if is_pressed and not was_pressed:
        if bool(inside_taskbar):
            click_hold = True
            interaction = True
            left_at = 0.0
        else:
            return TaskbarAutohideInteractionPlan(
                keeps_visible=False,
                interaction_visible=False,
                click_hold_visible=False,
                mouse_pressed=is_pressed,
                left_taskbar_at=0.0,
            )

    if bool(dragging):
        if not click_hold:
            interaction = False
            left_at = 0.0
        return TaskbarAutohideInteractionPlan(
            keeps_visible=click_hold,
            interaction_visible=interaction,
            click_hold_visible=click_hold,
            mouse_pressed=is_pressed,
            left_taskbar_at=left_at,
        )

    if click_hold:
        return TaskbarAutohideInteractionPlan(
            keeps_visible=True,
            interaction_visible=interaction,
            click_hold_visible=True,
            mouse_pressed=is_pressed,
            left_taskbar_at=left_at,
        )

    if bool(cursor_reveals):
        return TaskbarAutohideInteractionPlan(
            keeps_visible=True,
            interaction_visible=True,
            click_hold_visible=False,
            mouse_pressed=is_pressed,
            left_taskbar_at=0.0,
        )

    if interaction and bool(inside_taskbar):
        return TaskbarAutohideInteractionPlan(
            keeps_visible=True,
            interaction_visible=True,
            click_hold_visible=False,
            mouse_pressed=is_pressed,
            left_taskbar_at=0.0,
        )

    if interaction:
        if left_at <= 0.0:
            try:
                left_at = float(now)
            except (TypeError, ValueError):
                left_at = 0.0
        try:
            elapsed = float(now) - left_at
        except (TypeError, ValueError):
            elapsed = 0.0
        if elapsed < max(0.0, float(leave_grace_seconds)):
            return TaskbarAutohideInteractionPlan(
                keeps_visible=True,
                interaction_visible=True,
                click_hold_visible=False,
                mouse_pressed=is_pressed,
                left_taskbar_at=left_at,
            )

    return TaskbarAutohideInteractionPlan(
        keeps_visible=False,
        interaction_visible=False,
        click_hold_visible=False,
        mouse_pressed=is_pressed,
        left_taskbar_at=0.0,
    )


def select_taskbar_autohide_probe_rect(
    taskbar_rect: QRectF | None = None,
    appbar_rect: QRectF | None = None,
    shell_rect: QRectF | None = None,
) -> QRectF:
    taskbar = QRectF(taskbar_rect) if taskbar_rect is not None else QRectF()
    if taskbar.isNull():
        taskbar = QRectF(appbar_rect) if appbar_rect is not None else QRectF()
    if taskbar.isNull():
        taskbar = QRectF(shell_rect) if shell_rect is not None else QRectF()
    return taskbar


def build_taskbar_autohide_hidden_plan(
    *,
    taskbar_rect: QRectF | None = None,
    appbar_rect: QRectF | None = None,
    shell_rect: QRectF | None = None,
    taskbar_auto_hide_mode: str = "auto",
    detected_auto_hide: bool = False,
    interaction_keeps_visible: bool = False,
    unit: float = 1.0,
) -> TaskbarAutohideHiddenPlan:
    mode = normalize_taskbar_auto_hide_mode(taskbar_auto_hide_mode)
    auto_hide = True if mode == "enabled" else False if mode == "disabled" else bool(detected_auto_hide)
    probe = select_taskbar_autohide_probe_rect(taskbar_rect, appbar_rect, shell_rect)
    if not auto_hide:
        return TaskbarAutohideHiddenPlan(auto_hide=False, hidden=False, probe_taskbar=probe)
    if not probe.isNull() and bool(interaction_keeps_visible):
        return TaskbarAutohideHiddenPlan(auto_hide=True, hidden=False, probe_taskbar=probe)
    shell = QRectF(shell_rect) if shell_rect is not None else QRectF()
    if shell.isNull():
        return TaskbarAutohideHiddenPlan(auto_hide=True, hidden=True, probe_taskbar=probe)
    hidden = taskbar_shell_rect_is_hidden_autohide(
        shell,
        QRectF(appbar_rect) if appbar_rect is not None else QRectF(),
        auto_hide=True,
        unit=unit,
    )
    return TaskbarAutohideHiddenPlan(auto_hide=True, hidden=hidden, probe_taskbar=probe)


def build_taskbar_current_visibility_follow_plan(
    taskbar_rect: QRectF,
    screen_geometry,
    *,
    auto_hide_enabled: bool,
    force_visible: bool = False,
    force_visible_active: bool = False,
    appbar_rect: QRectF | None = None,
    shell_rect: QRectF | None = None,
    interaction_keeps_visible: bool = False,
    hidden_autohide: bool = False,
) -> TaskbarCurrentVisibilityFollowPlan:
    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull():
        return TaskbarCurrentVisibilityFollowPlan(
            follow_rect=QRectF(taskbar),
            base_rect=QRectF(taskbar),
            hidden_autohide=False,
        )
    if bool(force_visible) or bool(force_visible_active) or not bool(auto_hide_enabled):
        return TaskbarCurrentVisibilityFollowPlan(
            follow_rect=QRectF(taskbar),
            base_rect=QRectF(taskbar),
            hidden_autohide=False,
        )
    base = QRectF(appbar_rect) if appbar_rect is not None and not QRectF(appbar_rect).isNull() else QRectF(taskbar)
    if bool(interaction_keeps_visible):
        return TaskbarCurrentVisibilityFollowPlan(
            follow_rect=QRectF(base),
            base_rect=QRectF(base),
            hidden_autohide=False,
        )
    shell = QRectF(shell_rect) if shell_rect is not None else QRectF()
    if bool(hidden_autohide):
        return TaskbarCurrentVisibilityFollowPlan(
            follow_rect=hidden_autohide_follow_rect(screen_geometry, base),
            base_rect=QRectF(base),
            hidden_autohide=True,
        )
    if not shell.isNull():
        return TaskbarCurrentVisibilityFollowPlan(
            follow_rect=QRectF(shell),
            base_rect=QRectF(base),
            hidden_autohide=False,
        )
    return TaskbarCurrentVisibilityFollowPlan(
        follow_rect=QRectF(base),
        base_rect=QRectF(base),
        hidden_autohide=False,
    )


def build_taskbar_visibility_follow_plan(
    taskbar_rect: QRectF,
    screen_geometry,
    *,
    force_visible: bool = False,
    force_visible_active: bool = False,
    hidden_autohide: bool = False,
) -> TaskbarVisibilityFollowPlan:
    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull():
        return TaskbarVisibilityFollowPlan(QRectF(taskbar), hidden_autohide=False)
    if bool(force_visible) or bool(force_visible_active) or not bool(hidden_autohide):
        return TaskbarVisibilityFollowPlan(QRectF(taskbar), hidden_autohide=False)
    return TaskbarVisibilityFollowPlan(
        hidden_autohide_follow_rect(screen_geometry, taskbar),
        hidden_autohide=True,
    )


def resolve_taskbar_rect_for_screen(
    *,
    screen_geometry,
    available_geometry,
    shell_rect: QRectF | None = None,
    appbar_auto_hide: bool = False,
    appbar_rect: QRectF | None = None,
    appbar_edge: str = "",
    taskbar_auto_hide_mode: str = "auto",
    previous_taskbar_rect: QRectF | None = None,
    unit: float = 1.0,
) -> QRectF | None:
    try:
        geo = QRectF(screen_geometry)
    except Exception:
        geo = QRectF()
    if geo.isNull():
        return None
    try:
        available = QRectF(available_geometry)
    except Exception:
        available = QRectF()
    appbar = QRectF(appbar_rect) if appbar_rect is not None else QRectF()
    shell = QRectF(shell_rect) if shell_rect is not None else QRectF()
    mode = str(taskbar_auto_hide_mode or "auto").strip().lower()
    if mode == "enabled":
        auto_hide = True
    elif mode == "disabled":
        auto_hide = False
    else:
        auto_hide = bool(appbar_auto_hide)
    u = max(0.0, float(unit))

    if not shell.isNull():
        if taskbar_shell_rect_is_hidden_autohide(shell, appbar, auto_hide=auto_hide, unit=u):
            if not appbar.isNull():
                return appbar
            fallback = taskbar_hidden_fallback_rect(
                geo,
                previous_taskbar_rect=previous_taskbar_rect,
                edge=appbar_edge,
                unit=u,
            )
            return None if fallback.isNull() else fallback
        full_available = (
            not available.isNull()
            and abs(available.left() - geo.left()) <= 1.0
            and abs(available.top() - geo.top()) <= 1.0
            and abs(available.width() - geo.width()) <= 1.0
            and abs(available.height() - geo.height()) <= 1.0
        )
        thickness = min(float(shell.width()), float(shell.height()))
        if full_available and thickness <= max(6.0 * u, 4.0):
            fallback = taskbar_hidden_fallback_rect(
                geo,
                previous_taskbar_rect=previous_taskbar_rect,
                edge=appbar_edge,
                appbar_rect=appbar,
                unit=u,
            )
            return None if fallback.isNull() else fallback
        return shell

    if auto_hide and not appbar.isNull():
        return appbar
    if available.isNull():
        return None
    if available.bottom() < geo.bottom() - 1:
        return QRectF(float(geo.left()), float(available.bottom() + 1), float(geo.width()), float(geo.bottom() - available.bottom()))
    if available.top() > geo.top() + 1:
        return QRectF(float(geo.left()), float(geo.top()), float(geo.width()), float(available.top() - geo.top()))
    if available.left() > geo.left() + 1:
        return QRectF(float(geo.left()), float(geo.top()), float(available.left() - geo.left()), float(geo.height()))
    if available.right() < geo.right() - 1:
        return QRectF(float(available.right() + 1), float(geo.top()), float(geo.right() - available.right()), float(geo.height()))
    return None


def taskbar_hidden_fallback_rect(
    screen_geometry,
    *,
    previous_taskbar_rect: QRectF | None = None,
    edge: str = "",
    appbar_rect: QRectF | None = None,
    unit: float = 1.0,
) -> QRectF:
    try:
        geo = QRectF(screen_geometry)
    except Exception:
        return QRectF()
    if geo.isNull():
        return QRectF()

    appbar = QRectF(appbar_rect) if appbar_rect is not None else QRectF()
    if not appbar.isNull() and appbar.intersects(geo):
        return appbar.intersected(geo)

    previous = QRectF(previous_taskbar_rect) if previous_taskbar_rect is not None else QRectF()
    edge_value = str(edge or "").strip().casefold()
    if edge_value not in {"left", "top", "right", "bottom"}:
        if not previous.isNull():
            if previous.width() < previous.height():
                edge_value = "left" if previous.center().x() <= geo.center().x() else "right"
            else:
                edge_value = "top" if previous.center().y() <= geo.center().y() else "bottom"
        else:
            edge_value = "bottom"

    u = max(0.0, float(unit))
    if not previous.isNull():
        if edge_value in {"left", "right"}:
            thickness = max(34.0 * u, min(96.0 * u, float(previous.width())))
        else:
            thickness = max(34.0 * u, min(96.0 * u, float(previous.height())))
    else:
        thickness = 48.0 * u

    if edge_value == "left":
        return QRectF(float(geo.left()), float(geo.top()), thickness, float(geo.height()))
    if edge_value == "right":
        return QRectF(float(geo.right()) - thickness, float(geo.top()), thickness, float(geo.height()))
    if edge_value == "top":
        return QRectF(float(geo.left()), float(geo.top()), float(geo.width()), thickness)
    return QRectF(float(geo.left()), float(geo.bottom()) - thickness, float(geo.width()), thickness)


def select_taskbar_drag_reference_rect(
    *,
    taskbar_rect: QRectF | None = None,
    screen_geometry=None,
    previous_taskbar_rect: QRectF | None = None,
    fallback_rect: QRectF | None = None,
) -> QRectF:
    try:
        screen_geo = QRectF(screen_geometry) if screen_geometry is not None else QRectF()
    except Exception:
        screen_geo = QRectF()
    candidates: list[QRectF] = []

    def add_candidate(rect: QRectF | None):
        candidate = QRectF(rect) if rect is not None else QRectF()
        if candidate.isNull():
            return
        if not screen_geo.isNull() and not candidate.adjusted(-1.0, -1.0, 1.0, 1.0).intersects(screen_geo):
            return
        candidates.append(candidate)

    add_candidate(taskbar_rect)
    add_candidate(previous_taskbar_rect)
    add_candidate(fallback_rect)
    if not candidates:
        return QRectF()
    return max(
        candidates,
        key=lambda rect: min(float(rect.width()), float(rect.height())) if not rect.isNull() else 0.0,
    )


def drag_main_offset_for_press(press_pos: QPointF, main_rect: QRectF, key: str = "") -> QPointF:
    rect = QRectF(main_rect)
    if rect.isNull():
        return QPointF()
    if str(key or "").startswith("plugin-alert:"):
        return QPointF(rect.width() / 2.0, rect.height() / 2.0)
    try:
        point = QPointF(press_pos)
    except Exception:
        return QPointF(rect.width() / 2.0, rect.height() / 2.0)
    if rect.contains(point):
        return point - rect.topLeft()
    return QPointF(rect.width() / 2.0, rect.height() / 2.0)


def point_in_finder_frame(
    *,
    finder_mode: bool,
    finder_hover_rect: QRectF,
    point: QPointF,
    unit: float = 1.0,
) -> bool:
    if not finder_mode:
        return False
    rect = QRectF(finder_hover_rect)
    if rect.isNull():
        return False
    try:
        pos = QPointF(point)
    except Exception:
        return False
    u = max(0.0, float(unit))
    return rect.adjusted(-2.0 * u, -2.0 * u, 2.0 * u, 2.0 * u).contains(pos)


def taskbar_compact_hit_test_key(
    *,
    point: QPointF,
    compact_active: bool,
    expanded: bool,
    left_handle_rect: QRectF,
    left_handle_visible: bool,
    music_button_rects,
    selected_drop_zone_local_rect: QRectF,
    main_drag_key: str,
    unit: float = 1.0,
) -> str | None:
    if not compact_active:
        return None
    try:
        pos = QPointF(point)
    except Exception:
        return None
    u = max(0.0, float(unit))
    handle = QRectF(left_handle_rect)
    if (
        not handle.isNull()
        and bool(left_handle_visible)
        and handle.adjusted(-5.0 * u, -5.0 * u, 5.0 * u, 5.0 * u).contains(pos)
    ):
        return "taskbar:left-handle"
    if not expanded:
        for key, rect in dict(music_button_rects or {}).items():
            button = QRectF(rect)
            if not button.isNull() and button.contains(pos):
                return str(key)
    grab_rect = QRectF(selected_drop_zone_local_rect)
    if not grab_rect.isNull() and grab_rect.contains(pos):
        return str(main_drag_key or "music")
    return None


def taskbar_selected_drop_zone_local_rect(
    selected_drop_zone: QRectF,
    window_global_origin: QPoint | QPointF | None = None,
    *,
    unit: float = 1.0,
) -> QRectF:
    zone = QRectF(selected_drop_zone)
    if zone.isNull() or zone.width() <= 1.0 or zone.height() <= 1.0:
        return QRectF()
    if window_global_origin is not None:
        try:
            origin = QPointF(window_global_origin)
            zone.translate(-float(origin.x()), -float(origin.y()))
        except Exception:
            pass
    u = max(0.0, float(unit))
    return zone.adjusted(-8.0 * u, -6.0 * u, 8.0 * u, 6.0 * u)


def taskbar_material_exclusion_rects(
    *,
    selected_drop_zone: QRectF,
    main_global_rect: QRectF,
    motion_global_rects,
    taskbar_compact_active: bool,
    unit: float = 1.0,
) -> list[QRectF]:
    u = max(0.0, float(unit))
    rects: list[QRectF] = []
    selected = QRectF(selected_drop_zone)
    if not selected.isNull() and selected.width() > 1.0 and selected.height() > 1.0:
        rects.append(selected.adjusted(-14.0 * u, -8.0 * u, 14.0 * u, 8.0 * u))
    tasker_slot_is_authoritative = bool(taskbar_compact_active) and bool(rects)
    main = QRectF(main_global_rect)
    if not tasker_slot_is_authoritative and not main.isNull() and main.width() > 1.0 and main.height() > 1.0:
        rects.append(main.adjusted(-18.0 * u, -10.0 * u, 18.0 * u, 10.0 * u))
    for motion_rect in motion_global_rects or []:
        rect = QRectF(motion_rect)
        if rect.isNull() or rect.width() <= 1.0 or rect.height() <= 1.0:
            continue
        rects.append(rect.adjusted(-10.0 * u, -6.0 * u, 10.0 * u, 6.0 * u))
    return rects


def taskbar_compact_height_from_rects(
    rects,
    *,
    compact_min: float = 40.0,
    min_candidate: float = 12.0,
) -> float:
    candidates: list[float] = []
    for rect in rects or []:
        value = QRectF(rect)
        if value.isNull():
            continue
        height = min(float(value.width()), float(value.height()))
        if height >= float(min_candidate):
            candidates.append(height)
    if not candidates:
        return 0.0
    return max(float(compact_min), max(candidates))


def taskbar_height_from_rect(
    taskbar_rect: QRectF | None,
    *,
    compact: bool,
    unit: float = 1.0,
) -> float:
    u = max(0.0, float(unit))
    rect = QRectF(taskbar_rect) if taskbar_rect is not None else QRectF()
    if rect.isNull():
        return 48.0 * u
    height = min(float(rect.width()), float(rect.height()))
    if height <= 1.0:
        return 48.0 * u
    if compact:
        return max(40.0 * u, height)
    return max(34.0 * u, min(72.0 * u, height))


def taskbar_corner_radius(taskbar_height: float, style: str | None = None, *, unit: float = 1.0) -> float:
    normalized = str(style or "win11").strip().lower()
    if normalized == "win10":
        return 0.0
    u = max(0.0, float(unit))
    return max(4.0 * u, min(10.0 * u, float(taskbar_height) * 0.18))


def taskbar_compact_main_height(
    taskbar_rect: QRectF | None = None,
    *,
    fallback: float | None = None,
    current_taskbar_height: float | None = None,
    unit: float = 1.0,
) -> float:
    default = 40.0 * max(0.0, float(unit)) if fallback is None else float(fallback)
    if taskbar_rect is not None:
        taskbar = QRectF(taskbar_rect)
        if not taskbar.isNull():
            height = min(float(taskbar.width()), float(taskbar.height()))
            if height > 1.0:
                return height
    if fallback is not None:
        return default
    if current_taskbar_height is not None:
        try:
            height = float(current_taskbar_height)
        except (TypeError, ValueError):
            height = 0.0
        if height > 1.0:
            return height
    return default


def taskbar_full_compact_height_for_rect(taskbar_rect: QRectF, fallback: float) -> float:
    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull():
        return float(fallback)
    height = min(float(taskbar.width()), float(taskbar.height()))
    if height <= 1.0:
        return float(fallback)
    return height


def taskbar_width_with_manual(
    default_width: float,
    *,
    compact_active: bool,
    manual_width,
    unit: float = 1.0,
) -> float:
    if not compact_active:
        return float(default_width)
    try:
        manual = float(manual_width or 0.0)
    except (TypeError, ValueError):
        manual = 0.0
    if manual <= 0.0:
        return float(default_width)
    u = max(0.0, float(unit))
    return max(120.0 * u, min(760.0 * u, manual))


def taskbar_compact_music_size(
    *,
    taskbar_height: float,
    default_music_width: float,
    selected_zone: QRectF,
    compact_active: bool,
    manual_width,
    unit: float = 1.0,
) -> tuple[float, float]:
    u = max(0.0, float(unit))
    height = taskbar_compact_main_height(fallback=taskbar_height, unit=u)
    normal_width = taskbar_width_with_manual(
        max(180.0 * u, float(default_music_width)),
        compact_active=compact_active,
        manual_width=manual_width,
        unit=u,
    )
    selected = QRectF(selected_zone)
    try:
        manual = float(manual_width or 0.0)
    except (TypeError, ValueError):
        manual = 0.0
    if compact_active and manual <= 0.0 and not selected.isNull():
        slot_width = max(0.0, float(selected.width()))
        if slot_width > 1.0 and slot_width < normal_width:
            cover_width = min(slot_width, max(28.0 * u, height + 8.0 * u))
            return cover_width, height
    return normal_width, height


def taskbar_cover_only_music_active(
    *,
    compact_active: bool,
    selected_zone: QRectF,
    rect: QRectF | None = None,
    default_music_width: float = 420.0,
    unit: float = 1.0,
) -> bool:
    if not compact_active:
        return False
    u = max(0.0, float(unit))
    selected = QRectF(selected_zone)
    if not selected.isNull() and selected.width() < 120.0 * u:
        return True
    if rect is not None:
        value = QRectF(rect)
        if not value.isNull():
            crowded_threshold = max(
                value.height() + 24.0 * u,
                min(150.0 * u, float(default_music_width) * 0.32),
            )
            if value.width() <= crowded_threshold:
                return True
    return False


def taskbar_synced_corner_radius(
    rect: QRectF,
    *,
    taskbar_height: float,
    style: str | None = None,
    unit: float = 1.0,
) -> float:
    value = QRectF(rect)
    if value.isNull():
        return 0.0
    height = min(float(value.height()), float(taskbar_height))
    return min(
        float(value.width()) / 2.0,
        float(value.height()) / 2.0,
        taskbar_corner_radius(height, style, unit=unit),
    )


def _kind_key(kind) -> str:
    return str(getattr(kind, "value", kind) or "").strip().lower()


def compact_music_target_width_from_content(
    *,
    content_width: float,
    title_width: float,
    artist_width: float,
    lyric_width: float,
    current_dual_width: float,
    instrumental: bool,
    compact_lyric_cover_enabled: bool,
    unit: float = 1.0,
) -> float:
    content = float(content_width)
    title = float(title_width)
    artist = float(artist_width)
    lyric = float(lyric_width)
    dual = float(current_dual_width)
    if bool(instrumental):
        content = max(content, title, artist * 0.72)
    elif dual > 0.0:
        content = max(content, dual, title * 0.64)
    else:
        content = max(content, lyric * 0.76, title * 0.64)
    media_lead = ((50.0 if dual > 0.0 else 30.0) + 15.0) if bool(compact_lyric_cover_enabled) else 0.0
    u = max(0.0, float(unit))
    required = (9.0 + media_lead + 32.0) * u + content
    min_width = 470.0 * u if dual > 0.0 else 430.0 * u
    max_width = 760.0 * u if dual > 0.0 else 610.0 * u
    return max(min_width, min(max_width, required))


def stack_target_width(
    main_kind,
    *,
    music_width: float,
    empty_text_width: float,
    empty_time_width: float,
    default_stack_width: float = 560.0,
    unit: float = 1.0,
) -> float:
    kind = _kind_key(main_kind)
    if kind == "music":
        return float(music_width)
    if kind == "empty_text":
        return float(empty_text_width)
    if kind == "time":
        return float(empty_time_width)
    return float(default_stack_width) * max(0.0, float(unit))


def plugin_focus_weights(
    count: int,
    *,
    expanded: bool,
    from_index: int,
    target_index: int,
    progress: float,
    expand_active: bool = True,
) -> list[float]:
    total = max(0, int(count))
    if total <= 0:
        return []
    if not bool(expanded) or not bool(expand_active):
        return [1.0] * total
    source = max(0, min(int(from_index), total - 1))
    target = max(0, min(int(target_index), total - 1))
    amount = max(0.0, min(1.0, float(progress)))
    weights = [0.0] * total
    if source == target:
        weights[target] = 1.0
    else:
        weights[source] = 1.0 - amount
        weights[target] = amount
    return weights


def plugin_slot_widths(
    count: int,
    total_width: float,
    *,
    expanded: bool,
    from_index: int,
    target_index: int,
    progress: float,
    expand_active: bool = True,
    focus_width_fraction: float = 0.40,
    focus_width_cap: float = 220.0,
) -> list[float]:
    total = max(0, int(count))
    if total <= 0:
        return []
    width = float(total_width)
    if not bool(expanded) or total == 1 or not bool(expand_active):
        return [width / total] * total
    focus_cap = min(width * max(0.0, float(focus_width_fraction)), float(focus_width_cap))
    focus_width = max(width / total, focus_cap)
    compact_slot = (width - focus_width) / max(1, total - 1)
    source = max(0, min(int(from_index), total - 1))
    target = max(0, min(int(target_index), total - 1))
    amount = max(0.0, min(1.0, float(progress)))
    from_widths = [compact_slot] * total
    target_widths = [compact_slot] * total
    from_widths[source] = focus_width
    target_widths[target] = focus_width
    return [from_widths[index] + (target_widths[index] - from_widths[index]) * amount for index in range(total)]


def plugin_item_rects(rect: QRectF, widths: list[float] | tuple[float, ...]) -> list[tuple[int, QRectF]]:
    value = QRectF(rect)
    cells: list[tuple[int, QRectF]] = []
    x = float(value.left())
    slot_widths = list(widths or [])
    for index, width in enumerate(slot_widths):
        right = float(value.right()) if index == len(slot_widths) - 1 else x + float(width)
        cells.append((index, QRectF(x, float(value.top()), right - x, float(value.height()))))
        x = right
    return cells


def plugin_active_focus(
    count: int,
    *,
    expanded: bool,
    current_index: int,
    from_index: int,
    target_index: int,
    progress: float,
) -> int:
    total = max(0, int(count))
    if total <= 0:
        return 0
    if not bool(expanded):
        return max(0, min(int(current_index), total - 1))
    source = max(0, min(int(from_index), total - 1))
    target = max(0, min(int(target_index), total - 1))
    amount = max(0.0, min(1.0, float(progress)))
    if amount >= 1.0:
        return target
    return source if amount < 0.5 else target


def plugin_focus_index_for_x(
    cells: list[tuple[int, QRectF]] | tuple[tuple[int, QRectF], ...],
    x: float,
) -> int | None:
    values = list(cells or [])
    if not values:
        return None
    position = float(x)
    for index, cell in values:
        rect = QRectF(cell)
        if rect.left() <= position <= rect.right():
            return int(index)
    nearest = min(values, key=lambda item: abs(QRectF(item[1]).center().x() - position))
    return int(nearest[0])


@dataclass(frozen=True)
class PluginStripLayout:
    active_focus: int
    weights: list[float]
    widths: list[float]
    cells: list[tuple[int, QRectF]]


def plugin_strip_layout(
    rect: QRectF,
    count: int,
    *,
    expanded: bool,
    current_index: int,
    from_index: int,
    target_index: int,
    progress: float,
    expand_active: bool = True,
    focus_width_fraction: float = 0.40,
    focus_width_cap: float = 220.0,
) -> PluginStripLayout:
    total = max(0, int(count))
    active_focus = plugin_active_focus(
        total,
        expanded=expanded,
        current_index=current_index,
        from_index=from_index,
        target_index=target_index,
        progress=progress,
    )
    weights = plugin_focus_weights(
        total,
        expanded=expanded,
        from_index=from_index,
        target_index=target_index,
        progress=progress,
        expand_active=expand_active,
    )
    widths = plugin_slot_widths(
        total,
        QRectF(rect).width(),
        expanded=expanded,
        from_index=from_index,
        target_index=target_index,
        progress=progress,
        expand_active=expand_active,
        focus_width_fraction=focus_width_fraction,
        focus_width_cap=focus_width_cap,
    )
    return PluginStripLayout(
        active_focus=active_focus,
        weights=weights,
        widths=widths,
        cells=plugin_item_rects(rect, widths),
    )


@dataclass(frozen=True)
class MainSizeFacts:
    stack_width: float
    expanded: bool = False
    taskbar_compact: bool = False
    music_expanded_layout_style: str = "standard"
    taskbar_compact_music_size: tuple[float, float] | None = None
    stick_notch_active: bool = False
    stick_notch_music_height: float = 0.0
    top_notch_active: bool = False
    top_notch_music_height: float = 0.0
    compact_music_content_height: float = 46.0
    enabled_plugin_slot_count: int = 1
    taskbar_compact_main_height: float = 48.0
    empty_text_size: tuple[float, float] = (56.0, 40.0)
    empty_time_size: tuple[float, float] = (74.0, 40.0)
    empty_bar_size: tuple[float, float] = (40.0, 6.0)
    manual_width: float = 0.0
    unit: float = 1.0


def main_size_for_kind(main_kind, facts: MainSizeFacts) -> tuple[float, float]:
    kind = _kind_key(main_kind)
    u = max(0.0, float(facts.unit))
    stack_width = float(facts.stack_width)
    compact_height = float(facts.taskbar_compact_main_height)

    if kind == "music":
        if facts.expanded:
            style = str(facts.music_expanded_layout_style or "standard")
            return stack_width, 212.0 * u if style == "dual_panel" else 176.0 * u
        if facts.taskbar_compact and facts.taskbar_compact_music_size is not None:
            return float(facts.taskbar_compact_music_size[0]), float(facts.taskbar_compact_music_size[1])
        if facts.stick_notch_active:
            return stack_width, float(facts.stick_notch_music_height)
        if facts.top_notch_active:
            return stack_width, float(facts.top_notch_music_height)
        return stack_width, float(facts.compact_music_content_height)

    if kind == "plugin":
        if facts.expanded:
            return stack_width, 48.0 * u
        width = 420.0 * u / 3.0 * max(1, int(facts.enabled_plugin_slot_count))
        height = compact_height if facts.taskbar_compact else 48.0 * u
        return taskbar_width_with_manual(
            width,
            compact_active=facts.taskbar_compact,
            manual_width=facts.manual_width,
            unit=u,
        ), height

    if kind == "message":
        width = stack_width if facts.expanded else min(430.0 * u, stack_width)
        height = compact_height if facts.taskbar_compact else 48.0 * u
        return taskbar_width_with_manual(
            width,
            compact_active=facts.taskbar_compact,
            manual_width=facts.manual_width,
            unit=u,
        ), height

    if kind == "empty_text":
        width, height = facts.empty_text_size
        return float(width), compact_height if facts.taskbar_compact else float(height)

    if kind == "time":
        width, height = facts.empty_time_size
        return float(width), compact_height if facts.taskbar_compact else float(height)

    width, height = facts.empty_bar_size
    return float(width), float(height)


def taskbar_anchor_main_size(
    main_kind,
    *,
    expanded: bool,
    base_size: tuple[float, float] | None = None,
    compact_music_size: tuple[float, float] | None = None,
    taskbar_rect: QRectF | None = None,
    unit: float = 1.0,
) -> tuple[float, float] | None:
    kind = _kind_key(main_kind)
    if not expanded and kind == "music" and compact_music_size is not None:
        return float(compact_music_size[0]), float(compact_music_size[1])
    if base_size is None:
        return None
    width, height = float(base_size[0]), float(base_size[1])
    if not expanded and kind in {"plugin", "message", "empty_text", "time"}:
        return width, taskbar_compact_main_height(taskbar_rect, fallback=height, unit=unit)
    return width, height


def expanded_stack_height(
    main_kind,
    main_height: float,
    *,
    expanded: bool,
    include_popups: bool,
    plugin_enabled: bool,
    plugin_panel_visible: bool,
    plugin_panel_height: float,
    message_count: int,
    folded_message_count: int,
    popup_count: int,
    unit: float = 1.0,
) -> float:
    u = max(0.0, float(unit))
    gap = 10.0 * u
    height = float(main_height)
    kind = _kind_key(main_kind)

    if expanded and plugin_enabled:
        if kind == "music":
            height += gap + 48.0 * u
            if plugin_panel_visible:
                height += gap + float(plugin_panel_height)
        elif kind == "plugin" and plugin_panel_visible:
            height += gap + float(plugin_panel_height)

    if expanded:
        count = max(0, int(folded_message_count if kind == "message" else message_count))
        if count:
            height += gap + 48.0 * u * count + gap * max(0, count - 1)
    elif include_popups:
        count = 0 if kind == "message" else min(1, max(0, int(popup_count)))
        if count:
            height += gap + 46.0 * u * count
    return height


def time_visible_module_for_width(
    width: float,
    current_module: str,
    *,
    breakpoint: float = 560.0,
    modules: tuple[str, ...] = ("countdown", "alarm"),
) -> str:
    if float(width or 0.0) >= float(breakpoint):
        return "all"
    module = str(current_module or "countdown")
    return module if module in modules else "countdown"


def plugin_panel_height(
    width: float | None,
    *,
    time_mode: str,
    last_width: float = 0.0,
    breakpoint: float = 560.0,
    unit: float = 1.0,
) -> float:
    u = max(0.0, float(unit))
    mode = str(time_mode or "dashboard")
    if mode == "alarm_editor":
        return 190.0 * u
    panel_w = float(width if width is not None else last_width or 0.0)
    if panel_w > 0.0 and panel_w < float(breakpoint):
        return 156.0 * u
    return 236.0 * u


def plugin_panel_shell_rect(panel_rect: QRectF, strip_rect: QRectF | None = None) -> QRectF:
    panel = QRectF(panel_rect)
    strip = QRectF(strip_rect) if strip_rect is not None else QRectF()
    if strip.isNull():
        return panel
    left = min(panel.left(), strip.left())
    top = min(panel.top(), strip.top())
    right = max(panel.right(), strip.right())
    bottom = max(panel.bottom(), strip.bottom())
    return QRectF(left, top, right - left, bottom - top)


def plugin_panel_layout(
    rect: QRectF,
    *,
    visible_module: str,
    breakpoint: float = 560.0,
    unit: float = 1.0,
) -> dict[str, QRectF | str]:
    value = QRectF(rect)
    u = max(0.0, float(unit))
    outer_pad = 10.0 * u
    nav_h = max(30.0, 34.0 * u)
    nav = QRectF(value.left() + outer_pad, value.top() + 8.0 * u, value.width() - outer_pad * 2.0, nav_h)
    body = QRectF(
        value.left() + 18.0 * u,
        nav.bottom() + 12.0 * u,
        value.width() - 36.0 * u,
        max(88.0 * u, value.bottom() - nav.bottom() - 30.0 * u),
    )
    reminders_h = max(26.0, 30.0 * u)
    reminders = QRectF(body.left(), value.bottom() - reminders_h - 12.0 * u, body.width(), reminders_h)
    action_w = min(104.0 * u, max(82.0 * u, body.width() * 0.28))
    action = QRectF(body.right() - action_w, reminders.top() - 34.0 * u, action_w, max(24.0, 26.0 * u))
    icon_size = min(82.0 * u, max(58.0 * u, body.height() - 26.0 * u))
    icon = QRectF(body.left() + 4.0 * u, body.top() + 6.0 * u, icon_size, icon_size)
    text = QRectF(
        icon.right() + 18.0 * u,
        body.top() + 2.0 * u,
        max(120.0 * u, body.right() - icon.right() - 24.0 * u),
        max(56.0 * u, action.top() - body.top() - 4.0 * u),
    )
    module_top = body.top() + 5.0 * u
    module_bottom = max(module_top + 82.0 * u, body.bottom() - 15.0 * u)
    module_h = min(116.0 * u, module_bottom - module_top)
    gap = 9.0 * u
    clock_w = min(170.0 * u, max(94.0 * u, body.width() * 0.21))
    countdown_w = min(420.0 * u, max(150.0 * u, body.width() * 0.32))
    alarm_w = max(76.0 * u, body.width() - clock_w - countdown_w - gap * 2.0)
    time_clock = QRectF(body.left(), module_top, clock_w, module_h)
    time_countdown = QRectF(time_clock.right() + gap, module_top, countdown_w, module_h)
    time_alarm = QRectF(time_countdown.right() + gap, module_top, alarm_w, module_h)
    time_layout_mode = "single" if value.width() < float(breakpoint) else "triple"
    visible = str(visible_module or "countdown")
    if time_layout_mode == "single":
        single_rect = QRectF(body.left(), module_top, body.width(), module_h)
        hidden = QRectF(single_rect.left(), single_rect.top(), 0.0, single_rect.height())
        time_clock = QRectF(single_rect if visible == "clock" else hidden)
        time_countdown = QRectF(single_rect if visible == "countdown" else hidden)
        time_alarm = QRectF(single_rect if visible == "alarm" else hidden)
    return {
        "nav": nav,
        "body": body,
        "icon": icon,
        "text": text,
        "action": action,
        "reminders": reminders,
        "time_clock": time_clock,
        "time_countdown": time_countdown,
        "time_alarm": time_alarm,
        "time_layout_mode": time_layout_mode,
        "time_visible_module": visible,
    }


def plugin_panel_time_layout(
    rect: QRectF,
    *,
    visible_module: str,
    breakpoint: float = 560.0,
    stretch_breakpoint: float = 960.0,
    unit: float = 1.0,
) -> dict[str, QRectF | str]:
    value = QRectF(rect)
    u = max(0.0, float(unit))
    title = QRectF(value.left() + 12.0 * u, value.top() + 6.0 * u, value.width() - 24.0 * u, 18.0 * u)
    body_top = title.bottom() + 4.0 * u
    body = QRectF(
        value.left() + 18.0 * u,
        body_top,
        value.width() - 36.0 * u,
        max(88.0 * u, value.bottom() - body_top - 5.0 * u),
    )
    module_top = body.top() + 2.0 * u
    module_h = min(205.0 * u, max(92.0 * u, body.bottom() - module_top - 1.0 * u))
    gap = 12.0 * u
    pager = QRectF()
    visible = str(visible_module or "countdown")
    if value.width() < float(breakpoint):
        pager_w = 14.0 * u
        usable_w = max(260.0 * u, body.width() - pager_w - 8.0 * u)
        clock_w = min(182.0 * u, max(132.0 * u, usable_w * 0.47))
        right_w = max(118.0 * u, usable_w - clock_w - gap)
        time_clock = QRectF(body.left(), module_top, clock_w, module_h)
        right_slot = QRectF(time_clock.right() + gap, module_top, right_w, module_h)
        hidden = QRectF(right_slot.left(), right_slot.top(), 0.0, right_slot.height())
        time_countdown = QRectF(right_slot if visible == "countdown" else hidden)
        time_alarm = QRectF(right_slot if visible == "alarm" else hidden)
        pager = QRectF(body.right() - pager_w, module_top, pager_w, module_h)
        mode = "double"
    else:
        stretch = value.width() >= float(stretch_breakpoint)
        if stretch:
            clock_w = min(190.0 * u, max(142.0 * u, body.width() * 0.20))
            countdown_w = min(400.0 * u, max(240.0 * u, body.width() * 0.34))
        else:
            clock_w = min(180.0 * u, max(126.0 * u, body.width() * 0.24))
            countdown_w = min(205.0 * u, max(138.0 * u, body.width() * 0.28))
        alarm_w = max(176.0 * u, body.width() - clock_w - countdown_w - gap * 2.0)
        time_clock = QRectF(body.left(), module_top, clock_w, module_h)
        time_countdown = QRectF(time_clock.right() + gap, module_top, countdown_w, module_h)
        time_alarm = QRectF(time_countdown.right() + gap, module_top, alarm_w, module_h)
        mode = "stretch" if stretch else "triple"
        visible = "all"
    return {
        "nav": title,
        "time_title": title,
        "body": body,
        "icon": QRectF(),
        "text": QRectF(),
        "action": QRectF(),
        "reminders": QRectF(),
        "time_clock": time_clock,
        "time_countdown": time_countdown,
        "time_alarm": time_alarm,
        "time_pager": pager,
        "time_layout_mode": mode,
        "time_visible_module": visible,
    }


def taskbar_message_effective_style(
    preferred_style: str,
    count: int,
    *,
    selected_zone: QRectF | None = None,
    unit: float = 1.0,
) -> str:
    style = str(preferred_style or "strip").strip().lower()
    if style != "block":
        return "strip"
    if int(count) > 3:
        return "strip"
    u = max(0.0, float(unit))
    selected = QRectF(selected_zone) if selected_zone is not None else QRectF()
    if not selected.isNull() and selected.width() < 160.0 * u:
        return "strip"
    return "block"


def taskbar_message_rect(
    main_rect: QRectF,
    index: int,
    count: int,
    *,
    preferred_style: str,
    selected_zone: QRectF | None = None,
    compact_active: bool,
    notification_outer_gap: float,
    unit: float = 1.0,
) -> QRectF:
    rect = QRectF(main_rect)
    if rect.isNull():
        return QRectF()
    u = max(0.0, float(unit))
    style = taskbar_message_effective_style(
        preferred_style,
        count,
        selected_zone=selected_zone,
        unit=u,
    )
    total = max(1, int(count))
    slot = max(0, min(int(index), total - 1))
    row_gap = 4.0 * u
    if style == "block":
        gap = 4.0 * u
        width = max(24.0 * u, min(34.0 * u, rect.height() * 0.86))
        height = width
    else:
        gap = 3.0 * u
        width = max(4.0 * u, min(6.0 * u, rect.height() * 0.15))
        height = max(20.0 * u, rect.height() * 0.72)
    if compact_active:
        gap = float(notification_outer_gap)
        x = rect.right() + gap + slot * (width + row_gap)
    else:
        side = "right" if slot % 2 == 0 else "left"
        side_index = slot // 2
        if side == "right":
            x = rect.right() + gap + side_index * (width + row_gap)
        else:
            x = rect.left() - gap - width - side_index * (width + row_gap)
    y = rect.center().y() - height / 2.0
    return QRectF(x, y, width, height)


def message_dot_rect(
    main_rect: QRectF,
    index: int,
    count: int,
    *,
    direction: int = 1,
    taskbar_compact_active: bool = False,
    preferred_taskbar_style: str = "strip",
    selected_taskbar_zone: QRectF | None = None,
    taskbar_notification_outer_gap: float = 9.0,
    unit: float = 1.0,
) -> QRectF:
    rect = QRectF(main_rect)
    if rect.isNull():
        return QRectF()
    u = max(0.0, float(unit))
    if taskbar_compact_active:
        return taskbar_message_rect(
            rect,
            index,
            count,
            preferred_style=preferred_taskbar_style,
            selected_zone=selected_taskbar_zone,
            compact_active=True,
            notification_outer_gap=taskbar_notification_outer_gap,
            unit=u,
        )
    dot_size = 7.0 * u
    gap = 8.0 * u
    total = max(0, int(count))
    total_w = total * dot_size + max(0, total - 1) * gap
    x = rect.center().x() - total_w / 2.0 + int(index) * (dot_size + gap)
    if int(direction) >= 0:
        y = rect.bottom() + 1.0 * u
    else:
        y = rect.top() - 1.0 * u - dot_size
    return QRectF(x, y, dot_size, dot_size)


def plugin_alert_main_rect_screen_pressure(
    main_rect: QRectF,
    *,
    target_width: float,
    screen_left: float,
    screen_right: float,
    unit: float = 1.0,
) -> str:
    rect = QRectF(main_rect)
    if rect.isNull():
        return ""
    u = max(0.0, float(unit))
    is_shrunk = rect.width() < float(target_width) - 2.0 * u
    if not is_shrunk:
        return ""
    if rect.right() > float(screen_right) + 1.0 * u:
        return "right"
    if rect.left() < float(screen_left) - 1.0 * u:
        return "left"
    return ""


def plugin_alert_circle_size(height: float, *, unit: float = 1.0) -> float:
    return max(34.0 * max(0.0, float(unit)), float(height))


def plugin_alert_x_for_side(main_rect: QRectF, width: float, gap: float, side: str) -> float:
    rect = QRectF(main_rect)
    if str(side or "").strip().lower() == "left":
        return rect.left() - float(gap) - float(width)
    return rect.right() + float(gap)


def plugin_alert_side_space(
    main_rect: QRectF,
    gap: float,
    side: str,
    screen_left: float,
    screen_right: float,
) -> float:
    rect = QRectF(main_rect)
    if str(side or "").strip().lower() == "left":
        return max(0.0, rect.left() - float(gap) - float(screen_left))
    return max(0.0, float(screen_right) - rect.right() - float(gap))


def plugin_alert_row_fits(
    main_rect: QRectF,
    width: float,
    gap: float,
    row_gap: float,
    total: int,
    side: str,
    screen_left: float,
    screen_right: float,
) -> bool:
    rect = QRectF(main_rect)
    count = max(1, int(total))
    row_width = float(width) * count + float(row_gap) * max(0, count - 1)
    if str(side or "").strip().lower() == "left":
        return rect.left() - float(gap) - row_width >= float(screen_left)
    return rect.right() + float(gap) + row_width <= float(screen_right)


def plugin_alert_side_for_row(
    main_rect: QRectF,
    width: float,
    gap: float,
    row_gap: float,
    total: int,
    preferred_side: str,
    screen_left: float,
    screen_right: float,
) -> str:
    preferred = "left" if str(preferred_side or "").strip().lower() == "left" else "right"
    alternate = "right" if preferred == "left" else "left"
    if plugin_alert_row_fits(main_rect, width, gap, row_gap, total, preferred, screen_left, screen_right):
        return preferred
    if plugin_alert_row_fits(main_rect, width, gap, row_gap, total, alternate, screen_left, screen_right):
        return alternate
    preferred_space = plugin_alert_side_space(main_rect, gap, preferred, screen_left, screen_right)
    alternate_space = plugin_alert_side_space(main_rect, gap, alternate, screen_left, screen_right)
    return preferred if preferred_space >= alternate_space else alternate


def plugin_alert_width_for_row(
    main_rect: QRectF,
    width: float,
    gap: float,
    row_gap: float,
    total: int,
    side: str,
    screen_left: float,
    screen_right: float,
    *,
    height: float = 42.0,
    unit: float = 1.0,
) -> float:
    count = max(1, int(total))
    value = float(width)
    if count <= 1:
        return value
    space = plugin_alert_side_space(main_rect, gap, side, screen_left, screen_right)
    available = max(0.0, space - float(row_gap) * (count - 1))
    compact_min = plugin_alert_circle_size(height, unit=unit)
    if available <= 0.0:
        return compact_min
    item_width = min(value, available / count)
    if item_width < value - 1.0 * max(0.0, float(unit)):
        return compact_min
    return item_width


def plugin_alert_x_for_slot(
    main_rect: QRectF,
    width: float,
    gap: float,
    row_gap: float,
    side: str,
    slot: int,
) -> float:
    rect = QRectF(main_rect)
    value_slot = max(0, int(slot))
    if str(side or "").strip().lower() == "left":
        return rect.left() - float(gap) - float(width) - value_slot * (float(width) + float(row_gap))
    return rect.right() + float(gap) + value_slot * (float(width) + float(row_gap))


def rect_x_fits_screen(x: float, width: float, screen_left: float, screen_right: float) -> bool:
    left = float(x)
    return left >= float(screen_left) and left + float(width) <= float(screen_right)


def clamp_rect_x_to_screen(x: float, width: float, screen_left: float, screen_right: float) -> float:
    return max(float(screen_left), min(float(x), float(screen_right) - float(width)))


def plugin_alert_uses_ring(rect: QRectF, *, unit: float = 1.0) -> bool:
    value = QRectF(rect)
    if value.isNull():
        return False
    return value.width() <= plugin_alert_circle_size(value.height(), unit=unit) + 0.5 * max(0.0, float(unit))


def taskbar_compact_plugin_alert_rect(
    main_rect: QRectF,
    *,
    slot: int,
    total: int,
    preferred_style: str,
    notification_outer_gap: float,
    unit: float = 1.0,
) -> QRectF:
    rect = QRectF(main_rect)
    if rect.isNull():
        return QRectF()
    u = max(0.0, float(unit))
    style = str(preferred_style or "rail").strip().lower()
    if style == "block":
        rail_w = max(30.0 * u, min(42.0 * u, rect.height() * 0.90))
        rail_h = rail_w
    else:
        rail_w = max(4.0 * u, min(7.0 * u, rect.height() * 0.16))
        rail_h = max(22.0 * u, rect.height() * 0.72)
    count = max(1, int(total))
    value_slot = max(0, min(int(slot), count - 1))
    row_gap = 4.0 * u
    x = rect.right() + float(notification_outer_gap) + value_slot * (rail_w + row_gap)
    y = rect.center().y() - rail_h / 2.0
    return QRectF(x, y, rail_w, rail_h)


def plugin_alert_rect(
    main_rect: QRectF,
    *,
    slot: int,
    total: int,
    width: float,
    row_width: float,
    height: float,
    gap: float,
    row_gap: float,
    screen_left: float,
    screen_right: float,
    window_height: float,
    pressure: str = "",
    same_time_row: bool = False,
    min_width: float = 128.0,
    vertical_margin: float = 8.0,
    unit: float = 1.0,
) -> QRectF:
    rect = QRectF(main_rect)
    if rect.isNull():
        return QRectF()
    u = max(0.0, float(unit))
    value_width = float(width)
    value_height = float(height)
    value_gap = float(gap)
    value_row_gap = float(row_gap)
    left_bound = float(screen_left)
    right_bound = float(screen_right)
    count = max(1, int(total))
    value_slot = max(0, min(int(slot), count - 1))
    minimum_width = float(min_width)

    right_x = rect.right() + value_gap
    left_x = rect.left() - value_gap - value_width
    right_space = max(0.0, right_bound - right_x)
    left_space = max(0.0, rect.left() - value_gap - left_bound)
    side = "right"
    x = right_x
    pressure_side = str(pressure or "").strip().lower()

    if pressure_side:
        value_width = plugin_alert_circle_size(value_height, unit=u)
        preferred = "left" if pressure_side == "right" else "right"
        side = preferred
        x = plugin_alert_x_for_side(rect, value_width, value_gap, preferred)
        if not rect_x_fits_screen(x, value_width, left_bound, right_bound):
            side = "right" if preferred == "left" else "left"
            x = plugin_alert_x_for_side(rect, value_width, value_gap, side)
        x = clamp_rect_x_to_screen(x, value_width, left_bound, right_bound)
    elif rect_x_fits_screen(right_x, value_width, left_bound, right_bound):
        side = "right"
        x = right_x
    elif rect_x_fits_screen(left_x, value_width, left_bound, right_bound):
        side = "left"
        x = left_x
    elif right_space >= left_space and right_space >= minimum_width:
        side = "right"
        value_width = max(minimum_width, min(value_width, right_space))
        x = right_x
    elif left_space >= minimum_width:
        side = "left"
        value_width = max(minimum_width, min(value_width, left_space))
        x = rect.left() - value_gap - value_width
    else:
        side = "right" if right_space >= left_space else "left"
        usable = max(minimum_width, right_bound - left_bound)
        value_width = min(value_width, usable)
        x = clamp_rect_x_to_screen(rect.left() - value_gap - value_width, value_width, left_bound, right_bound)

    if count > 1:
        desired_row_width = max(float(row_width), value_width)
        if pressure_side:
            desired_row_width = value_width
        side = plugin_alert_side_for_row(
            rect,
            desired_row_width,
            value_gap,
            value_row_gap,
            count,
            side,
            left_bound,
            right_bound,
        )
        split_sides = False
        alternate = "left" if side == "right" else "right"
        row_fits_side = plugin_alert_row_fits(rect, desired_row_width, value_gap, value_row_gap, count, side, left_bound, right_bound)
        row_fits_alternate = plugin_alert_row_fits(rect, desired_row_width, value_gap, value_row_gap, count, alternate, left_bound, right_bound)
        right_single = rect_x_fits_screen(
            plugin_alert_x_for_side(rect, desired_row_width, value_gap, "right"),
            desired_row_width,
            left_bound,
            right_bound,
        )
        left_single = rect_x_fits_screen(
            plugin_alert_x_for_side(rect, desired_row_width, value_gap, "left"),
            desired_row_width,
            left_bound,
            right_bound,
        )
        if count == 2 and same_time_row and not row_fits_side and not row_fits_alternate:
            value_width = plugin_alert_circle_size(value_height, unit=u)
            side = "right" if right_space >= left_space else "left"
            if not plugin_alert_row_fits(rect, value_width, value_gap, value_row_gap, count, side, left_bound, right_bound):
                alternate = "left" if side == "right" else "right"
                if plugin_alert_row_fits(rect, value_width, value_gap, value_row_gap, count, alternate, left_bound, right_bound):
                    side = alternate
        elif count == 2 and not pressure_side and not row_fits_side and not row_fits_alternate and right_single and left_single:
            value_width = desired_row_width
            side = "right" if value_slot == 0 else "left"
            x = plugin_alert_x_for_side(rect, value_width, value_gap, side)
            split_sides = True
        else:
            value_width = plugin_alert_width_for_row(
                rect,
                desired_row_width,
                value_gap,
                value_row_gap,
                count,
                side,
                left_bound,
                right_bound,
                height=value_height,
                unit=u,
            )
        if not split_sides and plugin_alert_uses_ring(QRectF(0.0, 0.0, value_width, value_height), unit=u):
            alternate = "left" if side == "right" else "right"
            row_fits_side = plugin_alert_row_fits(rect, value_width, value_gap, value_row_gap, count, side, left_bound, right_bound)
            row_fits_alternate = plugin_alert_row_fits(rect, value_width, value_gap, value_row_gap, count, alternate, left_bound, right_bound)
            right_single = rect_x_fits_screen(plugin_alert_x_for_side(rect, value_width, value_gap, "right"), value_width, left_bound, right_bound)
            left_single = rect_x_fits_screen(plugin_alert_x_for_side(rect, value_width, value_gap, "left"), value_width, left_bound, right_bound)
            if count == 2 and not same_time_row and not row_fits_side and not row_fits_alternate and right_single and left_single:
                side = "right" if value_slot == 0 else "left"
                x = plugin_alert_x_for_side(rect, value_width, value_gap, side)
                split_sides = True
            else:
                side = plugin_alert_side_for_row(
                    rect,
                    value_width,
                    value_gap,
                    value_row_gap,
                    count,
                    side,
                    left_bound,
                    right_bound,
                )
        if not split_sides:
            x = plugin_alert_x_for_slot(rect, value_width, value_gap, value_row_gap, side, value_slot)
        x = clamp_rect_x_to_screen(x, value_width, left_bound, right_bound)

    margin = float(vertical_margin)
    y = rect.center().y() - value_height / 2.0
    y = max(margin, min(float(window_height) - value_height - margin, y))
    return QRectF(x, y, value_width, value_height)


def taskbar_follow_refresh_due(
    *,
    now: float,
    last_refresh_at: float,
    interval: float,
) -> bool:
    try:
        last = float(last_refresh_at or 0.0)
    except (TypeError, ValueError):
        last = 0.0
    if last <= 0.0:
        return True
    return float(now) - last >= max(0.012, float(interval))


def taskbar_follow_force_visible_active(
    *,
    now: float,
    force_visible_until: float,
) -> bool:
    try:
        until = float(force_visible_until or 0.0)
    except (TypeError, ValueError):
        until = 0.0
    if until <= 0.0:
        return False
    return float(now) < until


def available_taskbar_slot_rects(
    task_area: QRectF,
    occupied_rects: list[QRectF],
    *,
    min_size: float = 72.0,
) -> list[QRectF]:
    area = QRectF(task_area)
    if area.isNull():
        return []
    min_extent = max(0.0, float(min_size))
    horizontal = area.width() >= area.height()
    if horizontal:
        segments: list[tuple[float, float]] = [(area.left(), area.right())]
        occupied_iter = sorted((QRectF(rect).intersected(area) for rect in occupied_rects), key=lambda rect: rect.left())
        for occupied in occupied_iter:
            if occupied.isNull() or occupied.height() < area.height() * 0.24:
                continue
            segments = _subtract_segment_occupancy(segments, occupied.left(), occupied.right(), min_extent)
        return [
            QRectF(left, area.top(), right - left, area.height())
            for left, right in segments
            if right - left >= min_extent
        ]

    segments = [(area.top(), area.bottom())]
    occupied_iter = sorted((QRectF(rect).intersected(area) for rect in occupied_rects), key=lambda rect: rect.top())
    for occupied in occupied_iter:
        if occupied.isNull() or occupied.width() < area.width() * 0.24:
            continue
        segments = _subtract_segment_occupancy(segments, occupied.top(), occupied.bottom(), min_extent)
    return [
        QRectF(area.left(), top, area.width(), bottom - top)
        for top, bottom in segments
        if bottom - top >= min_extent
    ]


def side_taskbar_slot_rects(
    task_area: QRectF,
    available_slots: list[QRectF],
    *,
    min_size: float = 72.0,
    max_center_gap: float = 96.0,
) -> list[QRectF]:
    area = QRectF(task_area)
    if area.isNull():
        return []
    slots = [QRectF(slot).intersected(area) for slot in available_slots if slot is not None and not slot.isNull()]
    slots = [slot for slot in slots if not slot.isNull()]
    horizontal = area.width() >= area.height()
    center = area.center().x() if horizontal else area.center().y()
    min_extent = max(0.0, float(min_size))
    gap = min(max(0.0, float(max_center_gap)), (area.width() if horizontal else area.height()) * 0.12)

    if not slots or (len(slots) == 1 and abs((_slot_center(slots[0], horizontal)) - center) < min_extent):
        return _split_taskbar_area_around_center(area, horizontal, center, gap, min_extent)

    before = [slot for slot in slots if _slot_center(slot, horizontal) < center]
    after = [slot for slot in slots if _slot_center(slot, horizontal) > center]
    side_slots: list[QRectF] = []
    if before:
        side_slots.append(max(before, key=lambda slot: _slot_extent(slot, horizontal)))
    if after:
        side_slots.append(max(after, key=lambda slot: _slot_extent(slot, horizontal)))
    if len(side_slots) == 2:
        side_slots.sort(key=lambda slot: _slot_center(slot, horizontal))
        return side_slots
    return _split_taskbar_area_around_center(area, horizontal, center, gap, min_extent)


def taskbar_drop_zone_rect(
    taskbar_rect: QRectF,
    desired_width: float | None = None,
    *,
    unit: float = 1.0,
) -> QRectF:
    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull():
        return QRectF()
    u = max(0.0, float(unit))
    inset = 5.0 * u
    if taskbar.width() >= taskbar.height():
        width = min(float(desired_width or 420.0 * u), max(180.0 * u, taskbar.width() - inset * 2.0))
        height = max(24.0 * u, taskbar.height() - inset * 2.0)
        return QRectF(taskbar.center().x() - width / 2.0, taskbar.center().y() - height / 2.0, width, height)
    width = max(24.0 * u, taskbar.width() - inset * 2.0)
    height = min(float(desired_width or 360.0 * u), max(180.0 * u, taskbar.height() - inset * 2.0))
    return QRectF(taskbar.center().x() - width / 2.0, taskbar.center().y() - height / 2.0, width, height)


def build_taskbar_drop_zones(
    taskbar_rect: QRectF,
    task_area: QRectF,
    occupied_rects: list[QRectF],
    *,
    desired_width: float | None = None,
    unit: float = 1.0,
) -> list[QRectF]:
    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull():
        return []
    base = QRectF(task_area)
    if base.isNull():
        base = QRectF(taskbar)
    slots = available_taskbar_slot_rects(base, occupied_rects, min_size=72.0 * float(unit))
    slots = side_taskbar_slot_rects(base, slots, min_size=72.0 * float(unit), max_center_gap=96.0 * float(unit))
    zones = [taskbar_drop_zone_rect(slot, desired_width=desired_width, unit=unit) for slot in slots]
    zones = [zone for zone in zones if not zone.isNull()]
    if zones:
        return zones
    fallback = taskbar_drop_zone_rect(taskbar, desired_width=desired_width, unit=unit)
    return [] if fallback.isNull() else [fallback]


def select_taskbar_drop_zone(zones: list[QRectF], global_pos: QPoint | QPointF | None) -> QRectF:
    clean = [QRectF(zone) for zone in zones if zone is not None and not zone.isNull()]
    if not clean:
        return QRectF()
    if global_pos is None:
        return clean[0]
    px = float(global_pos.x())
    py = float(global_pos.y())

    def score(zone: QRectF) -> float:
        dx = abs(px - zone.center().x())
        dy = abs(py - zone.center().y())
        inside_bonus = -10000.0 if zone.contains(QPointF(px, py)) else 0.0
        return dx * dx + dy * dy + inside_bonus

    return min(clean, key=score)


def taskbar_attach_hot_zone_rect(screen_geometry, *, unit: float = 1.0) -> QRectF:
    try:
        geo = QRectF(screen_geometry)
    except Exception:
        return QRectF()
    if geo.isNull():
        return QRectF()
    u = max(0.0, float(unit))
    width = max(320.0 * u, min(640.0 * u, geo.width() * 0.34))
    height = max(96.0 * u, min(180.0 * u, geo.height() * 0.15))
    zone = QRectF(0.0, 0.0, width, height)
    zone.moveCenter(geo.center())
    return zone


def taskbar_attach_hot_zone_host_rect(hot_zone: QRectF, *, unit: float = 1.0) -> QRectF:
    zone = QRectF(hot_zone)
    if zone.isNull():
        return QRectF()
    u = max(0.0, float(unit))
    pad_x = max(12.0 * u, zone.width() * 0.035)
    pad_y = max(10.0 * u, zone.height() * 0.08)
    return zone.adjusted(-pad_x, -pad_y, pad_x, pad_y)


def taskbar_attach_hot_zone_contains(hot_zone: QRectF, global_pos: QPoint | QPointF) -> bool:
    zone = QRectF(hot_zone)
    if zone.isNull():
        return False
    return zone.contains(QPointF(global_pos))


def taskbar_attach_hot_zone_accepts_rect(hot_zone: QRectF, main_rect: QRectF) -> bool:
    zone = QRectF(hot_zone)
    rect = QRectF(main_rect)
    if zone.isNull() or rect.isNull() or rect.width() <= 1.0 or rect.height() <= 1.0:
        return False
    return zone.contains(rect.center())


def taskbar_left_handle_rect(main_rect: QRectF, *, unit: float = 1.0) -> QRectF:
    rect = QRectF(main_rect)
    if rect.isNull():
        return QRectF()
    u = max(0.0, float(unit))
    width = max(2.0 * u, min(4.0 * u, rect.height() * 0.09))
    height = max(18.0 * u, rect.height() * 0.62)
    gap = max(4.0 * u, min(7.0 * u, rect.height() * 0.16))
    return QRectF(rect.left() - gap - width, rect.center().y() - height / 2.0, width, height)


def taskbar_left_handle_frame_rect(main_rect: QRectF, *, unit: float = 1.0) -> QRectF:
    rect = QRectF(main_rect)
    if rect.isNull():
        return QRectF()
    handle = taskbar_left_handle_rect(rect, unit=unit)
    if handle.isNull():
        return QRectF()
    u = max(0.0, float(unit))
    pad_x = max(4.0 * u, handle.width() * 1.4)
    overlap = max(5.0 * u, min(9.0 * u, rect.height() * 0.20))
    right = rect.left() + overlap
    left = handle.left() - pad_x
    width = max(12.0 * u, right - left)
    return QRectF(
        right - width,
        rect.top(),
        width,
        rect.height(),
    )


def taskbar_left_handle_hover_keeps_visible(hovered_key: str) -> bool:
    key = str(hovered_key or "")
    return key == "taskbar:left-handle" or key in {"music", "plugin", "message"} or key.startswith("message:")


def taskbar_left_handle_visible(
    *,
    taskbar_compact_active: bool,
    expanded: bool,
    now: float,
    visible_until: float,
    dragging: bool,
    drag_press_key: str,
    hovered_key: str,
) -> bool:
    if not bool(taskbar_compact_active) or bool(expanded):
        return False
    try:
        if float(now) <= float(visible_until or 0.0):
            return True
    except (TypeError, ValueError):
        pass
    if bool(dragging) and str(drag_press_key or "") == "taskbar:left-handle":
        return True
    return taskbar_left_handle_hover_keeps_visible(hovered_key)


def taskbar_left_handle_visibility_progress(
    *,
    taskbar_compact_active: bool,
    expanded: bool,
    now: float,
    visible_until: float,
    dragging: bool,
    drag_press_key: str,
    hovered_key: str,
    fade_duration: float = 0.9,
) -> float:
    if not bool(taskbar_compact_active) or bool(expanded):
        return 0.0
    if bool(dragging) and str(drag_press_key or "") == "taskbar:left-handle":
        return 1.0
    if taskbar_left_handle_hover_keeps_visible(hovered_key):
        return 1.0
    try:
        remaining = float(visible_until or 0.0) - float(now)
    except (TypeError, ValueError):
        return 0.0
    if remaining <= 0.0:
        return 0.0
    return max(0.0, min(1.0, remaining / max(0.001, float(fade_duration))))


def extend_taskbar_left_handle_visible_until(
    current_visible_until: float,
    now: float,
    *,
    duration: float = 3.0,
) -> float:
    try:
        current = float(current_visible_until or 0.0)
    except (TypeError, ValueError):
        current = 0.0
    return max(current, float(now) + max(0.0, float(duration)))


def taskbar_manual_width_for_left_edge(
    main_rect: QRectF,
    track_rect: QRectF,
    left_x: float,
    *,
    unit: float = 1.0,
) -> float:
    rect = QRectF(main_rect)
    track = QRectF(track_rect)
    if rect.isNull() or track.isNull():
        return 0.0
    u = max(0.0, float(unit))
    min_width = max(120.0 * u, rect.height() + 16.0 * u)
    max_width = max(min_width, track.width())
    right_edge = max(track.left() + min_width, min(track.right(), rect.right()))
    left_edge = max(track.left(), min(float(left_x), right_edge - min_width))
    return max(min_width, min(max_width, right_edge - left_edge))


def taskbar_drag_track_band_rect(taskbar_rect: QRectF, *, unit: float = 1.0) -> QRectF:
    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull():
        return QRectF()
    u = max(0.0, float(unit))
    thickness = min(float(taskbar.width()), float(taskbar.height()))
    margin = max(28.0 * u, thickness * 1.8)
    if taskbar.width() >= taskbar.height():
        return taskbar.adjusted(0.0, -margin, 0.0, margin)
    return taskbar.adjusted(-margin, 0.0, margin, 0.0)


def taskbar_drag_should_stay_on_track(
    taskbar_rect: QRectF,
    global_pos: QPoint | QPointF,
    *,
    unit: float = 1.0,
) -> bool:
    band = taskbar_drag_track_band_rect(taskbar_rect, unit=unit)
    if band.isNull():
        return False
    return band.contains(QPointF(global_pos))


def taskbar_drag_should_detach_to_free(
    taskbar_rect: QRectF,
    screen_geometry,
    delta: QPoint | QPointF,
    *,
    left_handle_drag: bool = False,
    unit: float = 1.0,
) -> bool:
    if bool(left_handle_drag):
        return False
    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull():
        return False
    try:
        geo = QRectF(screen_geometry)
    except Exception:
        geo = QRectF()
    u = max(0.0, float(unit))
    thickness = min(float(taskbar.width()), float(taskbar.height()))
    threshold = max(46.0 * u, min(92.0 * u, thickness * 1.25))
    dx = float(delta.x())
    dy = float(delta.y())
    if taskbar.width() >= taskbar.height():
        bottom_edge = geo.isNull() or taskbar.center().y() >= geo.center().y()
        away = -dy if bottom_edge else dy
        lateral = abs(dx)
    else:
        right_edge = not geo.isNull() and taskbar.center().x() >= geo.center().x()
        away = -dx if right_edge else dx
        lateral = abs(dy)
    if away < threshold:
        return False
    return away >= lateral * 0.55


def build_taskbar_drag_target(
    *,
    global_pos: QPoint | QPointF,
    taskbar_rect: QRectF,
    track_rect: QRectF,
    screen_geometry,
    window_width: float,
    main_kind: object,
    stack_width: float,
    main_width: float,
    main_height: float,
    main_top: float,
    direction: int,
    drag_press_main_offset: QPointF | QPoint | None = None,
    bottom_extension: float = 0.0,
) -> TaskbarDragTarget | None:
    taskbar = QRectF(taskbar_rect)
    track = QRectF(track_rect)
    if taskbar.isNull() or track.isNull() or screen_geometry is None:
        return None
    try:
        screen = QRectF(screen_geometry)
    except Exception:
        return None
    if screen.isNull():
        return None
    try:
        offset_x = float(drag_press_main_offset.x()) if drag_press_main_offset is not None else 0.0
    except Exception:
        offset_x = 0.0
    desired_center = float(global_pos.x()) + float(main_width) / 2.0 - offset_x
    ratio = position_ratio_for_center(track, main_width, desired_center)
    center_x = center_for_position_ratio(track, main_width, ratio)
    center_y = taskbar.center().y()
    zone = intended_zone_rect(center_x, center_y, main_width, main_height)
    pos = QPoint(
        int(round(center_x - float(window_width) / 2.0)),
        int(round(center_y - float(main_top) - float(main_height) / 2.0)),
    )
    pos = clamp_taskbar_window_pos(
        pos,
        main_top,
        main_width,
        main_height,
        screen,
        window_width,
        bottom_extension,
    )
    return TaskbarDragTarget(
        taskbar_rect=QRectF(taskbar),
        track_rect=QRectF(track),
        zone_rect=QRectF(zone),
        window_pos=QPoint(pos),
        main_kind=main_kind,
        stack_width=float(stack_width),
        main_width=float(main_width),
        main_height=float(main_height),
        main_top=float(main_top),
        direction=int(direction),
        position_ratio=float(ratio),
        manual_width=0.0,
    )


def build_taskbar_left_resize_target(
    *,
    global_pos: QPoint | QPointF,
    main_rect: QRectF,
    taskbar_rect: QRectF,
    track_rect: QRectF,
    screen_geometry,
    window_width: float,
    main_kind: object,
    stack_width: float,
    main_width: float,
    main_height: float,
    main_top: float,
    direction: int,
    unit: float = 1.0,
    bottom_extension: float = 0.0,
) -> TaskbarDragTarget | None:
    rect = QRectF(main_rect)
    taskbar = QRectF(taskbar_rect)
    track = QRectF(track_rect)
    if rect.isNull() or taskbar.isNull() or track.isNull() or screen_geometry is None:
        return None
    width = taskbar_manual_width_for_left_edge(rect, track, float(global_pos.x()), unit=unit)
    if width <= 0.0:
        return None
    final_main_width = max(float(main_width), float(width))
    right_edge = max(track.left() + final_main_width, min(track.right(), rect.right()))
    left_edge = max(track.left(), right_edge - final_main_width)
    if left_edge + final_main_width > track.right():
        left_edge = max(track.left(), track.right() - final_main_width)
    center_x = left_edge + final_main_width / 2.0
    center_y = taskbar.center().y()
    ratio = position_ratio_for_center(track, final_main_width, center_x)
    zone = QRectF(left_edge, center_y - float(main_height) / 2.0, final_main_width, float(main_height))
    pos = QPoint(
        int(round(center_x - float(window_width) / 2.0)),
        int(round(center_y - float(main_top) - float(main_height) / 2.0)),
    )
    pos = clamp_taskbar_window_pos(
        pos,
        main_top,
        final_main_width,
        main_height,
        screen_geometry,
        window_width,
        bottom_extension,
    )
    return TaskbarDragTarget(
        taskbar_rect=QRectF(taskbar),
        track_rect=QRectF(track),
        zone_rect=QRectF(zone),
        window_pos=QPoint(pos),
        main_kind=main_kind,
        stack_width=float(stack_width),
        main_width=float(final_main_width),
        main_height=float(main_height),
        main_top=float(main_top),
        direction=int(direction),
        position_ratio=float(ratio),
        manual_width=float(width),
    )


def bottom_release_reaches_taskbar_threshold(
    anchor_rect: QRectF,
    release_global_pos,
    screen_geometry,
    taskbar_rect: QRectF,
    min_threshold: float = 24.0,
    max_threshold: float = 96.0,
    taskbar_height_factor: float = 1.6,
) -> bool:
    anchor = QRectF(anchor_rect)
    taskbar = QRectF(taskbar_rect)
    if anchor.isNull() or taskbar.isNull() or screen_geometry is None:
        return False
    try:
        screen = QRectF(screen_geometry)
    except Exception:
        return False
    if screen.isNull():
        return False
    threshold = max(
        float(min_threshold),
        min(float(max_threshold), float(taskbar.height()) * float(taskbar_height_factor)),
    )
    release_y = anchor.bottom()
    point_y = _point_y(release_global_pos)
    if point_y is not None:
        release_y = max(release_y, point_y)
    return release_y >= screen.bottom() - threshold


def should_snap_above_taskbar_for_release(
    anchor_rect: QRectF,
    release_global_pos,
    screen_geometry,
    taskbar_rect: QRectF,
    *,
    taskbar_allowed: bool,
    auto_hide_enabled: bool,
    min_threshold: float = 24.0,
    max_threshold: float = 96.0,
    taskbar_height_factor: float = 1.6,
) -> bool:
    return (
        bool(taskbar_allowed)
        and bool(auto_hide_enabled)
        and bottom_release_reaches_taskbar_threshold(
            anchor_rect,
            release_global_pos,
            screen_geometry,
            taskbar_rect,
            min_threshold=min_threshold,
            max_threshold=max_threshold,
            taskbar_height_factor=taskbar_height_factor,
        )
    )


def should_enter_taskbar_for_visible_taskbar_release(
    anchor_rect: QRectF,
    release_global_pos,
    screen_geometry,
    taskbar_rect: QRectF,
    *,
    taskbar_allowed: bool,
    auto_hide_enabled: bool,
    min_threshold: float = 24.0,
    max_threshold: float = 96.0,
    taskbar_height_factor: float = 1.6,
) -> bool:
    return (
        bool(taskbar_allowed)
        and not bool(auto_hide_enabled)
        and bottom_release_reaches_taskbar_threshold(
            anchor_rect,
            release_global_pos,
            screen_geometry,
            taskbar_rect,
            min_threshold=min_threshold,
            max_threshold=max_threshold,
            taskbar_height_factor=taskbar_height_factor,
        )
    )


def tasker_release_point_reaches_bottom_slop(
    release_global_pos,
    screen_geometry,
    taskbar_rect: QRectF,
    *,
    taskbar_compact_active: bool,
    min_slop: float = 8.0,
    max_slop: float = 18.0,
    taskbar_height_factor: float = 0.35,
) -> bool:
    if not bool(taskbar_compact_active):
        return False
    point_y = _point_y(release_global_pos)
    if point_y is None:
        return False
    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull() or screen_geometry is None:
        return False
    try:
        screen = QRectF(screen_geometry)
    except Exception:
        return False
    if screen.isNull():
        return False
    slop = max(float(min_slop), min(float(max_slop), float(taskbar.height()) * float(taskbar_height_factor)))
    return point_y >= screen.bottom() - slop


def should_snap_top_notch_for_release(
    anchor_rect: QRectF,
    release_global_pos,
    screen_geometry,
    *,
    mydockfinder_available: bool,
    finder_mode: bool,
    min_threshold: float = 18.0,
    max_threshold: float = 84.0,
    anchor_height_factor: float = 1.4,
) -> bool:
    if bool(mydockfinder_available) or bool(finder_mode):
        return False
    anchor = QRectF(anchor_rect)
    if anchor.isNull() or screen_geometry is None:
        return False
    try:
        screen = QRectF(screen_geometry)
    except Exception:
        return False
    if screen.isNull():
        return False
    threshold = max(
        float(min_threshold),
        min(float(max_threshold), float(anchor.height()) * float(anchor_height_factor)),
    )
    release_y = anchor.top()
    point_y = _point_y(release_global_pos)
    if point_y is not None:
        release_y = min(release_y, point_y)
    return release_y <= screen.top() + threshold


def decide_standalone_release_action(
    anchor_rect: QRectF,
    release_global_pos,
    screen_geometry,
    taskbar_rect: QRectF,
    *,
    taskbar_allowed: bool,
    auto_hide_enabled: bool,
    mydockfinder_available: bool,
    finder_mode: bool,
    taskbar_compact_active: bool,
    current_location_mode: str = "free",
    bottom_min_threshold: float = 24.0,
    bottom_max_threshold: float = 96.0,
    top_min_threshold: float = 18.0,
    top_max_threshold: float = 84.0,
) -> StandaloneReleaseDecision:
    if bool(finder_mode) or bool(taskbar_compact_active):
        return StandaloneReleaseDecision("none")
    anchor = QRectF(anchor_rect)
    if anchor.isNull() or screen_geometry is None:
        return StandaloneReleaseDecision("none")
    try:
        screen = QRectF(screen_geometry)
    except Exception:
        return StandaloneReleaseDecision("none")
    if screen.isNull():
        return StandaloneReleaseDecision("none")
    taskbar = QRectF(taskbar_rect)

    if should_snap_top_notch_for_release(
        anchor,
        release_global_pos,
        screen,
        mydockfinder_available=mydockfinder_available,
        finder_mode=finder_mode,
        min_threshold=top_min_threshold,
        max_threshold=top_max_threshold,
    ):
        return StandaloneReleaseDecision("top")

    if should_enter_taskbar_for_visible_taskbar_release(
        anchor,
        release_global_pos,
        screen,
        taskbar,
        taskbar_allowed=taskbar_allowed,
        auto_hide_enabled=auto_hide_enabled,
        min_threshold=bottom_min_threshold,
        max_threshold=bottom_max_threshold,
    ):
        return StandaloneReleaseDecision("tasker")

    if should_snap_above_taskbar_for_release(
        anchor,
        release_global_pos,
        screen,
        taskbar,
        taskbar_allowed=taskbar_allowed,
        auto_hide_enabled=auto_hide_enabled,
        min_threshold=bottom_min_threshold,
        max_threshold=bottom_max_threshold,
    ):
        return StandaloneReleaseDecision("stick")

    mode = str(current_location_mode or "free").strip().casefold()
    return StandaloneReleaseDecision("free", exiting_edge_notch_mode=mode in {"stick", "top"})


def standalone_release_direction_for_anchor(
    anchor_rect: QRectF,
    screen_geometry,
    *,
    expand_direction: str = "auto",
    auto_expand_bounds: bool = True,
    expanded: bool = False,
    expanded_down_height: float | None = None,
    bottom_margin: float = 14.0,
) -> int:
    mode = str(expand_direction or "auto").lower()
    if mode == "down":
        return 1
    if mode == "up":
        return -1
    if not bool(auto_expand_bounds) or screen_geometry is None:
        return 1
    anchor = QRectF(anchor_rect)
    if anchor.isNull():
        return 1
    try:
        screen = QRectF(screen_geometry)
    except Exception:
        return 1
    if screen.isNull():
        return 1
    cutoff = float(screen.top()) + float(screen.height()) * (2.0 / 3.0)
    if anchor.center().y() >= cutoff:
        return -1
    if not bool(expanded):
        return 1
    if expanded_down_height is not None and anchor.top() + float(expanded_down_height) > screen.bottom() - float(bottom_margin):
        return -1
    return 1


def build_standalone_top_notch_target(
    *,
    anchor_rect: QRectF,
    screen_geometry,
    window_width: float,
    main_kind: object,
    stack_width: float,
    main_width: float,
    main_height: float,
    main_top: float,
) -> StandalonePlacementTarget | None:
    anchor = QRectF(anchor_rect)
    if anchor.isNull() or screen_geometry is None:
        return None
    try:
        screen = QRectF(screen_geometry)
    except Exception:
        return None
    if screen.isNull():
        return None
    desired_center_x = max(
        screen.left() + float(main_width) / 2.0,
        min(screen.right() - float(main_width) / 2.0, anchor.center().x()),
    )
    window_x = desired_center_x - float(window_width) / 2.0
    window_x = max(screen.left(), min(screen.right() - float(window_width), window_x))
    pos = QPoint(int(round(window_x)), int(round(screen.top() - float(main_top))))
    return StandalonePlacementTarget(
        window_pos=QPoint(pos),
        main_kind=main_kind,
        stack_width=float(stack_width),
        main_width=float(main_width),
        main_height=float(main_height),
        main_top=float(main_top),
        main_left=float(window_width) / 2.0 - float(main_width) / 2.0,
        direction=1,
    )


def build_standalone_position_preset_target(
    *,
    preset: str,
    screen_geometry,
    window_width: float,
    main_kind: object,
    stack_width: float,
    main_width: float,
    main_height: float,
    main_top: float,
    main_left: float | None = None,
    margin: float = 8.0,
) -> StandalonePlacementTarget | None:
    value = canonical_position_preset(preset)
    if value == "last" or screen_geometry is None:
        return None
    try:
        screen = QRectF(screen_geometry)
    except Exception:
        return None
    if screen.isNull():
        return None
    left_offset = float(main_left) if main_left is not None else float(window_width) / 2.0 - float(main_width) / 2.0
    edge_margin = float(margin)
    left = float(screen.left())
    top = float(screen.top())
    right = left + float(screen.width())
    bottom = top + float(screen.height())
    if value.endswith("_left"):
        x = left + edge_margin - left_offset
    elif value.endswith("_right"):
        x = right - edge_margin - left_offset - float(main_width)
    else:
        x = left + float(screen.width()) / 2.0 - left_offset - float(main_width) / 2.0
    direction = -1 if value.startswith("bottom_") else 1
    if direction < 0:
        y = bottom - edge_margin - float(main_top) - float(main_height)
    else:
        y = top + edge_margin - float(main_top)
    pos = clamp_standalone_window_pos(
        x,
        y,
        left_offset,
        main_top,
        main_width,
        main_height,
        screen,
        margin=edge_margin,
    )
    return StandalonePlacementTarget(
        window_pos=QPoint(pos),
        main_kind=main_kind,
        stack_width=float(stack_width),
        main_width=float(main_width),
        main_height=float(main_height),
        main_top=float(main_top),
        main_left=float(left_offset),
        direction=direction,
    )


def build_standalone_free_release_target(
    *,
    anchor_rect: QRectF,
    screen_geometry,
    window_width: float,
    main_kind: object,
    stack_width: float,
    main_width: float,
    main_height: float,
    main_top: float,
    direction: int,
    bottom_extension: float = 0.0,
    margin: float = 6.0,
) -> StandalonePlacementTarget | None:
    anchor = QRectF(anchor_rect)
    if anchor.isNull() or screen_geometry is None:
        return None
    main_left = float(window_width) / 2.0 - float(main_width) / 2.0
    pos = clamp_standalone_window_pos(
        anchor.left() - main_left,
        anchor.top() - float(main_top),
        main_left,
        main_top,
        main_width,
        main_height,
        screen_geometry,
        margin=margin,
        bottom_extension=bottom_extension,
    )
    return StandalonePlacementTarget(
        window_pos=QPoint(pos),
        main_kind=main_kind,
        stack_width=float(stack_width),
        main_width=float(main_width),
        main_height=float(main_height),
        main_top=float(main_top),
        main_left=float(main_left),
        direction=int(direction),
    )


def build_taskbar_follow_target(
    *,
    screen: object,
    visible_taskbar: QRectF,
    follow_taskbar: QRectF,
    track_rect: QRectF,
    screen_geometry,
    window_width: float,
    main_kind: object,
    stack_width: float,
    main_width: float,
    main_height: float,
    main_top: float,
    direction: int,
    current_position_ratio: float = 0.5,
    target_center_x: float | None = None,
    position_ratio: float | None = None,
    auto_hide_follow: bool = False,
    hidden_autohide: bool = False,
    visible_follow_lift: float = 0.0,
    bottom_extension: float = 0.0,
) -> TaskbarFollowTarget:
    visible = QRectF(visible_taskbar)
    follow = QRectF(follow_taskbar)
    track = QRectF(track_rect)
    if track.isNull():
        track = QRectF(visible)
    if target_center_x is not None:
        ratio = position_ratio_for_center(track, main_width, float(target_center_x))
    elif position_ratio is not None:
        ratio = max(0.0, min(1.0, float(position_ratio)))
    else:
        ratio = max(0.0, min(1.0, float(current_position_ratio)))
    center_x = center_for_position_ratio(track, main_width, ratio)
    center_y = follow.center().y()
    if bool(auto_hide_follow) and not bool(hidden_autohide):
        center_y -= max(0.0, float(visible_follow_lift))
    zone = intended_zone_rect(center_x, center_y, main_width, main_height)
    new_pos = QPoint(
        int(round(center_x - float(window_width) / 2.0)),
        int(round(center_y - float(main_top) - float(main_height) / 2.0)),
    )
    new_pos = clamp_taskbar_window_pos(
        new_pos,
        main_top,
        main_width,
        main_height,
        screen_geometry,
        window_width,
        bottom_extension,
    )
    follow_differs_from_base = _rect_key(follow) != _rect_key(visible)
    partial_shell_follow = bool(auto_hide_follow) and follow_differs_from_base and not bool(hidden_autohide)
    return TaskbarFollowTarget(
        screen=screen,
        visible_taskbar=QRectF(visible),
        follow_taskbar=QRectF(follow),
        track_rect=QRectF(track),
        zone_rect=QRectF(zone),
        window_pos=QPoint(new_pos),
        main_kind=main_kind,
        stack_width=float(stack_width),
        main_width=float(main_width),
        main_height=float(main_height),
        main_top=float(main_top),
        direction=int(direction),
        position_ratio=float(ratio),
        hidden_autohide=bool(hidden_autohide),
        auto_hide_follow=bool(auto_hide_follow),
        partial_shell_follow=bool(partial_shell_follow),
    )


def build_taskbar_follow_refresh_plan(
    target: TaskbarFollowTarget,
    *,
    stable_geometry: bool,
    previous_hidden: bool,
    force_visible: bool,
    taskbar_autohide_hidden: bool,
    material_elapsed: float,
    material_interval: float,
) -> TaskbarFollowRefreshPlan:
    if target.visible_taskbar.isNull() or target.track_rect.isNull():
        return TaskbarFollowRefreshPlan(valid=False)
    animate_follow = (
        bool(target.auto_hide_follow)
        and not bool(target.partial_shell_follow)
        and (bool(target.hidden_autohide) or bool(previous_hidden))
    )
    material_due = float(material_elapsed) >= max(0.20, float(material_interval))
    refresh_material = (
        (bool(force_visible) or not bool(taskbar_autohide_hidden))
        and (not bool(stable_geometry) or material_due)
    )
    return TaskbarFollowRefreshPlan(
        valid=True,
        animate_follow=animate_follow,
        refresh_material=refresh_material,
    )


def build_taskbar_above_follow_refresh_plan(
    *,
    above_follow_active: bool,
    location_mode: str,
    finder_mode: bool,
    taskbar_rect: QRectF,
    auto_hide_enabled: bool,
    target_anchor_rect: QRectF,
    current_anchor_rect: QRectF,
) -> TaskbarAboveFollowRefreshPlan:
    if not bool(above_follow_active):
        return TaskbarAboveFollowRefreshPlan(action="inactive")

    mode = str(location_mode or "free").strip().lower()
    if mode != "stick" or bool(finder_mode):
        return TaskbarAboveFollowRefreshPlan(
            action="clear_invalid_mode",
            clear_above_taskbar_follow=True,
        )

    taskbar = QRectF(taskbar_rect)
    if taskbar.isNull():
        return TaskbarAboveFollowRefreshPlan(action="missing_taskbar")

    if not bool(auto_hide_enabled):
        return TaskbarAboveFollowRefreshPlan(
            action="clear_autohide_disabled",
            clear_above_taskbar_follow=True,
            force_location_mode="free" if mode == "stick" else "",
        )

    anchor = QRectF(target_anchor_rect)
    if anchor.isNull():
        anchor = QRectF(current_anchor_rect)
    if anchor.isNull():
        anchor = QRectF(taskbar.center().x(), taskbar.top(), 1.0, 1.0)

    return TaskbarAboveFollowRefreshPlan(
        action="settle",
        settle_above_taskbar=True,
        anchor_rect=QRectF(anchor),
    )


def build_taskbar_dock_plan(
    *,
    screen_available: bool,
    taskbar_rect: QRectF,
    probe_x: float,
    target_center_x: float | None = None,
    dock_animation_requested: bool = False,
) -> TaskbarDockPlan:
    taskbar = QRectF(taskbar_rect)
    if not bool(screen_available) or taskbar.isNull():
        return TaskbarDockPlan(valid=False, visible_taskbar=QRectF())
    try:
        target_x = float(target_center_x) if target_center_x is not None else float(probe_x)
    except (TypeError, ValueError):
        target_x = float(probe_x)
    animate = bool(dock_animation_requested)
    return TaskbarDockPlan(
        valid=True,
        visible_taskbar=QRectF(taskbar),
        target_center_x=target_x,
        mark_follow_force_visible=True,
        force_visible=True,
        use_current_visibility=False,
        refresh_material=True,
        animate=animate,
        dock=animate,
        consume_dock_animation_request=True,
    )


def build_standalone_above_taskbar_target(
    *,
    visible_taskbar: QRectF,
    follow_taskbar: QRectF,
    track_rect: QRectF,
    screen_geometry,
    window_width: float,
    main_kind: object,
    stack_width: float,
    main_width: float,
    main_height: float,
    main_top: float,
    target_center_x: float,
    current_position_ratio: float = 0.5,
    main_left: float | None = None,
    gap: float = 0.0,
    auto_hide_follow: bool = False,
    hidden_autohide: bool = False,
    previous_hidden_autohide: bool = False,
    animate_requested: bool = False,
    bottom_extension: float = 0.0,
    margin: float = 6.0,
) -> StandaloneAboveTaskbarTarget:
    visible = QRectF(visible_taskbar)
    follow = QRectF(follow_taskbar)
    track = QRectF(track_rect)
    if track.isNull():
        track = QRectF(visible)
    try:
        screen = QRectF(screen_geometry)
    except Exception:
        screen = QRectF()
    direction = -1 if follow.center().y() >= screen.center().y() else 1
    left = float(main_left) if main_left is not None else float(window_width) / 2.0 - float(main_width) / 2.0
    desired_center_x = max(
        track.left() + float(main_width) / 2.0,
        min(track.right() - float(main_width) / 2.0, float(target_center_x)),
    )
    if track.isNull():
        ratio = max(0.0, min(1.0, float(current_position_ratio)))
    else:
        ratio = position_ratio_for_center(track, main_width, desired_center_x)
    center_x = center_for_position_ratio(track, main_width, ratio)
    clearance = max(0.0, float(gap))
    if direction < 0:
        main_top_global = follow.top() - clearance - float(main_height)
    else:
        main_top_global = follow.bottom() + clearance
    new_pos = clamp_standalone_window_pos(
        center_x - left - float(main_width) / 2.0,
        main_top_global - float(main_top),
        left,
        main_top,
        main_width,
        main_height,
        screen_geometry,
        margin=margin,
        bottom_extension=bottom_extension,
    )
    new_pos = nudge_window_pos_outside_taskbar(new_pos, follow, main_top, main_height, direction, clearance)
    follow_differs_from_base = _rect_key(follow) != _rect_key(visible)
    partial_shell_follow = bool(auto_hide_follow) and follow_differs_from_base and not bool(hidden_autohide)
    animate_follow = bool(animate_requested) or (
        bool(auto_hide_follow)
        and not partial_shell_follow
        and (bool(hidden_autohide) or bool(previous_hidden_autohide))
    )
    return StandaloneAboveTaskbarTarget(
        visible_taskbar=QRectF(visible),
        follow_taskbar=QRectF(follow),
        track_rect=QRectF(track),
        window_pos=QPoint(new_pos),
        main_kind=main_kind,
        stack_width=float(stack_width),
        main_width=float(main_width),
        main_height=float(main_height),
        main_top=float(main_top),
        main_left=float(left),
        direction=int(direction),
        position_ratio=float(ratio),
        gap=float(clearance),
        hidden_autohide=bool(hidden_autohide),
        auto_hide_follow=bool(auto_hide_follow),
        partial_shell_follow=bool(partial_shell_follow),
        animate_follow=bool(animate_follow),
    )


def _rect_key(rect: QRectF) -> tuple[int, int, int, int]:
    value = QRectF(rect)
    if value.isNull():
        return (0, 0, 0, 0)
    return (
        int(round(value.left())),
        int(round(value.top())),
        int(round(value.width())),
        int(round(value.height())),
    )


def _point_y(point) -> float | None:
    if point is None:
        return None
    try:
        return float(point.y())
    except Exception:
        return None


def _subtract_segment_occupancy(
    segments: list[tuple[float, float]],
    occupied_start: float,
    occupied_end: float,
    min_size: float,
) -> list[tuple[float, float]]:
    next_segments: list[tuple[float, float]] = []
    for start, end in segments:
        if occupied_end <= start or occupied_start >= end:
            next_segments.append((start, end))
            continue
        if occupied_start - start >= min_size:
            next_segments.append((start, min(end, occupied_start)))
        if end - occupied_end >= min_size:
            next_segments.append((max(start, occupied_end), end))
    return next_segments


def _slot_center(slot: QRectF, horizontal: bool) -> float:
    return slot.center().x() if horizontal else slot.center().y()


def _slot_extent(slot: QRectF, horizontal: bool) -> float:
    return slot.width() if horizontal else slot.height()


def _split_taskbar_area_around_center(
    area: QRectF,
    horizontal: bool,
    center: float,
    gap: float,
    min_size: float,
) -> list[QRectF]:
    if horizontal:
        left = QRectF(area.left(), area.top(), max(0.0, center - gap / 2.0 - area.left()), area.height())
        right = QRectF(center + gap / 2.0, area.top(), max(0.0, area.right() - center - gap / 2.0), area.height())
    else:
        left = QRectF(area.left(), area.top(), area.width(), max(0.0, center - gap / 2.0 - area.top()))
        right = QRectF(area.left(), center + gap / 2.0, area.width(), max(0.0, area.bottom() - center - gap / 2.0))
    return [slot for slot in (left, right) if _slot_extent(slot, horizontal) >= min_size]
