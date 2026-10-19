"""Export cached artwork for the current white-card/black-island installer.

The current native wizard composes these twofold-density PNGs at 60 Hz with
GDI+ into one premultiplied-alpha DIB. Its outer boundary is a native vector
path; no binary window region or stretched 1x frame defines the visible edge.
The older AlbumFlow exporter remains available for compatibility previews.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

from local.music_surface_background import AlbumFlowBackgroundRenderer
from local.playback_entry_window import PlaybackEntryWindow


FRAME_RATE = 30
LOOP_SECONDS = 20
FRAME_COUNT = FRAME_RATE * LOOP_SECONDS
RASTER_SIZE = QSize(256, 185)
COMPACT_PROGRESS_PRESENTATION = "native-directory-row-capsule-v1"


def prepare_brand_animation(destination: Path, root: Path = ROOT) -> None:
    """Export the user's white-card/island storyboard at native 60 Hz.

    All motion assets are cached before the native window is shown. The brand
    retains the approved v3 word geometry; Source Han Heavy renders the finish
    line. Fourfold drawing is resolved to 2x cached assets; the native outer
    boundary is independently antialiased by GDI+ at its physical pixel size.
    """
    import math
    from PIL import Image, ImageDraw, ImageFont, ImageChops, ImageColor
    from tools import preview_install_animation as brand_source
    frame_at = brand_source.frame_at
    original_output_scale = brand_source.OUTPUT_SCALE
    brand_source.OUTPUT_SCALE = 4

    destination.mkdir(parents=True, exist_ok=True)
    # A refreshed cache must not continue shipping the retired perimeter and
    # fade rasters through the installer's compact-*.png resource wildcard.
    for obsolete in destination.glob("compact-progress-*.png"):
        obsolete.unlink()
    font = Path("C:/Windows/Fonts/impact.ttf")
    chinese_font = root / "assets/fonts/SourceHanSans/SourceHanSansSC-Heavy.otf"
    if not font.is_file() or not chinese_font.is_file():
        raise OSError("Approved brand/Source Han Heavy font is unavailable")
    scale = 4
    output_scale = 2
    size = (370, 258)
    palette = ("#7ACAE6", "#EE7535", "#4FA82E", "#B62EAD")
    pale = ("#B7DEEB", "#F5AA85", "#ADE49B", "#E09BDA")
    messages = (
        "如果有什么功能\n想要\n请告诉我",
        "GLUSPOT的研发\n少不了更伟大的\n开源前辈们",
        "海内存知己，\n天涯若比邻",
        "好用的话\n请给我一个\nstar吧!",
    )

    def canvas(width, height):
        return Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))

    def rr(draw, box, radius, fill, **kw):
        draw.rounded_rectangle(tuple(round(v * scale) for v in box), radius=round(radius * scale), fill=fill, **kw)

    def save(graphic, name):
        graphic.resize((graphic.width * output_scale // scale, graphic.height * output_scale // scale), Image.Resampling.LANCZOS).save(destination / name)

    body = canvas(*size)
    rr(ImageDraw.Draw(body), (0, 0, 370, 258), 32, "white")
    save(body, "compact-body.png")
    island = canvas(342, 48)
    rr(ImageDraw.Draw(island), (0, 0, 342, 48), 24, "black")
    save(island, "compact-island.png")
    # The Stage 3 model IS the black island: map its capsule boundary onto
    # the complete 342x48 island, instead of placing a smaller white outline
    # inside another capsule. Inverting the v3 positive/negative geometry gives
    # black capsule material and white holes/letters on the supplied white card.
    # Constant white RGB + the original coverage mask avoids double-multiplying
    # gray antialias pixels when PNG alpha is composited by the native control.
    for index in range(300):
        field = frame_at(font, index / 60).convert("L")
        field = field.crop((64, 20, 976, 176))
        coverage = ImageChops.invert(field).resize((342 * output_scale, 48 * output_scale), Image.Resampling.LANCZOS)
        graphic = Image.new("RGBA", coverage.size, "white")
        graphic.putalpha(coverage)
        graphic.save(destination / f"compact-brand-{index:03d}.png")
    brand_source.OUTPUT_SCALE = original_output_scale

    # These are the exact 100-unit silhouettes of
    # local.music_surface_layout.paint_content_layout_glyph. Keep semantic
    # cover/footer/three lyric-or-queue blocks through every in-place morph.
    poses = (
        ((23,12,54,54,9), (20,76,60,10,5), (28,18,0,9,3), (28,33,0,9,3), (28,48,0,9,3)),
        ((22,8,56,59,9), (19,77,62,10,5), (28,18,34,9,3), (28,33,44,9,3), (28,48,34,9,3)),
        ((6,20,47,48,8), (8,79,82,10,5), (60,24,32,10,3), (60,40,39,10,3), (60,56,32,10,3)),
        ((10,28,50,50,9), (8,79,0,10,5), (68,10,24,22,5), (68,42,24,22,5), (68,74,24,22,5)),
    )
    def color_mix(first, second, amount):
        a, b = ImageColor.getrgb(first), ImageColor.getrgb(second)
        return tuple(round(x + (y - x) * amount) for x, y in zip(a, b)) + (255,)

    def lower(mode, amount=1.):
        graphic = canvas(342, 120)
        draw = ImageDraw.Draw(graphic)
        previous = (mode - 1) % 4
        eased = amount * amount * (3 - 2 * amount)
        # Reuse the actual deliberate-selection spring formula; all geometry
        # remains centered at the same (78,60) icon origin, never slides away.
        elastic = 1 + .16 * math.sin(amount * math.pi * 3) * (1 - amount)
        strong = color_mix(palette[previous], palette[mode], eased)
        light = color_mix(pale[previous], pale[mode], eased)
        for index, (source, target) in enumerate(zip(poses[previous], poses[mode])):
            x, y, width, height, radius = tuple(a + (b - a) * eased for a, b in zip(source, target))
            if width <= .001 or height <= .001:
                continue
            center_x, center_y = (x + width / 2 - 50) * 1.55 * elastic + 78, (y + height / 2 - 50) * 1.2 * elastic + 60
            width, height = width * 1.55 * elastic, height * 1.2 * elastic
            rr(draw, (center_x-width/2, center_y-height/2, center_x+width/2, center_y+height/2), radius * 1.2 * elastic,
               light if index in (0,1) else strong)
        text_font = ImageFont.truetype(str(chinese_font), 19 * scale)
        for text_mode, alpha in ((previous, 1-eased), (mode, eased)):
            if alpha <= 0:
                continue
            text_layer = canvas(342, 120)
            text_draw = ImageDraw.Draw(text_layer)
            lines = messages[text_mode].split("\n")
            text_draw.multiline_text((178*scale, (58-len(lines)*11)*scale), messages[text_mode], font=text_font,
                                     fill=ImageColor.getrgb(palette[text_mode]) + (round(255*alpha),), spacing=3*scale)
            graphic.alpha_composite(text_layer)
        return graphic

    lower_poses = [lower(mode) for mode in range(4)]
    for mode in range(4):
        for frame in range(31):
            save(lower(mode, frame/30), f"compact-lower-{mode}-{frame:02d}.png")
        for frame in range(36):
            graphic = lower_poses[mode].copy()
            graphic.putalpha(graphic.getchannel("A").point(lambda value: round(value*(1-frame/35))))
            save(graphic, f"compact-lower-out-{mode}-{frame:02d}.png")

    slogan = canvas(370, 64)
    draw = ImageDraw.Draw(slogan)
    slogan_text = r"This is true music \^0^/"
    slogan_font = ImageFont.truetype(str(chinese_font), 24 * scale)
    bounds = draw.textbbox((0, 0), slogan_text, font=slogan_font)
    draw.text(((370 * scale - (bounds[2] - bounds[0])) / 2 - bounds[0], (64 * scale - (bounds[3] - bounds[1])) / 2 - bounds[1]), slogan_text, font=slogan_font, fill="white")
    for frame in range(31):
        graphic = slogan.copy()
        graphic.putalpha(graphic.getchannel("A").point(lambda value: round(value * frame / 30)))
        save(graphic, f"compact-slogan-{frame:02d}.png")
    # Continue the final outline spatially: its white interior withdraws while
    # the slogan remains white throughout. Black therefore grows inward and
    # reveals the letters as negative space, without an all-black pause/fade-in.
    final_hole = Image.new("L", (370 * scale, 64 * scale), 0)
    brand_hole = Image.open(destination / "compact-brand-299.png").getchannel("A")
    brand_hole = brand_hole.resize((342 * scale, 48 * scale), Image.Resampling.LANCZOS)
    final_hole.paste(brand_hole, (14 * scale, 12 * scale))
    slogan_mask = slogan.getchannel("A")
    for frame in range(40):
        amount = frame / 39
        amount = amount * amount * (3 - 2 * amount)
        remaining = Image.new("L", final_hole.size, 0)
        if amount < 1:
            width, height = 318 * (1 - amount), 36 * (1 - amount)
            center_y = 36 - 4 * amount
            rr(ImageDraw.Draw(remaining), (185-width/2, center_y-height/2,
                                         185+width/2, center_y+height/2), height/2, 255)
        # Intersect the moving interior with the exact last outline, preserving
        # its native antialias edge at the loop-to-completion boundary.
        remaining = ImageChops.darker(final_hole, remaining)
        graphic = canvas(370, 64)
        graphic.paste("white", (0, 0, graphic.width, graphic.height))
        graphic.putalpha(ImageChops.lighter(remaining, slogan_mask))
        save(graphic, f"compact-finish-{frame:02d}.png")
    # Rasterize the actual settings close/minimize SVGs, with the exact
    # settings hue/coverage tint. Do not invent a third/maximize control.
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtCore import QRectF as SvgRect
    control_names = (("close", "#ff5f57"), ("minimize", "#ffbd2e"))
    for key, hue in control_names:
        native_mask = QImage(128, 128, QImage.Format.Format_RGBA8888)
        native_mask.fill(0)
        with QPainter(native_mask) as painter:
            QSvgRenderer(str(root / f"assets/window_controls/{key}.svg")).render(painter, SvgRect(0,0,128,128))
        raw = Image.frombytes("RGBA", (128,128), bytes(native_mask.constBits()))
        alpha = raw.getchannel("A")
        coverage = sum(alpha.get_flattened_data()) / (255 * 128 * 128)
        retained = .38 + .25 * coverage
        tint = tuple(round(channel * retained) for channel in ImageColor.getrgb(hue))
        glyph = Image.new("RGBA", (128,128), tint + (255,))
        glyph.putalpha(alpha)
        glyph = glyph.resize((round(10.3*scale), round(10.3*scale)), Image.Resampling.LANCZOS)
        face = canvas(20,20)
        face_draw = ImageDraw.Draw(face)
        face_draw.ellipse((round(.6*scale), round(.6*scale), round(19.4*scale), round(19.4*scale)), fill=hue)
        face.alpha_composite(glyph, ((20*scale-glyph.width)//2,(20*scale-glyph.height)//2))
        for frame in range(31):
            faded = face.copy()
            faded.putalpha(faded.getchannel("A").point(lambda value: round(value*frame/30)))
            save(faded, f"compact-control-{key}-{frame:02d}.png")
    control_back = canvas(64,32)
    control_draw = ImageDraw.Draw(control_back)
    rr(control_draw,(1.4,1.4,62.6,30.6),14.6,(255,255,255,90),outline="#BFC0C3",width=round(2.8*scale))
    for frame in range(31):
        faded = control_back.copy()
        faded.putalpha(faded.getchannel("A").point(lambda value: round(value*frame/30)))
        save(faded, f"compact-control-capsule-{frame:02d}.png")
    (destination / "COMPACT.json").write_text(json.dumps({
        "storyboard": "user 2026-10-16 white card, black island, bottom capsule progress",
        "body": [370, 258], "preflight_body": [370, 258], "final_island": [370, 64], "fps": 60, "brand_frames": 300, "brand_loop_seconds": 5,
        "brand_source": "tools.preview_install_animation.frame_at (approved v3)",
        "word_block_morph": True, "entry": "right-to-left squeeze", "no_alpha_crossfade": True,
        "brand_fills_island": True, "nested_white_capsule": False, "double_alpha": False,
        "layout_morph": "in-place semantic blocks; exact player glyph coordinates and damped spring",
        "layout_lyric_ink": "three accent bars, distinct from the cover fill",
        "shadow_margin": 24, "native_surface": [418, 306],
        "card_shadow": "native layered rounded fills, 18px soft falloff, 4px downward offset",
        "palettes": list(palette), "messages": list(messages),
        "slogan": slogan_text, "slogan_font": str(chinese_font.relative_to(root)),
        "completion": ["lower fades", "white body shrinks to ring", "finish brand loop", "black grows inward leaving white slogan glyphs", "slogan holds then fades out", "quintic S-curve travel to screen top, zero start/end velocity and acceleration", "--installer-handoff X Y W H"],
        "finish_morph_frames": 40, "slogan_negative_space": True,
        "callback_file_io": False, "raster_density": 2,
        "progress_presentation": COMPACT_PROGRESS_PRESENTATION,
        "progress_rect": [16, 214, 338, 30], "progress_radius": 15,
        "progress_source": "WizardForm.ProgressGauge.Position / Max",
        "progress_rasters": 0, "perimeter_progress": False,
        "native_compositor": "GDI+ PARGB DIB, antialiased vector outer path, ULW_ALPHA",
        "window_controls": {"keys": ["close", "minimize"], "colors": ["#ff5f57", "#ffbd2e"], "source": "local.mac_style MacTrafficButton / original window_controls SVG", "movement_reveal": True, "idle_hide": True},
    }, indent=2, ensure_ascii=False), encoding="utf-8")

def prepare(destination: Path, root: Path = ROOT) -> None:
    prepare_brand_animation(destination, root)
    app = QGuiApplication.instance() or QGuiApplication([])
    destination.mkdir(parents=True, exist_ok=True)
    renderer = AlbumFlowBackgroundRenderer()
    palettes = tuple(tuple(QColor(value) for value in palette)
                     for palette in PlaybackEntryWindow.PALETTES)
    # Prewarm the last palette. At elapsed 0 the real player renderer starts
    # its 3 -> 0 transition; later targets change exactly at 5, 10 and 15 s.
    # The first image still contains palette 3, so returning to it at 20 s
    # closes geometry and leaves the next real 1.2 s transition intact.
    fade = renderer.PALETTE_TRANSITION_SECONDS
    for step in range(round(2.0 * FRAME_RATE) + 1):
        now = step / FRAME_RATE
        renderer.render(RASTER_SIZE, palettes[-1], now, .74, weights=())
    first = QImage()
    for index in range(FRAME_COUNT):
        elapsed = index / FRAME_RATE
        palette_index = int((elapsed + 1e-9) // PlaybackEntryWindow.PALETTE_INTERVAL_SECONDS) % len(palettes)
        frame = renderer.render(RASTER_SIZE, palettes[palette_index], 2.0 + elapsed, .74, weights=()).copy()
        if first.isNull():
            first = frame.copy()
        if elapsed >= LOOP_SECONDS - fade:
            fraction = min(1.0, max(0.0, (elapsed - LOOP_SECONDS + fade) / fade))
            fraction = fraction * fraction * (3.0 - 2.0 * fraction)
            with QPainter(frame) as painter:
                painter.setOpacity(fraction)
                painter.drawImage(QRectF(frame.rect()), first)
        if not frame.save(str(destination / f"background-{index:04d}.bmp"), "BMP"):
            raise OSError("Could not save installer background frame")
    first.save(str(destination / "background-first.bmp"), "BMP")
    brand = QImage(str(root / "assets/brand/gluspot-glu-icon-256.png"))
    if brand.isNull():
        raise OSError("GluSpot brand icon is unavailable")
    brand.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio,
                 Qt.TransformationMode.SmoothTransformation).save(str(destination / "brand-small.bmp"), "BMP")
    evidence = {
        "renderer": "local.music_surface_background.AlbumFlowBackgroundRenderer",
        "frames": FRAME_COUNT, "fps": FRAME_RATE, "raster": [RASTER_SIZE.width(), RASTER_SIZE.height()],
        "flow_intensity": .74, "weights": [],
        "target_palette_seconds": PlaybackEntryWindow.PALETTE_INTERVAL_SECONDS,
        "palette_transition_seconds": fade, "loop_transition_seconds": fade,
        "decorative_overlay": False,
    }
    (destination / "RENDERER.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--brand-only", action="store_true",
                        help="Export current compact installer assets without a Qt process")
    options = parser.parse_args()
    if options.brand_only:
        prepare_brand_animation(options.destination)
    else:
        prepare(options.destination)
