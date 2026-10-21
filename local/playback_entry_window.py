"""First-run playback choices and source login display; no network or settings I/O.

The application controller owns synchronous OnlineMusicService calls in its
workers. Only a service-supplied QR image is displayed. Signals are the entire
outbound interface; closing this window never quits the tray application.
"""

from __future__ import annotations

from local.runtime_platform import normalize_playback_selection, owned_playback_only, system_media_unavailable_reason

import time
from dataclasses import dataclass

from PySide6.QtCore import QByteArray, QBuffer, QEasingCurve, QIODevice, QPoint, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QImageReader, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import QAbstractButton, QLabel, QPushButton, QWidget

from api.online_music import AccountSnapshot
from local.branding import APP_NAME
from local.island_stack_visual import IslandStackVisual
from local.music_lyric_reading import LyricReadingRenderer
from local.music_surface_background import AlbumFlowBackgroundRenderer
from local.music_source_icons import paint_music_source_icon
from local.liquid_glass import GlassPushButton, paint_liquid_glass, widget_glass_backdrop
from local.control_hover import install_control_hover


@dataclass(frozen=True)
class EntrySource:
    source_id: str
    name: str
    color: str


@dataclass(frozen=True)
class ChoicePlacement:
    """Mapped native-island geometry; the window never detects a system bar."""
    mode: str
    geometry: QRect
    content: QRectF
    shell: QPainterPath
    color: QColor


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


class EntryNavigationButton(QAbstractButton):
    """Shared native navigation geometry with a 44 px interactive target."""
    def __init__(self, kind: str, label: str, parent: QWidget):
        super().__init__(parent)
        self.kind = kind
        self.setAccessibleName(label)
        self.setToolTip(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        install_control_hover(self)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            backdrop, source = widget_glass_backdrop(self, rect)
            paint_liquid_glass(painter, rect, radius=12, hovered=self.underMouse(), pressed=self.isDown(),
                              backdrop=backdrop, backdrop_rect=source, refractive=True, cache_token=id(self),
                              border_color=QColor("#494b51") if self.hasFocus() else None)
            painter.setPen(QPen(QColor("#e8e9ec"), 2, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            center = rect.center()
            if self.kind == "back":
                painter.drawLine(center + QPointF(3, -7), center + QPointF(-4, 0))
                painter.drawLine(center + QPointF(-4, 0), center + QPointF(3, 7))
            else:
                painter.drawLine(center + QPointF(-5, -5), center + QPointF(5, 5))
                painter.drawLine(center + QPointF(-5, 5), center + QPointF(5, -5))


class PlaybackModeButton(QAbstractButton):
    """Accessible capsule with the existing lyric reading sweep on selection."""

    SWEEP_SECONDS = 0.78

    def __init__(self, text: str, parent: QWidget, *, description: str = "", island_style: bool = False):
        super().__init__(parent)
        self.setText(text)
        self.setAccessibleName(text)
        self.description = description
        self.island_style = island_style
        self.setAccessibleDescription(description)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reading = LyricReadingRenderer()
        self._progress = 0.0
        self._selected_at: float | None = None
        self._keyboard_focus = False
        self._rejected_at: float | None = None
        self.toggled.connect(self._selection_changed)
        install_control_hover(self)

    def _selection_changed(self, checked: bool):
        self._selected_at = time.monotonic() if checked else None
        self._progress = 0.0
        self.update()

    def settle(self):
        self._selected_at = None
        self._progress = 1.0 if self.isChecked() else 0.0
        self.update()

    def advance(self, now: float):
        if self._rejected_at is not None:
            if now - self._rejected_at >= 1.1:
                self._rejected_at = None
            self.update()
        if self._selected_at is not None:
            self._progress = min(1.0, max(0.0, (now - self._selected_at) / self.SWEEP_SECONDS))
            if self._progress >= 1.0:
                self._selected_at = None
            self.update()

    def release_rasters(self):
        self._selected_at = None
        self._rejected_at = None
        self._reading = LyricReadingRenderer()

    def focusInEvent(self, event):
        self._keyboard_focus = event.reason() in {Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason}
        super().focusInEvent(event)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if self.island_style:
                rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
                face = QColor("#ffffff") if self.isChecked() else QColor("#707175")
                if self.isDown():
                    face = face.darker(112)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(face)
                painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
                font = _entry_font(max(14, min(21, int((rect.width() - 16) / max(1, len(self.text()))))), heavy=True)
                layout = self._reading.whole_line(self.text(), font)
                width = layout.lineAt(0).naturalTextWidth() if layout.lineCount() else 0
                self._reading.paint_whole_block(painter, ((
                    rect, rect.center().x() - width / 2, self.text(), font,
                    QColor("#d8d8da"), QColor("#050505"),
                ),), self._progress if self.isChecked() else 0.0)
                if self._rejected_at is not None:
                    phase = min(1.0, max(0.0, (time.monotonic() - self._rejected_at) / 1.1))
                    painter.setPen(QPen(QColor(255, 181, 86, round(235 * (1.0 - phase))), 2))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), rect.height() / 2, rect.height() / 2)
                if self.hasFocus() and self._keyboard_focus:
                    painter.setPen(QPen(QColor("#92949a"), 2, Qt.PenStyle.DotLine))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), rect.height() / 2, rect.height() / 2)
                return
            rect = QRectF(self.rect()).adjusted(10, 10, -10, -10)
            if self.isChecked():
                for spread, alpha in ((8, 13), (6, 20), (4, 30), (2, 50)):
                    painter.setPen(QPen(QColor(255, 255, 255, alpha), spread * 2))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
            backdrop, source = widget_glass_backdrop(self, rect)
            paint_liquid_glass(painter, rect, radius=rect.height() / 2, light=self.isChecked(),
                              base_color=QColor("#f4f4f4") if self.isChecked() else QColor("#8c8e91"),
                              border_color=QColor(255, 255, 255, 225) if self.isChecked() else None,
                              backdrop=backdrop, backdrop_rect=source, refractive=True, cache_token=id(self),
                              hovered=self.underMouse(), pressed=self.isDown(), active=self.isChecked())
            if self._rejected_at is not None:
                phase = min(1.0, max(0.0, (time.monotonic() - self._rejected_at) / 1.1))
                painter.setPen(QPen(QColor(155, 64, 18, round(235 * (1.0 - phase))), 2.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect.adjusted(-3 * phase, -3 * phase, 3 * phase, 3 * phase), rect.height() / 2, rect.height() / 2)
            if self.description:
                title_rect = QRectF(rect.x() + 12, rect.y() + (9 if rect.height() < 70 else 17),
                                    rect.width() - 52, 28)
                font = _entry_font(max(13, min(25, int(title_rect.width() / max(1, len(self.text()))))), heavy=True)
            else:
                title_rect = rect.adjusted(8, 0, -8, 0)
                font = _entry_font(max(18, min(32, round(rect.height() * .43))), heavy=True)
            layout = self._reading.whole_line(self.text(), font)
            width = layout.lineAt(0).naturalTextWidth() if layout.lineCount() else 0
            self._reading.paint_whole_block(painter, ((
                title_rect, title_rect.center().x() - width / 2,
                self.text(), font, QColor("#d5d5d5"), QColor("#090909"),
            ),), self._progress if self.isChecked() else 0.0)
            if self.description:
                ink = QColor("#292d34") if self.isChecked() else QColor("#e1e3e7")
                painter.setPen(ink)
                painter.setFont(_entry_font(11 if rect.width() < 200 else 12))
                caption = painter.fontMetrics().elidedText(self.description, Qt.TextElideMode.ElideRight,
                                                           max(1, round(rect.width() - 24)))
                painter.drawText(QRectF(rect.x() + 12, title_rect.bottom() + 1, rect.width() - 24, 20),
                                 Qt.AlignmentFlag.AlignCenter, caption)
                check = QRectF(rect.right() - 31, title_rect.center().y() - 8, 16, 16)
                painter.setPen(QPen(ink, 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(check)
                if self.isChecked():
                    path = QPainterPath()
                    path.moveTo(check.x() + 4, check.y() + 8)
                    path.lineTo(check.x() + 7, check.y() + 11)
                    path.lineTo(check.x() + 12, check.y() + 5)
                    painter.setPen(QPen(ink, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                        Qt.PenJoinStyle.RoundJoin))
                    painter.drawPath(path)
            if self.hasFocus() and self._keyboard_focus:
                painter.setPen(QPen(QColor("#131313"), 2, Qt.PenStyle.DotLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect.adjusted(4, 4, -4, -4), rect.height() / 2, rect.height() / 2)


class MusicSourceButton(QAbstractButton):
    """User-supplied source artwork with the existing themed selection rim."""

    def __init__(self, source: EntrySource, parent: QWidget):
        super().__init__(parent)
        self.source = source
        self.show_caption = True
        self._availability_status = ""
        self._availability_reason = ""
        self.setText(source.name)
        self.setAccessibleName(source.name)
        self.setToolTip(source.name)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        install_control_hover(self)

    @property
    def availability_status(self) -> str:
        """Short, visible status for an unavailable source tile."""
        return self._availability_status

    def set_availability_status(self, status: str = "", reason: str = "") -> None:
        """Keep the concise tile label separate from the backend diagnostic.

        The source service owns the full reason.  The tile only presents a
        bounded summary while exposing the complete diagnostic to tooltips and
        assistive technology.
        """
        self._availability_status = str(status or "").strip()[:32]
        self._availability_reason = str(reason or "").strip()[:256]
        if self._availability_status:
            self.setAccessibleName(f"{self.source.name}（{self._availability_status}）")
            description = f"{self.source.name}：{self._availability_status}"
            if self._availability_reason:
                description += f"。{self._availability_reason}"
            self.setAccessibleDescription(description)
        else:
            self.setAccessibleName(self.source.name)
            self.setAccessibleDescription("")
        self.update()

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if not self.isEnabled():
                painter.setOpacity(.38)
            rect = QRectF(self.rect()).adjusted(8, 8, -8, -24 if self.show_caption else -8)
            face = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            backdrop, source = widget_glass_backdrop(self, face)
            paint_liquid_glass(painter, face, radius=16,
                              backdrop=backdrop, backdrop_rect=source, refractive=True, cache_token=id(self),
                              border_color=QColor(self.source.color) if self.isChecked() else QColor("#3c3e44"),
                              hovered=self.underMouse(), pressed=self.isDown(),
                              active=self.isChecked())
            if self.isChecked() or self.hasFocus():
                painter.setPen(QPen(QColor(self.source.color), 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 15, 15)
            # Reserve a small text lane for disabled-source diagnostics while
            # keeping the four source tiles at their existing geometry.
            compact = self.height() < 80
            side = min((24.0 if self._availability_status else 28.0) if compact else
                       (34.0 if self._availability_status else 40.0), rect.width(), rect.height())
            icon_y = 5 if self._availability_status else 7
            icon = QRectF(rect.center().x() - side / 2, icon_y if compact else rect.center().y() - side / 2, side, side)
            paint_music_source_icon(painter, icon, self.source.source_id)
            title_bottom = self.height() - 25
            if self._availability_status:
                title_bottom = self.height() - 39
            if compact:
                title_bottom = 28 if self._availability_status else 37
            painter.setOpacity(1.0 if self.isEnabled() else .8)
            painter.setFont(_entry_font(11 if self._availability_status else 12))
            painter.setPen(QColor("#e2e3e7"))
            title = self.source.name
            if self.show_caption:
                painter.drawText(QRectF(2, title_bottom, self.width() - 4, 18), Qt.AlignmentFlag.AlignCenter, title)
                if self._availability_status:
                    # Disabled artwork keeps its quiet tone; the diagnostic
                    # still needs to be legible before a user hovers the tile.
                    painter.setOpacity(.78)
                    painter.setFont(_entry_font(10))
                    painter.setPen(QColor("#b9bcc6"))
                    painter.drawText(QRectF(2, self.height() - 19 if compact else self.height() - 21,
                                           self.width() - 4, 16), Qt.AlignmentFlag.AlignCenter, self._availability_status)


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

    def __init__(self, parent: QWidget | None = None, *, owned: bool = True, system: bool = True):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        IslandStackVisual.load_embedded_lyric_fonts()
        self.setWindowTitle(f"{APP_NAME} · 播放来源")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setProperty("controlHoverDisabled", True)
        self._resize_page(370, 176)
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
        self._source_background_size = None
        self._palette_index = 0
        self._palette_started_at = time.monotonic()
        self._expanded = 0.0
        self._drag_origin: QPoint | None = None
        self._mode_back_enabled = True
        self._mode_warning_until = 0.0
        self._system_session_hint = "系统抓取通过 Windows SMTC 同步外部播放器。"
        self._choices_placement: ChoicePlacement | None = None
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
        self._page_morph = QVariantAnimation(self)
        self._page_morph.setDuration(420)
        self._page_morph.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._page_morph.valueChanged.connect(self._advance_page_morph)
        self._page_morph.finished.connect(self._finish_page_morph)
        self._page_morph_origin = QRect()
        self._page_morph_target = QRect()
        self._page_morph_from = QImage()
        self._page_morph_to = QImage()
        self._page_morph_fraction = 0.0
        self._expansion = QVariantAnimation(self)
        self._expansion.setDuration(300)
        self._expansion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._expansion.valueChanged.connect(self._set_expanded)
        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(33)
        self._frame_timer.timeout.connect(self._advance)

        self.owned_button = PlaybackModeButton("程序内播放", self, description="连接账号，播放音乐库", island_style=True)
        self.system_button = PlaybackModeButton("系统内抓取", self, description="同步外部播放器与歌词", island_style=True)
        owned, system = normalize_playback_selection(owned, system)
        self.owned_button.setChecked(bool(owned) or not system)
        self.system_button.setChecked(bool(system))
        if owned_playback_only():
            self.system_button.setEnabled(False)
            self.owned_button.setEnabled(False)
        self.owned_button.settle()
        self.system_button.settle()
        self.owned_button.toggled.connect(lambda checked: self._ensure_mode(self.owned_button, checked))
        self.system_button.toggled.connect(lambda checked: self._ensure_mode(self.system_button, checked))
        self.continue_button = QPushButton("确定", self)
        self.continue_button.setAccessibleName("继续选择播放来源")
        self.continue_button.clicked.connect(self._accept_modes)
        self.close_button = EntryNavigationButton("close", "关闭播放来源窗口", self)
        self.close_button.setAccessibleName("关闭播放来源窗口")
        self.close_button.clicked.connect(self.close)
        self.back_button = EntryNavigationButton("back", "返回播放模式", self)
        self.back_button.setAccessibleName("返回播放模式")
        self.back_button.clicked.connect(self._go_back)
        self.refresh_button = GlassPushButton("刷新二维码", self)
        self.refresh_button.clicked.connect(self._refresh_login)
        self.enter_button = GlassPushButton("继续", self)
        self.enter_button.clicked.connect(self._accept_login)
        self.message_label = QLabel("选择音乐来源", self)
        self.message_label.setWordWrap(True)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setFont(_entry_font(14))
        self.message_label.setStyleSheet("color: #b3b6c0; background: transparent;")
        self.mode_hint = QLabel(self)
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.mode_hint.setFont(_entry_font(13))
        self.mode_hint.setStyleSheet("color: #f4f5f7; background: transparent;")
        self.owned_button.setToolTip("在 GluSpot 内连接音乐账号并播放歌单")
        self.system_button.setToolTip("通过 Windows SMTC 同步外部播放器的歌曲与播放状态")
        self._update_mode_hint()
        self.source_buttons = {source.source_id: MusicSourceButton(source, self) for source in ENTRY_SOURCES}
        for source_id, button in self.source_buttons.items():
            button.setEnabled(False)
            button.setToolTip(f"{button.source.name}：正在确认服务状态")
            button.clicked.connect(lambda checked=False, sid=source_id: self.select_source(sid))
        for button in (self.refresh_button, self.enter_button):
            button.setGlassLight(False)
            button.setProperty("glassRadius", 12.0)
            button.setProperty("glassRefractive", True)
            button.setFont(_entry_font(15))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet("QPushButton { color: #f4f5f7; background: rgba(255,255,255,12); border: none; border-radius: 12px; padding: 6px 15px; } QPushButton:hover { background: rgba(255,255,255,24); } QPushButton:disabled { color: #9296a1; } QPushButton:focus { border: 2px solid #afb6c8; }")
        self.continue_button.setFont(_entry_font(21, heavy=True))
        self.continue_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.continue_button.setStyleSheet("QPushButton { color: #050505; background: #ffffff; border: none; border-radius: 21px; padding: 0; } QPushButton:pressed { background: #dedee0; } QPushButton:focus { border: 2px solid #92949a; }")
        self._layout_children()

    @property
    def selection(self) -> tuple[bool, bool]:
        return self.owned_button.isChecked(), self.system_button.isChecked()

    def _resize_page(self, width: int, height: int):
        # QWidget.screen() reparents shared wrappers to this transient widget.
        screen = QGuiApplication.screenAt(self.frameGeometry().center()) or QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)
        self._available_page_height = available.height()
        self.resize(min(width, available.width()), min(height, available.height()))

    def _ensure_mode(self, changed: PlaybackModeButton, checked: bool):
        if not checked and not any(self.selection):
            changed.setChecked(True)
            changed._rejected_at = time.monotonic()
            self._mode_warning_until = time.monotonic() + 2.4
        self._update_mode_hint()

    def set_system_session_hint(self, message: str):
        """Present provider evidence; never infer another app's SMTC switch."""
        self._system_session_hint = str(message)
        self._update_mode_hint()

    def _update_mode_hint(self):
        if not hasattr(self, "mode_hint"):
            return
        if owned_playback_only():
            self.mode_hint.setText(system_media_unavailable_reason())
            self.mode_hint.setToolTip(system_media_unavailable_reason())
            return
        self.mode_hint.setToolTip(self._system_session_hint)
        self.mode_hint.setAccessibleDescription(self._system_session_hint)
        if time.monotonic() < self._mode_warning_until:
            self.mode_hint.setText("至少保留一种")
            return
        self.mode_hint.setText("可在设置中更改")

    def _accept_modes(self):
        owned, system = self.selection
        self.selectionAccepted.emit(owned, system)

    def show_choices(self):
        """Select the first-run page; the controller decides when to show it."""
        self.show_modes()

    def show_choices_from(self, global_origin: QRect):
        """Expand the installer-owned island in already-mapped Qt coordinates."""
        if not isinstance(global_origin, QRect) or not global_origin.isValid():
            raise ValueError("Playback choices require a valid logical island QRect")
        self.show_modes()
        target = self._choices_placement.geometry if self._choices_placement is not None else self._anchored_page_geometry(global_origin)
        self._start_page_morph(global_origin, target, QImage())

    def set_choices_placement(self, placement: ChoicePlacement):
        """Follow the controller's real Top/Finder shell without moving login."""
        previous = self._choices_placement
        if previous == placement:
            return
        animate = previous is not None and self.page == "modes" and self.isVisible()
        origin = QRect(self.geometry())
        frame = self.transition_snapshot() if animate else QImage()
        self._choices_placement = placement
        if self.page != "modes":
            return
        if animate and previous.geometry != placement.geometry:
            self._start_page_morph(origin, placement.geometry, frame)
        elif self._page_morph_to.isNull():
            self.setGeometry(placement.geometry)
            self._layout_children()
        self.update()

    def _choices_content_rect(self) -> QRectF:
        placement = self._choices_placement
        return QRectF(placement.content) if placement is not None else QRectF(self.rect())

    def show_login(self):
        """Select the source page without starting a backend login request."""
        animate = self.isVisible() and self.page == "modes"
        origin = QRect(self.geometry())
        frame = self.transition_snapshot() if animate else QImage()
        self.show_sources()
        if animate:
            self._start_page_morph(origin, self._anchored_page_geometry(origin), frame)

    def set_mode_back_enabled(self, enabled: bool):
        """Prevent completed onboarding from being re-entered via login Back."""
        self._mode_back_enabled = bool(enabled)
        self.back_button.setAccessibleName("返回播放模式" if enabled else "返回音乐来源")
        self.back_button.setToolTip(self.back_button.accessibleName())
        self._layout_children()

    def _go_back(self):
        if self.page != "sources":
            return
        if self.selected_source:
            self.show_sources()
            self.backRequested.emit()
            return
        if self._mode_back_enabled:
            self.show_modes()
            return
        self.show_sources()
        self.backRequested.emit()

    def show_modes(self):
        self._cancel_page_morph()
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self._release_login()
        self.page = "modes"
        self._accept_snapshots = True
        self.selected_source = ""
        self._background_frame = QImage()
        if self._choices_placement is not None:
            self.setGeometry(self._choices_placement.geometry)
        else:
            self._resize_page(370, 176)
        self._layout_children()
        if self.isVisible():
            self._frame_timer.start()
            self._advance()
        self.backRequested.emit()
        if was_visible:
            self.show()
        self.update()

    def show_sources(self):
        self._cancel_page_morph()
        was_visible = self.isVisible()
        # Only the edge-attached first-use island needs topmost placement.
        # Login retains the normal window level so settings/modal UI can focus.
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self._release_login()
        self._frame_timer.stop()
        self.page = "sources"
        self._background_frame = QImage()
        self._source_background_size = None
        self._accept_snapshots = True
        self.selected_source = ""
        for button in self.source_buttons.values():
            button.setChecked(False)
        self._expanded = 0.0
        self._resize_page(560, 264)
        self.message_label.setText("选择音乐来源")
        self._layout_children()
        if was_visible:
            self.show()
        self.update()

    def _anchored_page_geometry(self, origin: QRect) -> QRect:
        screen = QGuiApplication.screenAt(origin.center()) or QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)
        x = max(available.left(), min(origin.center().x() - (self.width() - 1) // 2,
                                      available.right() - self.width() + 1))
        y = max(available.top(), min(origin.y(), available.bottom() - self.height() + 1))
        return QRect(x, y, self.width(), self.height())

    def _cancel_page_morph(self):
        if not hasattr(self, "_page_morph"):
            return
        self._page_morph.stop()
        self._page_morph_from = QImage()
        self._page_morph_to = QImage()

    def _start_page_morph(self, origin: QRect, target: QRect, frame: QImage):
        self._cancel_page_morph()
        self.setGeometry(target)
        self._layout_children()
        target_frame = self.transition_snapshot()
        self._page_morph_origin = QRect(origin)
        self._page_morph_target = QRect(target)
        self._page_morph_from = frame
        self._page_morph_to = target_frame
        self._page_morph_fraction = 0.0
        self.setGeometry(origin)
        for child in self.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            child.hide()
        self._page_morph.setStartValue(0.0)
        self._page_morph.setEndValue(1.0)
        self._page_morph.start()

    def _advance_page_morph(self, value):
        fraction = self._page_morph_fraction = float(value)
        origin, target = self._page_morph_origin, self._page_morph_target
        self.setGeometry(QRect(*(round(a + (b - a) * fraction) for a, b in zip(
            (origin.x(), origin.y(), origin.width(), origin.height()),
            (target.x(), target.y(), target.width(), target.height()),
        ))))
        self.update()

    def _finish_page_morph(self):
        self.setGeometry(self._page_morph_target)
        self._cancel_page_morph()
        self._layout_children()
        self.update()

    def set_sources(self, sources):
        catalog = {str(getattr(source, "source_id", "")): source for source in sources}
        for source_id, button in self.source_buttons.items():
            source = catalog.get(source_id)
            available = bool(source is not None and getattr(source, "available", False))
            reason = str(getattr(source, "reason", "") or "当前服务不可用") if not available else ""
            status = self._source_availability_status(source_id, reason) if not available else ""
            self._source_available[source_id] = available
            self._source_reasons[source_id] = reason
            button.setEnabled(available)
            button.setToolTip(button.source.name if available else f"{button.source.name}：{reason}")
            button.set_availability_status(status, reason)
        if self.selected_source and not self._source_available.get(self.selected_source, False):
            self.set_login_snapshot(type("Unavailable", (), {"source_id": self.selected_source, "state": "unavailable", "message": self._source_reasons[self.selected_source]})())
        elif not self.selected_source:
            self.message_label.setText("选择音乐来源" if any(self._source_available.values()) else self._source_unavailable_message())

    @staticmethod
    def _source_availability_status(source_id: str, reason: str) -> str:
        """Map provider diagnostics to a short source-page status label."""
        text = str(reason or "")
        if source_id == "qishui" or "不支持" in text or "尚无已验证" in text:
            return "暂不支持"
        if "版本不匹配" in text or "更新" in text:
            return "需更新运行时"
        if "未安装" in text or "适配依赖" in text:
            return "需安装运行时"
        return "暂不可用"

    def _source_unavailable_message(self) -> str:
        statuses = {button.availability_status for button in self.source_buttons.values()}
        hints = []
        if "需安装运行时" in statuses and "需更新运行时" in statuses:
            hints.append("请修复在线音乐运行时")
        elif "需安装运行时" in statuses:
            hints.append("请安装在线音乐运行时")
        elif "需更新运行时" in statuses:
            hints.append("请更新在线音乐运行时")
        if "暂不支持" in statuses:
            hints.append("汽水音乐暂不支持")
        if not hints:
            return "没有可用音乐来源，请稍后重试或返回系统抓取"
        return "没有可用音乐来源；" + "；".join(hints) + "。"

    def select_source(self, source_id: str):
        if source_id not in self.source_buttons or not self._source_available.get(source_id, False):
            return
        if source_id == self.selected_source:
            # A checked Qt button toggles before clicked. Keep the active
            # source selected without cancelling its QR/account transaction.
            # Retry is the adjacent explicit action, not another source click.
            self.source_buttons[source_id].setChecked(True)
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
        self._resize_page(560, round(264 + (224 if self._account is not None else 296) * self._expanded))
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
        defaults = {"starting": "正在请求登录二维码…", "loading": "正在请求登录二维码…", "waiting": "等待扫码", "scanned": "已扫码，请在手机确认", "confirmed": "登录成功", "expired": "二维码已过期，请刷新", "cancelled": "登录已取消", "unavailable": "当前音乐服务不可用", "unsupported": "当前音乐来源暂不支持登录", "error": "登录暂不可用，请重试"}
        message = str(getattr(snapshot, "message", "") or defaults.get(self.login_state, "正在确认登录状态"))
        backend_message = message
        # The adjacent guide explains how to scan. Condense only the service's
        # known generic instructions; retain distinct provider diagnostics.
        standard_messages = {
            "waiting": {"使用手机音乐应用扫码登录", "使用对应音乐 App 扫码登录"},
            "scanned": {"已扫码，请在手机上确认", "已扫码，请在音乐 App 中确认"},
        }
        if message in standard_messages.get(self.login_state, set()):
            message = defaults[self.login_state]
        if self._qr_image.isNull() and self.login_state in {"waiting", "scanned"}:
            message = "登录服务未提供可显示的二维码，请刷新重试"
        self.message_label.setText(message)
        self.message_label.setToolTip(backend_message if message != backend_message else "")
        self.message_label.setAccessibleDescription(backend_message if message != backend_message else "")
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
        self.enter_button.setText("进入音乐库")
        self.enter_button.setFont(_entry_font(16))
        source = next(item for item in ENTRY_SOURCES if item.source_id == account.source_id)
        self.enter_button.setStyleSheet(f"QPushButton {{ color: #f4f5f7; background: rgba(255,255,255,18); border: 1px solid {source.color}; border-radius: 12px; padding: 6px 15px; }} QPushButton:hover {{ background: rgba(255,255,255,32); }} QPushButton:focus {{ border: 2px solid #b7baca; }}")
        self.enter_button.setGlassLight(False)
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
            self.message_label.setToolTip("")
            self.message_label.setAccessibleDescription("")
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
        if hasattr(self, "message_label"):
            self.message_label.setToolTip("")
            self.message_label.setAccessibleDescription("")
        if hasattr(self, "enter_button"):
            self.enter_button.setText("继续")
            self.enter_button.setFont(_entry_font(15))
            self.enter_button.setStyleSheet("QPushButton { color: #f4f5f7; background: rgba(255,255,255,12); border: none; border-radius: 12px; padding: 6px 15px; } QPushButton:focus { border: 2px solid #afb6c8; }")
            self.enter_button.setGlassLight(False)

    def _source_backdrop(self):
        """Freeze the existing onboarding pigment scene; no login timer/capture."""
        key = (self.width(), self.height())
        if self._source_background_size == key and not self._background_frame.isNull():
            return
        renderer = AlbumFlowBackgroundRenderer()
        self._background_frame = renderer.render(self.size(), tuple(QColor(value) for value in self.PALETTES[1]),
                                                 0.0, intensity=0.0).copy()
        with QPainter(self._background_frame) as painter:
            painter.fillRect(self._background_frame.rect(), QColor(12, 14, 20, 142))
        self._source_background_size = key

    def _compact_login_layout(self) -> bool:
        # Choose the short-display composition once for the target screen.
        # A normal-height expansion passes through 264..480 px; that must not
        # move the source row into compact mode and back during the animation.
        return bool(self.selected_source) and (self._available_page_height < 480 or
                                               (self._expanded > .85 and self.height() < 480))

    def _source_pane_rect(self) -> QRectF:
        top = 152 if self._compact_login_layout() else 232
        return QRectF(32, top, self.width() - 64, max(1, self.height() - top - 24))

    def _login_content_rects(self) -> tuple[QRectF, QRectF]:
        """QR and its status/action share one pane at short display heights."""
        pane = self._source_pane_rect()
        side = min(192, max(96, (self.width() - 112) * .45), max(96, pane.height() - 48))
        qr = QRectF(56, pane.center().y() - side / 2, side, side)
        detail_x = qr.right() + 24
        return qr, QRectF(detail_x, pane.top() + (14 if self._compact_login_layout() else 28),
                         max(1, self.width() - 56 - detail_x), max(1, pane.height() - 42))

    def _login_detail_geometry(self) -> tuple[QRectF, QRectF, QRectF, QRectF]:
        _qr, detail = self._login_content_rects()
        compact = self._compact_login_layout()
        title = QRectF(detail.x(), detail.y(), detail.width(), 28 if compact else 32)
        helper = QRectF(detail.x(), detail.y() + (34 if compact else 40), detail.width(), 32 if compact else 48)
        action_y = self._source_pane_rect().bottom() - (8 if compact else 16) - 44
        message_y = helper.bottom() + 6
        message = QRectF(detail.x(), message_y, detail.width(), max(24, min(76, action_y - 8 - message_y)))
        action = QRectF(detail.x(), action_y, min(152, detail.width()), 44)
        return title, helper, message, action

    def _account_content_rects(self) -> tuple[QRectF, QRectF, QRectF]:
        pane = self._source_pane_rect()
        compact = self._compact_login_layout()
        side = 96 if compact else 128
        avatar = QRectF(56 if compact else 64, pane.center().y() - side / 2, side, side)
        text_x = avatar.right() + (24 if compact else 40)
        name = QRectF(text_x, pane.top() + (14 if compact else 60), self.width() - 32 - text_x, 32 if compact else 40)
        action = QRectF(text_x, pane.bottom() - (8 if compact else 36) - 44, min(152, name.width()), 44)
        return avatar, name, action

    def _login_placeholder_text(self) -> str:
        return {
            "starting": "正在连接…", "loading": "正在读取账号…",
            "expired": "二维码已过期", "error": "暂时无法连接",
            "unsupported": "暂不支持登录", "unavailable": "服务不可用",
            "cancelled": "登录已取消", "confirmed": "已登录",
        }.get(self.login_state, "暂无二维码")

    def _login_action_text(self) -> str:
        if self.login_state in {"starting", "loading"}:
            return "正在连接…"
        if self.login_state == "error" and "刷新" in self.message_label.text():
            return "刷新二维码"
        if self.login_state in {"error", "cancelled"}:
            return "重试登录"
        return "刷新二维码"

    def _layout_children(self):
        modes = self.page == "modes"
        self.owned_button.setVisible(modes)
        self.system_button.setVisible(modes)
        self.continue_button.setVisible(modes)
        self.mode_hint.setVisible(modes)
        self.close_button.setGeometry(self.width() - 68, 16, 44, 44)
        self.close_button.setVisible(not modes)
        self.back_button.setGeometry(24, 16, 44, 44)
        self.back_button.setVisible(not modes and (self._mode_back_enabled or bool(self.selected_source)))
        self.back_button.setAccessibleName("返回音乐来源" if self.selected_source else "返回播放模式")
        self.back_button.setToolTip(self.back_button.accessibleName())
        if modes:
            body = self._choices_content_rect().toAlignedRect()
            inset, gutter = (28 if body.width() >= 360 else 20), 20
            card_width = (body.width() - inset * 2 - gutter) // 2
            self.owned_button.setGeometry(body.x() + inset, body.y() + 22, card_width, 46)
            self.system_button.setGeometry(body.x() + inset + card_width + gutter, body.y() + 22, card_width, 46)
            action_x = body.x() + max(inset, round(body.width() * .245))
            self.continue_button.setGeometry(action_x, body.bottom() + 1 - 64, 84, 44)
            hint_x = action_x + 94
            self.mode_hint.setFont(_entry_font(13))
            self.mode_hint.setGeometry(hint_x, body.bottom() + 1 - 62, body.right() + 1 - hint_x - inset, 38)
        for index, button in enumerate(self.source_buttons.values()):
            button.setVisible(not modes)
            tile_width = (self.width() - 112) // 4
            button.setGeometry(32 + index * (tile_width + 16), 72 if self._compact_login_layout() else 112,
                               tile_width, 64 if self._compact_login_layout() else 96)
        expanded = not modes and self._expanded > .85
        account_card = expanded and self._account is not None
        self.message_label.setVisible(not modes and not account_card)
        if expanded and not account_card:
            _title, _helper, message, _action = self._login_detail_geometry()
            self.message_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self.message_label.setFont(_entry_font(12 if self._compact_login_layout() else 14))
            self.message_label.setGeometry(message.toAlignedRect())
        else:
            self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.message_label.setFont(_entry_font(14))
            self.message_label.setGeometry(32, 224, self.width() - 64, 24)
        self.refresh_button.setVisible(expanded and self.login_state != "confirmed")
        self.refresh_button.setEnabled(self.login_state not in {"starting", "loading", "unavailable", "unsupported"})
        self.refresh_button.setText(self._login_action_text())
        self.refresh_button.setAccessibleName(self.refresh_button.text())
        _title, _helper, _message, action = self._login_detail_geometry()
        self.refresh_button.setGeometry(action.toAlignedRect())
        self.enter_button.setVisible(expanded and self.login_state == "confirmed")
        if account_card:
            _avatar, _name, action = self._account_content_rects()
            self.enter_button.setGeometry(action.toAlignedRect())
        else:
            self.enter_button.setGeometry((self.width() - 120) // 2, self.height() - 84, 120, 44)
        if not modes:
            self._source_backdrop()
        if not self._page_morph_to.isNull():
            for child in self.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
                child.hide()

    def _advance(self):
        if not self.isVisible():
            self._frame_timer.stop()
            return
        now = time.monotonic()
        if self.page == "modes":
            self.owned_button.advance(now)
            self.system_button.advance(now)
            if self._mode_warning_until and now >= self._mode_warning_until:
                self._mode_warning_until = 0.0
                self._update_mode_hint()
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
        else:
            self._source_backdrop()

    def hideEvent(self, event):
        self._cancel_page_morph()
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
        self._cancel_page_morph()
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
        if event.button() == Qt.MouseButton.LeftButton and not (self.page == "modes" and self._choices_placement is not None):
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
            radius = min(panel.height() / 2, 32 if self.page == "modes" else 24)
            clip.addRoundedRect(panel, radius, radius)
            placement = self._choices_placement if self.page == "modes" else None
            if placement is not None:
                scale = QTransform.fromScale(self.width() / placement.geometry.width(), self.height() / placement.geometry.height())
                clip = scale.map(placement.shell)
                panel = QRectF(self.rect())
            painter.setClipPath(clip)
            if not self._page_morph_to.isNull():
                painter.fillRect(panel, QColor("#000000"))
                fraction = self._page_morph_fraction
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                if not self._page_morph_from.isNull():
                    painter.setOpacity(max(0.0, 1.0 - fraction / .38))
                    painter.drawImage(panel, self._page_morph_from)
                painter.setOpacity(max(0.0, min(1.0, (fraction - .30) / .70)))
                painter.drawImage(panel, self._page_morph_to)
                return
            if self._contraction_target is not None and not self._contraction_frame.isNull():
                painter.setOpacity(1.0 - max(0.0, (self._contraction_fraction - .65) / .35))
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawImage(panel, self._contraction_frame)
                return
            if self.page == "modes":
                if placement is not None and placement.mode == "finder":
                    painter.fillPath(clip, placement.color)
                    body = QPainterPath()
                    body.addRoundedRect(placement.content, 32, 32)
                    painter.fillPath(body, QColor("#000000"))
                else:
                    painter.fillRect(panel, QColor("#000000"))
            else:
                painter.fillRect(panel, QColor("#15171c"))
                if not self._background_frame.isNull():
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                    painter.drawImage(panel, self._background_frame)
                painter.setFont(_entry_font(23))
                painter.setPen(QColor("#f1f2f5"))
                painter.drawText(QRectF(84 if self.back_button.isVisible() else 24, 20, self.width() - 168, 36),
                                 Qt.AlignmentFlag.AlignVCenter, "音乐来源")
                if not self._compact_login_layout():
                    painter.setFont(_entry_font(13))
                    painter.setPen(QColor("#b8bdc7"))
                    painter.drawText(QRectF(32, 76, self.width() - 64, 24), Qt.AlignmentFlag.AlignVCenter,
                                     "连接你的音乐账号，继续收听收藏与歌单")
                if self._expanded > .05:
                    pane = self._source_pane_rect()
                    sx = self._background_frame.width() / max(1, self.width())
                    sy = self._background_frame.height() / max(1, self.height())
                    source_rect = QRectF(pane.x() * sx, pane.y() * sy, pane.width() * sx, pane.height() * sy)
                    paint_liquid_glass(painter, pane, radius=20, backdrop=self._background_frame,
                                       backdrop_rect=source_rect, refractive=True,
                                       cache_token=(id(self), "login-pane"))
                    if self._expanded > .85:
                        if self._account is not None:
                            avatar, name_rect, _action = self._account_content_rects()
                            source = next(item for item in ENTRY_SOURCES if item.source_id == self._account.source_id)
                            painter.setPen(QPen(QColor(source.color), 6))
                            painter.setBrush(QColor("#343740"))
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
                            painter.setFont(_entry_font(18 if self._compact_login_layout() else 22))
                            painter.setPen(QColor("#f4f5f8"))
                            name = painter.fontMetrics().elidedText(self._account.nickname or "已登录账号", Qt.TextElideMode.ElideRight, round(name_rect.width()))
                            painter.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter, name)
                            painter.setFont(_entry_font(13))
                            painter.setPen(QColor("#a3a7b3"))
                            painter.drawText(QRectF(name_rect.x(), name_rect.bottom() + 4, name_rect.width(), 24),
                                             Qt.AlignmentFlag.AlignVCenter, f"{source.name} · 已连接")
                            return
                        qr, detail = self._login_content_rects()
                        source = next((item for item in ENTRY_SOURCES if item.source_id == self.selected_source), ENTRY_SOURCES[0])
                        if not self._qr_image.isNull():
                            # Keep service pixels exact and high contrast. The
                            # glass material surrounds, never blurs, the QR.
                            painter.setPen(QPen(QColor(255, 255, 255, 190), 1))
                            painter.setBrush(QColor("#ffffff"))
                            painter.drawRoundedRect(qr, 16, 16)
                            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                            painter.drawImage(qr.adjusted(12, 12, -12, -12), self._qr_image)
                        else:
                            # A failure must not keep pretending it is waiting
                            # for a live QR. Match the actual backend state.
                            painter.setPen(QPen(QColor(255, 255, 255, 45), 1))
                            painter.setBrush(QColor(255, 255, 255, 9))
                            painter.drawRoundedRect(qr, 16, 16)
                            icon = QRectF(qr.center().x() - 22, qr.center().y() - 46, 44, 44)
                            paint_music_source_icon(painter, icon, source.source_id)
                            painter.setFont(_entry_font(14))
                            painter.setPen(QColor("#c5cad5"))
                            painter.drawText(QRectF(qr.x() + 10, qr.center().y() + 12, qr.width() - 20, 48),
                                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                                             self._login_placeholder_text())
                        title_rect, helper_rect, _message_rect, _action = self._login_detail_geometry()
                        painter.setFont(_entry_font(18 if self._compact_login_layout() else 20))
                        painter.setPen(QColor("#f4f5f8"))
                        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter,
                                         f"连接{source.name}")
                        painter.setFont(_entry_font(12 if self._compact_login_layout() else 13))
                        painter.setPen(QColor("#b8bdc7"))
                        if self.login_state in {"starting", "loading"}:
                            helper = "正在确认账号与登录服务。\n你也可以选择其他平台。"
                        elif self.login_state == "scanned":
                            helper = "已扫码，在手机上确认后\n即可进入音乐库。"
                        elif self._qr_image.isNull():
                            helper = "请重试连接，或在上方\n选择其他音乐来源。"
                        else:
                            helper = f"打开{source.name} App\n扫描左侧二维码登录"
                        painter.drawText(helper_rect,
                                         Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap, helper)
