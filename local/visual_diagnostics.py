from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class VisualPaintSectionSampleSummary:
    recorded_count: int = 0
    sample_count: int = 0
    average_ms: float = 0.0
    max_ms: float = 0.0
    last_ms: float = 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "recorded_count": self.recorded_count,
            "sample_count": self.sample_count,
            "average_ms": round(self.average_ms, 3),
            "max_ms": round(self.max_ms, 3),
            "last_ms": round(self.last_ms, 3),
        }


@dataclass(frozen=True)
class VisualPaintSectionGroupSummary:
    section_count: int = 0
    recorded_count: int = 0
    sample_count: int = 0
    average_ms: float = 0.0
    max_ms: float = 0.0
    last_ms: float = 0.0

    def as_dict(self) -> dict[str, float | int]:
        return {
            "section_count": self.section_count,
            "recorded_count": self.recorded_count,
            "sample_count": self.sample_count,
            "average_ms": round(self.average_ms, 3),
            "max_ms": round(self.max_ms, 3),
            "last_ms": round(self.last_ms, 3),
        }


@dataclass(frozen=True)
class VisualPaintSectionDiagnosticsSummary:
    section_count: int = 0
    total_recorded_count: int = 0
    total_sample_count: int = 0
    last_section: str = ""
    sections: dict[str, VisualPaintSectionSampleSummary] | None = None
    groups: dict[str, VisualPaintSectionGroupSummary] | None = None

    def as_dict(self) -> dict[str, object]:
        sections = self.sections or {}
        groups = self.groups or {}
        return {
            "section_count": self.section_count,
            "total_recorded_count": self.total_recorded_count,
            "total_sample_count": self.total_sample_count,
            "last_section": self.last_section,
            "sections": {name: summary.as_dict() for name, summary in sorted(sections.items())},
            "groups": {name: summary.as_dict() for name, summary in sorted(groups.items())},
        }


@dataclass(frozen=True)
class VisualFrameDiagnosticsSummary:
    frame_count: int = 0
    sample_count: int = 0
    average_interval_ms: float = 0.0
    average_fps: float = 0.0
    max_interval_ms: float = 0.0
    jitter_ms: float = 0.0
    long_frame_count: int = 0
    target_interval_ms: float = 0.0
    last_interval_ms: float = 0.0
    last_reason: str = ""

    def as_dict(self) -> dict[str, float | int | str]:
        return {
            "frame_count": self.frame_count,
            "sample_count": self.sample_count,
            "average_interval_ms": round(self.average_interval_ms, 3),
            "average_fps": round(self.average_fps, 2),
            "max_interval_ms": round(self.max_interval_ms, 3),
            "jitter_ms": round(self.jitter_ms, 3),
            "long_frame_count": self.long_frame_count,
            "target_interval_ms": round(self.target_interval_ms, 3),
            "last_interval_ms": round(self.last_interval_ms, 3),
            "last_reason": self.last_reason,
        }


class VisualFrameDiagnosticsRecorder:
    def __init__(self, *, capacity: int = 180, long_frame_factor: float = 1.65):
        self.capacity = max(4, int(capacity))
        self.long_frame_factor = max(1.0, float(long_frame_factor))
        self._last_frame_at: float | None = None
        self._intervals: deque[float] = deque(maxlen=self.capacity)
        self._targets: deque[float] = deque(maxlen=self.capacity)
        self._frame_count = 0
        self._long_frame_count = 0
        self._last_reason = ""
        self._section_durations: dict[str, deque[float]] = {}
        self._section_record_counts: dict[str, int] = {}
        self._last_section = ""

    def record_frame(
        self,
        now: float,
        *,
        target_interval_seconds: float | None = None,
        reason: str = "",
    ) -> VisualFrameDiagnosticsSummary:
        now_value = float(now)
        target = self._safe_positive_float(target_interval_seconds)
        self._frame_count += 1
        self._last_reason = str(reason or "")
        previous = self._last_frame_at
        self._last_frame_at = now_value
        if previous is not None:
            interval = max(0.0, now_value - previous)
            self._intervals.append(interval)
            if target > 0.0:
                self._targets.append(target)
                if interval > target * self.long_frame_factor:
                    self._long_frame_count += 1
        elif target > 0.0:
            self._targets.append(target)
        return self.summary()

    def summary(self) -> VisualFrameDiagnosticsSummary:
        intervals = list(self._intervals)
        targets = list(self._targets)
        if not intervals:
            target = targets[-1] if targets else 0.0
            return VisualFrameDiagnosticsSummary(
                frame_count=self._frame_count,
                sample_count=0,
                target_interval_ms=target * 1000.0,
                last_reason=self._last_reason,
            )

        average = sum(intervals) / len(intervals)
        variance = sum((item - average) ** 2 for item in intervals) / len(intervals)
        target = targets[-1] if targets else 0.0
        return VisualFrameDiagnosticsSummary(
            frame_count=self._frame_count,
            sample_count=len(intervals),
            average_interval_ms=average * 1000.0,
            average_fps=(1.0 / average) if average > 0.0 else 0.0,
            max_interval_ms=max(intervals) * 1000.0,
            jitter_ms=(variance ** 0.5) * 1000.0,
            long_frame_count=self._long_frame_count,
            target_interval_ms=target * 1000.0,
            last_interval_ms=intervals[-1] * 1000.0,
            last_reason=self._last_reason,
        )

    def record_section(self, section: str, duration_seconds: float) -> VisualPaintSectionDiagnosticsSummary:
        name = self._section_name(section)
        duration = self._safe_positive_float(duration_seconds)
        samples = self._section_durations.get(name)
        if samples is None:
            samples = deque(maxlen=self.capacity)
            self._section_durations[name] = samples
        samples.append(duration)
        self._section_record_counts[name] = self._section_record_counts.get(name, 0) + 1
        self._last_section = name
        return self.section_summary()

    def section_summary(self) -> VisualPaintSectionDiagnosticsSummary:
        sections: dict[str, VisualPaintSectionSampleSummary] = {}
        total_sample_count = 0
        total_recorded_count = 0
        for name, samples_deque in self._section_durations.items():
            samples = list(samples_deque)
            recorded_count = self._section_record_counts.get(name, len(samples))
            total_sample_count += len(samples)
            total_recorded_count += recorded_count
            if not samples:
                sections[name] = VisualPaintSectionSampleSummary(recorded_count=recorded_count)
                continue
            average = sum(samples) / len(samples)
            sections[name] = VisualPaintSectionSampleSummary(
                recorded_count=recorded_count,
                sample_count=len(samples),
                average_ms=average * 1000.0,
                max_ms=max(samples) * 1000.0,
                last_ms=samples[-1] * 1000.0,
            )
        return VisualPaintSectionDiagnosticsSummary(
            section_count=len(sections),
            total_recorded_count=total_recorded_count,
            total_sample_count=total_sample_count,
            last_section=self._last_section,
            sections=sections,
            groups=visual_paint_section_group_summaries(sections),
        )

    @staticmethod
    def _safe_positive_float(value: object) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, number)

    @staticmethod
    def _section_name(value: object) -> str:
        name = str(value or "").strip()
        if not name:
            return "unknown"
        return name[:96]


def visual_paint_section_group(section: object) -> str:
    name = str(section or "").strip().lower()
    if name.startswith("music."):
        name = name[6:]
    if name in {"total", "music_total"}:
        return "total"
    if name in {
        "background",
        "dual_panel_background",
        "pure_white_background",
        "solid_album_background",
        "yaoguang_expanded_background",
        "yaoguang_compact_background",
        "pure_black_background",
        "bottom_ambience",
        "bilateral_diffraction_ambience",
    }:
        return "background_surface"
    if name in {"light_wave", "reactive_overlay", "surface_transition"}:
        return "reactive_effects"
    if name in {"compact_content", "expanded_content", "lyrics", "content"}:
        return "content"
    if name in {"glass_body", "blur_mask", "border", "yaoguang_compact_frame_overlay", "shell"}:
        return "shell"
    return "other"


def visual_paint_section_group_summaries(
    sections: dict[str, VisualPaintSectionSampleSummary] | None,
) -> dict[str, VisualPaintSectionGroupSummary]:
    buckets: dict[str, list[VisualPaintSectionSampleSummary]] = {}
    for name, summary in (sections or {}).items():
        buckets.setdefault(visual_paint_section_group(name), []).append(summary)
    groups: dict[str, VisualPaintSectionGroupSummary] = {}
    for group, items in buckets.items():
        groups[group] = VisualPaintSectionGroupSummary(
            section_count=len(items),
            recorded_count=sum(item.recorded_count for item in items),
            sample_count=sum(item.sample_count for item in items),
            average_ms=sum(item.average_ms for item in items),
            max_ms=max((item.max_ms for item in items), default=0.0),
            last_ms=sum(item.last_ms for item in items),
        )
    return groups
