"""Precomputed, bounded lyric/list veil for the isolated narrow study.

Only ``prepare`` performs image filtering.  A caller prepares a canonical
31-pixel scroll pose outside paint, then ``paint`` moves that cached texture
by the remaining fraction of a row.  The masks stay in window coordinates.
The production background and clear-glass renderers are deliberately absent.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import math
from typing import Callable, Hashable

from PIL import Image, ImageFilter
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter


TOP_END = 88.0
TOP_FEATHER_START = 64.0
BOTTOM_HEIGHT = 128.0
BOTTOM_FEATHER = 32.0
MAX_BLUR = 16.0
DIM_ALPHA = .55
DIM_COLOR = QColor.fromRgbF(0.0, 0.0, 0.0, DIM_ALPHA)
CACHE_LIMIT = 6
RASTER_SCALE = .5
SCROLL_GUARD = 16.0


@dataclass(frozen=True)
class _RadiusStrip:
    degree: int
    top: bool
    target_start: float
    target_end: float
    source_start: float
    scale: float
    scale_y: float
    rect: QRectF


@dataclass(frozen=True)
class _Bands:
    image: QImage
    width: float
    height: float
    scale: float
    strips: tuple[_RadiusStrip, ...]
    feathers: tuple[QRectF, QRectF] | None = None


class VeilOverlay:
    """Share one veil implementation between transparent content sources.

    Cache keys are owned by the caller and must include text/list layout,
    theme, DPR and scroll position rounded to 31 logical pixels.  The scene's
    animated background is not part of the transparent content source.
    ``prepare`` accepts a factory so a cache hit does not rasterize content.
    ``hits``/``misses`` count prepare lookups; paint never changes those counts.
    """

    TOP_END = TOP_END
    TOP_FEATHER_START = TOP_FEATHER_START
    BOTTOM_HEIGHT = BOTTOM_HEIGHT
    BOTTOM_FEATHER = BOTTOM_FEATHER
    MAX_BLUR = MAX_BLUR
    DIM_ALPHA = DIM_ALPHA
    CACHE_LIMIT = CACHE_LIMIT

    def __init__(self):
        self.cache: OrderedDict[Hashable, _Bands] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.builds = 0
        self.cache_bytes = 0

    @property
    def cache_size(self):
        return len(self.cache)

    def has_key(self, key):
        return key in self.cache

    @staticmethod
    def band_strength(y, height=640.0):
        """Logical-space blur/dim strength, exactly zero in the middle."""
        if y < TOP_END:
            return max(0.0, min(1.0, (TOP_END - y) / (TOP_END - TOP_FEATHER_START)))
        bottom = float(height) - BOTTOM_HEIGHT
        if y > bottom:
            return max(0.0, min(1.0, (y - bottom) / BOTTOM_FEATHER))
        return 0.0

    def clear(self):
        self.cache.clear()
        self.cache_bytes = 0

    @staticmethod
    def _profile_region(degree, top, height):
        """Support of one Gaussian interpolation weight in window space."""
        if top:
            unit = (TOP_END - TOP_FEATHER_START) / (MAX_BLUR / 2)
            return (0.0 if degree == 8 else max(0.0, TOP_END - (degree + 1) * unit),
                    min(TOP_END, TOP_END - (degree - 1) * unit))
        start = height - BOTTOM_HEIGHT
        unit = BOTTOM_FEATHER / (MAX_BLUR / 2)
        return (max(start, start + (degree - 1) * unit),
                height if degree == 8 else min(height, start + (degree + 1) * unit))

    def prepare(self, key, source: QImage | Callable[[], QImage]):
        """Filter a half-resolution canonical pose once, outside paint.

        The packed image holds Gaussian scale-space strips with half-row
        motion guards.  Broad Gaussian outputs can be stored smaller after
        filtering, keeping six DPR 2 entries below 2.8 MB.  Filtering uses
        premultiplied channels, preventing dirty transparent color fringes.
        """
        cached = self.cache.pop(key, None)
        if cached is not None:
            self.hits += 1
            self.cache[key] = cached
            return cached.image
        self.misses += 1
        original = source() if callable(source) else source
        if original.isNull():
            raise ValueError("veil source must be a nonempty transparent content image")
        dpr = original.devicePixelRatio()
        width, height = original.width() / dpr, original.height() / dpr
        scale = dpr * RASTER_SCALE
        reduced = original.scaled(
            max(1, round(width * scale)), max(1, round(height * scale)),
            Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation,
        ).convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
        reduced.setDevicePixelRatio(scale)
        pixels = Image.frombytes("RGBA", (reduced.width(), reduced.height()), bytes(reduced.bits()))
        pending = []
        for degree in range(1, int(MAX_BLUR / 2) + 1):
            # A Gaussian with σ≥4 has no useful detail at the original sample
            # frequency.  This affects storage after the exact filter only.
            reduction = 1.0 if degree == 1 else .5 if degree < 4 else .25
            for top in (True, False):
                target_start, target_end = self._profile_region(degree, top, height)
                source_start = target_start - SCROLL_GUARD
                source_end = target_end + SCROLL_GUARD
                first = math.floor(source_start * scale)
                last = math.ceil(source_end * scale)
                radius = degree * 2 * scale
                padding = math.ceil(radius * 3)
                crop = Image.new("RGBA", (reduced.width(), last - first + 2 * padding))
                available_first = max(0, first - padding)
                available_last = min(reduced.height(), last + padding)
                crop.paste(pixels.crop((0, available_first, reduced.width(), available_last)),
                           (0, available_first - first + padding))
                blurred = crop.filter(ImageFilter.GaussianBlur(radius)).crop(
                    (0, padding, reduced.width(), padding + last - first))
                size = (max(1, round(reduced.width() * reduction)), max(1, round(blurred.height * reduction)))
                # Resize premultiplied channels independently; Pillow's RGBA
                # resize would otherwise premultiply this content a second time.
                stored = Image.merge("RGBA", tuple(channel.resize(size, Image.Resampling.BILINEAR)
                                                     for channel in blurred.split()))
                pending.append((degree, top, target_start, target_end, first / scale,
                                stored.width / width, stored.height / ((last - first) / scale), stored))
        # Shelf-pack the varying resolutions into one RGBA image per key.
        # At DPR 1 preserve feather ink at native width after mixing the
        # half-resolution Gaussian levels.  DPR 2 has enough half-scale
        # samples already, and retaining its native width wastes cache space.
        atlas_width = original.width() if dpr <= 1.0 else reduced.width()
        cursor_x = cursor_y = shelf_height = 0
        placements = []
        for item in pending:
            stored = item[-1]
            if cursor_x + stored.width > atlas_width:
                cursor_y += shelf_height
                cursor_x = shelf_height = 0
            placements.append((cursor_x, cursor_y))
            cursor_x += stored.width
            shelf_height = max(shelf_height, stored.height)
        atlas = Image.new("RGBA", (atlas_width, cursor_y + shelf_height))
        strips = []
        for item, (x, y) in zip(pending, placements):
            degree, top, target_start, target_end, source_start, sample_scale, sample_scale_y, stored = item
            atlas.paste(stored, (x, y))
            strips.append(_RadiusStrip(degree, top, target_start, target_end, source_start,
                                       sample_scale, sample_scale_y, QRectF(x, y, stored.width, stored.height)))
        data = atlas.tobytes()
        image = QImage(data, atlas.width, atlas.height, atlas.width * 4,
                       QImage.Format.Format_RGBA8888_Premultiplied).copy()
        provisional = _Bands(image, width, height, scale, tuple(strips))
        # The overwhelmingly common settled scroll pose needs only two image
        # draws per band.  Precompose its feathers in the same packed image;
        # the broad 16px plateaus already exist in the scale-space strips.
        # Keep mask interpolation at native vertical resolution.  At .5 DPR,
        # a 3px triangular radius weight would occupy a fractional raster row.
        # Horizontal downsampling remains safe after Gaussian filtering.
        settled = QImage(original.size(), QImage.Format.Format_RGBA8888_Premultiplied)
        settled.setDevicePixelRatio(dpr)
        settled.fill(Qt.GlobalColor.transparent)
        with QPainter(settled) as layer:
            self._paint_band(layer, original, provisional, 1.0, True, 0.0)
            self._paint_band(layer, original, provisional, 1.0, False, 0.0)
        settled_pixels = Image.frombytes("RGBA", (settled.width(), settled.height()), bytes(settled.bits()))
        top_first, top_last = round(TOP_FEATHER_START * dpr), round(TOP_END * dpr)
        bottom_first = round((height - BOTTOM_HEIGHT) * dpr)
        bottom_last = round((height - BOTTOM_HEIGHT + BOTTOM_FEATHER) * dpr)
        top_feather = settled_pixels.crop((0, top_first, settled.width(), top_last))
        bottom_feather = settled_pixels.crop((0, bottom_first, settled.width(), bottom_last))
        top_feather = Image.merge("RGBA", tuple(channel.resize((atlas.width, top_feather.height), Image.Resampling.BILINEAR)
                                                for channel in top_feather.split()))
        bottom_feather = Image.merge("RGBA", tuple(channel.resize((atlas.width, bottom_feather.height), Image.Resampling.BILINEAR)
                                                   for channel in bottom_feather.split()))
        packed = Image.new("RGBA", (atlas.width, atlas.height + top_last - top_first + bottom_last - bottom_first))
        packed.paste(atlas, (0, 0))
        packed.paste(top_feather, (0, atlas.height))
        packed.paste(bottom_feather,
                     (0, atlas.height + top_last - top_first))
        feathers = (QRectF(0, atlas.height, atlas.width, top_last - top_first),
                    QRectF(0, atlas.height + top_last - top_first, atlas.width, bottom_last - bottom_first))
        data = packed.tobytes()
        image = QImage(data, packed.width, packed.height, packed.width * 4,
                       QImage.Format.Format_RGBA8888_Premultiplied).copy()
        cached = _Bands(image, width, height, scale, tuple(strips), feathers)
        self.cache[key] = cached
        self.cache_bytes += image.sizeInBytes()
        self.builds += 1
        while len(self.cache) > CACHE_LIMIT:
            _, old = self.cache.popitem(last=False)
            self.cache_bytes -= old.image.sizeInBytes()
        return image

    @staticmethod
    def _source_rect(rect, scale):
        return QRectF(rect.x() * scale, rect.y() * scale, rect.width() * scale, rect.height() * scale)

    @staticmethod
    def _weight_mask(degree, top, height, local_start):
        if top:
            unit = (TOP_END - TOP_FEATHER_START) / (MAX_BLUR / 2)
            center = TOP_END - degree * unit
            gradient = QLinearGradient(0, center - unit - local_start, 0, center + unit - local_start)
            levels = (1.0, 1.0, 0.0) if degree == 8 else (0.0, 1.0, 1.0) if degree == 0 else (0.0, 1.0, 0.0)
        else:
            unit = BOTTOM_FEATHER / (MAX_BLUR / 2)
            center = height - BOTTOM_HEIGHT + degree * unit
            gradient = QLinearGradient(0, center - unit - local_start, 0, center + unit - local_start)
            levels = (0.0, 1.0, 1.0) if degree == 8 else (1.0, 1.0, 0.0) if degree == 0 else (0.0, 1.0, 0.0)
        for stop, alpha in zip((0.0, .5, 1.0), levels):
            gradient.setColorAt(stop, QColor.fromRgbF(1.0, 1.0, 1.0, alpha))
        return gradient

    def _paint_band(self, painter, source, cached, opacity, top, delta):
        """Blend transparent ink additively before compositing on the scene."""
        dpr = source.devicePixelRatio()
        y = 0.0 if top else cached.height - BOTTOM_HEIGHT
        band_height = TOP_END if top else BOTTOM_HEIGHT
        band = QImage(source.width(), max(1, round(band_height * dpr)),
                      QImage.Format.Format_ARGB32_Premultiplied)
        band.setDevicePixelRatio(dpr)
        band.fill(Qt.GlobalColor.transparent)
        local = QRectF(0, 0, cached.width, band_height)
        with QPainter(band) as layer:
            layer.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            layer.setOpacity(1.0 - opacity)
            layer.drawImage(local, source, self._source_rect(QRectF(0, y, cached.width, band_height), dpr))
            layer.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            layer.setOpacity(opacity)
            if cached.feathers is not None and abs(delta) < .000001:
                feather_y = TOP_FEATHER_START if top else cached.height - BOTTOM_HEIGHT
                feather_height = TOP_END - TOP_FEATHER_START if top else BOTTOM_FEATHER
                layer.drawImage(QRectF(0, feather_y - y, cached.width, feather_height), cached.image,
                                cached.feathers[0 if top else 1])
                plateau_start = 0.0 if top else cached.height - BOTTOM_HEIGHT + BOTTOM_FEATHER
                plateau_end = TOP_FEATHER_START if top else cached.height
                strip = next(s for s in cached.strips if s.degree == 8 and s.top == top)
                sample = QRectF(strip.rect.x(), strip.rect.y() + (plateau_start - strip.source_start) * strip.scale_y,
                                strip.rect.width(), (plateau_end - plateau_start) * strip.scale_y)
                layer.drawImage(QRectF(0, plateau_start - y, cached.width, plateau_end - plateau_start),
                                cached.image, sample)
                painter.drawImage(QPointF(0, y), band)
                return
            # Radius weights are evaluated at the destination y.  Sampling
            # source y-delta moves lyrics, while the blur feather stays put.
            for degree in range(9):
                start, end = self._profile_region(degree, top, cached.height)
                tile = QImage(source.width(), max(1, round((end - start) * dpr)),
                              QImage.Format.Format_ARGB32_Premultiplied)
                tile.setDevicePixelRatio(dpr)
                tile.fill(Qt.GlobalColor.transparent)
                tile_rect = QRectF(0, 0, cached.width, end - start)
                with QPainter(tile) as patch:
                    patch.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                    if degree == 0:
                        patch.drawImage(tile_rect, source,
                                        self._source_rect(QRectF(0, start, cached.width, end - start), dpr))
                    else:
                        strip = next(s for s in cached.strips if s.degree == degree and s.top == top)
                        sample = QRectF(strip.rect.x(), strip.rect.y() + (start - delta - strip.source_start) * strip.scale_y,
                                        strip.rect.width(), (end - start) * strip.scale_y)
                        patch.drawImage(tile_rect, cached.image, sample)
                    patch.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                    patch.fillRect(tile_rect, self._weight_mask(degree, top, cached.height, start))
                layer.drawImage(QPointF(0, start - y), tile)
        painter.drawImage(QPointF(0, y), band)

    def paint(self, painter, sharp_content, key, opacity, scroll_delta=0.0):
        """Compose prepared pixels and a fixed dim mask; never filter here.

        ``scroll_delta`` is the current content translation relative to the
        quantized canonical source, with magnitude at most half a 31px row.
        Missing preparation leaves sharp content visible and performs no
        hidden expensive work in the paint event.
        """
        alpha = max(0.0, min(1.0, float(opacity)))
        if alpha <= 0:
            painter.drawImage(QPointF(0, 0), sharp_content)
            return
        cached = self.cache.get(key)
        dpr = sharp_content.devicePixelRatio()
        width = sharp_content.width() / dpr
        height = sharp_content.height() / dpr
        painter.save()
        if cached is None:
            painter.drawImage(QPointF(0, 0), sharp_content)
        else:
            middle = QRectF(0, TOP_END, width, max(0.0, height - BOTTOM_HEIGHT - TOP_END))
            painter.drawImage(middle, sharp_content, self._source_rect(middle, dpr))
            self._paint_band(painter, sharp_content, cached, alpha, True, scroll_delta)
            self._paint_band(painter, sharp_content, cached, alpha, False, scroll_delta)
        painter.setOpacity(painter.opacity() * alpha)
        top = QLinearGradient(0, TOP_FEATHER_START, 0, TOP_END)
        top.setColorAt(0, DIM_COLOR)
        top.setColorAt(1, QColor(0, 0, 0, 0))
        painter.fillRect(QRectF(0, 0, width, TOP_END), top)
        bottom = QLinearGradient(0, height - BOTTOM_HEIGHT, 0, height - BOTTOM_HEIGHT + BOTTOM_FEATHER)
        bottom.setColorAt(0, QColor(0, 0, 0, 0))
        bottom.setColorAt(1, DIM_COLOR)
        painter.fillRect(QRectF(0, height - BOTTOM_HEIGHT, width, BOTTOM_HEIGHT), bottom)
        painter.restore()
