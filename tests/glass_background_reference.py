"""Frozen pre-optimization background compositor for regression tests."""
import math
import numpy as np
from PIL import Image
from PIL.ImageFilter import GaussianBlur
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from local.music_surface_background import relative_luminance

def reference_field_pixels(cls, size: QSize, colors: tuple[QColor, ...],
                        strengths: tuple[float, ...], geometry_seconds: float) -> QImage:
    width, height = size.width(), size.height()
    base = np.array(colors[0].getRgb()[:3], dtype=np.float32)
    # Light covers in the reference carry broad, connected color bands;
    # dark covers let localized pigments leave a mostly quiet base. Ease
    # their occupied size with base brightness, including during retargets.
    spread = 1.0 + 0.45 * min(1.0, relative_luminance(colors[0]) / cls.MAX_LUMINANCE)
    total = np.zeros((height, width), dtype=np.float32)
    strongest = np.zeros_like(total)
    # Keep channels planar while combining masks: contiguous spatial
    # operations avoid a strided three-channel broadcast for every field.
    weighted = np.zeros((3, height, width), dtype=np.float32)
    for index, color in enumerate(colors[1:]):
        if color == colors[0]:
            continue
        mask = QImage(size, QImage.Format.Format_Grayscale8)
        mask.fill(Qt.GlobalColor.black)
        strength = strengths[index] if strengths else 1.0
        with QPainter(mask) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(Qt.GlobalColor.white)
            painter.drawPath(cls._blob_path(size, index, geometry_seconds, strength * spread))
        pixels = Image.frombytes("L", (width, height), mask.constBits().tobytes(),
                                 "raw", "L", mask.bytesPerLine())
        # Soften the domain, not already mixed RGB paint. The interior
        # can retain its pigment while long feathered edges blend.
        domain = np.asarray(pixels.filter(GaussianBlur(max(1.0, max(width, height) * 0.055 * max(.35, strength)))),
                            dtype=np.float32) / 255.0
        weight = domain * domain * domain
        total += weight
        np.maximum(strongest, domain, out=strongest)
        tone = 0.97 + 0.03 * math.sin(geometry_seconds * 0.18 + index * 1.21)
        pigment = np.array(color.getRgb()[:3], dtype=np.float32) * tone
        weighted += pigment[:, None, None] * weight[None, :, :]
    mixture = weighted / np.maximum(total[None, :, :], 1e-8)
    # A dominant-field composition is independent of painter order.
    # Preserve the cover base wherever all domains have left the frame.
    coverage = strongest * strongest
    rgb = np.rint(base[:, None, None] + coverage[None, :, :] * (mixture - base[:, None, None]))
    rgb = rgb.clip(0, 255).astype(np.uint8).transpose(1, 2, 0)
    opaque = np.full((height, width, 1), 255, dtype=np.uint8)
    payload = np.concatenate((rgb, opaque), axis=2).tobytes()
    return QImage(payload, size.width(), size.height(), size.width() * 4,
                  QImage.Format.Format_RGBA8888).copy()
