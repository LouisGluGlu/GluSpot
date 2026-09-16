from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from local.diagnostics_evidence import DiagnosticsEvidenceReport, load_diagnostics_evidence_file
from local.runtime_experiment import (
    RuntimeExperimentComparison,
    RuntimeExperimentPlan,
    build_runtime_experiment_plan,
    compare_runtime_experiment,
    load_runtime_experiment_file,
)
from local.runtime_remediation import RuntimeRemediationAction, runtime_remediation_actions


@dataclass(frozen=True)
class DiagnosticsSupportFocus:
    area: str
    focus: str
    files: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "area": self.area,
            "focus": self.focus,
            "files": list(self.files),
            "tests": list(self.tests),
        }


@dataclass(frozen=True)
class DiagnosticsSupportGuide:
    report: Mapping[str, Any]
    focus_items: tuple[DiagnosticsSupportFocus, ...]
    remediation_actions: tuple[RuntimeRemediationAction, ...]
    exit_code: int
    experiment_plan: RuntimeExperimentPlan | None = None
    experiment: RuntimeExperimentComparison | None = None

    def as_dict(self) -> dict[str, object]:
        data = {
            "exit_code": int(self.exit_code),
            "report": dict(self.report),
            "focus": [item.as_dict() for item in self.focus_items],
            "remediation": [item.as_dict() for item in self.remediation_actions],
        }
        if self.experiment_plan is not None:
            data["experiment_plan"] = self.experiment_plan.as_dict()
        if self.experiment is not None:
            data["experiment"] = self.experiment.as_dict()
        return data


def diagnostics_evidence_exit_code(report: Mapping[str, Any] | DiagnosticsEvidenceReport) -> int:
    data = _report_dict(report)
    if bool(data.get("ready")) and str(data.get("status") or "") == "ready":
        return 0
    if str(data.get("status") or "") == "invalid":
        return 2
    return 1


def build_diagnostics_support_guide(
    report: Mapping[str, Any] | DiagnosticsEvidenceReport,
    *,
    experiment: RuntimeExperimentComparison | None = None,
) -> DiagnosticsSupportGuide:
    data = dict(_report_dict(report))
    return DiagnosticsSupportGuide(
        report=data,
        focus_items=diagnostics_support_focus_items(data),
        remediation_actions=runtime_remediation_actions(data),
        exit_code=diagnostics_evidence_exit_code(data),
        experiment_plan=build_runtime_experiment_plan(data),
        experiment=experiment,
    )


def diagnostics_support_focus_items(
    report: Mapping[str, Any] | DiagnosticsEvidenceReport,
) -> tuple[DiagnosticsSupportFocus, ...]:
    data = _report_dict(report)
    areas = _unique_area_keys(
        str(data.get("dominant_area") or ""),
        str(data.get("bottleneck_group") or ""),
    )
    items: list[DiagnosticsSupportFocus] = []
    for area in areas:
        focus = _AREA_GUIDANCE.get(area)
        if focus is not None:
            items.append(focus)
    if items:
        return tuple(items)
    if str(data.get("status") or "") == "invalid":
        return (
            DiagnosticsSupportFocus(
                area="diagnostics_export",
                focus="diagnostics export structure; re-export before code changes",
            ),
        )
    return (
        DiagnosticsSupportFocus(
            area="runtime_evidence",
            focus="runtime evidence capture; keep the island visible and export again before code changes",
        ),
    )


def diagnostics_support_focus(report: Mapping[str, Any] | DiagnosticsEvidenceReport) -> tuple[str, ...]:
    lines: list[str] = []
    for item in diagnostics_support_focus_items(report):
        lines.append(f"focus: {item.focus}")
        if item.files:
            lines.append(f"files: {', '.join(item.files)}")
        if item.tests:
            lines.append(f"tests: {', '.join(item.tests)}")
    return tuple(lines)


def format_diagnostics_support_report(report: Mapping[str, Any] | DiagnosticsEvidenceReport) -> str:
    data = _report_dict(report)
    issues = data.get("issues")
    lines = [
        "GluSpot runtime diagnostics evidence",
        f"status: {data.get('status') or 'missing'}",
        f"ready: {'yes' if data.get('ready') else 'no'}",
        f"source: {_source_summary(data)}",
        f"app: {_app_summary(data)}",
        f"health: {data.get('health_status') or 'missing'}",
        f"runtime: {int(_float(data.get('trace_sample_count')))} samples / {_float(data.get('trace_window_seconds')):.1f}s",
        f"visual: {_visual_summary(data)}",
        f"dominant_area: {data.get('dominant_area') or '-'}",
        f"bottleneck_group: {data.get('bottleneck_group') or '-'}",
        f"summary: {data.get('summary') or '-'}",
        f"next_action: {data.get('next_action') or '-'}",
    ]
    lines.extend(diagnostics_support_focus(data))
    for action in runtime_remediation_actions(data):
        lines.append(f"remediation: P{action.priority} {action.area} - {action.summary}")
        if action.setting_keys:
            lines.append(f"settings: {', '.join(action.setting_keys)}")
        if action.verify:
            lines.append(f"verify: {action.verify}")
    plan = build_runtime_experiment_plan(data)
    lines.append(f"experiment_plan: {plan.key} - {plan.summary}")
    if plan.setting_changes:
        changes = ", ".join(f"{key}={value}" for key, value in plan.setting_changes.items())
        lines.append(f"experiment_settings: {changes}")
    lines.append(f"experiment_capture: {plan.capture_seconds}s before/after")
    lines.append(f"experiment_compare: {plan.compare_command}")
    if isinstance(issues, Sequence) and not isinstance(issues, (str, bytes)):
        for issue in issues:
            if not isinstance(issue, Mapping):
                continue
            lines.append(
                "issue: "
                f"{issue.get('severity') or 'attention'} "
                f"{issue.get('key') or 'runtime_evidence'} - "
                f"{issue.get('summary') or '运行证据不完整'}"
            )
    return "\n".join(lines) + "\n"


def format_runtime_experiment_comparison(comparison: RuntimeExperimentComparison) -> str:
    data = comparison.as_dict()
    evidence = data["evidence"] if isinstance(data.get("evidence"), Mapping) else {}
    lines = [
        "GluSpot runtime experiment comparison",
        f"classification: {comparison.classification}",
        f"summary: {comparison.summary}",
        f"next_action: {comparison.next_action}",
        (
            "before: "
            f"{comparison.before.dominant_area or '-'} / {comparison.before.bottleneck_group or '-'} / "
            f"lag {comparison.before.sync_lag_lines:+d} / reactive "
            f"{comparison.before.reactive_budget_ratio:.2f}"
        ),
        (
            "after: "
            f"{comparison.after.dominant_area or '-'} / {comparison.after.bottleneck_group or '-'} / "
            f"lag {comparison.after.sync_lag_lines:+d} / reactive "
            f"{comparison.after.reactive_budget_ratio:.2f}"
        ),
        f"reactive_pressure_dropped: {'yes' if evidence.get('reactive_pressure_dropped') else 'no'}",
        f"reactive_intensity_delta: {evidence.get('reactive_intensity_delta', 0)}",
    ]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m local.diagnostics_support",
        description="Analyze an exported GluSpot runtime diagnostics JSON file.",
    )
    parser.add_argument("path", help="Path to a GluSpot runtime diagnostics JSON export.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the normalized diagnostics evidence report as JSON.",
    )
    parser.add_argument(
        "--guide-json",
        action="store_true",
        help="Print the diagnostics evidence report plus structured repair focus as JSON.",
    )
    parser.add_argument(
        "--compare-after",
        help="Compare the main export with a second post-remediation export.",
    )
    args = parser.parse_args(argv)
    out = stdout or sys.stdout
    report = load_diagnostics_evidence_file(Path(args.path))
    data = report.as_dict()
    experiment = None
    if args.compare_after:
        experiment = compare_runtime_experiment(
            load_runtime_experiment_file(Path(args.path)),
            load_runtime_experiment_file(Path(args.compare_after)),
        )
    if args.guide_json:
        out.write(
            json.dumps(
                build_diagnostics_support_guide(data, experiment=experiment).as_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    elif args.json:
        out.write(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        out.write(format_diagnostics_support_report(data))
        if experiment is not None:
            out.write(format_runtime_experiment_comparison(experiment))
    return diagnostics_evidence_exit_code(data)


def _report_dict(report: Mapping[str, Any] | DiagnosticsEvidenceReport) -> Mapping[str, Any]:
    if isinstance(report, DiagnosticsEvidenceReport):
        return report.as_dict()
    return report if isinstance(report, Mapping) else {}


def _unique_area_keys(*areas: str) -> tuple[str, ...]:
    result: list[str] = []
    for area in areas:
        key = str(area or "").strip()
        if key and key not in result:
            result.append(key)
    return tuple(result)


def _source_summary(data: Mapping[str, Any]) -> str:
    kind = str(data.get("source_kind") or "raw")
    schema = data.get("source_schema_version")
    generated_at = str(data.get("generated_at") or "").strip()
    parts = [kind]
    if schema is not None:
        parts.append(f"schema {schema}")
    if generated_at:
        parts.append(generated_at)
    return " / ".join(parts)


def _app_summary(data: Mapping[str, Any]) -> str:
    name = str(data.get("app_name") or "").strip()
    version = str(data.get("app_version") or "").strip()
    if name and version:
        return f"{name} {version}"
    return name or version or "-"


def _visual_summary(data: Mapping[str, Any]) -> str:
    ambience = str(data.get("ambience_style") or "-")
    lyric = str(data.get("lyric_flow_style") or "-")
    reactive = "reactive on" if data.get("reactive_background_enabled") else "reactive off"
    return f"{ambience} / {lyric} / {reactive}"


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


_AREA_GUIDANCE: dict[str, DiagnosticsSupportFocus] = {
    "frame_cadence": DiagnosticsSupportFocus(
        area="frame_cadence",
        focus="frame cadence / timer scheduling",
        files=(
            "local/island_presentation.py",
            "local/island_stack_visual.py",
            "local/visual_diagnostics.py",
        ),
        tests=(
            "tests/test_island_presentation.py",
            "tests/test_island_stack_visual_messages.py",
            "tests/test_visual_diagnostics.py",
        ),
    ),
    "paint": DiagnosticsSupportFocus(
        area="paint",
        focus="paint section costs",
        files=("local/visual_diagnostics.py", "local/island_stack_visual.py"),
        tests=("tests/test_visual_diagnostics.py", "tests/test_island_stack_visual_messages.py"),
    ),
    "background_surface": DiagnosticsSupportFocus(
        area="background_surface",
        focus="background surface rendering",
        files=(
            "local/music_background_renderer.py",
            "local/audio_reactive_engine.py",
            "local/island_presentation.py",
        ),
        tests=(
            "tests/test_music_background_renderer.py",
            "tests/test_audio_reactive_engine.py",
            "tests/test_island_presentation.py",
        ),
    ),
    "reactive_effects": DiagnosticsSupportFocus(
        area="reactive_effects",
        focus="music reactive rendering",
        files=(
            "local/audio_reactive_engine.py",
            "local/music_background_renderer.py",
            "local/visual_diagnostics.py",
        ),
        tests=(
            "tests/test_audio_reactive_engine.py",
            "tests/test_music_background_renderer.py",
            "tests/test_visual_diagnostics.py",
        ),
    ),
    "music_wave_cadence": DiagnosticsSupportFocus(
        area="music_wave_cadence",
        focus="music wave cadence versus render/lyric cadence",
        files=(
            "local/island_presentation.py",
            "local/island_stack_visual.py",
            "local/runtime_diagnostics.py",
            "local/audio_reactive_engine.py",
        ),
        tests=(
            "tests/test_island_presentation.py",
            "tests/test_island_stack_visual_messages.py",
            "tests/test_runtime_diagnostics.py",
            "tests/test_audio_reactive_engine.py",
        ),
    ),
    "runtime_contention": DiagnosticsSupportFocus(
        area="runtime_contention",
        focus="visual frame budget versus lyric timeline contention",
        files=(
            "local/runtime_diagnostics.py",
            "local/visual_diagnostics.py",
            "local/music_background_renderer.py",
            "api/lyric_timeline.py",
        ),
        tests=(
            "tests/test_runtime_diagnostics.py",
            "tests/test_visual_diagnostics.py",
            "tests/test_music_background_renderer.py",
            "tests/test_lyric_timeline.py",
        ),
    ),
    "content": DiagnosticsSupportFocus(
        area="content",
        focus="lyrics / album art / message overlay content",
        files=("api/lyric_timeline.py", "local/album_art_pipeline.py", "local/island_stack_visual.py"),
        tests=(
            "tests/test_lyric_timeline.py",
            "tests/test_album_art_pipeline.py",
            "tests/test_island_stack_visual_messages.py",
        ),
    ),
    "shell": DiagnosticsSupportFocus(
        area="shell",
        focus="island shell / taskbar fusion",
        files=("local/taskbar_positioning.py", "local/island_visual_stack.py", "local/island_stack_visual.py"),
        tests=(
            "tests/test_taskbar_positioning.py",
            "tests/test_island_visual_stack.py",
            "tests/test_island_stack_visual_messages.py",
        ),
    ),
    "lyric_timeline": DiagnosticsSupportFocus(
        area="lyric_timeline",
        focus="media position and lyric timeline",
        files=("api/lyric_timeline.py", "local/media_position_pipeline.py", "local/island_stack_visual.py"),
        tests=(
            "tests/test_lyric_timeline.py",
            "tests/test_media_position_pipeline.py",
            "tests/test_island_stack_visual_messages.py",
        ),
    ),
    "reactive_input": DiagnosticsSupportFocus(
        area="reactive_input",
        focus="live audio input and band cache",
        files=("api/audio_analyzer.py", "local/audio_reactive_engine.py"),
        tests=("tests/test_audio_reactive_engine.py",),
    ),
}


if __name__ == "__main__":
    raise SystemExit(main())
