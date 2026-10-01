"""Music-surface output selector; all device work stays behind the provider."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from local.music_lyric_motion import MotionSimulationClock
from local.no_wheel_widgets import NoWheelSlider
from api.audio_routes import AudioRouteDevice, AudioRouteSnapshot


def paint_audio_output_icon(painter: QPainter, rect: QRectF, kind: str, color: QColor):
    """Small code-native device symbols, shared by the panel and surface button."""
    painter.save()
    painter.translate(rect.topLeft())
    painter.scale(rect.width() / 24.0, rect.height() / 24.0)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(color, 1.65, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin))
    kind = str(kind).lower()
    if kind in {"homepod", "airplay"}:
        painter.drawRoundedRect(QRectF(5.0, 3.5, 14.0, 17.0), 5.0, 5.0)
        painter.drawEllipse(QRectF(8.0, 5.3, 8.0, 2.5))
        painter.drawLine(QPointF(8.0, 17.0), QPointF(16.0, 17.0))
    elif "head" in kind or "ear" in kind:
        painter.drawArc(QRectF(4.0, 3.5, 16.0, 17.0), 0, 180 * 16)
        painter.drawRoundedRect(QRectF(3.7, 12.0, 4.0, 8.0), 1.8, 1.8)
        painter.drawRoundedRect(QRectF(16.3, 12.0, 4.0, 8.0), 1.8, 1.8)
    elif kind == "output":
        painter.drawRoundedRect(QRectF(3.0, 3.5, 18.0, 13.0), 2.0, 2.0)
        triangle = QPainterPath()
        triangle.moveTo(7.5, 21.0)
        triangle.lineTo(12.0, 16.5)
        triangle.lineTo(16.5, 21.0)
        triangle.closeSubpath()
        painter.setBrush(color)
        painter.drawPath(triangle)
    else:
        painter.drawRoundedRect(QRectF(6.0, 2.5, 12.0, 19.0), 2.5, 2.5)
        painter.drawEllipse(QRectF(10.5, 6.0, 3.0, 3.0))
        painter.drawEllipse(QRectF(8.5, 12.0, 7.0, 7.0))
    painter.restore()


class _OutputRow(QAbstractButton):
    """An accessible button with a timer-driven arrival and connection pose."""

    def __init__(self, device: AudioRouteDevice, parent=None):
        super().__init__(parent)
        self.device = device
        self.state = "available"
        self.progress = 0.0
        self.elapsed = 0.0
        self.setFixedHeight(68)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.set_device(device)

    def set_device(self, device):
        self.device = device
        self.setEnabled(bool(device.available))
        self.setAccessibleName(str(device.name))
        self.setToolTip(str(device.name))

    @property
    def subtitle(self):
        if self.state == "pending":
            return "正在连接…"
        if self.state == "active":
            return "正在投放" if self.device.transport == "airplay" else "当前输出"
        if not self.device.available:
            return "设备不可用"
        if self.device.transport == "airplay":
            return "HomePod · AirPlay"
        if self.device.is_bluetooth:
            return "蓝牙音频"
        return "Windows 默认输出" if self.device.is_default else "本地音频"

    def advance(self, elapsed: float):
        self.elapsed += elapsed
        # Retarget smoothly from the current pose; paint never advances it.
        target = 1.0 if self.state == "active" else 0.0
        self.progress += (target - self.progress) * (1.0 - math.exp(-elapsed * 15.0))
        self.update()

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            bounds = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
            accent = QColor(132, 218, 205)
            painter.setPen(QPen(QColor(132, 218, 205, round(45 + self.progress * 65))
                                if self.state in {"active", "pending"} else QColor(255, 255, 255, 20), 1.0))
            painter.setBrush(QColor(255, 255, 255, 20 if self.underMouse() or self.hasFocus() else 8))
            painter.drawRoundedRect(bounds, 15.0, 15.0)
            arrival = 1.0 - math.exp(-self.elapsed * 12.0)
            painter.save()
            icon = QRectF(17.0, 21.0, 26.0, 26.0)
            painter.translate(icon.center())
            painter.scale(0.82 + 0.18 * arrival, 0.82 + 0.18 * arrival)
            painter.translate(-icon.center())
            paint_audio_output_icon(painter, icon, self.device.kind,
                                    accent if self.state in {"active", "pending"} else QColor(224, 232, 240))
            painter.restore()
            painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold))
            painter.setPen(QColor(238, 243, 247) if self.isEnabled() else QColor(143, 150, 158))
            text_rect = QRectF(58.0, 12.0, max(1.0, self.width() - 105.0), 24.0)
            name = QFontMetricsF(painter.font()).elidedText(str(self.device.name), Qt.TextElideMode.ElideRight, text_rect.width())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, name)
            painter.setFont(QFont("Microsoft YaHei UI", 8))
            painter.setPen(QColor(158, 176, 189))
            painter.drawText(QRectF(58.0, 36.0, text_rect.width(), 19.0), Qt.AlignmentFlag.AlignVCenter, self.subtitle)
            marker = QRectF(self.width() - 32.0, 26.0, 15.0, 15.0)
            painter.setPen(QPen(accent, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            if self.state == "pending":
                painter.drawArc(marker, int(-self.elapsed * 280.0 * 16), 250 * 16)
            elif self.progress > 0.01:
                painter.setOpacity(self.progress)
                painter.drawLine(marker.topLeft() + QPointF(1.0, 8.0), marker.topLeft() + QPointF(6.0, 13.0))
                painter.drawLine(marker.topLeft() + QPointF(6.0, 13.0), marker.topLeft() + QPointF(15.0, 2.0))


class AudioOutputPanel(QWidget):
    """Child popup that reads immutable route snapshots and queues user actions."""

    dismissed = Signal()
    APPEAR_SECONDS = 0.32

    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self.provider = provider
        self._snapshot: AudioRouteSnapshot | None = None
        self._rows: dict[str, _OutputRow] = {}
        self._device_order = ()
        self._anchor = QPoint()
        self._appearance_elapsed = 0.0
        self._clock = MotionSimulationClock()
        self._action_error = ""
        self.setObjectName("GluSpotAudioOutputPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("音频输出设备")
        self.setStyleSheet("""
            QWidget#GluSpotAudioOutputPanel { background: rgba(17, 24, 31, 247); border: 1px solid rgba(255,255,255,38); border-radius: 21px; }
            QLabel { color: #eff4f7; background: transparent; border: none; }
            QPushButton { color: #cddde4; background: rgba(255,255,255,13); border: 1px solid rgba(255,255,255,22); border-radius: 10px; padding: 7px 12px; }
            QPushButton:hover { background: rgba(255,255,255,27); }
            QPushButton:disabled { color: #65747e; }
            QScrollArea { background: transparent; border: none; }
            QScrollArea > QWidget > QWidget { background: transparent; }
            QScrollBar:vertical { background: transparent; width: 5px; }
            QScrollBar::handle:vertical { background: #46535d; border-radius: 2px; min-height: 24px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("音频输出")
        title.setFont(QFont("Microsoft YaHei UI", 12, QFont.Weight.DemiBold))
        header.addWidget(title)
        header.addStretch()
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self._refresh)
        header.addWidget(self.refresh_button)
        self.close_button = QPushButton("×")
        self.close_button.setAccessibleName("关闭设备面板")
        self.close_button.setFixedWidth(36)
        self.close_button.clicked.connect(self.dismiss)
        header.addWidget(self.close_button)
        layout.addLayout(header)
        self.status_label = QLabel("正在读取设备…")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #a7bac8; font-size: 12px;")
        layout.addWidget(self.status_label)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rows_widget = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_widget)
        self.rows_layout.setContentsMargins(0, 0, 4, 0)
        self.rows_layout.setSpacing(7)
        self.scroll.setWidget(self.rows_widget)
        layout.addWidget(self.scroll, 1)
        self.empty_label = QLabel("暂无可用输出设备")
        self.empty_label.setStyleSheet("color: #a7bac8; padding: 14px;")
        self.rows_layout.insertWidget(0, self.empty_label)
        self.stop_button = QPushButton("停止 HomePod 投放")
        self.stop_button.clicked.connect(self._stop)
        self.rows_layout.addWidget(self.stop_button)
        self.latency_widget = QWidget()
        delay_layout = QVBoxLayout(self.latency_widget)
        delay_layout.setContentsMargins(0, 0, 0, 0)
        delay_layout.setSpacing(5)
        self.latency_label = QLabel("歌词延后 1.50 秒")
        self.latency_label.setStyleSheet("color: #a7bac8; font-size: 12px;")
        delay_layout.addWidget(self.latency_label)
        self.latency_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.latency_slider.setRange(0, 500)
        self.latency_slider.setSingleStep(5)
        self.latency_slider.setAccessibleName("HomePod 投放的歌词延后时间")
        self.latency_slider.setStyleSheet("QSlider::groove:horizontal { background: #35434d; height: 4px; border-radius: 2px; } QSlider::sub-page:horizontal { background: #84dacd; border-radius: 2px; } QSlider::handle:horizontal { background: #d1f4ed; width: 13px; margin: -5px 0; border-radius: 6px; }")
        self.latency_slider.valueChanged.connect(self._latency_changed)
        delay_layout.addWidget(self.latency_slider)
        self.rows_layout.addWidget(self.latency_widget)
        manual = QHBoxLayout()
        self.host_input = QLineEdit()
        self.host_input.setPlaceholderText("HomePod IP 地址")
        self.host_input.setAccessibleName("手动添加 HomePod 的 IP 地址")
        self.host_input.setStyleSheet("QLineEdit { color: #eff4f7; background: rgba(255,255,255,12); border: 1px solid rgba(255,255,255,24); border-radius: 9px; padding: 8px; }")
        self.host_input.returnPressed.connect(self._add_homepod)
        manual.addWidget(self.host_input, 1)
        self.add_button = QPushButton("添加")
        self.add_button.clicked.connect(self._add_homepod)
        self.add_button.setEnabled(callable(getattr(provider, "add_homepod", None)))
        self.host_input.setEnabled(self.add_button.isEnabled())
        manual.addWidget(self.add_button)
        self.rows_layout.addLayout(manual)
        hint = QLabel("HomePod 投放电脑当前声音，本地仍可能出声。蓝牙设备需先在 Windows 配对；HomePod 需在同一局域网。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8296a5; font-size: 11px;")
        self.rows_layout.addWidget(hint)
        self.rows_layout.addStretch()
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._animation_timer = QTimer(self)
        self._animation_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._animation_timer.setInterval(16)
        self._animation_timer.timeout.connect(self._advance_animation)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(350)
        self._poll_timer.timeout.connect(self.sync_snapshot)
        self.hide()

    def open_at(self, anchor: QPoint):
        self._anchor = QPoint(anchor)
        parent = self.parentWidget()
        width = min(370, max(260, parent.width() - 24)) if parent else 370
        height = min(510, max(280, parent.height() - anchor.y() - 16)) if parent else 510
        self.resize(width, height)
        self._anchor.setX(max(12, min(anchor.x() - width, parent.width() - width - 12)) if parent else anchor.x())
        self._anchor.setY(max(12, min(anchor.y(), parent.height() - height - 12)) if parent else anchor.y())
        self._appearance_elapsed = 0.0
        self._clock.reset(time.monotonic())
        self._opacity.setOpacity(0.0)
        self.move(self._anchor + QPoint(0, -10))
        self.sync_snapshot()
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.PopupFocusReason)
        self._animation_timer.start()
        self._poll_timer.start()
        self._refresh()

    def dismiss(self):
        was_visible = self.isVisible()
        self.hide()
        if was_visible:
            self.dismissed.emit()

    def hideEvent(self, event):
        self._animation_timer.stop()
        self._poll_timer.stop()
        super().hideEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _refresh(self):
        self._action_error = ""
        try:
            self.provider.refresh_audio_outputs()
        except Exception:
            self._action_error = "暂时无法刷新设备，请稍后重试。"
        self.sync_snapshot()

    def _select(self, device_id: str):
        self._action_error = ""
        try:
            if not self.provider.select_audio_output(device_id):
                self._action_error = "无法切换到此设备，请刷新后重试。"
        except Exception:
            self._action_error = "无法切换到此设备，请刷新后重试。"
        self.sync_snapshot()

    def _stop(self):
        self._action_error = ""
        try:
            self.provider.stop_audio_cast()
        except Exception:
            self._action_error = "暂时无法停止投放，请稍后重试。"
        self.sync_snapshot()

    def _add_homepod(self):
        self._action_error = ""
        try:
            if self.provider.add_homepod(self.host_input.text().strip()):
                self.host_input.clear()
            else:
                self._action_error = "请填写有效的 HomePod IP 地址。"
        except Exception:
            self._action_error = "暂时无法添加 HomePod，请稍后重试。"
        self.sync_snapshot()

    def _latency_changed(self, value: int):
        seconds = value / 100.0
        self.latency_label.setText(f"歌词延后 {seconds:.2f} 秒")
        try:
            if self.provider.set_audio_output_latency(seconds) is False:
                self._action_error = "暂时无法调整歌词延后时间。"
                self.sync_snapshot()
        except Exception:
            self._action_error = "暂时无法调整歌词延后时间。"
            self.status_label.setText(self._action_error)

    def sync_snapshot(self):
        """Read only the provider's published state; never discover in this UI."""
        try:
            snapshot = self.provider.get_audio_route_snapshot()
        except Exception:
            self.status_label.setText("音频设备暂时不可用。")
            self.stop_button.setEnabled(False)
            return
        self._snapshot = snapshot
        order = tuple(device.id for device in snapshot.devices)
        if order != self._device_order:
            for row in self._rows.values():
                self.rows_layout.removeWidget(row)
            for device_id in tuple(self._rows):
                if device_id not in order:
                    self._rows.pop(device_id).deleteLater()
            for index, device in enumerate(snapshot.devices):
                row = self._rows.get(device.id)
                if row is None:
                    row = _OutputRow(device, self.rows_widget)
                    row.clicked.connect(lambda _checked=False, device_id=device.id: self._select(device_id))
                    self._rows[device.id] = row
                self.rows_layout.insertWidget(index, row)
            self._device_order = order
        for device in snapshot.devices:
            row = self._rows[device.id]
            row.set_device(device)
            row.state = ("pending" if device.id == snapshot.pending_id else
                         "active" if device.id == snapshot.active_id else "available")
            row.setAccessibleDescription(row.subtitle)
            row.update()
        self.empty_label.setVisible(not order)
        self.status_label.setText(self._action_error or snapshot.status_message or "选择要播放的设备")
        self.status_label.setStyleSheet("color: #f0b5ae; font-size: 12px;" if self._action_error or snapshot.state in {"error", "failed"} else "color: #a7bac8; font-size: 12px;")
        active = next((d for d in snapshot.devices if d.id == snapshot.active_id), None)
        pending = next((d for d in snapshot.devices if d.id == snapshot.pending_id), None)
        self.stop_button.setEnabled(any(d is not None and d.transport == "airplay" for d in (active, pending)))
        casting = any(d is not None and d.transport == "airplay" for d in (active, pending))
        self.latency_widget.setVisible(casting)
        if not self.latency_slider.isSliderDown():
            self.latency_slider.blockSignals(True)
            self.latency_slider.setValue(round(max(0.0, min(5.0, snapshot.cast_latency_seconds)) * 100))
            self.latency_slider.blockSignals(False)
            self.latency_label.setText(f"歌词延后 {self.latency_slider.value() / 100.0:.2f} 秒")
        if self.isVisible() and (snapshot.pending_id or any(abs(row.progress - (1.0 if row.state == "active" else 0.0)) > 0.01 or row.elapsed < 0.5 for row in self._rows.values())):
            if not self._animation_timer.isActive():
                self._clock.reset(time.monotonic())
                self._animation_timer.start()

    def _advance_animation(self, now: float | None = None):
        elapsed = self._clock.advance(time.monotonic() if now is None else now)
        self._appearance_elapsed += elapsed
        phase = min(1.0, self._appearance_elapsed / self.APPEAR_SECONDS)
        settle = 1.0 - (1.0 + 3.2 * phase) * math.exp(-3.2 * phase)
        settle /= 1.0 - 4.2 * math.exp(-3.2)
        self._opacity.setOpacity(max(0.0, min(1.0, settle)))
        self.move(self._anchor + QPoint(0, round(-10.0 * (1.0 - settle))))
        for row in self._rows.values():
            row.advance(elapsed)
        pending = bool(self._snapshot and self._snapshot.pending_id)
        rows_animating = any(row.elapsed < 0.5 or abs(row.progress - (1.0 if row.state == "active" else 0.0)) > 0.01 for row in self._rows.values())
        if phase >= 1.0 and not pending and not rows_animating:
            self._animation_timer.stop()
