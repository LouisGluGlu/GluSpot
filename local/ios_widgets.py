from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from local.ui_theme import app_dark_mode, is_theme_change_event


class IosSwitch(QWidget):
    toggled = Signal(bool)
    CONTROL_HEIGHT = 21
    TRACK_WIDTH = 36.0
    TRACK_HEIGHT = 18.0
    NOTE_HEIGHT = 24
    NOTE_TOP_MARGIN = 5

    def __init__(self, text: str, checked: bool = False, parent=None):
        super().__init__(parent)
        self._text = str(text or "")
        self._checked = bool(checked)
        self._unavailable_reason = ""
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(self.CONTROL_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def text(self) -> str:
        return self._text

    def setText(self, text: str):
        self._text = str(text or "")
        self.update()

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool):
        checked = bool(checked)
        if self._checked == checked:
            return
        self._checked = checked
        self.update()

    def unavailable_reason(self) -> str:
        return self._unavailable_reason

    def set_unavailable_reason(self, reason: str = ""):
        self._unavailable_reason = str(reason or "").strip()
        self._refresh_height()

    def _shows_unavailable_reason(self) -> bool:
        return bool(self._unavailable_reason) and not self.isEnabled()

    def _refresh_height(self):
        height = self.CONTROL_HEIGHT
        if self._shows_unavailable_reason():
            height += self.NOTE_TOP_MARGIN + self.NOTE_HEIGHT
        self.setMinimumHeight(height)
        self.updateGeometry()
        self.update()

    def setEnabled(self, enabled: bool):
        super().setEnabled(enabled)
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ForbiddenCursor)
        self._refresh_height()

    def sizeHint(self) -> QSize:
        return QSize(164, self.minimumHeight())

    def mouseReleaseEvent(self, event):
        if not self.isEnabled():
            super().mouseReleaseEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self._checked = not self._checked
            self.toggled.emit(self._checked)
            self.update()
        super().mouseReleaseEvent(event)

    def changeEvent(self, event):
        if is_theme_change_event(event):
            self.update()
        super().changeEvent(event)

    def paintEvent(self, event):
        dark = app_dark_mode()
        enabled = self.isEnabled()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        control_height = float(self.CONTROL_HEIGHT)
        label_rect = QRectF(0, 0, max(0, self.width() - 50), control_height)
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        if enabled:
            painter.setPen(QColor(245, 245, 247) if dark else QColor(28, 28, 30))
        else:
            painter.setPen(QColor(132, 132, 138) if dark else QColor(142, 142, 147))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)

        switch_w = self.TRACK_WIDTH
        switch_h = self.TRACK_HEIGHT
        switch = QRectF(float(self.width()) - switch_w, (control_height - switch_h) / 2.0, switch_w, switch_h)
        if enabled:
            bg = QColor(52, 199, 89) if self._checked else (QColor(72, 72, 76) if dark else QColor(209, 209, 214))
        else:
            bg = QColor(70, 70, 74) if dark else QColor(220, 220, 224)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(switch, switch_h / 2.0, switch_h / 2.0)

        knob = switch_h - 4.0
        knob_x = switch.right() - knob - 2.0 if self._checked else switch.left() + 2.0
        knob_rect = QRectF(knob_x, switch.top() + 2.0, knob, knob)
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(knob_rect)
        painter.setPen(QPen(QColor(0, 0, 0, 18), 1.0))
        painter.drawEllipse(knob_rect.adjusted(0.5, 0.5, -0.5, -0.5))

        painter.setFont(QFont("Microsoft YaHei UI", 6, QFont.Weight.Bold))
        if enabled:
            painter.setPen(QColor(255, 255, 255, 215) if self._checked else QColor(116, 116, 120, 190))
        else:
            painter.setPen(QColor(150, 150, 156) if dark else QColor(168, 168, 174))
        state_text = "开" if self._checked else "关"
        state_rect = switch.adjusted(5.0, 0.0, -5.0, 0.0)
        alignment = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if self._checked else Qt.AlignmentFlag.AlignRight)
        painter.drawText(state_rect, alignment, state_text)

        if self._shows_unavailable_reason():
            note_top = control_height + float(self.NOTE_TOP_MARGIN)
            note_rect = QRectF(0, note_top, float(self.width()), float(self.NOTE_HEIGHT))
            painter.setPen(QPen(QColor(255, 82, 82, 86) if dark else QColor(255, 59, 48, 62), 1.0))
            painter.setBrush(QColor(255, 82, 82, 24) if dark else QColor(255, 59, 48, 20))
            painter.drawRoundedRect(note_rect, 7.0, 7.0)

            note_font = QFont("Microsoft YaHei UI", 8)
            painter.setFont(note_font)
            painter.setPen(QColor(255, 166, 166) if dark else QColor(160, 42, 38))
            metrics = QFontMetrics(note_font)
            note_text = metrics.elidedText(
                self._unavailable_reason,
                Qt.TextElideMode.ElideRight,
                max(0, int(note_rect.width() - 14)),
            )
            painter.drawText(
                note_rect.adjusted(8.0, 0.0, -8.0, 0.0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                note_text,
            )
