"""User-supplied audio symbols with bounded, aspect-preserving Qt SVG caches."""
from __future__ import annotations

from functools import lru_cache
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen
from PySide6.QtSvg import QSvgRenderer

from local.branding import ROOT_DIR


_ICON_FILES = {
    "output": "output.link.svg",
    "speaker": "speaker.fill.svg",
    "homepod": "homepod.fill.svg",
    "homepod-mini": "homepod.mini.fill.svg",
    "airpods-pro": "airpods.pro.svg",
}

_STATUS_KINDS = frozenset({
    "homepod", "homepod-mini", "airpods-pro", "headphones", "speaker", "airplay", "device",
})
_OPAQUE_ALPHA_TABLE = bytes(0 if value == 0 else 255 for value in range(256))


def _icon_kind(kind: str) -> str:
    key = str(kind or "").lower().replace("_", "-")
    return {"link": "output", "homepodmini": "homepod-mini", "speakers": "speaker",
            "headphone": "headphones", "headset": "headphones", "earphone": "headphones",
            "earphones": "headphones", "earbuds": "headphones",
            "airpods": "headphones"}.get(key, key)


@lru_cache(maxsize=8)
def _svg_renderer(kind: str) -> QSvgRenderer | None:
    filename = _ICON_FILES.get(kind)
    if filename is None:
        return None
    renderer = QSvgRenderer(str(ROOT_DIR / "assets" / "audio_outputs" / filename))
    return renderer if renderer.isValid() else None


@lru_cache(maxsize=128)
def _icon_mask(kind: str, width: int, height: int) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    renderer = _svg_renderer(kind)
    if renderer is not None:
        view = renderer.viewBoxF()
        factor = min(width / max(1.0, view.width()), height / max(1.0, view.height()))
        target = QRectF((width - view.width() * factor) / 2.0,
                        (height - view.height() * factor) / 2.0,
                        view.width() * factor, view.height() * factor)
        with QPainter(image) as painter:
            renderer.render(painter, target)
    return image


@lru_cache(maxsize=256)
def _tinted_icon(kind: str, width: int, height: int, rgba: int) -> QImage:
    image = _icon_mask(kind, width, height).copy()
    with QPainter(image) as painter:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(image.rect(), QColor.fromRgba(rgba))
    return image


def paint_audio_output_icon(painter: QPainter, rect: QRectF, kind: str, color: QColor) -> None:
    """Tint the supplied device/link asset; retain generic headphones as fallback.

    Device identification stays in the provider. This painter only consumes an
    explicit kind and does not infer HomePod/AirPods models from display names.
    Source SVG opacity and aspect ratio survive every tint and cached render.
    """
    key = _icon_kind(kind)
    if not rect.isValid() or rect.isEmpty():
        return
    if key in _ICON_FILES and _svg_renderer(key) is not None:
        device = painter.device()
        dpr = max(1.0, float(device.devicePixelRatioF())) if device is not None else 1.0
        width = max(1, min(512, math.ceil(rect.width() * dpr)))
        height = max(1, min(512, math.ceil(rect.height() * dpr)))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(rect, _tinted_icon(key, width, height, color.rgba()))
        painter.restore()
        return
    painter.save()
    painter.translate(rect.topLeft())
    painter.scale(rect.width() / 24.0, rect.height() / 24.0)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(color, 1.65, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin))
    if key == "airplay":
        painter.drawRoundedRect(QRectF(3.0, 3.5, 18.0, 13.0), 2.0, 2.0)
        triangle = QPainterPath()
        triangle.moveTo(7.5, 21.0)
        triangle.lineTo(12.0, 16.5)
        triangle.lineTo(16.5, 21.0)
        triangle.closeSubpath()
        painter.setBrush(color)
        painter.drawPath(triangle)
    elif key == "headphones":
        painter.drawArc(QRectF(4.0, 3.5, 16.0, 17.0), 0, 180 * 16)
        painter.drawRoundedRect(QRectF(3.7, 12.0, 4.0, 8.0), 1.8, 1.8)
        painter.drawRoundedRect(QRectF(16.3, 12.0, 4.0, 8.0), 1.8, 1.8)
    else:
        # Unidentified peripherals use a neutral device, distinct from both
        # the supplied speaker and the headphone fallback.
        painter.drawRoundedRect(QRectF(6.0, 2.5, 12.0, 19.0), 2.5, 2.5)
        painter.drawRoundedRect(QRectF(8.5, 5.5, 7.0, 10.0), 1.0, 1.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(12.0, 18.5), 1.0, 1.0)
    painter.restore()


def _status_signature(kinds: tuple[str, ...], count: int | None = None) -> tuple[tuple[str, ...], int]:
    """Preserve the first two distinct kinds in connection order."""
    normalized = tuple(_icon_kind(kind) for kind in kinds)
    member_count = len(normalized) if count is None else max(0, int(count))
    if not member_count:
        return ("output",), 0
    representatives = tuple(dict.fromkeys(
        kind if kind in _STATUS_KINDS else "device" for kind in normalized))[:2]
    return representatives or ("device",), member_count


def _glyph_image(kind: str, rect: QRectF) -> QImage:
    image = QImage(max(1, math.ceil(rect.width())), max(1, math.ceil(rect.height())),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    with QPainter(image) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_audio_output_icon(painter, QRectF(image.rect()), kind, QColor(255, 255, 255))
    return image


def _clear_glyph_halo(painter: QPainter, glyph: QImage, rect: QRectF, margin: int) -> None:
    # The source assets contain intentional translucent fills. Erasing the rear
    # glyph with their original alpha would darken overlapping regions. Use an
    # opaque silhouette only for the erase, then draw the untouched front asset.
    alpha = glyph.convertToFormat(QImage.Format.Format_Alpha8)
    data = bytes(alpha.constBits()).translate(_OPAQUE_ALPHA_TABLE)
    silhouette = QImage(data, alpha.width(), alpha.height(), alpha.bytesPerLine(),
                        QImage.Format.Format_Alpha8).copy()
    painter.save()
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
    for x, y in ((-margin, -margin), (-margin, margin), (margin, -margin), (margin, margin),
                 (0, -margin), (0, margin), (-margin, 0), (margin, 0), (0, 0)):
        painter.drawImage(rect.translated(x, y), silhouette)
    painter.restore()


@lru_cache(maxsize=128)
def _status_mask(representatives: tuple[str, ...], count: int, diameter: int) -> QImage:
    """Transparent cached composition, without the caller-owned circular face."""
    image = QImage(diameter, diameter, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    with QPainter(image) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if count == 0:
            rect = QRectF(diameter * .225, diameter * .225, diameter * .55, diameter * .55)
            paint_audio_output_icon(painter, rect, "output", QColor(255, 255, 255))
        elif count == 1:
            rect = QRectF(diameter * .14, diameter * .14, diameter * .72, diameter * .72)
            paint_audio_output_icon(painter, rect, representatives[0], QColor(255, 255, 255))
        elif len(representatives) == 1:
            rect = QRectF(diameter * .19, diameter * .11, diameter * .62, diameter * .51)
            paint_audio_output_icon(painter, rect, representatives[0], QColor(255, 255, 255))
        elif set(representatives) == {"homepod", "homepod-mini"}:
            rear = QRectF(diameter * .20, diameter * .105, diameter * .43, diameter * .43)
            front = QRectF(diameter * .43, diameter * .265, diameter * .40, diameter * .37)
            paint_audio_output_icon(painter, rear, "homepod", QColor(255, 255, 255))
            front_glyph = _glyph_image("homepod-mini", front)
            _clear_glyph_halo(painter, front_glyph, front, max(1, round(diameter * .012)))
            painter.drawImage(front, front_glyph)
        else:
            for kind, x in zip(representatives, (.14, .52)):
                rect = QRectF(diameter * x, diameter * .13, diameter * .34, diameter * .48)
                paint_audio_output_icon(painter, rect, kind, QColor(255, 255, 255))
        if count > 1:
            font = QFont("Segoe UI")
            font.setWeight(QFont.Weight.DemiBold)
            font.setPixelSize(max(1, round(diameter * .24)))
            metrics = QFontMetricsF(font)
            text = str(count)
            width = diameter * .62
            if metrics.horizontalAdvance(text) > width:
                font.setPixelSize(max(1, math.floor(font.pixelSize() * width / metrics.horizontalAdvance(text))))
            painter.setFont(font)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(QRectF(diameter * .19, diameter * .64, width, diameter * .29),
                             Qt.AlignmentFlag.AlignCenter, text)
    return image


@lru_cache(maxsize=256)
def _tinted_status(representatives: tuple[str, ...], count: int, diameter: int, rgba: int) -> QImage:
    image = _status_mask(representatives, count, diameter).copy()
    with QPainter(image) as painter:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(image.rect(), QColor.fromRgba(rgba))
    return image


def paint_audio_output_status(painter: QPainter, circle_rect: QRectF,
                              kinds: tuple[str, ...], color: QColor, *, count: int | None = None) -> None:
    """Paint connected device glyphs and the total member count inside a circle.

    Empty membership retains the output-link symbol. One device displays its
    supplied or generic glyph; homogeneous groups add a count below one glyph,
    and mixed groups use their first two distinct representatives above the
    total count. Only the HomePod/HomePod mini pair overlaps; all other pairs
    use ordered left/right slots. Pass an explicit count when kinds contains
    only the tracker's representatives rather than all member kinds.
    The caller owns the face, ring and opacity. Device kinds must come from
    provider evidence, never display-name guesses in paint.
    """
    if not circle_rect.isValid() or circle_rect.isEmpty():
        return
    representatives, count = _status_signature(kinds, count)
    device = painter.device()
    dpr = max(1.0, float(device.devicePixelRatioF())) if device is not None else 1.0
    logical_diameter = min(circle_rect.width(), circle_rect.height())
    diameter = max(1, min(512, math.ceil(logical_diameter * dpr)))
    target = QRectF(circle_rect.center().x() - logical_diameter / 2,
                    circle_rect.center().y() - logical_diameter / 2,
                    logical_diameter, logical_diameter)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.drawImage(target, _tinted_status(representatives, count, diameter, color.rgba()))
    painter.restore()
