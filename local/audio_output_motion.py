"""Bounded connection motion, independent of device I/O and the lyric clock."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

from local.audio_output_icons import paint_audio_output_icon, paint_audio_output_status
from local.audio_output_status import ConnectedOutputStatus, OutputMember
from local.music_lyric_motion import MotionSimulationClock


def _smooth(value: float) -> float:
    value = min(1.0, max(0.0, value))
    return value * value * (3.0 - 2.0 * value)


@dataclass(frozen=True)
class ConnectionPose:
    previous: ConnectedOutputStatus
    target: ConnectedOutputStatus
    device: OutputMember
    old_scale: float = 1.0
    old_opacity: float = 1.0
    new_scale: float = 0.06
    new_opacity: float = 0.0
    blue: float = 0.0
    capsule: float = 0.0
    final_opacity: float = 0.0
    glyph_target: ConnectedOutputStatus | None = None


class OutputConnectionMotion:
    """Queue confirmations; reconcile removals without restarting visible poses.

    The clock bounds long UI stalls. Paint consumes the current immutable pose
    and never advances it. Fast arrivals queue by identity, while volume and
    discovery updates merely refresh the target. Hiding intentionally settles
    the engine rather than replaying a stale notice when the surface returns.
    """

    DURATION = 3.35
    MAX_QUEUED = 8

    def __init__(self):
        self._clock = MotionSimulationClock()
        self._queue = deque(maxlen=self.MAX_QUEUED)
        self.status = ConnectedOutputStatus()
        self.pose: ConnectionPose | None = None
        self._elapsed = 0.0
        self._return_from: ConnectionPose | None = None
        self._return_elapsed = 0.0

    @property
    def active(self) -> bool:
        return self.pose is not None

    def settle(self, status: ConnectedOutputStatus, now: float) -> None:
        self.status = status
        self.pose = None
        self._queue.clear()
        self._return_from = None
        self._elapsed = 0.0
        self._clock.reset(now)

    def pause(self, now: float) -> None:
        """A bitmap transition owns paint; retain the pose without time debt."""
        self._clock.reset(now)

    def announce(self, device: OutputMember, previous: ConnectedOutputStatus,
                 target: ConnectedOutputStatus, now: float) -> bool:
        self.status = target
        if ((self.pose is not None and self.pose.device.id == device.id)
                or any(item[0].id == device.id for item in self._queue)):
            return False
        event = (device, previous, target)
        if self.active:
            self._queue.append(event)
        else:
            self._begin(event, now)
        return True

    def _begin(self, event, now: float):
        device, previous, target = event
        self.pose = ConnectionPose(previous, target, device)
        self._elapsed = 0.0
        self._return_from = None
        self._clock.reset(now)

    def reconcile(self, status: ConnectedOutputStatus, now: float, *, removed: tuple[str, ...] = ()):
        self.status = status
        if self.pose is None:
            return
        self._queue = deque((item for item in self._queue if item[0].id not in removed),
                            maxlen=self.MAX_QUEUED)
        if removed and self._return_from is None:
            # Freeze the actual displayed transform at interruption, then
            # blend it back to the confirmed route over a short continuous exit.
            self._return_from = self.pose
            self._return_elapsed = 0.0
            self._clock.reset(now)
        else:
            self.pose = replace(self.pose, target=status)

    def advance(self, now: float) -> bool:
        if self.pose is None:
            return False
        elapsed = self._clock.advance(now)
        if self._return_from is not None:
            self._return_elapsed += elapsed
            blend = _smooth(self._return_elapsed / .32)
            source = self._return_from
            self.pose = replace(source, target=self.status,
                glyph_target=source.glyph_target or source.target,
                old_opacity=source.old_opacity * (1 - blend),
                new_opacity=source.new_opacity * (1 - blend),
                capsule=source.capsule * (1 - blend),
                final_opacity=source.final_opacity + (1 - source.final_opacity) * blend)
            complete = self._return_elapsed >= .32
        else:
            self._elapsed += elapsed
            t = self._elapsed
            old = 1 - _smooth(t / .24)
            growth = _smooth((t - .09) / .36)
            # Reach normal size, grow a little more, then settle. Maximum glyph
            # diameter is .72 * 1.12, safely inside the circular face.
            bump = .12 * math.sin(math.pi * min(1.0, max(0.0, (t - .45) / .42))) ** 2
            scale = .06 + .94 * growth + bump
            capsule = _smooth((t - 1.08) / .34) * (1 - _smooth((t - 2.52) / .36))
            final = _smooth((t - 2.95) / .40)
            self.pose = replace(self.pose,
                old_scale=.08 + .92 * old, old_opacity=old,
                new_scale=scale, new_opacity=_smooth((t - .09) / .18) * (1 - final),
                blue=_smooth((t - .16) / .32), capsule=capsule,
                final_opacity=final)
            complete = t >= self.DURATION
        if complete:
            if self._queue:
                device, _previous, _target = self._queue.popleft()
                # The last sequence has just displayed its final target. A
                # queued introduction starts from that visible group, never
                # from an older snapshot captured when the event was queued.
                previous = self.pose.target
                self._begin((device, previous, self.status), now)
            else:
                self.pose = None
                self._return_from = None
        return True


def connection_capsule_rect(circle: QRectF, pose: ConnectionPose,
                            font: QFont, available_left: float) -> QRectF:
    """Anchor on the original right edge; never move neighboring controls."""
    text_width = QFontMetricsF(font).horizontalAdvance(pose.device.name)
    extra = min(max(0.0, circle.left() - available_left),
                min(circle.width() * 5.0, text_width + circle.width() * .34))
    return circle.adjusted(-extra * pose.capsule, 0, 0, 0)


def paint_connection_pose(painter: QPainter, circle: QRectF, pose: ConnectionPose,
                          font: QFont, *, available_left: float = 16.0) -> None:
    """Direct vector transforms avoid caching a whole control row each frame."""
    painter.save()
    base_opacity = painter.opacity()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    capsule = connection_capsule_rect(circle, pose, font, available_left)
    p = pose.capsule
    painter.setPen(QPen(QColor(round(168 - 93 * p), round(170 - 94 * p), round(172 - 95 * p)), 1.2))
    painter.setBrush(QColor(round(248 - 193 * p), round(249 - 193 * p), round(250 - 193 * p), 245))
    painter.drawRoundedRect(capsule, circle.height() / 2, circle.height() / 2)
    shape = QPainterPath()
    shape.addRoundedRect(capsule.adjusted(1, 1, -1, -1), circle.height() / 2, circle.height() / 2)
    painter.setClipPath(shape)
    grey = QColor(44, 46, 48)
    blue = QColor(0, 175, 228)
    def scaled(rect, factor):
        side = rect.width() * factor
        return QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2, side, side)
    if pose.old_opacity > 0:
        painter.setOpacity(base_opacity * pose.old_opacity)
        paint_audio_output_status(painter, scaled(circle, pose.old_scale),
                                  pose.previous.representatives, grey, count=pose.previous.count)
    painter.setOpacity(base_opacity * pose.new_opacity)
    color = QColor(round(grey.red() + (blue.red() - grey.red()) * pose.blue),
                   round(grey.green() + (blue.green() - grey.green()) * pose.blue),
                   round(grey.blue() + (blue.blue() - grey.blue()) * pose.blue))
    # On expansion the glyph travels to the pill's left cap; text is revealed
    # by the growing capsule and does not move the other three controls.
    color = QColor(round(color.red() + (232 - color.red()) * p),
                   round(color.green() + (233 - color.green()) * p),
                   round(color.blue() + (234 - color.blue()) * p))
    glyph_circle = QRectF(circle)
    glyph_circle.moveLeft(capsule.left())
    glyph = scaled(glyph_circle, .72 * pose.new_scale)
    glyph_target = pose.glyph_target or pose.target
    if pose.final_opacity > 0 and pose.device.kind in glyph_target.representatives:
        representatives = glyph_target.representatives
        if len(representatives) == 2 and set(representatives) == {"homepod", "homepod-mini"}:
            coordinates = ((.20, .105, .43, .43) if pose.device.kind == "homepod"
                           else (.43, .265, .40, .37))
        elif len(representatives) == 2:
            coordinates = ((.14, .13, .34, .48) if representatives[0] == pose.device.kind
                           else (.52, .13, .34, .48))
        elif glyph_target.count > 1:
            coordinates = (.19, .11, .62, .51)
        else:
            coordinates = (.14, .14, .72, .72)
        x, y, w, h = coordinates
        destination = QRectF(circle.left() + x * circle.width(), circle.top() + y * circle.height(),
                             w * circle.width(), h * circle.height())
        blend = pose.final_opacity
        glyph = QRectF(glyph.x() + (destination.x() - glyph.x()) * blend,
                       glyph.y() + (destination.y() - glyph.y()) * blend,
                       glyph.width() + (destination.width() - glyph.width()) * blend,
                       glyph.height() + (destination.height() - glyph.height()) * blend)
        color = QColor(round(color.red() + (grey.red() - color.red()) * blend),
                       round(color.green() + (grey.green() - color.green()) * blend),
                       round(color.blue() + (grey.blue() - color.blue()) * blend))
    paint_audio_output_icon(painter, glyph, pose.device.kind, color)
    if p > 0:
        painter.setOpacity(base_opacity * pose.new_opacity * p)
        painter.setFont(font)
        painter.setPen(QColor(235, 236, 238))
        full_capsule = connection_capsule_rect(circle, replace(pose, capsule=1.0), font, available_left)
        text_rect = QRectF(capsule.left() + circle.width() * .98, capsule.top(),
                           max(0.0, full_capsule.width() - circle.width() * 1.23), capsule.height())
        text = QFontMetricsF(font).elidedText(pose.device.name, Qt.TextElideMode.ElideRight,
                                             max(0.0, text_rect.width()))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, text)
    if pose.final_opacity > 0:
        painter.setOpacity(base_opacity * pose.final_opacity)
        paint_audio_output_status(painter, circle, pose.target.representatives, grey, count=pose.target.count)
    painter.restore()
