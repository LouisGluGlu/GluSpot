"""Account music library: immutable provider data in, user requests out.

Artwork and catalog retrieval belong to the controller. This native window
shares the island's presentation glyphs and never creates account or song data.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from html import escape
import math
import threading
import time

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QGuiApplication, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import QAbstractButton, QGraphicsOpacityEffect, QLabel, QMessageBox, QPushButton, QScrollArea, QWidget

from api.online_music import AccountSnapshot, OnlineSong
from local.branding import APP_NAME
from local.island_stack_visual import IslandStackVisual
from local.music_mode_controls import paint_presentation_glyph
from local.music_library_layout import CatalogMotionOverlay
from local.music_library_search import LibraryProgress, LibrarySearch
from local.artwork_skeleton import paint_artwork_skeleton
from local.music_page_resize import MusicPageResizeMixin
from local.music_source_icons import music_source_icon_image, paint_music_source_icon
from local.liquid_glass import (GlassCaptureMixin, GlassPushButton, glass_owner_is_live,
                                paint_liquid_glass, widget_glass_backdrop, widget_glass_level)
from local.music_surface_background import AlbumFlowBackgroundRenderer
from local.music_surface_palette import artwork_surface_palette, cached_artwork_surface_palette
from local.music_lyric_motion import MotionSimulationClock
from local.control_hover import HOVER_SECONDS, install_control_hover
from local.playback_entry_window import ENTRY_SOURCES, EntryNavigationButton, MusicSourceButton, _entry_font


@dataclass(frozen=True)
class LibraryCollection:
    key: str
    kind: str
    title: str
    artwork_url: str
    playlist_id: str = ""
    song: OnlineSong | None = None
    group: str = "personal"


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
        install_control_hover(self)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
            backdrop, source = widget_glass_backdrop(self, rect)
            paint_liquid_glass(painter, rect, radius=12, hovered=self.underMouse(), pressed=self.isDown(),
                              refractive=True, backdrop=backdrop, backdrop_rect=source, cache_token=id(self),
                              material_level=widget_glass_level(self))
            paint_music_source_icon(painter, rect.adjusted(10, 10, -10, -10), self.kind,
                                     tint=QColor("white") if self.kind == "switch" else None)
            if self.hasFocus() and self.window().testAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange):
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(255, 255, 255, 110), 1.0))
                painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 11, 11)


class _NowPlayingCover(QAbstractButton):
    """The current album is a single explicit playback-window target."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAccessibleName("打开当前歌曲播放窗口")
        self.setToolTip(self.accessibleName())
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        owner = self.parentWidget()
        if owner._navigation_capture_hide_cover:
            return
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            path = QPainterPath()
            path.addEllipse(rect)
            painter.setClipPath(path)
            painter.translate(rect.center())
            painter.rotate(owner._album_rotation)
            painter.translate(-rect.center())
            _paint_crop(painter, rect, owner._header_album_image)
            painter.resetTransform()
            painter.setClipping(False)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#c8cdd6") if self.hasFocus() else QColor(255, 255, 255, 44), 1))
            painter.drawEllipse(rect)


class _ExpandSwitch(QAbstractButton):
    """A 44-pixel target with an explicit persisted expand-all switch."""
    def __init__(self,parent):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(_entry_font(12))

    def paintEvent(self,event):
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setFont(self.font());p.setPen(QColor('#e8ecf4'))
            p.drawText(QRectF(0,0,self.width()-52,self.height()),Qt.AlignmentFlag.AlignVCenter,self.text())
            rect=QRectF(self.width()-48,10,46,24)
            backdrop,source=widget_glass_backdrop(self,rect)
            paint_liquid_glass(p,rect,radius=12,refractive=True,backdrop=backdrop,backdrop_rect=source,
                              hovered=self.underMouse(),pressed=self.isDown(),
                              active=self.isChecked(),cache_token=id(self),
                              material_level=widget_glass_level(self))
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#f4f5f7'))
            p.drawEllipse(QRectF(rect.right()-21 if self.isChecked() else rect.left()+3,rect.top()+3,18,18))
            if self.hasFocus() and self.window().testAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange):
                p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(QPen(QColor(255,255,255,110),1))
                p.drawRoundedRect(QRectF(self.rect()).adjusted(1,1,-1,-1),12,12)


class _ModeButton(QAbstractButton):
    def __init__(self, mode: str, parent: QWidget):
        super().__init__(parent)
        self.mode = mode
        self.caption = {"fullscreen": "全屏", "window": "窗口", "island": "岛"}[mode]
        self.setAccessibleName({"fullscreen": "全屏播放", "window": "窗口播放", "island": "岛模式播放"}[mode])
        self.setToolTip(self.accessibleName())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        install_control_hover(self)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
            backdrop, source = widget_glass_backdrop(self, rect)
            paint_liquid_glass(painter, rect, radius=12, hovered=self.underMouse(), pressed=self.isDown(),
                              refractive=True, backdrop=backdrop, backdrop_rect=source, cache_token=id(self),
                              material_level=widget_glass_level(self))
            if self.hasFocus():
                painter.setPen(QPen(QColor("#a8a8a8"), 1.3))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect, 12, 12)
            side = min(25.0, rect.width() * .6)
            glyph = QRectF(rect.center().x() - side / 2, rect.top() + 6, side, side)
            paint_presentation_glyph(painter, glyph, "windowed" if self.mode == "window" else self.mode,
                                     QColor("#ffffff"))
            painter.setFont(_entry_font(11))
            painter.setPen(QColor("#e4e7ed"))
            painter.drawText(rect.adjusted(2, rect.height() - 22, -2, -3), Qt.AlignmentFlag.AlignCenter, self.caption)


class _CollectionCover(QAbstractButton):
    def __init__(self, source_id: str, kind: str, parent: QWidget):
        super().__init__(parent)
        self.image = QImage()
        self.loading = False
        self.source_id = source_id
        self.kind = kind
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = 0.0
        self._opening_pose = False
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(round(HOVER_SECONDS * 1000))
        self._animation.setEasingCurve(QEasingCurve.Type.OutQuint)
        self._animation.valueChanged.connect(self._set_hover)
        install_control_hover(self, max_scale=1.03)

    def _set_hover(self, value):
        self._hover = float(value)
        self.update()

    def _retarget(self, target):
        if self._opening_pose:
            target = 1.
        self._animation.stop()
        self._animation.setStartValue(self._hover)
        self._animation.setEndValue(float(target))
        self._animation.start()

    def set_opening_pose(self, active):
        self._opening_pose = bool(active)
        self._retarget(1. if active else float(self.underMouse()))

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
        if getattr(self, "_navigation_capture_hidden", False):
            return
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = self.artwork_rect()
            clip = QPainterPath()
            clip.addRoundedRect(rect, 14, 14)
            painter.setClipPath(clip)
            if not self.image.isNull():
                _paint_crop(painter, rect, self.image)
            else:
                # Clearly branded placeholder, never a fabricated album cover.
                source = next((item for item in ENTRY_SOURCES if item.source_id == self.source_id), ENTRY_SOURCES[0])
                accent = QColor(source.color)
                base = QColor("#23262e")
                base = QColor(*(round(a * 227 / 255 + b * 28 / 255)
                                for a, b in zip(base.getRgb()[:3], accent.getRgb()[:3])))
                paint_liquid_glass(painter, rect, radius=14, base_color=base,
                                  hovered=self.underMouse(), pressed=self.isDown())
                if self.loading:
                    paint_artwork_skeleton(painter, rect, QColor("#ffffff"), time.monotonic(), radius=14)
                side = min(44.0, rect.width() * .36)
                icon = QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2 - 8, side, side)
                paint_music_source_icon(painter, icon, source.source_id)
                label = {"daily": "每日推荐", "fm": "私人漫游", "playlist": "歌单", "song": "歌曲"}.get(self.kind, "音乐")
                painter.setFont(_entry_font(12))
                painter.setPen(QColor("#c4c7d0"))
                painter.drawText(rect.adjusted(8, rect.height() - 30, -8, -6), Qt.AlignmentFlag.AlignCenter, label)
            painter.setClipping(False)
            painter.setPen(QPen(QColor("#717171") if self.hasFocus() else QColor("#343434"), 1.4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 14, 14)
            # The existing cover is the default-play target. Keep this cue
            # inside that target; it neither adds another command nor hides
            # the three explicitly labeled presentation choices.
            if self.kind == "song" and (self.underMouse() or self.hasFocus()):
                center = rect.center()
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(12, 14, 20, 155))
                painter.drawEllipse(center, 20, 20)
                play = QPainterPath()
                play.moveTo(center + QPointF(-4, -8))
                play.lineTo(center + QPointF(8, 0))
                play.lineTo(center + QPointF(-4, 8))
                play.closeSubpath()
                painter.fillPath(play, QColor("#ffffff"))

    def hideEvent(self, event):
        self._animation.stop()
        self._hover = 0.0
        self._opening_pose = False
        super().hideEvent(event)


class _CollectionRow(QWidget):
    activated = Signal(object, str, object)
    browseRequested = Signal(object)
    queueRequested = Signal(object)

    def __init__(self, collection: LibraryCollection, source_id: str, parent: QWidget):
        super().__init__(parent)
        self.collection = collection
        self.fraction = 1.0
        self.cover = _CollectionCover(source_id, collection.kind, self)
        self.cover.loading = bool(collection.artwork_url) or collection.kind in {"daily", "fm"}
        is_song = collection.song is not None
        self.cover.setAccessibleName(f"播放{collection.title}")
        self.cover.setAccessibleDescription("按上次结束播放的界面打开" if is_song else "从第一首开始播放歌单")
        self.cover.setToolTip(self.cover.accessibleName())
        self.cover.clicked.connect(lambda: self._activate("default"))
        self.mode_buttons = {}
        self.queue_button = GlassPushButton("", self)
        queue_icon = music_source_icon_image("queue_next", QSize(56, 56), tint=QColor("white"))
        queue_icon.setDevicePixelRatio(2.)
        self.queue_button.setIcon(QIcon(QPixmap.fromImage(queue_icon)))
        self.queue_button.setIconSize(QSize(28, 28))
        self.queue_button.setToolTip("下一首播放")
        self.queue_button.setProperty("glassRefractive", True)
        self.queue_button.setProperty("glassRadius", 12.)
        self.queue_button.setGlassLight(False)
        self.queue_button.setFont(_entry_font(12))
        self.queue_button.setStyleSheet("QPushButton { color: white; background: transparent; border: none; }")
        self.queue_button.setAccessibleName(f"将{collection.title}加入下一首播放")
        self.queue_button.clicked.connect(lambda: self.queueRequested.emit(self.collection))
        self.queue_button.setVisible(is_song)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _activate(self, mode):
        rect = self.cover.artwork_rect().toAlignedRect()
        global_rect = QRect(self.cover.mapToGlobal(rect.topLeft()), rect.size())
        self.activated.emit(self.collection, mode, global_rect)

    def set_fraction(self, fraction: float):
        self.fraction = fraction
        self._layout()
        self.update()

    def _layout(self):
        side = 96
        left = 16
        self.cover.setGeometry(left, 12, side, side)
        text_x = left + side + 16
        self.queue_button.setGeometry(self.width() - 64, 38, 44, 44)
        self._group_rect = QRectF(self.queue_button.geometry()) if self.collection.song is not None else QRectF()
        text_width = max(1, (self.queue_button.x()-16 if self.collection.song else self.width()-24)-text_x)
        self._title_rect = QRectF(text_x, 24, text_width, 32)
        self._subtitle_rect = QRectF(text_x, 62, text_width, 24)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            if self.collection.song is None:
                self._activate("default")
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event):
        self._layout()
        super().resizeEvent(event)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            panel = QRectF(self.rect()).adjusted(4, 4, -4, -4)
            backdrop, source = widget_glass_backdrop(self, panel)
            paint_liquid_glass(painter, panel, radius=18, refractive=True,
                              backdrop=backdrop, backdrop_rect=source, cache_token=id(self))
            painter.setFont(_entry_font(21))
            painter.setPen(QColor("#f8f8f8"))
            title = painter.fontMetrics().elidedText(self.collection.title, Qt.TextElideMode.ElideRight,
                                                     round(self._title_rect.width()))
            painter.drawText(self._title_rect, Qt.AlignmentFlag.AlignVCenter, title)
            painter.setFont(_entry_font(13))
            painter.setPen(QColor("#989eac"))
            subtitle = self.collection.song.artist if self.collection.song is not None else {
                "daily": "为你推荐的音乐", "fm": "私人漫游", "playlist": "歌单", "song": "歌曲"}.get(self.collection.kind, "音乐合集")
            painter.drawText(self._subtitle_rect, Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(subtitle, Qt.TextElideMode.ElideRight,
                                                             round(self._subtitle_rect.width())))


class _LibraryGroup(QAbstractButton):
    """A folded stack only opens its group; it never starts playback."""
    def __init__(self, title, items, parent, *, key=""):
        super().__init__(parent)
        self.title, self.items = title, tuple(items)
        self.group_key = key
        self.images = {}
        self.setAccessibleName(f"展开{title}")
        self.setToolTip("展开为横向歌单，点击封面从第一首播放")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event):
        with QPainter(self) as p:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            compact = self.width() < 430
            side = 76 if compact else 94
            step = 8 if compact else 11
            for i in reversed(range(min(3, len(self.items)))):
                face = QRectF(12 + i*step, 12 + i*2, side, side)
                backdrop, source = widget_glass_backdrop(self, face)
                paint_liquid_glass(p, face, radius=15, backdrop=backdrop, backdrop_rect=source,
                                  refractive=True, cache_token=(id(self), i))
                image = self.images.get(self.items[i].key, QImage())
                if not image.isNull():
                    p.save()
                    path = QPainterPath(); path.addRoundedRect(face, 15, 15)
                    p.setClipPath(path); _paint_crop(p, face, image); p.restore()
                elif self.items[i].artwork_url or self.items[i].kind in {"daily", "fm"}:
                    paint_artwork_skeleton(p, face, QColor("#ffffff"), time.monotonic(), radius=15)
            left = 112 if compact else 150
            width = max(1,self.width()-left-12)
            p.setPen(QColor("#f5f6fa")); p.setFont(_entry_font(14 if compact else 17))
            p.drawText(QRectF(left, 18, width, 28), Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(self.title,Qt.TextElideMode.ElideRight,width))
            p.setPen(QColor("#b7beca")); p.setFont(_entry_font(11 if compact else 12))
            text = "、".join(item.title for item in self.items[:3])
            p.drawText(QRectF(left, 50, width, 48), Qt.TextFlag.TextWordWrap,
                       p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(1, width*2)))


class _CollectionShelf(QWidget):
    """A horizontally scrollable row of actual collection covers."""
    def __init__(self, key, title, collections, owner):
        super().__init__(owner.content)
        self.group_key, self.title, self.items, self.owner = key, title, tuple(collections), owner
        self.scroll = QScrollArea(self)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setStyleSheet("QScrollArea,QScrollArea > QWidget > QWidget {background:transparent;border:none;}"
            "QScrollBar:horizontal {background:transparent;height:8px;margin:0 6px;}"
            "QScrollBar::handle:horizontal {background:#606979;min-width:32px;border-radius:4px;}"
            "QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal {width:0;}"
            "QScrollBar::add-page:horizontal,QScrollBar::sub-page:horizontal {background:transparent;}")
        self.body = QWidget(); self.scroll.setWidget(self.body)
        self.scroll.viewport().installEventFilter(self)
        self.body.installEventFilter(self)
        self.tiles, self.labels = [], []
        for collection in self.items:
            cover = _CollectionCover(owner.source_button.source.source_id, collection.kind, self.body)
            cover.loading = bool(collection.artwork_url) or collection.kind in {"daily", "fm"}
            cover.installEventFilter(self)
            cover.setAccessibleName(f"播放{collection.title}")
            cover.setAccessibleDescription("从第一首开始，按上次关闭的播放界面打开")
            cover.setToolTip(collection.title)
            cover.image = owner._artwork.get(collection.key, QImage())
            cover.clicked.connect(lambda checked=False, item=collection, cover=cover:self._play(item,cover))
            self.tiles.append((collection,cover,None))
            label = QLabel(self.body);label.installEventFilter(self)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setFont(_entry_font(13));label.setStyleSheet("color:#e8ebf0;background:transparent;")
            label.setToolTip(collection.title);self.labels.append(label)
        self.scroll.horizontalScrollBar().valueChanged.connect(lambda _value:owner._request_visible_artwork())

    def _play(self, collection, cover):
        rect = cover.artwork_rect().toAlignedRect()
        self.owner._collection_activated(collection,"default",QRect(cover.mapToGlobal(rect.topLeft()),rect.size()))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel:
            pixels = event.pixelDelta()
            angles = event.angleDelta()
            # Horizontal trackpad/wheel deltas stay horizontal. A vertical
            # wheel moves this shelf only when the user holds Shift; otherwise
            # let the enclosing library page handle its natural vertical scroll.
            delta = pixels.x() or angles.x()
            vertical = pixels.y() or angles.y()
            if not delta and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                delta = vertical
            if not delta:
                if vertical:
                    # The nested QScrollArea can consume vertical wheel events
                    # despite its vertical bar being disabled. Apply the same
                    # pixel/per-notch movement to the library's page bar here.
                    bar = self.owner.scroll.verticalScrollBar()
                    step = -pixels.y() if pixels.y() else int(-angles.y() / 120 * 62)
                    if step and bar.maximum() > bar.minimum():
                        bar.setValue(bar.value() + step)
                        event.accept()
                        return True
                    event.ignore()
                    return True
                return super().eventFilter(watched, event)
            bar = self.scroll.horizontalScrollBar();bar.setValue(bar.value()-delta)
            event.accept();return True
        return super().eventFilter(watched,event)

    def resizeEvent(self,event):
        self.scroll.setGeometry(0,36,self.width(),184)
        self.body.resize(max(self.scroll.viewport().width(),156*len(self.tiles)+12),166)
        for i,(item,cover,_) in enumerate(self.tiles):
            cover.setGeometry(8+156*i,0,132,132)
            label=self.labels[i];label.setGeometry(12+156*i,136,136,26)
            label.setText(label.fontMetrics().elidedText(item.title,Qt.TextElideMode.ElideRight,136))
        super().resizeEvent(event)

    def paintEvent(self,event):
        with QPainter(self) as p:
            p.setFont(_entry_font(17));p.setPen(QColor("#f0f2f7"))
            p.drawText(QRectF(12,0,self.width()-24,30),Qt.AlignmentFlag.AlignVCenter,self.title)


class _CatalogSkeleton(QWidget):
    """Metadata placeholders use the same light sweep as missing artwork."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName("音乐库正在加载，未完成的内容会自动重试")

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            seconds = time.monotonic()
            ink = QColor("#ffffff")
            paint_artwork_skeleton(painter, QRectF(12, 9, 112, 15), ink, seconds, radius=7.5)
            side = 132
            for left in range(12, self.width(), 156):
                paint_artwork_skeleton(painter, QRectF(left, 42, side, side), ink, seconds, radius=14)
                paint_artwork_skeleton(painter, QRectF(left + 3, 184, 100, 11), ink, seconds, radius=5.5)


class MusicLibraryWindow(GlassCaptureMixin, MusicPageResizeMixin, QWidget):
    backRequested = Signal()
    sourceSwitchRequested = Signal()
    logoutRequested = Signal()
    searchRequested = Signal(str)
    moreRequested = Signal()
    artworkNeeded = Signal(str, str)
    collectionRequested = Signal(object, str, object)
    collectionBrowseRequested = Signal(object)
    playlistMoreRequested = Signal(object)
    queueNextRequested = Signal(object)
    nowPlayingRequested = Signal()
    libraryExpandChanged = Signal(bool)
    closed = Signal()
    backgroundPaletteReady = Signal(int, object, object)

    # 256 downsampled 256px covers are bounded to roughly 64 MiB, allowing all
    # ordinary library pages to retain their covers before a user expands them.
    ARTWORK_MAX_ENTRIES = 256
    TOOLBAR_ANIMATION_MS = 360
    HEADER_ANIMATION_MS = 420
    ALBUM_REVOLUTION_SECONDS = 20.

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        IslandStackVisual.load_embedded_lyric_fonts()
        self.setWindowTitle(f"{APP_NAME} · 音乐库")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # Application queries preserve the shared QScreen wrapper's lifetime.
        screen = QGuiApplication.screenAt(self.frameGeometry().center()) or QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)
        self.setMinimumSize(min(330, available.width()), min(400, available.height()))
        self.resize(min(960, available.width()), min(540, available.height()))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._account: AccountSnapshot | None = None
        self._logout_confirmation = None
        self._background_frame = QImage()
        self._background_key = None
        self._background_source_key = None
        self._background_renderer = AlbumFlowBackgroundRenderer()
        self._background_seconds = 0.0
        self._background_clock = MotionSimulationClock()
        self._background_settings_getter = None
        self._background_runner = None
        self._background_palette = None
        self._background_palette_key = None
        self._background_palette_current_key = None
        self._background_palette_failed_key = None
        self._background_palette_pending = None
        self._background_palette_worker_key = None
        self._background_palette_worker_token = 0
        self._background_closing = False
        self.backgroundPaletteReady.connect(self._on_background_palette_ready)
        self._background_timer = QTimer(self)
        self._background_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._background_timer.timeout.connect(self._advance_background)
        self._catalog: tuple[LibraryCollection, ...] = ()
        self._results: tuple[LibraryCollection, ...] = ()
        self._rows: list[_CollectionRow] = []
        self._groups = []
        self._shelves = []
        self._library_expanded = False
        self._expanded_groups = set()
        self._expanded_playlists = set()
        self._playlist_data = {}
        self._selected_collection = None
        self._selected_group_key = None
        self._peer_order = []
        self._section_overlay = None
        self._now_playing = None
        self._now_playing_image = QImage()
        self._now_playing_art = b''
        self._header_album_image = QImage()
        self._header_album_key = None
        self._header_fraction = 0.
        self._header_target = 0.
        self._account_avatar_rect = QRectF()
        self._album_rotation = 0.
        self._album_rotation_sample = None
        self._navigation_cover_frozen = False
        self._navigation_capture_hide_cover = False
        self._header_motion = QVariantAnimation(self)
        self._header_motion.setDuration(self.HEADER_ANIMATION_MS)
        self._header_motion.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._header_motion.valueChanged.connect(self._set_header_fraction)
        self._album_rotation_timer = QTimer(self)
        self._album_rotation_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._album_rotation_timer.setInterval(33)
        self._album_rotation_timer.timeout.connect(self._advance_album_rotation)
        self._notice = ""
        self.setProperty("controlHoverDisabled", True)
        self._artwork: OrderedDict[str, QImage] = OrderedDict()
        self._artwork_pending: set[str] = set()
        self._artwork_skeleton_timer = QTimer(self)
        self._artwork_skeleton_timer.setInterval(33)
        self._artwork_skeleton_timer.timeout.connect(self._pulse_artwork_skeleton)
        self._expanded = 1.0
        self._toolbar_target = 1.0
        self._loading = False
        self._catalog_loading = set()
        self._message = ""
        self._error = ""
        self._opening = False
        self._opening_cover = None
        self._has_more = False
        self._more_requested = False
        self._drag_origin: QPoint | None = None
        self._section_starts = {}
        self._section_targets = {}
        self._section_motion = QVariantAnimation(self)
        self._section_motion.setDuration(360)
        self._section_motion.setEasingCurve(QEasingCurve.Type.Linear)
        self._section_motion.valueChanged.connect(self._advance_sections)
        self._section_motion.finished.connect(self._finish_section_motion)
        self._section_motion.finished.connect(self._request_visible_artwork)
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
        self.source_button.show_caption = False
        self.source_button.setCheckable(False)
        self.source_button.clicked.connect(self.sourceSwitchRequested)
        self.switch_button = _LibraryActionButton("switch", "切换音乐平台", self)
        self.switch_button.clicked.connect(self.sourceSwitchRequested)
        self.logout_button = _LibraryActionButton("logout", "退出当前平台账号", self)
        self.logout_button.clicked.connect(self._confirm_logout)
        self.back_button = EntryNavigationButton("back", "返回音乐来源", self)
        self.back_button.clicked.connect(self.backRequested)
        # Retain compatibility objects for controller/keyboard contracts. The
        # source glyph is the sole platform action in the visible layout.
        self.back_button.hide()
        self.switch_button.hide()
        self.close_button = EntryNavigationButton("close", "关闭音乐库", self)
        self.close_button.clicked.connect(self.close)
        self.expand_button = _ExpandSwitch(self)
        self.expand_button.setText("全部展开")
        self.expand_button.setCheckable(True)
        self.expand_button.setProperty("glassRefractive", True)
        self.expand_button.setProperty("glassRadius", 14.)
        self.expand_button.setFont(_entry_font(12))
        self.expand_button.setStyleSheet("QPushButton {color:#f1f3f8;background:transparent;border:none;}")
        self.expand_button.setAccessibleName("全部展开歌单")
        self.expand_button.toggled.connect(self._toggle_library_expanded)
        self.account_name_label = QLabel(self)
        self.account_name_label.setTextFormat(Qt.TextFormat.PlainText)
        self.account_name_label.setFont(_entry_font(17))
        self.account_name_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.account_name_label.setStyleSheet("color: #f1f2f5; background: transparent;")
        self.now_playing_button = _NowPlayingCover(self)
        self.now_playing_button.clicked.connect(self.nowPlayingRequested)
        self.now_playing_button.hide()
        self.search = LibrarySearch(self)
        self.search.setAccessibleName("搜索当前平台的歌曲与歌单")
        self.search.setPlaceholderText("搜索歌曲与歌单")
        self.search.setFont(_entry_font(16))
        search_icon = QPixmap.fromImage(music_source_icon_image("search", QSize(68, 68)))
        search_icon.setDevicePixelRatio(2.0)
        self.search_button = QPushButton(self.search)
        self.search_button.setIcon(QIcon(search_icon))
        self.search_button.setIconSize(QSize(22, 22))
        self.search_button.setAccessibleName("搜索歌曲与歌单")
        self.search_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_button.setStyleSheet("QPushButton { border: none; background: transparent; }")
        self.search_button.clicked.connect(self._activate_search_button)
        self.progress_button = LibraryProgress(self.search, self)
        self.search.poseChanged.connect(self._layout_search)
        self.clear_search_button = EntryNavigationButton("close", "清除搜索并返回音乐库", self.search)
        self._clear_search_opacity = QGraphicsOpacityEffect(self.clear_search_button)
        self.clear_search_button.setGraphicsEffect(self._clear_search_opacity)
        self.clear_search_button.clicked.connect(self._clear_search)
        self.find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self.find_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.find_shortcut.activated.connect(self._focus_empty_search)
        self.empty_action = GlassPushButton("搜索歌曲", self)
        self.empty_action.setFont(_entry_font(14))
        self.empty_action.setProperty("glassRadius", 12.0)
        self.empty_action.setProperty("glassRefractive", True)
        self.empty_action.setGlassLight(False)
        self.empty_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.empty_action.setStyleSheet("QPushButton { color: #f3f4f7; background: transparent; border: none; padding: 0 16px; }")
        self.empty_action.clicked.connect(self._focus_empty_search)
        self.recovery_action = GlassPushButton(self)
        self.recovery_action.setFont(_entry_font(14))
        self.recovery_action.setProperty("glassRadius", 12.0)
        self.recovery_action.setProperty("glassRefractive", True)
        self.recovery_action.setGlassLight(False)
        self.recovery_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.recovery_action.setStyleSheet(self.empty_action.styleSheet())
        self.recovery_action.clicked.connect(self._recover_empty_error)
        self.search.installEventFilter(self)
        self.search.textChanged.connect(self._search_changed)
        self.search.returnPressed.connect(self._submit_search)
        self.scroll = QScrollArea(self)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setWidgetResizable(False)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; } QScrollBar:vertical { background: transparent; width: 10px; margin: 4px 2px; } QScrollBar::handle:vertical { background: #444a56; border-radius: 3px; min-height: 28px; } QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; } QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }")
        self.content = QWidget()
        self.content.setStyleSheet("background: transparent;")
        self.scroll.setWidget(self.content)
        self._catalog_skeleton = _CatalogSkeleton(self.content)
        self._catalog_skeleton.hide()
        self.more_button = GlassPushButton("加载更多歌单", self.content)
        self.more_button.setFont(_entry_font(13))
        self.more_button.setProperty("glassRefractive", True)
        self.more_button.setGlassLight(False)
        self.more_button.setStyleSheet(self.empty_action.styleSheet())
        self.more_button.clicked.connect(self._load_more_clicked)
        self.scroll.viewport().setMouseTracking(True)
        self.scroll.viewport().installEventFilter(self)
        self.scroll.verticalScrollBar().valueChanged.connect(self._scroll_changed)
        self._layout()

    @property
    def collections(self) -> tuple[LibraryCollection, ...]:
        return self._results if self.search.text().strip() else self._catalog

    @property
    def browse_collection(self):
        return self._selected_collection

    def set_now_playing(self, track):
        previous = self._now_playing
        self._accumulate_album_rotation()
        self._now_playing = track
        art = getattr(track,'album_art_bytes',b'')
        if art != self._now_playing_art:
            self._now_playing_art = art
            self._now_playing_image = QImage()
            if art:
                self._now_playing_image.loadFromData(art)
        if track is not None:
            cover_key = (self._now_playing_image.cacheKey(), track.album_color.rgba(),
                         self.source_button.source.source_id)
            if cover_key != self._header_album_key:
                self._header_album_key = cover_key
                self._header_album_image = QImage(self._now_playing_image)
                if self._header_album_image.isNull():
                    self._header_album_image = QImage(128, 128, QImage.Format.Format_ARGB32_Premultiplied)
                    self._header_album_image.fill(QColor(*track.album_color.rgba()))
                    with QPainter(self._header_album_image) as painter:
                        paint_music_source_icon(painter, QRectF(38, 38, 52, 52), self.source_button.source.source_id)
                self.now_playing_button.update()
            self.now_playing_button.setAccessibleDescription(f"{track.title} · {track.artist}")
        self._retarget_header(track is not None)
        self._sync_album_rotation()
        self._prepare_background_palette()
        self._sync_background_animation()
        background_changed = self._update_background()
        self.search.update()
        self.progress_button.update()
        if background_changed:
            self.update()
        if (getattr(previous,'track_id',''),getattr(previous,'title',''),getattr(previous,'artist','')) != (
                getattr(track,'track_id',''),getattr(track,'title',''),getattr(track,'artist','')) or art != getattr(previous,'album_art_bytes',b''):
            self.update(QRect(16,16,self.width()-32,80))

    def _retarget_header(self, has_track):
        target = float(bool(has_track))
        self.now_playing_button.setEnabled(bool(has_track))
        if target == self._header_target:
            return
        self._header_target = target
        self._header_motion.stop()
        if not self.isVisible():
            self._set_header_fraction(target)
            return
        self._header_motion.setStartValue(self._header_fraction)
        self._header_motion.setEndValue(target)
        self._header_motion.start()

    def _set_header_fraction(self, value):
        self._header_fraction = max(0., min(1., float(value)))
        self._layout_header()
        self.update(QRect(16, 16, max(1, self.width()-32), 80))

    def _layout_header(self):
        fraction = self._header_fraction
        self._account_avatar_rect = QRectF(_mix(24, 108, fraction), _mix(24, 30, fraction),
                                           _mix(64, 36, fraction), _mix(64, 36, fraction))
        # Let identity clear the old slot before the album grows into it;
        # reversing the same path retracts the album before identity returns.
        side = 64 * max(0., (fraction-.55)/.45)
        self.now_playing_button.setGeometry(QRectF(56-side/2, 56-side/2, side, side).toAlignedRect())
        self.now_playing_button.setVisible(side > .5)
        label_x = round(_mix(104, 154, fraction))
        self.account_name_label.setGeometry(label_x, round(_mix(30, 32, fraction)),
                                            max(1, self.source_button.x()-16-label_x), 30)
        name = self._account.nickname if self._account else ""
        full_name = name or "音乐账号"
        self.account_name_label.setText(self.account_name_label.fontMetrics().elidedText(
            full_name, Qt.TextElideMode.ElideRight, self.account_name_label.width()))
        self.account_name_label.setToolTip(f"<qt>{escape(full_name)}</qt>")
        self.account_name_label.setAccessibleName(full_name)
        self.account_name_label.setAccessibleDescription(self.source_button.source.name if self._account else "")

    def _confirm_logout(self):
        """Keep the current account until an explicit, account-bound answer."""
        if self._logout_confirmation is not None:
            self._logout_confirmation.raise_()
            self._logout_confirmation.activateWindow()
            return
        account = self._account
        if account is None:
            return
        identity = (account.source_id, account.user_id)
        dialog = QMessageBox(self)
        dialog.setWindowTitle(f"{APP_NAME} · 退出登录")
        dialog.setText("确认退出当前音乐账号？")
        dialog.setInformativeText("退出后需要重新登录才能访问此账号的音乐库。")
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        dialog.button(QMessageBox.StandardButton.Ok).setText("退出登录")
        dialog.button(QMessageBox.StandardButton.Cancel).setText("取消")
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        dialog.setEscapeButton(QMessageBox.StandardButton.Cancel)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._logout_confirmation = dialog

        def finished(result):
            if self._logout_confirmation is not dialog:
                return
            self._logout_confirmation = None
            current = self._account
            confirmed = result == int(QMessageBox.StandardButton.Ok)
            if confirmed and current is not None and (current.source_id, current.user_id) == identity and self.isVisible():
                self.logoutRequested.emit()
            dialog.deleteLater()

        dialog.finished.connect(finished)
        dialog.open()

    def _album_should_rotate(self):
        return (not self._background_closing and not self._navigation_cover_frozen
                and self.isVisible() and not self.isMinimized()
                and self._now_playing is not None and self._now_playing.is_playing)

    def _accumulate_album_rotation(self):
        if self._album_rotation_sample is None:
            return
        now = time.monotonic()
        self._album_rotation = (self._album_rotation + max(0., now-self._album_rotation_sample)
                                * 360. / self.ALBUM_REVOLUTION_SECONDS) % 360.
        self._album_rotation_sample = now

    def _sync_album_rotation(self):
        if not self._album_should_rotate():
            self._album_rotation_timer.stop()
            self._album_rotation_sample = None
            return
        if not self._album_rotation_timer.isActive():
            self._album_rotation_sample = time.monotonic()
            self._album_rotation_timer.start()

    def _advance_album_rotation(self):
        if not self._album_should_rotate():
            self._sync_album_rotation()
            return
        self._accumulate_album_rotation()
        self.now_playing_button.update()

    def navigation_cover_state(self):
        """Global album bounds, unrotated image and clockwise angle in degrees."""
        if self.now_playing_button.isHidden():
            return QRect(), QImage(), 0.
        button = self.now_playing_button
        rect = button.rect().adjusted(1, 1, -1, -1)
        return (QRect(button.mapToGlobal(rect.topLeft()), rect.size()),
                QImage(self._header_album_image), self._album_rotation)

    def freeze_navigation_cover(self):
        self._accumulate_album_rotation()
        self._navigation_cover_frozen = True
        self._sync_album_rotation()
        # The navigation layer owns a frozen copy of this page. Do not keep
        # repainting scenery or missing covers behind its invisible target.
        self._sync_background_animation()
        self._sync_artwork_skeleton()
        return self.navigation_cover_state()

    def resume_navigation_cover(self):
        self._navigation_cover_frozen = False
        self._sync_album_rotation()
        self._sync_background_animation()
        self._sync_artwork_skeleton()

    def navigation_background_frame(self, excluded_cover_rect=None):
        """Capture the UI without the separately animated navigation album."""
        excluded = []
        if excluded_cover_rect is not None:
            for cover in self._collection_covers():
                center = cover.mapToGlobal(cover.rect().center())
                if cover.isVisible() and excluded_cover_rect.contains(center):
                    cover._navigation_capture_hidden = True
                    excluded.append(cover)
        self._navigation_capture_hide_cover = True
        try:
            return self.grab().toImage()
        finally:
            self._navigation_capture_hide_cover = False
            self.now_playing_button.update()
            for cover in excluded:
                cover._navigation_capture_hidden = False
                cover.update()

    def navigation_backdrop_frame(self):
        return QImage(self._background_frame)

    def navigation_opening_cover_state(self):
        """Capture the selected playlist's actual prepared pose once ready."""
        cover = self._opening_cover
        if cover is not None:
            try:
                if cover.isVisible():
                    rect = cover.artwork_rect().toAlignedRect()
                    global_rect = QRect(cover.mapToGlobal(rect.topLeft()), rect.size())
                    local = QRect(self.mapFromGlobal(global_rect.topLeft()), global_rect.size())
                    return global_rect, self.grab(local).toImage(), 0.
            except RuntimeError:
                pass
        return None

    def set_notice(self, message):
        self._notice = str(message or '')
        self._layout(); self.update()

    def _toggle_library_expanded(self, expanded):
        self.set_library_expanded(expanded, animate=True)
        self.libraryExpandChanged.emit(bool(expanded))

    def set_library_expanded(self, expanded, *, animate=False):
        changed = self._library_expanded != bool(expanded)
        self._library_expanded = bool(expanded)
        blocked = self.expand_button.blockSignals(True)
        self.expand_button.setChecked(bool(expanded))
        self.expand_button.setText("全部收拢" if expanded else "全部展开")
        self.expand_button.blockSignals(blocked)
        if changed:
            self._selected_group_key = None
            self._expanded_groups.clear(); self._expanded_playlists.clear()
            self._selected_collection = None
            self._rebuild_rows(preserve_scroll=True,animate=animate)

    def _expand_group(self, key):
        if self._opening or self._library_expanded:
            return
        groups = self._catalog_groups()
        if key not in groups:
            return
        old = self._selected_group_key
        if old == key:
            self._selected_group_key = None
            self._peer_order = []
        else:
            peers = [item for item in self._peer_order if item in groups and item != old]
            peers.extend(item for item in groups if item != old and item not in peers)
            # Adjacent peers fill the clicked slot first. The former top
            # selection moves into that adjacent peer's previous slot.
            if old is not None:
                clicked = peers.index(key)
                adjacent = clicked + 1 if clicked + 1 < len(peers) else clicked - 1
                if adjacent >= 0:
                    peers[clicked], peers[adjacent] = peers[adjacent], old
                else:
                    peers[clicked] = old
            else:
                peers = [item for item in groups if item != key]
            self._selected_group_key = key
            self._peer_order = [item for item in peers if item != key]
        self._expanded_groups = {key} if self._selected_group_key else set()
        self._rebuild_rows(preserve_scroll=False,animate=True,old_selected=old)

    def _expand_playlist(self, collection):
        # Legacy caller compatibility: collections are now direct play targets.
        if collection in self.collections:
            rect = QRect(self.mapToGlobal(QPoint(24,210)),QSize(132,132))
            self._collection_activated(collection,"default",rect)

    def set_playlist_tracks(self, collection, rows, *, loading=False, error='', has_more=False):
        # Retain the old data interface for callers retiring browse tasks. No
        # song shelf or browse result can re-enter the collection-only catalog.
        if collection is None:
            self._playlist_data.clear();self._expanded_playlists.clear()
            self._selected_collection = None
            return
        rows = tuple(rows)
        if not all(isinstance(row,LibraryCollection) and row.song is not None for row in rows):
            raise TypeError("Playlist tracks require immutable song collections")
        self._playlist_data[collection.key]=(rows,bool(loading),str(error),bool(has_more))

    def set_account(self, account: AccountSnapshot):
        if not isinstance(account, AccountSnapshot):
            raise TypeError("Music library requires an AccountSnapshot")
        if self._account is None or self._account.source_id != account.source_id or self._account.user_id != account.user_id:
            self._finish_section_motion()
            self._selected_group_key=None;self._peer_order=[]
            self._notice = self._message = self._error = ""
            self._artwork.clear()
            self._artwork_pending.clear()
            self._catalog = ()
            self._catalog_loading.clear()
            self._results = ()
            self._playlist_data.clear(); self._expanded_groups.clear(); self._expanded_playlists.clear()
            self._selected_collection = None
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
        self.source_button.setAccessibleName(f"{source.name}，切换音乐平台")
        self.source_button.setToolTip(f"当前来源：{source.name} · 点击切换平台")
        self.source_button.update()
        self._layout()
        self.update()

    def set_collections(self, collections: tuple[LibraryCollection, ...]):
        values = tuple(collections)
        if not all(isinstance(item,LibraryCollection) for item in values):
            raise TypeError("Music library collections must be immutable LibraryCollection values")
        self._catalog = values
        if not self.search.text().strip():
            self._rebuild_rows(preserve_scroll=True)

    def set_search_results(self, collections: tuple[LibraryCollection, ...]):
        values = tuple(collections)
        if not all(isinstance(item,LibraryCollection) for item in values):
            raise TypeError("Search results must be immutable LibraryCollection values")
        self._results = values
        if self.search.text().strip():
            self._rebuild_rows(preserve_scroll=True)

    def _catalog_groups(self):
        grouped = {}
        for item in self._catalog:
            key = 'for_you' if item.kind in {'daily','fm'} else item.group
            if key not in {'for_you','recommended','personal'}:
                key = 'personal'
            grouped.setdefault(key,[]).append(item)
        return {key:tuple(grouped[key]) for key in ('for_you','recommended','personal') if key in grouped}

    def _finish_section_motion(self):
        self._section_motion.stop()
        overlay = self._section_overlay
        self._section_overlay = None
        if overlay is not None:
            overlay.hide();overlay.setParent(None);overlay.deleteLater()
        for item in self._visual_items():
            item.setVisible(True)
        self.content.setEnabled(not self._opening)

    def _rebuild_rows(self, *, preserve_scroll=False, animate=False, old_selected=None):
        previous_scroll = self.scroll.verticalScrollBar().value() if preserve_scroll else 0
        self._toolbar.stop()
        self._finish_section_motion()
        previous = {self._section_key(item):(QRectF(item.geometry()),item.grab().toImage())
                    for item in self._visual_items()} if animate and self.isVisible() else {}
        horizontal = {s.group_key:s.scroll.horizontalScrollBar().value() for s in self._shelves}
        for row in self._visual_items():
            row.hide();row.setParent(None);row.deleteLater()
        self._rows,self._groups,self._shelves = [],[],[]
        if self.search.text().strip():
            for collection in self._results:
                source_id=self._account.source_id if self._account else 'netease'
                row=_CollectionRow(collection,source_id,self.content)
                row.activated.connect(self._collection_activated)
                row.queueRequested.connect(self.queueNextRequested)
                row.cover.image=self._artwork.get(collection.key,QImage())
                row.setEnabled(not self._opening);row.show();self._rows.append(row)
        else:
            groups=self._catalog_groups()
            if self._selected_group_key not in groups:
                self._selected_group_key=None
            titles={'for_you':'为你精选','recommended':'推荐歌单','personal':'个人歌单'}
            for key,items in groups.items():
                if self._library_expanded or key==self._selected_group_key:
                    shelf=_CollectionShelf(key,titles[key],items,self)
                    shelf.setEnabled(not self._opening);shelf.show();self._shelves.append(shelf)
                else:
                    group=_LibraryGroup(titles[key],items,self.content,key=key)
                    group.images=self._artwork
                    group.clicked.connect(lambda checked=False,key=key:self._expand_group(key))
                    group.show();self._groups.append(group)
        self._layout()
        for shelf in self._shelves:
            shelf.scroll.horizontalScrollBar().setValue(horizontal.get(shelf.group_key,0))
        self.scroll.verticalScrollBar().setValue(previous_scroll)
        if previous:
            frames={}
            for item in self._visual_items():
                key=self._section_key(item);target=QRectF(item.geometry())
                start,before=previous.get(key,(QRectF(target.x(),target.y()+24,target.width(),target.height()),QImage()))
                frames[key]=(before,item.grab().toImage(),start,target)
            self._section_overlay=CatalogMotionOverlay(self.content,frames,old_selected=old_selected,new_selected=self._selected_group_key)
            self._section_overlay.groupRequested.connect(self._expand_group)
            self.content.setEnabled(True)
            for item in self._visual_items():item.hide()
            self._section_overlay.show();self._section_overlay.raise_()
            self._section_motion.setDuration(640 if old_selected and self._selected_group_key else 360)
            self._section_motion.setStartValue(0.);self._section_motion.setEndValue(1.);self._section_motion.start()
        self._request_visible_artwork();self.update()

    def _visual_items(self):
        return [*self._shelves,*self._rows,*self._groups]

    @staticmethod
    def _section_key(item):
        return getattr(item,'group_key',('collection',item.collection.key) if hasattr(item,'collection') else None)

    def _advance_sections(self,value):
        if self._section_overlay is not None:
            self._section_overlay.fraction=float(value);self._section_overlay.update()

    def _load_more_clicked(self):
        if self._has_more and not self._more_requested and not self._loading:
            self._more_requested=True
            self.moreRequested.emit()

    def set_has_more(self, has_more: bool):
        self._has_more = bool(has_more)
        self._more_requested = False
        self._layout()

    def _scroll_changed(self, value):
        self._request_visible_artwork()
        bar = self.scroll.verticalScrollBar()
        if (self._has_more and not self._more_requested and not self._loading and not self._catalog_loading and
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
        for group in self._groups:
            if visible.intersects(group.geometry()):
                requested.extend((item.key,item.artwork_url) for item in group.items[:3] if item.artwork_url)
        for shelf in self._shelves:
            if not visible.intersects(shelf.geometry()):
                continue
            start = shelf.scroll.horizontalScrollBar().value()
            for item,cover,_ in shelf.tiles:
                if cover.x()+cover.width() >= start-150 and cover.x() <= start+shelf.scroll.width()+150 and item.artwork_url:
                    requested.append((item.key,item.artwork_url))
        for key, url in requested:
            image = self._artwork.get(key)
            if image is not None and not image.isNull():
                self._artwork.move_to_end(key)
            elif key not in self._artwork_pending:
                self._artwork_pending.add(key)
                self.artworkNeeded.emit(key, url)
        self._sync_artwork_skeleton()

    def _collection_covers(self):
        # Rows/shelves already own the exact cover set. Do not walk labels,
        # scrollbars and every other QObject on each 33ms skeleton pulse.
        for row in self._rows:
            yield row.cover
        for shelf in self._shelves:
            for _, cover, _ in shelf.tiles:
                yield cover

    def _missing_artwork_widgets(self):
        if self._navigation_cover_frozen or not self.isVisible() or self.isMinimized():
            return ()
        covers = tuple(cover for cover in self._collection_covers()
            if cover.loading and cover.image.isNull() and not cover.visibleRegion().isEmpty())
        groups = tuple(group for group in self._groups if not group.visibleRegion().isEmpty()
            and any((item.artwork_url or item.kind in {"daily", "fm"})
                    and self._artwork.get(item.key, QImage()).isNull() for item in group.items[:3]))
        skeletons = (self._catalog_skeleton,) if not self._catalog_skeleton.visibleRegion().isEmpty() else ()
        return covers + groups + skeletons

    def _sync_artwork_skeleton(self):
        if self._missing_artwork_widgets():
            if not self._artwork_skeleton_timer.isActive():
                self._artwork_skeleton_timer.start()
        else:
            self._artwork_skeleton_timer.stop()

    def _pulse_artwork_skeleton(self):
        targets = self._missing_artwork_widgets()
        if not targets:
            self._artwork_skeleton_timer.stop()
        for target in targets:
            target.update()

    def set_loading(self, loading: bool, message: str = ""):
        self._loading = bool(loading)
        self._message = str(message)
        if loading:
            self._error = ""
        self._layout()
        self._sync_artwork_skeleton()
        self.update()

    def set_catalog_loading(self, kind: str, loading: bool):
        """Keep independent catalog requests visible without blocking search."""
        if loading:
            self._catalog_loading.add(str(kind))
        else:
            self._catalog_loading.discard(str(kind))
        self._layout()
        self._sync_artwork_skeleton()
        self.update()

    def set_error(self, message: str):
        self._loading = False
        self._error = str(message)
        self._layout()
        self.update()

    def set_artwork(self, key: str, image: QImage):
        # Completion releases the request even if a newer search removed its row.
        self._artwork_pending.discard(key)
        known = {item.key for item in self._catalog + self._results}
        known.update(row.key for rows,_,_,_ in self._playlist_data.values() for row in rows)
        if not isinstance(image, QImage) or (key != "account" and key not in known):
            return
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
            for shelf in self._shelves:
                for item,cover,_ in shelf.tiles:
                    if item.key == oldest:
                        cover.image = QImage(); cover.update()
        for row in self._rows:
            if row.collection.key == key:
                row.cover.image = artwork
                row.cover.update()
        for group in self._groups:
            group.update()
        for shelf in self._shelves:
            for item,cover,_ in shelf.tiles:
                if item.key == key:
                    cover.image = artwork; cover.update()
        self._sync_artwork_skeleton()
        self.update()

    def _search_changed(self, text):
        self._search_timer.stop()
        self._has_more = False
        self._more_requested = False
        self._error = ""
        self._results = ()
        self._rebuild_rows()
        if text.strip():
            self.search.set_expanded(True)
            self._search_timer.start()
        else:
            self.searchRequested.emit("")

    def _submit_search(self):
        self._search_timer.stop()
        self.searchRequested.emit(self.search.text().strip())

    def _activate_search_button(self):
        if not self.search.expanded:
            self.search.set_expanded(True, focus=True)
        else:
            self._submit_search()

    def _clear_search(self):
        if self.search.isEnabled() and self.search.text().strip():
            # The controller owns cancellation of an opening search result.
            # Use its existing Back path so a late stream cannot reopen it.
            self.backRequested.emit()
            self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _recover_empty_error(self):
        """Use existing safe navigation/search actions, never retry playback."""
        if self._visual_items() or not self._error:
            return
        if self.search.text().strip() and self.search.isEnabled():
            self._submit_search()
        else:
            self.sourceSwitchRequested.emit()

    def _focus_empty_search(self):
        """Help the user begin or edit the existing platform search."""
        if self.search.isEnabled():
            self.set_toolbar_expanded(True)
            self.search.set_expanded(True, focus=True)
            self.search.selectAll()

    def _collection_activated(self, collection, mode, rect):
        if self._opening or self._section_overlay is not None or not self.isVisible():
            return
        self._opening = True
        self._opening_cover = next((cover for cover in self.findChildren(_CollectionCover)
            if cover.isVisible() and rect.contains(cover.mapToGlobal(cover.rect().center()))), None)
        if self._opening_cover is not None:
            self._opening_cover.set_opening_pose(True)
        self.content.setEnabled(False)
        for row in self._rows:
            row.setEnabled(False)
        for shelf in self._shelves:
            shelf.setEnabled(False)
        self.collectionRequested.emit(collection, mode, rect)

    def finish_open_transition(self):
        self._opening = False
        self.content.setEnabled(self._section_overlay is None)
        for row in self._rows:
            row.setEnabled(True)
        for shelf in self._shelves:
            shelf.setEnabled(True)
        cover, self._opening_cover = self._opening_cover, None
        if cover is not None:
            try:
                cover.set_opening_pose(False)
            except RuntimeError:
                pass

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
        if self._section_overlay is not None:
            self._finish_section_motion()
        self._update_background()
        self.back_button.hide()
        self.switch_button.hide()
        self._header_title_rect = QRectF()
        self.close_button.setGeometry(self.width()-68, 20, 44, 44)
        self.logout_button.setGeometry(self.width()-120, 20, 44, 44)
        self.logout_button.show()
        self.source_button.setGeometry(self.width()-172, 20, 44, 44)
        self.source_button.show()
        self.expand_button.setGeometry(self.width()-164, 158, 140, 44)
        self._layout_header()
        self._layout_search()
        query = bool(self.search.text().strip())
        self.search.setPlaceholderText(f"搜索{self.source_button.source.name}的歌曲与歌单")
        self.search.setAccessibleDescription("点击搜索图标向左展开；点击左侧进度圆环收起；Ctrl+F 搜索，Esc 返回")
        has_items = bool(self._visual_items())
        skeleton = bool(self._catalog_loading if not query else "search" in self._catalog_loading)
        skeleton = skeleton or (self._loading and not has_items)
        footer = 44 if (has_items or skeleton) and (self._error or self._notice) else 12
        self.scroll.setGeometry(16, 210, self.width()-32,max(1,self.height()-210-footer))
        peer_layout = bool(self._selected_group_key and not self._library_expanded and not query)
        block_height = sum(224 for _ in self._shelves) + 124*len(self._rows)
        block_height += 124 if peer_layout and self._groups else 124*len(self._groups)
        if skeleton:
            block_height += 216
        content_height = max(1,block_height+(60 if self._has_more else 8))
        scrollbar_width = self.scroll.verticalScrollBar().sizeHint().width() if content_height>self.scroll.height() else 0
        self.content.resize(max(1,self.scroll.width()-scrollbar_width),content_height)
        top=4
        for item in [*self._shelves,*self._rows]:
            height = 224 if isinstance(item,_CollectionShelf) else 124
            item.setGeometry(0,top,self.content.width(),height)
            if isinstance(item,_CollectionRow):item.set_fraction(1.0)
            top+=height
        if peer_layout and self._groups:
            order={key:index for index,key in enumerate(self._peer_order)}
            peers=sorted(self._groups,key=lambda item:order.get(item.group_key,99))
            gap=12;slot=(self.content.width()-gap*(len(peers)-1))/len(peers)
            for index,item in enumerate(peers):
                item.setGeometry(round(index*(slot+gap)),top,round(slot),124)
            top+=124
        else:
            for item in self._groups:
                item.setGeometry(0,top,self.content.width(),124);top+=124
        self._catalog_skeleton.setVisible(skeleton)
        self._catalog_skeleton.setGeometry(0, top, self.content.width(), 216)
        if skeleton:
            top += 216
        self.more_button.setVisible(self._has_more and not skeleton)
        self.more_button.setGeometry(max(8,(self.content.width()-180)//2),top+4,180,44)
        empty_ready = not has_items and not skeleton and not self._loading and not self._error and self.search.isEnabled()
        self.empty_action.setVisible(empty_ready)
        if empty_ready:
            self.empty_action.raise_()
        self.empty_action.setText("修改搜索词" if query else "搜索歌曲")
        self.empty_action.setAccessibleName(self.empty_action.text())
        hint = "尝试歌曲名、歌手名或歌单名" if query else "可搜索当前平台的歌曲与歌单"
        self.empty_action.setAccessibleDescription(hint)
        hint_height = 28 if self.scroll.height() >= 118 else 0
        block_height = 26 + hint_height + 12 + 44
        empty_y = self.scroll.y() + max(0, (self.scroll.height() - block_height) // 2)
        self._empty_title_rect = QRectF(24, empty_y, self.width() - 48, 26)
        self._empty_hint_rect = QRectF(24, empty_y + 26, self.width() - 48, hint_height)
        self.empty_action.setGeometry((self.width() - 160) // 2, empty_y + 26 + hint_height + 12, 160, 44)
        recovery_ready = not has_items and not skeleton and bool(self._error) and not self._loading
        self.recovery_action.setVisible(recovery_ready)
        self.recovery_action.setText("重新搜索" if query and self.search.isEnabled() else "切换平台")
        self.recovery_action.setAccessibleName(self.recovery_action.text())
        self.recovery_action.setGeometry(self.empty_action.geometry())
        if recovery_ready:
            self.recovery_action.raise_()

    def _layout_search(self):
        self.search.setGeometry(self.search.strip_geometry(QRect(24, 104, self.width()-48, 44)))
        query = bool(self.search.text().strip())
        if query != getattr(self, "_search_query_padding", None):
            self._search_query_padding = query
            self.search.setTextMargins(16, 0, 100 if query else 52, 0)
        self.search_button.setGeometry(self.search.width()-44, 0, 44, 44)
        self.clear_search_button.setVisible(query and self.search._ink > 0.)
        self._clear_search_opacity.setOpacity(self.search._ink)
        self.clear_search_button.setGeometry(self.search.width()-88, 0, 44, 44)
        self.progress_button.layout_strip(QRect(24, 104, self.width()-48, 44))

    def set_background_context(self, settings_getter, run_background=None):
        """Share the app's existing animation settings and cold-work runner."""
        self._background_settings_getter = settings_getter
        self._background_runner = run_background
        self.on_background_setting_changed()

    def _background_setting(self, key, default):
        getter = self._background_settings_getter
        return getter(key, default) if callable(getter) else default

    def _background_intensity(self):
        try:
            value = float(self._background_setting("v2_music_flow_intensity", .74))
        except (TypeError, ValueError, OverflowError):
            value = .74
        return max(0., min(1., value)) if math.isfinite(value) else .74

    def on_background_setting_changed(self, key="", value=None):
        if key and key not in {"v2_music_flow_intensity", "v2_animation_fps"}:
            return
        self._sync_background_animation()
        if self._update_background():
            self.update()

    def _background_should_animate(self):
        return (not self._background_closing and not self._navigation_cover_frozen
                and self.isVisible() and not self.isMinimized()
                and self._now_playing is not None and self._now_playing.is_playing
                and self._background_intensity() > 0.)

    def _sync_background_animation(self):
        if not self._background_should_animate():
            self._background_timer.stop()
            self._background_clock.reset(time.monotonic())
            return
        try:
            fps = float(self._background_setting("v2_animation_fps", 60))
        except (TypeError, ValueError, OverflowError):
            fps = 60.
        fps = max(1., min(30., fps)) if math.isfinite(fps) else 30.
        interval = max(round(1000. / fps), math.ceil(AlbumFlowBackgroundRenderer.FRAME_SECONDS * 1000.))
        if not self._background_timer.isActive():
            self._background_clock.reset(time.monotonic())
            self._background_timer.start(interval)
        elif self._background_timer.interval() != interval:
            self._background_timer.setInterval(interval)

    def _advance_background(self):
        if not self._background_should_animate():
            self._sync_background_animation()
            return
        self._background_seconds += self._background_clock.advance(time.monotonic())
        if self._update_background():
            self.update()

    def _prepare_background_palette(self):
        """Use provider colors now; prepare each new cover away from paint."""
        track = self._now_playing
        if track is None:
            self._background_palette = None
            self._background_palette_current_key = None
            self._background_palette_pending = None
            return
        primary, secondary = QColor(*track.album_color.rgba()), QColor(*track.secondary_color.rgba())
        image = self._now_playing_image
        key = (image.cacheKey(), primary.rgba(), secondary.rgba())
        if key != self._background_palette_current_key:
            self._background_palette_current_key = key
            self._background_palette = artwork_surface_palette(QImage(), primary, secondary)
            self._background_palette_key = None
        if key == self._background_palette_key:
            return
        cached = cached_artwork_surface_palette(image, primary, secondary)
        if cached is not None or image.isNull():
            self._background_palette = cached or self._background_palette
            self._background_palette_key = key
            return
        if key == self._background_palette_failed_key or key == self._background_palette_worker_key:
            return
        self._background_palette_pending = (key, QImage(image), primary, secondary)
        self._start_background_palette_worker()

    def _start_background_palette_worker(self):
        if (self._background_closing or not self.isVisible() or self._background_palette_worker_key is not None
                or self._background_palette_pending is None):
            return
        key, image, primary, secondary = self._background_palette_pending
        self._background_palette_pending = None
        self._background_palette_worker_key = key
        self._background_palette_worker_token += 1
        token = self._background_palette_worker_token
        deliver = self.backgroundPaletteReady
        def prepare():
            try:
                result = artwork_surface_palette(image, primary, secondary)
            except Exception:
                result = None
            try:
                deliver.emit(token, key, result)
            except RuntimeError:
                pass
        try:
            if callable(self._background_runner):
                self._background_runner("LibraryArtworkPalette", prepare)
            else:
                threading.Thread(target=prepare, name="GluSpotLibraryArtworkPalette", daemon=True).start()
        except Exception:
            self._background_palette_worker_key = None
            self._background_palette_failed_key = key

    def _on_background_palette_ready(self, token, key, palette):
        if token != self._background_palette_worker_token or key != self._background_palette_worker_key:
            return
        self._background_palette_worker_key = None
        if not self._background_closing and key == self._background_palette_current_key:
            if palette is not None:
                self._background_palette, self._background_palette_key = palette, key
                if self._update_background():
                    self.update()
            else:
                self._background_palette_failed_key = key
        pending = self._background_palette_pending
        if pending is not None and pending[0] != self._background_palette_current_key:
            self._background_palette_pending = None
        self._start_background_palette_worker()

    def _background_scene_identity(self):
        track = self._now_playing
        return (getattr(track, 'track_id', ''), getattr(track, 'title', ''),
                getattr(track, 'artist', ''), self._now_playing_image.cacheKey())

    def _update_background(self):
        """One bounded scene raster, frozen whenever playback is not visible."""
        source = self.source_button.source
        palette = self._background_palette if self._now_playing is not None else None
        if palette is not None:
            colors = palette.ambient_colors or (palette.primary, palette.secondary)
        else:
            colors = (QColor("#303a43"), QColor(source.color), QColor("#d1d7dd"))
        weights = palette.ambient_weights if palette else ()
        intensity = self._background_intensity() if palette else 0.
        seconds = self._background_seconds if palette else 0.
        key = (self.width(), self.height(), source.source_id if palette is None else None,
               tuple(color.rgba() for color in colors), weights, intensity, seconds)
        if key == self._background_key:
            return False
        if (palette is None or (not self._navigation_cover_frozen
                and not self._background_should_animate() and self.isVisible()
                and self._background_key is not None and key[3:5] != self._background_key[3:5])):
            # A deliberate paused song/artwork change still gets its own
            # static colors; it must not need a running timer to settle them.
            self._background_renderer = AlbumFlowBackgroundRenderer()
        live_owner = self if self._background_should_animate() and glass_owner_is_live(self) else None
        frame = self._background_renderer.render(
            self.size(), colors, seconds, intensity=intensity, weights=weights,
            material_owner=live_owner, material_scene=self._background_scene_identity)
        self._background_key = key
        source_key = frame.cacheKey()
        if source_key == self._background_source_key:
            # A pending worker retains the displayed source. Do not detach
            # and reapply the same reading veil on every unchanged tick.
            return False
        self._background_frame = frame.copy()
        with QPainter(self._background_frame) as painter:
            painter.fillRect(self._background_frame.rect(), QColor(12, 14, 20, 142))
        self._background_source_key = source_key
        return True

    def eventFilter(self, watched, event):
        if watched is self.search and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.backRequested.emit()
            return True
        if watched is self.search and event.type() == QEvent.Type.EnabledChange:
            self._layout()
        if watched is self.search and event.type() in {QEvent.Type.FocusIn, QEvent.Type.MouseButtonPress}:
            self.set_toolbar_expanded(True)
        return super().eventFilter(watched, event)

    def mouseMoveEvent(self, event):
        if self._move_page_resize(event):
            return
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if self._begin_page_resize(event):
            return
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 60:
            handle = self.windowHandle()
            if handle is None or not handle.startSystemMove():
                self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._end_page_resize()
        self._drag_origin = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.backRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        if hasattr(self, "scroll"):
            self._finish_section_motion()
            self._layout()
            self._request_visible_artwork()
        super().resizeEvent(event)

    def hideEvent(self, event):
        self._end_page_resize()
        if self._logout_confirmation is not None:
            self._logout_confirmation.reject()
        self._finish_section_motion()
        self._artwork_skeleton_timer.stop()
        self._background_timer.stop()
        self._background_clock.reset(time.monotonic())
        self._album_rotation_timer.stop()
        self._album_rotation_sample = None
        self._header_motion.stop()
        self._set_header_fraction(self._header_target)
        self._toolbar.stop()
        self._section_motion.stop()
        self._search_timer.stop()
        self._artwork_pending.clear()
        self.finish_open_transition()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self._background_closing = False
        self._prepare_background_palette()
        self._sync_background_animation()
        self._sync_album_rotation()
        if self._now_playing is not None and not self._background_should_animate():
            self._background_renderer = AlbumFlowBackgroundRenderer()
            self._background_key = None
        self._update_background()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self._request_visible_artwork()
        if self.search.text().strip() and not self._results:
            self._search_timer.start()

    def closeEvent(self, event):
        self._finish_section_motion()
        self._artwork_skeleton_timer.stop()
        self._background_closing = True
        self._background_timer.stop()
        self._album_rotation_timer.stop()
        self._album_rotation_sample = None
        self._header_motion.stop()
        self._background_palette_pending = None
        self._toolbar.stop()
        self._section_motion.stop()
        self._search_timer.stop()
        self.finish_open_transition()
        self.closed.emit()
        super().closeEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "_background_timer"):
            self._sync_background_animation()
            self._sync_album_rotation()
            self._sync_artwork_skeleton()

    def paintEvent(self, event):
        with QPainter(self) as painter:
            # Navigation owns the scenery separately. Clear the backing store
            # before capturing only this page's foreground controls and text.
            navigation_capture = self._navigation_capture_hide_cover
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            panel = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            clip = QPainterPath()
            clip.addRoundedRect(panel, 24, 24)
            painter.save()
            painter.setClipPath(clip)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            if not navigation_capture:
                painter.drawImage(panel, self._background_frame)
            painter.restore()
            if not navigation_capture:
                paint_liquid_glass(painter, panel, radius=24, border_color=QColor("#454b56"))
            avatar = self._account_avatar_rect
            painter.setBrush(QColor("#292d36"))
            painter.setPen(QPen(QColor("#414651"), 1))
            painter.drawEllipse(avatar)
            image = self._artwork.get("account", QImage())
            if not image.isNull():
                painter.save()
                path = QPainterPath(); path.addEllipse(avatar)
                painter.setClipPath(path)
                _paint_crop(painter, avatar, image)
                painter.restore()
            painter.setFont(_entry_font(13)); painter.setPen(QColor("#aeb6c4"))
            subtitle = (f"{self._now_playing.title} · {self._now_playing.artist}" if self._now_playing else
                        self.source_button.source.name if self._account else "")
            subtitle_x = _mix(104, 108, self._header_fraction)
            painter.drawText(QRectF(subtitle_x,64,self.width()-subtitle_x-24,24), Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(subtitle,Qt.TextElideMode.ElideRight,
                                                             round(self.width()-subtitle_x-24)))
            section = "搜索结果" if self.search.text().strip() else "我的音乐"
            painter.setFont(_entry_font(17));painter.setPen(QColor("#e3e7ef"))
            painter.drawText(QRectF(24,164,max(1,self.expand_button.x()-40),30),Qt.AlignmentFlag.AlignVCenter,section)
            if not self._visual_items() and not self._catalog_skeleton.isVisible():
                painter.setFont(_entry_font(17))
                painter.setPen(QColor("#b8b8b8"))
                message = self._error or (self._message or "正在加载音乐库…" if self._loading else
                                         ("未找到歌曲或歌单" if self.search.text().strip() else "暂无可显示的歌单"))
                if self.empty_action.isVisible() or self.recovery_action.isVisible():
                    if self.recovery_action.isVisible():
                        # Long provider diagnostics may wrap; reserve only
                        # the existing title+hint lane above the safe action.
                        painter.drawText(self._empty_title_rect.united(self._empty_hint_rect),
                                         Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                                         painter.fontMetrics().elidedText(message, Qt.TextElideMode.ElideRight,
                                                                         self.width() - 48))
                        return
                    painter.drawText(self._empty_title_rect, Qt.AlignmentFlag.AlignCenter, message)
                    painter.setFont(_entry_font(13))
                    painter.setPen(QColor("#a6adbb"))
                    painter.drawText(self._empty_hint_rect, Qt.AlignmentFlag.AlignCenter,
                                     self.empty_action.accessibleDescription())
                else:
                    painter.drawText(QRectF(24, self.scroll.y(), self.width() - 48, self.scroll.height()),
                                     Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, message)
            elif self._error or self._notice:
                painter.setFont(_entry_font(13))
                painter.setPen(QColor("#e4b5ad") if self._error else QColor("#969cab"))
                painter.drawText(QRectF(24, self.height() - 36, self.width() - 48, 25), Qt.AlignmentFlag.AlignCenter,
                                 painter.fontMetrics().elidedText(self._error or self._notice or self._message, Qt.TextElideMode.ElideRight, self.width() - 48))
