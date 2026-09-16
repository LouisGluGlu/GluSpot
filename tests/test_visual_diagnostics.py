import unittest

from local.visual_diagnostics import (
    VisualFrameDiagnosticsRecorder,
    VisualFrameDiagnosticsSummary,
    VisualPaintSectionDiagnosticsSummary,
    visual_paint_section_group,
)


class VisualDiagnosticsTests(unittest.TestCase):
    def test_summary_starts_empty_and_serializes_stable_shape(self):
        summary = VisualFrameDiagnosticsSummary()
        section_summary = VisualPaintSectionDiagnosticsSummary()

        self.assertEqual(
            summary.as_dict(),
            {
                "frame_count": 0,
                "sample_count": 0,
                "average_interval_ms": 0.0,
                "average_fps": 0.0,
                "max_interval_ms": 0.0,
                "jitter_ms": 0.0,
                "long_frame_count": 0,
                "target_interval_ms": 0.0,
                "last_interval_ms": 0.0,
                "last_reason": "",
            },
        )
        self.assertEqual(
            section_summary.as_dict(),
            {
                "section_count": 0,
                "total_recorded_count": 0,
                "total_sample_count": 0,
                "last_section": "",
                "sections": {},
                "groups": {},
            },
        )

    def test_recorder_reports_fps_jitter_and_long_frames(self):
        recorder = VisualFrameDiagnosticsRecorder(capacity=8, long_frame_factor=1.5)

        recorder.record_frame(10.000, target_interval_seconds=1 / 60, reason="media")
        recorder.record_frame(10.016, target_interval_seconds=1 / 60, reason="media")
        summary = recorder.record_frame(10.066, target_interval_seconds=1 / 60, reason="lyric")

        self.assertEqual(summary.frame_count, 3)
        self.assertEqual(summary.sample_count, 2)
        self.assertAlmostEqual(summary.average_interval_ms, 33.0, delta=0.001)
        self.assertAlmostEqual(summary.last_interval_ms, 50.0, delta=0.001)
        self.assertGreater(summary.jitter_ms, 0.0)
        self.assertEqual(summary.long_frame_count, 1)
        self.assertAlmostEqual(summary.target_interval_ms, 1000 / 60, delta=0.001)
        self.assertEqual(summary.last_reason, "lyric")
        self.assertAlmostEqual(summary.as_dict()["average_fps"], 30.3, delta=0.01)

    def test_capacity_limits_recent_interval_window_without_resetting_counters(self):
        recorder = VisualFrameDiagnosticsRecorder(capacity=4, long_frame_factor=2.0)

        for index in range(7):
            recorder.record_frame(20.0 + index * 0.02, target_interval_seconds=0.02)

        summary = recorder.summary()

        self.assertEqual(summary.frame_count, 7)
        self.assertEqual(summary.sample_count, 4)
        self.assertAlmostEqual(summary.average_interval_ms, 20.0, delta=0.001)

    def test_recorder_reports_paint_section_costs_without_resetting_counts(self):
        recorder = VisualFrameDiagnosticsRecorder(capacity=4)

        recorder.record_section("music.background", 0.003)
        recorder.record_section("music.background", 0.005)
        recorder.record_section("music.background", 0.007)
        recorder.record_section("music.background", 0.009)
        recorder.record_section("music.background", 0.011)
        summary = recorder.record_section("music.lyrics", 0.004)
        data = summary.as_dict()

        self.assertEqual(data["section_count"], 2)
        self.assertEqual(data["total_recorded_count"], 6)
        self.assertEqual(data["total_sample_count"], 5)
        self.assertEqual(data["last_section"], "music.lyrics")
        background = data["sections"]["music.background"]
        self.assertEqual(background["recorded_count"], 5)
        self.assertEqual(background["sample_count"], 4)
        self.assertAlmostEqual(background["average_ms"], 8.0, delta=0.001)
        self.assertAlmostEqual(background["max_ms"], 11.0, delta=0.001)
        self.assertAlmostEqual(background["last_ms"], 11.0, delta=0.001)
        lyrics = data["sections"]["music.lyrics"]
        self.assertEqual(lyrics["recorded_count"], 1)
        self.assertAlmostEqual(lyrics["last_ms"], 4.0, delta=0.001)
        groups = data["groups"]
        self.assertIn("background_surface", groups)
        self.assertIn("content", groups)
        self.assertEqual(groups["background_surface"]["section_count"], 1)
        self.assertEqual(groups["background_surface"]["recorded_count"], 5)
        self.assertAlmostEqual(groups["background_surface"]["average_ms"], 8.0, delta=0.001)
        self.assertAlmostEqual(groups["content"]["last_ms"], 4.0, delta=0.001)

    def test_paint_section_groups_identify_music_surface_domains(self):
        self.assertEqual(visual_paint_section_group("music.dual_panel_background"), "background_surface")
        self.assertEqual(visual_paint_section_group("music.reactive_overlay"), "reactive_effects")
        self.assertEqual(visual_paint_section_group("music.surface_transition"), "reactive_effects")
        self.assertEqual(visual_paint_section_group("music.compact_content"), "content")
        self.assertEqual(visual_paint_section_group("music.border"), "shell")
        self.assertEqual(visual_paint_section_group("music.total"), "total")


if __name__ == "__main__":
    unittest.main()
