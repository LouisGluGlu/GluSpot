import unittest

from local.lyric_paint_state import build_lyric_paint_state


class LyricPaintStateTests(unittest.TestCase):
    def test_build_lyric_paint_state_returns_phase_and_next_cache(self):
        state = build_lyric_paint_state(
            timed_lyrics=((0.0, "one"), (4.0, "two")),
            lyric_index=0,
            estimated_position_seconds=2.0,
            lyric_offset_seconds=0.0,
            lyric_changed_at=10.0,
            previous_phase_index=-1,
            previous_phase_progress=0.0,
            previous_phase_at=10.0,
            now=12.0,
            media_is_playing=True,
        )

        self.assertEqual(state.cache.active_index, 0)
        self.assertAlmostEqual(state.cache.updated_at, 12.0, delta=0.001)
        self.assertAlmostEqual(state.phase["progress"], 0.5, delta=0.001)
        self.assertAlmostEqual(state.cache.progress, 0.5, delta=0.001)
        self.assertEqual(state.phase["source"], "timed")

    def test_build_lyric_paint_state_prevents_small_timed_progress_backstep(self):
        state = build_lyric_paint_state(
            timed_lyrics=((0.0, "one"), (4.0, "two")),
            lyric_index=0,
            estimated_position_seconds=1.0,
            lyric_offset_seconds=0.0,
            lyric_changed_at=10.0,
            previous_phase_index=0,
            previous_phase_progress=0.5,
            previous_phase_at=20.0,
            now=20.2,
            media_is_playing=True,
        )

        self.assertGreaterEqual(state.phase["progress"], 0.5)
        self.assertAlmostEqual(state.cache.progress, state.phase["progress"], delta=0.001)

    def test_build_lyric_paint_state_uses_offset_and_coerces_invalid_cache(self):
        state = build_lyric_paint_state(
            timed_lyrics=((0.0, "one"), (4.0, "two")),
            lyric_index="bad",
            estimated_position_seconds=1.0,
            lyric_offset_seconds=1.0,
            lyric_changed_at="bad",
            previous_phase_index="bad",
            previous_phase_progress="bad",
            previous_phase_at="bad",
            now=12.0,
            media_is_playing=False,
        )

        self.assertEqual(state.cache.active_index, 0)
        self.assertAlmostEqual(state.phase["progress"], 0.5, delta=0.001)

    def test_build_lyric_paint_state_can_select_active_index_from_position(self):
        state = build_lyric_paint_state(
            timed_lyrics=((0.0, "one"), (4.0, "two"), (8.0, "three")),
            lyric_index=0,
            estimated_position_seconds=8.1,
            lyric_offset_seconds=0.0,
            lyric_changed_at=10.0,
            previous_phase_index=-1,
            previous_phase_progress=0.0,
            previous_phase_at=12.0,
            now=12.0,
            media_is_playing=True,
            position_selects_active_index=True,
            lead_seconds=0.0,
        )

        self.assertEqual(state.phase["active_index"], 2)
        self.assertEqual(state.cache.active_index, 2)


if __name__ == "__main__":
    unittest.main()
