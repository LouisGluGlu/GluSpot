"""Isolated, reproducible Qt raster baseline (not desktop/compositor FPS).

Each matrix cell owns a fresh process, QApplication and font/cache lifecycle.
Main samples contain no profiler/instrumentation. ``--diagnostic`` is a separate
run whose inclusive method timings must never be compared with main samples.
Synthetic owners have no network or playback and run background callbacks
synchronously; this limitation is recorded in every result.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def distribution(values):
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    def at(fraction):
        return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]
    return {"n": len(values), "p50_ms": statistics.median(values),
            "p95_ms": at(.95), "p99_ms": at(.99), "max_ms": ordered[-1],
            "over_8_33_ms": sum(value > 8.33 for value in values),
            "over_16_67_ms": sum(value > 16.67 for value in values)}


def physical_size(width, height, dpr):
    if min(width, height, dpr) <= 0:
        raise ValueError("positive size and DPR required")
    return max(1, round(width * dpr)), max(1, round(height * dpr))


def validate_prepared(prepared, timed, units):
    """Never accept loading/empty rows or dropped genuine anchors as fast ink."""
    if prepared is None or not prepared.timed:
        return False
    if prepared.source.timed != tuple(timed) or len(prepared.timed) != len(timed):
        return False
    expected = tuple(tuple(row) for row in units)
    return (prepared.source.units == expected and prepared.units == expected
            if expected else not any(prepared.units))


def source_manifest():
    paths = sorted((*ROOT.joinpath("local").glob("*.py"),
                    *ROOT.joinpath("api").rglob("*.py")))
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths if "__pycache__" not in path.parts}


def process_resources():
    import threading
    result = {"python_threads": threading.active_count(),
              "music_worker_threads": sum(thread.name.startswith("GluSpotMusic-")
                                          for thread in threading.enumerate())}
    if os.name != "nt":
        return result
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    *[(key, ctypes.c_size_t) for key in (
                        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                        "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                        "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    process = kernel.GetCurrentProcess()
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
        result.update(working_set_bytes=counters.WorkingSetSize,
                      private_bytes=counters.PrivateUsage,
                      peak_working_set_bytes=counters.PeakWorkingSetSize)
    handles = wintypes.DWORD()
    kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    if kernel.GetProcessHandleCount(process, ctypes.byref(handles)):
        result["handles"] = handles.value
    return result


def cache_snapshot(window):
    return {name: value if isinstance(value, int) else len(value)
            for name, value in vars(window).items()
            if "cache" in name and (name.endswith("_bytes") or isinstance(value, dict))}


def glyph_resource_snapshot(window):
    """Observe outside timed frames; cold budgets exclude explicit worksets.

    Pixel bytes are per-container accounting, not additive process memory:
    `_glyph_cache` may be an alias and other QImages can share their storage.
    Old-source runs compute sizes even where no explicit counter existed.
    """
    protected = getattr(window, "_emphasis_protected_glyph_runs", ())
    sources = getattr(window, "_emphasis_protected_source_keys", ())
    all_tints_pinned = not hasattr(window, "_emphasis_protected_tint_color")
    color = getattr(window, "_emphasis_protected_tint_color", None)
    result = {}
    groups = (
        ("run", "_glyph_run_cache", "_glyph_run_cache_bytes", "GLYPH_RUN_CACHE",
         lambda value: [value[0]], lambda key: key in protected, getattr(window, "_glyph_cache_key", None)),
        ("isolated", "_isolated_glyph_cache", "_isolated_glyph_cache_bytes", "ISOLATED_GLYPH_CACHE",
         lambda value: [image for image, _left in value], lambda key: key in protected, None),
        ("partial", "_isolated_glyph_builds", "_isolated_glyph_builds_bytes", "ISOLATED_GLYPH_BUILD",
         lambda value: [image for image, _left in value["result"]], lambda key: key in protected,
         getattr(window, "_isolated_glyph_build_last_key", None)),
        ("tint", "_glyph_tint_cache", "_glyph_tint_cache_bytes", "GLYPH_TINT_CACHE",
         lambda value: [value], lambda key: key[0] in sources and (all_tints_pinned or key[1] == color), None),
        ("halo", "_sparse_glyph_halo_cache", "_sparse_glyph_halo_cache_bytes", "SPARSE_GLYPH_HALO_CACHE",
         lambda value: [value[0]], lambda key: key in sources, None),
    )
    for label, name, counter, prefix, images, current, working in groups:
        stats = {"entries": 0, "bytes": 0}
        for group in ("cold", "current", "working"):
            stats[f"{group}_entries"] = stats[f"{group}_bytes"] = 0
        for key, value in getattr(window, name, {}).items():
            size = sum(int(image.sizeInBytes()) for image in images(value))
            group = "current" if current(key) else "working" if key == working else "cold"
            stats["entries"] += 1
            stats["bytes"] += size
            stats[f"{group}_entries"] += 1
            stats[f"{group}_bytes"] += size
        stats.update(counter_bytes=getattr(window, counter, None),
                     byte_limit=getattr(window, prefix + "_MAX_BYTES", None),
                     entry_limit=getattr(window, prefix + "_MAX_ENTRIES", None))
        result[label] = stats
    singleton = getattr(window, "_glyph_cache", None)
    key = getattr(window, "_glyph_cache_key", None)
    cached = getattr(window, "_glyph_run_cache", {}).get(key)
    result["singleton_run"] = {
        "bytes": int(singleton[0].sizeInBytes()) if singleton else 0,
        "aliases_retained_run": bool(singleton and cached and singleton[0].cacheKey() == cached[0].cacheKey()),
    }
    return result


def raster_snapshot(window):
    # QImage shells can share pixels; report fields individually, not an
    # additive process-memory claim. In particular include native backdrop.
    from PySide6.QtGui import QImage
    return {name: {"bytes": value.sizeInBytes(), "width": value.width(), "height": value.height(),
                   "dpr": value.devicePixelRatio(), "shared_cache_key": value.cacheKey()}
            for name, value in vars(window).items() if isinstance(value, QImage) and not value.isNull()}


def run_worker(args):
    # The legacy module's early parser honors --platform before Qt import.
    sys.path.insert(0, str(ROOT / "tests"))
    from benchmark_music_surface import SyntheticOwner
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import qVersion
    from PySide6.QtGui import QImage, QRegion
    from PySide6.QtWidgets import QApplication
    from local.music_surface_window import MusicSurfaceWindow
    from unittest.mock import patch
    app = QApplication.instance() or QApplication([])
    width, height = map(int, args.size.split("x"))
    owner = SyntheticOwner(args.scenario)
    store = None
    preparation = {"mode": "legacy-synchronous-accessor"}
    if args.production_store:
        # The live owner installs this same store before provider submission.
        # Await an actual complete result BEFORE any glyph speed is measured.
        from local.music_lyric_preparation_store import LyricPresentationStore
        store = LyricPresentationStore(owner)
        owner._lyric_presentation_store = store
        submitted_at = time.perf_counter()
        prepared = store.get()
        while prepared is None and time.perf_counter()-submitted_at < 15.:
            app.processEvents()
            time.sleep(.001)
            prepared = store.get()
        ready_ms = (time.perf_counter()-submitted_at)*1000
        if not validate_prepared(prepared, owner.timed_lyrics, getattr(owner, "timed_lyric_units", ())):
            store.close()
            raise RuntimeError("Production preparation did not preserve complete rows and real anchors")
        preparation = {"mode": "production-worker-store", "submit_to_ready_ms": ready_ms,
                       "worker_ms": store.prepare_seconds*1000,
                       "complete_timed_rows": len(prepared.timed),
                       "genuine_unit_rows": sum(bool(row) for row in prepared.units),
                       "source_units_preserved": True}
    started = time.perf_counter()
    window = MusicSurfaceWindow(owner)
    window.resize(width, height)
    window._fullscreen = True
    window._reset_control_idle(now=20.)
    window._timer.stop()
    window._audio_output_status_timer.stop()
    construction_ms = (time.perf_counter() - started) * 1000
    actual_size = (window.width(), window.height())
    output = QImage(*physical_size(*actual_size, args.dpr), QImage.Format.Format_RGB32)
    output.setDevicePixelRatio(args.dpr)
    output.fill(0xff000000)
    requests = []
    window.update = lambda *regions: requests.append(regions)
    simulated = [20.]
    methods = {}
    cache_events = {"control_hits": 0, "control_misses": 0,
                    "glass_requests": 0, "glass_builds": 0}
    wrappers = ExitStack()
    # Inclusive nested method costs are diagnostic only, never formal samples.
    if args.diagnostic:
        for name in ("_resolve_display_lyrics", "_lyric_frame", "_song_lyric_layouts",
                     "_advance_song_layout_build", "_wrapped_lyric_layout", "_paint_background",
                     "_paint_surface_glass", "_paint_control_layer", "_lyric_raster",
                     "_capture_transition_snapshot"):
            original = getattr(window, name)
            def wrapped(*a, _name=name, _original=original, **kw):
                began = time.perf_counter()
                try:
                    return _original(*a, **kw)
                finally:
                    methods.setdefault(_name, []).append((time.perf_counter() - began) * 1000)
            wrappers.enter_context(patch.object(window, name, new=wrapped))
        original_control_lookup = window._cached_control_layer
        def cached_control_layer(key):
            value = original_control_lookup(key)
            cache_events["control_hits" if value is not None else "control_misses"] += 1
            return value
        wrappers.enter_context(patch.object(window, "_cached_control_layer", new=cached_control_layer))
        import local.liquid_glass as glass
        original_glass, original_build = glass._glass_material, glass._render_glass_material
        def material(*a, **kw):
            cache_events["glass_requests"] += 1
            return original_glass(*a, **kw)
        def build_material(*a, **kw):
            cache_events["glass_builds"] += 1
            return original_build(*a, **kw)
        wrappers.enter_context(patch.object(glass, "_glass_material", new=material))
        wrappers.enter_context(patch.object(glass, "_render_glass_material", new=build_material))
    def frame():
        simulated[0] += args.step_ms / 1000
        # Keep controls present so reading is not confounded by idle transitions.
        window._control_last_motion_at = simulated[0]
        requests.clear()
        start = time.perf_counter()
        window._on_animation_tick()
        tick_end = time.perf_counter()
        region = QRegion()
        for request in requests:
            region |= QRegion(request[0] if request else window.rect())
        region &= QRegion(window.rect())
        union_end = time.perf_counter()
        if not region.isEmpty():
            # Matching origin preserves Qt's partial-render target coordinates.
            window.render(output, region.boundingRect().topLeft(), region)
        end = time.perf_counter()
        return {"tick_ms": (tick_end-start)*1000,
                "region_union_ms": (union_end-tick_end)*1000,
                "render_ms": (end-union_end)*1000,
                "total_ms": (end-start)*1000,
                "full_paint": region == QRegion(window.rect()),
                "painted": not region.isEmpty(),
                "logical_dirty_pixels": sum(r.width()*r.height() for r in region),
                "simulation_seconds": simulated[0],
                "timer_interval_ms": window._timer.interval()}
    result = {"scenario": args.scenario, "logical_size": actual_size,
              "target_physical_size": [output.width(), output.height()],
              "requested_dpr": args.dpr, "target_dpr": output.devicePixelRatio(),
              "platform": args.platform, "qt": qVersion(), "pyside": pyside_version,
              "python": platform.python_version(), "construction_ms": construction_ms,
              "instrumentation": "inclusive-method-wrappers" if args.diagnostic else "none",
              "scope": "Qt/Python raster workload; not compositor FPS; manual playback clock",
              "synthetic_owner": "no audio/network; palette background callback synchronous; lyric mode explicit",
              "simulation_step_ms": args.step_ms, "preparation": preparation, "phases": {}}
    try:
        with (patch("local.music_surface_window.time.monotonic", new=lambda: simulated[0]),
              patch.object(window, "devicePixelRatioF", new=lambda: args.dpr)):
            result["resources_before"] = process_resources()
            start = time.perf_counter()
            window.render(output)
            result["first_paint_ms"] = (time.perf_counter()-start)*1000
            result["first_lyric_rows"] = len(window._display_lyrics()[0])
            if store is not None and tuple(window._display_lyrics()[1]) != prepared.timed:
                raise RuntimeError("First paint did not consume the validated prepared timeline")
            result["prepare_construct_first_paint_ms"] = (
                preparation.get("submit_to_ready_ms", 0) + construction_ms + result["first_paint_ms"])
            for label, count in (("cold", args.cold_frames), ("warmup", args.warmup), ("stable", args.frames)):
                samples = [frame() for _ in range(count)]
                result["phases"][label] = {
                    "samples": samples,
                    "summary": {key: distribution([sample[key] for sample in samples])
                                for key in ("tick_ms", "region_union_ms", "render_ms", "total_ms")},
                    "paint_count": sum(sample["painted"] for sample in samples),
                    "full_paints": sum(sample["full_paint"] for sample in samples),
                    "caches": cache_snapshot(window), "glyph_resources": glyph_resource_snapshot(window),
                    "raster_fields": raster_snapshot(window),
                    "resources": process_resources()}
            result["final_lyric_rows"] = len(window._display_lyrics()[0])
            result["emphasis_mode"] = window._last_lyric_emphasis_mode
            result["glow_stage"] = window._active_glow_stage
            if store is not None:
                result["preparation_store"] = {key: getattr(store, key) for key in (
                    "hits", "misses", "discarded", "submitted", "cache_bytes", "prepare_seconds")}
                result["preparation_store"].update(entries=len(store.cache), active=store.active is not None,
                                                   pending=store.pending is not None)
            if args.diagnostic:
                start = time.perf_counter()
                snapshot = window._capture_transition_snapshot()
                result["transition_snapshot_ms"] = (time.perf_counter()-start)*1000
                result["transition_snapshot_bytes"] = snapshot.sizeInBytes()
                result["diagnostic_methods_inclusive"] = {key: distribution(value) for key, value in methods.items()}
                control_total = cache_events["control_hits"] + cache_events["control_misses"]
                glass_total = cache_events["glass_requests"]
                result["diagnostic_cache_events"] = dict(cache_events,
                    control_hit_rate=cache_events["control_hits"]/control_total if control_total else None,
                    glass_reuse_rate=1-cache_events["glass_builds"]/glass_total if glass_total else None,
                    scope="entire diagnostic lifecycle including first paint, cold/warm/stable and one snapshot")
            if args.image:
                output.save(args.image)
    finally:
        wrappers.close()
        window.shutdown()
        if store is not None:
            store.close()
        owner.close()
        window.deleteLater()
        owner.deleteLater()
        app.processEvents()
        if store is not None:
            # MusicTasks.close is nonblocking; observe bounded daemon retirement
            # separately instead of labeling a momentary live worker as a leak.
            began = time.perf_counter()
            while process_resources()["music_worker_threads"] and time.perf_counter()-began < 1.:
                app.processEvents()
                time.sleep(.001)
            result["worker_retirement_ms"] = (time.perf_counter()-began)*1000
    result["resources_after_shutdown"] = process_resources()
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("windows", "offscreen"), default="windows" if os.name == "nt" else "offscreen")
    parser.add_argument("--output", required=True)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--diagnostic", action="store_true")
    parser.add_argument("--production-store", action="store_true",
                        help="use production async store and await verified complete rows before paint samples")
    parser.add_argument("--size", default="960x540")
    parser.add_argument("--scenario", default="long-song")
    parser.add_argument("--dpr", type=float, default=1.)
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--cold-frames", type=int, default=60)
    parser.add_argument("--warmup", type=int, default=120)
    parser.add_argument("--step-ms", type=float, default=8.)
    parser.add_argument("--image")
    args = parser.parse_args()
    if args.worker:
        run_worker(args)
        return
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=True)
    before = source_manifest()
    manifest = {"source_before": before, "cells": [],
                "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "production_store": args.production_store, "diagnostic": args.diagnostic,
                "frames": args.frames, "cold_frames": args.cold_frames, "warmup": args.warmup,
                "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    for scenario in ("long-song", "unit-slow"):
        for size in ("960x540", "1920x1080"):
            for dpr in (1, 2):
                name = f"{scenario}-{size}-dpr{dpr}"
                output = directory / f"{name}.json"
                command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--platform", args.platform,
                           "--scenario", scenario, "--size", size, "--dpr", str(dpr), "--output", str(output),
                           "--frames", str(args.frames), "--cold-frames", str(args.cold_frames), "--warmup", str(args.warmup),
                           "--step-ms", str(args.step_ms), "--image", str(directory / f"{name}.png")]
                if args.diagnostic:
                    command.append("--diagnostic")
                if args.production_store:
                    command.append("--production-store")
                began = time.perf_counter()
                proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=300)
                (directory / f"{name}.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
                if proc.returncode:
                    raise RuntimeError(f"{name} failed; see its log")
                data = json.loads(output.read_text(encoding="utf-8"))
                item = {"name": name, "elapsed_seconds": time.perf_counter()-began,
                        "first_paint_ms": data["first_paint_ms"], "stable": data["phases"]["stable"]["summary"]}
                manifest["cells"].append(item)
                print(name, json.dumps(item["stable"]["total_ms"]), flush=True)
    manifest["source_after"] = source_manifest()
    manifest["application_source_unchanged"] = before == manifest["source_after"]
    manifest["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if not manifest["application_source_unchanged"]:
        raise RuntimeError("Application source changed while measuring")


if __name__ == "__main__":
    main()
