"""Short native-QImage cache lifecycle probe; never imports or mutates production caches.

This separates OrderedDict eviction/clear bookkeeping from the final native pixel
release. QImage copies are implicitly shared, so every case reports whether a
separate owner still keeps the pixel storage alive. It is a diagnostic fixture,
not an application FPS benchmark.
"""
from __future__ import annotations

import argparse
import ctypes
from collections import OrderedDict
import gc
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time
from types import SimpleNamespace
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QColor


def private_bytes() -> int | None:
    if sys.platform != "win32":
        return None
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            *[(key, ctypes.c_size_t) for key in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")],
        ]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    process = kernel.GetCurrentProcess()
    counters = Counters(); counters.cb = ctypes.sizeof(counters)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
        return None
    return int(counters.PrivateUsage)


def ms(fn):
    began = time.perf_counter(); value = fn(); return value, (time.perf_counter() - began) * 1000.


def image(size: tuple[int, int], color: str = "white") -> QImage:
    item = QImage(QSize(*size), QImage.Format.Format_ARGB32_Premultiplied)
    item.fill(QColor(color)); return item


def distribution(values):
    if not values: return {"n": 0}
    ordered = sorted(values)
    rank = lambda f: ordered[max(0, math.ceil(len(ordered) * f) - 1)]
    return {"n": len(values), "p50_ms": statistics.median(values),
            "p95_ms": rank(.95), "max_ms": ordered[-1]}


def run_worker(args):
    if args.variant.startswith("production-control-"):
        return run_control_worker(args)
    gc.collect()
    before = private_bytes()
    width, height = args.width, args.height
    cache = OrderedDict()
    keepers = []
    alias = args.variant.startswith("shared-alias")
    external = args.variant.endswith("external-owner")
    shared = image((width, height), "#7384aa") if alias else None
    add_times = []
    for index in range(args.entries):
        began = time.perf_counter()
        item = QImage(shared) if alias else image((width, height), "#7384aa")
        cache[index] = QImage(item)
        if external and not alias:
            keepers.append(item)
        add_times.append((time.perf_counter() - began) * 1000.)
    # Remove this often-overlooked loop local: otherwise the final cache item
    # still has an external pixel owner after clear() and the test lies.
    item = None
    if alias and not external:
        shared = None
    unique = {item.cacheKey(): item.sizeInBytes() for item in cache.values()}
    after_add = private_bytes()
    bookkeeping_times, final_release_times = [], []
    for _ in range(min(args.evictions, len(cache))):
        began = time.perf_counter()
        _key, old = cache.popitem(last=False)
        bookkeeping_times.append((time.perf_counter() - began) * 1000.)
        # pop returns a live QImage shell. Time its retirement separately;
        # this drops native pixel storage only when it is the final owner.
        began = time.perf_counter(); old = None
        final_release_times.append((time.perf_counter() - began) * 1000.)
    after_evict = private_bytes()
    began = time.perf_counter(); cache.clear()
    clear_ms = (time.perf_counter() - began) * 1000.
    after_clear = private_bytes()
    began = time.perf_counter(); keepers.clear(); shared = None
    external_release_ms = (time.perf_counter() - began) * 1000.
    gc.collect()
    after_release = private_bytes()
    print(json.dumps({
        "variant": args.variant, "entries": args.entries, "evictions": args.evictions,
        "image_size": [width, height], "bytes_each": width * height * 4,
        "summed_shell_size_bytes": width * height * 4 * args.entries,
        "unique_cache_keys": len(unique), "unique_key_size_bytes": sum(unique.values()),
        "before_private_bytes": before, "after_add_private_bytes": after_add,
        "after_evict_private_bytes": after_evict, "after_clear_private_bytes": after_clear,
        "after_release_private_bytes": after_release,
        "add_ms": distribution(add_times), "evict_bookkeeping_ms": distribution(bookkeeping_times),
        "evicted_shell_release_ms": distribution(final_release_times),
        "clear_ms": clear_ms, "external_owner_release_ms": external_release_ms,
        "cache_len_after_clear": len(cache), "external_pixel_owner_until_end": external,
        "notes": "QImage cache keys identify shared pixels in this fixture; PrivateUsage includes allocator noise.",
    }, ensure_ascii=False))


def run_control_worker(args):
    """Exercise the existing production insertion method, without QWidget paint.

    Allocations happen before the timed call. Each inserted image loses its
    external owner immediately, so later evictions can release the real pixels.
    This isolates lifecycle cost; it is not an animation or frame measurement.
    """
    import hashlib
    import inspect
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from local.music_surface_window import MusicSurfaceWindow

    method = MusicSurfaceWindow._remember_control_layer
    byte_limit = MusicSurfaceWindow.CONTROL_LAYER_CACHE_MAX_BYTES
    owner = SimpleNamespace(_control_layer_cache={}, _control_layer_cache_bytes=0,
                            CONTROL_LAYER_CACHE_MAX_BYTES=byte_limit)
    count_case = args.variant.endswith("count")
    size, entries = ((512, 64), 80) if count_case else ((1024, 1024), 24)
    private_before = private_bytes()
    cold_times, evict_times = [], []
    evictions, peak_entries, peak_bytes = 0, 0, 0
    for index in range(entries):
        item = image(size, "#7384aa")
        old_count = len(owner._control_layer_cache)
        began = time.perf_counter()
        method(owner, (index,), item)
        elapsed = (time.perf_counter() - began) * 1000.
        dropped = old_count + 1 - len(owner._control_layer_cache)
        evictions += dropped
        (evict_times if dropped else cold_times).append(elapsed)
        peak_entries = max(peak_entries, len(owner._control_layer_cache))
        peak_bytes = max(peak_bytes, owner._control_layer_cache_bytes)
        item = None
    actual_bytes = sum(int(item.sizeInBytes()) for item in owner._control_layer_cache.values())
    if actual_bytes != owner._control_layer_cache_bytes:
        raise AssertionError("production byte counter disagrees with actual images")
    private_full = private_bytes()
    began = time.perf_counter(); owner._control_layer_cache.clear()
    clear_ms = (time.perf_counter() - began) * 1000.
    owner._control_layer_cache_bytes = 0
    print(json.dumps({
        "variant": args.variant, "method": method.__qualname__,
        "method_source_sha256": hashlib.sha256(inspect.getsource(method).encode()).hexdigest(),
        "scope": "existing production cache insertion method; no widget draw and no FPS claim",
        "image_size": list(size), "insertions": entries, "evictions": evictions,
        "production_byte_limit": byte_limit, "production_count_limit": 48,
        "peak_retained_entries": peak_entries, "peak_retained_bytes": peak_bytes,
        "bytes_counter_verified": True,
        "insert_without_eviction_ms": distribution(cold_times),
        "insert_with_final_owner_eviction_ms": distribution(evict_times),
        "final_owner_bulk_clear_ms": clear_ms,
        "before_private_bytes": private_before, "full_private_bytes": private_full,
        "after_clear_private_bytes": private_bytes(),
        "notes": "Allocation excluded; byte sum counts independent QImages here. PrivateUsage includes allocator noise.",
    }, ensure_ascii=False))


def run_parent(args):
    target = Path(args.output); target.parent.mkdir(parents=True, exist_ok=True)
    cases = []
    for variant in ("unique-last-owner", "unique-external-owner", "shared-alias-last-owner", "shared-alias-external-owner",
                    "production-control-count", "production-control-bytes"):
        command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--variant", variant,
                   "--entries", str(args.entries), "--evictions", str(args.evictions),
                   "--width", str(args.width), "--height", str(args.height)]
        proc = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if proc.returncode:
            raise RuntimeError(proc.stdout + proc.stderr)
        cases.append(json.loads(proc.stdout.strip().splitlines()[-1]))
    result = {
        "scope": "fresh child-process Qt QImage ownership plus production control-cache insertion probes; not app FPS",
        "entries": args.entries, "evictions": args.evictions,
        "image_size": [args.width, args.height], "cases": cases,
        "all_samples_unprofiled": True,
    }
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(target), "cases": len(cases)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--output", default="logs/glyph-cache-eviction-probe.json")
    parser.add_argument("--entries", type=int, default=160)
    parser.add_argument("--evictions", type=int, default=64)
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=52)
    parser.add_argument("--variant", default="unique-last-owner")
    args = parser.parse_args()
    (run_worker if args.worker else run_parent)(args)


if __name__ == "__main__": main()
