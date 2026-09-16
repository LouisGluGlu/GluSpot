import json
import tempfile
import unittest
from pathlib import Path

from local.diagnostics_evidence import (
    build_diagnostics_evidence_report,
    load_diagnostics_evidence_file,
)


def complete_diagnostics(**overrides):
    data = {
        "runtime_diagnostics_snapshot": {
            "status": "warning",
            "dominant_area": "reactive_effects",
            "bottleneck_group": "reactive_effects",
        },
        "runtime_diagnostics_trace": {
            "sample_count": 5,
            "window_seconds": 4.0,
            "dominant_area": "reactive_effects",
            "bottleneck_group": "reactive_effects",
            "latest": {"status": "warning"},
        },
        "visual_runtime_context": {
            "ambience_style": "sea_level",
            "effective_ambience_style": "sea_level",
            "lyric_flow_style": "ember_fade",
            "reactive_background_enabled": True,
        },
        "visual_frame_diagnostics": {
            "frame_count": 120,
            "sample_count": 119,
            "average_interval_ms": 18.0,
            "target_interval_ms": 16.0,
        },
        "visual_paint_section_diagnostics": {
            "groups": {
                "background_surface": {"average_ms": 3.0, "last_ms": 4.0},
                "reactive_effects": {"average_ms": 8.0, "last_ms": 14.0},
            }
        },
        "lyric_timeline_diagnostics": {
            "visible_lyric_index": 5,
            "position_active_index": 7,
            "sync_lag_lines": 2,
        },
        "music_reactive_input_diagnostics": {
            "reactive_enabled": True,
            "live_audio_available": True,
            "band_source": "cache",
            "sampled_bands": [0.9, 0.4, 0.2],
        },
    }
    data.update(overrides)
    return data


class DiagnosticsEvidenceTests(unittest.TestCase):
    def test_export_payload_with_ready_report_is_ready_evidence(self):
        diagnostics = complete_diagnostics(
            runtime_verification_report={
                "status": "ready",
                "ready": True,
                "summary": "运行证据可用，健康状态 warning / 主因 reactive_effects",
                "health_status": "warning",
                "dominant_area": "reactive_effects",
                "bottleneck_group": "reactive_effects",
                "ambience_style": "sea_level",
                "lyric_flow_style": "ember_fade",
                "reactive_background_enabled": True,
                "trace_sample_count": 5,
                "trace_window_seconds": 4.0,
                "next_action": "运行证据已足够，可以导出并用于判断卡顿、歌词跟进或绘制瓶颈",
                "issues": [],
            }
        )
        report = build_diagnostics_evidence_report(
            {
                "schema_version": 1,
                "generated_at": "2026-06-28T12:34:56+08:00",
                "source": "settings",
                "app": {"name": "GluSpot", "version": "6.402"},
                "diagnostics": diagnostics,
            }
        ).as_dict()

        self.assertEqual(report["source_kind"], "export")
        self.assertEqual(report["source_schema_version"], 1)
        self.assertEqual(report["generated_at"], "2026-06-28T12:34:56+08:00")
        self.assertEqual(report["app_name"], "GluSpot")
        self.assertEqual(report["app_version"], "6.402")
        self.assertEqual(report["status"], "ready")
        self.assertTrue(report["ready"])
        self.assertEqual(report["dominant_area"], "reactive_effects")
        self.assertEqual(report["bottleneck_group"], "reactive_effects")
        self.assertEqual(report["ambience_style"], "sea_level")
        self.assertEqual(report["lyric_flow_style"], "ember_fade")
        self.assertTrue(report["reactive_background_enabled"])
        self.assertEqual(report["trace_sample_count"], 5)
        self.assertEqual(report["trace_window_seconds"], 4.0)
        self.assertIn("律动/过渡", report["next_action"])
        self.assertEqual(report["issues"], [])

    def test_raw_diagnostics_without_existing_verification_report_is_built(self):
        report = build_diagnostics_evidence_report(complete_diagnostics()).as_dict()

        self.assertEqual(report["source_kind"], "raw")
        self.assertEqual(report["status"], "ready")
        self.assertTrue(report["ready"])
        self.assertEqual(report["health_status"], "warning")
        self.assertEqual(report["dominant_area"], "reactive_effects")
        self.assertEqual(report["trace_sample_count"], 5)

    def test_music_wave_cadence_ready_report_uses_specific_next_action(self):
        report = build_diagnostics_evidence_report(
            {
                "diagnostics": {
                    "music_wave_cadence_diagnostics": {
                        "status": "ready",
                        "estimated_update_fps": 58.0,
                        "render_average_fps": 60.0,
                    },
                    "runtime_verification_report": {
                        "status": "ready",
                        "ready": True,
                        "summary": "runtime evidence ready",
                        "health_status": "attention",
                        "dominant_area": "music_wave_cadence",
                        "bottleneck_group": "",
                        "trace_sample_count": 5,
                        "trace_window_seconds": 4.0,
                        "issues": [],
                    },
                }
            }
        ).as_dict()

        self.assertEqual(report["source_kind"], "export")
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["dominant_area"], "music_wave_cadence")
        self.assertIn("音乐波形 cadence", report["next_action"])
        self.assertIn("歌词 FPS", report["next_action"])

    def test_partial_existing_report_is_not_promoted_to_ready(self):
        report = build_diagnostics_evidence_report(
            {
                "diagnostics": complete_diagnostics(
                    runtime_verification_report={
                        "status": "partial",
                        "ready": False,
                        "summary": "运行证据不完整：最近运行诊断样本数不足",
                        "health_status": "ok",
                        "dominant_area": "frame_cadence",
                        "bottleneck_group": "content",
                        "trace_sample_count": 1,
                        "trace_window_seconds": 0.0,
                        "suggested_wait_seconds": 2,
                        "next_action": "保持音乐岛可见并运行约 2 秒后刷新或导出诊断",
                        "issues": [
                            {
                                "key": "runtime_trace_samples",
                                "severity": "warning",
                                "summary": "最近运行诊断样本数不足",
                            }
                        ],
                    }
                )
            }
        ).as_dict()

        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["ready"])
        self.assertEqual(report["dominant_area"], "frame_cadence")
        self.assertEqual(report["bottleneck_group"], "content")
        self.assertIn("运行约 2 秒", report["next_action"])
        self.assertEqual(report["issues"][0]["key"], "runtime_trace_samples")

    def test_payload_with_missing_diagnostics_mapping_is_invalid(self):
        report = build_diagnostics_evidence_report({"schema_version": 1, "diagnostics": []}).as_dict()

        self.assertEqual(report["source_kind"], "invalid")
        self.assertEqual(report["status"], "invalid")
        self.assertFalse(report["ready"])
        self.assertEqual(report["issues"][0]["key"], "missing_diagnostics")
        self.assertIn("diagnostics", report["next_action"])

    def test_missing_raw_diagnostics_is_missing_not_invalid(self):
        report = build_diagnostics_evidence_report({}).as_dict()

        self.assertEqual(report["source_kind"], "raw")
        self.assertEqual(report["status"], "missing")
        self.assertFalse(report["ready"])
        self.assertIn("没有足够运行诊断样本", report["summary"])

    def test_load_file_reports_invalid_json(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "broken.json"
            path.write_text("{broken", encoding="utf-8")

            report = load_diagnostics_evidence_file(path).as_dict()

        self.assertEqual(report["source_kind"], "invalid")
        self.assertEqual(report["status"], "invalid")
        self.assertFalse(report["ready"])
        self.assertEqual(report["issues"][0]["key"], "invalid_json")

    def test_load_file_accepts_exported_json(self):
        payload = {
            "schema_version": 1,
            "generated_at": "2026-06-28T12:34:56+08:00",
            "app": {"name": "GluSpot", "version": "6.402"},
            "diagnostics": complete_diagnostics(),
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "gluspot-runtime-diagnostics.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            report = load_diagnostics_evidence_file(path).as_dict()

        self.assertEqual(report["source_kind"], "export")
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["app_name"], "GluSpot")

    def test_load_file_accepts_utf8_sig_json_from_windows_tools(self):
        payload = {
            "schema_version": 1,
            "generated_at": "2026-06-28T12:34:56+08:00",
            "app": {"name": "GluSpot", "version": "6.402"},
            "diagnostics": complete_diagnostics(),
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "gluspot-runtime-diagnostics.json"
            path.write_text(json.dumps(payload), encoding="utf-8-sig")

            report = load_diagnostics_evidence_file(path).as_dict()

        self.assertEqual(report["source_kind"], "export")
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["app_name"], "GluSpot")


if __name__ == "__main__":
    unittest.main()
