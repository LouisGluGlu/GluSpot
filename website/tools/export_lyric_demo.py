"""Export silent, transparent lyric demos with GluSpot's production Qt painter.

The user supplied these short lyric/translation excerpts. The display clock and
line times are synthetic demonstration data; no audio alignment is claimed.
Run using the application's Python environment; no live app or account is used.
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
from time import perf_counter
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "1"

SONGS = {
    "chace": ("The Rain Song", "Chace", "chace-belated-suffocation.jpg", [
        ("It’s been the same old thing", "一成不变"),
        ("Since it ended", "自结束时"),
        ("Since you left me", "自你离我而去"),
        ("Since I moved on", "自我继续前行"),
    ]),
    "alicks": ("Ibuprofen", "Alicks", "alicks-unwritten.jpg", [
        ("I get high and I get low", "我的意识在天空中翱翔"),
        ("It only matters which way we go", "我只关心往哪里走,不曾停留"),
        ("If I sigh or if I float", "我曾因世态炎凉而叹息,也曾漂浮于梦境"),
    ]),
}


def export_alpha_video(frames, path: Path, fps: int):
    """Encode the original native RGBA frames, then verify decoded alpha."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("Installed FFmpeg and FFprobe are required for the alpha WebM export")
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
               "-pixel_format", "rgba", "-video_size", "480x480", "-framerate", str(fps),
               "-i", "pipe:0", "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
               "-auto-alt-ref", "0", "-crf", "26", "-b:v", "0", "-threads", "2",
               "-deadline", "good", "-cpu-used", "3", str(path)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE)
    assert process.stdin is not None and process.stderr is not None
    try:
        for frame in frames:
            process.stdin.write(frame.tobytes())
    finally:
        process.stdin.close()
    error = process.stderr.read()
    if process.wait():
        raise RuntimeError(error.decode(errors="replace"))
    probe = subprocess.run([ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0",
                            "-show_streams", "-show_format", "-of", "json", str(path)],
                           check=True, capture_output=True)
    info = json.loads(probe.stdout)
    stream = info["streams"][0]
    assert stream["codec_name"] == "vp9"
    assert stream["tags"]["alpha_mode"] == "1"
    assert int(stream["nb_read_frames"]) == len(frames)
    duration = float(info["format"]["duration"])
    assert abs(duration - len(frames) / fps) < .01
    # FFmpeg's built-in VP9 decoder omits alpha. Force libvpx to verify
    # the actual auxiliary alpha bitstream rather than metadata alone.
    decoded = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-c:v", "libvpx-vp9",
                              "-ss", str(duration * .42), "-i", str(path), "-frames:v", "1",
                              "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"],
                             check=True, capture_output=True).stdout
    assert len(decoded) == 480 * 480 * 4
    alpha = decoded[3::4]
    assert min(alpha) == 0 and max(alpha) > 0
    return {"file": path.name, "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "codec": "VP9", "alpha_mode": 1, "decoded_alpha_extrema": [min(alpha), max(alpha)],
            "decoded_frame_count": int(stream["nb_read_frames"]), "duration_seconds": duration,
            "encoding": "libvpx-vp9, yuva420p, auto-alt-ref=0, crf=26, b:v=0, threads=2"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "public/assets/lyrics")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--seconds", type=float, default=12)
    args = parser.parse_args()
    root = args.app_root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(root))

    from PIL import Image
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QFont, QImage
    from PySide6.QtWidgets import QApplication
    from local.island_stack_visual import IslandStackVisual
    from local.music_surface_window import MusicSurfaceWindow
    from tests.benchmark_music_surface import SyntheticOwner

    app = QApplication.instance() or QApplication([])
    IslandStackVisual.load_embedded_lyric_fonts()
    app.setFont(QFont(IslandStackVisual.WAVE_FONT_FAMILY_BOLD, 10))
    records = []
    for key, (title, artist, artwork, lines) in SONGS.items():
        for translated in (False, True):
            began = perf_counter()
            owner = SyntheticOwner("sparse")
            owner.media_track_id = f"website-lyric-demo-{key}"
            owner.track_title, owner.artist = title, artist
            owner.album_art = QImage(str(output.parent / artwork))
            owner.media_position_seconds = 0.0
            owner.media_epoch = 100.0
            owner.media_duration_seconds = args.seconds
            owner.enable_dual_line_lyrics = translated
            owner.timed_lyrics = [(index * args.seconds / len(lines), f"{text}\n翻译：{translation}")
                                  for index, (text, translation) in enumerate(lines)]
            if len(lines) <= 3:
                # Production centers the artwork for <=3 semantic rows.
                # Repeat the first supplied row as next-loop context rather
                # than bypassing that product policy or inventing a lyric.
                text, translation = lines[0]
                owner.timed_lyrics.append((args.seconds, f"{text}\n翻译：{translation}"))
            owner.timed_lyric_units = [()] * len(owner.timed_lyrics)
            owner.timed_lyric_timing_kinds = ["line"] * len(owner.timed_lyrics)
            window = MusicSurfaceWindow(owner)
            window.resize(960, 540)
            window._fullscreen = True
            window._timer.stop()
            window._audio_output_status_timer.stop()
            window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            window.set_content_layout("lyrics", animate=False)
            clock = [100.0]
            frames = []
            active_indexes = set()
            try:
                with ExitStack() as stack:
                    stack.enter_context(patch("local.music_surface_window.time.monotonic", side_effect=lambda: clock[0]))
                    # Suppress non-lyric decoration only; all lyric layout,
                    # timing, glyph shaping, progressive lift and blur remain
                    # inside the unmodified production _paint_surface_frame.
                    for method in ("_paint_background", "_paint_transport_spatial_light", "_paint_window_controls",
                                   "_paint_library_back", "_paint_personal_dj_control"):
                        stack.enter_context(patch.object(window, method, return_value=None))
                    for index in range(round(args.seconds * args.fps)):
                        clock[0] = 100.0 + index / args.fps
                        window._on_animation_tick()
                        image = QImage(1920, 1080, QImage.Format.Format_ARGB32_Premultiplied)
                        image.setDevicePixelRatio(2)
                        image.fill(Qt.GlobalColor.transparent)
                        window.render(image)
                        clip = QRectF(window._lyric_clip_rect)
                        # Take the full native side column with a square
                        # transparent viewport and retain the native spacing.
                        side = max(clip.width() + 24.0, clip.height())
                        crop = QRectF(clip.center().x() - side / 2, clip.center().y() - side / 2, side, side)
                        crop = QRectF(crop.x() * 2, crop.y() * 2, side * 2, side * 2).toAlignedRect()
                        rgba = image.copy(crop).scaled(480, 480, Qt.AspectRatioMode.IgnoreAspectRatio,
                                                       Qt.TransformationMode.SmoothTransformation)
                        rgba = rgba.convertToFormat(QImage.Format.Format_RGBA8888)
                        frames.append(Image.frombytes("RGBA", (480, 480), bytes(rgba.bits())))
                        active_indexes.add(window._lyric_motion.active_index)
                name = f"{key}-{'translated' if translated else 'original'}"
                animated = output / f"{name}.webp"
                poster = output / f"{name}.png"
                video = export_alpha_video(frames, output / f"{name}.webm", args.fps)
                durations = [round((i + 1) * 1000 / args.fps) - round(i * 1000 / args.fps) for i in range(len(frames))]
                frames[0].save(animated, save_all=True, append_images=frames[1:], duration=durations,
                               loop=0, lossless=False, quality=88, method=4, minimize_size=True)
                frames[min(len(frames) - 1, round(args.fps * args.seconds * .42))].save(poster)
                with Image.open(animated) as check:
                    # WebP coalesces identical hold frames while preserving
                    # their total display duration.
                    assert check.size == (480, 480) and 1 < check.n_frames <= len(frames)
                    encoded_frames = check.n_frames
                    assert check.info.get("loop") == 0
                    encoded_duration = 0
                    for frame_index in range(check.n_frames):
                        check.seek(frame_index)
                        check.load()
                        encoded_duration += check.info["duration"]
                    assert encoded_duration == sum(durations)
                    check.seek(check.n_frames // 2)
                    alpha = check.convert("RGBA").getchannel("A")
                    assert alpha.getextrema()[0] == 0 and alpha.getextrema()[1] > 0
                records.append({"song": key, "translated": translated, "animation": animated.name,
                                "video": video,
                                "poster": poster.name, "frames": len(frames), "fps": args.fps,
                                "duration_seconds": sum(durations) / 1000, "dimensions": [480, 480],
                                "encoded_frames": encoded_frames,
                                "logical_dimensions": [240, 240], "active_indexes": sorted(active_indexes),
                                "unique_frames": len({hashlib.sha256(frame.tobytes()).digest() for frame in frames}),
                                "animation_bytes": animated.stat().st_size,
                                "sha256": hashlib.sha256(animated.read_bytes()).hexdigest(),
                                "export_seconds": round(perf_counter() - began, 2)})
                assert active_indexes == set(range(len(lines))), (key, active_indexes)
                print(json.dumps(records[-1]), flush=True)
            finally:
                window.shutdown()
                owner.close()
                app.processEvents()
    provenance = {"renderer": "local.music_surface_window.MusicSurfaceWindow._paint_surface_frame",
                  "application_version": "6.417", "source": "user-provided screenshot lyric excerpts",
                  "timing": "Synthetic display clock with line intervals; production estimated word interpolation. Not audio aligned.",
                  "native_timing_kinds": "line (no supplied source word times)",
                  "loop_context": "For the three-row Alicks excerpt, the first supplied row repeats at 12 seconds as next-loop context. Production centers artwork at <=3 rows; no invented lyric was added.",
                  "rendering": "Offscreen Qt; actual production lyric layout/read lift/blur, background and controls omitted; alpha square crop at DPR 2",
                  "playback": "Silent visual demonstration; no audio, live account, network, or desktop capture",
                  "songs": SONGS, "exports": records}
    (output / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
