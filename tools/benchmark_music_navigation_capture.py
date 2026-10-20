"""Compare production full-DPR capture with a bounded QWidget.render probe.

This is an exploratory startup-cost comparison, not an application FPS test.
The candidate is confined to this tool; production capture is never replaced.
Native Qt widgets stay off screen with in-memory account/artwork values, no
network service, settings store, media engine, receiver or real credential.
Run in a separate process for each QT_SCALE_FACTOR (1 and 2).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QIODevice, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from api.models import ColorToken, TrackData
from api.online_music import AccountSnapshot
from local.music_library_window import LibraryCollection, MusicLibraryWindow
from local.music_navigation_transition import capture_navigation_frame


def image_art():
    image = QImage(320, 320, QImage.Format.Format_ARGB32_Premultiplied)
    with QPainter(image) as painter:
        field = QLinearGradient(0, 0, 320, 320)
        field.setColorAt(0, QColor("#9373b3"))
        field.setColorAt(1, QColor("#dc9572"))
        painter.fillRect(image.rect(), field)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return image, bytes(buffer.data())


def direct_bounded_grab(window):
    """Render the existing widgets at the same final physical raster limit."""
    dpr = float(window.devicePixelRatioF())
    native = QSize(round(window.width() * dpr), round(window.height() * dpr))
    size = native.scaled(QSize(1600, 1600), Qt.AspectRatioMode.KeepAspectRatio) if max(native.width(), native.height()) > 1600 else native
    image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(dpr)
    image.fill(Qt.GlobalColor.transparent)
    with QPainter(image) as painter:
        painter.scale(size.width() / max(1, window.width() * dpr),
                      size.height() / max(1, window.height() * dpr))
        window.render(painter, QPoint())
    return QPixmap.fromImage(image)


def capture(window, candidate):
    with patch("local.music_library_window.time.monotonic", return_value=100.):
        if candidate:
            with patch.object(window, "grab", side_effect=lambda: direct_bounded_grab(window)):
                frame = capture_navigation_frame(window, separate_cover=True, freeze_cover=True)
        else:
            frame = capture_navigation_frame(window, separate_cover=True, freeze_cover=True)
    if frame is None:
        raise AssertionError("Capture failed")
    frame.release()
    return frame.image


def timing(samples):
    ordered = sorted(samples)
    return {"mean_ms": statistics.mean(samples), "p50_ms": statistics.median(samples),
            "p95_ms": ordered[math.ceil(len(samples) * .95) - 1], "max_ms": max(samples)}


def pixel_comparison(first, second):
    if first.size() != second.size() or first.devicePixelRatio() != second.devicePixelRatio():
        raise AssertionError("Candidate capture dimensions/DPR differ")
    first_rgba = first.convertToFormat(QImage.Format.Format_RGBA8888)
    second_rgba = second.convertToFormat(QImage.Format.Format_RGBA8888)
    a, b = bytes(first_rgba.constBits()), bytes(second_rgba.constBits())
    differences = [abs(x - y) for x, y in zip(a, b)]
    return {"identical_bytes": a == b, "mean_absolute_rgba_255": sum(differences) / len(differences),
            "max_channel_difference_255": max(differences),
            "fraction_channels_over_8": sum(value > 8 for value in differences) / len(differences)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expect-dpr", type=float, required=True)
    parser.add_argument("--samples", type=int, default=18)
    args = parser.parse_args()
    if not 8 <= args.samples <= 60:
        parser.error("samples must be between 8 and 60")
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    if app.platformName() != "windows":
        raise RuntimeError("Run with native Windows Qt")
    callbacks = []
    hook = sys.excepthook
    sys.excepthook = lambda kind, value, tb: callbacks.append(str(value))
    window = MusicLibraryWindow()
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    window.set_background_context({"v2_music_flow_intensity": .74, "v2_animation_fps": 60}.get,
                                  lambda _name, prepare: prepare())
    window.set_account(AccountSnapshot("netease", True, "capture-fixture", "离线捕获验收", state="verified"))
    image, encoded = image_art()
    window.set_now_playing(TrackData("真实组件捕获验收", "离线样例", (), ColorToken(160, 96, 175),
        ColorToken(60, 120, 145), (), album_art_bytes=encoded, track_id="owned:netease:offline-capture"))
    collections = tuple(LibraryCollection(f"fixture-{index}", "playlist", f"离线歌单 {index + 1}", "", str(index))
                        for index in range(24))
    window.set_collections(collections)
    window.set_library_expanded(True)
    for item in collections:
        window.set_artwork(item.key, image)
    report = {"at_utc": datetime.now(timezone.utc).isoformat(), "native_qt_platform": app.platformName(),
              "offscreen_widgets": True, "samples_per_method": args.samples, "callbacks": callbacks,
              "scope": "Exploratory capture startup cost only; alternating original full-DPR grab/bound and direct bounded QWidget.render; no production capture change or physical FPS claim",
              "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                for name in ("local/music_library_window.py", "local/music_navigation_transition.py")},
              "scenarios": []}
    try:
        window.show()
        app.processEvents()
        window._header_motion.setCurrentTime(window.HEADER_ANIMATION_MS)
        if abs(window.devicePixelRatioF() - args.expect_dpr) > .01:
            raise AssertionError("Unexpected Qt DPR")
        for width, height in ((330, 400), (960, 540), (1280, 800), (1920, 1080)):
            window.resize(width, height)
            app.processEvents()
            for timer in window.findChildren(QTimer):
                timer.stop()
            capture(window, False)
            capture(window, True)
            samples = {False: [], True: []}
            frames = {}
            for index in range(args.samples):
                for candidate in ((False, True) if index % 2 == 0 else (True, False)):
                    started = time.perf_counter_ns()
                    frames[candidate] = capture(window, candidate)
                    samples[candidate].append((time.perf_counter_ns() - started) / 1e6)
            name = f"{window.width()}x{window.height()}-dpr{args.expect_dpr:g}"
            for candidate, suffix in ((False, "full-grab"), (True, "direct-render")):
                if not frames[candidate].save(str(args.output / (name + "-" + suffix + ".png"))):
                    raise RuntimeError("Cannot save captured image")
            record = {"scenario": name, "logical_size": [window.width(), window.height()],
                      "physical_capture_size": [frames[False].width(), frames[False].height()],
                      "dpr": frames[False].devicePixelRatio(),
                      "full_grab_then_bound": timing(samples[False]), "direct_bounded_render": timing(samples[True]),
                      "pixels": pixel_comparison(frames[False], frames[True])}
            report["scenarios"].append(record)
            print(json.dumps(record, ensure_ascii=True), flush=True)
        if callbacks:
            raise RuntimeError("Qt callback exception")
        report["passed"] = True
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
        sys.excepthook = hook
        (args.output / "evidence.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
