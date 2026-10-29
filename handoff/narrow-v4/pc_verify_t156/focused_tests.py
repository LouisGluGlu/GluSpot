"""Run t156 regressions with disposable settings and cache paths on Windows."""
from __future__ import annotations
import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(r"E:\grok-gluspot\_wt_capture2")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "nt" or os.environ.get("QT_QPA_PLATFORM") != "windows":
        raise SystemExit("Native Windows Qt only")
    os.chdir(REPO)
    sys.path[:0] = [str(REPO), str(REPO / "tests")]
    names = sorted("tests." + path.stem for path in (REPO / "tests").glob("test_*t156*.py"))
    if not names:
        raise SystemExit("No committed t156 acceptance tests found")
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gluspot-t156-tests-") as temporary:
        os.environ["GLUSPOT_LYRICS_CACHE_DIR"] = str(Path(temporary) / "lyrics")
        os.environ["GLUSPOT_ALBUM_ART_CACHE_DIR"] = str(Path(temporary) / "artwork")
        import local.settings_store as store
        original = store.SettingsManager.__init__
        def private_settings(self, *, settings_path=None):
            if settings_path is not None:
                if not Path(settings_path).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
                    raise RuntimeError("Test settings must remain temporary")
            original(self, settings_path=settings_path or str(Path(temporary) / "settings.json"))
        store.SettingsManager.__init__ = private_settings
        try:
            with (args.out / "focused-unittest.log").open("w", encoding="utf-8") as log:
                with redirect_stdout(log), redirect_stderr(log):
                    result = unittest.TextTestRunner(stream=log, verbosity=2).run(
                        unittest.defaultTestLoader.loadTestsFromNames(names))
        finally:
            store.SettingsManager.__init__ = original
    report = dict(modules=names, tests=result.testsRun, passed=result.wasSuccessful(),
        failures=[test.id() for test, _ in result.failures],
        errors=[test.id() for test, _ in result.errors], qt_platform="windows")
    (args.out / "focused-tests.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
