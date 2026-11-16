from __future__ import annotations

import ctypes
import sys
import unittest


@unittest.skipUnless(sys.platform == "win32", "Windows process and window enumeration")
class NativeStructLeakTests(unittest.TestCase):
    def test_repeated_process_and_window_scans_do_not_grow_ctypes_type_cache(self):
        # A ctypes.Structure defined inside a function is a new class per call, and
        # POINTER(cls) keeps every one alive in ctypes' pointer cache (~46 MB/min live).
        from local import app, mydockfinder

        scans = (
            mydockfinder._running_mydockfinder_process,
            lambda: mydockfinder._native_finder_bar_window_snapshots("not-running.exe", ""),
            lambda: app._running_process_snapshots_for_names({"not-running.exe"}),
        )
        for scan in scans:
            scan()
        before = len(ctypes._pointer_type_cache)
        for _ in range(5):
            for scan in scans:
                scan()
        self.assertEqual(len(ctypes._pointer_type_cache), before)


if __name__ == "__main__":
    unittest.main()
