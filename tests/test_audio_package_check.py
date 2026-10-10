from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from api.windows_audio_devices import AudioOutputDevice
from local import audio_package_check as check


class AudioPackageCheckTests(unittest.TestCase):
    def test_diagnostic_dispatch_precedes_app_import_with_no_stdout(self):
        # A missing app module makes an accidental GUI/provider import fail.
        with patch.dict(sys.modules, {"local.app": None}), patch.object(sys, "stdout", None), \
                patch.object(check, "run_audio_package_check", return_value=0) as run:
            sys.modules.pop("local.main", None)
            entry = importlib.import_module("local.main")
            self.assertEqual(entry.main(["--audio-package-check", "验收.json"]), 0)
            run.assert_called_once_with("验收.json")

    def test_bad_diagnostic_arguments_never_start_gui(self):
        entry = importlib.import_module("local.main")
        with patch.dict(sys.modules, {"local.app": None}), patch.object(sys, "stderr", None), \
                patch.object(check, "run_audio_package_check") as run:
            self.assertEqual(entry.main(["--audio-package-check"]), 2)
            self.assertEqual(entry.main(["--audio-package-check", "file", "extra"]), 2)
            run.assert_not_called()

    def test_normal_dispatch_preserves_app_entry(self):
        entry = importlib.import_module("local.main")
        app_main = Mock(return_value=17)
        with patch.dict(sys.modules, {"local.app": SimpleNamespace(main=app_main)}):
            self.assertEqual(entry.main([]), 17)
        app_main.assert_called_once_with()

    def test_report_is_written_with_windowed_streams_and_keeps_failed_sections(self):
        report = {"ok": False, "native_capture": {"returncode": 1, "error": "采集失败"}}
        with tempfile.TemporaryDirectory() as temp, patch.object(sys, "stdout", None), \
                patch.object(sys, "stderr", None), patch.object(check, "build_audio_package_report", return_value=report):
            target = Path(temp) / "reports" / "验收.json"
            self.assertEqual(check.run_audio_package_check(target), 1)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), report)

    def test_report_write_failure_is_distinct_from_check_failure(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(check, "build_audio_package_report", return_value={"ok": True}):
            self.assertEqual(check.run_audio_package_check(temp), 2)

    def test_endpoints_are_enumerated_and_closed_without_selection(self):
        backend = Mock()
        backend.list_outputs.return_value = (
            AudioOutputDevice("local", "本地扬声器"),
            AudioOutputDevice("bt", "耳机", is_bluetooth=True, is_default=True),
        )
        with patch.object(check, "WindowsAudioDevices", return_value=backend):
            result = check._check_endpoints()
        self.assertTrue(result["ok"])
        self.assertEqual(result["default_endpoint_id"], "bt")
        self.assertEqual([call[0] for call in backend.method_calls], ["list_outputs", "close"])

    def test_endpoint_failure_retains_error_and_closes(self):
        backend = Mock()
        backend.list_outputs.side_effect = OSError("driver failure")
        with patch.object(check, "WindowsAudioDevices", return_value=backend):
            result = check._check_endpoints()
        self.assertFalse(result["ok"])
        self.assertIn("driver failure", result["error"])
        backend.close.assert_called_once_with()

    def test_empty_successful_discovery_is_valid_and_closes(self):
        discovery = Mock(state="browsing", error="")
        discovery.devices.return_value = ()
        with patch.object(check, "AirPlayDiscovery", return_value=discovery), patch.object(check.time, "sleep") as sleep:
            result = check._check_discovery()
        self.assertTrue(result["ok"])
        self.assertEqual(result["devices"], [])
        sleep.assert_called_once_with(check.DISCOVERY_SECONDS)
        self.assertEqual([call[0] for call in discovery.method_calls], ["start", "devices", "close"])

    def test_discovery_nonthrowing_failure_and_cleanup_failure_are_reported(self):
        discovery = Mock(state="unavailable", error="dependency missing")
        discovery.devices.return_value = ()
        discovery.close.side_effect = OSError("close failure")
        with patch.object(check, "AirPlayDiscovery", return_value=discovery), patch.object(check.time, "sleep") as sleep:
            result = check._check_discovery()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "dependency missing")
        self.assertIn("close failure", result["cleanup_error"])
        sleep.assert_not_called()

    def test_capture_uses_only_readonly_probe_and_silent_source_is_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            helper = Path(temp) / "GluSpotAirPlay.exe"
            helper.touch()
            output = '\n'.join(json.dumps(event) for event in (
                {"event": "capture_ready", "sample_rate": 48000},
                {"event": "stats", "captured_frames": 0}, {"event": "stopped"},
            ))
            with patch.object(check, "engine_path", return_value=helper), \
                    patch.object(check.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=output, stderr="")) as run:
                result = check._check_capture('endpoint $() " 空格')
        self.assertTrue(result["ok"])
        self.assertEqual(run.call_args.args[0], [str(helper), "--probe-capture", "--endpoint", 'endpoint $() " 空格'])
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["timeout"], check.CAPTURE_TIMEOUT_SECONDS)

    def test_missing_default_never_opens_capture_or_guesses_an_endpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            helper = Path(temp) / "helper.exe"
            helper.touch()
            with patch.object(check, "engine_path", return_value=helper), patch.object(check.subprocess, "run") as run:
                result = check._check_capture("")
        self.assertFalse(result["ok"])
        run.assert_not_called()

    def test_capture_failure_or_malformed_events_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            helper = Path(temp) / "helper.exe"
            helper.touch()
            for output in ('[]\n', '{"event":"capture_ready"}\n', 'not-json\n'):
                with self.subTest(output=output), patch.object(check, "engine_path", return_value=helper), \
                        patch.object(check.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=output, stderr="detail")):
                    result = check._check_capture("endpoint")
                self.assertFalse(result["ok"])
                self.assertEqual(result["returncode"], 0)
                self.assertEqual(result["stderr"], "detail")

    def test_archive_origin_does_not_claim_a_replaceable_loose_module(self):
        spec = SimpleNamespace(origin="/missing/archive/api/providers.py", loader=object())
        with patch.object(check.importlib.util, "find_spec", return_value=spec):
            result = check._module_info("api.providers")
        self.assertTrue(result["ok"])
        self.assertFalse(result["execution_checked"])
        self.assertIsNone(result["replacement_path"])
        self.assertEqual(result["storage"], "archive_or_virtual")

    def test_group_dispatch_uses_invalid_args_before_any_device_state_work(self):
        with tempfile.TemporaryDirectory() as temp:
            helper = Path(temp) / "GluSpotAirPlay.exe"
            helper.touch()
            event = {"event": "error", "message":
                     "Group requires a verified music process, source endpoint and two outputs or AirPlay"}
            reply = SimpleNamespace(returncode=2, stdout=json.dumps(event) + "\n", stderr="")
            with patch.object(check, "engine_path", return_value=helper), \
                    patch.object(check.subprocess, "run", return_value=reply) as run:
                result = check._check_group_dispatch()
            self.assertTrue(result["ok"])
            self.assertFalse(result["receiver_contacted"])
            self.assertEqual(run.call_args.args[0], [str(helper), "--group"])
            self.assertNotIn("shell", run.call_args.kwargs)

    def test_legacy_or_broken_group_helper_cannot_pass_new_package_acceptance(self):
        with tempfile.TemporaryDirectory() as temp:
            helper = Path(temp) / "GluSpotAirPlay.exe"
            helper.touch()
            for output, code in ((json.dumps({"event": "error", "message": "Unknown argument --group"}), 1),
                                 (json.dumps({"event": "error", "message":
                                              "Group requires a verified music process, source endpoint and HomePod"}), 2),
                                 ('[]\n', 2), ('invalid\n', 2),
                                 (json.dumps({"event": "connecting"}), 0)):
                with self.subTest(output=output), patch.object(check, "engine_path", return_value=helper), \
                        patch.object(check.subprocess, "run", return_value=SimpleNamespace(
                            returncode=code, stdout=output, stderr="")):
                    self.assertFalse(check._check_group_dispatch()["ok"])

    def test_new_group_modules_are_imported_without_constructing_audio_resources(self):
        spec = SimpleNamespace(origin="/missing/archive/module.py", loader=object())
        for name in ("api.windows_app_audio_routes", "local.music_output_clock"):
            with self.subTest(name=name), patch.object(check.importlib.util, "find_spec", return_value=spec), \
                    patch.object(check.importlib, "import_module", return_value=SimpleNamespace(__spec__=spec)) as imported:
                result = check._module_info(name)
            self.assertTrue(result["ok"])
            self.assertTrue(result["execution_checked"])
            imported.assert_called_once_with(name)

    def test_multi_output_parser_check_rejects_source_collision_before_windows_work(self):
        with tempfile.TemporaryDirectory() as temp:
            helper = Path(temp) / "helper.exe"
            helper.touch()
            event = {"event": "error", "message":
                     "Local group outputs must be unique and differ from muted source"}
            reply = SimpleNamespace(returncode=2, stdout=json.dumps(event) + "\n", stderr="")
            with patch.object(check, "engine_path", return_value=helper), \
                    patch.object(check.subprocess, "run", return_value=reply) as run:
                result = check._check_multi_output_dispatch()
        self.assertTrue(result["ok"])
        self.assertFalse(result["receiver_contacted"])
        self.assertFalse(result["windows_state_changed"])
        args = run.call_args.args[0]
        self.assertEqual(args.count("--local-endpoint"), 2)
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_old_single_renderer_parser_cannot_certify_multiple_output_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            helper = Path(temp) / "helper.exe"
            helper.touch()
            for output, code in ((json.dumps({"event": "error", "message": "Unknown or duplicate group argument"}), 2),
                                 (json.dumps({"event": "stopped"}), 0), ('[]\n', 2)):
                with self.subTest(output=output), patch.object(check, "engine_path", return_value=helper), \
                        patch.object(check.subprocess, "run", return_value=SimpleNamespace(
                            returncode=code, stdout=output, stderr="")):
                    self.assertFalse(check._check_multi_output_dispatch()["ok"])

    def test_failed_multiple_output_compatibility_fails_the_complete_report(self):
        with patch.object(check, "_dependency_info", return_value={"ok": True}), \
                patch.object(check, "_module_info", return_value={"ok": True}), \
                patch.object(check, "_check_endpoints", return_value={"ok": True, "default_endpoint_id": "bt"}), \
                patch.object(check, "_check_discovery", return_value={"ok": True}), \
                patch.object(check, "_check_capture", return_value={"ok": True}), \
                patch.object(check, "_check_group_dispatch", return_value={"ok": True}), \
                patch.object(check, "_check_multi_output_dispatch", return_value={"ok": False}):
            self.assertFalse(check.build_audio_package_report()["ok"])

    def test_external_helper_cannot_certify_a_frozen_bundle(self):
        with patch.object(check.sys, "frozen", True, create=True), \
                patch.dict(check.os.environ, {"GLUSPOT_AIRPLAY_ENGINE": "external.exe"}), \
                patch.object(check, "_dependency_info", return_value={"ok": True}), \
                patch.object(check, "_module_info", return_value={"ok": True}), \
                patch.object(check, "_check_endpoints", return_value={"ok": True, "default_endpoint_id": "bt"}), \
                patch.object(check, "_check_discovery", return_value={"ok": True}), \
                patch.object(check, "_check_capture", return_value={"ok": True}), \
                patch.object(check, "_check_group_dispatch", return_value={"ok": True}), \
                patch.object(check, "_check_multi_output_dispatch", return_value={"ok": True}):
            report = check.build_audio_package_report()
        self.assertFalse(report["ok"])
        self.assertFalse(report["native_capture"]["ok"])
        self.assertFalse(report["native_group_dispatch"]["ok"])
        self.assertFalse(report["native_multi_output_dispatch"]["ok"])


if __name__ == "__main__":
    unittest.main()
