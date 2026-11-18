"""The bracketed aside's exit: it breaks into fine grains that become its blur.

When the next sentence is halfway through, a finished aside comes apart from
left to right into fine grains. Each grain lifts a little and settles back
while the blurred aside condenses under it, column by column, so the crisp
words turn into their blur instead of vanishing. The grains drift right as
they go, so the blur they settle into rests one character to the right of
where the crisp aside stood: the grains arrive there while still visible,
then hold and fade into the blur. Only the window and fullscreen paint it; the island
has no asides.
"""
from __future__ import annotations

import math
import random
import re
import zlib
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QTextLayout

DISSOLVE_S = 1.4          # from the first grain to the settled blur
SWEEP = 0.45              # share of the exit the left-to-right front takes to cross
FLIGHT = 0.55             # share of the exit one column takes from crisp to blur
MAX_PARTICLES = 3500
ALPHA_STEPS = 8           # grains are drawn in this many opacity batches
PAD = 2.0


def dissolve_phase(window_end: float | None, position: float) -> float | None:
    """0..1 through the exit, or None outside it."""
    if window_end is None:
        return None
    elapsed = position - window_end
    if elapsed < 0.0 or elapsed >= DISSOLVE_S:
        return None
    return elapsed / DISSOLVE_S


_WORD = re.compile(r"[^\W_]+(?:['\u2019-][^\W_]+)*")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uf900-\ufaff]")


ARRIVE = 0.45             # share of a column's change by which its grains reach the landing


def word_shift(text: str, font: QFont) -> float:
    """How far right the settled blur rests: the aside's first character
    (one letter of its first word, or one Chinese, Japanese or Korean
    character)."""
    metrics = QFontMetricsF(font)
    for match in _WORD.finditer(text or ""):
        word = match.group(0)
        cjk = _CJK.search(word)
        return metrics.horizontalAdvance(cjk.group(0) if cjk is not None else word[0])
    return 0.0


def travel(local: float) -> float:
    """0..1 of the drift for a column ``local`` through its change. Done by
    ``ARRIVE``, while the grains are still bright and before the blur
    condenses, so the blur forms exactly where they stopped."""
    return _smooth(local / ARRIVE)


def _smooth(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def column_local(fx: float, phase: float) -> float:
    """0..1 through one column's change; 0 while it is still crisp."""
    return max(0.0, min(1.0, (phase - fx * SWEEP) / FLIGHT))


def blur_settle(phase: float, fx: float = 1.0) -> float:
    """Opacity of the blurred aside at column ``fx`` (the last column by default)."""
    return _smooth((column_local(fx, phase) - 0.25) / 0.6)


def particle_pose(fx: float, r1: float, r2: float, phase: float, scale: float = 1.0, shift: float = 0.0):
    """(dx, dy, size factor, alpha) of one grain ``fx`` across the aside.

    A grain lifts a few pixels and drifts ``shift`` to the right, then
    settles while it fades into the blur below it.
    """
    local = column_local(fx, phase)
    if local <= 0.0:
        return 0.0, 0.0, 1.0, 1.0
    lift = math.sin(math.pi * local)
    dx = shift * travel(local) + ((r1 - 0.5) * 6.0 + 2.0) * scale * lift
    dy = -(2.0 + 6.0 * r2) * scale * lift
    return dx, dy, 1.0 - 0.25 * lift, 1.0 - _smooth((local - 0.35) / 0.65)


@dataclass(frozen=True)
class AsideSprite:
    image: QImage
    origin_shift: float                     # the sprite's top-left is origin - PAD
    cells: tuple                            # (x, y, side, coverage, fx, r1, r2) in logical px
    color: QColor
    span: float
    scale: float
    arrays: dict


def build_sprite(layout: QTextLayout, text: str, font: QFont, width: float, height: float,
                 color: QColor, line_offsets, ratio: float) -> AsideSprite:
    """The aside's crisp glyphs, and the fine grains that carry their ink."""
    image = QImage(max(1, math.ceil((width + 2 * PAD) * ratio)), max(1, math.ceil((height + 2 * PAD) * ratio)),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(ratio)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setPen(QColor(color))
    for index in range(layout.lineCount()):
        offset = line_offsets[index] if index < len(line_offsets) else 0.0
        layout.lineAt(index).draw(painter, QPointF(PAD, PAD + offset))
    painter.end()
    pixel = font.pixelSize() if font.pixelSize() > 0 else font.pointSizeF() * 1.33
    scale = max(0.6, pixel / 24.0)
    cell = max(1.0 / ratio, pixel / 16.0)
    alpha = _alpha(image)
    span = width + 2 * PAD
    rng = random.Random(zlib.crc32(text.encode("utf-8")))
    for _ in range(5):
        cells = _ink_cells(alpha, ratio, cell, span, rng)
        if len(cells) <= MAX_PARTICLES:
            break
        cell *= 1.3
    names = ("x", "y", "side", "coverage", "fx", "r1", "r2")
    arrays = {name: np.array([cell[i] for cell in cells], dtype=float) for i, name in enumerate(names)}
    return AsideSprite(image, PAD, tuple(cells), QColor(color), span, scale, arrays)


def _alpha(image: QImage) -> np.ndarray:
    view = image.constBits()
    data = np.frombuffer(view, dtype=np.uint8, count=image.sizeInBytes())
    rows = data.reshape(image.height(), image.bytesPerLine())[:, : image.width() * 4]
    return rows.reshape(image.height(), image.width(), 4)[:, :, 3]


def _ink_cells(alpha: np.ndarray, ratio: float, cell: float, span: float, rng: random.Random):
    step = max(1, round(cell * ratio))
    height, width = alpha.shape
    rows, cols = height // step, width // step
    if rows == 0 or cols == 0:
        return []
    blocks = alpha[: rows * step, : cols * step].reshape(rows, step, cols, step).mean(axis=(1, 3)) / 255.0
    side = step / ratio
    cells = []
    for row, col in zip(*np.nonzero(blocks > 0.08)):
        x, y = col * side, row * side
        cells.append((x, y, side, float(blocks[row, col]), (x + 0.5 * side) / max(1.0, span),
                      rng.random(), rng.random()))
    return cells


def paint_particles(painter: QPainter, sprite: AsideSprite, origin: QPointF, phase: float,
                    shift: float = 0.0) -> None:
    """The crisp part right of the front, and the grains left of it, drifting
    ``shift`` to the right."""
    left, top = origin.x() - sprite.origin_shift, origin.y() - sprite.origin_shift
    front = left + max(0.0, phase / SWEEP) * sprite.span
    painter.save()
    # Columns the front has not reached are still the crisp aside.
    if front < left + sprite.span:
        painter.save()
        painter.setClipRect(QRectF(front, top - 4.0, left + sprite.span - front + 4.0,
                                   sprite.image.height() / sprite.image.devicePixelRatio() + 8.0),
                            Qt.ClipOperation.IntersectClip)
        painter.drawImage(QPointF(left, top), sprite.image)
        painter.restore()
    cells = sprite.arrays
    started = cells["fx"] * SWEEP < phase
    if started.any():
        fx = cells["fx"][started]
        local = np.clip((phase - fx * SWEEP) / FLIGHT, 0.0, 1.0)
        lift = np.sin(np.pi * local)
        fade = np.clip((local - 0.35) / 0.65, 0.0, 1.0)
        alpha = (1.0 - fade * fade * (3.0 - 2.0 * fade)) * cells["coverage"][started]
        side = cells["side"][started]
        size = side * (1.0 - 0.25 * lift)
        ease = np.clip(local / ARRIVE, 0.0, 1.0)
        drift = shift * ease * ease * (3.0 - 2.0 * ease)
        x = (left + cells["x"][started] + (side - size) * 0.5 + drift
             + ((cells["r1"][started] - 0.5) * 6.0 + 2.0) * sprite.scale * lift)
        y = top + cells["y"][started] + (side - size) * 0.5 - (2.0 + 6.0 * cells["r2"][started]) * sprite.scale * lift
        level = np.minimum(ALPHA_STEPS - 1, (alpha * ALPHA_STEPS).astype(int))
        keep = alpha > 0.03
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(sprite.color)
        base = painter.opacity()
        for index in range(ALPHA_STEPS):
            chosen = keep & (level == index)
            if not chosen.any():
                continue
            painter.setOpacity(base * (index + 0.5) / ALPHA_STEPS)
            painter.drawRects([QRectF(*r) for r in zip(x[chosen].tolist(), y[chosen].tolist(),
                                                         size[chosen].tolist(), size[chosen].tolist())])
    painter.restore()


def paint_blur_columns(painter: QPainter, sprite: AsideSprite, origin: QPointF, phase: float,
                       ratio: float, margin: float, draw_blur, shift: float = 0.0) -> None:
    """The blurred aside, ``shift`` right of the crisp one, condensing column
    by column behind the grains.

    ``draw_blur(layer)`` paints the blurred aside at ``origin`` in the
    layer's (logical) coordinates; the layer moves it ``shift`` right.
    """
    if blur_settle(phase, 0.0) <= 0.001:
        return
    left = origin.x() + shift - sprite.origin_shift - margin
    top = origin.y() - sprite.origin_shift - margin
    width = sprite.span + 2 * margin
    height = sprite.image.height() / sprite.image.devicePixelRatio() + 2 * margin
    layer = QImage(max(1, math.ceil(width * ratio)), max(1, math.ceil(height * ratio)),
                   QImage.Format.Format_ARGB32_Premultiplied)
    layer.setDevicePixelRatio(ratio)
    layer.fill(Qt.GlobalColor.transparent)
    lp = QPainter(layer)
    lp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    lp.translate(-left + shift, -top)
    draw_blur(lp)
    lp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    mask = QLinearGradient(origin.x() - sprite.origin_shift, 0.0, origin.x() - sprite.origin_shift + sprite.span, 0.0)
    for step in range(13):
        fx = step / 12.0
        mask.setColorAt(fx, QColor(0, 0, 0, round(255 * blur_settle(phase, fx))))
    lp.fillRect(QRectF(left - shift, top, width, height), mask)
    lp.end()
    painter.drawImage(QPointF(left, top), layer)
