from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MediaPositionUpdatePlan:
    position_seconds: float
    raw_position_seconds: float
    position_source: str
    position_confidence: float
    position_interactive: bool
    source_foreground: bool
    frontstage_position: bool
    refresh_interval_seconds: float
    listened_delta_seconds: float = 0.0
    reason: str = "raw"


def media_position_update_plan(
    *,
    track_changed: bool,
    incoming_is_playing: bool,
    incoming_position_seconds: object,
    incoming_position_source: object,
    incoming_position_confidence: object,
    incoming_position_interactive: object,
    incoming_source_foreground: object,
    previous_position_seconds: object,
    previous_sync_at: object,
    previous_raw_position_seconds: object,
    previous_raw_position_source: object,
    now: object,
) -> MediaPositionUpdatePlan:
    raw_position = max(0.0, _float(incoming_position_seconds, 0.0))
    source = str(incoming_position_source or "windows-media") or "windows-media"
    confidence = max(0.0, min(1.0, _float(incoming_position_confidence, 1.0)))
    interactive = bool(incoming_position_interactive)
    foreground = bool(incoming_source_foreground)
    frontstage = bool(foreground or interactive or source == "interactive")
    previous_position = max(0.0, _float(previous_position_seconds, 0.0))
    previous_sync = _float(previous_sync_at, _float(now, 0.0))
    now_value = _float(now, previous_sync)
    elapsed_since_sync = max(0.0, now_value - previous_sync)
    previous_raw = _optional_float(previous_raw_position_seconds)
    previous_source = str(previous_raw_position_source or "")
    same_position_source = previous_source == source
    raw_delta = None
    if previous_raw is not None and same_position_source:
        raw_delta = raw_position - previous_raw

    position = raw_position
    reason = "raw"
    trusted_external_position = confidence >= 0.75 and (source != "windows-media" or frontstage)
    repeated_stale_position = (
        not bool(track_changed)
        and bool(incoming_is_playing)
        and previous_raw is not None
        and same_position_source
        and raw_position > 0.0
        and abs(previous_raw - raw_position) <= 0.08
        and previous_position > raw_position + 0.35
    )
    stale_windows_fallback_position = (
        not bool(track_changed)
        and bool(incoming_is_playing)
        and source == "windows-media"
        and previous_source not in ("", "windows-media")
        and raw_position > 0.0
        and previous_position > raw_position + 0.35
    )
    seek_like_position = (
        not bool(track_changed)
        and bool(incoming_is_playing)
        and previous_raw is not None
        and same_position_source
        and raw_position > 0.0
        and raw_delta is not None
        and (
            raw_delta <= -0.35
            or abs(raw_position - previous_position) >= 2.8
            or raw_delta >= max(1.6, elapsed_since_sync + 0.9)
        )
    )

    if not bool(track_changed) and bool(incoming_is_playing) and frontstage:
        if raw_position > 0.0 or looks_like_seek_to_start(raw_position, previous_position, previous_raw):
            position = raw_position
            reason = "frontstage_raw"
        elif previous_position > 0.0:
            position = previous_position
            reason = "frontstage_preserve_previous"
    elif not bool(track_changed) and bool(incoming_is_playing) and (trusted_external_position or seek_like_position):
        if repeated_stale_position:
            position = previous_position
            reason = "trusted_repeated_stale_preserve_previous"
        else:
            position = raw_position
            reason = "trusted_or_seek_raw"
    elif not bool(track_changed) and bool(incoming_is_playing):
        if repeated_stale_position or stale_windows_fallback_position:
            position = previous_position
            reason = "stale_preserve_previous"
        elif position <= 0.0 and previous_position > 0.5:
            position = previous_position
            reason = "zero_preserve_previous"
        elif 0.0 < previous_position - position <= 2.5:
            position = previous_position
            reason = "small_backward_preserve_previous"
        elif position == 0.0 and previous_position > 0.0:
            position = previous_position
            reason = "zero_raw_preserve_previous"
        else:
            drift = position - previous_position
            if abs(drift) <= 0.95:
                position = previous_position + drift * 0.24
                reason = "small_drift_eased"

    listened_delta = 0.0
    if not bool(track_changed) and bool(incoming_is_playing):
        listened_delta = min(max(0.0, position - previous_position), elapsed_since_sync + 1.0)

    return MediaPositionUpdatePlan(
        position_seconds=max(0.0, position),
        raw_position_seconds=raw_position,
        position_source=source,
        position_confidence=confidence,
        position_interactive=interactive,
        source_foreground=foreground,
        frontstage_position=frontstage,
        refresh_interval_seconds=0.18 if frontstage else 0.65,
        listened_delta_seconds=max(0.0, listened_delta),
        reason=reason,
    )


def looks_like_seek_to_start(raw_position: object, previous_position: object, previous_raw_position: object) -> bool:
    previous_raw = _optional_float(previous_raw_position)
    if previous_raw is None:
        return False
    raw = max(0.0, _float(raw_position, 0.0))
    previous = max(0.0, _float(previous_position, 0.0))
    previous_raw = max(0.0, previous_raw)
    if raw > 0.12 or previous <= 3.0 or previous_raw <= 3.0:
        return False
    return abs(previous_raw - previous) <= max(5.0, previous * 0.12)


def _float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
