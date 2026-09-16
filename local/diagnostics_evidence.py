from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local.runtime_verification import build_runtime_verification_report


_DIAGNOSTIC_KEYS = frozenset(
    {
        "runtime_diagnostics_snapshot",
        "runtime_diagnostics_trace",
        "runtime_verification_report",
        "visual_runtime_context",
        "visual_frame_diagnostics",
        "visual_paint_section_diagnostics",
        "lyric_timeline_diagnostics",
        "music_reactive_input_diagnostics",
        "music_wave_cadence_diagnostics",
    }
)


@dataclass(frozen=True)
class DiagnosticsEvidenceIssue:
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
class DiagnosticsEvidenceReport:
    source_kind: str = "raw"
    source_schema_version: int | None = None
    generated_at: str = ""
    app_name: str = ""
    app_version: str = ""
    status: str = "missing"
    ready: bool = False
    summary: str = "没有足够运行诊断样本"
    next_action: str = "保持音乐岛可见并运行数秒后刷新或导出诊断"
    health_status: str = "missing"
    dominant_area: str = ""
    bottleneck_group: str = ""
    ambience_style: str = ""
    lyric_flow_style: str = ""
    reactive_background_enabled: bool = False
    trace_sample_count: int = 0
    trace_window_seconds: float = 0.0
    issues: tuple[DiagnosticsEvidenceIssue, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "source_kind": self.source_kind,
            "source_schema_version": self.source_schema_version,
            "generated_at": self.generated_at,
            "app_name": self.app_name,
            "app_version": self.app_version,
            "status": self.status,
            "ready": bool(self.ready),
            "summary": self.summary,
            "next_action": self.next_action,
            "health_status": self.health_status,
            "dominant_area": self.dominant_area,
            "bottleneck_group": self.bottleneck_group,
            "ambience_style": self.ambience_style,
            "lyric_flow_style": self.lyric_flow_style,
            "reactive_background_enabled": bool(self.reactive_background_enabled),
            "trace_sample_count": int(self.trace_sample_count),
            "trace_window_seconds": round(float(self.trace_window_seconds), 3),
            "issues": [issue.as_dict() for issue in self.issues],
        }


def load_diagnostics_evidence_file(path: str | Path) -> DiagnosticsEvidenceReport:
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return _invalid_report(
            "missing_file",
            f"诊断文件不存在：{target}",
            next_action="重新从 GluSpot 关于页导出运行诊断 JSON",
        )
    except json.JSONDecodeError as exc:
        return _invalid_report(
            "invalid_json",
            f"诊断文件不是有效 JSON：第 {exc.lineno} 行第 {exc.colno} 列",
            next_action="重新导出诊断文件，或确认文件没有被截断/手动改坏",
        )
    return build_diagnostics_evidence_report(payload)


def build_diagnostics_evidence_report(payload: Mapping[str, Any] | None) -> DiagnosticsEvidenceReport:
    if not isinstance(payload, Mapping):
        return _invalid_report(
            "invalid_payload",
            "诊断证据必须是 JSON object / mapping",
            next_action="提供 GluSpot 导出的运行诊断 JSON",
        )

    envelope = _extract_envelope(payload)
    if envelope.source_kind == "invalid":
        return _invalid_report(
            "missing_diagnostics",
            "导出文件缺少 diagnostics 对象",
            source_schema_version=envelope.source_schema_version,
            generated_at=envelope.generated_at,
            app_name=envelope.app_name,
            app_version=envelope.app_version,
            next_action="请从 GluSpot 关于页重新导出完整 diagnostics JSON",
        )
    if not envelope.diagnostics:
        return DiagnosticsEvidenceReport(
            source_kind=envelope.source_kind,
            source_schema_version=envelope.source_schema_version,
            generated_at=envelope.generated_at,
            app_name=envelope.app_name,
            app_version=envelope.app_version,
            status="missing",
            ready=False,
            summary="没有足够运行诊断样本",
            next_action="保持音乐岛可见并运行数秒后刷新或导出诊断",
        )

    verification = _verification_report(envelope.diagnostics)
    issues = tuple(_evidence_issues(verification))
    ready = bool(verification.get("ready")) and str(verification.get("status") or "") == "ready"
    status = "ready" if ready else str(verification.get("status") or "missing")
    summary = _evidence_summary(
        source_kind=envelope.source_kind,
        ready=ready,
        verification_summary=str(verification.get("summary") or "没有足够运行诊断样本"),
    )

    trace = _mapping(envelope.diagnostics.get("runtime_diagnostics_trace"))
    snapshot = _mapping(envelope.diagnostics.get("runtime_diagnostics_snapshot"))
    context = _mapping(envelope.diagnostics.get("visual_runtime_context"))

    dominant_area = str(
        verification.get("dominant_area") or trace.get("dominant_area") or snapshot.get("dominant_area") or ""
    )
    bottleneck_group = str(
        verification.get("bottleneck_group")
        or trace.get("bottleneck_group")
        or snapshot.get("bottleneck_group")
        or ""
    )
    trace_sample_count = int(_float(verification.get("trace_sample_count", trace.get("sample_count"))))
    trace_window_seconds = _float(verification.get("trace_window_seconds", trace.get("window_seconds")))

    return DiagnosticsEvidenceReport(
        source_kind=envelope.source_kind,
        source_schema_version=envelope.source_schema_version,
        generated_at=envelope.generated_at,
        app_name=envelope.app_name,
        app_version=envelope.app_version,
        status=status,
        ready=ready,
        summary=summary,
        next_action=_evidence_next_action(ready=ready, report=verification),
        health_status=str(verification.get("health_status") or "missing"),
        dominant_area=dominant_area,
        bottleneck_group=bottleneck_group,
        ambience_style=str(
            verification.get("ambience_style")
            or context.get("effective_ambience_style")
            or context.get("ambience_style")
            or ""
        ),
        lyric_flow_style=str(verification.get("lyric_flow_style") or context.get("lyric_flow_style") or ""),
        reactive_background_enabled=bool(
            verification.get(
                "reactive_background_enabled",
                context.get("reactive_background_enabled", False),
            )
        ),
        trace_sample_count=trace_sample_count,
        trace_window_seconds=trace_window_seconds,
        issues=issues,
    )


@dataclass(frozen=True)
class _DiagnosticsEnvelope:
    source_kind: str
    diagnostics: Mapping[str, Any]
    source_schema_version: int | None = None
    generated_at: str = ""
    app_name: str = ""
    app_version: str = ""


def _extract_envelope(payload: Mapping[str, Any]) -> _DiagnosticsEnvelope:
    app = payload.get("app")
    app_data = app if isinstance(app, Mapping) else {}
    if "diagnostics" in payload:
        diagnostics = payload.get("diagnostics")
        if not isinstance(diagnostics, Mapping):
            return _DiagnosticsEnvelope(
                source_kind="invalid",
                diagnostics={},
                source_schema_version=_optional_int(payload.get("schema_version")),
                generated_at=str(payload.get("generated_at") or ""),
                app_name=str(app_data.get("name") or ""),
                app_version=str(app_data.get("version") or ""),
            )
        return _DiagnosticsEnvelope(
            source_kind="export",
            diagnostics=diagnostics,
            source_schema_version=_optional_int(payload.get("schema_version")),
            generated_at=str(payload.get("generated_at") or ""),
            app_name=str(app_data.get("name") or ""),
            app_version=str(app_data.get("version") or ""),
        )
    return _DiagnosticsEnvelope(
        source_kind="raw",
        diagnostics=payload if _looks_like_diagnostics(payload) else {},
    )


def _verification_report(diagnostics: Mapping[str, Any]) -> Mapping[str, Any]:
    existing = diagnostics.get("runtime_verification_report")
    if isinstance(existing, Mapping):
        return existing
    return build_runtime_verification_report(diagnostics).as_dict()


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _evidence_issues(report: Mapping[str, Any]) -> tuple[DiagnosticsEvidenceIssue, ...]:
    issues = []
    raw_issues = report.get("issues")
    if isinstance(raw_issues, list):
        for item in raw_issues:
            if not isinstance(item, Mapping):
                continue
            issues.append(
                DiagnosticsEvidenceIssue(
                    key=str(item.get("key") or "runtime_evidence"),
                    severity=str(item.get("severity") or "attention"),
                    summary=str(item.get("summary") or "运行证据不完整"),
                )
            )
    return tuple(issues)


def _evidence_summary(*, source_kind: str, ready: bool, verification_summary: str) -> str:
    if not ready:
        return verification_summary
    prefix = "导出证据可用" if source_kind == "export" else "运行证据可用"
    if verification_summary:
        return f"{prefix}：{verification_summary}"
    return prefix


def _evidence_next_action(*, ready: bool, report: Mapping[str, Any]) -> str:
    if not ready:
        return str(report.get("next_action") or "保持音乐岛可见并运行数秒后刷新或导出诊断")
    area = str(report.get("dominant_area") or report.get("bottleneck_group") or "").strip()
    return _READY_NEXT_ACTIONS.get(
        area,
        "证据已足够；先按 dominant_area 和绘制分组定位，再做小步修复和回归验证",
    )


_READY_NEXT_ACTIONS = {
    "music_wave_cadence": "证据指向音乐波形 cadence；优先检查波形更新是否重新耦合到歌词 FPS 或渲染 FPS",
    "frame_cadence": "证据指向帧调度；优先检查动画定时器、重绘触发频率，以及律动是否抢占歌词刷新",
    "paint": "证据指向绘制耗时；优先检查 visual_paint_section_diagnostics 中的热点分组",
    "background_surface": "证据指向背景绘制；优先检查背景样式自身的分层绘制与缓存策略",
    "reactive_effects": "证据指向律动/过渡；优先检查音频平滑、左右波形/海平面渲染和绘制缓存",
    "content": "证据指向内容/歌词；优先检查歌词绘制、专辑封面合成和消息覆盖绘制顺序",
    "shell": "证据指向外壳；优先检查岛外框、任务栏融合和遮罩区域更新",
    "lyric_timeline": "证据指向歌词跟进；优先检查媒体位置决策、歌词 offset 和 line-level timeline",
    "reactive_input": "证据指向律动输入；优先检查音频采集、频段缓存和 fallback 输入是否有效",
}


def _invalid_report(
    key: str,
    summary: str,
    *,
    source_schema_version: int | None = None,
    generated_at: str = "",
    app_name: str = "",
    app_version: str = "",
    next_action: str,
) -> DiagnosticsEvidenceReport:
    return DiagnosticsEvidenceReport(
        source_kind="invalid",
        source_schema_version=source_schema_version,
        generated_at=generated_at,
        app_name=app_name,
        app_version=app_version,
        status="invalid",
        ready=False,
        summary=summary,
        next_action=next_action,
        issues=(DiagnosticsEvidenceIssue(key=key, severity="error", summary=summary),),
    )


def _looks_like_diagnostics(payload: Mapping[str, Any]) -> bool:
    return any(key in payload for key in _DIAGNOSTIC_KEYS)


def _optional_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
