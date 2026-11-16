"""Offscreen Linux UI fixture: timer callbacks, paint events and process CPU.

Run with the repository's Qt Python. Each scenario gets a fresh process, a
12-second settle and a 30-second sample. No accounts, settings, network, real
audio, video decoder or Windows provider are started. This is NOT a full-app
benchmark, OS wakeup count, compositor FPS or Windows Task Manager result.
All instrumentation lives here; production timers keep their own cadence.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time


SCENARIOS = ("island-empty", "island-paused", "island-playing",
             "player-paused", "player-playing", "hidden-paused")


def measure(args):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = "1"
    os.environ["GLUSPOT_BACKDROP_VARIANT"] = "frosted"
    os.environ["GLUSPOT_TRANSLATION_VARIANT"] = "classic"
    os.environ["GLUSPOT_FRAME_LOG"] = "0"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from PySide6.QtCore import (QAbstractAnimation, QByteArray, QBuffer, QEvent,
                               QEventLoop, QIODevice, QObject, Qt, QTimer, qVersion)
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication
    from api.models import ColorToken, TimedLyricLine
    from api.providers import EmptyIslandDataProvider, empty_visual_data
    from local.island_stack_visual import IslandStackVisual
    from local.liquid_glass_tasks import glass_pixel_tasks
    from local.spring_motion import SpringTicker

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    playing = args.scenario.endswith("-playing")
    empty = args.scenario == "island-empty"
    base = empty_visual_data()
    if not empty:
        cover = QImage(320, 320, QImage.Format.Format_RGB32)
        cover.fill(QColor(64, 100, 150))
        encoded = QByteArray()
        buffer = QBuffer(encoded)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not cover.save(buffer, "PNG"):
            raise RuntimeError("Could not prepare synthetic artwork")
        buffer.close()
        rows = tuple(TimedLyricLine(i * 6., f"Synthetic lyric line {i + 1} across the quiet water")
                     for i in range(48))
        base = replace(base, track=replace(
            base.track, title="Synthetic idle sample", artist="GluSpot fixture",
            lyrics=tuple(row.text for row in rows), timed_lyrics=rows,
            album_color=ColorToken(64, 100, 150), secondary_color=ColorToken(25, 40, 65),
            album_art_bytes=bytes(encoded), album_art_source="fixture",
            album_art_lookup_state="ready", lyrics_source="fixture",
            source_app="fixture", source_app_id="fixture", track_id="idle-fixture",
            position_source="fixture", position_confidence=1.,
            duration_seconds=288., position_seconds=24., is_playing=playing))
    provider = EmptyIslandDataProvider(base)
    original_get = provider.get_visual_data
    epoch = time.monotonic()
    counts = Counter()
    sampling = False

    def observed_get():
        # Synthetic time is supplied at the existing provider boundary; no
        # extra position timer or real media service is introduced.
        if sampling:
            counts["provider_reads"] += 1
        data = original_get()
        if empty:
            return data
        now = time.monotonic()
        position = 24. + (now - epoch if playing else 0.)
        return replace(data, track=replace(data.track, position_seconds=position,
                       position_observed_seconds=position, position_sampled_at=now))

    provider.get_visual_data = observed_get
    owner = IslandStackVisual(data_provider=provider)
    owner.set_location_mode("free", notify=False)
    owner.settings_getter = lambda _key, default=None: default
    surface = None
    objects = {"island": owner, "shared_springs": SpringTicker.instance()}
    if args.scenario.startswith("player-"):
        from _demos.narrow3.narrow_window import NarrowStudyWindow
        surface = NarrowStudyWindow(owner, "B", "left", live=True)
        owner.music_fullscreen_overlay = surface
        owner._music_surface_active = True
        surface.resize(960, 540)
        surface.show()
        owner.hide()
        objects["player"] = surface
    elif args.scenario == "hidden-paused":
        # Exercise the actual hide lifecycle rather than an unshown widget.
        owner.show()
        owner.hide()
    else:
        owner.show()

    labels = {id(obj): label for label, obj in objects.items()}

    class PaintCounter(QObject):
        def eventFilter(self, watched, event):
            if sampling and event.type() == QEvent.Type.Paint:
                name = labels.get(id(watched), type(watched).__name__)
                counts["paint:" + name] += 1
            return False

    observer = PaintCounter(app)
    app.installEventFilter(observer)

    def event_loop(seconds):
        loop = QEventLoop()
        stop = QTimer()
        stop.setTimerType(Qt.TimerType.PreciseTimer)
        stop.setSingleShot(True)
        stop.timeout.connect(loop.quit)
        stop.start(round(seconds * 1000))
        loop.exec()
        stop.stop()

    timers = {}
    callbacks = []
    try:
        event_loop(args.settle)
        # Keep zero-count timers, so stopped shared springs/study clocks are
        # visible in the result. Enumerate only known fixture-owned QTimers;
        # this deliberately excludes Qt-internal/basic timers and workers.
        for label, obj in objects.items():
            names = {id(value): key for key, value in vars(obj).items()
                     if isinstance(value, QTimer)}
            for index, timer in enumerate(obj.findChildren(QTimer)):
                name = label + "." + names.get(id(timer), f"child_timer_{index}")
                timers[name] = timer
                def fired(name=name):
                    if sampling:
                        counts["timer:" + name] += 1
                timer.timeout.connect(fired)
                callbacks.append((timer, fired))
        initial = {name: {"active": timer.isActive(), "interval_ms": timer.interval()}
                   for name, timer in timers.items()}
        service = glass_pixel_tasks(create=False)
        glass_before = service.submitted if service is not None else 0
        cpu_start = time.process_time()
        start = time.perf_counter()
        sampling = True
        event_loop(args.seconds)
        sampling = False
        elapsed = time.perf_counter() - start
        cpu = time.process_time() - cpu_start
        timer_results = {
            name: {"count": counts["timer:" + name],
                   "hz": round(counts["timer:" + name] / elapsed, 3),
                   "start": initial[name], "end_active": timer.isActive(),
                   "end_interval_ms": timer.interval()}
            for name, timer in sorted(timers.items())}
        paints = {key[6:]: value for key, value in sorted(counts.items()) if key.startswith("paint:")}
        service = glass_pixel_tasks(create=False)
        animated = sum(a.state() == QAbstractAnimation.State.Running
                       for obj in objects.values() for a in obj.findChildren(QAbstractAnimation))
        result = {
            "scope": "offscreen Linux UI fixture; not Windows or full LocalIslandApp",
            "scenario": args.scenario, "qt": qVersion(), "platform": app.platformName(),
            "screen_hz": app.primaryScreen().refreshRate(), "dpr": owner.devicePixelRatioF(),
            "settle_seconds": args.settle, "sample_seconds": round(elapsed, 3),
            "cpu_seconds": round(cpu, 4), "cpu_percent_one_core": round(cpu / elapsed * 100., 3),
            "playing": playing, "background": "frosted (music page); default island ambience",
            "video_cover": False, "audio_output": False, "full_app_services": False,
            "widgets": {label: {"visible": obj.isVisible(), "minimized": obj.isMinimized(),
                                  "size": [obj.width(), obj.height()]}
                        for label, obj in objects.items() if label != "shared_springs"},
            "timers": timer_results, "paints": paints,
            "paint_events_total": sum(paints.values()),
            "provider_reads": counts["provider_reads"],
            "shared_spring_subscribers": objects["shared_springs"].subscriber_count,
            "running_qt_animations_at_end": animated,
            "glass_jobs_submitted": (service.submitted if service is not None else 0) - glass_before,
        }
        if counts["timer:island.timer"] == 0:
            raise RuntimeError("Fixture owner clock never fired")
        if surface is not None and counts["timer:player._timer"] == 0:
            raise RuntimeError("Fixture player clock never fired")
        if args.scenario == "hidden-paused" and sum(paints.values()):
            raise RuntimeError("Hidden fixture unexpectedly painted")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    finally:
        sampling = False
        for timer, callback in callbacks:
            timer.timeout.disconnect(callback)
        app.removeEventFilter(observer)
        if surface is not None:
            surface.shutdown()
            surface.hide()
        owner.timer.stop()
        owner.close()
        service = glass_pixel_tasks(create=False)
        if service is not None:
            service.close()
        SpringTicker.reset_for_tests()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("all", *SCENARIOS), default="all")
    parser.add_argument("--settle", type=float, default=12.)
    parser.add_argument("--seconds", type=float, default=30.)
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("This harness reports offscreen Linux only")
    if not (0 < args.settle <= 120 and 0 < args.seconds <= 300):
        parser.error("Require 0 < settle <= 120 and 0 < seconds <= 300")
    if args.scenario == "all":
        for scenario in SCENARIOS:
            print(f"offscreen Linux: starting {scenario}", flush=True)
            subprocess.run([sys.executable, str(Path(__file__).resolve()), "--scenario", scenario,
                            "--settle", str(args.settle), "--seconds", str(args.seconds)], check=True)
    else:
        measure(args)


if __name__ == "__main__":
    main()
