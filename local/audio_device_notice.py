"""A short, nonactivating device-arrival notice driven by real route events."""
from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from local.audio_output_panel import paint_audio_output_icon
from local.music_lyric_motion import MotionSimulationClock


class AudioDeviceNotice(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedSize(330, 82)
        self._name = self._message = ""
        self._kind = "device"
        self._elapsed = 0.0
        self._clock = MotionSimulationClock()
        self._base_y = 0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._advance)

    def present(self, name: str, message: str, kind: str = "device", screen=None):
        # QWidget.screen() reparents the shared QScreen Python wrapper to this
        # transient widget in PySide6 6.11. Query the application so disposing
        # the notice cannot invalidate other users of the same screen wrapper.
        screen = screen or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self._name, self._message, self._kind = str(name), str(message), str(kind)
        self._elapsed = 0.0
        self._clock.reset(time.monotonic())
        self._base_y = available.top() + 24
        self.move(available.center().x() - self.width() // 2, self._base_y - 10)
        self.setWindowOpacity(0.0)
        self.show()
        self._timer.start()
        self.update()

    def _advance(self, now=None):
        self._elapsed += self._clock.advance(time.monotonic() if now is None else now)
        entrance = min(1.0, self._elapsed / 0.3)
        entrance = 1.0 - (1.0 - entrance) ** 3
        exit_phase = min(1.0, max(0.0, (self._elapsed - 3.0) / 0.3))
        self.setWindowOpacity(entrance * (1.0 - exit_phase))
        self.move(self.x(), round(self._base_y - 10 * (1.0 - entrance) - 6 * exit_phase))
        if self._elapsed >= 3.3:
            self.hide()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor(18, 24, 29, 247))
            painter.setPen(QPen(QColor(255, 255, 255, 35), 1.0))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 26, 26)
            paint_audio_output_icon(painter, QRectF(24, 24, 34, 34), self._kind, QColor(145, 224, 206))
            painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold))
            painter.setPen(QColor(241, 246, 249))
            name = QFontMetricsF(painter.font()).elidedText(self._name, Qt.TextElideMode.ElideRight, 237)
            painter.drawText(QRectF(74, 18, 237, 25), Qt.AlignmentFlag.AlignVCenter, name)
            painter.setFont(QFont("Microsoft YaHei UI", 9))
            painter.setPen(QColor(163, 186, 194))
            painter.drawText(QRectF(74, 44, 237, 20), Qt.AlignmentFlag.AlignVCenter, self._message)
