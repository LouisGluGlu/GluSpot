import unittest

from local.runtime_diagnostics import RuntimeDiagnosticsTraceRecorder, build_runtime_diagnostics_snapshot


class RuntimeDiagnosticsTests(unittest.TestCase):
    def test_snapshot_identifies_reactive_paint_pressure(self):
        snapshot = build_runtime_diagnostics_snapshot(
            {
                "visual_frame_diagnostics": {
                    "frame_count": 120,
                    "sample_count": 119,
                    "average_interval_ms": 18.0,
                    "target_interval_ms": 16.0,
                    "jitter_ms": 3.0,
                    "max_interval_ms": 24.0,
                    "long_frame_count": 0,
                },
                "visual_paint_section_diagnostics": {
                    "groups": {
                        "background_surface": {"average_ms": 4.0, "last_ms": 5.0},
                        "reactive_effects": {"average_ms": 8.0, "last_ms": 14.0},
                        "content": {"average_ms": 3.0, "last_ms": 4.0},
                    }
                },
                "lyric_timeline_diagnostics": {
                    "sync_lag_lines": 0,
                    "visible_lyric_index": 4,
                    "position_active_index": 4,
                    "phase_source": "timed",
                },
                "music_reactive_input_diagnostics": {
                    "reactive_enabled": True,
                    "live_audio_available": True,
                    "band_source": "cache",
                    "intensity": 0.7,
                    "sampled_bands": (0.9, 0.4, 0.2),
                },
            }
        )

        data = snapshot.as_dict()

        self.assertEqual(data["status"], "warning")
        self.assertEqual(data["dominant_area"], "reactive_effects")
        self.assertEqual(data["bottleneck_group"], "reactive_effects")
        self.assertIn("paint top reactive_effects", data["headline"])
        paint = next(signal for signal in data["signals"] if signal["key"] == "paint_pressure")
        self.assertEqual(paint["status"], "warning")
        self.assertEqual(paint["evidence"]["top_group"], "reactive_effects")

    def test_snapshot_keeps_lyric_lag_separate_from_paint_cost(self):
        snapshot = build_runtime_diagnostics_snapshot(
            {
                "visual_frame_diagnostics": {
                    "frame_count": 90,
                    "sample_count": 89,
                    "average_interval_ms": 16.2,
                    "target_interval_ms": 16.0,
                    "jitter_ms": 1.5,
                    "max_interval_ms": 20.0,
                    "long_frame_count": 0,
                },
                "visual_paint_section_diagnostics": {
                    "groups": {
                        "reactive_effects": {"average_ms": 1.0, "last_ms": 1.4},
                        "content": {"average_ms": 2.0, "last_ms": 2.2},
                    }
                },
                "lyric_timeline_diagnostics": {
                    "sync_lag_lines": 3,
                    "visible_lyric_index": 2,
                    "position_active_index": 5,
                    "phase_source": "timed",
                },
                "music_reactive_input_diagnostics": {
                    "reactive_enabled": True,
                    "live_audio_available": True,
                    "band_source": "cache",
                },
            }
        ).as_dict()

        self.assertEqual(snapshot["status"], "warning")
        self.assertEqual(snapshot["dominant_area"], "lyric_timeline")
        self.assertEqual(snapshot["bottleneck_group"], "content")
        lyric = next(signal for signal in snapshot["signals"] if signal["key"] == "lyric_timeline")
        self.assertEqual(lyric["evidence"]["sync_lag_lines"], 3)

    def test_snapshot_identifies_reactive_paint_and_lyric_lag_contention(self):
        snapshot = build_runtime_diagnostics_snapshot(
            {
                "visual_frame_diagnostics": {
                    "frame_count": 120,
                    "sample_count": 119,
                    "average_interval_ms": 22.0,
                    "target_interval_ms": 16.0,
                    "jitter_ms": 6.0,
                    "max_interval_ms": 38.0,
                    "long_frame_count": 9,
                },
                "visual_paint_section_diagnostics": {
                    "groups": {
                        "reactive_effects": {"average_ms": 7.0, "last_ms": 13.4},
                        "content": {"average_ms": 2.8, "last_ms": 3.1},
                    }
                },
                "lyric_timeline_diagnostics": {
                    "sync_lag_lines": 2,
                    "visible_lyric_index": 3,
                    "position_active_index": 5,
                    "phase_source": "timed",
                },
                "music_reactive_input_diagnostics": {
                    "reactive_enabled": True,
                    "live_audio_available": True,
                    "band_source": "cache",
                },
            }
        ).as_dict()

        self.assertEqual(snapshot["status"], "warning")
        self.assertEqual(snapshot["dominant_area"], "runtime_contention")
        self.assertEqual(snapshot["bottleneck_group"], "reactive_effects")
        contention = next(signal for signal in snapshot["signals"] if signal["key"] == "runtime_contention")
        self.assertEqual(contention["status"], "warning")
        self.assertEqual(contention["evidence"]["sync_lag_lines"], 2)
        self.assertGreater(contention["evidence"]["reactive_budget_ratio"], 0.75)

    def test_snapshot_reports_missing_evidence_without_guessing(self):
        snapshot = build_runtime_diagnostics_snapshot({}).as_dict()

        self.assertEqual(snapshot["status"], "missing")
        self.assertEqual(snapshot["headline"], "no-runtime-evidence")
        self.assertEqual(snapshot["dominant_area"], "")
        self.assertEqual(snapshot["bottleneck_group"], "")
        self.assertTrue(all(signal["status"] == "missing" for signal in snapshot["signals"]))

    def test_snapshot_reports_music_wave_cadence_coupled_to_render_fps(self):
        snapshot = build_runtime_diagnostics_snapshot(
            {
                "visual_frame_diagnostics": {
                    "frame_count": 120,
                    "sample_count": 119,
                    "average_interval_ms": 16.7,
                    "target_interval_ms": 16.0,
                    "average_fps": 60.0,
                    "jitter_ms": 1.0,
                    "max_interval_ms": 20.0,
                    "long_frame_count": 0,
                },
                "visual_paint_section_diagnostics": {
                    "groups": {"content": {"average_ms": 2.0, "last_ms": 2.2}}
                },
                "lyric_timeline_diagnostics": {
                    "sync_lag_lines": 0,
                    "visible_lyric_index": 2,
                    "position_active_index": 2,
                    "phase_source": "timed",
                },
                "music_reactive_input_diagnostics": {
                    "reactive_enabled": True,
                    "live_audio_available": True,
                    "band_source": "cache",
                },
                "music_wave_cadence_diagnostics": {
                    "status": "ready",
                    "update_count": 118,
                    "estimated_update_fps": 58.0,
                    "render_average_fps": 60.0,
                    "wave_to_render_fps_ratio": 0.967,
                    "cadence_limited": False,
                },
            }
        ).as_dict()

        cadence = next(signal for signal in snapshot["signals"] if signal["key"] == "music_wave_cadence")
        self.assertEqual(cadence["status"], "attention")
        self.assertEqual(cadence["area"], "music_wave_cadence")
        self.assertGreater(cadence["evidence"]["wave_to_render_fps_ratio"], 0.85)

    def test_trace_recorder_summarizes_recent_runtime_snapshots(self):
        recorder = RuntimeDiagnosticsTraceRecorder(capacity=3)

        recorder.record(
            10.0,
            {
                "status": "warning",
                "dominant_area": "reactive_effects",
                "bottleneck_group": "reactive_effects",
                "headline": "reactive paint",
            },
        )
        recorder.record(
            11.0,
            {
                "status": "attention",
                "dominant_area": "lyric_timeline",
                "bottleneck_group": "content",
                "headline": "lyric lag",
            },
        )
        recorder.record(
            12.0,
            {
                "status": "warning",
                "dominant_area": "reactive_effects",
                "bottleneck_group": "reactive_effects",
                "headline": "reactive paint",
            },
        )

        summary = recorder.summary().as_dict()

        self.assertEqual(summary["recorded_count"], 3)
        self.assertEqual(summary["sample_count"], 3)
        self.assertAlmostEqual(summary["window_seconds"], 2.0, delta=0.001)
        self.assertEqual(summary["warning_count"], 2)
        self.assertEqual(summary["attention_count"], 1)
        self.assertEqual(summary["dominant_area"], "reactive_effects")
        self.assertEqual(summary["dominant_area_count"], 2)
        self.assertEqual(summary["bottleneck_group"], "reactive_effects")
        self.assertEqual(summary["bottleneck_group_count"], 2)
        self.assertEqual(summary["latest"]["headline"], "reactive paint")
        self.assertEqual(
            [sample["headline"] for sample in summary["samples"]],
            ["reactive paint", "lyric lag", "reactive paint"],
        )
        self.assertEqual(
            [sample["sampled_at"] for sample in summary["samples"]],
            [10.0, 11.0, 12.0],
        )

    def test_trace_recorder_keeps_bounded_recent_window(self):
        recorder = RuntimeDiagnosticsTraceRecorder(capacity=2)

        recorder.record(1.0, {"status": "ok", "dominant_area": "", "bottleneck_group": ""})
        recorder.record(2.0, {"status": "attention", "dominant_area": "lyric_timeline"})
        recorder.record(3.0, {"status": "warning", "dominant_area": "frame_cadence"})

        summary = recorder.summary().as_dict()

        self.assertEqual(summary["recorded_count"], 3)
        self.assertEqual(summary["sample_count"], 2)
        self.assertAlmostEqual(summary["first_sample_at"], 2.0, delta=0.001)
        self.assertAlmostEqual(summary["last_sample_at"], 3.0, delta=0.001)
        self.assertNotIn("ok", summary["status_counts"])
        self.assertEqual(
            [sample["status"] for sample in summary["samples"]],
            ["attention", "warning"],
        )


if __name__ == "__main__":
    unittest.main()
