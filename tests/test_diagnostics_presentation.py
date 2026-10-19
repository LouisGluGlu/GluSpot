import unittest

from local.diagnostics_presentation import (
    DIAGNOSTIC_FIELD_ROWS,
    format_diagnostics_evidence,
    format_runtime_experiment_compare,
    format_runtime_experiment_plan,
    format_runtime_bottleneck,
    format_runtime_health,
    format_runtime_trace,
    format_runtime_verification,
    format_settings_diagnostics,
    format_visual_paint_hot_groups,
    format_visual_paint_hot_sections,
    format_visual_runtime_context,
)


class DiagnosticsPresentationTests(unittest.TestCase):
    def test_visual_context_groups_immersive_placements_under_three_public_modes(self):
        for mode, label in (("tasker", "沉浸栏 · Tasker Bar"), ("top", "Top"), ("free", "Free"),
                            ("finder", "沉浸栏 · Finder Bar"), ("stick", "Free")):
            with self.subTest(mode=mode):
                self.assertIn(f"紧凑/{label}", format_visual_runtime_context({"location_mode": mode}))

    def test_finder_diagnostics_distinguish_dock_process_from_visible_top_bar(self):
        values = format_settings_diagnostics({"mydockfinder_running": True,
                                              "mydockfinder_diagnostics": {"finder_enabled": False}})
        self.assertEqual(values["mydockfinder_running"], "开启")
        self.assertIn("尚未检测", values["finder_bar_state"])
        diagnostics = {"finder_enabled": True, "finder_bars": [{"enabled": True, "visible": True}]}
        self.assertEqual(format_settings_diagnostics({"mydockfinder_running": True,
                                                     "mydockfinder_diagnostics": diagnostics})["finder_bar_state"], "已开启 · 已显示")
        self.assertEqual(format_settings_diagnostics({"mydockfinder_running": True, "finder_bar_visible": False,
                                                     "mydockfinder_diagnostics": diagnostics})["finder_bar_state"], "已开启 · 自动隐藏")
        self.assertIn("尚未检测", format_settings_diagnostics({"mydockfinder_running": True,
                                                              "finder_bar_enabled": False,
                                                              "mydockfinder_diagnostics": diagnostics})["finder_bar_state"])
        self.assertEqual(format_settings_diagnostics({"mydockfinder_running": True,
                                                     "mydockfinder_diagnostics": {"finder_enabled": True, "finder_bars": 42}})["finder_bar_state"],
                         "已开启 · 自动隐藏")

    def test_field_rows_include_runtime_visual_and_lyric_diagnostics(self):
        keys = {key for key, _title in DIAGNOSTIC_FIELD_ROWS}

        self.assertIn("lyric_timeline_lag", keys)
        self.assertIn("music_reactive_input", keys)
        self.assertIn("visual_runtime_context", keys)
        self.assertIn("diagnostics_evidence", keys)
        self.assertIn("diagnostics_next_action", keys)
        self.assertIn("runtime_experiment_plan", keys)
        self.assertIn("runtime_experiment_compare", keys)
        self.assertIn("runtime_verification", keys)
        self.assertIn("runtime_health", keys)
        self.assertIn("runtime_bottleneck", keys)
        self.assertIn("runtime_trace", keys)
        self.assertIn("visual_frame_rate", keys)
        self.assertIn("visual_frame_jitter", keys)
        self.assertIn("visual_frame_long_frames", keys)
        self.assertIn("visual_paint_hot_groups", keys)
        self.assertIn("visual_paint_hot_sections", keys)

    def test_field_row_labels_are_readable_chinese(self):
        labels = dict(DIAGNOSTIC_FIELD_ROWS)
        self.assertEqual(labels["runtime_experiment_plan"], "运行实验")
        self.assertEqual(labels["runtime_experiment_compare"], "实验对比")

        self.assertEqual(labels["provider_status"], "媒体状态")
        self.assertEqual(labels["visual_runtime_context"], "视觉上下文")
        self.assertEqual(labels["diagnostics_evidence"], "证据结论")
        self.assertEqual(labels["diagnostics_next_action"], "诊断下一步")
        self.assertEqual(labels["runtime_verification"], "运行证据")
        self.assertEqual(labels["runtime_bottleneck"], "疑似瓶颈")

    def test_format_settings_diagnostics_surfaces_runtime_evidence(self):
        values = format_settings_diagnostics(
            {
                "provider_status": "ok",
                "winsdk_loaded": True,
                "music_player_mode": "qq",
                "position_confidence": 0.83,
                "visual_runtime_context": {
                    "ambience_style": "sea_level",
                    "effective_ambience_style": "sea_level",
                    "lyric_flow_style": "ember_fade",
                    "expanded": True,
                    "location_mode": "tasker",
                    "taskbar_compact_active": True,
                    "reactive_background_enabled": True,
                    "reactive_background_intensity": 72,
                    "animation_fps": 60,
                    "lyric_animation_fps": 45,
                    "animation_speed": 1.2,
                },
                "runtime_verification_report": {
                    "status": "ready",
                    "ready": True,
                    "summary": "运行证据可用，健康状态 warning / 主因 reactive_effects",
                    "health_status": "warning",
                    "dominant_area": "reactive_effects",
                    "bottleneck_group": "reactive_effects",
                    "suggested_wait_seconds": 0,
                    "next_action": "运行证据已足够，可以导出并用于判断卡顿、歌词跟进或绘制瓶颈",
                },
                "lyric_timeline_diagnostics": {
                    "sync_lag_lines": 2,
                    "visible_lyric_index": 4,
                    "position_active_index": 6,
                    "phase_source": "timed",
                },
                "music_reactive_input_diagnostics": {
                    "live_audio_available": True,
                    "band_source": "cache",
                    "intensity": 0.72,
                    "sampled_bands": (0.9, 0.5, 0.2),
                    "sample_age_ms": 48.0,
                },
                "runtime_diagnostics_snapshot": {
                    "status": "warning",
                    "headline": "paint top reactive_effects last 14.0ms avg 3.0ms target 16.0ms",
                    "dominant_area": "reactive_effects",
                    "bottleneck_group": "reactive_effects",
                    "bottleneck_summary": "paint top reactive_effects last 14.0ms avg 3.0ms target 16.0ms",
                },
                "runtime_diagnostics_trace": {
                    "sample_count": 5,
                    "window_seconds": 4.0,
                    "warning_count": 2,
                    "attention_count": 1,
                    "dominant_area": "reactive_effects",
                    "bottleneck_group": "reactive_effects",
                },
                "visual_frame_diagnostics": {
                    "average_fps": 57.8,
                    "average_interval_ms": 17.3,
                    "target_interval_ms": 16.0,
                    "jitter_ms": 5.2,
                    "max_interval_ms": 38.0,
                    "long_frame_count": 3,
                    "frame_count": 120,
                    "last_reason": "music",
                },
                "visual_paint_section_diagnostics": {
                    "groups": {
                        "background_surface": {"average_ms": 3.2, "last_ms": 4.1},
                        "reactive_effects": {"average_ms": 1.4, "last_ms": 2.8},
                        "content": {"average_ms": 4.0, "last_ms": 3.1},
                    },
                    "sections": {
                        "music.total": {"average_ms": 7.2, "max_ms": 12.6},
                        "music.compact_content": {"average_ms": 4.0, "max_ms": 8.0},
                    },
                },
            },
            music_player_mode_label=lambda mode: f"mode:{mode}",
        )

        self.assertEqual(values["provider_status"], "ok")
        self.assertEqual(values["winsdk_loaded"], "可用")
        self.assertEqual(values["music_player_mode"], "mode:qq")
        self.assertEqual(values["position_confidence"], "83%")
        self.assertIn("+2 行", values["lyric_timeline_lag"])
        self.assertIn("显示 4", values["lyric_timeline_lag"])
        self.assertIn("live / cache", values["music_reactive_input"])
        self.assertIn("bands 0.90/0.50/0.20", values["music_reactive_input"])
        self.assertIn("sea_level", values["visual_runtime_context"])
        self.assertIn("歌词 ember_fade", values["visual_runtime_context"])
        self.assertIn("展开/沉浸栏 · Tasker Bar紧凑", values["visual_runtime_context"])
        self.assertIn("律动开 72", values["visual_runtime_context"])
        self.assertIn("证据可用", values["diagnostics_evidence"])
        self.assertIn("5 samples/4.0s", values["diagnostics_evidence"])
        self.assertIn("reactive_contention_ablation", values["runtime_experiment_plan"])
        self.assertIn("v2_music_reactive_background_intensity=35", values["runtime_experiment_plan"])
        self.assertIn("--compare-after", values["runtime_experiment_compare"])
        self.assertIn("5s before/after", values["runtime_experiment_compare"])
        self.assertIn("sea_level/ember_fade", values["diagnostics_evidence"])
        self.assertIn("主因 律动/过渡", values["diagnostics_evidence"])
        self.assertIn("音频平滑", values["diagnostics_next_action"])
        self.assertIn("证据可用", values["runtime_verification"])
        self.assertIn("主因 律动/过渡", values["runtime_verification"])
        self.assertIn("异常", values["runtime_health"])
        self.assertIn("律动/过渡", values["runtime_health"])
        self.assertIn("律动/过渡", values["runtime_bottleneck"])
        self.assertIn("paint top reactive_effects", values["runtime_bottleneck"])
        self.assertIn("5 samples / 4.0s", values["runtime_trace"])
        self.assertIn("主因 律动/过渡", values["runtime_trace"])
        self.assertIn("57.8 FPS", values["visual_frame_rate"])
        self.assertIn("jitter 5.2ms", values["visual_frame_jitter"])
        self.assertEqual(values["visual_frame_long_frames"], "3 / 120 / music")
        self.assertIn("背景 avg 3.2ms last 4.1ms", values["visual_paint_hot_groups"])
        self.assertIn("内容/歌词 avg 4.0ms last 3.1ms", values["visual_paint_hot_groups"])
        self.assertIn("music.total avg 7.2ms max 12.6ms", values["visual_paint_hot_sections"])

    def test_format_visual_runtime_context_summarizes_key_visual_switches(self):
        text = format_visual_runtime_context(
            {
                "ambience_style": "pure_white",
                "effective_ambience_style": "yaoguang",
                "lyric_flow_style": "wave_stream",
                "expanded": False,
                "location_mode": "free",
                "reactive_background_enabled": False,
                "reactive_background_intensity": 55,
                "animation_fps": 60,
                "lyric_animation_fps": 30,
                "animation_speed": 0.75,
            }
        )

        self.assertIn("yaoguang", text)
        self.assertIn("请求 pure_white", text)
        self.assertIn("紧凑/Free", text)
        self.assertIn("律动关 55", text)
        self.assertIn("FPS 60/歌词 30", text)

    def test_format_runtime_verification_summarizes_readiness_and_fault_area(self):
        text = format_runtime_verification(
            {
                "status": "partial",
                "ready": False,
                "summary": "运行证据不完整：最近运行诊断样本数不足",
                "health_status": "ok",
                "dominant_area": "frame_cadence",
                "bottleneck_group": "content",
                "suggested_wait_seconds": 2,
                "next_action": "保持音乐岛可见并运行约 2 秒后刷新或导出诊断",
            }
        )

        self.assertIn("证据不完整", text)
        self.assertIn("健康 ok", text)
        self.assertIn("主因 帧调度", text)
        self.assertIn("瓶颈 内容/歌词", text)
        self.assertIn("建议等待 2s", text)
        self.assertIn("运行约 2 秒", text)

    def test_format_diagnostics_evidence_summarizes_support_readiness(self):
        text = format_diagnostics_evidence(
            {
                "status": "ready",
                "ready": True,
                "health_status": "warning",
                "dominant_area": "lyric_timeline",
                "bottleneck_group": "content",
                "ambience_style": "pure_black",
                "lyric_flow_style": "wave_stream",
                "trace_sample_count": 6,
                "trace_window_seconds": 5.4,
            }
        )

        self.assertIn("证据可用", text)
        self.assertIn("健康 warning", text)
        self.assertIn("6 samples/5.4s", text)
        self.assertIn("pure_black/wave_stream", text)
        self.assertIn("主因 歌词跟进", text)
        self.assertIn("瓶颈 内容/歌词", text)

    def test_format_runtime_experiment_plan_summarizes_next_validation_step(self):
        plan = {
            "key": "reactive_contention_ablation",
            "summary": "lower music reactive intensity first",
            "setting_changes": {"v2_music_reactive_background_intensity": 35},
            "success_criteria": (
                "classification is reactive_budget_confirmed",
                "sync_lag_lines is 0 or +/-1",
                "extra item should be hidden",
            ),
        }
        compare = {
            **plan,
            "capture_seconds": 5,
            "compare_command": "python -m local.diagnostics_support before.json --compare-after after.json",
            "before_capture": "Export diagnostics before changing settings.",
            "after_capture": "Export diagnostics after the setting change.",
        }

        text = format_runtime_experiment_plan(plan)
        compare_text = format_runtime_experiment_compare(compare)

        self.assertIn("reactive_contention_ablation", text)
        self.assertIn("v2_music_reactive_background_intensity=35", text)
        self.assertIn("reactive_budget_confirmed", text)
        self.assertIn("sync_lag_lines", text)
        self.assertNotIn("extra item", text)
        self.assertIn("5s before/after", compare_text)
        self.assertIn("--compare-after", compare_text)

    def test_format_visual_paint_hot_groups_uses_group_labels_and_skips_total(self):
        text = format_visual_paint_hot_groups(
            {
                "groups": {
                    "total": {"average_ms": 20.0, "last_ms": 20.0},
                    "background_surface": {"average_ms": 7.0, "last_ms": 11.0},
                    "reactive_effects": {"average_ms": 3.0, "last_ms": 14.0},
                    "content": {"average_ms": 5.5, "last_ms": 6.0},
                }
            },
            limit=2,
        )

        self.assertTrue(text.startswith("律动/过渡"))
        self.assertIn("背景 avg 7.0ms last 11.0ms", text)
        self.assertNotIn("total", text)

    def test_format_runtime_snapshot_summarizes_status_and_bottleneck(self):
        snapshot = {
            "status": "attention",
            "headline": "lyric lag +1 lines visible 2 position 3 source timed",
            "dominant_area": "runtime_contention",
            "bottleneck_group": "content",
            "bottleneck_summary": "paint top content last 5.0ms avg 3.0ms target 16.0ms",
        }

        self.assertIn("需观察", format_runtime_health(snapshot))
        self.assertIn("律动/歌词争用", format_runtime_health(snapshot))
        self.assertIn("内容/歌词", format_runtime_bottleneck(snapshot))

    def test_format_runtime_trace_summarizes_window_counts_and_labels(self):
        text = format_runtime_trace(
            {
                "sample_count": 7,
                "window_seconds": 6.2,
                "warning_count": 3,
                "attention_count": 2,
                "dominant_area": "runtime_contention",
                "bottleneck_group": "content",
            }
        )

        self.assertIn("7 samples / 6.2s", text)
        self.assertIn("异常 3", text)
        self.assertIn("需观察 2", text)
        self.assertIn("主因 律动/歌词争用", text)
        self.assertIn("瓶颈 内容/歌词", text)

    def test_format_visual_paint_hot_sections_ranks_by_max_cost(self):
        text = format_visual_paint_hot_sections(
            {
                "sections": {
                    "music.compact_content": {"average_ms": 4.0, "max_ms": 8.0},
                    "music.reactive_background": {"average_ms": 3.0, "max_ms": 22.0},
                    "music.border": {"average_ms": 1.0, "max_ms": 2.0},
                    "music.total": {"average_ms": 7.0, "max_ms": 12.0},
                }
            },
            limit=2,
        )

        self.assertTrue(text.startswith("music.reactive_background"))
        self.assertIn("music.total", text)
        self.assertNotIn("music.compact_content", text)


if __name__ == "__main__":
    unittest.main()
