"""Add-to-playlist only. Sorting and editing stay in 个人歌单.

A greys out a playlist that already has the song. B keeps the glass and shows
a check. Neither can be clicked again.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QLineEdit

from _demos.choices_t110.common import (
    SourceFlatButton, Stage, application, capture_window, ink, paint_flat_disc,
    paint_panel, ui_font,
)


@dataclass
class PlaylistItem:
    name: str
    hue: int
    count: int
    contains: bool
    pinned: bool = False


def seed() -> list[PlaylistItem]:
    return [
        PlaylistItem("夜航", 18, 12, True),
        PlaylistItem("通勤", 200, 8, False),
        PlaylistItem("深夜耳机", 262, 6, False),
        PlaylistItem("华语私藏", 340, 21, True, True),
        PlaylistItem("运动", 128, 4, False),
        PlaylistItem("雨天", 210, 3, False),
    ]


def reorder(items: list, source: int, target: int) -> None:
    if source == target or not 0 <= source < len(items):
        return
    item = items.pop(source)
    items.insert(max(0, min(target, len(items))), item)


_covers: dict[tuple[int, int], QImage] = {}


def cover_image(hue: int, side: int = 88) -> QImage:
    key = (hue, side)
    cached = _covers.get(key)
    if cached is not None:
        return cached
    image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor.fromHsv(hue % 360, 90, 180))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(0, 0, side, side, 16, 16)
    painter.end()
    _covers[key] = image
    return image


class PlaylistDemo(Stage):
    def __init__(self):
        super().__init__("4  添加到歌单", (
            ("grey", "A  已在歌单 · 变灰"), ("check", "B  已在歌单 · 打勾"),
        ))
        self.resize(1080, 760)
        self.items = seed()
        self.toast = "点还没有这首歌的歌单。已经在里面的不能再加。"
        self.drag_index: int | None = None
        self.drag_offset = QPointF()
        self.drag_point = QPointF()
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("搜索歌单")
        self.search.setFont(ui_font(13))
        self.search.setStyleSheet("QLineEdit { color: #f4f5f7; background: transparent; border: none; padding: 0 12px; }")
        self.search.textChanged.connect(lambda _text: self.update())
        self.creator = QLineEdit(self)
        self.creator.setPlaceholderText("新歌单名称，回车创建")
        self.creator.setFont(ui_font(13))
        self.creator.setStyleSheet("QLineEdit { color: #f4f5f7; background: transparent; border: none; padding: 0 12px; }")
        self.creator.returnPressed.connect(self.create_playlist)
        self.creator.hide()
        self.create_button = SourceFlatButton("新建歌单", self, primary=False)
        self.create_button.clicked.connect(self.begin_create)
        self.creating = False

    def description(self) -> str:
        if self.option == "grey":
            return "添加弹窗只负责加入。已经有这首歌的歌单变灰，写着「已在歌单」，点了没反应。排序和删除只在右边的个人歌单。"
        return "添加弹窗只负责加入。已经有这首歌的歌单打勾，写着「已在歌单」，不能再点。排序和删除只在右边的个人歌单。"

    def begin_create(self) -> None:
        self.creating = True
        self.relayout()
        self.creator.setFocus()

    def create_playlist(self) -> None:
        name = self.creator.text().strip()
        if not name:
            return
        self.items.insert(0, PlaylistItem(name, (len(self.items) * 47) % 360, 0, False))
        self.creator.clear()
        self.creating = False
        self.toast = f"已新建「{name}」。新歌单还没有这首歌，可以加入。"
        self.relayout()
        self.update()

    def sheet_rect(self) -> QRectF:
        body = self.body_rect()
        return QRectF(body.left(), body.top(), body.width() * 0.58, body.height())

    def personal_rect(self) -> QRectF:
        body = self.body_rect()
        left = self.sheet_rect().right() + 12
        return QRectF(left, body.top(), body.right() - left, body.height())

    def relayout(self) -> None:
        sheet = self.sheet_rect()
        self.search.setGeometry(int(sheet.left() + 16), int(sheet.top() + 56), int(sheet.width() - 32), 34)
        self.create_button.setGeometry(int(sheet.right() - 128), int(sheet.top() + 14), 112, 32)
        self.creator.setVisible(self.creating)
        self.creator.setGeometry(int(sheet.left() + 16), int(sheet.bottom() - 50), int(sheet.width() - 32), 34)

    def filtered(self) -> list[int]:
        query = self.search.text().strip()
        return [index for index, item in enumerate(self.items) if not query or query in item.name]

    def _add_rows(self) -> list[tuple[int, QRectF]]:
        sheet = self.sheet_rect()
        y = sheet.top() + 102
        rows = []
        for index in self.filtered():
            rect = QRectF(sheet.left() + 16, y, sheet.width() - 32, 62)
            if rect.bottom() > sheet.bottom() - (64 if self.creating else 36):
                break
            rows.append((index, rect))
            y += 68
        return rows

    def _personal_rows(self) -> list[tuple[int, QRectF]]:
        panel = self.personal_rect()
        y = panel.top() + 46
        rows = []
        for index, _item in enumerate(self.items):
            rect = QRectF(panel.left() + 12, y, panel.width() - 24, 48)
            if rect.bottom() > panel.bottom() - 16:
                break
            rows.append((index, rect))
            y += 54
        return rows

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        point = event.position()
        for index, rect in self._add_rows():
            if not rect.contains(point):
                continue
            item = self.items[index]
            if item.contains:
                self.toast = f"「{item.name}」已有这首歌，不能重复加入。"
            else:
                item.contains = True
                item.count += 1
                self.toast = f"已加入「{item.name}」。"
            self.update()
            event.accept()
            return
        for index, rect in self._personal_rows():
            if not rect.contains(point):
                continue
            pin = QRectF(rect.right() - 58, rect.top() + 12, 24, 24)
            delete = QRectF(rect.right() - 30, rect.top() + 12, 24, 24)
            if pin.contains(point):
                item = self.items.pop(index)
                item.pinned = True
                self.items.insert(0, item)
                self.toast = f"「{item.name}」已在个人歌单里置顶。"
            elif delete.contains(point):
                name = self.items.pop(index).name
                self.toast = f"已从个人歌单删除「{name}」。"
            else:
                self.drag_index = index
                self.drag_offset = point - rect.topLeft()
                self.drag_point = point
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.drag_index is None:
            super().mouseMoveEvent(event)
            return
        self.drag_point = event.position()
        target = next((index for index, rect in self._personal_rows() if rect.contains(event.position())), None)
        if target is not None and target != self.drag_index:
            reorder(self.items, self.drag_index, target)
            self.drag_index = target
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self.drag_index is None:
            super().mouseReleaseEvent(event)
            return
        self.drag_index = None
        self.toast = "个人歌单的顺序已更新。"
        self.update()
        event.accept()

    def paint_body(self, painter: QPainter, backdrop) -> None:
        sheet = self.sheet_rect()
        panel = self.personal_rect()
        self._paint_sheet(painter, backdrop, sheet)
        self._paint_personal(painter, backdrop, panel)
        painter.setPen(ink())
        painter.setFont(ui_font(12))
        painter.drawText(QRectF(sheet.left() + 16, sheet.bottom() - 28, sheet.width() - 32, 20),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.toast)

    def _paint_sheet(self, painter: QPainter, backdrop, sheet: QRectF) -> None:
        paint_panel(painter, sheet, backdrop, radius=22)
        painter.setPen(ink())
        painter.setFont(ui_font(16, bold=True))
        painter.drawText(QRectF(sheet.left() + 18, sheet.top() + 12, 180, 32),
                         Qt.AlignmentFlag.AlignVCenter, "添加到歌单")
        if self.search.isVisible():
            paint_flat_disc(painter, QRectF(self.search.geometry()))
        if self.creator.isVisible():
            paint_flat_disc(painter, QRectF(self.creator.geometry()))
        for index, rect in self._add_rows():
            item = self.items[index]
            if item.contains and self.option == "grey":
                painter.save()
                painter.setOpacity(0.38)
            paint_flat_disc(painter, rect, on=item.contains and self.option == "check", fill_on=78, edge_on=150)
            cover = QRectF(rect.left() + 8, rect.center().y() - 20, 40, 40)
            painter.drawImage(cover, cover_image(item.hue))
            painter.setPen(ink())
            painter.setFont(ui_font(14, bold=True))
            painter.drawText(QRectF(cover.right() + 10, rect.top() + 8, rect.width() - 150, 22),
                             Qt.AlignmentFlag.AlignVCenter, item.name)
            painter.setFont(ui_font(12))
            painter.setPen(ink(180))
            detail = "已在歌单" if item.contains else f"{item.count} 首歌曲"
            painter.drawText(QRectF(cover.right() + 10, rect.top() + 32, rect.width() - 150, 18),
                             Qt.AlignmentFlag.AlignVCenter, detail)
            if item.contains and self.option == "check":
                painter.setPen(QColor("#d7ffe4"))
                painter.setFont(ui_font(16, bold=True))
                painter.drawText(rect.adjusted(0, 0, -14, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "✓")
            if item.contains and self.option == "grey":
                painter.restore()

    def _paint_personal(self, painter: QPainter, backdrop, panel: QRectF) -> None:
        paint_panel(painter, panel, backdrop, radius=22)
        painter.setPen(ink())
        painter.setFont(ui_font(15, bold=True))
        painter.drawText(QRectF(panel.left() + 14, panel.top() + 10, panel.width() - 28, 28),
                         Qt.AlignmentFlag.AlignVCenter, "个人歌单")
        painter.setFont(ui_font(11))
        painter.setPen(ink(180))
        for index, rect in self._personal_rows():
            item = self.items[index]
            moving = index == self.drag_index
            target = rect.translated(self.drag_point - self.drag_offset - rect.topLeft()) if moving else rect
            paint_flat_disc(painter, target, on=item.pinned, fill_on=80, edge_on=160)
            painter.setPen(ink())
            painter.setFont(ui_font(13, bold=True))
            label = item.name + (" · 置顶" if item.pinned else "")
            painter.drawText(target.adjusted(12, 0, -64, 0), Qt.AlignmentFlag.AlignVCenter, label)
            painter.setFont(ui_font(11))
            painter.drawText(QRectF(target.right() - 58, target.top(), 24, target.height()), Qt.AlignmentFlag.AlignCenter, "顶")
            painter.drawText(QRectF(target.right() - 30, target.top(), 24, target.height()), Qt.AlignmentFlag.AlignCenter, "删")


def capture(directory) -> list:
    from pathlib import Path
    directory = Path(directory)
    items = ["a", "b", "c"]
    reorder(items, 2, 0)
    assert items == ["c", "a", "b"]
    app = application()
    window = PlaylistDemo()
    from _demos.choices_t110.common import assert_painted, contact_sheet, save_image
    frames = []
    for option, label in (("grey", "A  已在歌单变灰，不能再点"), ("check", "B  已在歌单打勾，不能再点")):
        window.choose(option)
        window.relayout()
        image = capture_window(window)
        assert_painted(image, label)
        frames.append((label, image))
        save_image(image, directory / f"playlist_{option}.png")
    blocked = [item.name for item in window.items if item.contains]
    assert "夜航" in blocked and "华语私藏" in blocked
    sheet = contact_sheet(frames, directory / "playlist_sheet.png", columns=1)
    window.close()
    del app
    return ["playlist add only", str(sheet)]


def main() -> None:
    app = application()
    window = PlaylistDemo()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
