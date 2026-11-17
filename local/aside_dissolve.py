"""The bracketed aside's exit: it breaks into particles, left to right.

When the next sentence is halfway through, a finished aside used to blur in
place over 0.18 s. Now its glyphs come apart from left to right into small
particles that float up and fade, and the blurred aside settles in behind
them. Only the window and fullscreen paint it; the island has no asides.
"""
from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QTextLayout

DISSOLVE_S = 1.1          # the whole exit, from the first particle to the settled blur
SWEEP = 0.50              # share of the exit the left-to-right front takes to cross
FLIGHT = 0.42             # share of the exit one particle floats before it is gone
BLUR_FROM = 0.45          # the blurred aside starts to settle in here
MAX_PARTICLES = 900
PAD = 2.0


def dissolve_phase(window_end: float | None, position: float) -> float | None:
    """0..1 through the exit, or None outside it."""
    if window_end is None:
        return None
    elapsed = position - window_end
    if elapsed < 0.0 or elapsed >= DISSOLVE_S:
        return None
    return elapsed / DISSOLVE_S


def blur_settle(phase: float) -> float:
    """Opacity of the blurred aside behind the particles."""
    x = max(0.0, min(1.0, (phase - BLUR_FROM) / (1.0 - BLUR_FROM)))
    return x * x * (3.0 - 2.0 * x)


def particle_pose(fx: float, r1: float, r2: float, r3: float, phase: float, scale: float = 1.0):
    """(dx, dy, size factor, alpha) of one particle ``fx`` across the aside."""
    start = fx * SWEEP + r3 * 0.08
    local = max(0.0, min(1.0, (phase - start) / FLIGHT))
    if local <= 0.0:
        return 0.0, 0.0, 1.0, 1.0
    eased = 1.0 - (1.0 - local) ** 2
    dx = (4.0 + 14.0 * r1) * eased * scale
    dy = -(8.0 + 22.0 * r2) * eased * scale + math.sin(local * math.pi * 2.0 + r1 * 6.0) * 1.5 * scale * local
    return dx, dy, 1.0 - 0.45 * local, (1.0 - local) ** 1.6


@dataclass(frozen=True)
class AsideSprite:
    image: QImage
    origin_shift: float                     # the sprite's top-left is origin - PAD
    cells: tuple                            # (source rect in logical px, fx, r1, r2, r3)
    scale: float


def build_sprite(layout: QTextLayout, text: str, font: QFont, width: float, height: float,
                 color: QColor, line_offsets, ratio: float) -> AsideSprite:
    """The aside's crisp glyphs, and the cells that carry ink."""
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
    cell = max(2.5, pixel / 7.0)
    alpha = _alpha(image)
    rng = random.Random(zlib.crc32(text.encode("utf-8")))
    for _ in range(4):
        cells = _ink_cells(alpha, ratio, cell, width + 2 * PAD, rng)
        if len(cells) <= MAX_PARTICLES:
            break
        cell *= 1.35
    return AsideSprite(image, PAD, tuple(cells), scale)


def _alpha(image: QImage) -> np.ndarray:
    view = image.constBits()
    data = np.frombuffer(view, dtype=np.uint8, count=image.sizeInBytes())
    rows = data.reshape(image.height(), image.bytesPerLine())[:, : image.width() * 4]
    return rows.reshape(image.height(), image.width(), 4)[:, :, 3]


def _ink_cells(alpha: np.ndarray, ratio: float, cell: float, span: float, rng: random.Random):
    step = max(1, round(cell * ratio))
    height, width = alpha.shape
    cells = []
    for top in range(0, height, step):
        for left in range(0, width, step):
            if alpha[top:top + step, left:left + step].max(initial=0) < 24:
                continue
            x, y = left / ratio, top / ratio
            cells.append((QRectF(x, y, step / ratio, step / ratio), (x + 0.5 * step / ratio) / max(1.0, span),
                          rng.random(), rng.random(), rng.random()))
    return cells


def paint_particles(painter: QPainter, sprite: AsideSprite, origin: QPointF, phase: float) -> None:
    """Draw the aside as particles; untouched cells stay in place."""
    base = painter.opacity()
    left, top = origin.x() - sprite.origin_shift, origin.y() - sprite.origin_shift
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    for source, fx, r1, r2, r3 in sprite.cells:
        dx, dy, size, alpha = particle_pose(fx, r1, r2, r3, phase, sprite.scale)
        if alpha <= 0.01:
            continue
        w, h = source.width() * size, source.height() * size
        cx = left + source.center().x() + dx
        cy = top + source.center().y() + dy
        painter.setOpacity(base * alpha)
        painter.drawImage(QRectF(cx - w / 2.0, cy - h / 2.0, w, h), sprite.image,
                          QRectF(source.x() * sprite.image.devicePixelRatio(),
                                 source.y() * sprite.image.devicePixelRatio(),
                                 source.width() * sprite.image.devicePixelRatio(),
                                 source.height() * sprite.image.devicePixelRatio()))
    painter.restore()
