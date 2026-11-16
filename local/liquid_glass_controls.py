"""Reusable liquid-glass plates and lenses.

One slider amount, 0 to 1, drives both the blur and the tint.
The full travel is the old 0–75% span. The previous 75–100% end was too heavy.

- 0 is about 4 px of blur and a 15% white or black tint
- 1 is about 23.5 px of blur and a 60% tint

Light plates use a white tint and black ink. Dark plates use a black tint
and white ink. The backdrop shows through the tint, so a green photo stays
faintly green inside a white plate. Nothing here starts a timer or a thread.
Blur rasters are cached until the backdrop or the blur radius changes.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen

BLUR_CLEAR_PX = 4.0
BLUR_SOLID_PX = 30.0
TINT_CLEAR = 0.15
TINT_SOLID = 0.75
_CACHE_LIMIT = 6


@dataclass(frozen=True)
class ControlGlassLevels:
    blur_px: float
    tint: float


def control_glass_levels(amount: float) -> ControlGlassLevels:
    """Map a 0..1 slider onto blur radius and tint strength.

    The right end is the old 75% mark. Farther than that read as too heavy.
    """
    try:
        value = float(amount)
    except (TypeError, ValueError):
        value = 0.0
    if value != value:
        value = 0.0
    value = min(1.0, max(0.0, value)) * 0.75
    return ControlGlassLevels(
        BLUR_CLEAR_PX + (BLUR_SOLID_PX - BLUR_CLEAR_PX) * value,
        TINT_CLEAR + (TINT_SOLID - TINT_CLEAR) * value,
    )


def control_glass_ink(dark: bool) -> QColor:
    return QColor(255, 255, 255) if dark else QColor(0, 0, 0)


def _to_rgba(image: QImage) -> np.ndarray:
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = converted.width(), converted.height()
    raw = np.frombuffer(bytes(converted.constBits()), dtype=np.uint8)
    row = converted.bytesPerLine()
    return np.ascontiguousarray(raw.reshape(height, row)[:, : width * 4].reshape(height, width, 4))


def _to_qimage(pixels: np.ndarray) -> QImage:
    array = np.ascontiguousarray(pixels, dtype=np.uint8)
    height, width, _channels = array.shape
    image = QImage(array.data, width, height, width * 4, QImage.Format.Format_RGBA8888)
    return image.copy()


def _blur_rgba(pixels: np.ndarray, radius: float) -> np.ndarray:
    if radius < 0.6 or pixels.size == 0:
        return pixels
    picture = Image.fromarray(pixels, "RGBA").filter(ImageFilter.GaussianBlur(max(0.6, float(radius))))
    return np.asarray(picture)


def _blur_rgba_ignoring_gaps(pixels: np.ndarray, radius: float) -> np.ndarray:
    """Blur that averages only painted pixels (a normalised convolution).

    A glass backdrop can be painted only around the controls and left
    transparent elsewhere. A plain blur mixed that transparent black into
    the plate, so its edge showed a dark inner rim. Here colour is weighted
    by coverage and divided by the blurred coverage, so gaps add nothing.
    """
    if pixels.size == 0:
        return pixels
    alpha = pixels[..., 3]
    if int(alpha.min()) >= 255:
        return _blur_rgba(pixels, radius)
    weight = alpha.astype(np.float32) / 255.0
    premultiplied = pixels.copy()
    premultiplied[..., :3] = np.rint(pixels[..., :3].astype(np.float32) * weight[..., None]).astype(np.uint8)
    blurred = _blur_rgba(premultiplied, radius).astype(np.float32)
    cover = np.maximum(blurred[..., 3:4], 1.0) / 255.0
    out = np.empty_like(pixels)
    out[..., :3] = np.clip(np.rint(blurred[..., :3] / cover), 0, 255).astype(np.uint8)
    out[..., 3] = 255
    return out


def _rounded_rect(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def _capsule_radius(rect: QRectF, radius: float | None) -> float:
    limit = min(rect.width(), rect.height()) / 2.0
    if radius is None:
        return limit
    return min(max(0.0, float(radius)), limit)


class ControlGlassMaterial:
    """Blur cache for one backdrop. Replacing the image drops the old rasters."""

    def __init__(self) -> None:
        self._backdrop = QImage()
        self._key: tuple | None = None
        self._rgba: np.ndarray | None = None
        self._blurs: OrderedDict[int, QImage] = OrderedDict()
        self.blur_builds = 0

    def set_backdrop(self, image: QImage) -> None:
        key = (int(image.cacheKey()), image.width(), image.height(), image.format())
        if key == self._key:
            return
        self._backdrop = QImage(image)
        self._key = key
        self._rgba = None
        self._blurs.clear()

    @property
    def backdrop(self) -> QImage:
        return self._backdrop

    def pixels(self) -> np.ndarray | None:
        if self._backdrop.isNull():
            return None
        if self._rgba is None:
            self._rgba = _to_rgba(self._backdrop)
        return self._rgba

    def blurred(self, radius: float) -> QImage:
        if self._backdrop.isNull():
            return QImage()
        bucket = int(round(max(0.0, float(radius))))
        cached = self._blurs.get(bucket)
        if cached is not None:
            self._blurs.move_to_end(bucket)
            return cached
        if bucket == 0:
            image = QImage(self._backdrop)
        else:
            pixels = self.pixels()
            image = _to_qimage(_blur_rgba(pixels, bucket)) if pixels is not None else QImage()
            self.blur_builds += 1
        self._blurs[bucket] = image
        while len(self._blurs) > _CACHE_LIMIT:
            self._blurs.popitem(last=False)
        return image


_shadows: OrderedDict[tuple[int, int, int], QImage] = OrderedDict()


def _shadow_image(width: int, height: int, radius: int) -> QImage:
    key = (width, height, radius)
    cached = _shadows.get(key)
    if cached is not None:
        _shadows.move_to_end(key)
        return cached
    pad = 16
    canvas_h, canvas_w = height + pad * 2, width + pad * 2
    yy, xx = np.mgrid[0:canvas_h, 0:canvas_w]
    rad = max(1.0, min(float(radius), width / 2.0, height / 2.0))
    local_x = xx - pad
    local_y = yy - pad
    dx = np.abs(local_x + 0.5 - width / 2.0) - (width / 2.0 - rad)
    dy = np.abs(local_y + 0.5 - height / 2.0) - (height / 2.0 - rad)
    distance = np.hypot(np.maximum(dx, 0.0), np.maximum(dy, 0.0)) + np.minimum(np.maximum(dx, dy), 0.0) - rad
    mask = np.where(distance <= 0.0, np.uint8(255), np.uint8(0))
    blurred = np.asarray(Image.fromarray(mask, "L").filter(ImageFilter.GaussianBlur(8)))
    canvas = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
    canvas[..., 3] = (blurred.astype(np.float32) * 0.45).astype(np.uint8)
    image = _to_qimage(canvas)
    _shadows[key] = image
    while len(_shadows) > 24:
        _shadows.popitem(last=False)
    return image


def _shadow(painter: QPainter, rect: QRectF, radius: float) -> None:
    """Soft shadow on the photo only. It does not darken the glass interior."""
    image = _shadow_image(max(1, int(round(rect.width()))), max(1, int(round(rect.height()))), int(round(radius)))
    outside = QPainterPath()
    outside.addRect(rect.adjusted(-28.0, -12.0, 28.0, 36.0))
    outside = outside.subtracted(_rounded_rect(rect, radius))
    painter.save()
    painter.setClipPath(outside)
    painter.drawImage(QPointF(rect.left() - 16.0, rect.top() - 16.0 + 5.0), image)
    painter.restore()


def _rim(rect: QRectF) -> QLinearGradient:
    """Bottom edge carries the bright line. The top edge stays faint."""
    rim = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    rim.setColorAt(0.0, QColor(255, 255, 255, 72))
    rim.setColorAt(0.45, QColor(255, 255, 255, 110))
    rim.setColorAt(1.0, QColor(255, 255, 255, 236))
    return rim


def paint_control_glass(
    painter: QPainter,
    rect: QRectF,
    material: ControlGlassMaterial,
    *,
    dark: bool,
    amount: float,
    radius: float | None = None,
) -> None:
    """Capsule or circle. The backdrop is sampled in the same coordinates as ``rect``."""
    if rect.isEmpty():
        return
    levels = control_glass_levels(amount)
    curve = _capsule_radius(rect, radius)
    _shadow(painter, rect, curve)
    shape = _rounded_rect(rect, curve)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setClipPath(shape)
    blurred = material.blurred(levels.blur_px)
    if not blurred.isNull():
        painter.drawImage(rect, blurred, rect)
    # One even veil. No extra dark bands inside the top or bottom.
    tint = QColor(0, 0, 0) if dark else QColor(255, 255, 255)
    tint.setAlphaF(min(1.0, levels.tint))
    painter.fillRect(rect, tint)
    painter.restore()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(_rim(rect), 1.6))
    painter.drawPath(shape)
    painter.restore()


def _sample_rect(pixels: np.ndarray, rect: QRectF, scale: float, bend: float) -> QImage:
    height, width, _channels = pixels.shape
    out_w = max(2, int(round(rect.width())))
    out_h = max(2, int(round(rect.height())))
    center_x = rect.center().x()
    center_y = rect.center().y()
    ys = np.linspace(0.0, out_h - 1.0, out_h, dtype=np.float32)
    xs = np.linspace(0.0, out_w - 1.0, out_w, dtype=np.float32)
    source_y = center_y + (ys - (out_h - 1.0) / 2.0) / scale
    source_x = center_x + (xs - (out_w - 1.0) / 2.0) / scale
    bend_shift = np.sin(ys / max(out_h - 1.0, 1.0) * np.pi) * bend
    sample_x = source_x[None, :] + bend_shift[:, None]
    sample_y = np.broadcast_to(source_y[:, None], sample_x.shape)
    x = np.clip(sample_x, 0.0, width - 1.0)
    y = np.clip(sample_y, 0.0, height - 1.0)
    x0 = np.floor(x).astype(np.int32)
    y0 = np.floor(y).astype(np.int32)
    x1 = np.minimum(x0 + 1, width - 1)
    y1 = np.minimum(y0 + 1, height - 1)
    fx = (x - x0)[..., None]
    fy = (y - y0)[..., None]
    top = pixels[y0, x0] * (1.0 - fx) + pixels[y0, x1] * fx
    bottom = pixels[y1, x0] * (1.0 - fx) + pixels[y1, x1] * fx
    mixed = top * (1.0 - fy) + bottom * fy
    return _to_qimage(np.rint(mixed).astype(np.uint8))


def paint_control_lens(
    painter: QPainter,
    rect: QRectF,
    material: ControlGlassMaterial,
    *,
    dark: bool,
    scale: float = 2.4,
) -> None:
    """Almost no blur. The backdrop under the thumb is magnified and slightly bent."""
    if rect.isEmpty():
        return
    curve = _capsule_radius(rect, None)
    _shadow(painter, rect, curve)
    shape = _rounded_rect(rect, curve)
    pixels = material.pixels()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setClipPath(shape)
    if pixels is not None:
        lens = _sample_rect(pixels, rect, scale, bend=2.2)
        painter.drawImage(rect, lens)
    veil = QColor(0, 0, 0, 28) if dark else QColor(255, 255, 255, 36)
    painter.fillRect(rect, veil)
    painter.restore()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(_rim(rect), 1.5))
    painter.drawPath(shape)
    painter.restore()


def merged_lens_path(thumb: QRectF, icon: QRectF, merge: float) -> QPainterPath:
    """One capsule while the thumb and an end icon overlap, with a narrowing neck."""
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    path.addRoundedRect(thumb, thumb.height() / 2.0, thumb.height() / 2.0)
    path.addRoundedRect(icon, icon.height() / 2.0, icon.height() / 2.0)
    amount = min(1.0, max(0.0, float(merge)))
    if amount <= 0.0:
        return path
    neck = max(5.0, min(thumb.height(), icon.height()) * (0.28 + 0.72 * amount))
    left = min(thumb.center().x(), icon.center().x())
    bridge = QRectF(left, thumb.center().y() - neck / 2.0, abs(icon.center().x() - thumb.center().x()), neck)
    path.addRoundedRect(bridge, neck / 2.0, neck / 2.0)
    return path


def paint_merged_lens(
    painter: QPainter,
    thumb: QRectF,
    icon: QRectF,
    material: ControlGlassMaterial,
    *,
    dark: bool,
    merge: float,
) -> None:
    span = thumb.united(icon)
    path = merged_lens_path(thumb, icon, merge)
    pixels = material.pixels()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 36))
    painter.drawPath(path.translated(0.0, 4.0))
    painter.setClipPath(path)
    if pixels is not None:
        lens = _sample_rect(pixels, span, 2.3, bend=2.0)
        painter.drawImage(span, lens)
    painter.fillRect(span, QColor(0, 0, 0, 24) if dark else QColor(255, 255, 255, 32))
    painter.restore()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(_rim(span), 1.5))
    painter.drawPath(path)
    painter.restore()


SCENE_TINT_CLEAR = 0.06   # Apple glass stays clear; the veil only settles contrast
SCENE_TINT_SOLID = 0.42
WORK_SCALE = 0.5          # blur and lens run at half resolution, then scale up


def _amount(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if number != number else min(1.0, max(0.0, number))


LENS_BAND = 0.42      # share of the short side that bends near the rim
LENS_PULL = 0.55      # how far (in band widths) the rim reaches outside
VIBRANCY = 1.35       # saturation lift of the backdrop seen through the glass


def _vibrance(pixels: np.ndarray, amount: float) -> np.ndarray:
    if abs(amount - 1.0) < 1e-3:
        return pixels
    rgb = pixels[..., :3].astype(np.float32)
    gray = rgb @ np.asarray((0.2126, 0.7152, 0.0722), dtype=np.float32)
    lifted = gray[..., None] + (rgb - gray[..., None]) * float(amount)
    out = pixels.copy()
    out[..., :3] = np.clip(lifted, 0.0, 255.0).astype(np.uint8)
    return out


_lens_maps: OrderedDict[tuple, tuple] = OrderedDict()


def _lens_map(width: int, height: int, curve: float):
    """Sample offsets for one plate size. Cached: the geometry rarely changes."""
    key = (width, height, int(round(curve)))
    cached = _lens_maps.get(key)
    if cached is not None:
        _lens_maps.move_to_end(key)
        return cached
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    px = xs + 0.5 - width / 2.0
    py = ys + 0.5 - height / 2.0
    half_w, half_h = width / 2.0, height / 2.0
    rad = max(1.0, min(curve, half_w, half_h))
    qx = np.abs(px) - (half_w - rad)
    qy = np.abs(py) - (half_h - rad)
    outside = np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0))
    inside = np.minimum(np.maximum(qx, qy), 0.0)
    depth = -(outside + inside - rad)
    band = max(2.0, min(width, height) * LENS_BAND)
    weight = np.clip(1.0 - depth / band, 0.0, 1.0) ** 2
    nx = np.where(qx > 0.0, np.maximum(qx, 0.0), 0.0) * np.sign(px)
    ny = np.where(qy > 0.0, np.maximum(qy, 0.0), 0.0) * np.sign(py)
    flat = (qx <= 0.0) & (qy <= 0.0)
    pick_x = flat & (qx > qy)
    nx = np.where(pick_x, np.sign(px), nx)
    ny = np.where(flat & ~pick_x, np.sign(py), ny)
    norm = np.maximum(np.hypot(nx, ny), 1e-4)
    shift = weight * band * LENS_PULL
    dx = xs + nx / norm * shift
    dy = ys + ny / norm * shift
    result = (dx, dy)
    _lens_maps[key] = result
    while len(_lens_maps) > 48:
        _lens_maps.popitem(last=False)
    return result


def _lens_sample(pixels: np.ndarray, origin_x: float, origin_y: float, width: int, height: int,
                 curve: float) -> np.ndarray:
    """Sample a rounded rectangle from ``pixels`` with an edge lens.

    The interior maps straight through. Inside a band along the rim, samples
    are pushed outward along the rim normal, so the edge shows compressed
    backdrop from just beyond the plate, the way a thick glass edge bends it.
    Bilinear only inside the band; the interior is a plain copy.
    """
    src_h, src_w, _ = pixels.shape
    dx, dy = _lens_map(width, height, curve)
    ox, oy = int(round(origin_x)), int(round(origin_y))
    x = np.clip(dx + ox, 0.0, src_w - 1.0)
    y = np.clip(dy + oy, 0.0, src_h - 1.0)
    xi = np.rint(x).astype(np.int32)
    yi = np.rint(y).astype(np.int32)
    return pixels[yi, xi]


class SceneGlassPatches:
    """Blurred crops of a live scene, one per control rectangle.

    The scene can be any resolution; ``bounds`` is the logical rectangle it
    covers. Only the patch under a control (plus the blur margin) is blurred,
    so an animated background costs one small blur per plate per new frame.
    """

    def __init__(self, limit: int = 12) -> None:
        self._items: OrderedDict[tuple, tuple[QImage, QRectF]] = OrderedDict()
        self._limit = max(1, int(limit))
        self.blur_builds = 0

    def clear(self) -> None:
        self._items.clear()

    def patch(self, scene: QImage, bounds: QRectF, rect: QRectF, radius: float, curve: float = 0.0):
        if scene is None or scene.isNull() or rect.width() < 2.0 or rect.height() < 2.0 or bounds.isEmpty():
            return None
        key = (int(scene.cacheKey()), scene.width(), scene.height(),
               round(bounds.width(), 1), round(bounds.height(), 1),
               round(rect.x(), 1), round(rect.y(), 1), round(rect.width(), 1), round(rect.height(), 1),
               int(round(radius)), int(round(curve)))
        cached = self._items.get(key)
        if cached is not None:
            self._items.move_to_end(key)
            return cached
        sx = scene.width() / max(1.0, bounds.width()) * WORK_SCALE
        sy = scene.height() / max(1.0, bounds.height()) * WORK_SCALE
        pad = max(2.0, float(radius) * 1.5)
        work_w = max(2, int(round(scene.width() * WORK_SCALE)))
        work_h = max(2, int(round(scene.height() * WORK_SCALE)))
        full = QRectF(
            (rect.x() - bounds.x() - pad) * sx, (rect.y() - bounds.y() - pad) * sy,
            (rect.width() + 2.0 * pad) * sx, (rect.height() + 2.0 * pad) * sy,
        ).toAlignedRect().intersected(QRectF(0, 0, work_w, work_h).toAlignedRect())
        if full.isEmpty():
            return None
        inv = 1.0 / WORK_SCALE
        crop = scene.copy(QRectF(full.x() * inv, full.y() * inv, full.width() * inv, full.height() * inv).toAlignedRect())
        crop = crop.scaled(full.width(), full.height(), Qt.AspectRatioMode.IgnoreAspectRatio,
                           Qt.TransformationMode.FastTransformation)
        pixels = _to_rgba(crop)
        blurred = _vibrance(_blur_rgba_ignoring_gaps(pixels, max(0.6, float(radius) * (sx + sy) / 2.0)), VIBRANCY)
        self.blur_builds += 1
        out_w = max(2, (int(round(rect.width() * sx)) // 2) * 2)
        out_h = max(2, (int(round(rect.height() * sy)) // 2) * 2)
        origin_x = (rect.x() - bounds.x()) * sx - full.x()
        origin_y = (rect.y() - bounds.y()) * sy - full.y()
        lensed = _lens_sample(blurred, origin_x, origin_y, out_w, out_h, float(curve) * (sx + sy) / 2.0)
        result = (_to_qimage(lensed), QRectF(rect))
        self._items[key] = result
        while len(self._items) > self._limit:
            self._items.popitem(last=False)
        return result


def _soft_shadow(painter: QPainter, rect: QRectF, radius: float) -> None:
    """A barely-there contact shadow. Vector only: cheap while a plate moves."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    outside = QPainterPath()
    outside.addRect(rect.adjusted(-12.0, -8.0, 12.0, 16.0))
    outside = outside.subtracted(_rounded_rect(rect, radius))
    painter.setClipPath(outside)
    for grow, alpha in ((5.0, 7), (3.0, 10), (1.5, 14)):
        painter.setBrush(QColor(0, 0, 0, alpha))
        shape = rect.adjusted(-grow, -grow * 0.4, grow, grow * 1.2).translated(0.0, 1.5)
        painter.drawPath(_rounded_rect(shape, radius + grow))
    painter.restore()


def _scene_rim(rect: QRectF, dark: bool) -> QLinearGradient:
    """Thin specular edge: clearer along the bottom, faint on top."""
    rim = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    top, middle, bottom = (38, 24, 120) if dark else (90, 40, 170)
    rim.setColorAt(0.0, QColor(255, 255, 255, top))
    rim.setColorAt(0.5, QColor(255, 255, 255, middle))
    rim.setColorAt(1.0, QColor(255, 255, 255, bottom))
    return rim


def paint_scene_glass(
    painter: QPainter,
    rect: QRectF,
    patches: SceneGlassPatches,
    scene: QImage,
    bounds: QRectF,
    *,
    dark: bool,
    amount: float,
    radius: float | None = None,
    shadow: bool = False,
) -> None:
    """The same plate as ``paint_control_glass``, sampled from a live scene.

    Light: white veil, black ink. Dark: black veil, white ink. The slider
    amount sets blur and veil together; the bright rim sits on the bottom edge.
    """
    if rect.isEmpty():
        return
    levels = control_glass_levels(amount)
    curve = _capsule_radius(rect, radius)
    if shadow:
        _soft_shadow(painter, rect, curve)
    shape = _rounded_rect(rect, curve)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.setClipPath(shape)
    found = patches.patch(scene, bounds, rect, levels.blur_px, curve)
    if found is not None:
        image, logical = found
        painter.drawImage(logical, image)
    else:
        painter.fillRect(rect, QColor(0, 0, 0) if dark else QColor(255, 255, 255))
    tint = QColor(0, 0, 0) if dark else QColor(255, 255, 255)
    tint.setAlphaF(min(1.0, SCENE_TINT_CLEAR + (SCENE_TINT_SOLID - SCENE_TINT_CLEAR) * _amount(amount)))
    painter.fillRect(rect, tint)
    painter.restore()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    edge = rect.adjusted(0.5, 0.5, -0.5, -0.5)
    painter.setPen(QPen(_scene_rim(edge, dark), 1.0))
    painter.drawPath(_rounded_rect(edge, max(0.0, curve - 0.5)))
    painter.restore()
