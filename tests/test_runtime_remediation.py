import unittest

from local.runtime_remediation import runtime_remediation_actions


class RuntimeRemediationTests(unittest.TestCase):
    def test_runtime_contention_prioritizes_reactive_intensity_before_lyric_fps(self):
        actions = runtime_remediation_actions(
            {
                "status": "ready",
                "ready": True,
                "dominant_area": "runtime_contention",
                "bottleneck_group": "reactive_effects",
            }
        )

        keys = [action.key for action in actions]
        self.assertEqual(keys[0], "lower_reactive_intensity_for_contention")
        self.assertIn("hold_lyric_fps_before_visual_fps", keys)
        self.assertIn("profile_reactive_renderer", keys)
        self.assertIn("v2_music_reactive_background_intensity", actions[0].setting_keys)
        self.assertIn("dominant_area", actions[0].verify)

    def test_partial_report_asks_for_runtime_capture_before_repair(self):
        actions = runtime_remediation_actions({"status": "partial", "ready": False})

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].key, "capture_runtime_window")
        self.assertEqual(actions[0].area, "runtime_evidence")

    def test_invalid_report_asks_for_reexport(self):
        actions = runtime_remediation_actions({"status": "invalid", "ready": False})

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].key, "reexport_diagnostics")

    def test_lyric_timeline_focus_points_to_position_pipeline_not_karaoke(self):
        actions = runtime_remediation_actions(
            {
                "status": "ready",
                "ready": True,
                "dominant_area": "lyric_timeline",
                "bottleneck_group": "content",
            }
        )

        keys = [action.key for action in actions]
        self.assertIn("verify_media_position_pipeline", keys)
        lyric_action = next(action for action in actions if action.key == "verify_media_position_pipeline")
        self.assertIn("line-level LRC", lyric_action.summary)

    def test_music_wave_cadence_focus_points_to_cadence_decoupling(self):
        actions = runtime_remediation_actions(
            {
                "status": "ready",
                "ready": True,
                "dominant_area": "music_wave_cadence",
                "bottleneck_group": "",
            }
        )

        self.assertEqual(actions[0].key, "separate_music_wave_cadence")
        self.assertEqual(actions[0].area, "music_wave_cadence")
        self.assertIn("v2_lyric_animation_fps", actions[0].setting_keys)
        self.assertIn("music_wave_cadence_diagnostics", actions[0].verify)


if __name__ == "__main__":
    unittest.main()
