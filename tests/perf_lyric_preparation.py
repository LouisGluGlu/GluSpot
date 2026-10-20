"""Uncached lyric CPU microbenchmark; distinct from surface-frame metrics.

Loads the exact previous surface source into an isolated module. The old
resolver's cache is invalidated on every call; the new pure preparation
includes both surface and island plans. No rendering, worker wait or profiler
is timed. Results include semantic equivalence checks and source digests.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from benchmark_music_surface import SyntheticOwner
from perf_music_surface import distribution
from api.models import TimedLyricUnit
from local.music_lyric_preparation import prepare_lyric_presentation, snapshot_lyric_input
from PySide6.QtWidgets import QApplication


def mixed_owner(translated):
    owner = SyntheticOwner("unit-slow")
    owner.media_track_id = "micro-mixed-duet"
    owner.artist = "Alice / Bob"
    owner.enable_dual_line_lyrics = translated
    owner.timed_lyrics = [
        (0., "作词 Lyricist：A"), (8., "Shine on\n翻译：继续闪耀"),
        (15., "Alice: Keep singing\n翻译：继续唱"),
        (15., "Bob: We sing together\n翻译：一起唱"),
        (18., "(ah)"), (24., "Next sentence"),
    ]
    owner.lyrics = [text for _start, text in owner.timed_lyrics]
    owner.timed_lyric_units = [(),
        (TimedLyricUnit(8., 9., "Shine "), TimedLyricUnit(9., 10., "on")),
        (TimedLyricUnit(15., 17., "Alice: Keep "), TimedLyricUnit(17., 19., "singing")),
        (TimedLyricUnit(15., 18., "Bob: We sing "), TimedLyricUnit(18., 21., "together")),
        (TimedLyricUnit(18., 22., "(ah)"),), ()]
    owner.timed_lyric_timing_kinds = ["line", "unit", "unit", "unit", "unit", "line"]
    return owner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--platform", default="windows")
    args = parser.parse_args()
    if args.samples < 30:
        raise ValueError("at least 30 measured preparations required")
    spec = importlib.util.spec_from_file_location("gluspot_surface_6415_micro", args.old_source)
    old_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old_module
    spec.loader.exec_module(old_module)
    app = QApplication.instance() or QApplication([])
    result = {"scope": "uncached CPU microbenchmark; no render; new includes surface+island plans",
              "samples_per_case": args.samples,
              "old_source_sha256": hashlib.sha256(Path(args.old_source).read_bytes()).hexdigest(),
              "new_source_sha256": hashlib.sha256((ROOT / "local/music_lyric_preparation.py").read_bytes()).hexdigest(),
              "cases": []}
    for name in ("long-song", "unit-slow", "mixed-translated", "mixed-original"):
        owner = mixed_owner(name == "mixed-translated") if name.startswith("mixed") else SyntheticOwner(name)
        window = old_module.MusicSurfaceWindow(owner)
        window._timer.stop()
        window._audio_output_status_timer.stop()
        translated = bool(getattr(owner, "enable_dual_line_lyrics", False))
        window._translation_render_enabled = lambda _now: translated
        source = snapshot_lyric_input(owner)
        def before():
            window._display_lyrics_cache_key = None
            window._display_lyrics_cache = None
            rows = window._resolve_display_lyrics()
            return (tuple(rows[0]), tuple(rows[1]), window._display_timed_units,
                    window._display_timed_kinds, window._display_row_timings,
                    window._display_raw_source_texts, window._display_timed_read_ends,
                    window._display_duet_presentation)
        def after():
            return prepare_lyric_presentation(source)
        def normalized(value):
            return (value.lines, value.timed, value.units, value.kinds, value.timings,
                    value.raw_source_texts, value.read_ends, value.duet)
        try:
            start = time.perf_counter()
            old_value = before()
            first_before = (time.perf_counter()-start)*1000
            start = time.perf_counter()
            new_value = after()
            first_after = (time.perf_counter()-start)*1000
            if old_value != normalized(new_value):
                fields = ("lines", "timed", "units", "kinds", "timings", "sources", "read_ends", "duet")
                bad = [field for field, old, new in zip(fields, old_value, normalized(new_value)) if old != new]
                raise AssertionError(f"{name} semantics changed: {bad}")
            old_ms, new_ms, snapshot_ms = [], [], []
            for index in range(args.samples):
                # Alternate order to reduce a systematic before/after bias.
                for key, callback, samples in (("before", before, old_ms), ("after", after, new_ms))[::(-1 if index % 2 else 1)]:
                    started = time.perf_counter()
                    value = callback()
                    samples.append((time.perf_counter()-started)*1000)
                    if (value if key == "before" else normalized(value)) != old_value:
                        raise AssertionError(f"{name} unstable output")
                started = time.perf_counter()
                snapshot_lyric_input(owner)
                snapshot_ms.append((time.perf_counter()-started)*1000)
            case = {"name": name, "first_before_ms": first_before, "first_after_ms": first_after,
                    "before": distribution(old_ms), "after": distribution(new_ms),
                    "gui_snapshot": distribution(snapshot_ms), "equivalent": True,
                    "timed_rows": len(new_value.timed), "island_plans": len(new_value.island_timings),
                    "genuine_unit_rows": sum(bool(row) for row in new_value.units)}
            result["cases"].append(case)
            print(name, json.dumps(case), flush=True)
        finally:
            window.shutdown()
            owner.close()
            app.processEvents()
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
