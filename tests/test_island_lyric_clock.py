import unittest
from types import SimpleNamespace

from api.audio_routes import AudioRouteSnapshot
from local.island_stack_visual import IslandStackVisual


class IslandLyricClockTests(unittest.TestCase):
    def _visual(self):
        visual = IslandStackVisual.__new__(IslandStackVisual)
        visual.media_track_id = "song"
        visual.media_position_seconds = visual.media_raw_position_seconds = 10.0
        visual.media_position_sync_at = visual.media_position_sampled_at = 100.0
        visual.media_position_source = "windows-media"
        visual.media_duration_seconds = 90.0
        visual.media_is_playing = True
        visual.lyric_offset_seconds = visual.media_app_lyric_offset_seconds = 0.0
        visual.timed_lyrics = [(i * 4.0, str(i)) for i in range(20)]
        visual.lyrics = [row[1] for row in visual.timed_lyrics]
        visual.timed_lyric_units = [() for _ in visual.lyrics]
        visual.timed_lyric_timing_kinds = ["line" for _ in visual.lyrics]
        visual.lyric_index = visual.prev_lyric_index = 2
        visual.lyric_changed_at = 100.0
        return visual

    def test_repeated_bad_source_position_does_not_skip_rows_or_word_time(self):
        visual = self._visual()
        visual._sync_timed_lyrics(100)
        visual.media_position_seconds = visual.media_raw_position_seconds = 50.0
        visual.media_position_sync_at = visual.media_position_sampled_at = 100.1
        visual._sync_timed_lyrics(100.1)
        for now in (100.2, 100.75, 101.0):
            visual.media_position_sync_at = visual.media_position_sampled_at = now
            visual.media_position_seconds = 50.0
            visual._sync_timed_lyrics(now)
            self.assertEqual(visual.lyric_index, 2)
            self.assertLess(visual._stable_lyric_source_position(now), 12.0)
        visual.media_position_seconds = visual.media_raw_position_seconds = 11.2
        visual.media_position_sync_at = visual.media_position_sampled_at = 101.2
        self.assertAlmostEqual(visual._stable_lyric_source_position(101.2), 11.2, places=1)

    def test_paint_cannot_consume_output_delay_change_before_row_sync(self):
        visual = self._visual()
        visual.media_position_seconds = visual.media_raw_position_seconds = 4.1
        visual.lyric_index = 1
        visual._sync_timed_lyrics(100)
        visual.lyric_offset_seconds = -0.3
        visual._lyric_phase(100)
        self.assertTrue(visual._sync_timed_lyrics(100))
        self.assertEqual(visual.lyric_index, 0)

    def test_delivery_projection_cannot_make_frozen_observation_advance(self):
        visual = self._visual()
        visual.media_position_observed_seconds = 10.0
        visual._sync_timed_lyrics(100)
        for at, observed_at, projected in ((100.2, 100.1, 50.1), (101.5, 100.75, 50.75)):
            visual.media_position_observed_seconds = 50.0
            visual.media_raw_position_seconds = visual.media_position_seconds = projected
            visual.media_position_sync_at = at
            visual.media_position_sampled_at = observed_at
            visual._sync_timed_lyrics(at)
            self.assertLess(visual._stable_lyric_source_position(at), 12)
            self.assertEqual(visual.lyric_index, 2)

    def test_confirmed_small_backward_seek_is_not_suppressed_a_second_time(self):
        visual = self._visual()
        visual.media_position_seconds = visual.media_raw_position_seconds = 4.1
        visual.lyric_index = 1
        visual._sync_timed_lyrics(100)
        visual.media_position_seconds = visual.media_raw_position_seconds = 2.7
        visual.media_position_sync_at = visual.media_position_sampled_at = 100.1
        visual._sync_timed_lyrics(100.1)
        visual.media_position_seconds = visual.media_raw_position_seconds = 2.9
        visual.media_position_sync_at = visual.media_position_sampled_at = 100.3
        visual._sync_timed_lyrics(100.3)
        self.assertEqual(visual.lyric_index, 0)

    def test_cast_compensation_never_changes_source_seek_clock(self):
        visual = self._visual()
        route = [AudioRouteSnapshot(state="ready")]
        visual.data_provider = SimpleNamespace(get_audio_route_snapshot=lambda: route[0])
        visual._sync_timed_lyrics(100)
        route[0] = AudioRouteSnapshot(state="casting", cast_latency_seconds=2.5)
        visual._sync_timed_lyrics(100.1)
        self.assertEqual(visual.lyric_index, 1)
        self.assertAlmostEqual(visual._stable_lyric_source_position(100.1), 10.1)
        self.assertEqual(visual._effective_lyric_offset_seconds(include_output_latency=False), 0)
        self.assertIsNone(visual._island_lyric_source_clock.pending_clock_position)

    def test_fresh_paused_seek_and_new_song_reanchor(self):
        visual = self._visual()
        visual._sync_timed_lyrics(100)
        visual.media_is_playing = False
        visual._sync_timed_lyrics(100.1)
        visual.media_position_seconds = visual.media_raw_position_seconds = 2.0
        visual.media_position_sync_at = visual.media_position_sampled_at = 100.2
        visual._sync_timed_lyrics(100.2)
        self.assertEqual(visual.lyric_index, 0)
        visual.media_is_playing = True
        visual.media_track_id = "new song"
        visual.media_position_seconds = visual.media_raw_position_seconds = 0.0
        visual.media_position_sync_at = visual.media_position_sampled_at = 100.3
        self.assertEqual(visual._stable_lyric_source_position(100.3), 0.0)

    def test_unknown_paused_observation_cannot_seek_from_stamp_alone(self):
        visual = self._visual()
        visual.media_is_playing = False
        visual.media_position_observed_seconds = None
        visual._sync_timed_lyrics(100)
        for now in (100.1, 100.2, 101.0):
            visual.media_position_seconds = visual.media_raw_position_seconds = 50.0
            visual.media_position_sync_at = visual.media_position_sampled_at = now
            visual._sync_timed_lyrics(now)
            self.assertEqual(visual.lyric_index, 2)


if __name__ == "__main__":
    unittest.main()
