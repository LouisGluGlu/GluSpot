"""Export GluSpot's native gold highlight for a user-authored demonstration.

All character clocks are authored fixture data, not recorded music timing.
No app source, live account, audio, network or desktop capture is involved.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "1"
PHRASE = "音乐高光，感知清晰！"
FPS, SECONDS = 30, 8
WIDTH, HEIGHT = 960, 300


def encode(frames, target):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("The installed official FFmpeg and FFprobe are required")
    process = subprocess.Popen([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pixel_format", "rgba", "-video_size", f"{WIDTH}x{HEIGHT}",
        "-framerate", str(FPS), "-i", "pipe:0", "-an", "-c:v", "libvpx-vp9",
        "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", "-crf", "26", "-b:v", "0",
        "-threads", "2", "-deadline", "good", "-cpu-used", "3", str(target)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    assert process.stdin is not None and process.stderr is not None
    try:
        for frame in frames:
            process.stdin.write(frame.tobytes())
    finally:
        process.stdin.close()
    error = process.stderr.read()
    if process.wait():
        raise RuntimeError(error.decode(errors="replace"))
    result = subprocess.run([ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0",
        "-show_streams", "-show_format", "-of", "json", str(target)], check=True, capture_output=True)
    info = json.loads(result.stdout)
    stream = info["streams"][0]
    assert stream["codec_name"] == "vp9" and stream["tags"]["alpha_mode"] == "1"
    assert int(stream["nb_read_frames"]) == FPS * SECONDS
    assert abs(float(info["format"]["duration"]) - SECONDS) < .01
    decoded = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-c:v", "libvpx-vp9",
        "-ss", "3.2", "-i", str(target), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"],
        check=True, capture_output=True).stdout
    assert len(decoded) == WIDTH * HEIGHT * 4
    alpha = decoded[3::4]
    assert min(alpha) == 0 and max(alpha) == 255
    return {"file": target.name, "bytes": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "alpha_mode": 1,
            "decoded_alpha_extrema": [min(alpha), max(alpha)], "decoded_frames": int(stream["nb_read_frames"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "public/assets/highlight")
    args = parser.parse_args()
    root, output = args.app_root.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(root))
    from PIL import Image, ImageDraw
    from PySide6.QtCore import QRect, QRectF, Qt
    from PySide6.QtGui import QFont, QImage
    from PySide6.QtWidgets import QApplication
    from api.models import TimedLyricUnit
    from local.island_stack_visual import IslandStackVisual
    from local.lyric_unit_timing import explicit_slow_unit_groups
    from local.music_surface_window import MusicSurfaceWindow
    from tests.benchmark_music_surface import SyntheticOwner

    app = QApplication.instance() or QApplication([])
    IslandStackVisual.load_embedded_lyric_fonts()
    app.setFont(QFont(IslandStackVisual.WAVE_FONT_FAMILY_BOLD, 10))
    owner = SyntheticOwner("sparse")
    owner.media_track_id, owner.track_title, owner.artist = "website-highlight", PHRASE, "GluSpot"
    owner.album_art = QImage(str(output.parent / "chace-belated-suffocation.jpg"))
    assert not owner.album_art.isNull()
    owner.media_epoch, owner.media_position_seconds = 100.0, 0.0
    owner.media_duration_seconds = 32.0
    owner.music_surface_reading_strategy = "gold"
    owner.music_surface_reading_lift_level = 4
    owner.enable_dual_line_lyrics = False
    owner.timed_lyrics = [(float(index * SECONDS), PHRASE) for index in range(4)]
    owner.timed_lyric_units = []
    authored_clocks = []
    for row in range(4):
        cursor, units = row * SECONDS, []
        for character in PHRASE:
            length = .9 if character.isalnum() else .2
            units.append(TimedLyricUnit(cursor, cursor + length, character))
            if row == 0:
                authored_clocks.append({"text": character, "start": round(cursor, 3), "end": round(cursor + length, 3)})
            cursor += length
        owner.timed_lyric_units.append(tuple(units))
    owner.timed_lyric_timing_kinds = ["unit"] * 4
    groups = explicit_slow_unit_groups(PHRASE, owner.timed_lyric_units[0])
    assert len(groups) == 2
    window = MusicSurfaceWindow(owner)
    window.resize(960, 540)
    window._fullscreen = True
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    window._timer.stop()
    window._audio_output_status_timer.stop()
    window.set_content_layout("lyrics", animate=False)
    clock, frames, modes = [100.0], [], set()
    try:
        with ExitStack() as stack:
            stack.enter_context(patch("local.music_surface_window.time.monotonic", side_effect=lambda: clock[0]))
            # Isolate the active native row for this typography specimen.
            # Its timing, priority dispatch, glyph wave, glow, scale and lift
            # execute unchanged inside the production surface painter.
            for method in ("_paint_background", "_paint_transport_spatial_light", "_paint_window_controls",
                           "_paint_library_back", "_paint_personal_dj_control",
                           "_draw_sprung_cached_wrapped_layout", "_draw_future_wrapped_layout"):
                stack.enter_context(patch.object(window, method, return_value=None))
            sparse = stack.enter_context(patch.object(window, "_paint_sparse_glyph_run", wraps=window._paint_sparse_glyph_run))
            def render():
                image = QImage(1920, 1080, QImage.Format.Format_ARGB32_Premultiplied)
                image.setDevicePixelRatio(2)
                image.fill(Qt.GlobalColor.transparent)
                window.render(image)
                return image
            # Prewarm the exact production glyph assets without advancing
            # the demonstration clock or displaying a cold fallback frame.
            for _ in range(80):
                window._on_animation_tick()
                render()
            native_row = window._lyric_hit_rects[0]
            crop = QRectF(window._lyric_clip_rect.left() - 13, native_row.top() - 56, 480, 150)
            crop = QRect(round(crop.x() * 2), round(crop.y() * 2), WIDTH, HEIGHT)
            native_width = window._song_layouts[0][0].lineAt(0).naturalTextWidth() * 2
            source_left = native_row.left() * 2 - crop.left()
            center_shift = round((WIDTH - native_width) / 2 - source_left)
            for index in range(FPS * SECONDS):
                clock[0] = 100 + index / FPS
                window._on_animation_tick()
                image = render().copy(crop).convertToFormat(QImage.Format.Format_RGBA8888)
                assert image.width() == WIDTH and image.height() == HEIGHT
                native = Image.frombytes("RGBA", (WIDTH, HEIGHT), bytes(image.bits()))
                centered = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
                centered.alpha_composite(native, (center_shift, 0))
                frames.append(centered)
                modes.add(window._last_lyric_emphasis_mode)
            assert sparse.call_count > FPS * SECONDS
            assert modes == {"scatter_glow"}, modes
        video = encode(frames, output / "music-highlight.webm")
        poster_index = 105
        frames[poster_index].save(output / "music-highlight.png")
        # A dark-background contact sheet is QA evidence, not a product skin.
        contact = Image.new("RGB", (WIDTH, HEIGHT * 4), "#252525")
        draw = ImageDraw.Draw(contact)
        for row, index in enumerate((20, 65, 105, 185)):
            composited = Image.alpha_composite(Image.new("RGBA", (WIDTH, HEIGHT), "#252525"), frames[index])
            contact.paste(composited.convert("RGB"), (0, row * HEIGHT))
            draw.text((12, row * HEIGHT + HEIGHT - 22), f"native {index / FPS:.2f}s", fill="white")
        contact.save(output / "qa-contact.png")
        unique = len({hashlib.sha256(frame.tobytes()).digest() for frame in frames})
        assert unique > 200
        record = {"phrase": PHRASE, "application_version": "6.417", "video": video,
            "poster": "music-highlight.png", "dimensions": [WIDTH, HEIGHT], "frames": len(frames),
            "fps": FPS, "duration_seconds": SECONDS, "unique_frames": unique,
            "native_strategy": "gold", "native_lift_level": 4, "native_mode": sorted(modes),
            "native_dispatch": "_paint_surface_frame -> explicit_slow_unit_groups -> _real_sparse_glyph_states -> _paint_sparse_glyph_run",
            "native_effect": "Production eight-stage glyph lift/scale/glow plus continuous read front",
            "timing": "Explicitly authored synthetic character clocks for this user-authored marketing phrase; not measured or aligned to music.",
            "authored_clocks": authored_clocks,
            "palette": "Production artwork_surface_palette from the existing Chace artwork; white read text and native artwork-derived unread tone",
            "isolation": "Non-active rows and non-lyric decoration omitted; native glyph renderer unchanged; 1:1 DPR2 pixel crop recentered as one group, no glyph scaling after render",
            "loop": "8-second clip replays the authored reading demonstration; end-to-start reset is intentional",
            "provenance": "User literal wording, actual production Qt renderer. No audio, account, network or desktop capture.",
            "integration": "Contains the entire phrase: use as visible headline, with accessible text/alt to avoid duplicate visible DOM wording. video loop muted playsInline; pause preserves currentTime; PNG for reduced motion."}
        (output / "provenance.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(record, ensure_ascii=True), flush=True)
    finally:
        window.shutdown()
        owner.close()
        app.processEvents()


if __name__ == "__main__":
    main()
