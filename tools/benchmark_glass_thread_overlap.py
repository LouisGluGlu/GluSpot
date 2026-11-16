"""Probe PySide painter GIL behavior and same-frame thread overlap.

Run on an otherwise idle machine with QT_QPA_PLATFORM=offscreen. These are
microbenchmarks, not window-paint or presentation timings. The background
case intentionally assumes its inputs are already available before the
painter setup; the current window does not yet have that ordering.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import statistics
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from local.glass_style import GLASS_CLEAR
from local.music_control_glass import _shade
from local.music_surface_background import AlbumFlowBackgroundRenderer


def gil_probe():
    target = QImage(4000, 4000, QImage.Format.Format_ARGB32_Premultiplied)
    source = QImage(4000, 4000, QImage.Format.Format_ARGB32_Premultiplied)
    source.fill(QColor(100, 100, 100, 127))
    for _ in range(5):
        start = threading.Event()
        count = [0]

        def worker():
            start.wait()
            for _ in range(300000):
                count[0] += 1

        thread = threading.Thread(target=worker)
        thread.start()
        old_interval = sys.getswitchinterval()
        try:
            # Prevent the worker from starting between set() and drawImage().
            sys.setswitchinterval(1.0)
            painter = QPainter(target)
            start.set()
            begun = time.perf_counter()
            painter.drawImage(QRectF(0, 0, 4000, 4000), source)
            elapsed = (time.perf_counter() - begun) * 1000
            iterations = count[0]
            painter.end()
        finally:
            sys.setswitchinterval(old_interval)
            thread.join()
        print(f'drawImage {elapsed:.2f} ms, Python worker iterations: {iterations}')


def measure(label, work, paint, samples):
    with ThreadPoolExecutor(max_workers=1) as pool:
        for parallel in (False, True):
            timings = []
            for index in range(samples + 20):
                begun = time.perf_counter()
                if parallel:
                    future = pool.submit(work)
                    paint()
                    future.result()
                else:
                    work()
                    paint()
                if index >= 20:
                    timings.append((time.perf_counter() - begun) * 1000)
            p95 = sorted(timings)[round(.95 * (len(timings) - 1))]
            mode = 'threaded' if parallel else 'serial'
            print(f'{label} {mode}: median {statistics.median(timings):.3f} ms, p95 {p95:.3f} ms')


def overlap_probes(samples):
    scene = QImage(960, 540, QImage.Format.Format_RGB32)
    scene.fill(QColor(94, 121, 151))
    target = QImage(960, 540, QImage.Format.Format_ARGB32_Premultiplied)
    bounds, rect = QRectF(0, 0, 960, 540), QRectF(100, 100, 80, 80)
    key = (160, 160, 40., 2., GLASS_CLEAR, None)

    def shade():
        return _shade(scene, bounds, rect, 2., GLASS_CLEAR.at_level(50), key, (255, 255, 255))

    def draw_scene():
        with QPainter(target) as painter:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            for _ in range(3):
                painter.drawImage(bounds, scene)

    colors = tuple(QColor(*rgb) for rgb in ((30, 55, 85), (170, 85, 45),
                                            (42, 140, 190), (110, 50, 95), (45, 90, 70)))

    def field():
        return AlbumFlowBackgroundRenderer._paint_field_pixels(
            QSize(256, 144), colors, (1., .8, .6, .7), 17.5)

    def setup_painter():
        with QPainter(target) as painter:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(target.rect(), Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setClipRect(target.rect())

    measure('shade + drawing', shade, draw_scene, samples)
    measure('field + painter setup', field, setup_painter, samples)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, default=250)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error('--samples must be positive')
    app = QApplication.instance() or QApplication([])
    gil_probe()
    overlap_probes(args.samples)
    return app


if __name__ == '__main__':
    main()
