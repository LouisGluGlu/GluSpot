from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from local.ui_theme import app_dark_mode, is_theme_change_event
from local.liquid_glass import paint_liquid_glass
from local.control_hover import install_control_hover


class IosSwitch(QWidget):
    toggled = Signal(bool)
    CONTROL_HEIGHT = 21
    TRACK_WIDTH = 36.0
    TRACK_HEIGHT = 18.0
    NOTE_HEIGHT = 24
    NOTE_TOP_MARGIN = 5
    # Apple style (settings window's new layout): platform proportions,
    # no state text, sliding knob, liquid-glass knob while pressed.
    APPLE_CONTROL_HEIGHT = 24
    APPLE_TRACK_WIDTH = 40.0
    APPLE_TRACK_HEIGHT = 24.0
    APPLE_ON = QColor(52, 199, 89)

    def __init__(self, text: str, checked: bool = False, parent=None):
        super().__init__(parent)
        self._text = str(text or "")
        self._checked = bool(checked)
        self._unavailable_reason = ""
        self._apple_style = False
        self._position = 1.0 if self._checked else 0.0
        self._press = 0.0
        self._slide = QVariantAnimation(self)
        self._slide.setDuration(260)
        self._slide.setEasingCurve(QEasingCurve.Type.OutBack)
        self._slide.valueChanged.connect(self._set_position)
        self._squeeze = QVariantAnimation(self)
        self._squeeze.setDuration(140)
        self._squeeze.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._squeeze.valueChanged.connect(self._set_press)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(self.CONTROL_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        install_control_hover(self)

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
        self._animate_to_state()
        self.update()

    def apple_style(self) -> bool:
        return self._apple_style

    def set_apple_style(self, enabled: bool):
        self._apple_style = bool(enabled)
        self._slide.stop()
        self._position = 1.0 if self._checked else 0.0
        self._refresh_height()

    def _control_height(self) -> int:
        return self.APPLE_CONTROL_HEIGHT if self._apple_style else self.CONTROL_HEIGHT

    def track_width(self) -> float:
        return self.APPLE_TRACK_WIDTH if self._apple_style else self.TRACK_WIDTH

    def _set_position(self, value):
        self._position = float(value)
        self.update()

    def _set_press(self, value):
        self._press = float(value)
        self.update()

    def _animate_to_state(self):
        target = 1.0 if self._checked else 0.0
        self._slide.stop()
        if not (self._apple_style and self.isVisible()):
            self._position = target
            return
        self._slide.setStartValue(self._position)
        self._slide.setEndValue(target)
        self._slide.start()

    def _animate_press(self, pressed: bool):
        if not self._apple_style:
            return
        self._squeeze.stop()
        self._squeeze.setStartValue(self._press)
        self._squeeze.setEndValue(1.0 if pressed else 0.0)
        self._squeeze.start()

    def unavailable_reason(self) -> str:
        return self._unavailable_reason

    def set_unavailable_reason(self, reason: str = ""):
        self._unavailable_reason = str(reason or "").strip()
        self._refresh_height()

    def _shows_unavailable_reason(self) -> bool:
        return bool(self._unavailable_reason) and not self.isEnabled()

    def _refresh_height(self):
        height = self._control_height()
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

    def mousePressEvent(self, event):
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self._animate_press(True)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if not self.isEnabled():
            super().mouseReleaseEvent(event)
            return
        self._animate_press(False)
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self._checked = not self._checked
            self._animate_to_state()
            self.toggled.emit(self._checked)
            self.update()
        super().mouseReleaseEvent(event)

    def changeEvent(self, event):
        if is_theme_change_event(event):
            self.update()
        super().changeEvent(event)

    def paintEvent(self, event):
        dark_override = self.property("darkMode")
        dark = app_dark_mode() if dark_override is None else bool(dark_override)
        if self._apple_style:
            self._paint_apple(dark)
            return
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
        paint_liquid_glass(painter, switch, radius=switch_h / 2.0, base_color=bg,
                          light=not dark, hovered=enabled and self.underMouse(),
                          opacity=1.0 if enabled else .6)

        knob = switch_h - 4.0
        knob_x = switch.right() - knob - 2.0 if self._checked else switch.left() + 2.0
        knob_rect = QRectF(knob_x, switch.top() + 2.0, knob, knob)
        paint_liquid_glass(painter, knob_rect, radius=knob / 2, base_color=QColor("white"), light=True,
                          border_color=QColor(0, 0, 0, 18),
                          opacity=1.0 if enabled else .6)

        painter.setFont(QFont("Microsoft YaHei UI", 6, QFont.Weight.Bold))
        if enabled:
            if self._checked:
                painter.setPen(QColor("#0b2819"))
            elif dark:
                painter.setPen(QColor("#c4c4ca"))
            else:
                painter.setPen(QColor("#38383e"))
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

    def _paint_apple(self, dark: bool):
        enabled = self.isEnabled()
        height = float(self.APPLE_CONTROL_HEIGHT)
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if self._text:
                font = QFont(self.font())
                font.setPixelSize(13)
                painter.setFont(font)
                ink = QColor(245, 245, 247) if dark else QColor(29, 29, 31)
                if not enabled:
                    ink.setAlphaF(0.35)
                painter.setPen(ink)
                painter.drawText(QRectF(0, 0, max(0.0, self.width() - self.APPLE_TRACK_WIDTH - 10), height),
                                 Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)
            painter.setOpacity(1.0 if enabled else 0.4)
            track = QRectF(self.width() - self.APPLE_TRACK_WIDTH, (height - self.APPLE_TRACK_HEIGHT) / 2,
                           self.APPLE_TRACK_WIDTH, self.APPLE_TRACK_HEIGHT)
            radius = track.height() / 2
            # Track: the gray fill crossfades to green as the knob travels.
            off = QColor(120, 120, 128, 82) if dark else QColor(120, 120, 128, 40)
            amount = max(0.0, min(1.0, self._position))
            fill = QColor(
                round(off.red() + (self.APPLE_ON.red() - off.red()) * amount),
                round(off.green() + (self.APPLE_ON.green() - off.green()) * amount),
                round(off.blue() + (self.APPLE_ON.blue() - off.blue()) * amount),
                round(off.alpha() + (255 - off.alpha()) * amount),
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(track, radius, radius)
            # Knob: stretches while pressed and turns into a liquid-glass
            # lens, then slides to the other side with a slight settle.
            inset = 2.0
            knob_h = track.height() - 2 * inset
            knob_w = knob_h + 7.0 * self._press
            travel = track.width() - 2 * inset - knob_w
            left = track.left() + inset + travel * self._position
            knob = QRectF(left, track.top() + inset, knob_w, knob_h)
            for spread, alpha in ((2.2, 14), (1.2, 22), (0.5, 30)):
                painter.setBrush(QColor(0, 0, 0, alpha))
                shadow = knob.adjusted(-spread * 0.4, -spread * 0.2 + 1.0, spread * 0.4, spread + 1.0)
                painter.drawRoundedRect(shadow, shadow.height() / 2, shadow.height() / 2)
            face = QColor(255, 255, 255)
            face.setAlphaF(1.0 - 0.55 * self._press)
            painter.setBrush(face)
            painter.drawRoundedRect(knob, knob_h / 2, knob_h / 2)
            if self._press > 0.01:
                paint_liquid_glass(painter, knob, radius=knob_h / 2, light=not dark,
                                   opacity=self._press, border_color=QColor(255, 255, 255, 120))
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(0, 0, 0, 16), 0.5))
                painter.drawRoundedRect(knob.adjusted(0.25, 0.25, -0.25, -0.25), knob_h / 2, knob_h / 2)
            if self._shows_unavailable_reason():
                painter.setOpacity(1.0)
                note_rect = QRectF(0, height + self.NOTE_TOP_MARGIN, float(self.width()), float(self.NOTE_HEIGHT))
                font = QFont(self.font())
                font.setPixelSize(12)
                painter.setFont(font)
                painter.setPen(QColor(255, 105, 97) if dark else QColor(215, 0, 21))
                painter.drawText(note_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                                 QFontMetrics(font).elidedText(self._unavailable_reason, Qt.TextElideMode.ElideRight,
                                                               max(0, int(note_rect.width()))))
