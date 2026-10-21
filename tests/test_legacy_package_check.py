from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from local import legacy_package_check as check


class LegacyPackageCheckBoundaryTests(unittest.TestCase):
    def test_cli_dispatch_isolated_from_gui_and_propagates_failure(self):
        entry = importlib.import_module("local.main")
        with patch.dict(sys.modules, {"local.app": None}), patch.object(sys, "stdout", None), \
                patch.object(check, "run_legacy_package_check", return_value=1) as run:
            self.assertEqual(entry.main(["--legacy-package-check", "兼容验收.json"]), 1)
        run.assert_called_once_with("兼容验收.json")

    def test_invalid_or_mixed_diagnostic_arguments_cannot_start_gui(self):
        entry = importlib.import_module("local.main")
        malformed = (
            ["--legacy-package-check"],
            ["--legacy-package-check", "report.json", "extra"],
            ["report.json", "--legacy-package-check"],
            ["--audio-package-check", "--legacy-package-check", "report.json"],
            ["--installer-handoff", "--legacy-package-check", "report.json"],
        )
        with patch.dict(sys.modules, {"local.app": None}), patch.object(sys, "stderr", None), \
                patch.object(check, "run_legacy_package_check") as run:
            for arguments in malformed:
                with self.subTest(arguments=arguments):
                    self.assertEqual(entry.main(arguments), 2)
        run.assert_not_called()

    def test_modern_runtime_cannot_pass_or_import_legacy_qt(self):
        initial_hook = sys.excepthook
        forbidden_imports = {"local.app": None, "PySide2": None,
            "PySide6": None, "PySide6.QtWidgets": None}
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(sys.modules, forbidden_imports), \
                patch.object(check.sys, "version_info", (3, 12, 10, "final", 0)), \
                patch.object(check, "_policy_check") as policy, \
                patch.object(check, "_media_check") as media, \
                patch.object(check, "_surface_check") as surface, \
                patch.object(sys, "stdout", None), patch.object(sys, "stderr", None):
            target = Path(directory) / "reports" / "兼容验收.json"
            self.assertEqual(check.run_legacy_package_check(target), 1)
            report = json.loads(target.read_text(encoding="utf-8"))
        self.assertFalse(report["passed"])
        self.assertEqual(report["checks"], [{"name": "python_38_runtime", "passed": False}])
        self.assertIn("python_38_runtime", report["errors"][0])
        self.assertIn("process_bits", report["host"])
        self.assertEqual(report["qt_callback_errors"], [])
        self.assertIs(sys.excepthook, initial_hook)
        policy.assert_not_called()
        media.assert_not_called()
        surface.assert_not_called()

    def test_unwritable_report_is_distinct_from_runtime_rejection(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(check.sys, "version_info", (3, 12, 10, "final", 0)), \
                patch.object(sys, "stderr", None):
            # An existing directory cannot become the diagnostic JSON file.
            self.assertEqual(check.run_legacy_package_check(directory), 2)


if __name__ == "__main__":
    unittest.main()
