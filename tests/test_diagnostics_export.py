import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from local.diagnostics_export import (
    build_diagnostics_export_payload,
    build_diagnostics_export_support_metadata,
    default_diagnostics_export_path,
    diagnostics_export_json,
    write_diagnostics_export_file,
)
from local.diagnostics_evidence import build_diagnostics_evidence_report


@dataclass(frozen=True)
class DemoSignal:
    value: int


class DiagnosticsExportTests(unittest.TestCase):
    def test_payload_keeps_runtime_evidence_json_safe(self):
        payload = build_diagnostics_export_payload(
            {
                "runtime_diagnostics_snapshot": {"status": "warning"},
                "runtime_diagnostics_trace": {
                    "sample_count": 3,
                    "samples": (
                        {"sampled_at": 10.0, "status": "warning", "dominant_area": "reactive_effects"},
                        {"sampled_at": 11.0, "status": "attention", "dominant_area": "lyric_timeline"},
                    ),
                },
                "runtime_verification_report": {
                    "status": "ready",
                    "ready": True,
                    "dominant_area": "reactive_effects",
                    "issues": (),
                },
                "visual_frame_diagnostics": {"average_fps": 48.5},
                "visual_paint_section_diagnostics": {"groups": {"content": {"last_ms": 7.0}}},
                "visual_runtime_context": {
                    "ambience_style": "sea_level",
                    "lyric_flow_style": "ember_fade",
                    "reactive_background_enabled": True,
                    "reactive_background_intensity": 70,
                },
                "music_reactive_input_diagnostics": {"sampled_bands": (0.9, 0.4, 0.2)},
                "music_wave_cadence_diagnostics": {"estimated_update_fps": 30.0, "cadence_limited": True},
                "lyric_timeline_diagnostics": {"sync_lag_lines": 2},
                "album_art": b"binary-image",
                "dataclass_value": DemoSignal(7),
                "set_value": {"b", "a"},
            },
            generated_at="2026-06-28T12:34:56+08:00",
            app_name="GluSpot",
            app_version="6.402",
            source="settings",
        )

        text = diagnostics_export_json(payload)
        data = json.loads(text)
        diagnostics = data["diagnostics"]

        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["app"]["name"], "GluSpot")
        self.assertEqual(data["app"]["version"], "6.402")
        self.assertEqual(diagnostics["runtime_diagnostics_snapshot"]["status"], "warning")
        self.assertEqual(diagnostics["runtime_diagnostics_trace"]["sample_count"], 3)
        self.assertEqual(
            [sample["dominant_area"] for sample in diagnostics["runtime_diagnostics_trace"]["samples"]],
            ["reactive_effects", "lyric_timeline"],
        )
        self.assertEqual(diagnostics["runtime_verification_report"]["status"], "ready")
        self.assertTrue(diagnostics["runtime_verification_report"]["ready"])
        self.assertEqual(diagnostics["runtime_verification_report"]["issues"], [])
        self.assertEqual(diagnostics["visual_frame_diagnostics"]["average_fps"], 48.5)
        self.assertEqual(diagnostics["visual_paint_section_diagnostics"]["groups"]["content"]["last_ms"], 7.0)
        self.assertEqual(diagnostics["visual_runtime_context"]["ambience_style"], "sea_level")
        self.assertEqual(diagnostics["visual_runtime_context"]["lyric_flow_style"], "ember_fade")
        self.assertTrue(diagnostics["visual_runtime_context"]["reactive_background_enabled"])
        self.assertEqual(diagnostics["music_reactive_input_diagnostics"]["sampled_bands"], [0.9, 0.4, 0.2])
        self.assertEqual(diagnostics["music_wave_cadence_diagnostics"]["estimated_update_fps"], 30.0)
        self.assertTrue(diagnostics["music_wave_cadence_diagnostics"]["cadence_limited"])
        self.assertEqual(diagnostics["lyric_timeline_diagnostics"]["sync_lag_lines"], 2)
        self.assertTrue(diagnostics["album_art"]["redacted"])
        self.assertEqual(diagnostics["dataclass_value"]["value"], 7)
        self.assertEqual(diagnostics["set_value"], ["a", "b"])

    def test_payload_includes_runtime_experiment_support_metadata(self):
        payload = build_diagnostics_export_payload(
            {
                "runtime_diagnostics_snapshot": {"dominant_area": "runtime_contention"},
                "runtime_diagnostics_trace": {
                    "sample_count": 5,
                    "window_seconds": 5.0,
                    "dominant_area": "runtime_contention",
                    "bottleneck_group": "reactive_effects",
                },
                "runtime_verification_report": {
                    "status": "ready",
                    "ready": True,
                    "dominant_area": "runtime_contention",
                    "bottleneck_group": "reactive_effects",
                    "trace_sample_count": 5,
                    "trace_window_seconds": 5.0,
                },
                "visual_runtime_context": {
                    "reactive_background_enabled": True,
                    "reactive_background_intensity": 88,
                },
                "visual_frame_diagnostics": {
                    "target_interval_ms": 16.0,
                    "average_interval_ms": 18.0,
                    "max_interval_ms": 26.0,
                    "frame_count": 80,
                    "long_frame_count": 1,
                },
                "visual_paint_section_diagnostics": {
                    "groups": {"reactive_effects": {"last_ms": 9.5, "average_ms": 7.0}},
                },
                "lyric_timeline_diagnostics": {"sync_lag_lines": 2},
                "music_reactive_input_diagnostics": {"band_source": "cache"},
            },
            generated_at="2026-06-28T12:34:56+08:00",
            app_name="GluSpot",
            app_version="6.402",
            source="settings",
        )

        data = json.loads(diagnostics_export_json(payload))
        support = data["support"]

        self.assertEqual(support["schema_version"], 1)
        self.assertEqual(support["runtime_experiment_plan"]["key"], "reactive_contention_ablation")
        self.assertEqual(
            support["runtime_experiment_plan"]["setting_changes"]["v2_music_reactive_background_intensity"],
            35,
        )
        self.assertEqual(support["runtime_experiment_compare"]["capture_seconds"], 5)
        self.assertIn("--compare-after", support["runtime_experiment_compare"]["command"])

        report = build_diagnostics_evidence_report(data).as_dict()
        self.assertEqual(report["source_kind"], "export")
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["dominant_area"], "runtime_contention")

    def test_support_metadata_handles_missing_diagnostics(self):
        support = build_diagnostics_export_support_metadata(None)

        self.assertEqual(support["schema_version"], 1)
        self.assertEqual(support["runtime_experiment_plan"]["key"], "capture_ready_baseline")
        self.assertEqual(support["runtime_experiment_compare"]["capture_seconds"], 5)

    def test_default_path_and_write_export_file(self):
        with tempfile.TemporaryDirectory() as temp:
            target = default_diagnostics_export_path(
                Path(temp),
                generated_at="2026-06-28T12:34:56+08:00",
                app_name="GluSpot",
            )
            written = write_diagnostics_export_file(
                {
                    "schema_version": 1,
                    "generated_at": "2026-06-28T12:34:56+08:00",
                    "diagnostics": {"runtime_diagnostics_trace": {"sample_count": 1}},
                },
                target,
            )

            self.assertEqual(written, target)
            self.assertEqual(target.name, "gluspot-runtime-diagnostics-20260628-123456.json")
            data = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(data["diagnostics"]["runtime_diagnostics_trace"]["sample_count"], 1)


if __name__ == "__main__":
    unittest.main()
