"""Narrow hide: the cover and title shrink to the top, and private broadcast
can take the cover slot.

A pins the cover, title and artist while the broadcast button leaves with the
other controls. B keeps the broadcast in the cover slot the whole time. C
crossfades: the cover gives that slot to the broadcast as the chrome hides.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPainterPath

from local.personal_dj_icon import paint_personal_dj_icon

from _demos.choices_t110.common import (
    SourceFlatButton, Stage, application, capture_window, ink, paint_flat_disc, ui_font,
)


LYRICS = (
    "Tell you that I might say goodbye?",
    "你应该很清楚这一点吧？",
    "D-E-P-E-N-D-S",
    "把这句话留在窗边",
    "夜航还没有靠岸",
    "封面先让开一点位置",
    "私人播报坐到左上角",
    "歌词继续往下读",
)


# The large cover uses a 14 px corner on a 92 px side. Shrink with the same ratio.
COVER_CORNER_RATIO = 14.0 / 92.0


def cover_corner_radius(side: float) -> float:
    return side * COVER_CORNER_RATIO


def squircle_path(rect: QRectF, exponent: float = 4.0, steps: int = 64) -> QPainterPath:
    """Continuous-curvature cover. The same exponent keeps the corner ratio at every size."""
    path = QPainterPath()
    if rect.width() < 2 or rect.height() < 2:
        return path
    center_x, center_y = rect.center().x(), rect.center().y()
    radius_x, radius_y = rect.width() / 2.0, rect.height() / 2.0
    power = 2.0 / exponent
    points = []
    for index in range(steps):
        angle = math.tau * index / steps
        cosine, sine = math.cos(angle), math.sin(angle)
        points.append(QPointF(
            center_x + radius_x * math.copysign(abs(cosine) ** power, cosine),
            center_y + radius_y * math.copysign(abs(sine) ** power, sine),
        ))
    path.moveTo(points[0])
    for point in points[1:]:
        path.lineTo(point)
    path.closeSubpath()
    return path


def ease(progress: float) -> float:
    progress = max(0.0, min(1.0, progress))
    return progress * progress * (3 - 2 * progress)


def header_pose(option: str, progress: float, frame: QRectF) -> dict:
    """Shown is 0, hidden is 1. Coordinates are inside the narrow frame."""
    shown = ease(progress)
    cover_side = 92 - 56 * shown
    top = 16 - 6 * shown
    left = 16.0
    cover = QRectF(left, top, cover_side, cover_side)
    text_left = cover.right() + 12
    text_width = frame.width() - text_left - 16
    title_h = 28 - 10 * shown
    title = QRectF(text_left, cover.center().y() - title_h - 1, text_width, title_h)
    artist = QRectF(text_left, cover.center().y() + 2, text_width, 18 - 4 * shown)
    broadcast_button = QRectF(frame.center().x() - 18, frame.bottom() - 58, 36, 36)
    if option == "B":
        broadcast = QRectF(cover)
        cover_opacity = 0.0
        button_opacity = 0.0
    elif option == "C":
        broadcast = QRectF(
            broadcast_button.left() + (cover.left() - broadcast_button.left()) * shown,
            broadcast_button.top() + (cover.top() - broadcast_button.top()) * shown,
            broadcast_button.width() + (cover.width() - broadcast_button.width()) * shown,
            broadcast_button.height() + (cover.height() - broadcast_button.height()) * shown,
        )
        cover_opacity = 1.0 - shown
        button_opacity = 1.0 - shown
    else:
        broadcast = QRectF(broadcast_button)
        cover_opacity = 1.0
        button_opacity = 1.0 - shown
    chrome = 1.0 - shown
    lyric_top = cover.bottom() + 18
    return {
        "cover": cover, "title": title, "artist": artist, "broadcast": broadcast,
        "cover_opacity": cover_opacity, "button_opacity": button_opacity,
        "chrome": chrome, "lyric_top": lyric_top, "title_px": 20 - 7 * shown,
        "artist_px": 13 - 2 * shown,
    }


class NarrowDemo(Stage):
    def __init__(self):
        super().__init__("3  窄窗隐藏", (
            ("A", "A  收到顶"), ("B", "B  播报占位"), ("C", "C  交叉切换"),
        ))
        self.resize(920, 860)
        self.progress = 0.0
        self.playing = False
        self.toggle_button = SourceFlatButton("隐藏控件", self, primary=True)
        self.toggle_button.clicked.connect(self.toggle)
        self.anim = QVariantAnimation(self)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.anim.valueChanged.connect(self._set_progress)
        self.anim.finished.connect(self._finished)

    def description(self) -> str:
        if self.option == "A":
            return "隐藏时封面、歌名、歌手缩小并贴住顶部。封面保持连续曲率圆角，圆角按尺寸等比缩小，不会收成圆。私人播报仍在底栏。"
        if self.option == "B":
            return "窄窗里私人播报一直坐在封面槽：左上角，歌名和歌手在它右边。隐藏时这一组一起缩小贴顶。"
        return "显示时左上是封面。隐藏时封面淡出，私人播报从底栏滑进封面槽，再和标题一起贴顶。过渡约 420 毫秒。"

    def on_option(self, key: str) -> None:
        self.progress = 0.0
        self.toggle_button.setText("隐藏控件")
        self.toggle_button.update()

    def relayout(self) -> None:
        self.toggle_button.setGeometry(self.width() - 150, 74, 120, 34)

    def frame_rect(self) -> QRectF:
        body = self.body_rect()
        side = min(390.0, body.width() - 24)
        height = min(680.0, body.height() - 8)
        return QRectF(body.center().x() - side / 2, body.top() + 4, side, height)

    def toggle(self) -> None:
        target = 0.0 if self.progress > 0.5 else 1.0
        self.anim.stop()
        self.anim.setStartValue(float(self.progress))
        self.anim.setEndValue(target)
        self.anim.setDuration(420 if self.option == "C" else 300)
        self.playing = True
        self.toggle_button.setText("显示控件" if target > 0.5 else "隐藏控件")
        self.toggle_button.update()
        self.anim.start()

    def _set_progress(self, value) -> None:
        self.progress = float(value)
        self.update()

    def _finished(self) -> None:
        self.playing = False

    def set_progress(self, value: float) -> None:
        self.anim.stop()
        self.progress = value
        self.toggle_button.setText("显示控件" if value > 0.5 else "隐藏控件")
        self.toggle_button.update()
        self.update()

    def paint_body(self, painter: QPainter, _backdrop) -> None:
        frame = self.frame_rect()
        local = QRectF(0, 0, frame.width(), frame.height())
        pose = header_pose(self.option, self.progress, local)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(6, 8, 12, 170))
        painter.drawRoundedRect(frame, 28, 28)
        painter.save()
        painter.setClipRect(frame.adjusted(1, 1, -1, -1))
        painter.translate(frame.topLeft())
        self._paint_lyrics(painter, local, pose["lyric_top"])
        self._paint_header(painter, pose)
        if pose["chrome"] > 0.02:
            painter.save()
            painter.setOpacity(pose["chrome"])
            self._paint_transport(painter, local)
            painter.restore()
        if self.option == "A" and pose["button_opacity"] > 0.02:
            painter.save()
            painter.setOpacity(pose["button_opacity"])
            button = QRectF(local.center().x() - 18, local.bottom() - 58, 36, 36)
            self._paint_broadcast(painter, button, small=True)
            painter.restore()
        painter.restore()
        painter.setPen(ink(180))
        painter.setFont(ui_font(12))
        state = "过渡中" if self.playing else ("已隐藏" if self.progress > 0.85 else "控件显示")
        radius = cover_corner_radius(pose["cover"].width())
        painter.drawText(QRectF(frame.left(), frame.bottom() + 6, frame.width(), 20),
                         Qt.AlignmentFlag.AlignCenter,
                         f"{state} · 圆角 {radius:.1f}px · 按封面等比")

    def _paint_lyrics(self, painter: QPainter, frame: QRectF, top: float) -> None:
        y = top
        for index, line in enumerate(LYRICS):
            current = index == 0
            painter.setFont(ui_font(22 if current else 16, bold=current))
            painter.setPen(ink(255 if current else 120))
            painter.drawText(QRectF(frame.left() + 28, y, frame.width() - 56, 34), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
            y += 40 if current else 32
            if y > frame.bottom() - 70:
                break

    def _paint_header(self, painter: QPainter, pose: dict) -> None:
        if pose["cover_opacity"] > 0.02:
            painter.save()
            painter.setOpacity(pose["cover_opacity"])
            cover = pose["cover"]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#c8874d"))
            painter.drawPath(squircle_path(cover))
            painter.setPen(ink())
            painter.setFont(ui_font(max(9, cover.width() * 0.28), bold=True))
            painter.drawText(cover, Qt.AlignmentFlag.AlignCenter, "封面")
            painter.restore()
        if self.option != "A":
            self._paint_broadcast(painter, pose["broadcast"], small=pose["broadcast"].width() < 60)
        painter.setPen(ink())
        painter.setFont(ui_font(pose["title_px"], bold=True))
        title = painter.fontMetrics().elidedText("Tell you that I might", Qt.TextElideMode.ElideRight, int(pose["title"].width()))
        painter.drawText(pose["title"], Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        painter.setFont(ui_font(pose["artist_px"]))
        painter.setPen(ink(190))
        painter.drawText(pose["artist"], Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "Haley Smalls")

    def _paint_broadcast(self, painter: QPainter, rect: QRectF, *, small: bool) -> None:
        if rect.width() < 8:
            return
        paint_flat_disc(painter, rect, on=True, fill_on=64, edge_on=160)
        glyph = rect.adjusted(rect.width() * 0.22, rect.height() * 0.22, -rect.width() * 0.22, -rect.height() * 0.22)
        paint_personal_dj_icon(painter, glyph, QColor("#d7ffe4"), elapsed=0.8)
        if not small:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor(63, 210, 127, 180))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 12, 12)

    def _paint_transport(self, painter: QPainter, frame: QRectF) -> None:
        y = frame.bottom() - 112
        for index, label in enumerate(("上一首", "播放", "下一首")):
            side = 42 if index == 1 else 34
            x = frame.center().x() + (index - 1) * 56 - side / 2
            rect = QRectF(x, y + (42 - side) / 2, side, side)
            paint_flat_disc(painter, rect, on=index == 1, fill_on=74, edge_on=120)
            painter.setPen(ink())
            painter.setFont(ui_font(11))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "▶" if index == 1 else ("<<" if index == 0 else ">>"))


def capture(directory) -> list:
    from pathlib import Path
    directory = Path(directory)
    frame = QRectF(0, 0, 390, 680)
    shown = header_pose("A", 0, frame)
    hidden = header_pose("A", 1, frame)
    assert hidden["cover"].top() < shown["cover"].top()
    assert hidden["cover"].width() < shown["cover"].width() * 0.6
    assert hidden["chrome"] < 0.05
    assert abs(cover_corner_radius(shown["cover"].width()) / shown["cover"].width()
               - cover_corner_radius(hidden["cover"].width()) / hidden["cover"].width()) < 1e-6
    assert cover_corner_radius(hidden["cover"].width()) < hidden["cover"].width() * 0.25
    occupied = header_pose("B", 0, frame)
    assert occupied["cover_opacity"] == 0.0
    assert occupied["broadcast"] == occupied["cover"]
    mid = header_pose("C", 0.5, frame)
    assert 0.2 < mid["cover_opacity"] < 0.8
    assert mid["broadcast"].top() < header_pose("C", 0, frame)["broadcast"].top()
    app = application()
    window = NarrowDemo()
    frames = []
    from _demos.choices_t110.common import assert_painted, contact_sheet, save_image
    for option, label, progress in (
        ("A", "A 显示", 0.0), ("A", "A 贴顶隐藏", 1.0),
        ("B", "B 播报占封面", 0.0), ("B", "B 贴顶隐藏", 1.0),
        ("C", "C 交叉中途", 0.45), ("C", "C 播报已入封面槽", 1.0),
    ):
        window.choose(option)
        window.set_progress(progress)
        image = capture_window(window)
        assert_painted(image, label)
        frames.append((label, image))
        save_image(image, directory / f"narrow_{option}_{int(progress * 100)}.png")
    sheet = contact_sheet(frames, directory / "narrow_sheet.png", columns=3)
    window.close()
    del app
    return ["narrow hide", str(sheet)]


def main() -> None:
    app = application()
    window = NarrowDemo()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
