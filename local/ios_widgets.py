from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from local.ui_theme import app_dark_mode, is_theme_change_event


class IosSwitch(QWidget):
    toggled = Signal(bool)

    def __init__(self, text: str, checked: bool = False, parent=None):
        super().__init__(parent)
        self._text = str(text or "")
        self._checked = bool(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool):
        checked = bool(checked)
        if self._checked == checked:
            return
        self._checked = checked
        self.update()

    def mouseReleaseEvent(self, event):
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
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        label_rect = QRectF(0, 0, max(0, self.width() - 64), self.height())
        painter.setFont(QFont("Microsoft YaHei UI", 10))
        painter.setPen(QColor(245, 245, 247) if dark else QColor(28, 28, 30))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)

        switch_w = 48.0
        switch_h = 24.0
        switch = QRectF(float(self.width()) - switch_w, (float(self.height()) - switch_h) / 2.0, switch_w, switch_h)
        bg = QColor(52, 199, 89) if self._checked else (QColor(72, 72, 76) if dark else QColor(209, 209, 214))
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

        painter.setFont(QFont("Microsoft YaHei UI", 7, QFont.Weight.Bold))
        painter.setPen(QColor(255, 255, 255, 215) if self._checked else QColor(116, 116, 120, 190))
        state_text = "开" if self._checked else "关"
        state_rect = switch.adjusted(6.0, 0.0, -6.0, 0.0)
        alignment = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if self._checked else Qt.AlignmentFlag.AlignRight)
        painter.drawText(state_rect, alignment, state_text)
