"""Geometry for the island-lyric and magnet-tile design sample.

Numbers that already exist in the product are named below. The three lyric
rules and three snap rules are proposals for this sample only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QRectF
from PySide6.QtGui import QFont, QFontMetrics


# Compact island. `_island_cover_geometry` uses an 8px inset, so a 48px pill
# holds a 32px cover. `_paint_music_compact_base` starts text 12px after the
# cover and stops 25px before the right edge. Compact type is lyric size 18
# minus 2, which is 16.
ISLAND_H = 48.0
INSET = 8.0
GAP = 12.0
RIGHT_PAD = 25.0
FONT_PX = 16
MIN_FONT = 11
# Proposal, not a stored product constant. Single-line content width is
# clamped to 430–610 by `compact_music_target_width_from_content`.
STANDARD_WIDTH = 480.0
MIN_WIDTH = 300.0
MAX_WIDTH = 760.0

SHORT_LINE = "晚风把灯吹得很轻"
LONG_LINE = "穿过整夜的街灯以后还是会在路口等你回头看一眼才肯把这句话说完再走开"

# `build_standalone_position_preset_target` uses an 8px margin.
# `build_standalone_top_notch_target` keeps the release X and sits on the top edge.
SCREEN_W = 1920.0
SCREEN_H = 1080.0
MAGNET_H = 48.0
CONTENT_WIDTH = 520.0
EDGE_WIDTH = 610.0  # min(610, 1920 * 0.32)
CORNER_WIDTH = 320.0
# Sample let-go point for scheme A. The island center follows this on an edge.
RELEASE_X = 360.0
RELEASE_Y = 420.0

ANCHORS = (
    ("top", "顶中"),
    ("top_left", "左上"),
    ("top_right", "右上"),
    ("left", "左中"),
    ("right", "右中"),
    ("bottom", "底中"),
    ("bottom_left", "左下"),
    ("bottom_right", "右下"),
)


def ui_font(pixel: int, *, bold: bool = False) -> QFont:
    font = QFont("WenQuanYi Micro Hei")
    if not font.exactMatch():
        font = QFont("文泉驿微米黑")
    font.setPixelSize(max(1, int(pixel)))
    font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
    return font


def text_width(text: str, pixel: int) -> float:
    return float(QFontMetrics(ui_font(pixel, bold=True)).horizontalAdvance(text))


def clamp(value: float, low: float, high: float) -> float:
    if high < low:
        return low
    return min(max(value, low), high)


def cover_side(height: float = ISLAND_H) -> float:
    return max(0.0, height - 2.0 * INSET)


def clamp_shift(width: float, shift: float, height: float = ISLAND_H) -> float:
    return clamp(shift, 0.0, max(0.0, width - 2.0 * INSET - cover_side(height)))


def cover_rect(island: QRectF, shift: float) -> QRectF:
    side = cover_side(island.height())
    x = island.left() + INSET + clamp_shift(island.width(), shift, island.height())
    return QRectF(x, island.top() + INSET, side, side)


def slot_edges(island: QRectF, cover: QRectF) -> tuple[float, float]:
    return cover.right() + GAP, island.right() - RIGHT_PAD


def album_anchor_offset(height: float = ISLAND_H, standard_width: float = STANDARD_WIDTH) -> float:
    """Distance from the cover center to the remainder center at the standard width.

    Frozen once. Later width changes do not recompute it.
    """
    island = QRectF(0.0, 0.0, standard_width, height)
    cover = cover_rect(island, 0.0)
    left, right = slot_edges(island, cover)
    return (left + right) / 2.0 - cover.center().x()


@dataclass(frozen=True)
class LyricPose:
    anchor: float
    text_center: float
    font_px: int
    text_width: float
    clip: QRectF
    cover: QRectF
    fade: bool

    @property
    def text_left(self) -> float:
        return self.text_center - self.text_width / 2.0

    @property
    def text_right(self) -> float:
        return self.text_center + self.text_width / 2.0


def lyric_pose(scheme: str, island: QRectF, shift: float, text: str, phase: float) -> LyricPose:
    """Live lyric anchor. `phase` only moves scheme A's marquee."""
    cover = cover_rect(island, shift)
    left, right = slot_edges(island, cover)
    clip = QRectF(left, island.top(), max(1.0, right - left), island.height())
    if scheme == "b":
        size = FONT_PX
        while size > MIN_FONT:
            width = text_width(text, size)
            text_left = island.center().x() - width / 2.0
            if text_left >= left - 0.5 and text_left + width <= right + 0.5:
                break
            size -= 1
        width = text_width(text, size)
        center = island.center().x()
        return LyricPose(center, center, size, width, clip, cover, True)
    width = text_width(text, FONT_PX)
    if scheme == "c":
        center = cover.center().x() + album_anchor_offset(island.height())
        return LyricPose(center, center, FONT_PX, width, clip, cover, True)
    # Scheme A: rest on the remainder center, then scroll around it.
    anchor = (left + right) / 2.0
    travel = max(0.0, width - clip.width())
    center = anchor - math.sin(phase) * travel / 2.0
    return LyricPose(anchor, center, FONT_PX, width, clip, cover, False)


@dataclass(frozen=True)
class MagnetPose:
    rect: QRectF
    margin: float
    locked: bool
    rule: str

    @property
    def x(self) -> float:
        return self.rect.x()

    @property
    def y(self) -> float:
        return self.rect.y()

    @property
    def w(self) -> float:
        return self.rect.width()

    @property
    def h(self) -> float:
        return self.rect.height()


def _edge_point(anchor: str, width: float, height: float, margin: float, along_x: float, along_y: float, *, lock: bool) -> tuple[float, float]:
    if anchor == "top":
        x = (SCREEN_W - width) / 2.0 if lock else along_x - width / 2.0
        return clamp(x, 0.0, SCREEN_W - width), margin
    if anchor == "bottom":
        x = (SCREEN_W - width) / 2.0 if lock else along_x - width / 2.0
        return clamp(x, margin, SCREEN_W - margin - width), SCREEN_H - margin - height
    if anchor == "left":
        y = (SCREEN_H - height) / 2.0 if lock else along_y - height / 2.0
        return margin, clamp(y, margin, SCREEN_H - margin - height)
    if anchor == "right":
        y = (SCREEN_H - height) / 2.0 if lock else along_y - height / 2.0
        return SCREEN_W - margin - width, clamp(y, margin, SCREEN_H - margin - height)
    # Corners are fully determined.
    left = anchor.endswith("left")
    top = anchor.startswith("top")
    x = margin if left else SCREEN_W - margin - width
    y = margin if top else SCREEN_H - margin - height
    return x, y


def magnet_pose(scheme: str, anchor: str, release_x: float = RELEASE_X, release_y: float = RELEASE_Y) -> MagnetPose:
    if scheme == "a":
        margin = 0.0 if anchor == "top" else 8.0
        x, y = _edge_point(anchor, CONTENT_WIDTH, MAGNET_H, margin, release_x, release_y, lock=False)
        rect = QRectF(x, y, CONTENT_WIDTH, MAGNET_H)
        return MagnetPose(rect, margin, locked=anchor not in {"top", "bottom", "left", "right"}, rule="内容宽 520，吸附不改")
    if scheme == "b":
        margin = 16.0
        x, y = _edge_point(anchor, STANDARD_WIDTH, MAGNET_H, margin, release_x, release_y, lock=True)
        rect = QRectF(x, y, STANDARD_WIDTH, MAGNET_H)
        return MagnetPose(rect, margin, locked=True, rule="固定 480")
    edge = anchor in {"top", "bottom", "left", "right"}
    width = EDGE_WIDTH if edge else CORNER_WIDTH
    margin = 8.0 if edge else 16.0
    x, y = _edge_point(anchor, width, MAGNET_H, margin, release_x, release_y, lock=True)
    rect = QRectF(x, y, width, MAGNET_H)
    rule = "贴边 610" if edge else "贴角 320"
    return MagnetPose(rect, margin, locked=True, rule=rule)


LYRIC_COPY = {
    "a": (
        "A 剩余区",
        "基准：封面右缘加 12，到岛右缘减 25，取这段的中线。",
        "跟随：岛变宽或封面移动，都用封面的当前位置重算这根中线。",
        "长句：绕这根中线来回滚动，字号不变。",
    ),
    "b": (
        "B 整岛",
        "基准：整条岛的几何中线，不是封面右侧剩余区。",
        "跟随：变宽时跟岛中心走。封面移动不带动歌词，只裁掉叠住的字。",
        "长句：字号从 16 缩到 11，仍超出就沿岛中线截断。",
    ),
    "c": (
        "C 专辑锚点",
        "基准：封面中心再往右一段固定距离，距离按标准宽 480 冻结。",
        "跟随：只跟封面走。岛变宽时歌词停在原地。",
        "长句：字号不变，超出剩余区的两端淡出截断。",
    ),
}

MAGNET_COPY = {
    "a": (
        "A 沿边留白",
        "位置：顶边贴屏幕顶；其他边和角距屏幕 8。四角两个方向都定死。",
        "沿边：顶、底、左、右留在松手的位置，不强制居中。",
        "宽度：吸附不改宽。示例是一条内容宽 520，产品单行夹在 430 到 610。",
    ),
    "b": (
        "B 固定胶囊",
        "位置：四边和四角都距屏幕 16。贴边时锁在该边的中点。",
        "沿边：不保留松手位置，松手就收到这个标准点。",
        "宽度：收到固定 480，跟歌词长短无关。",
    ),
    "c": (
        "C 边宽角窄",
        "位置：贴四边距 8，并锁在该边中点；贴四角距 16。",
        "沿边：边和角都锁死，不保留松手位置。",
        "宽度：贴边取 min(610, 屏宽×0.32)，1920 屏上是 610；贴角收到 320。",
    ),
}
