from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
from typing import Any


@dataclass(frozen=True)
class RuntimeVerificationIssue:
    key: str
    severity: str
    summary: str

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "severity": self.severity,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class RuntimeVerificationRequirement:
    key: str
    status: str
    current: object
    required: object
    summary: str

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "status": self.status,
            "current": self.current,
            "required": self.required,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class RuntimeVerificationReport:
    status: str = "missing"
    ready: bool = False
    summary: str = "没有足够运行诊断样本"
    health_status: str = "missing"
    dominant_area: str = ""
    bottleneck_group: str = ""
    ambience_style: str = ""
    lyric_flow_style: str = ""
    reactive_background_enabled: bool = False
    trace_sample_count: int = 0
    trace_window_seconds: float = 0.0
    min_trace_samples: int = 3
    min_trace_window_seconds: float = 2.0
    remaining_trace_samples: int = 3
    remaining_trace_window_seconds: float = 2.0
    suggested_wait_seconds: int = 3
    next_action: str = "保持音乐岛可见并运行数秒后刷新或导出诊断"
    requirements: tuple[RuntimeVerificationRequirement, ...] = ()
    issues: tuple[RuntimeVerificationIssue, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "ready": bool(self.ready),
            "summary": self.summary,
            "health_status": self.health_status,
            "dominant_area": self.dominant_area,
            "bottleneck_group": self.bottleneck_group,
            "ambience_style": self.ambience_style,
            "lyric_flow_style": self.lyric_flow_style,
            "reactive_background_enabled": bool(self.reactive_background_enabled),
            "trace_sample_count": int(self.trace_sample_count),
            "trace_window_seconds": round(float(self.trace_window_seconds), 3),
            "min_trace_samples": int(self.min_trace_samples),
            "min_trace_window_seconds": round(float(self.min_trace_window_seconds), 3),
            "remaining_trace_samples": int(self.remaining_trace_samples),
            "remaining_trace_window_seconds": round(float(self.remaining_trace_window_seconds), 3),
            "suggested_wait_seconds": int(self.suggested_wait_seconds),
            "next_action": self.next_action,
            "requirements": [item.as_dict() for item in self.requirements],
            "issues": [item.as_dict() for item in self.issues],
        }


def build_runtime_verification_report(
    diagnostics: Mapping[str, Any] | None,
    *,
    min_trace_samples: int = 3,
    min_trace_window_seconds: float = 2.0,
) -> RuntimeVerificationReport:
    data = diagnostics if isinstance(diagnostics, Mapping) else {}
    snapshot = _mapping(data.get("runtime_diagnostics_snapshot"))
    trace = _mapping(data.get("runtime_diagnostics_trace"))
    visual_context = _mapping(data.get("visual_runtime_context"))
    frame = _mapping(data.get("visual_frame_diagnostics"))
    paint = _mapping(data.get("visual_paint_section_diagnostics"))
    lyric = _mapping(data.get("lyric_timeline_diagnostics"))
    reactive = _mapping(data.get("music_reactive_input_diagnostics"))

    trace_samples = int(_float(trace.get("sample_count")))
    trace_window = _float(trace.get("window_seconds"))
    frame_samples = int(_float(frame.get("sample_count", frame.get("frame_count"))))
    paint_groups = _mapping(paint.get("groups"))
    ambience_style = str(
        visual_context.get("effective_ambience_style")
        or visual_context.get("ambience_style")
        or ""
    )
    lyric_flow_style = str(visual_context.get("lyric_flow_style") or "")
    reactive_enabled = bool(
        visual_context.get("reactive_background_enabled", reactive.get("reactive_enabled", False))
    )
    reactive_has_input = bool(reactive) and (
        bool(reactive.get("band_source")) or bool(reactive.get("sampled_bands"))
    )
    trace_targets = _trace_capture_targets(
        trace_samples=trace_samples,
        trace_window=trace_window,
        min_trace_samples=max(1, int(min_trace_samples)),
        min_trace_window_seconds=max(0.0, float(min_trace_window_seconds)),
    )

    requirements = (
        _requirement(
            "runtime_trace_samples",
            trace_samples >= max(1, int(min_trace_samples)),
            trace_samples,
            f">= {max(1, int(min_trace_samples))}",
            "最近运行诊断样本数",
        ),
        _requirement(
            "runtime_trace_window",
            trace_window >= max(0.0, float(min_trace_window_seconds)),
            round(trace_window, 3),
            f">= {max(0.0, float(min_trace_window_seconds)):.1f}s",
            "最近运行诊断覆盖时长",
        ),
        _requirement(
            "visual_frame_samples",
            frame_samples > 0,
            frame_samples,
            "> 0",
            "渲染帧采样",
        ),
        _requirement(
            "visual_paint_groups",
            bool(paint_groups),
            len(paint_groups),
            "> 0",
            "绘制耗时分组",
        ),
        _requirement(
            "visual_runtime_context",
            bool(ambience_style and lyric_flow_style),
            _style_context_summary(ambience_style, lyric_flow_style),
            "background + lyric style",
            "视觉上下文",
        ),
        _requirement(
            "lyric_timeline",
            bool(lyric) and "visible_lyric_index" in lyric,
            "present" if lyric else "missing",
            "visible lyric index",
            "歌词时间线诊断",
        ),
        _requirement(
            "reactive_input",
            (not reactive_enabled) or reactive_has_input,
            "present" if reactive_has_input else "missing",
            "present when reactive enabled",
            "律动输入诊断",
        ),
    )

    issues = tuple(_issues_for_requirements(requirements))
    ready = all(item.status == "ok" for item in requirements)
    if ready:
        status = "ready"
        summary = _ready_summary(snapshot, trace)
    elif any(item.status == "ok" for item in requirements):
        status = "partial"
        summary = _partial_summary(issues)
    else:
        status = "missing"
        summary = "没有足够运行诊断样本"

    return RuntimeVerificationReport(
        status=status,
        ready=ready,
        summary=summary,
        health_status=str(
            trace.get("latest", {}).get("status")
            if isinstance(trace.get("latest"), Mapping)
            else snapshot.get("status") or "missing"
        )
        or "missing",
        dominant_area=str(trace.get("dominant_area") or snapshot.get("dominant_area") or ""),
        bottleneck_group=str(trace.get("bottleneck_group") or snapshot.get("bottleneck_group") or ""),
        ambience_style=ambience_style,
        lyric_flow_style=lyric_flow_style,
        reactive_background_enabled=reactive_enabled,
        trace_sample_count=trace_samples,
        trace_window_seconds=trace_window,
        min_trace_samples=trace_targets["min_trace_samples"],
        min_trace_window_seconds=trace_targets["min_trace_window_seconds"],
        remaining_trace_samples=trace_targets["remaining_trace_samples"],
        remaining_trace_window_seconds=trace_targets["remaining_trace_window_seconds"],
        suggested_wait_seconds=trace_targets["suggested_wait_seconds"],
        next_action=_next_action(ready=ready, issues=issues, trace_targets=trace_targets),
        requirements=requirements,
        issues=issues,
    )


def _trace_capture_targets(
    *,
    trace_samples: int,
    trace_window: float,
    min_trace_samples: int,
    min_trace_window_seconds: float,
) -> dict[str, int | float]:
    remaining_samples = max(0, int(min_trace_samples) - int(trace_samples))
    remaining_window = max(0.0, float(min_trace_window_seconds) - float(trace_window))
    wait_for_samples = float(remaining_samples)
    if int(trace_samples) <= 0 and remaining_samples > 0:
        wait_for_samples = float(max(0, remaining_samples - 1))
    suggested_wait = int(math.ceil(max(remaining_window, wait_for_samples, 0.0)))
    if remaining_samples > 0 or remaining_window > 0.0:
        suggested_wait = max(1, suggested_wait)
    return {
        "min_trace_samples": int(min_trace_samples),
        "min_trace_window_seconds": round(float(min_trace_window_seconds), 3),
        "remaining_trace_samples": int(remaining_samples),
        "remaining_trace_window_seconds": round(float(remaining_window), 3),
        "suggested_wait_seconds": int(suggested_wait),
    }


def _requirement(
    key: str,
    ok: bool,
    current: object,
    required: object,
    summary: str,
) -> RuntimeVerificationRequirement:
    return RuntimeVerificationRequirement(
        key=key,
        status="ok" if ok else "missing",
        current=current,
        required=required,
        summary=summary,
    )


def _issues_for_requirements(
    requirements: tuple[RuntimeVerificationRequirement, ...],
) -> tuple[RuntimeVerificationIssue, ...]:
    issues: list[RuntimeVerificationIssue] = []
    for requirement in requirements:
        if requirement.status == "ok":
            continue
        severity = "warning" if requirement.key in {"runtime_trace_samples", "runtime_trace_window"} else "attention"
        issues.append(
            RuntimeVerificationIssue(
                key=requirement.key,
                severity=severity,
                summary=f"{requirement.summary}不足：当前 {requirement.current}，需要 {requirement.required}",
            )
        )
    return tuple(issues)


def _next_action(
    *,
    ready: bool,
    issues: tuple[RuntimeVerificationIssue, ...],
    trace_targets: Mapping[str, int | float],
) -> str:
    if ready:
        return "运行证据已足够，可以导出并用于判断卡顿、歌词跟进或绘制瓶颈"
    issue_keys = {issue.key for issue in issues}
    wait_seconds = int(trace_targets.get("suggested_wait_seconds", 0) or 0)
    if {"runtime_trace_samples", "runtime_trace_window"} & issue_keys:
        return f"保持音乐岛可见并运行约 {max(1, wait_seconds)} 秒后刷新或导出诊断"
    return "保持播放和当前背景样式，刷新诊断以补齐歌词、绘制或律动输入证据"


def _ready_summary(
    snapshot: Mapping[str, Any],
    trace: Mapping[str, Any],
) -> str:
    health = str(
        trace.get("latest", {}).get("status")
        if isinstance(trace.get("latest"), Mapping)
        else snapshot.get("status") or "missing"
    )
    dominant = str(trace.get("dominant_area") or snapshot.get("dominant_area") or "").strip()
    bottleneck = str(trace.get("bottleneck_group") or snapshot.get("bottleneck_group") or "").strip()
    parts = [f"运行证据可用，健康状态 {health or 'missing'}"]
    if dominant:
        parts.append(f"主因 {dominant}")
    if bottleneck:
        parts.append(f"瓶颈 {bottleneck}")
    return " / ".join(parts)


def _partial_summary(issues: tuple[RuntimeVerificationIssue, ...]) -> str:
    if not issues:
        return "运行证据不完整"
    return "运行证据不完整：" + "；".join(issue.summary for issue in issues[:3])


def _style_context_summary(ambience_style: str, lyric_flow_style: str) -> str:
    if not ambience_style and not lyric_flow_style:
        return "missing"
    return f"{ambience_style or '-'} / {lyric_flow_style or '-'}"


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
