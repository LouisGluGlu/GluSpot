from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from local.mydockfinder import (
    _read_finder_enabled_for_runtime,
    finder_bar_logical_rect,
    finder_bar_screen_mapping,
    read_live_finder_bars,
    refresh_live_finder_bar,
)


def bar_row(**changes):
    row = {
        "hwnd": 41, "pid": 22224, "class_name": "MyFinderApp",
        "process_name": "Dock_64.exe", "process_path": r"F:\MyDockFinder\dock_64.exe",
        "visible": True, "cloaked": False, "minimized": False,
        "physical_rect": [0, 0, 1920, 24], "monitor_name": r"\\.\DISPLAY1",
        "physical_monitor_rect": [0, 0, 1920, 1080],
    }
    row.update(changes)
    return row


class FinderBarDetectionTests(unittest.TestCase):
    def probe(self, rows, *, enabled=None, previous=(), matched_path=r"F:\MyDockFinder\dock_64.exe"):
        with patch("local.mydockfinder._native_finder_bar_window_snapshots", return_value=rows), \
                patch("local.mydockfinder._read_finder_enabled_for_runtime", return_value=enabled):
            return read_live_finder_bars(matched_name="dock_64.exe", matched_path=matched_path, previous_bars=previous)

    def test_visible_actual_finder_bar_is_enabled_without_config(self):
        result = self.probe([bar_row()])
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["enabled"])
        self.assertTrue(result[0]["visible"])
        self.assertEqual(result[0]["physical_resting_rect"], [0.0, 0.0, 1920.0, 24.0])

    def test_installed_config_without_actual_window_cannot_create_bar(self):
        self.assertEqual(self.probe([], enabled=True), [])

    def test_wrong_class_dock_management_and_capture_windows_are_excluded(self):
        rows = [bar_row(class_name=name) for name in ("MyDockAPP", "MYDOCKFINDER", "MyDockFinderCaptureHwnd", "MyFinderVoltip")]
        self.assertEqual(self.probe(rows, enabled=True), [])

    def test_same_name_from_other_path_cannot_use_a_trusted_runtime(self):
        self.assertEqual(self.probe([bar_row(process_path=r"F:\Other\dock_64.exe")]), [])

    def test_unknown_process_cannot_use_a_trusted_bar_class(self):
        self.assertEqual(self.probe([bar_row(process_name="Other.exe")]), [])

    def test_explicit_process_path_cannot_fall_back_when_query_denied(self):
        self.assertEqual(self.probe([bar_row(process_path="")]), [])

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
                self.assertEqual(result[0]["physical_resting_rect"], [0.0, 0.0, 1920.0, 24.0])

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

    def test_no_trusted_name_never_calls_native_probe(self):
        with patch("local.mydockfinder._native_finder_bar_window_snapshots") as native:
            self.assertEqual(read_live_finder_bars(matched_name=""), [])
        native.assert_not_called()

    def test_cached_refresh_uses_identity_without_reading_config_or_enumerating_processes(self):
        original = self.probe([bar_row()])[-1]
        with patch("local.mydockfinder._native_finder_bar_window_snapshots", return_value=[bar_row(physical_rect=[0, -24, 1920, 0])]) as native, \
                patch("local.mydockfinder._read_finder_enabled_for_runtime", side_effect=AssertionError("no file IO in follow tick")):
            result = refresh_live_finder_bar(original)
        native.assert_called_once_with("dock_64.exe", r"f:\mydockfinder\dock_64.exe", _cached_snapshot=original)
        self.assertTrue(result["enabled"])
        self.assertFalse(result["visible"])
        self.assertEqual(result["physical_resting_rect"], original["physical_resting_rect"])

    def test_missing_cached_hwnd_refresh_is_unavailable(self):
        original = self.probe([bar_row()])[-1]
        with patch("local.mydockfinder._native_finder_bar_window_snapshots", return_value=[]):
            self.assertIsNone(refresh_live_finder_bar(original))

    def test_config_flag_requires_recognized_section_and_zero_or_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "dock_64.exe"
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
