"""Optional library presentations; catalog, playback and search stay with the owner.

The classic window never uses these geometry/paint branches. Collection widgets
remain in the owner's rows/shelves so artwork, paging and navigation share the
same controller contracts in every presentation.
"""
from __future__ import annotations

import math
import os
import time

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QGraphicsOpacityEffect, QLabel, QWidget

from local.liquid_glass import GlassPushButton, paint_liquid_glass, widget_glass_backdrop, widget_glass_level
from local.artwork_skeleton import paint_artwork_skeleton
from local.music_library_query import paint_highlighted_text
from local.music_library_search import LibraryProgress
from local.music_source_icons import paint_music_source_icon
from local.playback_entry_window import MusicSourceButton, _entry_font
from local.login_layout_variants import login_variant_layout

LIBRARY_VARIANTS = frozenset({"classic", "sidebar", "listen", "compact"})

# The 6.51 refresh of the sidebar library: a short header with only the
# window's close at the top right, account actions (platform, sign out) in
# the rail's footer, a raised content plane, denser rows with a hover plate.
# GLUSPOT_LIBRARY_REFRESH=0 starts on the earlier look; Ctrl+Shift+L flips it.
REFRESH_HEADER = 60
REFRESH_ROW, REFRESH_STRIDE, REFRESH_COVER = 60, 64, 48
RAIL_WIDE, RAIL_NARROW, RAIL_KEY = 156, 56, 38
SECTION_ORDER = ("for_you", "recommended", "personal")
PULL_TO_REFRESH = 360       # wheel units up past the top of a list that reload the section
REFRESH_MIN_S, REFRESH_MAX_S = 0.6, 4.0
APPEAR_S, APPEAR_STAGGER_S = 0.24, 0.045   # each new row fades in, one after another, in order
COVER_WAIT_S = 3.0          # a row waits at most this long for its cover before it shows
SCROLL_FADE = 22            # px at the list's top edge that fade out under the heading once scrolled
PLATE_MS = 260
# One radius family: the window 24; planes 18; keys, plates and rows 12.
RADIUS_WINDOW, RADIUS_PLANE, RADIUS_KEY = 24, 18, 12


BAR_SLACK = 24             # px a size must clear past a threshold before the bar changes pose


def bar_mode_for(width: int, height: int, previous: str | None = None) -> str:
    """The now-playing bar's pose for the window's shape: wide and tall keeps
    the expanded island, wide and short one row, narrow the free island.
    Near a threshold the previous pose holds, so a resize does not flicker."""
    def pose(w, h):
        if w >= 640:
            return "tall" if h >= 700 else "row"
        return "tall" if h >= 760 else "pill"
    chosen = pose(width, height)
    if previous and previous != chosen and previous in {
            pose(width + dx, height + dy) for dx in (-BAR_SLACK, 0, BAR_SLACK) for dy in (-BAR_SLACK, 0, BAR_SLACK)}:
        return previous
    return chosen


def _fine_glyph(p, rect, key, color):
    """Thin line icons for the rail: sparkle, grid, note list, magnifier."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(color, max(1.4, rect.width() * .075), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
               Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    r = QRectF(rect)
    c, u = r.center(), r.width() / 24.
    if key == "for_you":
        star = QPainterPath()
        for i, (dx, dy) in enumerate(((0, -9), (2.2, -2.2), (9, 0), (2.2, 2.2), (0, 9), (-2.2, 2.2), (-9, 0), (-2.2, -2.2))):
            point = QPointF(c.x() + dx * u - 1.5 * u, c.y() + dy * u + 1.5 * u)
            star.moveTo(point) if i == 0 else star.lineTo(point)
        star.closeSubpath()
        p.drawPath(star)
        small = QPainterPath()
        sc = QPointF(c.x() + 7 * u, c.y() - 7 * u)
        for i, (dx, dy) in enumerate(((0, -3), (.8, -.8), (3, 0), (.8, .8), (0, 3), (-.8, .8), (-3, 0), (-.8, -.8))):
            point = QPointF(sc.x() + dx * u, sc.y() + dy * u)
            small.moveTo(point) if i == 0 else small.lineTo(point)
        small.closeSubpath()
        p.drawPath(small)
    elif key == "recommended":
        side, gap = 7.5 * u, 2.5 * u
        for dx in (-1, 1):
            for dy in (-1, 1):
                x = c.x() + (gap / 2 if dx > 0 else -gap / 2 - side)
                y = c.y() + (gap / 2 if dy > 0 else -gap / 2 - side)
                p.drawRoundedRect(QRectF(x, y, side, side), 2.2 * u, 2.2 * u)
    elif key == "personal":
        for i, length in enumerate((10, 10, 6)):
            y = c.y() - 6 * u + i * 5 * u
            p.drawLine(QPointF(c.x() - 9 * u, y), QPointF(c.x() - 9 * u + length * u, y))
        stem_x = c.x() + 5 * u
        p.drawLine(QPointF(stem_x, c.y() + 6 * u), QPointF(stem_x, c.y() - 8 * u))
        p.drawLine(QPointF(stem_x, c.y() - 8 * u), QPointF(stem_x + 4.5 * u, c.y() - 6 * u))
        p.drawEllipse(QPointF(stem_x - 2.4 * u, c.y() + 6.5 * u), 2.6 * u, 2.2 * u)
    else:
        p.drawEllipse(QPointF(c.x() - 1.5 * u, c.y() - 1.5 * u), 6.5 * u, 6.5 * u)
        p.drawLine(QPointF(c.x() + 3.4 * u, c.y() + 3.4 * u), QPointF(c.x() + 8.5 * u, c.y() + 8.5 * u))
    p.restore()


def _type(painter, rect, text, size, color, weight=QFont.Weight.Normal, align=Qt.AlignmentFlag.AlignVCenter):
    font = _entry_font(size)
    font.setWeight(weight)
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(QRectF(rect), align, painter.fontMetrics().elidedText(
        text, Qt.TextElideMode.ElideRight, max(1, round(rect.width()))))


class _TopPull(QObject):
    """Wheel up past the top of a section's list: a loading capsule (three
    looping dots in a large rounded frame) grows in; past the threshold the
    section reloads."""

    def __init__(self, view):
        super().__init__(view.owner)
        self.view = view
        self.pull = 0
        self.reset = QTimer(self)
        self.reset.setSingleShot(True)
        self.reset.setInterval(600)
        self.reset.timeout.connect(self._clear)

    def _clear(self):
        self.pull = 0
        self.view.set_pull(0.0)

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel or not self.view.pull_active():
            return False
        bar = self.view.owner.scroll.verticalScrollBar()
        delta = event.angleDelta().y()
        if delta > 0 and bar.value() <= bar.minimum():
            self.pull += delta
            self.reset.start()
            if self.pull >= PULL_TO_REFRESH:
                self.pull = 0
                self.view.start_refresh()
            else:
                self.view.set_pull(self.pull / PULL_TO_REFRESH)
            return True
        if self.pull:
            self._clear()
        return False


def refresh_default() -> bool:
    return os.environ.get("GLUSPOT_LIBRARY_REFRESH", "1").strip().lower() not in {"0", "off", "false", "no"}


def refresh_tones(dark: bool) -> dict:
    """Planes of the refreshed library: page < rail < content, each a step apart."""
    if dark:
        return {"page": QColor(0, 0, 0), "sidebar": QColor(26, 26, 30), "content": QColor(17, 17, 21),
                "edge": QColor(255, 255, 255, 18), "hover": QColor(255, 255, 255, 16),
                "rule": QColor(255, 255, 255, 26), "muted": QColor(160, 166, 178)}
    return {"page": QColor(255, 255, 255), "sidebar": QColor(236, 236, 241), "content": QColor(247, 247, 250),
            "edge": QColor(0, 0, 0, 16), "hover": QColor(0, 0, 0, 12),
            "rule": QColor(0, 0, 0, 22), "muted": QColor(96, 100, 110)}
SECTIONS = {"for_you": "为你精选", "recommended": "推荐歌单", "personal": "个人歌单", "search": "搜索"}


def _glass(painter, widget, rect, token, radius=18, *, active=False, opacity=1.):
    from local.ui_theme import active_tokens
    rect = QRectF(rect)
    backdrop, source = widget_glass_backdrop(widget, rect)
    light = not active_tokens(widget).dark
    paint_liquid_glass(painter, rect, radius=radius, backdrop=backdrop, backdrop_rect=source,
                       refractive=True, light=light, opacity=opacity, active=active,
                       material_level=widget_glass_level(widget), cache_token=(id(widget), token))


def _text(painter, rect, text, size=13, color="#f3f4f7", *, bold=False, align=Qt.AlignmentFlag.AlignVCenter):
    from local.theme_tokens import hex_rgb
    from local.ui_theme import active_tokens
    device = painter.device()
    tokens = active_tokens(device if hasattr(device, "parentWidget") else None)
    if not (tokens.dark and tokens.shade >= 0.999):
        muted = {"#adb8c8", "#b5c1cf", "#c3d1df", "#c0c9d7", "#b4c1d1", "#bec8d7", "#aeb3c0"}
        if str(color).lower() in muted or str(color).lower() in {"#f3f4f7", "#f4f6fb", "#eef1f6", "#ffffff", "white"}:
            color = hex_rgb(tokens.muted if str(color).lower() in muted else tokens.text)
    font = _entry_font(size)
    font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(QRectF(rect), align, painter.fontMetrics().elidedText(
        text, Qt.TextElideMode.ElideRight, max(1, round(rect.width()))))


def _section_glyph(p, rect, key, widget=None):
    from local.theme_tokens import hex_rgb
    from local.ui_theme import active_tokens
    tokens = active_tokens(widget)
    tint = QColor("#eef1f6") if tokens.dark and tokens.shade >= 0.999 else QColor(hex_rgb(tokens.text))
    if key == "search":
        paint_music_source_icon(p, rect, "search", tint=tint)
        return
    if key == "for_you":
        paint_music_source_icon(p, rect, "heart_outline", tint=tint)
        return
    if key == "personal":
        paint_music_source_icon(p, rect, "library", tint=tint)
        return
    p.save()
    p.setPen(QPen(tint, 1.4))
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
        view = self.view
        if view.refresh and view.owner.library_variant == "sidebar":
            self._paint_refreshed()
            return
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            face = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            if self.isChecked() or self.underMouse() or self.hasFocus():
                _glass(p, self, face, "selection", 13, active=self.isChecked(), opacity=.85)
            icon = QRectF((self.width() - 24) / 2 if self.icons_only else 12, (self.height() - 24) / 2, 24, 24)
            _section_glyph(p, icon, self.key, self)
            if not self.icons_only:
                _text(p, QRectF(46, 0, self.width() - 50, self.height()), SECTIONS[self.key], 13,
                      bold=self.isChecked())
            if self.isChecked() and self.icons_only:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#d8e9f0"))
                p.drawEllipse(QRectF(self.width() / 2 - 2, self.height() - 7, 4, 4))

    def _paint_refreshed(self):
        from local.row_states import paint_row_state
        from local.ui_theme import active_tokens
        dark = active_tokens(self).dark
        ink = QColor(244, 245, 247) if dark else QColor(20, 21, 26)
        quiet = QColor(ink)
        quiet.setAlphaF(.62)
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            if not self.isChecked() and (self.underMouse() or self.hasFocus()):
                paint_row_state(p, QRectF(self.rect()).adjusted(1, 1, -1, -1), RADIUS_KEY, dark=dark, hovered=True)
            color = ink if self.isChecked() else quiet
            side = 20
            icon = QRectF((self.width() - side) / 2 if self.icons_only else 11, (self.height() - side) / 2, side, side)
            _fine_glyph(p, icon, self.key, color)
            if not self.icons_only:
                _type(p, QRectF(40, 0, self.width() - 44, self.height()), SECTIONS[self.key], 13, color,
                      QFont.Weight.DemiBold if self.isChecked() else QFont.Weight.Normal)


class _RowIconButton(QAbstractButton):
    """A small row action drawn in the theme's ink, plate only on hover.

    The old "＋" text button sat on a light glass plate with white text, so
    on the dark search page it showed as a blank white square.
    """

    def __init__(self, glyph, tip, parent):
        super().__init__(parent)
        self.glyph = glyph
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def paintEvent(self, event):
        from local.theme_tokens import hex_rgb
        from local.ui_theme import active_tokens
        tokens = active_tokens(self)
        dark = tokens.dark
        ink = QColor("#f4f5f7") if dark and tokens.shade >= 0.999 else QColor(hex_rgb(tokens.text))
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            face = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            if self.underMouse() or self.isDown() or self.hasFocus():
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 46 if self.isDown() else 32) if dark else QColor(0, 0, 0, 26))
                p.drawRoundedRect(face, 10, 10)
            else:
                ink.setAlphaF(0.82)
            glyph = face.adjusted(8, 8, -8, -8)
            if self.glyph == "play":
                from PySide6.QtGui import QPainterPath
                from PySide6.QtCore import QPointF
                c, s = glyph.center(), glyph.width() * 0.42
                path = QPainterPath()
                path.moveTo(c + QPointF(-s * 0.75, -s))
                path.lineTo(c + QPointF(s, 0))
                path.lineTo(c + QPointF(-s * 0.75, s))
                path.closeSubpath()
                p.fillPath(path, ink)
            else:
                paint_music_source_icon(p, glyph, "queue_next", tint=ink)


class _VariantRow(QWidget):
    """A square card or slim row, retaining the real cover/navigation target."""
    def __init__(self, collection, owner, *, card=False):
        super().__init__(owner.content)
        from local.music_library_window import _CollectionCover
        self.collection, self.owner, self.card = collection, owner, card
        self.cover = _CollectionCover(owner.source_button.source.source_id, collection.kind, self)
        self.cover.loading = bool(collection.artwork_url)
        self.cover.image = owner._artwork.get(collection.key, self.cover.image)
        self.cover.setAccessibleName(f"播放{collection.title}")
        is_song = collection.song is not None
        # A song row plays now and joins the current queue; a list
        # row opens its page.
        self.cover.setToolTip("立即播放这首，之后回到原播放列表" if is_song else collection.title)
        self.cover.clicked.connect(self.activate)
        self.queue_button = _RowIconButton("next", "下一首播放", self)
        self.queue_button.setAccessibleName(f"将{collection.title}加入下一首播放")
        self.queue_button.clicked.connect(lambda: owner.queueNextRequested.emit(self.collection))
        self.queue_button.setVisible(is_song)
        self.insert_button = _RowIconButton("play", "立即播放这首，之后回到原播放列表", self)
        self.insert_button.setAccessibleName(f"插播{collection.title}")
        self.insert_button.clicked.connect(self._insert)
        self.insert_button.setVisible(is_song)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setEnabled(not owner._opening)
        if owner.search.text().strip():
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _refreshed(self):
        view = getattr(self.owner, "_variant_view", None)
        return bool(view is not None and view.refresh and self.owner.library_variant == "sidebar")

    def _is_playing(self):
        song, track = self.collection.song, getattr(self.owner, "_now_playing", None)
        return bool(song is not None and track is not None
                    and getattr(track, "track_id", "") == f"owned:{song.source_id}:{song.song_id}")

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if self.owner._move_search_key(self, event):
            event.accept()
            return
        super().keyPressEvent(event)

    def activate(self):
        rect = self.cover.artwork_rect().toAlignedRect()
        self.owner._collection_activated(self.collection, "default", QRect(self.cover.mapToGlobal(rect.topLeft()), rect.size()))

    def _insert(self):
        if self.owner._opening or not self.owner.isVisible():
            return
        rect = self.cover.artwork_rect().toAlignedRect()
        self.owner.songInsertRequested.emit(self.collection, QRect(self.cover.mapToGlobal(rect.topLeft()), rect.size()))

    def _layout(self):
        if not self.card and self._refreshed():
            self.cover.setGeometry(6, (self.height() - REFRESH_COVER) // 2, REFRESH_COVER, REFRESH_COVER)
        else:
            side = self.width() if self.card else 54
            self.cover.setGeometry(0, 0 if self.card else 5, side, side)
        self.queue_button.setGeometry(self.width() - 40, (self.height() - 36) // 2, 36, 36)
        self.insert_button.setGeometry(self.width() - 80, (self.height() - 36) // 2, 36, 36)

    def resizeEvent(self, event):
        self._layout()
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
            refreshed = self._refreshed()
            if refreshed:
                from local.row_states import paint_row_state
                from local.ui_theme import active_tokens
                dark = active_tokens(self).dark
                tones = refresh_tones(dark)
                if not self.card:
                    x, y = 6 + REFRESH_COVER + 14, (self.height() - 42) // 2
                    paint_row_state(p, QRectF(self.rect()), RADIUS_KEY, dark=dark,
                                    hovered=self.underMouse() and self.isEnabled(), selected=self._is_playing())
            width = self.width() - x - (88 if self.collection.song else 8)
            query = getattr(self.owner, "_highlight_query", "")
            subtitle = self.collection.song.artist if self.collection.song else {
                "daily": "每日推荐", "fm": "私人漫游", "playlist": "歌单"
            }.get(self.collection.kind, "")
            if subtitle == self.collection.title:
                subtitle = ""
            if query:
                font = _entry_font(13)
                font.setWeight(QFont.Weight.Bold if self.card else QFont.Weight.Normal)
                p.setFont(font)
                paint_highlighted_text(p, QRectF(x, y, width, 23), self.collection.title, query,
                                       QColor("#f3f4f7"), QColor("#ffe7a3"))
                font = _entry_font(10)
                p.setFont(font)
                paint_highlighted_text(p, QRectF(x, y + 23, width, 19), subtitle, query,
                                       QColor("#adb8c8"), QColor("#ffe7a3"))
                if self.hasFocus():
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.setPen(QPen(QColor(255, 231, 163, 180), 1.2))
                    p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 12, 12)
            elif refreshed:
                ink = QColor(244, 245, 247) if dark else QColor(20, 21, 26)
                _type(p, QRectF(x, y, width, 22), self.collection.title, 14, ink,
                      QFont.Weight.Bold if self._is_playing() else QFont.Weight.DemiBold)
                _type(p, QRectF(x, y + 22, width, 20), subtitle, 11, tones["muted"])
            else:
                _text(p, QRectF(x, y, width, 23), self.collection.title, 13, bold=self.card)
                _text(p, QRectF(x, y + 23, width, 19), subtitle, 10, "#adb8c8")


class _PlayerTarget(QAbstractButton):
    """The mini bar shares the existing cover's playback-window action."""
    def paintEvent(self, event):
        if self.hasFocus():
            with QPainter(self) as p:
                p.setPen(QPen(QColor("#b6bac5"), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 18, 18)


class LibraryVariantPresentation:
    def __init__(self, owner):
        self.owner = owner
        self.refresh = refresh_default()
        self.plate = QRectF()
        self._plate_from = self._plate_to = QRectF()
        self._plate_motion = QVariantAnimation(owner)
        self._plate_motion.setDuration(PLATE_MS)
        self._plate_motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._plate_motion.setStartValue(0.)
        self._plate_motion.setEndValue(1.)
        self._plate_motion.valueChanged.connect(self._move_plate)
        self._pull = _TopPull(self)
        owner.scroll.viewport().installEventFilter(self._pull)
        self.pull_amount = 0.0
        self.refreshing_since = None
        self.dots_rect = QRectF()
        self._dots_timer = QTimer(owner)
        self._dots_timer.setInterval(33)
        self._dots_timer.timeout.connect(self._tick_dots)
        self._appear_at = {}                     # row key -> when its fade-in begins
        self._waiting_since = {}                 # row key -> since when it waits for its cover
        self._last_appear_at = 0.0
        self._shown_keys = {}                    # section -> keys that have appeared
        self._appear_timer = QTimer(owner)
        self._appear_timer.setInterval(16)
        self._appear_timer.timeout.connect(self._tick_appear)
        self.heading_rule = QRectF()
        owner.scroll.verticalScrollBar().valueChanged.connect(self._scrolled)
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
        self.play_button.setStyleSheet("color:#f4f5f7;background:transparent;border:none;")
        self.play_button.setFont(_entry_font(13))
        self.play_button.clicked.connect(owner.nowPlayingRequested)
        self.player_button = _PlayerTarget(owner)
        self.player_button.setAccessibleName("打开当前歌曲播放窗口")
        self.player_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.player_button.clicked.connect(owner.nowPlayingRequested)
        self.status_started_at = time.monotonic()
        self.status_timer = QTimer(owner)
        self.status_timer.setInterval(90)
        self.status_timer.timeout.connect(self.advance_status_pulse)
        self.headings = []
        self.search_rect = QRect()
        self.rail = self.pane = self.player = self.cluster = self.rule = QRectF()
        self.set_visible(False)

    # --- the sliding selection plate ---------------------------------------
    def _plate_target(self):
        for key, button in self.buttons.items():
            if button.isChecked() and button.isVisible():
                return QRectF(button.geometry())
        return QRectF()

    def _sync_plate(self, animate):
        target = self._plate_target()
        if target == self._plate_to and (self._plate_motion.state() == QVariantAnimation.State.Running or self.plate == target):
            return
        if not animate or self.plate.isEmpty() or target.isEmpty():
            self._plate_motion.stop()
            self.plate = self._plate_from = self._plate_to = target
            self.owner.update(self.rail.toAlignedRect().adjusted(-4, -4, 4, 4))
            return
        self._plate_from, self._plate_to = QRectF(self.plate), target
        self._plate_motion.stop()
        self._plate_motion.start()

    def _move_plate(self, value):
        t = float(value)
        a, b = self._plate_from, self._plate_to
        self.plate = QRectF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t,
                            a.width() + (b.width() - a.width()) * t, a.height() + (b.height() - a.height()) * t)
        self.owner.update(self.rail.toAlignedRect().adjusted(-4, -4, 4, 4))

    # --- turning to the next section past the end of a list ----------------
    def pull_active(self):
        o = self.owner
        return (self.refresh and o.library_variant == "sidebar" and not o.search.text().strip()
                and self.section in SECTION_ORDER and not getattr(o, "playlist_page_open", False))

    def set_pull(self, amount):
        if self.refreshing_since is not None:
            return
        self.pull_amount = max(0.0, min(1.0, float(amount)))
        if self.pull_amount > 0.0 and not self._dots_timer.isActive():
            self._dots_timer.start()
        self.owner.update(self.dots_rect.toAlignedRect().adjusted(-4, -4, 4, 4))

    # --- the heading stays a layer above the list ---------------------------
    def _heading_layered(self):
        o = self.owner
        return (self.refresh and o.library_variant == "sidebar" and o.isVisible()
                and o.scroll.verticalScrollBar().value() > 0)

    def _scrolled(self, _value=None):
        """Once the list is scrolled, it fades out under the heading and a
        hairline separates the two; at the top nothing changes."""
        o = self.owner
        layered = self._heading_layered()
        effect = o.scroll.graphicsEffect()
        mine = isinstance(effect, QGraphicsOpacityEffect) and getattr(effect, "_gluspot_heading", False)
        if layered and not mine:
            effect = QGraphicsOpacityEffect(o.scroll)
            effect._gluspot_heading = True
            gradient = QLinearGradient(0.0, 0.0, 0.0, float(SCROLL_FADE))
            gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
            gradient.setColorAt(1.0, QColor(0, 0, 0, 255))
            effect.setOpacityMask(QBrush(gradient))
            o.scroll.setGraphicsEffect(effect)
        elif not layered and mine:
            o.scroll.setGraphicsEffect(None)
        if not self.heading_rule.isEmpty():
            o.update(self.heading_rule.toAlignedRect().adjusted(-2, -2, 2, 2))

    def _section_loading(self):
        """The visible section is loading, or the whole library is and shows nothing yet."""
        o = self.owner
        if self.section in getattr(o, "_catalog_loading", ()):
            return True
        return bool(getattr(o, "_loading", False)) and not o._visual_items()

    def start_refresh(self):
        """Reload the section the listener pulled on; dots run until it lands."""
        if self.refreshing_since is not None:
            return
        self.refreshing_since = time.monotonic()
        self.pull_amount = 1.0
        if not self._dots_timer.isActive():
            self._dots_timer.start()
        signal = getattr(self.owner, "sectionRefreshRequested", None)
        if signal is not None:
            signal.emit(self.section)

    def _tick_dots(self):
        o = self.owner
        if self.refreshing_since is not None:
            elapsed = time.monotonic() - self.refreshing_since
            loading = self.section in getattr(o, "_catalog_loading", ())
            if elapsed >= REFRESH_MAX_S or (elapsed >= REFRESH_MIN_S and not loading):
                self.refreshing_since = None
                self.pull_amount = 0.0
        if self.pull_amount <= 0.0 and self.refreshing_since is None and not self._section_loading():
            self._dots_timer.stop()
        o.update(self.dots_rect.toAlignedRect().adjusted(-4, -4, 4, 4))

    def paint_dots(self, p, ink):
        """Three looping dots in a large rounded frame, faded in with the pull."""
        rect = self.dots_rect
        loading = self._section_loading()
        if rect.isEmpty() or (self.pull_amount <= 0.0 and self.refreshing_since is None and not loading):
            return
        p.save()
        p.setOpacity(p.opacity() * (1.0 if self.refreshing_since is not None or loading else self.pull_amount))
        _glass(p, self.owner, rect, "refresh-dots", rect.height() / 2, opacity=.9)
        frame = QColor(ink)
        frame.setAlpha(70)
        p.setPen(QPen(frame, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect.adjusted(.5, .5, -.5, -.5), rect.height() / 2, rect.height() / 2)
        now = time.monotonic()
        p.setPen(Qt.PenStyle.NoPen)
        for index in range(3):
            phase = math.sin(math.tau * (now * 1.4 - index * 0.18))
            dot = QColor(ink)
            dot.setAlphaF(0.30 + 0.70 * max(0.0, phase))
            p.setBrush(dot)
            centre = QPointF(rect.center().x() + (index - 1) * 12.0, rect.center().y() - 1.5 * max(0.0, phase))
            p.drawEllipse(centre, 3.2, 3.2)
        p.restore()

    # --- rows appear in order, each once its cover is in ------------------
    def _note_appearing(self, row, key, section, now, order):
        """A row new to this section waits, invisible, until its cover has
        arrived (or failed, or it has none); rows then appear strictly in
        order, one after another. A row seen before shows at once."""
        seen = self._shown_keys.setdefault(section, set())
        begin = self._appear_at.get(key)
        if key in seen and begin is None:
            return order
        effect = QGraphicsOpacityEffect(row)
        if begin is None:
            row.waiting_since = self._waiting_since.setdefault(key, now)
            effect.setOpacity(0.0)
        else:
            row.appear_at = begin
            effect.setOpacity(max(0.0, min(1.0, (now - begin) / APPEAR_S)))
        row.setGraphicsEffect(effect)
        if not self._appear_timer.isActive():
            self._appear_timer.start()
        return order + 1

    def _cover_ready(self, row, now):
        o, key = self.owner, row.collection.key
        cover = row.cover
        if not cover.loading or not cover.image.isNull() or key in getattr(o, "_artwork", {}):
            return True                       # no cover to wait for, or it came (or failed)
        if cover.visibleRegion().isEmpty():
            return True                       # off screen: its cover is only asked for when seen
        return now - getattr(row, "waiting_since", now) >= COVER_WAIT_S

    def _tick_appear(self):
        now, busy = time.monotonic(), False
        section = self.section
        seen = self._shown_keys.setdefault(section, set())
        last = getattr(self, "_last_appear_at", 0.0)
        blocked = False
        for row in list(self.owner._rows):
            key = row.collection.key
            if getattr(row, "waiting_since", None) is not None:
                if blocked or not self._cover_ready(row, now):
                    blocked, busy = True, True      # strict order: later rows wait too
                    continue
                row.waiting_since = None
                self._waiting_since.pop(key, None)
                begin = max(now, last + APPEAR_STAGGER_S)
                last = begin
                row.appear_at = self._appear_at[key] = begin
                seen.add(key)
            begin = getattr(row, "appear_at", None)
            if begin is None:
                continue
            effect = row.graphicsEffect()
            amount = (now - begin) / APPEAR_S
            if amount >= 1.0 or effect is None:
                row.setGraphicsEffect(None)
                row.appear_at = None
                self._appear_at.pop(key, None)
                continue
            effect.setOpacity(max(0.0, amount))
            busy = True
        self._last_appear_at = last
        if not busy:
            self._appear_timer.stop()

    def toggle_refresh(self):
        """Ctrl+Shift+L: the refreshed library or the earlier one, for a side-by-side look."""
        self.refresh = not self.refresh
        o = self.owner
        self.plate = QRectF()
        o._background_key = None
        if o._update_background():
            o.update()
        o._rebuild_rows(preserve_scroll=True)
        o.set_notice("新版音乐库" if self.refresh else "旧版音乐库")
        o.update()

    def set_visible(self, visible):
        for button in (self.owner.source_button, self.owner.logout_button, self.owner.close_button):
            button.setProperty("glassClusterMember", visible)
        for button in (*self.buttons.values(), self.rail_source, self.play_button, self.player_button):
            button.setVisible(visible)
        if not visible:
            self.status_timer.stop()
            for button in (self.owner.empty_action, self.owner.recovery_action):
                button.setGlassLight(False)

    def select_section(self, key):
        self.section = key
        o = self.owner
        for name, button in self.buttons.items():
            button.setChecked(name == key)
        self._sync_plate(True)
        # A playlist page covers the list; choosing a section leaves it.
        close_page = getattr(o, "close_playlist_page", None)
        if callable(close_page):
            close_page()
        if key == "search":
            if o.library_variant in {"sidebar", "compact"}:
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
        if o.library_variant in {"sidebar", "compact"}:
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
        if o.library_variant in {"sidebar", "compact"} and not o.search.text().strip():
            values = o._catalog_groups().get(self.section, ()) if self.section != "search" else ()
        appear = (self.refresh and o.library_variant == "sidebar" and not o.search.text().strip()
                  and o.isVisible() and not o.isMinimized())
        now, order = time.monotonic(), 0
        for item in values:
            row = _VariantRow(item, o, card=o.library_variant == "sidebar" and o.width() >= 720
                              and not o.search.text().strip())
            if appear:
                order = self._note_appearing(row, item.key, self.section, now, order)
            row.show()
            o._rows.append(row)

    def layout_header(self):
        o = self.owner
        o.account_name_label.hide()
        o._account_avatar_rect = QRectF()
        o.now_playing_button.setVisible(o._now_playing is not None)
        self.player_button.setGeometry(self.player.toAlignedRect())
        self.player_button.setVisible(o.library_variant == "sidebar" and o._now_playing is not None)
        if o.library_variant == "sidebar":
            self.player_button.hide()
            o.now_playing_button.hide()
            island = o.now_playing_island
            island.setFixedSize(self.player.size().toSize())
            island.move(self.player.topLeft().toPoint())
            island.raise_()
            return
        if self.player.isEmpty():
            return
        if o.library_variant == "listen":
            side = 144 if o.width() >= 720 else 100
            x, y = self.player.x() + 18, self.player.y() + 18
        else:
            side = 48 if o.library_variant == "sidebar" else 32
            x, y = self.player.x() + 12, self.player.y() + 10
        o.now_playing_button.setGeometry(round(x), round(y), side, side)
        o.now_playing_button.raise_()

    def connected_buttons(self):
        o = self.owner
        if not o.isVisible() or o.isMinimized() or o.library_variant == "classic":
            return ()
        return tuple(b for b in (o.source_button, self.rail_source)
                     if b.isVisible() and b.source_status == "connected" and not b.status_rect().isEmpty())

    def sync_status_pulse(self):
        if self.connected_buttons():
            if not self.status_timer.isActive():
                self.status_timer.start()
        else:
            self.status_timer.stop()

    def advance_status_pulse(self, now=None):
        buttons = self.connected_buttons()
        if not buttons:
            self.status_timer.stop()
            return
        now = time.monotonic() if now is None else now
        opacity = .55 + .45 * (.5 - .5 * math.cos(math.tau * (now - self.status_started_at) / 2.))
        for button in buttons:
            button.status_opacity = opacity
            button.update(button.status_rect())

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

    def _cards_are_wide(self):
        """Keep the card/list choice until the width clears a small dead band."""
        width, height = self.owner.width(), self.owner.height()
        wide = width >= 720 and height >= 520
        previous = getattr(self, "_wide_cards", None)
        if previous is True and height >= 520 and 688 <= width < 720:
            wide = True
        elif previous is False and height >= 520 and 720 <= width < 752:
            wide = False
        self._wide_cards = wide
        return wide

    def _rail_is_wide(self, width):
        previous = getattr(self, "_rail_wide", None)
        wide = width >= 720
        if previous is True and 688 <= width < 720:
            wide = True
        elif previous is False and 720 <= width < 752:
            wide = False
        self._rail_wide = wide
        return wide

    def _stable_grid(self, width):
        """Fit as many cards as the width allows, and don't flip at the boundary."""
        width = max(1, int(width))
        gap, min_side, max_side, slack = 24, 132, 176, 24
        if getattr(self, "refresh", False) and self.owner.library_variant == "sidebar":
            min_side, max_side = 148, 212
        def side_for(columns):
            return (width - gap * max(0, columns - 1)) / max(1, columns)
        columns = 1
        while side_for(columns + 1) >= min_side:
            columns += 1
        previous = getattr(self, "_grid_columns", None)
        if previous and previous != columns:
            stayed = side_for(int(previous))
            if min_side - slack <= stayed <= max_side + slack:
                columns = int(previous)
        self._grid_columns = columns
        return columns, max(1, int(min(max_side, side_for(columns))))

    def layout(self):
        o, variant = self.owner, self.owner.library_variant
        w, h = o.width(), o.height()
        wide = self._cards_are_wide()
        query = bool(o.search.text().strip())
        refreshed = self.refresh and variant == "sidebar"
        if refreshed:
            self.cluster = QRectF(w - 64, 8, 48, 48)
            o.close_button.setGeometry(w - 62, 10, 44, 44)
            o.close_button.show()
            o.source_button.hide()
            o.logout_button.show()
        else:
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
        from local.theme_tokens import hex_rgb
        from local.ui_theme import active_tokens
        tokens = active_tokens(o)
        if not (tokens.dark and tokens.shade >= 0.999):
            ink = hex_rgb(tokens.text)
            light = True
        else:
            ink, light = "#f4f5f7", False
        for button in (self.play_button, self.player_button, *self.buttons.values()):
            if hasattr(button, "setGlassLight"):
                button.setGlassLight(light)
            button.setStyleSheet(f"color:{ink};background:transparent;border:none;")
        for key, button in self.buttons.items():
            button.setVisible(variant in {"sidebar", "compact"})
            button.setChecked(key == ("search" if query else self.section))
            button.icons_only = not wide
        if refreshed:
            # The rail's width follows the window's width only (with the
            # cards' dead band), never its height: one less jump on resize.
            rail_wide = self._rail_is_wide(w)
            # Header 60 px; a slim rail; the content plane takes the rest.
            # Account actions sit in the rail's footer, under a rule. The
            # now-playing bar takes the window shape's pose.
            island = o.now_playing_island
            mode = bar_mode_for(w, h, island.bar_mode)
            island.set_bar_mode(mode)
            bar_h = island.bar_height(mode)
            bar_top = h - bar_h - 14
            rail_w = RAIL_WIDE if rail_wide else RAIL_NARROW
            self.rail = QRectF(16, REFRESH_HEADER + 4, rail_w, max(1, bar_top - 10 - (REFRESH_HEADER + 4)))
            left = round(self.rail.right()) + 12
            self.pane = QRectF(left, self.rail.y(), w - left - 16, self.rail.height())
            if mode == "pill":
                pill_w = min(w - 32, island.PILL_WIDTH)
                self.player = QRectF(round((w - pill_w) / 2), bar_top, pill_w, bar_h)
                island.merged_bottom_radius = 0.0
            else:
                # The bar is the window's bottom edge: full width, flush with
                # the frame, its bottom corners the window's own.
                bar_top = h - 1 - bar_h
                self.player = QRectF(1, bar_top, w - 2, bar_h)
                island.merged_bottom_radius = RADIUS_WINDOW - 1
                self.rail.setBottom(bar_top - 12)
                self.pane.setBottom(bar_top - 12)
            slots = 6 if not rail_wide else 5
            key_h = min(RAIL_KEY, max(26, int((self.rail.height() - 28) / slots)))
            gap = min(4, max(0, int((self.rail.height() - 28 - key_h * slots) / slots)))
            inset = 7
            for index, button in enumerate(self.buttons.values()):
                button.setGeometry(round(self.rail.x()) + inset, round(self.rail.y()) + 8 + index * (key_h + gap),
                                   rail_w - 2 * inset, key_h)
            footer = round(self.rail.bottom()) - key_h - 8
            for button in self.buttons.values():
                button.icons_only = not rail_wide
            self.source_name_rect = QRectF()
            if rail_wide:
                # [platform key] platform name [sign out], one footer row.
                key = min(32, key_h)
                self.rail_source.login_style = "sidebar-icons"
                self.rail_source.setGeometry(round(self.rail.x()) + 6, footer, key_h, key_h)
                o.logout_button.setGeometry(round(self.rail.right()) - 6 - key, footer + (key_h - key) // 2, key, key)
                self.source_name_rect = QRectF(self.rail.x() + 6 + key_h + 6, footer,
                                               rail_w - 12 - key_h - key - 12, key_h)
                self.rule = QRectF(self.rail.x() + 12, footer - 8, self.rail.width() - 24, 1)
            else:
                self.rail_source.setGeometry(round(self.rail.x()) + 6, footer, rail_w - 12, key_h)
                upper = footer - key_h - 4
                o.logout_button.setGeometry(round(self.rail.x()) + (rail_w - key_h) // 2, upper, key_h, key_h)
                self.rule = QRectF(self.rail.x() + 10, upper - 8, self.rail.width() - 20, 1)
            o.logout_button.raise_()
            searching = query or self.section == "search"
            self.search_rect = QRect(left + 14, round(self.pane.y()) + 54, round(self.pane.width()) - 28, 40)
            top = round(self.pane.y()) + (104 if searching else 58)
            o.search.setVisible(searching)
            scroll_rect = QRect(left + 8, top, round(self.pane.width()) - 12, max(1, round(self.pane.bottom()) - top - 6))
        elif variant == "sidebar":
            self.rule = QRectF()
            o.now_playing_island.set_bar_mode("tall")
            o.now_playing_island.merged_bottom_radius = 0.0
            login = login_variant_layout("sidebar", w, h, (True,) * 4)
            bar_top = h - o.now_playing_island.HEIGHT - 16
            self.rail = QRectF(login.rail)
            self.rail.setBottom(bar_top - 12)
            left = round(login.pane.x())
            rail_w = round(self.rail.width())
            self.pane = QRectF(left, self.rail.y(), w - left - 24, bar_top - self.rail.y() - 12)
            self.player = QRectF(16, bar_top, w - 32, o.now_playing_island.HEIGHT)
            rail_height = min(44, max(24, int((self.rail.height() - 16) / 5)))
            rail_gap = min(8, max(0, int((self.rail.height() - 16 - rail_height * 5) / 4)))
            for index, button in enumerate(self.buttons.values()):
                button.setGeometry(round(self.rail.x()) + (12 if wide else 8),
                                   round(self.rail.y()) + 8 + index * (rail_height + rail_gap),
                                   rail_w - (24 if wide else 16), rail_height)
            self.rail_source.setGeometry(round(self.rail.x()) + (12 if wide else 8),
                                         round(self.rail.bottom()) - rail_height - 8,
                                         rail_w - (24 if wide else 16), rail_height)
            searching = query or self.section == "search"
            self.search_rect = QRect(left, round(self.pane.y()) + 44, round(self.pane.width()), 44)
            top = round(self.pane.y()) + (100 if searching else 48)
            o.search.setVisible(searching)
            scroll_rect = QRect(left, top, round(self.pane.width()), max(1, round(self.pane.bottom()) - top))
        elif variant == "listen":
            self.rule = QRectF()
            self.rail = QRectF()
            self.search_rect = QRect(24, 78, w - 48, 44)
            o.search.show()
            self.player = QRectF(16, 136, w - 32, 198 if wide else 178)
            self.pane = QRectF(16, self.player.bottom() + 12, w - 32, h - self.player.bottom() - 28)
            scroll_rect = self.pane.adjusted(4, 2, -4, -4).toAlignedRect()
            self.play_button.setGeometry(round(self.player.x()) + (184 if wide else 132),
                                         round(self.player.y()) + (104 if wide else 80), 116, 38)
        else:
            self.rule = QRectF()
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
            columns, side = (1, cw) if not wide else self._stable_grid(cw)
            for index, row in enumerate(o._rows):
                row.card = wide
                if wide:
                    row.setGeometry(index % columns * (side + 24), top + index // columns * (side + 64), side, side + 48)
                elif refreshed:
                    row.setGeometry(0, top + index * REFRESH_STRIDE, cw, REFRESH_ROW)
                else:
                    row.setGeometry(0, top + index * 76, cw, 68)
                row._layout()
            stride = REFRESH_STRIDE if refreshed else 76
            top += math.ceil(len(o._rows) / columns) * (side + 64) if wide else len(o._rows) * stride
        elif refreshed:
            for row in o._rows:
                row.setGeometry(0, top, cw, REFRESH_ROW)
                row._layout()
                top += REFRESH_STRIDE
        else:
            for row in o._rows:
                row.setGeometry(4, top, cw - 8, 66)
                top += 66
        has_items = bool(o._visual_items())
        catalog_loading = (self.section in o._catalog_loading if variant == "sidebar" else o._catalog_loading)
        skeleton = bool((catalog_loading if not query else "search" in o._catalog_loading)
                        or (o._loading and not has_items and not o._catalog_loading))
        if o._search_waiting_empty() or (query and o._error and not has_items):
            skeleton = False
        if refreshed and not query:
            # No skeleton: a playlist shows once its cover is in, in order;
            # while the section loads, the dots in its heading run.
            skeleton = False
            if self._section_loading() and not self._dots_timer.isActive():
                self._dots_timer.start()
        o._catalog_skeleton.setVisible(skeleton)
        skeleton_height = 216
        if variant == "sidebar":
            skeleton_height = 240 if wide else 228
        o._catalog_skeleton.setGeometry(0, top, cw, skeleton_height)
        if skeleton:
            top += skeleton_height
        more = o._visible_has_more() and not skeleton and not o._catalog_loading
        o.more_button.setVisible(more)
        o.more_button.setGeometry(max(0, (cw - 172) // 2), top + 6, min(172, cw), 42)
        if more:
            top += 56
        o.content.resize(cw, max(1, top))
        ready = not has_items and not skeleton and not o._loading
        o.empty_action.setVisible(ready and not o._error and o.search.isEnabled()
                                  and not o._search_waiting_empty()
                                  and not (variant == "sidebar" and self.section == "search" and not query))
        o.recovery_action.setVisible(ready and bool(o._error))
        o.empty_action.setText("修改搜索词" if query else "搜索歌曲")
        o.recovery_action.setText("重新搜索" if query else "切换平台")
        for button in (o.empty_action, o.recovery_action):
            button.setGlassLight(True)
            # A light plate takes dark ink, or the label disappears into it.
            button.setStyleSheet("color:#15161a;background:transparent;border:none;")
            button.setGeometry(scroll_rect.x() + (scroll_rect.width() - 148) // 2,
                               scroll_rect.y() + max(70, (scroll_rect.height() - 20) // 2), 148, 40)
            button.raise_()
        o._sync_artwork_skeleton()
        self.sync_status_pulse()
        if refreshed:
            self._sync_plate(False)
            self.dots_rect = QRectF(self.pane.right() - 20 - 64, self.pane.y() + 14, 64, 30)
            self.heading_rule = QRectF(self.pane.x() + 16, scroll_rect.y() - 1, self.pane.width() - 32, 1)
        else:
            self.dots_rect = QRectF()
            self.heading_rule = QRectF()
        self._scrolled()

    def _progress(self, p, rect, *, times=True):
        track = self.owner._now_playing
        if not LibraryProgress.progress_available(track):
            return
        position, duration = LibraryProgress._times(track)
        fraction = position / duration if duration else 0.
        from local.ui_theme import active_tokens
        tokens = active_tokens(self.owner)
        if tokens.dark and tokens.shade >= 0.999:
            track, fill = QColor(255, 255, 255, 40), QColor("#e7edf6")
        else:
            track, fill = QColor(*tokens.text, 55), QColor(*tokens.text)
        p.setPen(QPen(track, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(rect.topLeft(), rect.topRight())
        p.setPen(QPen(fill, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(rect.topLeft(), rect.topLeft() + (rect.topRight() - rect.topLeft()) * fraction)
        if times:
            clock = lambda value: f"{int(value)//60}:{int(value)%60:02}"
            _text(p, QRectF(rect.x(), rect.y() + 5, rect.width(), 14), clock(position), 9, "#bec8d7")
            _text(p, QRectF(rect.x(), rect.y() + 5, rect.width(), 14), clock(duration), 9, "#bec8d7",
                  align=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def paint_skeleton(self, p, widget):
        from local.ui_theme import active_tokens
        tokens = active_tokens(self.owner)
        seconds = time.monotonic()
        ink = QColor("white") if tokens.dark and tokens.shade >= 0.999 else QColor(*tokens.muted)
        if self.owner.library_variant == "compact" or (self.owner.library_variant == "sidebar" and not self._cards_are_wide()):
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
        columns, side = self._stable_grid(widget.width())
        for index in range(columns):
            x = index * (side + 24)
            paint_artwork_skeleton(p, QRectF(x, 4, side, side), ink, seconds, radius=14)
            paint_artwork_skeleton(p, QRectF(x + 4, side + 16, min(100, side - 8), 10), ink, seconds, radius=5)

    def paint(self, p):
        o, variant = self.owner, self.owner.library_variant
        wide = o.width() >= 720 and o.height() >= 520
        from local.theme_tokens import library_surface_rgb
        from local.ui_theme import active_tokens
        tones = library_surface_rgb(active_tokens(o).dark)
        def plane(rect, key, radius):
            rect = QRectF(rect)
            if rect.isEmpty():
                return
            color = QColor(*tones[key])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(rect, radius, radius)
        refreshed = self.refresh and variant == "sidebar"
        name = o._account.nickname if o._account and o._account.logged_in else "未登录"
        if o._notice:
            name = o._notice
        if refreshed:
            # The song's own background (drawn by the window) shows through:
            # the rail is a faint glass strip, the content a full glass plane.
            dark = active_tokens(o).dark
            fresh = refresh_tones(dark)
            ink = QColor(244, 245, 247) if dark else QColor(20, 21, 26)
            p.setPen(Qt.PenStyle.NoPen)
            _glass(p, o, self.cluster, "utilities", 24)
            _type(p, QRectF(24, 9, o.width() - 120, 28), "音乐库", 20, ink, QFont.Weight.Bold)
            _type(p, QRectF(25, 36, o.width() - 120, 18), name, 11, fresh["muted"])
            if not self.rail.isEmpty():
                _glass(p, o, self.rail, "rail", RADIUS_PLANE, opacity=.55)
            if not self.pane.isEmpty():
                _glass(p, o, self.pane, "content", RADIUS_PLANE)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(fresh["edge"], 1))
                p.drawRoundedRect(self.pane.adjusted(.5, .5, -.5, -.5), RADIUS_PLANE, RADIUS_PLANE)
                p.setPen(Qt.PenStyle.NoPen)
            if not self.plate.isEmpty():
                from local.row_states import paint_row_state
                _glass(p, o, self.plate, "selection", RADIUS_KEY, active=True, opacity=.9)
                paint_row_state(p, self.plate, RADIUS_KEY, dark=dark, selected=True)
            if not self.rule.isEmpty():
                p.setBrush(fresh["rule"])
                p.drawRect(self.rule)
            name_rect = getattr(self, "source_name_rect", QRectF())
            if not name_rect.isEmpty():
                _type(p, name_rect, o.source_button.source.name, 12, fresh["muted"])
        else:
            plane(QRectF(o.rect()), "page", 24)
            plane(self.rail, "sidebar", 20)
            plane(self.pane, "content", 18)
            plane(self.cluster, "header", 17)
            _glass(p, o, self.cluster, "utilities", 17)
            _text(p, QRectF(24, 17, o.width() - 196, 32), "音乐库", 24, bold=True)
            _text(p, QRectF(25, 50, o.width() - 196, 20), name, 11, "#b5c1cf")
            if not self.rail.isEmpty():
                _glass(p, o, self.rail, "rail", 20)
        if variant != "sidebar":
            _glass(p, o, self.pane, "content", 22, opacity=.48)
        if refreshed:
            page_open = getattr(o, "playlist_page_open", False)
            heading = "" if page_open else ("搜索" if o.search.isVisible() else SECTIONS[self.section])
            count = len(o._rows)
            title_rect = QRectF(self.pane.x() + 20, self.pane.y() + 12, self.pane.width() - 40, 34)
            if heading:
                _type(p, title_rect, heading, 22, ink, QFont.Weight.Bold)
            self.paint_dots(p, ink)
            if not self.heading_rule.isEmpty() and not page_open and self._heading_layered():
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fresh["rule"])
                p.drawRect(self.heading_rule)
            if heading and count and not o._catalog_skeleton.isVisible():
                font = _entry_font(22)
                font.setWeight(QFont.Weight.Bold)
                offset = QFontMetricsF(font).horizontalAdvance(heading) + 10
                _type(p, title_rect.adjusted(offset, 4, 0, 0), f"{count} 项", 12, fresh["muted"])
        elif variant == "sidebar":
            _text(p, QRectF(self.pane.x(), self.pane.y(), self.pane.width(), 32),
                  "搜索" if o.search.isVisible() else SECTIONS[self.section], 21, bold=True)
        elif variant == "compact":
            title = "搜索结果" if o.search.text().strip() else SECTIONS[self.section]
            _text(p, QRectF(36, self.pane.y() + 8, self.pane.width() - 40, 26), title, 15, bold=True)
        if variant != "sidebar":
            _glass(p, o, self.player, "now-playing", 20, opacity=.72)
        track = o._now_playing
        title = track.title if track else "未在播放"
        artist = track.artist if track else ""
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
        elif variant != "sidebar":
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
            message = o._error or o._search_status_message() or ("未找到歌曲或歌单" if o.search.text().strip() else
                                  "输入歌曲、歌手或歌单名" if self.section == "search" else
                                  "暂无歌单" if o._account and o._account.logged_in else "登录后同步歌单")
            _text(p, QRectF(rect.x() + 8, rect.y() + 16, rect.width() - 16, 30), message, 15,
                  align=Qt.AlignmentFlag.AlignCenter)
        elif o._error:
            o.paint_library_error_toast(p)
        # Headings are content children in spirit, painted here with scroll clipping.
        if self.headings:
            p.save()
            p.setClipRect(o.scroll.geometry())
            for title, rect, _key in self.headings:
                rect = rect.translated(o.scroll.x(), o.scroll.y() - o.scroll.verticalScrollBar().value())
                _text(p, rect, title, 15, bold=True)
            p.restore()
