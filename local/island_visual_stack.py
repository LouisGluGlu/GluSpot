# -*- coding: utf-8 -*-
"""Pure stack visual planning primitives for the GluSpot island."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar

from PySide6.QtCore import QRectF


class IslandKind(Enum):
    MUSIC = "music"
    PLUGIN = "plugin"
    PLUGIN_PANEL = "plugin_panel"
    PLUGIN_ALERT = "plugin_alert"
    MESSAGE = "message"
    EMPTY_TEXT = "empty_text"
    TIME = "time"
    THIN_BAR = "thin_bar"


@dataclass
class StackVisual:
    key: str
    kind: IslandKind
    rect: QRectF
    folded: bool = False
    message: object | None = None
    z_index: int = 0


@dataclass(frozen=True)
class MotionSnapshot:
    rect: QRectF
    opacity: float = 1.0


TMotion = TypeVar("TMotion")


@dataclass(frozen=True)
class StackPaintItem(Generic[TMotion]):
    visual: StackVisual
    motion: TMotion
    isolate_painter_state: bool = True


@dataclass(frozen=True)
class StackFramePaintPlan(Generic[TMotion]):
    visible_items: tuple[tuple[StackVisual, TMotion], ...]
    paint_items: tuple[StackPaintItem[TMotion], ...]
    mask_rects: tuple[QRectF, ...]


@dataclass(frozen=True)
class StackPaintContextPlan:
    rect: QRectF
    opacity: float
    paint_taskbar_left_handle: bool = False
    paint_edge_notch_backing: bool = False
    clip_edge_notch: bool = False


@dataclass(frozen=True)
class InteractionMaskUpdatePlan:
    action: str = "skip"
    mask_rect: QRectF = field(default_factory=QRectF)
    record_update_at: bool = False


@dataclass(frozen=True)
class BarrageBackgroundStylePlan:
    dark_background: bool
    sample_luma: bool = False
    record_checked_at: bool = False


@dataclass(frozen=True)
class BarrageLayoutSource:
    message: object
    text: str
    action_key: str
    started_at: float
    serial: int
    text_width: float


@dataclass(frozen=True)
class BarragePaintItem:
    message: object
    text: str
    action_key: str
    text_rect: QRectF
    hit_rect: QRectF
    lane: int
    progress: float


@dataclass(frozen=True)
class BarrageSceneLayoutPlan:
    paint_items: tuple[BarragePaintItem, ...]
    hit_rects: Mapping[str, QRectF]
    alive_preview_messages: tuple[object, ...]
    alive_fullscreen_messages: tuple[object, ...]


@dataclass(frozen=True)
class ActiveMotionStepPlan:
    create_missing_motion: bool
    snap_to_target: bool
    target_rect: QRectF
    target_opacity: float = 1.0
    response: float = 0.34
    damping: float = 0.76


@dataclass(frozen=True)
class StaleMotionStepPlan:
    drop_immediately: bool
    target_rect: QRectF | None = None
    target_opacity: float = 0.0
    response: float = 0.24
    damping: float = 0.76
    delete_below_opacity: float = 0.025


MessageKeyFn = Callable[[object], str]


def main_visual_identity(
    main_kind: IslandKind,
    messages: Sequence[object],
    message_key: MessageKeyFn,
) -> tuple[str, object | None]:
    if main_kind == IslandKind.MUSIC:
        return "music", None
    if main_kind == IslandKind.PLUGIN:
        return "plugin", None
    if main_kind == IslandKind.EMPTY_TEXT:
        return "empty-text", None
    if main_kind == IslandKind.TIME:
        return "time", None
    if main_kind == IslandKind.MESSAGE and messages:
        message = messages[0]
        return f"message:{message_key(message)}", message
    return "thin", None


def message_visual_key(message: object, message_key: MessageKeyFn, *, folded: bool = False) -> str:
    prefix = "message-dot" if folded else "message"
    return f"{prefix}:{message_key(message)}"


def candidate_dot_messages(
    main_kind: IslandKind,
    messages: Sequence[object],
    folded_limit: int,
) -> list[object]:
    limit = max(0, int(folded_limit))
    if limit <= 0:
        return []
    if main_kind == IslandKind.MESSAGE:
        return list(messages[1 : 1 + limit])
    return list(messages[:limit])


def side_notification_messages(
    *,
    main_kind: IslandKind,
    messages: Sequence[object],
    folded_limit: int,
    taskbar_side_notifications: bool,
    plugin_alert_count: int,
    notification_limit: int,
) -> tuple[list[object], int]:
    dot_candidates = candidate_dot_messages(main_kind, messages, folded_limit)
    if not taskbar_side_notifications:
        return dot_candidates, max(0, int(plugin_alert_count))
    remaining_slots = max(0, int(notification_limit) - max(0, int(plugin_alert_count)))
    dot_messages = dot_candidates[:remaining_slots]
    return dot_messages, max(0, int(plugin_alert_count)) + len(dot_messages)


def expanded_message_rows(
    *,
    main_kind: IslandKind,
    messages: Sequence[object],
    folded_limit: int,
) -> list[object]:
    if main_kind == IslandKind.MESSAGE:
        return list(messages[1 : 1 + max(0, int(folded_limit))])
    return list(messages)


def popup_message_rows(
    *,
    main_kind: IslandKind,
    messages: Sequence[object],
    now: float,
    taskbar_side_notifications: bool,
    expanded: bool,
) -> list[object]:
    if expanded or main_kind == IslandKind.MESSAGE or taskbar_side_notifications:
        return []
    visible = [message for message in messages if float(getattr(message, "popup_until", 0.0) or 0.0) > now]
    if main_kind == IslandKind.MESSAGE and messages:
        main_message = messages[0]
        visible = [message for message in visible if message is not main_message]
    return visible[:1]


def plugin_alert_key(plugin_alert: Sequence[Any]) -> str:
    try:
        plugin_id = str(plugin_alert[6] or "")
    except (IndexError, TypeError):
        plugin_id = ""
    return f"plugin-alert:{plugin_id}"


def taskbar_should_drop_stale_motion_key(key: str, *, taskbar_compact_active: bool) -> bool:
    if not taskbar_compact_active:
        return False
    value = str(key or "")
    return value.startswith(("plugin-alert:", "taskbar-overlay:", "message-dot:"))


def active_visual_motion_params(
    *,
    finder_mode: bool,
    taskbar_compact_active: bool,
    stack_bounce_active: bool,
) -> tuple[float, float]:
    if finder_mode:
        return (0.19 if stack_bounce_active else 0.24, 0.84)
    if taskbar_compact_active:
        return (0.16 if stack_bounce_active else 0.22, 0.86)
    return (0.28 if stack_bounce_active else 0.34, 0.76)


def active_motion_step_plan(
    visual: StackVisual,
    *,
    motion_exists: bool,
    drag_hides_auxiliary_visuals: bool,
    finder_mode: bool,
    taskbar_compact_active: bool,
    stack_bounce_active: bool,
) -> ActiveMotionStepPlan:
    target = QRectF(visual.rect)
    create_missing_motion = not bool(motion_exists)
    if (
        getattr(visual, "z_index", 0) == 10
        and drag_hides_auxiliary_visuals
        and not target.isNull()
    ):
        return ActiveMotionStepPlan(
            create_missing_motion=create_missing_motion,
            snap_to_target=True,
            target_rect=target,
            target_opacity=1.0,
            response=1.0,
            damping=1.0,
        )

    response, damping = active_visual_motion_params(
        finder_mode=finder_mode,
        taskbar_compact_active=taskbar_compact_active,
        stack_bounce_active=stack_bounce_active,
    )
    return ActiveMotionStepPlan(
        create_missing_motion=create_missing_motion,
        snap_to_target=False,
        target_rect=target,
        target_opacity=1.0,
        response=response,
        damping=damping,
    )


def stack_paint_items(items: Sequence[tuple[StackVisual, TMotion]]) -> list[StackPaintItem[TMotion]]:
    return [
        StackPaintItem(visual, motion, isolate_painter_state=True)
        for visual, motion in sorted(items, key=lambda item: item[0].z_index)
    ]


def stack_frame_paint_plan(
    visuals: Sequence[StackVisual],
    motions: Mapping[str, TMotion],
    *,
    minimum_opacity: float = 0.06,
) -> StackFramePaintPlan[TMotion]:
    threshold = max(0.0, float(minimum_opacity))
    visible_items: list[tuple[StackVisual, TMotion]] = []
    mask_rects: list[QRectF] = []
    for visual in visuals:
        motion = motions.get(visual.key)
        if motion is None:
            continue
        try:
            opacity = float(getattr(motion, "opacity"))
        except (AttributeError, TypeError, ValueError):
            opacity = 0.0
        if opacity <= threshold:
            continue
        try:
            rect = getattr(motion, "rect")
        except AttributeError:
            continue
        visible_items.append((visual, motion))
        mask_rects.append(QRectF(rect))
    return StackFramePaintPlan(
        visible_items=tuple(visible_items),
        paint_items=tuple(stack_paint_items(visible_items)),
        mask_rects=tuple(mask_rects),
    )


def hover_rect_for_stack_item(
    rect: QRectF,
    *,
    now: float,
    expanded: bool,
    hover_started_at: float,
) -> QRectF:
    out = QRectF(rect)
    if expanded or not hover_started_at:
        return out
    progress = max(0.0, min(1.0, float(now) - float(hover_started_at)))
    scale = 1.0 + 0.026 * (1.0 - math.cos(progress * math.pi)) / 2.0
    center = out.center()
    out.setWidth(rect.width() * scale)
    out.setHeight(rect.height() * scale)
    out.moveCenter(center)
    return out


def stack_paint_context_plan(
    visual: StackVisual,
    motion: object,
    *,
    now: float,
    hovered_key: str,
    hover_started_at: float,
    hover_bounce_until: float,
    expanded: bool,
    taskbar_compact_active: bool,
    edge_notch_active: bool,
) -> StackPaintContextPlan:
    try:
        rect = QRectF(getattr(motion, "rect"))
    except (AttributeError, TypeError):
        rect = QRectF(visual.rect)
    try:
        opacity = float(getattr(motion, "opacity"))
    except (AttributeError, TypeError, ValueError):
        opacity = 0.0
    opacity = max(0.0, min(1.0, opacity))
    if visual.key == str(hovered_key or "") and float(now) < float(hover_bounce_until or 0.0):
        rect = hover_rect_for_stack_item(
            rect,
            now=now,
            expanded=expanded,
            hover_started_at=hover_started_at,
        )
    is_main = getattr(visual, "z_index", 0) == 10
    return StackPaintContextPlan(
        rect=rect,
        opacity=opacity,
        paint_taskbar_left_handle=is_main and bool(taskbar_compact_active),
        paint_edge_notch_backing=is_main and bool(edge_notch_active),
        clip_edge_notch=is_main and bool(edge_notch_active),
    )


def interaction_mask_update_plan(
    rects: Sequence[QRectF],
    *,
    platform_win32: bool,
    finder_mode: bool,
    scene_suppressed: bool,
    interaction_mask_active: bool,
    finder_hover_rect: QRectF,
    last_mask_rect: QRectF,
    now: float,
    mask_deferred_until: float,
    last_pointer_move_at: float,
    last_mask_update_at: float,
    unit: float = 1.0,
) -> InteractionMaskUpdatePlan:
    if not platform_win32:
        return InteractionMaskUpdatePlan()
    if not finder_mode and not scene_suppressed:
        return InteractionMaskUpdatePlan(action="clear") if interaction_mask_active else InteractionMaskUpdatePlan()
    if float(now) < float(mask_deferred_until or 0.0):
        return InteractionMaskUpdatePlan()

    pointer_quiet_seconds = 0.06 if finder_mode else 0.12
    if float(now) - float(last_pointer_move_at or 0.0) < pointer_quiet_seconds:
        return InteractionMaskUpdatePlan()

    mask_interval = 0.24 if finder_mode else 0.18
    if float(now) - float(last_mask_update_at or 0.0) < mask_interval:
        return InteractionMaskUpdatePlan()

    u = max(0.0, float(unit))
    mask_rect = QRectF()
    hover_rect = QRectF(finder_hover_rect)
    if finder_mode and not hover_rect.isNull():
        mask_rect = hover_rect
    for rect in rects:
        grown = QRectF(rect).adjusted(-8.0 * u, -6.0 * u, 8.0 * u, 6.0 * u)
        mask_rect = grown if mask_rect.isNull() else mask_rect.united(grown)
    if finder_mode and not mask_rect.isNull():
        mask_rect = mask_rect.adjusted(-4.0 * u, -2.0 * u, 4.0 * u, 4.0 * u)
    if mask_rect.isNull() or mask_rect.width() <= 0 or mask_rect.height() <= 0:
        return InteractionMaskUpdatePlan(action="clear", record_update_at=True) if interaction_mask_active else InteractionMaskUpdatePlan(record_update_at=True)

    previous = QRectF(last_mask_rect)
    if interaction_mask_active and not previous.isNull():
        delta = max(
            abs(previous.left() - mask_rect.left()),
            abs(previous.top() - mask_rect.top()),
            abs(previous.right() - mask_rect.right()),
            abs(previous.bottom() - mask_rect.bottom()),
        )
        threshold = 6.0 if finder_mode else 1.5
        if delta < threshold:
            return InteractionMaskUpdatePlan(record_update_at=True)

    return InteractionMaskUpdatePlan(
        action="apply",
        mask_rect=QRectF(mask_rect),
        record_update_at=True,
    )


def barrage_background_style_plan(
    *,
    current_dark_background: bool,
    now: float,
    drag_candidate: bool,
    last_pointer_press_at: float,
    last_checked_at: float,
    sampled_luma: float | None = None,
) -> BarrageBackgroundStylePlan:
    current_dark = bool(current_dark_background)
    if bool(drag_candidate) or float(now) - float(last_pointer_press_at or 0.0) < 1.2:
        return BarrageBackgroundStylePlan(dark_background=current_dark)
    if float(now) - float(last_checked_at or 0.0) <= 0.55:
        return BarrageBackgroundStylePlan(dark_background=current_dark)
    if sampled_luma is None:
        return BarrageBackgroundStylePlan(dark_background=current_dark, sample_luma=True)

    try:
        luma = float(sampled_luma)
    except (TypeError, ValueError):
        luma = 0.5
    if luma < 0.46:
        return BarrageBackgroundStylePlan(dark_background=True, record_checked_at=True)
    if luma > 0.58:
        return BarrageBackgroundStylePlan(dark_background=False, record_checked_at=True)
    return BarrageBackgroundStylePlan(dark_background=current_dark, record_checked_at=True)


def _barrage_content_metrics(
    source: BarrageLayoutSource,
    *,
    font_height: float,
    show_icon: bool,
    speed_scale: float,
    travel_base_width: float,
    unit: float,
) -> tuple[float, float, float, float, float]:
    u = max(0.0, float(unit))
    text_width = max(0.0, float(getattr(source, "text_width", 0.0) or 0.0))
    icon_space = 24.0 * u if show_icon else 0.0
    content_width = text_width + 26.0 * u + icon_space
    content_height = max(24.0 * u, float(font_height) + 10.0 * u)
    travel = max(1.0, float(travel_base_width)) + content_width + 140.0 * u
    base_speed = max(160.0 * u, 150.0 + content_width * 0.14) * speed_scale
    duration = max(2.8 / speed_scale, travel / max(1.0, base_speed))
    return content_width, content_height, travel, duration, travel / duration


def barrage_scene_layout_plan(
    active_sources: Sequence[BarrageLayoutSource],
    *,
    preview_sources: Sequence[BarrageLayoutSource] = (),
    fullscreen_sources: Sequence[BarrageLayoutSource] = (),
    viewport: QRectF,
    clock: float,
    font_height: float,
    y_offset: float,
    speed: float,
    show_icon: bool,
    travel_base_width: float,
    unit: float = 1.0,
) -> BarrageSceneLayoutPlan:
    active = sorted(active_sources, key=lambda source: (float(source.started_at), int(source.serial)))
    if not active:
        return BarrageSceneLayoutPlan((), {}, (), ())

    u = max(0.0, float(unit))
    rect = QRectF(viewport)
    paint_clock = float(clock)
    font_height_value = max(0.0, float(font_height))
    speed_scale = max(0.4, min(2.2, float(speed) / 100.0))
    lane_count = max(1, min(6, len(active)))
    lane_height = max(26.0 * u, font_height_value + 12.0 * u)
    max_start_y = max(0.0, rect.height() - lane_height)
    start_y = max(0.0, min(max_start_y, float(y_offset)))
    lane_available_at = [0.0] * lane_count

    def metrics(source: BarrageLayoutSource) -> tuple[float, float, float, float, float]:
        return _barrage_content_metrics(
            source,
            font_height=font_height_value,
            show_icon=bool(show_icon),
            speed_scale=speed_scale,
            travel_base_width=travel_base_width,
            unit=u,
        )

    def source_alive(source: BarrageLayoutSource) -> bool:
        content_width, _content_height, travel, duration, _speed = metrics(source)
        progress = max(0.0, (paint_clock - float(source.started_at)) / max(0.01, duration))
        x = rect.right() + 22.0 * u - progress * travel
        return x >= -content_width - 120.0 * u

    paint_items: list[BarragePaintItem] = []
    hit_rects: dict[str, QRectF] = {}
    for source in active:
        content_width, content_height, travel, duration, source_speed = metrics(source)
        lane = min(
            range(lane_count),
            key=lambda index: max(float(source.started_at), lane_available_at[index]),
        )
        effective_start = max(float(source.started_at), lane_available_at[lane])
        if paint_clock < effective_start:
            continue
        progress = max(0.0, min(1.4, (paint_clock - effective_start) / duration))
        x = rect.right() + 22.0 * u - progress * travel
        if x < -content_width - 120.0 * u:
            continue

        lane_available_at[lane] = effective_start + (content_width + 92.0 * u) / max(1.0, source_speed)
        y = start_y + lane * lane_height
        text_rect = QRectF(x, y, content_width, content_height)
        hit_rect = text_rect.adjusted(-10.0 * u, -8.0 * u, 12.0 * u, 8.0 * u)
        item = BarragePaintItem(
            message=source.message,
            text=str(source.text),
            action_key=str(source.action_key),
            text_rect=text_rect,
            hit_rect=hit_rect,
            lane=lane,
            progress=progress,
        )
        paint_items.append(item)
        if item.action_key:
            hit_rects[item.action_key] = QRectF(hit_rect)

    return BarrageSceneLayoutPlan(
        paint_items=tuple(paint_items),
        hit_rects=hit_rects,
        alive_preview_messages=tuple(source.message for source in preview_sources if source_alive(source)),
        alive_fullscreen_messages=tuple(source.message for source in fullscreen_sources if source_alive(source)),
    )


def initial_motion_rect_for_visual(
    visual: StackVisual,
    visuals: Sequence[StackVisual],
    motions: Mapping[str, MotionSnapshot],
    *,
    stack_direction: int,
    expanded: bool,
    now: float,
    taskbar_compact_active: bool,
    unit: float = 1.0,
) -> QRectF:
    start = QRectF(visual.rect)
    direction = -1 if int(stack_direction) < 0 else 1
    u = max(0.0, float(unit))
    if taskbar_compact_active and getattr(visual, "z_index", 0) == 10:
        return start
    if visual.kind == IslandKind.PLUGIN_PANEL:
        strip = next((item for item in visuals if item.key == "plugin"), None)
        if strip is not None:
            height = min(10.0 * u, max(2.0 * u, visual.rect.height()))
            if direction < 0:
                return QRectF(visual.rect.left(), strip.rect.top() - height, visual.rect.width(), height)
            return QRectF(visual.rect.left(), strip.rect.bottom(), visual.rect.width(), height)
    if visual.kind == IslandKind.MESSAGE:
        message_motions = [
            snapshot.rect
            for key, snapshot in motions.items()
            if str(key).startswith("message:") and float(snapshot.opacity) > 0.1
        ]
        main_motion = next(
            (motions.get(item.key) for item in visuals if item.z_index == 10 and item.key in motions),
            None,
        )
        popup_until = float(getattr(visual.message, "popup_until", 0.0) or 0.0) if visual.message else 0.0
        if message_motions and visual.message is not None and now < popup_until:
            source = message_motions[0]
            if direction < 0:
                return QRectF(source.center().x() - 8.0 * u, source.top() - 14.0 * u, 16.0 * u, 10.0 * u)
            return QRectF(source.center().x() - 8.0 * u, source.bottom() + 4.0 * u, 16.0 * u, 10.0 * u)
        if main_motion is not None:
            if direction < 0:
                return QRectF(main_motion.rect.center().x() - 3.0 * u, main_motion.rect.top() - 17.0 * u, 7.0 * u, 7.0 * u)
            return QRectF(main_motion.rect.center().x() - 3.0 * u, main_motion.rect.bottom() + 10.0 * u, 7.0 * u, 7.0 * u)
        if visual.message is not None and expanded and now >= popup_until:
            if direction < 0:
                return QRectF(visual.rect.center().x() - 3.0 * u, visual.rect.bottom() + 7.0 * u, 7.0 * u, 7.0 * u)
            return QRectF(visual.rect.center().x() - 3.0 * u, visual.rect.top() - 14.0 * u, 7.0 * u, 7.0 * u)
    start.moveTop(start.top() - 12.0 * u if direction >= 0 else start.top() + 12.0 * u)
    return start


def plugin_alert_exit_rect_towards_main(
    alert_rect: QRectF,
    visuals: Sequence[StackVisual],
    *,
    unit: float = 1.0,
) -> QRectF:
    main = next((item for item in visuals if item.z_index == 10), None)
    u = max(0.0, float(unit))
    if main is None:
        size = min(10.0 * u, max(4.0 * u, alert_rect.height()))
        return QRectF(alert_rect.center().x() - size / 2.0, alert_rect.center().y() - size / 2.0, size, size)
    size = min(10.0 * u, max(4.0 * u, alert_rect.height() * 0.24))
    y = alert_rect.center().y() - size / 2.0
    if alert_rect.center().x() >= main.rect.center().x():
        x = main.rect.right() + 4.0 * u
    else:
        x = main.rect.left() - 4.0 * u - size
    return QRectF(x, y, size, size)


def stale_motion_step_plan(
    key: str,
    motion: MotionSnapshot,
    visuals: Sequence[StackVisual],
    *,
    expanded: bool,
    finder_mode: bool,
    drop_immediately: bool = False,
    unit: float = 1.0,
) -> StaleMotionStepPlan:
    if drop_immediately:
        return StaleMotionStepPlan(drop_immediately=True)

    if str(key or "").startswith("plugin-alert:") and expanded:
        rect = plugin_alert_exit_rect_towards_main(motion.rect, visuals, unit=unit)
        response = 0.18 if finder_mode else 0.22
        damping = 0.68 if finder_mode else 0.62
    else:
        u = max(0.0, float(unit))
        rect = motion.rect.adjusted(0.0, -8.0 * u, 0.0, -motion.rect.height() + 8.0 * u)
        response = 0.18 if finder_mode else 0.24
        damping = 0.84 if finder_mode else 0.76

    return StaleMotionStepPlan(
        drop_immediately=False,
        target_rect=QRectF(rect),
        target_opacity=0.0,
        response=response,
        damping=damping,
        delete_below_opacity=0.025,
    )
