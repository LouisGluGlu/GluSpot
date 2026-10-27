"""Frozen pre-optimization glass shader for numerical regression tests."""
import math
import sys
from functools import lru_cache
import numpy as np
from PIL import Image
from PIL.ImageFilter import GaussianBlur
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from local.music_control_glass import control_geometry, _sample, GLASS_MATERIAL_MAX_EDGE

@lru_cache(maxsize=32)
def _reference_sampling_maps(geometry_key, pad, sw, sh):
    """Cache the scene-independent bilinear indices and weights."""
    samples, _, alpha = control_geometry(*geometry_key)
    h, w = alpha.shape
    full_w, full_h = w + 2 * pad, h + 2 * pad
    maps = []
    for x, y in samples:
        x = np.clip((x + pad) * sw / full_w - .5, 0., sw - 1.001)
        y = np.clip((y + pad) * sh / full_h - .5, 0., sh - 1.001)
        ix, iy = x.astype(np.int32), y.astype(np.int32)
        maps.append((iy * sw + ix, sw, x - ix, y - iy))
    return tuple(maps)


def reference_shade(scene, scene_bounds, rect, dpr, spec, geometry_key, tint):
    geometry = control_geometry(*geometry_key)
    _, light, alpha = geometry
    h, w = alpha.shape
    pad = math.ceil((spec.lens * (1. + spec.chroma) + spec.blur * 3.) * dpr + 3.)
    full_w, full_h = w + 2 * pad, h + 2 * pad
    ratio = min(1., GLASS_MATERIAL_MAX_EDGE / max(full_w, full_h))
    sw, sh = max(2, round(full_w * ratio)), max(2, round(full_h * ratio))
    area = rect.adjusted(-pad / dpr, -pad / dpr, pad / dpr, pad / dpr)
    crop = QImage(sw, sh, QImage.Format.Format_RGB32)
    crop.fill(QColor(112, 112, 112))
    if not scene.isNull() and scene_bounds.contains(area):
        source = QRectF((area.x() - scene_bounds.x()) * scene.width() / scene_bounds.width(),
                        (area.y() - scene_bounds.y()) * scene.height() / scene_bounds.height(),
                        area.width() * scene.width() / scene_bounds.width(),
                        area.height() * scene.height() / scene_bounds.height())
        with QPainter(crop) as p:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(QRectF(0, 0, sw, sh), scene, source)
        # RGB32 has native BGRA bytes on little-endian Qt platforms. Pillow
        # converts directly while copying, avoiding a second QImage raster.
        raw_format = "BGRX" if sys.byteorder == "little" else "XRGB"
        pixels = Image.frombuffer("RGB", (sw, sh), crop.constBits(), "raw", raw_format, 0, 1)
    elif not scene.isNull():
        # Clamp with edge replication, including controls near window edges.
        sx = (area.x() - scene_bounds.x()) * scene.width() / scene_bounds.width()
        sy = (area.y() - scene_bounds.y()) * scene.height() / scene_bounds.height()
        rgba = scene.convertToFormat(QImage.Format.Format_RGBA8888)
        source = np.frombuffer(rgba.constBits(), np.uint8).reshape(rgba.height(), rgba.bytesPerLine())[:, :rgba.width() * 4].reshape(rgba.height(), rgba.width(), 4)
        if min(source.shape[:2]) < 2:
            source = np.pad(source, ((0, int(source.shape[0] < 2)), (0, int(source.shape[1] < 2)), (0, 0)), mode="edge")
        xs = sx + (np.arange(sw, dtype=np.float32) + .5) / sw * area.width() * scene.width() / scene_bounds.width() - .5
        ys = sy + (np.arange(sh, dtype=np.float32) + .5) / sh * area.height() * scene.height() / scene_bounds.height() - .5
        xx, yy = np.meshgrid(xs, ys)
        pixels = np.stack([_sample(source[..., c].astype(np.float32), xx, yy) for c in range(3)], axis=-1).astype(np.uint8)
    else:
        pixels = np.full((sh, sw, 3), 112, np.uint8)
    if not isinstance(pixels, Image.Image):
        pixels = Image.fromarray(pixels)
    pixels = np.asarray(pixels.filter(GaussianBlur(spec.blur * dpr * ratio)), dtype=np.float32)
    flat = pixels.reshape(-1)
    rgb_channels = []
    for c, (index, width, fx, fy) in enumerate(_reference_sampling_maps(geometry_key, pad, sw, sh)):
        index = index * 3 + c
        top = flat[index] * (1. - fx) + flat[index + 3] * fx
        bottom = flat[index + width * 3] * (1. - fx) + flat[index + width * 3 + 3] * fx
        rgb_channels.append(top * (1. - fy) + bottom * fy)
    rgb = np.stack(rgb_channels, axis=-1)
    lum = rgb @ np.array([.2126, .7152, .0722], np.float32)
    rgb = lum[..., None] + (rgb - lum[..., None]) * spec.sat
    if spec.veil:
        dark = float(lum.mean()) / 255. < .55
        amount = spec.veil * (1. if dark else 1.6)
        rgb = rgb * (1. - amount) + (255. if dark else 0.) * amount
    if spec.tint_alpha:
        rgb = rgb * (1. - spec.tint_alpha) + np.array(tint, np.float32) * spec.tint_alpha
    rgb = np.clip(rgb, 0., 255.)
    rgb += (255. - rgb) * light
    rgba = np.empty((h, w, 4), np.uint8)
    rgba[..., :3], rgba[..., 3] = rgb, alpha * 255.
    return QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
