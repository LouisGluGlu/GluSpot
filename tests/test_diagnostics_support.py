import io
import json
import tempfile
import unittest
from pathlib import Path

from local.diagnostics_support import (
    build_diagnostics_support_guide,
    diagnostics_evidence_exit_code,
    diagnostics_support_focus,
    diagnostics_support_focus_items,
    format_diagnostics_support_report,
    main,
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
        "visual_frame_diagnostics": {"frame_count": 120, "sample_count": 119},
        "visual_paint_section_diagnostics": {
            "groups": {"reactive_effects": {"average_ms": 8.0, "last_ms": 14.0}}
        },
        "lyric_timeline_diagnostics": {"visible_lyric_index": 5},
        "music_reactive_input_diagnostics": {
            "reactive_enabled": True,
            "band_source": "cache",
            "sampled_bands": [0.9, 0.4, 0.2],
        },
    }
    data.update(overrides)
    return data


class DiagnosticsSupportTests(unittest.TestCase):
    def test_exit_code_distinguishes_ready_partial_and_invalid(self):
        self.assertEqual(diagnostics_evidence_exit_code({"status": "ready", "ready": True}), 0)
        self.assertEqual(diagnostics_evidence_exit_code({"status": "partial", "ready": False}), 1)
        self.assertEqual(diagnostics_evidence_exit_code({"status": "missing", "ready": False}), 1)
        self.assertEqual(diagnostics_evidence_exit_code({"status": "invalid", "ready": False}), 2)

    def test_format_report_includes_support_summary_and_next_action(self):
        text = format_diagnostics_support_report(
            {
                "status": "ready",
                "ready": True,
                "source_kind": "export",
                "source_schema_version": 1,
                "generated_at": "2026-06-28T12:34:56+08:00",
                "app_name": "GluSpot",
                "app_version": "6.402",
                "health_status": "warning",
                "trace_sample_count": 5,
                "trace_window_seconds": 4.0,
                "ambience_style": "sea_level",
                "lyric_flow_style": "ember_fade",
                "reactive_background_enabled": True,
                "dominant_area": "reactive_effects",
                "bottleneck_group": "reactive_effects",
                "summary": "导出证据可用",
                "next_action": "证据指向律动/过渡；优先检查音频平滑、左右波形/海平面渲染和绘制缓存",
                "issues": [],
            }
        )

        self.assertIn("GluSpot runtime diagnostics evidence", text)
        self.assertIn("status: ready", text)
        self.assertIn("source: export / schema 1 / 2026-06-28T12:34:56+08:00", text)
        self.assertIn("app: GluSpot 6.402", text)
        self.assertIn("runtime: 5 samples / 4.0s", text)
        self.assertIn("visual: sea_level / ember_fade / reactive on", text)
        self.assertIn("dominant_area: reactive_effects", text)
        self.assertIn("音频平滑", text)
        self.assertIn("focus: music reactive rendering", text)
        self.assertIn("local/audio_reactive_engine.py", text)
        self.assertIn("tests/test_music_background_renderer.py", text)
        self.assertIn("remediation:", text)
        self.assertIn("v2_music_reactive_background_intensity", text)

    def test_support_focus_combines_dominant_and_bottleneck_without_duplicates(self):
        focus = diagnostics_support_focus(
            {
                "status": "ready",
                "ready": True,
                "dominant_area": "lyric_timeline",
                "bottleneck_group": "content",
            }
        )

        text = "\n".join(focus)
        self.assertIn("focus: media position and lyric timeline", text)
        self.assertIn("local/media_position_pipeline.py", text)
        self.assertIn("focus: lyrics / album art / message overlay content", text)
        self.assertIn("local/album_art_pipeline.py", text)
        self.assertEqual(text.count("focus: media position and lyric timeline"), 1)

    def test_support_focus_items_are_structured_for_automation(self):
        items = diagnostics_support_focus_items(
            {
                "status": "ready",
                "ready": True,
                "dominant_area": "reactive_effects",
                "bottleneck_group": "reactive_effects",
            }
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].area, "reactive_effects")
        self.assertEqual(items[0].focus, "music reactive rendering")
        self.assertIn("local/audio_reactive_engine.py", items[0].files)
        self.assertIn("tests/test_music_background_renderer.py", items[0].tests)
        self.assertEqual(
            items[0].as_dict()["files"],
            [
                "local/audio_reactive_engine.py",
                "local/music_background_renderer.py",
                "local/visual_diagnostics.py",
            ],
        )

    def test_support_focus_items_include_runtime_contention_files(self):
        items = diagnostics_support_focus_items(
            {
                "status": "ready",
                "ready": True,
                "dominant_area": "runtime_contention",
                "bottleneck_group": "reactive_effects",
            }
        )

        self.assertEqual([item.area for item in items], ["runtime_contention", "reactive_effects"])
        self.assertEqual(items[0].focus, "visual frame budget versus lyric timeline contention")
        self.assertIn("local/runtime_diagnostics.py", items[0].files)
        self.assertIn("api/lyric_timeline.py", items[0].files)
        self.assertIn("tests/test_runtime_diagnostics.py", items[0].tests)

    def test_support_focus_items_include_music_wave_cadence_files(self):
        items = diagnostics_support_focus_items(
            {
                "status": "ready",
                "ready": True,
                "dominant_area": "music_wave_cadence",
                "bottleneck_group": "content",
            }
        )

        self.assertEqual([item.area for item in items], ["music_wave_cadence", "content"])
        self.assertEqual(items[0].focus, "music wave cadence versus render/lyric cadence")
        self.assertIn("local/island_presentation.py", items[0].files)
        self.assertIn("local/runtime_diagnostics.py", items[0].files)
        self.assertIn("tests/test_island_presentation.py", items[0].tests)
        self.assertIn("tests/test_runtime_diagnostics.py", items[0].tests)

    def test_support_guide_carries_report_focus_and_exit_code(self):
        guide = build_diagnostics_support_guide(
            {
                "status": "ready",
                "ready": True,
                "dominant_area": "lyric_timeline",
                "bottleneck_group": "content",
            }
        )

        data = guide.as_dict()
        self.assertEqual(guide.exit_code, 0)
        self.assertEqual(data["exit_code"], 0)
        self.assertEqual(data["report"]["dominant_area"], "lyric_timeline")
        self.assertEqual([item["area"] for item in data["focus"]], ["lyric_timeline", "content"])
        self.assertEqual(data["remediation"][0]["area"], "lyric_timeline")
        self.assertEqual(data["experiment_plan"]["key"], "lyric_timeline_control")
        self.assertIn("local/media_position_pipeline.py", data["focus"][0]["files"])
        self.assertIn("local/album_art_pipeline.py", data["focus"][1]["files"])

    def test_support_guide_carries_music_wave_cadence_remediation(self):
        guide = build_diagnostics_support_guide(
            {
                "status": "ready",
                "ready": True,
                "dominant_area": "music_wave_cadence",
                "bottleneck_group": "",
            }
        )

        data = guide.as_dict()
        self.assertEqual(data["focus"][0]["area"], "music_wave_cadence")
        self.assertEqual(data["remediation"][0]["area"], "music_wave_cadence")
        self.assertEqual(data["remediation"][0]["key"], "separate_music_wave_cadence")
        self.assertIn("music_wave_cadence_diagnostics", data["remediation"][0]["verify"])
        self.assertEqual(data["experiment_plan"]["key"], "music_wave_cadence_control")
        self.assertEqual(data["experiment_plan"]["setting_changes"], {})
        self.assertIn("cadence_limited", " ".join(data["experiment_plan"]["success_criteria"]))

    def test_support_focus_handles_missing_or_invalid_evidence(self):
        self.assertEqual(
            diagnostics_support_focus({"status": "invalid", "ready": False}),
            ("focus: diagnostics export structure; re-export before code changes",),
        )
        self.assertEqual(
            diagnostics_support_focus({"status": "missing", "ready": False}),
            ("focus: runtime evidence capture; keep the island visible and export again before code changes",),
        )

    def test_main_returns_zero_and_prints_text_for_ready_export(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "gluspot-runtime-diagnostics.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "generated_at": "2026-06-28T12:34:56+08:00",
                        "app": {"name": "GluSpot", "version": "6.402"},
                        "diagnostics": complete_diagnostics(),
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()

            code = main([str(path)], stdout=output)

        self.assertEqual(code, 0)
        self.assertIn("status: ready", output.getvalue())
        self.assertIn("next_action:", output.getvalue())

    def test_main_returns_one_for_partial_export(self):
        partial = complete_diagnostics(
            runtime_diagnostics_trace={"sample_count": 1, "window_seconds": 0.0},
            visual_frame_diagnostics={"frame_count": 1, "sample_count": 1},
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "gluspot-runtime-diagnostics.json"
            path.write_text(json.dumps({"diagnostics": partial}), encoding="utf-8")
            output = io.StringIO()

            code = main([str(path)], stdout=output)

        self.assertEqual(code, 1)
        self.assertIn("status: partial", output.getvalue())

    def test_main_returns_two_and_can_emit_json_for_invalid_export(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "broken.json"
            path.write_text("{broken", encoding="utf-8")
            output = io.StringIO()

            code = main(["--json", str(path)], stdout=output)
            report = json.loads(output.getvalue())

        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["issues"][0]["key"], "invalid_json")

    def test_main_can_emit_guide_json_without_changing_report_json_shape(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "gluspot-runtime-diagnostics.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "generated_at": "2026-06-28T12:34:56+08:00",
                        "app": {"name": "GluSpot", "version": "6.402"},
                        "diagnostics": complete_diagnostics(),
                    }
                ),
                encoding="utf-8",
            )
            report_output = io.StringIO()
            guide_output = io.StringIO()

            report_code = main(["--json", str(path)], stdout=report_output)
            guide_code = main(["--guide-json", str(path)], stdout=guide_output)

        report = json.loads(report_output.getvalue())
        guide = json.loads(guide_output.getvalue())
        self.assertEqual(report_code, 0)
        self.assertEqual(guide_code, 0)
        self.assertIn("status", report)
        self.assertNotIn("focus", report)
        self.assertNotIn("remediation", report)
        self.assertNotIn("experiment_plan", report)
        self.assertEqual(guide["exit_code"], 0)
        self.assertEqual(guide["report"]["status"], "ready")
        self.assertEqual(guide["focus"][0]["area"], "reactive_effects")
        self.assertEqual(guide["remediation"][0]["area"], "reactive_effects")
        self.assertEqual(guide["experiment_plan"]["key"], "reactive_contention_ablation")
        self.assertEqual(
            guide["experiment_plan"]["setting_changes"]["v2_music_reactive_background_intensity"],
            35,
        )
        self.assertIn("tests/test_visual_diagnostics.py", guide["focus"][0]["tests"])

    def test_main_guide_json_can_compare_before_and_after_exports(self):
        before = complete_diagnostics(
            runtime_diagnostics_snapshot={
                "status": "warning",
                "dominant_area": "runtime_contention",
                "bottleneck_group": "reactive_effects",
            },
            runtime_diagnostics_trace={
                "sample_count": 5,
                "window_seconds": 4.0,
                "dominant_area": "runtime_contention",
                "bottleneck_group": "reactive_effects",
                "latest": {"status": "warning"},
            },
            visual_runtime_context={
                "ambience_style": "sea_level",
                "effective_ambience_style": "sea_level",
                "lyric_flow_style": "ember_fade",
                "reactive_background_enabled": True,
                "reactive_background_intensity": 70,
            },
            visual_frame_diagnostics={
                "frame_count": 120,
                "sample_count": 119,
                "target_interval_ms": 16.0,
                "average_interval_ms": 18.0,
                "max_interval_ms": 24.0,
                "long_frame_count": 0,
            },
            visual_paint_section_diagnostics={
                "groups": {"reactive_effects": {"average_ms": 8.0, "last_ms": 13.0}}
            },
            lyric_timeline_diagnostics={"sync_lag_lines": 2, "visible_lyric_index": 3, "position_active_index": 5},
        )
        after = complete_diagnostics(
            runtime_diagnostics_snapshot={"status": "ok", "dominant_area": "", "bottleneck_group": "content"},
            runtime_diagnostics_trace={
                "sample_count": 5,
                "window_seconds": 4.0,
                "dominant_area": "",
                "bottleneck_group": "content",
                "latest": {"status": "ok"},
            },
            visual_runtime_context={
                "ambience_style": "sea_level",
                "effective_ambience_style": "sea_level",
                "lyric_flow_style": "ember_fade",
                "reactive_background_enabled": True,
                "reactive_background_intensity": 35,
            },
            visual_frame_diagnostics={
                "frame_count": 120,
                "sample_count": 119,
                "target_interval_ms": 16.0,
                "average_interval_ms": 16.2,
                "max_interval_ms": 20.0,
                "long_frame_count": 0,
            },
            visual_paint_section_diagnostics={
                "groups": {"reactive_effects": {"average_ms": 2.0, "last_ms": 3.0}}
            },
            lyric_timeline_diagnostics={"sync_lag_lines": 0, "visible_lyric_index": 5, "position_active_index": 5},
        )
        with tempfile.TemporaryDirectory() as temp:
            before_path = Path(temp) / "before.json"
            after_path = Path(temp) / "after.json"
            before_path.write_text(json.dumps({"diagnostics": before}), encoding="utf-8")
            after_path.write_text(json.dumps({"diagnostics": after}), encoding="utf-8")
            output = io.StringIO()

            code = main(
                ["--guide-json", str(before_path), "--compare-after", str(after_path)],
                stdout=output,
            )

        guide = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(guide["experiment"]["classification"], "reactive_budget_confirmed")
        self.assertEqual(guide["experiment"]["before"]["dominant_area"], "runtime_contention")
        self.assertEqual(guide["experiment"]["after"]["sync_lag_lines"], 0)
        self.assertTrue(guide["experiment"]["evidence"]["reactive_pressure_dropped"])

    def test_main_guide_json_can_compare_music_wave_cadence_exports(self):
        before = complete_diagnostics(
            runtime_diagnostics_snapshot={
                "status": "attention",
                "dominant_area": "music_wave_cadence",
                "bottleneck_group": "content",
            },
            runtime_diagnostics_trace={
                "sample_count": 5,
                "window_seconds": 4.0,
                "dominant_area": "music_wave_cadence",
                "bottleneck_group": "content",
                "latest": {"status": "attention"},
            },
            visual_frame_diagnostics={
                "frame_count": 120,
                "sample_count": 119,
                "target_interval_ms": 16.0,
                "average_interval_ms": 16.2,
                "max_interval_ms": 20.0,
                "long_frame_count": 0,
            },
            visual_paint_section_diagnostics={
                "groups": {"content": {"average_ms": 2.0, "last_ms": 2.2}}
            },
            lyric_timeline_diagnostics={"sync_lag_lines": 0, "visible_lyric_index": 3, "position_active_index": 3},
            music_wave_cadence_diagnostics={
                "status": "ready",
                "update_count": 118,
                "estimated_update_fps": 58.0,
                "render_average_fps": 60.0,
                "wave_to_render_fps_ratio": 0.967,
                "cadence_limited": False,
            },
        )
        after = complete_diagnostics(
            runtime_diagnostics_snapshot={"status": "ok", "dominant_area": "", "bottleneck_group": "content"},
            runtime_diagnostics_trace={
                "sample_count": 5,
                "window_seconds": 4.0,
                "dominant_area": "",
                "bottleneck_group": "content",
                "latest": {"status": "ok"},
            },
            visual_frame_diagnostics={
                "frame_count": 120,
                "sample_count": 119,
                "target_interval_ms": 16.0,
                "average_interval_ms": 16.2,
                "max_interval_ms": 20.0,
                "long_frame_count": 0,
            },
            visual_paint_section_diagnostics={
                "groups": {"content": {"average_ms": 2.0, "last_ms": 2.2}}
            },
            lyric_timeline_diagnostics={"sync_lag_lines": 0, "visible_lyric_index": 3, "position_active_index": 3},
            music_wave_cadence_diagnostics={
                "status": "ready",
                "update_count": 60,
                "estimated_update_fps": 30.0,
                "render_average_fps": 60.0,
                "wave_to_render_fps_ratio": 0.5,
                "cadence_limited": True,
            },
        )
        with tempfile.TemporaryDirectory() as temp:
            before_path = Path(temp) / "before.json"
            after_path = Path(temp) / "after.json"
            before_path.write_text(json.dumps({"diagnostics": before}), encoding="utf-8")
            after_path.write_text(json.dumps({"diagnostics": after}), encoding="utf-8")
            output = io.StringIO()

            code = main(
                ["--guide-json", str(before_path), "--compare-after", str(after_path)],
                stdout=output,
            )

        guide = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(guide["experiment_plan"]["key"], "music_wave_cadence_control")
        self.assertEqual(guide["experiment"]["classification"], "music_wave_cadence_confirmed")
        self.assertAlmostEqual(
            guide["experiment"]["evidence"]["after_music_wave_to_render_fps_ratio"],
            0.5,
            delta=0.001,
        )
        self.assertTrue(guide["experiment"]["evidence"]["after_music_wave_cadence_limited"])


if __name__ == "__main__":
    unittest.main()
