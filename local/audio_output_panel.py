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
from local.music_surface_palette import relative_luminance
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
        self.ink = QColor(245, 245, 245)
        self.muted = QColor(178, 178, 178)
        self.accent = QColor(245, 245, 245)
        self.hover_fill = QColor(255, 255, 255, 12)
        self.selected_fill = QColor(255, 255, 255, 20)
        self.label_font = QFont("Segoe UI", 10)
        self.setFixedHeight(54)
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
            accent = self.accent
            fill = QColor(self.selected_fill)
            fill.setAlpha(round(fill.alpha() * self.progress))
            if self.underMouse() or self.hasFocus():
                fill = QColor(self.hover_fill)
            painter.setPen(QPen(accent, 1.0) if self.hasFocus() else Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(bounds, 11.0, 11.0)
            arrival = 1.0 - math.exp(-self.elapsed * 12.0)
            painter.save()
            icon = QRectF(12.0, 15.0, 24.0, 24.0)
            painter.translate(icon.center())
            painter.scale(0.82 + 0.18 * arrival, 0.82 + 0.18 * arrival)
            painter.translate(-icon.center())
            paint_audio_output_icon(painter, icon, self.device.kind,
                                    accent if self.state in {"active", "pending"} else
                                    self.ink if self.isEnabled() else self.muted)
            painter.restore()
            font = QFont(self.label_font)
            font.setPixelSize(13)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(self.ink if self.isEnabled() else self.muted)
            text_rect = QRectF(48.0, 8.0, max(1.0, self.width() - 81.0), 21.0)
            name = QFontMetricsF(painter.font()).elidedText(str(self.device.name), Qt.TextElideMode.ElideRight, text_rect.width())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter, name)
            font.setPixelSize(11)
            font.setWeight(QFont.Weight.Normal)
            painter.setFont(font)
            painter.setPen(self.muted)
            painter.drawText(QRectF(48.0, 29.0, text_rect.width(), 17.0), Qt.AlignmentFlag.AlignVCenter, self.subtitle)
            marker = QRectF(self.width() - 25.0, 20.0, 13.0, 13.0)
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
        self._style_key = None
        self._advanced_open = False
        self._max_height = 460
        self._ink = QColor(245, 245, 245)
        self._muted = QColor(180, 180, 180)
        self._accent = QColor(245, 245, 245)
        self._background = QColor(27, 27, 29)
        self._hover_fill = QColor(255, 255, 255, 16)
        self._selected_fill = QColor(255, 255, 255, 22)
        self._label_font = QFont()
        self._label_font.setFamilies(["Segoe UI", "Microsoft YaHei UI"])
        self._label_font.setPixelSize(13)
        self.setObjectName("GluSpotAudioOutputPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("音频输出设备")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(7)
        header = QHBoxLayout()
        self.title_label = QLabel("播放到")
        header.addWidget(self.title_label)
        header.addStretch()
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setFixedHeight(28)
        self.refresh_button.clicked.connect(self._refresh)
        header.addWidget(self.refresh_button)
        self.close_button = QPushButton("×")
        self.close_button.setAccessibleName("关闭设备面板")
        self.close_button.setFixedSize(28, 28)
        self.close_button.clicked.connect(self.dismiss)
        header.addWidget(self.close_button)
        layout.addLayout(header)
        self.status_label = QLabel("正在读取设备…")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rows_widget = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_widget)
        self.rows_layout.setContentsMargins(0, 0, 2, 0)
        self.rows_layout.setSpacing(3)
        self.scroll.setWidget(self.rows_widget)
        layout.addWidget(self.scroll, 1)
        self.empty_label = QLabel("暂无可用输出设备")
        self.empty_label.setMargin(12)
        self.rows_layout.insertWidget(0, self.empty_label)
        self.stop_button = QPushButton("停止 HomePod 投放")
        self.stop_button.setFixedHeight(30)
        self.stop_button.clicked.connect(self._stop)
        self.rows_layout.addWidget(self.stop_button)
        self.advanced_button = QPushButton("更多选项  ›")
        self.advanced_button.setCheckable(True)
        self.advanced_button.setFixedHeight(30)
        self.advanced_button.setAccessibleName("展开更多音频选项")
        self.advanced_button.toggled.connect(self._toggle_advanced)
        self.rows_layout.addWidget(self.advanced_button)
        self.advanced_widget = QWidget()
        self.advanced_widget.setObjectName("AudioOutputAdvanced")
        advanced_layout = QVBoxLayout(self.advanced_widget)
        advanced_layout.setContentsMargins(8, 9, 8, 7)
        advanced_layout.setSpacing(9)
        self.latency_widget = QWidget()
        delay_layout = QVBoxLayout(self.latency_widget)
        delay_layout.setContentsMargins(0, 0, 0, 0)
        delay_layout.setSpacing(5)
        self.latency_label = QLabel("歌词延后 1.50 秒")
        delay_layout.addWidget(self.latency_label)
        self.auto_latency_button = QPushButton("自动补偿")
        self.auto_latency_button.setCheckable(True)
        self.auto_latency_button.setFixedHeight(28)
        self.auto_latency_button.setToolTip("持续估计投放缓冲，不打断歌曲；实际出声仍可手动校准。")
        self.auto_latency_button.toggled.connect(self._auto_latency_changed)
        delay_layout.addWidget(self.auto_latency_button)
        self.latency_estimate_label = QLabel()
        delay_layout.addWidget(self.latency_estimate_label)
        self.latency_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.latency_slider.setRange(0, 500)
        self.latency_slider.setSingleStep(5)
        self.latency_slider.setAccessibleName("HomePod 投放的歌词延后时间")
        self.latency_slider.valueChanged.connect(self._latency_changed)
        delay_layout.addWidget(self.latency_slider)
        advanced_layout.addWidget(self.latency_widget)
        manual = QHBoxLayout()
        self.host_input = QLineEdit()
        self.host_input.setPlaceholderText("HomePod IP 地址")
        self.host_input.setAccessibleName("手动添加 HomePod 的 IP 地址")
        self.host_input.setFixedHeight(31)
        self.host_input.returnPressed.connect(self._add_homepod)
        manual.addWidget(self.host_input, 1)
        self.add_button = QPushButton("添加")
        self.add_button.setFixedHeight(31)
        self.add_button.clicked.connect(self._add_homepod)
        self.add_button.setEnabled(callable(getattr(provider, "add_homepod", None)))
        self.host_input.setEnabled(self.add_button.isEnabled())
        manual.addWidget(self.add_button)
        advanced_layout.addLayout(manual)
        self.hint_label = QLabel("蓝牙需在 Windows 配对；HomePod 需在同一局域网。投放时静音当前电脑输出，停止后恢复。")
        self.hint_label.setWordWrap(True)
        advanced_layout.addWidget(self.hint_label)
        self.rows_layout.addWidget(self.advanced_widget)
        self.rows_layout.addStretch(1)
        self.advanced_widget.hide()
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._animation_timer = QTimer(self)
        self._animation_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._animation_timer.setInterval(16)
        self._animation_timer.timeout.connect(self._advance_animation)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(350)
        self._poll_timer.timeout.connect(self.sync_snapshot)
        self._sync_surface_style()
        self.hide()

    def _sync_surface_style(self):
        """Use the music surface's cached backdrop and existing font family."""
        parent = self.parentWidget()
        sample = getattr(parent, "_background_sample_colors", None)
        colors = sample(QRectF(self.geometry())) if callable(sample) else (QColor(30, 30, 32),)
        backdrop = QColor(*(round(sum(color.getRgb()[index] for color in colors) / len(colors))
                            for index in range(3)))
        light = relative_luminance(backdrop) > 0.45
        target = QColor(255, 255, 255) if light else QColor(0, 0, 0)
        amount = 0.42 if light else 0.12
        background = QColor(*(round(a * (1.0 - amount) + b * amount)
                              for a, b in zip(backdrop.getRgb()[:3], target.getRgb()[:3])))
        font_factory = getattr(parent, "_surface_font", None)
        font = font_factory(13, QFont.Weight.Normal) if callable(font_factory) else self._label_font
        key = (background.rgba(), light, font.toString())
        if key == self._style_key:
            return
        self._style_key = key
        self._background = background
        self._label_font = QFont(font)
        self._ink = QColor(28, 28, 30) if light else QColor(246, 246, 247)
        self._muted = QColor(89, 89, 93) if light else QColor(195, 195, 198)
        self._accent = QColor(self._ink)
        self._hover_fill = QColor(0, 0, 0, 10) if light else QColor(255, 255, 255, 17)
        self._selected_fill = QColor(0, 0, 0, 15) if light else QColor(255, 255, 255, 24)
        self.setFont(font)
        title_font = QFont(font)
        title_font.setPixelSize(15)
        title_font.setWeight(QFont.Weight.DemiBold)
        self.title_label.setFont(title_font)
        minor = QFont(font)
        minor.setPixelSize(11)
        self.hint_label.setFont(minor)
        self.status_label.setFont(minor)
        ink, muted = self._ink.name(), self._muted.name()
        fill = "0,0,0" if light else "255,255,255"
        bg = ",".join(str(value) for value in background.getRgb()[:3])
        self.setStyleSheet(f"""
            QWidget#GluSpotAudioOutputPanel {{ background: rgba({bg},248); border: 1px solid rgba({fill},38); border-radius: 18px; }}
            QLabel {{ color: {ink}; background: transparent; border: none; }}
            QPushButton {{ color: {muted}; background: transparent; border: 1px solid transparent; border-radius: 14px; padding: 3px 9px; }}
            QPushButton:hover, QPushButton:checked {{ color: {ink}; background: rgba({fill},16); }}
            QPushButton:focus {{ color: {ink}; border: 1px solid {muted}; }}
            QPushButton:disabled {{ color: {muted}; }}
            QWidget#AudioOutputAdvanced {{ background: rgba({fill},6); border-radius: 11px; }}
            QLineEdit {{ color: {ink}; background: rgba({fill},9); border: 1px solid rgba({fill},25); border-radius: 9px; padding: 4px 8px; }}
            QLineEdit:focus {{ border: 1px solid {muted}; }}
            QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: transparent; width: 4px; }}
            QScrollBar::handle:vertical {{ background: {muted}; border-radius: 2px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QSlider::groove:horizontal {{ background: rgba({fill},36); height: 3px; border-radius: 1px; }}
            QSlider::sub-page:horizontal {{ background: {muted}; border-radius: 1px; }}
            QSlider::handle:horizontal {{ background: {ink}; width: 11px; margin: -4px 0; border-radius: 5px; }}
        """)
        self.hint_label.setStyleSheet(f"color: {muted};")
        self.latency_label.setStyleSheet(f"color: {muted};")
        self.latency_estimate_label.setStyleSheet(f"color: {muted};")
        for row in self._rows.values():
            self._style_row(row)

    def _style_row(self, row):
        row.ink, row.muted, row.accent = self._ink, self._muted, self._accent
        row.hover_fill, row.selected_fill = self._hover_fill, self._selected_fill
        row.label_font = self._label_font

    def _fit_to_content(self):
        visible = [self.rows_layout.itemAt(index) for index in range(self.rows_layout.count())
                   if self.rows_layout.itemAt(index).widget() is not None
                   and not self.rows_layout.itemAt(index).widget().isHidden()]
        content_height = sum(item.sizeHint().height() for item in visible)
        content_height += max(0, len(visible) - 1) * self.rows_layout.spacing()
        status_height = self.status_label.sizeHint().height() + 7 if not self.status_label.isHidden() else 0
        self.resize(self.width(), min(self._max_height, 59 + status_height + content_height))
        parent = self.parentWidget()
        if parent is not None:
            self._anchor.setY(max(12, min(self._anchor.y(), parent.height() - self.height() - 12)))
        if self._appearance_elapsed >= self.APPEAR_SECONDS:
            self.move(self._anchor)

    def _toggle_advanced(self, checked):
        self._advanced_open = bool(checked)
        self.advanced_widget.setVisible(self._advanced_open)
        self.advanced_button.setText("收起选项  ⌄" if checked else "更多选项  ›")
        self.advanced_button.setAccessibleName("收起更多音频选项" if checked else "展开更多音频选项")
        self._fit_to_content()

    def _sync_tab_order(self):
        controls = [self.refresh_button, self.close_button, *self._rows.values(),
                    self.stop_button, self.advanced_button, self.auto_latency_button, self.latency_slider,
                    self.host_input, self.add_button]
        for previous, current in zip(controls, controls[1:]):
            QWidget.setTabOrder(previous, current)

    def open_at(self, anchor: QPoint):
        self._anchor = QPoint(anchor)
        parent = self.parentWidget()
        width = min(324, max(260, parent.width() - 24)) if parent else 324
        self._max_height = min(460, max(220, parent.height() - anchor.y() - 16)) if parent else 460
        self.resize(width, self._max_height)
        self._anchor.setX(max(12, min(anchor.x() - width, parent.width() - width - 12)) if parent else anchor.x())
        self._anchor.setY(max(12, anchor.y()))
        self.advanced_button.setChecked(False)
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
            self.status_label.show()
            self._fit_to_content()

    def _auto_latency_changed(self, enabled: bool):
        self._action_error = ""
        try:
            if self.provider.set_audio_output_auto_latency(enabled) is False:
                self._action_error = "暂时无法调整自动补偿。"
        except Exception:
            self._action_error = "暂时无法调整自动补偿。"
        self.sync_snapshot()

    def sync_snapshot(self):
        """Read only the provider's published state; never discover in this UI."""
        self._sync_surface_style()
        try:
            snapshot = self.provider.get_audio_route_snapshot()
        except Exception:
            self.status_label.setText("音频设备暂时不可用。")
            self.status_label.show()
            self.stop_button.setEnabled(False)
            self._fit_to_content()
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
                self._style_row(row)
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
        message = self._action_error or snapshot.status_message
        self.status_label.setText(message)
        self.status_label.setVisible(bool(message))
        error = bool(self._action_error or snapshot.state in {"error", "failed"})
        error_color = "#a22d2d" if relative_luminance(self._background) > 0.45 else "#ffb9b3"
        self.status_label.setStyleSheet(f"color: {error_color if error else self._muted.name()};")
        active = next((d for d in snapshot.devices if d.id == snapshot.active_id), None)
        pending = next((d for d in snapshot.devices if d.id == snapshot.pending_id), None)
        self.stop_button.setEnabled(any(d is not None and d.transport == "airplay" for d in (active, pending)))
        casting = any(d is not None and d.transport == "airplay" for d in (active, pending))
        self.stop_button.setVisible(casting)
        self.latency_widget.setVisible(casting)
        self.auto_latency_button.blockSignals(True)
        self.auto_latency_button.setChecked(snapshot.cast_latency_auto)
        self.auto_latency_button.blockSignals(False)
        self.latency_slider.setEnabled(not snapshot.cast_latency_auto)
        estimate = snapshot.cast_latency_estimate_seconds
        self.latency_estimate_label.setText(
            f"投放缓冲估计 {estimate:.2f} 秒" if estimate is not None else "等待投放缓冲数据"
        )
        if not self.latency_slider.isSliderDown():
            self.latency_slider.blockSignals(True)
            self.latency_slider.setValue(round(max(0.0, min(5.0, snapshot.cast_latency_seconds)) * 100))
            self.latency_slider.blockSignals(False)
            self.latency_label.setText(f"歌词延后 {self.latency_slider.value() / 100.0:.2f} 秒")
        self._fit_to_content()
        self._sync_tab_order()
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
