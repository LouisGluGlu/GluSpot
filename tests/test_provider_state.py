from __future__ import annotations

import asyncio
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace

import api.providers as providers_module
from api.models import TimedLyricLine
from api.providers import EmptyIslandDataProvider, NO_SESSION_VISUAL_DATA, WindowsMediaProvider, empty_visual_data
from api.netease_position import NeteasePositionSample, QueueTrack


class FakeMediaProperties:
    def __init__(self, title: str, artist: str = ""):
        self.title = title
        self.artist = artist


class FakeTimeSpan:
    def __init__(self, seconds: float = 0.0):
        self._seconds = float(seconds)

    def total_seconds(self):
        return self._seconds


class FakeTimeline:
    def __init__(self, position: float = 0.0, duration: float = 0.0):
        self.position = FakeTimeSpan(position)
        self.start_time = FakeTimeSpan(0.0)
        self.end_time = FakeTimeSpan(duration)


class FakeSession:
    def __init__(self, app_id: str, title: str = "", artist: str = "", playing: bool = False, position: float = 0.0, duration: float = 0.0, status: int | None = None):
        self.source_app_user_model_id = app_id
        self.properties = FakeMediaProperties(title, artist)
        self.playback = SimpleNamespace(playback_status=(status if status is not None else (4 if playing else 3)))
        self.timeline = FakeTimeline(position, duration)
        self.seek_requests = []
        self.commands = []
        self.seek_result = True

    async def try_get_media_properties_async(self):
        return self.properties

    def get_playback_info(self):
        return self.playback

    def get_timeline_properties(self):
        return self.timeline

    async def try_change_playback_position_async(self, requested):
        self.seek_requests.append(int(requested))
        return self.seek_result

    async def try_skip_previous_async(self):
        self.commands.append("previous")
        return True

    async def try_skip_next_async(self):
        self.commands.append("next")
        return True

    async def try_toggle_play_pause_async(self):
        self.commands.append("toggle_play_pause")
        return True


class FakeManager:
    def __init__(self, sessions, current_error: Exception | None = None):
        self._sessions = list(sessions)
        self._current_error = current_error

    def get_current_session(self):
        if self._current_error is not None:
            raise self._current_error
        return self._sessions[0] if self._sessions else None

    def get_sessions(self):
        return list(self._sessions)


class FakeLyricsStore:
    def resolve_cached_or_local(self, *_args):
        return SimpleNamespace(timed_lyrics=(), lyrics=(), source="")

    def is_lookup_pending(self, *_args):
        return False

    def resolve_in_background(self, *_args):
        return False

    def enrich_in_background(self, *_args):
        return False


class TrackingLyricsStore(FakeLyricsStore):
    def __init__(self):
        self.local_calls = []
        self.pending_calls = []
        self.background_calls = []

    def resolve_cached_or_local(self, title, artist, album_title="", duration_seconds=0.0):
        self.local_calls.append((title, artist, album_title, duration_seconds))
        return SimpleNamespace(timed_lyrics=(), lyrics=(), source="")

    def is_lookup_pending(self, title, artist, album_title="", duration_seconds=0.0):
        self.pending_calls.append((title, artist, album_title, duration_seconds))
        return False

    def resolve_in_background(self, title, artist, album_title="", duration_seconds=0.0):
        self.background_calls.append((title, artist, album_title, duration_seconds))
        return True


class FakePositionReader:
    source_prefix = "netease"

    def __init__(self):
        self.immediate_calls = []
        self.read_calls = []

    def duration_for_track(self, _title, _artist):
        return 0.0

    def read_immediate(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.immediate_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos))
        return NeteasePositionSample(42.0, 180.0, "netease-uia-progress", 0.92, sampled_at=time.monotonic())

    def read(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.read_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos))
        return None


class FakeTimedLyricsStore(FakeLyricsStore):
    def resolve_cached_or_local(self, *_args):
        lines = (
            TimedLyricLine(96.0, "before"),
            TimedLyricLine(160.0, "line"),
            TimedLyricLine(166.0, "after"),
        )
        return SimpleNamespace(timed_lyrics=lines, lyrics=tuple(line.text for line in lines), source="test")


class FakeClickPositionReader(FakePositionReader):
    def read_immediate(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.immediate_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos))
        if allow_lyric_anchor:
            return NeteasePositionSample(160.0, 240.0, "netease-uia-lyrics", 0.86, 1, "line", time.monotonic())
        return NeteasePositionSample(144.0, 240.0, "netease-uia-progress", 0.92, sampled_at=time.monotonic())


class FakeStaleProgressReader(FakePositionReader):
    def read_immediate(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.immediate_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos))
        return NeteasePositionSample(153.0, 240.0, "netease-uia-progress", 0.92, sampled_at=time.monotonic())


class FakeQueuePositionReader(FakePositionReader):
    def __init__(self):
        super().__init__()
        self.adjacent_calls = []

    def adjacent_track(self, title, artist, direction):
        self.adjacent_calls.append((title, artist, direction))
        return SimpleNamespace(
            title="Next Song" if direction == "next" else "Previous Song",
            artist="Next Artist" if direction == "next" else "Previous Artist",
            album_title="Next Album" if direction == "next" else "Previous Album",
            duration_seconds=201.0 if direction == "next" else 199.0,
        )


class FakeTwoStepQueuePositionReader(FakePositionReader):
    def __init__(self):
        super().__init__()
        self.adjacent_calls = []

    def adjacent_track(self, title, artist, direction):
        self.adjacent_calls.append((title, artist, direction))
        if direction != "next":
            return None
        if title == "Song":
            return SimpleNamespace(title="Next Song", artist="Next Artist", album_title="Next Album", duration_seconds=201.0)
        if title == "Next Song":
            return SimpleNamespace(title="Third Song", artist="Third Artist", album_title="Third Album", duration_seconds=199.0)
        return None


class FakeAlbumQueuePositionReader(FakePositionReader):
    def __init__(self):
        super().__init__()
        self.upcoming_calls = []

    def upcoming_tracks(self, title, artist, count):
        self.upcoming_calls.append((title, artist, count))
        return (
            QueueTrack(
                title="Next Song",
                artist="Next Artist",
                album_title="Next Album",
                duration_seconds=201.0,
                album_art_url="https://example.test/next.jpg",
            ),
        )


class FakeForceImagePositionReader(FakePositionReader):
    def read(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.read_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos, force_progress_image))
        if force_progress_image:
            return NeteasePositionSample(66.0, 240.0, "netease-uia-progress", 0.74, sampled_at=time.monotonic())
        return None


class FakeWindowTrackPositionReader(FakePositionReader):
    def __init__(self):
        super().__init__()
        self.current_track_calls = 0

    def current_track(self):
        self.current_track_calls += 1
        return SimpleNamespace(
            title="New Song",
            artist="New Artist",
            album_title="New Album",
            duration_seconds=201.0,
        )


class FakeProgressPointPositionReader(FakePositionReader):
    def __init__(self):
        super().__init__()
        self.point_calls = []

    def position_from_progress_point(self, point, duration_seconds):
        self.point_calls.append((point, duration_seconds))
        return 170.0

    def read_immediate(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.immediate_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos))
        return NeteasePositionSample(58.0, 240.0, "netease-uia-progress", 0.74, sampled_at=time.monotonic())


class FakeLyricGeometryPositionReader(FakePositionReader):
    def __init__(self):
        super().__init__()
        self.geometry_calls = []

    def position_from_lyric_click_point(self, point, timed_lyrics, duration_seconds):
        self.geometry_calls.append((point, tuple(timed_lyrics), duration_seconds))
        return NeteasePositionSample(160.0, 240.0, "netease-uia-lyrics", 0.62, 1, "line", time.monotonic())

    def read_immediate(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.immediate_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos))
        raise AssertionError("lyric click should use local geometry before slow immediate read")


class AppearingLyricsStore(FakeLyricsStore):
    def __init__(self):
        self.calls = 0
        self.background_calls = []

    def resolve_cached_or_local(self, *_args):
        self.calls += 1
        if self.calls < 2:
            return SimpleNamespace(timed_lyrics=(), lyrics=(), source="")
        lines = (
            TimedLyricLine(40.0, "current"),
            TimedLyricLine(48.0, "next"),
        )
        return SimpleNamespace(timed_lyrics=lines, lyrics=tuple(line.text for line in lines), source="netease")

    def is_lookup_pending(self, *_args):
        return self.calls < 2

    def resolve_in_background(self, title, artist, album_title="", duration_seconds=0.0):
        self.background_calls.append((title, artist, album_title, duration_seconds))
        return True


class FakeUser32:
    def __init__(self, state: int, pos: tuple[int, int] = (0, 0)):
        self.state = int(state)
        self.pos = pos

    def GetAsyncKeyState(self, _key):
        return self.state

    def GetCursorPos(self, point):
        point._obj.x = int(self.pos[0])
        point._obj.y = int(self.pos[1])
        return True


class FakeSequenceUser32:
    def __init__(self, states, positions):
        self.states = list(states)
        self.positions = list(positions)
        self.index = 0

    def GetAsyncKeyState(self, _key):
        index = min(self.index, len(self.states) - 1)
        return int(self.states[index])

    def GetCursorPos(self, point):
        index = min(self.index, len(self.positions) - 1)
        pos = self.positions[index]
        point._obj.x = int(pos[0])
        point._obj.y = int(pos[1])
        self.index += 1
        return True


class ProviderStateTests(unittest.TestCase):
    def test_empty_visual_data_has_stable_empty_track(self):
        first = empty_visual_data("暂无媒体", "播放一首歌后会自动同步", "windows-media")
        second = empty_visual_data("暂无媒体", "播放一首歌后会自动同步", "windows-media")

        self.assertEqual(first.track.track_id, second.track.track_id)
        self.assertEqual(first.track.source_app, "windows-media")
        self.assertEqual(first.messages, ())
        self.assertEqual(first.plugins, ())

    def test_empty_provider_returns_configured_state(self):
        provider = EmptyIslandDataProvider(NO_SESSION_VISUAL_DATA)

        data = provider.get_visual_data()

        self.assertEqual(data.track.title, "暂无媒体")
        self.assertEqual(data.track.source_app, "windows-media")
        self.assertIsNone(provider.get_audio_frame())
        self.assertFalse(provider.send_playback_command("next"))
        self.assertFalse(provider.seek_playback_position(12.0))

    def test_background_zero_position_is_distrusted_but_interactive_is_not(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)

        self.assertTrue(provider._should_distrust_background_position(False, True, 0.0, 12.0))
        self.assertFalse(provider._should_distrust_background_position(True, True, 0.0, 12.0))

    def test_interactive_zero_raw_position_holds_previous_when_untrusted(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)

        position, source, confidence = provider._interactive_position_from_raw(0.0, 42.0, 0.0)

        self.assertEqual(position, 42.0)
        self.assertEqual(source, "interactive-hold")
        self.assertGreaterEqual(confidence, 0.64)

    def test_interactive_zero_raw_position_can_seek_to_start(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)

        position, source, confidence = provider._interactive_position_from_raw(0.0, 42.0, 41.6)

        self.assertEqual(position, 0.0)
        self.assertEqual(source, "interactive")
        self.assertGreaterEqual(confidence, 0.82)

    def test_local_position_projection_advances_cached_data(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        data = empty_visual_data("Song", "Artist", "cloudmusic.exe")
        track = replace(
            data.track,
            is_playing=True,
            position_seconds=10.0,
            duration_seconds=60.0,
            position_source="local-background",
        )
        data = replace(data, track=track)

        projected = provider._project_visual_position(data, now=105.0, anchor_at=100.0)

        self.assertAlmostEqual(projected.track.position_seconds, 15.0)

    def test_windows_media_position_projection_advances_cached_data(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        data = empty_visual_data("Song", "Artist", "cloudmusic.exe")
        track = replace(
            data.track,
            is_playing=True,
            position_seconds=10.0,
            duration_seconds=60.0,
            position_source="windows-media",
        )
        data = replace(data, track=track)

        projected = provider._project_visual_position(data, now=105.0, anchor_at=100.0)

        self.assertAlmostEqual(projected.track.position_seconds, 15.0)

    def test_interactive_hold_position_projection_advances_cached_data(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        data = empty_visual_data("Song", "Artist", "cloudmusic.exe")
        track = replace(
            data.track,
            is_playing=True,
            position_seconds=10.0,
            duration_seconds=60.0,
            position_source="interactive-hold",
        )
        data = replace(data, track=track)

        projected = provider._project_visual_position(data, now=105.0, anchor_at=100.0)

        self.assertAlmostEqual(projected.track.position_seconds, 15.0)

    def test_click_lyric_anchor_projection_advances_cached_data(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        data = empty_visual_data("Song", "Artist", "cloudmusic.exe")
        track = replace(
            data.track,
            is_playing=True,
            position_seconds=160.0,
            duration_seconds=240.0,
            position_source="netease-uia-lyrics",
        )
        data = replace(data, track=track)

        projected = provider._project_visual_position(data, now=105.0, anchor_at=100.0)

        self.assertAlmostEqual(projected.track.position_seconds, 165.0)

    def test_small_player_progress_backwards_jitter_keeps_projected_position(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        data = empty_visual_data("Song", "Artist", "cloudmusic.exe")
        provider._last_ready_at = 100.0
        provider._last_data = replace(
            data,
            track=replace(
                data.track,
                track_id="same-track",
                is_playing=True,
                position_seconds=34.0,
                duration_seconds=180.0,
                position_source="netease-uia-progress",
            ),
        )
        incoming = replace(
            data,
            track=replace(
                data.track,
                track_id="same-track",
                is_playing=True,
                position_seconds=33.75,
                duration_seconds=180.0,
                position_source="netease-uia-progress",
            ),
        )

        stabilized = provider._stabilize_incoming_position(incoming, now=100.4)

        self.assertAlmostEqual(stabilized.track.position_seconds, 34.4, delta=0.02)

    def test_large_player_progress_backwards_jump_is_preserved(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        data = empty_visual_data("Song", "Artist", "cloudmusic.exe")
        provider._last_ready_at = 100.0
        provider._last_data = replace(
            data,
            track=replace(
                data.track,
                track_id="same-track",
                is_playing=True,
                position_seconds=120.0,
                duration_seconds=180.0,
                position_source="netease-uia-progress",
            ),
        )
        incoming = replace(
            data,
            track=replace(
                data.track,
                track_id="same-track",
                is_playing=True,
                position_seconds=35.0,
                duration_seconds=180.0,
                position_source="netease-uia-progress",
            ),
        )

        stabilized = provider._stabilize_incoming_position(incoming, now=100.4)

        self.assertEqual(stabilized.track.position_seconds, 35.0)

    def test_select_media_session_prefers_playing_session_with_title(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._media_session_count = 0
        provider._media_session_candidates = ()
        provider._media_session_error = ""
        provider._selected_media_session_index = -1
        current = FakeSession("Shell", "", "", False)
        playing = FakeSession("cloudmusic.exe", "Song", "Artist", True, 15.0, 200.0)

        selected = asyncio.run(provider._select_media_session(current, [current, playing]))

        self.assertIs(selected, playing)
        self.assertEqual(provider._media_session_count, 2)
        self.assertEqual(provider._selected_media_session_index, 1)

    def test_get_session_falls_back_to_session_list_when_current_session_fails(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._provider_status = "starting"
        provider._media_session_count = 0
        provider._media_session_candidates = ()
        provider._media_session_error = ""
        provider._selected_media_session_index = -1
        provider._manager = FakeManager(
            [FakeSession("cloudmusic.exe", "Song", "Artist", True, 15.0, 200.0)],
            current_error=RuntimeError("current unavailable"),
        )

        original_loader = providers_module._ensure_winsdk_loaded
        providers_module._ensure_winsdk_loaded = lambda: True
        try:
            selected = asyncio.run(provider._get_session())
        finally:
            providers_module._ensure_winsdk_loaded = original_loader

        self.assertIsNotNone(selected)
        self.assertEqual(provider._media_session_count, 1)
        self.assertIn("current-session-failed", provider._media_session_error)
        self.assertIn("cloudmusic.exe", " ".join(provider._media_session_candidates))

    def test_music_player_adapter_default_uses_selected_netease_core_adapter(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": False,
            "v2_music_player_mode": "auto",
        }.get(key, default)

        self.assertEqual(provider._music_player_adapter_for("网易云音乐", "cloudmusic.exe"), "netease")
        self.assertEqual(provider._music_player_adapter_for("汽水音乐", "qishui.exe"), "system")
        self.assertEqual(provider._music_player_mode(), "netease")

    def test_core_adapter_uses_selected_netease_when_progress_plugin_disabled(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": False,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "netease",
        }.get(key, default)

        self.assertEqual(provider._music_player_adapter_for("网易云音乐", "cloudmusic.exe"), "netease")

    def test_betterncm_mode_matches_netease_source_and_uses_netease_reader_until_bridge_exists(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakePositionReader()
        provider.netease_position = reader
        provider.qq_position = None
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "netease_betterncm",
        }.get(key, default)

        self.assertEqual(provider._music_player_mode(), "netease_betterncm")
        self.assertEqual(provider._music_player_adapter_for("网易云音乐", "cloudmusic.exe"), "netease_betterncm")
        self.assertIs(provider._player_position_reader_for("netease_betterncm"), reader)

    def test_music_player_adapter_requires_selected_netease_source(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "qishui",
        }.get(key, default)

        self.assertEqual(provider._music_player_adapter_for("网易云音乐", "cloudmusic.exe"), "system")
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "netease",
        }.get(key, default)
        self.assertEqual(provider._music_player_adapter_for("网易云音乐", "cloudmusic.exe"), "netease")
        self.assertEqual(provider._music_player_adapter_for("汽水音乐", "qishui.exe"), "system")

    def test_music_player_adapter_auto_detects_qishui_source(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
        }.get(key, default)

        self.assertEqual(provider._music_player_adapter_for("汽水音乐", "qishui.exe"), "qishui")
        self.assertEqual(provider._music_player_adapter_for("网易云音乐", "cloudmusic.exe"), "netease")

    def test_music_player_adapter_auto_detects_qq_source(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.netease_position = SimpleNamespace(source_prefix="netease")
        provider.qq_position = SimpleNamespace(source_prefix="qq")
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
        }.get(key, default)

        self.assertEqual(provider._music_player_adapter_for("QQ音乐", "QQMusic.exe"), "qq")
        self.assertEqual(provider._player_position_reader_for("qq").source_prefix, "qq")
        self.assertEqual(provider._player_position_reader_for("netease").source_prefix, "netease")

    def test_current_titled_session_beats_stale_timeline_session(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._media_session_count = 0
        provider._media_session_candidates = ()
        provider._media_session_error = ""
        provider._selected_media_session_index = -1
        current = FakeSession("cloudmusic.exe", "Faded", "Alan Walker", False, 0.0, 0.0, status=5)
        stale = FakeSession("QQMusic.exe", "Wild", "Monogem", False, 4.5, 216.0, status=5)

        selected = asyncio.run(provider._select_media_session(current, [stale, current]))

        self.assertIs(selected, current)

    def test_netease_lyric_anchor_is_ignored_for_position(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)

        self.assertFalse(provider._should_use_netease_sample("netease-uia-lyrics", 0.1, False))
        self.assertFalse(provider._should_use_netease_sample("netease-uia-lyrics", 0.1, True))
        self.assertTrue(provider._should_use_netease_sample("netease-uia-progress", 1.0, False))
        self.assertFalse(provider._should_use_netease_sample("netease-uia-lyric-time", 0.5, True))
        self.assertFalse(provider._should_use_netease_sample("netease-uia-lyric-time", 1.2, True))
        self.assertFalse(provider._should_use_netease_sample("netease-uia-progress", 2.0, False))

    def test_player_progress_sample_can_supply_unknown_duration(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        sample = SimpleNamespace(
            position_seconds=72.0,
            duration_seconds=210.0,
            source="netease-uia-progress",
            confidence=0.92,
            sampled_at=100.0,
        )

        self.assertTrue(provider._should_use_player_sample("netease", sample.source, 0.2, True))
        self.assertEqual(sample.duration_seconds, 210.0)

    def test_interactive_lyric_time_sample_does_not_apply_click_jump(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)

        self.assertFalse(
            provider._should_apply_player_sample(
                "netease",
                "netease-uia-lyric-time",
                0.2,
                True,
                True,
                159.4,
                160.0,
                True,
            )
        )

    def test_interactive_visible_lyric_sample_can_apply_click_jump(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)

        self.assertTrue(
            provider._should_apply_player_sample(
                "netease",
                "netease-uia-lyrics",
                0.2,
                True,
                True,
                96.0,
                160.0,
                True,
            )
        )

    def test_visible_lyric_sample_does_not_apply_in_background(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)

        self.assertFalse(
            provider._should_apply_player_sample(
                "netease",
                "netease-uia-lyrics",
                0.2,
                True,
                False,
                96.0,
                160.0,
                True,
            )
        )

    def test_interactive_progress_sample_can_apply_small_click_jump(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)

        self.assertTrue(
            provider._should_apply_player_sample(
                "netease",
                "netease-uia-progress",
                0.2,
                True,
                True,
                159.4,
                160.0,
                True,
            )
        )

    def test_interactive_progress_sample_rejects_stale_backward_anchor_without_pointer(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)

        self.assertFalse(
            provider._should_apply_player_sample(
                "netease",
                "netease-uia-progress",
                0.2,
                True,
                True,
                247.4,
                245.6,
                True,
            )
        )

    def test_recent_pointer_progress_sample_can_apply_backward_seek(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)

        self.assertTrue(
            provider._should_apply_player_sample(
                "netease",
                "netease-uia-progress",
                0.2,
                True,
                True,
                247.4,
                145.6,
                True,
                user_initiated=True,
            )
        )

    def test_recent_lyric_click_forces_immediate_player_anchor_read(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeClickPositionReader()
        session = FakeSession("cloudmusic.exe", "Song", "Artist", True, 96.0, 240.0)
        base = empty_visual_data()
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = FakeTimedLyricsStore()
        provider._last_data = replace(
            base,
            track=replace(
                base.track,
                title="Song",
                artist="Artist",
                track_id="same-track",
                is_playing=True,
                duration_seconds=240.0,
                position_seconds=96.0,
                position_source="local-estimate",
                position_confidence=0.58,
            ),
        )
        provider._last_ready_at = time.monotonic()
        provider._source_app_foreground_at = provider._last_ready_at
        provider._source_app_recent_click_at = provider._last_ready_at
        provider._source_app_recent_click_pos = (420, 260)
        provider._source_app_recent_pointer_at = provider._last_ready_at
        provider._last_raw_timeline_track_id = "same-track"
        provider._last_raw_timeline_position = 96.0
        provider._last_raw_timeline_at = provider._last_ready_at
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider.netease_position = reader
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: True
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return session

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(len(reader.immediate_calls), 1)
        self.assertEqual(reader.read_calls, [])
        self.assertTrue(reader.immediate_calls[0][5])
        self.assertEqual(reader.immediate_calls[0][6], (420, 260))
        self.assertEqual(data.track.position_source, "netease-uia-lyrics")
        self.assertAlmostEqual(data.track.position_seconds, 160.0, delta=0.25)
        self.assertEqual(session.seek_requests, [1_600_000_000])
        self.assertAlmostEqual(provider._local_seek_position_seconds, 160.0, delta=0.25)
        self.assertEqual(provider._local_seek_track_id, "same-track")

    def test_cached_visual_data_samples_global_pointer_click(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        base = empty_visual_data("Song", "Artist", "cloudmusic.exe")
        provider._loop = object()
        provider.fallback = EmptyIslandDataProvider(base)
        provider._last_data = replace(
            base,
            track=replace(
                base.track,
                title="Song",
                artist="Artist",
                source_app="cloudmusic.exe",
                track_id="same-track",
                is_playing=True,
                position_seconds=96.0,
                duration_seconds=240.0,
                position_source="interactive-hold",
            ),
        )
        now = time.monotonic()
        provider._last_ready_at = now
        provider._last_refresh_at = now
        provider._refresh_future = None
        provider._music_player_adapter = "netease"
        provider._source_app_foreground = True
        provider._source_app_interactive = True
        provider._source_app_foreground_at = now
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())

        original_windll = providers_module.ctypes.windll
        providers_module.ctypes.windll = SimpleNamespace(
            user32=FakeSequenceUser32((0x8000, 0x0000), ((420, 260), (420, 260)))
        )
        try:
            provider.get_visual_data()
            provider.get_visual_data()
        finally:
            providers_module.ctypes.windll = original_windll

        self.assertEqual(getattr(provider, "_global_recent_click_pos", None), (420, 260))
        self.assertGreater(float(getattr(provider, "_global_recent_click_at", 0.0) or 0.0), 0.0)

    def test_global_lyric_click_forces_immediate_player_anchor_read_when_source_poll_misses_it(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeClickPositionReader()
        base = empty_visual_data()
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = FakeTimedLyricsStore()
        provider._last_data = replace(
            base,
            track=replace(
                base.track,
                title="Song",
                artist="Artist",
                source_app="cloudmusic.exe",
                track_id="same-track",
                is_playing=True,
                duration_seconds=240.0,
                position_seconds=96.0,
            ),
        )
        provider._last_ready_at = time.monotonic()
        provider._source_app_foreground_at = provider._last_ready_at
        provider._source_app_recent_click_at = 0.0
        provider._source_app_recent_click_pos = None
        provider._source_app_recent_pointer_at = 0.0
        provider._global_recent_click_at = provider._last_ready_at
        provider._global_recent_click_pos = (420, 260)
        provider._global_recent_pointer_at = provider._last_ready_at
        provider._global_recent_pointer_pos = (420, 260)
        provider._last_raw_timeline_track_id = "same-track"
        provider._last_raw_timeline_position = 96.0
        provider._last_raw_timeline_at = provider._last_ready_at
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider.netease_position = reader
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: True
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 96.0, 240.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(len(reader.immediate_calls), 1)
        self.assertEqual(reader.read_calls, [])
        self.assertTrue(reader.immediate_calls[0][5])
        self.assertEqual(reader.immediate_calls[0][6], (420, 260))
        self.assertEqual(data.track.position_source, "netease-uia-lyrics")
        self.assertAlmostEqual(data.track.position_seconds, 160.0, delta=0.25)

    def test_global_pointer_sampler_captures_click_between_media_refreshes(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        now = time.monotonic()
        provider._global_left_down = False
        provider._global_left_down_at = 0.0
        provider._global_left_down_pos = None
        provider._global_recent_click_at = 0.0
        provider._global_recent_click_pos = None
        provider._global_recent_pointer_at = 0.0
        provider._global_recent_pointer_pos = None

        original_windll = providers_module.ctypes.windll
        providers_module.ctypes.windll = SimpleNamespace(
            user32=FakeSequenceUser32((0x8000, 0x0000), ((420, 260), (420, 260)))
        )
        try:
            provider._sample_global_pointer_once(now)
            provider._sample_global_pointer_once(now + 0.04)
        finally:
            providers_module.ctypes.windll = original_windll

        self.assertTrue(provider._global_recent_click(now + 0.05))
        self.assertEqual(provider._global_recent_click_position(now + 0.05), (420, 260))

    def test_recent_progress_drag_forces_immediate_player_progress_read(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeClickPositionReader()
        base = empty_visual_data()
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = FakeLyricsStore()
        provider._last_data = replace(
            base,
            track=replace(
                base.track,
                title="Song",
                artist="Artist",
                track_id="same-track",
                is_playing=True,
                duration_seconds=240.0,
                position_seconds=96.0,
                position_source="local-estimate",
                position_confidence=0.58,
            ),
        )
        provider._last_ready_at = time.monotonic()
        provider._source_app_foreground_at = provider._last_ready_at
        provider._source_app_recent_click_at = 0.0
        provider._source_app_recent_pointer_at = provider._last_ready_at
        provider._last_raw_timeline_track_id = "same-track"
        provider._last_raw_timeline_position = 96.0
        provider._last_raw_timeline_at = provider._last_ready_at
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider.netease_position = reader
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: True
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 96.0, 240.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(len(reader.immediate_calls), 1)
        self.assertEqual(reader.read_calls, [])
        self.assertFalse(reader.immediate_calls[0][5])
        self.assertEqual(data.track.position_source, "netease-uia-progress")
        self.assertAlmostEqual(data.track.position_seconds, 144.0, delta=0.25)

    def test_recent_progress_drag_uses_core_netease_progress_when_plugin_disabled(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeClickPositionReader()
        base = empty_visual_data()
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = FakeLyricsStore()
        provider._last_data = replace(
            base,
            track=replace(
                base.track,
                title="Song",
                artist="Artist",
                track_id="same-track",
                is_playing=True,
                duration_seconds=240.0,
                position_seconds=96.0,
                position_source="local-estimate",
                position_confidence=0.58,
            ),
        )
        provider._last_ready_at = time.monotonic()
        provider._source_app_foreground_at = provider._last_ready_at
        provider._source_app_recent_click_at = 0.0
        provider._source_app_recent_pointer_at = provider._last_ready_at
        provider._last_raw_timeline_track_id = "same-track"
        provider._last_raw_timeline_position = 96.0
        provider._last_raw_timeline_at = provider._last_ready_at
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider.netease_position = reader
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": False,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: True
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 96.0, 240.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(len(reader.immediate_calls), 1)
        self.assertFalse(reader.immediate_calls[0][5])
        self.assertEqual(data.track.position_source, "netease-uia-progress")
        self.assertAlmostEqual(data.track.position_seconds, 144.0, delta=0.25)

    def test_background_netease_read_does_not_force_progress_image_without_user_action(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeForceImagePositionReader()
        base = empty_visual_data()
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = FakeLyricsStore()
        provider._last_data = replace(
            base,
            track=replace(
                base.track,
                title="Song",
                artist="Artist",
                track_id="same-track",
                is_playing=True,
                duration_seconds=240.0,
                position_seconds=34.0,
                position_source="local-background",
                position_confidence=0.46,
            ),
        )
        provider._last_ready_at = time.monotonic()
        provider._source_app_foreground_at = 0.0
        provider._source_app_recent_click_at = 0.0
        provider._source_app_recent_pointer_at = 0.0
        provider._last_raw_timeline_track_id = "same-track"
        provider._last_raw_timeline_position = 0.0
        provider._last_raw_timeline_at = provider._last_ready_at
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider.netease_position = reader
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": False,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: False
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 0.0, 240.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(len(reader.read_calls), 1)
        self.assertFalse(reader.read_calls[0][7])
        self.assertEqual(data.track.position_source, "local-background")
        self.assertGreaterEqual(data.track.position_seconds, 34.0)

    def test_background_netease_window_title_corrects_stale_windows_metadata(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeWindowTrackPositionReader()
        base = empty_visual_data()
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = FakeLyricsStore()
        provider._last_data = replace(
            base,
            track=replace(
                base.track,
                title="Old Song",
                artist="Old Artist",
                track_id="old-track",
                source_app="cloudmusic.exe",
                is_playing=True,
                duration_seconds=180.0,
                position_seconds=95.0,
                position_source="local-background",
            ),
        )
        provider._last_ready_at = time.monotonic()
        provider._source_app_foreground_at = 0.0
        provider._source_app_recent_click_at = 0.0
        provider._source_app_recent_pointer_at = 0.0
        provider._last_raw_timeline_track_id = "old-track"
        provider._last_raw_timeline_position = 95.0
        provider._last_raw_timeline_at = provider._last_ready_at
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider.netease_position = reader
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": False,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: False
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Old Song", "Old Artist", True, 95.0, 180.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(reader.current_track_calls, 1)
        self.assertEqual(data.track.title, "New Song")
        self.assertEqual(data.track.artist, "New Artist")
        self.assertEqual(data.track.duration_seconds, 201.0)
        self.assertNotEqual(data.track.track_id, "old-track")

    def test_recent_progress_drag_point_updates_local_seek_before_player_sample(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeProgressPointPositionReader()
        base = empty_visual_data()
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = FakeLyricsStore()
        provider._last_data = replace(
            base,
            track=replace(
                base.track,
                title="Song",
                artist="Artist",
                track_id="same-track",
                is_playing=True,
                duration_seconds=240.0,
                position_seconds=58.0,
                position_source="netease-uia-progress",
            ),
        )
        now = time.monotonic()
        provider._last_ready_at = now
        provider._source_app_foreground_at = now
        provider._source_app_recent_click_at = 0.0
        provider._source_app_recent_click_pos = None
        provider._source_app_recent_pointer_at = now
        provider._source_app_recent_pointer_pos = (802, 868)
        provider._last_raw_timeline_track_id = "same-track"
        provider._last_raw_timeline_position = 0.0
        provider._last_raw_timeline_at = now
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider.netease_position = reader
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": False,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: True
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 0.0, 240.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(reader.point_calls, [((802, 868), 240.0)])
        self.assertEqual(data.track.position_source, "local-seek")
        self.assertAlmostEqual(data.track.position_seconds, 170.0, delta=0.25)

    def test_cold_start_progress_drag_point_updates_local_seek_before_player_sample(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeProgressPointPositionReader()
        base = empty_visual_data()
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = FakeLyricsStore()
        provider._last_data = None
        now = time.monotonic()
        provider._last_ready_at = 0.0
        provider._source_app_foreground_at = now
        provider._source_app_recent_click_at = 0.0
        provider._source_app_recent_click_pos = None
        provider._source_app_recent_pointer_at = now
        provider._source_app_recent_pointer_pos = (520, 740)
        provider._global_recent_click_at = 0.0
        provider._global_recent_click_pos = None
        provider._global_recent_pointer_at = 0.0
        provider._global_recent_pointer_pos = None
        provider._global_pointer_lock = None
        provider._last_raw_timeline_track_id = ""
        provider._last_raw_timeline_position = None
        provider._last_raw_timeline_at = 0.0
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider.netease_position = reader
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": False,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: True
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 96.0, 240.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(reader.point_calls, [((520, 740), 240.0)])
        self.assertEqual(data.track.position_source, "local-seek")
        self.assertAlmostEqual(data.track.position_seconds, 170.0, delta=0.25)
        self.assertEqual(provider._local_seek_track_id, data.track.track_id)

    def test_current_read_prefetches_next_queue_track_lyrics(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeQueuePositionReader()
        lyrics_store = TrackingLyricsStore()
        base = empty_visual_data()
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = lyrics_store
        provider._last_data = None
        provider._last_ready_at = 0.0
        provider._source_app_foreground_at = 0.0
        provider._last_raw_timeline_track_id = ""
        provider._last_raw_timeline_position = None
        provider._last_raw_timeline_at = 0.0
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider.netease_position = reader
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": False,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: True
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 12.0, 240.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(reader.adjacent_calls, [("Song", "Artist", "next"), ("Next Song", "Next Artist", "next")])
        self.assertIn(("Next Song", "Next Artist", "Next Album", 201.0), lyrics_store.background_calls)

    def test_current_read_exposes_future_album_queue_art(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeAlbumQueuePositionReader()
        lyrics_store = TrackingLyricsStore()
        cover_bytes = b"cover-bytes"
        base = empty_visual_data()
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = lyrics_store
        provider._last_data = None
        provider._last_ready_at = 0.0
        provider._source_app_foreground_at = 0.0
        provider._last_raw_timeline_track_id = ""
        provider._last_raw_timeline_position = None
        provider._last_raw_timeline_at = 0.0
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider._queue_art_cache = {}
        provider.netease_position = reader
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": False,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
            "v2_album_island_cover_count": 5,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: True
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")
        provider._queue_album_art_bytes = lambda track: cover_bytes if track.album_art_url else b""

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 12.0, 240.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(reader.upcoming_calls, [("Song", "Artist", 2)])
        self.assertEqual(data.track.album_queue[0].title, "Next Song")
        self.assertEqual(data.track.album_queue[0].album_art_bytes, cover_bytes)

    def test_netease_current_read_prefetches_next_two_queue_track_lyrics(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeTwoStepQueuePositionReader()
        lyrics_store = TrackingLyricsStore()
        provider.lyrics_store = lyrics_store
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)

        result = provider._prefetch_netease_upcoming_lyrics("Song", "Artist", reader, count=2)

        self.assertTrue(result)
        self.assertEqual(
            reader.adjacent_calls,
            [("Song", "Artist", "next"), ("Next Song", "Next Artist", "next")],
        )
        self.assertEqual(
            lyrics_store.background_calls,
            [
                ("Next Song", "Next Artist", "Next Album", 201.0),
                ("Third Song", "Third Artist", "Third Album", 199.0),
            ],
        )

    def test_current_read_starts_lyric_lookup_before_thumbnail_read(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        events = []

        class OrderedLyricsStore(TrackingLyricsStore):
            def resolve_in_background(self, title, artist, album_title="", duration_seconds=0.0):
                events.append(("lyrics-background", title, artist))
                return super().resolve_in_background(title, artist, album_title, duration_seconds)

        lyrics_store = OrderedLyricsStore()
        base = empty_visual_data()
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = lyrics_store
        provider._last_data = None
        provider._last_ready_at = 0.0
        provider._source_app_foreground_at = 0.0
        provider._last_raw_timeline_track_id = ""
        provider._last_raw_timeline_position = None
        provider._last_raw_timeline_at = 0.0
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider.netease_position = FakePositionReader()
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": False,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: False

        async def fake_read_thumbnail(_properties, _track_key):
            events.append(("thumbnail", "", ""))
            return b""

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 12.0, 240.0)

        provider._read_thumbnail = fake_read_thumbnail
        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertLess(events.index(("lyrics-background", "Song", "Artist")), events.index(("thumbnail", "", "")))

    def test_command_prefetch_uses_reader_current_track_when_cached_track_is_stale(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeQueuePositionReader()
        reader.current_track = lambda: SimpleNamespace(
            title="Live Song",
            artist="Live Artist",
            album_title="Live Album",
            duration_seconds=200.0,
        )
        lyrics_store = TrackingLyricsStore()
        base = empty_visual_data()
        provider._last_data = replace(
            base,
            track=replace(base.track, title="Old Song", artist="Old Artist", track_id="old-track"),
        )
        provider._music_player_adapter = "netease"
        provider.netease_position = reader
        provider.qq_position = None
        provider.lyrics_store = lyrics_store

        provider._prefetch_adjacent_lyrics_for_command("next")

        self.assertEqual(reader.adjacent_calls, [("Live Song", "Live Artist", "next")])

    def test_successful_skip_command_requests_immediate_refresh_and_prefetch(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._loop = object()
        refresh_calls = []

        async def fake_send(_command):
            return True

        provider._send_command = fake_send
        provider._run = lambda coro: asyncio.run(coro)
        provider._request_immediate_media_refresh = lambda: refresh_calls.append("refresh")
        provider._prefetch_adjacent_lyrics_for_command = lambda command: refresh_calls.append(f"prefetch:{command}")

        self.assertTrue(provider.send_playback_command("next"))
        self.assertEqual(refresh_calls, ["refresh", "prefetch:next"])

    def test_immediate_refresh_can_replace_slow_pending_background_refresh(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        scheduled = []

        class PendingFuture:
            def done(self):
                return False

            def cancel(self):
                scheduled.append("cancel")
                return True

        provider._loop = object()
        provider._last_refresh_at = 123.0
        provider._refresh_future = PendingFuture()
        provider._read_visual_data = lambda: "read"
        original = providers_module.asyncio.run_coroutine_threadsafe
        providers_module.asyncio.run_coroutine_threadsafe = lambda coro, loop: scheduled.append((coro, loop)) or "new-future"
        try:
            provider._request_immediate_media_refresh()
        finally:
            providers_module.asyncio.run_coroutine_threadsafe = original

        self.assertEqual(provider._last_refresh_at, 0.0)
        self.assertEqual(scheduled, ["cancel", ("read", provider._loop)])
        self.assertEqual(provider._refresh_future, "new-future")

    def test_successful_seek_without_cached_track_keeps_pending_anchor_and_refreshes(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._loop = object()
        provider._last_data = None
        provider._last_ready_at = 0.0
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        refresh_calls = []

        async def fake_seek(_position):
            return True

        provider._seek_position = fake_seek
        provider._run = lambda coro: asyncio.run(coro)
        provider._request_immediate_media_refresh = lambda: refresh_calls.append("refresh")

        self.assertTrue(provider.seek_playback_position(123.0))
        self.assertAlmostEqual(provider._local_seek_position_seconds, 123.0, delta=0.01)
        self.assertGreater(provider._local_seek_at, 0.0)
        self.assertEqual(provider._local_seek_track_id, "")
        self.assertEqual(refresh_calls, ["refresh"])

    def test_first_read_applies_pending_cold_start_seek_to_current_track(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        base = empty_visual_data()
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = FakeLyricsStore()
        provider._last_data = None
        provider._last_ready_at = 0.0
        provider._source_app_foreground_at = 0.0
        provider._source_app_recent_click_at = 0.0
        provider._source_app_recent_click_pos = None
        provider._source_app_recent_pointer_at = 0.0
        provider._global_recent_click_at = 0.0
        provider._global_recent_click_pos = None
        provider._global_recent_pointer_at = 0.0
        provider._global_recent_pointer_pos = None
        provider._global_pointer_lock = None
        provider._last_raw_timeline_track_id = ""
        provider._last_raw_timeline_position = None
        provider._last_raw_timeline_at = 0.0
        provider._local_seek_position_seconds = 123.0
        provider._local_seek_at = time.monotonic()
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider.netease_position = None
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": False,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: False
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 30.0, 240.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(data.track.position_source, "local-seek")
        self.assertAlmostEqual(data.track.position_seconds, 123.0, delta=0.25)
        self.assertEqual(provider._local_seek_track_id, data.track.track_id)

    def test_consuming_first_refresh_applies_pending_cold_start_seek(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        base = empty_visual_data()
        track = replace(
            base.track,
            title="Song",
            artist="Artist",
            track_id="same-track",
            is_playing=True,
            duration_seconds=240.0,
            position_seconds=30.0,
            position_source="windows-media",
            position_confidence=1.0,
        )
        data = replace(base, track=track)

        class DoneFuture:
            def done(self):
                return True

            def result(self):
                return data

        provider._refresh_future = DoneFuture()
        provider._last_data = None
        provider._last_ready_at = 0.0
        provider._local_seek_position_seconds = 123.0
        provider._local_seek_at = time.monotonic()
        provider._local_seek_track_id = ""

        provider._consume_refresh_future(time.monotonic())

        self.assertIsNotNone(provider._last_data)
        self.assertEqual(provider._last_data.track.position_source, "local-seek")
        self.assertAlmostEqual(provider._last_data.track.position_seconds, 123.0, delta=0.25)
        self.assertEqual(provider._local_seek_track_id, "same-track")

    def test_recent_local_seek_rejects_stale_forward_progress_anchor(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeStaleProgressReader()
        base = empty_visual_data()
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = FakeTimedLyricsStore()
        provider._last_data = replace(
            base,
            track=replace(
                base.track,
                title="Song",
                artist="Artist",
                track_id="same-track",
                is_playing=True,
                duration_seconds=240.0,
                position_seconds=82.0,
                position_source="local-seek",
                position_confidence=0.86,
            ),
        )
        provider._last_ready_at = time.monotonic()
        provider._source_app_foreground_at = provider._last_ready_at
        provider._source_app_recent_click_at = 0.0
        provider._source_app_recent_click_pos = None
        provider._source_app_recent_pointer_at = provider._last_ready_at
        provider._last_raw_timeline_track_id = "same-track"
        provider._last_raw_timeline_position = 0.0
        provider._last_raw_timeline_at = provider._last_ready_at
        provider._local_seek_position_seconds = 82.0
        provider._local_seek_at = provider._last_ready_at
        provider._local_seek_track_id = "same-track"
        provider._art_cache = {}
        provider.netease_position = reader
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: True
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 0.0, 240.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(len(reader.immediate_calls), 1)
        self.assertEqual(data.track.position_source, "local-seek")
        self.assertLess(data.track.position_seconds, 84.0)

    def test_netease_newly_loaded_lyrics_force_one_sync_after_lookup(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakePositionReader()
        store = AppearingLyricsStore()
        base = empty_visual_data()
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = store
        provider._last_data = None
        provider._last_ready_at = 0.0
        provider._source_app_foreground_at = 0.0
        provider._source_app_recent_click_at = 0.0
        provider._source_app_recent_pointer_at = 0.0
        provider._last_raw_timeline_track_id = ""
        provider._last_raw_timeline_position = None
        provider._last_raw_timeline_at = 0.0
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider._netease_lyric_sync_track_ids = set()
        provider.netease_position = reader
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: False
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 0.0, 180.0)

        provider._get_session = fake_get_session

        first = asyncio.run(provider._read_visual_data())
        provider._last_data = first
        provider._last_ready_at = time.monotonic()
        second = asyncio.run(provider._read_visual_data())
        provider._last_data = second
        third = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNotNone(third)
        self.assertEqual(len(reader.immediate_calls), 2)
        self.assertEqual(reader.immediate_calls[0][0], "Song")
        self.assertEqual(reader.immediate_calls[1][0], "Song")
        self.assertEqual(second.track.position_source, "netease-uia-progress")
        self.assertEqual(third.track.position_source, "local-background")

    def test_netease_foreground_window_title_can_override_stale_metadata(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        base = empty_visual_data()
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = FakeLyricsStore()
        provider._last_data = None
        provider._last_ready_at = 0.0
        provider._source_app_foreground_at = 0.0
        provider._last_raw_timeline_track_id = ""
        provider._last_raw_timeline_position = None
        provider._last_raw_timeline_at = 0.0
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider.netease_position = FakePositionReader()
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: True
        provider._last_foreground_snapshot = {
            "title": "Tunnel Vision - Chace",
            "class_name": "Chrome_WidgetWin_1",
            "process_path": r"C:\\Program Files\\NetEase\\CloudMusic\\cloudmusic.exe",
            "process": "cloudmusic.exe",
        }
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "butterflies", "Denise Julia", True, 161.0, 212.79)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(data.track.title, "Tunnel Vision")
        self.assertEqual(data.track.artist, "Chace")
        self.assertEqual(data.track.track_id != "", True)

    def test_netease_track_change_forces_immediate_player_anchor_read(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakePositionReader()
        base = empty_visual_data()
        old_track = replace(
            base.track,
            title="Old Song",
            artist="Artist",
            track_id="old-track",
            source_app="cloudmusic.exe",
            is_playing=True,
            duration_seconds=180.0,
            position_seconds=88.0,
            position_source="windows-media",
            position_confidence=1.0,
        )
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = FakeLyricsStore()
        provider._last_data = replace(base, track=old_track)
        provider._last_ready_at = time.monotonic()
        provider._source_app_foreground_at = provider._last_ready_at
        provider._last_raw_timeline_track_id = "old-track"
        provider._last_raw_timeline_position = 88.0
        provider._last_raw_timeline_at = provider._last_ready_at
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider.netease_position = reader
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: True
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "New Song", "Artist", True, 88.0, 180.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(len(reader.immediate_calls), 1)
        self.assertEqual(reader.read_calls, [])
        self.assertEqual(data.track.title, "New Song")
        self.assertEqual(data.track.position_source, "netease-uia-progress")
        self.assertAlmostEqual(data.track.position_seconds, 42.0, delta=0.25)

    def test_recent_skip_forces_anchor_read_even_when_netease_is_not_foreground(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakePositionReader()
        base = empty_visual_data()
        old_track = replace(
            base.track,
            title="Old Song",
            artist="Artist",
            track_id="old-track",
            source_app="cloudmusic.exe",
            is_playing=True,
            duration_seconds=180.0,
            position_seconds=88.0,
            position_source="windows-media",
            position_confidence=1.0,
        )
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = FakeLyricsStore()
        provider._last_data = replace(base, track=old_track)
        provider._last_ready_at = time.monotonic()
        provider._recent_skip_command_at = provider._last_ready_at
        provider._source_app_foreground_at = 0.0
        provider._source_app_recent_click_at = 0.0
        provider._source_app_recent_click_pos = None
        provider._source_app_recent_pointer_at = 0.0
        provider._source_app_recent_pointer_pos = None
        provider._global_recent_click_at = 0.0
        provider._global_recent_click_pos = None
        provider._global_recent_pointer_at = 0.0
        provider._global_recent_pointer_pos = None
        provider._global_pointer_lock = None
        provider._last_raw_timeline_track_id = "old-track"
        provider._last_raw_timeline_position = 88.0
        provider._last_raw_timeline_at = provider._last_ready_at
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider.netease_position = reader
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": False,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: False
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "New Song", "Artist", True, 0.0, 180.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(len(reader.immediate_calls), 1)
        self.assertEqual(reader.read_calls, [])
        self.assertEqual(data.track.title, "New Song")
        self.assertEqual(data.track.position_source, "netease-uia-progress")
        self.assertAlmostEqual(data.track.position_seconds, 42.0, delta=0.25)
        self.assertEqual(provider._recent_skip_command_at, 0.0)

    def test_foreground_title_change_corrects_cached_track_immediately(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        base = empty_visual_data()
        cached = replace(
            base,
            track=replace(
                base.track,
                title="Old Song",
                artist="Old Artist",
                lyrics=("old lyric",),
                timed_lyrics=(TimedLyricLine(12.0, "old lyric"),),
                lyrics_source="old",
                source_app="cloudmusic.exe",
                track_id="old-track",
                is_playing=True,
                duration_seconds=233.0,
                position_seconds=77.0,
                position_source="windows-media",
                source_app_foreground=True,
            ),
        )
        provider._music_player_adapter = "netease"
        provider._raw_source_app_user_model_id = "cloudmusic.exe"
        provider._last_foreground_snapshot = {"title": "New Song - New Artist"}
        provider.netease_position = SimpleNamespace(reset=lambda: None)
        provider.qq_position = None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: True

        corrected = provider._foreground_correct_cached_data(cached, time.monotonic())

        self.assertEqual(corrected.track.title, "New Song")
        self.assertEqual(corrected.track.artist, "New Artist")
        self.assertEqual(corrected.track.lyrics, ())
        self.assertEqual(corrected.track.timed_lyrics, ())
        self.assertEqual(corrected.track.position_seconds, 0.0)
        self.assertEqual(corrected.track.position_source, "foreground-title")
        self.assertNotEqual(corrected.track.track_id, "old-track")

    def test_new_pointer_down_clears_stale_lyric_click_anchor(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        now = time.monotonic()
        provider._source_app_left_down = False
        provider._source_app_left_down_at = 0.0
        provider._source_app_left_down_pos = None
        provider._source_app_left_down_started_in_foreground = False
        provider._source_app_recent_click_at = now - 0.25
        provider._source_app_recent_click_pos = (420, 260)
        provider._source_app_recent_pointer_at = 0.0
        original_windll = providers_module.ctypes.windll
        providers_module.ctypes.windll = SimpleNamespace(user32=FakeUser32(0x8000, (820, 676)))
        try:
            provider._record_source_app_pointer_state(True, now)
        finally:
            providers_module.ctypes.windll = original_windll

        self.assertFalse(provider._source_app_recent_click(now))
        self.assertIsNone(provider._source_app_recent_click_position(now))
        self.assertTrue(provider._source_app_recent_pointer_activity(now))
        self.assertEqual(provider._source_app_left_down_pos, (820, 676))

    def test_recent_click_without_position_uses_progress_anchor(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeClickPositionReader()
        base = empty_visual_data()
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = FakeTimedLyricsStore()
        provider._last_data = replace(
            base,
            track=replace(
                base.track,
                title="Song",
                artist="Artist",
                track_id="same-track",
                is_playing=True,
                duration_seconds=240.0,
                position_seconds=96.0,
                position_source="local-estimate",
                position_confidence=0.58,
            ),
        )
        provider._last_ready_at = time.monotonic()
        provider._source_app_foreground_at = provider._last_ready_at
        provider._source_app_recent_click_at = provider._last_ready_at
        provider._source_app_recent_click_pos = None
        provider._source_app_recent_pointer_at = provider._last_ready_at
        provider._last_raw_timeline_track_id = "same-track"
        provider._last_raw_timeline_position = 96.0
        provider._last_raw_timeline_at = provider._last_ready_at
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider.netease_position = reader
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: True
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 96.0, 240.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(len(reader.immediate_calls), 1)
        self.assertFalse(reader.immediate_calls[0][5])
        self.assertIsNone(reader.immediate_calls[0][6])
        self.assertEqual(data.track.position_source, "netease-uia-progress")

    def test_netease_lyric_click_applies_local_seek_before_slow_reader(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeLyricGeometryPositionReader()
        session = FakeSession("cloudmusic.exe", "Song", "Artist", True, 96.0, 240.0)
        base = empty_visual_data()
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = FakeTimedLyricsStore()
        provider._last_data = replace(
            base,
            track=replace(
                base.track,
                title="Song",
                artist="Artist",
                track_id="same-track",
                is_playing=True,
                duration_seconds=240.0,
                position_seconds=96.0,
                position_source="local-estimate",
                position_confidence=0.58,
            ),
        )
        provider._last_ready_at = time.monotonic()
        provider._source_app_foreground_at = provider._last_ready_at
        provider._source_app_recent_click_at = provider._last_ready_at
        provider._source_app_recent_click_pos = (420, 260)
        provider._source_app_recent_pointer_at = provider._last_ready_at
        provider._last_raw_timeline_track_id = "same-track"
        provider._last_raw_timeline_position = 96.0
        provider._last_raw_timeline_at = provider._last_ready_at
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider.netease_position = reader
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: True
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return session

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(reader.geometry_calls[0][0], (420, 260))
        self.assertEqual(reader.immediate_calls, [])
        self.assertEqual(data.track.position_source, "local-seek")
        self.assertAlmostEqual(data.track.position_seconds, 160.0, delta=0.25)
        self.assertEqual(session.seek_requests, [1_600_000_000])

    def test_background_lyric_time_sample_does_not_take_over_position(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)

        self.assertFalse(
            provider._should_apply_player_sample(
                "netease",
                "netease-uia-lyric-time",
                0.2,
                True,
                False,
                159.4,
                160.0,
                True,
            )
        )

    def test_dual_line_enrichment_is_disabled_by_default(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: default

        self.assertFalse(provider._dual_line_lyrics_enabled())

    def test_first_read_uses_immediate_player_progress_anchor(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakePositionReader()
        provider.fallback = EmptyIslandDataProvider(empty_visual_data())
        provider.lyrics_store = FakeLyricsStore()
        provider._last_data = None
        provider._last_ready_at = 0.0
        provider._source_app_foreground_at = 0.0
        provider._last_raw_timeline_track_id = ""
        provider._last_raw_timeline_position = None
        provider._last_raw_timeline_at = 0.0
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider.netease_position = reader
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 0.0, 180.0)

        provider._get_session = fake_get_session
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: False

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(len(reader.immediate_calls), 1)
        self.assertEqual(reader.read_calls, [])
        self.assertAlmostEqual(data.track.position_seconds, 42.0, delta=0.25)
        self.assertEqual(data.track.position_source, "netease-uia-progress")

    def test_first_read_uses_core_netease_progress_when_plugin_disabled(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakePositionReader()
        provider.fallback = EmptyIslandDataProvider(empty_visual_data())
        provider.lyrics_store = FakeLyricsStore()
        provider._last_data = None
        provider._last_ready_at = 0.0
        provider._source_app_foreground_at = 0.0
        provider._last_raw_timeline_track_id = ""
        provider._last_raw_timeline_position = None
        provider._last_raw_timeline_at = 0.0
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider.netease_position = reader
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": False,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 0.0, 180.0)

        provider._get_session = fake_get_session
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: False

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(len(reader.immediate_calls), 1)
        self.assertEqual(reader.read_calls, [])
        self.assertAlmostEqual(data.track.position_seconds, 42.0, delta=0.25)
        self.assertEqual(data.track.position_source, "netease-uia-progress")

    def test_netease_syncs_once_when_delayed_lyrics_finish_loading_even_in_background(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakePositionReader()
        lyrics_store = AppearingLyricsStore()
        base = empty_visual_data()
        old_track = replace(
            base.track,
            title="Song",
            artist="Artist",
            source_app="cloudmusic.exe",
            track_id="same-track",
            is_playing=True,
            duration_seconds=240.0,
            position_seconds=38.0,
            position_source="local-estimate",
            timed_lyrics=(),
        )
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = lyrics_store
        provider._last_data = replace(base, track=old_track)
        provider._last_ready_at = time.monotonic()
        provider._source_app_foreground_at = 0.0
        provider._source_app_recent_click_at = 0.0
        provider._source_app_recent_click_pos = None
        provider._source_app_recent_pointer_at = 0.0
        provider._source_app_recent_pointer_pos = None
        provider._global_recent_click_at = 0.0
        provider._global_recent_click_pos = None
        provider._global_recent_pointer_at = 0.0
        provider._global_recent_pointer_pos = None
        provider._global_pointer_lock = None
        provider._last_raw_timeline_track_id = "same-track"
        provider._last_raw_timeline_position = 38.0
        provider._last_raw_timeline_at = provider._last_ready_at
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider._netease_lyric_sync_track_ids = set()
        provider._netease_upcoming_prefetch_keys = set()
        provider.netease_position = reader
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": False,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: False
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 0.0, 240.0)

        provider._get_session = fake_get_session

        first = asyncio.run(provider._read_visual_data())
        provider._last_data = first
        provider._last_ready_at = time.monotonic()
        second = asyncio.run(provider._read_visual_data())

        self.assertEqual(len(reader.immediate_calls), 1)
        self.assertEqual(second.track.position_source, "netease-uia-progress")
        self.assertAlmostEqual(second.track.position_seconds, 42.0, delta=0.25)


if __name__ == "__main__":
    unittest.main()
