"""Run the narrow regression group with native Windows Qt and private caches."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

REPO = Path(r"E:\grok-gluspot\_wt_capture2")
MODULES = (
    "tests.test_narrow_chrome", "tests.test_narrow_content_layouts",
    "tests.test_narrow_back_pc_regression", "tests.test_narrow_back_resize",
    "tests.test_library_back_owned_playback", "tests.test_narrow_shared_runtime",
    "tests.test_narrow_flicker_v2",
    "tests.test_music_surface_layouts", "tests.test_music_native_display_cache",
    "tests.test_music_width_breakpoint", "tests.test_narrow_width_breakpoint",
    "tests.test_narrow_chrome_slide", "tests.test_lyric_singer_tags",
    "tests.test_narrow_background_veil", "tests.test_lyric_fit_width",
    "tests.test_mac_preferences_shell", "tests.test_music_continuous_lift",
    "tests.test_music_fullscreen", "tests.test_music_lyric_reading",
    "tests.test_music_glass_level", "tests.test_music_surface_glass",
    "tests.test_liquid_glass_music_surfaces",
    "tests.test_reading_single_scheme",
)
# This older unbound-method harness predates the mounted shared renderer.
# Box baseline and final both produce 25 errors in its 15 tests. Preserve its
# evidence separately so an inherited harness failure cannot prevent capture.
BASELINE_DIAGNOSTICS = ("tests.test_narrow_live_lag",)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("Native Windows Qt verification only")
    os.chdir(REPO)
    sys.path[:0] = [str(REPO), str(REPO / "tests")]
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="gluspot-mega-tests-") as temporary:
        os.environ["GLUSPOT_LYRICS_CACHE_DIR"] = str(Path(temporary) / "lyrics")
        os.environ["GLUSPOT_ALBUM_ART_CACHE_DIR"] = str(Path(temporary) / "artwork")
        with (args.out / "focused-unittest.log").open("w", encoding="utf-8") as log:
            with redirect_stdout(log), redirect_stderr(log):
                suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
                result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
        report = dict(modules=MODULES, tests=result.testsRun, passed=result.wasSuccessful(),
                      failures=[test.id() for test, _ in result.failures],
                      errors=[test.id() for test, _ in result.errors],
                      skips=[dict(test=test.id(), reason=reason) for test, reason in result.skipped],
                      seconds=round(time.monotonic() - started, 3), qt_platform="windows")
        with (args.out / "baseline-diagnostics.log").open("w", encoding="utf-8") as log:
            with redirect_stdout(log), redirect_stderr(log):
                suite = unittest.defaultTestLoader.loadTestsFromNames(BASELINE_DIAGNOSTICS)
                diagnostic = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
        report["baseline_diagnostics"] = dict(
            modules=BASELINE_DIAGNOSTICS, gating=False, tests=diagnostic.testsRun,
            failures=[test.id() for test, _ in diagnostic.failures],
            errors=[test.id() for test, _ in diagnostic.errors],
            box_baseline="15 tests; 25 errors; unbound harness calls delegated super()",
            log="baseline-diagnostics.log")
        (args.out / "focused-tests.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
