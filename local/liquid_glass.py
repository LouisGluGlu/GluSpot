"""Fine native control rims, plus an opt-in blurred scene material.

Owners supply their original state color and geometry. Only that face becomes
slightly translucent; directional light is confined to the edge. No extra
plate or backdrop lens is introduced by default. Music surfaces can opt into a
bounded scene crop with blur and gentle edge refraction. Neither mode captures
widgets or starts timers; glyphs remain independent foreground paint.
"""
from __future__ import annotations

from collections import OrderedDict
import math
import time

import numpy as np
from PIL import Image
from PIL.ImageFilter import GaussianBlur
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QPushButton, QStyle, QStyleOptionButton
from local.control_hover import install_control_hover
from local.settings_store import MUSIC_GLASS_DEFAULT_LEVEL, normalize_music_glass_level

GLASS_FACE_OPACITY = .80
GLASS_MATERIAL_MAX_EDGE = 160
GLASS_MATERIAL_CACHE_MAX_BYTES = 4 * 1024 * 1024
GLASS_MATERIAL_CACHE_MAX_ENTRIES = 64
GLASS_MATERIAL_FRAME_SECONDS = .1
_glass_material_cache: OrderedDict[tuple, QImage] = OrderedDict()
_glass_material_slots: OrderedDict[tuple, tuple[tuple, float]] = OrderedDict()
_glass_material_cache_bytes = 0


def clear_glass_cache() -> None:
    """Release only the bounded, opt-in scene-material rasters."""
    global _glass_material_cache_bytes
    _glass_material_cache.clear()
    _glass_material_slots.clear()
    _glass_material_cache_bytes = 0


def glass_material_tint(*, light: bool = False, hovered: bool = False,
                        pressed: bool = False, active: bool = False,
                        material_level: int = MUSIC_GLASS_DEFAULT_LEVEL) -> QColor:
    """A neutral theme veil, shared with callers choosing foreground contrast."""
    # Hover light is composed above the face below. Raising a dark veil's
    # alpha here would darken the very control the pointer should brighten.
    alpha = 42 if pressed else 88 if active else 58
    alpha = min(230, round(alpha * normalize_music_glass_level(material_level) / 50.))
    return QColor(255, 255, 255, alpha) if light else QColor(20, 22, 27, alpha)


def widget_glass_level(widget) -> int:
    """Read the song owner's existing setting, including its child popovers."""
    current = widget
    while current is not None:
        getter = getattr(getattr(current, "owner", None), "settings_getter", None)
        if not callable(getter):
            getter = getattr(current, "settings_getter", None)
        if callable(getter):
            return normalize_music_glass_level(getter("v2_music_glass_level", MUSIC_GLASS_DEFAULT_LEVEL))
        current = current.parentWidget()
    return MUSIC_GLASS_DEFAULT_LEVEL


def glass_backdrop_opacity(material_level: int = MUSIC_GLASS_DEFAULT_LEVEL) -> float:
    level = normalize_music_glass_level(material_level)
    return .78 * level / 50. if level <= 50 else .78 + .18 * (level - 50) / 50.


def glass_blur_scale(material_level: int = MUSIC_GLASS_DEFAULT_LEVEL) -> float:
    return normalize_music_glass_level(material_level) / 50.


def _sample_rgba(pixels: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Bilinear samples clamp at scene edges rather than adding dark margins."""
    x = np.clip(x, 0., pixels.shape[1] - 1.)
    y = np.clip(y, 0., pixels.shape[0] - 1.)
    x0, y0 = np.floor(x).astype(np.intp), np.floor(y).astype(np.intp)
    x1, y1 = np.minimum(x0 + 1, pixels.shape[1] - 1), np.minimum(y0 + 1, pixels.shape[0] - 1)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    return ((pixels[y0, x0] * (1. - fx) + pixels[y0, x1] * fx) * (1. - fy)
            + (pixels[y1, x0] * (1. - fx) + pixels[y1, x1] * fx) * fy)


def _render_glass_material(backdrop: QImage, source_rect: QRectF,
                           width: float, height: float, radius: float,
                           material_level: int = MUSIC_GLASS_DEFAULT_LEVEL) -> QImage:
    """Blur a padded scene crop, with a subpixel lens confined to its inner rim."""
    blur = min(9., max(3.5, min(width, height) * .16)) * glass_blur_scale(material_level)
    logical_padding = math.ceil(blur * 3.) + 3
    # Leave rounding headroom: the padded work raster also stays <=160 px.
    scale = min(1., (GLASS_MATERIAL_MAX_EDGE - 4.) / (max(width, height) + 2. * logical_padding))
    core_width, core_height = max(1, math.ceil(width * scale)), max(1, math.ceil(height * scale))
    padding = max(1, math.ceil(logical_padding * scale))
    work_width, work_height = core_width + 2 * padding, core_height + 2 * padding
    # Music surfaces supply their <=256 px displayed scene. Bound conversion
    # too when another caller supplies a full-resolution background raster.
    scene = backdrop
    if max(scene.width(), scene.height()) > 512:
        scene = scene.scaled(512, 512, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    source = QRectF(source_rect.x() * scene.width() / backdrop.width(),
                    source_rect.y() * scene.height() / backdrop.height(),
                    source_rect.width() * scene.width() / backdrop.width(),
                    source_rect.height() * scene.height() / backdrop.height())
    scene = scene.convertToFormat(QImage.Format.Format_RGBA8888)
    pixels = np.frombuffer(bytes(scene.constBits()), dtype=np.uint8).reshape(
        scene.height(), scene.bytesPerLine())[:, :scene.width() * 4].reshape(scene.height(), scene.width(), 4)
    xs = source.left() + (np.arange(work_width, dtype=np.float32) - padding + .5) * source.width() / core_width - .5
    ys = source.top() + (np.arange(work_height, dtype=np.float32) - padding + .5) * source.height() / core_height - .5
    sampled = _sample_rgba(pixels, xs[None, :], ys[:, None]).round().astype(np.uint8)
    blurred = np.asarray(Image.fromarray(sampled).filter(GaussianBlur(blur * scale)))
    yy, xx = np.mgrid[:core_height, :core_width].astype(np.float32)
    dx, dy = xx + .5 - core_width / 2., yy + .5 - core_height / 2.
    corner = min(radius * scale, core_width / 2., core_height / 2.)
    qx, qy = np.abs(dx) - (core_width / 2. - corner), np.abs(dy) - (core_height / 2. - corner)
    distance = np.hypot(np.maximum(qx, 0.), np.maximum(qy, 0.)) + np.minimum(np.maximum(qx, qy), 0.) - corner
    band = np.clip(1. + distance / max(1.5, 5. * scale), 0., 1.) ** 2
    length = np.maximum(1., np.hypot(dx, dy))
    displacement = max(.18, .9 * scale) * band
    refracted = _sample_rgba(blurred, xx + padding + dx / length * displacement,
                             yy + padding + dy / length * displacement).round().astype(np.uint8)
    raw = refracted.tobytes()
    return QImage(raw, core_width, core_height, core_width * 4, QImage.Format.Format_RGBA8888).copy()


def _glass_material(backdrop: QImage, source_rect: QRectF, rect: QRectF,
                    radius: float, state: tuple, cache_token,
                    material_level: int = MUSIC_GLASS_DEFAULT_LEVEL) -> QImage:
    global _glass_material_cache_bytes
    geometry = tuple(round(value, 4) for value in (
        source_rect.x(), source_rect.y(), source_rect.width(), source_rect.height(),
        rect.width(), rect.height(), radius))
    try:
        hash(cache_token)
        token = cache_token
    except TypeError:
        token = (type(cache_token).__name__, id(cache_token))
    slot = (token, backdrop.width(), backdrop.height(), geometry, state, material_level)
    key = (backdrop.cacheKey(), slot)
    now = time.monotonic()
    previous = _glass_material_slots.get(slot) if cache_token is not None else None
    if previous is not None and 0. <= now - previous[1] < GLASS_MATERIAL_FRAME_SECONDS:
        cached = _glass_material_cache.get(previous[0])
        if cached is not None:
            _glass_material_cache.move_to_end(previous[0])
            _glass_material_slots.move_to_end(slot)
            return cached
    cached = _glass_material_cache.get(key)
    if cached is None:
        cached = _render_glass_material(backdrop, source_rect, rect.width(), rect.height(), radius, material_level)
        _glass_material_cache[key] = cached
        _glass_material_cache_bytes += int(cached.sizeInBytes())
        while (len(_glass_material_cache) > GLASS_MATERIAL_CACHE_MAX_ENTRIES
               or _glass_material_cache_bytes > GLASS_MATERIAL_CACHE_MAX_BYTES):
            _old_key, old_image = _glass_material_cache.popitem(last=False)
            _glass_material_cache_bytes -= int(old_image.sizeInBytes())
    else:
        _glass_material_cache.move_to_end(key)
    if cache_token is not None:
        _glass_material_slots[slot] = (key, now)
        _glass_material_slots.move_to_end(slot)
        while len(_glass_material_slots) > GLASS_MATERIAL_CACHE_MAX_ENTRIES:
            _glass_material_slots.popitem(last=False)
    return cached


def widget_glass_backdrop(widget, rect: QRectF | None = None) -> tuple[QImage | None, QRectF | None]:
    """Map an existing ancestor raster without capture, for existing callers."""
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


def paint_liquid_glass(painter: QPainter, rect: QRectF, *, radius: float | None = None,
                       base_color: QColor | None = None, border_color: QColor | None = None,
                       face_opacity: float = GLASS_FACE_OPACITY,
                       tint: QColor | None = None, light: bool = False,
                       hovered: bool = False, pressed: bool = False, active: bool = False,
                       backdrop: QImage | None = None, backdrop_rect: QRectF | None = None,
                       opacity: float = 1.0, cache_token=None, refractive: bool = False,
                       material_level: int = MUSIC_GLASS_DEFAULT_LEVEL,
                       path: QPainterPath | None = None) -> None:
    """Keep the supplied face and add an inset rim; ``None`` paints only a rim.

    Original alpha is multiplied by ``face_opacity``. Selection and hover never
    replace the owner's colors. Legacy backdrop/tint arguments are accepted
    without sampling or recoloring the center, so a changing scene cannot make
    a second surface or alter the original state's pigment. ``refractive`` is
    opt-in: sample ``backdrop_rect`` in native backdrop pixels, blur that crop,
    and add a mild theme veil before the same sharp edge. No widget capture or
    timer is involved, and glyphs remain the caller's independent next layer.
    """
    if rect.isEmpty() or opacity <= 0:
        return
    radius = min(rect.width(), rect.height()) / 2 if radius is None else max(0, radius)
    radius = min(radius, min(rect.width(), rect.height()) / 2)
    material_level = normalize_music_glass_level(material_level)
    shape = QPainterPath(path) if path is not None else QPainterPath()
    if path is None:
        shape.addRoundedRect(rect, radius, radius)
    painter.save()
    painter.setOpacity(painter.opacity() * min(1., opacity))
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    if refractive:
        painter.save()
        painter.setClipPath(shape, Qt.ClipOperation.IntersectClip)
        if material_level > 0 and isinstance(backdrop, QImage) and not backdrop.isNull():
            sample = QRectF(backdrop_rect) if backdrop_rect is not None else QRectF(backdrop.rect())
            if sample.isValid() and not sample.isEmpty():
                material = _glass_material(backdrop, sample, rect, radius,
                                            (bool(light), bool(hovered), bool(pressed), bool(active)), cache_token, material_level)
                painter.save()
                painter.setOpacity(painter.opacity() * glass_backdrop_opacity(material_level))
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawImage(rect, material)
                painter.restore()
        painter.fillRect(rect, glass_material_tint(light=light, hovered=hovered, pressed=pressed,
                                                 active=active, material_level=material_level))
        painter.restore()
    if base_color is not None:
        face = QColor(base_color)
        face.setAlphaF(face.alphaF() * min(1., max(0., face_opacity)))
        painter.setBrush(face)
        painter.drawPath(shape)
    if refractive and hovered and not pressed:
        # A neutral light over the existing material stays visible even on a
        # transparent setting; it does not replace the scene or foreground.
        painter.fillPath(shape, QColor(255, 255, 255, 30))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    edge = rect.adjusted(.5, .5, -.5, -.5)
    if border_color is not None:
        painter.setPen(QPen(QColor(border_color), 1.0))
        painter.drawPath(shape) if path is not None else painter.drawRoundedRect(edge, max(0., radius - .5), max(0., radius - .5))
    # Opposing fine highlights follow the original boundary. The interior has
    # no gradient wash; icons, captions and selected state remain independent.
    strength = .68 if pressed else 1.18 if hovered else 1.
    if refractive:
        # Glass already has a scene edge. Keep one quiet reflection inside
        # its contour, especially on thin transport glyphs; an outward,
        # double white stroke reads as an outline rather than glass.
        strength *= (.32 if path is not None else .50) * (.35 + .65 * material_level / 100.)
        painter.setClipPath(shape, Qt.ClipOperation.IntersectClip)
    rim = QLinearGradient(rect.topLeft(), rect.bottomRight())
    for stop, rgb, alpha in ((0., 255, 132), (.27, 255, 45), (.53, 0, 45 if light else 23),
                             (.73, 255, 18), (1., 255, 91)):
        rim.setColorAt(stop, QColor(rgb, rgb, rgb, min(255, round(alpha * strength))))
    painter.setPen(QPen(rim, .9 if refractive else 1.3))
    painter.drawPath(shape) if path is not None else painter.drawRoundedRect(edge, max(0., radius - .5), max(0., radius - .5))
    # Very narrow inner reflection, analogous to an inset CSS highlight.
    inner = rect.adjusted(1.5, 1.5, -1.5, -1.5)
    if not inner.isEmpty() and path is None and not refractive:
        reflection = QLinearGradient(rect.topLeft(), rect.bottomRight())
        reflection.setColorAt(0., QColor(255, 255, 255, round(28 * strength)))
        reflection.setColorAt(.4, QColor(255, 255, 255, 0))
        reflection.setColorAt(1., QColor(255, 255, 255, round(13 * strength)))
        painter.setPen(QPen(reflection, .9))
        painter.drawRoundedRect(inner, max(0., radius - 1.5), max(0., radius - 1.5))
    painter.restore()


class GlassPushButton(QPushButton):
    """Original Qt/QSS face and label, with a translucent face and fine rim."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Draw the existing QSS bevel exactly once below, including alpha.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        install_control_hover(self)

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
        radius = self.property("glassRadius")
        radius = max(0., float(radius)) if radius is not None else 5.
        face = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        with QPainter(self) as painter:
            refractive = bool(self.property("glassRefractive"))
            level = normalize_music_glass_level(self.property("glassMaterialLevel"))
            if refractive:
                backdrop, source = widget_glass_backdrop(self, face)
                paint_liquid_glass(painter, face, radius=radius, light=bool(light),
                                   refractive=True, backdrop=backdrop, backdrop_rect=source,
                                   hovered=self.underMouse(), pressed=self.isDown(),
                                   active=self.isChecked(), cache_token=id(self),
                                   material_level=level,
                                   opacity=1. if self.isEnabled() else .35)
            painter.save()
            painter.setOpacity(min(1., GLASS_FACE_OPACITY * level / 50.))
            self.style().drawControl(QStyle.ControlElement.CE_PushButtonBevel, option, painter, self)
            painter.restore()
            if not refractive:
                paint_liquid_glass(painter, face, radius=radius, light=bool(light),
                                   hovered=self.underMouse(), pressed=self.isDown(),
                                   opacity=1. if self.isEnabled() else .35)
            label = QStyleOptionButton(option)
            label.rect = self.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, self)
            painter.save()
            painter.setClipRect(label.rect, Qt.ClipOperation.IntersectClip)
            self.style().drawControl(QStyle.ControlElement.CE_PushButtonLabel, label, painter, self)
            painter.restore()
            if self.hasFocus():
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(option.palette.highlight().color(), 1.4))
                painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2),
                                        max(0., radius - 1.5), max(0., radius - 1.5))
