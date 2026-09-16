from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from local.diagnostics_evidence import build_diagnostics_evidence_report


@dataclass(frozen=True)
class RuntimeExperimentMetrics:
    status: str = "missing"
    ready: bool = False
    dominant_area: str = ""
    bottleneck_group: str = ""
    reactive_background_enabled: bool = False
    reactive_background_intensity: int = 0
    trace_sample_count: int = 0
    trace_window_seconds: float = 0.0
    target_interval_ms: float = 0.0
    average_interval_ms: float = 0.0
    jitter_ms: float = 0.0
    max_interval_ms: float = 0.0
    frame_count: int = 0
    long_frame_count: int = 0
    long_frame_ratio: float = 0.0
    sync_lag_lines: int = 0
    reactive_last_ms: float = 0.0
    reactive_average_ms: float = 0.0
    reactive_budget_ratio: float = 0.0
    reactive_band_source: str = ""
    music_wave_status: str = "missing"
    music_wave_update_count: int = 0
    music_wave_estimated_update_fps: float = 0.0
    music_wave_render_average_fps: float = 0.0
    music_wave_to_render_fps_ratio: float = 0.0
    music_wave_cadence_limited: bool = False

    @property
    def frame_cadence_problem(self) -> bool:
        if self.dominant_area == "frame_cadence":
            return True
        if self.target_interval_ms <= 0.0:
            return False
        return (
            self.average_interval_ms > self.target_interval_ms * 1.4
            or self.max_interval_ms > self.target_interval_ms * 2.5
            or self.long_frame_ratio > 0.05
        )

    @property
    def reactive_pressure(self) -> bool:
        if self.bottleneck_group == "reactive_effects" or self.dominant_area == "reactive_effects":
            return True
        return self.reactive_budget_ratio > 0.40

    @property
    def lyric_lag_problem(self) -> bool:
        return abs(self.sync_lag_lines) >= 2 or self.dominant_area == "lyric_timeline"

    @property
    def reactive_input_problem(self) -> bool:
        return self.dominant_area == "reactive_input" or self.reactive_band_source in {"", "playing_fallback"}

    @property
    def music_wave_cadence_problem(self) -> bool:
        if self.dominant_area == "music_wave_cadence":
            return True
        if self.music_wave_status != "ready":
            return False
        return (
            self.music_wave_render_average_fps > 40.0
            and self.music_wave_estimated_update_fps > 35.0
            and self.music_wave_to_render_fps_ratio > 0.85
            and not self.music_wave_cadence_limited
        )

    @property
    def music_wave_cadence_healthy(self) -> bool:
        if self.music_wave_status != "ready":
            return False
        return (
            24.0 <= self.music_wave_estimated_update_fps <= 35.0
            and self.music_wave_to_render_fps_ratio < 0.85
            and self.music_wave_cadence_limited
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "ready": bool(self.ready),
            "dominant_area": self.dominant_area,
            "bottleneck_group": self.bottleneck_group,
            "reactive_background_enabled": bool(self.reactive_background_enabled),
            "reactive_background_intensity": int(self.reactive_background_intensity),
            "trace_sample_count": int(self.trace_sample_count),
            "trace_window_seconds": round(float(self.trace_window_seconds), 3),
            "target_interval_ms": round(float(self.target_interval_ms), 3),
            "average_interval_ms": round(float(self.average_interval_ms), 3),
            "jitter_ms": round(float(self.jitter_ms), 3),
            "max_interval_ms": round(float(self.max_interval_ms), 3),
            "frame_count": int(self.frame_count),
            "long_frame_count": int(self.long_frame_count),
            "long_frame_ratio": round(float(self.long_frame_ratio), 4),
            "sync_lag_lines": int(self.sync_lag_lines),
            "reactive_last_ms": round(float(self.reactive_last_ms), 3),
            "reactive_average_ms": round(float(self.reactive_average_ms), 3),
            "reactive_budget_ratio": round(float(self.reactive_budget_ratio), 4),
            "reactive_band_source": self.reactive_band_source,
            "music_wave_status": self.music_wave_status,
            "music_wave_update_count": int(self.music_wave_update_count),
            "music_wave_estimated_update_fps": round(float(self.music_wave_estimated_update_fps), 3),
            "music_wave_render_average_fps": round(float(self.music_wave_render_average_fps), 3),
            "music_wave_to_render_fps_ratio": round(float(self.music_wave_to_render_fps_ratio), 4),
            "music_wave_cadence_limited": bool(self.music_wave_cadence_limited),
        }


@dataclass(frozen=True)
class RuntimeExperimentComparison:
    classification: str
    summary: str
    next_action: str
    before: RuntimeExperimentMetrics
    after: RuntimeExperimentMetrics
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "classification": self.classification,
            "summary": self.summary,
            "next_action": self.next_action,
            "before": self.before.as_dict(),
            "after": self.after.as_dict(),
            "evidence": dict(self.evidence or {}),
        }


@dataclass(frozen=True)
class RuntimeExperimentPlan:
    key: str
    summary: str
    trigger_area: str = ""
    setting_changes: Mapping[str, object] = field(default_factory=dict)
    capture_seconds: int = 5
    before_capture: str = "Export diagnostics before changing settings."
    after_capture: str = "Export diagnostics after the setting change while playing the same track."
    compare_command: str = "python -m local.diagnostics_support <before.json> --compare-after <after.json> --guide-json"
    success_criteria: tuple[str, ...] = ()
    failure_criteria: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "summary": self.summary,
            "trigger_area": self.trigger_area,
            "setting_changes": dict(self.setting_changes or {}),
            "capture_seconds": int(self.capture_seconds),
            "before_capture": self.before_capture,
            "after_capture": self.after_capture,
            "compare_command": self.compare_command,
            "success_criteria": list(self.success_criteria),
            "failure_criteria": list(self.failure_criteria),
        }


def load_runtime_experiment_file(path: str | Path) -> RuntimeExperimentMetrics:
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return RuntimeExperimentMetrics(status="invalid", ready=False)
    return build_runtime_experiment_metrics(payload)


def build_runtime_experiment_metrics(payload: Mapping[str, Any] | None) -> RuntimeExperimentMetrics:
    data = payload if isinstance(payload, Mapping) else {}
    if _looks_like_evidence_report(data) and not _diagnostics_payload(data):
        return _metrics_from_evidence_report(data)
    report = build_diagnostics_evidence_report(data).as_dict()
    diagnostics = _diagnostics_payload(data)
    frame = _mapping(diagnostics.get("visual_frame_diagnostics"))
    paint = _mapping(diagnostics.get("visual_paint_section_diagnostics"))
    lyric = _mapping(diagnostics.get("lyric_timeline_diagnostics"))
    reactive = _mapping(diagnostics.get("music_reactive_input_diagnostics"))
    wave = _mapping(diagnostics.get("music_wave_cadence_diagnostics"))
    context = _mapping(diagnostics.get("visual_runtime_context"))
    trace = _mapping(diagnostics.get("runtime_diagnostics_trace"))
    snapshot = _mapping(diagnostics.get("runtime_diagnostics_snapshot"))
    groups = _mapping(paint.get("groups"))
    reactive_group = _mapping(groups.get("reactive_effects"))

    target_ms = _float(frame.get("target_interval_ms"))
    frame_count = int(_float(frame.get("frame_count")))
    long_frames = int(_float(frame.get("long_frame_count")))
    reactive_last_ms = _float(reactive_group.get("last_ms", reactive_group.get("average_ms")))
    reactive_avg_ms = _float(reactive_group.get("average_ms"))
    lag = int(round(_first_number(lyric.get("sync_lag_lines"), _signal_evidence(snapshot, "lyric_timeline", "sync_lag_lines"))))
    band_source = str(reactive.get("band_source") or _signal_evidence(snapshot, "reactive_input", "band_source") or "")

    return RuntimeExperimentMetrics(
        status=str(report.get("status") or "missing"),
        ready=bool(report.get("ready")),
        dominant_area=str(report.get("dominant_area") or trace.get("dominant_area") or snapshot.get("dominant_area") or ""),
        bottleneck_group=str(
            report.get("bottleneck_group") or trace.get("bottleneck_group") or snapshot.get("bottleneck_group") or ""
        ),
        reactive_background_enabled=bool(
            report.get("reactive_background_enabled", context.get("reactive_background_enabled", False))
        ),
        reactive_background_intensity=int(_float(context.get("reactive_background_intensity"))),
        trace_sample_count=int(_float(report.get("trace_sample_count", trace.get("sample_count")))),
        trace_window_seconds=_float(report.get("trace_window_seconds", trace.get("window_seconds"))),
        target_interval_ms=target_ms,
        average_interval_ms=_float(frame.get("average_interval_ms")),
        jitter_ms=_float(frame.get("jitter_ms")),
        max_interval_ms=_float(frame.get("max_interval_ms")),
        frame_count=frame_count,
        long_frame_count=long_frames,
        long_frame_ratio=(long_frames / frame_count) if frame_count > 0 else 0.0,
        sync_lag_lines=lag,
        reactive_last_ms=reactive_last_ms,
        reactive_average_ms=reactive_avg_ms,
        reactive_budget_ratio=(reactive_last_ms / target_ms) if target_ms > 0.0 else 0.0,
        reactive_band_source=band_source,
        music_wave_status=str(wave.get("status") or "missing"),
        music_wave_update_count=int(_float(wave.get("update_count"))),
        music_wave_estimated_update_fps=_float(wave.get("estimated_update_fps")),
        music_wave_render_average_fps=_float(wave.get("render_average_fps")),
        music_wave_to_render_fps_ratio=_float(wave.get("wave_to_render_fps_ratio")),
        music_wave_cadence_limited=bool(wave.get("cadence_limited")),
    )


def build_runtime_experiment_plan(
    report: Mapping[str, Any] | RuntimeExperimentMetrics | None,
) -> RuntimeExperimentPlan:
    metrics = report if isinstance(report, RuntimeExperimentMetrics) else build_runtime_experiment_metrics(report)
    if not metrics.ready:
        return RuntimeExperimentPlan(
            key="capture_ready_baseline",
            summary="capture a ready diagnostics baseline before changing visual or lyric settings",
            trigger_area=metrics.dominant_area,
            setting_changes={},
            success_criteria=(
                "before export status is ready",
                "trace_sample_count >= 3",
                "trace_window_seconds >= 2.0",
            ),
            failure_criteria=(
                "status remains partial or invalid",
                "runtime trace has fewer than 3 samples",
            ),
        )
    if metrics.dominant_area == "runtime_contention" or metrics.bottleneck_group == "reactive_effects":
        return RuntimeExperimentPlan(
            key="reactive_contention_ablation",
            summary="lower music reactive intensity first to separate renderer budget pressure from lyric timeline faults",
            trigger_area=metrics.dominant_area or metrics.bottleneck_group,
            setting_changes={"v2_music_reactive_background_intensity": 35},
            success_criteria=(
                "comparison classification is reactive_budget_confirmed",
                "after dominant_area is not runtime_contention",
                "after sync_lag_lines is 0 or +/-1",
                "after reactive_budget_ratio <= 0.40 or drops by at least 30%",
            ),
            failure_criteria=(
                "classification is lyric_timeline_remaining",
                "classification is frame_scheduler_remaining",
                "classification is reactive_input_remaining",
            ),
        )
    if metrics.dominant_area == "lyric_timeline":
        return RuntimeExperimentPlan(
            key="lyric_timeline_control",
            summary="keep reactive settings stable and verify media position versus line-level lyric timeline",
            trigger_area=metrics.dominant_area,
            setting_changes={},
            success_criteria=(
                "visible_lyric_index and position_active_index differ by no more than 1 line",
                "sync_lag_lines returns to 0 or +/-1 with the same track and offset",
            ),
            failure_criteria=(
                "sync_lag_lines remains >= 2 while reactive_budget_ratio is low",
                "media position reason changes during the same playback window",
            ),
        )
    if metrics.dominant_area == "frame_cadence":
        return RuntimeExperimentPlan(
            key="frame_scheduler_control",
            summary="hold lyric timing stable and test whether visual frame cadence improves under a lower visual FPS budget",
            trigger_area=metrics.dominant_area,
            setting_changes={"v2_animation_fps": 45},
            success_criteria=(
                "average_interval_ms approaches the new target interval",
                "long_frame_ratio drops below 0.05",
                "comparison classification is not frame_scheduler_remaining",
            ),
            failure_criteria=(
                "classification remains frame_scheduler_remaining",
                "long_frame_count stays high after the FPS control change",
            ),
        )
    if metrics.dominant_area == "music_wave_cadence":
        return RuntimeExperimentPlan(
            key="music_wave_cadence_control",
            summary="keep lyric and visual settings stable and verify music wave updates stay cadence-limited near 30fps",
            trigger_area=metrics.dominant_area,
            setting_changes={},
            success_criteria=(
                "comparison classification is music_wave_cadence_confirmed",
                "after estimated_update_fps is near 30",
                "after cadence_limited is true",
                "after wave_to_render_fps_ratio < 0.85",
            ),
            failure_criteria=(
                "classification is music_wave_cadence_remaining",
                "estimated_update_fps remains close to render_average_fps",
                "cadence_limited remains false while render fps is above 40",
            ),
        )
    if metrics.dominant_area == "reactive_input":
        return RuntimeExperimentPlan(
            key="reactive_input_control",
            summary="stabilize live/cache audio bands before judging rhythm visuals",
            trigger_area=metrics.dominant_area,
            setting_changes={},
            success_criteria=(
                "band_source is live or cache",
                "sampled_bands vary while music is playing",
            ),
            failure_criteria=(
                "band_source remains playing_fallback",
                "live audio is unavailable while reactive background is enabled",
            ),
        )
    return RuntimeExperimentPlan(
        key="repeat_same_track_capture",
        summary="repeat a same-track diagnostics capture before changing architecture or renderer code",
        trigger_area=metrics.dominant_area or metrics.bottleneck_group,
        setting_changes={},
        success_criteria=(
            "two ready exports agree on dominant_area or show no remaining bottleneck",
            "trace window covers at least 2 seconds in both exports",
        ),
        failure_criteria=(
            "dominant_area changes without a setting change",
            "comparison classification is no_material_change",
        ),
    )


def compare_runtime_experiment(
    before: Mapping[str, Any] | RuntimeExperimentMetrics | None,
    after: Mapping[str, Any] | RuntimeExperimentMetrics | None,
) -> RuntimeExperimentComparison:
    before_metrics = before if isinstance(before, RuntimeExperimentMetrics) else build_runtime_experiment_metrics(before)
    after_metrics = after if isinstance(after, RuntimeExperimentMetrics) else build_runtime_experiment_metrics(after)
    evidence = _comparison_evidence(before_metrics, after_metrics)

    if not before_metrics.ready or not after_metrics.ready:
        return RuntimeExperimentComparison(
            classification="insufficient_evidence",
            summary="before and after exports must both be ready before attributing runtime regressions",
            next_action="Capture 3-5 seconds of visible playback for both states, then compare the exports again.",
            before=before_metrics,
            after=after_metrics,
            evidence=evidence,
        )

    pressure_dropped = bool(evidence["reactive_pressure_dropped"])
    contention_cleared = (
        before_metrics.dominant_area in {"runtime_contention", "reactive_effects"}
        and after_metrics.dominant_area != "runtime_contention"
        and abs(after_metrics.sync_lag_lines) <= 1
        and not after_metrics.frame_cadence_problem
    )
    if pressure_dropped and contention_cleared:
        return RuntimeExperimentComparison(
            classification="reactive_budget_confirmed",
            summary="lower reactive load removed the contention signature",
            next_action=(
                "Keep lyric FPS stable and continue optimizing the reactive renderer or style-specific motion plans "
                "before increasing intensity again."
            ),
            before=before_metrics,
            after=after_metrics,
            evidence=evidence,
        )
    if pressure_dropped and after_metrics.lyric_lag_problem:
        return RuntimeExperimentComparison(
            classification="lyric_timeline_remaining",
            summary="reactive paint pressure dropped but lyric lag remains",
            next_action=(
                "Inspect media position selection and the line-level lyric timeline; do not treat this as a "
                "background renderer problem first."
            ),
            before=before_metrics,
            after=after_metrics,
            evidence=evidence,
        )
    if after_metrics.frame_cadence_problem:
        return RuntimeExperimentComparison(
            classification="frame_scheduler_remaining",
            summary="after export still shows poor frame cadence",
            next_action="Inspect animation timers, repaint frequency, and long-frame sources before changing lyric timing.",
            before=before_metrics,
            after=after_metrics,
            evidence=evidence,
        )
    if before_metrics.music_wave_cadence_problem and after_metrics.music_wave_cadence_healthy:
        return RuntimeExperimentComparison(
            classification="music_wave_cadence_confirmed",
            summary="music wave updates are now cadence-limited instead of tracking render fps",
            next_action=(
                "Keep this cadence seam stable, then tune style-specific motion plans without coupling "
                "wave sampling back to lyric or render ticks."
            ),
            before=before_metrics,
            after=after_metrics,
            evidence=evidence,
        )
    if after_metrics.music_wave_cadence_problem:
        return RuntimeExperimentComparison(
            classification="music_wave_cadence_remaining",
            summary="music wave updates are still too close to render fps",
            next_action=(
                "Inspect the wave update gate in island_presentation/island_stack_visual before changing "
                "style-specific renderer code."
            ),
            before=before_metrics,
            after=after_metrics,
            evidence=evidence,
        )
    if after_metrics.reactive_input_problem and after_metrics.reactive_background_enabled:
        return RuntimeExperimentComparison(
            classification="reactive_input_remaining",
            summary="reactive rendering is still driven by missing or fallback input",
            next_action="Stabilize live/cache audio bands before judging style-specific visual motion.",
            before=before_metrics,
            after=after_metrics,
            evidence=evidence,
        )
    return RuntimeExperimentComparison(
        classification="no_material_change",
        summary="the comparison does not prove a specific remaining bottleneck",
        next_action="Repeat the experiment with one changed setting at a time and compare again.",
        before=before_metrics,
        after=after_metrics,
        evidence=evidence,
    )


def _comparison_evidence(
    before: RuntimeExperimentMetrics,
    after: RuntimeExperimentMetrics,
) -> dict[str, object]:
    pressure_delta = before.reactive_budget_ratio - after.reactive_budget_ratio
    last_ms_delta = before.reactive_last_ms - after.reactive_last_ms
    intensity_delta = before.reactive_background_intensity - after.reactive_background_intensity
    wave_ratio_delta = before.music_wave_to_render_fps_ratio - after.music_wave_to_render_fps_ratio
    wave_fps_delta = before.music_wave_estimated_update_fps - after.music_wave_estimated_update_fps
    pressure_dropped = (
        after.reactive_budget_ratio <= 0.40
        or (before.reactive_budget_ratio > 0.0 and after.reactive_budget_ratio <= before.reactive_budget_ratio * 0.70)
        or pressure_delta >= 0.20
        or (before.reactive_last_ms > 0.0 and after.reactive_last_ms <= before.reactive_last_ms * 0.70)
    )
    return {
        "reactive_pressure_dropped": bool(pressure_dropped),
        "reactive_budget_ratio_delta": round(float(pressure_delta), 4),
        "reactive_last_ms_delta": round(float(last_ms_delta), 3),
        "reactive_intensity_delta": int(intensity_delta),
        "lyric_lag_delta": int(abs(before.sync_lag_lines) - abs(after.sync_lag_lines)),
        "frame_average_ms_delta": round(float(before.average_interval_ms - after.average_interval_ms), 3),
        "after_frame_cadence_problem": bool(after.frame_cadence_problem),
        "after_lyric_lag_problem": bool(after.lyric_lag_problem),
        "after_reactive_input_problem": bool(after.reactive_input_problem),
        "music_wave_ratio_delta": round(float(wave_ratio_delta), 4),
        "music_wave_fps_delta": round(float(wave_fps_delta), 3),
        "before_music_wave_estimated_update_fps": round(float(before.music_wave_estimated_update_fps), 3),
        "after_music_wave_estimated_update_fps": round(float(after.music_wave_estimated_update_fps), 3),
        "before_music_wave_to_render_fps_ratio": round(float(before.music_wave_to_render_fps_ratio), 4),
        "after_music_wave_to_render_fps_ratio": round(float(after.music_wave_to_render_fps_ratio), 4),
        "after_music_wave_cadence_limited": bool(after.music_wave_cadence_limited),
        "after_music_wave_cadence_problem": bool(after.music_wave_cadence_problem),
    }


def _diagnostics_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, Mapping):
        return diagnostics
    if _looks_like_diagnostics(payload):
        return payload
    return {}


def _metrics_from_evidence_report(report: Mapping[str, Any]) -> RuntimeExperimentMetrics:
    return RuntimeExperimentMetrics(
        status=str(report.get("status") or "missing"),
        ready=bool(report.get("ready")),
        dominant_area=str(report.get("dominant_area") or ""),
        bottleneck_group=str(report.get("bottleneck_group") or ""),
        reactive_background_enabled=bool(report.get("reactive_background_enabled")),
        trace_sample_count=int(_float(report.get("trace_sample_count"))),
        trace_window_seconds=_float(report.get("trace_window_seconds")),
    )


def _looks_like_evidence_report(payload: Mapping[str, Any]) -> bool:
    return any(
        key in payload
        for key in (
            "source_kind",
            "ready",
            "dominant_area",
            "bottleneck_group",
            "trace_sample_count",
            "trace_window_seconds",
        )
    )


def _looks_like_diagnostics(payload: Mapping[str, Any]) -> bool:
    return any(
        key in payload
        for key in (
            "runtime_diagnostics_snapshot",
            "runtime_diagnostics_trace",
            "visual_runtime_context",
            "visual_frame_diagnostics",
            "visual_paint_section_diagnostics",
            "lyric_timeline_diagnostics",
            "music_reactive_input_diagnostics",
        )
    )


def _signal_evidence(snapshot: Mapping[str, Any], key: str, evidence_key: str) -> object:
    signals = snapshot.get("signals")
    if not isinstance(signals, list):
        return None
    for item in signals:
        if not isinstance(item, Mapping) or item.get("key") != key:
            continue
        evidence = _mapping(item.get("evidence"))
        return evidence.get(evidence_key)
    return None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_number(*values: object) -> float:
    for value in values:
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
