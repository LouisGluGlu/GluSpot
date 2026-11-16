"""Shared fixture for the two search-result layouts.

This demo is not the product. Album and artist rows are sample data so the
layout choice can be seen; the shipping search still returns songs and playlists.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QLineEdit, QWidget

from local.music_library_query import match_spans, paint_highlighted_text


GROUPS = ("song", "album", "artist", "playlist")
GROUP_TITLES = {"song": "歌曲", "album": "专辑", "artist": "艺人", "playlist": "歌单"}


@dataclass(frozen=True)
class DemoItem:
    kind: str
    title: str
    subtitle: str


ITEMS = (
    DemoItem("song", "海阔天空", "Beyond"),
    DemoItem("song", "光辉岁月", "Beyond"),
    DemoItem("song", "真的爱你", "Beyond"),
    DemoItem("album", "海阔天空", "Beyond · 1993"),
    DemoItem("album", "乐与怒", "Beyond · 1993"),
    DemoItem("artist", "Beyond", "乐队"),
    DemoItem("artist", "黄家驹", "歌手"),
    DemoItem("playlist", "华语经典", "32 首"),
    DemoItem("playlist", "海边散步", "18 首"),
    DemoItem("playlist", "深夜唱片", "24 首"),
)


def filtered(query):
    query = query.strip()
    if not query:
        return ITEMS
    return tuple(item for item in ITEMS
                 if match_spans(item.title, query) or match_spans(item.subtitle, query))


def grouped(query):
    rows = filtered(query)
    return {kind: tuple(item for item in rows if item.kind == kind) for kind in GROUPS}


class DemoSearch(QWidget):
    def __init__(self, title):
        super().__init__()
        self.setWindowTitle(title)
        self.resize(880, 640)
        self.group_rects = {}
        self.query = QLineEdit(self)
        self.query.setPlaceholderText("搜索歌曲、专辑、艺人或歌单")
        self.query.setFont(QFont("sans-serif", 14))
        self.query.textChanged.connect(self.update)
        self.query.setStyleSheet(
            "QLineEdit { color: #f4f6fb; background: #1c212b; border: 1px solid #3c4452;"
            " border-radius: 10px; padding: 0 12px; }"
        )
        self.query.setGeometry(24, 20, 832, 40)

    def resizeEvent(self, event):
        self.query.setGeometry(24, 20, max(120, self.width() - 48), 40)
        super().resizeEvent(event)

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.fillRect(self.rect(), QColor("#12151b"))
            self.paint_results(painter)

    def paint_results(self, painter):
        raise NotImplementedError

    def paint_match(self, painter, rect, text, size, color):
        font = QFont("sans-serif", size)
        painter.setFont(font)
        paint_highlighted_text(painter, rect, text, self.query.text(), QColor(color), QColor("#ffe7a3"))

    def paint_heading(self, painter, rect, kind):
        font = QFont("sans-serif", 13)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#d5dbe6"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter, GROUP_TITLES[kind])
