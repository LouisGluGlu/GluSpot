"""Native, bounded optical button material over the app's existing background.

Original Qt implementation: a rounded lens samples only the supplied cached
background, with a soft interior, refracted rim and directional specular edge.
It never grabs/renders widgets, starts timers, or samples the user's desktop.
"""
from __future__ import annotations

from collections import OrderedDict
import math
import time

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QPushButton, QStyle, QStyleOptionButton

GLASS_CACHE_ENTRIES = 96
GLASS_CACHE_BYTES = 4 * 1024 * 1024
GLASS_FRAME_SECONDS = .10
GLASS_MAX_SIDE = 160
GLASS_GEOMETRY_ENTRIES = 24
GLASS_GEOMETRY_BYTES = 2 * 1024 * 1024
_materials: OrderedDict[tuple, tuple[float, int, QImage]] = OrderedDict()
_material_bytes = 0
_geometry: OrderedDict[tuple, tuple] = OrderedDict()
_geometry_bytes = 0


def clear_glass_cache() -> None:
    global _material_bytes, _geometry_bytes
    _materials.clear()
    _geometry.clear()
    _material_bytes = 0
    _geometry_bytes = 0


def glass_cache_info() -> tuple[int, int]:
    return len(_materials), _material_bytes


def glass_geometry_cache_info() -> tuple[int, int]:
    return len(_geometry), _geometry_bytes


def _lens_geometry(width: int, height: int, radius_fraction: float) -> tuple:
    """Keep shape maps independent of album frames and bounded by bytes."""
    global _geometry_bytes
    key = width, height, round(radius_fraction, 4)
    cached = _geometry.pop(key, None)
    if cached is not None:
        _geometry[key] = cached
        return cached
    yy, xx = np.mgrid[:height, :width].astype(np.float32)
    x, y = xx + .5 - width / 2, yy + .5 - height / 2
    radius = min(width, height) * min(.5, max(.0, radius_fraction))
    qx, qy = np.abs(x) - (width / 2 - radius), np.abs(y) - (height / 2 - radius)
    ox, oy = np.maximum(qx, 0), np.maximum(qy, 0)
    length = np.maximum(np.hypot(ox, oy), .0001)
    distance = np.minimum(np.maximum(qx, qy), 0) + length - radius
    nx, ny = ox / length * np.sign(x), oy / length * np.sign(y)
    flat = (ox == 0) & (oy == 0)
    nx = np.where(flat & (qx >= qy), np.sign(x), nx)
    ny = np.where(flat & (qx < qy), np.sign(y), ny)
    depth = np.clip(-distance / max(2, min(width, height) * .18), 0, 1)
    bend = np.sin(depth * math.pi) * min(3.5, min(width, height) * .065)
    sample_x = np.clip(xx + 5 + nx * bend, 0, width + 8.999)
    sample_y = np.clip(yy + 5 + ny * bend, 0, height + 8.999)
    ix, iy = sample_x.astype(np.int32), sample_y.astype(np.int32)
    result = (ix, iy, (sample_x - ix).astype(np.float32)[..., None],
              (sample_y - iy).astype(np.float32)[..., None])
    _geometry[key] = result
    _geometry_bytes += sum(array.nbytes for array in result)
    while len(_geometry) > GLASS_GEOMETRY_ENTRIES or _geometry_bytes > GLASS_GEOMETRY_BYTES:
        _, oldest = _geometry.popitem(last=False)
        _geometry_bytes -= sum(array.nbytes for array in oldest)
    return result


def widget_glass_backdrop(widget, rect: QRectF | None = None) -> tuple[QImage | None, QRectF | None]:
    """Map a child into an already-painted ancestor raster, without capture."""
    ancestor = widget.parentWidget()
    while ancestor is not None:
        for name in ("_material", "_background_frame", "_background_cache"):
            image = getattr(ancestor, name, None)
            if not isinstance(image, QImage) or image.isNull():
                continue
            origin = widget.mapTo(ancestor, QPoint(0, 0))
            sx, sy = image.width() / max(1, ancestor.width()), image.height() / max(1, ancestor.height())
            bounds = QRectF(rect) if rect is not None else QRectF(widget.rect())
            return image, QRectF((origin.x() + bounds.x()) * sx, (origin.y() + bounds.y()) * sy,
                                 bounds.width() * sx, bounds.height() * sy)
        ancestor = ancestor.parentWidget()
    return None, None


def _lens_material(backdrop: QImage, source: QRectF, size: QSize,
                   radius_fraction: float, cache_token) -> QImage:
    """Cache a tiny lens; live backdrop updates are capped at ten per second.

    Rounded-box normals displace only the rim. The interior is softened by
    native smooth down/up sampling; bilinear refraction avoids strip seams.
    Bounds/DPR are part of the slot, while changing image frames replace it.
    """
    global _material_bytes
    width, height = max(1, size.width()), max(1, size.height())
    slot = (cache_token, width, height, round(radius_fraction, 4),
            tuple(round(v, 3) for v in (source.x(), source.y(), source.width(), source.height())))
    now, serial = time.monotonic(), int(backdrop.cacheKey())
    cached = _materials.pop(slot, None)
    if cached is not None:
        at, old_serial, image = cached
        if old_serial == serial or 0 <= now - at < GLASS_FRAME_SECONDS:
            _materials[slot] = cached
            return image
        _material_bytes -= int(image.sizeInBytes())
    # Add a small optical overscan so a bent rim never clamps at the button edge.
    padding = 5
    patch = QImage(width + 2 * padding, height + 2 * padding, QImage.Format.Format_RGBA8888)
    patch.fill(Qt.GlobalColor.transparent)
    sx, sy = source.width() / width, source.height() / height
    region = source.adjusted(-padding * sx, -padding * sy, padding * sx, padding * sy)
    with QPainter(patch) as painter:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(QRectF(patch.rect()), backdrop, region)
    small = patch.scaled(max(1, patch.width() // 3), max(1, patch.height() // 3),
                         Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    softened = small.scaled(patch.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation).convertToFormat(QImage.Format.Format_RGBA8888)
    pixels = np.frombuffer(softened.bits(), np.uint8).reshape(softened.height(), softened.bytesPerLine())
    pixels = pixels[:, :softened.width() * 4].reshape(softened.height(), softened.width(), 4)
    ix, iy, fx, fy = _lens_geometry(width, height, radius_fraction)
    data = ((pixels[iy, ix] * (1 - fx) + pixels[iy, ix + 1] * fx) * (1 - fy)
            + (pixels[iy + 1, ix] * (1 - fx) + pixels[iy + 1, ix + 1] * fx) * fy)
    data = np.ascontiguousarray(np.clip(data, 0, 255).astype(np.uint8))
    image = QImage(data.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
    _materials[slot] = (now, serial, image)
    _material_bytes += int(image.sizeInBytes())
    while len(_materials) > GLASS_CACHE_ENTRIES or _material_bytes > GLASS_CACHE_BYTES:
        _, (_, _, oldest) = _materials.popitem(last=False)
        _material_bytes -= int(oldest.sizeInBytes())
    return image


def paint_liquid_glass(painter: QPainter, rect: QRectF, *, radius: float | None = None,
                       tint: QColor | None = None, light: bool = False,
                       hovered: bool = False, pressed: bool = False, active: bool = False,
                       backdrop: QImage | None = None, backdrop_rect: QRectF | None = None,
                       opacity: float = 1.0, cache_token=None) -> None:
    """Draw optical material only, leaving glyphs and hit paths to their owner.

    ``backdrop_rect`` is the source pixel region in the cached image. Without
    a supplied raster the transparent tint and rim still reveal the existing
    painted content; no window/desktop capture is attempted.
    """
    if rect.isEmpty() or opacity <= 0:
        return
    radius = min(rect.width(), rect.height()) / 2 if radius is None else max(0, radius)
    radius = min(radius, min(rect.width(), rect.height()) / 2)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.save()
    painter.setOpacity(painter.opacity() * min(1., opacity))
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
    if isinstance(backdrop, QImage) and not backdrop.isNull():
        source = QRectF(backdrop_rect or QRectF(backdrop.rect()))
        factor = min(2., float(painter.device().devicePixelRatioF()), GLASS_MAX_SIDE / max(rect.width(), rect.height()))
        size = QSize(max(1, round(rect.width() * factor)), max(1, round(rect.height() * factor)))
        material = _lens_material(backdrop, source, size, radius / min(rect.width(), rect.height()),
                                  cache_token if cache_token is not None else (round(rect.x(), 2), round(rect.y(), 2)))
        painter.drawImage(rect, material)
    pale = bool(light or active)
    base = QColor(245, 248, 252, 204 if pale else 0) if pale else QColor(24, 27, 34, 132)
    if hovered:
        base = QColor(248, 250, 255, 219) if pale else QColor(39, 44, 54, 145)
    if pressed:
        base = QColor(222, 229, 240, 222) if pale else QColor(14, 18, 26, 160)
    painter.fillPath(path, base)
    if tint is not None:
        tone = QColor(tint)
        if tone.isValid():
            tone.setAlpha(84 if active else 48)
            painter.fillPath(path, tone)
    bloom = QLinearGradient(rect.topLeft(), rect.bottomRight())
    bloom.setColorAt(0., QColor(255, 255, 255, 48 if pale else 34))
    bloom.setColorAt(.38, QColor(255, 255, 255, 8))
    bloom.setColorAt(.72, QColor(255, 255, 255, 0))
    bloom.setColorAt(1., QColor(255, 255, 255, 18 if pale else 10))
    painter.fillPath(path, bloom)
    # Two opposing highlights, with a dark separator, give a continuous rim.
    rim = QLinearGradient(rect.topLeft(), rect.bottomRight())
    for stop, color in ((0., QColor(255, 255, 255, 190 if hovered else 136)),
                        (.28, QColor(255, 255, 255, 58)),
                        (.54, QColor(0, 0, 0, 62 if pale else 38)),
                        (.82, QColor(255, 255, 255, 25)),
                        (1., QColor(255, 255, 255, 105))):
        rim.setColorAt(stop, color)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(rim, 1.2 if hovered else 1.0))
    painter.drawRoundedRect(rect.adjusted(.5, .5, -.5, -.5), max(0., radius - .5), max(0., radius - .5))
    painter.restore()


class GlassPushButton(QPushButton):
    """Standard Qt button semantics with one shared native optical background."""
    def setGlassTint(self, color) -> None:
        self.setProperty("glassTint", color)
        self.update()

    def setGlassLight(self, value: bool) -> None:
        self.setProperty("glassLight", bool(value))
        self.update()

    def paintEvent(self, event) -> None:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        light = self.property("glassLight")
        if light is None:
            light = option.palette.window().color().lightness() > 155
        tint = self.property("glassTint")
        if tint is None:
            if self.objectName() == "Primary":
                tint = QColor("#428ce8")
            elif self.objectName() == "Danger":
                tint = QColor("#cc4650")
        radius = self.property("glassRadius")
        active = self.property("glassActive")
        face = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        backdrop, source = widget_glass_backdrop(self, face)
        with QPainter(self) as painter:
            paint_liquid_glass(painter, face,
                               radius=float(radius) if radius is not None else min(14., self.height() / 2),
                               light=bool(light), tint=QColor(tint) if tint is not None else None,
                               hovered=self.underMouse(), pressed=self.isDown(),
                               active=self.isChecked() if active is None else bool(active),
                               backdrop=backdrop, backdrop_rect=source, opacity=1. if self.isEnabled() else .48,
                               cache_token=(id(self), "button"))
            label = QStyleOptionButton(option)
            label.rect = self.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, self)
            self.style().drawControl(QStyle.ControlElement.CE_PushButtonLabel, label, painter, self)
            if self.hasFocus():
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(91, 161, 255, 225), 1.6))
                painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 11, 11)
