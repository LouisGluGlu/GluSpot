"""Capture opt-in and cross-platform import invariants."""
import subprocess
import sys
import unittest

from api.capture import FEATURE_SETTING, capture_enabled
from api.capture.contracts import TimelineSample


class CaptureContractTests(unittest.TestCase):
    def test_feature_defaults_off_and_requires_boolean(self):
        self.assertFalse(capture_enabled(None))
        for value in (None, False, 1, "true", "false"):
            self.assertFalse(capture_enabled(lambda _key, _default: value))
        self.assertTrue(capture_enabled(lambda key, default: key == FEATURE_SETTING))

    def test_import_has_no_native_dependencies_or_workers(self):
        result = subprocess.run([sys.executable, "-c", "import sys, threading; "
                                 "import api.capture.contracts; "
                                 "assert not any(n.startswith(('winsdk', 'winrt', 'pywinauto', 'comtypes', 'PySide6')) for n in sys.modules); "
                                 "assert len(threading.enumerate()) == 1"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_identity_separates_players_and_tracks(self):
        sample = TimelineSample("cloudmusic.exe", "song", 10, 20, 30)
        self.assertEqual(sample.identity, ("cloudmusic.exe", "song"))
