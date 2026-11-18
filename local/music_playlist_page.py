"""A playlist's own page: a large cover with its details, then every song.

Opened from any personal or recommended playlist (and the daily mix) in the
music library; the personal radio has no list and keeps playing directly.
Each song row offers three actions:

* play this: the page's list is inserted after the sounding song and starts
  at this row; when the list ends the earlier queue carries on;
* play next: the existing "下一首播放";
* add to a playlist.

Clicking a row's cover, or double-clicking the row, plays only that song now
and then returns to the earlier queue (插播).

The page paints its rows itself (one widget, no per-row children) so a long
list scrolls without creating hundreds of widgets.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QVariantAnimation, QEasingCurve, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QScrollArea, QWidget, QFrame

from local.liquid_glass import GlassPushButton, paint_liquid_glass, widget_glass_backdrop, widget_glass_level
from local.music_source_icons import paint_music_source_icon
from local.playback_entry_window import _entry_font

HEADER_COVER = 148
HEADER_TOP = 52
ROW_HEIGHT = 58
ROW_COVER = 42
ACTION_SIZE = 30
ACTIONS = ("play", "next", "add")
ACTION_TIPS = {"play": "播放这首（按本列表播放，播完回到原播放列表）",
               "next": "下一首播放", "add": "加入歌单"}
KIND_LABELS = {"daily": "每日推荐", "playlist": "歌单", "album": "专辑"}
SHRINK_MS = 320


def _ink(widget) -> tuple[QColor, QColor, bool]:
    from local.theme_tokens import hex_rgb
    from local.ui_theme import active_tokens
    tokens = active_tokens(widget)
    if tokens.dark and tokens.shade >= 0.999:
        return QColor("#f3f4f7"), QColor("#adb8c8"), True
    return QColor(hex_rgb(tokens.text)), QColor(hex_rgb(tokens.muted)), tokens.dark


def _clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _total(seconds: float) -> str:
    minutes = max(0, int(round(seconds / 60.0)))
    if minutes >= 60:
        return f"{minutes // 60} 小时 {minutes % 60} 分钟"
    return f"{minutes} 分钟"


def _crop(painter: QPainter, rect: QRectF, image: QImage, radius: float) -> None:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.save()
    painter.setClipPath(path)
    side = min(image.width(), image.height())
    source = QRectF((image.width() - side) / 2, (image.height() - side) / 2, side, side)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(rect, image, source)
    painter.restore()


def _placeholder(painter: QPainter, rect: QRectF, radius: float, dark: bool) -> None:
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255, 22) if dark else QColor(0, 0, 0, 18))
    painter.drawRoundedRect(rect, radius, radius)
    painter.restore()


def _play_glyph(painter: QPainter, rect: QRectF, color: QColor) -> None:
    c = rect.center()
    s = min(rect.width(), rect.height()) * 0.30
    path = QPainterPath()
    path.moveTo(c + QPointF(-s * 0.75, -s))
    path.lineTo(c + QPointF(s, 0))
    path.lineTo(c + QPointF(-s * 0.75, s))
    path.closeSubpath()
    painter.fillPath(path, color)


class _Canvas(QWidget):
    """Header and rows of one playlist, painted together."""

    def __init__(self, page: "PlaylistPage"):
        super().__init__(page.scroll)
        self.page = page
        self.setMouseTracking(True)
        self._hover_row = -1
        self._hover_action = ""
        self._pressed = None

    # --- geometry ---------------------------------------------------------
    def header_top(self) -> int:
        # With the toolbar row the back key no longer floats over the header.
        return 6 if self.page.crumb else HEADER_TOP

    def header_height(self) -> int:
        return self.header_top() + HEADER_COVER + 24

    def cover_rect(self) -> QRectF:
        return QRectF(20, self.header_top(), HEADER_COVER, HEADER_COVER)

    def play_all_rect(self) -> QRectF:
        cover = self.cover_rect()
        return QRectF(cover.right() + 20, cover.bottom() - 38, 116, 38)

    def sync_rect(self) -> QRectF:
        """Playlist sync, beside 播放全部, only on the user's own playlists."""
        play = self.play_all_rect()
        return QRectF(play.right() + 10, play.top(), 92, play.height())

    def row_rect(self, index: int) -> QRectF:
        return QRectF(8, self.header_height() + index * ROW_HEIGHT, max(1, self.width() - 16), ROW_HEIGHT)

    def row_cover_rect(self, index: int) -> QRectF:
        row = self.row_rect(index)
        return QRectF(row.x() + 40, row.y() + (ROW_HEIGHT - ROW_COVER) / 2, ROW_COVER, ROW_COVER)

    def action_rect(self, index: int, action: str) -> QRectF:
        row = self.row_rect(index)
        order = ACTIONS.index(action)
        right = row.right() - 10 - (len(ACTIONS) - 1 - order) * (ACTION_SIZE + 6)
        return QRectF(right - ACTION_SIZE, row.y() + (ROW_HEIGHT - ACTION_SIZE) / 2, ACTION_SIZE, ACTION_SIZE)

    def content_height(self) -> int:
        rows = max(1, len(self.page.rows))
        return self.header_height() + rows * ROW_HEIGHT + 24

    def hit(self, point: QPointF):
        if self.play_all_rect().contains(point) and self.page.collection is not None:
            return ("play_all", -1)
        if self.page.can_sync() and self.sync_rect().contains(point):
            return ("sync", -1)
        top = self.header_height()
        if point.y() < top:
            return None
        index = int((point.y() - top) // ROW_HEIGHT)
        if not 0 <= index < len(self.page.rows):
            return None
        for action in ACTIONS:
            if self.action_rect(index, action).adjusted(-3, -3, 3, 3).contains(point):
                return (action, index)
        if self.row_cover_rect(index).adjusted(-4, -4, 4, 4).contains(point):
            return ("cover", index)
        return ("row", index)

    # --- input ------------------------------------------------------------
    def mouseMoveEvent(self, event):
        found = self.hit(event.position())
        row = found[1] if found else -1
        action = found[0] if found and found[0] in ACTIONS + ("play_all", "cover", "sync") else ""
        if (row, action) != (self._hover_row, self._hover_action):
            self._hover_row, self._hover_action = row, action
            tip = ACTION_TIPS.get(action) or ("播放全部" if action == "play_all" else
                                              "把这个歌单同步到另一个平台" if action == "sync" else
                                              "立即播放这首，之后回到原播放列表" if action == "cover" else "")
            self.setToolTip(tip)
            self.setCursor(Qt.CursorShape.PointingHandCursor if action else Qt.CursorShape.ArrowCursor)
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover_row, self._hover_action = -1, ""
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = self.hit(event.position())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        pressed, self._pressed = self._pressed, None
        if event.button() != Qt.MouseButton.LeftButton or pressed is None:
            return super().mouseReleaseEvent(event)
        if self.hit(event.position()) != pressed:
            return super().mouseReleaseEvent(event)
        kind, index = pressed
        if kind == "play_all":
            self.page.request_play_all(self.global_rect(self.cover_rect()))
        elif kind == "sync":
            self.page.syncRequested.emit(self.page.collection)
        elif kind == "cover":
            self.page.request_song(index, "single", self.global_rect(self.row_cover_rect(index)))
        elif kind == "play":
            self.page.request_song(index, "list", self.global_rect(self.row_cover_rect(index)))
        elif kind == "next":
            self.page.queueNextRequested.emit(self.page.rows[index])
        elif kind == "add":
            self.page.addRequested.emit(self.page.rows[index],
                                        self.mapToGlobal(self.action_rect(index, "add").bottomLeft().toPoint()))
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        found = self.hit(event.position())
        if event.button() == Qt.MouseButton.LeftButton and found is not None and found[0] == "row":
            index = found[1]
            self.page.request_song(index, "single", self.global_rect(self.row_cover_rect(index)))
            return
        super().mouseDoubleClickEvent(event)

    def global_rect(self, rect: QRectF) -> QRect:
        aligned = rect.toAlignedRect()
        return QRect(self.mapToGlobal(aligned.topLeft()), aligned.size())

    # --- paint ------------------------------------------------------------
    def paintEvent(self, event):
        page = self.page
        ink, muted, dark = _ink(self)
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            scale, shift, opacity = page.shrink_transform_values()
            if scale < 1.0:
                # Shrink toward the right edge, about the middle of what shows.
                middle = page.scroll.verticalScrollBar().value() + page.scroll.viewport().height() / 2.0
                p.translate(self.width(), middle)
                p.scale(scale, scale)
                p.translate(-self.width(), -middle)
                p.setOpacity(opacity)
            self._paint_header(p, ink, muted, dark)
            visible = event.rect()
            first = max(0, int((visible.top() - self.header_height()) // ROW_HEIGHT))
            last = min(len(page.rows), int((visible.bottom() - self.header_height()) // ROW_HEIGHT) + 2)
            if not page.rows:
                message = page.error or ("正在载入歌曲…" if page.loading else "这个歌单还没有歌曲")
                font = _entry_font(13)
                p.setFont(font)
                p.setPen(muted)
                p.drawText(QRectF(0, self.header_height() + 12, self.width(), 40), Qt.AlignmentFlag.AlignCenter, message)
            for index in range(first, last):
                self._paint_row(p, index, ink, muted, dark)
            if page.loading and page.rows:
                p.setFont(_entry_font(12))
                p.setPen(muted)
                p.drawText(QRectF(0, self.row_rect(len(page.rows)).y() - 4, self.width(), 24),
                           Qt.AlignmentFlag.AlignCenter, "正在载入更多…")

    def _paint_header(self, p: QPainter, ink: QColor, muted: QColor, dark: bool):
        page = self.page
        cover = self.cover_rect()
        image = page.artwork(page.collection.key) if page.collection is not None else QImage()
        if image is not None and not image.isNull():
            _crop(p, cover, image, 16)
        else:
            _placeholder(p, cover, 16, dark)
        if page.collection is None:
            return
        left = cover.right() + 20
        width = max(1, self.width() - left - 20)
        font = _entry_font(12)
        p.setFont(font)
        p.setPen(muted)
        p.drawText(QRectF(left, cover.top() + 2, width, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   KIND_LABELS.get(page.collection.kind, "歌单"))
        title_font = _entry_font(22)
        title_font.setWeight(QFont.Weight.Bold)
        p.setFont(title_font)
        p.setPen(ink)
        title_rect = QRectF(left, cover.top() + 24, width, 60)
        p.drawText(title_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                   p.fontMetrics().elidedText(page.collection.title, Qt.TextElideMode.ElideRight, round(width * 2 - 20)))
        p.setFont(_entry_font(12))
        p.setPen(muted)
        meta = page.summary()
        p.drawText(QRectF(left, cover.top() + 88, width, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(meta, Qt.TextElideMode.ElideRight, round(width)))
        if page.description:
            p.drawText(QRectF(left, cover.top() + 106, width, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(page.description, Qt.TextElideMode.ElideRight, round(width)))
        if page.collection is not None:
            # Playing everything needs no loaded rows: the player fetches the list.
            button = self.play_all_rect()
            backdrop, source = widget_glass_backdrop(self, button)
            paint_liquid_glass(p, button, radius=19, refractive=True, backdrop=backdrop, backdrop_rect=source,
                               hovered=self._hover_action == "play_all", light=not dark,
                               material_level=widget_glass_level(self), cache_token=(id(self), "play_all"))
            _play_glyph(p, QRectF(button.x() + 12, button.y() + 9, 20, 20), ink)
            label = _entry_font(13)
            label.setWeight(QFont.Weight.Bold)
            p.setFont(label)
            p.setPen(ink)
            p.drawText(button.adjusted(36, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "播放全部")
        if page.can_sync():
            button = self.sync_rect()
            backdrop, source = widget_glass_backdrop(self, button)
            paint_liquid_glass(p, button, radius=19, refractive=True, backdrop=backdrop, backdrop_rect=source,
                               hovered=self._hover_action == "sync", light=not dark,
                               material_level=widget_glass_level(self), cache_token=(id(self), "sync"))
            label = _entry_font(13)
            p.setFont(label)
            p.setPen(ink)
            p.drawText(button, Qt.AlignmentFlag.AlignCenter, "同步歌单")

    def _paint_row(self, p: QPainter, index: int, ink: QColor, muted: QColor, dark: bool):
        page = self.page
        item = page.rows[index]
        song = item.song
        row = self.row_rect(index)
        current = page.is_current(item)
        if index == self._hover_row or current:
            from local.row_states import paint_row_state
            paint_row_state(p, row.adjusted(0, 2, 0, -2), 12, dark=dark,
                            hovered=index == self._hover_row, selected=current)
        p.setFont(_entry_font(12))
        p.setPen(muted)
        p.drawText(QRectF(row.x(), row.y(), 34, row.height()), Qt.AlignmentFlag.AlignCenter, str(index + 1))
        cover = self.row_cover_rect(index)
        image = page.artwork(item.key)
        if image is not None and not image.isNull():
            _crop(p, cover, image, 8)
        else:
            _placeholder(p, cover, 8, dark)
            page.need_artwork(item)
        if index == self._hover_row and self._hover_action == "cover":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(10, 12, 18, 150))
            p.drawRoundedRect(cover, 8, 8)
            _play_glyph(p, cover, QColor("white"))
        text_left = cover.right() + 12
        show_actions = index == self._hover_row or self.width() >= 520
        # Without the buttons showing, the words take their room.
        right = (self.action_rect(index, ACTIONS[0]).left() if show_actions else row.right() - 4) - 52
        width = max(1, right - text_left)
        title = _entry_font(14)
        title.setWeight(QFont.Weight.Bold if current else QFont.Weight.Normal)
        p.setFont(title)
        p.setPen(ink)
        p.drawText(QRectF(text_left, row.y() + 9, width, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(item.title, Qt.TextElideMode.ElideRight, round(width)))
        p.setFont(_entry_font(12))
        p.setPen(muted)
        artist = song.artist if song is not None else ""
        p.drawText(QRectF(text_left, row.y() + 31, width, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(artist, Qt.TextElideMode.ElideRight, round(width)))
        if song is not None and song.duration_seconds:
            p.drawText(QRectF(right, row.y(), 46, row.height()), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       _clock(song.duration_seconds))
        if not show_actions:
            return
        for action in ACTIONS:
            rect = self.action_rect(index, action)
            hovered = index == self._hover_row and self._hover_action == action
            if hovered:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 40) if dark else QColor(0, 0, 0, 30))
                p.drawRoundedRect(rect, 9, 9)
            glyph = rect.adjusted(6, 6, -6, -6)
            tint = QColor(ink)
            if not hovered:
                tint.setAlphaF(0.78)
            if action == "play":
                _play_glyph(p, rect, tint)
            else:
                paint_music_source_icon(p, glyph, "queue_next" if action == "next" else "playlist_add", tint=tint)


class PlaylistPage(QWidget):
    """The page over the library's list area. The owner places and raises it."""

    backRequested = Signal()
    playAllRequested = Signal(object, object)            # collection, global cover rect
    songRequested = Signal(object, object, int, object, str)  # collection, rows, index, rect, "single"|"list"
    queueNextRequested = Signal(object)                  # song row
    addRequested = Signal(object, object)                # song row, global QPoint
    syncRequested = Signal(object)                       # collection: open playlist sync for it
    artworkNeeded = Signal(str, str)

    def __init__(self, parent: QWidget, artwork_lookup, current_lookup=None):
        super().__init__(parent)
        self._artwork_lookup = artwork_lookup
        self._current_lookup = current_lookup
        self.collection = None
        self.crumb = ""            # the section this page came from; "" = no toolbar row
        self.rows: tuple = ()
        self.loading = False
        self.error = ""
        self.description = ""
        self.sync_source = ""  # the library account's platform; set by the window
        self._asked: set[str] = set()
        self._shrink = 0.0
        self._shrink_animation = QVariantAnimation(self)
        self._shrink_animation.setDuration(SHRINK_MS)
        self._shrink_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._shrink_animation.valueChanged.connect(self._set_shrink)
        self._shrink_animation.finished.connect(self._shrink_finished)
        self._shrink_request = None
        self.scroll = QScrollArea(self)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(False)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # The library list's own thin scrollbar.
        self.scroll.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }"
            " QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 2px; }"
            " QScrollBar::handle:vertical { background: #444a56; border-radius: 3px; min-height: 28px; }"
            " QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            " QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }")
        self.scroll.viewport().setAutoFillBackground(False)
        self.canvas = _Canvas(self)
        self.scroll.setWidget(self.canvas)
        self.back_button = GlassPushButton("‹ 返回", self)
        self.back_button.setProperty("glassRefractive", True)
        self.back_button.setProperty("glassRadius", 14.)
        self.back_button.setFont(_entry_font(13))
        self.back_button.setAccessibleName("返回音乐库")
        self.back_button.clicked.connect(self.backRequested)
        self.hide()

    # --- data -------------------------------------------------------------
    def open(self, collection) -> None:
        self.collection = collection
        self.rows = ()
        self.loading = True
        self.error = ""
        self.description = ""
        self._asked.clear()
        self._shrink = 0.0
        self.scroll.verticalScrollBar().setValue(0)
        self._layout()
        self.show()
        self.raise_()
        self.update()

    def close_page(self) -> None:
        self._shrink_animation.stop()
        self._shrink = 0.0
        self._shrink_request = None
        self.collection = None
        self.rows = ()
        self.hide()

    def set_rows(self, collection, rows, *, loading=False, error="", description="") -> None:
        if self.collection is None or collection is None or collection.key != self.collection.key:
            return
        self.rows = tuple(rows)
        self.loading = bool(loading)
        self.error = str(error or "")
        if description:
            self.description = str(description)
        self._layout()
        self.canvas.update()

    def artwork(self, key: str):
        return self._artwork_lookup(key)

    def need_artwork(self, item) -> None:
        if item.artwork_url and item.key not in self._asked:
            self._asked.add(item.key)
            self.artworkNeeded.emit(item.key, item.artwork_url)

    def artwork_changed(self, key: str) -> None:
        # A failed cover is not asked for again from paint (that would loop
        # with the controller's backoff); reopening the page asks again.
        if self.isVisible():
            self.canvas.update()

    def is_current(self, item) -> bool:
        lookup = self._current_lookup
        try:
            return bool(callable(lookup) and lookup(item))
        except Exception:
            return False

    def can_sync(self) -> bool:
        """Only the user's own playlists can be a sync source."""
        collection = self.collection
        return bool(collection is not None and getattr(collection, "kind", "") == "playlist"
                    and getattr(collection, "group", "") == "personal" and getattr(collection, "playlist_id", "")
                    and getattr(self, "sync_source", "") in {"netease", "apple_music"})

    def summary(self) -> str:
        if self.loading and not self.rows:
            return "正在载入…"
        total = sum(float(getattr(row.song, "duration_seconds", 0) or 0) for row in self.rows)
        text = f"{len(self.rows)} 首歌曲"
        if total > 0:
            text += f" · {_total(total)}"
        return text

    # --- actions ----------------------------------------------------------
    def request_play_all(self, rect: QRect) -> None:
        if self.collection is not None:
            self.playAllRequested.emit(self.collection, rect)

    def request_song(self, index: int, how: str, rect: QRect) -> None:
        if self.collection is None or not 0 <= index < len(self.rows) or self._shrink_animation.state() == QVariantAnimation.State.Running:
            return
        if how == "list":
            # The list shrinks to the right, toward the playback window's own
            # list, before the window opens on it.
            self._shrink_request = (self.collection, self.rows, index, rect)
            self._shrink_animation.stop()
            self._shrink_animation.setStartValue(0.0)
            self._shrink_animation.setEndValue(1.0)
            self._shrink_animation.start()
            return
        self.songRequested.emit(self.collection, self.rows, index, rect, how)

    def _set_shrink(self, value) -> None:
        self._shrink = float(value)
        self.back_button.setVisible(self._shrink < 0.05)
        self.canvas.update()

    def _shrink_finished(self) -> None:
        request, self._shrink_request = self._shrink_request, None
        if request is not None:
            collection, rows, index, rect = request
            self.songRequested.emit(collection, rows, index, rect, "list")

    def reset_shrink(self) -> None:
        self._shrink_animation.stop()
        self._shrink = 0.0
        self.back_button.show()
        self.canvas.update()

    # --- layout/paint -----------------------------------------------------
    TOOLBAR = 50

    def set_crumb(self, crumb: str) -> None:
        """A toolbar row with a round back key and ``crumb``, or the old floating key."""
        crumb = str(crumb or "")
        if crumb != self.crumb:
            self.crumb = crumb
            self._layout()
            self.update()

    def _layout(self) -> None:
        ink, _muted, dark = _ink(self)
        if self.crumb:
            self.scroll.setGeometry(self.rect().adjusted(0, self.TOOLBAR, 0, 0))
            self.back_button.setText("‹")
            self.back_button.setFont(_entry_font(20))
            self.back_button.setProperty("glassRadius", 17.)
            self.back_button.setGeometry(14, 9, 34, 34)
        else:
            self.scroll.setGeometry(self.rect())
            self.back_button.setText("‹ 返回")
            self.back_button.setFont(_entry_font(13))
            self.back_button.setProperty("glassRadius", 14.)
            self.back_button.setGeometry(12, 8, 86, 34)
        width = max(1, self.scroll.viewport().width())
        self.canvas.resize(width, self.canvas.content_height())
        self.back_button.setGlassLight(not dark)
        self.back_button.setStyleSheet(f"color:{ink.name()};background:transparent;border:none;")
        self.back_button.raise_()

    def paintEvent(self, event):
        if not self.crumb:
            return
        ink, muted, _dark = _ink(self)
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            font = _entry_font(13)
            p.setFont(font)
            p.setPen(muted)
            p.drawText(QRectF(58, 9, self.width() - 72, 34), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       p.fontMetrics().elidedText(self.crumb, Qt.TextElideMode.ElideRight, max(1, self.width() - 72)))

    def resizeEvent(self, event):
        self._layout()
        super().resizeEvent(event)

    def shrink_transform_values(self) -> tuple[float, float, float]:
        """(scale, x shift, opacity) of the list for the current shrink."""
        f = self._shrink
        return 1.0 - 0.42 * f, self.width() * 0.42 * f, 1.0 - 0.85 * f
