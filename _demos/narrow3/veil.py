"""Precomputed, bounded lyric/list veil for the isolated narrow study.

Only ``prepare`` performs image filtering.  A caller prepares a canonical
row-quantized scroll pose outside paint, then ``paint`` moves that cached texture
by the remaining fraction of a row.  The masks stay in window coordinates.
The production background and clear-glass renderers are deliberately absent.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
import math
from typing import Callable, Hashable

from PIL import Image, ImageFilter
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter


# Legacy distances remain chrome travel aliases only; blur geometry is
# proportional to the current height and never uses these fixed shelves.
TOP_END = 214.0
TOP_FEATHER_START = 166.0
BOTTOM_HEIGHT = 192.0
BOTTOM_FEATHER = 80.0
MAX_BLUR = 6.0
DIM_ALPHA = .18
DIM_COLOR = QColor.fromRgbF(0.0, 0.0, 0.0, DIM_ALPHA)
BLUR_LEVELS = 8
PLATEAU_FRACTION = .14
RAMP_FRACTION = .20
CACHE_LIMIT = 6
RASTER_SCALE = .5
# Half of the largest 58px lyric step, with room for interpolation.
SCROLL_GUARD = 32.0


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
    theme, DPR and scroll position rounded to the lyric baseline step.  The scene's
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
        self._prepared_key = None
        self._prepared_bands = None

    @property
    def cache_size(self):
        return len(self.cache)

    def has_key(self, key):
        return key in self.cache

    @staticmethod
    def _smoothstep(value):
        t = max(0.0, min(1.0, value))
        return t * t * (3.0 - 2.0 * t)

    @staticmethod
    def _inverse_smoothstep(value):
        t = max(0.0, min(1.0, value))
        return .5 - math.sin(math.asin(1.0 - 2.0 * t) / 3.0)

    @staticmethod
    def band_strength(y, height=640.0):
        """Symmetric, height-normalized edge blur with zero-slope joins."""
        height = max(1., float(height))
        distance = min(float(y), height - float(y)) / height
        return 1. - VeilOverlay._smoothstep((distance - PLATEAU_FRACTION) / RAMP_FRACTION)

    @staticmethod
    def progress(opacity):
        alpha = max(0., min(1., float(opacity)))
        return DIM_ALPHA * alpha, MAX_BLUR * alpha

    def clear(self):
        self.cache.clear()
        self.cache_bytes = 0
        self._prepared_key = None
        self._prepared_bands = None

    @staticmethod
    @lru_cache(maxsize=144)
    def _profile_region(degree, top, height):
        """Support of one radius weight along the shared smoothstep."""
        low = VeilOverlay._inverse_smoothstep((degree - 1) / BLUR_LEVELS)
        high = VeilOverlay._inverse_smoothstep((degree + 1) / BLUR_LEVELS)
        feather = RAMP_FRACTION * height
        end = (PLATEAU_FRACTION + RAMP_FRACTION) * height
        if top:
            return (0.0 if degree == BLUR_LEVELS else end - high * feather,
                    end - low * feather)
        start = height - end
        return (start + low * feather,
                height if degree == BLUR_LEVELS else start + high * feather)

    def prepare(self, key, source: QImage | Callable[[], QImage]):
        """Filter a half-resolution canonical pose once, outside paint.

        The packed image holds Gaussian scale-space strips with half-row
        motion guards.  Broad Gaussian outputs can be stored smaller after
        filtering, keeping six DPR 2 entries below 2.8 MB.  Filtering uses
        premultiplied channels, preventing dirty transparent color fringes.
        """
        if self._prepared_bands is not None and key == self._prepared_key:
            self.hits += 1
            self._prepared_key = key
            return self._prepared_bands.image
        cached = self.cache.pop(key, None)
        if cached is not None:
            self.hits += 1
            self.cache[key] = cached
            self._prepared_key, self._prepared_bands = key, cached
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
        for degree in range(1, BLUR_LEVELS + 1):
            # A Gaussian with σ≥4 has no useful detail at the original sample
            # frequency.  This affects storage after the exact filter only.
            reduction = (1.0 if degree == 1 else .5 if degree < 4 else .25) * min(1.0, 1.0 / dpr)
            if dpr > 1 and degree == BLUR_LEVELS:
                reduction *= .875
            for top in (True, False):
                target_start, target_end = self._profile_region(degree, top, height)
                source_start = target_start - SCROLL_GUARD
                source_end = target_end + SCROLL_GUARD
                first = math.floor(source_start * scale)
                last = math.ceil(source_end * scale)
                radius = degree * MAX_BLUR / BLUR_LEVELS * scale
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
        # Pack the guarded strips into one bounded atlas. A skyline avoids
        # reserving a whole full-width shelf for each tall, narrow blur level.
        # Feather samples keep native width so the clear edge never loses ink.
        atlas_width = original.width()
        skyline = [0] * atlas_width
        pending.sort(key=lambda item: (item[-1].height, item[-1].width), reverse=True)
        placements = []
        candidates = {0}
        for item in pending:
            stored = item[-1]
            choices = [(max(skyline[x:x + stored.width]), x)
                       for x in candidates if x + stored.width <= atlas_width]
            y, x = min(choices)
            placements.append((x, y))
            skyline[x:x + stored.width] = [y + stored.height] * stored.width
            candidates.add(x + stored.width)
        atlas = Image.new("RGBA", (atlas_width, max(skyline)))
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
        # Compose radius weights before compacting the feather samples. The
        # clear contribution is deliberately excluded here and is drawn from
        # the live native source during composition.
        settled = QImage(original.size(), QImage.Format.Format_RGBA8888_Premultiplied)
        settled.setDevicePixelRatio(dpr)
        settled.fill(Qt.GlobalColor.transparent)
        with QPainter(settled) as layer:
            self._paint_band(layer, original, provisional, 1.0, True, 0.0, include_clear=False)
            self._paint_band(layer, original, provisional, 1.0, False, 0.0, include_clear=False)
        settled_pixels = Image.frombytes("RGBA", (settled.width(), settled.height()), bytes(settled.bits()))
        top_first = round(PLATEAU_FRACTION * height * dpr)
        top_last = round((PLATEAU_FRACTION + RAMP_FRACTION) * height * dpr)
        bottom_first = round((1. - PLATEAU_FRACTION - RAMP_FRACTION) * height * dpr)
        bottom_last = round((1. - PLATEAU_FRACTION) * height * dpr)
        top_feather = settled_pixels.crop((0, top_first, settled.width(), top_last))
        bottom_feather = settled_pixels.crop((0, bottom_first, settled.width(), bottom_last))
        # Feather samples contain only radius >=2px ink. The smooth profile
        # permits a .875x vertical raster at DPR2; the sharp contribution stays
        # native and the Gaussian strips retain their own motion guards.
        feather_scale = 1.0 if dpr <= 1.0 else .875 / dpr
        top_size = (atlas.width, max(1, round(top_feather.height * feather_scale)))
        bottom_size = (atlas.width, max(1, round(bottom_feather.height * feather_scale)))
        top_feather = Image.merge("RGBA", tuple(channel.resize(top_size, Image.Resampling.BILINEAR)
                                                for channel in top_feather.split()))
        bottom_feather = Image.merge("RGBA", tuple(channel.resize(bottom_size, Image.Resampling.BILINEAR)
                                                   for channel in bottom_feather.split()))
        packed = Image.new("RGBA", (atlas.width, atlas.height + top_feather.height + bottom_feather.height))
        packed.paste(atlas, (0, 0))
        packed.paste(top_feather, (0, atlas.height))
        packed.paste(bottom_feather, (0, atlas.height + top_feather.height))
        feathers = (QRectF(0, atlas.height, atlas.width, top_feather.height),
                    QRectF(0, atlas.height + top_feather.height, atlas.width, bottom_feather.height))
        data = packed.tobytes()
        image = QImage(data, packed.width, packed.height, packed.width * 4,
                       QImage.Format.Format_RGBA8888_Premultiplied).copy()
        cached = _Bands(image, width, height, scale, tuple(strips), feathers)
        self.cache[key] = cached
        self._prepared_key, self._prepared_bands = key, cached
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
    @lru_cache(maxsize=144)
    def _weight_mask(degree, top, height, local_start):
        start = PLATEAU_FRACTION * height if top else (1. - PLATEAU_FRACTION - RAMP_FRACTION) * height
        end = (PLATEAU_FRACTION + RAMP_FRACTION) * height if top else (1. - PLATEAU_FRACTION) * height
        gradient = QLinearGradient(0, start - local_start, 0, end - local_start)
        # Shared sample locations preserve a partition of unity between all
        # Gaussian levels. Include every radius knot so the cropped supports
        # meet exactly and cannot leave a bright or dark horizontal seam.
        stops = {i / 64 for i in range(65)}
        stops.update(VeilOverlay._inverse_smoothstep(i / BLUR_LEVELS) for i in range(BLUR_LEVELS + 1))
        for t in sorted(stops):
            strength = VeilOverlay.band_strength(start + t * (end - start), height)
            weight = max(0.0, 1.0 - abs(strength * BLUR_LEVELS - degree))
            gradient.setColorAt(t, QColor.fromRgbF(1.0, 1.0, 1.0, weight))
        return gradient

    @staticmethod
    @lru_cache(maxsize=16)
    def _dim_mask(top, height):
        start = PLATEAU_FRACTION * height if top else (1. - PLATEAU_FRACTION - RAMP_FRACTION) * height
        end = (PLATEAU_FRACTION + RAMP_FRACTION) * height if top else (1. - PLATEAU_FRACTION) * height
        gradient = QLinearGradient(0, start, 0, end)
        for i in range(65):
            t = i / 64
            strength = VeilOverlay.band_strength(start + t * (end - start), height)
            gradient.setColorAt(t, QColor.fromRgbF(0.0, 0.0, 0.0, DIM_ALPHA * strength))
        return gradient

    def _paint_band(self, painter, source, cached, opacity, top, delta, *, include_clear=True):
        """Blend transparent ink additively before compositing on the scene."""
        dpr = source.devicePixelRatio()
        band_height = (PLATEAU_FRACTION + RAMP_FRACTION) * cached.height
        y = 0.0 if top else cached.height - band_height
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
                feather_y = PLATEAU_FRACTION * cached.height if top else cached.height - band_height
                feather_height = RAMP_FRACTION * cached.height
                layer.drawImage(QRectF(0, feather_y - y, cached.width, feather_height), cached.image,
                                cached.feathers[0 if top else 1])
                plateau_start = 0.0 if top else (1. - PLATEAU_FRACTION) * cached.height
                plateau_end = PLATEAU_FRACTION * cached.height if top else cached.height
                strip = next(s for s in cached.strips if s.degree == BLUR_LEVELS and s.top == top)
                sample = QRectF(strip.rect.x(), strip.rect.y() + (plateau_start - strip.source_start) * strip.scale_y,
                                strip.rect.width(), (plateau_end - plateau_start) * strip.scale_y)
                layer.drawImage(QRectF(0, plateau_start - y, cached.width, plateau_end - plateau_start),
                                cached.image, sample)
                # A zero-radius contribution must use the exact live source.
                # Baking it into a half-resolution feather softens the clear
                # edge and can reveal the band's horizontal crop boundary.
                start, end = self._profile_region(0, top, cached.height)
                tile = QImage(source.width(), max(1, round((end - start) * dpr)),
                              QImage.Format.Format_ARGB32_Premultiplied)
                tile.setDevicePixelRatio(dpr)
                tile.fill(Qt.GlobalColor.transparent)
                tile_rect = QRectF(0, 0, cached.width, end - start)
                with QPainter(tile) as patch:
                    patch.drawImage(tile_rect, source,
                                    self._source_rect(QRectF(0, start, cached.width, end - start), dpr))
                    patch.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
                    patch.fillRect(tile_rect, self._weight_mask(0, top, cached.height, start))
                layer.drawImage(QPointF(0, start - y), tile)
                painter.drawImage(QPointF(0, y), band)
                return
            # Radius weights are evaluated at the destination y.  Sampling
            # source y-delta moves lyrics, while the blur feather stays put.
            for degree in range(0 if include_clear else 1, BLUR_LEVELS + 1):
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

    def paint(self, painter, sharp_content, key, opacity, scroll_delta=0.0, *,
              bottom_start=None, include_dim=True):
        """Compose prepared pixels and a fixed dim mask; never filter here.

        ``scroll_delta`` is the current content translation relative to the
        quantized canonical source, with magnitude at most half a lyric step.
        Missing preparation leaves sharp content visible and performs no
        hidden expensive work in the paint event.
        """
        alpha = max(0.0, min(1.0, float(opacity)))
        if alpha <= 0:
            painter.drawImage(QPointF(0, 0), sharp_content)
            return
        cached = self._prepared_bands if key is self._prepared_key else self.cache.get(key)
        dpr = sharp_content.devicePixelRatio()
        width = sharp_content.width() / dpr
        height = sharp_content.height() / dpr
        # A narrow lyric viewport can extend below the transport plateau.
        # Preserve those rows sharply; the caller derives this clip from its
        # layout, without changing the transport's geometry or paint path.
        bottom = (1. - PLATEAU_FRACTION - RAMP_FRACTION) * height
        if bottom_start is not None:
            bottom = min(height, max(bottom, float(bottom_start)))
        painter.save()
        if cached is None:
            painter.drawImage(QPointF(0, 0), sharp_content)
        else:
            top_end = (PLATEAU_FRACTION + RAMP_FRACTION) * height
            middle = QRectF(0, top_end, width, max(0.0, bottom - top_end))
            painter.drawImage(middle, sharp_content, self._source_rect(middle, dpr))
            self._paint_band(painter, sharp_content, cached, alpha, True, scroll_delta)
            painter.save()
            painter.setClipRect(QRectF(0, bottom, width, height - bottom), Qt.ClipOperation.IntersectClip)
            self._paint_band(painter, sharp_content, cached, alpha, False, scroll_delta)
            painter.restore()
        painter.restore()
        if include_dim:
            self.paint_dim(painter, width, height, alpha, bottom_start=bottom)

    def paint_dim(self, painter, width, height, opacity, *, bottom_start=None):
        """Dim belongs to the window, independently of moving lyric ink."""
        edge_end = (PLATEAU_FRACTION + RAMP_FRACTION) * height
        bottom = height - edge_end if bottom_start is None else bottom_start
        painter.save()
        painter.setOpacity(painter.opacity() * opacity)
        painter.fillRect(QRectF(0, 0, width, edge_end), self._dim_mask(True, height))
        painter.fillRect(QRectF(0, bottom, width, max(0., height - bottom)),
                         self._dim_mask(False, height))
        painter.restore()


class BackgroundEdgeVeil:
    """One continuous masked backdrop veil above the caller's live sharp base.

    Preparation is frozen by the caller during drag/reveal. The clear core
    stays transparent, so it continues showing the current animated backdrop.
    No cropped shelf, source replacement or per-paint raster is involved.
    """

    IDLE_RADIUS = MAX_BLUR
    REVEAL_RADIUS = MAX_BLUR
    IDLE_DIM = .04
    REVEAL_DIM = .18
    MAX_SAMPLE_SIDE = 512

    def __init__(self):
        self._key = None
        self._source = QImage()
        self._states = ()
        self._alpha_profile = ()
        self.builds = 0
        self.veil_prepares = 0
        self.veil_stale_frames = 0
        self.veil_seam_max_step = 0.

    @property
    def prepare_count(self):
        return self.veil_prepares

    @property
    def ready(self):
        return bool(self._states)

    @property
    def cache_bytes(self):
        return sum(state.sizeInBytes() for state in self._states)

    @staticmethod
    def progress(progress):
        amount = max(0., min(1., float(progress)))
        return (.04 + .14 * amount, MAX_BLUR)

    def prepare(self, source, width, height):
        """Eight blur samples interpolate continuously into one masked raster."""
        if source.isNull() or width <= 0 or height <= 0:
            return False
        key = (source.cacheKey(), float(width), float(height))
        if key == self._key:
            return False
        scale = min(1., self.MAX_SAMPLE_SIDE / max(float(width), float(height)))
        sample_width, sample_height = max(1, round(width * scale)), max(1, round(height * scale))
        sampled = source.scaled(sample_width, sample_height,
                                Qt.AspectRatioMode.IgnoreAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
        sampled = sampled.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
        pixels = Image.frombytes("RGBA", (sample_width, sample_height),
                                 bytes(sampled.constBits()), "raw", "RGBA", sampled.bytesPerLine())
        profile = tuple(VeilOverlay.band_strength((y + .5) * height / sample_height, height)
                        for y in range(sample_height))
        sample_scale = sample_width / float(width)
        levels = (pixels,) + tuple(
            pixels.filter(ImageFilter.GaussianBlur(MAX_BLUR * level / BLUR_LEVELS * sample_scale))
            for level in range(1, BLUR_LEVELS + 1))
        endpoint = Image.new("RGBA", pixels.size)
        for y, strength in enumerate(profile):
            if strength <= 0.:
                continue
            degree = strength * BLUR_LEVELS
            lower = min(BLUR_LEVELS, int(degree))
            upper = min(BLUR_LEVELS, lower + 1)
            box = (0, y, sample_width, y + 1)
            row = Image.blend(levels[lower].crop(box), levels[upper].crop(box), degree - lower)
            # Premultiplied RGB and alpha use the same continuous mask. The
            # clear-core alpha is zero; native sharp pixels stay untouched.
            rgb = [round(value * strength * (1. - self.REVEAL_DIM * strength))
                   for value in range(256)]
            alpha = [round(value * strength) for value in range(256)]
            endpoint.paste(row.point(rgb * 3 + alpha), (0, y))
        data = endpoint.tobytes()
        image = QImage(data, sample_width, sample_height, sample_width * 4,
                       QImage.Format.Format_RGBA8888_Premultiplied).copy()
        self._key, self._source, self._states = key, sampled, (image,)
        self._alpha_profile = profile
        self.builds += 1
        self.veil_prepares += 1
        # Profile-only diagnostic. PC seam scans report actual image luma,
        # including real artwork detail, separately from mask continuity.
        self.veil_seam_max_step = max((abs(a - b) * self.REVEAL_DIM * 255
                                      for a, b in zip(profile, profile[1:])), default=0.)
        return True

    def paint(self, painter, rect, progress):
        """One whole-frame overlay after the caller's one sharp-background draw."""
        if not self.ready or rect.isEmpty():
            return
        amount = max(0., min(1., float(progress)))
        density = self.IDLE_DIM / self.REVEAL_DIM
        density += (1. - density) * amount
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setOpacity(painter.opacity() * density)
        painter.drawImage(rect, self._states[0])
        painter.restore()
