"""Export silent GluSpot UI references with the production Qt painters."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from time import perf_counter
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "1"

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--app-root", type=Path, default=Path(__file__).resolve().parents[2],
                    help="GluSpot source root (defaults to the normal website/ layout)")
parser.add_argument("--output", type=Path, default=None,
                    help="public/assets output directory (defaults to app-root/website/public/assets)")
args = parser.parse_args()
app_root = args.app_root.resolve()
out = (args.output.resolve() if args.output else app_root / "website" / "public" / "assets")
island_out = out / "islands"
island_out.mkdir(parents=True, exist_ok=True)
out.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(app_root))

from PIL import Image
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.island_stack_visual import IslandStackVisual
from local.music_surface_palette import artwork_surface_palette
from local.music_surface_background import AlbumFlowBackgroundRenderer
from tests.test_finder_bar_surface import FinderBarSurfaceTests


FPS = 60
FORWARD_SAMPLES = 361
LOOP_FRAME_COUNT = (FORWARD_SAMPLES - 1) * 2
BACKGROUND_SIZE = QSize(960, 540)
SONGS = [
    ("chace", "The Rain Song", "Chace", "chace-belated-suffocation.jpg", "It’s been the same old thing"),
    ("alicks", "Ibuprofen", "Alicks", "alicks-unwritten.jpg", "I get high and I get low"),
]
if (out / "goodboy-noah-airport-bar.jpg").exists():
    SONGS.append(("noah", "Airport Bar", "goodboy noah", "goodboy-noah-airport-bar.jpg", "Airport Bar"))

app = QApplication.instance() or QApplication([])
IslandStackVisual.load_embedded_lyric_fonts()
app.setFont(QFont(IslandStackVisual.WAVE_FONT_FAMILY_BOLD, 10))
island_records = []
background_records = []
ffmpeg = shutil.which("ffmpeg")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def export_h264(renderer: AlbumFlowBackgroundRenderer, colors, weights, path: Path):
    if not ffmpeg:
        return None
    renderer.FRAME_SECONDS = 1.0 / FPS
    first_frames = []
    render_started = perf_counter()
    for index in range(FORWARD_SAMPLES):
        image = renderer.render(BACKGROUND_SIZE, colors, 100.0 + index / FPS, weights=weights)
        image = image.convertToFormat(QImage.Format.Format_RGB888)
        image = image.scaled(256, 144, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        first_frames.append(bytes(image.bits()))
    render_seconds = perf_counter() - render_started

    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", "256x144",
        "-framerate", str(FPS), "-i", "pipe:0", "-an", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "20", "-threads", "2",
        "-vf", "scale=960:540:flags=lanczos:in_range=pc:out_range=tv:out_color_matrix=bt709,"
        "format=yuv420p,setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-color_range", "tv",
        "-g", str(FPS), "-keyint_min", str(FPS), "-sc_threshold", "0",
        "-movflags", "+faststart", str(path),
    ]
    encode_started = perf_counter()
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE)
    try:
        assert process.stdin is not None
        for frame in first_frames:
            process.stdin.write(frame)
        for index in range(FORWARD_SAMPLES - 2, 0, -1):
            process.stdin.write(first_frames[index])
        process.stdin.close()
        error = process.stderr.read() if process.stderr else b""
        return_code = process.wait()
    except (BrokenPipeError, OSError):
        if process.stdin:
            process.stdin.close()
        error = process.stderr.read() if process.stderr else b""
        return_code = process.wait()
    if return_code:
        raise RuntimeError(f"FFmpeg H.264 export failed: {error.decode(errors='replace')}")
    return {
        "file": path.name,
        "sampled_fps": FPS,
        "forward_samples": FORWARD_SAMPLES,
        "unique_forward_samples": len({hashlib.sha256(frame).digest() for frame in first_frames}),
        "loop_frames": LOOP_FRAME_COUNT,
        "loop_duration_seconds": LOOP_FRAME_COUNT / FPS,
        "source_size": [256, 144],
        "size": [960, 540],
        "codec": "H.264 (libx264), yuv420p",
        "color_range": "tv",
        "color_primaries": "bt709",
        "color_transfer": "bt709",
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
        "render_seconds": round(render_seconds, 3),
        "encode_seconds": round(perf_counter() - encode_started, 3),
    }


for key, title, artist, art, lyric in SONGS:
    fixture = FinderBarSurfaceTests()
    fixture.app = app
    fixture.setUp()
    visual = fixture.visual
    try:
        visual._solid_album_palette_async_enabled = False
        visual._music_system_dark = True
        visual.set_music_ambience_style("solid_album", notify=False)
        visual.album_art = QImage(str(out / art))
        palette = artwork_surface_palette(visual.album_art, QColor("#555555"), QColor("#777777"))
        visual.album_color = QColor(palette.primary)
        visual.secondary_color = QColor(palette.secondary)
        visual._lyric_presentation_store = None
        visual.track_title = title
        visual.artist = artist
        visual.lyrics = [lyric, lyric]
        visual.timed_lyrics = [(0.0, lyric), (10.0, lyric)]
        visual.media_position_sampled_at = 100.0
        visual.media_position_observed_seconds = 2.0
        visual.media_position_seconds = 2.0
        visual.finder_bar_style = "fade"
        visual.finder_bar_rect = QRectF(0, 0, 360, 30)
        visual.finder_bar_enabled = True
        visual.taskbar_follow_base_rect = QRectF(0, 0, 360, 40)
        visual.taskbar_material_color = QColor("#273033")
        visual.finder_material.mid_color = QColor("#273033")
        visual.taskbar_selected_drop_zone = QRectF()
        for mode, width, height in (("free", 360, 46), ("top", 360, 62),
                                    ("tasker", 300, 40), ("finder", 360, 30)):
            visual.location_mode = mode
            visual.finder_mode = mode == "finder"
            visual.mydockfinder_running = mode == "finder"
            image = QImage(width * 2, height * 2, QImage.Format.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(2.0)
            image.fill(Qt.GlobalColor.transparent)
            with patch("local.island_stack_visual.time.monotonic", return_value=100.0), QPainter(image) as painter:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                visual._paint_music(painter, QRectF(0, 0, width, height), False)
            filename = f"{key}-{mode}.png"
            target = island_out / filename
            assert image.save(str(target))
            island_records.append({
                "file": filename,
                "logical_size": [width, height],
                "dpr": 2,
                "ambience_style": "solid_album",
                "source": "local/island_stack_visual.py::_paint_music",
            })
    finally:
        fixture.tearDown()

    still_renderer = AlbumFlowBackgroundRenderer()
    still = still_renderer.render(BACKGROUND_SIZE, palette.ambient_colors, 100.0,
                                  weights=palette.ambient_weights)
    still_path = out / f"{key}-background-still.webp"
    assert still.save(str(still_path), "WEBP", 92)

    legacy_renderer = AlbumFlowBackgroundRenderer()
    legacy_frames = []
    for index in range(96):
        frame = legacy_renderer.render(BACKGROUND_SIZE, palette.ambient_colors,
                                       100.0 + index / 8.0, weights=palette.ambient_weights)
        frame = frame.convertToFormat(QImage.Format.Format_RGBA8888)
        legacy_frames.append(Image.frombytes("RGBA", (frame.width(), frame.height()), bytes(frame.bits())).convert("RGB"))
    legacy_sequence = legacy_frames + legacy_frames[-2:0:-1]
    legacy_path = out / f"{key}-background.webp"
    legacy_sequence[0].save(legacy_path, save_all=True, append_images=legacy_sequence[1:],
                            duration=125, loop=0, quality=90, method=4)

    video_path = out / f"{key}-background.mp4"
    video_record = export_h264(AlbumFlowBackgroundRenderer(), palette.ambient_colors,
                               palette.ambient_weights, video_path)
    background_records.append({
        "tone": key,
        "still": {"file": still_path.name, "sha256": sha256(still_path), "bytes": still_path.stat().st_size},
        "legacy_webp_fallback": {"file": legacy_path.name, "sha256": sha256(legacy_path), "bytes": legacy_path.stat().st_size,
                                 "sampled_fps": 8, "forward_samples": 96, "loop_frames": len(legacy_sequence)},
        "video": video_record,
        "source": "local/music_surface_background.py::AlbumFlowBackgroundRenderer.render",
    })

(island_out / "provenance.json").write_text(json.dumps({
    "description": "Production Qt painter; supplied public artwork and silent fixed lyric position; no account/audio/network/desktop capture",
    "assets": island_records,
}, ensure_ascii=False, indent=2), encoding="utf-8")
(out / "background-provenance.json").write_text(json.dumps({
    "description": "Production album-flow renderer sampled offline; sampled FPS describes generated frames, not screen FPS.",
    "video_exported": bool(ffmpeg),
    "ffmpeg_version": subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, check=True).stdout.splitlines()[0] if ffmpeg else None,
    "assets": background_records,
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"island_exports": len(island_records), "background_exports": background_records}, ensure_ascii=False))
