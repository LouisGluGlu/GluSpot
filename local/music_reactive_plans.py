from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))


def _number(values: Mapping[str, float], key: str, default: float = 0.0) -> float:
    try:
        return float(values.get(key, default))
    except (TypeError, ValueError):
        return float(default)


@dataclass(frozen=True)
class ReactiveMotionDrives:
    beat: float
    glow: float
    lift: float


@dataclass(frozen=True)
class SeaLevelMotionPlan:
    phase_rate: float
    center_shift: float
    glow_height_scale: float
    amplitude_scale: float
    thickness_scale: float
    crest_alpha_scale: float


@dataclass(frozen=True)
class SeaLevelWavePaintPlan:
    flow: float
    phase_ambient_rate: float
    phase_time_rate: float
    center_y_ratio: float
    center_flow_units: float
    center_band_units: float
    center_shift_ratio: float
    glow_height_ratio: float
    glow_flow_units: float
    amplitude_units: float
    thickness_units: float
    counter_y_offset_units: float
    counter_amplitude_scale: float
    counter_thickness_scale: float
    crest_alpha_scale: float


@dataclass(frozen=True)
class DuotoneMotionPlan:
    transition_height_scale: float
    transition_alpha_scale: float
    side_flow: float
    side_alpha_scale: float
    side_amplitude_scale: float
    interference_flow: float
    interference_alpha_scale: float


@dataclass(frozen=True)
class DuotoneSplitBandPaintPlan:
    upper_solid_stop: float
    bridge_in_stop: float
    bridge_out_stop: float
    lower_solid_stop: float
    transition_top_ratio: float
    transition_height_ratio: float


@dataclass(frozen=True)
class DuotoneSideWavePaintPlan:
    enabled: bool
    flow: float
    phase_response_rate: float
    phase_time_rate: float
    phase_ambient_rate: float
    point_count: int
    alpha: int
    y_top_ratio: float
    y_bottom_inset_ratio: float
    side_anchor_ratio: float
    amplitude_ratio: float
    halo_width_units: float
    line_width_units: float


@dataclass(frozen=True)
class DuotoneInterferenceBlobPlan:
    role: str
    center_x_ratio: float
    center_y_ratio: float
    radius_x_ratio: float
    radius_y_ratio: float
    alpha: int
    color_mix_ratio: float = 0.0
    bridge_mix_ratio: float = 0.0


@dataclass(frozen=True)
class DuotoneInterferencePaintPlan:
    enabled: bool
    blobs: tuple[DuotoneInterferenceBlobPlan, ...]


@dataclass(frozen=True)
class BilateralMotionPlan:
    flow: float
    phase_rate: float
    side_wash_scale: float
    ring_speed_scale: float
    ring_alpha_scale: float
    ring_width_scale: float
    waveform_alpha_scale: float
    waveform_amplitude_scale: float
    center_glow_scale: float


@dataclass(frozen=True)
class BilateralWaveformPaintPlan:
    enabled: bool
    point_count: int
    alpha: int
    y_top_ratio: float
    y_bottom_inset_ratio: float
    side_anchor_ratio: float
    amplitude_ratio: float
    halo_width_units: float
    line_width_units: float
    carrier_cycles: float
    carrier_phase_rate: float
    carrier_scale: float
    overtone_cycles: float
    overtone_phase_rate: float
    overtone_seed_rate: float
    overtone_scale: float
    sample_center: float
    sample_scale: float


@dataclass(frozen=True)
class BilateralCenterGlowPaintPlan:
    center_flow_x_units: float
    center_phase_x_rate: float
    center_flow_y_units: float
    center_phase_y_rate: float
    center_phase_y_offset: float
    radius_width_ratio: float
    alpha_amount: float


@dataclass(frozen=True)
class BilateralSideWashPaintPlan:
    side: int
    band: float
    seed: float
    x_outer_width_ratio: float
    inward_offset_scale: float
    y_flow_units: float
    y_phase_rate: float
    wash_alpha_amount: float
    wash_rx_width_ratio: float
    wash_rx_height_ratio: float
    wash_ry_width_ratio: float
    wash_ry_height_ratio: float
    halo_rx_width_ratio: float
    halo_rx_height_ratio: float
    halo_ry_width_ratio: float
    halo_ry_height_ratio: float
    halo_offset_units: float
    halo_alpha_scale: float


@dataclass(frozen=True)
class BilateralRingPaintPlan:
    side: int
    band: float
    seed: float
    center_outer_width_ratio: float
    center_y_flow_units: float
    center_y_phase_rate: float
    travel_rate: float
    travel_seed_rate: float
    falloff_power: float
    rx_width_base_ratio: float
    rx_width_travel_ratio: float
    rx_height_base_ratio: float
    rx_height_travel_ratio: float
    rx_flow_units: float
    rx_flow_base: float
    rx_flow_sample_scale: float
    ry_width_base_ratio: float
    ry_width_travel_ratio: float
    ry_height_base_ratio: float
    ry_height_travel_ratio: float
    ry_flow_units: float
    ry_flow_base: float
    ry_flow_sample_scale: float
    alpha_base: float
    alpha_band_scale: float
    alpha_sample_scale: float
    alpha_multiplier: float
    alpha_cap: int
    compact_min_alpha: int
    tint_base: float
    tint_travel_scale: float
    pen_base_units: float
    pen_falloff_units: float
    pen_sample_units: float
    pen_width_scale: float


@dataclass(frozen=True)
class BilateralDiffractionPaintPlan:
    inward_offset_width_ratio: float
    ring_count: int
    center_glow: BilateralCenterGlowPaintPlan
    side_washes: tuple[BilateralSideWashPaintPlan, ...]
    rings: tuple[BilateralRingPaintPlan, ...]


@dataclass(frozen=True)
class SolidAccentMotionPlan:
    alpha_amount: float
    edge_alpha_scale: float
    wave_alpha_scale: float
    wave_amplitude_scale: float
    wave_thickness_scale: float
    phase_rate: float
    center_y_ratio: float
    center_protection: float


@dataclass(frozen=True)
class BackgroundReactiveMotionPlan:
    ambience_style: str
    motion_family: str
    primary_axis: str
    preserves_lyric_center: bool
    sea_level: SeaLevelMotionPlan | None = None
    duotone: DuotoneMotionPlan | None = None
    bilateral: BilateralMotionPlan | None = None
    solid: SolidAccentMotionPlan | None = None


def reactive_motion_drives(response: Mapping[str, float]) -> ReactiveMotionDrives:
    beat = _number(response, "beat_drive", _number(response, "pulse", 0.0))
    glow = _number(response, "glow_drive", max(_number(response, "surface_glow", 0.0), _number(response, "energy", 0.0) * 0.55))
    lift = _number(response, "lift_drive", _number(response, "low", 0.0) * 0.50)
    return ReactiveMotionDrives(
        beat=_clamp(beat),
        glow=_clamp(glow),
        lift=_clamp(lift),
    )


def sea_level_motion_plan(response: Mapping[str, float]) -> SeaLevelMotionPlan:
    drives = reactive_motion_drives(response)
    water = _clamp(drives.beat * 0.44 + drives.glow * 0.30 + drives.lift * 0.34)
    return SeaLevelMotionPlan(
        phase_rate=1.0 + drives.beat * 0.16 + drives.lift * 0.08,
        center_shift=drives.lift * 0.018 - drives.beat * 0.004,
        glow_height_scale=1.0 + drives.glow * 0.32 + drives.lift * 0.12,
        amplitude_scale=1.0 + water * 0.42,
        thickness_scale=1.0 + drives.glow * 0.22 + drives.lift * 0.16,
        crest_alpha_scale=1.0 + drives.glow * 0.24 + drives.beat * 0.18,
    )


def _bands(values: object) -> tuple[float, float, float]:
    try:
        low, mid, high = values  # type: ignore[misc]
    except (TypeError, ValueError):
        return (0.0, 0.0, 0.0)
    try:
        return (_clamp(float(low)), _clamp(float(mid)), _clamp(float(high)))
    except (TypeError, ValueError):
        return (0.0, 0.0, 0.0)


def sea_level_wave_paint_plan(
    response: Mapping[str, float],
    *,
    bands: Sequence[float] | object = (),
    flow: object = 0.0,
    expanded: bool = False,
    media_is_playing: bool = True,
) -> SeaLevelWavePaintPlan:
    low, mid, high = _bands(bands)
    flow_value = _clamp(_coerce_float(flow))
    if not bool(media_is_playing):
        flow_value *= 0.42
    motion = sea_level_motion_plan(response)
    wave_height = max(0.36, _number(response, "wave_height", 1.0))
    wave_speed = max(0.10, _number(response, "wave_speed", 1.0))
    energy = _clamp(_number(response, "energy", 0.0))
    band_mix = _clamp(low * 0.58 + mid * 0.30 + high * 0.22)
    amplitude_base = 7.0 if bool(expanded) else 3.8
    amplitude_flow = (15.0 if bool(expanded) else 8.0) * flow_value * band_mix
    thickness_base = 15.0 if bool(expanded) else 7.0
    thickness_flow = (14.0 if bool(expanded) else 7.0) * flow_value * (0.28 + mid)
    return SeaLevelWavePaintPlan(
        flow=flow_value,
        phase_ambient_rate=1.15 + flow_value * 1.30,
        phase_time_rate=(0.38 + flow_value * 0.45) * wave_speed * motion.phase_rate,
        center_y_ratio=0.64 if bool(expanded) else 0.60,
        center_flow_units=8.0 * flow_value,
        center_band_units=4.0 * flow_value * (low - high),
        center_shift_ratio=_number(response, "split_shift", 0.0) + motion.center_shift,
        glow_height_ratio=((0.22 if bool(expanded) else 0.24) + energy * 0.040) * motion.glow_height_scale,
        glow_flow_units=14.0 * flow_value * (0.24 + mid) * motion.glow_height_scale,
        amplitude_units=(amplitude_base + amplitude_flow) * wave_height * motion.amplitude_scale,
        thickness_units=(thickness_base + thickness_flow) * (1.0 + (wave_height - 1.0) * 0.45) * motion.thickness_scale,
        counter_y_offset_units=12.0,
        counter_amplitude_scale=0.58,
        counter_thickness_scale=0.40,
        crest_alpha_scale=motion.crest_alpha_scale,
    )


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def duotone_motion_plan(response: Mapping[str, float], base_flow: float) -> DuotoneMotionPlan:
    drives = reactive_motion_drives(response)
    side = _clamp(drives.beat * 0.42 + drives.glow * 0.30 + drives.lift * 0.28)
    interference = _clamp(_number(response, "interference_flow", 0.0) + drives.glow * 0.28 + drives.beat * 0.18)
    return DuotoneMotionPlan(
        transition_height_scale=1.0 + drives.lift * 0.20 + drives.glow * 0.08,
        transition_alpha_scale=1.0 + drives.glow * 0.22 + drives.beat * 0.10,
        side_flow=_clamp(base_flow + interference * 0.42 + side * 0.18),
        side_alpha_scale=1.0 + side * 0.54,
        side_amplitude_scale=1.0 + side * 0.58,
        interference_flow=_clamp(base_flow + interference),
        interference_alpha_scale=1.0 + drives.glow * 0.26 + drives.lift * 0.10,
    )


def duotone_split_band_paint_plan(
    response: Mapping[str, float],
    *,
    transition_height_scale: object = 1.0,
) -> DuotoneSplitBandPaintPlan:
    shift = _number(response, "transition_shift", 0.0)
    softness = _number(response, "transition_softness", 0.0)
    upper_solid = max(0.34, min(0.48, 0.42 + shift - softness * 0.18))
    bridge_in = max(upper_solid + 0.02, min(0.53, 0.48 + shift - softness * 0.58))
    bridge_out = max(bridge_in + 0.04, min(0.60, 0.54 + shift + softness * 0.30))
    return DuotoneSplitBandPaintPlan(
        upper_solid_stop=upper_solid,
        bridge_in_stop=bridge_in,
        bridge_out_stop=bridge_out,
        lower_solid_stop=max(bridge_out + 0.04, min(0.70, 0.62 + shift + softness * 0.62)),
        transition_top_ratio=max(0.34, min(0.46, 0.40 + shift - softness * 0.40)),
        transition_height_ratio=max(0.22, min(0.34, 0.24 + softness * 1.30))
        * max(0.0, _coerce_float(transition_height_scale, 1.0)),
    )


def duotone_side_wave_paint_plan(
    response: Mapping[str, float],
    *,
    flow: object = 0.0,
    expanded: bool = False,
    intensity: object = 1.0,
    opacity_boost: object = 1.0,
    alpha_scale: object = 1.0,
    amplitude_scale: object = 1.0,
) -> DuotoneSideWavePaintPlan:
    flow_value = _clamp(_coerce_float(flow))
    alpha_amount = _number(response, "side_wave_alpha", 0.0) * max(0.0, _coerce_float(alpha_scale, 1.0))
    alpha = int(
        max(
            0,
            min(
                98,
                alpha_amount
                * 118.0
                * max(0.0, _coerce_float(intensity, 1.0))
                * max(0.0, _coerce_float(opacity_boost, 1.0)),
            ),
        )
    )
    point_count = 34 if bool(expanded) else 26
    y_top_ratio = 0.24 if bool(expanded) else 0.28
    y_bottom_inset_ratio = 0.17 if bool(expanded) else 0.20
    side_anchor_ratio = 0.080 if bool(expanded) else 0.105
    side_width = max(0.70, min(1.60, _number(response, "side_wave_width", 1.0)))
    base_amplitude = 0.016 if bool(expanded) else 0.022
    amplitude_ratio = (
        (base_amplitude + _number(response, "side_wave_height", 0.0) * 0.028)
        * side_width
        * max(0.0, _coerce_float(amplitude_scale, 1.0))
    )
    return DuotoneSideWavePaintPlan(
        enabled=alpha_amount > 0.001 and flow_value > 0.01 and alpha > 2,
        flow=flow_value,
        phase_response_rate=1.8,
        phase_time_rate=0.20 + flow_value * 0.34,
        phase_ambient_rate=0.38,
        point_count=point_count,
        alpha=alpha,
        y_top_ratio=y_top_ratio,
        y_bottom_inset_ratio=y_bottom_inset_ratio,
        side_anchor_ratio=side_anchor_ratio,
        amplitude_ratio=amplitude_ratio,
        halo_width_units=5.6 if bool(expanded) else 4.1,
        line_width_units=1.65 if bool(expanded) else 1.28,
    )


def _duotone_interference_drift(
    *,
    seed: float,
    x: float,
    y: float,
    dx_period: float,
    dy_period: float,
    amount: float,
    flow: float,
    now: float,
) -> tuple[float, float]:
    px = x + amount * flow * (
        0.20 * math.sin(now / dx_period * math.tau + seed)
        + 0.08 * math.sin(now / (dx_period * 0.43) * math.tau + seed * 2.1)
    )
    py = y + amount * flow * (
        0.16 * math.sin(now / dy_period * math.tau + seed * 1.7)
        + 0.07 * math.sin(now / (dy_period * 0.51) * math.tau + seed * 2.9)
    )
    return (px, py)


def _alpha_units(value: float, *, cap: int, intensity: float, opacity_boost: float) -> int:
    return int(max(0, min(cap, value * 255.0 * intensity * opacity_boost)))


def duotone_interference_paint_plan(
    *,
    flow: object = 0.0,
    now: object = 0.0,
    bands: Sequence[float] | object = (),
    intensity: object = 1.0,
    opacity_boost: object = 1.0,
) -> DuotoneInterferencePaintPlan:
    flow_value = _clamp(_coerce_float(flow))
    if flow_value <= 0.01:
        return DuotoneInterferencePaintPlan(enabled=False, blobs=())
    low, mid, high = _bands(bands)
    now_value = _coerce_float(now)
    intensity_value = _coerce_float(intensity, 1.0)
    opacity_value = _coerce_float(opacity_boost, 1.0)

    accent_pulse = 0.5 + 0.5 * math.sin(now_value / 9.5 * math.tau + 0.4)
    main_pulse = 0.5 + 0.5 * math.sin(now_value / 11.5 * math.tau + 3.0)
    mix_progress = 0.5 + 0.5 * math.sin(now_value / 13.0 * math.tau + 1.2)
    highlight_pulse = 0.5 + 0.5 * math.sin(now_value / 15.5 * math.tau + 1.8)

    accent_x, accent_y = _duotone_interference_drift(
        seed=0.3,
        x=0.24,
        y=0.38,
        dx_period=18.0,
        dy_period=24.0,
        amount=1.0,
        flow=flow_value,
        now=now_value,
    )
    main_x, main_y = _duotone_interference_drift(
        seed=5.7,
        x=0.76,
        y=0.74,
        dx_period=22.0,
        dy_period=27.0,
        amount=0.78,
        flow=flow_value,
        now=now_value,
    )
    mix_x = accent_x + (main_x - accent_x) * (0.45 + 0.10 * math.sin(now_value / 17.0 * math.tau + 0.8))
    mix_y = accent_y + (main_y - accent_y) * (0.48 + 0.08 * math.sin(now_value / 15.0 * math.tau + 2.6))
    highlight_x, highlight_y = _duotone_interference_drift(
        seed=11.2,
        x=0.50,
        y=0.50,
        dx_period=31.0,
        dy_period=21.0,
        amount=0.72,
        flow=flow_value,
        now=now_value,
    )

    return DuotoneInterferencePaintPlan(
        enabled=True,
        blobs=(
            DuotoneInterferenceBlobPlan(
                role="accent",
                center_x_ratio=accent_x,
                center_y_ratio=accent_y,
                radius_x_ratio=0.38 + accent_pulse * 0.24 + high * 0.040,
                radius_y_ratio=0.48 + accent_pulse * 0.32 + mid * 0.036,
                alpha=_alpha_units(0.36 + accent_pulse * 0.165 + high * 0.044, cap=220, intensity=intensity_value, opacity_boost=opacity_value),
            ),
            DuotoneInterferenceBlobPlan(
                role="main",
                center_x_ratio=main_x,
                center_y_ratio=main_y,
                radius_x_ratio=0.38 + main_pulse * 0.22 + low * 0.040,
                radius_y_ratio=0.36 + main_pulse * 0.22 + mid * 0.030,
                alpha=_alpha_units(0.30 + main_pulse * 0.135 + low * 0.038, cap=205, intensity=intensity_value, opacity_boost=opacity_value),
            ),
            DuotoneInterferenceBlobPlan(
                role="mix",
                center_x_ratio=mix_x,
                center_y_ratio=mix_y,
                radius_x_ratio=0.34 + mix_progress * 0.14 + mid * 0.032,
                radius_y_ratio=0.36 + mix_progress * 0.18 + mid * 0.030,
                alpha=_alpha_units(0.20 + mix_progress * 0.105 + mid * 0.034, cap=190, intensity=intensity_value, opacity_boost=opacity_value),
                color_mix_ratio=0.50 + 0.12 * math.sin(now_value / 18.0 * math.tau + 1.7),
                bridge_mix_ratio=0.42,
            ),
            DuotoneInterferenceBlobPlan(
                role="highlight",
                center_x_ratio=highlight_x,
                center_y_ratio=highlight_y,
                radius_x_ratio=0.22 + highlight_pulse * 0.10,
                radius_y_ratio=0.26 + highlight_pulse * 0.13,
                alpha=_alpha_units(0.050 + highlight_pulse * 0.034 + mid * 0.010, cap=74, intensity=intensity_value, opacity_boost=opacity_value),
                bridge_mix_ratio=0.72,
            ),
        ),
    )


def bilateral_motion_plan(response: Mapping[str, float], base_flow: float, frequency: float) -> BilateralMotionPlan:
    drives = reactive_motion_drives(response)
    side = _clamp(drives.beat * 0.48 + drives.glow * 0.26 + drives.lift * 0.26)
    flow = _clamp(base_flow + _number(response, "energy", 0.0) * 0.14 + side * 0.18)
    return BilateralMotionPlan(
        flow=flow,
        phase_rate=1.0 + drives.beat * 0.16 + _clamp(frequency) * 0.06,
        side_wash_scale=1.0 + drives.glow * 0.22 + drives.lift * 0.10,
        ring_speed_scale=1.0 + drives.beat * 0.24 + drives.lift * 0.08,
        ring_alpha_scale=1.0 + drives.glow * 0.28 + drives.beat * 0.16,
        ring_width_scale=1.0 + drives.lift * 0.16 + drives.glow * 0.08,
        waveform_alpha_scale=1.0 + side * 0.58,
        waveform_amplitude_scale=1.0 + side * 0.64,
        center_glow_scale=1.0 + drives.glow * 0.10,
    )


def bilateral_diffraction_paint_plan(
    *,
    bands: Sequence[float] | object = (),
    flow: object = 0.0,
    boost: object = 0.0,
    frequency: object = 0.0,
    expanded: bool = False,
    intensity: object = 1.0,
    opacity_boost: object = 1.0,
    size_boost: object = 1.0,
    ring_count: object = 8,
    center_glow_scale: object = 1.0,
    side_wash_scale: object = 1.0,
    ring_speed_scale: object = 1.0,
    ring_alpha_scale: object = 1.0,
    ring_width_scale: object = 1.0,
) -> BilateralDiffractionPaintPlan:
    low, mid, high = _bands(bands)
    flow_value = _clamp(_coerce_float(flow))
    boost_value = max(0.0, _coerce_float(boost))
    frequency_value = _clamp(_coerce_float(frequency))
    intensity_value = max(0.0, _coerce_float(intensity, 1.0))
    opacity_value = max(0.0, _coerce_float(opacity_boost, 1.0))
    size_value = max(0.0, _coerce_float(size_boost, 1.0))
    center_scale = max(0.0, _coerce_float(center_glow_scale, 1.0))
    wash_scale = max(0.0, _coerce_float(side_wash_scale, 1.0))
    speed_scale = max(0.0, _coerce_float(ring_speed_scale, 1.0))
    alpha_scale = max(0.0, _coerce_float(ring_alpha_scale, 1.0))
    width_scale = max(0.0, _coerce_float(ring_width_scale, 1.0))
    try:
        count = int(round(float(ring_count)))
    except (TypeError, ValueError):
        count = 8
    count = max(1, min(24, count))
    is_expanded = bool(expanded)

    center_glow = BilateralCenterGlowPaintPlan(
        center_flow_x_units=8.0,
        center_phase_x_rate=0.56,
        center_flow_y_units=2.8,
        center_phase_y_rate=0.41,
        center_phase_y_offset=1.7,
        radius_width_ratio=0.56 if is_expanded else 0.46,
        alpha_amount=((0.020 if is_expanded else 0.014) * opacity_value + mid * 0.006) * center_scale,
    )

    wash_specs = ((-1, low, 0.0), (1, high, 2.1))
    side_washes: list[BilateralSideWashPaintPlan] = []
    for side, band, seed in wash_specs:
        if is_expanded:
            wash_rx_width = (0.28 * size_value + band * 0.026) * wash_scale
            wash_ry_height = (1.26 * size_value + mid * 0.044) * wash_scale
            halo_rx_width = (0.18 + band * 0.018) * wash_scale
            halo_ry_height = (0.72 + mid * 0.036) * wash_scale
            wash_rx_height = wash_ry_width = halo_rx_height = halo_ry_width = 0.0
        else:
            wash_radius = (1.18 * size_value + band * 0.12 + mid * 0.06) * wash_scale
            halo_radius = (0.82 + band * 0.08 + mid * 0.04) * wash_scale
            wash_rx_width = wash_ry_width = halo_rx_width = halo_ry_width = 0.0
            wash_rx_height = wash_ry_height = wash_radius
            halo_rx_height = halo_ry_height = halo_radius
        side_washes.append(
            BilateralSideWashPaintPlan(
                side=side,
                band=band,
                seed=seed,
                x_outer_width_ratio=0.055 if is_expanded else 0.115,
                inward_offset_scale=0.55,
                y_flow_units=7.0,
                y_phase_rate=0.72,
                wash_alpha_amount=((0.115 if is_expanded else 0.094) * opacity_value + band * 0.028) * wash_scale,
                wash_rx_width_ratio=wash_rx_width,
                wash_rx_height_ratio=wash_rx_height,
                wash_ry_width_ratio=wash_ry_width,
                wash_ry_height_ratio=wash_ry_height,
                halo_rx_width_ratio=halo_rx_width,
                halo_rx_height_ratio=halo_rx_height,
                halo_ry_width_ratio=halo_ry_width,
                halo_ry_height_ratio=halo_ry_height,
                halo_offset_units=10.0,
                halo_alpha_scale=0.44,
            )
        )

    rings: list[BilateralRingPaintPlan] = []
    travel_rate = (0.060 + flow_value * 0.075 + frequency_value * 0.125) * speed_scale
    for side, band, seed in wash_specs:
        if is_expanded:
            rx_width_base = 0.105
            rx_width_travel = 0.42
            rx_height_base = rx_height_travel = 0.0
            ry_width_base = ry_width_travel = 0.0
            ry_height_base = 0.48
            ry_height_travel = 1.04
            rx_flow_units = 8.0 * width_scale
            rx_flow_base = 0.0
            rx_flow_sample_scale = 1.0
            ry_flow_units = 7.0 * width_scale
            ry_flow_base = 0.34 + band
            ry_flow_sample_scale = 0.0
        else:
            rx_width_base = rx_width_travel = 0.0
            ry_width_base = ry_width_travel = 0.0
            rx_height_base = ry_height_base = 0.62
            rx_height_travel = ry_height_travel = 1.42
            rx_flow_units = ry_flow_units = 10.0 * width_scale
            rx_flow_base = ry_flow_base = 0.34 + band
            rx_flow_sample_scale = ry_flow_sample_scale = 0.55
        rings.append(
            BilateralRingPaintPlan(
                side=side,
                band=band,
                seed=seed,
                center_outer_width_ratio=0.030 if is_expanded else 0.130,
                center_y_flow_units=5.5,
                center_y_phase_rate=0.60,
                travel_rate=travel_rate,
                travel_seed_rate=0.061,
                falloff_power=1.36,
                rx_width_base_ratio=rx_width_base,
                rx_width_travel_ratio=rx_width_travel,
                rx_height_base_ratio=rx_height_base,
                rx_height_travel_ratio=rx_height_travel,
                rx_flow_units=rx_flow_units,
                rx_flow_base=rx_flow_base,
                rx_flow_sample_scale=rx_flow_sample_scale,
                ry_width_base_ratio=ry_width_base,
                ry_width_travel_ratio=ry_width_travel,
                ry_height_base_ratio=ry_height_base,
                ry_height_travel_ratio=ry_height_travel,
                ry_flow_units=ry_flow_units,
                ry_flow_base=ry_flow_base,
                ry_flow_sample_scale=ry_flow_sample_scale,
                alpha_base=0.36,
                alpha_band_scale=0.22,
                alpha_sample_scale=0.10,
                alpha_multiplier=intensity_value * opacity_value * alpha_scale,
                alpha_cap=132,
                compact_min_alpha=0 if is_expanded else 3,
                tint_base=0.28,
                tint_travel_scale=0.22,
                pen_base_units=1.0,
                pen_falloff_units=0.9 if is_expanded else 0.55,
                pen_sample_units=0.36,
                pen_width_scale=width_scale,
            )
        )

    return BilateralDiffractionPaintPlan(
        inward_offset_width_ratio=0.035 + boost_value * (0.19 if is_expanded else 0.14),
        ring_count=count,
        center_glow=center_glow,
        side_washes=tuple(side_washes),
        rings=tuple(rings),
    )


def bilateral_waveform_paint_plan(
    response: Mapping[str, float],
    *,
    frequency: object = 0.0,
    expanded: bool = False,
    intensity: object = 1.0,
    opacity_boost: object = 1.0,
    alpha_scale: object = 1.0,
    amplitude_scale: object = 1.0,
) -> BilateralWaveformPaintPlan:
    high = _clamp(_number(response, "high", 0.0))
    mid = _clamp(_number(response, "mid", 0.0))
    frequency_value = _clamp(_coerce_float(frequency))
    alpha_amount = _number(response, "side_wave_alpha", 0.0) * max(0.0, _coerce_float(alpha_scale, 1.0))
    alpha = int(
        max(
            0,
            min(
                156,
                alpha_amount
                * 138.0
                * max(0.0, _coerce_float(intensity, 1.0))
                * max(0.0, _coerce_float(opacity_boost, 1.0)),
            ),
        )
    )
    base_amplitude = 0.020 if bool(expanded) else 0.026
    height_scale = 0.026 if bool(expanded) else 0.036
    return BilateralWaveformPaintPlan(
        enabled=alpha_amount > 0.001 and alpha > 2,
        point_count=max(24, min(72, int(30 + high * 20.0 + frequency_value * 22.0))),
        alpha=alpha,
        y_top_ratio=0.17 if bool(expanded) else 0.21,
        y_bottom_inset_ratio=0.16 if bool(expanded) else 0.19,
        side_anchor_ratio=0.105 if bool(expanded) else 0.130,
        amplitude_ratio=(base_amplitude + _number(response, "side_wave_height", 0.0) * height_scale)
        * max(0.0, _coerce_float(amplitude_scale, 1.0)),
        halo_width_units=4.2 if bool(expanded) else 3.0,
        line_width_units=1.35 if bool(expanded) else 1.05,
        carrier_cycles=1.8 + mid * 1.6,
        carrier_phase_rate=1.35,
        carrier_scale=0.42,
        overtone_cycles=4.2 + high * 2.4,
        overtone_phase_rate=-0.92,
        overtone_seed_rate=1.7,
        overtone_scale=0.18,
        sample_center=0.34,
        sample_scale=0.92,
    )


def solid_accent_motion_plan(response: Mapping[str, float], *, light_surface: bool) -> SolidAccentMotionPlan:
    drives = reactive_motion_drives(response)
    energy = _clamp(_number(response, "energy", 0.0))
    accent = _clamp(drives.beat * 0.30 + drives.glow * 0.42 + drives.lift * 0.16 + energy * 0.16)
    surface_guard = 0.92 if light_surface else 1.0
    return SolidAccentMotionPlan(
        alpha_amount=_clamp(_number(response, "solid_accent_alpha", 0.0)),
        edge_alpha_scale=surface_guard + accent * (0.12 if light_surface else 0.20),
        wave_alpha_scale=(0.82 if light_surface else 1.0) + accent * (0.10 if light_surface else 0.18),
        wave_amplitude_scale=1.0 + drives.beat * 0.22 + drives.lift * 0.16,
        wave_thickness_scale=1.0 + drives.glow * 0.26 + drives.lift * 0.08,
        phase_rate=1.0 + drives.beat * 0.14 + energy * 0.05,
        center_y_ratio=0.78 if light_surface else 0.76,
        center_protection=0.82 if light_surface else 0.68,
    )


def background_motion_plan(
    ambience_style: str,
    response: Mapping[str, float],
    *,
    base_flow: float = 0.0,
    frequency: float = 0.0,
    light_surface: bool = False,
) -> BackgroundReactiveMotionPlan:
    style = str(ambience_style or "")
    if style == "sea_level":
        return BackgroundReactiveMotionPlan(
            ambience_style=style,
            motion_family="layered_water",
            primary_axis="lower_water_band",
            preserves_lyric_center=True,
            sea_level=sea_level_motion_plan(response),
        )
    if style == "duotone_full":
        return BackgroundReactiveMotionPlan(
            ambience_style=style,
            motion_family="full_duotone_side_interference",
            primary_axis="side_waves_and_split_band",
            preserves_lyric_center=True,
            duotone=duotone_motion_plan(response, base_flow),
        )
    if style == "bilateral_diffraction":
        return BackgroundReactiveMotionPlan(
            ambience_style=style,
            motion_family="bilateral_frequency_waveforms",
            primary_axis="left_right_edges",
            preserves_lyric_center=True,
            bilateral=bilateral_motion_plan(response, base_flow, frequency),
        )
    if style in {"pure_black", "pure_white", "solid_album"}:
        return BackgroundReactiveMotionPlan(
            ambience_style=style,
            motion_family="solid_edge_accent",
            primary_axis="edge_and_lower_wave",
            preserves_lyric_center=True,
            solid=solid_accent_motion_plan(response, light_surface=light_surface),
        )
    return BackgroundReactiveMotionPlan(
        ambience_style=style,
        motion_family="none",
        primary_axis="none",
        preserves_lyric_center=True,
    )
