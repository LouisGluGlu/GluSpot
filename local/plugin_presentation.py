from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any


PLUGIN_PANEL_KEYS = (
    "label",
    "detail",
    "summary",
    "color",
    "icon",
    "plugin_id",
    "status",
    "alert_title",
    "alert_body",
    "alert_priority",
    "progress",
)

PLUGIN_ANIMATED_STATUSES = frozenset(
    {
        "running",
        "loading",
        "soon",
        "due",
        "done",
        "error",
        "alarm",
        "severe",
        "rain",
    }
)

PLUGIN_ALERT_STATUSES = PLUGIN_ANIMATED_STATUSES

PLUGIN_ALERT_STATUS_PRIORITY = {
    "due": 0,
    "severe": 1,
    "alarm": 2,
    "done": 3,
    "soon": 4,
    "rain": 5,
    "error": 6,
    "running": 7,
    "loading": 8,
}

DEFAULT_PLUGIN_IDS = ("time", "weather", "agenda")

ALARM_FREQUENCY_UNITS = (
    ("daily", "每天"),
    ("weekly", "每周"),
    ("monthly", "每月"),
    ("yearly", "每年"),
)

ALARM_FREQUENCY_OPTIONS = ("每周", "每天", "工作日", "仅一次")
ALARM_WEEKDAY_OPTIONS = ((1, 3, 5), (2, 4), (6, 7), (1, 2, 3, 4, 5, 6, 7))
DEFAULT_ALARM_LABEL = "闹钟"


def plugin_panel_focus_model(
    plugins: Sequence[Sequence[Any]],
    active_index: int,
    *,
    action: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    if not plugins:
        return None
    index = max(0, min(int(active_index), len(plugins) - 1))
    values = list(plugins[index])
    model: dict[str, Any] = {"index": index}
    for offset, key in enumerate(PLUGIN_PANEL_KEYS):
        model[key] = values[offset] if offset < len(values) else _plugin_panel_default_value(key)
    if action is not None:
        model["action"] = dict(action)
    return model


def plugin_sample_tuple(plugin: Sequence[Any]) -> tuple[Any, Any, Any, Any, Any, Any, Any, Any, Any, Any, Any]:
    values = list(plugin)
    if len(values) >= 11:
        label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority, progress = values[:11]
        return label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority, progress
    if len(values) >= 10:
        label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority = values[:10]
        return label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority, 0.0
    if len(values) >= 7:
        label, detail, summary, color, icon, plugin_id, status = values[:7]
        return label, detail, summary, color, icon, plugin_id, status, "", "", 50, 0.0
    label, detail, summary, color, icon = values[:5]
    return label, detail, summary, color, icon, "", "normal", "", "", 50, 0.0


def enabled_plugin_ids(
    raw_ids: Sequence[Any] | set[Any] | None,
    enabled_count: int,
    known_plugin_ids: Sequence[str] = DEFAULT_PLUGIN_IDS,
) -> list[str]:
    known = [str(plugin_id or "").strip().casefold() for plugin_id in known_plugin_ids]
    if raw_ids is None:
        count = max(0, min(len(known), int(enabled_count or 0)))
        return known[:count]
    values = {str(item or "").strip().casefold() for item in raw_ids}
    return [plugin_id for plugin_id in known if plugin_id in values]


def enabled_plugin_samples(plugin_samples: Sequence[Sequence[Any]], enabled_ids: Sequence[str]) -> list[Sequence[Any]]:
    enabled = {str(plugin_id or "").strip().casefold() for plugin_id in enabled_ids}
    if not enabled:
        return []
    samples: list[Sequence[Any]] = []
    for plugin in list(plugin_samples):
        plugin_id = str(plugin_sample_tuple(plugin)[5] or "").strip().casefold()
        if plugin_id in enabled:
            samples.append(plugin)
    return samples


def plugin_index_for_id(samples: Sequence[Sequence[Any]], plugin_id: str) -> int:
    target = str(plugin_id or "").strip().casefold()
    if not target:
        return -1
    if target.startswith("time:"):
        target = "time"
    for index, sample in enumerate(samples):
        current_id = str(plugin_sample_tuple(sample)[5] or "").strip().casefold()
        if current_id == target:
            return index
    return -1


def time_alert_side_suppressed(
    *,
    include_expanded: bool,
    expanded: bool,
    plugin_panel_open: bool,
    main_kind_is_plugin: bool,
) -> bool:
    if bool(include_expanded):
        return False
    return bool(expanded and plugin_panel_open and main_kind_is_plugin)


def time_alarm_side_display_mode(raw_value: object = "note", setting_value: object = None) -> str:
    value = str(raw_value or "note").strip().casefold()
    setting = str(setting_value or value or "note").strip().casefold()
    return "time" if setting in {"time", "deadline", "due", "clock"} else "note"


def plugin_compact_value(label: str, detail: str, summary: str, status: str = "normal") -> str:
    text = str(detail or "").strip()
    if str(label or "").upper() == "TIME" and str(status or "").lower() == "running":
        text = strip_timer_prefix(str(summary or "").strip())
    return text or str(summary or "").strip()


def strip_timer_prefix(text: str) -> str:
    text = str(text or "").strip()
    match = re.search(r"\d{1,2}:\d{2}(?::\d{2})?", text)
    if match:
        return match.group(0)
    return re.sub(r"^\s*(?:倒计时|Countdown|Timer|timer|计时)\s*", "", text, flags=re.I).strip()


def time_reminder_display_text(text: str, max_chars: int = 18) -> str:
    value = str(text or "").strip()
    max_chars = max(4, int(max_chars))
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def time_reminder_kind(status: str = "", text: str = "") -> str:
    source = f"{status} {text}".lower()
    if "alarm" in source or "闹钟" in source:
        return "alarm"
    return "countdown"


def time_alert_kind_from_plugin_id(plugin_id: str) -> str:
    value = str(plugin_id or "").strip().casefold()
    if value.endswith(":alarm"):
        return "alarm"
    return "countdown"


def time_alarm_alert_text(note: str) -> str:
    value = str(note or "").strip()
    if not value:
        return "闹钟"
    value = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", "", value).strip()
    value = re.sub(r"^\s*(?:闹钟提醒?|提醒)[:：\s]*", "", value).strip()
    lowered = value.strip().lower()
    if lowered in {"alarm", "timer", "countdown"} or any(
        token in lowered
        for token in (
            "倒计时",
            "计时器",
            "时间到",
            "计时结束",
            "countdown",
            "timer",
            "time up",
            "done",
        )
    ):
        return "闹钟"
    return value or "闹钟"


def time_alarm_alert_text_from_parts(*parts: str) -> str:
    for part in parts:
        value = time_alarm_alert_text(part)
        if value != "闹钟":
            return value
    return "闹钟"


def time_text_looks_like_countdown(text: str) -> bool:
    value = str(text or "").strip().lower()
    if not value:
        return False
    if re.search(r"\d{1,2}:\d{2}(?::\d{2})?", value):
        return True
    return any(token in value for token in ("倒计时", "计时", "计时器", "时间到", "计时结束", "countdown", "timer"))


def time_alarm_due_acknowledged_matches(time_text: str, label: str, acknowledged_key: str) -> bool:
    acknowledged = str(acknowledged_key or "").strip()
    if not acknowledged:
        return False
    label = str(label or DEFAULT_ALARM_LABEL).strip() or DEFAULT_ALARM_LABEL
    time_text = str(time_text or "").strip()
    key = f"{time_text}|{label}" if time_text else ""
    return bool((key and acknowledged == key) or acknowledged.endswith(f"|{label}"))


def capture_time_alarm_due_pending(
    model: dict[str, object],
    *,
    existing_pending: object = None,
    acknowledged_key: str = "",
    captured_at: float = 0.0,
) -> dict[str, object]:
    existing = dict(existing_pending) if isinstance(existing_pending, dict) and existing_pending else {}
    status = str(model.get("status") or "").strip().lower()
    if status != "alarm":
        return {
            "pending": {},
            "set_pending": False,
            "clear_acknowledged": not bool(existing),
        }

    summary = str(model.get("summary") or "")
    alert_body = str(model.get("alert_body") or "")
    countdown_source = f"{alert_body} {summary}".lower()
    has_countdown_context = any(
        token in countdown_source
        for token in (
            "countdown",
            "timer",
            "time up",
            "done",
            "\u5012\u8ba1\u65f6",
            "\u8ba1\u65f6",
            "\u65f6\u95f4\u5230",
            "\u8ba1\u65f6\u7ed3\u675f",
        )
    )
    label_parts = [alert_body, str(model.get("alert_title") or "")]
    if not has_countdown_context:
        label_parts.append(summary)
    label = time_alarm_alert_text_from_parts(*label_parts)
    if label == DEFAULT_ALARM_LABEL and has_countdown_context and existing:
        return {"pending": existing, "set_pending": False, "clear_acknowledged": False}
    if label == DEFAULT_ALARM_LABEL and has_countdown_context and time_text_looks_like_countdown(alert_body):
        return {"pending": {}, "set_pending": False, "clear_acknowledged": False}

    time_source = summary if not has_countdown_context else ""
    time_text = strip_timer_prefix(str(time_source or model.get("detail") or ""))
    if not re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", time_text):
        match = re.search(r"\d{1,2}:\d{2}(?::\d{2})?", str(time_source or model.get("detail") or ""))
        time_text = match.group(0) if match else ""
    if time_alarm_due_acknowledged_matches(time_text, label, acknowledged_key):
        return {"pending": {}, "set_pending": False, "clear_acknowledged": False}

    pending = {
        "time": time_text,
        "label": label or DEFAULT_ALARM_LABEL,
        "key": f"{time_text}|{label or DEFAULT_ALARM_LABEL}",
        "captured_at": float(captured_at or 0.0),
    }
    return {"pending": pending, "set_pending": True, "clear_acknowledged": False}


def _time_text_has_countdown_context(text: str) -> bool:
    value = str(text or "").strip()
    lowered = value.lower()
    if any(
        token in lowered
        for token in (
            "countdown",
            "timer",
            "time up",
            "timer done",
            "done",
            "\u5012\u8ba1\u65f6",
            "\u8ba1\u65f6",
            "\u8ba1\u65f6\u5668",
            "\u65f6\u95f4\u5230",
            "\u8ba1\u65f6\u7ed3\u675f",
        )
    ):
        return True
    if not time_text_looks_like_countdown(value):
        return False
    timer_value = strip_timer_prefix(value)
    return timer_value != value or not bool(re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", value))


def _time_text_is_done_countdown(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    return any(
        token in lowered
        for token in (
            "time up",
            "timer done",
            "done",
            "\u65f6\u95f4\u5230",
            "\u8ba1\u65f6\u7ed3\u675f",
        )
    )


def time_alarm_side_segment_text(segment: dict[str, object], mode: str = "note") -> str:
    if str(mode or "").strip().lower() == "time":
        due_at = segment.get("due_at")
        if isinstance(due_at, datetime):
            return due_at.strftime("%H:%M")
        match = re.search(r"\d{1,2}:\d{2}", str(segment.get("text") or ""))
        if match:
            return match.group(0)
    return str(segment.get("text") or "").strip() or DEFAULT_ALARM_LABEL


def time_side_alert_candidates(
    *,
    index: int,
    label: str,
    detail: str,
    summary: str,
    color: object,
    icon: str,
    plugin_id: str,
    status: str,
    alert_title: str,
    alert_body: str,
    alert_priority: int,
    pending_alarm: object = None,
    acknowledged_key: str = "",
    compact_segments: object = None,
    alarm_side_display_mode: str = "note",
) -> list[tuple[int, int, int, str, str, str, object, str, str, str, str, str, int]]:
    status_key = str(status or "").strip().lower()
    base_id = str(plugin_id or "time").strip() or "time"
    priority_value = int(alert_priority)
    pending = dict(pending_alarm) if isinstance(pending_alarm, dict) and pending_alarm else {}
    candidates: list[tuple[int, int, int, str, str, str, object, str, str, str, str, str, int]] = []

    def add(kind: str, text: str, candidate_status: str, title: str, priority: int) -> None:
        body = str(text or "").strip()
        if not body:
            return
        virtual_id = f"{base_id}:alarm" if kind == "alarm" else base_id
        candidate_summary = DEFAULT_ALARM_LABEL if kind == "alarm" else body
        candidates.append(
            (
                int(priority),
                plugin_alert_status_priority(candidate_status),
                int(index),
                label,
                body,
                candidate_summary,
                color,
                icon,
                virtual_id,
                candidate_status,
                title,
                body,
                int(priority),
            )
        )

    def add_countdown_from_text(text: str, priority: int, allow_bare_time: bool = True) -> bool:
        source = str(text or "").strip()
        if not time_text_looks_like_countdown(source):
            return False
        has_countdown_context = _time_text_has_countdown_context(source)
        if not allow_bare_time and not has_countdown_context:
            return False
        value = strip_timer_prefix(source)
        if _time_text_is_done_countdown(source) or value in {"", "\u7ed3\u675f"}:
            add("countdown", "\u65f6\u95f4\u5230", "done", "\u5012\u8ba1\u65f6", priority)
        else:
            add("countdown", value, "running", "\u5012\u8ba1\u65f6", max(priority, 30))
        return True

    if status_key == "alarm":
        countdown_context = _time_text_has_countdown_context(f"{alert_body} {summary}")
        alarm_label = time_alarm_alert_text_from_parts(alert_body, alert_title)
        if alarm_label == DEFAULT_ALARM_LABEL and countdown_context:
            if pending:
                pending_label = str(pending.get("label") or DEFAULT_ALARM_LABEL).strip() or DEFAULT_ALARM_LABEL
                add("alarm", pending_label, "alarm", DEFAULT_ALARM_LABEL, min(priority_value, 3))
            elif not time_text_looks_like_countdown(alert_body):
                time_text = strip_timer_prefix(str(detail or ""))
                if not time_alarm_due_acknowledged_matches(time_text, alarm_label, acknowledged_key):
                    add("alarm", alarm_label, "alarm", DEFAULT_ALARM_LABEL, min(priority_value, 3))
        else:
            time_source = "" if countdown_context else summary
            time_text = strip_timer_prefix(str(time_source or detail or ""))
            if not re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", time_text):
                match = re.search(r"\d{1,2}:\d{2}(?::\d{2})?", str(time_source or detail or ""))
                time_text = match.group(0) if match else ""
            if not time_alarm_due_acknowledged_matches(time_text, alarm_label, acknowledged_key):
                add("alarm", alarm_label, "alarm", DEFAULT_ALARM_LABEL, min(priority_value, 3))
        add_countdown_from_text(summary, max(min(priority_value + 2, 5), 4), allow_bare_time=False)
        return candidates

    if pending:
        pending_label = str(pending.get("label") or DEFAULT_ALARM_LABEL).strip() or DEFAULT_ALARM_LABEL
        add("alarm", pending_label, "alarm", DEFAULT_ALARM_LABEL, min(priority_value, 3))

    if status_key in {"done", "due"}:
        note = time_alarm_alert_text(alert_body)
        if note != DEFAULT_ALARM_LABEL and not time_text_looks_like_countdown(alert_body):
            candidates.clear()
            add("alarm", note, "alarm", DEFAULT_ALARM_LABEL, min(priority_value, 3))
        else:
            countdown_source = next(
                (
                    str(candidate or "")
                    for candidate in (alert_body, summary, detail)
                    if time_text_looks_like_countdown(str(candidate or ""))
                ),
                "\u65f6\u95f4\u5230",
            )
            add_countdown_from_text(countdown_source, priority_value)
        return candidates

    if isinstance(compact_segments, Sequence):
        for segment in list(compact_segments)[1:]:
            if not isinstance(segment, dict):
                continue
            kind = str(segment.get("kind") or "").strip().lower()
            text = str(segment.get("text") or "").strip()
            if kind == "alarm":
                add(
                    "alarm",
                    time_alarm_side_segment_text(segment, alarm_side_display_mode),
                    "running",
                    DEFAULT_ALARM_LABEL,
                    29,
                )
            elif kind == "countdown":
                segment_status = str(segment.get("status") or status_key or "running").strip().lower()
                candidate_status = "done" if segment_status in {"done", "due"} or text == "\u65f6\u95f4\u5230" else "running"
                add(
                    "countdown",
                    text or strip_timer_prefix(alert_body or summary or detail),
                    candidate_status,
                    "\u5012\u8ba1\u65f6",
                    priority_value,
                )
    return candidates


def format_timer_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def time_display_segment_text(detail: str, now: datetime, *, show_seconds: bool = True) -> str:
    match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", str(detail or ""))
    if match:
        hour = int(match.group(1)) % 24
        minute = int(match.group(2)) % 60
        second = int(match.group(3) or now.second) % 60
    else:
        hour, minute, second = now.hour, now.minute, now.second
    if show_seconds:
        return f"{hour:02d}:{minute:02d}:{second:02d}"
    return f"{hour:02d}:{minute:02d}"


def time_countdown_active_model(
    model: dict[str, object],
    *,
    paused_remaining: int = 0,
    paused_progress: float = 0.0,
) -> dict[str, object]:
    paused_remaining = max(0, int(paused_remaining or 0))
    if paused_remaining > 0:
        return {
            "active": True,
            "text": format_timer_seconds(paused_remaining),
            "progress": _clamped_float(paused_progress),
            "status": "paused",
        }
    status = str(model.get("status", "") or "").strip().lower()
    text_source = " ".join(
        str(model.get(key, "") or "")
        for key in ("alert_body", "summary", "detail")
        if str(model.get(key, "") or "").strip()
    )
    text = strip_timer_prefix(text_source)
    active = status in {"running", "done"} and bool(text)
    if status == "done":
        text = "完成"
    return {
        "active": active,
        "text": text if active else "",
        "progress": _clamped_float(model.get("progress", 0.0)),
        "status": status,
    }


def time_countdown_entries(
    raw_entries: object,
    active_model: dict[str, object] | None,
    *,
    now: datetime,
    paused_remaining: int = 0,
) -> list[dict[str, object]]:
    if isinstance(raw_entries, list) and raw_entries:
        entries = []
        for raw in raw_entries[-2:]:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or "").strip()
            started = None
            target = None
            for key, holder in (("started_at", "started"), ("end_at", "target")):
                raw_time = str(raw.get(key) or "").strip()
                if not raw_time:
                    continue
                try:
                    value = datetime.fromisoformat(raw_time)
                except ValueError:
                    continue
                if value.tzinfo is None:
                    value = value.replace(tzinfo=now.tzinfo)
                if holder == "started":
                    started = value.astimezone(now.tzinfo)
                else:
                    target = value.astimezone(now.tzinfo)
            progress = _clamped_float(raw.get("progress", 0.0))
            status = str(raw.get("status") or "running").strip().lower()
            if status == "paused":
                if not text:
                    remaining = max(0, int(paused_remaining or 0))
                    text = format_timer_seconds(remaining) if remaining > 0 else "0:00"
                entries.append({"active": True, "text": text, "progress": progress, "status": "paused"})
                continue
            if target is not None:
                remaining = max(0, int((target - now).total_seconds()))
                text = format_timer_seconds(remaining)
                status = "done" if remaining <= 0 and status != "paused" else status
                if started is not None and target > started:
                    total = max(1.0, (target - started).total_seconds())
                    elapsed = max(0.0, (now - started).total_seconds())
                    progress = _clamped_float(elapsed / total)
            if not text:
                continue
            entries.append({"active": True, "text": text, "progress": progress, "status": status})
        return list(reversed(entries))
    active = dict(active_model or {})
    return [active] if active.get("active") else []


def append_countdown_entry(entries: object, minutes: int, started_at: datetime, end_at: datetime) -> list[dict[str, object]]:
    next_entries = [dict(entry) for entry in entries if isinstance(entry, dict)] if isinstance(entries, list) else []
    next_entries.append(
        {
            "minutes": int(minutes),
            "started_at": started_at.isoformat(timespec="seconds"),
            "end_at": end_at.isoformat(timespec="seconds"),
            "status": "running",
        }
    )
    return next_entries[-2:]


def pause_latest_countdown_entry(entries: object, remaining_seconds: int, progress: float) -> list[dict[str, object]]:
    remaining = max(0, int(remaining_seconds))
    clamped_progress = _clamped_float(progress)
    if not isinstance(entries, list) or not entries:
        return [{"text": format_timer_seconds(remaining), "progress": clamped_progress, "status": "paused"}]
    updated = [dict(entry) for entry in entries[-2:] if isinstance(entry, dict)]
    if not updated:
        return []
    latest = dict(updated[-1])
    latest["text"] = format_timer_seconds(remaining)
    latest["progress"] = clamped_progress
    latest["status"] = "paused"
    latest.pop("end_at", None)
    updated[-1] = latest
    return updated[-2:]


def resume_latest_countdown_entry(
    entries: object,
    remaining_seconds: int,
    started_at: datetime,
    end_at: datetime,
) -> list[dict[str, object]]:
    remaining = max(1, int(remaining_seconds))
    if not isinstance(entries, list) or not entries:
        minutes = max(1, (remaining + 59) // 60)
        return append_countdown_entry([], minutes, started_at, end_at)
    updated = [dict(entry) for entry in entries[-2:] if isinstance(entry, dict)]
    if not updated:
        return []
    latest = dict(updated[-1])
    latest["started_at"] = started_at.isoformat(timespec="seconds")
    latest["end_at"] = end_at.isoformat(timespec="seconds")
    latest["status"] = "running"
    latest.pop("text", None)
    updated[-1] = latest
    return updated[-2:]


def handle_timer_action(
    command: str,
    *,
    now: datetime,
    entries: object = None,
    paused_remaining: int = 0,
    settings_remaining: int = 0,
    settings_progress: float = 0.0,
) -> dict[str, object]:
    command = str(command or "").strip().lower()
    start_match = re.fullmatch(r"timer-start-(\d+)", command)
    if start_match:
        minutes = max(1, min(180, int(start_match.group(1))))
        end_at = now + timedelta(minutes=minutes)
        return {
            "handled": True,
            "entries": append_countdown_entry(entries, minutes, now, end_at),
            "clear_paused": True,
            "settings": {
                "v2_timer_started_at": now.isoformat(timespec="seconds"),
                "v2_timer_end_at": end_at.isoformat(timespec="seconds"),
            },
            "mode": "dashboard",
            "refresh": True,
        }

    if command == "timer-pause":
        remaining = max(0, int(settings_remaining or 0))
        if remaining <= 0:
            return {"handled": True, "refresh": False}
        progress = _clamped_float(settings_progress)
        return {
            "handled": True,
            "entries": pause_latest_countdown_entry(entries, remaining, progress),
            "paused": {"remaining": remaining, "progress": progress},
            "settings": {"v2_timer_started_at": "", "v2_timer_end_at": ""},
            "refresh": True,
        }

    if command == "timer-resume":
        remaining = max(0, int(paused_remaining or 0))
        if remaining <= 0:
            return {"handled": True, "refresh": False}
        end_at = now + timedelta(seconds=remaining)
        return {
            "handled": True,
            "entries": resume_latest_countdown_entry(entries, remaining, now, end_at),
            "clear_paused": True,
            "settings": {
                "v2_timer_started_at": now.isoformat(timespec="seconds"),
                "v2_timer_end_at": end_at.isoformat(timespec="seconds"),
            },
            "mode": "dashboard",
            "refresh": True,
        }

    if command == "timer-clear":
        return {
            "handled": True,
            "entries": [],
            "clear_paused": True,
            "settings": {"v2_timer_started_at": "", "v2_timer_end_at": ""},
            "refresh": True,
        }

    return {"handled": False}


def time_compact_segments(
    detail: str,
    summary: str,
    status: str,
    alert_body: str = "",
    *,
    now: datetime,
    show_seconds: bool = True,
    countdown_entries: object = None,
    alarm_rows: object = None,
) -> list[dict[str, object]]:
    status_key = str(status or "").strip().lower()
    segments: list[dict[str, object]] = [
        {"kind": "time", "text": time_display_segment_text(detail, now, show_seconds=show_seconds), "due_at": now}
    ]
    reminders: list[dict[str, object]] = []

    countdown_candidates: list[dict[str, object]] = []
    if isinstance(countdown_entries, list):
        for raw in countdown_entries:
            if not isinstance(raw, dict):
                continue
            due_at = _datetime_from_iso(raw.get("end_at"), now)
            text = str(raw.get("text") or "").strip()
            if due_at and not text:
                text = format_timer_seconds(max(0, int((due_at - now).total_seconds())))
            if text or due_at:
                countdown_candidates.append(
                    {
                        "kind": "countdown",
                        "text": text or "0:00",
                        "due_at": due_at or now,
                        "status": str(raw.get("status") or status_key),
                    }
                )
    fallback_text = strip_timer_prefix(str(alert_body or summary or ""))
    if not countdown_candidates and status_key in {"running", "done", "due"}:
        countdown_candidates.append(
            {
                "kind": "countdown",
                "text": "\u65f6\u95f4\u5230" if status_key == "done" else (fallback_text or plugin_compact_value("TIME", detail, summary, status)),
                "due_at": now if status_key == "done" else now + timedelta(days=1),
                "status": status_key or "running",
            }
        )
    if countdown_candidates:
        countdown_candidates.sort(key=lambda item: item["due_at"])
        reminders.append(countdown_candidates[0])

    alarm_candidates: list[dict[str, object]] = []
    for alarm in alarm_rows if isinstance(alarm_rows, list) else []:
        if not isinstance(alarm, dict) or not bool(alarm.get("enabled", False)):
            continue
        due_at = time_reminder_due_from_alarm(alarm, now)
        if due_at is None:
            continue
        alarm_candidates.append(
            {
                "kind": "alarm",
                "text": str(alarm.get("label") or alarm.get("time") or "\u5907\u6ce8"),
                "due_at": due_at,
                "status": "running",
            }
        )
    if alarm_candidates:
        alarm_candidates.sort(key=lambda item: item["due_at"])
        reminders.append(alarm_candidates[0])
    reminders.sort(key=lambda item: item["due_at"])
    segments.extend(reminders[:2])
    return segments


def alarm_unit_from_frequency(label: str) -> str:
    text = str(label or "").strip()
    if text in {"每周", "周", "weekly"}:
        return "weekly"
    if text in {"每月", "每闰月", "每非明年二月", "每大月", "月", "monthly"}:
        return "monthly"
    if text in {"每年", "每闰年", "闰年", "年", "yearly"}:
        return "yearly"
    return "daily"


def alarm_weekdays_from_value(value: object) -> list[int]:
    if isinstance(value, (list, tuple, set)):
        result = []
        for item in value:
            try:
                day = int(item)
            except (TypeError, ValueError):
                continue
            if 1 <= day <= 7 and day not in result:
                result.append(day)
        return sorted(result)
    text = str(value or "").strip()
    if not text:
        return []
    if text in {"每天", "每日"}:
        return [1, 2, 3, 4, 5, 6, 7]
    if text in {"周末", "末"}:
        return [6, 7]
    mapping = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "日": 7, "天": 7}
    result = []
    for token in re.findall(r"[1-7一二三四五六七日天]", text):
        day = int(token) if token.isdigit() else mapping.get(token, 0)
        if 1 <= day <= 7 and day not in result:
            result.append(day)
    return sorted(result)


def alarm_row_time_parts(row: dict[str, object]) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(row.get("time") or ""))
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def alarm_int(value: object, lower: int, upper: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(default)
    return max(int(lower), min(int(upper), number))


def days_in_month(year: int, month: int) -> int:
    year = max(1, int(year))
    month = max(1, min(12, int(month)))
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    return (next_month - timedelta(days=1)).day


def yearly_alarm_max_day(month: int, boundary_year: int) -> int:
    month = max(1, min(12, int(month)))
    if month == 2:
        return 29
    return days_in_month(boundary_year, month)


def alarm_recurrence_label(editor: dict[str, object]) -> str:
    unit = str(editor.get("frequency_unit") or alarm_unit_from_frequency(str(editor.get("frequency") or ""))).lower()
    if unit == "weekly":
        return "每周"
    if unit == "monthly":
        day = alarm_int(editor.get("month_day", 28), 1, 31, 28)
        if day == 29:
            return "每非明年二月"
        if day == 31:
            return "每大月"
        return "每月"
    if unit == "yearly":
        month = alarm_int(editor.get("year_month", 4), 1, 12, 4)
        day = alarm_int(editor.get("year_day", 28), 1, 31, 28)
        if month == 2 and day == 29:
            return "每闰年"
        return "每年"
    return "每天"


def alarm_recurrence_value_label(editor: dict[str, object]) -> str:
    unit = str(editor.get("frequency_unit") or alarm_unit_from_frequency(str(editor.get("frequency") or ""))).lower()
    if unit == "weekly":
        days = alarm_weekdays_from_value(editor.get("weekdays", []))
        if set(days) == {1, 2, 3, 4, 5, 6, 7}:
            return "整周"
        return " ".join(str(day) for day in days)
    if unit == "monthly":
        day = alarm_int(editor.get("month_day", 28), 1, 31, 28)
        return f"{day}号"
    if unit == "yearly":
        month = alarm_int(editor.get("year_month", 4), 1, 12, 4)
        day = alarm_int(editor.get("year_day", 28), 1, 31, 28)
        return f"{month}月{day}日"
    return ""


def normalize_alarm_editor_recurrence(editor: dict[str, object], boundary_year: int) -> dict[str, object]:
    normalized = dict(editor)
    unit = str(normalized.get("frequency_unit") or "").strip().lower()
    if not unit:
        unit = alarm_unit_from_frequency(str(normalized.get("frequency") or "每周"))
    if unit not in {"daily", "weekly", "monthly", "yearly"}:
        unit = alarm_unit_from_frequency(unit)
    normalized["frequency_unit"] = unit
    if unit == "weekly":
        weekdays = alarm_weekdays_from_value(normalized.get("weekdays", [2, 4]))
        normalized["weekdays"] = weekdays or [1]
    elif unit == "monthly":
        try:
            month_day = int(normalized.get("month_day", 28) or 28)
        except (TypeError, ValueError):
            month_day = 28
        normalized["month_day"] = ((month_day - 1) % 31) + 1
    elif unit == "yearly":
        try:
            month = int(normalized.get("year_month", 4) or 4)
        except (TypeError, ValueError):
            month = 4
        month = ((month - 1) % 12) + 1
        try:
            day = int(normalized.get("year_day", 28) or 28)
        except (TypeError, ValueError):
            day = 28
        max_day = yearly_alarm_max_day(month, boundary_year)
        normalized["year_month"] = month
        normalized["year_day"] = max(1, min(max_day, day))
    normalized["frequency"] = alarm_recurrence_label(normalized)
    return normalized


def default_alarm_editor_state(now: datetime) -> dict[str, object]:
    return {
        "hour": now.hour,
        "minute": now.minute,
        "note": "",
        "frequency": alarm_recurrence_label({"frequency_unit": "weekly"}),
        "frequency_unit": "weekly",
        "weekdays": [2, 4],
        "month_day": 28,
        "year_month": 4,
        "year_day": 28,
        "editor_page": "main",
        "detail_mode": "",
        "edit_index": None,
    }


def alarm_editor_state(editor: object, *, now: datetime) -> dict[str, object]:
    if not isinstance(editor, dict):
        return default_alarm_editor_state(now)
    defaults = default_alarm_editor_state(now)
    state = dict(editor)
    state.setdefault("hour", defaults["hour"])
    state.setdefault("minute", defaults["minute"])
    state.setdefault("note", "")
    state.setdefault("frequency", defaults["frequency"])
    state["frequency_unit"] = str(
        state.get("frequency_unit") or alarm_unit_from_frequency(str(state.get("frequency") or defaults["frequency"]))
    )
    state.setdefault("weekdays", defaults["weekdays"])
    state.setdefault("month_day", defaults["month_day"])
    state.setdefault("year_month", defaults["year_month"])
    state.setdefault("year_day", defaults["year_day"])
    state.setdefault("editor_page", "main")
    state.setdefault("detail_mode", "")
    state.setdefault("edit_index", None)
    return state


def alarm_editor_state_from_row(index: int, row: dict[str, object], *, now: datetime) -> dict[str, object]:
    parsed = alarm_row_time_parts(row)
    if parsed is None:
        hour, minute = now.hour, now.minute
    else:
        hour, minute = parsed
    frequency = str(row.get("frequency") or alarm_recurrence_label({"frequency_unit": "weekly"}))
    return {
        "hour": hour,
        "minute": minute,
        "note": str(row.get("label") or ""),
        "frequency": frequency,
        "weekdays": row.get("weekdays", [2, 4]),
        "frequency_unit": str(row.get("frequency_unit") or alarm_unit_from_frequency(frequency)),
        "month_day": alarm_int(row.get("month_day", 28), 1, 31, 28),
        "year_month": alarm_int(row.get("year_month", 4), 1, 12, 4),
        "year_day": alarm_int(row.get("year_day", 28), 1, 31, 28),
        "editor_page": "main",
        "detail_mode": "",
        "edit_index": int(index),
    }


def handle_alarm_editor_action(
    editor: dict[str, object],
    command: str,
    *,
    boundary_year: int,
) -> dict[str, object]:
    command = str(command or "").strip().lower()
    next_editor = dict(editor)

    focus_match = re.fullmatch(
        r"alarm-editor-focus-(hour|minute|note|frequency|weekdays|frequency_unit|month_day|year_month|year_day)",
        command,
    )
    if focus_match:
        focus = focus_match.group(1)
        return {
            "handled": True,
            "editor": next_editor,
            "focus": focus,
            "clear_input_buffers": focus in {"hour", "minute", "month_day", "year_month", "year_day"},
        }

    if command == "alarm-editor-note":
        return {"handled": True, "editor": next_editor, "focus": "note"}

    if command in {"alarm-editor-page-main", "alarm-editor-page-note"}:
        previous_page = str(next_editor.get("editor_page") or "main")
        next_page = "note" if command.endswith("note") else "main"
        next_editor["editor_page"] = next_page
        next_editor["detail_mode"] = ""
        result: dict[str, object] = {"handled": True, "editor": next_editor}
        if previous_page != next_page:
            result["page_animation"] = {
                "from": previous_page,
                "to": next_page,
                "steps": 1 if next_page == "note" else -1,
            }
        return result

    if command == "alarm-editor-detail-confirm":
        next_editor["detail_mode"] = ""
        next_editor["editor_page"] = "main"
        return {"handled": True, "editor": next_editor, "focus": ""}

    detail_commands = {
        "alarm-editor-open-weekdays": "weekly",
        "alarm-editor-open-month-day": "monthly",
        "alarm-editor-open-year-date": "yearly",
    }
    if command in detail_commands:
        next_editor["detail_mode"] = detail_commands[command]
        next_editor["editor_page"] = "main"
        next_editor = normalize_alarm_editor_recurrence(next_editor, boundary_year)
        return {"handled": True, "editor": next_editor}

    weekday_match = re.fullmatch(r"alarm-editor-toggle-weekday-(\d)", command)
    if weekday_match:
        day = max(1, min(7, int(weekday_match.group(1))))
        weekdays = alarm_weekdays_from_value(next_editor.get("weekdays", []))
        if day in weekdays:
            weekdays = [item for item in weekdays if item != day]
        else:
            weekdays.append(day)
        next_editor["frequency_unit"] = "weekly"
        next_editor["frequency"] = alarm_recurrence_label({"frequency_unit": "weekly"})
        next_editor["weekdays"] = sorted(weekdays) or [day]
        next_editor["detail_mode"] = "weekly"
        return {"handled": True, "editor": next_editor, "spin_field": f"weekday_{day}", "spin_steps": 1}

    return {"handled": False, "editor": next_editor}


def alarm_frequency_units() -> list[tuple[str, str]]:
    return list(ALARM_FREQUENCY_UNITS)


def adjust_alarm_editor_field(
    editor: dict[str, object],
    field: str,
    steps: int,
    *,
    boundary_year: int,
) -> dict[str, object]:
    field = str(field or "")
    steps = int(steps)
    adjusted = True
    next_editor = dict(editor)
    reflow: dict[str, object] | None = None

    if field == "hour":
        next_editor["hour"] = (alarm_int(next_editor.get("hour", 0), 0, 23, 0) + steps) % 24
    elif field == "minute":
        next_editor["minute"] = (alarm_int(next_editor.get("minute", 0), 0, 59, 0) + steps) % 60
    elif field == "frequency":
        options = list(ALARM_FREQUENCY_OPTIONS)
        current = str(next_editor.get("frequency", options[0]))
        index = options.index(current) if current in options else 0
        next_editor["frequency"] = options[(index + steps) % len(options)]
        next_editor["frequency_unit"] = alarm_unit_from_frequency(str(next_editor["frequency"]))
    elif field == "frequency_unit":
        units = alarm_frequency_units()
        current = str(next_editor.get("frequency_unit") or alarm_unit_from_frequency(str(next_editor.get("frequency") or "每周")))
        keys = [key for key, _label in units]
        index = keys.index(current) if current in keys else 0
        next_unit = keys[(index + steps) % len(keys)]
        next_editor["frequency_unit"] = next_unit
        next_editor = normalize_alarm_editor_recurrence(next_editor, boundary_year)
        if next_unit != current:
            reflow = {"from": current, "to": next_unit, "steps": steps}
    elif field == "weekdays":
        options = [list(option) for option in ALARM_WEEKDAY_OPTIONS]
        current = alarm_weekdays_from_value(next_editor.get("weekdays", options[0]))
        index = options.index(current) if current in options else 0
        next_editor["weekdays"] = options[(index + steps) % len(options)]
        next_editor["frequency_unit"] = "weekly"
        next_editor["frequency"] = "每周"
    elif field == "month_day":
        current = alarm_int(next_editor.get("month_day", 28), 1, 31, 28)
        next_editor["frequency_unit"] = "monthly"
        next_editor["month_day"] = ((current + steps - 1) % 31) + 1
        next_editor = normalize_alarm_editor_recurrence(next_editor, boundary_year)
    elif field == "year_month":
        current = alarm_int(next_editor.get("year_month", 4), 1, 12, 4)
        next_editor["frequency_unit"] = "yearly"
        next_editor["year_month"] = ((current + steps - 1) % 12) + 1
        next_editor = normalize_alarm_editor_recurrence(next_editor, boundary_year)
    elif field == "year_day":
        next_editor["frequency_unit"] = "yearly"
        month = alarm_int(next_editor.get("year_month", 4), 1, 12, 4)
        max_day = yearly_alarm_max_day(month, boundary_year)
        current = alarm_int(next_editor.get("year_day", 28), 1, max_day, 28)
        next_editor["year_day"] = ((current + steps - 1) % max_day) + 1
        next_editor = normalize_alarm_editor_recurrence(next_editor, boundary_year)
    else:
        adjusted = False

    return {"editor": next_editor, "adjusted": adjusted, "reflow": reflow}


def alarm_picker_window_values(
    options: list[object],
    current: object,
    *,
    visible_count: int = 7,
    offset: float = 0.0,
    zero_pad: int = 0,
) -> list[dict[str, object]]:
    if not options:
        values = [{"text": str(current), "current": True, "opacity": 1.0}]
        return _alarm_picker_apply_offset(values, offset)
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
        if zero_pad:
            text = f"{int(value):0{int(zero_pad)}d}"
        else:
            text = str(value)
        values.append(
            {
                "text": text,
                "current": current_row,
                "opacity": 1.0 if current_row else max(0.18, 0.54 - distance * 0.1),
            }
        )
    return _alarm_picker_apply_offset(values, offset)


def alarm_picker_display_values(
    editor: dict[str, object],
    field: str,
    *,
    boundary_year: int,
    offset: float = 0.0,
) -> list[dict[str, object]]:
    editor = dict(editor)
    field = str(field or "")
    if field == "hour":
        current = alarm_int(editor.get("hour", 0), 0, 23, 0)
        return alarm_picker_window_values(list(range(24)), current, offset=offset, zero_pad=2)
    if field == "minute":
        current = alarm_int(editor.get("minute", 0), 0, 59, 0)
        return alarm_picker_window_values(list(range(60)), current, offset=offset, zero_pad=2)
    if field == "month_day":
        current = alarm_int(editor.get("month_day", 28), 1, 31, 28)
        return alarm_picker_window_values(list(range(1, 32)), current, offset=offset)
    if field == "year_month":
        current = alarm_int(editor.get("year_month", 4), 1, 12, 4)
        return alarm_picker_window_values(list(range(1, 13)), current, offset=offset)
    if field == "year_day":
        month = alarm_int(editor.get("year_month", 4), 1, 12, 4)
        max_day = yearly_alarm_max_day(month, boundary_year)
        current = alarm_int(editor.get("year_day", 28), 1, max_day, 28)
        return alarm_picker_window_values(list(range(1, max_day + 1)), current, offset=offset)

    options_by_field = {
        "frequency": list(ALARM_FREQUENCY_OPTIONS),
    }
    if field == "frequency_unit":
        units = alarm_frequency_units()
        options_by_field[field] = [label for _key, label in units]
        unit = str(editor.get("frequency_unit") or alarm_unit_from_frequency(str(editor.get("frequency") or "每周")))
        editor[field] = dict(units).get(unit, "每周")
    elif field == "weekdays":
        options_by_field[field] = ["1 3 5", "2 4", "6 7", "整周"]
        editor[field] = alarm_recurrence_value_label({"frequency_unit": "weekly", "weekdays": editor.get("weekdays", [])})

    options = options_by_field.get(field)
    if options:
        current = str(editor.get(field, options[0]))
        return alarm_picker_window_values(list(options), current, offset=offset)
    current = str(editor.get(field, ""))
    return _alarm_picker_apply_offset([{"text": current, "current": True, "opacity": 1.0}], offset)


def handle_alarm_editor_key_input(
    editor: dict[str, object],
    focus: str,
    text: str,
    key_action: str = "",
    *,
    input_buffers: dict[str, str] | None = None,
    boundary_year: int,
) -> dict[str, object]:
    focus = str(focus or "")
    text = str(text or "")
    key_action = str(key_action or "")
    next_editor = dict(editor)
    buffers = dict(input_buffers or {})
    numeric_limits = {
        "hour": (0, 23),
        "minute": (0, 59),
        "month_day": (1, 31),
        "year_month": (1, 12),
    }
    if focus == "year_day":
        month = alarm_int(next_editor.get("year_month", 1), 1, 12, 1)
        numeric_limits["year_day"] = (1, yearly_alarm_max_day(month, boundary_year))

    if focus in numeric_limits:
        current = str(buffers.get(focus, ""))
        if key_action == "backspace":
            current = current[:-1]
        elif key_action == "enter":
            return {"handled": True, "save": True, "editor": next_editor, "input_buffers": buffers}
        elif text and text.isdigit():
            current = text if len(current) >= 2 else current + text
        else:
            return {"handled": False, "editor": next_editor, "input_buffers": buffers}

        lower, upper = numeric_limits[focus]
        value = max(lower, min(upper, int(current or str(lower))))
        next_editor[focus] = value
        if focus == "month_day":
            next_editor["frequency_unit"] = "monthly"
        elif focus in {"year_month", "year_day"}:
            next_editor["frequency_unit"] = "yearly"
        if focus in {"month_day", "year_month", "year_day"}:
            next_editor = normalize_alarm_editor_recurrence(next_editor, boundary_year)
        buffers[focus] = current
        return {
            "handled": True,
            "editor": next_editor,
            "input_buffers": buffers,
            "spin_field": focus,
            "spin_steps": 1,
        }

    if focus != "note":
        return {"handled": False, "editor": next_editor, "input_buffers": buffers}

    note = str(next_editor.get("note") or "")
    if key_action == "backspace":
        next_editor["note"] = note[:-1]
    elif key_action == "enter":
        return {"handled": True, "save": True, "editor": next_editor, "input_buffers": buffers}
    elif text and text.isprintable():
        if len(note) >= 10:
            return {
                "handled": True,
                "editor": next_editor,
                "input_buffers": buffers,
                "note_hint": "最多10字",
                "note_hint_seconds": 1.4,
            }
        next_editor["note"] = (note + text)[:10]
        return {
            "handled": True,
            "editor": next_editor,
            "input_buffers": buffers,
            "note_hint": "",
            "note_hint_seconds": 0.0,
        }
    else:
        return {"handled": False, "editor": next_editor, "input_buffers": buffers}

    return {"handled": True, "editor": next_editor, "input_buffers": buffers}


def alarm_row_from_editor(editor: dict[str, object], *, boundary_year: int) -> dict[str, object]:
    normalized = normalize_alarm_editor_recurrence(editor, boundary_year)
    return {
        "time": f"{alarm_int(normalized.get('hour'), 0, 23, 0):02d}:{alarm_int(normalized.get('minute'), 0, 59, 0):02d}",
        "label": str(normalized.get("note") or "闹钟"),
        "enabled": True,
        "frequency": alarm_recurrence_label(normalized),
        "frequency_unit": str(normalized.get("frequency_unit") or "daily"),
        "weekdays": normalized.get("weekdays", ""),
        "month_day": normalized.get("month_day", 28),
        "year_month": normalized.get("year_month", 4),
        "year_day": normalized.get("year_day", 28),
    }


def coerce_alarm_rows(rows: object, *, boundary_year: int) -> list[dict[str, object]]:
    if isinstance(rows, dict):
        rows = rows.get("alarms") or rows.get("rows") or []
    if not isinstance(rows, list):
        return []
    result: list[dict[str, object]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        parsed = alarm_row_time_parts(item)
        if parsed is None:
            continue
        hour, minute = parsed
        unit = str(item.get("frequency_unit") or "").strip().lower()
        if unit not in {"daily", "weekly", "monthly", "yearly"}:
            unit = alarm_unit_from_frequency(str(item.get("frequency") or ""))
        row = {
            "time": f"{hour:02d}:{minute:02d}",
            "label": str(item.get("label") or item.get("note") or DEFAULT_ALARM_LABEL).strip()[:10] or DEFAULT_ALARM_LABEL,
            "enabled": bool(item.get("enabled", True)),
            "frequency_unit": unit,
            "weekdays": alarm_weekdays_from_value(item.get("weekdays", [])),
            "month_day": alarm_int(item.get("month_day", 28), 1, 31, 28),
            "year_month": alarm_int(item.get("year_month", 4), 1, 12, 4),
            "year_day": alarm_int(item.get("year_day", 28), 1, 31, 28),
        }
        if unit == "weekly" and not row["weekdays"]:
            row["weekdays"] = [1]
        if unit == "yearly":
            row["year_day"] = min(int(row["year_day"]), yearly_alarm_max_day(int(row["year_month"]), boundary_year))
        row["frequency"] = str(item.get("frequency") or alarm_recurrence_label(row))
        result.append(row)
        if len(result) >= 8:
            break
    return result


def handle_alarm_rows_action(rows: object, command: str) -> dict[str, object]:
    command = str(command or "").strip().lower()
    next_rows = [dict(row) for row in rows] if isinstance(rows, list) else []

    toggle_match = re.fullmatch(r"alarm-toggle-(\d+)", command)
    if toggle_match:
        index = int(toggle_match.group(1))
        if 0 <= index < len(next_rows):
            next_rows[index]["enabled"] = not bool(next_rows[index].get("enabled", False))
            return {"handled": True, "changed": True, "rows": next_rows, "clear_delete_mode": True, "request_update": True}
        return {"handled": True, "changed": False, "rows": next_rows, "clear_delete_mode": True, "request_update": False}

    delete_match = re.fullmatch(r"alarm-delete-(\d+)", command)
    if delete_match:
        index = int(delete_match.group(1))
        changed = 0 <= index < len(next_rows)
        if changed:
            next_rows.pop(index)
        return {"handled": True, "changed": changed, "rows": next_rows, "clear_delete_mode": True, "request_update": True}

    return {"handled": False, "changed": False, "rows": next_rows, "clear_delete_mode": False, "request_update": False}


def save_alarm_editor_rows(
    editor: dict[str, object],
    rows: object,
    *,
    boundary_year: int,
) -> list[dict[str, object]]:
    next_rows = [dict(row) for row in rows] if isinstance(rows, list) else []
    new_row = alarm_row_from_editor(editor, boundary_year=boundary_year)
    edit_index = editor.get("edit_index")
    try:
        edit_index = int(edit_index)
    except (TypeError, ValueError):
        edit_index = None
    if edit_index is not None and 0 <= edit_index < len(next_rows):
        new_row["enabled"] = bool(next_rows[edit_index].get("enabled", True))
        next_rows[edit_index].update(new_row)
    else:
        next_rows.insert(0, new_row)
    return next_rows


def alarm_row_matches_date(row: dict[str, object], due: datetime) -> bool:
    unit = str(row.get("frequency_unit") or alarm_unit_from_frequency(str(row.get("frequency") or ""))).strip().lower()
    if unit == "weekly":
        weekdays = alarm_weekdays_from_value(row.get("weekdays", []))
        return due.isoweekday() in weekdays if weekdays else False
    if unit == "monthly":
        day = alarm_int(row.get("month_day", 1), 1, 31, 1)
        return day <= days_in_month(due.year, due.month) and due.day == day
    if unit == "yearly":
        month = alarm_int(row.get("year_month", 1), 1, 12, 1)
        day = alarm_int(row.get("year_day", 1), 1, 31, 1)
        return due.month == month and due.day == day and day <= days_in_month(due.year, month)
    return True


def time_reminder_due_from_alarm(alarm: dict[str, object], now: datetime) -> datetime | None:
    parsed = alarm_row_time_parts(alarm)
    if parsed is None:
        return None
    hour, minute = parsed
    for offset in range(0, 366 * 5):
        day = now + timedelta(days=offset)
        due = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if due < now:
            continue
        if alarm_row_matches_date(alarm, due):
            return due
    return None


def plugin_alert_display_value(
    label: str,
    detail: str,
    summary: str,
    status: str,
    alert_title: str = "",
    alert_body: str = "",
) -> str:
    label_key = str(label or "").strip().upper()
    status_key = str(status or "").strip().lower()
    body = str(alert_body or summary or detail or "").strip()
    if label_key == "TIME" and status_key in {"running", "done"}:
        value = strip_timer_prefix(body) or strip_timer_prefix(detail)
        if status_key == "done" and not value:
            value = "时间到"
        return time_reminder_display_text(value or "TIME")
    return plugin_compact_value(label, detail, body, status)


def plugin_alert_status_is_active(status: str) -> bool:
    return str(status or "").strip().lower() in PLUGIN_ALERT_STATUSES


def plugin_alert_status_priority(status: str, default: int = 99) -> int:
    return int(PLUGIN_ALERT_STATUS_PRIORITY.get(str(status or "").strip().lower(), int(default)))


def plugin_alert_sort_key(priority: int, status: str, index: int) -> tuple[int, int, int]:
    return int(priority), plugin_alert_status_priority(status), int(index)


def generic_plugin_alert_candidate(
    *,
    index: int,
    label: str,
    detail: str,
    summary: str,
    color: object,
    icon: str,
    plugin_id: str,
    status: str,
    alert_title: str = "",
    alert_body: str = "",
    alert_priority: int = 50,
) -> tuple[int, int, int, str, str, str, object, str, str, str, str, str, int] | None:
    normalized_status = str(status or "").lower()
    if not (plugin_alert_status_is_active(normalized_status) or alert_title or alert_body):
        return None
    return (
        int(alert_priority),
        plugin_alert_status_priority(normalized_status),
        int(index),
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
    )


def active_plugin_alerts(
    candidates: Sequence[Sequence[Any]],
    limit: int = 2,
) -> list[tuple[int, str, str, str, object, str, str, str, str, str, int]]:
    normalized = [tuple(candidate) for candidate in candidates if len(tuple(candidate)) >= 13]
    normalized.sort(key=lambda item: plugin_alert_sort_key(item[0], item[9], item[2]))
    return [
        (index, label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority)
        for _alert_priority, _status_priority, index, label, detail, summary, color, icon, plugin_id, status, alert_title, alert_body, alert_priority in normalized[
            : max(0, int(limit))
        ]
    ]


def taskbar_main_overlay_alert(
    alerts: Sequence[Sequence[Any]],
    *,
    taskbar_compact_active: bool,
) -> Sequence[Any] | None:
    if not taskbar_compact_active:
        return None
    for alert in alerts:
        values = tuple(alert)
        if len(values) < 8:
            continue
        label = values[1]
        plugin_id = values[6]
        status = values[7]
        is_time = str(label or "").strip().upper() == "TIME" or str(plugin_id or "").strip().casefold().startswith("time")
        if is_time and str(status or "").strip().lower() in {"done", "due", "alarm"}:
            return alert
    return None


def time_plugin_alert_width_units(detail: str, summary: str) -> float:
    source = str(summary or detail or "")
    if time_reminder_kind("", source) == "alarm" or not strip_timer_prefix(source):
        note = time_alarm_alert_text(source)
        char_units = max(5, min(10, len(note)))
        return max(150.0, min(168.0, 92.0 + char_units * 10.0))
    value = strip_timer_prefix(source)
    value_units = max(46.0, min(72.0, len(value) * 6.8 + 22.0)) if value else 48.0
    return max(150.0, min(168.0, 84.0 + value_units))


def plugin_alert_width_units(label: str, detail: str, summary: str) -> float:
    if str(label or "").strip().upper() == "TIME":
        return time_plugin_alert_width_units(detail, summary)
    longest = max(len(str(label or "")), len(str(detail or "")), len(str(summary or "")))
    summary_weight = min(10, len(str(summary or "")))
    width_units = 112.0 + min(10, longest) * 4.0 + summary_weight * 2.0
    return max(126.0, min(226.0, width_units))


def plugin_action_model(label: str, detail: str, summary: str, status: str) -> dict[str, str]:
    label_key = str(label or "").strip().upper()
    status_key = str(status or "").strip().lower()
    if label_key == "TIME":
        if status_key in {"running", "done", "alarm"}:
            return {"command": "timer-clear", "label": "清除计时", "hint": "停止当前提醒"}
        return {"command": "timer-open", "label": "设置计时", "hint": "打开时间插件"}
    if label_key == "WEATHER":
        return {"command": "weather-refresh", "label": "刷新天气", "hint": "立即更新"}
    if label_key == "AGENDA":
        return {"command": "agenda-open", "label": "管理日程", "hint": "添加或查看"}
    return {"command": "plugin-open", "label": "打开插件", "hint": "查看详情"}


def plugin_ring_style(status: str = "normal") -> dict[str, float]:
    status_key = str(status or "").strip().lower()
    animated = status_key in PLUGIN_ANIMATED_STATUSES
    return {
        "width": 3.8 if animated else 2.2,
        "track_width": 3.2 if animated else 1.8,
        "pulse": 1.0 if animated else 0.0,
    }


def plugin_alert_text_parts(
    label: str,
    detail: str,
    summary: str,
    status: str,
    alert_title: str = "",
    alert_body: str = "",
) -> tuple[str, str]:
    normalized_status = str(status or "").strip().lower()
    status_text = {
        "done": "完成",
        "due": "现在",
        "soon": "将近",
        "running": "计时中",
        "loading": "刷新中",
        "error": "需要注意",
        "alarm": "闹钟",
        "severe": "严重天气",
        "rain": "降雨",
    }.get(normalized_status, "提醒")
    if str(alert_title or "").strip() or str(alert_body or "").strip():
        title = str(alert_title or status_text).strip()
        body = str(alert_body or detail or summary or "").strip()
        return f"{label} · {title}", body
    body = str(detail or summary or "")
    if normalized_status == "running" and str(summary or "").strip():
        body = str(summary)
    return f"{label} · {status_text}", body


def plugin_panel_reminder_models(
    alerts: Sequence[Sequence[Any]],
    samples: Sequence[Sequence[Any]],
) -> list[dict[str, Any]]:
    reminders: list[dict[str, Any]] = []
    for alert in alerts:
        values = list(alert)
        index = int(values[0]) if len(values) > 0 else 0
        label = values[1] if len(values) > 1 else ""
        detail = values[2] if len(values) > 2 else ""
        summary = values[3] if len(values) > 3 else ""
        color = values[4] if len(values) > 4 else ""
        icon = values[5] if len(values) > 5 else ""
        plugin_id = values[6] if len(values) > 6 else ""
        status = values[7] if len(values) > 7 else "normal"
        alert_title = values[8] if len(values) > 8 else ""
        alert_body = values[9] if len(values) > 9 else ""
        title, body = plugin_alert_text_parts(str(label), str(detail), str(summary), str(status), str(alert_title), str(alert_body))
        progress = 0.0
        try:
            sample_values = list(samples[index])
            progress = float(sample_values[10] if len(sample_values) > 10 else 0.0)
        except Exception:
            progress = 0.0
        reminders.append(
            {
                "index": index,
                "label": label,
                "detail": detail,
                "summary": summary,
                "color": color,
                "icon": icon,
                "plugin_id": plugin_id,
                "status": status,
                "title": title,
                "body": body,
                "progress": max(0.0, min(1.0, float(progress or 0.0))),
            }
        )
    return reminders


def plugin_function_lines(
    label: str,
    detail: str,
    summary: str,
    status: str,
    alert_title: str = "",
    alert_body: str = "",
) -> tuple[str, tuple[str, ...]]:
    label_key = str(label or "").strip().upper()
    detail = str(detail or "").strip()
    summary = str(summary or "").strip()
    status_key = str(status or "").strip().lower()
    status_copy = {
        "running": "进行中",
        "done": "已完成",
        "due": "现在",
        "soon": "即将开始",
        "loading": "刷新中",
        "error": "需要注意",
    }.get(status_key, "实时状态")
    alert_line = str(alert_body or alert_title or "").strip()
    if label_key == "TIME":
        value = plugin_compact_value(label, detail, summary, status)
        second = alert_line or (summary if summary and summary != value else status_copy)
        return "时间 TIME", tuple(line for line in (value, second, "时钟、倒计时与闹钟在这里运行") if line)
    if label_key == "WEATHER":
        return "天气 WEATHER", tuple(line for line in (detail or "天气刷新中", alert_line or summary or status_copy, "公共天气源 · 定位优先") if line)
    if label_key == "AGENDA":
        return "日程 AGENDA", tuple(line for line in (detail or "暂无日程", alert_line or summary or "JSON / ICS", "课表读取录入 · 课表提醒功能") if line)
    return str(label or "PLUGIN"), tuple(line for line in (detail, summary, status_copy) if line)


def time_plugin_panel_sections(model: dict[str, object]) -> list[dict[str, str]]:
    detail = str(model.get("detail", "") or "").strip()
    summary = str(model.get("summary", "") or "").strip()
    status = str(model.get("status", "") or "").strip().lower()
    alert_title = str(model.get("alert_title", "") or "").strip()
    alert_body = str(model.get("alert_body", "") or "").strip()
    alert = alert_body or alert_title
    clock = detail or "本地时间"
    if status in {"running", "done", "alarm"}:
        countdown = alert or summary or "计时提醒"
    else:
        countdown = "5 / 15 / 30 分钟"
    alarm = alert if status == "alarm" else (summary if re.search(r"\d{1,2}:\d{2}", summary) else "未设置")
    return [
        {"id": "clock", "title": "本地时钟", "body": clock},
        {"id": "countdown", "title": "倒计时", "body": countdown},
        {"id": "alarm", "title": "闹钟", "body": alarm or "未设置"},
    ]


def weather_plugin_panel_sections(model: dict[str, object]) -> list[dict[str, str]]:
    detail = str(model.get("detail", "") or "").strip()
    summary = str(model.get("summary", "") or "").strip()
    alert_title = str(model.get("alert_title", "") or "").strip()
    alert_body = str(model.get("alert_body", "") or "").strip()
    alert = alert_body or alert_title
    metrics = summary or alert or "等待天气数据"
    return [
        {"id": "current", "title": "当前天气", "body": detail or "刷新中"},
        {"id": "metrics", "title": "风与湿度", "body": metrics},
        {"id": "source", "title": "数据源", "body": alert or "定位优先 · 公共天气源"},
    ]


def agenda_plugin_panel_sections(model: dict[str, object]) -> list[dict[str, str]]:
    detail = str(model.get("detail", "") or "").strip()
    summary = str(model.get("summary", "") or "").strip()
    alert_title = str(model.get("alert_title", "") or "").strip()
    alert_body = str(model.get("alert_body", "") or "").strip()
    alert = alert_body or alert_title
    next_item = alert or summary or detail or "暂无日程"
    return [
        {"id": "next", "title": "下一项", "body": next_item},
        {"id": "todo", "title": "代办清单", "body": "课表读取录入 · 课表提醒功能"},
        {"id": "reminder", "title": "提醒", "body": "15 / 30 / 5 分钟"},
    ]


def generic_plugin_panel_sections(model: dict[str, object]) -> list[dict[str, str]]:
    label = str(model.get("label", "")).strip()
    detail = str(model.get("detail", "") or "").strip()
    summary = str(model.get("summary", "") or "").strip()
    status = str(model.get("status", "") or "").strip().lower()
    return [
        {"id": "primary", "title": label or "PLUGIN", "body": detail},
        {"id": "secondary", "title": "摘要", "body": summary},
        {"id": "state", "title": "状态", "body": status or "normal"},
    ]


def plugin_panel_sections(model: dict[str, object]) -> list[dict[str, str]]:
    label = str(model.get("label", "")).strip().upper()
    if label == "TIME":
        return time_plugin_panel_sections(model)
    if label == "WEATHER":
        return weather_plugin_panel_sections(model)
    if label == "AGENDA":
        return agenda_plugin_panel_sections(model)
    return generic_plugin_panel_sections(model)


def _plugin_panel_default_value(key: str) -> Any:
    if key in {"alert_priority"}:
        return 50
    if key in {"progress"}:
        return 0.0
    if key in {"status"}:
        return "normal"
    return ""


def _datetime_from_iso(value: object, now: datetime) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
    return parsed.astimezone(now.tzinfo)


def _alarm_picker_apply_offset(values: list[dict[str, object]], offset: float) -> list[dict[str, object]]:
    try:
        offset = float(offset or 0.0)
    except (TypeError, ValueError):
        offset = 0.0
    if abs(offset) <= 0.001:
        return values
    adjusted = []
    for item in values:
        clone = dict(item)
        clone["offset"] = offset
        adjusted.append(clone)
    return adjusted


def _clamped_float(value: object, lower: float = 0.0, upper: float = 1.0) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        number = 0.0
    return max(float(lower), min(float(upper), number))
