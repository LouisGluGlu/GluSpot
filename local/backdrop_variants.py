"""Static, bounded album-palette fields shared by AlbumFlow callers.

The classic compositor remains in music_surface_background. These fields
mix hue along the shortest HSL arc with a saturation floor, rather than
averaging complementary RGB pigments into a gray/brown wash.
"""
from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage


BACKDROP_VARIANTS = ("classic", "flow", "frosted", "noir")
BACKDROP_MAX_LUMINANCE = .12


def resolve_backdrop_variant(value: str | None = None, *, default: str = "frosted") -> str:
    """Normalize an explicit value or the environment; unknowns use frosted."""
    if value is None:
        value = os.environ.get("GLUSPOT_BACKDROP_VARIANT", default)
    name = str(value).strip().lower()
    return name if name in BACKDROP_VARIANTS else "frosted"


def _palette_hsl(colors: tuple[QColor, ...], weights: tuple[float, ...]):
    chromatic = []
    support = []
    for index, color in enumerate(colors):
        hue, saturation, lightness, _alpha = color.getHslF()
        if hue >= 0. and saturation >= .14 and .035 < lightness < .965:
            chromatic.append((hue, np.clip(saturation, .72, .88)))
            support.append(weights[index] if len(weights) == len(colors) else 1.)
    if not chromatic:
        # A grayscale album stays neutral, rather than acquiring a fake hue.
        return np.array(((0., 0.),), dtype=np.float32), np.ones(1, dtype=np.float32)
    palette = np.asarray(chromatic, dtype=np.float32)
    anchor = palette[0, 0]
    palette[:, 0] = anchor + (palette[:, 0] - anchor + .5) % 1. - .5
    support = np.asarray(support, dtype=np.float32)
    support = np.sqrt(np.clip(np.nan_to_num(support, nan=0., posinf=1., neginf=0.), .02, 1.))
    support /= support.max()
    return palette, support


def _hsl_rgb(hue, saturation, lightness):
    """Vectorized HSL conversion; hue wraps continuously at red."""
    chroma = (1. - np.abs(2. * lightness - 1.)) * saturation
    channels = []
    for offset in (0., 8., 4.):
        k = (hue * 12. + offset) % 12.
        channels.append(lightness - chroma * .5 * np.clip(np.minimum(k - 3., 9. - k), -1., 1.))
    return np.stack(channels, axis=-1)


def paint_backdrop_variant(size: QSize, colors: tuple[QColor, ...], weights: tuple[float, ...],
                           variant: str) -> QImage:
    """Paint one static frame at AlbumFlow's bounded raster size (<=256 px)."""
    width, height = size.width(), size.height()
    y, x = np.mgrid[:height, :width].astype(np.float32)
    x = (x + .5) / width
    y = (y + .5) / height
    palette, support = _palette_hsl(colors, weights)
    if variant == "flow":
        # Analytic Gaussian blobs have no contour edge or diagonal seam.
        # All phases are fixed: login and zero-intensity snapshots agree.
        centers = ((.12, .12, .36, .42), (.88, .24, .40, .38), (.57, .94, .44, .37),
                   (.06, .85, .33, .38), (.95, .82, .35, .36), (.48, .37, .34, .34))
        total = np.full((height, width), .045, dtype=np.float32)
        bloom_energy = np.zeros_like(total)
        hue = np.full_like(total, palette[0, 0] * .045)
        saturation = np.full_like(total, palette[0, 1] * .045)
        for index, ((h, s), strength) in enumerate(zip(palette, support)):
            cx, cy, rx, ry = centers[index % len(centers)]
            domain = np.exp(-1.25 * (((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2)) * strength
            bloom_energy += domain * domain
            total += domain
            hue += domain * h
            saturation += domain * s
        hue /= total
        saturation /= total
        lightness = .018 + .42 * (1. - np.exp(-bloom_energy * 1.5))
        rgb = _hsl_rgb(hue, saturation, lightness)
    else:
        # One broad upper-left bloom; no additional colored corners.
        hue = float(np.average(palette[:, 0], weights=support))
        saturation = float(np.average(palette[:, 1], weights=support))
        glow = np.exp(-.5 * (((x - .13) / .42) ** 2 + ((y - .12) / .48) ** 2))
        if variant == "frosted":
            lightness = .018 + .105 * glow
        else:  # noir: near-pure black with only a faint palette vignette
            lightness = .003 + .024 * glow
        rgb = _hsl_rgb(hue, saturation, lightness)
    # A per-pixel linear-light bound preserves hue while keeping white ink
    # above 6:1 contrast before either page's reading veil.
    linear = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
    luminance = linear @ np.array((.2126, .7152, .0722), dtype=np.float32)
    linear *= np.minimum(1., BACKDROP_MAX_LUMINANCE / np.maximum(luminance, 1e-8))[..., None]
    rgb = np.where(linear <= .0031308, linear * 12.92, 1.055 * linear ** (1. / 2.4) - .055)
    # Stable ordered dither plus fine deterministic grain prevents bands.
    # There is no RNG/time seed and no fresh noise work on cache hits.
    bayer = np.array(((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5)), dtype=np.float32)
    iy, ix = np.indices((height, width), dtype=np.uint32)
    hashed = (ix * 374761393 + iy * 668265263) ^ ((ix + iy * 31) * 1274126177)
    grain = ((hashed >> 16) & 255).astype(np.float32) / 255. - .5
    dither = (bayer[iy % 4, ix % 4] + .5) / 16. - .5
    amplitude = .6 if variant == "noir" else 1.2
    pixels = np.clip(np.rint(rgb * 255. + dither[..., None] + grain[..., None] * amplitude), 0, 255)
    rgba = np.empty((height, width, 4), dtype=np.uint8)
    rgba[..., :3] = pixels
    rgba[..., 3] = 255
    return QImage(rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
