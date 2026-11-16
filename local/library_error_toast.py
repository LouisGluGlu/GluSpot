"""One liquid-glass error toast for every music-library layout.

Playback failures such as "音频连接失败，请检查网络后重试" used to be drawn
on the window's bottom edge, under the player card. They now sit in one
shared pill just above that card, hold for ten seconds, then fade out.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter

from local.liquid_glass import paint_liquid_glass, widget_glass_backdrop, widget_glass_level
from local.playback_entry_window import _entry_font

VISIBLE_SECONDS = 10.0
FADE_SECONDS = 0.45
TOAST_GAP = 10.0


class LibraryErrorToast:
    def __init__(self) -> None:
        self.message = ""
        self.shown_at = 0.0

    def present(self, message: str, now: float) -> None:
        message = str(message or "")
        if not message:
            self.clear()
            return
        if message != self.message:
            self.shown_at = float(now)
        self.message = message

    def clear(self) -> None:
        self.message = ""
        self.shown_at = 0.0

    def opacity(self, now: float) -> float:
        if not self.message:
            return 0.0
        elapsed = max(0.0, float(now) - self.shown_at)
        if elapsed <= VISIBLE_SECONDS:
            return 1.0
        fade = (elapsed - VISIBLE_SECONDS) / FADE_SECONDS
        if fade >= 1.0:
            return 0.0
        t = max(0.0, min(1.0, fade))
        return 1.0 - t * t * (3.0 - 2.0 * t)

    def finished(self, now: float) -> bool:
        return bool(self.message) and self.opacity(now) <= 0.0


def error_toast_rect(host_width: float, anchor_top: float, text: str, font: QFont | None = None) -> QRectF:
    """Centered pill just above ``anchor_top`` (the player card's top)."""
    font = font or _entry_font(13)
    metrics = QFontMetricsF(font)
    text_width = metrics.horizontalAdvance(str(text or ""))
    pad_x = 18.0
    height = max(32.0, metrics.height() + 14.0)
    width = min(max(120.0, text_width + pad_x * 2.0), max(120.0, float(host_width) - 32.0))
    left = max(16.0, (float(host_width) - width) / 2.0)
    top = float(anchor_top) - TOAST_GAP - height
    if top < 8.0:
        top = 8.0
    return QRectF(left, top, width, height)


def paint_library_error_toast(painter: QPainter, widget, rect: QRectF, text: str, opacity: float) -> None:
    """Translucent fill, thin bright rim, and the same blur as the other glass."""
    if opacity <= 0.001 or rect.isEmpty() or not text:
        return
    painter.save()
    painter.setOpacity(painter.opacity() * opacity)
    backdrop, source = widget_glass_backdrop(widget, rect)
    radius = rect.height() / 2.0
    paint_liquid_glass(
        painter, rect, radius=radius, backdrop=backdrop, backdrop_rect=source,
        refractive=True, light=True, opacity=0.88,
        border_color=QColor(255, 255, 255, 190),
        material_level=widget_glass_level(widget),
        cache_token=(id(widget), "library-error-toast", round(rect.width()), round(rect.height())),
    )
    font = _entry_font(13)
    painter.setFont(font)
    painter.setPen(QColor(255, 236, 230))
    inner = rect.adjusted(16.0, 0.0, -16.0, 0.0)
    shown = painter.fontMetrics().elidedText(str(text), Qt.TextElideMode.ElideRight, max(1, round(inner.width())))
    painter.drawText(inner, Qt.AlignmentFlag.AlignCenter, shown)
    painter.restore()
