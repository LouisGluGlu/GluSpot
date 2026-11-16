"""Island lyric centering and magnet-tile placement. Design sample only.

Run from the repository root. Does not open a player or change product code.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QApplication, QWidget

from layout import (
    ANCHORS,
    FONT_PX,
    GAP,
    INSET,
    ISLAND_H,
    LONG_LINE,
    LYRIC_COPY,
    MAGNET_COPY,
    MAGNET_H,
    MAX_WIDTH,
    MIN_WIDTH,
    RELEASE_X,
    RELEASE_Y,
    RIGHT_PAD,
    SCREEN_H,
    SCREEN_W,
    SHORT_LINE,
    STANDARD_WIDTH,
    album_anchor_offset,
    clamp,
    clamp_shift,
    cover_rect,
    lyric_pose,
    magnet_pose,
    ui_font,
)


DESK = QColor("#12141a")
INK = QColor(244, 245, 247)
TICK = QColor("#f2c14d")
# Source-page flat glass: translucent fill, 1px rim, no specular highlight.
FILL_IDLE = 56
FILL_PRIMARY = 74
EDGE_IDLE = 56
EDGE_PRIMARY = 96


def _num(value: float) -> str:
    rounded = round(value)
    if abs(value - rounded) < 0.05:
        return str(int(rounded))
    return f"{value:.1f}"


def _overflow(pose) -> bool:
    return pose.text_left < pose.clip.left() - 1.0 or pose.text_right > pose.clip.right() + 1.0


def treatment(scheme: str, pose) -> str:
    overflows = _overflow(pose)
    if scheme == "a":
        return "滚动" if overflows else "放得下"
    if scheme == "b":
        if pose.font_px < FONT_PX and overflows:
            return "缩小后截断"
        if pose.font_px < FONT_PX:
            return "缩小"
        return "截断" if overflows else "放得下"
    return "截断" if overflows else "放得下"


class FlatButton(QAbstractButton):
    """Flat translucent pill. One-pixel bright rim, no highlight streak."""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setText(text)
        self.selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFont(ui_font(13))
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setFixedHeight(32)
        self.fit()

    def fit(self) -> None:
        self.setFixedWidth(max(64, self.fontMetrics().horizontalAdvance(self.text()) + 28))

    def paintEvent(self, _event) -> None:
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if rect.width() < 2 or rect.height() < 2:
            return
        fill = FILL_PRIMARY if self.selected else FILL_IDLE
        edge = EDGE_PRIMARY if self.selected else EDGE_IDLE
        if self.isDown():
            fill = max(36, fill - 12)
        elif self.underMouse():
            fill = min(255, fill + 16)
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setPen(QPen(QColor(255, 255, 255, edge), 1))
            painter.setBrush(QColor(255, 255, 255, fill))
            painter.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
            painter.setPen(INK)
            painter.setFont(self.font())
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


def _cover_image(side: int = 180) -> QImage:
    image = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#1b1630"))
    with QPainter(image) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        sky = QLinearGradient(0, 0, 0, side)
        sky.setColorAt(0.0, QColor("#ffd7a1"))
        sky.setColorAt(0.42, QColor("#f08a5c"))
        sky.setColorAt(0.72, QColor("#6d4c7a"))
        sky.setColorAt(1.0, QColor("#241833"))
        painter.fillRect(image.rect(), sky)
        sun = QRectF(side * 0.58, side * 0.34, side * 0.22, side * 0.22)
        glow = QLinearGradient(sun.center().x(), sun.top(), sun.center().x(), sun.bottom())
        glow.setColorAt(0.0, QColor(255, 236, 196, 230))
        glow.setColorAt(1.0, QColor(255, 176, 96, 40))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(sun.adjusted(-side * 0.06, -side * 0.06, side * 0.06, side * 0.06))
        painter.setBrush(QColor("#ffe6b4"))
        painter.drawEllipse(sun)
        painter.setBrush(QColor("#2a2144"))
        painter.drawRect(QRectF(0, side * 0.78, side, side * 0.22))
        buildings = (
            (0.08, 0.62, 0.16, 0.38),
            (0.22, 0.48, 0.12, 0.52),
            (0.33, 0.56, 0.18, 0.44),
            (0.50, 0.42, 0.10, 0.58),
            (0.60, 0.58, 0.22, 0.42),
            (0.80, 0.50, 0.14, 0.50),
        )
        for x, y, w, h in buildings:
            block = QRectF(side * x, side * y, side * w, side * h)
            painter.setBrush(QColor("#1a1430"))
            painter.drawRect(block)
            painter.setBrush(QColor("#ffd7a1"))
            step = max(3.0, side * 0.035)
            light_y = block.top() + step
            while light_y < block.bottom() - step:
                light_x = block.left() + step * 0.7
                while light_x < block.right() - step * 0.6:
                    if int(light_x + light_y) % 2 == 0:
                        painter.drawRect(QRectF(light_x, light_y, max(1.0, step * 0.28), max(1.0, step * 0.28)))
                    light_x += step
                light_y += step
    return image


class DemoWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GluSpot 岛歌词居中与磁贴")
        self.mode = "lyric"
        self.scheme = "a"
        self.anchor = "top"
        self.island_width = 420.0
        self.shift = 24.0
        self.phase = 0.0
        self.release_x = RELEASE_X
        self.release_y = RELEASE_Y
        self._cover = _cover_image()
        self._drag = ""
        self._drag_anchor = 0.0
        self._frozen_crop: QRectF | None = None
        self._last = time.monotonic()
        self.mode_buttons: list[tuple[str, FlatButton]] = []
        self.scheme_buttons: list[tuple[str, FlatButton]] = []
        self.anchor_buttons: list[tuple[str, FlatButton]] = []
        for key, label in (("lyric", "歌词居中"), ("magnet", "磁贴定位")):
            button = FlatButton(label, self)
            button.clicked.connect(lambda _checked=False, key=key: self.set_mode(key))
            self.mode_buttons.append((key, button))
        for key in ("a", "b", "c"):
            button = FlatButton(LYRIC_COPY[key][0], self)
            button.clicked.connect(lambda _checked=False, key=key: self.set_scheme(key))
            self.scheme_buttons.append((key, button))
        for key, label in ANCHORS:
            button = FlatButton(label, self)
            button.clicked.connect(lambda _checked=False, key=key: self.set_anchor(key))
            self.anchor_buttons.append((key, button))
        self._sync_buttons()
        self.setMinimumSize(1100, 640)
        self.resize(1280, 740)
        self.setMouseTracking(True)
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def set_mode(self, mode: str) -> None:
        if mode not in {"lyric", "magnet"}:
            return
        self.mode = mode
        self._drag = ""
        self._sync_buttons()
        self.update()

    def set_scheme(self, scheme: str) -> None:
        if scheme not in {"a", "b", "c"}:
            return
        self.scheme = scheme
        self._sync_buttons()
        self.update()

    def set_anchor(self, anchor: str) -> None:
        if anchor not in {key for key, _label in ANCHORS}:
            return
        self.anchor = anchor
        self._sync_buttons()
        self.update()

    def prepare_lyric(self, scheme: str) -> None:
        self.timer.stop()
        self.mode = "lyric"
        self.scheme = scheme
        self.island_width = 420.0
        self.shift = 24.0
        self.phase = 0.0
        self._drag = ""
        self._sync_buttons()
        self.update()

    def prepare_magnet(self, scheme: str) -> None:
        self.timer.stop()
        self.mode = "magnet"
        self.scheme = scheme
        self.anchor = "top"
        self.release_x = RELEASE_X
        self.release_y = RELEASE_Y
        self._drag = ""
        self._frozen_crop = None
        self._sync_buttons()
        self.update()

    def _sync_buttons(self) -> None:
        copy = LYRIC_COPY if self.mode == "lyric" else MAGNET_COPY
        for key, button in self.mode_buttons:
            button.selected = key == self.mode
            button.update()
        for key, button in self.scheme_buttons:
            button.setText(copy[key][0])
            button.fit()
            button.selected = key == self.scheme
            button.update()
        for key, button in self.anchor_buttons:
            button.setVisible(self.mode == "magnet")
            button.selected = key == self.anchor
            button.update()
        self._place_buttons()

    def _place_buttons(self) -> None:
        x = 28
        y = 74
        limit = max(200, self.width() - 28)
        for _key, button in self.mode_buttons + self.scheme_buttons:
            if x + button.width() > limit:
                x = 28
                y += 40
            button.setGeometry(x, y, button.width(), 32)
            x += button.width() + 8
        if self.mode != "magnet":
            return
        x = 28
        y = 246
        for _key, button in self.anchor_buttons:
            if x + button.width() > limit:
                x = 28
                y += 40
            button.setGeometry(x, y, button.width(), 32)
            x += button.width() + 8

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_buttons()

    def facts_rect(self) -> QRectF:
        return QRectF(28, 118, max(200, self.width() - 56), 118)

    def lyric_islands(self) -> tuple[QRectF, QRectF]:
        left = 64.0
        width = clamp(self.island_width, MIN_WIDTH, MAX_WIDTH)
        short = QRectF(left, 268, width, ISLAND_H)
        long = QRectF(left, short.bottom() + 58, width, ISLAND_H)
        return short, long

    def magnet_dest(self) -> QRectF:
        top = 300.0
        return QRectF(28, top, max(200, self.width() - 56), max(180, self.height() - top - 78))

    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.05, max(0.0, now - self._last))
        self._last = now
        if self.mode == "lyric" and self.scheme == "a":
            self.phase += 1.15 * dt
            self.update()

    def keyPressEvent(self, event) -> None:
        scheme_keys = {Qt.Key.Key_1: "a", Qt.Key.Key_2: "b", Qt.Key.Key_3: "c"}
        if event.key() in scheme_keys:
            self.set_scheme(scheme_keys[event.key()])
            event.accept()
            return
        if event.key() == Qt.Key.Key_Tab:
            self.set_mode("magnet" if self.mode == "lyric" else "lyric")
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        if self.mode == "lyric":
            if event.key() == Qt.Key.Key_Left:
                self.island_width = clamp(self.island_width - 8, MIN_WIDTH, MAX_WIDTH)
            elif event.key() == Qt.Key.Key_Right:
                self.island_width = clamp(self.island_width + 8, MIN_WIDTH, MAX_WIDTH)
            elif event.key() == Qt.Key.Key_Up:
                self.shift = clamp_shift(self.island_width, self.shift + 4)
            elif event.key() == Qt.Key.Key_Down:
                self.shift = clamp_shift(self.island_width, self.shift - 4)
            else:
                super().keyPressEvent(event)
                return
            self.shift = clamp_shift(self.island_width, self.shift)
            self.update()
            event.accept()
            return
        if event.key() in {Qt.Key.Key_Up, Qt.Key.Key_Down}:
            order = [key for key, _label in ANCHORS]
            step = 1 if event.key() == Qt.Key.Key_Down else -1
            self.set_anchor(order[(order.index(self.anchor) + step) % len(order)])
            event.accept()
            return
        if self.scheme == "a" and event.key() in {Qt.Key.Key_Left, Qt.Key.Key_Right}:
            delta = -12 if event.key() == Qt.Key.Key_Left else 12
            if self.anchor in {"top", "bottom"}:
                self.release_x = clamp(self.release_x + delta, 0, SCREEN_W)
            elif self.anchor in {"left", "right"}:
                self.release_y = clamp(self.release_y + delta, 0, SCREEN_H)
            self.update()
            event.accept()
            return
        super().keyPressEvent(event)

    def _edge_hit(self, pos: QPointF, rect: QRectF) -> bool:
        return abs(pos.x() - rect.right()) <= 12 and rect.top() - 8 <= pos.y() <= rect.bottom() + 8

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = QPointF(event.position())
        if self.mode == "lyric":
            for rect in self.lyric_islands():
                if self._edge_hit(pos, rect):
                    self._drag = "edge"
                    self._drag_anchor = rect.left()
                    event.accept()
                    return
                cover = cover_rect(rect, self.shift)
                if cover.contains(pos):
                    self._drag = "cover"
                    self._drag_anchor = pos.x() - cover.left()
                    event.accept()
                    return
            return
        if self.scheme != "a" or self.anchor not in {"top", "bottom", "left", "right"}:
            return
        pose = self._magnet()
        origin, scale = self._magnet_view()
        dest = self.magnet_dest()
        screen = self._to_screen(pos, origin, scale, dest)
        if pose.rect.contains(screen):
            self._drag = "magnet"
            self._frozen_crop = self._crop(pose)
            if self.anchor in {"top", "bottom"}:
                self._drag_anchor = screen.x() - self.release_x
            else:
                self._drag_anchor = screen.y() - self.release_y
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        pos = QPointF(event.position())
        if self._drag == "edge":
            self.island_width = clamp(pos.x() - self._drag_anchor, MIN_WIDTH, MAX_WIDTH)
            self.shift = clamp_shift(self.island_width, self.shift)
            self.update()
            return
        if self._drag == "cover":
            island = self.lyric_islands()[0]
            self.shift = clamp_shift(self.island_width, pos.x() - self._drag_anchor - island.left() - INSET)
            self.update()
            return
        if self._drag == "magnet" and self._frozen_crop is not None:
            origin, scale = self._magnet_view()
            screen = self._to_screen(pos, origin, scale, self.magnet_dest())
            if self.anchor in {"top", "bottom"}:
                self.release_x = clamp(screen.x() - self._drag_anchor, 0, SCREEN_W)
            else:
                self.release_y = clamp(screen.y() - self._drag_anchor, 0, SCREEN_H)
            self.update()
            return
        if self.mode == "lyric":
            cursor = Qt.CursorShape.ArrowCursor
            for rect in self.lyric_islands():
                if self._edge_hit(pos, rect):
                    cursor = Qt.CursorShape.SizeHorCursor
                    break
                if cover_rect(rect, self.shift).contains(pos):
                    cursor = Qt.CursorShape.OpenHandCursor
                    break
            self.setCursor(cursor)
        elif self._drag == "":
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, _event) -> None:
        self._drag = ""
        self._frozen_crop = None
        self.unsetCursor()
        self.update()

    def _magnet(self):
        return magnet_pose(self.scheme, self.anchor, self.release_x, self.release_y)

    def _crop(self, pose) -> QRectF:
        pad = 72.0
        rect = pose.rect
        # Top and bottom use one wide strip so a centered island and a
        # let-go offset can be compared on the same screen.
        if self.anchor in {"top", "bottom"}:
            x0 = 0.0
            x1 = min(SCREEN_W, max(1320.0, rect.right() + 48.0))
            if self.anchor == "top":
                y0, y1 = 0.0, 150.0
            else:
                y0, y1 = SCREEN_H - 150.0, SCREEN_H
            return QRectF(x0, y0, max(1.0, x1 - x0), max(1.0, y1 - y0))
        x0, y0 = rect.left() - pad, rect.top() - pad
        x1, y1 = rect.right() + pad, rect.bottom() + pad
        if self.anchor in {"top_left", "top_right"}:
            y0 = 0.0
        if self.anchor in {"bottom_left", "bottom_right"}:
            y1 = SCREEN_H
        if self.anchor in {"left", "top_left", "bottom_left"}:
            x0 = 0.0
        if self.anchor in {"right", "top_right", "bottom_right"}:
            x1 = SCREEN_W
        x0, y0 = max(0.0, x0), max(0.0, y0)
        x1, y1 = min(SCREEN_W, x1), min(SCREEN_H, y1)
        return QRectF(x0, y0, max(1.0, x1 - x0), max(1.0, y1 - y0))

    def _magnet_view(self) -> tuple[QRectF, float]:
        pose = self._magnet()
        crop = self._frozen_crop if self._frozen_crop is not None else self._crop(pose)
        dest = self.magnet_dest()
        scale = min(1.45, dest.width() / crop.width(), dest.height() / crop.height())
        return crop, scale

    def _to_widget(self, x: float, y: float, origin: QRectF, scale: float, dest: QRectF) -> QPointF:
        return QPointF(dest.x() + (x - origin.x()) * scale, dest.y() + (y - origin.y()) * scale)

    def _to_screen(self, pos: QPointF, origin: QRectF, scale: float, dest: QRectF) -> QPointF:
        return QPointF(origin.x() + (pos.x() - dest.x()) / scale, origin.y() + (pos.y() - dest.y()) / scale)

    def paintEvent(self, _event) -> None:
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.fillRect(self.rect(), DESK)
            self._paint_header(painter)
            if self.mode == "lyric":
                self._paint_lyric_page(painter)
            else:
                self._paint_magnet_page(painter)
            painter.setFont(ui_font(12))
            painter.setPen(QColor(244, 245, 247, 150))
            hint = "1 2 3 换方案   Tab 换页   方向键微调   Esc 退出   这是设计示例，不改产品"
            painter.drawText(QRectF(28, self.height() - 32, self.width() - 56, 22),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, hint)

    def _paint_header(self, painter: QPainter) -> None:
        painter.setPen(INK)
        painter.setFont(ui_font(20, bold=True))
        painter.drawText(QRectF(28, 14, self.width() - 56, 28),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         "岛歌词居中" if self.mode == "lyric" else "磁贴的标准位置和宽度")
        painter.setFont(ui_font(13))
        painter.setPen(QColor(244, 245, 247, 200))
        summary = ("拖右缘改宽度，拖封面改专辑位置。中线每帧按当前封面重算。琥珀竖线只是示意。"
                   if self.mode == "lyric"
                   else "点边角看吸附结果。方案 A 可沿边拖。金色条是边距示意，不是产品材质。")
        painter.drawText(QRectF(28, 44, self.width() - 56, 24),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, summary)
        copy = (LYRIC_COPY if self.mode == "lyric" else MAGNET_COPY)[self.scheme]
        rect = self.facts_rect()
        painter.setFont(ui_font(14))
        painter.setPen(INK)
        block = "\n".join(copy[1:])
        body = QRectF(rect.left(), rect.top(), rect.width(), rect.height() - 28)
        painter.drawText(body, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, block)
        painter.setFont(ui_font(12))
        painter.setPen(QColor(244, 245, 247, 140))
        note = ("产品现状：短句已在剩余区里居中；长句整行滚动从剩余区左缘拉开。"
                if self.mode == "lyric"
                else "产品现状：顶边吸附贴顶，并留在松手的横向位置；角落预设距边 8；吸附不改内容宽。")
        painter.drawText(QRectF(rect.left(), rect.bottom() - 22, rect.width(), 20),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, note)

    def _paint_island(self, painter: QPainter, rect: QRectF, text: str, scheme: str, phase: float) -> None:
        pose = lyric_pose(scheme, rect, self.shift, text, phase)
        radius = min(rect.height() / 2.0, 30.0)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        shadow = QColor(0, 0, 0, 70)
        painter.setBrush(shadow)
        painter.drawPath(self._shifted(path, 0, 5))
        body = QLinearGradient(0, rect.top(), 0, rect.bottom())
        body.setColorAt(0.0, QColor(4, 8, 18))
        body.setColorAt(0.30, QColor(6, 11, 24))
        body.setColorAt(0.66, QColor(8, 16, 32))
        body.setColorAt(1.0, QColor(12, 24, 44))
        painter.setBrush(body)
        painter.drawPath(path)
        painter.setClipPath(path)
        cover = pose.cover
        cover_path = QPainterPath()
        cover_path.addRoundedRect(cover, min(cover.width() / 4.0, 8.0), min(cover.width() / 4.0, 8.0))
        painter.setClipPath(cover_path, Qt.ClipOperation.IntersectClip)
        painter.drawImage(cover, self._cover)
        painter.setClipPath(path)
        slot = QPainterPath()
        slot.addRect(pose.clip)
        painter.setClipPath(slot, Qt.ClipOperation.IntersectClip)
        painter.setFont(ui_font(pose.font_px, bold=True))
        painter.setPen(QColor(255, 255, 255, 236))
        painter.drawText(QRectF(pose.text_left, rect.top(), pose.text_width + 4, rect.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        if pose.fade and _overflow(pose):
            self._paint_fade(painter, pose)
        painter.setClipping(False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 46), 1))
        painter.drawPath(path)
        painter.restore()
        self._paint_tick(painter, pose.anchor, rect.top())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 78))
        grip = QRectF(rect.right() - 9, rect.center().y() - 9, 5, 18)
        painter.drawRoundedRect(grip, 2, 2)

    def _shifted(self, path: QPainterPath, dx: float, dy: float) -> QPainterPath:
        moved = QPainterPath(path)
        moved.translate(dx, dy)
        return moved

    def _paint_fade(self, painter: QPainter, pose) -> None:
        color = QColor(8, 16, 32)
        fade = 16.0
        if pose.text_left < pose.clip.left() - 1:
            grad = QLinearGradient(pose.clip.left(), 0, pose.clip.left() + fade, 0)
            grad.setColorAt(0.0, color)
            grad.setColorAt(1.0, QColor(color.red(), color.green(), color.blue(), 0))
            painter.fillRect(QRectF(pose.clip.left(), pose.clip.top(), fade, pose.clip.height()), grad)
        if pose.text_right > pose.clip.right() + 1:
            start = pose.clip.right() - fade
            grad = QLinearGradient(start, 0, pose.clip.right(), 0)
            grad.setColorAt(0.0, QColor(color.red(), color.green(), color.blue(), 0))
            grad.setColorAt(1.0, color)
            painter.fillRect(QRectF(start, pose.clip.top(), fade, pose.clip.height()), grad)

    def _paint_tick(self, painter: QPainter, x: float, top: float) -> None:
        painter.setPen(QPen(TICK, 1.4))
        painter.drawLine(QPointF(x, top - 16), QPointF(x, top + 8))
        painter.setFont(ui_font(11))
        painter.drawText(QRectF(x + 5, top - 32, 52, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, _num(x))

    def _paint_lyric_page(self, painter: QPainter) -> None:
        short, long = self.lyric_islands()
        self._paint_caption(painter, short, SHORT_LINE, "短句")
        self._paint_island(painter, short, SHORT_LINE, self.scheme, 0.0)
        self._paint_caption(painter, long, LONG_LINE, "长句")
        self._paint_island(painter, long, LONG_LINE, self.scheme, self.phase)
        short_pose = lyric_pose(self.scheme, short, self.shift, SHORT_LINE, 0.0)
        long_pose = lyric_pose(self.scheme, long, self.shift, LONG_LINE, self.phase)
        painter.setFont(ui_font(13))
        painter.setPen(QColor(244, 245, 247, 210))
        cover = short_pose.cover
        line = (f"宽 {_num(short.width())}    封面中心 {_num(cover.center().x())}    "
                f"岛中心 {_num(short.center().x())}    短句中线 {_num(short_pose.anchor)}    "
                f"长句中线 {_num(long_pose.anchor)}    锚点距离 {_num(album_anchor_offset())}")
        painter.drawText(QRectF(64, long.bottom() + 18, self.width() - 96, 22),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
        painter.setPen(QColor(244, 245, 247, 140))
        painter.setFont(ui_font(12))
        painter.drawText(QRectF(64, long.bottom() + 42, self.width() - 96, 20),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         f"剩余区从封面右缘加 {int(GAP)} 到岛右缘减 {int(RIGHT_PAD)}。"
                         f"标准宽 {int(STANDARD_WIDTH)} 只用于方案 C 的冻结距离。")

    def _paint_caption(self, painter: QPainter, rect: QRectF, text: str, name: str) -> None:
        pose = lyric_pose(self.scheme, rect, self.shift, text, 0.0 if name == "短句" else self.phase)
        painter.setFont(ui_font(12))
        painter.setPen(QColor(244, 245, 247, 180))
        painter.drawText(QRectF(rect.left(), rect.top() - 22, 220, 18),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         f"{name} · {pose.font_px}px · {treatment(self.scheme, pose)}")

    def _paint_magnet_page(self, painter: QPainter) -> None:
        pose = self._magnet()
        origin, scale = self._magnet_view()
        dest = self.magnet_dest()
        # Letterbox the scaled screen inside dest.
        used = QRectF(dest.x(), dest.y(), origin.width() * scale, origin.height() * scale)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#d7dbe3"))
        painter.drawRoundedRect(used.adjusted(-1, -1, 1, 1), 8, 8)
        painter.save()
        painter.setClipRect(used)
        painter.fillRect(used, QColor("#e7ebf2"))
        outer = self._margin_outer(pose)
        if outer is not None:
            a = self._to_widget(outer.left(), outer.top(), origin, scale, dest)
            b = self._to_widget(outer.right(), outer.bottom(), origin, scale, dest)
            painter.fillRect(QRectF(a, b), QColor(214, 140, 48, 170))
        island = pose.rect
        top_left = self._to_widget(island.left(), island.top(), origin, scale, dest)
        mapped = QRectF(top_left.x(), top_left.y(), island.width() * scale, island.height() * scale)
        self._paint_mapped_island(painter, mapped)
        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(70, 78, 92), 1.2))
        painter.drawRoundedRect(used.adjusted(-1, -1, 1, 1), 8, 8)
        self._paint_screen_edges(painter, origin, scale, dest, used)
        names = dict(ANCHORS)
        painter.setFont(ui_font(13))
        painter.setPen(INK)
        center_x = pose.rect.center().x()
        deviate = center_x - SCREEN_W / 2.0
        line = (f"{names[self.anchor]}    边距 {_num(pose.margin)}    宽 {_num(pose.w)}    高 {_num(pose.h)}    "
                f"岛中心 {_num(center_x)}    屏幕中线 {int(SCREEN_W / 2)}    偏离 {_num(deviate)}    {pose.rule}")
        painter.drawText(QRectF(28, used.bottom() + 14, self.width() - 56, 22),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
        painter.setFont(ui_font(12))
        painter.setPen(QColor(244, 245, 247, 145))
        extra = "方案 A 的顶、底、左、右可沿边拖动，松手停在拖到的位置。" if self.scheme == "a" else "这一方案锁在标准点，拖动不会改位置。"
        painter.drawText(QRectF(28, used.bottom() + 36, self.width() - 56, 20),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, extra)

    def _margin_outer(self, pose) -> QRectF | None:
        if pose.margin <= 0:
            return None
        rect = pose.rect
        extra_left = pose.margin if self.anchor in {"left", "top_left", "bottom_left"} else 0.0
        extra_right = pose.margin if self.anchor in {"right", "top_right", "bottom_right"} else 0.0
        extra_top = pose.margin if self.anchor in {"top", "top_left", "top_right"} else 0.0
        extra_bottom = pose.margin if self.anchor in {"bottom", "bottom_left", "bottom_right"} else 0.0
        return QRectF(rect.left() - extra_left, rect.top() - extra_top,
                      rect.width() + extra_left + extra_right, rect.height() + extra_top + extra_bottom)

    def _paint_mapped_island(self, painter: QPainter, rect: QRectF) -> None:
        # The magnet page shows a real compact island. Lyric centering inside
        # it follows scheme A so the sample itself is not left-aligned.
        radius = min(rect.height() / 2.0, rect.height() * 0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        body = QLinearGradient(0, rect.top(), 0, rect.bottom())
        body.setColorAt(0.0, QColor(4, 8, 18))
        body.setColorAt(1.0, QColor(12, 24, 44))
        painter.setBrush(body)
        painter.drawPath(path)
        cover_side = rect.height() - 2.0 * (INSET / ISLAND_H) * rect.height()
        inset = (INSET / ISLAND_H) * rect.height()
        cover = QRectF(rect.left() + inset, rect.top() + inset, cover_side, cover_side)
        painter.save()
        painter.setClipPath(path)
        cover_path = QPainterPath()
        cover_path.addRoundedRect(cover, cover.width() / 4.0, cover.width() / 4.0)
        painter.setClipPath(cover_path, Qt.ClipOperation.IntersectClip)
        painter.drawImage(cover, self._cover)
        painter.restore()
        # Map the real 48px pose into this scaled pill.
        local = QRectF(0, 0, MAGNET_H * rect.width() / rect.height(), MAGNET_H)
        pose = lyric_pose("a", local, 0.0, SHORT_LINE, 0.0)
        scale = rect.height() / MAGNET_H
        painter.save()
        painter.setClipPath(path)
        slot = QRectF(rect.left() + pose.clip.left() * scale, rect.top(), pose.clip.width() * scale, rect.height())
        painter.setClipRect(slot, Qt.ClipOperation.IntersectClip)
        font_px = max(8, int(round(FONT_PX * scale)))
        painter.setFont(ui_font(font_px, bold=True))
        painter.setPen(QColor(255, 255, 255, 236))
        text_left = rect.left() + pose.text_left * scale
        painter.drawText(QRectF(text_left, rect.top(), pose.text_width * scale + 4, rect.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, SHORT_LINE)
        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1))
        painter.drawPath(path)

    def _paint_screen_edges(self, painter: QPainter, origin: QRectF, scale: float, dest: QRectF, used: QRectF) -> None:
        painter.setPen(QPen(QColor(90, 98, 112), 1))
        if abs(origin.top()) < 0.5:
            y = self._to_widget(0, 0, origin, scale, dest).y()
            painter.drawLine(QPointF(used.left(), y), QPointF(used.right(), y))
        if abs(origin.left()) < 0.5:
            x = self._to_widget(0, 0, origin, scale, dest).x()
            painter.drawLine(QPointF(x, used.top()), QPointF(x, used.bottom()))
        if origin.left() <= SCREEN_W / 2.0 <= origin.right() and self.anchor in {"top", "bottom"}:
            mark = self._to_widget(SCREEN_W / 2.0, 0, origin, scale, dest).x()
            painter.setPen(QPen(QColor(96, 122, 168), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(mark, used.top()), QPointF(mark, used.bottom()))
            painter.setFont(ui_font(11))
            painter.drawText(QRectF(mark + 6, used.bottom() - 18, 70, 14),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "屏幕中线")


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(ui_font(13))
    window = DemoWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
