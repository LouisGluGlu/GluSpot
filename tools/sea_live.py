"""Live, isolated preview of the real expanded music island.

Space pauses cover rotation; 1-4 select a cover; S toggles simulated sound.
"""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import statistics
import shutil
import subprocess
import threading
from collections import deque
import sys
import tempfile
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Set before importing the application, including on Windows.
SANDBOX = tempfile.TemporaryDirectory(prefix="gluspot-sea-")
for key in ("HOME", "APPDATA", "LOCALAPPDATA"):
    os.environ[key] = SANDBOX.name

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget
from api.models import TimedLyricLine, AudioFrame
from api.audio_analyzer import LiveAudioAnalyzer
from local.owned_audio_bridge import OwnedAudioBridge
from api.providers import empty_visual_data
from local.island_stack_visual import IslandStackVisual
from local.sea_wave import sea_intensity, INTENSITY_LABELS

COVERS = [("深蓝", (28, 52, 110)), ("暖橙", (196, 108, 52)),
          ("墨绿", (30, 74, 58)), ("浅粉", (222, 170, 186))]
CURRENT, NEXT = "示例歌词 当前行", "下一行歌词"
RECT = QRectF(0, 0, 440, 200)


def cover_bytes(rgb):
    image = QImage(300, 300, QImage.Format.Format_RGB32)
    p = QPainter(image)
    base = QColor(*rgb)
    gradient = QLinearGradient(0, 0, 300, 300)
    gradient.setColorAt(0, base.lighter(115))
    gradient.setColorAt(.55, base)
    gradient.setColorAt(1, base.darker(115))
    p.fillRect(image.rect(), gradient)
    p.setPen(QPen(QColor(255, 255, 255, 18), 2))
    for i in range(-300, 600, 24):
        p.drawLine(i, 0, i + 160, 300)
    p.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


class AudioSource:
    def __init__(self, path):
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QAudioFormat, QAudioBuffer
        self.player = QMediaPlayer()
        self.output = QAudioOutput()
        self.player.setAudioOutput(self.output)
        self.analyzer = LiveAudioAnalyzer()
        self.frames = deque()
        self.source = "owned-pcm"
        self.error = ""
        try:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg and sys.platform == "win32":
                candidate = Path(r"C:\Program Files\ffmpeg-9.0.1-essentials_build\bin\ffmpeg.exe")
                ffmpeg = str(candidate) if candidate.is_file() else None
            if not ffmpeg:
                raise FileNotFoundError("ffmpeg not found")
            decoded = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-f", "s16le",
                                      "-ac", "2", "-ar", "44100", "pipe:1"],
                                     check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
            fmt = QAudioFormat()
            fmt.setSampleRate(44100)
            fmt.setChannelCount(2)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            lock, smooth = threading.Lock(), [0.0, 0.0, 0.0]
            for offset in range(0, len(decoded), 176400):
                chunk = decoded[offset:offset + 176400]
                buffer = QAudioBuffer(QByteArray(chunk), fmt, round(offset / 176400 * 1000000))
                OwnedAudioBridge._append_band_frames(buffer, self.frames, lock, smooth)
        except (OSError, subprocess.CalledProcessError) as exc:
            self.error = str(exc)
        self.player.setSource(QUrl.fromLocalFile(str(path.resolve())))
        self.player.play()

    def frame(self):
        if self.analyzer.has_recent_real_frame():
            self.source = "system-capture"
            return self.analyzer.get_frame(is_playing=True, position_seconds=self.player.position() / 1000)
        self.source = "owned-pcm" if self.frames else "simulated"
        position = self.player.position() / 1000
        for start, end, frame in self.frames:
            if start <= position < end:
                return frame
        return AudioFrame(0.0, 0.0, 0.0) if self.frames else None

    def close(self):
        self.player.stop()
        self.analyzer.stop()


class Scene:
    def __init__(self, app):
        self.app = app
        self.audio = None
        self.frame = None
        self.data = empty_visual_data("Song", "Artist", "fixture.music")
        with patch("local.island_stack_visual.read_finder_material", return_value=SimpleNamespace(available=False)):
            self.visual = IslandStackVisual(SimpleNamespace(get_visual_data=lambda: self.data, get_audio_frame=lambda: self.frame, has_live_audio=lambda: self.frame is not None))
        self.visual.timer.stop()
        self.visual.hide()
        self.visual.music_ambience_style = "sea_level"
        self.visual.lyric_font_size = 22
        self.art = [cover_bytes(rgb) for _, rgb in COVERS]
        self.rows = {}
        original = self.visual._paint_expanded_lyric_row
        def record(painter, rect, text, *args):
            self.rows[text] = [rect.top(), rect.bottom()]
            return original(painter, rect, text, *args)
        self.visual._paint_expanded_lyric_row = record
        self.select(0)

    def select(self, index):
        self.index = index
        track = replace(self.data.track, title=f"Song {index}", artist="Artist", track_id=f"sea-{index}",
                        album_art_bytes=self.art[index], is_playing=True, position_source="windows-media",
                        duration_seconds=120, position_seconds=2,
                        timed_lyrics=(TimedLyricLine(0, CURRENT), TimedLyricLine(10, NEXT),
                                      TimedLyricLine(20, "远方还有一首歌")), lyrics_source="fixture")
        self.data = replace(self.data, track=track)
        self.visual._apply_visual_data(self.data)
        self.visual._solid_album_surface_palette()  # queue the real asynchronous artwork extraction

    def settle(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            self.app.processEvents()
            self.visual._solid_album_surface_palette()
            if (self.visual._solid_album_palette_cache_key == self.visual._solid_album_palette_requested_key
                    and self.visual._solid_album_palette_pending is None
                    and self.visual._solid_album_palette_worker_key is None):
                break
            time.sleep(.005)
        else:
            raise RuntimeError("artwork palette did not finish")
        # Let the real palette transition settle as well.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)

    def advance(self, t, sound=True):
        beat = (t * 2) % 1
        if self.audio:
            self.frame = self.audio.frame() if sound else AudioFrame(0, 0, 0)
        else:
            self.frame = AudioFrame(.25 + .65 * math.exp(-beat * 6),
                .30 + .25 * (.5 + .5 * math.sin(t * 1.7)),
                .18 + .30 * math.exp(-((beat + .5) % 1) * 9)) if sound else AudioFrame(0, 0, 0)
        self.visual.media_is_playing = True
        self.visual.media_no_audio = False
        self.visual.ambient_phase = t * .6
        self.visual._update_wave_values()

    def render(self, scale=1):
        image = QImage(440 * scale, 200 * scale, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(scale)
        image.fill(Qt.GlobalColor.transparent)
        self.rows.clear()
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.visual._paint_music(painter, RECT, True)
        painter.end()
        return image

    def measurements(self):
        renderer = self.visual._native_music_background_renderer()
        context = renderer.render_context("sea_level", expanded=True)
        points = renderer._sea_level_surface(context, RECT, True)
        sea, line = renderer._sea_level_colors(context)
        return {"target_rgb": COVERS[self.index][1], "solid_album_background": sea.getRgb()[:3],
                "line_rgb": line.getRgb()[:3], "water_y_center": points[len(points)//2].y(),
                "current_row_y": self.rows.get(CURRENT), "next_row_y": self.rows.get(NEXT),
                "target_current_ink_y_approx": [106, 125], "target_next_ink_y_approx": [146, 165]}


class Window(QWidget):
    def __init__(self, scene, log=None, bench=False):
        super().__init__()
        self.log = log
        self.bench = bench
        self.setWindowTitle("GluSpot 海平面预览")
        self.paint_times = []
        self.paint_at = []
        self.scene = scene
        self.resize(480, 240)
        self.start = time.monotonic()
        self.last_switch = self.start
        self.last_frame = self.start
        self.fps = 0.0
        self.rotate = True
        self.sound = True
        self.timer = QTimer(self, interval=16, timeout=self.tick)
        self.timer.start()

    def tick(self):
        now = time.monotonic()
        if self.rotate and now - self.last_switch >= 4:
            self.scene.select((self.scene.index + 1) % 4)
            self.last_switch = now
        self.scene.advance(now - self.start, self.sound)
        self.fps = .9 * self.fps + .1 / max(.001, now - self.last_frame)
        self.last_frame = now
        color = self.scene.visual._solid_album_background_color().getRgb()[:3]
        source = self.scene.audio.source if self.scene.audio else "simulated"
        low, mid, high = self.scene.visual.current_audio_bands
        self.setWindowTitle(f"GluSpot 海平面预览 · {INTENSITY_LABELS[sea_intensity(getattr(self.scene.visual, 'sea_wave_intensity', None))]} | {source} | {low:.2f} {mid:.2f} {high:.2f} | {self.fps:.1f} fps")
        self.update()

    def paintEvent(self, event):
        start = time.perf_counter()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(235, 235, 238))
        p.translate(20, 20)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.scene.rows.clear()
        self.scene.visual._paint_music(p, RECT, True)
        p.end()
        now = time.monotonic()
        if self.bench:
            self.paint_at.append(now)
            self.paint_times.append((time.perf_counter() - start) * 1000)
        if self.log:
            renderer = self.scene.visual._native_music_background_renderer()
            context = renderer.render_context("sea_level", expanded=True)
            points = renderer._sea_level_surface(context, RECT, True)
            source = self.scene.audio.source if self.scene.audio else "simulated"
            record = {"time": now - self.start, "source": source,
                      "bands": self.scene.visual.current_audio_bands,
                      "intensity": sea_intensity(getattr(self.scene.visual, "sea_wave_intensity", None)),
                      "impulse": getattr(getattr(self.scene.visual, "_sea_wave_state", None), "impulse", 0.0),
                      "water_y": [points[i].y() for i in (0, len(points)//4, len(points)//2, 3*len(points)//4, len(points)-1)]}
            self.log.write(json.dumps(record, ensure_ascii=False) + "\n")
            self.log.flush()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close()
        elif key == Qt.Key.Key_Space:
            self.rotate = not self.rotate
            self.last_switch = time.monotonic()
        elif key in (Qt.Key.Key_Q, Qt.Key.Key_W, Qt.Key.Key_E):
            self.scene.visual.sea_wave_intensity = {Qt.Key.Key_Q: "gentle", Qt.Key.Key_W: "medium", Qt.Key.Key_E: "strong"}[key]
        elif key == Qt.Key.Key_S:
            self.sound = not self.sound
        elif Qt.Key.Key_1 <= key <= Qt.Key.Key_4:
            self.scene.select(key - Qt.Key.Key_1)
            self.last_switch = time.monotonic()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", type=Path)
    parser.add_argument("--bench", type=int, default=0)
    parser.add_argument("--grab", type=Path)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--intensity", choices=("gentle", "medium", "strong"))
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    scene = Scene(app)
    scene.visual.sea_wave_intensity = sea_intensity(args.intensity)
    if args.audio:
        if not args.audio.is_file():
            parser.error(f"audio file does not exist: {args.audio}")
        scene.audio = AudioSource(args.audio)
        if scene.audio.error:
            print(f"PCM fallback unavailable: {scene.audio.error}", file=sys.stderr)
    log = args.log.open("w", encoding="utf-8", newline="\n") if args.log else None
    window = Window(scene, log, bool(args.bench))
    scene.advance(0.0)
    window.show()
    app.processEvents()
    if args.grab:
        args.grab.mkdir(parents=True, exist_ok=True)
        window.rotate = False
        for index in range(len(COVERS)):
            scene.select(index)
            scene.settle()
            window.tick()
            window.repaint()
            app.processEvents()
            if not window.grab().save(str(args.grab / f"sea_{index + 1}.png")):
                raise RuntimeError("window grab failed")
    if args.bench:
        window.rotate = False
        window.paint_times.clear()
        window.paint_at.clear()
        while len(window.paint_times) < args.bench:
            app.processEvents()
            time.sleep(.001)
        times = window.paint_times
        elapsed = window.paint_at[-1] - window.paint_at[0]
        print(f"window: median={statistics.median(times):.2f}ms p95={sorted(times)[math.ceil(len(times)*.95)-1]:.2f}ms fps={(len(times)-1)/elapsed if elapsed else 0:.1f}", flush=True)
    if args.shots or args.bench:
        if args.shots:
            args.shots.mkdir(parents=True, exist_ok=True)
        results = {}
        for index, (name, _) in enumerate(COVERS):
            scene.select(index)
            scene.settle()
            scene.advance(.25)
            # Surface phase uses monotonic time in product code; freeze it for reproducible shots.
            with patch("local.music_background_renderer.time", SimpleNamespace(monotonic=lambda: 100.0)):
                image = scene.render()
                measurement = scene.measurements()
                if args.shots:
                    image.save(str(args.shots / f"sea_{index + 1}.png"))
                    scene.render(2).save(str(args.shots / f"sea_{index + 1}_2x.png"))
            results[name] = measurement
            if args.bench:
                times = []
                for frame in range(args.bench):
                    start = time.perf_counter()
                    scene.advance(frame / 60)
                    scene.render()
                    times.append((time.perf_counter() - start) * 1000)
                ordered = sorted(times)
                print(f"{name}: median={statistics.median(times):.2f}ms p95={ordered[math.ceil(len(times)*.95)-1]:.2f}ms max={max(times):.2f}ms >16.7ms={sum(t > 16.7 for t in times)}", flush=True)
        output = json.dumps(results, ensure_ascii=False, indent=2)
        print(output)
        if args.shots:
            (args.shots / "measurements.json").write_text(output + "\n", encoding="utf-8", newline="\n")
    if not (args.shots or args.bench or args.grab):
        app.exec()
    window.close()
    if scene.audio:
        scene.audio.close()
    if log:
        log.close()
    scene.visual.close()


if __name__ == "__main__":
    main()
