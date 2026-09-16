from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RuntimeDiagnosticSignal:
    key: str
    area: str
    status: str = "missing"
    severity: int = 0
    summary: str = ""
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "area": self.area,
            "status": self.status,
            "severity": self.severity,
            "summary": self.summary,
            "evidence": dict(self.evidence or {}),
        }


@dataclass(frozen=True)
class RuntimeDiagnosticsSnapshot:
    status: str = "missing"
    headline: str = "no-runtime-evidence"
    dominant_area: str = ""
    bottleneck_group: str = ""
    bottleneck_summary: str = ""
    signals: tuple[RuntimeDiagnosticSignal, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "headline": self.headline,
            "dominant_area": self.dominant_area,
            "bottleneck_group": self.bottleneck_group,
            "bottleneck_summary": self.bottleneck_summary,
            "signals": [signal.as_dict() for signal in self.signals],
        }


@dataclass(frozen=True)
class RuntimeDiagnosticsTraceSample:
    sampled_at: float
    status: str = "missing"
    dominant_area: str = ""
    bottleneck_group: str = ""
    headline: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "sampled_at": round(float(self.sampled_at), 3),
            "status": self.status,
            "dominant_area": self.dominant_area,
            "bottleneck_group": self.bottleneck_group,
            "headline": self.headline,
        }


@dataclass(frozen=True)
class RuntimeDiagnosticsTraceSummary:
    recorded_count: int = 0
    sample_count: int = 0
    first_sample_at: float = 0.0
    last_sample_at: float = 0.0
    window_seconds: float = 0.0
    status_counts: Mapping[str, int] = field(default_factory=dict)
    dominant_area_counts: Mapping[str, int] = field(default_factory=dict)
    bottleneck_group_counts: Mapping[str, int] = field(default_factory=dict)
    dominant_area: str = ""
    dominant_area_count: int = 0
    bottleneck_group: str = ""
    bottleneck_group_count: int = 0
    warning_count: int = 0
    attention_count: int = 0
    latest: RuntimeDiagnosticsTraceSample | None = None
    samples: tuple[RuntimeDiagnosticsTraceSample, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "recorded_count": self.recorded_count,
            "sample_count": self.sample_count,
            "first_sample_at": round(float(self.first_sample_at), 3),
            "last_sample_at": round(float(self.last_sample_at), 3),
            "window_seconds": round(float(self.window_seconds), 3),
            "status_counts": dict(sorted((self.status_counts or {}).items())),
            "dominant_area_counts": dict(sorted((self.dominant_area_counts or {}).items())),
            "bottleneck_group_counts": dict(sorted((self.bottleneck_group_counts or {}).items())),
            "dominant_area": self.dominant_area,
            "dominant_area_count": self.dominant_area_count,
            "bottleneck_group": self.bottleneck_group,
            "bottleneck_group_count": self.bottleneck_group_count,
            "warning_count": self.warning_count,
            "attention_count": self.attention_count,
            "latest": self.latest.as_dict() if self.latest is not None else {},
            "samples": [sample.as_dict() for sample in self.samples],
        }


class RuntimeDiagnosticsTraceRecorder:
    def __init__(self, *, capacity: int = 60):
        self.capacity = max(2, int(capacity))
        self._samples: deque[RuntimeDiagnosticsTraceSample] = deque(maxlen=self.capacity)
        self._recorded_count = 0

    def record(
        self,
        now: float,
        snapshot: Mapping[str, Any] | RuntimeDiagnosticsSnapshot | None,
    ) -> RuntimeDiagnosticsTraceSummary:
        data = snapshot.as_dict() if isinstance(snapshot, RuntimeDiagnosticsSnapshot) else _mapping(snapshot)
        sample = RuntimeDiagnosticsTraceSample(
            sampled_at=float(now),
            status=str(data.get("status") or "missing"),
            dominant_area=str(data.get("dominant_area") or ""),
            bottleneck_group=str(data.get("bottleneck_group") or ""),
            headline=str(data.get("headline") or ""),
        )
        self._samples.append(sample)
        self._recorded_count += 1
        return self.summary()

    def summary(self) -> RuntimeDiagnosticsTraceSummary:
        samples = tuple(self._samples)
        if not samples:
            return RuntimeDiagnosticsTraceSummary(recorded_count=self._recorded_count)

        status_counts = _count_values(sample.status for sample in samples)
        area_counts = _count_values(sample.dominant_area for sample in samples if sample.dominant_area)
        bottleneck_counts = _count_values(sample.bottleneck_group for sample in samples if sample.bottleneck_group)
        dominant_area, dominant_area_count = _top_count(area_counts)
        bottleneck_group, bottleneck_group_count = _top_count(bottleneck_counts)
        first = samples[0]
        latest = samples[-1]
        return RuntimeDiagnosticsTraceSummary(
            recorded_count=self._recorded_count,
            sample_count=len(samples),
            first_sample_at=first.sampled_at,
            last_sample_at=latest.sampled_at,
            window_seconds=max(0.0, latest.sampled_at - first.sampled_at),
            status_counts=status_counts,
            dominant_area_counts=area_counts,
            bottleneck_group_counts=bottleneck_counts,
            dominant_area=dominant_area,
            dominant_area_count=dominant_area_count,
            bottleneck_group=bottleneck_group,
            bottleneck_group_count=bottleneck_group_count,
            warning_count=status_counts.get("warning", 0),
            attention_count=status_counts.get("attention", 0),
            latest=latest,
            samples=samples,
        )


def build_runtime_diagnostics_snapshot(
    diagnostics: Mapping[str, Any] | None,
) -> RuntimeDiagnosticsSnapshot:
    data = diagnostics if isinstance(diagnostics, Mapping) else {}
    frame = _mapping(data.get("visual_frame_diagnostics"))
    paint = _mapping(data.get("visual_paint_section_diagnostics"))
    lyric = _mapping(data.get("lyric_timeline_diagnostics"))
    reactive = _mapping(data.get("music_reactive_input_diagnostics"))
    wave = _mapping(data.get("music_wave_cadence_diagnostics"))

    signals = (
        _frame_cadence_signal(frame),
        _paint_pressure_signal(paint, frame),
        _lyric_timeline_signal(lyric),
        _reactive_input_signal(reactive),
        _music_wave_cadence_signal(wave),
        _runtime_contention_signal(frame, paint, lyric, reactive),
    )
    dominant = max(signals, key=lambda item: (item.severity, item.key))
    if dominant.severity <= 0:
        status = "ok" if any(signal.status == "ok" for signal in signals) else "missing"
        headline = "runtime-diagnostics-ok" if status == "ok" else "no-runtime-evidence"
        dominant_area = ""
    else:
        status = dominant.status
        headline = dominant.summary or dominant.key
        dominant_area = dominant.area

    paint_signal = next((signal for signal in signals if signal.key == "paint_pressure"), None)
    bottleneck_group = ""
    bottleneck_summary = ""
    if paint_signal is not None:
        bottleneck_group = str(paint_signal.evidence.get("top_group") or "")
        bottleneck_summary = paint_signal.summary

    return RuntimeDiagnosticsSnapshot(
        status=status,
        headline=headline,
        dominant_area=dominant_area,
        bottleneck_group=bottleneck_group,
        bottleneck_summary=bottleneck_summary,
        signals=signals,
    )


def _frame_cadence_signal(frame: Mapping[str, Any]) -> RuntimeDiagnosticSignal:
    if not frame:
        return RuntimeDiagnosticSignal(
            key="frame_cadence",
            area="frame_cadence",
            summary="frame diagnostics missing",
        )
    frame_count = _float(frame.get("frame_count"))
    sample_count = _float(frame.get("sample_count"))
    target_ms = _float(frame.get("target_interval_ms"))
    avg_ms = _float(frame.get("average_interval_ms"))
    jitter_ms = _float(frame.get("jitter_ms"))
    max_ms = _float(frame.get("max_interval_ms"))
    long_frames = _float(frame.get("long_frame_count"))
    long_ratio = (long_frames / frame_count) if frame_count > 0.0 else 0.0
    evidence = {
        "frame_count": int(frame_count),
        "sample_count": int(sample_count),
        "target_interval_ms": round(target_ms, 3),
        "average_interval_ms": round(avg_ms, 3),
        "jitter_ms": round(jitter_ms, 3),
        "max_interval_ms": round(max_ms, 3),
        "long_frame_count": int(long_frames),
        "long_frame_ratio": round(long_ratio, 4),
    }
    summary = (
        f"frame avg {avg_ms:.1f}ms target {target_ms:.1f}ms "
        f"jitter {jitter_ms:.1f}ms long {int(long_frames)}/{int(frame_count)}"
    )
    if sample_count <= 0:
        return RuntimeDiagnosticSignal(
            key="frame_cadence",
            area="frame_cadence",
            status="missing",
            summary="frame samples not ready",
            evidence=evidence,
        )
    if target_ms > 0.0 and (avg_ms > target_ms * 1.4 or max_ms > target_ms * 2.5 or long_ratio > 0.05):
        return RuntimeDiagnosticSignal(
            key="frame_cadence",
            area="frame_cadence",
            status="warning",
            severity=3,
            summary=summary,
            evidence=evidence,
        )
    if target_ms > 0.0 and jitter_ms > target_ms * 0.5:
        return RuntimeDiagnosticSignal(
            key="frame_cadence",
            area="frame_cadence",
            status="attention",
            severity=2,
            summary=summary,
            evidence=evidence,
        )
    return RuntimeDiagnosticSignal(
        key="frame_cadence",
        area="frame_cadence",
        status="ok",
        summary=summary,
        evidence=evidence,
    )


def _paint_pressure_signal(
    paint: Mapping[str, Any],
    frame: Mapping[str, Any],
) -> RuntimeDiagnosticSignal:
    groups = _mapping(paint.get("groups"))
    if not groups:
        return RuntimeDiagnosticSignal(
            key="paint_pressure",
            area="paint",
            summary="paint group diagnostics missing",
        )
    ranked: list[tuple[float, str, Mapping[str, Any]]] = []
    for name, value in groups.items():
        group = str(name or "")
        if group == "total":
            continue
        item = _mapping(value)
        ranked.append((_float(item.get("last_ms", item.get("average_ms"))), group, item))
    if not ranked:
        return RuntimeDiagnosticSignal(
            key="paint_pressure",
            area="paint",
            summary="paint group diagnostics empty",
        )
    ranked.sort(key=lambda item: item[0], reverse=True)
    last_ms, group, item = ranked[0]
    avg_ms = _float(item.get("average_ms"))
    target_ms = _float(frame.get("target_interval_ms"))
    evidence = {
        "top_group": group,
        "top_group_last_ms": round(last_ms, 3),
        "top_group_average_ms": round(avg_ms, 3),
        "target_interval_ms": round(target_ms, 3),
        "group_count": len(ranked),
    }
    summary = f"paint top {group} last {last_ms:.1f}ms avg {avg_ms:.1f}ms target {target_ms:.1f}ms"
    if target_ms > 0.0 and last_ms > target_ms * 0.75:
        return RuntimeDiagnosticSignal(
            key="paint_pressure",
            area=group,
            status="warning",
            severity=3,
            summary=summary,
            evidence=evidence,
        )
    if target_ms > 0.0 and last_ms > target_ms * 0.4:
        return RuntimeDiagnosticSignal(
            key="paint_pressure",
            area=group,
            status="attention",
            severity=2,
            summary=summary,
            evidence=evidence,
        )
    return RuntimeDiagnosticSignal(
        key="paint_pressure",
        area=group,
        status="ok",
        summary=summary,
        evidence=evidence,
    )


def _lyric_timeline_signal(lyric: Mapping[str, Any]) -> RuntimeDiagnosticSignal:
    if not lyric:
        return RuntimeDiagnosticSignal(
            key="lyric_timeline",
            area="lyric_timeline",
            summary="lyric timeline diagnostics missing",
        )
    lag = int(round(_float(lyric.get("sync_lag_lines"))))
    visible = int(round(_float(lyric.get("visible_lyric_index"))))
    position = int(round(_float(lyric.get("position_active_index"))))
    source = str(lyric.get("phase_source") or "")
    evidence = {
        "sync_lag_lines": lag,
        "visible_lyric_index": visible,
        "position_active_index": position,
        "phase_source": source,
        "timed_lyrics_available": bool(lyric.get("timed_lyrics_available", True)),
    }
    summary = f"lyric lag {lag:+d} lines visible {visible} position {position} source {source or '-'}"
    severity = 0
    status = "ok"
    if abs(lag) >= 2:
        severity = 3
        status = "warning"
    elif abs(lag) >= 1:
        severity = 2
        status = "attention"
    return RuntimeDiagnosticSignal(
        key="lyric_timeline",
        area="lyric_timeline",
        status=status,
        severity=severity,
        summary=summary,
        evidence=evidence,
    )


def _reactive_input_signal(reactive: Mapping[str, Any]) -> RuntimeDiagnosticSignal:
    if not reactive:
        return RuntimeDiagnosticSignal(
            key="reactive_input",
            area="reactive_input",
            summary="reactive input diagnostics missing",
        )
    bands = _number_sequence(reactive.get("sampled_bands"), limit=3)
    band_source = str(reactive.get("band_source") or "")
    live_audio_available = bool(reactive.get("live_audio_available"))
    reactive_enabled = bool(reactive.get("reactive_enabled", True))
    intensity = _float(reactive.get("intensity"))
    evidence = {
        "reactive_enabled": reactive_enabled,
        "live_audio_available": live_audio_available,
        "band_source": band_source,
        "intensity": round(intensity, 3),
        "sampled_bands": tuple(round(item, 3) for item in bands),
        "sample_age_ms": reactive.get("sample_age_ms"),
    }
    band_text = "/".join(f"{item:.2f}" for item in bands) if bands else "0.00/0.00/0.00"
    summary = (
        f"reactive {'enabled' if reactive_enabled else 'disabled'} "
        f"{'live' if live_audio_available else 'fallback'} {band_source or '-'} "
        f"intensity {intensity:.2f} bands {band_text}"
    )
    if reactive_enabled and (not live_audio_available or band_source in {"", "playing_fallback"}):
        return RuntimeDiagnosticSignal(
            key="reactive_input",
            area="reactive_input",
            status="attention",
            severity=2,
            summary=summary,
            evidence=evidence,
        )
    return RuntimeDiagnosticSignal(
        key="reactive_input",
        area="reactive_input",
        status="ok",
        summary=summary,
        evidence=evidence,
    )


def _music_wave_cadence_signal(wave: Mapping[str, Any]) -> RuntimeDiagnosticSignal:
    if not wave:
        return RuntimeDiagnosticSignal(
            key="music_wave_cadence",
            area="music_wave_cadence",
            summary="music wave cadence diagnostics missing",
        )
    status_value = str(wave.get("status") or "missing")
    update_count = int(round(_float(wave.get("update_count"))))
    estimated_fps = _float(wave.get("estimated_update_fps"))
    render_fps = _float(wave.get("render_average_fps"))
    ratio = _float(wave.get("wave_to_render_fps_ratio"))
    age_ms = wave.get("update_age_ms")
    cadence_limited = bool(wave.get("cadence_limited"))
    evidence = {
        "status": status_value,
        "update_count": update_count,
        "estimated_update_fps": round(estimated_fps, 3),
        "render_average_fps": round(render_fps, 3),
        "wave_to_render_fps_ratio": round(ratio, 3),
        "cadence_limited": cadence_limited,
        "update_age_ms": age_ms,
    }
    summary = (
        f"music wave {estimated_fps:.1f}fps render {render_fps:.1f}fps "
        f"ratio {ratio:.2f} updates {update_count}"
    )
    if status_value != "ready":
        return RuntimeDiagnosticSignal(
            key="music_wave_cadence",
            area="music_wave_cadence",
            summary="music wave cadence samples not ready",
            evidence=evidence,
        )
    if render_fps > 40.0 and estimated_fps > 35.0 and ratio > 0.85:
        return RuntimeDiagnosticSignal(
            key="music_wave_cadence",
            area="music_wave_cadence",
            status="attention",
            severity=2,
            summary=summary,
            evidence=evidence,
        )
    return RuntimeDiagnosticSignal(
        key="music_wave_cadence",
        area="music_wave_cadence",
        status="ok",
        summary=summary,
        evidence=evidence,
    )


def _runtime_contention_signal(
    frame: Mapping[str, Any],
    paint: Mapping[str, Any],
    lyric: Mapping[str, Any],
    reactive: Mapping[str, Any],
) -> RuntimeDiagnosticSignal:
    groups = _mapping(paint.get("groups"))
    reactive_group = _mapping(groups.get("reactive_effects"))
    content_group = _mapping(groups.get("content"))
    target_ms = _float(frame.get("target_interval_ms"))
    reactive_last_ms = _float(reactive_group.get("last_ms", reactive_group.get("average_ms")))
    reactive_avg_ms = _float(reactive_group.get("average_ms"))
    content_last_ms = _float(content_group.get("last_ms", content_group.get("average_ms")))
    lag = int(round(_float(lyric.get("sync_lag_lines"))))
    enabled = bool(reactive.get("reactive_enabled", True))
    reactive_budget_ratio = (reactive_last_ms / target_ms) if target_ms > 0.0 else 0.0
    evidence = {
        "reactive_enabled": enabled,
        "sync_lag_lines": lag,
        "target_interval_ms": round(target_ms, 3),
        "reactive_last_ms": round(reactive_last_ms, 3),
        "reactive_average_ms": round(reactive_avg_ms, 3),
        "reactive_budget_ratio": round(reactive_budget_ratio, 4),
        "content_last_ms": round(content_last_ms, 3),
    }
    if not frame or not lyric or not groups:
        return RuntimeDiagnosticSignal(
            key="runtime_contention",
            area="runtime_contention",
            summary="runtime contention evidence missing",
            evidence=evidence,
        )
    summary = (
        f"runtime contention lyric lag {lag:+d} lines reactive "
        f"{reactive_last_ms:.1f}ms/{target_ms:.1f}ms content {content_last_ms:.1f}ms"
    )
    if enabled and abs(lag) >= 1 and target_ms > 0.0 and reactive_last_ms > target_ms * 0.40:
        return RuntimeDiagnosticSignal(
            key="runtime_contention",
            area="runtime_contention",
            status="warning" if abs(lag) >= 2 or reactive_last_ms > target_ms * 0.75 else "attention",
            severity=4 if abs(lag) >= 2 or reactive_last_ms > target_ms * 0.75 else 3,
            summary=summary,
            evidence=evidence,
        )
    return RuntimeDiagnosticSignal(
        key="runtime_contention",
        area="runtime_contention",
        status="ok",
        summary=summary,
        evidence=evidence,
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number_sequence(value: object, *, limit: int) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return ()
    numbers: list[float] = []
    for item in value:
        numbers.append(_float(item))
        if len(numbers) >= limit:
            break
    return tuple(numbers)


def _count_values(values: Iterable[object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "").strip()
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def _top_count(counts: Mapping[str, int]) -> tuple[str, int]:
    if not counts:
        return "", 0
    key, count = max(counts.items(), key=lambda item: (item[1], item[0]))
    return key, int(count)


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
