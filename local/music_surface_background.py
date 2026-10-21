"""Bounded artwork-color fields for the full-screen/windowed music surface.

The surface owns paint requests and visibility. This renderer owns only one
small opaque raster, palette retargeting and bounded visual motion; it does
not acquire artwork or start an independent animation loop.
"""

from __future__ import annotations

import math
import sys

import numpy as np
from PIL import Image
from PIL.ImageFilter import GaussianBlur
from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath

from local.music_lyric_motion import MotionSimulationClock
from local.music_surface_palette import balanced_background_colors, relative_luminance, single_hue_background_colors


def _render_background_snapshot(snapshot) -> QImage:
    """Only immutable numbers cross the existing pixel-worker boundary."""
    width, height, geometry_seconds, colors, strengths = snapshot
    return AlbumFlowBackgroundRenderer._paint_field_pixels(
        QSize(width, height), tuple(QColor(*color) for color in colors),
        strengths, geometry_seconds,
    )


def _snapshot_bytes(value) -> int:
    return sys.getsizeof(value) + (sum(_snapshot_bytes(item) for item in value)
                                     if isinstance(value, tuple) else 0)


class AlbumFlowBackgroundRenderer:
    """Paint slowly morphing, heavily softened fields from actual album hues.

    ``render`` returns an opaque image with both dimensions bounded to 256
    pixels. The caller scales it to its surface and may sample it for lyric
    contrast. Repeated calls share the cached image until the next 30 Hz frame
    or a palette/size/intensity change; a caller can safely retain the result.
    """

    MAX_WIDTH = 256
    MAX_HEIGHT = 256
    FRAME_SECONDS = 1.0 / 30.0
    PALETTE_TRANSITION_SECONDS = 1.2
    MAX_COLORS = 6
    # White read ink needs a brightness envelope, not an HSV value cap that
    # unnecessarily dims ordinary reds/blues. Neutral highlights stay near
    # the reference's light gray while all moving pigments satisfy this cap.
    MAX_LUMINANCE = 0.265

    def __init__(self):
        self._clock = MotionSimulationClock()
        self._motion_seconds = 0.0
        self._geometry_seconds = 0.0
        self._last_intensity: float | None = None
        self._palette_started_at = 0.0
        self._from_colors: tuple[QColor, ...] = ()
        self._target_colors: tuple[QColor, ...] = ()
        self._painted_colors: tuple[QColor, ...] = ()
        self._from_strengths: tuple[float, ...] = ()
        self._target_strengths: tuple[float, ...] = ()
        self._painted_strengths: tuple[float, ...] = ()
        self._frame = QImage()
        self._frame_at: float | None = None
        self._frame_key: tuple | None = None
        self._palette_input_key: tuple | None = None
        self._async_epoch = 0
        self._async_domain = None

    @classmethod
    def _ambient_color(cls, source: QColor) -> QColor:
        """Soften pure display pigments while retaining hue and gray order."""
        source = QColor(source)
        hue, saturation, value, _alpha = source.getHsv()
        if hue < 0 or saturation < 35 or max(source.getRgb()[:3]) - min(source.getRgb()[:3]) < 12:
            # Monotonic: black remains dark, white remains a light field.
            level = round(31 + 109 * (value / 255.0) ** 0.85)
            return QColor(level, level, level)
        # A photographic shadow is permitted to be a visible colored mass.
        # Only bright, highly pure pigments soften: the sampled palette,
        # Solid island and characteristic/control ink keep their source roles.
        color = QColor.fromHsv(hue, saturation, min(220, round(80 + value * 0.55)))
        original = cls._bounded_luminance(color)
        purity = min(1.0, max(0.0, (saturation - 180) / 60.0))
        visibility = min(1.0, max(0.0, (value - 64) / 96.0))
        soften = purity * purity * (3.0 - 2.0 * purity) * visibility * visibility * (3.0 - 2.0 * visibility)
        if soften <= 0.0:
            return original
        softened = QColor.fromHsvF(hue / 360.0, saturation / 255.0 * (1.0 - .34 * soften),
                                   color.valueF() * (1.0 - .16 * soften))
        # Blue has low perceptual luminance despite a high HSV value.
        # Desaturation raises its red/green channels, so constrain the result
        # against its previous display luminance as well as the lyric envelope.
        return cls._bounded_luminance(softened,
            maximum=min(cls.MAX_LUMINANCE, relative_luminance(original) * (1.0 - .16 * soften)))

    @classmethod
    def _bounded_luminance(cls, color: QColor, *, maximum: float | None = None) -> QColor:
        maximum = cls.MAX_LUMINANCE if maximum is None else max(0.0, min(cls.MAX_LUMINANCE, maximum))
        if relative_luminance(color) > maximum:
            low, high = 0.0, 1.0
            for _ in range(12):
                factor = (low + high) * 0.5
                candidate = QColor(*(round(channel * factor) for channel in color.getRgb()[:3]))
                if relative_luminance(candidate) > maximum:
                    high = factor
                else:
                    low = factor
            color = QColor(*(round(channel * low) for channel in color.getRgb()[:3]))
        return color

    @classmethod
    def _raster_size(cls, size: QSize) -> QSize:
        width, height = max(1, size.width()), max(1, size.height())
        scale = min(1.0, cls.MAX_WIDTH / width, cls.MAX_HEIGHT / height)
        return QSize(max(1, round(width * scale)), max(1, round(height * scale)))

    @classmethod
    def _palette_slots(cls, colors: tuple[QColor, ...], weights: tuple[float, ...] = ()) -> tuple[QColor, ...]:
        colors = tuple(color for color in colors[:cls.MAX_COLORS] if color.isValid())
        tonal_layers = single_hue_background_colors(colors, weights=weights)
        if tonal_layers:
            layers = tuple(cls._bounded_luminance(color) for color in tonal_layers)
            return (*layers, *(QColor(layers[0]) for _ in range(cls.MAX_COLORS + 1 - len(layers))))
        balanced = balanced_background_colors(colors, weights=weights)
        if balanced:
            # These display colors already have the shared perceptual
            # mapping. Keep the foundation quiet and every source pigment
            # in its own moving domain; only enforce the ink envelope here.
            layers = tuple(cls._bounded_luminance(color) for color in balanced)
            return (*layers, *(QColor(layers[0]) for _ in range(cls.MAX_COLORS + 1 - len(layers))))
        pigments = [cls._ambient_color(color) for color in colors]
        if not pigments:
            pigments = [QColor(44, 44, 44)]
        # Slot zero is the actual dominant cover pigment, used as the base.
        # Minor colors have one domain each. Empty slots have the base color
        # and exert no visible wash; a small gray detail cannot acquire three
        # layers simply because the source has fewer than six colors.
        base = pigments[0]
        dark_chromatic = cls._has_dark_chromatic_base(colors)
        if dark_chromatic:
            # Near-black chromatic photography needs a quiet dark base,
            # rather than brightening a brown shadow into a full-screen wash.
            base = QColor(31, 31, 31)
        fields = pigments if dark_chromatic else pigments[1:]
        if len(pigments) == 1 and not dark_chromatic:
            fields = [QColor(*(round(channel * 0.76) for channel in base.getRgb()[:3]))]
        fields += [QColor(base)] * (cls.MAX_COLORS - len(fields))
        return (QColor(base), *fields)

    @staticmethod
    def _has_dark_chromatic_base(colors: tuple[QColor, ...]) -> bool:
        if not colors:
            return False
        first = colors[0]
        return (first.value() < 48 and first.hsvHue() >= 0 and first.hsvSaturation() >= 35
                and max(first.getRgb()[:3]) - min(first.getRgb()[:3]) >= 12)

    @classmethod
    def _domain_strengths(cls, colors: tuple[QColor, ...], weights: tuple[float, ...]) -> tuple[float, ...]:
        if single_hue_background_colors(colors, weights=weights):
            # Synthetic tonal layers express depth rather than source area.
            # The strongest hue has the smallest domain, over a broad pale
            # mass and middle band; unused slots match the quiet base.
            return (1.0, 0.88, 0.60, 1.0, 1.0, 1.0)
        if not weights or len(weights) != len(colors):
            return (1.0,) * cls.MAX_COLORS
        # Cover support controls occupied area, not a global transparency
        # wash. Genuine small details still retain a concentrated core.
        all_domains = cls._has_dark_chromatic_base(colors) or bool(balanced_background_colors(colors, weights=weights))
        source_weights = weights[:cls.MAX_COLORS] if all_domains else weights[1:cls.MAX_COLORS]
        result = [min(1.0, max(0.18, math.sqrt(max(0.0, float(weight)) / 0.08)))
                  for weight in source_weights]
        result += [1.0] * (cls.MAX_COLORS - len(result))
        return tuple(result)

    @staticmethod
    def _mix_colors(first: QColor, second: QColor, fraction: float) -> QColor:
        return QColor(*(round(a + (b - a) * fraction)
                        for a, b in zip(first.getRgb()[:3], second.getRgb()[:3])))

    def _palette_fraction(self, now: float) -> float:
        fraction = min(1.0, max(0.0, (now - self._palette_started_at) / self.PALETTE_TRANSITION_SECONDS))
        # Smoothstep begins/ends at rest. An interrupted change takes the
        # exact last displayed pigments as its next source, never an old song.
        return fraction * fraction * (3.0 - 2.0 * fraction)

    def _palette_at(self, now: float) -> tuple[QColor, ...]:
        fraction = self._palette_fraction(now)
        return tuple(self._mix_colors(first, second, fraction)
                     for first, second in zip(self._from_colors, self._target_colors))

    @classmethod
    def _blob_path(cls, size: QSize, index: int, seconds: float, strength: float = 1.0) -> QPainterPath:
        """Closed cubic contours with independent translation and shape waves."""
        width, height = size.width(), size.height()
        phase = 0.90 + index * 1.91
        moving = seconds
        center_x = 0.50 + 0.72 * math.sin(moving * (0.16 + index * 0.009) + phase)
        center_y = 0.56 + 0.76 * math.cos(moving * (0.14 + index * 0.011) + phase * 1.45)
        radius_x = width * (0.35 + 0.085 * math.sin(moving * 0.19 + phase)) * strength
        radius_y = height * (0.30 + 0.10 * math.cos(moving * 0.17 + phase)) * strength
        rotation = phase * 0.39 + moving * 0.16 + 0.25 * math.sin(moving * 0.11 + phase)
        points = []
        for vertex in range(10):
            angle = vertex * math.tau / 10.0
            shape = (1.0 + 0.17 * math.sin(angle * 3.0 + phase + moving * 0.19)
                     + 0.12 * math.cos(angle * 2.0 - phase + moving * 0.13)
                     + 0.06 * math.sin(angle * 5.0 + phase * 0.5 - moving * 0.11))
            # Rotate the physical contour, not separately stretched sine and
            # cosine axes. This lets a ribbon turn and bend across a 16:9 view.
            x, y = radius_x * shape * math.cos(angle), radius_y * shape * math.sin(angle)
            points.append(QPointF(width * center_x + x * math.cos(rotation) - y * math.sin(rotation),
                                  height * center_y + x * math.sin(rotation) + y * math.cos(rotation)))
        path = QPainterPath(points[0])
        for vertex in range(len(points)):
            previous = points[(vertex - 1) % len(points)]
            start = points[vertex]
            end = points[(vertex + 1) % len(points)]
            following = points[(vertex + 2) % len(points)]
            path.cubicTo(start + (end - previous) / 6.0,
                         end - (following - start) / 6.0, end)
        path.closeSubpath()
        return path

    def _paint_field(self, size: QSize, colors: tuple[QColor, ...], strengths: tuple[float, ...] = ()) -> QImage:
        return self._paint_field_pixels(size, colors, strengths, self._geometry_seconds)

    @classmethod
    def _paint_field_pixels(cls, size: QSize, colors: tuple[QColor, ...],
                            strengths: tuple[float, ...], geometry_seconds: float) -> QImage:
        width, height = size.width(), size.height()
        base = np.array(colors[0].getRgb()[:3], dtype=np.float32)
        # Light covers in the reference carry broad, connected color bands;
        # dark covers let localized pigments leave a mostly quiet base. Ease
        # their occupied size with base brightness, including during retargets.
        spread = 1.0 + 0.45 * min(1.0, relative_luminance(colors[0]) / cls.MAX_LUMINANCE)
        total = np.zeros((height, width), dtype=np.float32)
        strongest = np.zeros_like(total)
        # Keep channels planar while combining masks: contiguous spatial
        # operations avoid a strided three-channel broadcast for every field.
        weighted = np.zeros((3, height, width), dtype=np.float32)
        for index, color in enumerate(colors[1:]):
            if color == colors[0]:
                continue
            mask = QImage(size, QImage.Format.Format_Grayscale8)
            mask.fill(Qt.GlobalColor.black)
            strength = strengths[index] if strengths else 1.0
            with QPainter(mask) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(Qt.GlobalColor.white)
                painter.drawPath(cls._blob_path(size, index, geometry_seconds, strength * spread))
            pixels = Image.frombytes("L", (width, height), mask.constBits().tobytes(),
                                     "raw", "L", mask.bytesPerLine())
            # Soften the domain, not already mixed RGB paint. The interior
            # can retain its pigment while long feathered edges blend.
            domain = np.asarray(pixels.filter(GaussianBlur(max(1.0, max(width, height) * 0.055 * max(.35, strength)))),
                                dtype=np.float32) / 255.0
            weight = domain * domain * domain
            total += weight
            np.maximum(strongest, domain, out=strongest)
            tone = 0.97 + 0.03 * math.sin(geometry_seconds * 0.18 + index * 1.21)
            pigment = np.array(color.getRgb()[:3], dtype=np.float32) * tone
            weighted += pigment[:, None, None] * weight[None, :, :]
        mixture = weighted / np.maximum(total[None, :, :], 1e-8)
        # A dominant-field composition is independent of painter order.
        # Preserve the cover base wherever all domains have left the frame.
        coverage = strongest * strongest
        rgb = np.rint(base[:, None, None] + coverage[None, :, :] * (mixture - base[:, None, None]))
        rgb = rgb.clip(0, 255).astype(np.uint8).transpose(1, 2, 0)
        opaque = np.full((height, width, 1), 255, dtype=np.uint8)
        payload = np.concatenate((rgb, opaque), axis=2).tobytes()
        return QImage(payload, size.width(), size.height(), size.width() * 4,
                      QImage.Format.Format_RGBA8888).copy()

    def _accept_background_frame(self, image: QImage, domain, stamp) -> None:
        if domain != self._async_domain:
            return
        self._frame = image
        self._frame_key, self._frame_at = domain[2], stamp[0]
        self._painted_colors = tuple(QColor(*color) for color in stamp[2])
        self._painted_strengths = stamp[3]

    def contrast_backdrop(self, displayed: QImage) -> QColor | None:
        """Bound the lightest possible field while its album pigments move.

        Every field, base blend and transition is a convex RGB blend of these
        luminance-bounded pigments. A stable light ink selected above
        this luminance remains readable as the masses change position.
        External test/reference rasters keep their own spatial sampling.
        """
        if self._frame.isNull() or displayed.cacheKey() != self._frame.cacheKey():
            return None
        return QColor(max(self._from_colors + self._target_colors, key=relative_luminance))

    def render(self, size: QSize, colors: tuple[QColor, ...], now: float, intensity: float = 0.74,
               *, weights: tuple[float, ...] = (), material_owner=None, material_scene=None) -> QImage:
        now = float(now)
        intensity = min(1.0, max(0.0, float(intensity)))
        # Normalize colors and their support together. An invalid first
        # QColor must neither decide the base nor shift a valid color's area.
        valid = tuple(index for index, color in enumerate(colors[:self.MAX_COLORS]) if color.isValid())
        weights = tuple(weights[index] for index in valid) if len(weights) == len(colors) else ()
        colors = tuple(colors[index] for index in valid)
        delta = self._clock.advance(now)
        self._motion_seconds += delta
        # Strength changes alter future speed, never the complete elapsed
        # phase. Charge the interval since the previous sample at its old
        # speed, so pausing and restoring flow keep the displayed geometry.
        previous_intensity = intensity if self._last_intensity is None else self._last_intensity
        self._geometry_seconds += delta * previous_intensity
        self._last_intensity = intensity
        # A paint-cache hit must not repeat hue analysis, luminance searches
        # and QColor allocation. Keep only the current bounded input, using
        # value snapshots so an in-place QColor change still retargets.
        palette_input_key = (tuple(QColor(color) for color in colors), weights)
        if palette_input_key != self._palette_input_key:
            target = self._palette_slots(colors, weights)
            strengths = self._domain_strengths(colors, weights)
            self._palette_input_key = palette_input_key
        else:
            target, strengths = self._target_colors, self._target_strengths
        retargeted = target != self._target_colors or strengths != self._target_strengths
        if retargeted:
            self._async_epoch += 1
            self._from_colors = self._painted_colors or target
            self._target_colors = target
            self._from_strengths = self._painted_strengths or strengths
            self._target_strengths = strengths
            self._palette_started_at = self._motion_seconds
        raster_size = self._raster_size(size)
        key = (raster_size.width(), raster_size.height(), intensity, strengths)
        if (retargeted or key != self._frame_key or self._frame.isNull()
                or self._frame_at is None or now < self._frame_at
                or now - self._frame_at >= self.FRAME_SECONDS - 1e-9):
            painted_colors = self._palette_at(self._motion_seconds)
            fraction = self._palette_fraction(self._motion_seconds)
            painted_strengths = tuple(first + (second - first) * fraction
                                      for first, second in zip(self._from_strengths, self._target_strengths))
            if material_owner is not None and not self._frame.isNull():
                from local.liquid_glass_tasks import glass_pixel_tasks
                tasks = glass_pixel_tasks()
                if tasks is not None:
                    rgb = tuple(color.getRgb()[:3] for color in painted_colors)
                    self._async_domain = (self._async_epoch, (size.width(), size.height()), key,
                                          tuple(color.getRgb()[:3] for color in target))
                    stamp = (now, self._geometry_seconds, rgb, painted_strengths)
                    snapshot = (raster_size.width(), raster_size.height(), self._geometry_seconds,
                                rgb, painted_strengths)
                    tasks.request(
                        material_owner, ("album-flow-background", id(self)), material_scene,
                        self._async_domain, stamp, snapshot, _render_background_snapshot,
                        material_owner.rect(), byte_count=_snapshot_bytes(snapshot),
                        # render's frame clock already owns the 30 Hz gate;
                        # a second delivery-time gate would slow it further.
                        interval=0., ready_callback=self._accept_background_frame,
                    )
                    return QImage(self._frame)
            # First paint and explicit snapshots keep a complete synchronous
            # frame. Fence any old live job before it can replace this image.
            self._async_epoch += 1
            self._async_domain = None
            self._painted_colors, self._painted_strengths = painted_colors, painted_strengths
            self._frame = self._paint_field(raster_size, painted_colors, painted_strengths)
            self._frame_key, self._frame_at = key, now
        return QImage(self._frame)
