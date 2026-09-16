import unittest

from local.media_position_pipeline import looks_like_seek_to_start, media_position_update_plan


class MediaPositionPipelineTests(unittest.TestCase):
    def _plan(self, **overrides):
        values = {
            "track_changed": False,
            "incoming_is_playing": True,
            "incoming_position_seconds": 12.0,
            "incoming_position_source": "windows-media",
            "incoming_position_confidence": 1.0,
            "incoming_position_interactive": False,
            "incoming_source_foreground": False,
            "previous_position_seconds": 10.0,
            "previous_sync_at": 100.0,
            "previous_raw_position_seconds": 10.0,
            "previous_raw_position_source": "windows-media",
            "now": 101.0,
        }
        values.update(overrides)
        return media_position_update_plan(**values)

    def test_frontstage_uses_raw_position_and_fast_refresh(self):
        plan = self._plan(
            incoming_position_seconds=21.0,
            incoming_source_foreground=True,
            previous_position_seconds=20.0,
            previous_raw_position_seconds=20.0,
        )

        self.assertAlmostEqual(plan.position_seconds, 21.0)
        self.assertTrue(plan.frontstage_position)
        self.assertAlmostEqual(plan.refresh_interval_seconds, 0.18)
        self.assertEqual(plan.reason, "frontstage_raw")

    def test_frontstage_zero_can_seek_to_start(self):
        self.assertTrue(looks_like_seek_to_start(0.0, 42.0, 42.1))
        plan = self._plan(
            incoming_position_seconds=0.0,
            incoming_source_foreground=True,
            previous_position_seconds=42.0,
            previous_raw_position_seconds=42.1,
        )

        self.assertAlmostEqual(plan.position_seconds, 0.0)
        self.assertEqual(plan.reason, "frontstage_raw")

    def test_repeated_stale_trusted_source_preserves_previous_position(self):
        plan = self._plan(
            incoming_position_seconds=30.0,
            incoming_position_source="netease-uia-progress",
            incoming_position_confidence=0.9,
            previous_position_seconds=32.0,
            previous_raw_position_seconds=30.02,
            previous_raw_position_source="netease-uia-progress",
        )

        self.assertAlmostEqual(plan.position_seconds, 32.0)
        self.assertEqual(plan.reason, "trusted_repeated_stale_preserve_previous")

    def test_windows_fallback_after_external_source_preserves_previous_position(self):
        plan = self._plan(
            incoming_position_seconds=30.0,
            incoming_position_source="windows-media",
            incoming_position_confidence=1.0,
            previous_position_seconds=32.0,
            previous_raw_position_seconds=31.8,
            previous_raw_position_source="netease-uia-progress",
        )

        self.assertAlmostEqual(plan.position_seconds, 32.0)
        self.assertEqual(plan.reason, "stale_preserve_previous")

    def test_small_backward_drift_preserves_previous_position(self):
        plan = self._plan(
            incoming_position_seconds=19.8,
            incoming_position_confidence=0.3,
            previous_position_seconds=20.0,
            previous_raw_position_seconds=20.0,
        )

        self.assertAlmostEqual(plan.position_seconds, 20.0)
        self.assertEqual(plan.reason, "small_backward_preserve_previous")

    def test_small_forward_drift_is_eased_for_background_position(self):
        plan = self._plan(
            incoming_position_seconds=20.4,
            incoming_position_confidence=0.3,
            previous_position_seconds=20.0,
            previous_raw_position_seconds=20.0,
        )

        self.assertAlmostEqual(plan.position_seconds, 20.096, delta=0.0001)
        self.assertEqual(plan.reason, "small_drift_eased")

    def test_listened_delta_is_clamped_to_elapsed_plus_grace(self):
        plan = self._plan(
            incoming_position_seconds=50.0,
            incoming_position_source="interactive",
            incoming_position_interactive=True,
            previous_position_seconds=40.0,
            previous_raw_position_seconds=40.0,
            now=100.25,
        )

        self.assertAlmostEqual(plan.position_seconds, 50.0)
        self.assertAlmostEqual(plan.listened_delta_seconds, 1.25)

    def test_track_change_keeps_raw_position_without_listened_delta(self):
        plan = self._plan(
            track_changed=True,
            incoming_position_seconds=3.0,
            previous_position_seconds=40.0,
            previous_raw_position_seconds=40.0,
        )

        self.assertAlmostEqual(plan.position_seconds, 3.0)
        self.assertAlmostEqual(plan.listened_delta_seconds, 0.0)


if __name__ == "__main__":
    unittest.main()
