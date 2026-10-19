"""Small native snapshot transition for the three catalog collection groups."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget


def interpolate_rect(start, target, fraction):
    return QRectF(*(a + (b - a) * fraction for a, b in zip(
        (start.x(), start.y(), start.width(), start.height()),
        (target.x(), target.y(), target.width(), target.height()))))


class CatalogMotionOverlay(QWidget):
    """Bounded snapshots keep replaced group views continuous and inert."""
    groupRequested = Signal(str)

    def __init__(self, parent, frames, *, old_selected=None, new_selected=None):
        super().__init__(parent)
        self.frames = frames
        self.old_selected, self.new_selected = old_selected, new_selected
        self.fraction = 0.
        self.setGeometry(parent.rect())
        self._pressed_group = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed_group = next((key for key in reversed(tuple(self.frames))
                if isinstance(key,str) and self.pose(key)[0].contains(event.position())),None)
            event.accept()

    def mouseReleaseEvent(self,event):
        key=self._pressed_group;self._pressed_group=None
        if (event.button()==Qt.MouseButton.LeftButton and key in self.frames
                and self.pose(key)[0].contains(event.position())):
            # During motion a repeated click can only select/collapse a
            # collection group. It can never activate a song/playlist cover.
            self.groupRequested.emit(key)
        event.accept()

    def pose(self, key):
        before, after, start, target = self.frames[key]
        phase = self.fraction
        swapping = self.old_selected in self.frames and self.new_selected in self.frames
        if swapping:
            split = .42
            first = min(1., phase / split)
            second = max(0., (phase - split) / (1. - split))
            first = first * first * (3. - 2. * first)
            second = second * second * (3. - 2. * second)
            if key == self.old_selected:
                clicked_start = self.frames[self.new_selected][2]
                baseline = QRectF(clicked_start.x(), max(4., clicked_start.y() - target.height() - 12.),
                                  target.width(), target.height())
                return (interpolate_rect(start, baseline, first) if phase < split else
                        interpolate_rect(baseline, target, second)), first
            if key == self.new_selected:
                return interpolate_rect(start, target, second), second
            return interpolate_rect(start, target, second), second
        eased = phase * phase * (3. - 2. * phase)
        return interpolate_rect(start, target, eased), eased

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            # The newly selected group stays above its traveling neighbors.
            order = [key for key in self.frames if key != self.new_selected]
            if self.new_selected in self.frames:
                order.append(self.new_selected)
            for key in order:
                before, after, start, target = self.frames[key]
                rect, blend = self.pose(key)
                painter.setOpacity(1. - blend)
                if not before.isNull():
                    painter.drawImage(rect, before)
                painter.setOpacity(blend)
                if not after.isNull():
                    painter.drawImage(rect, after)
