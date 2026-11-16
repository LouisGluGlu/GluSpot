"""Capability dispatch and isolated profile migration need no native runtime."""

from __future__ import annotations

import copy
import subprocess
import sys
import unittest
from dataclasses import FrozenInstanceError

from api.capture.capabilities import (
    CAPABILITIES,
    LEGACY_PROFILE_SETTING,
    PROFILE_SETTING,
    capability_for,
    capture_profile,
    normalize_capture_profiles,
)


class CaptureCapabilitiesTests(unittest.TestCase):
    def test_known_session_and_process_identities_select_default_routes(self):
        cases = (
            ("cloudmusic.exe", "", "netease", "hybrid"),
            ("NetEase.CloudMusic_desktop!App", "", "netease", "hybrid"),
            ("QQMusic", "", "qq", "hybrid"),
            ("Tencent.QQMusic_desktop!App", "", "qq", "hybrid"),
            ("SpotifyAB.SpotifyMusic_desktop!Spotify", "", "spotify", "smtc"),
            ("Chrome", "", "browser", "smtc"),
            ("Microsoft.MicrosoftEdge_desktop!MicrosoftEdge", "", "browser", "smtc"),
            ("unfamiliar-aumid", "CLOUDMUSIC.EXE", "netease", "hybrid"),
            ("unfamiliar-aumid", "qqmusic.exe", "qq", "hybrid"),
            ("unfamiliar-aumid", "firefox.exe", "browser", "smtc"),
        )
        for session, process, service, mode in cases:
            with self.subTest(session=session, process=process):
                capability = capability_for(session, process)
                self.assertEqual((capability.service, capability.mode), (service, mode))

    def test_executable_substrings_and_paths_do_not_inherit_hybrid_capability(self):
        for session, process in (
            ("fake-cloudmusic.exe", ""),
            ("myqqmusic.exe", ""),
            (r"C:\\Music\\cloudmusic.exe", ""),
            ("unknown", r"C:\\Music\\qqmusic.exe"),
            ("unknown", "/opt/music/cloudmusic.exe"),
            ("NetEase.UnrelatedGame_desktop!App", ""),
        ):
            with self.subTest(session=session, process=process):
                capability = capability_for(session, process)
                self.assertEqual(capability.service, "unknown")
                self.assertEqual(capability.mode, "smtc")

    def test_recognized_aumid_takes_precedence_over_process_hint(self):
        self.assertEqual(capability_for("Spotify", "qqmusic.exe").service, "spotify")

    def test_unknown_uses_smtc_and_passive_probe(self):
        capability = capability_for("new-player")
        self.assertEqual(capability.mode, "smtc")
        self.assertTrue(capability.probe)
        self.assertFalse(capability_for("cloudmusic.exe").probe)

    def test_seek_policy_retains_per_player_schedule_and_tolerance(self):
        hybrid = capability_for("qqmusic.exe").policy
        generic = capability_for("spotify.exe").policy
        self.assertEqual(hybrid.tolerance_seconds, 1.5)
        self.assertAlmostEqual(sum(hybrid.read_delays), 0.8)
        self.assertAlmostEqual(sum(generic.read_delays), 0.6)

    def test_capability_table_and_nested_records_are_immutable(self):
        with self.assertRaises(TypeError):
            CAPABILITIES["qq"] = CAPABILITIES["unknown"]
        with self.assertRaises(FrozenInstanceError):
            CAPABILITIES["qq"].mode = "off"
        with self.assertRaises(FrozenInstanceError):
            CAPABILITIES["qq"].policy.tolerance_seconds = 100.0

    def test_empty_legacy_defaults_migrate_to_auto_without_mutating_inputs(self):
        legacy = {
            "netease": {"path": "", "sync_enabled": False},
            "qq": {"path": "  ", "sync_enabled": False},
        }
        original = copy.deepcopy(legacy)
        result = normalize_capture_profiles(legacy, {})
        self.assertEqual(result["netease"], {"mode": "auto"})
        self.assertEqual(result["qq"], {"mode": "auto"})
        result["netease"]["mode"] = "off"
        self.assertEqual(legacy, original)

    def test_configured_legacy_manual_disable_is_preserved(self):
        legacy = {"netease": {"path": r"C:\\Music\\cloudmusic.exe", "sync_enabled": False}}
        self.assertEqual(normalize_capture_profiles(legacy, {})["netease"], {"mode": "off"})

    def test_new_explicit_modes_override_legacy_including_auto(self):
        legacy = {"netease": {"path": "configured.exe", "sync_enabled": False}}
        for mode in ("off", "smtc", "hybrid", "auto"):
            with self.subTest(mode=mode):
                current = {"netease": {"mode": mode}}
                self.assertEqual(normalize_capture_profiles(legacy, current)["netease"]["mode"], mode)
                self.assertEqual(current, {"netease": {"mode": mode}})

    def test_invalid_new_mode_cannot_erase_legacy_manual_disable(self):
        legacy = {"qq": {"path": "configured.exe", "sync_enabled": False}}
        for profile in (None, False, "auto", {"mode": "invalid"}, {"mode": True}):
            with self.subTest(profile=profile):
                self.assertEqual(normalize_capture_profiles(legacy, {"qq": profile})["qq"]["mode"], "off")

    def test_malformed_settings_and_legacy_nonboolean_values_use_auto(self):
        for value in (None, False, "bad", [], 100):
            with self.subTest(value=value):
                self.assertTrue(all(profile == {"mode": "auto"} for profile in normalize_capture_profiles(value, value).values()))
        legacy = {"qq": {"path": "configured.exe", "sync_enabled": "false"}}
        self.assertEqual(normalize_capture_profiles(legacy, None)["qq"]["mode"], "auto")

    def test_service_keys_and_modes_are_canonicalized(self):
        self.assertEqual(normalize_capture_profiles(None, {" QQ ": {"mode": " SMTC "}})["qq"], {"mode": "smtc"})

    def test_capture_profile_resolves_auto_without_any_path(self):
        values = {LEGACY_PROFILE_SETTING: {"netease": {"path": "", "sync_enabled": False}}}
        getter = lambda key, default=None: values.get(key, default)
        self.assertEqual(capture_profile(getter, capability_for("cloudmusic.exe")), "hybrid")
        values[PROFILE_SETTING] = {"netease": {"mode": "off"}}
        self.assertEqual(capture_profile(getter, capability_for("cloudmusic.exe")), "off")
        values[PROFILE_SETTING]["netease"]["mode"] = "smtc"
        self.assertEqual(capture_profile(getter, capability_for("cloudmusic.exe")), "smtc")

    def test_no_settings_getter_uses_capability_default(self):
        self.assertEqual(capture_profile(None, capability_for("spotify.exe")), "smtc")

    def test_module_import_has_no_native_dependencies_or_worker(self):
        result = subprocess.run([
            sys.executable, "-c",
            "import sys, threading; import api.capture.capabilities; "
            "assert not any(n.startswith(('winsdk', 'winrt', 'pywinauto', 'comtypes', 'PySide6')) for n in sys.modules); "
            "assert len(threading.enumerate()) == 1",
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
