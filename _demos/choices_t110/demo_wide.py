"""Fixed wide layouts you can resize.

A is layout G from PR #9: the cover, title and transport card are centered as
one group, so a tall window leaves empty space above and below. The lyric
column still starts at the old top cover position, and its current line sits
on the group center rather than the cover center.

B pins the group to the top margin and puts the current lyric on the cover
center. C also pins the group, then centers the current lyric in the right
column. Resize the window, or use the height presets.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter

from _demos.choices_t110.common import (
    SourceFlatButton, Stage, application, capture_window, ink, paint_flat_disc,
    paint_panel, ui_font,
)


LINES = (
    "Tell you that I might say goodbye?",
    "你应该很清楚这一点吧？",
    "这句话停在封面旁边",
    "窗口拉高以后",
    "左列不该悬在中间",
    "歌词的中心要有自己的位置",
    "D-E-P-E-N-D-S",
    "再往下还有一句",
    "夜航还没结束",
)


@dataclass(frozen=True)
class WideMetrics:
    margin: float
    cover: QRectF
    title: QRectF
    artist: QRectF
    card: QRectF
    lyric: QRectF
    lyric_anchor: float
    group_axis: float


def layout_wide(option: str, rect: QRectF) -> WideMetrics:
    """Geometry follows layout G's unit, then applies one fixed rule."""
    width, height = max(1.0, rect.width()), max(1.0, rect.height())
    unit = min(width / 100.0, height / 75.8)
    margin = max(18.0, unit * 5.0)
    left = max(margin, unit * 6.2)
    side = max(1.0, min(unit * 36.5, width * 0.40, height * 0.44))
    title_h = max(28.0, 3.4 * unit)
    artist_h = max(18.0, 2.2 * unit)
    gap_cover = unit * 1.35
    gap_card = unit * 1.55
    card_h = max(108.0, unit * 14.0)
    meta_h = title_h + artist_h + 4
    total = side + gap_cover + meta_h + gap_card + card_h
    if option == "A":
        top = (height - total) / 2
        top = max(margin, min(top, height - total - margin))
    else:
        top = margin
    origin_x, origin_y = rect.left(), rect.top()
    cover = QRectF(origin_x + left, origin_y + top, side, side)
    title = QRectF(cover.left(), cover.bottom() + gap_cover, side, title_h)
    artist = QRectF(cover.left(), title.bottom() + 4, side, artist_h)
    card = QRectF(cover.left(), artist.bottom() + gap_card, side, card_h)
    group_axis = (cover.top() + card.bottom()) / 2
    lyric_left = cover.right() + max(18.0, unit * 2.4)
    if option == "A":
        lyric = QRectF(lyric_left, origin_y, max(1.0, rect.right() - lyric_left), height)
        anchor = group_axis
    elif option == "B":
        lyric = QRectF(lyric_left, cover.top(), max(1.0, rect.right() - 16 - lyric_left),
                       max(1.0, rect.bottom() - cover.top() - margin))
        anchor = cover.center().y()
    else:
        lyric = QRectF(lyric_left, origin_y + margin, max(1.0, rect.right() - 16 - lyric_left),
                       max(1.0, height - margin * 2))
        anchor = lyric.center().y()
    return WideMetrics(margin, cover, title, artist, card, lyric, anchor, group_axis)


class WideDemo(Stage):
    def __init__(self):
        super().__init__("5  宽版布局", (
            ("A", "A  布局 G"), ("B", "B  贴顶对齐"), ("C", "C  分栏中心"),
        ))
        self.resize(1080, 980)
        self.setMinimumSize(860, 640)
        self.guides = True
        self.short_button = SourceFlatButton("矮窗", self, primary=False)
        self.mid_button = SourceFlatButton("常规", self, primary=False)
        self.tall_button = SourceFlatButton("高窗", self, primary=True)
        self.guide_button = SourceFlatButton("中心线", self, primary=True)
        self.short_button.clicked.connect(lambda: self.preset(760))
        self.mid_button.clicked.connect(lambda: self.preset(860))
        self.tall_button.clicked.connect(lambda: self.preset(1040))
        self.guide_button.clicked.connect(self.toggle_guides)

    def description(self) -> str:
        if self.option == "A":
            return "布局 G：左列整组垂直居中，高窗上下留白。歌词从旧的封面顶部位置排起，当前行对准整组中线，不对准封面。"
        if self.option == "B":
            return "贴顶：左列固定在上边距。歌词从封面顶部排起，当前行对准封面中心。拉高只把下方留出来。"
        return "分栏：左列同样贴顶。歌词当前行对准右栏自己的中线。两条中心线分开，各自对齐。"

    def preset(self, height: int) -> None:
        self.resize(self.width(), height)
        for button, match in ((self.short_button, 760), (self.mid_button, 860), (self.tall_button, 1040)):
            button.primary = height == match
            button.update()

    def toggle_guides(self) -> None:
        self.guides = not self.guides
        self.guide_button.primary = self.guides
        self.guide_button.update()
        self.update()

    def relayout(self) -> None:
        self.short_button.setGeometry(self.width() - 392, 74, 72, 34)
        self.mid_button.setGeometry(self.width() - 312, 74, 72, 34)
        self.tall_button.setGeometry(self.width() - 232, 74, 72, 34)
        self.guide_button.setGeometry(self.width() - 148, 74, 88, 34)

    def player_rect(self) -> QRectF:
        return self.body_rect()

    def paint_body(self, painter: QPainter, backdrop) -> None:
        player = self.player_rect()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 55))
        painter.drawRoundedRect(player, 20, 20)
        metrics = layout_wide(self.option, player.adjusted(8, 8, -8, -8))
        self._paint_cover(painter, metrics)
        self._paint_card(painter, metrics.card, backdrop)
        self._paint_lyrics(painter, metrics)
        if self.guides:
            self._paint_guides(painter, metrics)

    def _paint_cover(self, painter: QPainter, metrics: WideMetrics) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#c8874d"))
        painter.drawRoundedRect(metrics.cover, 18, 18)
        painter.setPen(ink())
        painter.setFont(ui_font(max(13, metrics.cover.width() * 0.12), bold=True))
        painter.drawText(metrics.cover, Qt.AlignmentFlag.AlignCenter, "封面")
        painter.setFont(ui_font(max(16, metrics.title.height() * 0.62), bold=True))
        painter.drawText(metrics.title, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "What Would U Do")
        painter.setFont(ui_font(max(12, metrics.artist.height() * 0.7)))
        painter.setPen(ink(190))
        painter.drawText(metrics.artist, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "Haley Smalls")

    def _paint_card(self, painter: QPainter, card: QRectF, backdrop) -> None:
        paint_panel(painter, card, backdrop, radius=18)
        track = card.adjusted(16, 16, -16, -card.height() + 28)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 40))
        painter.drawRoundedRect(track, 2, 2)
        done = QRectF(track.left(), track.top(), track.width() * 0.38, track.height())
        painter.setBrush(QColor(255, 255, 255, 200))
        painter.drawRoundedRect(done, 2, 2)
        painter.setFont(ui_font(11))
        painter.setPen(ink(180))
        painter.drawText(QRectF(card.left() + 16, track.bottom() + 4, 60, 16), Qt.AlignmentFlag.AlignLeft, "1:12")
        painter.drawText(QRectF(card.right() - 70, track.bottom() + 4, 54, 16), Qt.AlignmentFlag.AlignRight, "3:12")
        side = min(36.0, card.height() * 0.28)
        for index in range(3):
            button_side = side + 6 if index == 1 else side
            x = card.center().x() + (index - 1) * (side + 16) - button_side / 2
            rect = QRectF(x, card.bottom() - button_side - 12, button_side, button_side)
            paint_flat_disc(painter, rect, on=index == 1, fill_on=80, edge_on=140)
            painter.setPen(ink())
            painter.setFont(ui_font(11))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "▶" if index == 1 else ("<<" if index == 0 else ">>"))

    def _paint_lyrics(self, painter: QPainter, metrics: WideMetrics) -> None:
        painter.save()
        painter.setClipRect(metrics.lyric)
        line_h = 34.0
        if self.option == "A":
            top = metrics.lyric.top() + metrics.margin
            count = max(len(LINES), int((metrics.lyric_anchor - top) / line_h) + 2)
            closest = min(range(count), key=lambda index: abs(top + index * line_h + line_h / 2 - metrics.lyric_anchor))
        else:
            top = metrics.lyric_anchor - 2 * line_h - line_h / 2
            count = len(LINES)
            closest = 2
        for index in range(count):
            y = top + index * line_h
            if y > metrics.lyric.bottom() - 8:
                break
            line = LINES[index % len(LINES)]
            current = index == closest
            painter.setFont(ui_font(18 if current else 15, bold=current))
            painter.setPen(ink(255 if current else 120))
            painter.drawText(QRectF(metrics.lyric.left() + 8, y, metrics.lyric.width() - 16, line_h),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
        painter.restore()

    def _paint_guides(self, painter: QPainter, metrics: WideMetrics) -> None:
        painter.setFont(ui_font(11))
        painter.setPen(QColor(255, 196, 140, 210))
        y = metrics.cover.center().y()
        painter.drawLine(int(metrics.cover.left()), int(y), int(metrics.lyric.right()), int(y))
        painter.drawText(QRectF(metrics.cover.left() + 8, y - 18, 72, 16), Qt.AlignmentFlag.AlignLeft, "封面中心")
        painter.setPen(QColor(150, 210, 255, 220))
        y = metrics.lyric_anchor
        painter.drawLine(int(metrics.lyric.left()), int(y), int(metrics.lyric.right()), int(y))
        painter.drawText(QRectF(metrics.lyric.right() - 78, y - 18, 74, 16), Qt.AlignmentFlag.AlignRight, "歌词中心")


def capture(directory) -> list:
    from pathlib import Path
    directory = Path(directory)
    tall = QRectF(0, 0, 1000, 900)
    current = layout_wide("A", tall)
    pinned = layout_wide("B", tall)
    split = layout_wide("C", tall)
    assert current.cover.top() > current.margin + 40
    assert abs(pinned.cover.top() - (tall.top() + pinned.margin)) < 0.5
    assert abs(pinned.lyric_anchor - pinned.cover.center().y()) < 0.5
    assert abs(current.lyric_anchor - current.group_axis) < 0.5
    assert current.lyric.top() <= tall.top() + 0.5
    assert abs(current.lyric_anchor - current.cover.center().y()) > 30
    assert abs(split.lyric_anchor - split.lyric.center().y()) < 0.5
    assert abs(split.cover.top() - pinned.cover.top()) < 0.5
    app = application()
    window = WideDemo()
    window.resize(1080, 1040)
    from _demos.choices_t110.common import assert_painted, contact_sheet, save_image
    frames = []
    for option, label in (("A", "A  布局 G · 高窗"), ("B", "B  贴顶 · 高窗"), ("C", "C  分栏 · 高窗")):
        window.choose(option)
        image = capture_window(window)
        assert_painted(image, label)
        frames.append((label, image))
        save_image(image, directory / f"wide_{option}.png")
    sheet = contact_sheet(frames, directory / "wide_sheet.png", columns=3)
    window.close()
    del app
    return ["wide layout", str(sheet)]


def main() -> None:
    app = application()
    window = WideDemo()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
