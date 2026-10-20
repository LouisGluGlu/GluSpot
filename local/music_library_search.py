"""One persistent editor and progress control for the library search strip."""
from __future__ import annotations

import math

from PySide6.QtCore import QEasingCurve, QRect, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QAbstractButton, QLineEdit

from local.liquid_glass import paint_liquid_glass, widget_glass_backdrop, widget_glass_level


def _ease(value):
    value = max(0., min(1., value))
    return value * value * (3. - 2. * value)


class LibrarySearch(QLineEdit):
    """Animate geometry first, then native text/caret, without replacing input."""

    poseChanged = Signal()
    MOTION_MS = 540
    GEOMETRY_END = .72

    def __init__(self, parent):
        super().__init__(parent)
        self._phase = 0.
        self._target = 0.
        self._extent = 0.
        self._ink = 0.
        self._blur_selection = None
        self._motion = QVariantAnimation(self)
        self._motion.setEasingCurve(QEasingCurve.Type.Linear)
        self._motion.valueChanged.connect(self._set_phase)
        # Keep style polishing separate from the animated palette. Replacing
        # the stylesheet after a query changes can restore the transparent
        # palette cached while the editor was collapsed, hiding text/caret.
        self.setStyleSheet("QLineEdit { background: transparent; border: none; "
                          "border-radius: 14px; padding: 0; }")
        self._set_phase(0.)

    @property
    def expanded(self):
        return bool(self._target)

    def set_expanded(self, expanded, *, focus=False):
        if not self.isEnabled() and expanded:
            return
        target = float(bool(expanded))
        if focus and expanded:
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        if target == self._target:
            return
        self._target = target
        self._motion.stop()
        self._motion.setDuration(max(1, round(self.MOTION_MS * abs(target - self._phase))))
        self._motion.setStartValue(self._phase)
        self._motion.setEndValue(target)
        self._motion.start()
        if not expanded:
            selection = self._selection_state()
            if not self.hasFocus() and self._blur_selection is not None:
                text, saved = self._blur_selection
                if text == self.text():
                    selection = saved
            self.clearFocus()
            self._restore_selection(selection)

    def _selection_state(self):
        return self.cursorPosition(), self.selectionStart(), self.selectionLength()

    def _restore_selection(self, state):
        cursor, start, length = state
        if start >= 0 and length:
            if cursor == start:
                self.setSelection(start + length, -length)
            else:
                self.setSelection(start, length)
        else:
            self.setCursorPosition(cursor)

    def settle(self):
        self._motion.stop()
        self._set_phase(self._target)

    def _set_phase(self, value):
        self._phase = max(0., min(1., float(value)))
        self._extent = _ease(self._phase / self.GEOMETRY_END)
        self._ink = _ease((self._phase - self.GEOMETRY_END) / (1. - self.GEOMETRY_END))
        palette = self.palette()
        for role, color in ((QPalette.ColorRole.Text, "#f3f4f7"),
                            (QPalette.ColorRole.PlaceholderText, "#b4bac7"),
                            (QPalette.ColorRole.HighlightedText, "#ffffff"),
                            (QPalette.ColorRole.Highlight, "#526680")):
            ink = QColor(color)
            ink.setAlphaF(self._ink)
            palette.setColor(role, ink)
        self.setPalette(palette)
        self.poseChanged.emit()
        self.update()

    def strip_geometry(self, strip):
        """The right edge stays fixed; one 44px progress slot stays on the left."""
        strip = QRect(strip)
        width = round(44 + max(0, strip.width() - 96) * self._extent)
        return QRect(strip.right() - width + 1, strip.y(), width, 44)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
            backdrop, source = widget_glass_backdrop(self, rect)
            paint_liquid_glass(painter, rect, radius=14 + 8 * (1 - self._extent),
                              refractive=True, backdrop=backdrop, backdrop_rect=source,
                              border_color=QColor("#727989") if self.hasFocus() else None,
                              cache_token=id(self), material_level=widget_glass_level(self))
        # Omitting the native ink paint while width changes also suppresses the
        # native selection/caret; the underlying editor and composition persist.
        if self._ink > 0.:
            super().paintEvent(event)

    def focusInEvent(self, event):
        selection = self._selection_state() if not self.expanded else None
        super().focusInEvent(event)
        if selection is not None:
            self._restore_selection(selection)
        if event.reason() in {Qt.FocusReason.MouseFocusReason, Qt.FocusReason.TabFocusReason,
                              Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason}:
            self.set_expanded(True)

    def focusOutEvent(self, event):
        self._blur_selection = self.text(), self._selection_state()
        super().focusOutEvent(event)

    def mousePressEvent(self, event):
        self.set_expanded(True)
        super().mousePressEvent(event)

    def hideEvent(self, event):
        self.settle()
        super().hideEvent(event)


class LibraryProgress(QAbstractButton):
    """The same painted track contracts into a ring beside the open editor."""

    def __init__(self, search, parent):
        super().__init__(parent)
        self.search = search
        self.setAccessibleName("歌曲进度；点击收起搜索")
        self.setToolTip("歌曲进度")
        self.clicked.connect(lambda: self.search.set_expanded(False))

    def layout_strip(self, strip):
        strip = QRect(strip)
        right = self.search.geometry().left() - 8
        self.setGeometry(strip.x(), strip.y(), max(44, right - strip.x()), 44)
        expanded = self.search.expanded
        self.setCursor(Qt.CursorShape.PointingHandCursor if expanded else Qt.CursorShape.ArrowCursor)
        self.setToolTip("收起搜索" if expanded else "歌曲进度")
        track = getattr(self.parentWidget(), "_now_playing", None)
        position, duration = self._times(track)
        self.setAccessibleDescription(f"{int(position)//60}:{int(position)%60:02} / {int(duration)//60}:{int(duration)%60:02}")
        self.update()

    @staticmethod
    def _times(track):
        def number(name):
            try:
                value = float(getattr(track, name, 0))
                return max(0., value) if math.isfinite(value) else 0.
            except (ValueError, TypeError, OverflowError):
                return 0.
        duration = number("duration_seconds")
        return min(duration, number("position_seconds")), duration

    def _track_path(self, completion=1.):
        blend = self.search._extent
        path = QPainterPath()
        count = max(1, round(96 * completion))
        for index in range(count + 1):
            fraction = completion * index / count
            angle = -math.pi / 2 + 2 * math.pi * fraction
            line_x, line_y = 14 + max(0, self.width() - 28) * fraction, 18.
            ring_x, ring_y = self.width() / 2 + 13 * math.cos(angle), 22 + 13 * math.sin(angle)
            point = (line_x + (ring_x - line_x) * blend, line_y + (ring_y - line_y) * blend)
            if index:
                path.lineTo(*point)
            else:
                path.moveTo(*point)
        return path

    def paintEvent(self, event):
        track = getattr(self.parentWidget(), "_now_playing", None)
        position, duration = self._times(track)
        fraction = position / duration if duration else 0.
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            extent = self.search._extent
            if extent > 0.:
                rect = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
                backdrop, source = widget_glass_backdrop(self, rect)
                painter.setOpacity(extent)
                paint_liquid_glass(painter, rect, radius=22, refractive=True,
                                  backdrop=backdrop, backdrop_rect=source, cache_token=id(self),
                                  hovered=self.underMouse(), pressed=self.isDown(),
                                  material_level=widget_glass_level(self))
                painter.setOpacity(1.)
            pen = QPen(QColor(255, 255, 255, 52), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawPath(self._track_path())
            if fraction > 0.:
                pen.setColor(QColor("#e9edf5"))
                painter.setPen(pen)
                painter.drawPath(self._track_path(fraction))
            if extent < 1. and self.width() >= 120:
                ink = QColor("#b4bac7")
                ink.setAlphaF(1 - extent)
                painter.setPen(ink)
                font = self.font()
                font.setPixelSize(10)
                painter.setFont(font)
                clock = lambda seconds: f"{int(seconds)//60}:{int(seconds)%60:02}"
                rect = QRectF(14, 25, self.width() - 28, 16)
                painter.drawText(rect, Qt.AlignmentFlag.AlignLeft, clock(position))
                painter.drawText(rect, Qt.AlignmentFlag.AlignRight, clock(duration))
            if self.hasFocus() and self.window().testAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange):
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(233, 237, 245, 110), 1))
                painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 20, 20)
