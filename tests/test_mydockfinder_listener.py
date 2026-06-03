from __future__ import annotations

import unittest
from unittest.mock import patch

from local.app import _mydockfinder_process_diagnostics, _mydockfinder_process_running


class MyDockFinderListenerTests(unittest.TestCase):
    def test_steam_mydockfinder_runtime_processes_are_detected_without_configured_path(self):
        with patch(
            "local.app._running_process_snapshots_for_names",
            return_value=[
                (
                    "Dock_64.exe",
                    r"B:\SteamLibrary\steamapps\common\MyDockFinder\dock_64.exe",
                ),
                (
                    "Dockmod.exe",
                    r"B:\SteamLibrary\steamapps\common\MyDockFinder\dockmod.exe",
                ),
                (
                    "Dockmod64.exe",
                    r"B:\SteamLibrary\steamapps\common\MyDockFinder\Dockmod64.exe",
                ),
            ],
        ):
            diagnostics = _mydockfinder_process_diagnostics("")

        self.assertTrue(diagnostics["running"])
        self.assertIn("dock_64.exe", diagnostics["candidate_names"])
        self.assertIn("dockmod.exe", diagnostics["candidate_names"])
        self.assertIn("dockmod64.exe", diagnostics["candidate_names"])
        self.assertEqual(diagnostics["matched_name"], "Dock_64.exe")

    def test_configured_steam_folder_matches_dockmod_runtime_process(self):
        with patch(
            "local.app._running_process_snapshots_for_names",
            return_value=[
                (
                    "Dockmod64.exe",
                    r"B:\SteamLibrary\steamapps\common\MyDockFinder\Dockmod64.exe",
                )
            ],
        ):
            running = _mydockfinder_process_running(r"B:\SteamLibrary\steamapps\common\MyDockFinder")

        self.assertTrue(running)

    def test_configured_mydockfinder_folder_matches_running_process_inside_folder(self):
        with patch(
            "local.app._running_process_snapshots_for_names",
            return_value=[
                (
                    "MyDockFinder.exe",
                    r"C:\Program Files\MyDockFinder\MyDockFinder.exe",
                )
            ],
        ):
            running = _mydockfinder_process_running(r"C:\Program Files\MyDockFinder")

        self.assertTrue(running)

    def test_configured_mydockfinder_executable_matches_exact_running_path(self):
        with patch(
            "local.app._running_process_snapshots_for_names",
            return_value=[
                (
                    "MyDockFinder.exe",
                    r"D:\Tools\MyDockFinder\MyDockFinder.exe",
                )
            ],
        ):
            running = _mydockfinder_process_running(r"D:\Tools\MyDockFinder\MyDockFinder.exe")

        self.assertTrue(running)

    def test_configured_dock_launcher_matches_mydock_runtime_process_without_path_access(self):
        with patch(
            "local.app._running_process_snapshots_for_names",
            return_value=[
                (
                    "Mydock.exe",
                    "",
                )
            ],
        ):
            running = _mydockfinder_process_running(r"B:\SteamLibrary\steamapps\common\MyDockFinder\dock.exe")

        self.assertTrue(running)

    def test_listener_diagnostics_explain_scanned_processes(self):
        with patch(
            "local.app._running_process_snapshots_for_names",
            return_value=[
                (
                    "Mydock.exe",
                    "",
                )
            ],
        ):
            diagnostics = _mydockfinder_process_diagnostics(r"B:\SteamLibrary\steamapps\common\MyDockFinder\dock.exe")

        self.assertTrue(diagnostics["running"])
        self.assertIn("mydock.exe", diagnostics["candidate_names"])
        self.assertEqual(diagnostics["matched_name"], "Mydock.exe")
        self.assertIn("process name", diagnostics["reason"])


if __name__ == "__main__":
    unittest.main()
