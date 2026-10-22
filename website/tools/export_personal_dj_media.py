"""Export the production Personal DJ painter with inert, fixed fixture data."""
from __future__ import annotations

import argparse
from pathlib import Path
import json
import os
import sys
from unittest.mock import patch
from PIL import Image

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "1"


def _default_app_root() -> Path:
    script = Path(__file__).resolve()
    for candidate in script.parents:
        if (candidate / "local" / "music_surface_window.py").is_file():
            return candidate
    raise FileNotFoundError("Could not locate local/music_surface_window.py; pass --app-root")


def _alpha_report(frames: list[Image.Image]) -> dict:
    width, height = frames[0].size
    transparent_pixels = 0
    all_corners_transparent = True
    alpha_bounds = None
    for frame in frames:
        alpha = frame.getchannel("A")
        transparent_pixels += alpha.histogram()[0]
        if any(alpha.getpixel(point) != 0 for point in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))):
            all_corners_transparent = False
        bounds = alpha.getbbox()
        if bounds:
            if alpha_bounds is None:
                alpha_bounds = list(bounds)
            else:
                alpha_bounds = [min(alpha_bounds[0], bounds[0]), min(alpha_bounds[1], bounds[1]),
                                max(alpha_bounds[2], bounds[2]), max(alpha_bounds[3], bounds[3])]
    pixel_count = width * height * len(frames)
    return {
        "verified": all_corners_transparent and transparent_pixels > 0,
        "all_four_corners_transparent_in_every_frame": all_corners_transparent,
        "transparent_pixels": transparent_pixels,
        "total_pixels": pixel_count,
        "transparent_fraction": round(transparent_pixels / pixel_count, 6),
        "union_alpha_bounds": alpha_bounds,
    }


def _verify_encoded_animation(path: Path, expected_frames: int, expected_duration_ms: int) -> tuple[list[Image.Image], int]:
    with Image.open(path) as encoded:
        decoded = []
        for index in range(encoded.n_frames):
            encoded.seek(index)
            decoded.append(encoded.convert("RGBA"))
    if len(decoded) < 2 or len(decoded) > expected_frames:
        raise RuntimeError(f"{path.name}: invalid WebP frame count {len(decoded)} for {expected_frames} samples")
    payload = path.read_bytes()
    frame_durations = []
    offset = 12
    while offset + 8 <= len(payload):
        chunk_type = payload[offset:offset + 4]
        chunk_size = int.from_bytes(payload[offset + 4:offset + 8], "little")
        chunk = payload[offset + 8:offset + 8 + chunk_size]
        if chunk_type == b"ANMF":
            if len(chunk) < 16:
                raise RuntimeError(f"{path.name}: truncated WebP animation frame")
            frame_durations.append(int.from_bytes(chunk[12:15], "little"))
        offset += 8 + chunk_size + (chunk_size & 1)
    if len(frame_durations) != expected_frames:
        raise RuntimeError(f"{path.name}: WebP container has {len(frame_durations)} frames, expected {expected_frames}")
    duration_ms = sum(frame_durations)
    if duration_ms != expected_duration_ms:
        raise RuntimeError(f"{path.name}: encoded {duration_ms} ms, expected {expected_duration_ms} ms")
    return decoded, duration_ms


def _coalesce_adjacent_duplicate_frames(frames: list[Image.Image], durations: list[int]):
    unique_frames = []
    for frame, duration in zip(frames, durations):
        pixels = frame.tobytes()
        if unique_frames and pixels == unique_frames[-1][1]:
            unique_frames[-1][2] += duration
        else:
            unique_frames.append([frame, pixels, duration])
    return ([frame for frame, _pixels, _duration in unique_frames],
            [duration for _frame, _pixels, duration in unique_frames])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path,
                        help="GluSpot application source root (defaults to the nearest ancestor)")
    parser.add_argument("--output", type=Path,
                        help="Asset output directory (defaults to APP_ROOT/website/public/assets/dj)")
    args = parser.parse_args()
    app_root = (args.app_root or _default_app_root()).resolve()
    if not (app_root / "local" / "music_surface_window.py").is_file():
        parser.error(f"--app-root does not contain local/music_surface_window.py: {app_root}")
    out = (args.output or app_root / "website" / "public" / "assets" / "dj").resolve()
    sys.path.insert(0, str(app_root))

    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from local.music_surface_palette import artwork_surface_palette
    from local.personal_dj_icon import paint_personal_dj_icon
    from tests.test_personal_dj_surface_outro import PersonalDjSurfaceOutroTests

    app = QApplication.instance() or QApplication([])
    out.mkdir(parents=True, exist_ok=True)
    records = []
    for key, art in (("chace", "chace-belated-suffocation.jpg"),
                     ("alicks", "alicks-unwritten.jpg"),
                     ("noah", "goodboy-noah-airport-bar.jpg")):
        cover_path = app_root / "website" / "public" / "assets" / art
        if not cover_path.is_file():
            cover_path = out.parent / art
        if not cover_path.is_file():
            raise FileNotFoundError(f"Missing fixture artwork: {cover_path}")
        for state in ("speaking", "ready"):
            fixture = PersonalDjSurfaceOutroTests()
            fixture.app = app
            fixture.setUp()
            try:
                window = fixture.window
                fixture.owner.album_art = QImage(str(cover_path))
                palette = artwork_surface_palette(fixture.owner.album_art, QColor("#555555"), QColor("#777777"))
                fixture.owner.album_color = QColor(palette.primary)
                fixture.owner.secondary_color = QColor(palette.secondary)
                fixture.start_speech()
                start = 100.4
                if state == "ready":
                    fixture.complete_speech(preload="ready")
                    fixture.sync(120)
                    window._reset_control_idle(120)
                    start = 120
                region = QRectF(window.rect())
                with patch("local.music_surface_window.time.monotonic", return_value=start):
                    icon, _ = window._personal_dj_geometry(region, start)
                side = 120 if state == "speaking" else icon.width() + 22
                ready_crop = QRectF(icon.center().x() - side / 2, icon.center().y() - side / 2,
                                    side, side) if state == "ready" else None
                frames = []
                for frame in range(60):
                    image = QImage(round(side * 3), round(side * 3), QImage.Format.Format_RGBA8888)
                    image.fill(Qt.GlobalColor.transparent)
                    now = start + frame / 24
                    with patch("local.music_surface_window.time.monotonic", return_value=now), QPainter(image) as painter:
                        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                        painter.scale(3, 3)
                        if state == "speaking":
                            # Use the exact colors chosen by the native button,
                            # but paint only its production five-ring helper.
                            # This leaves the fixed crop transparent around the
                            # icon instead of including the expanding caption.
                            icon, caption = window._personal_dj_geometry(region, now)
                            button = icon.united(caption)
                            color = window._interpolated_control_color(
                                "dj-accent", window._future_lyric_color(button), now)
                            inner_width, inner_height = icon.width() * .64, icon.height() * .72
                            inner = QRectF((side - inner_width) / 2, (side - inner_height) / 2,
                                           inner_width, inner_height)
                            elapsed = max(0.0, now - window._dj_ring_started_at)
                            bands = window._dj_audio_frame if window._owned_dj_speaking else None
                            # The exported icon sits on the website's dark
                            # transparent canvas, so use the helper's native
                            # pale-neutral contrast default to keep its
                            # album hue visible against that actual backdrop.
                            paint_personal_dj_icon(painter, inner, color, bands=bands,
                                                   elapsed=elapsed)
                        else:
                            # Keep the already-correct native ready circle,
                            # including its green status outline.
                            painter.translate(-ready_crop.left(), -ready_crop.top())
                            window._paint_personal_dj_control(painter, region)
                    raw_pixels = bytes(image.constBits())[:image.sizeInBytes()]
                    frames.append(Image.frombytes("RGBA", (image.width(), image.height()), raw_pixels))
                name = f"{key}-{state}"
                still_path = out / f"{name}-still.webp"
                animation_path = out / f"{name}.webp"
                frames[0].save(still_path, lossless=True)
                # Alternating sample durations keep repeated adjacent images
                # as distinct encoded frames while preserving 2500 ms total.
                sample_durations = [42, 42, 41] * 20
                encoded_frames, durations = _coalesce_adjacent_duplicate_frames(frames, sample_durations)
                encoded_frames[0].save(animation_path, save_all=True, append_images=encoded_frames[1:],
                                       duration=durations, loop=0, lossless=True, method=4)
                decoded, encoded_duration = _verify_encoded_animation(animation_path, len(encoded_frames), 2500)
                alpha_report = _alpha_report(decoded)
                if not alpha_report["verified"]:
                    raise RuntimeError(f"{animation_path.name}: transparent crop verification failed")
                records.append({
                    "file": animation_path.name,
                    "still_file": still_path.name,
                    "state": state,
                    "sample_rate_fps": 24,
                    "sampled_frames": 60,
                    "encoded_frames": len(decoded),
                    "encoded_duration_ms": encoded_duration,
                    "size": list(decoded[0].size),
                    "pixel_alpha_verification": alpha_report,
                    "cover_palette": {
                        "primary": palette.primary.name(QColor.NameFormat.HexRgb),
                        "secondary": palette.secondary.name(QColor.NameFormat.HexRgb),
                    },
                    "source": "local/music_surface_window.py::_paint_personal_dj_control",
                })
            finally:
                fixture.tearDown()
    (out / "provenance.json").write_text(json.dumps({
        "description": "Actual GluSpot painter with inert fixtures; no account, network, audio, or user desktop",
        "status_colors": {"loading": "#248eea", "ready": "#16c777", "failed": "#ec373f"},
        "assets": records,
    }, indent=2), encoding="utf-8")
    print(json.dumps({"exported": len(records), "output": str(out)}))


if __name__ == "__main__":
    main()
