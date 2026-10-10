from __future__ import annotations

from bisect import bisect_right
from collections import OrderedDict
from dataclasses import dataclass
import math
import re
import unicodedata

from PySide6.QtCore import QPointF, QRectF, Qt, QTextBoundaryFinder
from PySide6.QtGui import QBrush, QColor, QFont, QFontInfo, QGuiApplication, QImage, QLinearGradient, QPainter, QPen, QTextCharFormat, QTextLayout

from local.music_lyric_motion import GLYPH_FLOAT_LIFT, continuous_read_lift
from local.lyric_unit_timing import timed_unit_reading_front


REFERENCE_LYRIC_PIXEL_SIZE = 38.0


def _font_pixel_size(font: QFont) -> float:
    pixel_size = float(font.pixelSize())
    if pixel_size > 0.0:
        return pixel_size
    pixel_size = float(QFontInfo(font).pixelSize())
    if pixel_size > 0.0:
        return pixel_size
    # Qt keeps pixelSize() at -1 for point-sized island fonts, including on
    # the raster path. Clamping that value to .01 suppressed almost all lift.
    point_size = float(font.pointSizeF())
    if point_size > 0.0:
        screen = QGuiApplication.primaryScreen()
        dpi = float(screen.logicalDotsPerInchY()) if screen is not None else 96.0
        return point_size * max(72.0, dpi) / 72.0
    return REFERENCE_LYRIC_PIXEL_SIZE


def utf16_offset(text: str, python_offset: int) -> int:
    return len(text[:max(0, int(python_offset))].encode("utf-16-le")) // 2


def utf16_slice(text: str, start: int, length: int) -> str:
    encoded = str(text).encode("utf-16-le")
    return encoded[max(0, start) * 2:max(0, start + length) * 2].decode("utf-16-le")


def is_cjk_cluster(text: str) -> bool:
    return any(
        "\u2e80" <= character <= "\u9fff"
        or "\uf900" <= character <= "\ufaff"
        or "\u3040" <= character <= "\u30ff"
        or "\u31f0" <= character <= "\u31ff"
        or "\u1100" <= character <= "\u11ff"
        or "\u3130" <= character <= "\u318f"
        or "\uac00" <= character <= "\ud7af"
        for character in text
    )


def reading_unit_offsets(text: str) -> tuple[int, ...]:
    """Keep Latin words and CJK graphemes intact when their baselines rise."""
    if not text:
        return (0,)
    finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, text)
    boundaries = [0]
    while True:
        offset = finder.toNextBoundary()
        if offset < 0:
            break
        boundaries.append(int(offset))
    encoded = text.encode("utf-16-le")
    utf16_length = len(encoded) // 2
    if boundaries[-1] != utf16_length:
        boundaries.append(utf16_length)
    clusters = [
        (start, end, encoded[start * 2:end * 2].decode("utf-16-le"))
        for start, end in zip(boundaries, boundaries[1:])
    ]
    units: list[list[object]] = []
    for cluster_index, (start, end, cluster) in enumerate(clusters):
        next_cluster = clusters[cluster_index + 1][2] if cluster_index + 1 < len(clusters) else ""
        is_cjk = is_cjk_cluster(cluster)
        is_word = (not is_cjk) and any(character.isalnum() for character in cluster)
        is_symbol = any(unicodedata.category(character).startswith("S") for character in cluster)
        is_connector = cluster in {"'", "’", "-", "‐", "‑"} and bool(next_cluster) and any(
            character.isalnum() for character in next_cluster
        )
        if cluster.isspace():
            if units:
                units[-1][1] = end
                units[-1][2] = "closed"
            else:
                units.append([start, end, "closed"])
        elif is_cjk:
            units.append([start, end, "cjk"])
        elif is_word or is_connector:
            if units and units[-1][2] == "word":
                units[-1][1] = end
            else:
                units.append([start, end, "word"])
        elif is_symbol:
            units.append([start, end, "symbol"])
        elif units:
            units[-1][1] = end
        else:
            units.append([start, end, "symbol"])
    output = [0]
    output.extend(int(unit[1]) for unit in units)
    if output[-1] != utf16_length:
        output[-1] = utf16_length
    return tuple(dict.fromkeys(output))


def apply_cjk_letter_spacing(
    layout: QTextLayout, text: str, font: QFont, *,
    reference_pixel_size: float = REFERENCE_LYRIC_PIXEL_SIZE,
    resolve_point_size: bool = False,
):
    pixel_size = _font_pixel_size(font) if resolve_point_size else font.pixelSize()
    spacing = max(0.75, max(1, pixel_size) / float(reference_pixel_size) * 1.35)
    ranges: list[QTextLayout.FormatRange] = []
    for match in re.finditer(
        r"[\u2e80-\u9fff\uf900-\ufaff\u3040-\u30ff\u31f0-\u31ff\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]+",
        text,
    ):
        format_range = QTextLayout.FormatRange()
        format_range.start = utf16_offset(text, match.start())
        format_range.length = utf16_offset(text, match.end()) - format_range.start
        char_format = QTextCharFormat()
        char_format.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
        char_format.setFontLetterSpacing(spacing)
        format_range.format = char_format
        ranges.append(format_range)
    if ranges:
        layout.setFormats(ranges)


def reading_front(
    progress: float, line_width: float, font: QFont, *,
    size_scale: float = 1.0, resolve_point_size: bool = False,
) -> tuple[float, float]:
    """One visual-row front, shared by all words and their moving images."""
    pixel_size = _font_pixel_size(font) if resolve_point_size else font.pixelSize()
    scale = max(0.01, float(size_scale))
    feather = min(160.0 * scale, max(pixel_size * 1.45, line_width * 0.42, 30.0 * scale))
    fraction = max(0.0, min(1.0, float(progress)))
    return fraction * (line_width + feather), feather


def read_wipe_image_at_front(
    source: QImage, *, front: float, feather: float, x_offset: float,
) -> QImage:
    """Apply one alpha gradient, avoiding vertical joins between thin strips."""
    if source.isNull():
        return source
    dpr = max(1.0, float(source.devicePixelRatio()))
    logical_width = source.width() / dpr
    feather = max(1.0 / dpr, float(feather))
    if front <= x_offset:
        empty = source.copy()
        empty.fill(Qt.GlobalColor.transparent)
        return empty
    if front >= x_offset + logical_width + feather:
        return source

    def alpha_at(x: float) -> int:
        return round(255.0 * max(0.0, min(1.0, (front - x_offset - x) / feather)))

    core = max(0.0, min(logical_width, front - x_offset - feather))
    edge = max(0.0, min(logical_width, front - x_offset))
    gradient = QLinearGradient(0.0, 0.0, logical_width, 0.0)
    gradient.setColorAt(0.0, QColor(0, 0, 0, alpha_at(0.0)))
    if 0.0 < core < logical_width:
        gradient.setColorAt(core / logical_width, QColor(0, 0, 0, 255))
    if 0.0 < edge < logical_width:
        gradient.setColorAt(edge / logical_width, QColor(0, 0, 0, 0))
    gradient.setColorAt(1.0, QColor(0, 0, 0, alpha_at(logical_width)))
    masked = source.copy()
    with QPainter(masked) as mask:
        mask.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        mask.fillRect(QRectF(0.0, 0.0, logical_width, source.height() / dpr), gradient)
    return masked


def whole_block_reading_gradient(
    bounds: QRectF, progress: float, unread_color: QColor, read_color: QColor,
) -> QLinearGradient:
    """Sample one diagonal reading front across a whole bilingual block."""
    width, height = max(1.0, bounds.width()), max(1.0, bounds.height())
    normal_x, normal_y = 0.5 / width, 0.5 / height
    normal_squared = normal_x * normal_x + normal_y * normal_y
    gradient = QLinearGradient(
        bounds.topLeft(),
        bounds.topLeft() + QPointF(normal_x / normal_squared, normal_y / normal_squared),
    )
    progress = max(0.0, min(1.0, float(progress)))
    if progress <= 0.0 or progress >= 1.0:
        color = unread_color if progress <= 0.0 else read_color
        gradient.setColorAt(0.0, color)
        gradient.setColorAt(1.0, color)
        return gradient
    feather = 0.09
    center = progress * (1.0 + 2.0 * feather) - feather

    def color_at(position: float) -> QColor:
        fraction = max(0.0, min(1.0, (center + feather - position) / (2.0 * feather)))
        fraction = fraction * fraction * (3.0 - 2.0 * fraction)
        return QColor(*(round(
            unread + (read - unread) * fraction,
        ) for unread, read in zip(unread_color.getRgb(), read_color.getRgb())))

    gradient.setColorAt(0.0, color_at(0.0))
    for boundary in (center - feather, center + feather):
        if 0.0 < boundary < 1.0:
            gradient.setColorAt(boundary, color_at(boundary))
    gradient.setColorAt(1.0, color_at(1.0))
    return gradient


@dataclass(frozen=True)
class LyricReadingRun:
    units: tuple[tuple[QImage, float], ...]
    width: float
    line_height: float
    vertical_bleed: float
    size_scale: float
    char_boundaries: tuple[float, ...] = ()
    unit_boundaries: tuple[float, ...] = ()


class LyricReadingRenderer:
    """Small-surface reading wave with native-size, device-resolution text layers."""

    CACHE_MAX_ENTRIES = 64
    CACHE_MAX_BYTES = 8 * 1024 * 1024
    TINT_CACHE_MAX_ENTRIES = 128

    def __init__(self):
        self.runs: OrderedDict[tuple[object, ...], LyricReadingRun] = OrderedDict()
        self.tints: OrderedDict[tuple[int, int], QImage] = OrderedDict()
        self.whole_lines: OrderedDict[tuple[str, str], QTextLayout] = OrderedDict()
        self.run_bytes = 0
        self.tint_bytes = 0

    def whole_line(self, text: str, font: QFont) -> QTextLayout:
        """Reuse Qt's shaped line while its shared block pen moves."""
        key = (str(text), font.toString())
        cached = self.whole_lines.get(key)
        if cached is not None:
            self.whole_lines.move_to_end(key)
            return cached
        layout = QTextLayout(text, font)
        apply_cjk_letter_spacing(layout, text, font, resolve_point_size=True)
        layout.beginLayout()
        line = layout.createLine()
        layout.endLayout()
        if line.isValid():
            self.whole_lines[key] = layout
            while len(self.whole_lines) > self.CACHE_MAX_ENTRIES:
                self.whole_lines.popitem(last=False)
        return layout

    def paint_whole_block(
        self, painter: QPainter,
        rows: tuple[tuple[QRectF, float, str, QFont, QColor, QColor], ...],
        progress: float,
    ) -> None:
        """Read all displayed original/translation lines with one static shape."""
        prepared = []
        bounds = QRectF()
        for rect, x, text, font, unread, read in rows:
            layout = self.whole_line(text, font)
            if not layout.lineCount():
                continue
            line = layout.lineAt(0)
            origin = QPointF(x, rect.center().y() - line.height() * 0.5)
            ink = line.naturalTextRect().translated(origin)
            if ink.isValid():
                bounds = bounds.united(ink) if bounds.isValid() else ink
            prepared.append((rect, line, origin, unread, read))
        if not bounds.isValid():
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        for rect, line, origin, unread, read in prepared:
            painter.save()
            painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
            painter.setPen(QPen(QBrush(whole_block_reading_gradient(bounds, progress, unread, read)), 1.0))
            line.draw(painter, origin)
            painter.restore()
        painter.restore()

    @staticmethod
    def _run_bytes(run: LyricReadingRun) -> int:
        return sum(image.sizeInBytes() for image, _left in run.units)

    def vertical_clip_margin(self, text: str, font: QFont, row_height: float, dpr: float = 1.0) -> float:
        """Keep the rising ink inside a horizontal text slot without cropping it vertically."""
        run = self.run(text, font, dpr)
        if not run.units:
            return 0.0
        image_height = max(image.height() / max(1.0, image.devicePixelRatio()) for image, _left in run.units)
        half_difference = (run.line_height - max(0.0, float(row_height))) / 2.0
        above = max(0.0, half_difference + run.vertical_bleed)
        below = max(
            0.0,
            image_height - run.vertical_bleed -
            (run.line_height + max(0.0, float(row_height))) / 2.0 +
            GLYPH_FLOAT_LIFT * run.size_scale,
        )
        return max(above, below) + 1.0 / max(1.0, float(dpr))

    def run(self, text: str, font: QFont, dpr: float = 1.0) -> LyricReadingRun:
        dpr = max(1.0, float(dpr))
        key = (str(text), font.toString(), dpr)
        cached = self.runs.get(key)
        if cached is not None:
            self.runs.move_to_end(key)
            return cached
        layout = QTextLayout(text, font)
        apply_cjk_letter_spacing(layout, text, font, resolve_point_size=True)
        layout.beginLayout()
        whole_line = layout.createLine()
        layout.endLayout()
        if not whole_line.isValid():
            return LyricReadingRun((), 0.0, 0.0, 0.0, 1.0)
        pixel_size = _font_pixel_size(font)
        size_scale = max(0.01, pixel_size / REFERENCE_LYRIC_PIXEL_SIZE)
        horizontal_bleed = max(2.0, pixel_size * 0.20)
        vertical_bleed = max(2.0, pixel_size * 0.12)
        offsets = reading_unit_offsets(text)
        units: list[tuple[QImage, float]] = []
        for start, end in zip(offsets, offsets[1:]):
            segment = utf16_slice(text, start, end - start)
            unit_layout = QTextLayout(segment, font)
            apply_cjk_letter_spacing(unit_layout, segment, font, resolve_point_size=True)
            unit_layout.beginLayout()
            line = unit_layout.createLine()
            unit_layout.endLayout()
            if not line.isValid():
                continue
            line.setPosition(QPointF(horizontal_bleed, vertical_bleed + max(0.0, whole_line.ascent() - line.ascent())))
            image = QImage(
                max(1, math.ceil((line.naturalTextWidth() + 2.0 * horizontal_bleed) * dpr)),
                max(1, math.ceil((max(whole_line.height(), line.y() - vertical_bleed + line.height()) + 2.0 * vertical_bleed) * dpr)),
                QImage.Format.Format_ARGB32_Premultiplied,
            )
            image.setDevicePixelRatio(dpr)
            image.fill(Qt.GlobalColor.transparent)
            with QPainter(image) as layer:
                layer.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
                layer.setPen(Qt.GlobalColor.white)
                unit_layout.draw(layer, QPointF())
            units.append((image, float(whole_line.cursorToX(start)[0]) - horizontal_bleed))
        char_boundaries = tuple(
            max(0.0, float(whole_line.cursorToX(utf16_offset(text, index))[0]))
            for index in range(len(text) + 1)
        )
        unit_boundaries = tuple(
            max(0.0, float(whole_line.cursorToX(offset)[0])) for offset in offsets
        )
        run = LyricReadingRun(
            tuple(units), whole_line.naturalTextWidth(), whole_line.height(),
            vertical_bleed, size_scale, char_boundaries, unit_boundaries,
        )
        run_bytes = self._run_bytes(run)
        if run_bytes <= self.CACHE_MAX_BYTES:
            self.runs[key] = run
            self.run_bytes += run_bytes
            while len(self.runs) > self.CACHE_MAX_ENTRIES or self.run_bytes > self.CACHE_MAX_BYTES:
                _old_key, old_run = self.runs.popitem(last=False)
                self.run_bytes -= self._run_bytes(old_run)
        return run

    def tinted(self, source: QImage, color: QColor) -> QImage:
        opaque = QColor(color)
        opaque.setAlpha(255)
        key = (source.cacheKey(), opaque.rgba())
        cached = self.tints.get(key)
        if cached is not None:
            self.tints.move_to_end(key)
            return cached
        tinted = QImage(source.size(), QImage.Format.Format_ARGB32_Premultiplied)
        tinted.setDevicePixelRatio(source.devicePixelRatio())
        tinted.fill(opaque)
        with QPainter(tinted) as layer:
            layer.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            layer.drawImage(0, 0, source)
        image_bytes = tinted.sizeInBytes()
        if image_bytes <= self.CACHE_MAX_BYTES:
            self.tints[key] = tinted
            self.tint_bytes += image_bytes
            while len(self.tints) > self.TINT_CACHE_MAX_ENTRIES or self.tint_bytes > self.CACHE_MAX_BYTES:
                _old_key, old_image = self.tints.popitem(last=False)
                self.tint_bytes -= old_image.sizeInBytes()
        return tinted

    def paint(
        self, painter: QPainter, rect: QRectF, x: float, text: str, font: QFont,
        progress: float, unread_color: QColor, read_color: QColor,
        *, timed_units: tuple[object, ...] = (), timed_position: float | None = None,
        timed_line_end: float | None = None,
        timed_character_front: float | None = None,
        whole_row: bool = False,
    ):
        device = painter.device()
        dpr = device.devicePixelRatioF() if device is not None else 1.0
        run = self.run(text, font, dpr)
        if not run.units:
            return
        if whole_row:
            # A row-only source has no intra-row evidence in low-perception
            # mode. Reuse the shaped images at their resting baseline; setting
            # progress to one would leave every word at its lifted landing.
            top = rect.center().y() - run.line_height / 2.0 - run.vertical_bleed
            top += GLYPH_FLOAT_LIFT * run.size_scale
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.setOpacity(painter.opacity() * read_color.alphaF())
            for source, left in run.units:
                self._draw_subpixel_image(painter, QPointF(x + left, top), self.tinted(source, read_color))
            painter.restore()
            return
        progress = max(0.0, min(1.0, float(progress)))
        front, feather = reading_front(progress, run.width, font, size_scale=run.size_scale, resolve_point_size=True)
        hard_front = None
        if timed_character_front is not None and len(run.char_boundaries) == len(text) + 1:
            char_front = max(0.0, min(float(len(text)), float(timed_character_front)))
            left_index = min(len(text) - 1, int(char_front)) if text else 0
            fraction = char_front - left_index
            left = run.char_boundaries[left_index]
            right = run.char_boundaries[left_index + 1]
            if right >= left:
                hard_front = left + (right - left) * fraction
        elif timed_units and timed_position is not None:
            hard_front = timed_unit_reading_front(
                text, timed_units, timed_position, run.char_boundaries,
                line_end=timed_line_end,
            )
        if hard_front is not None:
            hard_front = max(0.0, min(run.width, float(hard_front)))
            front = 0.0 if hard_front <= 0.0 else hard_front + feather
            progress = hard_front / max(1.0, run.width)
        top = rect.center().y() - run.line_height / 2.0 - run.vertical_bleed
        base_opacity = painter.opacity()
        count = len(run.units)
        placed_units: list[tuple[QImage, float, QPointF]] = []
        lift_progress = progress
        if hard_front is not None and len(run.unit_boundaries) == count + 1:
            # Real timestamps move the hard x front at each spoken unit's own
            # pace. Convert that measured x to a fractional word/grapheme
            # ordinal before sampling the overlapping vertical wave; a long
            # first word must not rush the following short words upward.
            boundaries = run.unit_boundaries
            if hard_front <= boundaries[0]:
                lift_progress = 0.0
            elif hard_front >= boundaries[-1]:
                lift_progress = 1.0
            else:
                unit_index = max(0, min(count - 1, bisect_right(boundaries, hard_front) - 1))
                left, right = boundaries[unit_index:unit_index + 2]
                lift_progress = (
                    unit_index + max(0.0, min(1.0, (hard_front - left) / max(1e-9, right - left)))
                ) / count
        for index, (source, left) in enumerate(run.units):
            lift = GLYPH_FLOAT_LIFT * continuous_read_lift(lift_progress, index, count)
            position = QPointF(x + left, top + (GLYPH_FLOAT_LIFT - lift) * run.size_scale)
            placed_units.append((source, left, position))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        # Isolated words/graphemes have a little horizontal ink bleed. Draw
        # every unread layer first: otherwise the next unit's unread edge can
        # cover the preceding unit's already-white trailing stroke.
        for source, left, position in placed_units:
            unit_right = left + source.width() / source.devicePixelRatio()
            if front < unit_right + feather:
                painter.setOpacity(base_opacity * unread_color.alphaF())
                self._draw_subpixel_image(painter, position, self.tinted(source, unread_color))
        for source, left, position in placed_units:
            if front > left:
                painter.setOpacity(base_opacity * read_color.alphaF())
                self._draw_subpixel_image(painter, position, read_wipe_image_at_front(
                    self.tinted(source, read_color), front=front, feather=feather, x_offset=left,
                ))
        painter.restore()

    @staticmethod
    def _draw_subpixel_image(painter: QPainter, origin: QPointF, image: QImage) -> None:
        """Sample small baseline lifts between whole device-pixel positions."""
        dpr = max(1.0, float(image.devicePixelRatio()))
        painter.drawImage(QRectF(
            origin.x(), origin.y(), image.width() / dpr,
            image.height() / dpr + 1.0 / 64.0,
        ), image)
