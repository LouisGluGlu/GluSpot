"""Bounded artwork-color fields for the full-screen/windowed music surface.

The surface owns paint requests and visibility. This renderer owns only one
small opaque raster, palette retargeting and bounded visual motion; it does
not acquire artwork or start an independent animation loop.
"""

from __future__ import annotations

import math

from PIL import Image
from PIL.ImageFilter import GaussianBlur
from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath

from local.music_lyric_motion import MotionSimulationClock
from local.music_surface_palette import background_role_color, relative_luminance


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
    # Fixed slots retain their geometry through palette changes. Their color
    # assignments interpolate instead of adding/removing a visible shape.
    _CENTERS = ((0.13, 0.17), (0.86, 0.24), (0.41, 0.85),
                (0.80, 0.78), (0.18, 0.64), (0.51, 0.29))

    def __init__(self):
        self._clock = MotionSimulationClock()
        self._motion_seconds = 0.0
        self._geometry_seconds = 0.0
        self._last_intensity: float | None = None
        self._palette_started_at = 0.0
        self._from_colors: tuple[QColor, ...] = ()
        self._target_colors: tuple[QColor, ...] = ()
        self._painted_colors: tuple[QColor, ...] = ()
        self._frame = QImage()
        self._frame_at: float | None = None
        self._frame_key: tuple | None = None

    @classmethod
    def _raster_size(cls, size: QSize) -> QSize:
        width, height = max(1, size.width()), max(1, size.height())
        scale = min(1.0, cls.MAX_WIDTH / width, cls.MAX_HEIGHT / height)
        return QSize(max(1, round(width * scale)), max(1, round(height * scale)))

    @classmethod
    def _palette_slots(cls, colors: tuple[QColor, ...]) -> tuple[QColor, ...]:
        pigments = [background_role_color(color) for color in colors[:cls.MAX_COLORS] if color.isValid()]
        if not pigments:
            pigments = [background_role_color(QColor(56, 56, 56))]
        return tuple(QColor(pigments[index * len(pigments) // cls.MAX_COLORS])
                     for index in range(cls.MAX_COLORS))

    @staticmethod
    def _mix_colors(first: QColor, second: QColor, fraction: float) -> QColor:
        return QColor(*(round(a + (b - a) * fraction)
                        for a, b in zip(first.getRgb()[:3], second.getRgb()[:3])))

    def _palette_at(self, now: float) -> tuple[QColor, ...]:
        fraction = min(1.0, max(0.0, (now - self._palette_started_at) / self.PALETTE_TRANSITION_SECONDS))
        # Smoothstep begins/ends at rest. An interrupted change takes the
        # exact last displayed pigments as its next source, never an old song.
        fraction = fraction * fraction * (3.0 - 2.0 * fraction)
        return tuple(self._mix_colors(first, second, fraction)
                     for first, second in zip(self._from_colors, self._target_colors))

    @classmethod
    def _blob_path(cls, size: QSize, index: int, seconds: float) -> QPainterPath:
        """Closed cubic contours with independent translation and shape waves."""
        width, height = size.width(), size.height()
        phase = index * 1.73
        moving = seconds
        center_x, center_y = cls._CENTERS[index]
        center_x += 0.14 * math.sin(moving * (0.13 + index * 0.012) + phase)
        center_y += 0.16 * math.cos(moving * (0.11 + index * 0.009) + phase * 0.83)
        radius_x = width * (0.45 + 0.07 * math.sin(moving * 0.17 + phase))
        radius_y = height * (0.64 + 0.11 * math.cos(moving * 0.14 + phase))
        rotation = phase * 0.29 + 0.12 * math.sin(moving * 0.09 + phase)
        points = []
        for vertex in range(10):
            angle = vertex * math.tau / 10.0
            shape = (1.0 + 0.17 * math.sin(angle * 3.0 + phase + moving * 0.19)
                     + 0.12 * math.cos(angle * 2.0 - phase + moving * 0.13)
                     + 0.06 * math.sin(angle * 5.0 + phase * 0.5 - moving * 0.11))
            points.append(QPointF(width * center_x + radius_x * shape * math.cos(angle + rotation),
                                  height * center_y + radius_y * shape * math.sin(angle + rotation)))
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

    def _paint_field(self, size: QSize, colors: tuple[QColor, ...]) -> QImage:
        image = QImage(size, QImage.Format.Format_RGB32)
        # A darker blend fills every pixel beneath the irregular fields,
        # including the corners, without a fixed spatial band or foreign hue.
        image.fill(QColor(*(round(sum(color.getRgb()[channel] for color in colors) / len(colors) * 0.66)
                            for channel in range(3))))
        with QPainter(image) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            for index, color in enumerate(colors):
                # Tonal variation also animates uniform/monochrome albums.
                # Scale RGB together so a neutral cover never acquires a hue.
                tone = 0.88 + 0.10 * math.sin(self._geometry_seconds * 0.18 + index * 1.21)
                pigment = QColor(*(round(channel * tone) for channel in color.getRgb()[:3]), 190)
                painter.setBrush(pigment)
                painter.drawPath(self._blob_path(size, index, self._geometry_seconds))
        rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
        pixels = Image.frombytes("RGBA", (size.width(), size.height()), rgba.constBits().tobytes(),
                                 "raw", "RGBA", rgba.bytesPerLine())
        # Blur is performed once on the small complete field; cover and text
        # retain their own display-resolution paint path in the owner.
        softened = pixels.filter(GaussianBlur(max(2.0, max(size.width(), size.height()) * 0.10)))
        payload = softened.tobytes()
        return QImage(payload, size.width(), size.height(), size.width() * 4,
                      QImage.Format.Format_RGBA8888).copy()

    def contrast_backdrop(self, displayed: QImage) -> QColor | None:
        """Bound the lightest possible field while its album pigments move.

        Every field, base blend, transition and Gaussian sample is a convex
        RGB blend of these capped pigments. A stable light ink selected above
        this luminance remains readable as the masses change position.
        External test/reference rasters keep their own spatial sampling.
        """
        if self._frame.isNull() or displayed.cacheKey() != self._frame.cacheKey():
            return None
        return QColor(max(self._from_colors + self._target_colors, key=relative_luminance))

    def render(self, size: QSize, colors: tuple[QColor, ...], now: float, intensity: float = 0.74) -> QImage:
        now = float(now)
        intensity = min(1.0, max(0.0, float(intensity)))
        delta = self._clock.advance(now)
        self._motion_seconds += delta
        # Strength changes alter future speed, never the complete elapsed
        # phase. Charge the interval since the previous sample at its old
        # speed, so pausing and restoring flow keep the displayed geometry.
        previous_intensity = intensity if self._last_intensity is None else self._last_intensity
        self._geometry_seconds += delta * previous_intensity
        self._last_intensity = intensity
        target = self._palette_slots(colors)
        retargeted = target != self._target_colors
        if retargeted:
            self._from_colors = self._painted_colors or target
            self._target_colors = target
            self._palette_started_at = self._motion_seconds
        raster_size = self._raster_size(size)
        key = (raster_size.width(), raster_size.height(), intensity)
        if (retargeted or key != self._frame_key or self._frame.isNull()
                or self._frame_at is None or now < self._frame_at
                or now - self._frame_at >= self.FRAME_SECONDS - 1e-9):
            self._painted_colors = self._palette_at(self._motion_seconds)
            self._frame = self._paint_field(raster_size, self._painted_colors)
            self._frame_key, self._frame_at = key, now
        return QImage(self._frame)
