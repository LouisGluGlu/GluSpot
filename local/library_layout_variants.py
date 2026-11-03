"""Optional library presentations; catalog, playback and search stay with the owner.

The classic window never uses these geometry/paint branches. Collection widgets
remain in the owner's rows/shelves so artwork, paging and navigation share the
same controller contracts in every presentation.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QWidget

from local.liquid_glass import GlassPushButton, paint_liquid_glass, widget_glass_backdrop, widget_glass_level
from local.artwork_skeleton import paint_artwork_skeleton
from local.music_library_search import LibraryProgress
from local.music_source_icons import paint_music_source_icon
from local.playback_entry_window import MusicSourceButton, _entry_font

LIBRARY_VARIANTS = frozenset({"classic", "sidebar", "listen", "compact"})
SECTIONS = {"for_you": "为你精选", "recommended": "推荐歌单", "personal": "个人歌单", "search": "搜索"}


def _glass(painter, widget, rect, token, radius=18, *, active=False, opacity=.68):
    rect = QRectF(rect)
    backdrop, source = widget_glass_backdrop(widget, rect)
    paint_liquid_glass(painter, rect, radius=radius, backdrop=backdrop, backdrop_rect=source,
                       refractive=True, light=True, opacity=opacity, active=active,
                       material_level=widget_glass_level(widget), cache_token=(id(widget), token))


def _text(painter, rect, text, size=13, color="#f3f4f7", *, bold=False, align=Qt.AlignmentFlag.AlignVCenter):
    painter.setFont(_entry_font(size, heavy=bold))
    painter.setPen(QColor(color))
    painter.drawText(QRectF(rect), align, painter.fontMetrics().elidedText(
        text, Qt.TextElideMode.ElideRight, max(1, round(rect.width()))))


def _section_glyph(p, rect, key):
    if key == "search":
        paint_music_source_icon(p, rect, "search", tint=QColor("#eef1f6"))
        return
    if key == "for_you":
        paint_music_source_icon(p, rect, "heart_outline", tint=QColor("#eef1f6"))
        return
    if key == "personal":
        paint_music_source_icon(p, rect, "library", tint=QColor("#eef1f6"))
        return
    p.save()
    p.setPen(QPen(QColor("#eef1f6"), 1.4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for x in (rect.x() + 2, rect.x() + 13):
        for y in (rect.y() + 2, rect.y() + 13):
            p.drawRoundedRect(QRectF(x, y, 7, 7), 2, 2)
    p.restore()


class _SectionButton(QAbstractButton):
    def __init__(self, key, view):
        super().__init__(view.owner)
        self.key, self.view = key, view
        self.icons_only = False
        self.setCheckable(True)
        self.setAccessibleName(SECTIONS[key])
        self.setToolTip(SECTIONS[key])
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(lambda: view.select_section(key))

    def paintEvent(self, event):
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            face = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            if self.isChecked() or self.underMouse() or self.hasFocus():
                _glass(p, self, face, "selection", 13, active=self.isChecked(), opacity=.85)
            icon = QRectF((self.width() - 24) / 2 if self.icons_only else 12, (self.height() - 24) / 2, 24, 24)
            _section_glyph(p, icon, self.key)
            if not self.icons_only:
                _text(p, QRectF(46, 0, self.width() - 50, self.height()), SECTIONS[self.key], 13,
                      bold=self.isChecked())
            if self.isChecked() and self.icons_only:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#d8e9f0"))
                p.drawEllipse(QRectF(self.width() / 2 - 2, self.height() - 7, 4, 4))


class _VariantRow(QWidget):
    """A square card or slim row, retaining the real cover/navigation target."""
    def __init__(self, collection, owner, *, card=False):
        super().__init__(owner.content)
        from local.music_library_window import _CollectionCover
        self.collection, self.owner, self.card = collection, owner, card
        self.cover = _CollectionCover(owner.source_button.source.source_id, collection.kind, self)
        self.cover.loading = bool(collection.artwork_url) or collection.kind in {"daily", "fm"}
        self.cover.image = owner._artwork.get(collection.key, self.cover.image)
        self.cover.setAccessibleName(f"播放{collection.title}")
        self.cover.setToolTip(collection.title)
        self.cover.clicked.connect(self.activate)
        self.queue_button = GlassPushButton("＋", self)
        self.queue_button.setProperty("glassRefractive", True)
        self.queue_button.setProperty("glassRadius", 12.)
        self.queue_button.setGlassLight(True)
        self.queue_button.setAccessibleName(f"将{collection.title}加入下一首播放")
        self.queue_button.setToolTip("下一首播放")
        self.queue_button.setStyleSheet("color:white;background:transparent;border:0;font-size:22px;")
        self.queue_button.clicked.connect(lambda: owner.queueNextRequested.emit(collection))
        self.queue_button.setVisible(collection.song is not None)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setEnabled(not owner._opening)

    def activate(self):
        rect = self.cover.artwork_rect().toAlignedRect()
        self.owner._collection_activated(self.collection, "default", QRect(self.cover.mapToGlobal(rect.topLeft()), rect.size()))

    def resizeEvent(self, event):
        side = self.width() if self.card else 54
        self.cover.setGeometry(0, 0 if self.card else 5, side, side)
        self.queue_button.setGeometry(self.width() - 44, (self.height() - 44) // 2, 44, 44)
        super().resizeEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.activate()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            x = 5 if self.card else 68
            y = self.width() + 2 if self.card else 8
            width = self.width() - x - (48 if self.collection.song else 8)
            _text(p, QRectF(x, y, width, 23), self.collection.title, 13, bold=self.card)
            subtitle = self.collection.song.artist if self.collection.song else {
                "daily": "每日更新 · 为你而选", "fm": "随心发现下一首", "playlist": "歌单 · 精选音乐"
            }.get(self.collection.kind, "音乐")
            _text(p, QRectF(x, y + 23, width, 19), subtitle, 10, "#adb8c8")
            if not self.card:
                p.setPen(QPen(QColor(255, 255, 255, 18), .7))
                p.drawLine(68, self.height() - 1, self.width() - 8, self.height() - 1)


class LibraryVariantPresentation:
    def __init__(self, owner):
        self.owner = owner
        self.section = "for_you"
        self.last_catalog_section = "for_you"
        self.buttons = {key: _SectionButton(key, self) for key in SECTIONS}
        self.rail_source = MusicSourceButton(owner.source_button.source, owner)
        self.rail_source.setCheckable(False)
        self.rail_source.clicked.connect(owner.sourceSwitchRequested)
        self.play_button = GlassPushButton("继续收听", owner)
        self.play_button.setAccessibleName("打开当前歌曲播放窗口")
        self.play_button.setProperty("glassRefractive", True)
        self.play_button.setProperty("glassRadius", 14.)
        self.play_button.setGlassLight(True)
        self.play_button.setStyleSheet("color:#f4f6fb;background:transparent;border:none;")
        self.play_button.setFont(_entry_font(13))
        self.play_button.clicked.connect(owner.nowPlayingRequested)
        self.headings = []
        self.search_rect = QRect()
        self.rail = self.pane = self.player = self.cluster = QRectF()
        self.set_visible(False)

    def set_visible(self, visible):
        for button in (*self.buttons.values(), self.rail_source, self.play_button):
            button.setVisible(visible)
        if not visible:
            for button in (self.owner.empty_action, self.owner.recovery_action):
                button.setGlassLight(False)

    def select_section(self, key):
        self.section = key
        o = self.owner
        if key == "search":
            if o.library_variant == "compact":
                o._rebuild_rows()
            else:
                o._layout()
            o.search.set_expanded(True, focus=True)
            o.search.settle()
            return
        self.last_catalog_section = key
        if o.search.text().strip():
            # Back is the controller's cancellation/clear authority.
            o.backRequested.emit()
        if o.library_variant == "compact":
            o._rebuild_rows()
        else:
            o._layout()
            for title, rect, group in self.headings:
                if group == key:
                    o.scroll.verticalScrollBar().setValue(round(rect.y()))
                    break
        o.update()

    def restore_catalog_section(self):
        if self.section == "search":
            self.section = self.last_catalog_section

    def rebuild(self):
        o = self.owner
        if o.library_variant == "listen" and not o.search.text().strip():
            from local.music_library_window import _CollectionShelf
            for key, items in o._catalog_groups().items():
                shelf = _CollectionShelf(key, SECTIONS[key], items, o)
                shelf.setEnabled(not o._opening)
                shelf.show()
                o._shelves.append(shelf)
            return
        values = o.collections
        if o.library_variant == "compact" and not o.search.text().strip():
            values = o._catalog_groups().get(self.section, ()) if self.section != "search" else ()
        for item in values:
            row = _VariantRow(item, o, card=o.library_variant == "sidebar" and not o.search.text().strip())
            row.show()
            o._rows.append(row)

    def layout_header(self):
        o = self.owner
        o.account_name_label.hide()
        o._account_avatar_rect = QRectF()
        o.now_playing_button.setVisible(o._now_playing is not None)
        if self.player.isEmpty():
            return
        if o.library_variant == "listen":
            side = 144 if o.width() >= 720 else 100
            x, y = self.player.x() + 18, self.player.y() + 18
        else:
            side = 48 if o.library_variant == "sidebar" else 32
            x, y = self.player.x() + 12, self.player.y() + 10
        o.now_playing_button.setGeometry(round(x), round(y), side, side)

    def layout_search(self):
        o = self.owner
        o.search.setGeometry(self.search_rect)
        o.search_button.setGeometry(max(0, self.search_rect.width() - 44), 0, 44, 44)
        query = bool(o.search.text().strip())
        o.search.setTextMargins(14, 0, 86 if query else 44, 0)
        o.clear_search_button.setGeometry(max(0, self.search_rect.width() - 84), 0, 40, 44)
        o.clear_search_button.setVisible(query and o.search.isVisible())
        o._clear_search_opacity.setOpacity(1.)
        o.progress_button.hide()

    def layout(self):
        o, variant = self.owner, self.owner.library_variant
        w, h = o.width(), o.height()
        wide = w >= 720
        query = bool(o.search.text().strip())
        self.cluster = QRectF(w - 164, 16, 148, 48)
        for button, x in ((o.source_button, w - 162), (o.logout_button, w - 114), (o.close_button, w - 66)):
            button.setGeometry(x, 18, 44, 44)
            button.show()
        o.source_button.login_style = "sidebar-icons"
        account = o._account
        if not o.source_button.source.login_supported:
            status = "upcoming"
        elif account and account.state in {"error", "expired"}:
            status = "error"
        else:
            status = "connected" if account and account.logged_in else "signed_out"
        o.source_button.set_connection_status(status)
        o.source_button.setToolTip(o.source_button.toolTip() + " · 切换平台")
        self.rail_source.source = o.source_button.source
        self.rail_source.login_style = "sidebar" if wide else "sidebar-icons"
        self.rail_source.set_connection_status(status)
        o.back_button.hide()
        o.switch_button.hide()
        o.expand_button.hide()
        self.rail_source.setVisible(variant == "sidebar")
        self.play_button.setVisible(variant == "listen" and o._now_playing is not None)
        for key, button in self.buttons.items():
            button.setVisible(variant in {"sidebar", "compact"})
            button.setChecked(key == ("search" if query else self.section))
            button.icons_only = not wide
        if variant == "sidebar":
            rail_w = 184 if wide else 60
            self.rail = QRectF(16, 82, rail_w, h - 98)
            left = 216 if wide else 88
            self.pane = QRectF(left, 82, w - left - 16, h - 180)
            self.player = QRectF(left, h - 88, w - left - 16, 72)
            for index, button in enumerate(self.buttons.values()):
                button.setGeometry(24, 100 + index * 50, rail_w - 16, 44)
            self.rail_source.setGeometry(24, h - 72, rail_w - 16, 44)
            searching = query or self.section == "search"
            self.search_rect = QRect(left + 12, 126, w - left - 40, 44)
            top = 180 if searching else 126
            o.search.setVisible(searching)
            scroll_rect = QRect(left + 8, top, w - left - 32, max(1, round(self.pane.bottom()) - top - 8))
        elif variant == "listen":
            self.rail = QRectF()
            self.search_rect = QRect(24, 78, w - 48, 44)
            o.search.show()
            self.player = QRectF(16, 136, w - 32, 198 if wide else 178)
            self.pane = QRectF(16, self.player.bottom() + 12, w - 32, h - self.player.bottom() - 28)
            scroll_rect = self.pane.adjusted(4, 2, -4, -4).toAlignedRect()
            self.play_button.setGeometry(round(self.player.x()) + (184 if wide else 132),
                                         round(self.player.y()) + (104 if wide else 80), 116, 38)
        else:
            self.rail = QRectF(16, 80, w - 32, 48)
            slot = (w - 40) / 4
            for index, button in enumerate(self.buttons.values()):
                button.setGeometry(round(20 + index * slot), 82, round(slot), 44)
            searching = query or self.section == "search"
            self.search_rect = QRect(24, 138, w - 48, 44)
            o.search.setVisible(searching)
            self.player = QRectF(16, 192 if searching else 138, w - 32, 62)
            self.pane = QRectF(16, self.player.bottom() + 16, w - 32, h - self.player.bottom() - 32)
            scroll_rect = self.pane.adjusted(8, 38, -8, -8).toAlignedRect()
        o.search.setPlaceholderText("搜索歌曲、歌手与歌单")
        o.search.setAccessibleDescription("搜索当前平台；Ctrl+F 搜索，Esc 返回音乐库")
        if not o.search.expanded:
            o.search.set_expanded(True)
            o.search.settle()
        self.layout_header()
        self.layout_search()
        o.scroll.setGeometry(scroll_rect)
        self.headings = []
        # Always reserve the scrollbar gutter: avoids a layout/scroll feedback loop.
        cw = max(1, o.scroll.width() - 14)
        top = 4
        if variant == "listen" and not query:
            for shelf in o._shelves:
                shelf.setGeometry(0, top, cw, 224)
                top += 228
        elif variant == "sidebar" and not query:
            columns = max(2, min(4, cw // 150))
            gap = 10
            side = max(40, (cw - 16 - gap * (columns - 1)) // columns)
            for key, items in o._catalog_groups().items():
                self.headings.append((SECTIONS[key], QRectF(10, top, cw - 20, 30), key))
                top += 34
                rows = [row for row in o._rows if row.collection in items]
                for index, row in enumerate(rows):
                    row.setGeometry(8 + index % columns * (side + gap), top + index // columns * (side + 52), side, side + 48)
                top += math.ceil(len(rows) / columns) * (side + 52) + 14
        else:
            for row in o._rows:
                row.setGeometry(4, top, cw - 8, 66)
                top += 66
        has_items = bool(o._visual_items())
        skeleton = bool((o._catalog_loading if not query else "search" in o._catalog_loading) or (o._loading and not has_items))
        o._catalog_skeleton.setVisible(skeleton)
        skeleton_height = 216
        if variant == "sidebar":
            columns = max(2, min(4, cw // 150))
            skeleton_height = max(216, (cw - 16 - 10 * (columns - 1)) // columns + 72)
        o._catalog_skeleton.setGeometry(0, top, cw, skeleton_height)
        if skeleton:
            top += skeleton_height
        o.more_button.setVisible(o._has_more and not skeleton)
        o.more_button.setGeometry(max(0, (cw - 172) // 2), top + 6, min(172, cw), 42)
        if o._has_more:
            top += 56
        o.content.resize(cw, max(1, top))
        ready = not has_items and not skeleton and not o._loading
        o.empty_action.setVisible(ready and not o._error and o.search.isEnabled())
        o.recovery_action.setVisible(ready and bool(o._error))
        o.empty_action.setText("修改搜索词" if query else "搜索歌曲")
        o.recovery_action.setText("重新搜索" if query else "切换平台")
        for button in (o.empty_action, o.recovery_action):
            button.setGlassLight(True)
            button.setGeometry(scroll_rect.x() + (scroll_rect.width() - 148) // 2,
                               scroll_rect.y() + max(70, (scroll_rect.height() - 20) // 2), 148, 40)
            button.raise_()
        o._sync_artwork_skeleton()

    def _progress(self, p, rect, *, times=True):
        position, duration = LibraryProgress._times(self.owner._now_playing)
        fraction = position / duration if duration else 0.
        p.setPen(QPen(QColor(255, 255, 255, 40), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(rect.topLeft(), rect.topRight())
        p.setPen(QPen(QColor("#e7edf6"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(rect.topLeft(), rect.topLeft() + (rect.topRight() - rect.topLeft()) * fraction)
        if times:
            clock = lambda value: f"{int(value)//60}:{int(value)%60:02}"
            _text(p, QRectF(rect.x(), rect.y() + 5, rect.width(), 14), clock(position), 9, "#bec8d7")
            _text(p, QRectF(rect.x(), rect.y() + 5, rect.width(), 14), clock(duration), 9, "#bec8d7",
                  align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def paint_skeleton(self, p, widget):
        seconds, ink = time.monotonic(), QColor("white")
        if self.owner.library_variant == "compact":
            for y in range(8, widget.height() - 40, 66):
                for rect, radius in ((QRectF(8, y, 44, 44), 10),
                                     (QRectF(68, y + 5, min(160, widget.width() - 88), 12), 6),
                                     (QRectF(68, y + 28, min(100, widget.width() - 88), 9), 4)):
                    paint_artwork_skeleton(p, rect, ink, seconds, radius=radius)
            return
        if self.owner.library_variant == "listen":
            paint_artwork_skeleton(p, QRectF(10, 8, 112, 13), ink, seconds, radius=6)
            for x in range(8, widget.width(), 156):
                paint_artwork_skeleton(p, QRectF(x, 40, 132, 132), ink, seconds, radius=14)
                paint_artwork_skeleton(p, QRectF(x + 4, 183, 100, 10), ink, seconds, radius=5)
            return
        columns = max(2, min(4, widget.width() // 150))
        side = max(40, (widget.width() - 16 - 10 * (columns - 1)) // columns)
        paint_artwork_skeleton(p, QRectF(10, 8, 112, 13), ink, seconds, radius=6)
        for index in range(columns):
            x = 8 + index * (side + 10)
            paint_artwork_skeleton(p, QRectF(x, 40, side, side), ink, seconds, radius=14)
            paint_artwork_skeleton(p, QRectF(x + 4, side + 51, min(100, side - 8), 10), ink, seconds, radius=5)

    def paint(self, p):
        o, variant = self.owner, self.owner.library_variant
        wide = o.width() >= 720
        _glass(p, o, self.cluster, "utilities", 17)
        _text(p, QRectF(24, 17, o.width() - 196, 32), "音乐库", 24, bold=True)
        name = o._account.nickname if o._account and o._account.logged_in else "发现你的下一首喜欢"
        if o._notice:
            name = o._notice
        _text(p, QRectF(25, 50, o.width() - 196, 20), name, 11, "#b5c1cf")
        if not self.rail.isEmpty():
            _glass(p, o, self.rail, "rail", 20)
        _glass(p, o, self.pane, "content", 22, opacity=.48)
        if variant == "sidebar":
            _text(p, QRectF(self.pane.x() + 18, 94, self.pane.width() - 36, 26),
                  "搜索音乐" if o.search.isVisible() else "为好音乐留个位置", 17, bold=True)
            if wide:
                _text(p, QRectF(36, o.height() - 120, 144, 22), name, 13, bold=True)
                _text(p, QRectF(36, o.height() - 98, 144, 18), "你的音乐，随时相伴", 10, "#afbac8")
        elif variant == "compact":
            title = "搜索结果" if o.search.text().strip() else SECTIONS[self.section]
            _text(p, QRectF(36, self.pane.y() + 8, self.pane.width() - 40, 26), title, 15, bold=True)
        _glass(p, o, self.player, "now-playing", 20, opacity=.72)
        track = o._now_playing
        title = track.title if track else "从喜欢的音乐开始"
        artist = track.artist if track else "选择歌单，开始收听"
        if variant == "listen":
            x = self.player.x() + (184 if wide else 132)
            if track is None:
                cover = QRectF(self.player.x() + 18, self.player.y() + 18, 144 if wide else 100, 144 if wide else 100)
                _glass(p, o, cover, "hero-placeholder", 16)
                paint_music_source_icon(p, cover.adjusted(30, 30, -30, -30), o.source_button.source.source_id)
            _text(p, QRectF(x, self.player.y() + 14, self.player.right() - x - 18, 20), "正在播放" if track else "现在就听", 10, "#c3d1df")
            _text(p, QRectF(x, self.player.y() + 39, self.player.right() - x - 18, 33), title, 27 if wide else 21, bold=True)
            _text(p, QRectF(x, self.player.y() + (76 if wide else 65), self.player.right() - x - 18, 23), artist, 13, "#c0c9d7")
            self._progress(p, QRectF(self.player.x() + 22, self.player.bottom() - 32, self.player.width() - 44, 3))
        else:
            x = self.player.x() + (72 if variant == "sidebar" and track else 54 if track else 16)
            _text(p, QRectF(x, self.player.y() + 8, self.player.right() - x - 16, 22), title, 13, bold=True)
            if variant == "sidebar":
                _text(p, QRectF(x, self.player.y() + 29, self.player.right() - x - 16, 17), artist, 10, "#b4c1d1")
                self._progress(p, QRectF(x, self.player.bottom() - 18, self.player.right() - x - 16, 3), times=False)
            else:
                _text(p, QRectF(x, self.player.y() + 30, self.player.right() - x - 16, 17), artist, 10, "#b4c1d1")
                self._progress(p, QRectF(self.player.x() + 14, self.player.bottom() - 2, self.player.width() - 28, 3), times=False)
        if not o._visual_items() and not o._catalog_skeleton.isVisible():
            rect = QRectF(o.scroll.geometry())
            message = o._error or ("未找到歌曲或歌单" if o.search.text().strip() else
                                  "暂无可显示的歌单" if o._account and o._account.logged_in else "连接账号，收藏你的音乐")
            _text(p, QRectF(rect.x() + 8, rect.y() + 16, rect.width() - 16, 30), message, 15,
                  align=Qt.AlignmentFlag.AlignCenter)
            _text(p, QRectF(rect.x() + 8, rect.y() + 48, rect.width() - 16, 20),
                  "试试搜索喜欢的歌曲与歌手", 11, "#b4c0ce", align=Qt.AlignmentFlag.AlignCenter)
        elif o._error:
            _text(p, QRectF(o.scroll.x() + 12, o.height() - 22, o.scroll.width() - 24, 18), o._error, 10, "#e8b5a6")
        # Headings are content children in spirit, painted here with scroll clipping.
        if self.headings:
            p.save()
            p.setClipRect(o.scroll.geometry())
            for title, rect, _key in self.headings:
                rect = rect.translated(o.scroll.x(), o.scroll.y() - o.scroll.verticalScrollBar().value())
                _text(p, rect, title, 15, bold=True)
            p.restore()
