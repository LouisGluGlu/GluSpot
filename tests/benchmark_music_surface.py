"""Repeatable raster-paint benchmark for the full-screen music surface.

Run from the repository root, for example::

    .venv/Scripts/python tests/benchmark_music_surface.py --sizes 960x540,1920x1080
    .venv/Scripts/python tests/benchmark_music_surface.py --sizes 1920x1080 --idle-controls --dprs 1,2

This uses a synthetic owner and QImage targets; it neither opens a player nor
changes app settings. Results measure Qt/Python raster work, not compositor FPS.
Windows Qt is the default platform on Windows for faithful font metrics, but
no window is shown unless ``--event-loop-seconds`` is requested, and that
option requires ``--platform offscreen``. The ``--idle-controls`` run measures
the two disjoint control dirty rectangles and title/artist animation work.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import math
import os
from pathlib import Path
import pstats
import statistics
import sys
import time
from unittest.mock import patch

# Qt reads its platform choice while importing/initializing the application.
# Parse this one option before importing PySide6, then parse all options later.
DEFAULT_PLATFORM = "windows" if os.name == "nt" else "offscreen"
_early_parser = argparse.ArgumentParser(add_help=False)
_early_parser.add_argument("--platform", choices=("windows", "offscreen"), default=DEFAULT_PLATFORM)
SELECTED_PLATFORM = _early_parser.parse_known_args()[0].platform
os.environ["QT_QPA_PLATFORM"] = SELECTED_PLATFORM
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from api.models import TimedLyricUnit
from local.music_surface_window import MusicSurfaceWindow


class SyntheticOwner(QWidget):
    def __init__(self, scenario: str):
        super().__init__()
        self.media_track_id = f"benchmark-{scenario}"
        self.track_title = (
            "A very long track title that needs a smooth, readable five second panorama"
        )
        self.artist = "A collaboration between several artists with unusually long names"
        self.album_color = QColor(92, 51, 137)
        self.secondary_color = QColor(31, 35, 65)
        self.album_art = QImage(1200, 1200, QImage.Format.Format_RGB32)
        self.album_art.fill(QColor(91, 59, 125))
        self.media_is_playing = True
        self.music_flow_intensity = 0.74
        self.music_sparse_highlight_seconds_per_unit = 1.5
        self.music_surface_reading_lift_level = 2
        self.music_intro_cleanup_enabled = True
        self.enable_dual_line_lyrics = False
        self._music_windowed_preference = False
        self.media_epoch = 20.0
        self.media_position_seconds = 2.0
        self.lyrics = []
        if scenario == "sparse":
            self.timed_lyrics = [
                (0.0, "shine"),
                (5.0, "A later line gives the sparse glyphs their full duration"),
                (9.0, "Another ordinary line follows"),
                (13.0, "The closing line keeps the lyric surface visible"),
            ]
        elif scenario == "long-song":
            self.timed_lyrics = [
                (
                    float(index * 3),
                    f"Line {index} we were just two lovers, driving through the summer "
                    "and remembering every word of this long verse",
                )
                for index in range(180)
            ]
            self.media_position_seconds = 241.0
        elif scenario == "unit-slow":
            text = "Shine through a quiet moment while the music holds each word"
            self.timed_lyrics = [(float(index * 12), text) for index in range(24)]
            self.timed_lyric_units = []
            for started, _text in self.timed_lyrics:
                units = [TimedLyricUnit(started, started + 5.0, "Shine ")]
                remainder = text[len("Shine "):].split(" ")
                duration = 6.0 / len(remainder)
                for index, word in enumerate(remainder):
                    suffix = " " if index + 1 < len(remainder) else ""
                    unit_start = started + 5.0 + index * duration
                    units.append(TimedLyricUnit(unit_start, unit_start + duration, word + suffix))
                self.timed_lyric_units.append(tuple(units))
            self.timed_lyric_timing_kinds = ["unit"] * len(self.timed_lyrics)
            self.media_position_seconds = 84.0
        else:
            raise ValueError(scenario)

    def _estimated_media_position(self, now: float) -> float:
        elapsed = max(0.0, now - self.media_epoch) if self.media_is_playing else 0.0
        return self.media_position_seconds + elapsed

    def _run_background(self, _name, callback):
        callback()

    def _restore_island_from_music_surface(self):
        pass


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def summary(values: list[float]) -> str:
    if not values:
        return "no paints"
    return (
        f"n={len(values):3d} median={statistics.median(values):6.2f}ms "
        f"p95={percentile(values, 0.95):6.2f}ms "
        f"max={max(values):6.2f}ms "
        f">8.33ms={sum(value > 8.33 for value in values):3d} "
        f">16.67ms={sum(value > 16.67 for value in values):3d}"
    )


def benchmark_raster(
    app: QApplication, scenario: str, width: int, height: int, frames: int, warmup: int,
    *, profile: bool = False, profile_cold: bool = False, profile_switch: bool = False,
) -> None:
    owner = SyntheticOwner(scenario)
    window = MusicSurfaceWindow(owner)
    window.resize(width, height)
    window._fullscreen = True
    window._reset_control_idle(now=20.0)
    output = QImage(window.size(), QImage.Format.Format_RGB32)
    output.fill(Qt.GlobalColor.black)
    simulated_at = [20.0]
    try:
        with patch("local.music_surface_window.time.monotonic", side_effect=lambda: simulated_at[0]):
            cold_profiler = cProfile.Profile() if profile_cold else None
            if cold_profiler is not None:
                cold_profiler.enable()
            cold_start = time.perf_counter()
            window.render(output)
            cold_ms = (time.perf_counter() - cold_start) * 1000.0
            if cold_profiler is not None:
                cold_profiler.disable()
                print("        cold timing includes profiler overhead")
                report = io.StringIO()
                pstats.Stats(cold_profiler, stream=report).strip_dirs().sort_stats("cumtime").print_stats(25)
                print(report.getvalue())
            for index in range(warmup):
                simulated_at[0] += 1.0 / 60.0
                window._on_animation_tick()
                window.render(output)
            print(f"{scenario:9s} {width:4d}x{height:<4d} cold={cold_ms:7.2f}ms")
            for region_name in ("full", "lyric"):
                durations: list[float] = []
                profiler = cProfile.Profile() if profile and region_name == "lyric" else None
                if profiler is not None:
                    profiler.enable()
                for index in range(frames):
                    simulated_at[0] += 1.0 / 60.0
                    if region_name == "full":
                        render = lambda: window.render(output)
                    else:
                        region = QRegion(window._lyric_animation_rect)
                        render = lambda: window.render(output, region.boundingRect().topLeft(), region)
                    started = time.perf_counter()
                    render()
                    durations.append((time.perf_counter() - started) * 1000.0)
                if profiler is not None:
                    profiler.disable()
                    print("        profiling enabled: these render timings include profiler overhead")
                print(f"  {region_name:5s} render {summary(durations)}")
                if region_name == "lyric":
                    print(f"        lyric region={window._lyric_animation_rect.width()}x{window._lyric_animation_rect.height()}")
                if profiler is not None:
                    report = io.StringIO()
                    pstats.Stats(profiler, stream=report).strip_dirs().sort_stats("cumtime").print_stats(25)
                    print(report.getvalue())
            # Ensure the loop did exercise the sparse-glow branch, rather than
            # silently benchmarking its ordinary-lyric fallback.
            if scenario == "sparse":
                print(f"        emphasis={window._last_lyric_emphasis_mode} glow={window._active_glow_stage}")
            if profile_switch:
                owner.media_track_id = "benchmark-second-track"
                owner.track_title = "A different long song title with another animated panorama"
                owner.artist = "Another group of artists with a lengthy credit line"
                owner.timed_lyrics = [
                    (float(index * 3), f"Another line {index} crossing a new timeline "
                     "with enough text to wrap onto several visual rows")
                    for index in range(180)
                ]
                owner.album_color = QColor(56, 123, 85)
                owner.album_art = QImage(1200, 1200, QImage.Format.Format_RGB32)
                owner.album_art.fill(owner.album_color)
                simulated_at[0] += 1.0 / 60.0
                profiler = cProfile.Profile()
                profiler.enable()
                started = time.perf_counter()
                window.render(output)
                elapsed = (time.perf_counter() - started) * 1000.0
                profiler.disable()
                print(f"        warm-process track-switch frame={elapsed:.2f}ms (includes profiler overhead)")
                report = io.StringIO()
                pstats.Stats(profiler, stream=report).strip_dirs().sort_stats("cumtime").print_stats(25)
                print(report.getvalue())
    finally:
        window.shutdown()
        owner.close()
        app.processEvents()


def benchmark_idle_controls(app: QApplication, width: int, height: int, dpr: float) -> None:
    """Measure the 5-second hide and pointer-triggered return on two Qt regions."""
    owner = SyntheticOwner("long-song")
    window = MusicSurfaceWindow(owner)
    window.resize(width, height)
    window._fullscreen = True
    window._reset_control_idle(now=20.0)
    output = QImage(
        max(1, round(width * dpr)), max(1, round(height * dpr)),
        QImage.Format.Format_RGB32,
    )
    output.setDevicePixelRatio(dpr)
    output.fill(Qt.GlobalColor.black)
    surface = QRectF(window.rect())
    scale = window._surface_scale(surface)
    top = QRectF(surface.right() - 170 * scale, 0, 170 * scale, 100 * scale).toAlignedRect()
    bottom_height = window.CONTROL_BOTTOM_REGION_HEIGHT * scale
    bottom = QRectF(0, surface.bottom() - bottom_height, 505 * scale, bottom_height).toAlignedRect()
    region = QRegion(top).united(QRegion(bottom))
    area = top.width() * top.height() + bottom.width() * bottom.height()
    simulated_at = [20.0]
    field_costs: dict[str, list[float]] = {"title": [], "artist": [], "layout": []}
    original_layout = window._metadata_layout
    original_metadata = window._paint_scrolling_metadata

    def timed_layout(*args):
        started = time.perf_counter()
        result = original_layout(*args)
        field_costs["layout"].append((time.perf_counter() - started) * 1000.0)
        return result

    def timed_metadata(*args, **kwargs):
        started = time.perf_counter()
        result = original_metadata(*args, **kwargs)
        field_costs[kwargs["field"]].append((time.perf_counter() - started) * 1000.0)
        return result

    try:
        with (
            patch("local.music_surface_window.time.monotonic", side_effect=lambda: simulated_at[0]),
            patch.object(window, "devicePixelRatioF", return_value=dpr),
        ):
            window.render(output)
            lead_ticks: list[float] = []
            lead_paints: list[float] = []
            # Advance the actual five-second quiet period so control and
            # metadata assets are prepared before the hide transition.
            for sample in range(44):
                simulated_at[0] = 20.5 + sample * 0.1
                started = time.perf_counter()
                window._on_animation_tick()
                lead_ticks.append((time.perf_counter() - started) * 1000.0)
                started = time.perf_counter()
                window.render(output, QPoint(0, 0), region)
                lead_paints.append((time.perf_counter() - started) * 1000.0)
            window._metadata_layout = timed_layout
            window._paint_scrolling_metadata = timed_metadata
            print(
                f"idle controls {width}x{height} DPR={dpr:g} "
                f"dirty={area}/{width * height} pixels ({100 * area / (width * height):.1f}%) "
                f"regions={region.rectCount()}"
            )
            print(f"  lead-in animation tick {summary(lead_ticks)}")
            print(f"  lead-in render         {summary(lead_paints)}")
            for phase, start_at in (("hide", 25.0), ("restore", 26.0)):
                if phase == "restore":
                    simulated_at[0] = start_at
                    window._note_pointer_moved(now=start_at)
                for samples in field_costs.values():
                    samples.clear()
                render_costs: list[float] = []
                tick_costs: list[float] = []
                reveals: list[float] = []
                steps = math.ceil(window.FULLSCREEN_CONTROL_TRANSITION_SECONDS * 60) + 2
                for step in range(steps):
                    simulated_at[0] = start_at + step / 60.0
                    started = time.perf_counter()
                    window._on_animation_tick()
                    tick_costs.append((time.perf_counter() - started) * 1000.0)
                    started = time.perf_counter()
                    window.render(output, QPoint(0, 0), region)
                    render_costs.append((time.perf_counter() - started) * 1000.0)
                    reveals.append(window._control_reveal_at(simulated_at[0]))
                print(
                    f"  {phase:7s} control reveal {reveals[0]:.3f}->{reveals[-1]:.3f}; "
                    f"render {summary(render_costs)}"
                )
                print(f"          animation tick {summary(tick_costs)}")
                for field in ("layout", "title", "artist"):
                    print(f"          metadata {field:6s} {summary(field_costs[field])}")
    finally:
        window.shutdown()
        owner.close()
        app.processEvents()


def benchmark_event_loop(
    app: QApplication, scenario: str, width: int, height: int, seconds: float,
) -> None:
    owner = SyntheticOwner(scenario)
    owner.media_epoch = time.monotonic()
    window = MusicSurfaceWindow(owner)
    window.resize(width, height)
    window._fullscreen = True
    paints: list[tuple[float, float, bool]] = []
    original_record = window._record_paint_cost

    def record(started_at, event):
        original_record(started_at, event)
        painted = event.region().boundingRect()
        partial = painted.width() * painted.height() < width * height * 0.90
        paints.append((time.perf_counter(), window._recent_paint_costs[-1], partial))

    window._record_paint_cost = record
    try:
        window.show()
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            app.processEvents()
        paints = paints[1:]  # Exclude cold first-show cache fill.
        costs = [item[1] for item in paints]
        gaps = [(right[0] - left[0]) * 1000.0 for left, right in zip(paints, paints[1:])]
        print(f"event loop {scenario} {width}x{height}: paint cost {summary(costs)}")
        print(f"        paint-end intervals {summary(gaps)} partial={sum(item[2] for item in paints)}/{len(paints)}")
    finally:
        window.shutdown()
        owner.close()
        app.processEvents()


def benchmark_startup_gradient_sequence(app: QApplication, width: int, height: int) -> None:
    """Measure cold open, a 31-step gradient drag, and three fresh songs.

    Run this in a fresh process for a true first-font sample. Subsequent runs
    in one process reuse Qt's process-wide font initialization.
    """
    owner = SyntheticOwner("long-song")
    window = MusicSurfaceWindow(owner)
    window.resize(width, height)
    window._fullscreen = True
    output = QImage(window.size(), QImage.Format.Format_RGB32)
    output.fill(Qt.GlobalColor.black)
    try:
        for track_index in range(4):
            if track_index:
                owner.media_track_id = f"benchmark-sequence-{track_index}"
                owner.track_title = f"Sequence song {track_index}"
                owner.album_color = QColor(48 + track_index * 26, 52, 76)
                owner.album_art = QImage(1200, 1200, QImage.Format.Format_RGB32)
                owner.album_art.fill(owner.album_color)
                owner.timed_lyrics = [
                    (float(row * 5), f"Line {track_index}-{row} shine through a deep "
                     "and moving atmosphere again")
                    for row in range(180)
                ]
                owner.media_position_seconds = 185.0
            started = time.perf_counter()
            window.render(output)
            print(f"startup sequence song {track_index}: first raster {(time.perf_counter() - started) * 1000.0:.2f} ms")
            if track_index == 0:
                slider_costs = []
                for steps in range(31):
                    owner.music_surface_reading_gradient_steps = steps
                    started = time.perf_counter()
                    window.render(output)
                    slider_costs.append((time.perf_counter() - started) * 1000.0)
                print(f"startup sequence gradient 0-30: {summary(slider_costs)}")
                print(f"        cached reading spans={len(window._reading_feather_cache)}")
    finally:
        window.shutdown()
        owner.close()
        app.processEvents()


def benchmark_reading_cadence(
    app: QApplication, width: int, height: int, dpr: float, frames: int, warmup: int,
) -> None:
    """Replay timer-requested regions for a slow genuine word-timed lyric.

    The baseline skips the stable native-size metadata rasters. Both retain
    the same complete ambient refresh, unit timestamps, gradient, highlight
    wave, native DPR, and 8 ms clock.
    """
    for baseline in (True, False):
        owner = SyntheticOwner("unit-slow")
        window = MusicSurfaceWindow(owner)
        window.resize(width, height)
        window._fullscreen = True
        window._reset_control_idle(now=20.0)
        output = QImage(
            max(1, round(width * dpr)), max(1, round(height * dpr)),
            QImage.Format.Format_RGB32,
        )
        output.setDevicePixelRatio(dpr)
        output.fill(Qt.GlobalColor.black)
        simulated_at = [20.0]
        requests: list[tuple] = []
        window.update = lambda *regions: requests.append(regions)

        if baseline:
            window._prewarm_idle_metadata_raster = lambda: None
        costs: list[float] = []
        ticks: list[float] = []
        full_paints = 0
        try:
            with (
                patch("local.music_surface_window.time.monotonic", side_effect=lambda: simulated_at[0]),
                patch.object(window, "devicePixelRatioF", return_value=dpr),
            ):
                window.render(output)
                for frame in range(warmup + frames):
                    simulated_at[0] += 0.008
                    # Keep this measurement on reading rather than control
                    # hide/restore and long-metadata marquee transitions.
                    window._control_last_motion_at = simulated_at[0]
                    requests.clear()
                    started = time.perf_counter()
                    window._on_animation_tick()
                    tick_ms = (time.perf_counter() - started) * 1000.0
                    region = QRegion()
                    for request in requests:
                        region = region.united(QRegion(request[0] if request else window.rect()))
                    if region.isEmpty():
                        continue
                    started = time.perf_counter()
                    window.render(output, region.boundingRect().topLeft(), region)
                    elapsed = (time.perf_counter() - started) * 1000.0
                    if frame >= warmup:
                        costs.append(elapsed)
                        ticks.append(tick_ms)
                        full_paints += int(region == QRegion(window.rect()))
                label = "native metadata" if baseline else "cached metadata"
                print(f"reading cadence {label:14s} {width}x{height} DPR={dpr:g}: {summary(costs)}")
                print(f"        tick {summary(ticks)} full={full_paints}/{len(costs)} emphasis={window._last_lyric_emphasis_mode}")
        finally:
            window.shutdown()
            owner.close()
            app.processEvents()


def benchmark_highlight_transitions(
    app: QApplication, width: int, height: int, dpr: float,
) -> None:
    """Time the actual timer requests while gold reading enters/leaves OBO glow."""
    from api.models import TimedLyricUnit

    ordinary = "We walk a quiet road while city lights are fading"
    highlighted = "This moment moves slowly across this line tonight"
    future_highlighted = "Another dream moves slowly between the stars again"
    for entering in (True, False):
        owner = SyntheticOwner("unit-slow")
        owner.music_surface_reading_strategy = "gold"
        owner.timed_lyrics = [
            (float(index * 6), (highlighted if index % 4 == 1 else future_highlighted) if index % 2 else ordinary)
            for index in range(24)
        ]
        owner.timed_lyric_units = []
        for start, phrase in owner.timed_lyrics:
            words = phrase.split(" ")
            slow = "slowly" in words
            other_duration = (6.0 - 1.6) / (len(words) - 1) if slow else 6.0 / len(words)
            cursor = start
            units = []
            for index, word in enumerate(words):
                duration = 1.6 if word == "slowly" else other_duration
                units.append(TimedLyricUnit(cursor, cursor + duration, word + (" " if index + 1 < len(words) else "")))
                cursor += duration
            owner.timed_lyric_units.append(tuple(units))
        owner.timed_lyric_timing_kinds = ["unit"] * len(owner.timed_lyrics)
        owner.media_position_seconds = 5.4 if entering else 11.4
        window = MusicSurfaceWindow(owner)
        window.resize(width, height)
        window._fullscreen = True
        window._reset_control_idle(now=20.0)
        output = QImage(round(width * dpr), round(height * dpr), QImage.Format.Format_RGB32)
        output.setDevicePixelRatio(dpr)
        output.fill(Qt.GlobalColor.black)
        simulated_at = [20.0]
        requests: list[tuple] = []
        window.update = lambda *regions: requests.append(regions)
        costs: list[float] = []
        tick_costs: list[float] = []
        modes: list[str] = []
        region_areas: list[tuple[int, int]] = []
        full_paints = 0
        try:
            with (
                patch("local.music_surface_window.time.monotonic", side_effect=lambda: simulated_at[0]),
                patch.object(window, "devicePixelRatioF", return_value=dpr),
            ):
                window.render(output)
                for frame in range(135):
                    simulated_at[0] = 20.0 + (frame + 1) * 0.008
                    window._control_last_motion_at = simulated_at[0]
                    requests.clear()
                    started = time.perf_counter()
                    window._on_animation_tick()
                    tick_ms = (time.perf_counter() - started) * 1000.0
                    region = QRegion()
                    for request in requests:
                        region = region.united(QRegion(request[0] if request else window.rect()))
                    if region.isEmpty():
                        continue
                    started = time.perf_counter()
                    window.render(output, region.boundingRect().topLeft(), region)
                    costs.append((time.perf_counter() - started) * 1000.0)
                    tick_costs.append(tick_ms)
                    modes.append(window._last_lyric_emphasis_mode)
                    region_areas.append((region.rectCount(), sum(rect.width() * rect.height() for rect in region)))
                    full_paints += int(region == QRegion(window.rect()))
                label = "ordinary->OBO" if entering else "OBO->ordinary"
                edges = [index for index in range(1, len(modes)) if modes[index] != modes[index - 1]]
                print(f"highlight transition {label:14s} {width}x{height} DPR={dpr:g}: {summary(costs)}")
                before = costs[max(0, edges[0] - 24):edges[0]] if edges else []
                after = costs[edges[0]:edges[0] + 24] if edges else []
                if before and after:
                    print(f"        pre-edge {summary(before)}; post-edge {summary(after)}")
                print(f"        tick {summary(tick_costs)} full={full_paints}/{len(costs)} edges={edges}")
                for edge in edges:
                    print(f"        edge {modes[edge - 1]} -> {modes[edge]} paint(ms)={costs[max(0, edge-3):edge+9]}")
                    print(
                        f"        edge caches glyph={len(window._isolated_glyph_cache)} "
                        f"halo={len(window._sparse_glyph_halo_cache)} "
                        f"tint={len(window._glyph_tint_cache)} pending={window._pending_glow_prewarm is not None}"
                    )
                    print(f"        edge regions={region_areas[max(0, edge-3):edge+9]}")
        finally:
            window.shutdown()
            owner.close()
            app.processEvents()


def parse_sizes(value: str) -> list[tuple[int, int]]:
    sizes = []
    for raw in value.split(","):
        width, separator, height = raw.lower().partition("x")
        if not separator or not width.isdecimal() or not height.isdecimal():
            raise argparse.ArgumentTypeError(f"invalid size: {raw!r}")
        parsed = (int(width), int(height))
        if min(parsed) < 1:
            raise argparse.ArgumentTypeError(f"invalid size: {raw!r}")
        sizes.append(parsed)
    return sizes


def parse_dprs(value: str) -> list[float]:
    try:
        dprs = [float(part) for part in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid DPR list: {value!r}") from exc
    if not dprs or any(not math.isfinite(dpr) or dpr <= 0.0 or dpr > 4.0 for dpr in dprs):
        raise argparse.ArgumentTypeError("DPR values must be between 0 and 4")
    return dprs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("windows", "offscreen"), default=SELECTED_PLATFORM)
    parser.add_argument("--sizes", type=parse_sizes, default=[(960, 540), (1920, 1080)])
    parser.add_argument("--dprs", type=parse_dprs, default=[1.0, 2.0])
    parser.add_argument("--frames", type=int, default=24)
    parser.add_argument("--warmup", type=int, default=8)
    parser.add_argument("--event-loop-seconds", type=float, default=0.0)
    parser.add_argument("--profile", action="store_true", help="print cProfile top calls for lyric-region paints")
    parser.add_argument("--profile-cold", action="store_true", help="print cProfile top calls for one long-song cold render")
    parser.add_argument("--profile-switch", action="store_true", help="print cProfile top calls for one warm-process 180-line track switch")
    parser.add_argument("--idle-controls", action="store_true", help="measure 5-second hide/restore over the two control dirty rectangles")
    parser.add_argument("--startup-gradient-sequence", action="store_true", help="measure cold open, 31 slider steps, and first three song changes")
    parser.add_argument("--reading-cadence", action="store_true", help="compare native versus cached metadata on slow real-unit lyric timer paints")
    parser.add_argument("--highlight-transitions", action="store_true", help="time gold OBO ordinary/highlight boundaries at native DPR")
    args = parser.parse_args()
    if args.frames < 1 or args.warmup < 0 or args.event_loop_seconds < 0:
        parser.error("frames must be positive; warmup and event-loop seconds cannot be negative")
    if args.event_loop_seconds and args.platform != "offscreen":
        parser.error("--event-loop-seconds requires --platform offscreen because it shows a window")
    app = QApplication.instance() or QApplication([])
    print(f"Qt platform={app.platformName()}; synthetic QImage raster, not display FPS")
    if args.startup_gradient_sequence:
        benchmark_startup_gradient_sequence(app, *args.sizes[0])
    for scenario in ("long-song", "sparse"):
        for width, height in args.sizes:
            benchmark_raster(
                app, scenario, width, height, args.frames, args.warmup,
                profile=args.profile and scenario == "long-song" and (width, height) == args.sizes[0],
                profile_cold=args.profile_cold and scenario == "long-song" and (width, height) == args.sizes[0],
                profile_switch=args.profile_switch and scenario == "long-song" and (width, height) == args.sizes[0],
            )
    if args.idle_controls:
        width, height = args.sizes[0]
        for dpr in args.dprs:
            benchmark_idle_controls(app, width, height, dpr)
    if args.reading_cadence:
        width, height = args.sizes[0]
        for dpr in args.dprs:
            benchmark_reading_cadence(app, width, height, dpr, args.frames, args.warmup)
    if args.highlight_transitions:
        width, height = args.sizes[0]
        for dpr in args.dprs:
            benchmark_highlight_transitions(app, width, height, dpr)
    if args.event_loop_seconds:
        width, height = args.sizes[0]
        benchmark_event_loop(app, "long-song", width, height, args.event_loop_seconds)


if __name__ == "__main__":
    main()
