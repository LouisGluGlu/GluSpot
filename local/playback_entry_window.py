"""First-run playback choices and source login display; no network or settings I/O.

The application controller owns synchronous OnlineMusicService calls in its
workers. Only a service-supplied QR image is displayed. Signals are the entire
outbound interface; closing this window never quits the tray application.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QByteArray, QBuffer, QEasingCurve, QIODevice, QPoint, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QImage, QImageReader, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QLabel, QPushButton, QWidget

from api.online_music import AccountSnapshot
from local.branding import APP_NAME
from local.island_stack_visual import IslandStackVisual
from local.music_lyric_reading import LyricReadingRenderer
from local.music_surface_background import AlbumFlowBackgroundRenderer
from local.music_source_icons import paint_music_source_icon


@dataclass(frozen=True)
class EntrySource:
    source_id: str
    name: str
    color: str


ENTRY_SOURCES = (
    EntrySource("netease", "网易云音乐", "#df2a16"),
    EntrySource("kugou", "酷狗音乐", "#368bff"),
    EntrySource("qq", "QQ 音乐", "#efd700"),
    EntrySource("qishui", "汽水音乐", "#183d30"),
)


def _entry_font(pixels: int, *, heavy: bool = False) -> QFont:
    font = QFont(IslandStackVisual.best_available_font_family("Alibaba PuHuiTi"))
    font.setPixelSize(pixels)
    font.setWeight(QFont.Weight.Black if heavy else QFont.Weight.Bold)
    return font


class PlaybackModeButton(QAbstractButton):
    """Accessible capsule with the existing lyric reading sweep on selection."""

    SWEEP_SECONDS = 0.78

    def __init__(self, text: str, parent: QWidget):
        super().__init__(parent)
        self.setText(text)
        self.setAccessibleName(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reading = LyricReadingRenderer()
        self._progress = 0.0
        self._selected_at: float | None = None
        self._keyboard_focus = False
        self.toggled.connect(self._selection_changed)

    def _selection_changed(self, checked: bool):
        self._selected_at = time.monotonic() if checked else None
        self._progress = 0.0
        self.update()

    def settle(self):
        self._selected_at = None
        self._progress = 1.0 if self.isChecked() else 0.0
        self.update()

    def advance(self, now: float):
        if self._selected_at is not None:
            self._progress = min(1.0, max(0.0, (now - self._selected_at) / self.SWEEP_SECONDS))
            if self._progress >= 1.0:
                self._selected_at = None
            self.update()

    def release_rasters(self):
        self._selected_at = None
        self._reading = LyricReadingRenderer()

    def focusInEvent(self, event):
        self._keyboard_focus = event.reason() in {Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason}
        super().focusInEvent(event)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect()).adjusted(10, 10, -10, -10)
            if self.isChecked():
                for spread, alpha in ((8, 13), (6, 20), (4, 30), (2, 50)):
                    painter.setPen(QPen(QColor(255, 255, 255, alpha), spread * 2))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
            painter.setPen(QPen(QColor(255, 255, 255, 225), 1) if self.isChecked() else Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#f4f4f4") if self.isChecked() else QColor("#8c8e91"))
            painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
            font = _entry_font(max(18, min(32, round(rect.height() * .43))), heavy=True)
            layout = self._reading.whole_line(self.text(), font)
            width = layout.lineAt(0).naturalTextWidth() if layout.lineCount() else 0
            self._reading.paint_whole_block(painter, ((
                rect.adjusted(8, 0, -8, 0), rect.center().x() - width / 2,
                self.text(), font, QColor("#d5d5d5"), QColor("#090909"),
            ),), self._progress if self.isChecked() else 0.0)
            if self.hasFocus() and self._keyboard_focus:
                painter.setPen(QPen(QColor("#131313"), 2, Qt.PenStyle.DotLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect.adjusted(4, 4, -4, -4), rect.height() / 2, rect.height() / 2)


class MusicSourceButton(QAbstractButton):
    """User-supplied source artwork with the existing themed selection rim."""

    def __init__(self, source: EntrySource, parent: QWidget):
        super().__init__(parent)
        self.source = source
        self.setText(source.name)
        self.setAccessibleName(source.name)
        self.setToolTip(source.name)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if not self.isEnabled():
                painter.setOpacity(.38)
            rect = QRectF(self.rect()).adjusted(10, 9, -10, -9)
            if self.isChecked() or self.hasFocus():
                painter.setPen(QPen(QColor(self.source.color), 3))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect.adjusted(-4, -4, 4, 4), 14, 14)
            paint_music_source_icon(painter, rect, self.source.source_id)


class PlaybackEntryWindow(QWidget):
    selectionAccepted = Signal(bool, bool)
    sourceSelected = Signal(str)
    refreshRequested = Signal()
    loginAccepted = Signal(str)
    backRequested = Signal()
    closed = Signal()

    PALETTE_INTERVAL_SECONDS = 5.0
    PALETTES = (
        ("#f0c488", "#c8874d", "#bdcf9b", "#d493a2"),
        ("#b5d5d7", "#658f94", "#e4c19b", "#b9b3d4"),
        ("#d5bbd6", "#9571a6", "#e5c894", "#94b7aa"),
        ("#cbd5ac", "#7c9c8a", "#e8ba92", "#c7a9bc"),
    )

    def __init__(self, parent: QWidget | None = None, *, owned: bool = True, system: bool = False):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        IslandStackVisual.load_embedded_lyric_fonts()
        self.setWindowTitle(f"{APP_NAME} · 播放来源")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(840, 650)
        self.page = "modes"
        self.selected_source = ""
        self.login_state = "unavailable"
        self._accept_snapshots = True
        self._source_available: dict[str, bool] = {}
        self._source_reasons: dict[str, str] = {}
        self._qr_image = QImage()
        self._account: AccountSnapshot | None = None
        self._account_image = QImage()
        self._flow = AlbumFlowBackgroundRenderer()
        self._background_frame = QImage()
        self._palette_index = 0
        self._palette_started_at = time.monotonic()
        self._expanded = 0.0
        self._drag_origin: QPoint | None = None
        self._mode_back_enabled = True
        self._contraction_frame = QImage()
        self._contraction_target: QRect | None = None
        self._contraction_origin = QRect()
        self._contraction_fraction = 0.0
        self._contraction_finished = None
        self._contraction = QVariantAnimation(self)
        self._contraction.setDuration(450)
        self._contraction.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._contraction.valueChanged.connect(self._advance_contraction)
        self._contraction.finished.connect(self._finish_contraction)
        self._expansion = QVariantAnimation(self)
        self._expansion.setDuration(300)
        self._expansion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._expansion.valueChanged.connect(self._set_expanded)
        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(33)
        self._frame_timer.timeout.connect(self._advance)

        self.owned_button = PlaybackModeButton("程序内播放", self)
        self.system_button = PlaybackModeButton("系统抓取", self)
        self.owned_button.setChecked(bool(owned) or not system)
        self.system_button.setChecked(bool(system))
        self.owned_button.settle()
        self.system_button.settle()
        self.owned_button.toggled.connect(lambda checked: self._ensure_mode(self.owned_button, checked))
        self.system_button.toggled.connect(lambda checked: self._ensure_mode(self.system_button, checked))
        self.continue_button = QPushButton("继续", self)
        self.continue_button.setAccessibleName("继续选择播放来源")
        self.continue_button.clicked.connect(self._accept_modes)
        self.close_button = QPushButton("×", self)
        self.close_button.setAccessibleName("关闭播放来源窗口")
        self.close_button.clicked.connect(self.close)
        self.back_button = QPushButton("‹", self)
        self.back_button.setAccessibleName("返回播放模式")
        self.back_button.clicked.connect(self._go_back)
        self.refresh_button = QPushButton("刷新二维码", self)
        self.refresh_button.clicked.connect(self._refresh_login)
        self.enter_button = QPushButton("继续", self)
        self.enter_button.clicked.connect(self._accept_login)
        self.message_label = QLabel("选择音乐来源", self)
        self.message_label.setWordWrap(True)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setFont(_entry_font(14))
        self.message_label.setStyleSheet("color: #333333; background: transparent;")
        self.source_buttons = {source.source_id: MusicSourceButton(source, self) for source in ENTRY_SOURCES}
        for source_id, button in self.source_buttons.items():
            button.setEnabled(False)
            button.setToolTip(f"{button.source.name}：正在确认服务状态")
            button.clicked.connect(lambda checked=False, sid=source_id: self.select_source(sid))
        for button in (self.continue_button, self.refresh_button, self.enter_button):
            button.setFont(_entry_font(15))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet("QPushButton { color: #111; background: #f4f4f4; border: 1px solid #ffffff; border-radius: 17px; padding: 6px 15px; } QPushButton:disabled { color: #858585; } QPushButton:focus { border: 2px solid #555; }")
        for button in (self.close_button, self.back_button):
            button.setFont(_entry_font(23))
            button.setStyleSheet("QPushButton { color: #303030; border: none; background: transparent; } QPushButton:hover { background: #ffffff40; border-radius: 15px; }")
        self._layout_children()

    @property
    def selection(self) -> tuple[bool, bool]:
        return self.owned_button.isChecked(), self.system_button.isChecked()

    def _ensure_mode(self, changed: PlaybackModeButton, checked: bool):
        if not checked and not any(self.selection):
            changed.setChecked(True)
            changed.settle()

    def _accept_modes(self):
        owned, system = self.selection
        self.selectionAccepted.emit(owned, system)

    def show_choices(self):
        """Select the first-run page; the controller decides when to show it."""
        self.show_modes()

    def show_login(self):
        """Select the source page without starting a backend login request."""
        self.show_sources()

    def set_mode_back_enabled(self, enabled: bool):
        """Prevent completed onboarding from being re-entered via login Back."""
        self._mode_back_enabled = bool(enabled)
        self.back_button.setAccessibleName("返回播放模式" if enabled else "返回音乐来源")
        self._layout_children()

    def _go_back(self):
        if self.page != "sources":
            return
        if self._mode_back_enabled:
            self.show_modes()
            return
        self.show_sources()
        self.backRequested.emit()

    def show_modes(self):
        self._release_login()
        self.page = "modes"
        self._accept_snapshots = True
        self.selected_source = ""
        self.resize(840, 650)
        self._layout_children()
        if self.isVisible():
            self._frame_timer.start()
            self._advance()
        self.backRequested.emit()
        self.update()

    def show_sources(self):
        self._release_login()
        self._frame_timer.stop()
        self.page = "sources"
        self._accept_snapshots = True
        self.selected_source = ""
        for button in self.source_buttons.values():
            button.setChecked(False)
        self._expanded = 0.0
        self.resize(500, 150)
        self.message_label.setText("选择音乐来源")
        self._layout_children()
        self.update()

    def set_sources(self, sources):
        catalog = {str(getattr(source, "source_id", "")): source for source in sources}
        for source_id, button in self.source_buttons.items():
            source = catalog.get(source_id)
            available = bool(source is not None and getattr(source, "available", False))
            reason = str(getattr(source, "reason", "") or "当前服务不可用") if not available else ""
            self._source_available[source_id] = available
            self._source_reasons[source_id] = reason
            button.setEnabled(available)
            button.setToolTip(button.source.name if available else f"{button.source.name}：{reason}")
        if self.selected_source and not self._source_available.get(self.selected_source, False):
            self.set_login_snapshot(type("Unavailable", (), {"source_id": self.selected_source, "state": "unavailable", "message": self._source_reasons[self.selected_source]})())
        elif not any(self._source_available.values()):
            self.message_label.setText("音乐服务暂不可用，请稍后重试或返回系统抓取")

    def select_source(self, source_id: str):
        if source_id not in self.source_buttons or not self._source_available.get(source_id, False):
            return
        self._release_login()
        self.selected_source = source_id
        self.login_state = "starting"
        for sid, button in self.source_buttons.items():
            button.setChecked(sid == source_id)
        self.message_label.setText("正在请求登录二维码…")
        self._expansion.setStartValue(self._expanded)
        self._expansion.setEndValue(1.0)
        self._expansion.start()
        self._layout_children()
        self.sourceSelected.emit(source_id)

    def _set_expanded(self, value):
        self._expanded = float(value)
        self.resize(500, round(150 + (218 if self._account is not None else 345) * self._expanded))
        self._layout_children()
        self.update()

    def set_login_snapshot(self, snapshot):
        """Display an immutable backend result; never create an invented QR."""
        if not self._accept_snapshots or self.page != "sources" or not self.selected_source:
            return
        if str(getattr(snapshot, "source_id", "")) != self.selected_source:
            return
        self.login_state = str(getattr(snapshot, "state", "unavailable"))
        self._qr_image = QImage()
        payload = getattr(snapshot, "qr_image_bytes", b"")
        if self.login_state not in {"confirmed", "expired", "error", "unavailable", "unsupported", "cancelled"} and isinstance(payload, bytes) and 0 < len(payload) <= 4 * 1024 * 1024:
            buffer = QBuffer()
            buffer.setData(QByteArray(payload))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            reader = QImageReader(buffer)
            size = reader.size()
            if 0 < size.width() <= 2048 and 0 < size.height() <= 2048:
                decoded = reader.read()
                if not decoded.isNull():
                    self._qr_image = decoded.scaled(512, 512, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
        defaults = {"starting": "正在请求登录二维码…", "loading": "正在请求登录二维码…", "waiting": "使用对应音乐 App 扫码登录", "scanned": "已扫码，请在音乐 App 中确认", "confirmed": "登录成功", "expired": "二维码已过期，请刷新", "cancelled": "登录已取消", "unavailable": "当前音乐服务不可用", "unsupported": "当前音乐来源暂不支持登录", "error": "登录暂不可用，请重试"}
        message = str(getattr(snapshot, "message", "") or defaults.get(self.login_state, "正在确认登录状态"))
        if self._qr_image.isNull() and self.login_state in {"waiting", "scanned"}:
            message = "登录服务未提供可显示的二维码，请刷新重试"
        self.message_label.setText(message)
        account = getattr(snapshot, "account", None)
        if self.login_state == "confirmed" and isinstance(account, AccountSnapshot) and account.logged_in:
            self.set_account(account)
        self._layout_children()
        self.update()

    def set_account(self, account: AccountSnapshot):
        """Display a verified account for the currently selected platform."""
        if (not self._accept_snapshots or self.page != "sources" or
                not isinstance(account, AccountSnapshot) or not account.logged_in or
                account.source_id != self.selected_source):
            return
        if self._account is None or self._account.user_id != account.user_id:
            self._account_image = QImage()
        self._account = account
        self._qr_image = QImage()
        self.login_state = "confirmed"
        self.enter_button.setText("进入")
        self.enter_button.setFont(_entry_font(24))
        source = next(item for item in ENTRY_SOURCES if item.source_id == account.source_id)
        self.enter_button.setStyleSheet(f"QPushButton {{ color: #111; background: {source.color}; border: none; border-radius: 19px; padding: 4px 15px; }} QPushButton:focus {{ border: 2px solid #555; }}")
        self._set_expanded(self._expanded)
        self.update()

    def set_account_artwork(self, image: QImage):
        """Accept controller-loaded artwork; this window performs no I/O."""
        if not self._accept_snapshots or self._account is None or not isinstance(image, QImage):
            return
        self._account_image = image.scaled(512, 512, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation) if not image.isNull() else QImage()
        self.update()

    def _refresh_login(self):
        if self.selected_source and self._source_available.get(self.selected_source, False):
            self._qr_image = QImage()
            self.login_state = "starting"
            self.message_label.setText("正在刷新登录二维码…")
            self._layout_children()
            self.refreshRequested.emit()
            self.update()

    def _accept_login(self):
        if self.login_state == "confirmed" and self.selected_source:
            self.loginAccepted.emit(self.selected_source)

    def _release_login(self):
        self._expansion.stop()
        self._qr_image = QImage()
        self._account = None
        self._account_image = QImage()
        self.login_state = "unavailable"
        if hasattr(self, "enter_button"):
            self.enter_button.setText("继续")
            self.enter_button.setFont(_entry_font(15))
            self.enter_button.setStyleSheet("QPushButton { color: #111; background: #f4f4f4; border: 1px solid #ffffff; border-radius: 17px; padding: 6px 15px; } QPushButton:focus { border: 2px solid #555; }")

    def _layout_children(self):
        modes = self.page == "modes"
        self.owned_button.setVisible(modes)
        self.system_button.setVisible(modes)
        self.continue_button.setVisible(modes)
        self.close_button.setGeometry(self.width() - 50, 18, 30, 30)
        self.back_button.setGeometry(20, 18, 30, 30)
        self.back_button.setVisible(not modes and (self._mode_back_enabled or bool(self.selected_source)))
        if modes:
            self.owned_button.setGeometry(round(self.width() * .10), round(self.height() * .51), round(self.width() * .35), 94)
            self.system_button.setGeometry(round(self.width() * .55), round(self.height() * .51), round(self.width() * .35), 94)
            self.continue_button.setGeometry((self.width() - 82) // 2, self.height() - 92, 82, 36)
        for index, button in enumerate(self.source_buttons.values()):
            button.setVisible(not modes)
            button.setGeometry(65 + index * 94, 35, 74, 74)
        expanded = not modes and self._expanded > .85
        account_card = expanded and self._account is not None
        self.message_label.setVisible(not modes and not account_card)
        self.message_label.setGeometry(48, self.height() - 90 if expanded else 108, self.width() - 96, 36)
        self.refresh_button.setVisible(expanded and self.login_state != "confirmed")
        self.refresh_button.setEnabled(self.login_state not in {"starting", "loading", "unavailable", "unsupported", "cancelled"})
        self.refresh_button.setGeometry((self.width() - 130) // 2, self.height() - 48, 130, 34)
        self.enter_button.setVisible(expanded and self.login_state == "confirmed")
        self.enter_button.setGeometry(250, 236, 125, 38) if account_card else self.enter_button.setGeometry((self.width() - 82) // 2, self.height() - 48, 82, 34)

    def _advance(self):
        if not self.isVisible():
            self._frame_timer.stop()
            return
        now = time.monotonic()
        if self.page == "modes":
            if now - self._palette_started_at >= self.PALETTE_INTERVAL_SECONDS:
                self._palette_index = (self._palette_index + 1) % len(self.PALETTES)
                self._palette_started_at = now
            colors = tuple(QColor(value) for value in self.PALETTES[self._palette_index])
            self._background_frame = self._flow.render(self.size(), colors, now)
            self.owned_button.advance(now)
            self.system_button.advance(now)
            self.update()

    def transition_snapshot(self) -> QImage:
        """A UI-only frame for the controller's contraction into the island."""
        return self.grab().toImage()

    def contract_to(self, global_target: QRect, finished=None):
        """Contract a frozen UI frame into the controller-owned island geometry.

        No island or playback state is created here. The completion callback
        runs once after a successful contraction; user close cancels it.
        """
        if not isinstance(global_target, QRect) or not global_target.isValid():
            raise ValueError("Playback entry contraction requires a valid global QRect")
        self._contraction.stop()
        self._contraction_finished = None
        self._contraction_frame = self.transition_snapshot()
        self._contraction_origin = QRect(self.geometry())
        self._contraction_target = QRect(global_target)
        self._contraction_fraction = 0.0
        self._contraction_finished = finished
        self._frame_timer.stop()
        self._accept_snapshots = False
        self._release_login()
        for child in self.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            child.hide()
        self._contraction.setStartValue(0.0)
        self._contraction.setEndValue(1.0)
        self._contraction.start()

    def _advance_contraction(self, value):
        if self._contraction_target is None:
            return
        fraction = float(value)
        self._contraction_fraction = fraction
        first, target = self._contraction_origin, self._contraction_target
        self.setGeometry(QRect(*(round(a + (b - a) * fraction) for a, b in zip(
            (first.x(), first.y(), first.width(), first.height()),
            (target.x(), target.y(), target.width(), target.height()),
        ))))
        self.update()

    def _finish_contraction(self):
        callback = self._contraction_finished
        self._contraction_finished = None
        self.hide()
        self._contraction_frame = QImage()
        self._contraction_target = None
        if callback is not None:
            callback()

    def content_geometry(self) -> QRect:
        return QRect(self.mapToGlobal(QPoint(0, 0)), self.size())

    def showEvent(self, event):
        super().showEvent(event)
        self._accept_snapshots = True
        self._palette_started_at = time.monotonic()
        if self.page == "modes":
            self._frame_timer.start()
            self._advance()

    def hideEvent(self, event):
        self._frame_timer.stop()
        self._accept_snapshots = False
        self._contraction.stop()
        self._contraction_finished = None
        self._contraction_frame = QImage()
        self._contraction_target = None
        self._release_login()
        self.owned_button.release_rasters()
        self.system_button.release_rasters()
        self._background_frame = QImage()
        self._flow = AlbumFlowBackgroundRenderer()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._frame_timer.stop()
        self._contraction.stop()
        self._contraction_finished = None
        self._contraction_frame = QImage()
        self._contraction_target = None
        self._accept_snapshots = False
        self._release_login()
        self.closed.emit()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "owned_button") and getattr(self, "_contraction_target", None) is None:
            self._layout_children()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.windowHandle()
            if handle is None or not handle.startSystemMove():
                self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_origin = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self.page == "sources" and (self._mode_back_enabled or self.selected_source):
                self._go_back()
            else:
                self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            panel = QRectF(self.rect()).adjusted(2, 2, -2, -2)
            clip = QPainterPath()
            radius = min(panel.height() / 2, 64 if self.page == "modes" else 48)
            clip.addRoundedRect(panel, radius, radius)
            painter.setClipPath(clip)
            if self._contraction_target is not None and not self._contraction_frame.isNull():
                painter.setOpacity(1.0 - max(0.0, (self._contraction_fraction - .65) / .35))
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawImage(panel, self._contraction_frame)
                return
            if self.page == "modes":
                painter.fillRect(panel, QColor("#b8bbb0"))
                if not self._background_frame.isNull():
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                    painter.drawImage(panel, self._background_frame)
                # Keep the reference's black brand readable over moving pigments.
                painter.fillRect(panel, QColor(255, 255, 255, 70))
                painter.setFont(_entry_font(62, heavy=True))
                painter.setPen(QColor("#050505"))
                painter.drawText(QRectF(0, self.height() * .095, self.width(), 83), Qt.AlignmentFlag.AlignCenter, "GLU")
                painter.drawText(QRectF(0, self.height() * .095 + 74, self.width(), 83), Qt.AlignmentFlag.AlignCenter, "spot")
            else:
                painter.fillRect(panel, QColor("#f2f2f2"))
                if self._expanded > .05:
                    pane = QRectF(22, 126, self.width() - 44, max(1, self.height() - (150 if self._account else 238)))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor("#d9d9d9"))
                    painter.drawRoundedRect(pane, 35, 35)
                    if self._expanded > .85:
                        if self._account is not None:
                            avatar = QRectF(64, 150, 155, 155)
                            source = next(item for item in ENTRY_SOURCES if item.source_id == self._account.source_id)
                            painter.setPen(QPen(QColor(source.color), 6))
                            painter.setBrush(QColor("#525252"))
                            painter.drawEllipse(avatar)
                            if not self._account_image.isNull():
                                painter.save()
                                avatar_clip = QPainterPath()
                                avatar_clip.addEllipse(avatar.adjusted(3, 3, -3, -3))
                                painter.setClipPath(avatar_clip, Qt.ClipOperation.IntersectClip)
                                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                                side = min(self._account_image.width(), self._account_image.height())
                                crop = QRectF((self._account_image.width() - side) / 2,
                                              (self._account_image.height() - side) / 2, side, side)
                                painter.drawImage(avatar, self._account_image, crop)
                                painter.restore()
                            painter.setFont(_entry_font(24))
                            painter.setPen(QColor("#111111"))
                            name_rect = QRectF(250, 185, 180, 42)
                            name = painter.fontMetrics().elidedText(self._account.nickname or "已登录账号", Qt.TextElideMode.ElideRight, round(name_rect.width()))
                            painter.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter, name)
                            return
                        qr = QRectF((self.width() - 174) / 2, 143, 174, 174)
                        source = next((item for item in ENTRY_SOURCES if item.source_id == self.selected_source), ENTRY_SOURCES[0])
                        painter.setPen(QPen(QColor(source.color), 6))
                        painter.setBrush(QColor("#ffffff"))
                        painter.drawRoundedRect(qr, 20, 20)
                        if not self._qr_image.isNull():
                            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                            painter.drawImage(qr.adjusted(12, 12, -12, -12), self._qr_image)
                        else:
                            painter.setFont(_entry_font(17))
                            painter.setPen(QColor("#4b4b4b"))
                            label = "已登录" if self.login_state == "confirmed" else "等待登录服务"
                            painter.drawText(qr.adjusted(10, 10, -10, -10), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, label)
