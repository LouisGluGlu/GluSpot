"""User-supplied source/account/playback SVGs with bounded Qt render caches."""
from __future__ import annotations

from functools import lru_cache
import math

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QGradient, QImage, QLinearGradient, QPainter
from PySide6.QtSvg import QSvgRenderer

from local.branding import ROOT_DIR


MUSIC_SOURCE_ICON_KEYS = frozenset({"qq", "qishui", "kugou", "netease", "search", "logout", "switch"})
MUSIC_CONTROL_ICON_KEYS = frozenset({"library", "library_back", "shuffle", "sequence", "repeat_one", "personal_fm"})


@lru_cache(maxsize=13)
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


def _gradient_brush(width: int, stops: tuple[tuple[float, QColor], ...] | None,
                    phase: float) -> QLinearGradient | None:
    if not stops or len(stops) < 2:
        return None
    try:
        offset = float(phase)
        if not math.isfinite(offset):
            return None
        colors = tuple((float(position), QColor(color)) for position, color in stops)
        if any(not math.isfinite(position) or not 0.0 <= position <= 1.0 or not color.isValid()
               for position, color in colors):
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    # One full icon width is a repeat. Keeping the offset bounded avoids
    # precision loss during long sessions; positive phase moves ink right.
    start = (offset % 1.0) * width
    gradient = QLinearGradient(start, 0.0, start + width, 0.0)
    gradient.setSpread(QGradient.Spread.RepeatSpread)
    gradient.setStops(sorted(colors, key=lambda item: item[0]))
    return gradient


def music_source_icon_image(key: str, size: QSize, *, tint: QColor | None = None,
                            gradient_stops: tuple[tuple[float, QColor], ...] | None = None,
                            gradient_phase: float = 0.0) -> QImage:
    """Keep supplied fills unless a caller explicitly tints a monochrome tool.

    The SVG bytes remain unchanged. UI callers render on the GUI thread,
    matching the existing audio-output SVG renderer/cache pattern.
    Optional stops color the cached alpha mask with one horizontal repeat
    per image width. Positive phase moves bands right; matching endpoint
    colors keep the repeat seamless. Color/phase frames are never cached.
    """
    if key not in MUSIC_SOURCE_ICON_KEYS | MUSIC_CONTROL_ICON_KEYS or size.isEmpty():
        return QImage()
    width, height = (max(1, min(256, value)) for value in (size.width(), size.height()))
    gradient = _gradient_brush(width, gradient_stops, gradient_phase)
    if gradient is not None or (key == "personal_fm" and tint is not None):
        # Continuously changing ink reuses one SVG mask per size instead
        # of evicting static platform/control rasters with every color frame.
        image = _rendered_icon(key, width, height, None).copy()
        with QPainter(image) as painter:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(image.rect(), gradient if gradient is not None else tint)
        return image
    return _rendered_icon(key, width, height, tint.rgba() if tint is not None else None).copy()


def paint_music_source_icon(painter: QPainter, rect: QRectF, key: str,
                            *, tint: QColor | None = None,
                            gradient_stops: tuple[tuple[float, QColor], ...] | None = None,
                            gradient_phase: float = 0.0) -> None:
    if not rect.isValid() or rect.isEmpty():
        return
    device = painter.device()
    dpr = device.devicePixelRatioF() if device is not None else 1.0
    size = QSize(math.ceil(rect.width() * dpr), math.ceil(rect.height() * dpr))
    image = music_source_icon_image(key, size, tint=tint, gradient_stops=gradient_stops,
                                    gradient_phase=gradient_phase)
    if image.isNull():
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(rect, image)
    painter.restore()
