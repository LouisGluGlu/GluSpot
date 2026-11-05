"""Shared title and artist marquee.

The motion follows the K treatment in the title/artist study:

* a line that fits its slot stays still
* a line that overflows waits, scrolls in a straight line until its tail is
  visible, waits again, then jumps back to the start
* one cycle lasts ``max(8s, text_width / 28)``
* the opening wait, the travel, and the closing wait are 14%, 72%, and 14%
  of that cycle (the study's 0% / 14% / 86% / 100% keyframes)
* edge fades are 6% on the left and 8% on the right of the slot, and only
  the side that is actually clipped fades

Title and artist keep separate clocks. Callers reset a clock when the song
changes, refresh a moving line at 30 fps or slower, and skip that refresh
while the panel is hidden.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPen

MIN_CYCLE_SECONDS = 8.0
PIXELS_PER_CYCLE_SECOND = 28.0
START_HOLD_FRACTION = 0.14
TRAVEL_FRACTION = 0.72
END_HOLD_FRACTION = 0.14
LEFT_FADE_FRACTION = 0.06
RIGHT_FADE_FRACTION = 0.08
FRAME_SECONDS = 1.0 / 30.0
OVERFLOW_EPSILON = 1.0
# Island and wide-panel titles share this pace: same shape as K, twice as brisk.
ISLAND_TITLE_SPEED_SCALE = 2.0


@dataclass(frozen=True)
class MarqueePose:
    offset: float
    moving: bool
    delay: float
    fade_left: bool
    fade_right: bool


def cycle_seconds(text_width: float, speed_scale: float = 1.0) -> float:
    """Full pause-travel-pause cycle. ``speed_scale`` 2 halves it."""
    scale = float(speed_scale) if speed_scale and float(speed_scale) > 0.0 else 1.0
    return max(
        MIN_CYCLE_SECONDS / scale,
        max(0.0, float(text_width)) / (PIXELS_PER_CYCLE_SECOND * scale),
    )


def marquee_pose(
    text_width: float, view_width: float, elapsed: float, speed_scale: float = 1.0,
) -> MarqueePose:
    """Offset grows from 0 to the overflow, then the cycle restarts at 0."""
    text_width = max(0.0, float(text_width))
    view_width = max(0.0, float(view_width))
    overflow = text_width - view_width
    if overflow <= OVERFLOW_EPSILON:
        return MarqueePose(0.0, False, float("inf"), False, False)
    duration = cycle_seconds(text_width, speed_scale)
    start_hold = START_HOLD_FRACTION * duration
    travel = TRAVEL_FRACTION * duration
    elapsed = float(elapsed) % duration
    if elapsed < start_hold:
        offset = 0.0
        moving = False
        delay = start_hold - elapsed
    elif elapsed < start_hold + travel:
        offset = overflow * (elapsed - start_hold) / travel
        moving = True
        delay = min(FRAME_SECONDS, start_hold + travel - elapsed)
    else:
        offset = overflow
        moving = False
        delay = duration - elapsed
    fade_left = offset > 0.5
    fade_right = offset < overflow - 0.5
    return MarqueePose(offset, moving, delay, fade_left, fade_right)


def brightened_cover_accent(color: QColor) -> QColor | None:
    """Lift a cover pigment into a readable artist color.

    Neutral or invalid colors have no hue to lift, so the caller keeps the
    previous artist ink.
    """
    color = QColor(color)
    if not color.isValid() or color.alpha() == 0:
        return None
    hue, saturation, lightness, _alpha = color.getHslF()
    if hue < 0.0 or saturation < 0.06:
        return None
    lightness = min(0.86, max(0.70, lightness * 0.35 + 0.62))
    saturation = min(0.85, max(0.45, saturation))
    return QColor.fromHslF(hue, saturation, lightness, 1.0)


def paint_faded_text(
    painter: QPainter,
    rect: QRectF,
    draw,
    *,
    fade_left: bool,
    fade_right: bool,
) -> None:
    """Draw one line inside ``rect`` and fade only the clipped edges."""
    if rect.width() <= 0.0 or rect.height() <= 0.0:
        return
    if not fade_left and not fade_right:
        painter.save()
        painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
        draw(painter)
        painter.restore()
        return
    device = painter.device()
    dpr = max(1.0, float(device.devicePixelRatioF()) if device is not None else 1.0)
    width = max(1, int(math.ceil(rect.width() * dpr)))
    height = max(1, int(math.ceil(rect.height() * dpr)))
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(dpr)
    image.fill(Qt.GlobalColor.transparent)
    with QPainter(image) as layer:
        layer.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = painter.font()
        if isinstance(font, QFont):
            layer.setFont(font)
        pen = painter.pen()
        if isinstance(pen, QPen):
            layer.setPen(pen)
        layer.translate(-rect.left(), -rect.top())
        layer.setClipRect(rect)
        draw(layer)
        gradient = QLinearGradient(0.0, 0.0, rect.width(), 0.0)
        if fade_left:
            gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
            gradient.setColorAt(LEFT_FADE_FRACTION, QColor(0, 0, 0, 255))
        else:
            gradient.setColorAt(0.0, QColor(0, 0, 0, 255))
        right_edge = 1.0 - RIGHT_FADE_FRACTION
        if fade_right:
            gradient.setColorAt(right_edge, QColor(0, 0, 0, 255))
            gradient.setColorAt(1.0, QColor(0, 0, 0, 0))
        else:
            gradient.setColorAt(1.0, QColor(0, 0, 0, 255))
        layer.resetTransform()
        layer.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        layer.fillRect(QRectF(0.0, 0.0, rect.width(), rect.height()), gradient)
    painter.drawImage(rect.topLeft(), image)
