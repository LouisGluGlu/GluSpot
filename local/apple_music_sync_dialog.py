"""Playlist sync window, in the settings window's style.

Look (confirmed by the user on 2026-11-18): the settings window's plain
grouped background, inset grouped cards (``AppleCard``), group titles and
footers outside the cards, Apple-style buttons and pop-ups from
``apple_settings_stylesheet``; candidates are iOS list rows with a blue tick
on the right; light and dark follow GluSpot's theme.

The window never talks to the network. The controller fills it, resolves
audio addresses and runs the reads, writes and audio checks on its workers.
Listening happens in the window's own small player, not the main queue.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizeGrip, QSizePolicy, QVBoxLayout, QWidget,
)

from api.apple_music_sync import SyncDecisions, sides, song_key
from local.apple_music_sync_flow import sync_direction_allowed

SOURCE_NAMES = {"netease": "网易云", "apple_music": "Apple Music"}
DIRECTION_LABELS = (("netease_to_apple", "网易云 → Apple Music"), ("apple_to_netease", "Apple Music → 网易云"))
MODE_LABELS = (("manual", "手动"), ("auto", "自动"))
MODE_HINTS = {
    "manual": "点「同步」才写入。来源里删掉的歌，删之前会再问你一次。",
    "auto": "每次打开 GluSpot 按这个方向自动同步：来源里加的歌会加过去，删的歌会删掉。目标歌单里的变动和拿不准的歌仍然等你来选。",
}
ROW_LIMIT = 300
CONTROL_WIDTH = 240  # pop-ups and the name field share one right edge
ROW_HPAD, ROW_VPAD = 16, 9
_log = logging.getLogger("GluSpot.sync")

# The current palette. The window sets it from GluSpot's theme when it opens.
_THEME: dict[str, str] = {}


def _use_theme(dark: bool) -> dict[str, str]:
    from local.settings_apple import apple_settings_palette
    palette = dict(apple_settings_palette(dark))
    # System status colours for the two appearances.
    palette.update({
        "dark": "1" if dark else "",
        "green": "#30d158" if dark else "#34c759",
        "orange": "#ff9f0a" if dark else "#ff9500",
        "yellow": "#ffd60a" if dark else "#e6b800",
        "red": "#ff453a" if dark else "#ff3b30",
        "pressed": "rgba(255, 255, 255, 34)" if dark else "rgba(0, 0, 0, 22)",
        "netease": "#ff5a5f" if dark else "#e0383e",
        "apple_music": "#ff5c7a" if dark else "#e8325a",
    })
    _THEME.clear()
    _THEME.update(palette)
    return _THEME


def _t(key: str) -> str:
    if not _THEME:
        _use_theme(True)
    return _THEME[key]


def _color(key: str) -> QColor:
    value = _t(key)
    if value.startswith("rgba("):
        r, g, b, a = (int(part) for part in value[5:-1].split(","))
        return QColor(r, g, b, a)
    return QColor(value)


def _font(size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont()
    font.setPixelSize(size)
    font.setWeight(weight)
    return font


def _label(text: str, size: int = 13, color: str = "text", weight=QFont.Weight.Normal, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setFont(_font(size, weight))
    value = color if color.startswith(("#", "rgba")) else _t(color)
    label.setStyleSheet(f"color: {value}; background: transparent;")
    label.setWordWrap(wrap)
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    if wrap:
        label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    return label


def _clock(seconds: float) -> str:
    seconds = int(round(seconds or 0))
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds > 0 else "--:--"


def _span(start: float, end: float) -> str:
    return f"{_clock(start) if start >= 1 else '0:00'}–{_clock(end)}"


class AppleSegmentedControl(QWidget):
    """Segmented control in the settings style: gray track, radius 7, lifted chosen segment."""

    changed = Signal(str)

    def __init__(self, options, parent=None, *, height: int = 28):
        super().__init__(parent)
        self._options = tuple(options)
        self._current = self._options[0][0]
        self._hover = self._pressed = -1
        self.setFont(_font(12, QFont.Weight.Medium))
        self.setFixedHeight(height)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(" / ".join(label for _key, label in self._options))

    def current(self) -> str:
        return self._current

    def set_current(self, key: str, *, notify: bool = False):
        if key not in {item[0] for item in self._options} or key == self._current:
            return
        self._current = key
        self.update()
        if notify:
            self.changed.emit(key)

    def sizeHint(self):
        width = max(self.fontMetrics().horizontalAdvance(label) for _key, label in self._options) + 28
        return QSize(width * len(self._options) + 4, self.height())

    def _segment(self, index: int) -> QRectF:
        inner = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        width = inner.width() / max(1, len(self._options))
        return QRectF(inner.left() + index * width, inner.top(), width, inner.height())

    def _index_at(self, x: float) -> int:
        for index in range(len(self._options)):
            segment = self._segment(index)
            if segment.left() <= x < segment.right():
                return index
        return -1

    def mouseMoveEvent(self, event):
        hover = self._index_at(event.position().x())
        if hover != self._hover:
            self._hover = hover
            self.update()

    def leaveEvent(self, _event):
        self._hover = self._pressed = -1
        self.update()

    def mousePressEvent(self, event):
        self._pressed = self._index_at(event.position().x())
        self.update()

    def mouseReleaseEvent(self, event):
        index = self._index_at(event.position().x())
        pressed, self._pressed = self._pressed, -1
        if self.isEnabled() and index >= 0 and index == pressed:
            self.set_current(self._options[index][0], notify=True)
        self.update()

    def paintEvent(self, _event):
        dark = bool(_t("dark"))
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if not self.isEnabled():
                painter.setOpacity(0.5)
            track = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_color("control"))
            painter.drawRoundedRect(track, 7, 7)
            painter.setFont(self.font())
            chosen = next(i for i, (key, _label) in enumerate(self._options) if key == self._current)
            for index, (_key, label) in enumerate(self._options):
                segment = self._segment(index)
                if index == chosen:
                    if not dark:
                        painter.setBrush(QColor(0, 0, 0, 22))
                        painter.drawRoundedRect(segment.translated(0, 0.6), 6, 6)
                    painter.setBrush(QColor(255, 255, 255, 46) if dark else QColor("#ffffff"))
                    painter.drawRoundedRect(segment, 6, 6)
                elif index == self._pressed:
                    painter.setBrush(_color("pressed"))
                    painter.drawRoundedRect(segment, 6, 6)
                elif index == self._hover:
                    painter.setBrush(_color("hover"))
                    painter.drawRoundedRect(segment, 6, 6)
                if index > 0 and chosen not in (index, index - 1):
                    painter.setPen(QPen(_color("separator"), 1))
                    x = segment.left() + 0.5
                    painter.drawLine(QPointF(x, segment.top() + 6), QPointF(x, segment.bottom() - 6))
                    painter.setPen(Qt.PenStyle.NoPen)
                painter.setPen(_color("text"))
                painter.drawText(segment, Qt.AlignmentFlag.AlignCenter, label)
                painter.setPen(Qt.PenStyle.NoPen)


class PlatformBadge(QLabel):
    """A small tinted tag naming the platform a song comes from."""

    def __init__(self, source_id: str, parent=None):
        super().__init__(SOURCE_NAMES.get(source_id, source_id), parent)
        tint = QColor(_t(source_id) if source_id in ("netease", "apple_music") else _t("secondary"))
        fill = QColor(tint)
        fill.setAlpha(36)
        self.setFont(_font(10, QFont.Weight.DemiBold))
        self.setStyleSheet(
            f"color: {tint.name()}; background: rgba({fill.red()},{fill.green()},{fill.blue()},{fill.alpha()});"
            " border-radius: 4px; padding: 1px 5px;")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)


class ListenButton(QAbstractButton):
    """Round play / stop key: gray control fill, accent while its song plays."""

    def __init__(self, parent=None, *, size: int = 26, text: str = ""):
        super().__init__(parent)
        self._text = text
        self.playing = False
        self._down = False
        width = size if not text else size + 8 + QFontMetrics(_font(11, QFont.Weight.Medium)).horizontalAdvance(text) + 6
        self.setFixedSize(width, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(text or "试听")
        self.setAccessibleName(text or "试听")

    def set_playing(self, playing: bool):
        if self.playing != playing:
            self.playing = playing
            self.setToolTip("停止" if playing else (self._text or "试听"))
            self.update()

    def mousePressEvent(self, event):
        self._down = True
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._down = False
        self.update()
        super().mouseReleaseEvent(event)

    def enterEvent(self, _event):
        self.update()

    def leaveEvent(self, _event):
        self._down = False
        self.update()

    def paintEvent(self, _event):
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            if self.playing:
                fill = _color("accent")
                if self._down:
                    fill = fill.darker(115)
            else:
                fill = _color("pressed" if self._down else ("control_hover" if self.underMouse() else "control"))
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, radius, radius)
            glyph_color = QColor("#ffffff") if self.playing else _color("text")
            center = QPointF(rect.left() + rect.height() / 2, rect.center().y())
            painter.setBrush(glyph_color)
            if self.playing:
                painter.drawRoundedRect(QRectF(center.x() - 3.5, center.y() - 3.5, 7, 7), 1.5, 1.5)
            else:
                path = QPainterPath()
                path.moveTo(center.x() - 2.5, center.y() - 4.5)
                path.lineTo(center.x() + 5, center.y())
                path.lineTo(center.x() - 2.5, center.y() + 4.5)
                path.closeSubpath()
                painter.drawPath(path)
            if self._text:
                painter.setPen(glyph_color)
                painter.setFont(_font(11, QFont.Weight.Medium))
                painter.drawText(rect.adjusted(rect.height() - 2, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter, self._text)


class ElidedLabel(QLabel):
    """One line that shortens itself with … instead of pushing the row wider than the window."""

    def __init__(self, text: str, size: int, color: str, weight=QFont.Weight.Normal, parent=None):
        super().__init__(parent)
        self._full = text
        self.setFont(_font(size, weight))
        self.set_color(color)
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(24)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._refit()

    def set_color(self, color: str):
        value = _t(color) if not color.startswith("#") and not color.startswith("rgba") else color
        self.setStyleSheet(f"color: {value}; background: transparent;")

    def full_text(self) -> str:
        return self._full

    def setText(self, text: str):  # noqa: N802 - Qt name
        self._full = text
        self.setToolTip(text)
        self._refit()

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(self.fontMetrics().horizontalAdvance(self._full) + 2, hint.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refit()

    def _refit(self):
        QLabel.setText(self, self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(1, self.width())))


class _CoverLoader(QObject):
    """Album covers for the window, fetched once each, small, off the paint path."""

    arrived = Signal(str)
    _instance = None

    @classmethod
    def shared(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        super().__init__()
        from PySide6.QtNetwork import QNetworkAccessManager
        self._network = QNetworkAccessManager(self)
        self._network.finished.connect(self._done)
        self._images: dict[str, object] = {}
        self._asked: set[str] = set()

    @staticmethod
    def url_for(song) -> str:
        url = getattr(song, "artwork_url", "") or ""
        if "music.126.net" in url and "param=" not in url:
            url += ("&" if "?" in url else "?") + "param=120y120"
        return url if url.startswith(("https://", "http://")) else ""

    def image(self, url: str):
        if not url:
            return None
        if url not in self._images and url not in self._asked:
            from PySide6.QtNetwork import QNetworkRequest
            self._asked.add(url)
            request = QNetworkRequest(QUrl(url))
            request.setTransferTimeout(8000)
            self._network.get(request)
        return self._images.get(url)

    def _done(self, reply):
        from PySide6.QtGui import QImage
        url = reply.request().url().toString()
        data = bytes(reply.readAll())
        reply.deleteLater()
        image = QImage()
        if image.loadFromData(data):
            self._images[url] = image.scaled(120, 120, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                             Qt.TransformationMode.SmoothTransformation)
            self.arrived.emit(url)


class CoverThumb(QWidget):
    """A song's album cover, rounded; a note glyph until the picture arrives."""

    def __init__(self, song, side: int = 40, parent=None):
        super().__init__(parent)
        self.setFixedSize(side, side)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._url = _CoverLoader.url_for(song)
        self.setToolTip(getattr(song, "album", "") or "")
        if self._url:
            _CoverLoader.shared().arrived.connect(self._arrived)

    def _arrived(self, url):
        if url == self._url:
            self.update()

    def paintEvent(self, _event):
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = max(4.0, self.width() * 0.14)
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            image = _CoverLoader.shared().image(self._url)
            if image is not None:
                painter.setClipPath(path)
                edge = min(image.width(), image.height())
                source = QRectF((image.width() - edge) / 2, (image.height() - edge) / 2, edge, edge)
                painter.drawImage(rect, image, source)
                painter.setClipping(False)
                painter.setPen(QPen(_color("separator"), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)
            else:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(_color("control"))
                painter.drawPath(path)
                painter.setPen(_color("secondary"))
                painter.setFont(_font(max(10, int(self.width() * 0.4))))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "♪")


class StatusPill(QLabel):
    """A tinted capsule naming a state: 没找到 red, 待确认 yellow, 预添加 green."""

    def __init__(self, text: str, color_key: str, parent=None):
        super().__init__(text, parent)
        tint = QColor(_t(color_key))
        fill = QColor(tint)
        fill.setAlpha(46)
        self.setFont(_font(12, QFont.Weight.DemiBold))
        self.setStyleSheet(
            f"color: {tint.name()}; background: rgba({fill.red()},{fill.green()},{fill.blue()},{fill.alpha()});"
            " border-radius: 10px; padding: 2px 9px;")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)


class SongLine(QWidget):
    """Title with its platform tag, artist · album under it, then duration and a listen key.

    Text shortens with … on a narrow window; the duration and the listen key
    keep their place on the right.
    """

    listen = Signal(object, float, float)  # song, start seconds, end seconds (0 = whole)

    def __init__(self, song, *, accent: str = "", note: str = "", listenable: bool = True, parent=None,
                 cover: int = 40, compare=None, differs: frozenset = frozenset()):
        super().__init__(parent)
        self.song = song
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(160)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        if accent:
            dot = QLabel()
            dot.setFixedSize(7, 7)
            dot.setStyleSheet(f"background: {_t(accent)}; border-radius: 3px;")
            row.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        if compare is not None:
            # The source's cover, small, then an arrow: the two albums side by side.
            row.addWidget(CoverThumb(compare, 26), 0, Qt.AlignmentFlag.AlignVCenter)
            arrow = _label("→", 12, "secondary")
            row.addWidget(arrow, 0, Qt.AlignmentFlag.AlignVCenter)
        if cover:
            row.addWidget(CoverThumb(song, cover), 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.title = ElidedLabel(song.title, 13, "orange" if "title" in differs else "text")
        head.addWidget(self.title, 1)
        head.addWidget(PlatformBadge(song.source_id), 0)
        if note:
            head.addWidget(_label(note, 11, "secondary"), 0)
        text.addLayout(head)
        self.meta = ElidedLabel(song.artist or "未知歌手", 12, "orange" if "artist" in differs else "secondary")
        text.addWidget(self.meta)
        if song.album:
            album = f"专辑：{song.album}"
            if compare is not None and compare.album and compare.album != song.album:
                album += f"（{SOURCE_NAMES.get(compare.source_id, '来源')}：{compare.album}）"
            self.album = ElidedLabel(album, 11, "orange" if "album" in differs else "secondary")
            text.addWidget(self.album)
        row.addLayout(text, 1)
        clock = _label(_clock(song.duration_seconds), 12, "orange" if "duration" in differs else "secondary")
        clock.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        row.addWidget(clock, 0)
        self.listen_button = None
        if listenable:
            self.listen_button = ListenButton(self)
            self.listen_button.clicked.connect(lambda _checked=False: self.listen.emit(self.song, 0.0, 0.0))
            row.addWidget(self.listen_button, 0)


class _PressRow(QAbstractButton):
    """A whole card row that can be tapped: gray while pressed, light gray on hover, iOS-style."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("appleRow", True)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(200)
        self._down = False

    def mousePressEvent(self, event):
        self._down = True
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._down = False
        self.update()
        super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._down = False
        self.update()
        super().leaveEvent(event)

    def _paint_plate_and_tick(self, painter: QPainter, tick_center_y: float):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._down or self.underMouse():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_color("pressed" if self._down else "hover"))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(4, 2, -4, -2), 8, 8)
        if self.isChecked():
            x = self.width() - ROW_HPAD - 12
            pen = QPen(_color("accent"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            path = QPainterPath()
            path.moveTo(x, tick_center_y)
            path.lineTo(x + 4, tick_center_y + 4.5)
            path.lineTo(x + 12, tick_center_y - 5)
            painter.drawPath(path)


class CandidateCard(_PressRow):
    """One candidate as a list row: tap to choose, the blue tick on the right shows the choice.

    The audio-check result and the passage keys sit on their own line under the
    song, so the row never grows wider than the window.
    """

    listen = Signal(object, float, float)

    def __init__(self, song, parent=None, *, differs: frozenset = frozenset(), tile: bool = False):
        super().__init__(parent)
        self.song = song
        self.tile = tile
        self.setAccessibleName(f"选 {song.title} / {song.artist}（{SOURCE_NAMES.get(song.source_id, '')}）")
        layout = QVBoxLayout(self)
        if tile:
            layout.setContentsMargins(12, 10, 12, 10)
        else:
            layout.setContentsMargins(ROW_HPAD, ROW_VPAD, ROW_HPAD + 28, ROW_VPAD)
        layout.setSpacing(4)
        self.line = SongLine(song, parent=self, differs=differs, cover=48 if tile else 40)
        self.line.listen.connect(self.listen)
        layout.addWidget(self.line)
        self.detail = QWidget(self)
        self.detail.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.side = QHBoxLayout(self.detail)
        self.side.setContentsMargins(0, 0, 0, 0)
        self.side.setSpacing(6)
        self.verdict = ElidedLabel("", 12, "secondary")
        self.side.addWidget(self.verdict, 1)
        self.detail.hide()
        layout.addWidget(self.detail)
        self.diff_buttons: list[ListenButton] = []

    def sizeHint(self):
        return QSize(320, self.layout().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(200, self.layout().minimumSize().height())

    def set_verdict(self, text: str, color: str):
        self.verdict.setText(text)
        self.verdict.set_color(color)
        self.detail.setVisible(bool(text))
        self.updateGeometry()

    def add_diff_button(self, label: str, start: float, end: float, song=None):
        button = ListenButton(self.detail, size=22, text=label)
        target = song or self.song
        button.clicked.connect(lambda _checked=False: self.listen.emit(target, start, end))
        self.side.addWidget(button, 0)
        self.diff_buttons.append(button)
        self.detail.show()
        return button

    def paintEvent(self, _event):
        with QPainter(self) as painter:
            if not self.tile:
                self._paint_plate_and_tick(painter, ROW_VPAD + self.line.height() / 2)
                return
            _paint_tile(painter, self, chosen=self.isChecked(), pressed=self._down, hover=self.underMouse())


def _paint_tile(painter: QPainter, widget: QWidget, *, chosen: bool = False, pressed: bool = False,
                hover: bool = False, color_key: str = "green"):
    """A song tile in a comparison: a soft plate; chosen gets a coloured ring, a tint and a tick badge."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(widget.rect()).adjusted(1, 1, -1, -1)
    if pressed:
        rect = rect.adjusted(1.5, 1, -1.5, -1)
    accent = _color(color_key)
    if chosen:
        fill = QColor(accent)
        fill.setAlpha(34)
        painter.setBrush(fill)
        painter.setPen(QPen(accent, 2))
    else:
        painter.setBrush(_color("pressed" if pressed else ("hover" if hover else "control")))
        painter.setPen(QPen(_color("separator"), 1))
    painter.drawRoundedRect(rect, 10, 10)
    if chosen:
        badge = QRectF(rect.right() - 22, rect.top() + 6, 16, 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawEllipse(badge)
        painter.setPen(QPen(QColor("#ffffff"), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(badge.left() + 4, badge.center().y()), QPointF(badge.left() + 7, badge.bottom() - 4.5))
        painter.drawLine(QPointF(badge.left() + 7, badge.bottom() - 4.5), QPointF(badge.right() - 3.5, badge.top() + 4.5))


class SourceTile(QWidget):
    """The left-hand tile of a comparison: the source song, not clickable."""

    def __init__(self, song, parent=None):
        super().__init__(parent)
        self.song = song
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(180)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        self.line = SongLine(song, parent=self, cover=48)
        layout.addWidget(self.line)
        self.listen = self.line.listen
        self.listen_button = self.line.listen_button

    def paintEvent(self, _event):
        with QPainter(self) as painter:
            _paint_tile(painter, self)


class ActionPill(_PressRow):
    """A decision key under a comparison (就是这首 / 不是，跳过): filled with its colour once chosen."""

    def __init__(self, text: str, color_key: str = "green", parent=None):
        super().__init__(parent)
        self.setText(text)
        self.color_key = color_key
        self.setProperty("appleRow", False)
        self.setFont(_font(13, QFont.Weight.DemiBold))
        self.setFixedHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(110)
        self.setAccessibleName(text)

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 48, 34)

    def paintEvent(self, _event):
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            if self._down:
                rect = rect.adjusted(1.5, 1, -1.5, -1)
            accent = _color(self.color_key)
            if self.isChecked():
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(accent.darker(112) if self._down else accent)
                ink = QColor("#ffffff")
                label = "✓ " + self.text()
            else:
                painter.setPen(QPen(_color("separator"), 1))
                painter.setBrush(_color("pressed" if self._down else ("control_hover" if self.underMouse() else "control")))
                ink = accent if self.underMouse() else _color("text")
                label = self.text()
            painter.drawRoundedRect(rect, 9, 9)
            painter.setPen(ink)
            painter.setFont(self.font())
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)


class CompareChoice(QWidget):
    """One song to confirm: a yellow state, the source and the most similar song side by side, two clear keys."""

    def __init__(self, item, parent=None):
        super().__init__(parent)
        from api.apple_music_catalog import album_key, duration_relation, same_artist, title_parts
        self.setProperty("appleRow", True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(380)
        source = item.source
        layout = QVBoxLayout(self)
        layout.setContentsMargins(ROW_HPAD, 12, ROW_HPAD, 12)
        layout.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(StatusPill("待确认", "yellow"), 0)
        reason = ElidedLabel(item.reason or "这是最像的一首", 12, "secondary")
        head.addWidget(reason, 1)
        layout.addLayout(head)
        candidate = item.candidates[0] if item.candidates else item.target
        differs = set()
        if candidate is not None:
            if title_parts(source.title) != title_parts(candidate.title):
                differs.add("title")
            if not same_artist(source.artist, candidate.artist):
                differs.add("artist")
            if source.album and candidate.album and album_key(source.album) != album_key(candidate.album):
                differs.add("album")
            if duration_relation(source.duration_seconds, candidate.duration_seconds) == "far":
                differs.add("duration")
        pair = QHBoxLayout()
        pair.setSpacing(8)
        self.source_tile = SourceTile(source)
        pair.addWidget(self.source_tile, 1)
        pair.addWidget(_label("→", 16, "secondary"), 0, Qt.AlignmentFlag.AlignVCenter)
        self.group = ChoiceGroup((candidate,) if candidate is not None else (), (("skip", "不是，跳过", "red"),),
                                 pill_class=ActionPill, tiles=True, differs=frozenset(differs))
        for card in self.group.cards:
            pair.addWidget(card, 1)
        layout.addLayout(pair)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)
        self.accept = ActionPill("就是这首", "green")
        self.accept.setCheckable(True)
        if self.group.cards:
            card = self.group.cards[0]
            # The key mirrors the tile: tapping either chooses this song, tapping again clears it.
            self.accept.clicked.connect(lambda _checked=False: (self.accept.setChecked(card.isChecked()), card.click()))
            card.toggled.connect(self.accept.setChecked)
            actions.addWidget(self.accept)
        for pill in self.group.pills:
            actions.addWidget(pill)
        layout.addLayout(actions)


class PillChoice(_PressRow):
    """A plain answer as a list row (跳过, 留着, 删掉): the text in accent colour, a tick when chosen."""

    def __init__(self, text: str, accent: str = "accent", parent=None):
        super().__init__(parent)
        self.setText(text)
        self.accent = accent
        self.setFont(_font(13))
        self.setFixedHeight(36)
        self.setAccessibleName(text)

    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 2 * ROW_HPAD + 30, 36)

    def paintEvent(self, _event):
        with QPainter(self) as painter:
            self._paint_plate_and_tick(painter, self.height() / 2)
            painter.setPen(_color(self.accent))
            painter.setFont(self.font())
            painter.drawText(QRectF(self.rect()).adjusted(ROW_HPAD, 0, -ROW_HPAD - 28, 0),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())


class ChoiceGroup(QWidget):
    """Candidate rows plus plain answers; at most one is chosen, and choosing it again clears it."""

    def __init__(self, songs, extras, parent=None, *, card=None, pill_class=None, tiles: bool = False,
                 differs: frozenset = frozenset()):
        super().__init__(parent)
        self.cards: list[CandidateCard] = []
        self.pills: list[PillChoice] = []
        self.values: dict[QAbstractButton, object] = {}
        self._buttons: list[QAbstractButton] = []
        self._card = card
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for song in songs:
            row = CandidateCard(song, differs=differs, tile=tiles)
            self._add(row, song)
            self.cards.append(row)
        for value, label, accent in extras:
            pill = (pill_class or PillChoice)(label, accent)
            self._add(pill, value)
            self.pills.append(pill)
        if card is None and not tiles:
            for button in self._buttons:
                layout.addWidget(button)

    def _add(self, button, value):
        self.values[button] = value
        self._buttons.append(button)
        button.clicked.connect(lambda _checked=False, clicked=button: self._only_one(clicked))
        if self._card is not None:
            self._card.add_row(button)

    def _only_one(self, clicked):
        for button in self._buttons:
            if button is not clicked and button.isChecked():
                button.setChecked(False)

    def value(self):
        for button in self._buttons:
            if button.isChecked():
                return self.values.get(button)
        return None

    def choose(self, index: int):
        if 0 <= index < len(self._buttons):
            button = self._buttons[index]
            button.setChecked(True)
            self._only_one(button)


class LoadingDots(QWidget):
    """Centred loading mark: three small dots rising in turn inside a rounded frame."""

    PERIOD_MS = 1100

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtCore import QElapsedTimer, QTimer
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(84, 44)
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)
        self.hide()

    def start(self):
        self._clock.start()
        self._timer.start()
        self.show()
        self.raise_()

    def stop(self):
        self._timer.stop()
        self.hide()

    def running(self) -> bool:
        return self._timer.isActive()

    def paintEvent(self, _event):
        import math
        elapsed = self._clock.elapsed() if self._clock.isValid() else 0
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            frame = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            dark = bool(_t("dark"))
            painter.setPen(QPen(_color("separator"), 1))
            painter.setBrush(QColor(44, 44, 46, 245) if dark else QColor(255, 255, 255, 245))
            painter.drawRoundedRect(frame, 12, 12)
            for index in range(3):
                phase = ((elapsed / self.PERIOD_MS) - index * 0.18) % 1.0
                lift = max(0.0, math.sin(phase * 2 * math.pi))  # one rise per cycle, then rest
                color = _color("accent") if dark else _color("accent")
                color.setAlphaF(0.35 + 0.65 * lift)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                radius = 3.6 + 1.2 * lift
                center = QPointF(self.width() / 2 + (index - 1) * 16, self.height() / 2 - 4 * lift)
                painter.drawEllipse(center, radius, radius)


class ScanPanel(QWidget):
    """While the playlist is checked: covers flowing past, the song count and a progress bar."""

    COVER = 92
    SPACING = 74
    KEEP = 7

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtCore import QEasingCurve, QVariantAnimation
        from PySide6.QtNetwork import QNetworkAccessManager
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedSize(320, 214)
        self.done = self.total = 0
        self.current = None
        self._songs: list = []
        self._images: dict[str, object] = {}
        self._asked: set[str] = set()
        self._network = QNetworkAccessManager(self)
        self._network.finished.connect(self._image_arrived)
        self._shift = 1.0
        self._slide = QVariantAnimation(self)
        self._slide.setDuration(360)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide.setStartValue(0.0)
        self._slide.setEndValue(1.0)
        self._slide.valueChanged.connect(self._slid)
        self.hide()

    def reset(self):
        self.done = self.total = 0
        self.current = None
        self._songs.clear()
        self._shift = 1.0
        self.update()

    def set_progress(self, done: int, total: int, song=None):
        self.done, self.total = int(done), int(total)
        if song is not None and (not self._songs or self._songs[-1] is not song):
            self.current = song
            self._songs.append(song)
            del self._songs[:-self.KEEP]
            self._request_cover(song)
            self._slide.stop()
            self._shift = 0.0
            self._slide.start()
        self.update()

    def _slid(self, value):
        self._shift = float(value)
        self.update()

    @staticmethod
    def _cover_url(song) -> str:
        url = getattr(song, "artwork_url", "") or ""
        if "music.126.net" in url and "param=" not in url:
            url += ("&" if "?" in url else "?") + "param=200y200"
        return url if url.startswith(("https://", "http://")) else ""

    def _request_cover(self, song):
        from PySide6.QtNetwork import QNetworkRequest
        url = self._cover_url(song)
        if not url or url in self._asked:
            return
        self._asked.add(url)
        request = QNetworkRequest(QUrl(url))
        request.setTransferTimeout(8000)
        self._network.get(request)

    def _image_arrived(self, reply):
        from PySide6.QtGui import QImage
        url = reply.request().url().toString()
        data = reply.readAll()
        reply.deleteLater()
        image = QImage()
        if image.loadFromData(bytes(data)):
            self._images[url] = image.scaled(self.COVER * 2, self.COVER * 2, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                             Qt.TransformationMode.SmoothTransformation)
            self.update()

    def paintEvent(self, _event):
        dark = bool(_t("dark"))
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            frame = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.setPen(QPen(_color("separator"), 1))
            painter.setBrush(QColor(44, 44, 46, 248) if dark else QColor(255, 255, 255, 250))
            painter.drawRoundedRect(frame, 16, 16)
            # Covers: the newest slides in from the right to the middle; older ones drift left and fade.
            outline = QPainterPath()
            outline.addRoundedRect(frame.adjusted(1, 1, -1, -1), 15, 15)
            painter.save()
            painter.setClipPath(outline)
            centre_x, centre_y = self.width() / 2, 18 + self.COVER / 2
            count = len(self._songs)
            placed = []
            for index, song in enumerate(self._songs):
                offset = index - (count - 1) + (1.0 - self._shift)
                if abs(offset) > 2.6:
                    continue
                placed.append((abs(offset), offset, song))
            for _distance, offset, song in sorted(placed, key=lambda item: -item[0]):
                scale = max(0.55, 1.0 - 0.2 * min(abs(offset), 2.0))
                side = self.COVER * scale
                rect = QRectF(centre_x + offset * self.SPACING - side / 2, centre_y - side / 2, side, side)
                painter.setOpacity(max(0.0, 1.0 - 0.38 * abs(offset)))
                path = QPainterPath()
                path.addRoundedRect(rect, 8, 8)
                image = self._images.get(self._cover_url(song))
                if image is not None:
                    painter.save()
                    painter.setClipPath(path)
                    source = QRectF(0, 0, image.width(), image.height())
                    edge = min(source.width(), source.height())
                    source = QRectF((source.width() - edge) / 2, (source.height() - edge) / 2, edge, edge)
                    painter.drawImage(rect, image, source)
                    painter.restore()
                else:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(_color("control"))
                    painter.drawPath(path)
                    painter.setPen(_color("secondary"))
                    painter.setFont(_font(int(side * 0.34)))
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "♪")
            painter.restore()
            painter.setOpacity(1.0)
            # Count, the current song, and the bar.
            top = 18 + self.COVER + 14
            painter.setPen(_color("text"))
            painter.setFont(_font(13, QFont.Weight.DemiBold))
            count_text = f"已处理 {self.done} / {self.total} 首" if self.total else "正在读取歌单…"
            painter.drawText(QRectF(16, top, self.width() - 32, 18), Qt.AlignmentFlag.AlignCenter, count_text)
            if self.current is not None:
                painter.setPen(_color("secondary"))
                painter.setFont(_font(12))
                line = f"{self.current.title} · {self.current.artist}" if self.current.artist else self.current.title
                line = painter.fontMetrics().elidedText(line, Qt.TextElideMode.ElideRight, self.width() - 40)
                painter.drawText(QRectF(16, top + 21, self.width() - 32, 16), Qt.AlignmentFlag.AlignCenter, line)
            bar = QRectF(28, self.height() - 22, self.width() - 56, 5)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_color("control"))
            painter.drawRoundedRect(bar, 2.5, 2.5)
            if self.total:
                filled = QRectF(bar.left(), bar.top(), bar.width() * min(1.0, self.done / self.total), bar.height())
                painter.setBrush(_color("accent"))
                painter.drawRoundedRect(filled, 2.5, 2.5)


class SkeletonRows(QWidget):
    """Placeholder rows that shimmer while the playlists are compared."""

    def __init__(self, rows: int = 4, parent=None):
        super().__init__(parent)
        from PySide6.QtCore import QElapsedTimer, QTimer
        self.setProperty("appleRow", True)
        self._rows = rows
        self.setFixedHeight(rows * 52)
        self._clock = QElapsedTimer()
        self._clock.start()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self.update)
        self._timer.start()

    def paintEvent(self, _event):
        from PySide6.QtGui import QLinearGradient
        width = max(1, self.width())
        sweep = (self._clock.elapsed() % 1400) / 1400.0
        base = _color("control")
        shine = _color("control_hover")
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            centre = -0.3 * width + sweep * 1.6 * width
            gradient = QLinearGradient(centre - 120, 0, centre + 120, 0)
            gradient.setColorAt(0.0, base)
            gradient.setColorAt(0.5, shine)
            gradient.setColorAt(1.0, base)
            for row in range(self._rows):
                top = row * 52 + 10
                painter.setBrush(gradient)
                painter.drawRoundedRect(QRectF(ROW_HPAD, top, width * 0.42, 12), 6, 6)
                painter.drawRoundedRect(QRectF(ROW_HPAD, top + 19, width * 0.28, 10), 5, 5)
                painter.drawEllipse(QRectF(width - ROW_HPAD - 26, top + 2, 26, 26))
                if row < self._rows - 1:
                    painter.setBrush(_color("separator"))
                    painter.drawRect(QRectF(ROW_HPAD, row * 52 + 51, width - 2 * ROW_HPAD, 1))


class _Owner:
    """What ``settings_apple`` asks of its owner: the theme and the glass level."""

    def __init__(self, dark: bool, getter=None):
        self._dark = dark
        self._getter = getter

    def _settings_dark_mode(self) -> bool:
        return self._dark

    def _setting_value(self, key, default):
        if callable(self._getter):
            try:
                return self._getter(key, default)
            except Exception:  # noqa: BLE001 - a missing setting uses its default
                return default
        return default


class Group(QWidget):
    """Group title outside the card, the card, and a footer under it (inset grouped)."""

    def __init__(self, owner, title: str = "", footer: str = "", count: int | None = None, mark: str = ""):
        super().__init__()
        from local.settings_apple import AppleCard
        self.setObjectName("AppleGroup")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        if title:
            head = QHBoxLayout()
            head.setContentsMargins(ROW_HPAD - 4 if mark else ROW_HPAD, 0, ROW_HPAD, 0)
            head.setSpacing(6)
            text = f"{title} · {count}" if count is not None else title
            if mark:
                head.addWidget(StatusPill(text, mark), 0)
            else:
                head.addWidget(_label(text, 12, "secondary", QFont.Weight.DemiBold))
            head.addStretch(1)
            layout.addLayout(head)
        self.card = AppleCard(owner)
        layout.addWidget(self.card)
        if footer:
            note = _label(footer, 12, "secondary", wrap=True)
            note.setContentsMargins(ROW_HPAD, 0, ROW_HPAD, 0)
            layout.addWidget(note)

    def add(self, widget: QWidget) -> QWidget:
        return self.card.add_row(widget)


def _row(title: str, control: QWidget) -> QWidget:
    """Title on the left, control on the right, like a settings row."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    layout.addWidget(_label(title, 13, "text"), 0)
    layout.addStretch(1)
    layout.addWidget(control, 0)
    return row


class ConfirmRemovalDialog(QDialog):
    """The extra confirmation before songs are removed from a NetEase playlist."""

    def __init__(self, target_name: str, songs, parent=None):
        super().__init__(parent)
        self.setWindowTitle("确认删除")
        self.setMinimumWidth(440)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(10)
        root.addWidget(_label(f"要从{target_name}歌单里删掉 {len(songs)} 首歌", 16, "text", QFont.Weight.DemiBold))
        root.addWidget(_label("这些歌在来源歌单里已经删了，或者是你选了删除。删掉后要在网易云里手动加回。", 12, "secondary", wrap=True))
        listing = QWidget()
        rows = QVBoxLayout(listing)
        rows.setContentsMargins(0, 0, 0, 0)
        for song in tuple(songs)[:ROW_LIMIT]:
            rows.addWidget(SongLine(song, accent="red", listenable=False))
        rows.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(listing)
        scroll.setMaximumHeight(300)
        root.addWidget(scroll)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_button = QPushButton("先不删")
        self.accept_button = QPushButton("确认删除")
        self.accept_button.setObjectName("Primary")
        for button in (self.cancel_button, self.accept_button):
            button.setMinimumWidth(96)
            actions.addWidget(button)
        self.cancel_button.clicked.connect(lambda _checked=False: self.reject())
        self.accept_button.clicked.connect(lambda _checked=False: self.accept())
        root.addLayout(actions)
        from local.settings_apple import apple_settings_stylesheet
        self.setStyleSheet(apple_settings_stylesheet(_THEME) + f"QDialog {{ background: {'#1c1c1e' if _t('dark') else '#f2f2f7'}; }}"
                           " QLabel { background: transparent; }")


class AuditionPlayer:
    """The window's own listening player: one address at a time, optionally one passage.

    It plays on the same output device as GluSpot's own player (output()
    returns that device and its volume), not on the Windows default: with
    headphones or another chosen output the default may be silent to the user.
    """

    def __init__(self, owner):
        self.owner = owner
        self.player = None
        self.output = None
        self.key = ""
        self.end = 0.0
        self._start = 0.0
        self.output_provider = None  # () -> (QAudioDevice | None, volume 0..1)

    def _ensure(self):
        if self.player is None:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            self.output = QAudioOutput(self.owner)
            self.output.setVolume(0.9)
            self.player = QMediaPlayer(self.owner)
            self.player.setAudioOutput(self.output)
            self.player.positionChanged.connect(self._position)
            self.player.mediaStatusChanged.connect(self._status)
            self.player.errorOccurred.connect(self._error)
        return self.player

    def _route(self):
        """Follow the main player's output device and volume."""
        provider = self.output_provider
        if not callable(provider):
            return "default"
        try:
            device, volume = provider()
        except Exception:  # noqa: BLE001 - fall back to the system default
            return "default"
        if device is not None and not device.isNull():
            self.output.setDevice(device)
        if isinstance(volume, (int, float)) and volume > 0.05:
            self.output.setVolume(float(min(1.0, volume)))
        return "main" if device is not None and not device.isNull() else "default"

    def play(self, key: str, url: str, start: float = 0.0, end: float = 0.0):
        player = self._ensure()
        self.key, self._start, self.end = key, max(0.0, start), max(0.0, end)
        player.stop()
        route = self._route()
        _log.info("sync stage=audition result=start device=%s scheme=%s passage=%s",
                  route, url.split(":", 1)[0], "yes" if end > 0 else "no")
        player.setSource(QUrl(url))
        player.play()

    def _error(self, error, _text=""):
        _log.info("sync stage=audition result=error kind=%s", getattr(error, "name", error))
        self.owner._audition_failed()

    def _status(self, status):
        from PySide6.QtMultimedia import QMediaPlayer
        if status in (QMediaPlayer.MediaStatus.BufferedMedia, QMediaPlayer.MediaStatus.InvalidMedia):
            _log.info("sync stage=audition result=%s", status.name)
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia) and self._start > 0:
            self.player.setPosition(int(self._start * 1000))
            self._start = 0.0
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.stop()

    def _position(self, position):
        if self.end > 0 and position >= self.end * 1000:
            self.stop()

    def stop(self):
        if self.player is not None:
            self.player.stop()
        if self.key:
            self.key = ""
            self.owner._audition_stopped()

    def playing(self) -> bool:
        return bool(self.key)


class PlaylistSyncDialog(QDialog):
    previewRequested = Signal()
    confirmRequested = Signal()
    directionChanged = Signal(str)
    modeChanged = Signal(str)
    sourceChanged = Signal(str)
    auditionRequested = Signal(object, float, float)  # song, start, end: the controller resolves the address
    auditionStarted = Signal()

    def __init__(self, parent=None, *, settings_get=None):
        super().__init__(parent)
        dark = True
        if callable(settings_get):
            try:
                from local.ui_theme import surface_is_dark
                dark = bool(surface_is_dark(settings_get))
            except Exception:  # noqa: BLE001 - dark is the safe default
                dark = True
        _use_theme(dark)
        self._dark = dark
        self._owner = _Owner(dark, settings_get)
        self.setWindowTitle("歌单同步")
        self.setMinimumSize(520, 560)
        self.resize(760, 780)
        # Frameless, like the settings window: its plain grouped background and traffic lights.
        self.setWindowFlags((self.windowFlags() | Qt.WindowType.FramelessWindowHint)
                            & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._drag_from = None
        self.island_widget = None
        self._direction = "netease_to_apple"
        self._mode = "manual"
        self._ready = False
        self._busy = False
        self._preview = None
        self._choices: dict[str, ChoiceGroup] = {}
        self._skips: dict[str, PillChoice] = {}
        self._target_added: dict[str, ChoiceGroup] = {}
        self._target_removed: dict[str, ChoiceGroup] = {}
        self._listen_buttons: dict[str, list[ListenButton]] = {}
        self._pending_audition = ""
        self.audition = AuditionPlayer(self)

        from local.mac_style import mac_traffic_lights
        from local.settings_apple import AppleWindowShell, apple_settings_stylesheet
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.shell = AppleWindowShell(self._owner)
        self.shell.setObjectName("MacWindowShell")
        outer.addWidget(self.shell)
        root = QVBoxLayout(self.shell)
        root.setContentsMargins(16, 12, 16, 14)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.addWidget(mac_traffic_lights(self), 0, Qt.AlignmentFlag.AlignLeft)
        top.addStretch(1)
        self.compare_status = _label("", 12, "secondary")
        top.addWidget(self.compare_status)
        root.addLayout(top)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("MacContentScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("MacScrollBody")
        body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        column = QVBoxLayout(body)
        column.setContentsMargins(8, 2, 8, 20)
        column.setSpacing(20)
        self.scroll.setWidget(body)
        root.addWidget(self.scroll, 1)

        heading = QVBoxLayout()
        heading.setSpacing(4)
        title = QLabel("歌单同步")
        title.setObjectName("AppleLargeTitle")
        heading.addWidget(title)
        heading.addWidget(_label("以来源歌单为准：来源里加的歌会加到目标歌单，来源里删的歌会从目标歌单删掉。"
                                 "只在目标歌单里发生的变动，由你来决定。", 12, "secondary", wrap=True))
        column.addLayout(heading)

        self.direction_switch = AppleSegmentedControl(DIRECTION_LABELS, height=30)
        self.direction_switch.changed.connect(self._direction_picked)
        column.addWidget(self.direction_switch)

        route = Group(self._owner, "歌单")
        self.source_list = QComboBox()
        self.source_list.setFixedWidth(CONTROL_WIDTH)
        self.source_list.currentIndexChanged.connect(self._source_picked)
        self.source_row = _row("来源", self.source_list)
        self.source_label = self.source_row.findChild(QLabel)
        route.add(self.source_row)
        self.target_list = QComboBox()
        self.target_list.setFixedWidth(CONTROL_WIDTH)
        self.target_list.currentIndexChanged.connect(self._target_picked)
        self.target_row = _row("目标", self.target_list)
        self.target_label = self.target_row.findChild(QLabel)
        route.add(self.target_row)
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(40)
        self.name_edit.setFixedWidth(CONTROL_WIDTH)
        self.name_row = _row("新歌单名字", self.name_edit)
        route.add(self.name_row)
        column.addWidget(route)

        mode = Group(self._owner, "同步方式")
        self.mode_switch = AppleSegmentedControl(MODE_LABELS, height=26)
        self.mode_switch.setFixedWidth(130)
        self.mode_switch.changed.connect(self._mode_picked)
        mode.add(_row("手动 / 自动", self.mode_switch))
        self.mode_hint = _label(MODE_HINTS["manual"], 12, "secondary", wrap=True)
        self.mode_hint.setContentsMargins(ROW_HPAD, 0, ROW_HPAD, 0)
        mode.layout().addWidget(self.mode_hint)
        column.addWidget(mode)

        self.results = QWidget()
        self.results.setObjectName("AppleColumn")
        self.results.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.results_layout = QVBoxLayout(self.results)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(20)
        column.addWidget(self.results)
        column.addStretch(1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.status = _label("先选好两个歌单，再点「检查差异」。", 12, "secondary", wrap=True)
        footer.addWidget(self.status, 1)
        self.cancel_button = QPushButton("关闭")
        self.preview_button = QPushButton("检查差异")
        self.confirm_button = QPushButton("同步")
        self.confirm_button.setObjectName("Primary")
        for button in (self.cancel_button, self.preview_button, self.confirm_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            footer.addWidget(button)
        self.preview_button.clicked.connect(lambda _checked=False: self.previewRequested.emit())
        self.confirm_button.clicked.connect(lambda _checked=False: self._confirm())
        self.cancel_button.clicked.connect(lambda _checked=False: self.reject())
        self.confirm_button.setEnabled(False)
        self.loading = LoadingDots(self.shell)
        self.scan = ScanPanel(self.shell)
        grip = QSizeGrip(self.shell)
        grip.setFixedSize(12, 12)
        footer.addWidget(grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        root.addLayout(footer)

        from local.mac_style import mac_preferences_stylesheet, mac_preferences_theme
        from local.ui_theme import style_combo_popup
        base_theme = mac_preferences_theme(dark)
        for combo in (self.source_list, self.target_list):
            style_combo_popup(combo, base_theme)
            combo.view().setMinimumWidth(CONTROL_WIDTH)
        self.setStyleSheet(mac_preferences_stylesheet(base_theme) + apple_settings_stylesheet(_THEME)
                           + " QDialog { background: transparent; } QLabel { background: transparent; }"
                           + " QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; border: 0; }"
                           + " QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
                           + f" QPushButton#Primary:disabled {{ background: {_t('control')}; color: {_t('tertiary')}; }}"
                           + f" QPushButton[syncChoice=\"true\"]:checked {{ background: {_t('accent')}; color: #ffffff; }}")
        self._apply_direction_text()
        self._show_empty()

    def done(self, result):
        self.audition.stop()
        super().done(result)

    # ---- frameless window ------------------------------------------------------
    def _settings_dark_mode(self) -> bool:
        return self._dark

    def _setting_value(self, key, default):
        return self._owner._setting_value(key, default)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() <= 44:
            self._drag_from = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_from is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_from)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_from = None
        super().mouseReleaseEvent(event)

    # ---- direction, pair and mode -------------------------------------------------
    def set_direction(self, direction: str):
        if not sync_direction_allowed(direction) or direction == self._direction:
            return
        self._direction = direction
        self.direction_switch.set_current(direction)
        self._apply_direction_text()
        self._invalidate()

    def _direction_picked(self, direction: str):
        if direction != self._direction:
            self.set_direction(direction)
            self.directionChanged.emit(direction)

    def _apply_direction_text(self):
        source, target = sides(self._direction)
        self.source_label.setText(f"来源 · {SOURCE_NAMES[source]}")
        self.target_label.setText(f"目标 · {SOURCE_NAMES[target]}")
        self.name_edit.setPlaceholderText(f"新建{SOURCE_NAMES[target]}歌单")
        self._sync_name_visibility()

    def direction(self) -> str:
        return self._direction

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str):
        if mode not in dict(MODE_LABELS):
            return
        self._mode = mode
        self.mode_switch.set_current(mode)
        self.mode_hint.setText(MODE_HINTS[mode])

    def _mode_picked(self, mode: str):
        self.set_mode(mode)
        self.modeChanged.emit(mode)

    def set_source_playlists(self, rows, *, select: str = ""):
        self._fill(self.source_list, rows, select)

    def set_target_playlists(self, rows, *, select: str = ""):
        _source, target = sides(self._direction)
        self._fill(self.target_list, [("", f"新建一个{SOURCE_NAMES[target]}歌单"), *rows], select)
        self._sync_name_visibility()

    def select_target(self, playlist_id: str):
        index = self.target_list.findData(playlist_id)
        if index >= 0:
            self.target_list.setCurrentIndex(index)

    def _source_picked(self, _index):
        self._invalidate()
        self.sourceChanged.emit(self.source_list.currentData() or "")

    def _target_picked(self, _index):
        self._sync_name_visibility()
        self._invalidate()

    def _sync_name_visibility(self):
        self.name_row.setVisible(not (self.target_list.currentData() or ""))

    @property
    def name_edit_hidden(self) -> bool:
        return self.name_row.isHidden()

    def _invalidate(self):
        self._ready = False
        self._preview = None
        self.confirm_button.setEnabled(False)
        if hasattr(self, "results_layout"):
            self._show_empty()

    # ---- preview -------------------------------------------------------------------
    def _clear_results(self):
        self.audition.stop()
        self._choices.clear()
        self._skips.clear()
        self._target_added.clear()
        self._target_removed.clear()
        self._listen_buttons.clear()
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                item.layout().deleteLater()

    def _show_empty(self):
        self._clear_results()
        empty = _label("点「检查差异」后，这里会列出要加的歌、要删的歌，以及需要你挑选的歌。", 12, "secondary", wrap=True)
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.results_layout.addWidget(empty)

    def _wire(self, line):
        """Every listen key goes through one handler, so one plays at a time."""
        line.listen.connect(self._listen)
        button = getattr(line, "listen_button", None) or getattr(getattr(line, "line", None), "listen_button", None)
        if button is not None:
            self._listen_buttons.setdefault(self._song_id(line.song), []).append(button)
        return line

    @staticmethod
    def _song_id(song, start: float = 0.0) -> str:
        return f"{song.source_id}:{song.song_id}@{start:.1f}"

    def set_preview(self, preview, summary: str = "", verdicts=None):
        """Show one SyncPreview. Pending choices start empty: nothing is decided for the user."""
        previous = self._preview
        same_pair = (previous is not None and previous.direction == preview.direction
                     and previous.source_playlist_id == preview.source_playlist_id
                     and previous.target_playlist_id == preview.target_playlist_id)
        keep = self.decisions() if same_pair else None
        self._clear_results()
        self._preview = preview
        plan = preview.plan
        _source_side, target_side = sides(plan.direction)
        target_name = SOURCE_NAMES[target_side]
        owner = self._owner
        if summary:
            self.results_layout.addWidget(_label(summary, 12, "secondary", wrap=True))

        if plan.choices:
            group = Group(owner, "待确认", f"左边是来源，右边是在{target_name}找到的最像的一首，对不上的地方标成橙色。"
                          "点 ▶ 试听；是同一首就点「就是这首」，不是就「跳过」，以后不再问。音轨比对完成后，声音一样的会自动选好。",
                          len(plan.choices), mark="yellow")
            for item in plan.choices[:ROW_LIMIT]:
                compare = CompareChoice(item)
                self._wire(compare.source_tile)
                for card in compare.group.cards:
                    self._wire(card)
                self._choices[song_key(item.source)] = compare.group
                group.add(compare)
            self.results_layout.addWidget(group)
        if plan.unmatched:
            group = Group(owner, "没找到", f"在{target_name}里没搜到。点「不再问」以后就跳过它。", len(plan.unmatched), mark="red")
            for item in plan.unmatched[:ROW_LIMIT]:
                row = QWidget()
                layout = QHBoxLayout(row)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.addWidget(self._wire(SongLine(item.source)), 1)
                skip = QPushButton("不再问")
                skip.setCheckable(True)
                skip.setProperty("syncChoice", True)
                self._skips[song_key(item.source)] = skip
                layout.addWidget(skip, 0)
                group.add(row)
            self.results_layout.addWidget(group)
        if plan.additions:
            group = Group(owner, f"预添加到{target_name}", "左边的小封面是来源，右边是要加进去的那首；专辑不同会一起写出来。",
                          len(plan.additions), mark="green")
            for item in plan.additions[:ROW_LIMIT]:
                note = "音轨一致" if "音轨一致" in item.reason else ""
                group.add(self._wire(SongLine(item.target, note=note, compare=item.source)))
            self.results_layout.addWidget(group)
            self._more(len(plan.additions))
        if plan.removals:
            hint = "来源歌单里删掉了这些歌。" + ("同步前会再问你一次。" if self._mode == "manual" else "自动模式会直接删。")
            group = Group(owner, f"会从{target_name}删掉", hint, len(plan.removals), mark="red")
            for item in plan.removals[:ROW_LIMIT]:
                group.add(self._wire(SongLine(item.target)))
            self.results_layout.addWidget(group)
            self._more(len(plan.removals))
        if plan.target_added:
            removable = target_side == "netease"
            hint = "这些歌只在目标歌单里有。" + ("留着，还是删掉？" if removable else "Apple Music 不允许 GluSpot 删歌，只能留着或你自己去删。")
            group = Group(owner, f"{target_name}里多出来的", hint, len(plan.target_added), mark="orange")
            extras = ((("keep", "留着", "accent"), ("remove", "删掉", "red")) if removable
                      else (("keep", "留着，不再问", "accent"),))
            for item in plan.target_added[:ROW_LIMIT]:
                group.add(self._wire(SongLine(item.target)))
                self._target_added[song_key(item.target)] = ChoiceGroup((), extras, card=group.card)
            self.results_layout.addWidget(group)
        if plan.target_removed:
            group = Group(owner, f"在{target_name}里被删掉的", "以前同步过，后来在目标歌单里删了，来源里还在。",
                          len(plan.target_removed), mark="orange")
            for item in plan.target_removed[:ROW_LIMIT]:
                group.add(self._wire(SongLine(item.source)))
                self._target_removed[song_key(item.source)] = ChoiceGroup(
                    (), (("readd", "加回去", "accent"), ("accept", "就让它删着", "secondary")), card=group.card)
            self.results_layout.addWidget(group)
        self._rebuild = None
        if plan.cannot_remove:
            group = Group(owner, "Apple Music 删不掉的歌",
                          "来源歌单里删了这些歌，但 Apple Music 不允许第三方程序从歌单删歌，也不能删整个歌单。"
                          "可以只加新歌、这些歌你自己去删；也可以新建一个歌单按来源完整重建，以后都同步到新歌单，旧歌单你自己去删。",
                          len(plan.cannot_remove), mark="red")
            for item in plan.cannot_remove[:ROW_LIMIT]:
                group.add(SongLine(item.target, listenable=False))
            self._rebuild = ChoiceGroup((), (("add", "只加新歌，删掉的歌我自己去删", "accent"),
                                             ("rebuild", "新建一个歌单，按来源完整重建", "accent")), card=group.card)
            self._rebuild.choose(0)
            self.results_layout.addWidget(group)
        if not (plan.choices or plan.unmatched or plan.additions or plan.removals or plan.target_added
                or plan.target_removed or plan.cannot_remove):
            done = _label("两边已经一致，没有要同步的。", 14, "text", QFont.Weight.Medium)
            done.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.results_layout.addWidget(done)
        for source_key, found in (verdicts or {}).items():
            for candidate_key, verdict in found.items():
                self.show_verdict(source_key, candidate_key, verdict)
        if keep is not None:
            self._restore(keep)
        self._ready = True
        self.confirm_button.setEnabled(not self._busy)
        self.status.setText(self._status_text(plan))

    def _footer(self, text: str) -> QLabel:
        note = _label(text, 12, "secondary", wrap=True)
        note.setContentsMargins(ROW_HPAD, 0, ROW_HPAD, 0)
        return note

    def _restore(self, decisions: SyncDecisions):
        for key, group in self._choices.items():
            chosen = decisions.picks.get(key)
            for index, card in enumerate(group.cards):
                if chosen is not None and song_key(card.song) == song_key(chosen):
                    group.choose(index)
            if key in decisions.skip and group.pills:
                group.choose(len(group.cards))

    def show_verdict(self, source_key: str, candidate_key: str, verdict):
        """Write one audio-check result onto its candidate row."""
        group = self._choices.get(source_key)
        if group is None or self._preview is None:
            return
        item = next((choice for choice in self._preview.plan.choices if song_key(choice.source) == source_key), None)
        for card in group.cards:
            if song_key(card.song) != candidate_key:
                continue
            for button in card.diff_buttons:
                button.hide()
                button.deleteLater()
            card.diff_buttons.clear()
            if verdict is None:
                card.set_verdict("比对中…", "secondary")
            elif verdict.same:
                card.set_verdict("音轨一致", "green")
            elif not verdict.found:
                card.set_verdict("听起来是另一首/另一个版本", "red")
            elif verdict.differing:
                start, end = verdict.differing[0]
                card.set_verdict(f"有区别 {_span(start, end)}", "orange")
                if item is not None:
                    self._add_diff_buttons(card, item.source, verdict, start, end)
            else:
                card.set_verdict(f"相似度 {verdict.score:.0%}", "orange")

    def _add_diff_buttons(self, card: CandidateCard, source, verdict, start: float, end: float):
        # The Apple side is the 30-second clip: its passage is in clip time,
        # the other side's is offset to where the clip sits.
        def span_for(song):
            if song.source_id == "apple_music":
                return start, end
            return start + verdict.offset_seconds, end + verdict.offset_seconds
        source_span = span_for(source)
        candidate_span = span_for(card.song)
        card.add_diff_button("来源这段", *source_span, song=source)
        card.add_diff_button("候选这段", *candidate_span)
        for button, song, span in ((card.diff_buttons[0], source, source_span), (card.diff_buttons[1], card.song, candidate_span)):
            self._listen_buttons.setdefault(self._song_id(song, span[0]), []).append(button)

    def set_compare_progress(self, done: int, total: int, running: bool):
        if running:
            self.compare_status.setText(f"音轨比对 {done}/{total}")
        elif total:
            self.compare_status.setText("音轨比对完成")
        else:
            self.compare_status.setText("")

    def _more(self, count: int):
        if count > ROW_LIMIT:
            self.results_layout.addWidget(self._footer(f"还有 {count - ROW_LIMIT} 首没列出来，同步时一起处理。"))

    @staticmethod
    def _status_text(plan) -> str:
        parts = [f"加 {len(plan.additions)} 首", f"删 {len(plan.removals)} 首"]
        if plan.needs_user():
            parts.append(f"{plan.needs_user()} 首等你选")
        return "，".join(parts) + "。没选的这次先不动。"

    def decisions(self) -> SyncDecisions:
        picks, skip = {}, set()
        for key, group in self._choices.items():
            value = group.value()
            if value == "skip":
                skip.add(key)
            elif value is not None:
                picks[key] = value
        for key, pill in self._skips.items():
            if pill.isChecked():
                skip.add(key)
        target_added = {key: group.value() for key, group in self._target_added.items() if group.value()}
        target_removed = {key: group.value() for key, group in self._target_removed.items() if group.value()}
        rebuild = getattr(self, "_rebuild", None) is not None and self._rebuild.value() == "rebuild"
        return SyncDecisions(picks, frozenset(skip), self._mode == "auto", target_added, target_removed, rebuild)

    def removal_songs(self, decisions: SyncDecisions):
        """Songs this sync would remove from the target, for the confirmation screen."""
        if self._preview is None:
            return ()
        plan = self._preview.plan
        songs = [item.target for item in plan.removals]
        songs.extend(item.target for item in plan.target_added
                     if decisions.target_added.get(song_key(item.target)) == "remove")
        return tuple(songs)

    def preview(self):
        return self._preview

    # ---- listening -------------------------------------------------------------
    def _listen(self, song, start: float = 0.0, end: float = 0.0):
        key = self._song_id(song, start)
        if self.audition.key == key:
            self.audition.stop()
            return
        self.audition.stop()
        self._pending_audition = key
        self._mark_playing(key)
        self.status.setText(f"正在准备试听：{song.title}（{SOURCE_NAMES.get(song.source_id, '')}）")
        self.auditionRequested.emit(song, float(start), float(end))

    def play_audition(self, song, url: str, start: float = 0.0, end: float = 0.0):
        """The controller found the address; play it here, not in the main queue."""
        key = self._song_id(song, start)
        if self._pending_audition != key:
            return
        if not url:
            _log.info("sync stage=audition result=no-address source=%s", song.source_id)
            self._audition_failed()
            return
        self.auditionStarted.emit()
        self.audition.play(key, url, start, end)
        self._mark_playing(key)
        label = f"正在试听：{song.title}（{SOURCE_NAMES.get(song.source_id, '')}"
        if song.source_id == "apple_music":
            label += "，30 秒试听片段"
        if end > 0:
            label += f"，{_span(start, end)}"
        self.status.setText(label + "）。再点一次停止。")

    def _mark_playing(self, key: str):
        for song_id, buttons in self._listen_buttons.items():
            for button in buttons:
                try:
                    button.set_playing(song_id == key)
                except RuntimeError:
                    pass

    def _audition_stopped(self):
        self._mark_playing("")

    def _audition_failed(self):
        self._pending_audition = ""
        self.audition.key = ""
        self._mark_playing("")
        self.status.setText("这首现在放不出来：可能没有试听片段，或者需要会员。")

    # ---- results and state ---------------------------------------------------------
    def set_result(self, text: str):
        self._clear_results()
        group = Group(self._owner, "结果")
        label = QLabel(text)
        label.setWordWrap(True)
        label.setFont(_font(13))
        label.setStyleSheet(f"color: {_t('text')}; background: transparent;")
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        group.add(label)
        self.results_layout.addWidget(group)
        self.confirm_button.setEnabled(False)
        self._ready = False
        self._preview = None
        self.status.setText("可以换一对歌单，或者再检查一次。")

    def set_message(self, text: str):
        self.status.setText(text)

    def start_loading(self, *, skeleton: bool):
        """Centred dots; with skeleton=True the results show shimmering placeholder groups."""
        if skeleton:
            self._clear_results()
            for title in ("需要你选", "会加到目标歌单"):
                group = Group(self._owner, title)
                group.add(SkeletonRows(3))
                self.results_layout.addWidget(group)
        self.scan.reset()
        self.scan.hide()
        self._place_loading()
        self.loading.start()

    def set_scan_progress(self, done: int, total: int, song=None):
        """Per-song progress while checking: the dots give way to covers, count and bar."""
        if not self._busy:
            return
        if total and not self.scan.isVisible():
            self.loading.stop()
            self._place_loading()
            self.scan.show()
            self.scan.raise_()
        self.scan.set_progress(done, total, song)
        if total:
            self.status.setText(f"正在比对两边的歌单：已处理 {done} / {total} 首")

    def stop_loading(self):
        self.loading.stop()
        self.scan.hide()

    def _place_loading(self):
        area = self.scroll.geometry()
        for widget in (self.loading, self.scan):
            widget.move(area.center().x() - widget.width() // 2, area.center().y() - widget.height() // 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "loading"):
            self._place_loading()

    def set_busy(self, busy: bool, text: str = ""):
        self._busy = busy
        if not busy:
            self.stop_loading()
        self.preview_button.setEnabled(not busy)
        self.confirm_button.setEnabled((not busy) and self._ready)
        self.direction_switch.setEnabled(not busy)
        if text:
            self.status.setText(text)

    def request(self) -> dict:
        name = self.name_edit.text().strip()
        rebuild = getattr(self, "_rebuild", None)
        if rebuild is not None and rebuild.value() == "rebuild" and (self.target_list.currentData() or ""):
            from datetime import date
            name = f"{self.target_list.currentText()}（{date.today():%m-%d} 重建）"[:40]
        return {
            "direction": self._direction,
            "source_playlist_id": self.source_list.currentData() or "",
            "target_playlist_id": self.target_list.currentData() or "",
            "new_name": name,
            "mode": self._mode,
        }

    def _confirm(self):
        if self._ready and not self._busy:
            self.confirmRequested.emit()

    def confirm_removals(self, songs) -> bool:
        """Manual mode: one more screen before anything is removed."""
        _source, target = sides(self._direction)
        dialog = ConfirmRemovalDialog(SOURCE_NAMES[target], songs, self)
        return dialog.exec() == QDialog.DialogCode.Accepted

    @staticmethod
    def _fill(box: QComboBox, rows, select: str = ""):
        current = select or box.currentData()
        box.blockSignals(True)
        box.clear()
        for playlist_id, title in rows:
            box.addItem(str(title), str(playlist_id))
        if current:
            index = box.findData(current)
            if index >= 0:
                box.setCurrentIndex(index)
        box.blockSignals(False)
