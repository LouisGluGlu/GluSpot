"""Draw one lyric frame. Product code is only asked for cover colors."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import sys

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QSize
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter

from timeline import (
    LINES,
    AppleKnobs,
    Line,
    Unit,
    coverage_for_spans,
    describe,
    displayed_focus,
)


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

COVER_DIR = ROOT / "_demos" / "narrow3" / "covers"
COVERS = (
    COVER_DIR / "sunset_city.jpg",
    COVER_DIR / "night_sea.jpg",
    COVER_DIR / "pink_forest.jpg",
    COVER_DIR / "clip.jpg",
)

MAIN_PX = 40
# Product classic translation is (38 - 6) * 0.5 against a 38px main line.
CURRENT_TRANSLATION_RATIO = 16.0 / 38.0
APPLE_TRANSLATION_RATIO = 0.50
# Ordinary product reading lift at the 38px reference (GLYPH_FLOAT_LIFT).
CURRENT_LIFT_PX = 5.2 * MAIN_PX / 38.0
# Default product gradient is about two CJK advances at 30 steps.
CURRENT_FEATHER_CHARS = 2.0
CURRENT_FUTURE_ALPHA = 242.0 / 255.0


@dataclass
class Scene:
    style: str = "apple"
    theme: str = "dark"
    t: float = 3.35
    knobs: AppleKnobs | None = None
    cover: Path = COVERS[0]

    def resolved_knobs(self) -> AppleKnobs:
        return self.knobs or AppleKnobs()


_BACKDROP_CACHE: dict[tuple, QImage] = {}
_BLOCK_CACHE: dict[tuple, QImage] = {}


def font_families() -> list[str]:
    return ["Microsoft YaHei UI", "Segoe UI", "WenQuanYi Micro Hei", "Droid Sans Fallback"]


def make_font(pixel_size: float, weight: QFont.Weight) -> QFont:
    font = QFont()
    font.setFamilies(font_families())
    font.setPixelSize(max(1, int(round(pixel_size))))
    font.setWeight(weight)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def render_scene(width: int, height: int, scene: Scene) -> QImage:
    width = max(1, int(width))
    height = max(1, int(height))
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    with QPainter(image) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        _paint_background(painter, width, height, scene)
        _paint_lyrics(painter, width, height, scene)
    return image


def _paint_background(painter: QPainter, width: int, height: int, scene: Scene) -> None:
    if scene.theme == "light":
        painter.fillRect(0, 0, width, height, QColor(255, 255, 255))
        return
    painter.drawImage(QRectF(0, 0, width, height), _backdrop(scene.cover, width, height))


def _backdrop(cover_path: Path, width: int, height: int) -> QImage:
    key = (str(cover_path), width, height)
    cached = _BACKDROP_CACHE.get(key)
    if cached is not None:
        return cached
    cover = QImage(str(cover_path))
    raster = _flow_backdrop(cover, max(320, min(width, 640)), max(240, min(height, 480)))
    if raster.isNull():
        raster = QImage(8, 8, QImage.Format.Format_RGB32)
        raster.fill(QColor(28, 24, 22))
    scaled = raster.scaled(
        width, height,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    _BACKDROP_CACHE[key] = scaled
    return scaled


def _flow_backdrop(cover: QImage, width: int, height: int) -> QImage:
    """Product palette + static flow field. Failure falls back to a flat dark."""
    try:
        from local.backdrop_variants import paint_backdrop_variant
        from local.music_surface_palette import artwork_surface_palette

        palette = artwork_surface_palette(cover, QColor(44, 44, 44), QColor(14, 14, 14))
        colors = tuple(palette.ambient_colors) or (
            palette.primary, palette.secondary, palette.characteristic,
        )
        weights = tuple(palette.ambient_weights)
        if len(weights) != len(colors):
            weights = ()
        return paint_backdrop_variant(QSize(width, height), colors, weights, "flow")
    except Exception:
        return QImage()


def _ink(theme: str) -> tuple[int, int, int]:
    return (22, 22, 24) if theme == "light" else (255, 255, 255)


def _smooth(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


def _paint_lyrics(painter: QPainter, width: int, height: int, scene: Scene) -> None:
    knobs = scene.resolved_knobs()
    apple = scene.style == "apple"
    focus = (
        displayed_focus(scene.t, knobs.lead_seconds(), knobs.scroll_seconds())
        if apple else displayed_focus(scene.t, 0.0, 0.42)
    )
    main_font = make_font(MAIN_PX, QFont.Weight.Bold)
    trans_font = make_font(
        MAIN_PX * (APPLE_TRANSLATION_RATIO if apple else CURRENT_TRANSLATION_RATIO),
        QFont.Weight.Normal,
    )
    main_height = QFontMetricsF(main_font).height()
    trans_height = QFontMetricsF(trans_font).height()
    trans_gap = knobs.translation_gap_ratio * MAIN_PX if apple else max(4.0, 0.12 * MAIN_PX)
    block_gap = knobs.block_gap_ratio * MAIN_PX if apple else max(18.0, height * 0.035)
    lift_reserve = 0.0 if apple else CURRENT_LIFT_PX
    slot = main_height + lift_reserve + trans_gap + trans_height
    pitch = slot + block_gap
    anchor = 52 + (height - 88) * 0.40
    index = max(0, min(len(LINES) - 1, int(math.floor(focus))))
    frac = focus - math.floor(focus)
    nxt = min(len(LINES) - 1, index + 1)

    def scale_at(distance: float) -> float:
        if not apple:
            return 1.0
        amount = abs(distance)
        if amount >= 1.0:
            return knobs.other_scale
        return 1.0 + (knobs.other_scale - 1.0) * _smooth(amount)

    def primary_center(line_index: int, scale: float) -> float:
        return line_index * pitch + lift_reserve * scale + main_height * scale * 0.5

    blended = (
        primary_center(index, scale_at(focus - index)) * (1.0 - frac)
        + primary_center(nxt, scale_at(focus - nxt)) * frac
    )
    camera = anchor - blended
    for line_index, line in enumerate(LINES):
        distance = line_index - focus
        scale = scale_at(distance)
        top = line_index * pitch + camera
        if top > height + 20 or top + slot < -20:
            continue
        block = _cached_block(scene, knobs, line, distance, apple, main_font, trans_font, trans_gap, lift_reserve)
        if block.isNull():
            continue
        drawn = block
        if abs(scale - 1.0) > 0.008:
            drawn = block.scaled(
                max(1, round(block.width() * scale)),
                max(1, round(block.height() * scale)),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        blur = _blur_radius(distance, knobs, apple)
        if blur > 0.35:
            drawn = _blur(drawn, blur)
        painter.drawImage(QPointF(52, top), drawn)
    _paint_caption(painter, width, height, scene, knobs)


def _blur_radius(distance: float, knobs: AppleKnobs, apple: bool) -> float:
    amount = abs(distance)
    if apple:
        if amount < 0.02:
            return 0.0
        if amount < 1.0:
            return knobs.blur_px * amount
        return knobs.blur_px * (1.0 + 0.85 * min(2.5, amount - 1.0))
    if amount <= 1.0:
        return 3.0 * amount
    if amount <= 2.0:
        return 3.0
    if amount <= 3.0:
        return 3.0 + 3.0 * (amount - 2.0)
    return 6.0


def _alphas(scene: Scene, knobs: AppleKnobs, line: Line, distance: float, apple: bool):
    amount = abs(distance)
    if not apple:
        return 1.0, CURRENT_FUTURE_ALPHA, 1.0
    if amount >= 3.0:
        level = knobs.far_alpha()
        read_alpha = unread_alpha = level
    elif amount >= 1.0:
        blend = _smooth((amount - 1.0) / 2.0)
        level = knobs.adjacent_alpha() + (knobs.far_alpha() - knobs.adjacent_alpha()) * blend
        read_alpha = unread_alpha = level
    else:
        # Fade the whole line toward the neighbor tone, but keep a read/unread
        # gap until it arrives there so a departing line can finish its wipe.
        blend = _smooth(amount)
        read_alpha = 1.0 + (knobs.adjacent_alpha() - 1.0) * blend
        gap = (1.0 - knobs.unread_alpha) * (1.0 - blend) ** 0.45
        unread_alpha = max(0.0, read_alpha - gap)
    del line
    return read_alpha, unread_alpha, knobs.translation_alpha() / max(0.05, knobs.unread_alpha)


def _cached_block(
    scene: Scene, knobs: AppleKnobs, line: Line, distance: float, apple: bool,
    main_font: QFont, trans_font: QFont, trans_gap: float, lift_reserve: float,
) -> QImage:
    read_alpha, unread_alpha, trans_factor = _alphas(scene, knobs, line, distance, apple)
    # Screen edge fade depends on the final top, so it is applied by scaling alpha
    # before the cache key. The caller does not know top until after layout, and
    # layout does not depend on alpha. Apply edge fade here using a second pass
    # only when apple — approximated by distance so far lines disappear.
    if apple and abs(distance) > 2.6:
        fade = max(0.0, 1.0 - (abs(distance) - 2.6) / 1.4)
        read_alpha *= fade
        unread_alpha *= fade
    trans_alpha = unread_alpha * trans_factor if apple else unread_alpha
    feather = knobs.feather_chars if apple else CURRENT_FEATHER_CHARS
    moving = any(unit.start - 0.02 <= scene.t <= unit.end + 0.02 for unit in line.spans())
    if not apple and line.start <= scene.t <= line.end:
        moving = True
    time_key = round(scene.t, 2) if moving else ("after" if scene.t >= line.end else "before")
    key = (
        scene.style, scene.theme, line.primary, time_key,
        round(read_alpha, 3), round(unread_alpha, 3), round(trans_alpha, 3),
        round(feather, 3), round(trans_gap, 2), round(lift_reserve, 2),
    )
    cached = _BLOCK_CACHE.get(key)
    if cached is not None:
        _BLOCK_CACHE[key] = _BLOCK_CACHE.pop(key)
        return cached
    image = _build_block(
        line, scene.t, scene.theme, main_font, trans_font, trans_gap, lift_reserve,
        read_alpha, unread_alpha, trans_alpha, feather, karaoke=not apple,
    )
    _BLOCK_CACHE[key] = image
    while len(_BLOCK_CACHE) > 96:
        _BLOCK_CACHE.pop(next(iter(_BLOCK_CACHE)))
    return image


def _build_block(
    line: Line, t: float, theme: str, main_font: QFont, trans_font: QFont,
    trans_gap: float, lift_reserve: float, read_alpha: float, unread_alpha: float,
    trans_alpha: float, feather_chars: float, karaoke: bool,
) -> QImage:
    ink = _ink(theme)
    primary = _raster_line(
        line.spans(), main_font, t, ink, read_alpha, unread_alpha,
        feather_chars, lift_reserve,
    )
    if karaoke:
        translation_spans = (Unit(line.translation, line.start, line.end),)
        translation = _raster_line(
            translation_spans, trans_font, t, ink, read_alpha, trans_alpha,
            feather_chars, 0.0,
        )
    else:
        translation = _raster_line(
            (Unit(line.translation, line.start, line.end),), trans_font, line.end,
            ink, trans_alpha, trans_alpha, 0.0, 0.0,
        )
    width = max(primary.width(), translation.width(), 1)
    height = primary.height() + int(round(trans_gap)) + translation.height()
    block = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    block.fill(Qt.GlobalColor.transparent)
    with QPainter(block) as painter:
        painter.drawImage(0, 0, primary)
        painter.drawImage(0, primary.height() + int(round(trans_gap)), translation)
    return block


def _raster_line(
    spans: tuple[Unit, ...], font: QFont, t: float, ink: tuple[int, int, int],
    read_alpha: float, unread_alpha: float, feather_chars: float, lift_reserve: float,
) -> QImage:
    metrics = QFontMetricsF(font)
    advances: list[tuple[float, float]] = []
    cursor = 0.0
    for unit in spans:
        width = max(0.0, metrics.horizontalAdvance(unit.text))
        advances.append((cursor, cursor + width))
        cursor += width
    if cursor <= 0.0 or not spans:
        image = QImage(1, max(1, int(math.ceil(metrics.height()))), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        return image
    average = cursor / max(1, len(spans))
    feather = max(0.0, feather_chars) * average
    coverage = coverage_for_spans(spans, advances, t, feather)
    width = int(math.ceil(cursor)) + 2
    height = int(math.ceil(metrics.height() + lift_reserve)) + 2
    glyphs = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    glyphs.fill(Qt.GlobalColor.transparent)
    with QPainter(glyphs) as painter:
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255))
        baseline = 1 + lift_reserve + metrics.ascent()
        for unit, (x0, x1) in zip(spans, advances):
            left = max(0, int(math.floor(x0)))
            right = min(len(coverage), int(math.ceil(x1)))
            samples = coverage[left:right] or [0.0]
            risen = lift_reserve * (sum(samples) / len(samples))
            painter.drawText(QPointF(1 + x0, baseline - risen), unit.text)
    return _apply_coverage(glyphs, coverage, ink, read_alpha, unread_alpha)


def _apply_coverage(
    glyphs: QImage, coverage: list[float], ink: tuple[int, int, int],
    read_alpha: float, unread_alpha: float,
) -> QImage:
    image = glyphs.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = image.width(), image.height()
    stride = image.bytesPerLine()
    raw = np.frombuffer(image.bits(), dtype=np.uint8, count=stride * height)
    pixels = raw.reshape(height, stride)[:, : width * 4].reshape(height, width, 4).copy()
    glyph_alpha = pixels[:, :, 3].astype(np.float32) / 255.0
    column = np.zeros(width, dtype=np.float32)
    for index, amount in enumerate(coverage):
        pixel = 1 + index
        if 0 <= pixel < width:
            column[pixel] = amount
    mix = unread_alpha + (read_alpha - unread_alpha) * column[None, :]
    pixels[:, :, 0] = ink[0]
    pixels[:, :, 1] = ink[1]
    pixels[:, :, 2] = ink[2]
    pixels[:, :, 3] = np.clip(np.rint(glyph_alpha * mix * 255.0), 0, 255).astype(np.uint8)
    packed = np.ascontiguousarray(pixels)
    return QImage(packed.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()


def _blur(image: QImage, radius: float) -> QImage:
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = converted.width(), converted.height()
    stride = converted.bytesPerLine()
    raw = np.frombuffer(converted.bits(), dtype=np.uint8, count=stride * height)
    pixels = raw.reshape(height, stride)[:, : width * 4].reshape(height, width, 4).astype(np.float32)
    alpha = pixels[:, :, 3:4] / 255.0
    rgb = pixels[:, :, :3] * alpha
    sigma = max(0.6, float(radius) * 0.55)
    reach = max(1, int(math.ceil(sigma * 2.0)))
    offsets = np.arange(-reach, reach + 1, dtype=np.float32)
    kernel = np.exp(-0.5 * (offsets / sigma) ** 2)
    kernel /= kernel.sum()
    rgb = _separable(rgb, kernel)
    alpha = _separable(alpha, kernel)
    safe = np.maximum(alpha, 1e-4)
    out = np.empty_like(pixels)
    out[:, :, :3] = np.clip(rgb / safe, 0.0, 255.0)
    out[:, :, 3] = np.clip(alpha[:, :, 0] * 255.0, 0.0, 255.0)
    packed = np.ascontiguousarray(out.astype(np.uint8))
    return QImage(packed.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()


def _separable(channels: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    flat = channels.reshape(channels.shape[0], channels.shape[1], -1)
    horizontal = np.empty_like(flat)
    for channel in range(flat.shape[2]):
        for row in range(flat.shape[0]):
            horizontal[row, :, channel] = np.convolve(flat[row, :, channel], kernel, mode="same")
    vertical = np.empty_like(horizontal)
    for channel in range(horizontal.shape[2]):
        for column in range(horizontal.shape[1]):
            vertical[:, column, channel] = np.convolve(horizontal[:, column, channel], kernel, mode="same")
    return vertical.reshape(channels.shape)


def _paint_caption(painter: QPainter, width: int, height: int, scene: Scene, knobs: AppleKnobs) -> None:
    title = "仿 Apple" if scene.style == "apple" else "现在的歌词"
    theme = "白底" if scene.theme == "light" else "深色"
    dark = scene.theme == "light"
    painter.setPen(QColor(22, 22, 24) if dark else QColor(255, 255, 255, 230))
    painter.setFont(make_font(18, QFont.Weight.Bold))
    painter.drawText(
        QRectF(52, 16, width - 80, 26),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        f"{title} · {theme}",
    )
    painter.setFont(make_font(13, QFont.Weight.Normal))
    painter.setPen(QColor(40, 40, 44, 220) if dark else QColor(255, 255, 255, 180))
    painter.drawText(
        QRectF(52, height - 34, width - 70, 22),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        describe(scene.t, scene.style, knobs),
    )
