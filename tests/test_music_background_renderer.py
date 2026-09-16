from __future__ import annotations

import math
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QRadialGradient

from local.music_background_renderer import MusicReactiveRenderState, NativeMusicBackgroundRenderer, SolidMusicBackgroundRenderer


class FakeReactiveHost:
    def __init__(self):
        self.band_calls = 0
        self.style_calls: list[str] = []
        self.ambient_phase = 1.25
        self.wave_values = [0.1, 0.2, 0.3]
        self.media_is_playing = False
        self.ambient_boost_calls = 0
        self.flow_calls = 0
        self.frequency_calls = 0
        self.ring_count_calls = 0
        self.solid_color_calls = 0
        self.lyric_fill_calls = 0
        self.full_duotone_calls: list[bool] = []
        self.sea_level_palette_calls = 0
        self.album_color = QColor(30, 120, 210)
        self.secondary_color = QColor(220, 80, 45)

    def _audio_bands(self) -> tuple[float, float, float]:
        self.band_calls += 1
        return (0.8, 0.5, 0.2)

    def _music_reactive_style_response(self, ambience_style: str) -> dict[str, float]:
        self.style_calls.append(ambience_style)
        return {"energy": 0.6, "style_index": float(len(self.style_calls))}

    def _ambient_boost(self) -> tuple[float, float, float, float]:
        self.ambient_boost_calls += 1
        return (1.5, 0.4, 1.2, 1.6)

    def _music_flow_amount(self) -> float:
        self.flow_calls += 1
        return 0.67

    def _diffraction_frequency_amount(self) -> float:
        self.frequency_calls += 1
        return 0.73

    def _bilateral_diffraction_ring_count(self) -> int:
        self.ring_count_calls += 1
        return 10

    def _solid_album_background_color(self) -> QColor:
        self.solid_color_calls += 1
        return QColor(18, 26, 38)

    def _active_lyric_fill_color(self) -> QColor:
        self.lyric_fill_calls += 1
        return QColor(240, 210, 170)

    def _safe_wave_color(self, color: QColor) -> QColor:
        return QColor(color)

    def _mix_color(self, a: QColor, b: QColor, amount: float) -> QColor:
        amount = max(0.0, min(1.0, amount))
        return QColor(
            int(a.red() + (b.red() - a.red()) * amount),
            int(a.green() + (b.green() - a.green()) * amount),
            int(a.blue() + (b.blue() - a.blue()) * amount),
        )

    def _full_duotone_palette_for_state(self, expanded: bool) -> tuple[QColor, QColor, QColor]:
        self.full_duotone_calls.append(bool(expanded))
        return (QColor(12, 34, 56), QColor(78, 90, 123), QColor(45, 67, 89))

    def _xiaomi_duotone_palette(self) -> tuple[QColor, QColor, QColor]:
        self.sea_level_palette_calls += 1
        return (QColor(20, 80, 160), QColor(170, 190, 220), QColor(80, 130, 180))


class PaintHost(FakeReactiveHost):
    def __init__(self, response: dict[str, float], bands: tuple[float, float, float] = (0.0, 0.0, 0.0)):
        super().__init__()
        self.response = response
        self.bands = bands
        self.media_is_playing = True
        self.ambient_phase = 0.6
        self.wave_values = [0.18 + 0.64 * abs(math.sin(index * 0.47)) for index in range(36)]

    def _audio_bands(self) -> tuple[float, float, float]:
        return self.bands

    def _music_reactive_style_response(self, ambience_style: str) -> dict[str, float]:
        self.style_calls.append(ambience_style)
        return dict(self.response)

    def _u(self, value: float) -> float:
        return float(value)

    def _music_shell_path(self, rect: QRectF, radius: float, expanded: bool = False) -> QPainterPath:
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), float(radius), float(radius))
        return path

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
            band = thickness * (0.82 + soft_peak * 0.26)
            points_top.append(QPointF(x, y - band * 0.5))
            points_bottom.append(QPointF(x, y + band * 0.5))

        path = QPainterPath(points_top[0])
        for point in points_top[1:]:
            path.lineTo(point)
        for point in reversed(points_bottom):
            path.lineTo(point)
        path.closeSubpath()
        return path

    def _mist_alpha(self, opacity: float, intensity: float) -> int:
        return int(max(0, min(196, float(opacity) * 255.0 * float(intensity))))

    def _boost_mist_color(self, color: QColor, boost: float) -> QColor:
        hue, saturation, lightness, _alpha = color.getHslF()
        if hue < 0:
            return QColor(color)
        saturation = max(0.0, min(0.92, saturation + float(boost) * 0.36))
        lightness = max(0.0, min(0.84, lightness + float(boost) * 0.15))
        return QColor.fromHslF(hue, saturation, lightness, 1.0)

    def _paint_mist_ellipse(
        self,
        painter: QPainter,
        center: QPointF,
        radius_x: float,
        radius_y: float,
        color: QColor,
        alpha: int,
    ) -> None:
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


def _reactive_response(**overrides: float) -> dict[str, float]:
    response = {
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
        "transition_softness": 0.050,
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
    response.update(overrides)
    return response


class MusicBackgroundRendererTests(unittest.TestCase):
    def _paint_image(self, paint_func, *, now: float = 42.0) -> QImage:
        image = QImage(260, 96, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        with patch("local.music_background_renderer.time.monotonic", return_value=now):
            paint_func(painter, QRectF(8.0, 8.0, 244.0, 80.0), 24.0)
        painter.end()
        return image

    def _image_difference(self, left: QImage, right: QImage) -> int:
        diff = 0
        for y in range(0, left.height(), 4):
            for x in range(0, left.width(), 4):
                a = left.pixelColor(x, y)
                b = right.pixelColor(x, y)
                diff += abs(a.red() - b.red())
                diff += abs(a.green() - b.green())
                diff += abs(a.blue() - b.blue())
                diff += abs(a.alpha() - b.alpha())
        return diff

    def _image_region_difference(self, left: QImage, right: QImage, region: QRectF) -> int:
        diff = 0
        left_bound = max(0, int(math.floor(region.left())))
        right_bound = min(left.width() - 1, int(math.ceil(region.right())))
        top_bound = max(0, int(math.floor(region.top())))
        bottom_bound = min(left.height() - 1, int(math.ceil(region.bottom())))
        for y in range(top_bound, bottom_bound + 1, 3):
            for x in range(left_bound, right_bound + 1, 3):
                a = left.pixelColor(x, y)
                b = right.pixelColor(x, y)
                diff += abs(a.red() - b.red())
                diff += abs(a.green() - b.green())
                diff += abs(a.blue() - b.blue())
                diff += abs(a.alpha() - b.alpha())
        return diff

    def _count_dark_pixels(self, image: QImage) -> int:
        dark = 0
        for y in range(0, image.height(), 4):
            for x in range(0, image.width(), 4):
                color = image.pixelColor(x, y)
                luma = color.red() * 0.2126 + color.green() * 0.7152 + color.blue() * 0.0722
                if color.alpha() > 24 and luma < 85:
                    dark += 1
        return dark

    def _assert_adjacent_frames_are_smooth(self, paint_func, *, minimum_long_change: int, maximum_short_ratio: float):
        first = self._paint_image(paint_func, now=42.000)
        next_frame = self._paint_image(paint_func, now=42.016)
        following_frame = self._paint_image(paint_func, now=42.033)
        later = self._paint_image(paint_func, now=42.250)

        first_step = self._image_difference(first, next_frame)
        second_step = self._image_difference(next_frame, following_frame)
        long_step = self._image_difference(first, later)

        self.assertGreater(long_step, minimum_long_change)
        self.assertLess(first_step, long_step * maximum_short_ratio)
        self.assertLess(second_step, long_step * maximum_short_ratio)

    def test_reactive_render_state_reads_bands_once_and_deduplicates_styles(self):
        host = FakeReactiveHost()
        renderer = SolidMusicBackgroundRenderer(host)

        state = renderer.reactive_render_state("sea_level", "duotone_full", "sea_level", "")

        self.assertEqual(host.band_calls, 1)
        self.assertEqual(host.style_calls, ["sea_level", "duotone_full"])
        self.assertEqual(state.bands, (0.8, 0.5, 0.2))
        self.assertEqual(state.response("sea_level")["style_index"], 1.0)
        self.assertEqual(state.response("duotone_full")["style_index"], 2.0)
        self.assertEqual(state.response("unknown"), {})

    def test_reactive_render_state_returns_defensive_style_response_copies(self):
        state = MusicReactiveRenderState(
            bands=(0.8, 0.5, 0.2),
            style_responses={"sea_level": {"energy": 0.6}},
        )

        response = state.response("sea_level")
        response["energy"] = 99.0

        self.assertEqual(state.response("sea_level")["energy"], 0.6)

    def test_render_context_carries_explicit_runtime_paint_inputs(self):
        host = FakeReactiveHost()
        renderer = SolidMusicBackgroundRenderer(host)

        context = renderer.render_context("sea_level")
        host.wave_values.append(0.9)

        self.assertEqual(context.bands, (0.8, 0.5, 0.2))
        self.assertEqual(context.response("sea_level")["energy"], 0.6)
        self.assertEqual(context.ambient_phase, 1.25)
        self.assertEqual(context.wave_values, (0.1, 0.2, 0.3))
        self.assertFalse(context.media_is_playing)
        self.assertEqual(context.tuning.ambient_boost, (1.5, 0.4, 1.2, 1.6))
        self.assertEqual(context.tuning.flow_amount, 0.67)
        self.assertEqual(context.tuning.diffraction_frequency, 0.73)
        self.assertEqual(context.tuning.diffraction_ring_count, 10)
        self.assertEqual(context.palette.album_color.getRgb(), (30, 120, 210, 255))
        self.assertEqual(context.palette.secondary_color.getRgb(), (220, 80, 45, 255))
        self.assertEqual(context.palette.solid_album_background.getRgb(), (18, 26, 38, 255))
        self.assertEqual(context.palette.active_lyric_fill.getRgb(), (240, 210, 170, 255))
        self.assertEqual(context.palette.sea_level_palette()[0].getRgb(), (20, 80, 160, 255))
        self.assertEqual(host.ambient_boost_calls, 1)
        self.assertEqual(host.flow_calls, 1)
        self.assertEqual(host.frequency_calls, 1)
        self.assertEqual(host.ring_count_calls, 1)
        self.assertEqual(host.solid_color_calls, 1)
        self.assertEqual(host.lyric_fill_calls, 1)
        self.assertEqual(host.full_duotone_calls, [])
        self.assertEqual(host.sea_level_palette_calls, 1)

    def test_render_context_carries_expanded_style_palette_and_defensive_color_copies(self):
        host = FakeReactiveHost()
        renderer = SolidMusicBackgroundRenderer(host)

        context = renderer.render_context("duotone_full", expanded=True)
        full = context.palette.full_duotone_palette()
        self.assertIsNotNone(full)
        assert full is not None
        first = full[0]
        first.setRed(250)

        self.assertEqual(host.full_duotone_calls, [True])
        self.assertEqual(context.palette.full_duotone_palette()[0].getRgb(), (12, 34, 56, 255))
        self.assertEqual(context.palette.sea_level_palette()[1].getRgb(), (170, 190, 220, 255))

    def test_render_context_defaults_missing_host_paint_inputs(self):
        class MinimalHost(FakeReactiveHost):
            def __init__(self):
                self.band_calls = 0
                self.style_calls = []

        renderer = SolidMusicBackgroundRenderer(MinimalHost())

        context = renderer.render_context("sea_level")

        self.assertEqual(context.ambient_phase, 0.0)
        self.assertEqual(context.wave_values, (0.25,) * 36)
        self.assertTrue(context.media_is_playing)
        self.assertEqual(context.tuning.ambient_boost, (1.04, 0.0, 1.0, 1.0))
        self.assertEqual(context.tuning.flow_amount, 0.0)
        self.assertEqual(context.tuning.diffraction_frequency, 0.30)
        self.assertEqual(context.tuning.diffraction_ring_count, 7)
        self.assertEqual(context.palette.solid_album_background.getRgb(), (24, 24, 24, 255))
        self.assertEqual(context.palette.sea_level_palette()[2].getRgb(), (24, 24, 24, 255))

    def test_solid_renderer_paints_with_explicit_helper_fallbacks(self):
        class MinimalPaintHost:
            album_color = QColor(35, 132, 220)
            secondary_color = QColor(230, 80, 120)
            ambient_phase = 0.4
            wave_values = (0.25,) * 36
            media_is_playing = True

            def _audio_bands(self) -> tuple[float, float, float]:
                return (0.66, 0.72, 0.48)

            def _music_reactive_style_response(self, _style: str) -> dict[str, float]:
                return _reactive_response(
                    energy=0.80,
                    low=0.66,
                    mid=0.72,
                    high=0.48,
                    bass=0.60,
                    shimmer=0.62,
                    side_wave_height=0.54,
                    solid_accent_alpha=0.56,
                    surface_glow=0.44,
                    wave_speed=1.20,
                    phase=0.95,
                    pulse=0.30,
                )

        image = self._paint_image(
            lambda painter, rect, radius: SolidMusicBackgroundRenderer(MinimalPaintHost()).paint_pure_white(painter, rect, radius)
        )

        self.assertGreater(image.pixelColor(130, 48).red(), 245)
        self.assertLess(self._count_dark_pixels(image), 4)

    def test_sea_level_wave_pixels_change_with_reactive_frame(self):
        quiet = PaintHost(_reactive_response(), bands=(0.05, 0.04, 0.03))
        reactive = PaintHost(
            _reactive_response(
                energy=0.82,
                low=0.90,
                mid=0.58,
                high=0.34,
                bass=0.88,
                lower_alpha=1.34,
                lower_scale=1.04,
                wave_height=1.48,
                wave_speed=1.22,
                color_boost=0.38,
                split_shift=0.012,
                top_guard=0.03,
                phase=1.1,
                pulse=0.42,
            ),
            bands=(0.90, 0.58, 0.34),
        )

        quiet_image = self._paint_image(
            lambda painter, rect, radius: NativeMusicBackgroundRenderer(quiet).paint_sea_level_wave(painter, rect, radius, expanded=False)
        )
        reactive_image = self._paint_image(
            lambda painter, rect, radius: NativeMusicBackgroundRenderer(reactive).paint_sea_level_wave(painter, rect, radius, expanded=False)
        )

        self.assertGreater(self._image_difference(quiet_image, reactive_image), 1400)

    def test_sea_level_reactive_wave_is_concentrated_in_lower_water_band(self):
        quiet = PaintHost(_reactive_response(), bands=(0.05, 0.04, 0.03))
        reactive = PaintHost(
            _reactive_response(
                energy=0.82,
                low=0.90,
                mid=0.58,
                high=0.34,
                bass=0.88,
                lower_alpha=1.34,
                lower_scale=1.04,
                wave_height=1.48,
                wave_speed=1.22,
                color_boost=0.38,
                split_shift=0.012,
                top_guard=0.03,
                phase=1.1,
                pulse=0.42,
            ),
            bands=(0.90, 0.58, 0.34),
        )

        quiet_image = self._paint_image(
            lambda painter, rect, radius: NativeMusicBackgroundRenderer(quiet).paint_sea_level_wave(painter, rect, radius, expanded=False)
        )
        reactive_image = self._paint_image(
            lambda painter, rect, radius: NativeMusicBackgroundRenderer(reactive).paint_sea_level_wave(painter, rect, radius, expanded=False)
        )
        upper_guard = self._image_region_difference(quiet_image, reactive_image, QRectF(24.0, 14.0, 212.0, 22.0))
        water_band = self._image_region_difference(quiet_image, reactive_image, QRectF(24.0, 54.0, 212.0, 30.0))

        self.assertGreater(water_band, 1800)
        self.assertGreater(water_band, upper_guard * 2)

    def test_sea_level_wave_motion_is_smooth_between_display_frames(self):
        reactive = PaintHost(
            _reactive_response(
                energy=0.82,
                low=0.90,
                mid=0.58,
                high=0.34,
                bass=0.88,
                lower_alpha=1.34,
                lower_scale=1.04,
                wave_height=1.48,
                wave_speed=1.22,
                color_boost=0.38,
                split_shift=0.012,
                top_guard=0.03,
                phase=1.1,
                pulse=0.42,
            ),
            bands=(0.90, 0.58, 0.34),
        )

        self._assert_adjacent_frames_are_smooth(
            lambda painter, rect, radius: NativeMusicBackgroundRenderer(reactive).paint_sea_level_wave(
                painter, rect, radius, expanded=False
            ),
            minimum_long_change=12000,
            maximum_short_ratio=0.45,
        )

    def test_sea_level_wave_consumes_semantic_drives_in_lower_water_band(self):
        base = dict(
            energy=0.58,
            low=0.54,
            mid=0.44,
            high=0.30,
            bass=0.52,
            lower_alpha=1.16,
            lower_scale=1.02,
            wave_height=1.12,
            wave_speed=1.08,
            color_boost=0.20,
            split_shift=0.004,
            top_guard=0.02,
            phase=1.0,
        )
        quiet = PaintHost(_reactive_response(**base), bands=(0.54, 0.44, 0.30))
        driven = PaintHost(
            _reactive_response(**base, beat_drive=0.92, glow_drive=0.84, lift_drive=0.76),
            bands=(0.54, 0.44, 0.30),
        )

        quiet_image = self._paint_image(
            lambda painter, rect, radius: NativeMusicBackgroundRenderer(quiet).paint_sea_level_wave(painter, rect, radius, expanded=False)
        )
        driven_image = self._paint_image(
            lambda painter, rect, radius: NativeMusicBackgroundRenderer(driven).paint_sea_level_wave(painter, rect, radius, expanded=False)
        )
        upper_guard = self._image_region_difference(quiet_image, driven_image, QRectF(24.0, 14.0, 212.0, 22.0))
        water_band = self._image_region_difference(quiet_image, driven_image, QRectF(24.0, 54.0, 212.0, 30.0))

        self.assertGreater(water_band, 700)
        self.assertGreater(water_band, upper_guard * 1.8)

    def test_duotone_full_pixels_change_with_side_wave_reactive_frame(self):
        quiet = PaintHost(_reactive_response(), bands=(0.04, 0.04, 0.04))
        reactive = PaintHost(
            _reactive_response(
                energy=0.78,
                low=0.62,
                mid=0.74,
                high=0.48,
                bass=0.58,
                color_boost=0.32,
                transition_shift=0.018,
                transition_softness=0.11,
                interference_flow=0.82,
                interference_alpha=1.48,
                side_wave_alpha=0.54,
                side_wave_height=0.72,
                side_wave_width=1.18,
                phase=1.35,
                pulse=0.34,
            ),
            bands=(0.62, 0.74, 0.48),
        )

        quiet_image = self._paint_image(
            lambda painter, rect, radius: NativeMusicBackgroundRenderer(quiet).paint_full_duotone_ambience(painter, rect, radius, expanded=False)
        )
        reactive_image = self._paint_image(
            lambda painter, rect, radius: NativeMusicBackgroundRenderer(reactive).paint_full_duotone_ambience(painter, rect, radius, expanded=False)
        )

        self.assertGreater(self._image_difference(quiet_image, reactive_image), 2200)

    def test_duotone_full_reactive_waves_keep_center_more_stable_than_edges(self):
        quiet = PaintHost(_reactive_response(), bands=(0.50, 0.50, 0.50))
        reactive = PaintHost(
            _reactive_response(
                side_wave_alpha=0.72,
                side_wave_height=0.82,
                side_wave_width=1.20,
                phase=1.35,
            ),
            bands=(0.50, 0.50, 0.50),
        )

        quiet_image = self._paint_image(
            lambda painter, rect, radius: NativeMusicBackgroundRenderer(quiet).paint_full_duotone_ambience(painter, rect, radius, expanded=False)
        )
        reactive_image = self._paint_image(
            lambda painter, rect, radius: NativeMusicBackgroundRenderer(reactive).paint_full_duotone_ambience(painter, rect, radius, expanded=False)
        )
        left_edge = self._image_region_difference(quiet_image, reactive_image, QRectF(16.0, 26.0, 54.0, 48.0))
        right_edge = self._image_region_difference(quiet_image, reactive_image, QRectF(190.0, 26.0, 54.0, 48.0))
        center = self._image_region_difference(quiet_image, reactive_image, QRectF(96.0, 30.0, 68.0, 38.0))

        self.assertGreater(left_edge + right_edge, 900)
        self.assertLess(center, (left_edge + right_edge) * 0.55)

    def test_duotone_full_consumes_semantic_drives_as_side_motion(self):
        base = dict(
            energy=0.54,
            low=0.50,
            mid=0.52,
            high=0.42,
            bass=0.48,
            color_boost=0.20,
            transition_shift=0.006,
            transition_softness=0.08,
            interference_flow=0.36,
            interference_alpha=1.14,
            side_wave_alpha=0.46,
            side_wave_height=0.44,
            side_wave_width=1.08,
            phase=1.12,
        )
        quiet = PaintHost(_reactive_response(**base), bands=(0.50, 0.52, 0.42))
        driven = PaintHost(
            _reactive_response(**base, beat_drive=0.88, glow_drive=0.82, lift_drive=0.66),
            bands=(0.50, 0.52, 0.42),
        )

        quiet_image = self._paint_image(
            lambda painter, rect, radius: NativeMusicBackgroundRenderer(quiet).paint_full_duotone_ambience(painter, rect, radius, expanded=False)
        )
        driven_image = self._paint_image(
            lambda painter, rect, radius: NativeMusicBackgroundRenderer(driven).paint_full_duotone_ambience(painter, rect, radius, expanded=False)
        )
        left_edge = self._image_region_difference(quiet_image, driven_image, QRectF(16.0, 26.0, 54.0, 48.0))
        right_edge = self._image_region_difference(quiet_image, driven_image, QRectF(190.0, 26.0, 54.0, 48.0))
        center = self._image_region_difference(quiet_image, driven_image, QRectF(96.0, 30.0, 68.0, 38.0))

        self.assertGreater(left_edge + right_edge, 500)
        self.assertLess(center, (left_edge + right_edge) * 0.70)

    def test_duotone_full_reactive_motion_is_smooth_between_display_frames(self):
        reactive = PaintHost(
            _reactive_response(
                energy=0.78,
                low=0.62,
                mid=0.74,
                high=0.48,
                bass=0.58,
                color_boost=0.32,
                transition_shift=0.018,
                transition_softness=0.11,
                interference_flow=0.82,
                interference_alpha=1.48,
                side_wave_alpha=0.54,
                side_wave_height=0.72,
                side_wave_width=1.18,
                phase=1.35,
                pulse=0.34,
            ),
            bands=(0.62, 0.74, 0.48),
        )

        self._assert_adjacent_frames_are_smooth(
            lambda painter, rect, radius: NativeMusicBackgroundRenderer(reactive).paint_full_duotone_ambience(
                painter, rect, radius, expanded=False
            ),
            minimum_long_change=1800,
            maximum_short_ratio=0.50,
        )

    def test_bilateral_diffraction_reactive_waveforms_are_bilateral_not_center_wash(self):
        quiet = PaintHost(_reactive_response(), bands=(0.45, 0.45, 0.45))
        reactive = PaintHost(
            _reactive_response(
                side_wave_alpha=0.78,
                side_wave_height=0.84,
                side_wave_width=1.32,
                phase=1.15,
            ),
            bands=(0.45, 0.45, 0.45),
        )

        quiet_image = self._paint_image(
            lambda painter, rect, radius: NativeMusicBackgroundRenderer(quiet).paint_bilateral_diffraction_ambience(painter, rect, radius, expanded=False)
        )
        reactive_image = self._paint_image(
            lambda painter, rect, radius: NativeMusicBackgroundRenderer(reactive).paint_bilateral_diffraction_ambience(painter, rect, radius, expanded=False)
        )
        left_wave = self._image_region_difference(quiet_image, reactive_image, QRectF(18.0, 24.0, 62.0, 52.0))
        right_wave = self._image_region_difference(quiet_image, reactive_image, QRectF(180.0, 24.0, 62.0, 52.0))
        lyric_center = self._image_region_difference(quiet_image, reactive_image, QRectF(96.0, 28.0, 68.0, 42.0))

        self.assertGreater(left_wave + right_wave, 1100)
        self.assertLess(lyric_center, (left_wave + right_wave) * 0.42)

    def test_bilateral_diffraction_consumes_semantic_drives_as_side_waveforms(self):
        base = dict(
            energy=0.50,
            low=0.46,
            mid=0.48,
            high=0.46,
            bass=0.44,
            color_boost=0.18,
            wave_speed=1.06,
            side_wave_alpha=0.50,
            side_wave_height=0.48,
            side_wave_width=1.10,
            surface_glow=0.16,
            phase=1.08,
        )
        quiet = PaintHost(_reactive_response(**base), bands=(0.46, 0.48, 0.46))
        driven = PaintHost(
            _reactive_response(**base, beat_drive=0.90, glow_drive=0.78, lift_drive=0.70),
            bands=(0.46, 0.48, 0.46),
        )

        quiet_image = self._paint_image(
            lambda painter, rect, radius: NativeMusicBackgroundRenderer(quiet).paint_bilateral_diffraction_ambience(painter, rect, radius, expanded=False)
        )
        driven_image = self._paint_image(
            lambda painter, rect, radius: NativeMusicBackgroundRenderer(driven).paint_bilateral_diffraction_ambience(painter, rect, radius, expanded=False)
        )
        left_wave = self._image_region_difference(quiet_image, driven_image, QRectF(18.0, 24.0, 62.0, 52.0))
        right_wave = self._image_region_difference(quiet_image, driven_image, QRectF(180.0, 24.0, 62.0, 52.0))
        lyric_center = self._image_region_difference(quiet_image, driven_image, QRectF(96.0, 28.0, 68.0, 42.0))

        self.assertGreater(left_wave + right_wave, 700)
        self.assertLess(lyric_center, (left_wave + right_wave) * 0.55)

    def test_bilateral_diffraction_motion_is_smooth_between_display_frames(self):
        reactive = PaintHost(
            _reactive_response(
                energy=0.72,
                low=0.62,
                mid=0.64,
                high=0.58,
                bass=0.64,
                color_boost=0.24,
                wave_speed=1.22,
                side_wave_alpha=0.78,
                side_wave_height=0.84,
                side_wave_width=1.32,
                surface_glow=0.26,
                phase=1.15,
                pulse=0.30,
            ),
            bands=(0.62, 0.64, 0.58),
        )

        self._assert_adjacent_frames_are_smooth(
            lambda painter, rect, radius: NativeMusicBackgroundRenderer(reactive).paint_bilateral_diffraction_ambience(
                painter, rect, radius, expanded=False
            ),
            minimum_long_change=36000,
            maximum_short_ratio=0.45,
        )

    def test_pure_white_reactive_accent_is_visible_without_dark_flash(self):
        quiet = PaintHost(_reactive_response(), bands=(0.0, 0.0, 0.0))
        reactive = PaintHost(
            _reactive_response(
                energy=0.80,
                low=0.66,
                mid=0.72,
                high=0.48,
                bass=0.60,
                shimmer=0.62,
                side_wave_height=0.54,
                solid_accent_alpha=0.56,
                surface_glow=0.44,
                wave_speed=1.20,
                phase=0.95,
                pulse=0.30,
            ),
            bands=(0.66, 0.72, 0.48),
        )

        quiet_image = self._paint_image(
            lambda painter, rect, radius: SolidMusicBackgroundRenderer(quiet).paint_pure_white(painter, rect, radius)
        )
        reactive_image = self._paint_image(
            lambda painter, rect, radius: SolidMusicBackgroundRenderer(reactive).paint_pure_white(painter, rect, radius)
        )
        center = reactive_image.pixelColor(130, 48)

        self.assertGreater(self._image_difference(quiet_image, reactive_image), 650)
        self.assertGreater(center.red() + center.green() + center.blue(), 620)
        self.assertLess(self._count_dark_pixels(reactive_image), 4)

    def test_pure_black_reactive_accent_uses_album_color_without_lifting_lyric_center(self):
        quiet = PaintHost(_reactive_response(), bands=(0.0, 0.0, 0.0))
        reactive = PaintHost(
            _reactive_response(
                energy=0.82,
                low=0.68,
                mid=0.62,
                high=0.36,
                bass=0.70,
                shimmer=0.40,
                side_wave_height=0.50,
                solid_accent_alpha=0.58,
                surface_glow=0.46,
                wave_speed=1.18,
                phase=1.05,
                pulse=0.32,
            ),
            bands=(0.68, 0.62, 0.36),
        )

        quiet_image = self._paint_image(
            lambda painter, rect, radius: SolidMusicBackgroundRenderer(quiet).paint_pure_black(painter, rect, radius)
        )
        reactive_image = self._paint_image(
            lambda painter, rect, radius: SolidMusicBackgroundRenderer(reactive).paint_pure_black(painter, rect, radius)
        )
        edge_change = self._image_region_difference(quiet_image, reactive_image, QRectF(16.0, 28.0, 72.0, 42.0))
        center = reactive_image.pixelColor(130, 48)

        self.assertGreater(edge_change, 420)
        self.assertLess(center.red() + center.green() + center.blue(), 96)


if __name__ == "__main__":
    unittest.main()
