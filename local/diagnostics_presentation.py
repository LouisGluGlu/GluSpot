from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from local.diagnostics_evidence import build_diagnostics_evidence_report
from local.runtime_experiment import RuntimeExperimentPlan, build_runtime_experiment_plan
from local.settings_store import placement_mode_label


DIAGNOSTIC_FIELD_ROWS: tuple[tuple[str, str], ...] = (
    ("provider_status", "媒体状态"),
    ("winsdk_loaded", "Windows 媒体 SDK"),
    ("music_player_mode", "进度识别服务"),
    ("player_progress_plugin_enabled", "进度插件"),
    ("player_progress_auto_detect", "插件自动识别"),
    ("music_player_adapter", "当前适配器"),
    ("mydockfinder_running", "MyDockFinder 进程"),
    ("finder_bar_state", "MyFinder 顶栏"),
    ("mydockfinder_match", "MyDockFinder 命中"),
    ("mydockfinder_reason", "MyDockFinder 诊断"),
    ("media_session_count", "Windows 媒体会话"),
    ("media_session_candidates", "媒体会话候选"),
    ("source_app", "来源应用"),
    ("track_title", "歌曲标题"),
    ("track_artist", "歌曲作者"),
    ("track_id", "Track ID"),
    ("position_seconds", "当前位置"),
    ("position_source", "位置来源"),
    ("position_confidence", "位置置信度"),
    ("estimated_position_seconds", "估算位置"),
    ("duration_seconds", "歌曲时长"),
    ("lyric_offset_seconds", "歌词偏移"),
    ("lyrics_source", "歌词来源"),
    ("timed_lyrics_count", "带时间歌词数"),
    ("visible_lyric_index", "当前歌词索引"),
    ("visible_current_lyric", "当前歌词"),
    ("lyric_phase_progress", "歌词进度"),
    ("lyric_phase_span", "歌词跨度"),
    ("lyric_phase_source", "同步来源"),
    ("lyric_timeline_lag", "歌词跟进滞后"),
    ("netease_position", "网易云锚点"),
    ("qq_position", "QQ 进度"),
    ("audio_status", "音频采集"),
    ("audio_has_recent_frame", "实时音频帧"),
    ("audio_last_error", "音频错误"),
    ("music_reactive_input", "律动输入"),
    ("visual_runtime_context", "视觉上下文"),
    ("diagnostics_evidence", "证据结论"),
    ("diagnostics_next_action", "诊断下一步"),
    ("runtime_verification", "运行证据"),
    ("runtime_health", "运行诊断"),
    ("runtime_bottleneck", "疑似瓶颈"),
    ("runtime_trace", "诊断趋势"),
    ("visual_frame_rate", "渲染帧率"),
    ("visual_frame_jitter", "渲染抖动"),
    ("visual_frame_long_frames", "渲染慢帧"),
    ("visual_paint_hot_groups", "绘制耗时分组"),
    ("visual_paint_hot_sections", "绘制耗时热点"),
    ("message_cached", "消息缓存"),
    ("message_notification_access", "通知权限"),
    ("message_notification_apps", "通知应用"),
    ("message_detection_enabled", "消息识别总开关"),
    ("wechat_notification_enabled", "微信新消息通知"),
    ("wechat_detail_notification_enabled", "微信详细消息通知"),
    ("qq_notification_enabled", "QQ 消息"),
    ("dingtalk_notification_enabled", "钉钉消息"),
    ("message_wechat", "微信 UIA"),
    ("message_wechat_native", "微信窗口句柄"),
    ("message_wechat_items", "微信会话样本"),
    ("message_wechat_error", "微信识别错误"),
)


DIAGNOSTIC_FIELD_ROWS = (
    DIAGNOSTIC_FIELD_ROWS[:48]
    + (
        ("runtime_experiment_plan", "运行实验"),
        ("runtime_experiment_compare", "实验对比"),
    )
    + DIAGNOSTIC_FIELD_ROWS[48:]
)


def _finder_bar_state_text(data: Mapping[str, Any], diagnostics: Mapping[str, Any]) -> str:
    if not data.get("mydockfinder_running"):
        return "未运行"
    enabled = bool(data.get("finder_bar_enabled", diagnostics.get("finder_enabled", False)))
    if not enabled:
        return "未开启或尚未检测到"
    bars = diagnostics.get("finder_bars") or []
    if not isinstance(bars, (list, tuple)):
        bars = []
    visible = bool(data.get("finder_bar_visible", any(
        isinstance(bar, dict) and bar.get("enabled") and bar.get("visible")
        and not bar.get("cloaked") and not bar.get("minimized") for bar in bars
    )))
    return "已开启 · 已显示" if visible else "已开启 · 自动隐藏"


def format_settings_diagnostics(
    diagnostics: Mapping[str, Any] | None,
    *,
    music_player_mode: object = "netease",
    music_player_mode_label: Callable[[object], str] | None = None,
) -> dict[str, object]:
    data = diagnostics if isinstance(diagnostics, Mapping) else {}
    audio = _mapping(data.get("audio"))
    netease_position = _mapping(data.get("netease_position"))
    qq_position = _mapping(data.get("qq_position"))
    messages = _mapping(data.get("messages"))
    mydockfinder = _mapping(data.get("mydockfinder_diagnostics"))
    position_confidence = data.get("position_confidence", data.get("visible_position_confidence", 0.0))
    mode_labeler = music_player_mode_label or _default_mode_label
    evidence = build_diagnostics_evidence_report(data).as_dict()
    experiment_plan = build_runtime_experiment_plan(evidence)

    return {
        "provider_status": data.get("provider_status", "-"),
        "winsdk_loaded": "可用" if data.get("winsdk_loaded") else "不可用",
        "music_player_mode": mode_labeler(data.get("music_player_mode", music_player_mode)),
        "player_progress_plugin_enabled": _enabled_text(data.get("player_progress_plugin_enabled")),
        "player_progress_auto_detect": _enabled_text(data.get("player_progress_auto_detect")),
        "music_player_adapter": data.get("music_player_adapter") or "-",
        "mydockfinder_running": "开启" if data.get("mydockfinder_running") else "关闭",
        "finder_bar_state": _finder_bar_state_text(data, mydockfinder),
        "mydockfinder_match": mydockfinder.get("matched_name") or "-",
        "mydockfinder_reason": mydockfinder.get("reason") or "-",
        "media_session_count": data.get("media_session_count", 0),
        "media_session_candidates": _join_sequence(data.get("media_session_candidates"), separator=" | ")
        or data.get("media_session_error")
        or "-",
        "source_app": data.get("source_app") or data.get("visible_source_app") or "-",
        "track_title": data.get("track_title") or data.get("visible_title") or "-",
        "track_artist": data.get("track_artist") or data.get("visible_artist") or "-",
        "track_id": data.get("track_id") or data.get("visible_track_id") or "-",
        "position_seconds": format_seconds(data.get("position_seconds", 0.0)),
        "position_source": data.get("position_source") or data.get("visible_position_source") or "-",
        "position_confidence": f"{_float(position_confidence) * 100:.0f}%",
        "estimated_position_seconds": format_seconds(data.get("estimated_position_seconds", 0.0)),
        "duration_seconds": format_seconds(data.get("duration_seconds", 0.0)),
        "lyric_offset_seconds": f"{_float(data.get('lyric_offset_seconds', 0.0)):+.3f}s",
        "lyrics_source": data.get("lyrics_source") or "-",
        "timed_lyrics_count": data.get("timed_lyrics_count", 0),
        "visible_lyric_index": data.get("visible_lyric_index", 0),
        "visible_current_lyric": data.get("visible_current_lyric") or "-",
        "lyric_phase_progress": f"{_float(data.get('lyric_phase_progress', 0.0)) * 100:.1f}%",
        "lyric_phase_span": f"{_float(data.get('lyric_phase_span', 0.0)):.3f}s",
        "lyric_phase_source": data.get("lyric_phase_source") or "-",
        "lyric_timeline_lag": format_lyric_timeline_lag(_mapping(data.get("lyric_timeline_diagnostics"))),
        "netease_position": format_position_adapter_diagnostics(netease_position),
        "qq_position": format_position_adapter_diagnostics(qq_position),
        "audio_status": audio.get("status", "-"),
        "audio_has_recent_frame": "是" if audio.get("has_recent_frame") else "否",
        "audio_last_error": audio.get("last_error") or "-",
        "music_reactive_input": format_music_reactive_input(_mapping(data.get("music_reactive_input_diagnostics"))),
        "visual_runtime_context": format_visual_runtime_context(_mapping(data.get("visual_runtime_context"))),
        "diagnostics_evidence": format_diagnostics_evidence(evidence),
        "diagnostics_next_action": evidence.get("next_action") or "-",
        "runtime_experiment_plan": format_runtime_experiment_plan(experiment_plan),
        "runtime_experiment_compare": format_runtime_experiment_compare(experiment_plan),
        "runtime_verification": format_runtime_verification(_mapping(data.get("runtime_verification_report"))),
        "runtime_health": format_runtime_health(_mapping(data.get("runtime_diagnostics_snapshot"))),
        "runtime_bottleneck": format_runtime_bottleneck(_mapping(data.get("runtime_diagnostics_snapshot"))),
        "runtime_trace": format_runtime_trace(_mapping(data.get("runtime_diagnostics_trace"))),
        "visual_frame_rate": format_visual_frame_rate(_mapping(data.get("visual_frame_diagnostics"))),
        "visual_frame_jitter": format_visual_frame_jitter(_mapping(data.get("visual_frame_diagnostics"))),
        "visual_frame_long_frames": format_visual_frame_long_frames(_mapping(data.get("visual_frame_diagnostics"))),
        "visual_paint_hot_groups": format_visual_paint_hot_groups(
            _mapping(data.get("visual_paint_section_diagnostics"))
        ),
        "visual_paint_hot_sections": format_visual_paint_hot_sections(
            _mapping(data.get("visual_paint_section_diagnostics"))
        ),
        "message_cached": messages.get("cached_messages", 0),
        "message_notification_access": messages.get("notification_access") or "-",
        "message_notification_apps": _join_sequence(messages.get("last_notification_apps"), separator=" / ") or "-",
        "message_detection_enabled": _enabled_text(messages.get("message_detection_enabled")),
        "wechat_notification_enabled": _enabled_text(messages.get("wechat_notification_enabled")),
        "wechat_detail_notification_enabled": _enabled_text(messages.get("wechat_detail_notification_enabled")),
        "qq_notification_enabled": _enabled_text(messages.get("qq_notification_enabled")),
        "dingtalk_notification_enabled": _enabled_text(messages.get("dingtalk_notification_enabled")),
        "message_wechat": (
            f"{messages.get('wechat_backend') or '-'} / "
            f"{messages.get('wechat_window_count', 0)} UIA 窗口 / "
            f"{messages.get('wechat_session_count', 0)} 会话 / "
            f"{messages.get('wechat_total_unread_count', messages.get('wechat_unread_count', 0))} 总未读"
        ),
        "message_wechat_native": (
            f"{messages.get('wechat_native_window_count', 0)} 窗口 / "
            + (_join_sequence(messages.get("wechat_native_windows"), separator=" | ") or "-")
        ),
        "message_wechat_items": _join_sequence(messages.get("wechat_debug_items"), separator=" | ") or "-",
        "message_wechat_error": messages.get("wechat_error") or "-",
    }


def format_position_adapter_diagnostics(diagnostics: Mapping[str, Any] | None) -> str:
    data = diagnostics if isinstance(diagnostics, Mapping) else {}
    source = data.get("last_source") or "-"
    confidence = _float(data.get("last_confidence", 0.0)) * 100.0
    lyric_text = str(data.get("last_lyric_text") or "").strip()
    if lyric_text:
        lyric_text = lyric_text[:36]
    error = str(data.get("last_error") or "").strip()
    if source != "-":
        return f"{source} / {confidence:.0f}% / {lyric_text or '-'}"
    return error or "-"


def format_seconds(value: object) -> str:
    seconds = max(0.0, _float(value))
    minutes = int(seconds // 60)
    remain = int(round(seconds - minutes * 60))
    return f"{minutes}:{remain:02d}"


def format_lyric_timeline_lag(diagnostics: Mapping[str, Any] | None) -> str:
    data = diagnostics if isinstance(diagnostics, Mapping) else {}
    if not data:
        return "-"
    lag = int(round(_float(data.get("sync_lag_lines", 0))))
    visible = int(round(_float(data.get("visible_lyric_index", 0))))
    position = int(round(_float(data.get("position_active_index", 0))))
    source = data.get("phase_source") or "-"
    return f"{lag:+d} 行 / 显示 {visible} / 位置 {position} / {source}"


def format_music_reactive_input(diagnostics: Mapping[str, Any] | None) -> str:
    data = diagnostics if isinstance(diagnostics, Mapping) else {}
    if not data:
        return "-"
    bands = _number_sequence(data.get("sampled_bands"), limit=3)
    band_text = "/".join(f"{item:.2f}" for item in bands) if bands else "0.00/0.00/0.00"
    source = data.get("band_source") or "-"
    live = "live" if data.get("live_audio_available") else "fallback"
    age = _optional_ms(data.get("sample_age_ms"))
    intensity = _float(data.get("intensity", 0.0))
    return f"{live} / {source} / 强度 {intensity:.2f} / bands {band_text} / age {age}"


def format_visual_runtime_context(context: Mapping[str, Any] | None) -> str:
    data = context if isinstance(context, Mapping) else {}
    if not data:
        return "-"
    style = data.get("effective_ambience_style") or data.get("ambience_style") or "-"
    requested = data.get("ambience_style") or "-"
    lyric = data.get("lyric_flow_style") or "-"
    location = data.get("location_mode") or "-"
    expanded = "展开" if data.get("expanded") else "紧凑"
    taskbar = "Tasker Bar紧凑" if data.get("taskbar_compact_active") else placement_mode_label(location) if location != "-" else "-"
    reactive = "律动开" if data.get("reactive_background_enabled") else "律动关"
    reactive_intensity = int(_float(data.get("reactive_background_intensity", 0)))
    fps = int(_float(data.get("animation_fps", 0)))
    lyric_fps = int(_float(data.get("lyric_animation_fps", 0)))
    speed = _float(data.get("animation_speed", 0.0))
    parts = [
        f"{style}",
        f"请求 {requested}",
        f"歌词 {lyric}",
        f"{expanded}/{taskbar}",
        f"{reactive} {reactive_intensity}",
        f"FPS {fps}/歌词 {lyric_fps}",
    ]
    if speed > 0:
        parts.append(f"速度 {speed:.2f}x")
    return " / ".join(parts)


def format_diagnostics_evidence(report: Mapping[str, Any] | None) -> str:
    data = report if isinstance(report, Mapping) else {}
    if not data:
        return "-"
    status = str(data.get("status") or "missing")
    ready = bool(data.get("ready"))
    health = str(data.get("health_status") or "missing")
    dominant = str(data.get("dominant_area") or "").strip()
    bottleneck = str(data.get("bottleneck_group") or "").strip()
    style = str(data.get("ambience_style") or "").strip()
    lyric = str(data.get("lyric_flow_style") or "").strip()
    samples = int(_float(data.get("trace_sample_count", 0)))
    window = _float(data.get("trace_window_seconds", 0.0))
    parts = [_verification_status_label(status, ready), f"健康 {health}"]
    if samples > 0 or window > 0.0:
        parts.append(f"{samples} samples/{window:.1f}s")
    if style or lyric:
        parts.append(f"{style or '-'}/{lyric or '-'}")
    if dominant:
        parts.append(f"主因 {_runtime_area_label(dominant)}")
    if bottleneck:
        parts.append(f"瓶颈 {_runtime_area_label(bottleneck)}")
    return " / ".join(parts)


def format_runtime_experiment_plan(plan: RuntimeExperimentPlan | Mapping[str, Any] | None) -> str:
    data = plan.as_dict() if isinstance(plan, RuntimeExperimentPlan) else _mapping(plan)
    if not data:
        return "-"
    key = str(data.get("key") or "-")
    summary = str(data.get("summary") or "").strip()
    changes = _mapping(data.get("setting_changes"))
    parts = [key]
    if summary:
        parts.append(summary)
    if changes:
        parts.append("settings " + ", ".join(f"{name}={value}" for name, value in changes.items()))
    criteria = _string_sequence(data.get("success_criteria"), limit=2)
    if criteria:
        parts.append("success " + " | ".join(criteria))
    return " / ".join(parts)


def format_runtime_experiment_compare(plan: RuntimeExperimentPlan | Mapping[str, Any] | None) -> str:
    data = plan.as_dict() if isinstance(plan, RuntimeExperimentPlan) else _mapping(plan)
    if not data:
        return "-"
    seconds = int(_float(data.get("capture_seconds", 0)))
    command = str(data.get("compare_command") or "").strip()
    before = str(data.get("before_capture") or "").strip()
    after = str(data.get("after_capture") or "").strip()
    parts: list[str] = []
    if seconds > 0:
        parts.append(f"{seconds}s before/after")
    if command:
        parts.append(command)
    if before:
        parts.append(before)
    if after:
        parts.append(after)
    return " / ".join(parts) or "-"


def format_runtime_verification(report: Mapping[str, Any] | None) -> str:
    data = report if isinstance(report, Mapping) else {}
    if not data:
        return "-"
    status = str(data.get("status") or "missing")
    ready = bool(data.get("ready"))
    health = str(data.get("health_status") or "missing")
    dominant = str(data.get("dominant_area") or "").strip()
    bottleneck = str(data.get("bottleneck_group") or "").strip()
    summary = str(data.get("summary") or "").strip()
    next_action = str(data.get("next_action") or "").strip()
    wait_seconds = int(_float(data.get("suggested_wait_seconds", 0)))
    parts = [_verification_status_label(status, ready), f"健康 {health}"]
    if dominant:
        parts.append(f"主因 {_runtime_area_label(dominant)}")
    if bottleneck:
        parts.append(f"瓶颈 {_runtime_area_label(bottleneck)}")
    if not ready and wait_seconds > 0:
        parts.append(f"建议等待 {wait_seconds}s")
    if summary:
        parts.append(summary)
    if next_action and next_action != summary:
        parts.append(next_action)
    return " / ".join(parts)


def format_runtime_health(snapshot: Mapping[str, Any] | None) -> str:
    data = snapshot if isinstance(snapshot, Mapping) else {}
    if not data:
        return "-"
    status = str(data.get("status") or "missing")
    headline = str(data.get("headline") or "").strip()
    area = str(data.get("dominant_area") or "").strip()
    parts = [_runtime_status_label(status)]
    if area:
        parts.append(_runtime_area_label(area))
    if headline:
        parts.append(headline)
    return " / ".join(part for part in parts if part) or "-"


def format_runtime_bottleneck(snapshot: Mapping[str, Any] | None) -> str:
    data = snapshot if isinstance(snapshot, Mapping) else {}
    if not data:
        return "-"
    group = str(data.get("bottleneck_group") or "").strip()
    summary = str(data.get("bottleneck_summary") or "").strip()
    if not group and not summary:
        return "-"
    label = _runtime_area_label(group) if group else "-"
    return f"{label} / {summary}" if summary else label


def format_runtime_trace(trace: Mapping[str, Any] | None) -> str:
    data = trace if isinstance(trace, Mapping) else {}
    if not data:
        return "-"
    sample_count = int(_float(data.get("sample_count", 0)))
    if sample_count <= 0:
        return "-"
    window = _float(data.get("window_seconds", 0.0))
    warning_count = int(_float(data.get("warning_count", 0)))
    attention_count = int(_float(data.get("attention_count", 0)))
    area = str(data.get("dominant_area") or "").strip()
    bottleneck = str(data.get("bottleneck_group") or "").strip()
    parts = [f"{sample_count} samples / {window:.1f}s"]
    if warning_count:
        parts.append(f"异常 {warning_count}")
    if attention_count:
        parts.append(f"需观察 {attention_count}")
    if area:
        parts.append(f"主因 {_runtime_area_label(area)}")
    if bottleneck:
        parts.append(f"瓶颈 {_runtime_area_label(bottleneck)}")
    return " / ".join(parts)


def format_visual_frame_rate(diagnostics: Mapping[str, Any] | None) -> str:
    data = diagnostics if isinstance(diagnostics, Mapping) else {}
    if not data:
        return "-"
    fps = _float(data.get("average_fps", 0.0))
    interval = _float(data.get("average_interval_ms", 0.0))
    target = _float(data.get("target_interval_ms", 0.0))
    if fps <= 0.0 and interval <= 0.0:
        return f"采样 {int(_float(data.get('sample_count', 0)))} / 目标 {target:.1f}ms"
    return f"{fps:.1f} FPS / avg {interval:.1f}ms / target {target:.1f}ms"


def format_visual_frame_jitter(diagnostics: Mapping[str, Any] | None) -> str:
    data = diagnostics if isinstance(diagnostics, Mapping) else {}
    if not data:
        return "-"
    return f"jitter {_float(data.get('jitter_ms', 0.0)):.1f}ms / max {_float(data.get('max_interval_ms', 0.0)):.1f}ms"


def format_visual_frame_long_frames(diagnostics: Mapping[str, Any] | None) -> str:
    data = diagnostics if isinstance(diagnostics, Mapping) else {}
    if not data:
        return "-"
    reason = str(data.get("last_reason") or "").strip()
    text = f"{int(_float(data.get('long_frame_count', 0)))} / {int(_float(data.get('frame_count', 0)))}"
    return f"{text} / {reason}" if reason else text


def format_visual_paint_hot_sections(diagnostics: Mapping[str, Any] | None, *, limit: int = 3) -> str:
    data = diagnostics if isinstance(diagnostics, Mapping) else {}
    sections = _mapping(data.get("sections"))
    if not sections:
        return "-"
    ranked: list[tuple[float, str, Mapping[str, Any]]] = []
    for name, summary in sections.items():
        item = _mapping(summary)
        ranked.append((_float(item.get("max_ms", item.get("average_ms", 0.0))), str(name), item))
    ranked.sort(key=lambda item: item[0], reverse=True)
    parts = []
    for _cost, name, item in ranked[: max(1, int(limit))]:
        parts.append(f"{name} avg {_float(item.get('average_ms', 0.0)):.1f}ms max {_float(item.get('max_ms', 0.0)):.1f}ms")
    return " | ".join(parts) or "-"


def format_visual_paint_hot_groups(diagnostics: Mapping[str, Any] | None, *, limit: int = 4) -> str:
    data = diagnostics if isinstance(diagnostics, Mapping) else {}
    groups = _mapping(data.get("groups"))
    if not groups:
        return "-"
    ranked: list[tuple[float, str, Mapping[str, Any]]] = []
    for name, summary in groups.items():
        if str(name) == "total":
            continue
        item = _mapping(summary)
        ranked.append((_float(item.get("last_ms", item.get("average_ms", 0.0))), str(name), item))
    ranked.sort(key=lambda item: item[0], reverse=True)
    parts = []
    for _cost, name, item in ranked[: max(1, int(limit))]:
        parts.append(
            f"{_visual_paint_group_label(name)} avg {_float(item.get('average_ms', 0.0)):.1f}ms "
            f"last {_float(item.get('last_ms', 0.0)):.1f}ms"
        )
    return " | ".join(parts) or "-"


def _visual_paint_group_label(name: str) -> str:
    return {
        "background_surface": "背景",
        "reactive_effects": "律动/过渡",
        "content": "内容/歌词",
        "shell": "外壳",
        "other": "其他",
    }.get(str(name), str(name))


def _runtime_status_label(status: str) -> str:
    return {
        "ok": "正常",
        "attention": "需观察",
        "warning": "异常",
        "missing": "缺少样本",
    }.get(str(status), str(status))


def _verification_status_label(status: str, ready: bool) -> str:
    if ready:
        return "证据可用"
    return {
        "ready": "证据可用",
        "partial": "证据不完整",
        "missing": "缺少证据",
    }.get(str(status), str(status))


def _runtime_area_label(area: str) -> str:
    return {
        "frame_cadence": "帧调度",
        "paint": "绘制",
        "background_surface": _visual_paint_group_label("background_surface"),
        "reactive_effects": _visual_paint_group_label("reactive_effects"),
        "content": _visual_paint_group_label("content"),
        "shell": _visual_paint_group_label("shell"),
        "other": _visual_paint_group_label("other"),
        "lyric_timeline": "歌词跟进",
        "reactive_input": "律动输入",
        "runtime_contention": "律动/歌词争用",
    }.get(str(area), str(area))


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _enabled_text(value: object) -> str:
    return "启用" if value else "关闭"


def _default_mode_label(mode: object) -> str:
    return str(mode or "-")


def _join_sequence(value: object, *, separator: str) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, Iterable):
        return ""
    return separator.join(str(item) for item in value if str(item).strip())


def _number_sequence(value: object, *, limit: int) -> Sequence[float]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return ()
    numbers = []
    for item in value:
        numbers.append(_float(item))
        if len(numbers) >= limit:
            break
    return tuple(numbers)


def _string_sequence(value: object, *, limit: int) -> Sequence[str]:
    if isinstance(value, str) or not isinstance(value, Iterable):
        return ()
    result = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text)
        if len(result) >= limit:
            break
    return tuple(result)


def _optional_ms(value: object) -> str:
    if value is None:
        return "-"
    return f"{_float(value):.0f}ms"


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
