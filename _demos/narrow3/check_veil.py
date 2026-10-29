"""Veil invariants, DPR captures and offscreen paint acceptance.

Run with the documented isolated Qt interpreter.  Default mode starts separate
DPR 1 / DPR 2 processes, so Qt's actual device scale participates in the tests.
Artifacts and the machine-readable report are written only to --out.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication
from PIL import Image, ImageSequence
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from layout import NarrowLayout
from veil import VeilOverlay


def rgba(image):
    source = image.convertToFormat(QImage.Format.Format_RGBA8888)
    rows = np.frombuffer(source.bits(), np.uint8).reshape(source.height(), source.bytesPerLine())
    return rows[:, :source.width() * 4].reshape(source.height(), source.width(), 4).copy()


def image_at_scale(dpr, transparent=True):
    image = QImage(round(360 * dpr), round(640 * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(dpr)
    image.fill(Qt.GlobalColor.transparent if transparent else Qt.GlobalColor.black)
    return image


def stripe_source(dpr):
    image = image_at_scale(dpr)
    with QPainter(image) as painter:
        painter.fillRect(QRectF(100, 0, 2, 640), Qt.GlobalColor.white)
    return image


def compose(overlay, source, key, opacity, delta=0):
    result = image_at_scale(source.devicePixelRatio(), False)
    with QPainter(result) as painter:
        overlay.paint(painter, source, key, opacity, delta)
    return result


def percentile(samples, fraction):
    ordered = sorted(samples)
    return ordered[math.ceil((len(ordered) - 1) * fraction)]


def synthetic_checks(dpr):
    overlay = VeilOverlay()
    source = stripe_source(dpr)
    key = ("stripes", "dark", dpr, 0)
    overlay.prepare(key, source)
    before = rgba(compose(overlay, source, key, 0))
    # A cache hit must do no image filtering, including during interrupted fades.
    with patch.object(Image.Image, "filter", side_effect=AssertionError("blur during paint")):
        overlay.prepare(key, lambda: (_ for _ in ()).throw(AssertionError("cache-hit factory called")))
        fades = [rgba(compose(overlay, source, key, value)) for value in (0, .25, .5, .75, 1)]
    full = fades[-1]
    # Painting an equal, separately constructed key must use the same atlas;
    # clearing the cache must also invalidate the most recently prepared key.
    equal_key = tuple(list(key))
    assert equal_key == key and equal_key is not key
    assert np.array_equal(full, rgba(compose(overlay, source, equal_key, 1)))
    overlay.clear()
    assert np.array_equal(rgba(compose(overlay, source, key, 1)),
                          rgba(compose(VeilOverlay(), source, key, 1))), "clear retained prepared bands"
    overlay.prepare(key, source)
    top_end = VeilOverlay.TOP_END
    top_start = VeilOverlay.TOP_FEATHER_START
    bottom_start = 640 - VeilOverlay.BOTTOM_HEIGHT
    bottom_end = bottom_start + VeilOverlay.BOTTOM_FEATHER
    middle = slice(round(top_end * dpr), round(bottom_start * dpr))
    assert np.array_equal(before[middle], full[middle]), "middle pixels changed"
    assert np.array_equal(before, fades[0]), "A opacity 0 changed content"
    for y in (30, top_start - 4, bottom_end + 4, 600):
        row = full[round(y * dpr), :, 0]
        assert row[round(100 * dpr)] < 40, ("sharp glyph survived strongest blur", y)
        assert row[round(94 * dpr)] > 0, ("blur does not spread glyph ink", y)
    # Maximal solid bands are constant; feather approaches clear middle.
    for y, expected in ((0, 1), (top_start, 1), (top_end, 0),
                        ((top_end + bottom_start) / 2, 0),
                        (bottom_start, 0), (bottom_end, 1), (639, 1)):
        assert abs(VeilOverlay.band_strength(y, 640) - expected) < 1e-6, (y, expected)
    assert top_end - top_start >= 40, "top feather regressed below 40px"
    assert VeilOverlay.BOTTOM_FEATHER >= 64, "bottom feather regressed below 64px"
    for start, end, reverse in ((top_start, top_end, True),
                                (bottom_start, bottom_end, False)):
        for fraction in (.01, .25, .5, .75, .99):
            expected = fraction * fraction * (3 - 2 * fraction)
            if reverse:
                expected = 1 - expected
            actual = VeilOverlay.band_strength(start + (end - start) * fraction, 640)
            assert abs(actual - expected) < 1e-6, ("veil is not smoothstep", actual, expected)
    empty = image_at_scale(dpr)
    dimmed = image_at_scale(dpr)
    dimmed.fill(Qt.GlobalColor.white)
    with QPainter(dimmed) as painter:
        overlay.paint(painter, empty, "empty-source", 1)
    dim_pixels = rgba(dimmed)
    for y in (30, 600):
        assert abs(int(dim_pixels[round(y*dpr), round(250*dpr), 0])-round(255*(1-VeilOverlay.DIM_ALPHA))) <= 1, "dim alpha differs from shared token"
    assert int(dim_pixels[round(300*dpr), round(250*dpr), 0]) == 255, "dim reaches the middle"
    # Both sides of each feather meet the clear/solid area with zero slope.
    # Read real composited pixels, so this also catches a straight dim seam.
    column = dim_pixels[:, round(250 * dpr), 0].astype(int)
    for boundary in (top_start, top_end, bottom_start, bottom_end):
        row = round(boundary * dpr)
        assert abs(column[row] - column[row - 1]) <= 1, ("dim seam", boundary)
    for row in range(round(top_start*dpr), round(top_end*dpr)):
        expected = 255 * (1 - VeilOverlay.DIM_ALPHA * VeilOverlay.band_strength((row + .5) / dpr, 640))
        assert abs(column[row] - expected) <= 2, ("blur and dim profiles differ", row, column[row], expected)
    for row in range(round(bottom_start*dpr), round(bottom_end*dpr)):
        expected = 255 * (1 - VeilOverlay.DIM_ALPHA * VeilOverlay.band_strength((row + .5) / dpr, 640))
        assert abs(column[row] - expected) <= 2, ("blur and dim profiles differ", row, column[row], expected)
    # Scrolling a vertically invariant source cannot move the radius profile:
    # the feather belongs to the window, while only the text texture moves.
    for delta in (-15.0, 15.0):
        shifted = rgba(compose(overlay, source, key, 1, delta))
        for y in (top_start + (top_end-top_start) * t for t in (.25, .5, .75)):
            row = round(y*dpr)
            error = np.abs(shifted[row].astype(int)-full[row].astype(int)).max()
            assert error <= 3, ("blur feather moved with scroll", dpr, y, delta, int(error))
        for y in (bottom_start + VeilOverlay.BOTTOM_FEATHER * t for t in (.25, .5, .75)):
            row = round(y*dpr)
            error = np.abs(shifted[row].astype(int)-full[row].astype(int)).max()
            assert error <= 3, ("blur feather moved with scroll", dpr, y, delta, int(error))
    # LRU keys contain all invalidation inputs, while sub-row movement reuses one.
    cache = VeilOverlay()
    samples_per_row = math.ceil(NarrowLayout(360, 640).lyric_step)
    for frame in range(samples_per_row * 10):
        cache.prepare(("text-layout", "dark", dpr, frame // samples_per_row), source)
    assert cache.hits / (cache.hits + cache.misses) >= .95, "steady scroll cache hit rate"
    assert cache.cache_size <= 6, "cache entry limit"
    assert cache.cache_bytes <= 2.8 * 1024 * 1024, "DPR2 cache exceeds approximately 2.8MB"
    hit_rate = cache.hits / (cache.hits + cache.misses)
    old_builds = cache.builds
    for changed in (("layout2", "dark", dpr, 9), ("layout2", "light", dpr, 9),
                    ("layout2", "light", dpr + 1, 9)):
        cache.prepare(changed, source)
    assert cache.builds == old_builds + 3, "layout/theme/DPR invalidation"
    old_builds = cache.builds
    cache.prepare(("text-layout", "dark", dpr, 0), source)
    assert cache.builds == old_builds + 1, "oldest LRU entry was retained"
    return {"synthetic": "passed", "cache_hit_rate": hit_rate,
            "cache_bytes": cache.cache_bytes, "cache_entries": cache.cache_size}


def set_opacity(window, opacity):
    fade = window.visibility.opacity
    fade.start = fade.target = opacity
    fade.duration = 0
    window.visibility.progress = opacity


def capture(window, output, dpr, mode):
    window.mode = mode
    window.set_content_layout(mode, animate=False)
    window._layout_animation.stop()
    window._finish_content_layout()
    window.refresh_veil()
    if mode == "lyrics":
        layout = NarrowLayout(window.width(), window.height())
        bucket = round(window.lyric_focus()[1])
        resting = rgba(window.content_image(layout, bucket, include_current=False))
        source = rgba(window.content_image(layout, bucket, veil_source=True, include_current=False))
        assert np.array_equal(resting, source), "veil source sharpens the resting lyric ink"
    images = {}
    for label, opacity in (("A", 0.0), ("B", 1.0)):
        set_opacity(window, opacity)
        window.refresh_controls(time.monotonic())
        window.repaint()
        images[label] = window.grab().toImage()
    first, second = (rgba(images[label]) for label in ("A", "B"))
    clear_top = VeilOverlay.TOP_END
    clear_bottom = 640 - VeilOverlay.BOTTOM_HEIGHT
    assert np.array_equal(first[round(clear_top*dpr):round(clear_bottom*dpr)],
                          second[round(clear_top*dpr):round(clear_bottom*dpr)]), "actual A/B middle changed"
    bottom_crop = max(0, clear_bottom - 24)
    for label, top, height in (("A", 0, 640), ("B_top", 0, clear_top + 24),
                               ("B_bottom", bottom_crop, 640 - bottom_crop)):
        image = images["A" if label == "A" else "B"]
        if label != "A":
            image = image.copy(0, round(top * dpr), round(360 * dpr), round(height * dpr))
            image.setDevicePixelRatio(dpr)
        path = output / f"veil_dpr{dpr}_{mode}_{label}.png"
        assert image.save(str(path)), path
    # Every alpha samples the exact same full content layer and geometry.
    original = rgba(window.content_image(NarrowLayout(360, 640, 0)))
    for top, bottom in ((20, clear_top), (clear_bottom, 620)):
        assert np.count_nonzero(original[round(top*dpr):round(bottom*dpr), :, 3]) > 100*dpr*dpr, "content no longer fills a veil band"
    animation = []
    forward = [1-(1-t)**3 for t in np.linspace(0, 1, 19)]
    reverse = [(1-t)**3 for t in np.linspace(0, 1, 19)]
    for alpha in forward + reverse[1:-1]:
        set_opacity(window, float(alpha))
        window.refresh_controls(time.monotonic())
        assert np.array_equal(original, rgba(window.content_image(NarrowLayout(360, 640, float(alpha))))), "content geometry changes with alpha"
        window.repaint()
        pixels = rgba(window.grab().toImage())
        animation.append(Image.fromarray(pixels, "RGBA").convert("RGB"))
    # GIF stores centiseconds: alternate 10/20ms delays instead of requesting
    # 17ms (which GIF would truncate to 10ms and shorten the actual fade).
    delays = [round((i+1)*window.visibility.DURATION*1000/18/10)*10-round(i*window.visibility.DURATION*1000/18/10)*10
              for i in range(len(animation))]
    animation[0].save(output / f"veil_dpr{dpr}_{mode}_transition.gif",
                      save_all=True, append_images=animation[1:],
                      duration=delays, loop=0)
    with Image.open(output / f"veil_dpr{dpr}_{mode}_transition.gif") as recorded:
        assert sum(frame.info.get("duration", 0) for frame in ImageSequence.Iterator(recorded)) == round(window.visibility.DURATION * 2000), "recorded fade timing"
    return {"captures": 3, "middle_identical": True, "content_motion_px": 0}


def actual_scroll_cache(window):
    if window.mode != "lyrics":
        return None
    original_position = window.owner.media_position_seconds
    old_hits, old_misses = window.veil.hits, window.veil.misses
    try:
        samples_per_row = math.ceil(NarrowLayout(window.width(), window.height()).lyric_step)
        for frame in range(samples_per_row * 10):
            focus = 13 + frame//samples_per_row
            fraction = (frame % samples_per_row)/samples_per_row
            # Invert the demo's cubic ease-out to advance exactly one logical
            # row through each bucket, sampled at at most one pixel per frame.
            transition = 1-(1-fraction)**(1/3)
            window.owner.media_position_seconds = focus*4 + transition*.36
            window.refresh_veil()
        hits = window.veil.hits-old_hits
        misses = window.veil.misses-old_misses
        rate = hits/(hits+misses)
        assert rate >= .95, ("actual steady scroll hit rate", rate)
        return rate
    finally:
        window.owner.media_position_seconds = original_position
        window.refresh_veil()


def render_frame(window, target, *, composite_only=False):
    window.tick()
    count = len(window.samples)
    with ExitStack() as guards:
        if composite_only:
            guards.enter_context(patch.object(Image.Image, "filter",
                                              side_effect=AssertionError("filter during fade paint")))
        if window.mode == "playlist":
            guards.enter_context(patch.object(window, "paint_list",
                                              side_effect=AssertionError("list rebuilt during paint")))
        window.render(target)
    assert len(window.samples) == count+1, "benchmark did not trigger exactly one paint"


def start_fade(window, now, reveal):
    window.shelf = window.footer_hover = reveal
    window.visibility.idle_since = None if reveal else now - window.visibility.IDLE_DELAY
    fade = window.visibility.opacity
    fade.start, fade.target = (0.0, 1.0) if reveal else (1.0, 0.0)
    fade.started, fade.duration = now, window.visibility.DURATION


def simulated_fades(window, target, frames, *, advancing=False, position=None):
    """Sample genuine 320ms fades near 60Hz; perf_counter stays a real clock.

    The old alpha assignment used duration=0, which sampled the steady paint
    path. Here tick and paint observe one monotonically advancing time and a
    live fade object. Each sample lies inside the transition, including frames
    beyond the list's 100ms material-refresh interval.
    """
    base = time.monotonic()
    samples_per_fade = round(window.visibility.DURATION * 60)
    frame_seconds = window.visibility.DURATION / samples_per_fade
    old_position = window.owner.media_position_seconds
    old_playing, old_freeze = window.owner.media_is_playing, window.freeze_clock
    window.owner.media_is_playing, window.freeze_clock = advancing, not advancing
    window.last_tick = base
    paints, totals = [], []
    old_builds = window.veil.builds
    try:
        for frame in range(frames):
            cycle, offset = divmod(frame, samples_per_fade)
            instant = base + (frame + .5) * frame_seconds
            if offset == 0:
                start_fade(window, base + cycle * window.visibility.DURATION, cycle % 2 == 0)
            if position is not None:
                window.owner.media_position_seconds = position + frame * frame_seconds
            with patch("narrow_window.time.monotonic", return_value=instant):
                assert not window.visibility.settled(instant), "sample is outside the fade"
                started = time.perf_counter()
                render_frame(window, target,
                             composite_only=not advancing and position is None)
                totals.append((time.perf_counter() - started) * 1000)
                paints.append(window.samples[-1])
        if not advancing and position is None:
            assert window.veil.builds == old_builds, "stationary fade rebuilt a blur band"
        return paints, totals
    finally:
        window.owner.media_is_playing, window.freeze_clock = old_playing, old_freeze
        window.owner.media_position_seconds = old_position
        # The simulated segment can finish ahead of wall time. Return the demo
        # sampling clocks to the real cadence before the next independent case.
        now = time.monotonic()
        window.last_tick = window._glass_feedback.last_tick = now
        window._controls_at = window._list_layer_at = now
        window._frame_control_color_at = now
        set_opacity(window, 1.0)
        window.shelf = window.footer_hover = True
        window.refresh_veil()


def paint_benchmark(window, frames, mode):
    target = image_at_scale(window.devicePixelRatioF())
    window.mode = mode
    window.shelf = window.footer_hover = True
    prepare_start = time.perf_counter()
    window.refresh_veil()
    prepare_ms = (time.perf_counter()-prepare_start)*1000
    # Warm both material endpoints before measuring the transition compositor.
    for alpha in (0.0, 1.0):
        set_opacity(window, alpha)
        for _ in range(8):
            render_frame(window, target)
    # Captures freeze monotonic time; let the production 360ms ink/theme
    # transition settle at the real app cadence before timing a stable-theme fade.
    warm_until = time.perf_counter()+.5
    while time.perf_counter() < warm_until:
        set_opacity(window, 1.0)
        render_frame(window, target)
        time.sleep(window.study_timer.interval()/1000)
    # Match the app's existing 33ms timer for its CPU acceptance, and report
    # a separate 60Hz stress sample. Both include tick/preparation + full paint.
    def fade_cpu(interval):
        start_fade(window, time.monotonic(), True)
        cpu_start = time.process_time()
        start = time.perf_counter()
        count = 0
        while time.perf_counter()-start < window.visibility.DURATION:
            render_frame(window, target)
            count += 1
            scheduled_frame = min(start+window.visibility.DURATION, start+count*interval)
            time.sleep(max(0, scheduled_frame-time.perf_counter()))
        return 100*(time.process_time()-cpu_start)/(time.perf_counter()-start)

    app_interval = window.study_timer.interval()/1000
    cpu_percent = fade_cpu(app_interval)
    stress_cpu_percent = fade_cpu(1/60)
    source = window.content_image(NarrowLayout(360, 640))
    cpu_start = time.process_time()
    start = time.perf_counter()
    count = 0
    while time.perf_counter()-start < window.visibility.DURATION:
        now = time.perf_counter()
        with QPainter(target) as painter:
            window.veil.paint(painter, source, window.veil_key,
                              1-(1-min(1.0, (now-start)/window.visibility.DURATION))**3, window.veil_scroll_delta)
        count += 1
        time.sleep(max(0, min(start+window.visibility.DURATION, start+count/60)-time.perf_counter()))
    veil_cpu = 100*(time.process_time()-cpu_start)/(time.perf_counter()-start)
    # Exercise the current run with the genuine advancing demo clock as well.
    old_playing, old_freeze = window.owner.media_is_playing, window.freeze_clock
    old_position = window.owner.media_position_seconds
    window.owner.media_is_playing, window.freeze_clock = True, False
    window.last_tick = time.monotonic()
    live_samples = []
    try:
        for _ in range(30):
            render_frame(window, target)
            live_samples.append(window.samples[-1])
            time.sleep(app_interval)
    finally:
        window.owner.media_is_playing, window.freeze_clock = old_playing, old_freeze
        window.owner.media_position_seconds = old_position
        window.refresh_veil()
    live_p95 = percentile(live_samples, .95)
    assert live_p95 <= 16.7, (mode, "steady advancing-clock p95", live_p95)
    # Measure advancing playback during a real reveal/hide too, separately
    # from the steady background path above.
    advancing_samples, advancing_totals = simulated_fades(window, target, 36, advancing=True)
    advancing_p95 = percentile(advancing_samples, .95)
    assert advancing_p95 <= 16.7, (mode, "advancing-clock fade p95", advancing_p95)
    scroll_p95 = None
    if mode == "lyrics":
        overlap_samples, _ = simulated_fades(window, target, 30, position=55.9)
        scroll_p95 = percentile(overlap_samples, .95)
        assert scroll_p95 <= 16.7, ("scroll+fade overlap p95", scroll_p95)
    samples, frame_totals = simulated_fades(window, target, frames)
    p95, p99 = percentile(samples, .95), percentile(samples, .99)
    return {"frames": frames, "p95_ms": p95, "p99_ms": p99, "prepare_ms": prepare_ms,
            "tick_and_paint_p95_ms": percentile(frame_totals, .95),
            "advancing_clock_frames": 30, "advancing_clock_p95_ms": live_p95,
            "advancing_clock_fade_frames": 36, "advancing_clock_fade_p95_ms": advancing_p95,
            "advancing_clock_fade_tick_and_paint_p95_ms": percentile(advancing_totals, .95),
            "scroll_fade_overlap_frames": 30 if mode == "lyrics" else 0,
            "scroll_fade_overlap_p95_ms": scroll_p95,
            "fade_cpu_percent": cpu_percent, "fade_cadence_ms": app_interval*1000,
            "stress_60hz_cpu_percent": stress_cpu_percent, "veil_fade_cpu_percent_60hz": veil_cpu,
            "fade_cpu_scenario": "paused source at the actual 33ms app cadence",
            "cpu_target_met": cpu_percent <= 15,
            "paint_target_met": p95 <= 16.7 and p99 <= 24}


def run_dpr(args):
    os.environ["QT_SCALE_FACTOR"] = str(args.dpr)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from narrow_window import NarrowStudyWindow, make_owner
    app = QApplication.instance() or QApplication([])
    args.out.mkdir(parents=True, exist_ok=True)
    owner = make_owner()
    window = NarrowStudyWindow(owner, "B", "left")
    window.show()
    app.processEvents()
    assert abs(window.devicePixelRatioF()-args.dpr) < 1e-6, "Qt device scale differs from requested DPR"
    window._timer.stop()
    window.study_timer.stop()
    window.freeze_clock = True
    window.hints = False
    # Keep background and control ink deterministic across the two captures.
    window.owner.media_is_playing = False
    window._frame_control_color_at = 0
    results = {"dpr": args.dpr, **synthetic_checks(args.dpr), "modes": {}}
    try:
        for mode in ("lyrics", "playlist"):
            fixed_time = time.monotonic()
            with patch("narrow_window.time.monotonic", return_value=fixed_time):
                captures = capture(window, args.out, args.dpr, mode)
                captures["actual_scroll_hit_rate"] = actual_scroll_cache(window)
            results["modes"][mode] = {**captures, **paint_benchmark(window, args.frames, mode)}
    finally:
        window.shutdown()
        owner.close()
    path = args.out / f"veil_acceptance_dpr{args.dpr}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False), flush=True)
    for mode, report in results["modes"].items():
        assert report["paint_target_met"], (mode, "paint target", report)
        assert report["cpu_target_met"], (mode, "fade CPU target", report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--frames", default=180, type=int)
    parser.add_argument("--dpr", choices=(1, 2), type=int)
    args = parser.parse_args()
    if args.dpr is not None:
        run_dpr(args)
    else:
        for dpr in (1, 2):
            subprocess.run([sys.executable, __file__, "--out", str(args.out),
                            "--frames", str(args.frames), "--dpr", str(dpr)], check=True)


if __name__ == "__main__":
    main()
