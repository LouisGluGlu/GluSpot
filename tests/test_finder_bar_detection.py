from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from local.mydockfinder import (
    _read_finder_enabled_for_runtime,
    _native_finder_bar_window_snapshots,
    detect_mydockfinder,
    finder_bar_logical_rect,
    finder_bar_screen_mapping,
    read_live_finder_bars,
    refresh_live_finder_bar,
)


def bar_row(**changes):
    row = {
        "hwnd": 41, "pid": 22224, "class_name": "MyFinderApp",
        "process_name": "Dock.exe", "process_path": r"F:\MyDockFinder\dock.exe",
        "visible": True, "cloaked": False, "minimized": False,
        "physical_rect": [0, 0, 1920, 24], "monitor_name": r"\\.\DISPLAY1",
        "physical_monitor_rect": [0, 0, 1920, 1080],
    }
    row.update(changes)
    return row


class FinderBarDetectionTests(unittest.TestCase):
    def probe(self, rows, *, enabled=None, previous=(), matched_path=r"F:\MyDockFinder\dock.exe"):
        with patch("local.mydockfinder._native_finder_bar_window_snapshots", return_value=rows), \
                patch("local.mydockfinder._read_finder_enabled_for_runtime", return_value=enabled):
            return read_live_finder_bars(matched_name="dock.exe", matched_path=matched_path, previous_bars=previous)

    def test_visible_actual_finder_bar_is_enabled_without_config(self):
        result = self.probe([bar_row()])
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["enabled"])
        self.assertTrue(result[0]["visible"])
        self.assertTrue(result[0]["window_visible"])
        self.assertEqual(result[0]["physical_resting_rect"], [0.0, 0.0, 1920.0, 24.0])

    def test_installed_config_without_actual_window_cannot_create_bar(self):
        self.assertEqual(self.probe([], enabled=True), [])

    def test_wrong_class_dock_management_and_capture_windows_are_excluded(self):
        rows = [bar_row(class_name=name) for name in ("MyDockAPP", "MYDOCKFINDER", "MyDockFinderCaptureHwnd", "MyFinderVoltip")]
        self.assertEqual(self.probe(rows, enabled=True), [])

    def test_same_name_from_other_path_cannot_use_a_trusted_runtime(self):
        self.assertEqual(self.probe([bar_row(process_path=r"F:\Other\dock.exe")]), [])

    def test_unknown_process_cannot_use_a_trusted_bar_class(self):
        self.assertEqual(self.probe([bar_row(process_name="Other.exe")]), [])

    def test_second_path_query_denial_retains_actual_name_pid_class_window_evidence(self):
        result = self.probe([bar_row(process_path="")])
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["enabled"])
        self.assertEqual(result[0]["process_path"], "")

    def test_name_only_trust_preserves_existing_listener_boundary(self):
        result = self.probe([bar_row(process_path="")], matched_path="")
        self.assertTrue(result[0]["enabled"])

    def test_explicitly_disabled_config_vetoes_stale_visible_hwnd(self):
        result = self.probe([bar_row()], enabled=False)
        self.assertFalse(result[0]["enabled"])
        self.assertFalse(result[0]["visible"])

    def test_hidden_unknown_bar_is_not_assumed_enabled(self):
        result = self.probe([bar_row(visible=False)])
        self.assertFalse(result[0]["enabled"])
        self.assertFalse(result[0]["visible"])

    def test_hidden_real_bar_with_explicit_enabled_config_remains_available(self):
        result = self.probe([bar_row(visible=False)], enabled=True)
        self.assertTrue(result[0]["enabled"])
        self.assertFalse(result[0]["visible"])

    def test_same_previously_visible_hwnd_can_follow_autohide_without_config(self):
        previous = self.probe([bar_row()])
        result = self.probe([bar_row(visible=False)], previous=previous)
        self.assertTrue(result[0]["enabled"])
        self.assertFalse(result[0]["visible"])

    def test_hidden_finder_preserves_same_window_config_when_path_access_is_denied_then_recovers(self):
        known_path = bar_row()["process_path"]
        previous = self.probe([bar_row()], enabled=True)
        hidden = bar_row(visible=False, physical_rect=[0, -24, 1920, 0])
        for missing_path in (True, True, False):
            with self.subTest(missing_path=missing_path):
                row = {**hidden, "process_path": "" if missing_path else known_path}
                previous = self.probe([row], enabled=None if missing_path else True,
                    matched_path="" if missing_path else known_path, previous=previous)
                self.assertTrue(previous[0]["enabled"])
                self.assertFalse(previous[0]["visible"])
                self.assertIs(previous[0]["config_enabled"], True)
                self.assertEqual(previous[0]["process_path"], known_path)
                self.assertEqual(previous[0]["physical_resting_rect"], [0., 0., 1920., 24.])
        disabled = self.probe([hidden], enabled=False, previous=previous)
        self.assertFalse(disabled[0]["enabled"])
        self.assertIs(disabled[0]["config_enabled"], False)

    def test_unknown_path_preserves_explicit_disabled_config_for_the_same_window(self):
        previous = self.probe([bar_row()], enabled=False)
        current = self.probe([bar_row(process_path="")], enabled=None, previous=previous, matched_path="")
        self.assertFalse(current[0]["enabled"])
        self.assertFalse(current[0]["visible"])
        self.assertIs(current[0]["config_enabled"], False)

    def test_path_denial_cannot_transfer_prior_enablement_to_new_window_or_runtime(self):
        previous = self.probe([bar_row()], enabled=True)
        for change in ({"hwnd": 42}, {"pid": 22225}, {"process_name": "Dock_64.exe"}):
            with self.subTest(change=change):
                row = bar_row(visible=False, process_path="", **change)
                with patch("local.mydockfinder._native_finder_bar_window_snapshots", return_value=[row]), \
                        patch("local.mydockfinder._read_finder_enabled_for_runtime", return_value=None):
                    result = read_live_finder_bars(matched_name=row["process_name"], previous_bars=previous)
                self.assertFalse(result[0]["enabled"])
                self.assertIsNone(result[0]["config_enabled"])
                self.assertEqual(result[0]["process_path"], "")

    def test_known_different_runtime_path_cannot_inherit_hidden_config(self):
        previous = self.probe([bar_row()], enabled=True)
        result = self.probe([bar_row(visible=False, process_path=r"F:\Other\dock.exe")],
            enabled=None, previous=previous, matched_path="")
        self.assertFalse(result[0]["enabled"])
        self.assertIsNone(result[0]["config_enabled"])
        self.assertEqual(result[0]["process_path"], r"F:\Other\dock.exe")

    def test_changed_hwnd_pid_or_path_cannot_inherit_hidden_bar_enablement(self):
        previous = self.probe([bar_row()])
        for change in ({"hwnd": 42}, {"pid": 22225}):
            with self.subTest(change=change):
                result = self.probe([bar_row(visible=False, **change)], previous=previous)
                self.assertFalse(result[0]["enabled"])

    def test_cloaked_or_minimized_bar_does_not_enable_finder(self):
        for change in ({"cloaked": True}, {"minimized": True}):
            with self.subTest(change=change):
                result = self.probe([bar_row(**change)], enabled=True)
                self.assertFalse(result[0]["enabled"])
                self.assertFalse(result[0]["visible"])

    def test_capture_position_far_from_monitor_is_excluded_even_with_enable_flag(self):
        self.assertEqual(self.probe([bar_row(physical_rect=[-32000, -32000, -31980, -31980])], enabled=True), [])

    def test_lower_window_or_horizontal_nonintersection_is_excluded(self):
        for bounds in ([0, 400, 1920, 424], [2000, 0, 3920, 24]):
            with self.subTest(bounds=bounds):
                self.assertEqual(self.probe([bar_row(physical_rect=bounds)], enabled=True), [])

    def test_empty_infinite_and_malformed_rects_are_excluded(self):
        for bounds in ([0, 0, 0, 24], [0, 0, float("inf"), 24], [0, 0, 1920], None):
            with self.subTest(bounds=bounds):
                self.assertEqual(self.probe([bar_row(physical_rect=bounds)], enabled=True), [])

    def test_partly_and_fully_slid_top_bar_preserve_resting_height_but_hide(self):
        previous = self.probe([bar_row()])
        for bounds in ([0, -12, 1920, 12], [0, -24, 1920, 0]):
            with self.subTest(bounds=bounds):
                result = self.probe([bar_row(physical_rect=bounds)], previous=previous)
                self.assertTrue(result[0]["enabled"])
                self.assertFalse(result[0]["visible"])
                self.assertTrue(result[0]["window_visible"])
                self.assertEqual(result[0]["physical_resting_rect"], [0.0, 0.0, 1920.0, 24.0])

    def test_native_hide_during_partial_slide_retains_raw_visibility_separately(self):
        previous = self.probe([bar_row()])
        bounds = [0, -12, 1920, 12]
        for native_visible in (True, False):
            with self.subTest(native_visible=native_visible):
                result = self.probe([bar_row(physical_rect=bounds, visible=native_visible)], previous=previous)
                self.assertTrue(result[0]["enabled"])
                self.assertFalse(result[0]["visible"])
                self.assertEqual(result[0]["window_visible"], native_visible)
                self.assertEqual(result[0]["physical_rect"], bounds)
                self.assertEqual(result[0]["physical_resting_rect"], [0.0, 0.0, 1920.0, 24.0])

    def test_cached_refresh_replaces_raw_visibility_after_native_hide_and_reveal(self):
        original = self.probe([bar_row()])[-1]
        for native_visible in (False, True):
            with self.subTest(native_visible=native_visible), \
                    patch("local.mydockfinder._native_finder_bar_window_snapshots", return_value=[bar_row(
                        physical_rect=[0, -12, 1920, 12], visible=native_visible)]):
                fresh = refresh_live_finder_bar(original)
                self.assertEqual(fresh["window_visible"], native_visible)
                self.assertFalse(fresh["visible"])
                original = fresh

    def test_one_pixel_reveal_strip_cannot_shrink_known_full_height(self):
        previous = self.probe([bar_row()])
        result = self.probe([bar_row(physical_rect=[0, 0, 1920, 1])], previous=previous)
        self.assertTrue(result[0]["enabled"])
        self.assertFalse(result[0]["visible"])
        self.assertEqual(result[0]["physical_resting_rect"], [0.0, 0.0, 1920.0, 24.0])

    def test_unknown_reveal_strip_has_no_invented_full_height(self):
        result = self.probe([bar_row(physical_rect=[0, 0, 1920, 1])], enabled=True)
        self.assertTrue(result[0]["enabled"])
        self.assertFalse(result[0]["visible"])
        self.assertNotIn("physical_resting_rect", result[0])

    def test_auto_hidden_lower_monitor_keeps_owner_when_nearest_lookup_finds_upper_monitor(self):
        previous = self.probe([bar_row(
            physical_rect=[0, 1080, 1920, 1104], monitor_name=r"\\.\DISPLAY2",
            physical_monitor_rect=[0, 1080, 1920, 2160])])
        result = self.probe([bar_row(physical_rect=[0, 1056, 1920, 1080])], previous=previous)
        self.assertTrue(result[0]["enabled"])
        self.assertFalse(result[0]["visible"])
        self.assertEqual(result[0]["monitor_name"], r"\\.\DISPLAY2")
        self.assertEqual(result[0]["physical_resting_rect"], [0.0, 1080.0, 1920.0, 1104.0])

    def test_malformed_previous_identity_is_ignored(self):
        result = self.probe([bar_row()], previous=[{"hwnd": "oops", "pid": 2}, None])
        self.assertTrue(result[0]["enabled"])

    def test_non_dock_names_never_call_native_probe(self):
        with patch("local.mydockfinder._native_finder_bar_window_snapshots") as native:
            for name in ("", "myfinder.exe", "mydockfinder.exe", "mydock.exe", "dockmod.exe", "dockmod64.exe", "Other.exe"):
                with self.subTest(name=name):
                    self.assertEqual(read_live_finder_bars(matched_name=name), [])
        native.assert_not_called()

    def test_cached_legacy_process_cannot_restart_finder_monitoring(self):
        with patch("local.mydockfinder._native_finder_bar_window_snapshots") as native:
            for name in ("myfinder.exe", "mydock.exe", "dockmod.exe", "dockmod64.exe", "Other.exe"):
                with self.subTest(name=name):
                    self.assertIsNone(refresh_live_finder_bar({**bar_row(process_name=name), "enabled": True}))
        native.assert_not_called()

    def test_cached_refresh_uses_identity_without_reading_config_or_enumerating_processes(self):
        original = self.probe([bar_row()])[-1]
        with patch("local.mydockfinder._native_finder_bar_window_snapshots", return_value=[bar_row(physical_rect=[0, -24, 1920, 0])]) as native, \
                patch("local.mydockfinder._read_finder_enabled_for_runtime", side_effect=AssertionError("no file IO in follow tick")):
            result = refresh_live_finder_bar(original)
        native.assert_called_once_with("dock.exe", r"f:\mydockfinder\dock.exe", _cached_snapshot=original)
        self.assertTrue(result["enabled"])
        self.assertFalse(result["visible"])
        self.assertEqual(result["physical_resting_rect"], original["physical_resting_rect"])

    def test_missing_cached_hwnd_refresh_is_unavailable(self):
        original = self.probe([bar_row()])[-1]
        with patch("local.mydockfinder._native_finder_bar_window_snapshots", return_value=[]):
            self.assertIsNone(refresh_live_finder_bar(original))

    def test_each_main_runtime_supports_actual_finder_discovery_and_cached_follow(self):
        for name in ("dock.exe", "DOCK_64.EXE"):
            with self.subTest(name=name):
                path = "F:/MyDockFinder/" + name
                row = bar_row(process_name=name, process_path=path)
                with patch("local.mydockfinder._native_finder_bar_window_snapshots", return_value=[row]), \
                        patch("local.mydockfinder._read_finder_enabled_for_runtime", return_value=True):
                    result = read_live_finder_bars(matched_name=name, matched_path=path)
                    self.assertEqual(len(result), 1)
                    self.assertTrue(result[0]["enabled"])
                    refreshed = refresh_live_finder_bar(result[0])
                self.assertEqual(refreshed["pid"], row["pid"])
                self.assertTrue(refreshed["enabled"])

    def test_config_flag_requires_recognized_section_and_zero_or_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "dock.exe"
            config = Path(tmp) / "config.ini"
            self.assertIsNone(_read_finder_enabled_for_runtime(str(runtime)))
            for content, expected in (
                ("[dockfinder]\nmyfinder=1\n", True),
                ("[dockfinder]\nmyfinder=0\n", False),
                ("[dockfinder]\nmyfinder=maybe\n", None),
                ("[normal]\nmyfinder=1\n", None),
                ("[broken", None),
            ):
                with self.subTest(content=content):
                    config.write_text(content, encoding="utf-8")
                    self.assertIs(_read_finder_enabled_for_runtime(str(runtime)), expected)


class DockProcessPresenceTests(unittest.TestCase):
    def test_installed_files_and_config_cannot_establish_a_running_dock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.ini").write_text("[dockfinder]\nmyfinder=1\n", encoding="utf-8")
            (root / "dock.exe").touch()
            with patch("local.mydockfinder._running_mydockfinder_process", return_value=None), \
                    patch("local.mydockfinder._known_roots") as paths:
                presence = detect_mydockfinder(root)
            self.assertFalse(presence.available)
            self.assertIsNone(presence.config_file)
            paths.assert_not_called()

    def test_other_runtime_names_cannot_establish_presence(self):
        for name in ("MyFinder.exe", "Mydock.exe", "MyDockFinder.exe", "Dockmod.exe", "Dockmod64.exe"):
            with self.subTest(name=name), \
                    patch("local.mydockfinder._running_mydockfinder_process", return_value=name), \
                    patch("local.mydockfinder._known_roots") as paths:
                self.assertFalse(detect_mydockfinder().available)
                paths.assert_not_called()

    def test_live_dock_can_read_its_material_config_without_creating_a_finder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.ini"
            config.write_text("[dockfinder]\nmyfinder=0\n", encoding="utf-8")
            with patch("local.mydockfinder._running_mydockfinder_process", return_value="DOCK.EXE"), \
                    patch("local.mydockfinder._known_roots", return_value=[root]):
                presence = detect_mydockfinder(root)
            self.assertTrue(presence.available)
            self.assertEqual(presence.config_file, config)

    def test_live_dock_without_config_is_still_detected(self):
        for name in ("dock.exe", "Dock_64.exe"):
            with self.subTest(name=name), \
                    patch("local.mydockfinder._running_mydockfinder_process", return_value=name), \
                    patch("local.mydockfinder._known_roots", return_value=[]):
                presence = detect_mydockfinder()
            self.assertTrue(presence.available)
            self.assertIsNone(presence.config_file)
            self.assertEqual(presence.source, "running process: " + name)


class NativeFinderPermissionTests(unittest.TestCase):
    """Exercise the native enumeration boundary without a QApplication."""

    def native_api(self, *, process_path="", open_allowed=False):
        def first(_handle, output):
            output._obj.th32ProcessID = 22224
            output._obj.szExeFile = "Dock.exe"
            return True

        def image(_handle, _flags, buffer, _size):
            buffer.value = process_path
            return bool(process_path)

        kernel = SimpleNamespace(
            CreateToolhelp32Snapshot=Mock(return_value=101),
            Process32FirstW=Mock(side_effect=first), Process32NextW=Mock(return_value=False),
            OpenProcess=Mock(return_value=102 if open_allowed else 0),
            QueryFullProcessImageNameW=Mock(side_effect=image), CloseHandle=Mock(),
        )

        def window_pid(_hwnd, output):
            output._obj.value = 22224
            return 1

        def class_name(_hwnd, buffer, _size):
            buffer.value = "MyFinderApp"
            return len(buffer.value)

        def window_rect(_hwnd, output):
            output._obj.left, output._obj.top, output._obj.right, output._obj.bottom = 0, 0, 1920, 24
            return True

        def monitor_info(_monitor, output):
            info = output._obj
            info.rcMonitor.left, info.rcMonitor.top = 0, 0
            info.rcMonitor.right, info.rcMonitor.bottom = 1920, 1080
            info.szDevice = r"\\.\DISPLAY1"
            return True

        user = SimpleNamespace(
            EnumWindows=Mock(side_effect=lambda callback, _data: callback(41, 0)),
            GetWindowThreadProcessId=Mock(side_effect=window_pid),
            GetClassNameW=Mock(side_effect=class_name), GetWindowRect=Mock(side_effect=window_rect),
            IsWindowVisible=Mock(return_value=True), IsIconic=Mock(return_value=False),
            MonitorFromWindow=Mock(return_value=103), GetMonitorInfoW=Mock(side_effect=monitor_info),
            SetThreadDpiAwarenessContext=Mock(return_value=104),
        )

        def library(name, **_kwargs):
            if name == "kernel32":
                return kernel
            if name == "user32":
                return user
            raise OSError(name)

        return kernel, user, library

    def test_native_permission_denial_keeps_actual_finder_but_never_creates_one(self):
        for open_allowed in (False, True):
            with self.subTest(open_allowed=open_allowed):
                kernel, user, library = self.native_api(open_allowed=open_allowed)
                with patch("local.mydockfinder.ctypes.WinDLL", side_effect=library):
                    rows = _native_finder_bar_window_snapshots("dock.exe", r"f:\mydockfinder\dock.exe")
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0]["pid"], rows[0]["hwnd"]), (22224, 41))
                self.assertEqual(rows[0]["process_path"], "")
                self.assertTrue(rows[0]["visible"])
                kernel.CloseHandle.assert_any_call(101)
                if open_allowed:
                    kernel.CloseHandle.assert_any_call(102)
                user.SetThreadDpiAwarenessContext.assert_called_with(104)
                user.EnumWindows.side_effect = lambda _callback, _data: True
                with patch("local.mydockfinder.ctypes.WinDLL", side_effect=library):
                    self.assertEqual(_native_finder_bar_window_snapshots("dock.exe", r"f:\mydockfinder\dock.exe"), [])

    def test_native_known_different_path_still_cannot_supply_trusted_finder(self):
        _kernel, user, library = self.native_api(open_allowed=True, process_path=r"F:\Other\dock.exe")
        with patch("local.mydockfinder.ctypes.WinDLL", side_effect=library):
            self.assertEqual(_native_finder_bar_window_snapshots("dock.exe", r"f:\mydockfinder\dock.exe"), [])
        user.EnumWindows.assert_not_called()

    def test_app_process_snapshot_keeps_exact_executable_name_when_path_access_is_denied(self):
        from local.app import _running_process_snapshots_for_names
        for open_allowed in (False, True):
            with self.subTest(open_allowed=open_allowed):
                kernel, _user, library = self.native_api(open_allowed=open_allowed)
                with patch("local.app.ctypes.WinDLL", side_effect=library):
                    self.assertEqual(_running_process_snapshots_for_names({"dock.exe"}), [("Dock.exe", "")])
                kernel.CloseHandle.assert_any_call(101)
                if open_allowed:
                    kernel.CloseHandle.assert_any_call(102)


class FinderBarCoordinateTests(unittest.TestCase):
    def mapping(self, snapshot=None, **changes):
        kwargs = {"screen_name": r"\\.\DISPLAY1", "screen_rect": [0, 0, 1920, 1080], "device_pixel_ratio": 1.0}
        kwargs.update(changes)
        return finder_bar_logical_rect(snapshot or {**bar_row(), "enabled": True}, **kwargs)

    def test_native_one_to_one_geometry(self):
        self.assertEqual(self.mapping(), (0.0, 0.0, 1920.0, 24.0))

    def test_scaled_screen_preserves_global_origin_and_scales_only_offsets(self):
        snapshot = {**bar_row(physical_rect=[2040, 0, 3840, 48], physical_monitor_rect=[1920, 0, 3840, 2160]), "enabled": True}
        self.assertEqual(self.mapping(snapshot, screen_rect=[1920, 0, 960, 1080], device_pixel_ratio=2.0), (1980.0, 0.0, 900.0, 24.0))

    def test_negative_horizontal_and_vertical_monitor_origin_is_preserved(self):
        snapshot = {**bar_row(physical_rect=[-2880, -1800, 0, -1764], physical_monitor_rect=[-2880, -1800, 0, 0]), "enabled": True}
        self.assertEqual(self.mapping(snapshot, screen_rect=[-2880, -1800, 1920, 1200], device_pixel_ratio=1.5), (-2880.0, -1800.0, 1920.0, 24.0))

    def test_wrong_monitor_does_not_fall_back_to_primary_screen(self):
        self.assertIsNone(self.mapping(screen_name=r"\\.\DISPLAY2"))

    def test_matching_name_without_matching_physical_screen_bounds_is_rejected(self):
        for screen_rect, dpr in (([1920, 0, 1920, 1080], 1.0), ([0, 0, 1280, 720], 1.0), ([0, 0, 1920, 1080], 2.0)):
            with self.subTest(screen_rect=screen_rect, dpr=dpr):
                self.assertIsNone(self.mapping(screen_rect=screen_rect, device_pixel_ratio=dpr))

    def test_logical_size_rounding_within_two_physical_pixels_is_accepted(self):
        self.assertEqual(self.mapping(screen_rect=[0, 0, 1097, 617], device_pixel_ratio=1.75), (0.0, 0.0, 1920 / 1.75, 24 / 1.75))

    def test_friendly_name_needs_explicit_verified_geometry_fallback(self):
        self.assertIsNone(self.mapping(screen_name="DELL U2723QE"))
        self.assertEqual(self.mapping(screen_name="DELL U2723QE", allow_geometry_fallback=True), (0.0, 0.0, 1920.0, 24.0))

    def test_bar_crossing_monitor_bounds_is_clipped_before_mapping(self):
        snapshot = {**bar_row(physical_rect=[-100, 0, 2020, 24]), "enabled": True}
        self.assertEqual(self.mapping(snapshot), (0.0, 0.0, 1920.0, 24.0))

    def test_autohide_uses_last_actual_resting_shape_for_layout(self):
        snapshot = {**bar_row(physical_rect=[0, 0, 1920, 1]), "enabled": True, "visible": False, "physical_resting_rect": [0, 0, 1920, 24]}
        self.assertEqual(self.mapping(snapshot), (0.0, 0.0, 1920.0, 24.0))

    def test_invalid_scale_screen_or_unavailable_bar_is_rejected(self):
        for changes in ({"device_pixel_ratio": 0}, {"device_pixel_ratio": float("nan")}, {"screen_rect": [0, 0, -1, 100]}):
            with self.subTest(changes=changes):
                self.assertIsNone(self.mapping(**changes))
        self.assertIsNone(self.mapping({**bar_row(), "enabled": False}))


class FinderBarScreenSelectionTests(unittest.TestCase):
    def screen(self, name, bounds, dpr=1.0):
        x, y, width, height = bounds
        geometry = SimpleNamespace(x=lambda: x, y=lambda: y, width=lambda: width, height=lambda: height)
        return SimpleNamespace(name=lambda: name, geometry=lambda: geometry, devicePixelRatio=lambda: dpr)

    def test_unique_friendly_name_monitor_is_selected_by_physical_bounds(self):
        actual = self.screen("DELL U2723QE", [1920, 0, 1920, 1080], 2.0)
        primary = self.screen(r"\\.\DISPLAY1", [0, 0, 1920, 1080])
        snapshot = {**bar_row(monitor_name=r"\\.\DISPLAY2", physical_rect=[1920, 0, 5760, 48], physical_monitor_rect=[1920, 0, 5760, 2160]), "enabled": True}
        selected = finder_bar_screen_mapping(snapshot, [primary, actual])
        self.assertIs(selected[0], actual)
        self.assertEqual(selected[1], (1920.0, 0.0, 1920.0, 24.0))

    def test_same_friendly_name_on_two_monitors_does_not_select_by_name(self):
        primary = self.screen("DELL U2723QE", [0, 0, 1920, 1080])
        actual = self.screen("DELL U2723QE", [-2880, -1800, 1920, 1200], 1.5)
        snapshot = {**bar_row(physical_rect=[-2880, -1800, 0, -1764], physical_monitor_rect=[-2880, -1800, 0, 0]), "enabled": True}
        selected = finder_bar_screen_mapping(snapshot, [primary, actual])
        self.assertIs(selected[0], actual)
        self.assertEqual(selected[1], (-2880.0, -1800.0, 1920.0, 24.0))

    def test_exact_name_with_wrong_geometry_cannot_beat_real_geometry(self):
        wrong = self.screen(r"\\.\DISPLAY1", [1920, 0, 1920, 1080])
        actual = self.screen("Generic Monitor", [0, 0, 1920, 1080])
        selected = finder_bar_screen_mapping({**bar_row(), "enabled": True}, [wrong, actual])
        self.assertIs(selected[0], actual)

    def test_overlapping_identical_bounds_are_ambiguous_even_with_one_matching_name(self):
        first = self.screen(r"\\.\DISPLAY1", [0, 0, 1920, 1080])
        second = self.screen("Cloned Monitor", [0, 0, 1920, 1080])
        self.assertIsNone(finder_bar_screen_mapping({**bar_row(), "enabled": True}, [first, second]))

    def test_no_geometry_match_never_falls_back_to_first_or_primary(self):
        primary = self.screen(r"\\.\DISPLAY1", [1920, 0, 1920, 1080])
        self.assertIsNone(finder_bar_screen_mapping({**bar_row(), "enabled": True}, [primary]))

    def test_same_screen_object_repeated_in_inventory_is_not_ambiguous(self):
        screen = self.screen("Generic Monitor", [0, 0, 1920, 1080])
        selected = finder_bar_screen_mapping({**bar_row(), "enabled": True}, [screen, screen])
        self.assertIs(selected[0], screen)

    def test_disconnected_invalid_screen_is_ignored_without_changing_valid_match(self):
        disconnected = SimpleNamespace(geometry=lambda: (_ for _ in ()).throw(RuntimeError("Internal C++ object deleted")))
        actual = self.screen("Generic Monitor", [0, 0, 1920, 1080])
        selected = finder_bar_screen_mapping({**bar_row(), "enabled": True}, [disconnected, actual])
        self.assertIs(selected[0], actual)


if __name__ == "__main__":
    unittest.main()
