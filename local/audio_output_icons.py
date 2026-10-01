"""User-supplied audio symbols with bounded, aspect-preserving Qt SVG caches."""
from __future__ import annotations

from functools import lru_cache
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtSvg import QSvgRenderer

from local.branding import ROOT_DIR


_ICON_FILES = {
    "output": "output.link.svg",
    "speaker": "speaker.fill.svg",
    "homepod": "homepod.fill.svg",
    "homepod-mini": "homepod.mini.fill.svg",
    "airpods-pro": "airpods.pro.svg",
}


def _icon_kind(kind: str) -> str:
    key = str(kind or "").lower().replace("_", "-")
    return {"link": "output", "homepodmini": "homepod-mini", "speakers": "speaker"}.get(key, key)


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
    elif "head" in key or "ear" in key or key == "airpods":
        painter.drawArc(QRectF(4.0, 3.5, 16.0, 17.0), 0, 180 * 16)
        painter.drawRoundedRect(QRectF(3.7, 12.0, 4.0, 8.0), 1.8, 1.8)
        painter.drawRoundedRect(QRectF(16.3, 12.0, 4.0, 8.0), 1.8, 1.8)
    else:
        # Unknown devices keep a neutral code-native speaker, without claiming
        # a model-specific source SVG that the provider has not identified.
        painter.drawRoundedRect(QRectF(6.0, 2.5, 12.0, 19.0), 2.5, 2.5)
        painter.drawEllipse(QRectF(10.5, 6.0, 3.0, 3.0))
        painter.drawEllipse(QRectF(8.5, 12.0, 7.0, 7.0))
    painter.restore()
