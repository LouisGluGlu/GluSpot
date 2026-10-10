"""Render the audited installation GLU outlines as transparent Windows icons.

Default regeneration uses the checked-in SVG and needs no installed font.
--update-outline --font PATH extracts the same Impact glyphs used by
preview_install_animation.py; the font file itself is never copied.
"""
from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path

from PIL import Image, ImageFont
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QFont, QImage, QPainter, QPainterPath, QRawFont, QTransform
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "assets" / "brand"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
SVG_NAME = "gluspot-glu.svg"


def path_data(path):
    commands, index = [], 0
    def point(element):
        return f"{element.x:.4f},{element.y:.4f}"
    while index < path.elementCount():
        element = path.elementAt(index)
        if element.type == QPainterPath.ElementType.MoveToElement:
            commands.append("M" + point(element))
        elif element.type == QPainterPath.ElementType.LineToElement:
            commands.append("L" + point(element))
        elif element.type == QPainterPath.ElementType.CurveToElement:
            commands.append("C" + " ".join(point(path.elementAt(index + step)) for step in range(3)))
            index += 2
        else:
            raise ValueError("unexpected_outline_element")
        index += 1
    return " ".join(commands)


def extract_svg(font_path):
    """Use actual font outlines and the animation's final glyph placement."""
    font_path = Path(font_path)
    raw = QRawFont(str(font_path), 148, QFont.HintingPreference.PreferNoHinting)
    if not raw.isValid() or raw.familyName().casefold() != "impact":
        raise ValueError("the installation reference uses Impact")
    font = ImageFont.truetype(str(font_path), 148)
    ascent = font.getmetrics()[0]
    letters = []
    for letter, center in zip("GLU", (66.0, 85.0, 104.0)):
        glyphs = raw.glyphIndexesForString(letter)
        if len(glyphs) != 1 or not glyphs[0]:
            raise ValueError("missing_reference_glyph")
        left, top, right, bottom = font.getbbox(letter)
        # These are preview_install_animation.glyph/centered_paste's final
        # 37 px, 1.10-width-scale, 4x sampling positions. The outlines retain
        # the font's curves, rather than tracing pixels from the user's GIF.
        scaled_width = round((right - left) * 1.10)
        x = round(center * 4 - scaled_width / 2) - left * 1.10
        y = round(24.5 * 4 - (bottom - top) / 2) - top + ascent
        letters.append(QTransform(1.10, 0, 0, 1, x, y).map(raw.pathForGlyph(glyphs[0])))
    word = QPainterPath()
    for path in letters:
        word.addPath(path)
    bounds = word.boundingRect()
    scale = 222.0 / bounds.width()
    transform = QTransform(scale, 0, 0, scale,
        128 - bounds.center().x() * scale, 128 - bounds.center().y() * scale)
    fingerprint = hashlib.sha256(font_path.read_bytes()).hexdigest()
    paths = "\n".join(f'  <path data-letter="{letter}" d="{path_data(transform.map(path))}"/>'
        for letter, path in zip("GLU", letters))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">\n'
        '  <title>GluSpot GLU</title>\n'
        '  <desc>Impact outlines from the installation animation final GLU pose; '
        f'font SHA256 {fingerprint}. Transparent standalone letters with a white contrast keyline.</desc>\n'
        '  <g fill="#161616" stroke="#ffffff" stroke-width="12" stroke-linejoin="round" fill-rule="evenodd" paint-order="stroke fill">\n'
        f'{paths}\n  </g>\n</svg>\n')


def render_png(svg_bytes, size):
    renderer = QSvgRenderer(QByteArray(svg_bytes))
    if not renderer.isValid():
        raise ValueError("invalid_glu_svg")
    canvas = QImage(size * 8, size * 8, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, canvas.width(), canvas.height()))
    painter.end()
    canvas = canvas.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    encoded = QByteArray()
    stream = QBuffer(encoded)
    stream.open(QIODevice.OpenModeFlag.WriteOnly)
    if not canvas.save(stream, "PNG"):
        raise RuntimeError("icon_png_render_failed")
    # Re-encode image pixels only. No source/GIF/clipboard metadata enters
    # the shipped PNG or ICO frames.
    return Image.open(io.BytesIO(bytes(encoded))).convert("RGBA")


def generate(output_dir=ASSET_DIR, *, update_outline=False, font_path=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    svg_path = output_dir / SVG_NAME
    if update_outline:
        if font_path is None:
            raise ValueError("--font is required to update the outline")
        svg_path.write_text(extract_svg(font_path), encoding="utf-8", newline="\n")
    svg_bytes = svg_path.read_bytes()
    images = {size: render_png(svg_bytes, size) for size in ICON_SIZES}
    png_path = output_dir / "gluspot-glu-icon-256.png"
    images[256].save(png_path)
    ico_path = output_dir / "gluspot-glu.ico"
    images[256].save(ico_path, format="ICO", sizes=[(size, size) for size in ICON_SIZES],
        append_images=[images[size] for size in ICON_SIZES if size != 256])
    return svg_path, png_path, ico_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ASSET_DIR)
    parser.add_argument("--update-outline", action="store_true")
    parser.add_argument("--font", type=Path)
    arguments = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    outputs = generate(arguments.output_dir, update_outline=arguments.update_outline, font_path=arguments.font)
    print("Generated GLU SVG, transparent PNG and native-size ICO: " + ", ".join(path.name for path in outputs))
