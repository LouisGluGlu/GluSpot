"""Slow avatar/icon color field for the source page.

The quantizer follows the confirmed page example: ignore near-gray pixels,
bucket the rest, and keep the strongest non-gray mass. Motion is five soft
blobs on a 14–19 s loop. Darkness stays in the same family as the library
backdrop variants so the page does not become a second, brighter theme.
"""

from __future__ import annotations

from collections import OrderedDict
from functools import lru_cache

import numpy as np
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage

from local.backdrop_variants import BACKDROP_MAX_LUMINANCE
from local.music_source_icons import music_source_icon_image


# role, center, radius, travel (canvas fraction), peak scale, opacity, seconds
_BLOBS = (
    ("icon", 0.21, 0.17, 0.31, 0.37, 0.050, 0.044, 1.08, 0.78, 16.0),
    ("icon", 0.88, 0.93, 0.26, 0.31, -0.036, -0.031, 1.10, 0.68, 19.0),
    ("avatar", 0.52, 0.42, 0.22, 0.26, -0.022, 0.042, 1.06, 0.72, 14.0),
    ("avatar", 0.75, 0.14, 0.19, 0.22, -0.023, 0.018, 1.12, 0.55, 18.0),
    ("icon", 0.23, 0.78, 0.17, 0.20, 0.020, -0.016, 1.05, 0.55, 15.0),
)
FIELD_PERIODS = tuple(blob[-1] for blob in _BLOBS)
FIELD_BLEND_SECONDS = 0.6
# One step of a 60 fps clock. The 600 ms blend is a crossfade of two cached
# plates, so this interval does not rebuild the blobs.
FIELD_BLEND_INTERVAL_MS = 16
# Library variants stay distinguishable, and frosted uses the shared cap.
_VARIANT_SHADE = {"classic": 1.0, "flow": 0.90, "frosted": 0.78, "noir": 0.55}
_VARIANT_CAP = {"classic": 0.265, "flow": 0.16, "frosted": BACKDROP_MAX_LUMINANCE, "noir": 0.045}
_avatar_colors: OrderedDict[int, QColor] = OrderedDict()


def _channel(value: float) -> int:
    return max(0, min(255, int(value)))


def boost_saturation(color: QColor, factor: float) -> QColor:
    """Move each channel away from the mean, matching the example's boost."""
    red, green, blue, _alpha = color.getRgb()
    average = (red + green + blue) / 3.0
    return QColor(
        _channel(average + (red - average) * factor),
        _channel(average + (green - average) * factor),
        _channel(average + (blue - average) * factor),
    )


def blend_color(start: QColor, end: QColor, amount: float) -> QColor:
    amount = 0.0 if amount < 0.0 else 1.0 if amount > 1.0 else amount
    return QColor(
        _channel(start.red() + (end.red() - start.red()) * amount),
        _channel(start.green() + (end.green() - start.green()) * amount),
        _channel(start.blue() + (end.blue() - start.blue()) * amount),
    )


def extract_dominant_color(image: QImage) -> QColor:
    """Quantize a non-gray mass. Gray, near-black and near-white stay out."""
    fallback = QColor(180, 140, 120)
    if image is None or image.isNull() or image.width() < 1 or image.height() < 1:
        return fallback
    side = min(64, image.width(), image.height())
    sample = image
    if image.width() != side or image.height() != side:
        sample = image.scaled(side, side, Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
    sample = sample.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = sample.width(), sample.height()
    raw = np.frombuffer(bytes(sample.constBits()), dtype=np.uint8).reshape(height, sample.bytesPerLine())
    pixels = raw[:, :width * 4].reshape(height, width, 4)
    red = pixels[..., 0].astype(np.float32)
    green = pixels[..., 1].astype(np.float32)
    blue = pixels[..., 2].astype(np.float32)
    opaque = pixels[..., 3] >= 200
    if not np.any(opaque):
        return fallback
    peak = np.maximum(np.maximum(red, green), blue)
    floor = np.minimum(np.minimum(red, green), blue)
    saturation = np.where(peak == 0, 0.0, (peak - floor) / np.maximum(peak, 1.0))
    luminance = (red * 0.2126 + green * 0.7152 + blue * 0.0722) / 255.0
    gray = (luminance < 0.18) | (luminance > 0.90) | (saturation < 0.14) | ~opaque
    keep = ~gray
    if np.any(keep):
        score = (0.5 + saturation) * (1.0 - np.abs(luminance - 0.55) * 1.2)
        warm = (red > green) & (red > blue) & (green > blue * 0.7)
        score = np.where(warm, score * 1.35, score)
        score = np.where(keep, score, 0.0)
        key = ((red // 24).astype(np.int32) << 16) | ((green // 24).astype(np.int32) << 8) | (blue // 24).astype(np.int32)
        flat_key = key[keep].ravel()
        flat_score = score[keep].ravel().astype(np.float64)
        flat_red = red[keep].ravel().astype(np.float64)
        flat_green = green[keep].ravel().astype(np.float64)
        flat_blue = blue[keep].ravel().astype(np.float64)
        unique, inverse = np.unique(flat_key, return_inverse=True)
        scores = np.zeros(unique.shape[0], dtype=np.float64)
        sums_red = np.zeros_like(scores)
        sums_green = np.zeros_like(scores)
        sums_blue = np.zeros_like(scores)
        counts = np.zeros_like(scores)
        np.add.at(scores, inverse, flat_score)
        np.add.at(sums_red, inverse, flat_red)
        np.add.at(sums_green, inverse, flat_green)
        np.add.at(sums_blue, inverse, flat_blue)
        np.add.at(counts, inverse, 1.0)
        best = int(np.argmax(scores))
        count = counts[best]
        return QColor(int(sums_red[best] / count), int(sums_green[best] / count), int(sums_blue[best] / count))
    average = (red + green + blue) / 3.0
    usable = opaque & (average >= 40) & (average <= 220)
    if not np.any(usable):
        return fallback
    return QColor(int(red[usable].mean()), int(green[usable].mean()), int(blue[usable].mean()))


@lru_cache(maxsize=8)
def icon_dominant_color(source_id: str) -> QColor:
    """Platform icon pigment, slightly lifted so the field stays readable as color."""
    image = music_source_icon_image(source_id, QSize(64, 64))
    return boost_saturation(extract_dominant_color(image), 1.15)


def avatar_dominant_color(image: QImage) -> QColor:
    key = int(image.cacheKey())
    cached = _avatar_colors.get(key)
    if cached is not None:
        _avatar_colors.move_to_end(key)
        return cached
    color = boost_saturation(extract_dominant_color(image), 1.05)
    _avatar_colors[key] = color
    while len(_avatar_colors) > 8:
        _avatar_colors.popitem(last=False)
    return color


def _raster_size(size: QSize) -> tuple[int, int]:
    width, height = max(1, size.width()), max(1, size.height())
    scale = min(1.0, 256.0 / width, 256.0 / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _cap_luminance(rgb: np.ndarray, cap: float) -> np.ndarray:
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    luminance = linear @ np.array((0.2126, 0.7152, 0.0722), dtype=np.float32)
    linear *= np.minimum(1.0, cap / np.maximum(luminance, 1e-6))[..., None]
    return np.where(linear <= 0.0031308, linear * 12.92, 1.055 * linear ** (1.0 / 2.4) - 0.055)


def render_source_color_field(size: QSize, avatar: QColor | None, icon: QColor,
                               seconds: float, variant: str = "frosted") -> QImage:
    """Paint one bounded frame. ``avatar is None`` uses only the icon pigment."""
    width, height = _raster_size(size)
    y, x = np.mgrid[:height, :width].astype(np.float32)
    x = (x + 0.5) / width
    y = (y + 0.5) / height
    field = np.empty((height, width, 3), dtype=np.float32)
    field[..., 0] = 14.0 / 255.0
    field[..., 1] = 14.0 / 255.0
    field[..., 2] = 16.0 / 255.0
    icon_rgb = np.array(QColor(icon).getRgbF()[:3], dtype=np.float32)
    avatar_rgb = icon_rgb if avatar is None else np.array(QColor(avatar).getRgbF()[:3], dtype=np.float32)
    for role, cx, cy, rx, ry, dx, dy, scale, opacity, period in _BLOBS:
        phase = 0.5 - 0.5 * np.cos(2.0 * np.pi * (float(seconds) / period))
        grown = 1.0 + (scale - 1.0) * phase
        spot = np.exp(-((((x - (cx + dx * phase)) / (rx * grown)) ** 2)
                        + (((y - (cy + dy * phase)) / (ry * grown)) ** 2)))
        color = avatar_rgb if role == "avatar" else icon_rgb
        alpha = (opacity * spot)[..., None]
        field = field * (1.0 - alpha) + color * alpha
    # The example's edge veil keeps captions off the brightest mass.
    distance = np.hypot((x - 0.50) / 0.55, (y - 0.40) / 0.425)
    veil = np.clip(np.where(distance < 0.65, distance / 0.65 * 0.533,
                            0.533 + (distance - 0.65) / 0.35 * 0.29), 0.0, 0.82)
    base = np.array((14.0 / 255.0, 14.0 / 255.0, 16.0 / 255.0), dtype=np.float32)
    field = field * (1.0 - veil[..., None]) + base * veil[..., None]
    shade = _VARIANT_SHADE.get(variant, _VARIANT_SHADE["frosted"])
    cap = _VARIANT_CAP.get(variant, _VARIANT_CAP["frosted"])
    field = _cap_luminance(np.clip(field * shade, 0.0, 1.0), cap)
    bayer = np.array(((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5)), dtype=np.float32)
    iy, ix = np.indices((height, width), dtype=np.uint32)
    hashed = (ix * 374761393 + iy * 668265263) ^ ((ix + iy * 31) * 1274126177)
    grain = ((hashed >> 16) & 255).astype(np.float32) / 255.0 - 0.5
    dither = (bayer[iy % 4, ix % 4] + 0.5) / 16.0 - 0.5
    pixels = np.clip(np.rint(field * 255.0 + dither[..., None] + grain[..., None] * 1.2), 0, 255).astype(np.uint8)
    rgba = np.empty((height, width, 4), dtype=np.uint8)
    rgba[..., :3] = pixels
    rgba[..., 3] = 255
    return QImage(rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()


def _tight_rgba(image: QImage) -> np.ndarray:
    sample = image.convertToFormat(QImage.Format.Format_RGBA8888)
    raw = np.frombuffer(bytes(sample.constBits()), dtype=np.uint8)
    view = raw.reshape(sample.height(), sample.bytesPerLine())[:, :sample.width() * 4]
    return view.reshape(sample.height(), sample.width(), 4)


def crossfade_color_fields(start: QImage, end: QImage, amount: float) -> QImage:
    """Blend two cached plates. No blob rebuild, blur, or color sampling."""
    if start.isNull():
        return end.copy()
    if end.isNull() or amount <= 0.0:
        return start.copy()
    if amount >= 1.0:
        return end.copy()
    before = _tight_rgba(start).astype(np.float32)
    after = _tight_rgba(end)
    if before.shape != after.shape:
        return (end if amount >= 0.5 else start).copy()
    mixed = before * (1.0 - amount) + after.astype(np.float32) * amount
    pixels = np.clip(np.rint(mixed), 0, 255).astype(np.uint8)
    height, width, _channels = pixels.shape
    return QImage(pixels.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
