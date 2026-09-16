from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient

from local.music_reactive_plans import (
    background_motion_plan,
    bilateral_waveform_paint_plan,
    duotone_interference_paint_plan,
    duotone_side_wave_paint_plan,
    duotone_split_band_paint_plan,
    sea_level_wave_paint_plan,
)


def _copy_color(color: QColor) -> QColor:
    return QColor(color)


def _copy_color_tuple(colors: tuple[QColor, QColor, QColor] | None) -> tuple[QColor, QColor, QColor] | None:
    if colors is None:
        return None
    return tuple(_copy_color(color) for color in colors)  # type: ignore[return-value]


@dataclass(frozen=True)
class MusicReactiveRenderState:
    bands: tuple[float, float, float]
    style_responses: dict[str, dict[str, float]]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "style_responses",
            {str(style): dict(response) for style, response in self.style_responses.items()},
        )

    def response(self, ambience_style: str) -> dict[str, float]:
        return dict(self.style_responses.get(str(ambience_style or ""), {}))


@dataclass(frozen=True)
class MusicBackgroundTuning:
    ambient_intensity: float
    intensity: float
    boost: float
    size_boost: float
    opacity_boost: float
    flow_amount: float
    diffraction_frequency: float
    diffraction_ring_count: int

    @property
    def ambient_boost(self) -> tuple[float, float, float, float]:
        return (self.intensity, self.boost, self.size_boost, self.opacity_boost)


@dataclass(frozen=True)
class MusicBackgroundPalette:
    album_color: QColor
    secondary_color: QColor
    solid_album_background: QColor
    active_lyric_fill: QColor
    bilateral_secondary: QColor
    bilateral_bridge: QColor
    full_duotone: tuple[QColor, QColor, QColor] | None = None
    sea_level: tuple[QColor, QColor, QColor] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "album_color", _copy_color(self.album_color))
        object.__setattr__(self, "secondary_color", _copy_color(self.secondary_color))
        object.__setattr__(self, "solid_album_background", _copy_color(self.solid_album_background))
        object.__setattr__(self, "active_lyric_fill", _copy_color(self.active_lyric_fill))
        object.__setattr__(self, "bilateral_secondary", _copy_color(self.bilateral_secondary))
        object.__setattr__(self, "bilateral_bridge", _copy_color(self.bilateral_bridge))
        object.__setattr__(self, "full_duotone", _copy_color_tuple(self.full_duotone))
        object.__setattr__(self, "sea_level", _copy_color_tuple(self.sea_level))

    def full_duotone_palette(self) -> tuple[QColor, QColor, QColor] | None:
        return _copy_color_tuple(self.full_duotone)

    def sea_level_palette(self) -> tuple[QColor, QColor, QColor] | None:
        return _copy_color_tuple(self.sea_level)


@dataclass(frozen=True)
class MusicBackgroundRenderContext:
    reactive: MusicReactiveRenderState
    tuning: MusicBackgroundTuning
    palette: MusicBackgroundPalette
    ambient_phase: float
    wave_values: tuple[float, ...]
    media_is_playing: bool

    @property
    def bands(self) -> tuple[float, float, float]:
        return self.reactive.bands

    def response(self, ambience_style: str) -> dict[str, float]:
        return self.reactive.response(ambience_style)


class _HostBackedRenderer:
    """Transitional adapter while painter helpers still live on IslandStackVisual."""

    def __init__(self, host: Any):
        self.host = host

    def reactive_render_state(self, *ambience_styles: str) -> MusicReactiveRenderState:
        styles = tuple(dict.fromkeys(str(style or "") for style in ambience_styles if str(style or "")))
        responses = {style: self.host._music_reactive_style_response(style) for style in styles}
        return MusicReactiveRenderState(bands=self.host._audio_bands(), style_responses=responses)

    def render_context(self, *ambience_styles: str, expanded: bool | None = None) -> MusicBackgroundRenderContext:
        wave_values = getattr(self.host, "wave_values", None) or (0.25,) * 36
        return MusicBackgroundRenderContext(
            reactive=self.reactive_render_state(*ambience_styles),
            tuning=self._render_tuning(),
            palette=self._render_palette(expanded=expanded),
            ambient_phase=float(getattr(self.host, "ambient_phase", 0.0) or 0.0),
            wave_values=tuple(float(value) for value in wave_values),
            media_is_playing=bool(getattr(self.host, "media_is_playing", True)),
        )

    def _render_tuning(self) -> MusicBackgroundTuning:
        intensity, boost, size_boost, opacity_boost = self._ambient_boost_values()
        frequency = self._float_host_call("_diffraction_frequency_amount", self._float_host_attr("diffraction_frequency", 0.30))
        frequency = max(0.0, min(1.0, frequency))
        ring_default = int(round(5 + frequency * 7))
        ring_count = int(round(self._float_host_call("_bilateral_diffraction_ring_count", ring_default)))
        return MusicBackgroundTuning(
            ambient_intensity=self._float_host_attr("ambient_intensity", 0.0),
            intensity=intensity,
            boost=boost,
            size_boost=size_boost,
            opacity_boost=opacity_boost,
            flow_amount=max(0.0, min(1.0, self._float_host_call("_music_flow_amount", self._float_host_attr("music_flow", 0.0)))),
            diffraction_frequency=frequency,
            diffraction_ring_count=max(5, min(12, ring_count)),
        )

    def _render_palette(self, *, expanded: bool | None) -> MusicBackgroundPalette:
        solid = self._color_host_call("_solid_album_background_color", QColor(24, 24, 24))
        album = _copy_color(getattr(self.host, "album_color", solid))
        if not album.isValid():
            album = _copy_color(solid)
        secondary = _copy_color(getattr(self.host, "secondary_color", solid))
        if not secondary.isValid():
            secondary = _copy_color(solid)
        active_fill = self._color_host_call("_active_lyric_fill_color", secondary)
        bilateral_secondary = self._safe_wave_color_value(secondary)
        bilateral_bridge = self._mix_color_value(active_fill, bilateral_secondary, 0.46)
        full_duotone = None
        if expanded is not None:
            full_duotone = self._color_tuple_host_call(
                "_full_duotone_palette_for_state",
                self._fallback_style_palette(solid, secondary),
                bool(expanded),
            )
        sea_level = self._color_tuple_host_call("_xiaomi_duotone_palette", self._fallback_style_palette(solid, secondary))
        return MusicBackgroundPalette(
            album_color=album,
            secondary_color=secondary,
            solid_album_background=solid,
            active_lyric_fill=active_fill,
            bilateral_secondary=bilateral_secondary,
            bilateral_bridge=bilateral_bridge,
            full_duotone=full_duotone,
            sea_level=sea_level,
        )

    def _fallback_style_palette(self, primary: QColor, secondary: QColor) -> tuple[QColor, QColor, QColor]:
        return (_copy_color(primary), _copy_color(secondary), self._mix_color_value(primary, secondary, 0.40))

    def _ambient_boost_values(self) -> tuple[float, float, float, float]:
        values = self._host_call("_ambient_boost", None)
        if values is not None:
            try:
                intensity, boost, size_boost, opacity_boost = values
                return (
                    max(0.0, float(intensity)),
                    max(0.0, min(1.0, float(boost))),
                    max(0.0, float(size_boost)),
                    max(0.0, float(opacity_boost)),
                )
            except (TypeError, ValueError):
                pass
        value = max(0.0, min(1.0, self._float_host_attr("ambient_intensity", 0.0)))
        eased = value * value * (3.0 - 2.0 * value)
        return (1.04 + eased * 1.04, eased, 1.0 + eased * 0.32, 1.0 + eased * 0.56)

    def _host_call(self, name: str, default: Any) -> Any:
        func = getattr(self.host, name, None)
        if not callable(func):
            return default
        try:
            return func()
        except Exception:
            return default

    def _host_call_args(self, name: str, default: Any, *args: Any) -> Any:
        func = getattr(self.host, name, None)
        if not callable(func):
            return default
        try:
            return func(*args)
        except Exception:
            return default

    def _color_host_call(self, name: str, default: QColor, *args: Any) -> QColor:
        value = self._host_call_args(name, default, *args) if args else self._host_call(name, default)
        color = QColor(value)
        if not color.isValid():
            return _copy_color(default)
        return color

    def _color_tuple_host_call(self, name: str, default: tuple[QColor, QColor, QColor], *args: Any) -> tuple[QColor, QColor, QColor]:
        value = self._host_call_args(name, default, *args) if args else self._host_call(name, default)
        try:
            first, second, third = value
        except (TypeError, ValueError):
            return _copy_color_tuple(default) or default
        colors = (QColor(first), QColor(second), QColor(third))
        if not all(color.isValid() for color in colors):
            return _copy_color_tuple(default) or default
        return colors

    def _safe_wave_color_value(self, color: QColor) -> QColor:
        func = getattr(self.host, "_safe_wave_color", None)
        if callable(func):
            try:
                safe = QColor(func(QColor(color)))
                if safe.isValid():
                    return safe
            except Exception:
                pass
        return _copy_color(color)

    def _mix_color_value(self, a: QColor, b: QColor, amount: float) -> QColor:
        func = getattr(self.host, "_mix_color", None)
        if callable(func):
            try:
                mixed = QColor(func(QColor(a), QColor(b), amount))
                if mixed.isValid():
                    return mixed
            except Exception:
                pass
        amount = max(0.0, min(1.0, float(amount)))
        return QColor(
            int(a.red() + (b.red() - a.red()) * amount),
            int(a.green() + (b.green() - a.green()) * amount),
            int(a.blue() + (b.blue() - a.blue()) * amount),
        )

    def _float_host_call(self, name: str, default: float) -> float:
        try:
            return float(self._host_call(name, default))
        except (TypeError, ValueError):
            return float(default)

    def _float_host_attr(self, name: str, default: float) -> float:
        try:
            return float(getattr(self.host, name, default) or default)
        except (TypeError, ValueError):
            return float(default)

    def _u(self, value: float) -> float:
        func = getattr(self.host, "_u", None)
        if callable(func):
            try:
                return float(func(value))
            except (TypeError, ValueError):
                pass
        return float(value)

    def _music_shell_path(self, rect: QRectF, radius: float, expanded: bool = False) -> QPainterPath:
        func = getattr(self.host, "_music_shell_path", None)
        if callable(func):
            try:
                return func(rect, radius, expanded)
            except TypeError:
                return func(rect, radius)
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), float(radius), float(radius))
        return path

    def _mix_color(self, a: QColor, b: QColor, amount: float) -> QColor:
        return self._mix_color_value(a, b, amount)

    def _safe_wave_color(self, color: QColor) -> QColor:
        return self._safe_wave_color_value(color)

    def _boost_mist_color(self, color: QColor, boost: float) -> QColor:
        func = getattr(self.host, "_boost_mist_color", None)
        if callable(func):
            try:
                boosted = QColor(func(QColor(color), boost))
                if boosted.isValid():
                    return boosted
            except Exception:
                pass
        hue, saturation, lightness, _alpha = QColor(color).getHslF()
        if hue < 0:
            return QColor(color)
        saturation = max(0.0, min(0.92, saturation + float(boost) * 0.36))
        lightness = max(0.0, min(0.84, lightness + float(boost) * 0.15))
        return QColor.fromHslF(hue, saturation, lightness, 1.0)

    def _mist_alpha(self, opacity: float, intensity: float) -> int:
        func = getattr(self.host, "_mist_alpha", None)
        if callable(func):
            try:
                return int(func(opacity, intensity))
            except (TypeError, ValueError):
                pass
        return int(max(0, min(196, float(opacity) * 255.0 * float(intensity))))

    def _paint_mist_ellipse(
        self,
        painter: QPainter,
        center: QPointF,
        radius_x: float,
        radius_y: float,
        color: QColor,
        alpha: int,
    ) -> None:
        func = getattr(self.host, "_paint_mist_ellipse", None)
        if callable(func):
            try:
                func(painter, center, radius_x, radius_y, color, alpha)
                return
            except Exception:
                pass
        if alpha <= 0:
            return
        painter.save()
        painter.translate(center)
        painter.scale(max(1.0, radius_x), max(1.0, radius_y))
        glow = QRadialGradient(QPointF(0.0, 0.0), 1.0)
        glow.setColorAt(0.00, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.72)))
        glow.setColorAt(0.42, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.28)))
        glow.setColorAt(1.00, QColor(color.red(), color.green(), color.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(-1.0, -1.0, 2.0, 2.0))
        painter.restore()

    def _sea_level_wave_path(
        self,
        rect: QRectF,
        center_y: float,
        amplitude: float,
        thickness: float,
        phase: float,
        layer: float = 0.0,
    ) -> QPainterPath:
        points_top: list[QPointF] = []
        points_bottom: list[QPointF] = []
        count = 40
        for index in range(count):
            pos = index / (count - 1)
            x = rect.left() + rect.width() * pos
            soft_peak = math.sin(pos * math.pi)
            swell = math.sin(pos * math.tau * (1.16 + layer * 0.12) + phase + layer * 0.70)
            ripple = math.sin(pos * math.tau * (2.35 + layer * 0.18) - phase * 0.58 + layer * 1.40)
            surface = math.sin(pos * math.tau * (4.20 + layer * 0.20) + phase * 0.34 + layer * 2.10)
            level = swell * 0.58 + ripple * 0.29 + surface * 0.13
            y = center_y - amplitude * level * (0.46 + soft_peak * 0.54)
            band = thickness * (0.82 + soft_peak * 0.26 + 0.05 * math.sin(pos * math.tau * 1.55 + phase * 0.42 + layer))
            points_top.append(QPointF(x, y - band * 0.5))
            points_bottom.append(QPointF(x, y + band * 0.5))

        path = QPainterPath(points_top[0])
        for point in points_top[1:]:
            path.lineTo(point)
        for point in reversed(points_bottom):
            path.lineTo(point)
        path.closeSubpath()
        return path


class SolidMusicBackgroundRenderer(_HostBackedRenderer):
    def paint_pure_black(self, painter: QPainter, rect: QRectF, radius: float):
        context = self.render_context("pure_black")
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 255))
        painter.fillPath(self._music_shell_path(rect, radius), QColor(0, 0, 0, 255))
        self.paint_reactive_accent(painter, rect, radius, "pure_black", light_surface=False, context=context)
        painter.restore()

    def paint_pure_white(self, painter: QPainter, rect: QRectF, radius: float):
        context = self.render_context("pure_white")
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        shell = self._music_shell_path(rect, radius)
        painter.fillPath(shell, QColor(255, 255, 255, 255))
        sheen = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 82))
        sheen.setColorAt(0.55, QColor(245, 247, 250, 34))
        sheen.setColorAt(1.0, QColor(225, 229, 235, 54))
        inner = rect.adjusted(self._u(0.8), self._u(0.8), -self._u(0.8), -self._u(0.8))
        painter.fillPath(self._music_shell_path(inner, max(1.0, radius - self._u(0.8))), QBrush(sheen))
        self.paint_reactive_accent(painter, rect, radius, "pure_white", light_surface=True, context=context)
        painter.restore()

    def paint_solid_album(self, painter: QPainter, rect: QRectF, radius: float):
        context = self.render_context("solid_album")
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        shell = self._music_shell_path(rect, radius)
        base = context.palette.solid_album_background
        painter.fillPath(shell, base)
        painter.setClipPath(shell)
        sheen = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
        sheen.setColorAt(0.00, QColor(255, 255, 255, 28))
        sheen.setColorAt(0.42, QColor(255, 255, 255, 0))
        sheen.setColorAt(1.00, QColor(0, 0, 0, 38))
        painter.fillRect(rect, sheen)
        self.paint_reactive_accent(painter, rect, radius, "solid_album", light_surface=True, context=context)
        painter.restore()

    def reactive_album_accent_pair(self, light_surface: bool, context: MusicBackgroundRenderContext | None = None) -> tuple[QColor, QColor]:
        context = context or self.render_context()
        album = QColor(context.palette.album_color)
        album_hue, album_sat, album_val, _album_alpha = album.getHsvF()
        if not album.isValid() or album_hue < 0 or album_sat < 0.10 or album_val < 0.08:
            album = QColor(context.palette.solid_album_background)
        base = self._safe_wave_color(album)
        secondary = self._safe_wave_color(QColor(context.palette.secondary_color))
        hue, sat, val, _alpha = base.getHsvF()
        if hue < 0:
            hue = 0.56
        if light_surface:
            base = QColor.fromHsvF(hue, max(0.42, min(0.78, sat * 0.76)), max(0.76, min(0.97, val + 0.22)), 1.0)
            secondary_hue, secondary_sat, secondary_val, _secondary_alpha = secondary.getHsvF()
            if secondary_hue < 0:
                secondary_hue = (hue + 0.16) % 1.0
            secondary = QColor.fromHsvF(secondary_hue, max(0.36, min(0.72, secondary_sat * 0.72)), max(0.78, min(0.98, secondary_val + 0.22)), 1.0)
        else:
            base = QColor.fromHsvF(hue, max(0.58, min(0.96, sat + 0.08)), max(0.54, min(0.96, val + 0.18)), 1.0)
            secondary = self._mix_color(secondary, base, 0.22)
        return base, secondary

    def paint_reactive_accent(
        self,
        painter: QPainter,
        rect: QRectF,
        radius: float,
        ambience_style: str,
        light_surface: bool,
        context: MusicBackgroundRenderContext | None = None,
    ):
        context = context or self.render_context(ambience_style)
        reactive = context.response(ambience_style)
        motion = background_motion_plan(ambience_style, reactive, light_surface=light_surface).solid
        if motion is None:
            return
        alpha_amount = motion.alpha_amount
        if alpha_amount <= 0.001:
            return
        accent, companion = self.reactive_album_accent_pair(light_surface, context)
        shell = self._music_shell_path(rect, radius)
        painter.save()
        painter.setClipPath(shell)
        painter.setPen(Qt.PenStyle.NoPen)
        edge_alpha = int(
            max(
                0,
                min(
                    118 if not light_surface else 84,
                    alpha_amount * (150 if not light_surface else 112) * motion.edge_alpha_scale,
                ),
            )
        )
        side = QLinearGradient(rect.left(), 0, rect.right(), 0)
        side.setColorAt(0.00, QColor(accent.red(), accent.green(), accent.blue(), edge_alpha))
        side.setColorAt(0.16, QColor(accent.red(), accent.green(), accent.blue(), int(edge_alpha * 0.42)))
        side.setColorAt(0.38, QColor(accent.red(), accent.green(), accent.blue(), 0))
        side.setColorAt(0.62, QColor(companion.red(), companion.green(), companion.blue(), 0))
        side.setColorAt(0.84, QColor(companion.red(), companion.green(), companion.blue(), int(edge_alpha * 0.36)))
        side.setColorAt(1.00, QColor(companion.red(), companion.green(), companion.blue(), int(edge_alpha * 0.82)))
        painter.fillRect(rect, side)

        phase = (
            float(reactive.get("phase", 0.0))
            * (1.15 + float(reactive.get("wave_speed", 1.0)) * 0.35)
            * motion.phase_rate
            + context.ambient_phase * 0.42
        )
        center_y = rect.top() + rect.height() * motion.center_y_ratio
        amplitude = rect.height() * (0.025 + float(reactive.get("side_wave_height", 0.0)) * 0.040)
        amplitude *= motion.wave_amplitude_scale
        thickness = rect.height() * (0.070 + float(reactive.get("surface_glow", 0.0)) * 0.080)
        thickness *= motion.wave_thickness_scale
        wave_rect = rect.adjusted(self._u(9.0), 0.0, -self._u(9.0), 0.0)
        wave = self._sea_level_wave_path(wave_rect, center_y, amplitude, thickness, phase * 2.1, layer=0.35)
        wave_alpha = int(
            max(
                0,
                min(
                    82 if not light_surface else 54,
                    alpha_amount * (96 if not light_surface else 68) * motion.wave_alpha_scale,
                ),
            )
        )
        center_alpha_factor = (0.18 if light_surface else 0.28) * (1.0 - motion.center_protection * 0.25)
        wave_gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
        wave_gradient.setColorAt(0.00, QColor(accent.red(), accent.green(), accent.blue(), 0))
        wave_gradient.setColorAt(0.22, QColor(accent.red(), accent.green(), accent.blue(), int(wave_alpha * 0.78)))
        wave_gradient.setColorAt(0.52, QColor(accent.red(), accent.green(), accent.blue(), int(wave_alpha * center_alpha_factor)))
        wave_gradient.setColorAt(0.80, QColor(companion.red(), companion.green(), companion.blue(), int(wave_alpha * 0.62)))
        wave_gradient.setColorAt(1.00, QColor(companion.red(), companion.green(), companion.blue(), 0))
        painter.fillPath(wave, QBrush(wave_gradient))
        painter.restore()


class NativeMusicBackgroundRenderer(_HostBackedRenderer):
    def paint_bilateral_diffraction_ambience(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        self._paint_bilateral_diffraction_ambience(painter, rect, radius, expanded)

    def paint_full_duotone_ambience(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        self._paint_full_duotone_ambience(painter, rect, radius, expanded)

    def paint_sea_level_ambience(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        self._paint_xiaomi_duotone_ambience(painter, rect, radius, expanded)

    def paint_sea_level_wave(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        self._paint_xiaomi_duotone_wave(painter, rect, radius, expanded)

    def music_wave_path(self, rect: QRectF, center_y: float, amplitude: float, thickness: float, phase: float) -> QPainterPath:
        return self._music_wave_path(rect, center_y, amplitude, thickness, phase)

    def sea_level_wave_path(self, rect: QRectF, center_y: float, amplitude: float, thickness: float, phase: float, layer: float = 0.0) -> QPainterPath:
        return self._sea_level_wave_path(rect, center_y, amplitude, thickness, phase, layer)

    def _paint_bilateral_diffraction_ambience(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        painter.save()
        inset = self._u(1.2)
        bleed = self._u(1.0)
        paint_rect = rect.adjusted(-bleed, -bleed, bleed, bleed)
        clip_rect = rect.adjusted(inset - bleed, inset - bleed, -inset + bleed, -inset + bleed)
        painter.setClipPath(self._music_shell_path(clip_rect, max(1.0, radius - inset + bleed), expanded))
        painter.setPen(Qt.PenStyle.NoPen)

        context = self.render_context("bilateral_diffraction", expanded=expanded)
        fill = context.palette.active_lyric_fill
        secondary = context.palette.bilateral_secondary
        bridge = context.palette.bilateral_bridge
        low, mid, high = context.bands
        intensity, boost, size_boost, opacity_boost = context.tuning.ambient_boost
        reactive = context.response("bilateral_diffraction")
        low = max(low, float(reactive["low"]))
        mid = max(mid, float(reactive["mid"]))
        high = max(high, float(reactive["high"]))
        frequency = context.tuning.diffraction_frequency
        inward_offset = rect.width() * (0.035 + boost * (0.19 if expanded else 0.14))
        flow = 0.42 + context.tuning.flow_amount * 0.58
        motion = background_motion_plan(
            "bilateral_diffraction",
            reactive,
            base_flow=flow,
            frequency=frequency,
        ).bilateral
        if motion is None:
            painter.restore()
            return
        flow = motion.flow
        if not context.media_is_playing:
            intensity *= 0.76
            flow *= 0.42
        fill = self._boost_mist_color(fill, boost * 0.52 + float(reactive["color_boost"]) * 0.60)
        secondary = self._boost_mist_color(secondary, boost * 0.34 + float(reactive["color_boost"]) * 0.42)
        bridge = self._boost_mist_color(bridge, boost * 0.28 + float(reactive["color_boost"]) * 0.34)

        now = time.monotonic()
        phase = (
            context.ambient_phase * (0.92 + flow * 0.48)
            + now * (0.28 + flow * 0.36) * float(reactive["wave_speed"]) * motion.phase_rate
        )
        values = list(context.wave_values)

        center = rect.center() + QPointF(self._u(8.0) * flow * math.sin(phase * 0.56), self._u(2.8) * math.sin(phase * 0.41 + 1.7))
        center_radius = rect.width() * (0.56 if expanded else 0.46)
        center_glow = QRadialGradient(center, center_radius)
        center_alpha = self._mist_alpha(
            ((0.020 if expanded else 0.014) * opacity_boost + mid * 0.006) * motion.center_glow_scale,
            intensity,
        )
        center_glow.setColorAt(0.00, QColor(bridge.red(), bridge.green(), bridge.blue(), center_alpha))
        center_glow.setColorAt(0.34, QColor(bridge.red(), bridge.green(), bridge.blue(), int(center_alpha * 0.42)))
        center_glow.setColorAt(0.68, QColor(0, 0, 0, 0))
        center_glow.setColorAt(1.00, QColor(0, 0, 0, 0))
        painter.fillRect(paint_rect, center_glow)

        side_specs = (
            (-1, fill, bridge, low, 0.0),
            (1, secondary, bridge, high, 2.1),
        )
        for side, side_color, halo_color, band, seed in side_specs:
            side_x = rect.left() - rect.width() * (0.055 if expanded else 0.115) if side < 0 else rect.right() + rect.width() * (0.055 if expanded else 0.115)
            side_x -= side * inward_offset * 0.55
            side_y = rect.center().y() + self._u(7.0) * flow * math.sin(phase * 0.72 + seed)
            wash_alpha = self._mist_alpha(
                ((0.115 if expanded else 0.094) * opacity_boost + band * 0.028) * motion.side_wash_scale,
                intensity,
            )
            if expanded:
                wash_rx = rect.width() * (0.28 * size_boost + band * 0.026) * motion.side_wash_scale
                wash_ry = rect.height() * (1.26 * size_boost + mid * 0.044) * motion.side_wash_scale
                halo_rx = rect.width() * (0.18 + band * 0.018) * motion.side_wash_scale
                halo_ry = rect.height() * (0.72 + mid * 0.036) * motion.side_wash_scale
            else:
                wash_rx = wash_ry = rect.height() * (1.18 * size_boost + band * 0.12 + mid * 0.06) * motion.side_wash_scale
                halo_rx = halo_ry = rect.height() * (0.82 + band * 0.08 + mid * 0.04) * motion.side_wash_scale
            self._paint_mist_ellipse(
                painter,
                QPointF(side_x, side_y),
                wash_rx,
                wash_ry,
                side_color,
                wash_alpha,
            )
            self._paint_mist_ellipse(
                painter,
                QPointF(side_x + side * self._u(10.0), side_y),
                halo_rx,
                halo_ry,
                halo_color,
                int(wash_alpha * 0.44),
            )

        ring_count = context.tuning.diffraction_ring_count
        for side, side_color, _halo_color, band, seed in side_specs:
            center_x = rect.left() - rect.width() * (0.030 if expanded else 0.130) if side < 0 else rect.right() + rect.width() * (0.030 if expanded else 0.130)
            center_x -= side * inward_offset
            center_y = rect.center().y() + self._u(5.5) * flow * math.sin(phase * 0.60 + seed)
            for index in range(ring_count):
                wave_sample = float(values[(index * 5 + (0 if side < 0 else 2)) % len(values)])
                travel = (
                    now * (0.060 + flow * 0.075 + frequency * 0.125) * motion.ring_speed_scale
                    + index / ring_count
                    + seed * 0.061
                ) % 1.0
                falloff = (1.0 - travel) ** 1.36
                if expanded:
                    rx = rect.width() * (0.105 + travel * 0.42)
                    ry = rect.height() * (0.48 + travel * 1.04)
                    rx += self._u(8.0) * flow * wave_sample * motion.ring_width_scale
                    ry += self._u(7.0) * flow * (0.34 + band) * motion.ring_width_scale
                else:
                    ring_radius = rect.height() * (0.62 + travel * 1.42)
                    ring_radius += self._u(10.0) * flow * (0.34 + band + wave_sample * 0.55) * motion.ring_width_scale
                    rx = ry = ring_radius
                alpha = int(max(0, min(132, falloff * (0.36 + band * 0.22 + wave_sample * 0.10) * 255.0 * intensity * opacity_boost * motion.ring_alpha_scale)))
                if not expanded and alpha <= 2:
                    alpha = 3
                if alpha <= 2:
                    continue
                tint = self._mix_color(side_color, bridge, 0.28 + 0.22 * travel)
                pen_color = QColor(tint.red(), tint.green(), tint.blue(), alpha)
                pen_width = self._u(1.0 + (0.9 if expanded else 0.55) * (1.0 - travel) + wave_sample * 0.36) * motion.ring_width_scale
                painter.setPen(QPen(pen_color, pen_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                arc_rect = QRectF(center_x - rx, center_y - ry, rx * 2.0, ry * 2.0)
                if side < 0:
                    painter.drawArc(arc_rect, -90 * 16, 180 * 16)
                else:
                    painter.drawArc(arc_rect, 90 * 16, 180 * 16)

        self._paint_bilateral_reactive_waveforms(
            painter,
            rect,
            fill,
            secondary,
            bridge,
            intensity,
            opacity_boost,
            flow,
            phase,
            values,
            reactive,
            frequency,
            motion.waveform_alpha_scale,
            motion.waveform_amplitude_scale,
            expanded,
        )

        edge_shadow = QLinearGradient(rect.left(), 0, rect.right(), 0)
        edge_shadow.setColorAt(0.00, QColor(0, 0, 0, 50))
        edge_shadow.setColorAt(0.18, QColor(0, 0, 0, 0))
        edge_shadow.setColorAt(0.50, QColor(0, 0, 0, 0))
        edge_shadow.setColorAt(0.82, QColor(0, 0, 0, 0))
        edge_shadow.setColorAt(1.00, QColor(0, 0, 0, 50))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(edge_shadow)
        painter.fillRect(paint_rect, edge_shadow)
        painter.restore()

    def _paint_bilateral_reactive_waveforms(
        self,
        painter: QPainter,
        rect: QRectF,
        fill: QColor,
        secondary: QColor,
        bridge: QColor,
        intensity: float,
        opacity_boost: float,
        flow: float,
        phase: float,
        values: list[float],
        reactive: dict[str, float],
        frequency: float,
        alpha_scale: float,
        amplitude_scale: float,
        expanded: bool,
    ):
        waveform_plan = bilateral_waveform_paint_plan(
            reactive,
            frequency=frequency,
            expanded=expanded,
            intensity=intensity,
            opacity_boost=opacity_boost,
            alpha_scale=alpha_scale,
            amplitude_scale=amplitude_scale,
        )
        if not waveform_plan.enabled:
            return
        count = waveform_plan.point_count
        y_top = rect.top() + rect.height() * waveform_plan.y_top_ratio
        y_bottom = rect.bottom() - rect.height() * waveform_plan.y_bottom_inset_ratio
        amplitude = rect.width() * waveform_plan.amplitude_ratio
        alpha = waveform_plan.alpha

        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for side, color, seed in ((-1, fill, 0.0), (1, secondary, 2.35)):
            base_x = (
                rect.left() + rect.width() * waveform_plan.side_anchor_ratio
                if side < 0
                else rect.right() - rect.width() * waveform_plan.side_anchor_ratio
            )
            path = QPainterPath()
            for index in range(count):
                pos = index / max(1, count - 1)
                y = y_top + (y_bottom - y_top) * pos
                sample = values[(index * 3 + (0 if side < 0 else 5)) % len(values)] if values else 0.25
                carrier = math.sin(pos * math.tau * waveform_plan.carrier_cycles + phase * waveform_plan.carrier_phase_rate + seed)
                overtone = math.sin(
                    pos * math.tau * waveform_plan.overtone_cycles
                    + phase * waveform_plan.overtone_phase_rate
                    + seed * waveform_plan.overtone_seed_rate
                )
                envelope = math.sin(pos * math.pi)
                displacement = amplitude * envelope * (
                    (sample - waveform_plan.sample_center) * waveform_plan.sample_scale
                    + carrier * waveform_plan.carrier_scale
                    + overtone * waveform_plan.overtone_scale
                )
                x = base_x - side * displacement
                point = QPointF(x, y)
                if index == 0:
                    path.moveTo(point)
                else:
                    path.lineTo(point)
            tint = self._mix_color(color, bridge, 0.24)
            soft = QColor(tint.red(), tint.green(), tint.blue(), int(alpha * 0.34))
            strong = QColor(color.red(), color.green(), color.blue(), alpha)
            painter.setPen(QPen(soft, self._u(waveform_plan.halo_width_units), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(path)
            painter.setPen(QPen(strong, self._u(waveform_plan.line_width_units), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(path)
        painter.restore()

    def _paint_full_duotone_ambience(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        painter.save()
        inset = self._u(1.4)
        bleed = self._u(1.0)
        paint_rect = rect.adjusted(-bleed, -bleed, bleed, bleed)
        clip_rect = rect.adjusted(inset - bleed, inset - bleed, -inset + bleed, -inset + bleed)
        painter.setClipPath(self._music_shell_path(clip_rect, max(1.0, radius - inset + bleed), expanded))
        painter.setPen(Qt.PenStyle.NoPen)

        context = self.render_context("duotone_full", expanded=expanded)
        main, accent, bridge = context.palette.full_duotone_palette() or context.palette.sea_level_palette()
        low, mid, high = context.bands
        now = time.monotonic()
        intensity, boost, size_boost, opacity_boost = context.tuning.ambient_boost
        flow = context.tuning.flow_amount
        reactive = context.response("duotone_full")
        motion = background_motion_plan("duotone_full", reactive, base_flow=flow).duotone
        if motion is None:
            painter.restore()
            return
        if not context.media_is_playing:
            intensity *= 0.84
            flow *= 0.50
        main = self._boost_mist_color(main, boost * 0.58 + reactive["color_boost"] * 0.80)
        accent = self._boost_mist_color(accent, reactive["color_boost"] * 0.32)
        bridge = self._boost_mist_color(bridge, boost * 0.34 + reactive["color_boost"] * 0.46)

        def solid_alpha(opacity: float) -> int:
            return int(max(0, min(236, opacity * 255.0 * intensity)))

        band_plan = duotone_split_band_paint_plan(reactive, transition_height_scale=motion.transition_height_scale)
        base = QLinearGradient(0, paint_rect.top(), 0, paint_rect.bottom())
        base.setColorAt(0.00, QColor(accent.red(), accent.green(), accent.blue(), solid_alpha(0.90 + high * 0.024)))
        base.setColorAt(band_plan.upper_solid_stop, QColor(accent.red(), accent.green(), accent.blue(), solid_alpha(0.97 + high * 0.026)))
        base.setColorAt(band_plan.bridge_in_stop, QColor(bridge.red(), bridge.green(), bridge.blue(), solid_alpha(0.98 + mid * 0.024 + reactive["energy"] * 0.018)))
        base.setColorAt(band_plan.bridge_out_stop, QColor(bridge.red(), bridge.green(), bridge.blue(), solid_alpha(0.98 + mid * 0.024 + reactive["energy"] * 0.018)))
        base.setColorAt(band_plan.lower_solid_stop, QColor(main.red(), main.green(), main.blue(), solid_alpha(0.98 + low * 0.026 + reactive["bass"] * 0.020)))
        base.setColorAt(1.00, QColor(main.red(), main.green(), main.blue(), solid_alpha(1.00)))
        painter.fillRect(paint_rect, base)

        def wave(duration: float, delay: float, offset: float = 0.0) -> float:
            return math.sin(((now + delay) / duration) * math.tau + offset)

        def breathe(duration: float, delay: float, offset: float = 0.0) -> float:
            return 0.5 + 0.5 * wave(duration, delay, offset)

        transition_top = paint_rect.top() + paint_rect.height() * band_plan.transition_top_ratio
        transition_height = paint_rect.height() * band_plan.transition_height_ratio
        transition = QLinearGradient(paint_rect.left(), transition_top, paint_rect.right(), transition_top + transition_height)
        transition.setColorAt(0.00, QColor(accent.red(), accent.green(), accent.blue(), 0))
        transition.setColorAt(0.30, QColor(accent.red(), accent.green(), accent.blue(), solid_alpha((0.036 + high * 0.008) * motion.transition_alpha_scale)))
        transition.setColorAt(0.50, QColor(bridge.red(), bridge.green(), bridge.blue(), solid_alpha((0.092 + mid * 0.016 + reactive["energy"] * 0.020) * motion.transition_alpha_scale)))
        transition.setColorAt(0.70, QColor(main.red(), main.green(), main.blue(), solid_alpha((0.060 + low * 0.012 + reactive["bass"] * 0.018) * motion.transition_alpha_scale)))
        transition.setColorAt(1.00, QColor(main.red(), main.green(), main.blue(), 0))
        painter.fillRect(QRectF(paint_rect.left(), transition_top, paint_rect.width(), transition_height), transition)

        shadow = QLinearGradient(0, paint_rect.top(), 0, paint_rect.bottom())
        shadow.setColorAt(0.00, QColor(0, 0, 0, 48 if expanded else 42))
        shadow.setColorAt(0.12, QColor(0, 0, 0, 32 if expanded else 28))
        shadow.setColorAt(0.32, QColor(0, 0, 0, 10))
        shadow.setColorAt(0.62, QColor(0, 0, 0, 4))
        shadow.setColorAt(1.00, QColor(0, 0, 0, 16))
        painter.fillRect(paint_rect, shadow)

        sheen = QLinearGradient(paint_rect.left(), paint_rect.top(), paint_rect.right(), paint_rect.bottom())
        sheen.setColorAt(0.00, QColor(255, 255, 255, 22))
        sheen.setColorAt(0.28, QColor(255, 255, 255, 5))
        sheen.setColorAt(0.54, QColor(255, 255, 255, 0))
        sheen.setColorAt(1.00, QColor(255, 255, 255, 12))
        painter.fillRect(paint_rect, sheen)

        self._paint_duotone_reactive_side_waves(
            painter,
            rect,
            main,
            accent,
            bridge,
            intensity,
            opacity_boost,
            motion.side_flow,
            now,
            reactive,
            context.ambient_phase,
            context.wave_values,
            motion.side_alpha_scale,
            motion.side_amplitude_scale,
            expanded,
        )

        self._paint_full_duotone_interference(
            painter,
            rect,
            main,
            accent,
            bridge,
            intensity,
            opacity_boost * reactive["interference_alpha"] * motion.interference_alpha_scale,
            motion.interference_flow,
            now,
            low,
            mid,
            high,
        )

        painter.restore()

    def _paint_duotone_reactive_side_waves(
        self,
        painter: QPainter,
        rect: QRectF,
        main: QColor,
        accent: QColor,
        bridge: QColor,
        intensity: float,
        opacity_boost: float,
        flow: float,
        now: float,
        reactive: dict[str, float],
        ambient_phase: float,
        wave_values: tuple[float, ...],
        alpha_scale: float,
        amplitude_scale: float,
        expanded: bool,
    ):
        wave_plan = duotone_side_wave_paint_plan(
            reactive,
            flow=flow,
            expanded=expanded,
            intensity=intensity,
            opacity_boost=opacity_boost,
            alpha_scale=alpha_scale,
            amplitude_scale=amplitude_scale,
        )
        if not wave_plan.enabled:
            return
        phase = float(reactive["phase"]) * wave_plan.phase_response_rate + now * wave_plan.phase_time_rate + ambient_phase * wave_plan.phase_ambient_rate
        count = wave_plan.point_count
        alpha = wave_plan.alpha
        y_top = rect.top() + rect.height() * wave_plan.y_top_ratio
        y_bottom = rect.bottom() - rect.height() * wave_plan.y_bottom_inset_ratio
        amplitude = rect.width() * wave_plan.amplitude_ratio
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        values = list(wave_values or (0.25,) * 36)
        for side, color, seed in ((-1, accent, 0.4), (1, main, 2.1)):
            base_x = rect.left() + rect.width() * wave_plan.side_anchor_ratio if side < 0 else rect.right() - rect.width() * wave_plan.side_anchor_ratio
            path = QPainterPath()
            for index in range(count):
                pos = index / max(1, count - 1)
                y = y_top + (y_bottom - y_top) * pos
                sample = values[(index * 4 + (1 if side < 0 else 6)) % len(values)]
                body = math.sin(pos * math.tau * 1.55 + phase + seed) * 0.48
                detail = math.sin(pos * math.tau * 3.4 - phase * 0.72 + seed) * 0.18
                envelope = 0.32 + 0.68 * math.sin(pos * math.pi)
                x = base_x - side * amplitude * envelope * ((sample - 0.30) * 0.62 + body + detail)
                point = QPointF(x, y)
                if index == 0:
                    path.moveTo(point)
                else:
                    path.lineTo(point)
            tint = self._mix_color(color, bridge, 0.26)
            painter.setPen(QPen(QColor(tint.red(), tint.green(), tint.blue(), int(alpha * 0.46)), self._u(wave_plan.halo_width_units), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(path)
            painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), alpha), self._u(wave_plan.line_width_units), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(path)
        painter.restore()

    def _paint_full_duotone_interference(
        self,
        painter: QPainter,
        rect: QRectF,
        main: QColor,
        accent: QColor,
        bridge: QColor,
        intensity: float,
        opacity_boost: float,
        flow: float,
        now: float,
        low: float,
        mid: float,
        high: float,
    ):
        plan = duotone_interference_paint_plan(
            flow=flow,
            now=now,
            bands=(low, mid, high),
            intensity=intensity,
            opacity_boost=opacity_boost,
        )
        if not plan.enabled:
            return
        for blob in plan.blobs:
            if blob.role == "accent":
                color = accent
            elif blob.role == "main":
                color = main
            elif blob.role == "mix":
                color = self._mix_color(accent, main, blob.color_mix_ratio)
                color = self._mix_color(color, bridge, blob.bridge_mix_ratio)
            elif blob.role == "highlight":
                color = self._mix_color(QColor(255, 255, 255), bridge, blob.bridge_mix_ratio)
            else:
                continue
            center = QPointF(
                rect.left() + rect.width() * blob.center_x_ratio,
                rect.top() + rect.height() * blob.center_y_ratio,
            )
            self._paint_interference_blob(
                painter,
                center,
                rect.width() * blob.radius_x_ratio,
                rect.height() * blob.radius_y_ratio,
                color,
                blob.alpha,
            )

    def _paint_interference_blob(self, painter: QPainter, center: QPointF, radius_x: float, radius_y: float, color: QColor, alpha: int):
        if alpha <= 0:
            return
        painter.save()
        painter.translate(center)
        painter.scale(max(1.0, radius_x), max(1.0, radius_y))
        glow = QRadialGradient(QPointF(0.0, 0.0), 1.0)
        glow.setColorAt(0.00, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.58)))
        glow.setColorAt(0.24, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.50)))
        glow.setColorAt(0.52, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.30)))
        glow.setColorAt(0.78, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.115)))
        glow.setColorAt(1.00, QColor(color.red(), color.green(), color.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(-1.0, -1.0, 2.0, 2.0))
        painter.restore()

    def _paint_full_duotone_orbs(
        self,
        painter: QPainter,
        rect: QRectF,
        main: QColor,
        accent: QColor,
        bridge: QColor,
        intensity: float,
        opacity_boost: float,
        flow: float,
        now: float,
        low: float,
        mid: float,
        high: float,
    ):
        if flow <= 0.01:
            return
        specs = (
            (0.0, 19.0, 27.0, 35.0, 0.28, 0.40, 0.28, 0.30, low, accent, main, 0.10),
            (6.4, 24.0, 31.0, 41.0, 0.70, 0.60, 0.32, 0.34, high, main, accent, 0.08),
            (13.2, 30.0, 23.0, 47.0, 0.50, 0.50, 0.24, 0.28, mid, bridge, main, 0.06),
        )
        for seed, dx_period, dy_period, pulse_period, base_x, base_y, width_ratio, height_ratio, band, base_color, tint_color, tint_amount in specs:
            x = base_x
            x += flow * (0.23 * math.sin(now / dx_period * math.tau + seed) + 0.11 * math.sin(now / (dx_period * 0.47) * math.tau + seed * 2.4))
            y = base_y
            y += flow * (0.18 * math.sin(now / dy_period * math.tau + seed * 1.6) + 0.10 * math.sin(now / (dy_period * 0.53) * math.tau + seed * 3.1))
            pulse = 0.5 + 0.5 * math.sin(now / pulse_period * math.tau + seed * 0.9)
            slow_tint = tint_amount * (0.5 + 0.5 * math.sin(now / (pulse_period * 1.85) * math.tau + seed * 0.37))
            orb_color = self._mix_color(base_color, tint_color, slow_tint)
            mixed_color = self._mix_color(orb_color, bridge, 0.12 + 0.16 * pulse)
            center = QPointF(
                rect.left() + rect.width() * max(-0.08, min(1.08, x)),
                rect.top() + rect.height() * max(-0.10, min(1.10, y)),
            )
            rx = rect.width() * (width_ratio + pulse * 0.060 + band * 0.018)
            ry = rect.height() * (height_ratio + pulse * 0.054 + mid * 0.012)
            alpha = int(max(0, min(118, (0.155 + pulse * 0.042 + band * 0.026) * 255.0 * intensity * opacity_boost)))
            self._paint_background_blob(painter, center, rx, ry, mixed_color, alpha)

    def _paint_background_blob(self, painter: QPainter, center: QPointF, radius_x: float, radius_y: float, color: QColor, alpha: int):
        if alpha <= 0:
            return
        painter.save()
        painter.translate(center)
        painter.scale(max(1.0, radius_x), max(1.0, radius_y))
        glow = QRadialGradient(QPointF(0.0, 0.0), 1.0)
        glow.setColorAt(0.00, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.46)))
        glow.setColorAt(0.30, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.34)))
        glow.setColorAt(0.62, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.16)))
        glow.setColorAt(0.84, QColor(color.red(), color.green(), color.blue(), int(alpha * 0.045)))
        glow.setColorAt(1.00, QColor(color.red(), color.green(), color.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(-1.0, -1.0, 2.0, 2.0))
        painter.restore()

    def _paint_xiaomi_duotone_ambience(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        painter.save()
        inset = self._u(1.4)
        bleed = self._u(1.0)
        paint_rect = rect.adjusted(-bleed, -bleed, bleed, bleed)
        clip_rect = rect.adjusted(inset - bleed, inset - bleed, -inset + bleed, -inset + bleed)
        painter.setClipPath(self._music_shell_path(clip_rect, max(1.0, radius - inset + bleed), expanded))
        painter.setPen(Qt.PenStyle.NoPen)

        context = self.render_context("sea_level", expanded=expanded)
        main, accent, bridge = context.palette.sea_level_palette()
        low, mid, high = context.bands
        now = time.monotonic()
        intensity, boost, size_boost, opacity_boost = context.tuning.ambient_boost
        flow = context.tuning.flow_amount
        reactive = context.response("sea_level")
        motion = background_motion_plan("sea_level", reactive).sea_level
        if motion is None:
            painter.restore()
            return
        if not context.media_is_playing:
            intensity *= 0.80
            flow *= 0.45
        main = self._boost_mist_color(main, boost * 0.72 + reactive["color_boost"] * 0.82)
        accent = self._boost_mist_color(accent, reactive["color_boost"] * 0.22)
        bridge = self._boost_mist_color(bridge, boost * 0.42 + reactive["color_boost"] * 0.45)

        split = paint_rect.top() + paint_rect.height() * ((0.34 if expanded else 0.38) + reactive["split_shift"])
        body = QLinearGradient(0, paint_rect.top(), 0, paint_rect.bottom())
        body.setColorAt(0.00, QColor(0, 0, 0, 0))
        body.setColorAt(0.22, QColor(accent.red(), accent.green(), accent.blue(), self._mist_alpha(0.044 * opacity_boost, intensity)))
        body.setColorAt(0.36, QColor(accent.red(), accent.green(), accent.blue(), self._mist_alpha(0.096 * opacity_boost + mid * 0.020, intensity)))
        body.setColorAt(0.52, QColor(bridge.red(), bridge.green(), bridge.blue(), self._mist_alpha((0.150 + reactive["energy"] * 0.018) * opacity_boost + high * 0.026, intensity)))
        body.setColorAt(0.76, QColor(main.red(), main.green(), main.blue(), self._mist_alpha((0.245 * reactive["lower_alpha"]) * opacity_boost + low * 0.040, intensity)))
        body.setColorAt(1.00, QColor(main.red(), main.green(), main.blue(), self._mist_alpha((0.225 * reactive["lower_alpha"]) * opacity_boost + mid * 0.034, intensity)))
        painter.fillRect(paint_rect, body)

        def wave(duration: float, delay: float, offset: float = 0.0) -> float:
            return math.sin(((now + delay) / duration) * math.tau + offset)

        def breathe(duration: float, delay: float, offset: float = 0.0) -> float:
            return 0.5 + 0.5 * wave(duration, delay, offset)

        flows = (
            (accent, 19.0, 0.0, 0.30, 0.34, 34.0, 13.0, 0.82, 0.28, 0.115, 0.074, mid, 0.0),
            (main, 15.5, -3.0, 0.70, 0.72, 42.0, 16.0, 1.08, 0.44, 0.230, 0.126, low, 1.1),
            (bridge, 22.0, -7.0, 0.48, 0.58, 38.0, 15.0, 1.12, 0.38, 0.172, 0.104, high, 2.4),
            (main, 28.0, -11.0, 0.22, 0.82, 26.0, 10.0, 0.72, 0.26, 0.140, 0.082, mid, 3.5),
            (accent, 31.0, -14.0, 0.78, 0.42, 24.0, 9.0, 0.58, 0.20, 0.070, 0.050, high, 4.4),
        )
        for color, duration, delay, x_ratio, y_ratio, drift_x, drift_y, width_ratio, height_ratio, base_opacity, opacity_span, band, offset in flows:
            breath = breathe(duration, delay, offset)
            shimmer = breathe(duration * 1.6, delay * 0.7, offset + 1.8)
            lower_weight = max(0.0, min(1.0, (y_ratio - 0.42) / 0.40))
            native_alpha = 1.0 + (reactive["lower_alpha"] - 1.0) * lower_weight
            native_scale = 1.0 + (reactive["lower_scale"] - 1.0) * lower_weight
            center = QPointF(
                rect.left() + rect.width() * x_ratio + self._u(drift_x) * flow * wave(duration, delay, offset + 0.6),
                rect.top() + rect.height() * (y_ratio + reactive["split_shift"] * lower_weight + motion.center_shift * lower_weight)
                + self._u(drift_y) * flow * wave(duration, delay, offset + 2.0),
            )
            alpha = self._mist_alpha((base_opacity + opacity_span * shimmer + band * 0.030) * native_alpha, intensity)
            rx = rect.width() * (width_ratio * size_boost * native_scale + breath * 0.08 + band * 0.016)
            ry = rect.height() * (height_ratio * size_boost * native_scale + breath * 0.042 + band * 0.012)
            self._paint_mist_ellipse(painter, center, rx * 1.35, ry * 1.30, color, int(alpha * 0.34))
            self._paint_mist_ellipse(painter, center, rx, ry, color, alpha)

        join = QLinearGradient(0, split - rect.height() * 0.18, 0, split + rect.height() * 0.34)
        join.setColorAt(0.00, QColor(accent.red(), accent.green(), accent.blue(), 0))
        join.setColorAt(0.38, QColor(accent.red(), accent.green(), accent.blue(), self._mist_alpha(0.054 * opacity_boost, intensity)))
        join.setColorAt(0.56, QColor(bridge.red(), bridge.green(), bridge.blue(), self._mist_alpha(0.092 * opacity_boost + high * 0.018, intensity)))
        join.setColorAt(0.78, QColor(main.red(), main.green(), main.blue(), self._mist_alpha(0.050 * (reactive["lower_alpha"] - 1.0) * opacity_boost, intensity)))
        join.setColorAt(1.00, QColor(main.red(), main.green(), main.blue(), 0))
        painter.fillRect(QRectF(paint_rect.left(), split - paint_rect.height() * 0.18, paint_rect.width(), paint_rect.height() * 0.56), join)

        top_fade = QLinearGradient(0, paint_rect.top(), 0, paint_rect.bottom())
        guard = reactive["top_guard"]
        top_fade.setColorAt(0.00, QColor(0, 0, 0, min(250, (238 if expanded else 230) + int(guard * 42))))
        top_fade.setColorAt(0.17, QColor(0, 0, 0, min(224, (206 if expanded else 190) + int(guard * 38))))
        top_fade.setColorAt(0.30, QColor(0, 0, 0, min(134, (116 if expanded else 104) + int(guard * 24))))
        top_fade.setColorAt(0.43, QColor(0, 0, 0, min(40, 30 + int(guard * 16))))
        top_fade.setColorAt(0.62, QColor(0, 0, 0, 0))
        top_fade.setColorAt(1.00, QColor(0, 0, 0, 0))
        painter.fillRect(paint_rect, top_fade)

        painter.restore()

    def _music_wave_path(self, rect: QRectF, center_y: float, amplitude: float, thickness: float, phase: float) -> QPainterPath:
        values = self.wave_values or [0.25] * 36
        points_top: list[QPointF] = []
        points_bottom: list[QPointF] = []
        count = max(2, len(values))
        for i, value in enumerate(values):
            pos = i / (count - 1)
            x = rect.left() + rect.width() * pos
            soft_peak = math.sin(pos * math.pi)
            ripple = math.sin(pos * math.tau * 2.0 + phase) * 0.30 + math.sin(pos * math.tau * 4.2 - phase * 0.72) * 0.16
            level = (value - 0.42) * 1.24 + ripple
            y = center_y - amplitude * level * (0.25 + soft_peak * 0.75)
            band = thickness * (0.72 + soft_peak * 0.40 + max(0.0, value - 0.25) * 0.34)
            points_top.append(QPointF(x, y - band * 0.5))
            points_bottom.append(QPointF(x, y + band * 0.5))

        path = QPainterPath(points_top[0])
        for point in points_top[1:]:
            path.lineTo(point)
        for point in reversed(points_bottom):
            path.lineTo(point)
        path.closeSubpath()
        return path

    def _sea_level_wave_path(self, rect: QRectF, center_y: float, amplitude: float, thickness: float, phase: float, layer: float = 0.0) -> QPainterPath:
        points_top: list[QPointF] = []
        points_bottom: list[QPointF] = []
        count = 40
        for index in range(count):
            pos = index / (count - 1)
            x = rect.left() + rect.width() * pos
            soft_peak = math.sin(pos * math.pi)
            swell = math.sin(pos * math.tau * (1.16 + layer * 0.12) + phase + layer * 0.70)
            ripple = math.sin(pos * math.tau * (2.35 + layer * 0.18) - phase * 0.58 + layer * 1.40)
            surface = math.sin(pos * math.tau * (4.20 + layer * 0.20) + phase * 0.34 + layer * 2.10)
            level = swell * 0.58 + ripple * 0.29 + surface * 0.13
            y = center_y - amplitude * level * (0.46 + soft_peak * 0.54)
            band = thickness * (0.82 + soft_peak * 0.26 + 0.05 * math.sin(pos * math.tau * 1.55 + phase * 0.42 + layer))
            points_top.append(QPointF(x, y - band * 0.5))
            points_bottom.append(QPointF(x, y + band * 0.5))

        path = QPainterPath(points_top[0])
        for point in points_top[1:]:
            path.lineTo(point)
        for point in reversed(points_bottom):
            path.lineTo(point)
        path.closeSubpath()
        return path

    def _paint_xiaomi_duotone_wave(self, painter: QPainter, rect: QRectF, radius: float, expanded: bool):
        painter.save()
        inset = self._u(1.4)
        painter.setClipPath(self._music_shell_path(rect.adjusted(inset, inset, -inset, -inset), max(1.0, radius - inset), expanded))
        painter.setPen(Qt.PenStyle.NoPen)

        context = self.render_context("sea_level", expanded=expanded)
        main, accent, bridge = context.palette.sea_level_palette()
        low, mid, high = context.bands
        intensity, boost, _size_boost, opacity_boost = context.tuning.ambient_boost
        reactive = context.response("sea_level")
        wave_plan = sea_level_wave_paint_plan(
            reactive,
            bands=context.bands,
            flow=context.tuning.flow_amount,
            expanded=expanded,
            media_is_playing=context.media_is_playing,
        )
        flow = wave_plan.flow
        if not context.media_is_playing:
            intensity *= 0.70
        main = self._boost_mist_color(main, boost * 0.60 + reactive["color_boost"] * 0.86)
        accent = self._boost_mist_color(accent, reactive["color_boost"] * 0.20)
        bridge = self._boost_mist_color(bridge, boost * 0.38 + reactive["color_boost"] * 0.48)

        now = time.monotonic()
        phase = context.ambient_phase * wave_plan.phase_ambient_rate + now * wave_plan.phase_time_rate
        center_y = rect.top() + rect.height() * wave_plan.center_y_ratio
        center_y += self._u(wave_plan.center_flow_units) * math.sin(phase * 0.68) + self._u(wave_plan.center_band_units)
        center_y += rect.height() * wave_plan.center_shift_ratio

        glow_h = rect.height() * wave_plan.glow_height_ratio + self._u(wave_plan.glow_flow_units)
        glow_rect = QRectF(rect.left(), center_y - glow_h * 0.38, rect.width(), glow_h * 1.48)
        wash = QLinearGradient(0, glow_rect.top(), 0, glow_rect.bottom())
        wash.setColorAt(0.00, QColor(accent.red(), accent.green(), accent.blue(), 0))
        wash.setColorAt(0.22, QColor(accent.red(), accent.green(), accent.blue(), self._mist_alpha(0.026 * opacity_boost + high * 0.008, intensity)))
        wash.setColorAt(0.50, QColor(bridge.red(), bridge.green(), bridge.blue(), self._mist_alpha(0.090 * opacity_boost + mid * 0.020, intensity)))
        wash.setColorAt(0.78, QColor(main.red(), main.green(), main.blue(), self._mist_alpha(0.170 * opacity_boost * reactive["lower_alpha"] + low * 0.034, intensity)))
        wash.setColorAt(1.00, QColor(main.red(), main.green(), main.blue(), 0))
        painter.fillRect(glow_rect, wash)

        horizontal = QLinearGradient(rect.left(), 0, rect.right(), 0)
        horizontal.setColorAt(0.00, QColor(accent.red(), accent.green(), accent.blue(), 0))
        horizontal.setColorAt(0.18, QColor(accent.red(), accent.green(), accent.blue(), self._mist_alpha(0.076 * opacity_boost + high * 0.018, intensity)))
        horizontal.setColorAt(0.45, QColor(bridge.red(), bridge.green(), bridge.blue(), self._mist_alpha(0.132 * opacity_boost + mid * 0.028, intensity)))
        horizontal.setColorAt(0.74, QColor(main.red(), main.green(), main.blue(), self._mist_alpha(0.188 * opacity_boost * reactive["lower_alpha"] + low * 0.036, intensity)))
        horizontal.setColorAt(1.00, QColor(main.red(), main.green(), main.blue(), 0))

        amplitude = self._u(wave_plan.amplitude_units)
        thickness = self._u(wave_plan.thickness_units)
        main_path = self._sea_level_wave_path(rect.adjusted(self._u(5.0), 0, -self._u(5.0), 0), center_y, amplitude, thickness, phase)
        painter.fillPath(main_path, QBrush(horizontal))

        counter = QLinearGradient(rect.left(), 0, rect.right(), 0)
        counter.setColorAt(0.00, QColor(main.red(), main.green(), main.blue(), 0))
        counter.setColorAt(0.32, QColor(main.red(), main.green(), main.blue(), self._mist_alpha(0.060 * opacity_boost * reactive["lower_alpha"] + low * 0.014, intensity)))
        counter.setColorAt(0.58, QColor(accent.red(), accent.green(), accent.blue(), self._mist_alpha(0.066 * opacity_boost + high * 0.016, intensity)))
        counter.setColorAt(1.00, QColor(accent.red(), accent.green(), accent.blue(), 0))
        counter_path = self._sea_level_wave_path(
            rect.adjusted(self._u(16.0), 0, -self._u(16.0), 0),
            center_y - self._u(wave_plan.counter_y_offset_units),
            amplitude * wave_plan.counter_amplitude_scale,
            thickness * wave_plan.counter_thickness_scale,
            phase + 2.2,
            layer=1.0,
        )
        painter.fillPath(counter_path, QBrush(counter))

        crest = QPainterPath()
        values = context.wave_values
        count = max(2, len(values))
        for i, value in enumerate(values):
            pos = i / (count - 1)
            x = rect.left() + self._u(10.0) + (rect.width() - self._u(20.0)) * pos
            soft_peak = math.sin(pos * math.pi)
            swell = math.sin(pos * math.tau * 1.16 + phase) * 0.54
            ripple = math.sin(pos * math.tau * 2.35 - phase * 0.58) * 0.30
            surface = math.sin(pos * math.tau * 4.20 + phase * 0.34) * 0.12
            y = center_y - amplitude * (swell + ripple + surface) * (0.46 + soft_peak * 0.54)
            if i == 0:
                crest.moveTo(x, y)
            else:
                crest.lineTo(x, y)

        crest_gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
        crest_gradient.setColorAt(0.00, QColor(accent.red(), accent.green(), accent.blue(), 0))
        crest_gradient.setColorAt(0.26, QColor(bridge.red(), bridge.green(), bridge.blue(), self._mist_alpha((0.026 + flow * 0.018) * opacity_boost * wave_plan.crest_alpha_scale, intensity)))
        crest_gradient.setColorAt(0.55, QColor(main.red(), main.green(), main.blue(), self._mist_alpha(((0.034 + flow * 0.026) * opacity_boost * reactive["lower_alpha"] + mid * 0.008) * wave_plan.crest_alpha_scale, intensity)))
        crest_gradient.setColorAt(0.84, QColor(accent.red(), accent.green(), accent.blue(), self._mist_alpha((0.022 + flow * 0.016) * opacity_boost * wave_plan.crest_alpha_scale, intensity)))
        crest_gradient.setColorAt(1.00, QColor(main.red(), main.green(), main.blue(), 0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QBrush(crest_gradient), self._u(1.7 if expanded else 1.1), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(crest)

        painter.restore()
