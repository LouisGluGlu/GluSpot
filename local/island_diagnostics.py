from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from local.runtime_diagnostics import build_runtime_diagnostics_snapshot


@dataclass(frozen=True)
class VisibleMediaDiagnostics:
    title: str = ""
    artist: str = ""
    track_id: str = ""
    source_app: str = ""
    position_source: str = ""
    position_reason: str = ""
    position_confidence: float = 0.0
    position_interactive: bool = False
    source_foreground: bool = False
    mydockfinder_running: bool = False
    mydockfinder_diagnostics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def position_frontstage(self) -> bool:
        return bool(
            self.source_foreground
            or self.position_interactive
            or str(self.position_source or "") == "interactive"
        )


@dataclass(frozen=True)
class VisualRuntimeDiagnostics:
    ambience_style: str = "sea_level"
    effective_ambience_style: str = "sea_level"
    lyric_flow_style: str = "wave_stream"
    expanded: bool = False
    location_mode: str = "free"
    taskbar_compact_active: bool = False
    reactive_background_enabled: bool = False
    reactive_background_intensity: int = 55
    animation_fps: int = 60
    lyric_animation_fps: int = 60
    animation_speed: float = 1.0
    compact_duotone_balance: int = 50
    expanded_duotone_balance: int = 50
    diffraction_frequency: float = 0.30
    yaoguang_color_source: str = "custom"
    yaoguang_album_inverse_enabled: bool = False
    yaoguang_blur_strength: int = 58
    yaoguang_height_mode: str = "regular"

    def as_dict(self) -> dict[str, object]:
        return {
            "ambience_style": self.ambience_style,
            "effective_ambience_style": self.effective_ambience_style,
            "lyric_flow_style": self.lyric_flow_style,
            "expanded": bool(self.expanded),
            "location_mode": self.location_mode,
            "taskbar_compact_active": bool(self.taskbar_compact_active),
            "reactive_background_enabled": bool(self.reactive_background_enabled),
            "reactive_background_intensity": int(self.reactive_background_intensity),
            "animation_fps": int(self.animation_fps),
            "lyric_animation_fps": int(self.lyric_animation_fps),
            "animation_speed": round(float(self.animation_speed), 3),
            "compact_duotone_balance": int(self.compact_duotone_balance),
            "expanded_duotone_balance": int(self.expanded_duotone_balance),
            "diffraction_frequency": round(float(self.diffraction_frequency), 3),
            "yaoguang_color_source": self.yaoguang_color_source,
            "yaoguang_album_inverse_enabled": bool(self.yaoguang_album_inverse_enabled),
            "yaoguang_blur_strength": int(self.yaoguang_blur_strength),
            "yaoguang_height_mode": self.yaoguang_height_mode,
        }


def build_island_provider_diagnostics(
    *,
    provider_diagnostics: Mapping[str, Any] | None = None,
    visible: VisibleMediaDiagnostics | None = None,
    visual_context: VisualRuntimeDiagnostics | Mapping[str, Any] | None = None,
    lyric_diagnostics: Mapping[str, Any] | None = None,
    visual_frame_diagnostics: Mapping[str, Any] | None = None,
    visual_paint_section_diagnostics: Mapping[str, Any] | None = None,
    music_reactive_input_diagnostics: Mapping[str, Any] | None = None,
    music_wave_cadence_diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = dict(provider_diagnostics or {})
    visible_data = visible or VisibleMediaDiagnostics()
    lyric = _mapping(lyric_diagnostics)
    result.update(
        {
            "visible_title": visible_data.title,
            "visible_artist": visible_data.artist,
            "visible_track_id": visible_data.track_id,
            "visible_source_app": visible_data.source_app,
            "estimated_position_seconds": lyric.get("estimated_position_seconds", 0.0),
            "visible_position_source": visible_data.position_source,
            "visible_position_reason": visible_data.position_reason,
            "visible_position_confidence": visible_data.position_confidence,
            "visible_position_interactive": bool(visible_data.position_interactive),
            "visible_position_frontstage": visible_data.position_frontstage,
            "mydockfinder_running": bool(visible_data.mydockfinder_running),
            "mydockfinder_diagnostics": dict(visible_data.mydockfinder_diagnostics or {}),
            "visual_runtime_context": _visual_context_dict(visual_context),
            "lyric_offset_seconds": lyric.get("lyric_offset_seconds", 0.0),
            "visible_lyric_index": lyric.get("visible_lyric_index", 0),
            "visible_current_lyric": lyric.get("visible_current_lyric", ""),
            "visible_lyrics_count": lyric.get("visible_lyrics_count", 0),
            "lyric_phase_progress": lyric.get("phase_progress", 0.0),
            "lyric_phase_span": lyric.get("phase_span", 0.0),
            "lyric_phase_source": lyric.get("phase_source", ""),
            "lyric_timeline_diagnostics": dict(lyric),
            "visual_frame_diagnostics": dict(visual_frame_diagnostics or {}),
            "visual_paint_section_diagnostics": dict(visual_paint_section_diagnostics or {}),
            "music_reactive_input_diagnostics": dict(music_reactive_input_diagnostics or {}),
            "music_wave_cadence_diagnostics": dict(music_wave_cadence_diagnostics or {}),
        }
    )
    result["runtime_diagnostics_snapshot"] = build_runtime_diagnostics_snapshot(result).as_dict()
    return result


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _visual_context_dict(value: object) -> dict[str, object]:
    if isinstance(value, VisualRuntimeDiagnostics):
        return value.as_dict()
    return dict(value) if isinstance(value, Mapping) else VisualRuntimeDiagnostics().as_dict()
