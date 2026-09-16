import unittest

from local.island_diagnostics import (
    VisibleMediaDiagnostics,
    VisualRuntimeDiagnostics,
    build_island_provider_diagnostics,
)


class IslandDiagnosticsTests(unittest.TestCase):
    def test_build_provider_diagnostics_merges_provider_and_runtime_evidence(self):
        visual_frame = {
            "frame_count": 90,
            "sample_count": 89,
            "average_interval_ms": 16.2,
            "target_interval_ms": 16.0,
            "jitter_ms": 1.2,
            "max_interval_ms": 20.0,
            "long_frame_count": 0,
        }
        visual_paint = {
            "section_count": 2,
            "groups": {
                "background_surface": {"average_ms": 2.0, "last_ms": 2.2},
                "content": {"average_ms": 1.8, "last_ms": 1.9},
            },
        }
        reactive_input = {
            "reactive_enabled": True,
            "live_audio_available": True,
            "band_source": "cache",
            "sampled_bands": (0.2, 0.3, 0.4),
        }
        wave_cadence = {
            "status": "ready",
            "estimated_update_fps": 30.0,
            "render_average_fps": 60.0,
            "wave_to_render_fps_ratio": 0.5,
            "cadence_limited": True,
        }

        diagnostics = build_island_provider_diagnostics(
            provider_diagnostics={"provider_status": "ok", "track_title": "provider title"},
            visible=VisibleMediaDiagnostics(
                title="visible title",
                artist="visible artist",
                track_id="track-1",
                source_app="CloudMusic",
                position_source="netease",
                position_reason="trusted_or_seek_raw",
                position_confidence=0.76,
                mydockfinder_running=True,
                mydockfinder_diagnostics={"matched_name": "Mydock.exe"},
            ),
            visual_context=VisualRuntimeDiagnostics(
                ambience_style="duotone_full",
                effective_ambience_style="duotone_full",
                lyric_flow_style="ember_fade",
                expanded=True,
                location_mode="tasker",
                reactive_background_enabled=True,
                reactive_background_intensity=72,
                animation_fps=60,
                lyric_animation_fps=45,
                animation_speed=1.25,
            ),
            lyric_diagnostics={
                "estimated_position_seconds": 42.4,
                "lyric_offset_seconds": 0.3,
                "visible_lyric_index": 5,
                "visible_current_lyric": "line",
                "visible_lyrics_count": 9,
                "phase_progress": 0.62,
                "phase_span": 3.5,
                "phase_source": "timed",
            },
            visual_frame_diagnostics=visual_frame,
            visual_paint_section_diagnostics=visual_paint,
            music_reactive_input_diagnostics=reactive_input,
            music_wave_cadence_diagnostics=wave_cadence,
        )

        self.assertEqual(diagnostics["provider_status"], "ok")
        self.assertEqual(diagnostics["track_title"], "provider title")
        self.assertEqual(diagnostics["visible_title"], "visible title")
        self.assertEqual(diagnostics["visible_artist"], "visible artist")
        self.assertEqual(diagnostics["visible_track_id"], "track-1")
        self.assertEqual(diagnostics["visible_source_app"], "CloudMusic")
        self.assertEqual(diagnostics["visible_position_source"], "netease")
        self.assertEqual(diagnostics["visible_position_reason"], "trusted_or_seek_raw")
        self.assertEqual(diagnostics["visible_position_confidence"], 0.76)
        self.assertFalse(diagnostics["visible_position_frontstage"])
        self.assertTrue(diagnostics["mydockfinder_running"])
        self.assertEqual(diagnostics["mydockfinder_diagnostics"], {"matched_name": "Mydock.exe"})
        self.assertEqual(diagnostics["estimated_position_seconds"], 42.4)
        self.assertEqual(diagnostics["visible_lyric_index"], 5)
        self.assertEqual(diagnostics["visible_current_lyric"], "line")
        self.assertEqual(diagnostics["lyric_phase_progress"], 0.62)
        self.assertEqual(diagnostics["lyric_phase_source"], "timed")
        self.assertEqual(diagnostics["lyric_timeline_diagnostics"]["phase_span"], 3.5)
        self.assertEqual(diagnostics["visual_frame_diagnostics"], visual_frame)
        self.assertEqual(diagnostics["visual_paint_section_diagnostics"], visual_paint)
        self.assertEqual(diagnostics["music_reactive_input_diagnostics"], reactive_input)
        self.assertEqual(diagnostics["music_wave_cadence_diagnostics"], wave_cadence)
        self.assertEqual(diagnostics["visual_runtime_context"]["ambience_style"], "duotone_full")
        self.assertEqual(diagnostics["visual_runtime_context"]["lyric_flow_style"], "ember_fade")
        self.assertTrue(diagnostics["visual_runtime_context"]["expanded"])
        self.assertTrue(diagnostics["visual_runtime_context"]["reactive_background_enabled"])
        self.assertEqual(diagnostics["visual_runtime_context"]["reactive_background_intensity"], 72)
        self.assertEqual(diagnostics["visual_runtime_context"]["lyric_animation_fps"], 45)
        self.assertIn("runtime_diagnostics_snapshot", diagnostics)
        self.assertEqual(diagnostics["runtime_diagnostics_snapshot"]["status"], "ok")

    def test_visible_frontstage_uses_foreground_interactive_or_interactive_source(self):
        self.assertTrue(VisibleMediaDiagnostics(source_foreground=True).position_frontstage)
        self.assertTrue(VisibleMediaDiagnostics(position_interactive=True).position_frontstage)
        self.assertTrue(VisibleMediaDiagnostics(position_source="interactive").position_frontstage)
        self.assertFalse(VisibleMediaDiagnostics(position_source="netease").position_frontstage)

    def test_build_provider_diagnostics_keeps_provider_error_with_empty_runtime(self):
        diagnostics = build_island_provider_diagnostics(
            provider_diagnostics={"provider_error": "boom"},
            visible=None,
            lyric_diagnostics=None,
            visual_frame_diagnostics=None,
            visual_paint_section_diagnostics=None,
            music_reactive_input_diagnostics=None,
            music_wave_cadence_diagnostics=None,
        )

        self.assertEqual(diagnostics["provider_error"], "boom")
        self.assertEqual(diagnostics["visible_title"], "")
        self.assertEqual(diagnostics["estimated_position_seconds"], 0.0)
        self.assertEqual(diagnostics["lyric_timeline_diagnostics"], {})
        self.assertEqual(diagnostics["visual_frame_diagnostics"], {})
        self.assertEqual(diagnostics["visual_paint_section_diagnostics"], {})
        self.assertEqual(diagnostics["music_reactive_input_diagnostics"], {})
        self.assertEqual(diagnostics["music_wave_cadence_diagnostics"], {})
        self.assertEqual(diagnostics["visual_runtime_context"]["ambience_style"], "sea_level")
        self.assertEqual(diagnostics["visual_runtime_context"]["lyric_flow_style"], "wave_stream")
        self.assertEqual(diagnostics["runtime_diagnostics_snapshot"]["status"], "missing")


if __name__ == "__main__":
    unittest.main()
