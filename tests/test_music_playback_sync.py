from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from api.models import TimedLyricLine
from api.netease_position import NeteasePositionSample
from api.providers import EmptyIslandDataProvider, WindowsMediaProvider, empty_visual_data
from local.island_stack_visual import IslandStackVisual
from local.music_surface_window import MusicSurfaceWindow
from tests.test_provider_state import FakeSession, FakeTimedLyricsStore


class MusicPlaybackClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _owner(self, *, playing=True):
        data = empty_visual_data("Song", "Artist", "cloudmusic.exe")
        track = replace(
            data.track, track_id="song", is_playing=playing,
            position_seconds=30.0, duration_seconds=180.0,
            position_sampled_at=99.9,
            timed_lyrics=(TimedLyricLine(0.0, "First"), TimedLyricLine(40.0, "Second")),
        )
        with patch("local.island_stack_visual.time.monotonic", return_value=100.0):
            visual = IslandStackVisual(data_provider=EmptyIslandDataProvider(replace(data, track=track)))
        visual.timer.stop()
        self.addCleanup(visual.close)
        visual._run_background = lambda _name, _callback: None
        return visual, data, track

    def test_pause_and_resume_share_the_current_clock_in_all_music_views(self):
        visual, _data, _track = self._owner()
        with patch("local.island_stack_visual.time.monotonic", return_value=100.65):
            visual._send_playback_command_async("toggle_play_pause")
        self.assertFalse(visual.media_is_playing)
        self.assertAlmostEqual(visual._estimated_media_position(104.0), 30.65)
        with patch("local.island_stack_visual.time.monotonic", return_value=105.0):
            visual._send_playback_command_async("toggle_play_pause")
        self.assertTrue(visual.media_is_playing)
        self.assertAlmostEqual(visual._estimated_media_position(105.5), 31.15)

    def test_large_music_page_command_freezes_the_owner_lyric_clock_immediately(self):
        visual, _data, _track = self._owner()
        surface = MusicSurfaceWindow(visual)
        self.addCleanup(surface.shutdown)
        timed = [(0.0, "First"), (40.0, "Second")]
        surface._lyric_frame(timed, 100.6)
        with patch("local.music_surface_window.time.monotonic", return_value=100.65):
            surface._send_media_command("toggle_play_pause")
        self.assertFalse(visual.media_is_playing)
        frame = surface._lyric_frame(timed, 101.5)
        self.assertAlmostEqual(frame.reading_progress, 30.65 / 40.0, places=4)
        with patch("local.music_surface_window.time.monotonic", return_value=105.0):
            surface._send_media_command("toggle_play_pause")
        self.assertTrue(visual.media_is_playing)
        self.assertAlmostEqual(visual._estimated_media_position(105.5), 31.15)

    def test_inflight_pre_pause_frame_cannot_rewind_or_reopen_the_clock(self):
        visual, data, track = self._owner()
        with patch("local.island_stack_visual.time.monotonic", return_value=100.65):
            visual._send_playback_command_async("toggle_play_pause")
        with patch("local.island_stack_visual.time.monotonic", return_value=100.8):
            visual._apply_visual_data(replace(data, track=track))
        self.assertFalse(visual.media_is_playing)
        self.assertAlmostEqual(visual._estimated_media_position(101.0), 30.65)

    def test_pre_resume_paused_sample_does_not_restore_a_paused_clock(self):
        visual, data, track = self._owner(playing=False)
        with patch("local.island_stack_visual.time.monotonic", return_value=100.65):
            visual._send_playback_command_async("toggle_play_pause")
        with patch("local.island_stack_visual.time.monotonic", return_value=100.8):
            visual._apply_visual_data(replace(data, track=track))
        self.assertTrue(visual.media_is_playing)
        self.assertAlmostEqual(visual._estimated_media_position(101.0), 30.35)

    def test_matching_new_sample_confirms_pause_and_clears_pending_command(self):
        visual, data, track = self._owner()
        with patch("local.island_stack_visual.time.monotonic", return_value=100.65):
            visual._send_playback_command_async("toggle_play_pause")
        confirmed = replace(track, is_playing=False, position_seconds=30.7, position_sampled_at=100.7)
        with patch("local.island_stack_visual.time.monotonic", return_value=100.8):
            visual._apply_visual_data(replace(data, track=confirmed))
        self.assertFalse(visual.media_is_playing)
        self.assertIsNone(visual._pending_music_playback_toggle)
        self.assertAlmostEqual(visual._estimated_media_position(101.0), 30.7)

    def test_failed_pause_restores_the_source_without_waiting_for_timeout(self):
        visual, data, track = self._owner()
        visual.data_provider.send_playback_command = lambda _command: False
        visual._run_background = lambda _name, callback: callback()
        with patch("local.island_stack_visual.time.monotonic", return_value=100.65):
            visual._send_playback_command_async("toggle_play_pause")
        with patch("local.island_stack_visual.time.monotonic", return_value=100.8):
            visual._apply_visual_data(replace(data, track=replace(track, position_seconds=30.8, position_sampled_at=100.7)))
        self.assertTrue(visual.media_is_playing)
        self.assertIsNone(visual._pending_music_playback_toggle)

    def test_unconfirmed_command_expires_and_new_track_drops_its_generation(self):
        visual, _data, _track = self._owner()
        visual._begin_music_playback_toggle(100.65)
        self.assertEqual(visual._reconcile_music_playback_toggle("song", True, 32.6, 100.2, 102.6), (True, 32.6))
        self.assertIsNone(visual._pending_music_playback_toggle)
        visual._begin_music_playback_toggle(103.0)
        self.assertEqual(visual._reconcile_music_playback_toggle("next", True, 0.2, 103.1, 103.2, track_changed=True), (True, 0.2))
        self.assertIsNone(visual._pending_music_playback_toggle)

    def test_song_tracking_consumes_provider_frames_faster_for_only_ten_seconds(self):
        visual, _data, _track = self._owner()
        reads = []
        visual.media_refresh_interval = 0.65
        visual.last_media_refresh_at = 100.0
        visual.data_provider = SimpleNamespace(get_visual_data=lambda: reads.append(True))
        visual._apply_visual_data = lambda _data: False
        visual._refresh_media_data(100.1)
        self.assertEqual(reads, [])
        visual._refresh_media_data(100.13)
        self.assertEqual(reads, [True])
        visual.last_media_refresh_at = 110.0
        visual._refresh_media_data(110.13)
        self.assertEqual(reads, [True])


class ProviderPlaybackBoundaryTests(unittest.TestCase):
    def _provider(self, *, previous_playing: bool, source_playing: bool, sample_playing: bool):
        settings = {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
        }
        with patch.object(WindowsMediaProvider, "_start_loop", return_value=None):
            provider = WindowsMediaProvider(settings_getter=lambda key, default=None: settings.get(key, default))
        session = FakeSession("cloudmusic.exe", "Song", "Artist", source_playing, 30.75, 180.0)
        sample = NeteasePositionSample(
            30.0, 180.0, "netease-uia-progress", 0.92,
            sampled_at=99.8, is_playing=sample_playing,
        )
        provider.netease_position = SimpleNamespace(
            source_prefix="netease", read=lambda *_args, **_kwargs: sample,
            duration_for_track=lambda *_args: 180.0,
        )
        provider.lyrics_store = FakeTimedLyricsStore()
        provider._is_source_app_foreground = lambda *_args: False
        provider._schedule_current_netease_art_lookup = lambda *_args, **_kwargs: None
        provider._thumbnail_for_track = lambda *_args: asyncio.sleep(0, result=b"")
        provider._get_session = lambda: asyncio.sleep(0, result=session)
        base = empty_visual_data("Song", "Artist", "网易云音乐")
        provider._last_data = replace(base, track=replace(
            base.track, track_id="same-song", is_playing=previous_playing,
            position_seconds=30.0, duration_seconds=180.0,
        ))
        provider._last_ready_at = 99.9
        return provider, sample

    def test_cached_playing_uia_sample_cannot_override_new_paused_session(self):
        provider, _sample = self._provider(previous_playing=True, source_playing=False, sample_playing=True)
        with patch("api.providers.time.monotonic", return_value=100.0):
            data = asyncio.run(provider._read_visual_data())
        self.assertFalse(data.track.is_playing)
        self.assertAlmostEqual(data.track.position_seconds, 30.75)
        self.assertEqual(data.track.position_source, "windows-media")

    def test_cached_paused_uia_sample_cannot_anchor_new_resumed_session(self):
        provider, _sample = self._provider(previous_playing=False, source_playing=True, sample_playing=False)
        with patch("api.providers.time.monotonic", return_value=100.0):
            data = asyncio.run(provider._read_visual_data())
        self.assertTrue(data.track.is_playing)
        self.assertAlmostEqual(data.track.position_seconds, 30.75)

    def test_toggle_boundary_rejects_old_samples_even_before_session_reports_pause(self):
        provider, sample = self._provider(previous_playing=True, source_playing=True, sample_playing=True)
        provider._last_playback_toggle_at = 100.0
        provider._last_playback_toggle_track_id = "same-song"
        self.assertFalse(provider._player_sample_after_playback_boundary("same-song", sample))
        self.assertTrue(provider._player_sample_after_playback_boundary("same-song", replace(sample, sampled_at=100.1)))
        self.assertTrue(provider._player_sample_after_playback_boundary("different-song", sample))

    def test_provider_track_tracking_is_bounded_for_generic_windows_sessions(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._music_player_adapter = "system"
        provider._source_app_interactive = False
        provider._source_app_foreground_at = 0.0
        provider._refresh_interval = 0.55
        provider._media_strong_tracking_until = 110.0
        self.assertAlmostEqual(provider._current_refresh_interval(109.99), 0.12)
        self.assertAlmostEqual(provider._current_refresh_interval(110.0), 0.55)


if __name__ == "__main__":
    unittest.main()
