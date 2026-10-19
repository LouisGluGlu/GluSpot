from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from local.app import LocalIslandApp, _mydockfinder_process_diagnostics, _mydockfinder_process_running
from local.settings_store import DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES, MYDOCKFINDER_LISTEN_PROCESS_CHOICES
from local.settings_store import DEFAULT_MUSIC_AMBIENCE_VALUES


class MyDockFinderListenerTests(unittest.TestCase):
    def test_runtime_permission_is_main_runtimes_only_despite_retired_selection(self):
        for old_selection in ([], ["dock.exe"], ["unknown.exe"], ["myfinder.exe", "dock_64.exe"]):
            with self.subTest(old_selection=old_selection):
                app = SimpleNamespace(settings={"v2_mydockfinder_listener_process_names": old_selection})
                self.assertEqual(LocalIslandApp._mydockfinder_listener_process_names_setting(app), ("dock.exe", "dock_64.exe"))
                self.assertEqual(app.settings["v2_mydockfinder_listener_process_names"], old_selection)

    def test_default_listener_scans_only_main_executables_case_insensitively(self):
        with patch("local.app._running_process_snapshots_for_names", return_value=[
                ("Dock.EXE", r"F:\MyDockFinder\dock.exe")]) as scan, \
                patch("local.app.read_live_finder_bars", return_value=[]):
            diagnostics = _mydockfinder_process_diagnostics("")
        scan.assert_called_once_with({"dock.exe", "dock_64.exe"})
        self.assertTrue(diagnostics["running"])
        self.assertEqual(DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES, ("dock.exe", "dock_64.exe"))
        self.assertEqual(MYDOCKFINDER_LISTEN_PROCESS_CHOICES, (("dock.exe", "dock.exe"), ("dock_64.exe", "Dock_64.exe")))
        self.assertEqual(diagnostics["candidate_names"], ["dock.exe", "dock_64.exe"])
        self.assertEqual(diagnostics["listener_process_names"], ["dock.exe", "dock_64.exe"])
        self.assertEqual(diagnostics["matched_name"], "Dock.EXE")

    def test_legacy_or_unknown_executable_cannot_expand_current_runtime_contract(self):
        for name in ("MyDockFinder.exe", "MyFinder.exe", "Mydock.exe",
                     "Dockmod.exe", "Dockmod64.exe", "Other.exe"):
            for has_path in (True, False):
                with self.subTest(name=name, has_path=has_path):
                    configured = "F:/MyDockFinder/" + name
                    snapshot = (name, configured if has_path else "")
                    with patch("local.app._running_process_snapshots_for_names", return_value=[snapshot]) as scan, \
                            patch("local.app.read_live_finder_bars") as probe:
                        diagnostics = _mydockfinder_process_diagnostics(configured, listener_process_names=(name,))
                    scan.assert_called_once_with({"dock.exe", "dock_64.exe"})
                    self.assertFalse(diagnostics["running"])
                    self.assertFalse(diagnostics["finder_enabled"])
                    self.assertEqual(diagnostics["candidate_names"], ["dock.exe", "dock_64.exe"])
                    self.assertEqual(diagnostics["snapshots"], [])
                    probe.assert_not_called()

    def test_64_bit_main_runtime_is_detected_independently_of_service_startup_and_path_access(self):
        bar = {"hwnd": 41, "pid": 22224, "enabled": True, "visible": True}
        for name in ("dock.exe", "DOCK_64.EXE"):
            for path in ("", "F:/MyDockFinder/" + name):
                with self.subTest(name=name, path=path), \
                        patch("local.app._running_process_snapshots_for_names", return_value=[
                            ("Mydock.exe", ""), ("Dockmod.exe", ""), ("Dockmod64.exe", ""), (name, path)]), \
                        patch("local.app.read_live_finder_bars", return_value=[bar]) as probe:
                    diagnostics = _mydockfinder_process_diagnostics("C:/Old/dock.exe", listener_process_names=[])
                self.assertTrue(diagnostics["running"])
                self.assertTrue(diagnostics["finder_enabled"])
                self.assertEqual(diagnostics["snapshots"], [{"name": name, "path": path}])
                self.assertEqual(diagnostics["finder_bars"], [bar])
                probe.assert_called_once_with(matched_name=name, matched_path=path, previous_bars=())

    def test_configured_folder_keeps_live_dock_exe_detection(self):
        with patch("local.app._running_process_snapshots_for_names", return_value=[
                ("dock.exe", r"F:\MyDockFinder\dock.exe")]), \
                patch("local.app.read_live_finder_bars", return_value=[]):
            self.assertTrue(_mydockfinder_process_running(r"F:\MyDockFinder", listener_process_names=("MyFinder.exe",)))

    def test_configured_dock_executable_matches_exact_running_path(self):
        with patch("local.app._running_process_snapshots_for_names", return_value=[
                ("Dock.exe", r"F:\MyDockFinder\dock.exe")]), \
                patch("local.app.read_live_finder_bars", return_value=[]):
            self.assertTrue(_mydockfinder_process_running(r"F:\MyDockFinder\DOCK.EXE"))

    def test_dock_without_path_access_keeps_name_only_listener_boundary(self):
        with patch("local.app._running_process_snapshots_for_names", return_value=[("DOCK.EXE", "")]), \
                patch("local.app.read_live_finder_bars", return_value=[]) as probe:
            diagnostics = _mydockfinder_process_diagnostics(r"F:\MyDockFinder\dock.exe",
                                                          listener_process_names=("myfinder.exe",))
        self.assertTrue(diagnostics["running"])
        self.assertEqual(diagnostics["candidate_names"], ["dock.exe", "dock_64.exe"])
        self.assertEqual(diagnostics["matched_name"], "DOCK.EXE")
        self.assertIn("process name", diagnostics["reason"])
        probe.assert_called_once_with(matched_name="DOCK.EXE", matched_path="", previous_bars=())

    def test_live_finder_probe_uses_only_a_trusted_matching_runtime(self):
        prior = [{"hwnd": 41, "enabled": True}]
        bar = {"hwnd": 41, "enabled": True, "visible": True}
        with patch("local.app._running_process_snapshots_for_names", return_value=[("dock.exe", r"F:\MyDockFinder\dock.exe")]), \
                patch("local.app.read_live_finder_bars", return_value=[bar]) as probe:
            diagnostics = _mydockfinder_process_diagnostics(r"F:\MyDockFinder", previous_bars=prior)
        probe.assert_called_once_with(matched_name="dock.exe", matched_path=r"F:\MyDockFinder\dock.exe", previous_bars=prior)
        self.assertTrue(diagnostics["running"])
        self.assertTrue(diagnostics["finder_enabled"])
        self.assertEqual(diagnostics["finder_bars"], [bar])

    def test_retired_configured_path_cannot_restrict_live_dock_detection(self):
        with patch("local.app._running_process_snapshots_for_names", return_value=[("dock.exe", r"F:\Other\dock.exe")]), \
                patch("local.app.read_live_finder_bars", return_value=[]) as probe:
            diagnostics = _mydockfinder_process_diagnostics(r"F:\MyDockFinder")
        probe.assert_called_once_with(matched_name="dock.exe", matched_path=r"F:\Other\dock.exe", previous_bars=())
        self.assertTrue(diagnostics["running"])
        self.assertFalse(diagnostics["finder_enabled"])
        self.assertEqual(diagnostics["finder_bars"], [])

    def test_denied_process_path_does_not_depend_on_retired_folder_name(self):
        for configured in ("", r"F:\old-location", r"F:\old-location\Other.exe"):
            with self.subTest(configured=configured), \
                    patch("local.app._running_process_snapshots_for_names", return_value=[("DOCK.EXE", "")]), \
                    patch("local.app.read_live_finder_bars", return_value=[]) as probe:
                diagnostics = _mydockfinder_process_diagnostics(configured)
            self.assertTrue(diagnostics["running"])
            self.assertFalse(diagnostics["finder_enabled"])
            self.assertIn("without path access", diagnostics["reason"])
            probe.assert_called_once_with(matched_name="DOCK.EXE", matched_path="", previous_bars=())

    def test_running_dock_without_finder_does_not_report_finder_enabled(self):
        with patch("local.app._running_process_snapshots_for_names", return_value=[("dock.exe", r"F:\MyDockFinder\dock.exe")]), \
                patch("local.app.read_live_finder_bars", return_value=[]):
            diagnostics = _mydockfinder_process_diagnostics("")
        self.assertTrue(diagnostics["running"])
        self.assertFalse(diagnostics["finder_enabled"])

    def test_distinct_live_dock_runtimes_keep_their_exact_finder_identity(self):
        bar = {"hwnd": 41, "enabled": True, "visible": True}
        def probe(*, matched_path, **_kwargs):
            return [bar] if matched_path == r"G:\MyDockFinder\dock.exe" else []
        with patch("local.app._running_process_snapshots_for_names", return_value=[
            ("dock.exe", r"F:\MyDockFinder\dock.exe"), ("dock.exe", r"G:\MyDockFinder\dock.exe")]), \
                patch("local.app.read_live_finder_bars", side_effect=probe) as bars:
            diagnostics = _mydockfinder_process_diagnostics("")
        self.assertEqual(diagnostics["matched_path"], r"F:\MyDockFinder\dock.exe")
        self.assertEqual(bars.call_count, 2)
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
            _mydockfinder_listener_process_names_setting=lambda: DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES,
            _sync_tray_status=Mock())
        with patch("local.app._mydockfinder_process_diagnostics", return_value={"running": True, "finder_bars": updated}) as diagnostics:
            LocalIslandApp._poll_mydockfinder_presence(app)
        diagnostics.assert_called_once_with("", listener_process_names=DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES, previous_bars=previous)
        window.set_mydockfinder_running.assert_not_called()
        window.set_finder_bar_presence.assert_called_once_with(updated, notify=False)
        app.settings_window.sync_from_widget.assert_called_once_with()

    def test_disabled_monitor_stops_timer_and_never_probes_processes_or_bars(self):
        app = SimpleNamespace(settings={"v2_mydockfinder_monitor_enabled": False},
            window=Mock(), mydockfinder_listener_timer=Mock(),
            settings_window=SimpleNamespace(sync_from_widget=Mock()), _sync_tray_status=Mock())
        app._poll_mydockfinder_presence = lambda: LocalIslandApp._poll_mydockfinder_presence(app)
        with patch("local.app._mydockfinder_process_diagnostics") as probe:
            LocalIslandApp._configure_mydockfinder_listener(app)
        probe.assert_not_called()
        app.mydockfinder_listener_timer.stop.assert_called_once()
        app.mydockfinder_listener_timer.start.assert_not_called()
        app.window.set_mydockfinder_running.assert_called_once_with(False, notify=False)
        app.window.set_finder_bar_presence.assert_called_once_with([], notify=False)
        self.assertFalse(app.window.mydockfinder_monitor_enabled)

    def test_enabled_poll_discovers_actual_runtime_without_legacy_path_or_selection_restrictions(self):
        old_settings = {"v2_mydockfinder_monitor_enabled": True,
                        "v2_mydockfinder_path": "C:/Old/MyDockFinder.exe",
                        "v2_mydockfinder_listener_process_names": []}
        window = SimpleNamespace(mydockfinder_path="C:/Old/MyDockFinder.exe",
            mydockfinder_listener_process_names=(), mydockfinder_running=False, mydockfinder_diagnostics={},
            set_mydockfinder_path=Mock(), set_mydockfinder_listener_process_names=Mock(),
            set_mydockfinder_diagnostics=Mock(), set_mydockfinder_running=Mock(), set_finder_bar_presence=Mock())
        app = SimpleNamespace(settings=dict(old_settings), window=window, _sync_tray_status=Mock())
        app._mydockfinder_listener_process_names_setting = lambda: LocalIslandApp._mydockfinder_listener_process_names_setting(app)
        actual_path = r"F:\SteamLibrary\steamapps\common\MyDockFinder\dock.exe"
        with patch("local.app._running_process_snapshots_for_names", return_value=[("dock.exe", actual_path)]) as scan, \
                patch("local.app.read_live_finder_bars", return_value=[]) as bars:
            LocalIslandApp._poll_mydockfinder_presence(app)
        scan.assert_called_once_with(set(DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES))
        bars.assert_called_once_with(matched_name="dock.exe", matched_path=actual_path, previous_bars=())
        window.set_mydockfinder_path.assert_called_once_with(actual_path, notify=False)
        window.set_mydockfinder_listener_process_names.assert_called_once_with(DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES, notify=False)
        window.set_mydockfinder_running.assert_called_once_with(True, notify=True)
        diagnostics = window.set_mydockfinder_diagnostics.call_args.args[0]
        self.assertEqual(diagnostics["configured_path"], "")
        self.assertTrue(window.mydockfinder_monitor_enabled)
        self.assertEqual(app.settings, old_settings)

    def test_startup_attaches_finder_only_after_saved_style_ratio_and_position_preset(self):
        bar = {"hwnd": 41, "enabled": True, "visible": True}
        window = Mock(plugin_focus_target=0)
        app = SimpleNamespace(settings={"v2_finder_bar_style": "feathered", "v2_finder_bar_center_ratio": 0.3,
            "v2_bar_hide_follow_enabled": False,
            "v2_mydockfinder_path": "C:/Old/Dock.exe", "v2_mydockfinder_listener_process_names": []}, window=window,
            _location_mode_setting=lambda: "top",
            _music_ambience_values_for_style=lambda style: dict(DEFAULT_MUSIC_AMBIENCE_VALUES[style]))
        app._mydockfinder_listener_process_names_setting = lambda: LocalIslandApp._mydockfinder_listener_process_names_setting(app)
        actual_path = "F:/MyDockFinder/dock.exe"
        with patch("local.app._mydockfinder_process_diagnostics", return_value={"running": True,
                   "matched_path": actual_path, "finder_bars": [bar]}) as scan:
            LocalIslandApp._apply_saved_settings(app)
        scan.assert_called_once_with("", listener_process_names=DEFAULT_MYDOCKFINDER_LISTEN_PROCESS_NAMES)
        window.set_mydockfinder_path.assert_called_once_with(actual_path, notify=False)
        calls = [item[0] for item in window.method_calls]
        self.assertLess(calls.index("set_finder_bar_style"), calls.index("set_mydockfinder_running"))
        self.assertLess(calls.index("set_finder_bar_center_ratio"), calls.index("set_mydockfinder_running"))
        window.set_bar_hide_follow_enabled.assert_called_once_with(False, notify=False)
        self.assertLess(calls.index("set_bar_hide_follow_enabled"), calls.index("set_mydockfinder_running"))
        self.assertLess(calls.index("apply_position_preset"), calls.index("set_finder_bar_presence"))
        self.assertEqual(calls[-1], "set_finder_bar_presence")
        window.set_finder_bar_presence.assert_called_once_with([bar], notify=False)

    def test_saved_follow_switch_changes_runtime_without_restarting_monitor(self):
        window = SimpleNamespace(set_bar_hide_follow_enabled=Mock())
        app = SimpleNamespace(window=window, _configure_mydockfinder_listener=Mock())
        for enabled in (False, True):
            LocalIslandApp._playback_mode_changed(app, "v2_bar_hide_follow_enabled", enabled)
            window.set_bar_hide_follow_enabled.assert_called_with(enabled, notify=False)
        app._configure_mydockfinder_listener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
