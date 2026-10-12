"""A fine glass rim on the existing native button face.

Owners supply their original state color and geometry. Only that face becomes
slightly translucent; directional light is confined to the edge. No extra
plate or backdrop lens is introduced. The separate hover helper reuses bounded
native control pixels for motion; rim drawing itself requires no cache/timer.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QPushButton, QStyle, QStyleOptionButton
from local.control_hover import install_control_hover

GLASS_FACE_OPACITY = .80


def clear_glass_cache() -> None:
    """Compatibility for native preview callers; edge drawing has no cache."""


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
                       opacity: float = 1.0, cache_token=None) -> None:
    """Keep the supplied face and add an inset rim; ``None`` paints only a rim.

    Original alpha is multiplied by ``face_opacity``. Selection and hover never
    replace the owner's colors. Legacy backdrop/tint arguments are accepted
    without sampling or recoloring the center, so a changing scene cannot make
    a second surface or alter the original state's pigment.
    """
    if rect.isEmpty() or opacity <= 0:
        return
    radius = min(rect.width(), rect.height()) / 2 if radius is None else max(0, radius)
    radius = min(radius, min(rect.width(), rect.height()) / 2)
    painter.save()
    painter.setOpacity(painter.opacity() * min(1., opacity))
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    if base_color is not None:
        face = QColor(base_color)
        face.setAlphaF(face.alphaF() * min(1., max(0., face_opacity)))
        painter.setBrush(face)
        painter.drawRoundedRect(rect, radius, radius)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    edge = rect.adjusted(.5, .5, -.5, -.5)
    if border_color is not None:
        painter.setPen(QPen(QColor(border_color), 1.0))
        painter.drawRoundedRect(edge, max(0., radius - .5), max(0., radius - .5))
    # Opposing fine highlights follow the original boundary. The interior has
    # no gradient wash; icons, captions and selected state remain independent.
    strength = .68 if pressed else 1.18 if hovered else 1.
    rim = QLinearGradient(rect.topLeft(), rect.bottomRight())
    for stop, rgb, alpha in ((0., 255, 132), (.27, 255, 45), (.53, 0, 45 if light else 23),
                             (.73, 255, 18), (1., 255, 91)):
        rim.setColorAt(stop, QColor(rgb, rgb, rgb, min(255, round(alpha * strength))))
    painter.setPen(QPen(rim, 1.3))
    painter.drawRoundedRect(edge, max(0., radius - .5), max(0., radius - .5))
    # Very narrow inner reflection, analogous to an inset CSS highlight.
    inner = rect.adjusted(1.5, 1.5, -1.5, -1.5)
    if not inner.isEmpty():
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
            painter.save()
            painter.setOpacity(GLASS_FACE_OPACITY)
            self.style().drawControl(QStyle.ControlElement.CE_PushButtonBevel, option, painter, self)
            painter.restore()
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
