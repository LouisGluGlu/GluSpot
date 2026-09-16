import unittest

from local.runtime_verification import build_runtime_verification_report


class RuntimeVerificationTests(unittest.TestCase):
    def test_report_marks_complete_runtime_evidence_ready(self):
        report = build_runtime_verification_report(
            {
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
                    "sampled_bands": (0.9, 0.4, 0.2),
                },
            },
            min_trace_samples=3,
            min_trace_window_seconds=2.0,
        ).as_dict()

        self.assertEqual(report["status"], "ready")
        self.assertTrue(report["ready"])
        self.assertEqual(report["health_status"], "warning")
        self.assertEqual(report["dominant_area"], "reactive_effects")
        self.assertEqual(report["bottleneck_group"], "reactive_effects")
        self.assertEqual(report["ambience_style"], "sea_level")
        self.assertEqual(report["lyric_flow_style"], "ember_fade")
        self.assertEqual(report["remaining_trace_samples"], 0)
        self.assertEqual(report["remaining_trace_window_seconds"], 0.0)
        self.assertEqual(report["suggested_wait_seconds"], 0)
        self.assertIn("运行证据已足够", report["next_action"])
        self.assertEqual(report["issues"], [])
        self.assertTrue(all(item["status"] == "ok" for item in report["requirements"]))

    def test_report_rejects_single_export_sample_as_not_enough_runtime_evidence(self):
        report = build_runtime_verification_report(
            {
                "runtime_diagnostics_snapshot": {"status": "ok"},
                "runtime_diagnostics_trace": {
                    "sample_count": 1,
                    "window_seconds": 0.0,
                    "latest": {"status": "ok"},
                },
                "visual_runtime_context": {
                    "ambience_style": "duotone_full",
                    "lyric_flow_style": "wave_stream",
                    "reactive_background_enabled": True,
                },
                "visual_frame_diagnostics": {"frame_count": 1, "sample_count": 1},
                "visual_paint_section_diagnostics": {"groups": {"content": {"last_ms": 2.0}}},
                "lyric_timeline_diagnostics": {"visible_lyric_index": 1},
                "music_reactive_input_diagnostics": {
                    "reactive_enabled": True,
                    "band_source": "cache",
                    "sampled_bands": (0.3, 0.2, 0.1),
                },
            },
            min_trace_samples=3,
            min_trace_window_seconds=2.0,
        ).as_dict()

        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["ready"])
        issue_keys = {item["key"] for item in report["issues"]}
        self.assertIn("runtime_trace_samples", issue_keys)
        self.assertIn("runtime_trace_window", issue_keys)
        self.assertIn("运行证据不完整", report["summary"])
        self.assertEqual(report["remaining_trace_samples"], 2)
        self.assertEqual(report["remaining_trace_window_seconds"], 2.0)
        self.assertEqual(report["suggested_wait_seconds"], 2)
        self.assertIn("约 2 秒", report["next_action"])

    def test_report_requires_reactive_input_only_when_reactive_background_is_enabled(self):
        disabled = build_runtime_verification_report(
            {
                "runtime_diagnostics_trace": {"sample_count": 3, "window_seconds": 3.0},
                "visual_runtime_context": {
                    "ambience_style": "pure_black",
                    "lyric_flow_style": "wave_stream",
                    "reactive_background_enabled": False,
                },
                "visual_frame_diagnostics": {"frame_count": 20, "sample_count": 19},
                "visual_paint_section_diagnostics": {"groups": {"content": {"last_ms": 2.0}}},
                "lyric_timeline_diagnostics": {"visible_lyric_index": 0},
            }
        ).as_dict()
        enabled = build_runtime_verification_report(
            {
                "runtime_diagnostics_trace": {"sample_count": 3, "window_seconds": 3.0},
                "visual_runtime_context": {
                    "ambience_style": "pure_black",
                    "lyric_flow_style": "wave_stream",
                    "reactive_background_enabled": True,
                },
                "visual_frame_diagnostics": {"frame_count": 20, "sample_count": 19},
                "visual_paint_section_diagnostics": {"groups": {"content": {"last_ms": 2.0}}},
                "lyric_timeline_diagnostics": {"visible_lyric_index": 0},
            }
        ).as_dict()

        self.assertEqual(disabled["status"], "ready")
        self.assertEqual(enabled["status"], "partial")
        self.assertIn("reactive_input", {item["key"] for item in enabled["issues"]})


if __name__ == "__main__":
    unittest.main()
