from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from local.app import LocalIslandApp, _mydockfinder_process_diagnostics, _mydockfinder_process_running
from local.settings_store import DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES
from local.settings_store import DEFAULT_MUSIC_AMBIENCE_VALUES


class MyDockFinderListenerTests(unittest.TestCase):
    def test_default_mydockfinder_listener_scans_both_main_dock_executables_without_configured_path(self):
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
        self.assertEqual(DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES, ("dock.exe", "dock_64.exe"))
        self.assertEqual(diagnostics["candidate_names"], ["dock.exe", "dock_64.exe"])
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

    def test_live_finder_probe_uses_only_a_trusted_matching_runtime(self):
        prior = [{"hwnd": 41, "enabled": True}]
        bar = {"hwnd": 41, "enabled": True, "visible": True}
        with patch("local.app._running_process_snapshots_for_names", return_value=[("dock_64.exe", r"F:\MyDockFinder\dock_64.exe")]), \
                patch("local.app.read_live_finder_bars", return_value=[bar]) as probe:
            diagnostics = _mydockfinder_process_diagnostics(r"F:\MyDockFinder", previous_bars=prior)
        probe.assert_called_once_with(matched_name="dock_64.exe", matched_path=r"F:\MyDockFinder\dock_64.exe", previous_bars=prior)
        self.assertTrue(diagnostics["running"])
        self.assertTrue(diagnostics["finder_enabled"])
        self.assertEqual(diagnostics["finder_bars"], [bar])

    def test_unmatched_runtime_and_installed_path_cannot_probe_or_enable_finder(self):
        with patch("local.app._running_process_snapshots_for_names", return_value=[("dock_64.exe", r"F:\Other\dock_64.exe")]), \
                patch("local.app.read_live_finder_bars") as probe:
            diagnostics = _mydockfinder_process_diagnostics(r"F:\MyDockFinder")
        probe.assert_not_called()
        self.assertFalse(diagnostics["running"])
        self.assertFalse(diagnostics["finder_enabled"])
        self.assertEqual(diagnostics["finder_bars"], [])

    def test_running_dock_without_finder_does_not_report_finder_enabled(self):
        with patch("local.app._running_process_snapshots_for_names", return_value=[("dock_64.exe", r"F:\MyDockFinder\dock_64.exe")]), \
                patch("local.app.read_live_finder_bars", return_value=[]):
            diagnostics = _mydockfinder_process_diagnostics("")
        self.assertTrue(diagnostics["running"])
        self.assertFalse(diagnostics["finder_enabled"])

    def test_first_launcher_match_does_not_hide_finder_owned_by_another_trusted_runtime(self):
        bar = {"hwnd": 41, "enabled": True, "visible": True}
        def probe(*, matched_name, **_kwargs):
            return [bar] if matched_name == "dock_64.exe" else []
        with patch("local.app._running_process_snapshots_for_names", return_value=[
            ("dock.exe", r"F:\MyDockFinder\dock.exe"), ("dock_64.exe", r"F:\MyDockFinder\dock_64.exe")]), \
                patch("local.app.read_live_finder_bars", side_effect=probe):
            diagnostics = _mydockfinder_process_diagnostics("")
        self.assertEqual(diagnostics["matched_name"], "dock.exe")
        self.assertTrue(diagnostics["finder_enabled"])
        self.assertEqual(diagnostics["finder_bars"], [bar])

    def test_poll_updates_finder_snapshot_even_when_dock_running_state_is_unchanged(self):
        previous = [{"hwnd": 41, "enabled": True}]
        updated = [{"hwnd": 41, "enabled": True, "physical_rect": [0, 0, 1920, 24]}]
        window = SimpleNamespace(
            mydockfinder_path="", mydockfinder_listener_process_names=DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES,
            mydockfinder_running=True, mydockfinder_diagnostics={"finder_bars": previous},
            set_mydockfinder_diagnostics=Mock(), set_mydockfinder_running=Mock(), set_finder_bar_presence=Mock(),
        )
        app = SimpleNamespace(settings={}, window=window, settings_window=SimpleNamespace(sync_from_widget=Mock()),
            _mydockfinder_listener_process_names_setting=lambda: DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES)
        with patch("local.app._mydockfinder_process_diagnostics", return_value={"running": True, "finder_bars": updated}) as diagnostics:
            LocalIslandApp._poll_mydockfinder_presence(app)
        diagnostics.assert_called_once_with("", listener_process_names=DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES, previous_bars=previous)
        window.set_mydockfinder_running.assert_not_called()
        window.set_finder_bar_presence.assert_called_once_with(updated, notify=False)
        app.settings_window.sync_from_widget.assert_called_once_with()

    def test_startup_attaches_finder_only_after_saved_style_ratio_and_position_preset(self):
        bar = {"hwnd": 41, "enabled": True, "visible": True}
        window = Mock(plugin_focus_target=0)
        app = SimpleNamespace(settings={"v2_finder_bar_style": "feathered", "v2_finder_bar_center_ratio": 0.3}, window=window,
            _mydockfinder_listener_process_names_setting=lambda: DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES,
            _location_mode_setting=lambda: "top",
            _music_ambience_values_for_style=lambda style: dict(DEFAULT_MUSIC_AMBIENCE_VALUES[style]))
        with patch("local.app._mydockfinder_process_diagnostics", return_value={"running": True, "finder_bars": [bar]}):
            LocalIslandApp._apply_saved_settings(app)
        calls = [item[0] for item in window.method_calls]
        self.assertLess(calls.index("set_finder_bar_style"), calls.index("set_mydockfinder_running"))
        self.assertLess(calls.index("set_finder_bar_center_ratio"), calls.index("set_mydockfinder_running"))
        self.assertLess(calls.index("apply_position_preset"), calls.index("set_finder_bar_presence"))
        self.assertEqual(calls[-1], "set_finder_bar_presence")
        window.set_finder_bar_presence.assert_called_once_with([bar], notify=False)


if __name__ == "__main__":
    unittest.main()
