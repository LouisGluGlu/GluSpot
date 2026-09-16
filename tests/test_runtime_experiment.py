import unittest

from local.runtime_experiment import (
    build_runtime_experiment_metrics,
    build_runtime_experiment_plan,
    compare_runtime_experiment,
)


def export_payload(
    *,
    dominant_area="runtime_contention",
    bottleneck_group="reactive_effects",
    intensity=70,
    reactive_last_ms=13.0,
    reactive_average_ms=8.0,
    sync_lag_lines=2,
    average_interval_ms=18.0,
    max_interval_ms=24.0,
    long_frame_count=0,
    band_source="cache",
    wave_status="ready",
    wave_update_count=118,
    wave_estimated_update_fps=58.0,
    wave_render_average_fps=60.0,
    wave_to_render_fps_ratio=0.967,
    wave_cadence_limited=False,
):
    return {
        "schema_version": 1,
        "app": {"name": "GluSpot", "version": "6.402"},
        "diagnostics": {
            "runtime_diagnostics_snapshot": {
                "status": "warning" if dominant_area else "ok",
                "dominant_area": dominant_area,
                "bottleneck_group": bottleneck_group,
            },
            "runtime_diagnostics_trace": {
                "sample_count": 5,
                "window_seconds": 4.0,
                "dominant_area": dominant_area,
                "bottleneck_group": bottleneck_group,
                "latest": {"status": "warning" if dominant_area else "ok"},
            },
            "visual_runtime_context": {
                "ambience_style": "sea_level",
                "effective_ambience_style": "sea_level",
                "lyric_flow_style": "ember_fade",
                "reactive_background_enabled": True,
                "reactive_background_intensity": intensity,
            },
            "visual_frame_diagnostics": {
                "frame_count": 120,
                "sample_count": 119,
                "target_interval_ms": 16.0,
                "average_interval_ms": average_interval_ms,
                "jitter_ms": 2.0,
                "max_interval_ms": max_interval_ms,
                "long_frame_count": long_frame_count,
            },
            "visual_paint_section_diagnostics": {
                "groups": {
                    "reactive_effects": {
                        "average_ms": reactive_average_ms,
                        "last_ms": reactive_last_ms,
                    },
                    "content": {"average_ms": 2.0, "last_ms": 2.4},
                }
            },
            "lyric_timeline_diagnostics": {
                "sync_lag_lines": sync_lag_lines,
                "visible_lyric_index": 3,
                "position_active_index": 3 + sync_lag_lines,
                "phase_source": "timed",
            },
            "music_reactive_input_diagnostics": {
                "reactive_enabled": True,
                "live_audio_available": True,
                "band_source": band_source,
                "sampled_bands": [0.8, 0.4, 0.2],
            },
            "music_wave_cadence_diagnostics": {
                "status": wave_status,
                "update_count": wave_update_count,
                "estimated_update_fps": wave_estimated_update_fps,
                "render_average_fps": wave_render_average_fps,
                "wave_to_render_fps_ratio": wave_to_render_fps_ratio,
                "cadence_limited": wave_cadence_limited,
            },
        },
    }


class RuntimeExperimentTests(unittest.TestCase):
    def test_metrics_extract_frame_lyric_and_reactive_pressure_from_export(self):
        metrics = build_runtime_experiment_metrics(export_payload())

        self.assertTrue(metrics.ready)
        self.assertEqual(metrics.dominant_area, "runtime_contention")
        self.assertEqual(metrics.bottleneck_group, "reactive_effects")
        self.assertEqual(metrics.reactive_background_intensity, 70)
        self.assertEqual(metrics.sync_lag_lines, 2)
        self.assertAlmostEqual(metrics.reactive_budget_ratio, 13.0 / 16.0, delta=0.001)
        self.assertEqual(metrics.as_dict()["reactive_band_source"], "cache")
        self.assertEqual(metrics.music_wave_status, "ready")
        self.assertEqual(metrics.music_wave_update_count, 118)
        self.assertAlmostEqual(metrics.music_wave_estimated_update_fps, 58.0, delta=0.001)
        self.assertAlmostEqual(metrics.music_wave_to_render_fps_ratio, 0.967, delta=0.001)
        self.assertFalse(metrics.music_wave_cadence_limited)
        self.assertTrue(metrics.music_wave_cadence_problem)

    def test_plan_for_runtime_contention_prescribes_reactive_intensity_ablation(self):
        plan = build_runtime_experiment_plan(
            {
                "status": "ready",
                "ready": True,
                "dominant_area": "runtime_contention",
                "bottleneck_group": "reactive_effects",
                "reactive_background_enabled": True,
                "trace_sample_count": 5,
                "trace_window_seconds": 4.0,
            }
        )

        self.assertEqual(plan.key, "reactive_contention_ablation")
        self.assertEqual(plan.setting_changes["v2_music_reactive_background_intensity"], 35)
        self.assertIn("reactive_budget_confirmed", plan.success_criteria[0])
        self.assertIn("--compare-after", plan.compare_command)

    def test_plan_for_partial_report_requires_ready_baseline_first(self):
        plan = build_runtime_experiment_plan({"status": "partial", "ready": False})

        self.assertEqual(plan.key, "capture_ready_baseline")
        self.assertEqual(plan.setting_changes, {})
        self.assertIn("trace_sample_count >= 3", plan.success_criteria)

    def test_plan_for_music_wave_cadence_keeps_settings_stable(self):
        plan = build_runtime_experiment_plan(
            {
                "status": "ready",
                "ready": True,
                "dominant_area": "music_wave_cadence",
                "trace_sample_count": 5,
                "trace_window_seconds": 4.0,
            }
        )

        self.assertEqual(plan.key, "music_wave_cadence_control")
        self.assertEqual(plan.setting_changes, {})
        self.assertIn("music_wave_cadence_confirmed", plan.success_criteria[0])
        self.assertIn("cadence_limited", " ".join(plan.success_criteria))
        self.assertIn("music_wave_cadence_remaining", plan.failure_criteria[0])

    def test_comparison_confirms_reactive_budget_when_lag_and_contention_clear(self):
        before = export_payload()
        after = export_payload(
            dominant_area="",
            bottleneck_group="content",
            intensity=35,
            reactive_last_ms=3.0,
            reactive_average_ms=2.0,
            sync_lag_lines=0,
            average_interval_ms=16.2,
            max_interval_ms=20.0,
            wave_estimated_update_fps=30.0,
            wave_to_render_fps_ratio=0.5,
            wave_cadence_limited=True,
        )

        comparison = compare_runtime_experiment(before, after)

        self.assertEqual(comparison.classification, "reactive_budget_confirmed")
        self.assertTrue(comparison.evidence["reactive_pressure_dropped"])
        self.assertEqual(comparison.evidence["reactive_intensity_delta"], 35)
        self.assertEqual(comparison.evidence["lyric_lag_delta"], 2)

    def test_comparison_keeps_lyric_timeline_separate_after_reactive_pressure_drops(self):
        before = export_payload()
        after = export_payload(
            dominant_area="lyric_timeline",
            bottleneck_group="content",
            intensity=35,
            reactive_last_ms=3.0,
            reactive_average_ms=2.0,
            sync_lag_lines=3,
            average_interval_ms=16.1,
            max_interval_ms=20.0,
            wave_estimated_update_fps=30.0,
            wave_to_render_fps_ratio=0.5,
            wave_cadence_limited=True,
        )

        comparison = compare_runtime_experiment(before, after)

        self.assertEqual(comparison.classification, "lyric_timeline_remaining")
        self.assertTrue(comparison.evidence["after_lyric_lag_problem"])
        self.assertIn("media position", comparison.next_action)

    def test_comparison_reports_frame_scheduler_when_after_cadence_is_still_poor(self):
        before = export_payload()
        after = export_payload(
            dominant_area="frame_cadence",
            bottleneck_group="content",
            intensity=35,
            reactive_last_ms=3.0,
            sync_lag_lines=0,
            average_interval_ms=28.0,
            max_interval_ms=48.0,
            long_frame_count=12,
        )

        comparison = compare_runtime_experiment(before, after)

        self.assertEqual(comparison.classification, "frame_scheduler_remaining")
        self.assertTrue(comparison.evidence["after_frame_cadence_problem"])

    def test_comparison_confirms_music_wave_cadence_when_sampling_is_limited(self):
        before = export_payload(
            dominant_area="music_wave_cadence",
            bottleneck_group="content",
            reactive_last_ms=2.0,
            sync_lag_lines=0,
            average_interval_ms=16.2,
            max_interval_ms=20.0,
            wave_update_count=118,
            wave_estimated_update_fps=58.0,
            wave_render_average_fps=60.0,
            wave_to_render_fps_ratio=0.967,
            wave_cadence_limited=False,
        )
        after = export_payload(
            dominant_area="",
            bottleneck_group="content",
            reactive_last_ms=2.0,
            sync_lag_lines=0,
            average_interval_ms=16.2,
            max_interval_ms=20.0,
            wave_update_count=60,
            wave_estimated_update_fps=30.0,
            wave_render_average_fps=60.0,
            wave_to_render_fps_ratio=0.5,
            wave_cadence_limited=True,
        )

        comparison = compare_runtime_experiment(before, after)

        self.assertEqual(comparison.classification, "music_wave_cadence_confirmed")
        self.assertAlmostEqual(comparison.evidence["music_wave_ratio_delta"], 0.467, delta=0.001)
        self.assertTrue(comparison.evidence["after_music_wave_cadence_limited"])
        self.assertFalse(comparison.evidence["after_music_wave_cadence_problem"])
        self.assertIn("cadence seam", comparison.next_action)

    def test_comparison_reports_music_wave_cadence_when_sampling_stays_coupled(self):
        before = export_payload(
            dominant_area="music_wave_cadence",
            bottleneck_group="content",
            reactive_last_ms=2.0,
            sync_lag_lines=0,
            average_interval_ms=16.2,
            max_interval_ms=20.0,
        )
        after = export_payload(
            dominant_area="music_wave_cadence",
            bottleneck_group="content",
            reactive_last_ms=2.0,
            sync_lag_lines=0,
            average_interval_ms=16.2,
            max_interval_ms=20.0,
            wave_estimated_update_fps=56.0,
            wave_render_average_fps=60.0,
            wave_to_render_fps_ratio=0.933,
            wave_cadence_limited=False,
        )

        comparison = compare_runtime_experiment(before, after)

        self.assertEqual(comparison.classification, "music_wave_cadence_remaining")
        self.assertTrue(comparison.evidence["after_music_wave_cadence_problem"])
        self.assertIn("wave update gate", comparison.next_action)

    def test_comparison_requires_ready_exports_before_attribution(self):
        before = {"diagnostics": {"runtime_diagnostics_trace": {"sample_count": 1, "window_seconds": 0.0}}}
        after = export_payload(dominant_area="", sync_lag_lines=0)

        comparison = compare_runtime_experiment(before, after)

        self.assertEqual(comparison.classification, "insufficient_evidence")
        self.assertFalse(comparison.before.ready)


if __name__ == "__main__":
    unittest.main()
