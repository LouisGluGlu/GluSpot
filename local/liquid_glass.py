"""Fine native control rims, plus an opt-in blurred scene material.

Owners supply their original state color and geometry. Only that face becomes
slightly translucent; directional light is confined to the edge. No extra
plate or backdrop lens is introduced by default. Music surfaces can opt into a
bounded scene crop with blur and gentle edge refraction. Neither mode captures
widgets or starts timers; glyphs remain independent foreground paint.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from functools import lru_cache
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


@contextmanager
def synchronous_glass_capture(owner):
    """QWidget.render/grab redirect painting without changing painter.device()."""
    previous = int(getattr(owner, '_glass_capture_depth', 0))
    owner._glass_capture_depth = previous + 1
    try:
        yield
    finally:
        owner._glass_capture_depth = previous


class GlassCaptureMixin:
    """Keep redirected/offscreen captures on the complete synchronous material."""
    def render(self, *args, **kwargs):
        with synchronous_glass_capture(self):
            return super().render(*args, **kwargs)

    def grab(self, *args, **kwargs):
        with synchronous_glass_capture(self):
            return super().grab(*args, **kwargs)


def glass_owner_is_live(owner):
    """Lifecycle/capture check shared by paint and popup pixel preparation."""
    if owner is None:
        return False
    try:
        if not owner.isVisible() or owner.window().isMinimized():
            return False
        current = owner
        while current is not None:
            if any(bool(getattr(current, flag, False)) for flag in (
                '_glass_capture_depth', '_capturing_transition_snapshot', '_capturing_content_snapshot',
                '_navigation_cover_capture', '_navigation_capture_hide_cover', '_navigation_cover_frozen',
            )):
                return False
            current = current.parentWidget()
    except RuntimeError:
        return False
    return True


def live_glass_paint(painter, owner):
    """Explicit opt-in only; captures and QImage blur sources remain synchronous."""
    return owner is not None and painter.device() is owner and glass_owner_is_live(owner)


def clear_glass_cache() -> None:
    """Release only the bounded, opt-in scene-material rasters."""
    global _glass_material_cache_bytes
    _glass_material_cache.clear()
    _glass_material_slots.clear()
    _glass_rim_samples.cache_clear()
    _glass_material_cache_bytes = 0
    from local.liquid_glass_tasks import glass_pixel_tasks
    service = glass_pixel_tasks(create=False)
    if service is not None:
        service.clear()


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
        if not callable(getter):
            # Library/login pages already receive the same settings through
            # their background context rather than a song-page owner.
            getter = getattr(current, "_background_settings_getter", None)
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
    # Keep the same float64 operation order while reusing the two row
    # results; a material refresh samples twice, and need not allocate every
    # intermediate full RGBA plane again for the following operation.
    top = pixels[y0, x0] * (1. - fx)
    top += pixels[y0, x1] * fx
    top *= 1. - fy
    bottom = pixels[y1, x0] * (1. - fx)
    bottom += pixels[y1, x1] * fx
    bottom *= fy
    top += bottom
    return top


@lru_cache(maxsize=8)
def _glass_rim_samples(core_width, core_height, padding, radius, scale):
    """Immutable shape coordinates shared by successive backdrop frames.

    The two float32 maps retain at most 8 * 2 * 160 * 160 * 4 bytes. Only
    geometry is retained; scene pixels, widgets and song identity stay out.
    functools keeps concurrent GUI capture and worker cache access coherent.
    """
    yy, xx = np.mgrid[:core_height, :core_width].astype(np.float32)
    dx, dy = xx + .5 - core_width / 2., yy + .5 - core_height / 2.
    corner = min(radius * scale, core_width / 2., core_height / 2.)
    qx, qy = np.abs(dx) - (core_width / 2. - corner), np.abs(dy) - (core_height / 2. - corner)
    distance = np.hypot(np.maximum(qx, 0.), np.maximum(qy, 0.)) + np.minimum(np.maximum(qx, qy), 0.) - corner
    band = np.clip(1. + distance / max(1.5, 5. * scale), 0., 1.) ** 2
    length = np.maximum(1., np.hypot(dx, dy))
    displacement = max(.18, .9 * scale) * band
    x = xx + padding + dx / length * displacement
    y = yy + padding + dy / length * displacement
    x.setflags(write=False)
    y.setflags(write=False)
    return x, y


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
    rim_x, rim_y = _glass_rim_samples(core_width, core_height, padding, radius, scale)
    refracted = _sample_rgba(blurred, rim_x, rim_y).round().astype(np.uint8)
    raw = refracted.tobytes()
    return QImage(raw, core_width, core_height, core_width * 4, QImage.Format.Format_RGBA8888).copy()


def _glass_material(backdrop: QImage, source_rect: QRectF, rect: QRectF,
                    radius: float, cache_token,
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
    # Hover/theme/selection veils and the rim are painted above this raster;
    # none changes its crop, blur or refraction. Share the expensive material
    # through those live state changes instead of rerasterizing it on input.
    slot = (token, backdrop.width(), backdrop.height(), geometry, material_level)
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


def _render_glass_request(payload):
    image, sample, width, height, radius, level = payload
    return _render_glass_material(image, QRectF(*sample), width, height, radius, level)


def _live_glass_material(owner, role, scene, dirty, backdrop, source_rect, rect, radius, level,
                         transition=0.):
    from local.liquid_glass_tasks import glass_pixel_tasks
    service = glass_pixel_tasks()
    if service is None:
        return None
    sample = tuple(float(value) for value in (
        source_rect.x(), source_rect.y(), source_rect.width(), source_rect.height()))
    geometry = tuple(round(value, 4) for value in (
        *sample, rect.width(), rect.height(), radius, float(level),
        float(backdrop.width()), float(backdrop.height())))
    # The material is local to its destination. Moving an already sampled
    # face (for example inside a control layer) changes only dirty coverage;
    # the source crop and shape above remain the strict pixel identity.
    # An independent implicitly shared wrapper is safe for read-only worker
    # use; any writer detaches. No deep copy is claimed or required here.
    snapshot = QImage(backdrop)
    payload = (snapshot, sample, rect.width(), rect.height(), radius, level)
    return service.request(owner, role, scene, geometry, backdrop.cacheKey(), payload,
                           _render_glass_request, dirty, byte_count=int(snapshot.sizeInBytes()),
                           interval=GLASS_MATERIAL_FRAME_SECONDS, transition=transition)


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
                       path: QPainterPath | None = None,
                       material_owner=None, material_role=None, material_scene=None,
                       material_dirty: QRectF | None = None,
                       material_layer: bool = False, material_transition: float = 0.) -> None:
    """Keep the supplied face and add an inset rim; ``None`` paints only a rim.

    Original alpha is multiplied by ``face_opacity``. Selection and hover never
    replace the owner's colors. Legacy backdrop/tint arguments are accepted
    without sampling or recoloring the center, so a changing scene cannot make
    a second surface or alter the original state's pigment. ``refractive`` is
    opt-in: sample ``backdrop_rect`` in native backdrop pixels, blur that crop,
    and add a mild theme veil before the same sharp edge. No widget capture or
    timer is involved, and glyphs remain the caller's independent next layer.
    ``material_layer`` explicitly permits a transient QImage canvas belonging
    to a live owner; captures remain synchronous. ``material_transition`` is
    for owners whose existing animation clock continues to paint the blend.
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
                live_layer = (material_layer and isinstance(painter.device(), QImage)
                              and glass_owner_is_live(material_owner))
                if material_role is not None and (live_layer or live_glass_paint(painter, material_owner)):
                    material = _live_glass_material(
                        material_owner, material_role, material_scene,
                        rect if material_dirty is None else material_dirty,
                        backdrop, sample, rect, radius, material_level, material_transition)
                else:
                    material = _glass_material(backdrop, sample, rect, radius, cache_token, material_level)
                if material is not None:
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


class GlassPushButton(GlassCaptureMixin, QPushButton):
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
