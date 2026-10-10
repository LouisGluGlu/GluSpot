"""User-supplied source/account/playback SVGs with bounded Qt render caches."""
from __future__ import annotations

from functools import lru_cache
import math

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

from local.branding import ROOT_DIR


MUSIC_SOURCE_ICON_KEYS = frozenset({"qq", "qishui", "kugou", "netease", "search", "logout", "switch"})
MUSIC_CONTROL_ICON_KEYS = frozenset({"library", "shuffle", "sequence", "repeat_one"})


@lru_cache(maxsize=11)
def _svg_renderer(key: str) -> QSvgRenderer | None:
    if key not in MUSIC_SOURCE_ICON_KEYS | MUSIC_CONTROL_ICON_KEYS:
        return None
    folder = "music_controls" if key in MUSIC_CONTROL_ICON_KEYS else "music_sources"
    renderer = QSvgRenderer(str(ROOT_DIR / "assets" / folder / f"{key}.svg"))
    return renderer if renderer.isValid() else None


@lru_cache(maxsize=88)
def _rendered_icon(key: str, width: int, height: int, tint_rgba: int | None) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    renderer = _svg_renderer(key)
    if renderer is None:
        return image
    view = renderer.viewBoxF()
    factor = min(width / max(1.0, view.width()), height / max(1.0, view.height()))
    target = QRectF((width - view.width() * factor) / 2,
                    (height - view.height() * factor) / 2,
                    view.width() * factor, view.height() * factor)
    with QPainter(image) as painter:
        renderer.render(painter, target)
        if tint_rgba is not None:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(image.rect(), QColor.fromRgba(tint_rgba))
    return image


def music_source_icon_image(key: str, size: QSize, *, tint: QColor | None = None) -> QImage:
    """Keep supplied fills unless a caller explicitly tints a monochrome tool.

    The SVG bytes remain unchanged. UI callers render on the GUI thread,
    matching the existing audio-output SVG renderer/cache pattern.
    """
    if key not in MUSIC_SOURCE_ICON_KEYS | MUSIC_CONTROL_ICON_KEYS or size.isEmpty():
        return QImage()
    width, height = (max(1, min(256, value)) for value in (size.width(), size.height()))
    return _rendered_icon(key, width, height, tint.rgba() if tint is not None else None).copy()


def paint_music_source_icon(painter: QPainter, rect: QRectF, key: str,
                            *, tint: QColor | None = None) -> None:
    if not rect.isValid() or rect.isEmpty():
        return
    device = painter.device()
    dpr = device.devicePixelRatioF() if device is not None else 1.0
    size = QSize(math.ceil(rect.width() * dpr), math.ceil(rect.height() * dpr))
    image = music_source_icon_image(key, size, tint=tint)
    if image.isNull():
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(rect, image)
    painter.restore()
