from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from local import mydockfinder


@unittest.skipUnless(sys.platform == "win32", "Windows process queries")
class DockProcessFastPathTests(unittest.TestCase):
    def setUp(self):
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.addCleanup(setattr, mydockfinder, "_last_dock_process", None)

    def test_live_pid_with_matching_image_name_is_still_running(self):
        name = os.path.basename(sys.executable)
        self.assertTrue(mydockfinder._still_running_dock_process(self.kernel32, os.getpid(), name.upper()))

    def test_live_pid_with_another_image_name_is_rejected(self):
        self.assertFalse(mydockfinder._still_running_dock_process(self.kernel32, os.getpid(), "Dock_64.exe"))

    def test_exited_pid_is_rejected(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.assertFalse(mydockfinder._still_running_dock_process(
            self.kernel32, child.pid, os.path.basename(sys.executable)))

    def test_remembered_dock_skips_the_full_scan_while_it_is_alive(self):
        mydockfinder._last_dock_process = (1234, "Dock_64.exe")
        with patch.object(mydockfinder, "_still_running_dock_process", return_value=True), \
                patch.object(mydockfinder, "_process_entry32w_type", side_effect=AssertionError("full scan")):
            self.assertEqual(mydockfinder._running_mydockfinder_process(), "Dock_64.exe")

    def test_a_gone_dock_is_forgotten_and_the_full_scan_decides(self):
        mydockfinder._last_dock_process = (1234, "Dock_64.exe")
        with patch.object(mydockfinder, "_still_running_dock_process", return_value=False), \
                patch.object(mydockfinder, "MYDOCKFINDER_PROCESS_NAMES", frozenset({"no-such-dock.exe"})):
            self.assertIsNone(mydockfinder._running_mydockfinder_process())
        self.assertIsNone(mydockfinder._last_dock_process)


if __name__ == "__main__":
    unittest.main()
