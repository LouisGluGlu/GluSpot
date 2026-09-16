from __future__ import annotations

from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import dataclass
import math
import time


@dataclass(frozen=True)
class AudioBandSamplePlan:
    bands: tuple[float, float, float]
    source: str
    cache_bands: tuple[float, float, float] | None = None
    cache_sampled_at: float | None = None
    live_audio_available: bool | None = None
    live_audio_checked_at: float | None = None


@dataclass(frozen=True)
class AudioBandSamplingRequestPlan:
    cached_bands: tuple[float, float, float] | None = None
    request_live_frame: bool = False
    reactive_enabled: bool = False
    source: str = "sample"


@dataclass(frozen=True)
class LiveAudioAvailabilityRequestPlan:
    cached_available: bool = False
    request_probe: bool = False
    source: str = "probe"


@dataclass(frozen=True)
class AudioReactiveInputSnapshot:
    sampled_bands: tuple[float, float, float] = (0.0, 0.0, 0.0)
    live_audio_available: bool = False
    band_source: str = ""
    live_audio_source: str = ""


@dataclass(frozen=True)
class AudioReactiveInputDiagnostics:
    reactive_enabled: bool = False
    intensity: float = 0.0
    has_snapshot: bool = False
    sampled_bands: tuple[float, float, float] = (0.0, 0.0, 0.0)
    band_source: str = ""
    live_audio_available: bool = False
    live_audio_source: str = ""
    cache_has_bands: bool = False
    sampled_at: float | None = None
    snapshot_at: float | None = None
    live_audio_checked_at: float | None = None
    sample_age_ms: float | None = None
    live_audio_check_age_ms: float | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "reactive_enabled": self.reactive_enabled,
            "intensity": self.intensity,
            "has_snapshot": self.has_snapshot,
            "sampled_bands": self.sampled_bands,
            "band_source": self.band_source,
            "live_audio_available": self.live_audio_available,
            "live_audio_source": self.live_audio_source,
            "cache_has_bands": self.cache_has_bands,
            "sampled_at": self.sampled_at,
            "snapshot_at": self.snapshot_at,
            "live_audio_checked_at": self.live_audio_checked_at,
            "sample_age_ms": self.sample_age_ms,
            "live_audio_check_age_ms": self.live_audio_check_age_ms,
        }


@dataclass(frozen=True)
class AudioReactiveStylePlan:
    ambience_style: str
    motion_family: str
    primary_axis: str
    preserves_lyric_center: bool
    response: dict[str, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "ambience_style", str(self.ambience_style or ""))
        object.__setattr__(self, "motion_family", str(self.motion_family or ""))
        object.__setattr__(self, "primary_axis", str(self.primary_axis or ""))
        object.__setattr__(self, "response", {str(key): float(value) for key, value in self.response.items()})

    def as_response(self) -> dict[str, float]:
        return dict(self.response)


@dataclass(frozen=True)
class ReactiveFrame:
    enabled: bool = False
    intensity: float = 0.0
    low: float = 0.0
    mid: float = 0.0
    high: float = 0.0
    energy: float = 0.0
    bass: float = 0.0
    shimmer: float = 0.0
    phase: float = 0.0
    pulse: float = 0.0
    sequence: int = 0
    beat: float = 0.0
    glow: float = 0.0
    lift: float = 0.0

    def as_dict(self) -> dict[str, float | bool]:
        return {
            "enabled": self.enabled,
            "intensity": self.intensity,
            "low": self.low,
            "mid": self.mid,
            "high": self.high,
            "energy": self.energy,
            "bass": self.bass,
            "shimmer": self.shimmer,
            "phase": self.phase,
            "pulse": self.pulse,
            "beat": self.beat,
            "glow": self.glow,
            "lift": self.lift,
        }


@dataclass(frozen=True)
class WaveAdvance:
    values: tuple[float, ...]
    targets: tuple[float, ...]
    frame: ReactiveFrame
    updated_at: float


@dataclass(frozen=True)
class ReactiveSnapshot:
    frame: ReactiveFrame
    style_responses: dict[str, dict[str, float]]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "style_responses",
            {str(style): dict(response) for style, response in self.style_responses.items()},
        )

    def state(self) -> dict[str, float | bool]:
        return self.frame.as_dict()

    def style_response(self, ambience_style: str) -> dict[str, float]:
        return dict(self.style_responses.get(str(ambience_style or ""), {}))


class AudioReactiveEngine:
    def __init__(self, enabled: bool = False, intensity_percent: float = 55.0):
        self.enabled = bool(enabled)
        self.intensity_percent = 55.0
        self.frame_cache = ReactiveFrame()
        self.frame_updated_at = -999.0
        self.frame_sequence = 0
        self.style_response_cache: dict[tuple[str, int], dict[str, float]] = {}
        self.configure(enabled=enabled, intensity_percent=intensity_percent)

    def configure(self, enabled: bool, intensity_percent: float) -> None:
        previous_enabled = self.enabled
        previous_intensity = self.intensity_percent
        self.enabled = bool(enabled)
        try:
            self.intensity_percent = max(0.0, min(100.0, float(intensity_percent)))
        except (TypeError, ValueError):
            self.intensity_percent = 55.0
        if previous_enabled != self.enabled or abs(previous_intensity - self.intensity_percent) > 0.001:
            self.style_response_cache = {}

    @property
    def intensity(self) -> float:
        return max(0.0, min(1.0, self.intensity_percent / 100.0))

    def read_frame(self) -> ReactiveFrame:
        if not self.enabled or self.intensity <= 0.0:
            self.frame_cache = ReactiveFrame()
            self.style_response_cache = {}
            return self.frame_cache
        cached = self.frame_cache
        if cached.enabled and abs(cached.intensity - self.intensity) <= 0.001:
            return cached
        return ReactiveFrame()

    def advance(self, bands: tuple[float, float, float], now: float | None = None) -> ReactiveFrame:
        zero = ReactiveFrame()
        now = time.monotonic() if now is None else float(now)
        if not self.enabled:
            self.frame_cache = zero
            self.frame_updated_at = now
            self.style_response_cache = {}
            return zero
        intensity = self.intensity
        if intensity <= 0.0:
            self.frame_cache = zero
            self.frame_updated_at = now
            self.style_response_cache = {}
            return zero

        previous = self.frame_cache
        previous_at = float(self.frame_updated_at or -999.0)
        low, mid, high = self._scaled_bands(bands, intensity)
        energy = max(0.0, min(1.0, low * 0.54 + mid * 0.32 + high * 0.18))
        bass = max(0.0, min(1.0, low * 0.72 + max(0.0, low - mid) * 0.18 + high * 0.05))
        shimmer = max(0.0, min(1.0, high * 0.52 + mid * 0.34 + energy * 0.18))
        target = ReactiveFrame(True, intensity, low, mid, high, energy, bass, shimmer, 0.0)
        if not previous.enabled or previous_at < 0.0:
            frame = ReactiveFrame(True, intensity, low, mid, high, energy, bass, shimmer, 0.0, 0.0, self._next_sequence())
            self._store_frame(frame, now)
            return frame

        dt = max(0.0, min(0.20, now - previous_at))
        next_low = self._follow(previous.low, target.low, dt)
        next_mid = self._follow(previous.mid, target.mid, dt)
        next_high = self._follow(previous.high, target.high, dt)
        next_energy = self._follow(previous.energy, target.energy, dt)
        next_bass = self._follow(previous.bass, target.bass, dt)
        next_shimmer = self._follow(previous.shimmer, target.shimmer, dt)
        bass_onset = max(0.0, target.bass - previous.bass)
        energy_onset = max(0.0, target.energy - previous.energy)
        shimmer_onset = max(0.0, target.shimmer - previous.shimmer)
        onset = max(0.0, min(1.0, bass_onset * 1.44 + energy_onset * 0.62 + shimmer_onset * 0.18))
        pulse_decay = math.pow(0.20, dt) if dt > 0.0 else 1.0
        next_pulse = max(previous.pulse * pulse_decay, onset)
        next_beat, next_glow, next_lift = self._motion_drive(previous, target, dt, onset)
        phase = previous.phase + dt * (0.68 + next_energy * 1.10 + next_shimmer * 0.42)
        frame = ReactiveFrame(
            True,
            intensity,
            next_low,
            next_mid,
            next_high,
            next_energy,
            next_bass,
            next_shimmer,
            phase,
            next_pulse,
            self._next_sequence(),
            next_beat,
            next_glow,
            next_lift,
        )
        self._store_frame(frame, now)
        return frame

    def state(self) -> dict[str, float | bool]:
        return self.read_frame().as_dict()

    def advance_wave_values(
        self,
        wave_values: list[float] | tuple[float, ...],
        bands: tuple[float, float, float],
        ambient_phase: float,
        now: float | None = None,
        previous_at: float | None = None,
    ) -> WaveAdvance:
        now = time.monotonic() if now is None else float(now)
        frame = self.advance(bands, now)
        low_band, mid_band, high_band = bands
        if frame.enabled:
            low_band = frame.low
            mid_band = frame.mid
            high_band = frame.high

        previous = now - (1.0 / 60.0) if previous_at is None else float(previous_at)
        dt = max(0.0, min(0.12, now - previous))
        values = list(wave_values or [])
        if not values:
            values = [0.0] * 36
        next_values: list[float] = []
        next_targets: list[float] = []
        for index, value in enumerate(values):
            pos = index / max(1, len(values) - 1)
            low_shape = max(0.0, 1.0 - abs(pos - 0.14) / 0.28)
            mid_shape = max(0.0, 1.0 - abs(pos - 0.50) / 0.24)
            high_shape = max(0.0, 1.0 - abs(pos - 0.84) / 0.18)
            drift = math.sin(float(ambient_phase) * 0.9 + pos * math.pi * 2.1) * 0.5 + 0.5
            target = (
                0.16
                + 0.78 * low_band * low_shape
                + 0.68 * mid_band * mid_shape
                + 0.56 * high_band * high_shape
                + 0.06 * drift
            )
            if frame.enabled:
                target += frame.pulse * (0.08 * low_shape + 0.05 * mid_shape + 0.035 * high_shape)
            target = max(0.04, min(1.0, target))
            tau = 0.060 if target > value else 0.180
            amount = 1.0 - math.exp(-dt / tau) if dt > 0.0 else 0.0
            next_targets.append(target)
            next_values.append(float(value) + (target - float(value)) * amount)
        return WaveAdvance(tuple(next_values), tuple(next_targets), frame, now)

    def style_response(self, ambience_style: str) -> dict[str, float]:
        style = str(ambience_style or "")
        for cached_key, cached_response in self.style_response_cache.items():
            if cached_key[0] == style:
                return dict(cached_response)
        frame = self.read_frame()
        cache_key = (style, frame.sequence)
        cached = self.style_response_cache.get(cache_key)
        if cached is not None:
            return dict(cached)
        plan = audio_reactive_style_plan(frame, style)
        return self._cache_style_response(cache_key, plan.as_response())

    def _store_frame(self, frame: ReactiveFrame, now: float) -> None:
        self.frame_cache = frame
        self.frame_updated_at = now
        self.style_response_cache = {}

    def _next_sequence(self) -> int:
        self.frame_sequence += 1
        return self.frame_sequence

    @staticmethod
    def _scaled_bands(bands: tuple[float, float, float], intensity: float) -> tuple[float, float, float]:
        low, mid, high = bands
        return (
            max(0.0, min(1.0, float(low) * intensity)),
            max(0.0, min(1.0, float(mid) * intensity)),
            max(0.0, min(1.0, float(high) * intensity)),
        )

    @staticmethod
    def _follow(old: float, new: float, dt: float) -> float:
        tau = 0.055 if new > old else 0.26
        amount = 1.0 - math.exp(-dt / tau) if dt > 0.0 else 0.0
        return max(0.0, min(1.0, old + (new - old) * amount))

    @classmethod
    def _motion_drive(
        cls,
        previous: ReactiveFrame,
        target: ReactiveFrame,
        dt: float,
        onset: float,
    ) -> tuple[float, float, float]:
        beat_target = max(target.energy * 0.14, target.bass * 0.18, onset * 1.08)
        glow_target = max(target.energy * 0.22 + target.shimmer * 0.20, target.bass * 0.16, onset * 0.72)
        lift_target = max(target.energy * 0.10, target.bass * 0.16, onset * 0.58)
        return (
            cls._follow_drive(previous.beat, beat_target, dt, rise_tau=0.018, release_tau=0.180),
            cls._follow_drive(previous.glow, glow_target, dt, rise_tau=0.050, release_tau=0.360),
            cls._follow_drive(previous.lift, lift_target, dt, rise_tau=0.034, release_tau=0.260),
        )

    @staticmethod
    def _follow_drive(old: float, new: float, dt: float, *, rise_tau: float, release_tau: float) -> float:
        target = max(0.0, min(1.0, float(new)))
        tau = rise_tau if target > old else release_tau
        amount = 1.0 - math.exp(-dt / tau) if dt > 0.0 else 0.0
        return max(0.0, min(1.0, float(old) + (target - float(old)) * amount))

    @staticmethod
    def _default_style_response() -> dict[str, float]:
        return {
            "energy": 0.0,
            "low": 0.0,
            "mid": 0.0,
            "high": 0.0,
            "bass": 0.0,
            "shimmer": 0.0,
            "lower_alpha": 1.0,
            "lower_scale": 1.0,
            "wave_height": 1.0,
            "wave_speed": 1.0,
            "color_boost": 0.0,
            "split_shift": 0.0,
            "top_guard": 0.0,
            "transition_shift": 0.0,
            "transition_softness": 0.0,
            "interference_flow": 0.0,
            "interference_alpha": 1.0,
            "side_wave_alpha": 0.0,
            "side_wave_height": 0.0,
            "side_wave_width": 1.0,
            "solid_accent_alpha": 0.0,
            "solid_accent_lightness": 0.0,
            "surface_glow": 0.0,
            "phase": 0.0,
            "pulse": 0.0,
            "beat_drive": 0.0,
            "glow_drive": 0.0,
            "lift_drive": 0.0,
        }

    def _cache_style_response(self, cache_key: tuple[str, int], response: dict[str, float]) -> dict[str, float]:
        self.style_response_cache[cache_key] = dict(response)
        return dict(response)


def audio_reactive_style_plan(frame: ReactiveFrame, ambience_style: str) -> AudioReactiveStylePlan:
    style = str(ambience_style or "")
    response = AudioReactiveEngine._default_style_response()
    defaults = {
        "sea_level": ("layered_water", "horizontal_surface", True),
        "duotone_full": ("full_duotone_interference", "lower_band", True),
        "bilateral_diffraction": ("side_frequency_waves", "bilateral", True),
        "pure_black": ("solid_album_accent", "edge_accent", True),
        "pure_white": ("solid_album_accent", "edge_accent", True),
        "solid_album": ("solid_album_accent", "edge_accent", True),
    }
    family, axis, preserves_center = defaults.get(style, ("none", "none", True))
    if style not in defaults or not frame.enabled:
        return AudioReactiveStylePlan(
            ambience_style=style,
            motion_family=family,
            primary_axis=axis,
            preserves_lyric_center=preserves_center,
            response=response,
        )

    energy = frame.energy
    low = frame.low
    mid = frame.mid
    high = frame.high
    bass = frame.bass
    shimmer = frame.shimmer
    pulse = frame.pulse
    beat = max(frame.beat, pulse * 0.72)
    glow = max(frame.glow, shimmer * 0.16)
    lift = max(frame.lift, bass * 0.10)
    response.update(
        {
            "energy": energy,
            "low": low,
            "mid": mid,
            "high": high,
            "bass": bass,
            "shimmer": shimmer,
            "phase": frame.phase,
            "pulse": pulse,
            "beat_drive": beat,
            "glow_drive": glow,
            "lift_drive": lift,
        }
    )
    if style == "sea_level":
        response.update(
            {
                "lower_alpha": 1.0 + energy * 0.28 + bass * 0.08 + glow * 0.12 + beat * 0.08,
                "lower_scale": 1.0 + energy * 0.024 + lift * 0.030 + beat * 0.008,
                "wave_height": 1.0 + bass * 0.28 + mid * 0.12 + beat * 0.20 + lift * 0.18,
                "wave_speed": 1.0 + energy * 0.12 + shimmer * 0.06 + beat * 0.06,
                "color_boost": energy * 0.22 + bass * 0.07 + glow * 0.10,
                "split_shift": lift * 0.014,
                "top_guard": energy * 0.036 + glow * 0.010,
            }
        )
    elif style == "duotone_full":
        response.update(
            {
                "lower_alpha": 1.0 + energy * 0.22 + glow * 0.18 + beat * 0.07,
                "lower_scale": 1.0 + energy * 0.040 + lift * 0.030 + beat * 0.010,
                "wave_speed": 1.0 + energy * 0.13 + beat * 0.06,
                "color_boost": energy * 0.26 + mid * 0.05 + glow * 0.10,
                "transition_shift": max(-0.030, min(0.030, (mid - low) * 0.012 + math.sin(frame.phase * 1.15) * energy * 0.010)),
                "transition_softness": 0.050 + energy * 0.046 + mid * 0.014 + glow * 0.018,
                "interference_flow": energy * 0.34 + shimmer * 0.12 + beat * 0.20 + glow * 0.12,
                "interference_alpha": 1.0 + energy * 0.34 + mid * 0.14 + glow * 0.18 + beat * 0.08,
                "side_wave_alpha": 0.12 + energy * 0.20 + glow * 0.14 + beat * 0.12,
                "side_wave_height": bass * 0.34 + mid * 0.18 + beat * 0.30 + lift * 0.14,
                "side_wave_width": 1.0 + energy * 0.18 + glow * 0.10,
            }
        )
    elif style == "bilateral_diffraction":
        response.update(
            {
                "color_boost": energy * 0.20 + shimmer * 0.07 + glow * 0.10,
                "wave_speed": 1.0 + energy * 0.14 + high * 0.07 + beat * 0.08,
                "side_wave_alpha": 0.18 + energy * 0.32 + shimmer * 0.08 + glow * 0.18 + beat * 0.16,
                "side_wave_height": bass * 0.48 + mid * 0.26 + high * 0.14 + beat * 0.34 + lift * 0.18,
                "side_wave_width": 1.0 + energy * 0.32 + glow * 0.14,
                "surface_glow": energy * 0.20 + glow * 0.16,
            }
        )
    else:
        lightness = 0.74 if style in {"pure_white", "solid_album"} else 0.52
        response.update(
            {
                "color_boost": energy * 0.15 + mid * 0.04 + glow * 0.08,
                "wave_speed": 1.0 + energy * 0.12 + beat * 0.06,
                "side_wave_alpha": 0.08 + energy * 0.16 + glow * 0.10 + beat * 0.06,
                "side_wave_height": bass * 0.26 + mid * 0.16 + beat * 0.18 + lift * 0.10,
                "solid_accent_alpha": 0.10 + energy * 0.40 + shimmer * 0.06 + glow * 0.16 + beat * 0.08,
                "solid_accent_lightness": lightness,
                "surface_glow": energy * 0.26 + bass * 0.10 + glow * 0.16,
            }
        )
    return AudioReactiveStylePlan(
        ambience_style=style,
        motion_family=family,
        primary_axis=axis,
        preserves_lyric_center=preserves_center,
        response=response,
    )


class AudioReactiveRuntime:
    def __init__(self, enabled: bool = False, intensity_percent: float = 55.0):
        self.engine = AudioReactiveEngine(enabled=enabled, intensity_percent=intensity_percent)
        self._input_cached_bands: tuple[float, float, float] | None = None
        self._input_sampled_at = -999.0
        self._input_live_audio_available = False
        self._input_live_audio_checked_at = -999.0
        self._input_last_snapshot: AudioReactiveInputSnapshot | None = None
        self._input_last_snapshot_at: float | None = None

    def configure(self, *, enabled: bool, intensity_percent: object) -> None:
        self.engine.configure(enabled=enabled, intensity_percent=float_or_default(intensity_percent, 55.0))

    @property
    def enabled(self) -> bool:
        return self.engine.enabled

    @property
    def intensity(self) -> float:
        return self.engine.intensity

    def read_frame(self) -> ReactiveFrame:
        return self.engine.read_frame()

    def advance_frame(self, bands: tuple[float, float, float], now: float | None = None) -> ReactiveFrame:
        return self.engine.advance(bands, now)

    def state(self) -> dict[str, float | bool]:
        return self.read_frame().as_dict()

    def style_response(self, ambience_style: str) -> dict[str, float]:
        return self.engine.style_response(ambience_style)

    def snapshot(self, ambience_styles: tuple[str, ...] = ()) -> ReactiveSnapshot:
        frame = self.read_frame()
        responses: dict[str, dict[str, float]] = {}
        for style in ambience_styles:
            key = str(style or "")
            if key and key not in responses:
                responses[key] = self.style_response(key)
        return ReactiveSnapshot(frame=frame, style_responses=responses)

    def advance_wave_values(
        self,
        wave_values: list[float] | tuple[float, ...],
        bands: tuple[float, float, float],
        ambient_phase: float,
        *,
        now: float,
        previous_at: float | None,
    ) -> WaveAdvance:
        return self.engine.advance_wave_values(
            wave_values,
            bands,
            ambient_phase,
            now=now,
            previous_at=previous_at,
        )

    def input_snapshot(
        self,
        *,
        now: float,
        reactive_enabled: bool | None = None,
        live_frame_reader: Callable[[], object | None] | None = None,
        live_audio_probe: Callable[[], bool] | None = None,
        audio_frames: Sequence[object] | None = None,
        audio_started_at: object = None,
        media_is_playing: bool = False,
        ambient_phase: object = 0.0,
    ) -> AudioReactiveInputSnapshot:
        now_value = float(now)
        enabled = self.enabled if reactive_enabled is None else bool(reactive_enabled)
        request_plan = audio_band_sampling_request_plan(
            now=now_value,
            reactive_enabled=enabled,
            cached_bands=self._input_cached_bands,
            sampled_at=self._input_sampled_at,
        )
        if request_plan.cached_bands is not None:
            bands = request_plan.cached_bands
            band_source = request_plan.source
        else:
            live_frame = None
            if request_plan.request_live_frame and live_frame_reader is not None:
                try:
                    live_frame = live_frame_reader()
                except Exception:
                    live_frame = None
            sample_plan = audio_band_sample_plan(
                now=now_value,
                reactive_enabled=request_plan.reactive_enabled,
                live_frame=live_frame,
                audio_frames=audio_frames,
                audio_started_at=now_value if audio_started_at is None else audio_started_at,
                media_is_playing=bool(media_is_playing),
                ambient_phase=ambient_phase,
            )
            self._apply_input_sample_plan(sample_plan)
            bands = sample_plan.bands
            band_source = sample_plan.source

        live_plan = live_audio_availability_request_plan(
            now=now_value,
            cached_available=self._input_live_audio_available,
            checked_at=self._input_live_audio_checked_at,
        )
        if live_plan.request_probe:
            available = False
            if live_audio_probe is not None:
                try:
                    available = bool(live_audio_probe())
                except Exception:
                    available = False
            self._input_live_audio_available = available
            self._input_live_audio_checked_at = now_value
            live_source = "probe"
        else:
            available = live_plan.cached_available
            live_source = live_plan.source

        snapshot = AudioReactiveInputSnapshot(
            sampled_bands=bands,
            live_audio_available=available,
            band_source=band_source,
            live_audio_source=live_source,
        )
        self._input_last_snapshot = snapshot
        self._input_last_snapshot_at = now_value
        return snapshot

    def input_diagnostics(self, *, now: float | None = None) -> dict[str, object]:
        snapshot = self._input_last_snapshot
        now_value = None if now is None else float(now)
        sampled_at = self._valid_timestamp_or_none(self._input_sampled_at)
        checked_at = self._valid_timestamp_or_none(self._input_live_audio_checked_at)
        return AudioReactiveInputDiagnostics(
            reactive_enabled=self.enabled,
            intensity=self.intensity,
            has_snapshot=snapshot is not None,
            sampled_bands=snapshot.sampled_bands if snapshot is not None else (0.0, 0.0, 0.0),
            band_source=snapshot.band_source if snapshot is not None else "",
            live_audio_available=snapshot.live_audio_available if snapshot is not None else bool(self._input_live_audio_available),
            live_audio_source=snapshot.live_audio_source if snapshot is not None else "",
            cache_has_bands=self._input_cached_bands is not None,
            sampled_at=sampled_at,
            snapshot_at=self._input_last_snapshot_at,
            live_audio_checked_at=checked_at,
            sample_age_ms=self._age_ms(now_value, sampled_at),
            live_audio_check_age_ms=self._age_ms(now_value, checked_at),
        ).as_dict()

    def _apply_input_sample_plan(self, plan: AudioBandSamplePlan) -> None:
        if plan.cache_bands is not None:
            self._input_cached_bands = plan.cache_bands
        if plan.cache_sampled_at is not None:
            self._input_sampled_at = plan.cache_sampled_at
        if plan.live_audio_available is not None:
            self._input_live_audio_available = plan.live_audio_available
        if plan.live_audio_checked_at is not None:
            self._input_live_audio_checked_at = plan.live_audio_checked_at

    @staticmethod
    def _valid_timestamp_or_none(value: object) -> float | None:
        timestamp = float_or_default(value, -999.0)
        return timestamp if timestamp > -900.0 else None

    @staticmethod
    def _age_ms(now: float | None, timestamp: float | None) -> float | None:
        if now is None or timestamp is None:
            return None
        return max(0.0, (now - timestamp) * 1000.0)


def reactive_audio_cache_hit(
    *,
    now: float,
    reactive_enabled: bool,
    cached_bands: object,
    sampled_at: object,
    ttl_seconds: float = 0.18,
) -> tuple[float, float, float] | None:
    if not reactive_enabled or cached_bands is None:
        return None
    try:
        sampled = float(sampled_at or -999.0)
        ttl = max(0.0, float(ttl_seconds))
    except (TypeError, ValueError):
        return None
    if float(now) - sampled >= ttl:
        return None
    return _coerce_audio_bands(cached_bands, default=None)


def audio_band_sampling_request_plan(
    *,
    now: float,
    reactive_enabled: bool,
    cached_bands: object,
    sampled_at: object,
    cache_ttl_seconds: float = 0.18,
) -> AudioBandSamplingRequestPlan:
    enabled = bool(reactive_enabled)
    cached = reactive_audio_cache_hit(
        now=now,
        reactive_enabled=enabled,
        cached_bands=cached_bands,
        sampled_at=sampled_at,
        ttl_seconds=cache_ttl_seconds,
    )
    if cached is not None:
        return AudioBandSamplingRequestPlan(
            cached_bands=cached,
            request_live_frame=False,
            reactive_enabled=enabled,
            source="cache",
        )
    return AudioBandSamplingRequestPlan(
        cached_bands=None,
        request_live_frame=enabled,
        reactive_enabled=enabled,
        source="sample",
    )


def live_audio_availability_request_plan(
    *,
    now: float,
    cached_available: object,
    checked_at: object,
    ttl_seconds: float = 0.45,
) -> LiveAudioAvailabilityRequestPlan:
    try:
        checked = float(checked_at or -999.0)
        ttl = max(0.0, float(ttl_seconds))
    except (TypeError, ValueError):
        return LiveAudioAvailabilityRequestPlan(
            cached_available=False,
            request_probe=True,
            source="probe",
        )
    if float(now) - checked < ttl:
        return LiveAudioAvailabilityRequestPlan(
            cached_available=bool(cached_available),
            request_probe=False,
            source="cache",
        )
    return LiveAudioAvailabilityRequestPlan(
        cached_available=bool(cached_available),
        request_probe=True,
        source="probe",
    )


def audio_band_sample_plan(
    *,
    now: float,
    reactive_enabled: bool,
    live_frame: object | None,
    audio_frames: Sequence[object] | None,
    audio_started_at: object,
    media_is_playing: bool,
    ambient_phase: object,
    live_audio_available: bool = False,
) -> AudioBandSamplePlan:
    now_value = float(now)
    if reactive_enabled and live_frame is not None:
        bands = _coerce_audio_bands(
            (
                getattr(live_frame, "low", 0.0),
                getattr(live_frame, "mid", 0.0),
                getattr(live_frame, "high", 0.0),
            ),
            default=(0.0, 0.0, 0.0),
        )
        return AudioBandSamplePlan(
            bands=bands,
            source="live_frame",
            cache_bands=bands,
            cache_sampled_at=now_value,
            live_audio_available=True,
            live_audio_checked_at=now_value,
        )

    frames = list(audio_frames or [])
    if reactive_enabled:
        live_audio_available = False
    if not frames:
        if not live_audio_available:
            bands = (0.10, 0.08, 0.05) if media_is_playing else (0.02, 0.015, 0.01)
            return AudioBandSamplePlan(
                bands=bands,
                source="playing_fallback" if media_is_playing else "idle_fallback",
                cache_bands=bands if reactive_enabled else None,
                cache_sampled_at=now_value if reactive_enabled else None,
                live_audio_available=False if reactive_enabled else None,
                live_audio_checked_at=now_value if reactive_enabled else None,
            )
        phase = _safe_float(ambient_phase, 0.0)
        bands = (
            math.sin(phase * 0.8) * 0.24 + 0.48,
            math.sin(phase * 1.3 + 1.6) * 0.22 + 0.50,
            math.sin(phase * 2.2 + 0.8) * 0.18 + 0.42,
        )
        return AudioBandSamplePlan(
            bands=bands,
            source="synthetic_live",
            cache_bands=bands if reactive_enabled else None,
            cache_sampled_at=now_value if reactive_enabled else None,
        )

    started_at = _safe_float(audio_started_at, now_value)
    pos = ((now_value - started_at) * 3.35) % len(frames)
    index = int(pos)
    next_index = (index + 1) % len(frames)
    mix = pos - index
    mix = mix * mix * (3.0 - 2.0 * mix)
    current = frames[index]
    upcoming = frames[next_index]
    bands = (
        _blend_band(getattr(current, "low", 0.0), getattr(upcoming, "low", 0.0), mix),
        _blend_band(getattr(current, "mid", 0.0), getattr(upcoming, "mid", 0.0), mix),
        _blend_band(getattr(current, "high", 0.0), getattr(upcoming, "high", 0.0), mix),
    )
    return AudioBandSamplePlan(
        bands=bands,
        source="sequence",
        live_audio_available=False if reactive_enabled else None,
        live_audio_checked_at=now_value if reactive_enabled else None,
    )


def _blend_band(start: object, end: object, mix: float) -> float:
    a = _safe_float(start, 0.0)
    b = _safe_float(end, 0.0)
    return max(0.0, min(1.0, a + (b - a) * mix))


def _coerce_audio_bands(
    bands: object,
    *,
    default: tuple[float, float, float] | None,
) -> tuple[float, float, float] | None:
    try:
        low, mid, high = bands  # type: ignore[misc]
    except (TypeError, ValueError):
        return default
    return (
        max(0.0, min(1.0, _safe_float(low, 0.0))),
        max(0.0, min(1.0, _safe_float(mid, 0.0))),
        max(0.0, min(1.0, _safe_float(high, 0.0))),
    )


def _safe_float(value: object, default: float) -> float:
    return float_or_default(value, default)


def float_or_default(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
