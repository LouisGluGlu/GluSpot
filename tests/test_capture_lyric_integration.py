"""Accepted model discontinuities must reach both existing lyric consumers."""
import unittest
from unittest.mock import patch

from local.music_surface_window import MusicSurfaceWindow
from tests.test_music_playback_sync import MusicPlaybackClockTests


class CaptureLyricIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        MusicPlaybackClockTests.setUpClass()

    def test_sparse_external_seek_reanchors_island_and_large_surface(self):
        # Reuse the real V2 owner fixture and its lifecycle cleanup.
        visual, _data, _track = MusicPlaybackClockTests._owner(self)
        surface = MusicSurfaceWindow(visual)
        self.addCleanup(surface.shutdown)
        visual.media_position_source = 'capture-model'
        visual.media_position_sampled_at = 100
        visual.media_position_observed_seconds = 30
        surface._lyric_frame([(0, 'first'), (40, 'second')], 100)
        visual._stable_lyric_source_position(100)
        for playing in (True, False):
            with self.subTest(playing=playing):
                visual.media_is_playing = playing
                visual.media_position_seconds = 60
                visual.media_position_sync_at = 101
                visual.media_position_sampled_at = 101
                visual.media_position_observed_seconds = 60
                self.assertAlmostEqual(visual._stable_lyric_source_position(101), 60)
                surface._lyric_frame([(0, 'first'), (40, 'second')], 101)
                self.assertAlmostEqual(surface._lyric_motion.last_source_position, 60)
