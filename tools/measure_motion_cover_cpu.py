"""Offscreen CPU estimate: static cover paint versus a looping motion cover.

Qt Multimedia decodes with FFmpeg inside this process. The sample is the
process utime+stime over a short paint loop, not a Windows playback trace.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, QUrl
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
from PySide6.QtWidgets import QApplication


def cpu_seconds() -> float:
    rest = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
    ticks = int(rest[11]) + int(rest[12])
    return ticks / os.sysconf("SC_CLK_TCK")


def paint_cover(target: QImage, frame: QImage) -> None:
    rect = QRectF(0, 0, target.width(), target.height())
    with QPainter(target) as painter:
        path = QPainterPath()
        path.addRoundedRect(rect, rect.width() * 0.055, rect.height() * 0.055)
        painter.setClipPath(path)
        painter.drawImage(rect, frame)


def sample(seconds: float, paint, fps: float = 30.0):
    """Pace like a cover repaint, so a tight loop does not hide the decoder."""
    interval = 1.0 / fps
    started = time.monotonic()
    cpu = cpu_seconds()
    frames = 0
    next_tick = started
    while True:
        now = time.monotonic()
        if now - started >= seconds:
            break
        paint()
        frames += 1
        QApplication.processEvents()
        next_tick += interval
        delay = next_tick - time.monotonic()
        if delay > 0:
            time.sleep(delay)
    elapsed = max(0.001, time.monotonic() - started)
    used = max(0.0, cpu_seconds() - cpu)
    return used / elapsed * 100.0, used, frames, elapsed


def main() -> int:
    app = QApplication([])
    root = Path(os.environ.get("TMPDIR", "/tmp")) / "gluspot-motion-cpu"
    root.mkdir(parents=True, exist_ok=True)
    video = root / "cover.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=480x480:rate=30",
        "-t", "2", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "28",
        str(video),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    static_image = QImage(480, 480, QImage.Format.Format_RGB32)
    static_image.fill(QColor("#335577"))
    target = QImage(240, 240, QImage.Format.Format_RGB32)
    static_cpu, static_used, static_frames, static_elapsed = sample(3.0, lambda: paint_cover(target, static_image))

    player = QMediaPlayer()
    sink = QVideoSink()
    player.setVideoOutput(sink)
    player.setLoops(QMediaPlayer.Loops.Infinite)
    holder = {"frame": QImage(static_image), "decoded": 0}

    def on_frame(frame):
        image = frame.toImage()
        if not image.isNull():
            holder["decoded"] += 1
            side = 240
            if max(image.width(), image.height()) > side:
                image = image.scaled(side, side)
            holder["frame"] = image

    sink.videoFrameChanged.connect(on_frame)
    player.setSource(QUrl.fromLocalFile(str(video)))
    player.play()
    deadline = time.monotonic() + 3
    while player.mediaStatus() not in {
        QMediaPlayer.MediaStatus.BufferedMedia,
        QMediaPlayer.MediaStatus.LoadedMedia,
        QMediaPlayer.MediaStatus.EndOfMedia,
    } and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    error = player.error()
    video_cpu, video_used, video_frames, video_elapsed = sample(3.0, lambda: paint_cover(target, holder["frame"]))
    player.stop()
    print(f"static_cpu_percent={static_cpu:.2f} cpu_seconds={static_used:.3f} frames={static_frames} seconds={static_elapsed:.2f}")
    print(f"video_cpu_percent={video_cpu:.2f} cpu_seconds={video_used:.3f} frames={video_frames} seconds={video_elapsed:.2f}")
    print(f"video_minus_static_cpu_percent={video_cpu - static_cpu:.2f}")
    print(f"player_error={error} media_status={player.mediaStatus()} decoded_frames={holder['decoded']}")
    print("note=offscreen process CPU, FFmpeg inside this process, not a Windows playback trace")
    return 0


if __name__ == "__main__":
    sys.exit(main())
