"""Render the six-frame GluSpot installation-animation concept as a GIF preview.

This script only writes preview artifacts under logs/previews/installation by
default; it does not change an installer or the application. Pillow draws the
capsules and rasterizes the local Impact font, without generated image assets.

Run from any directory:
    E:/gpt-island/.venv/Scripts/python.exe tools/preview_install_animation.py

Use --font PATH when Impact is unavailable, and --output-dir to change where
the GIF, six-keyframe montage, and reproducibility metadata are saved.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
BASE_SIZE = (257, 49)
SUPERSAMPLE = 4
OUTPUT_SCALE = 2
FPS = 25
FRAME_MS = 1000 // FPS
CAPSULE = (16.0, 5.0, 244.0, 44.0)
INNER = (24.0, 10.0, 236.0, 39.0)
THREE_HOLES = ((24.0, 89.0), (97.0, 162.0), (170.0, 236.0))
TWO_HOLES = ((24.0, 127.0), (136.0, 236.0))
KEYFRAMES = ("outline", "three_holes", "spaced_glu", "gluspot", "two_holes", "outline")
# Durations total exactly 5 seconds. Matching first/last poses closes the loop.
TIMELINE = (
    (0.34, "outline", "outline"),
    (0.54, "outline", "three_holes"),
    (0.26, "three_holes", "three_holes"),
    (0.46, "three_holes", "spaced_glu"),
    (0.44, "spaced_glu", "spaced_glu"),
    (0.46, "spaced_glu", "gluspot"),
    (0.68, "gluspot", "gluspot"),
    (0.54, "gluspot", "two_holes"),
    (0.30, "two_holes", "two_holes"),
    (0.54, "two_holes", "outline"),
    (0.44, "outline", "outline"),
)


def ease(value: float) -> float:
    """Quintic interpolation with zero velocity/acceleration at both ends."""
    value = max(0.0, min(1.0, value))
    return value**3 * (value * (value * 6.0 - 15.0) + 10.0)


def interpolate(start: float, end: float, amount: float) -> float:
    return start + (end - start) * amount


def pixel(value: float) -> int:
    return round(value * SUPERSAMPLE)


def new_mask() -> Image.Image:
    return Image.new("L", tuple(pixel(value) for value in BASE_SIZE), 0)


def capsule_mask() -> Image.Image:
    result = new_mask()
    ImageDraw.Draw(result).rounded_rectangle(
        tuple(pixel(value) for value in CAPSULE), radius=pixel(19.5), fill=255
    )
    return result


def hole_mask(holes: tuple[tuple[float, float], ...], roundness: float = 1.0) -> Image.Image:
    result = new_mask()
    draw = ImageDraw.Draw(result)
    for left, right in holes:
        draw.rounded_rectangle(
            (pixel(left), pixel(INNER[1]), pixel(right), pixel(INNER[3])),
            radius=pixel((INNER[3] - INNER[1]) / 2 * roundness),
            fill=255,
        )
    return result


def glyph(font_path: Path, text: str, size: float, width_scale: float = 1.0) -> Image.Image:
    font = ImageFont.truetype(str(font_path), pixel(size))
    left, top, right, bottom = font.getbbox(text)
    result = Image.new("L", (right - left, bottom - top), 0)
    ImageDraw.Draw(result).text((-left, -top), text, font=font, fill=255)
    if width_scale != 1.0:
        result = result.resize(
            (round(result.width * width_scale), result.height), Image.Resampling.LANCZOS
        )
    return result


def centered_paste(
    target: Image.Image, graphic: Image.Image, x: float, y: float
) -> None:
    overlay = new_mask()
    overlay.paste(graphic, (round(pixel(x) - graphic.width / 2), round(pixel(y) - graphic.height / 2)))
    target.paste(ImageChops.lighter(target, overlay))


def letter_mask(font_path: Path, amount: float = 0.0) -> Image.Image:
    """Slide opaque spot in from the right, pushing and squeezing GLU."""
    result = new_mask()
    starts = (56.0, 129.0, 204.0)
    ends = (66.0, 85.0, 104.0)
    amount = max(0.0, min(1.0, amount))
    spot = glyph(font_path, "spot", 37.0, 1.13)
    spot_half_width = spot.width / SUPERSAMPLE / 2
    # Start the entire opaque word beyond the capsule. compose() supplies
    # its curved clipping boundary, so the first visible ink enters at right.
    arrival = ease(min(1.0, amount / .78))
    spot_center = interpolate(CAPSULE[2] + spot_half_width + 4.0, 175.0, arrival)
    spot_left = spot_center - spot_half_width
    first_contact = starts[-1] + glyph(font_path, "U", 30.0).width / SUPERSAMPLE / 2
    final_right = ends[-1] + glyph(font_path, "U", 37.0, 1.10).width / SUPERSAMPLE / 2
    push = max(0.0, min(1.0, (first_contact - spot_left) / (first_contact - final_right)))
    # GLU stays spaced until spot reaches U. The incoming word supplies
    # most of the gathering, followed by a short elastic release to the
    # exact reference positions after spot has arrived.
    release = ease(max(0.0, min(1.0, (amount - .70) / .30)))
    movement = push + (1.0 - push) * release
    contact = max(0.0, min(1.0, (first_contact - spot_left) / (first_contact - (175.0 - spot_half_width))))
    pressure = math.sin(math.pi * contact)
    horizontal_scale = 1.0 - .22 * pressure + .035 * math.sin(math.pi * release)
    size = interpolate(30.0, 37.0, movement)
    for letter, start, end in zip("GLU", starts, ends):
        centered_paste(
            result,
            glyph(font_path, letter, size, interpolate(1.0, 1.10, movement) * horizontal_scale),
            interpolate(start, end, movement) - 4.0 * pressure,
            24.5,
        )
    centered_paste(result, spot, spot_center, 24.5)
    return result


def rounded_box_mask(boxes, radius: float = 4.0) -> Image.Image:
    result = new_mask()
    draw = ImageDraw.Draw(result)
    for left, top, right, bottom in boxes:
        if right <= left or bottom <= top:
            continue
        draw.rounded_rectangle(tuple(pixel(value) for value in (left, top, right, bottom)),
                               radius=pixel(min(radius, (right - left) / 2, (bottom - top) / 2)), fill=255)
    return result


def spaced_glyph_boxes(font_path: Path):
    boxes = []
    for letter, center in zip("GLU", (56.0, 129.0, 204.0)):
        graphic = glyph(font_path, letter, 30.0)
        width, height = graphic.width / SUPERSAMPLE, graphic.height / SUPERSAMPLE
        boxes.append((center - width / 2, 24.5 - height / 2, center + width / 2, 24.5 + height / 2))
    return tuple(boxes)


def holes_to_letters(font_path: Path, amount: float) -> Image.Image:
    """Narrow holes to exact glyph widths, then withdraw opaque geometry.

    Glyphs are always drawn at full coverage underneath a black shape. Only
    that shape's width/height/corners change; there is no alpha crossfade.
    """
    boxes = spaced_glyph_boxes(font_path)
    if amount <= .52:
        fraction = ease(amount / .52)
        sources = tuple((left, INNER[1], right, INNER[3]) for left, right in THREE_HOLES)
        current = tuple(tuple(interpolate(a, b, fraction) for a, b in zip(source, target))
                        for source, target in zip(sources, boxes))
        return rounded_box_mask(current, interpolate(14.5, 4.0, fraction))
    fraction = ease((amount - .52) / .48)
    opaque_letters = letter_mask(font_path)
    covers = []
    for left, top, right, bottom in boxes:
        center = (left + right) / 2
        half = (right - left) / 2 * (1 - fraction)
        covers.append((center - half, top, center + half, bottom))
    return ImageChops.lighter(opaque_letters, rounded_box_mask(covers))


def compact_word_boxes(font_path: Path):
    """Two tight ink bounds: one for the whole GLU word, one for spot."""
    compact = letter_mask(font_path, 1.0)
    boxes = []
    # The reference pose has an empty gap at x=126; this separates the
    # rendered words without splitting either into individual letter blocks.
    for left, right in ((0.0, 126.0), (126.0, float(BASE_SIZE[0]))):
        word = compact.crop((pixel(left), 0, pixel(right), pixel(BASE_SIZE[1])))
        bounds = word.getbbox()
        if bounds is None:
            raise ValueError("The final reference word has no visible ink")
        boxes.append((left + bounds[0] / SUPERSAMPLE, bounds[1] / SUPERSAMPLE,
                      left + bounds[2] / SUPERSAMPLE, bounds[3] / SUPERSAMPLE))
    return tuple(boxes)


def letters_to_holes(font_path: Path, amount: float) -> Image.Image:
    """Morph each whole word into one equal-width block, then expand it."""
    boxes = compact_word_boxes(font_path)
    if amount <= .44:
        fraction = ease(amount / .44)
        letters = letter_mask(font_path, 1.0)
        if fraction <= 0.0:
            return letters
        blocks = rounded_box_mask(boxes, 2.5)
        # Replace entire word-wide rows from the middle outward. There is
        # only one sweep per word, and every row remains fully opaque.
        # At the end the rounded rectangle owns even the corner pixels,
        # so the next expansion starts from precisely the same two blocks.
        sweep = new_mask()
        draw = ImageDraw.Draw(sweep)
        for left, top, right, bottom in boxes:
            center = (top + bottom) / 2
            half_height = (bottom - top) / 2 * fraction
            draw.rectangle((pixel(left), pixel(center - half_height),
                            pixel(right), pixel(center + half_height)), fill=255)
        return ImageChops.lighter(ImageChops.multiply(letters, ImageChops.invert(sweep)),
                                  ImageChops.multiply(blocks, sweep))
    fraction = ease((amount - .44) / .56)
    current = []
    for source, hole in zip(boxes, TWO_HOLES):
        target = (hole[0], INNER[1], hole[1], INNER[3])
        current.append(tuple(interpolate(a, b, fraction) for a, b in zip(source, target)))
    return rounded_box_mask(current, interpolate(2.5, 14.5, fraction))


def pose_mask(font_path: Path, pose: str) -> Image.Image:
    if pose == "outline":
        return hole_mask(((INNER[0], INNER[2]),))
    if pose == "three_holes":
        return hole_mask(THREE_HOLES)
    if pose == "two_holes":
        return hole_mask(TWO_HOLES)
    if pose == "spaced_glu":
        return letter_mask(font_path)
    if pose == "gluspot":
        return letter_mask(font_path, 1.0)
    raise ValueError(f"Unknown pose: {pose}")


def transition_mask(font_path: Path, start: str, end: str, amount: float) -> Image.Image:
    raw_amount = max(0.0, min(1.0, amount))
    amount = ease(raw_amount)
    if start == end:
        return pose_mask(font_path, start)
    if (start, end) == ("outline", "three_holes"):
        # Adjacent partitions gain a gap and round corners throughout the split.
        # Clipping keeps the original rounded outside ends at every intermediate pose.
        partitions = ((24.0, 93.0), (93.0, 166.0), (166.0, 236.0))
        holes = tuple(
            (interpolate(source[0], target[0], amount), interpolate(source[1], target[1], amount))
            for source, target in zip(partitions, THREE_HOLES)
        )
        return ImageChops.multiply(hole_mask(holes, amount), pose_mask(font_path, "outline"))
    if (start, end) == ("spaced_glu", "gluspot"):
        return letter_mask(font_path, raw_amount)
    if (start, end) == ("three_holes", "spaced_glu"):
        return holes_to_letters(font_path, raw_amount)
    if (start, end) == ("gluspot", "two_holes"):
        return letters_to_holes(font_path, raw_amount)
    if (start, end) == ("two_holes", "outline"):
        # The gap and inside corners flatten as both holes rejoin the empty outline.
        partitions = ((24.0, 131.5), (131.5, 236.0))
        holes = tuple(
            (interpolate(source[0], target[0], amount), interpolate(source[1], target[1], amount))
            for source, target in zip(TWO_HOLES, partitions)
        )
        return ImageChops.multiply(hole_mask(holes, 1.0 - amount), pose_mask(font_path, "outline"))
    raise ValueError(f"No geometric morph for {start} -> {end}")


def compose(mask: Image.Image) -> Image.Image:
    outer = capsule_mask()
    result = Image.new("L", outer.size, 0)
    result.paste(255, mask=outer)
    result.paste(0, mask=ImageChops.multiply(mask, outer))
    return result.resize(
        tuple(value * OUTPUT_SCALE for value in BASE_SIZE), Image.Resampling.LANCZOS
    )


def frame_at(font_path: Path, time_seconds: float) -> Image.Image:
    for duration, start, end in TIMELINE:
        if time_seconds < duration:
            return compose(transition_mask(font_path, start, end, time_seconds / duration))
        time_seconds -= duration
    return compose(pose_mask(font_path, "outline"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, default=Path("C:/Windows/Fonts/impact.ttf"))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "logs/previews/installation")
    args = parser.parse_args()
    if not args.font.is_file():
        parser.error(f"Font does not exist: {args.font}; supply a narrow heavy font with --font")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    duration_seconds = round(sum(item[0] for item in TIMELINE), 8)
    frame_count = round(duration_seconds * FPS)
    frames = [frame_at(args.font, index / FPS) for index in range(frame_count)]
    original = args.output_dir / "gluspot-installation-preview.gif"
    previous = args.output_dir / "gluspot-installation-preview-v1.gif"
    if original.is_file() and not previous.exists():
        shutil.copy2(original, previous)
    gif_path = args.output_dir / "gluspot-installation-preview-v3.gif"
    frames[0].save(
        gif_path,
        save_all=True,
        append_images=frames[1:],
        duration=FRAME_MS,
        loop=0,
        optimize=False,
        disposal=2,
        comment=b"GluSpot v3; word block morph; right-to-left squeeze; no alpha; 5000ms loop",
    )
    width, height = frames[0].size
    # Include all six reference poses and the width-matching/entry/block
    # intermediates. Labels are outside the pure black/white preview image.
    samples = [(pose, compose(pose_mask(args.font, pose))) for pose in KEYFRAMES]
    for start, end in (("three_holes", "spaced_glu"), ("spaced_glu", "gluspot"), ("gluspot", "two_holes")):
        for fraction in (.12, .25, .38, .44, .52, .65, .78, .90):
            samples.append((f"{start} > {end} {fraction:.0%}", compose(transition_mask(args.font, start, end, fraction))))
    label_height = 25
    montage = Image.new("RGB", (width * 2, (height + label_height) * ((len(samples) + 1) // 2)), "black")
    draw = ImageDraw.Draw(montage)
    for index, (label, sample) in enumerate(samples):
        x, y = (index % 2) * width, (index // 2) * (height + label_height)
        draw.text((x + 8, y + 5), label, fill="white")
        montage.paste(sample, (x, y + label_height))
    montage_path = args.output_dir / "gluspot-installation-morph-v3.png"
    montage.save(montage_path)
    metadata = {
        "font": str(args.font.resolve()),
        "renderer": "Pillow geometry and local font, 4x supersampling",
        "morph": "No-opacity: holes narrow to glyph widths; whole opaque spot enters right-to-left through the capsule clip and squeezes GLU horizontally; word block morph makes one exact-bounding-box-width rounded rectangle per word before expansion into two holes.",
        "entry": "right-to-left squeeze",
        "word_block_morph": True,
        "word_boxes": [list(box) for box in compact_word_boxes(args.font)],
        "intermediate_samples": len(samples) - len(KEYFRAMES),
        "no_alpha_crossfade": True,
        "keyframes": list(KEYFRAMES),
        "loop_ms": round(duration_seconds * 1000),
        "rendered_frames": frame_count,
        "fps": FPS,
        "canvas_px": [width, height],
        "gif": str(gif_path.resolve()),
        "montage": str(montage_path.resolve()),
    }
    (args.output_dir / "preview-metadata-v3.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False))


if __name__ == "__main__":
    main()
