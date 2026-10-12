"""Song-page DJ voice slider; catalog I/O stays in the existing worker boundary."""
from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from api.online_music import PersonalDjVoice
from local.liquid_glass import paint_liquid_glass
from local.music_tasks import MusicTasks


class PersonalDjVoicePopover(QWidget):
    """Four verified catalog entries form a categorical, keyboard-safe slider."""
    def __init__(self, provider, surface, *, tasks=None):
        super().__init__(surface)
        self.provider, self.surface = provider, surface
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("私人DJ音色选择")
        self._voices = ()
        self._request = 0
        self._pending = False
        self._selected = -1
        self._pressed = -1
        self._hovered = -1
        self._indicator_from = self._indicator_to = 0.
        self._indicator_started = time.monotonic()
        self._status = "正在获取音色…"
        self.tasks = tasks or MusicTasks(self)
        self.tasks.completed.connect(self._completed)
        worker = self.tasks
        self.destroyed.connect(lambda: worker.close())
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def present(self, visible):
        if not visible:
            self.hide()
            return self
        self.reposition()
        if not self.isVisible():
            self.show()
            self.raise_()
            self.reload()
        return self

    def reposition(self):
        _, caption = self.surface._personal_dj_geometry(QRectF(self.surface.rect()), time.monotonic())
        shift = self.surface._personal_dj_control_shift(QRectF(self.surface.rect()),
                                                       self.surface._personal_dj_reveal_at(time.monotonic()))
        self.setGeometry(caption.translated(shift).toAlignedRect())

    def reload(self):
        self._request += 1
        self._pending = True
        self._status = "正在获取音色…"
        self._voices = ()
        self._selected = -1
        getter = getattr(self.provider, "list_personal_dj_voices", None)
        if not callable(getter) or not self.tasks.submit(self._request, getter):
            self._completed(self._request, None, "unavailable")
        self.update()

    def _selected_index(self):
        getter = getattr(self.provider, "get_owned_personal_dj_voice_key", None)
        key = getter() if callable(getter) else ""
        return next((i for i, voice in enumerate(self._voices)
                     if voice.timbre_key == key or not key and voice.is_default), -1)

    def _completed(self, token, result, error):
        if token != self._request or not self._pending:
            return
        self._pending = False
        if error or not isinstance(result, tuple) or not result or not all(isinstance(v, PersonalDjVoice) for v in result):
            self._status = "请先登录网易云" if error == "auth_required" else "音色获取失败 · 点击重试"
            self.update()
            return
        self._voices = result
        self._selected = self._selected_index()
        self._indicator_from = self._indicator_to = float(max(0, self._selected))
        self._status = ""
        self.update()

    def _index_at(self, point):
        bounds = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        if not self._voices or not bounds.contains(point):
            return -1
        return min(len(self._voices) - 1, int((point.x() - bounds.left()) / bounds.width() * len(self._voices)))

    def choose(self, index):
        if self._pending or not 0 <= index < len(self._voices):
            return False
        setter = getattr(self.provider, "set_owned_personal_dj_voice", None)
        try:
            success = bool(setter(self._voices[index].timbre_key)) if callable(setter) else False
        except Exception:
            success = False
        if success:
            preview = getattr(self.provider, "preview_owned_personal_dj_voice", None)
            if callable(preview):
                try:
                    preview(self._voices[index].timbre_key)
                except Exception:
                    pass
        self._indicator_from = self._indicator_at(time.monotonic())
        self._selected = self._selected_index()
        self._indicator_to = float(max(0, self._selected))
        self._indicator_started = time.monotonic()
        self._timer.start()
        self.setToolTip(self._voices[index].description if success else "无法切换，请刷新音色")
        self.update()
        return success

    def _indicator_at(self, now):
        phase = max(0., min(1., (now - self._indicator_started) / .24))
        return self._indicator_from + (self._indicator_to - self._indicator_from) * (1 - (1 - phase) ** 3)

    def _tick(self):
        if not self.isVisible() or time.monotonic() - self._indicator_started >= .24:
            self._timer.stop()
        self.update()

    def paintEvent(self, event):
        bounds = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            paint_liquid_glass(painter, bounds, base_color=QColor(42, 43, 46, 228))
            font = QFont(self.font())
            font.setPixelSize(max(9, min(14, round(self.height() * .30))))
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            if self._voices:
                track = bounds.adjusted(4, 4, -4, -4)
                width = track.width() / len(self._voices)
                if self._selected >= 0:
                    indicator = QRectF(track.left() + self._indicator_at(time.monotonic()) * width,
                                       track.top(), width, track.height())
                    accent = self.surface._future_lyric_color(bounds)
                    accent = QColor(accent)
                    accent.setAlpha(65)
                    paint_liquid_glass(painter, indicator, base_color=accent, hovered=True)
                for i, voice in enumerate(self._voices):
                    cell = QRectF(track.left() + i * width, track.top(), width, track.height())
                    painter.setPen(QColor(255, 255, 255, 250 if i == self._selected else 195))
                    painter.drawText(cell, Qt.AlignmentFlag.AlignCenter, voice.name)
            else:
                painter.setPen(QColor(235, 236, 238))
                painter.drawText(bounds.adjusted(7, 0, -7, 0), Qt.AlignmentFlag.AlignCenter, self._status)
            if self.hasFocus():
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(180, 210, 255), 1.2))
                painter.drawRoundedRect(bounds.adjusted(2, 2, -2, -2), self.height() / 2 - 2, self.height() / 2 - 2)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = self._index_at(event.position())
            event.accept()

    def mouseMoveEvent(self, event):
        self._hovered = self._index_at(event.position())
        if self._hovered >= 0:
            self.setToolTip(self._voices[self._hovered].description)
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        index = self._index_at(event.position())
        if self._pressed >= 0 and index >= 0:
            self.choose(index)
        elif not self._voices and not self._pending:
            self.reload()
        self._pressed = -1
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.clearFocus()
            self.surface._close_dj_voice_selector()
            self.surface.setFocus(Qt.FocusReason.OtherFocusReason)
            event.accept()
            return
        direction = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1}.get(event.key())
        if direction is not None and self._voices:
            self.choose((max(0, self._selected) + direction) % len(self._voices))
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event):
        self.surface._set_dj_voice_selector_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.surface._set_dj_voice_selector_hover(False)
        super().leaveEvent(event)

    def hideEvent(self, event):
        self._request += 1
        self._pending = False
        self._pressed = -1
        self._timer.stop()
        self.clearFocus()
        super().hideEvent(event)
