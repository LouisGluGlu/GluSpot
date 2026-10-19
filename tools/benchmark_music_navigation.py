"""Compare direct DPR-stage scene composition with the bounded production path.

Uses only synthetic, hidden Qt surfaces: no accounts, media devices, native
input or application settings. Raster DPR is explicitly varied on the same
host. The reference shares the unchanged geometry/layer helpers and bypasses
only the new intermediate scene canvas. Timings include QWidget.render.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication

from local.music_navigation_transition import MusicNavigationTransition, NavigationFrame


def artwork(width, height, *, foreground=False):
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    with QPainter(image) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not foreground:
            gradient = QLinearGradient(0, 0, width, height)
            gradient.setColorAt(0, QColor("#372c58"))
            gradient.setColorAt(.5, QColor("#bf7656"))
            gradient.setColorAt(1, QColor("#2c6470"))
            painter.fillRect(image.rect(), gradient)
        for row in range(8):
            y = 55 + row * 96
            color = QColor("#ffffff")
            color.setAlpha(80 if foreground else 20)
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(90, y, width * .65, 55), 17, 17)
            if foreground:
                painter.setPen(QColor("#ffffff"))
                painter.drawText(QRectF(118, y + 8, width * .5, 35),
                                 Qt.AlignmentFlag.AlignVCenter, "GluSpot music library — track %02d" % row)
    return image


class BenchmarkTransition(MusicNavigationTransition):
    def __init__(self, raster_dpr, reference):
        self.raster_dpr = raster_dpr
        self.reference = reference
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)

    def devicePixelRatioF(self):
        return self.raster_dpr

    def _paint_cover_handoff(self, painter):
        if not self.reference:
            return super()._paint_cover_handoff(painter)
        f, p = self._fraction, self._cover_progress()
        offset = QPointF(self.geometry().topLeft())
        window = self._rect_between(self._origin_frame.geometry, self._target_frame.geometry, p).translated(-offset)
        clip = QPainterPath()
        clip.addRoundedRect(window, 24., 24.)
        painter.setClipPath(clip)
        self._paint_handoff_scene(painter, window, offset, f, p)
        self._paint_handoff_cover(painter, offset, p)


def transition(raster_dpr, reference, reverse):
    window = BenchmarkTransition(raster_dpr, reference)
    background = artwork(1600, 900)
    foreground = artwork(1600, 900, foreground=True)
    cover = artwork(640, 640)
    library = NavigationFrame(QRect(180, 100, 1100, 780), foreground,
                              QRectF(230, 150, 80, 80), cover, 350., background, cover_roundness=.5)
    playback = NavigationFrame(QRect(0, 0, 1920, 1080), foreground,
                               QRectF(190, 240, 520, 520), cover, 0., background)
    origin, target = (playback, library) if reverse else (library, playback)
    window._origin_frame, window._target_frame = origin, target
    window._origin, window._target = origin.image, target.image
    window._cover_transition = True
    window.setGeometry(origin.geometry.united(target.geometry))
    window._prepare_scene_canvas()
    return window


def measure(raster_dpr, reference, reverse, frames, destination):
    window = transition(raster_dpr, reference, reverse)
    image = QImage(round(window.width() * raster_dpr), round(window.height() * raster_dpr),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(raster_dpr)
    times = []
    try:
        for index in range(frames + 6):
            window._fraction = ((index - 6) % frames + 1) / (frames + 1)
            image.fill(Qt.GlobalColor.transparent)
            started = time.perf_counter_ns()
            window.render(image)
            elapsed = (time.perf_counter_ns() - started) / 1e6
            if index >= 6:
                times.append(elapsed)
        window._fraction = .5
        image.fill(Qt.GlobalColor.transparent)
        window.render(image)
        image.save(str(destination))
        ordered = sorted(times)
        return {"mean_ms": statistics.mean(times), "p95_ms": ordered[max(0, math.ceil(len(times) * .95) - 1)],
                "max_ms": max(times), "frames": len(times),
                "scene_raster": [window._scene_canvas.width(), window._scene_canvas.height()]}, image
    finally:
        window.cancel()
        window.close()
        window.deleteLater()


def mean_visual_difference(first, second):
    first = first.scaled(192, 108, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    second = second.scaled(192, 108, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    total = 0
    for y in range(first.height()):
        for x in range(first.width()):
            a, b = first.pixelColor(x, y), second.pixelColor(x, y)
            total += abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue())
    return total / (first.width() * first.height() * 3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=48)
    args = parser.parse_args()
    if not 12 <= args.frames <= 240:
        parser.error("--frames must be between 12 and 240")
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    failures = []
    previous_hook = sys.excepthook
    sys.excepthook = lambda kind, value, tb: failures.append(str(value))
    report = {"synthetic_raster_dpr": True, "callbacks": failures, "scenarios": [],
              "scene_raster_limit": MusicNavigationTransition.SCENE_RASTER_LIMIT,
              "source_sha256": hashlib.sha256((ROOT / "local/music_navigation_transition.py").read_bytes()).hexdigest()}
    try:
        for dpr in (1., 2.):
            for reverse in (False, True):
                label = "dpr%d-%s" % (dpr, "return" if reverse else "enter")
                baseline, before = measure(dpr, True, reverse, args.frames, args.output / (label + "-before.png"))
                bounded, after = measure(dpr, False, reverse, args.frames, args.output / (label + "-after.png"))
                result = {"scenario": label, "direct_stage": baseline, "bounded_scene": bounded,
                          "p95_improvement_percent": (1 - bounded["p95_ms"] / baseline["p95_ms"]) * 100,
                          "midpoint_mean_rgb_difference_255": mean_visual_difference(before, after)}
                report["scenarios"].append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                app.processEvents()
        (args.output / "evidence.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        if failures:
            raise RuntimeError(failures)
    finally:
        sys.excepthook = previous_hook
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
