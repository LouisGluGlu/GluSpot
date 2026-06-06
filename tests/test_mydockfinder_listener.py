from __future__ import annotations

import unittest
from unittest.mock import patch

from local.app import _mydockfinder_process_diagnostics, _mydockfinder_process_running
from local.settings_store import DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES


class MyDockFinderListenerTests(unittest.TestCase):
    def test_default_mydockfinder_listener_scans_only_dock_process_without_configured_path(self):
        with patch(
            "local.app._running_process_snapshots_for_names",
            return_value=[
                (
                    "dock.exe",
                    r"B:\SteamLibrary\steamapps\common\MyDockFinder\dock.exe",
                ),
            ],
        ):
            diagnostics = _mydockfinder_process_diagnostics("")

        self.assertTrue(diagnostics["running"])
        self.assertEqual(DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES, ("dock.exe",))
        self.assertEqual(diagnostics["candidate_names"], ["dock.exe"])
        self.assertEqual(diagnostics["matched_name"], "dock.exe")

    def test_default_listener_ignores_stale_mydock_service_process(self):
        with patch(
            "local.app._running_process_snapshots_for_names",
            return_value=[
                (
                    "Mydock.exe",
                    "",
                )
            ],
        ):
            diagnostics = _mydockfinder_process_diagnostics("")

        self.assertFalse(diagnostics["running"])
        self.assertNotIn("mydock.exe", diagnostics["candidate_names"])

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
            running = _mydockfinder_process_running(
                r"B:\SteamLibrary\steamapps\common\MyDockFinder",
                listener_process_names=("dockmod64.exe",),
            )

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
            running = _mydockfinder_process_running(
                r"C:\Program Files\MyDockFinder",
                listener_process_names=("mydockfinder.exe",),
            )

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

    def test_configured_dock_launcher_does_not_match_mydock_runtime_process_without_path_access_by_default(self):
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

        self.assertFalse(running)

    def test_user_selected_mydock_listener_can_match_mydock_runtime_process_without_path_access(self):
        with patch(
            "local.app._running_process_snapshots_for_names",
            return_value=[
                (
                    "Mydock.exe",
                    "",
                )
            ],
        ):
            running = _mydockfinder_process_running(
                r"B:\SteamLibrary\steamapps\common\MyDockFinder\dock.exe",
                listener_process_names=("dock.exe", "mydock.exe"),
            )

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
            diagnostics = _mydockfinder_process_diagnostics(
                r"B:\SteamLibrary\steamapps\common\MyDockFinder\dock.exe",
                listener_process_names=("mydock.exe",),
            )

        self.assertTrue(diagnostics["running"])
        self.assertIn("mydock.exe", diagnostics["candidate_names"])
        self.assertEqual(diagnostics["matched_name"], "Mydock.exe")
        self.assertIn("process name", diagnostics["reason"])


if __name__ == "__main__":
    unittest.main()
