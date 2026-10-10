"""Account music library: immutable provider data in, user requests out.

Artwork and catalog retrieval belong to the controller. This native window
shares the island's presentation glyphs and never creates account or song data.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QAbstractButton, QLineEdit, QPushButton, QScrollArea, QWidget

from api.online_music import AccountSnapshot, OnlineSong
from local.branding import APP_NAME
from local.island_stack_visual import IslandStackVisual
from local.music_mode_controls import paint_presentation_glyph
from local.music_source_icons import music_source_icon_image, paint_music_source_icon
from local.playback_entry_window import ENTRY_SOURCES, MusicSourceButton, _entry_font


@dataclass(frozen=True)
class LibraryCollection:
    key: str
    kind: str
    title: str
    artwork_url: str
    playlist_id: str = ""
    song: OnlineSong | None = None


def _mix(compact: float, expanded: float, fraction: float) -> float:
    return compact + (expanded - compact) * fraction


def _paint_crop(painter: QPainter, rect: QRectF, image: QImage):
    """Fill the artwork slot with a centered crop, preserving aspect ratio."""
    side = min(image.width(), image.height())
    source = QRectF((image.width() - side) / 2, (image.height() - side) / 2, side, side)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(rect, image, source)


class _LibraryActionButton(QAbstractButton):
    def __init__(self, kind: str, label: str, parent: QWidget):
        super().__init__(parent)
        self.kind = kind
        self.setAccessibleName(label)
        self.setToolTip(label)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#c80d0d") if self.kind == "logout" else QColor("#767676"))
            painter.drawRoundedRect(rect, 6, 6)
            paint_music_source_icon(painter, rect.adjusted(4, 4, -4, -4), self.kind,
                                     tint=QColor("white") if self.kind == "switch" else None)
            if self.hasFocus():
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("white"), 1.5, Qt.PenStyle.DotLine))
                painter.drawRoundedRect(rect, 6, 6)


class _ModeButton(QAbstractButton):
    def __init__(self, mode: str, parent: QWidget):
        super().__init__(parent)
        self.mode = mode
        self.setAccessibleName({"fullscreen": "全屏播放", "window": "窗口播放", "island": "岛模式播放"}[mode])
        self.setToolTip(self.accessibleName())
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect()).adjusted(3, 3, -3, -3)
            painter.setPen(QPen(QColor("#a8a8a8"), 1.3) if self.hasFocus() else Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#4d4d4d") if self.underMouse() else QColor("#373737"))
            painter.drawEllipse(rect)
            side = rect.width() * .70
            glyph = QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2, side, side)
            paint_presentation_glyph(painter, glyph, "windowed" if self.mode == "window" else self.mode,
                                     QColor("#ffffff"))


class _CollectionCover(QAbstractButton):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.image = QImage()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(160)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._set_hover)

    def _set_hover(self, value):
        self._hover = float(value)
        self.update()

    def _retarget(self, target):
        self._animation.stop()
        self._animation.setStartValue(self._hover)
        self._animation.setEndValue(float(target))
        self._animation.start()

    def enterEvent(self, event):
        self._retarget(1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._retarget(0)
        super().leaveEvent(event)

    def artwork_rect(self) -> QRectF:
        inset = 5.0 * (1.0 - self._hover)
        return QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = self.artwork_rect()
            clip = QPainterPath()
            clip.addRoundedRect(rect, 14, 14)
            painter.setClipPath(clip)
            painter.fillRect(rect, QColor("#252525"))
            if not self.image.isNull():
                _paint_crop(painter, rect, self.image)
            painter.setClipping(False)
            painter.setPen(QPen(QColor("#717171") if self.hasFocus() else QColor("#343434"), 1.4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 14, 14)

    def hideEvent(self, event):
        self._animation.stop()
        self._hover = 0.0
        super().hideEvent(event)


class _CollectionRow(QWidget):
    activated = Signal(object, str, object)

    def __init__(self, collection: LibraryCollection, parent: QWidget):
        super().__init__(parent)
        self.collection = collection
        self.fraction = 1.0
        self.cover = _CollectionCover(self)
        self.cover.setAccessibleName(f"播放{collection.title}")
        self.cover.setToolTip(collection.title)
        self.cover.clicked.connect(lambda: self._activate("default"))
        self.mode_buttons = {mode: _ModeButton(mode, self) for mode in ("fullscreen", "window", "island")}
        for mode, button in self.mode_buttons.items():
            button.clicked.connect(lambda checked=False, target=mode: self._activate(target))

    def _activate(self, mode):
        rect = self.cover.artwork_rect().toAlignedRect()
        global_rect = QRect(self.cover.mapToGlobal(rect.topLeft()), rect.size())
        self.activated.emit(self.collection, mode, global_rect)

    def set_fraction(self, fraction: float):
        self.fraction = fraction
        self._layout()
        self.update()

    def _layout(self):
        f = self.fraction
        side = round(_mix(144, 128, f))
        left = round(_mix(15, 11, f))
        self.cover.setGeometry(left, 4, side, side)
        text_x = left + side + 24
        font = _entry_font(24)
        title_width = min(200, QFontMetricsF(font).horizontalAdvance(self.collection.title) + 8)
        self._title_rect = QRectF(text_x, _mix(17, 34, f), max(50, self.width() - text_x - 14), 40)
        small_x = min(text_x + title_width + 12, self.width() - 132)
        group_x = round(_mix(text_x - 10, small_x, f))
        group_y = round(_mix(72, 36, f))
        group_width = round(_mix(185, 122, f))
        group_height = round(_mix(62, 42, f))
        self._group_rect = QRectF(group_x, group_y, group_width, group_height)
        button_side = round(_mix(54, 35, f))
        gap = (group_width - button_side * 3) / 4
        for index, button in enumerate(self.mode_buttons.values()):
            button.setGeometry(round(group_x + gap + index * (button_side + gap)),
                               round(group_y + (group_height - button_side) / 2), button_side, button_side)
        # Leave space for compact controls without allowing a long title to
        # paint through the inline mode group in the expanded reference.
        self._title_rect.setWidth(_mix(self.width() - text_x - 16, max(42, small_x - text_x - 8), f))

    def resizeEvent(self, event):
        self._layout()
        super().resizeEvent(event)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#575757"))
            painter.drawRoundedRect(self._group_rect, self._group_rect.height() / 2, self._group_rect.height() / 2)
            painter.setFont(_entry_font(24))
            painter.setPen(QColor("#f8f8f8"))
            title = painter.fontMetrics().elidedText(self.collection.title, Qt.TextElideMode.ElideRight,
                                                     round(self._title_rect.width()))
            painter.drawText(self._title_rect, Qt.AlignmentFlag.AlignVCenter, title)
            if self.collection.song is not None and self.fraction < .45:
                painter.setFont(_entry_font(13))
                painter.setPen(QColor("#aaaaaa"))
                painter.drawText(QRectF(self._title_rect.x(), 51, self._title_rect.width(), 21),
                                 Qt.AlignmentFlag.AlignVCenter,
                                 painter.fontMetrics().elidedText(self.collection.song.artist,
                                                                 Qt.TextElideMode.ElideRight,
                                                                 round(self._title_rect.width())))


class MusicLibraryWindow(QWidget):
    sourceSwitchRequested = Signal()
    logoutRequested = Signal()
    searchRequested = Signal(str)
    moreRequested = Signal()
    artworkNeeded = Signal(str, str)
    collectionRequested = Signal(object, str, object)
    closed = Signal()

    ARTWORK_MAX_ENTRIES = 48
    TOOLBAR_ANIMATION_MS = 360

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        IslandStackVisual.load_embedded_lyric_fonts()
        self.setWindowTitle(f"{APP_NAME} · 音乐库")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(520, 360)
        self.resize(660, 600)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._account: AccountSnapshot | None = None
        self._catalog: tuple[LibraryCollection, ...] = ()
        self._results: tuple[LibraryCollection, ...] = ()
        self._rows: list[_CollectionRow] = []
        self._artwork: OrderedDict[str, QImage] = OrderedDict()
        self._artwork_pending: set[str] = set()
        self._expanded = 1.0
        self._toolbar_target = 1.0
        self._loading = False
        self._message = ""
        self._error = ""
        self._opening = False
        self._has_more = False
        self._more_requested = False
        self._drag_origin: QPoint | None = None
        self._toolbar = QVariantAnimation(self)
        self._toolbar.setDuration(self.TOOLBAR_ANIMATION_MS)
        self._toolbar.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._toolbar.valueChanged.connect(self._set_expanded)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(350)
        self._search_timer.timeout.connect(self._submit_search)
        source = ENTRY_SOURCES[0]
        self.source_button = MusicSourceButton(source, self)
        self.source_button.setCheckable(False)
        self.source_button.clicked.connect(self.sourceSwitchRequested)
        self.switch_button = _LibraryActionButton("switch", "切换音乐平台", self)
        self.switch_button.clicked.connect(self.sourceSwitchRequested)
        self.logout_button = _LibraryActionButton("logout", "退出当前平台账号", self)
        self.logout_button.clicked.connect(self.logoutRequested)
        self.close_button = QPushButton("×", self)
        self.close_button.setAccessibleName("关闭音乐库")
        self.close_button.setStyleSheet("QPushButton { color: #aaa; border: none; background: transparent; font-size: 24px; } QPushButton:hover { color: white; }")
        self.close_button.clicked.connect(self.close)
        self.search = QLineEdit(self)
        self.search.setAccessibleName("搜索当前平台的歌曲与歌单")
        self.search.setPlaceholderText("搜索歌曲与歌单")
        self.search.setFont(_entry_font(16))
        search_icon = QPixmap.fromImage(music_source_icon_image("search", QSize(68, 68)))
        search_icon.setDevicePixelRatio(2.0)
        self.search_button = QPushButton(self.search)
        self.search_button.setIcon(QIcon(search_icon))
        self.search_button.setIconSize(QSize(34, 34))
        self.search_button.setAccessibleName("搜索歌曲与歌单")
        self.search_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_button.setStyleSheet("QPushButton { border: none; background: transparent; } QPushButton:focus { border: 1px dotted white; border-radius: 15px; }")
        self.search_button.clicked.connect(self._submit_search)
        self.search.installEventFilter(self)
        self.search.textChanged.connect(self._search_changed)
        self.search.returnPressed.connect(self._submit_search)
        self.scroll = QScrollArea(self)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setWidgetResizable(False)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }")
        self.content = QWidget()
        self.content.setStyleSheet("background: transparent;")
        self.scroll.setWidget(self.content)
        self.scroll.viewport().setMouseTracking(True)
        self.scroll.viewport().installEventFilter(self)
        self.scroll.verticalScrollBar().valueChanged.connect(self._scroll_changed)
        self._layout()

    @property
    def collections(self) -> tuple[LibraryCollection, ...]:
        return self._results if self.search.text().strip() else self._catalog

    def set_account(self, account: AccountSnapshot):
        if not isinstance(account, AccountSnapshot):
            raise TypeError("Music library requires an AccountSnapshot")
        if self._account is None or self._account.source_id != account.source_id or self._account.user_id != account.user_id:
            self._artwork.clear()
            self._artwork_pending.clear()
            self._catalog = ()
            self._results = ()
            self._search_timer.stop()
            self._has_more = False
            self._more_requested = False
            blocked = self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(blocked)
            self._rebuild_rows()
        self._account = account
        source = next((item for item in ENTRY_SOURCES if item.source_id == account.source_id), ENTRY_SOURCES[0])
        self.source_button.source = source
        self.source_button.setText(source.name)
        self.source_button.setAccessibleName(source.name)
        self.source_button.setToolTip(source.name)
        self.source_button.update()
        self.update()

    def set_collections(self, collections: tuple[LibraryCollection, ...]):
        self._catalog = tuple(collections)
        if not all(isinstance(item, LibraryCollection) for item in self._catalog):
            raise TypeError("Music library collections must be immutable LibraryCollection values")
        if not self.search.text().strip():
            self._rebuild_rows(preserve_scroll=True)

    def set_search_results(self, collections: tuple[LibraryCollection, ...]):
        self._results = tuple(collections)
        if not all(isinstance(item, LibraryCollection) for item in self._results):
            raise TypeError("Search results must be immutable LibraryCollection values")
        if self.search.text().strip():
            self._rebuild_rows(preserve_scroll=True)

    def _rebuild_rows(self, *, preserve_scroll: bool = False):
        previous_scroll = self.scroll.verticalScrollBar().value() if preserve_scroll else 0
        for row in self._rows:
            row.hide()
            row.setParent(None)
            row.deleteLater()
        self._rows = []
        for collection in self.collections:
            row = _CollectionRow(collection, self.content)
            row.activated.connect(self._collection_activated)
            row.cover.image = self._artwork.get(collection.key, QImage())
            row.setEnabled(not self._opening)
            row.show()
            self._rows.append(row)
        self._layout()
        self.scroll.verticalScrollBar().setValue(previous_scroll)
        self._request_visible_artwork()
        self.update()

    def set_has_more(self, has_more: bool):
        self._has_more = bool(has_more)
        self._more_requested = False

    def _scroll_changed(self, value):
        self._request_visible_artwork()
        bar = self.scroll.verticalScrollBar()
        if (self._has_more and not self._more_requested and not self._loading and
                bar.maximum() > 0 and value >= bar.maximum() - 160):
            self._more_requested = True
            self.moreRequested.emit()

    def _request_visible_artwork(self):
        if not self.isVisible():
            return
        requested = []
        if self._account is not None and self._account.avatar_url:
            requested.append(("account", self._account.avatar_url))
        visible = QRect(0, self.scroll.verticalScrollBar().value() - 160,
                        self.content.width(), self.scroll.viewport().height() + 320)
        for row in self._rows:
            if visible.intersects(row.geometry()) and row.collection.artwork_url:
                requested.append((row.collection.key, row.collection.artwork_url))
        for key, url in requested:
            image = self._artwork.get(key)
            if image is not None and not image.isNull():
                self._artwork.move_to_end(key)
            elif key not in self._artwork_pending:
                self._artwork_pending.add(key)
                self.artworkNeeded.emit(key, url)

    def set_loading(self, loading: bool, message: str = ""):
        self._loading = bool(loading)
        self._message = str(message)
        if loading:
            self._error = ""
        self.update()

    def set_error(self, message: str):
        self._loading = False
        self._error = str(message)
        self.update()

    def set_artwork(self, key: str, image: QImage):
        known = {item.key for item in self._catalog + self._results}
        if not isinstance(image, QImage) or (key != "account" and key not in known):
            return
        self._artwork_pending.discard(key)
        artwork = image.scaled(256, 256, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation) if not image.isNull() else QImage()
        self._artwork[key] = artwork
        self._artwork.move_to_end(key)
        while len(self._artwork) > self.ARTWORK_MAX_ENTRIES:
            # Account has its own visible slot and must survive cover churn.
            oldest = next((item for item in self._artwork if item != "account"), None)
            if oldest is None:
                break
            self._artwork.pop(oldest)
            for row in self._rows:
                if row.collection.key == oldest:
                    row.cover.image = QImage()
                    row.cover.update()
        for row in self._rows:
            if row.collection.key == key:
                row.cover.image = artwork
                row.cover.update()
        self.update()

    def _search_changed(self, text):
        self._search_timer.stop()
        self._has_more = False
        self._more_requested = False
        self._error = ""
        self._results = ()
        self._rebuild_rows()
        if text.strip():
            self._search_timer.start()
        else:
            self.searchRequested.emit("")

    def _submit_search(self):
        self._search_timer.stop()
        self.searchRequested.emit(self.search.text().strip())

    def _collection_activated(self, collection, mode, rect):
        if self._opening:
            return
        self._opening = True
        for row in self._rows:
            row.setEnabled(False)
        self.collectionRequested.emit(collection, mode, rect)

    def finish_open_transition(self):
        self._opening = False
        for row in self._rows:
            row.setEnabled(True)

    def set_toolbar_expanded(self, expanded: bool):
        target = float(bool(expanded))
        if target == self._toolbar_target:
            return
        self._toolbar_target = target
        self._toolbar.stop()
        self._toolbar.setStartValue(self._expanded)
        self._toolbar.setEndValue(target)
        self._toolbar.start()

    def _set_expanded(self, value):
        self._expanded = float(value)
        self._layout()
        self._request_visible_artwork()
        self.update()

    def _layout(self):
        f = self._expanded
        header_height = round(_mix(100, 156, f))
        self.close_button.setGeometry(self.width() - 34, 5, 28, 30)
        for button in (self.source_button, self.switch_button, self.logout_button):
            button.setVisible(f > .48)
        self.source_button.setGeometry(164, 3, 52, 52)
        self.switch_button.setGeometry(229, 9, 41, 41)
        self.logout_button.setGeometry(273, 9, 41, 41)
        search_x = round(_mix(105, 154, f))
        search_y = round(_mix(65, 68, f))
        search_height = round(_mix(14, 51, f))
        self.search.setGeometry(search_x, search_y, min(405, self.width() - search_x - 42), search_height)
        self.search.setStyleSheet(f"QLineEdit {{ color: white; background: #575757; border: none; border-radius: {search_height // 2}px; padding-left: 17px; padding-right: {48 if f > .7 else 12}px; }} QLineEdit:focus {{ border: 1px solid #aaa; }}")
        self.search.setPlaceholderText("搜索歌曲与歌单" if f > .7 else "")
        self.search_button.setVisible(f > .7)
        self.search_button.setGeometry(self.search.width() - 46, 4, 40, max(1, search_height - 8))
        self.scroll.setGeometry(0, header_height, self.width(), max(1, self.height() - header_height - 8))
        row_height = round(_mix(156, 142, f))
        self.content.resize(self.scroll.width(), max(1, len(self._rows) * row_height + 10))
        for index, row in enumerate(self._rows):
            row.setGeometry(0, index * row_height + 4, self.content.width(), row_height)
            row.set_fraction(f)

    def eventFilter(self, watched, event):
        if watched is self.search and event.type() in {QEvent.Type.FocusIn, QEvent.Type.MouseButtonPress, QEvent.Type.Enter}:
            self.set_toolbar_expanded(True)
        if watched is self.scroll.viewport():
            if event.type() == QEvent.Type.Wheel:
                delta = event.angleDelta().y()
                if delta < 0:
                    self.set_toolbar_expanded(False)
                elif delta > 0:
                    self.set_toolbar_expanded(True)
            elif event.type() == QEvent.Type.MouseMove and event.position().y() < 20:
                self.set_toolbar_expanded(True)
        return super().eventFilter(watched, event)

    def mouseMoveEvent(self, event):
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
        elif event.position().y() < 88:
            self.set_toolbar_expanded(True)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 60:
            handle = self.windowHandle()
            if handle is None or not handle.startSystemMove():
                self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_origin = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self.search.text():
                self.search.clear()
            else:
                self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        if hasattr(self, "scroll"):
            self._layout()
            self._request_visible_artwork()
        super().resizeEvent(event)

    def hideEvent(self, event):
        self._toolbar.stop()
        self._search_timer.stop()
        self._artwork_pending.clear()
        self.finish_open_transition()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self._request_visible_artwork()
        if self.search.text().strip() and not self._results:
            self._search_timer.start()

    def closeEvent(self, event):
        self._toolbar.stop()
        self._search_timer.stop()
        self.finish_open_transition()
        self.closed.emit()
        super().closeEvent(event)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            panel = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#000000"))
            painter.drawRoundedRect(panel, 24, 24)
            f = self._expanded
            if f > .48:
                painter.setBrush(QColor("#f4f4f4"))
                painter.drawRoundedRect(QRectF(172, 7, 151, 52), 8, 8)
            side = _mix(74, 120, f)
            avatar = QRectF(_mix(20, 14, f), _mix(11, 7, f), side, side)
            painter.setBrush(QColor("#242424"))
            painter.setPen(QPen(QColor("#424242"), 2))
            painter.drawEllipse(avatar)
            image = self._artwork.get("account", QImage())
            if not image.isNull():
                painter.save()
                clip = QPainterPath()
                clip.addEllipse(avatar)
                painter.setClipPath(clip)
                _paint_crop(painter, avatar, image)
                painter.restore()
            if f < .9:
                painter.setOpacity(1.0 - f)
                painter.setFont(_entry_font(18))
                painter.setPen(QColor("#eeeeee"))
                name = self._account.nickname if self._account else ""
                name_rect = QRectF(112, 24, max(1, self.width() - 162), 32)
                painter.drawText(name_rect, Qt.AlignmentFlag.AlignVCenter,
                                 painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight,
                                                                 round(name_rect.width())))
                painter.setOpacity(1.0)
            if not self._rows:
                painter.setFont(_entry_font(17))
                painter.setPen(QColor("#b8b8b8"))
                message = self._error or (self._message or "正在加载音乐库…" if self._loading else
                                         ("未找到歌曲或歌单" if self.search.text().strip() else "暂无可显示的歌单"))
                painter.drawText(QRectF(24, self.scroll.y() + 55, self.width() - 48, 80),
                                 Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, message)
            elif self._error:
                painter.setFont(_entry_font(13))
                painter.setPen(QColor("#e4b5ad"))
                painter.drawText(QRectF(24, self.height() - 31, self.width() - 48, 25),
                                 Qt.AlignmentFlag.AlignCenter, self._error)
