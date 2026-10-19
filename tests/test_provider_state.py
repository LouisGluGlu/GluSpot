from __future__ import annotations

import asyncio
import base64
from concurrent.futures import TimeoutError as FutureTimeoutError
import os
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import api.providers as providers_module
from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage
from api.audio_analyzer import LiveAudioAnalyzer
from api.lyrics import LocalLyricsStore, LyricsMatch
from api.models import TimedLyricLine, TimedLyricUnit
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

    def lyric_click_point_hits_visible_row(self, _point, _title="", *_args, **_kwargs):
        return True

    def read_immediate(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.immediate_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos, force_progress_image))
        return NeteasePositionSample(42.0, 180.0, "netease-uia-progress", 0.92, sampled_at=time.monotonic())

    def read(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.read_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos, force_progress_image))
        return None


class FakeQQPositionReader(FakePositionReader):
    source_prefix = "qq"

    def read_immediate(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.immediate_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos, force_progress_image))
        return NeteasePositionSample(42.0, 180.0, "qq-uia-progress", 0.92, sampled_at=time.monotonic())


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
        self.immediate_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos, force_progress_image))
        if allow_lyric_anchor:
            return NeteasePositionSample(160.0, 240.0, "netease-uia-lyrics", 0.86, 1, "line", time.monotonic())
        return NeteasePositionSample(144.0, 240.0, "netease-uia-progress", 0.92, sampled_at=time.monotonic())


class FakeStaleProgressReader(FakePositionReader):
    def read_immediate(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.immediate_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos))
        return NeteasePositionSample(153.0, 240.0, "netease-uia-progress", 0.92, sampled_at=time.monotonic())


class FakeLaggedProgressReader(FakePositionReader):
    def read(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.read_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos, force_progress_image))
        return NeteasePositionSample(96.0, 240.0, "netease-uia-progress", 0.92, sampled_at=time.monotonic())


class FakePausedLyricReader(FakePositionReader):
    def read(self, title, artist, timed_lyrics, duration_seconds, interactive=False, **_kwargs):
        self.read_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive))
        return NeteasePositionSample(
            160.0,
            240.0,
            "netease-uia-lyrics",
            0.86,
            1,
            "line",
            time.monotonic(),
            False,
        )


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


class FakeImmediateForceImagePositionReader(FakePositionReader):
    def read_immediate(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.immediate_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos, force_progress_image))
        if force_progress_image:
            return NeteasePositionSample(88.0, 240.0, "netease-uia-progress", 0.90, sampled_at=time.monotonic())
        return None

    def read(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.read_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos, force_progress_image))
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
        self.immediate_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos, force_progress_image))
        return NeteasePositionSample(58.0, 240.0, "netease-uia-progress", 0.74, sampled_at=time.monotonic())


class FakeProgressPointWithLyricHitRecorder(FakeProgressPointPositionReader):
    def __init__(self):
        super().__init__()
        self.hit_calls = []
        self.content_calls = []

    def lyric_click_point_hits_visible_row(self, point, title="", *_args, **_kwargs):
        self.hit_calls.append((point, title))
        return False

    def lyric_click_point_may_hit_player_content(self, point, title="", *_args, **_kwargs):
        self.content_calls.append((point, title))
        return False


class FakeProgressOnlyImmediateReader(FakeProgressPointPositionReader):
    def __init__(self):
        super().__init__()
        self.progress_immediate_calls = []

    def read_progress_immediate(self, title, artist, timed_lyrics, duration_seconds):
        self.progress_immediate_calls.append((title, artist, tuple(timed_lyrics), duration_seconds))
        return NeteasePositionSample(171.0, 240.0, "netease-uia-progress", 0.90, sampled_at=time.monotonic())

    def read_immediate(self, *_args, **_kwargs):
        raise AssertionError("progress anchors must use read_progress_immediate when available")


class FakeLightweightFollowReader(FakePositionReader):
    def __init__(self):
        super().__init__()
        self.hit_calls = []
        self.content_calls = []

    def lyric_click_point_hits_visible_row(self, point, title="", *_args, **_kwargs):
        self.hit_calls.append((point, title))
        return False

    def lyric_click_point_may_hit_player_content(self, point, title="", *_args, **_kwargs):
        self.content_calls.append((point, title))
        return False

    def read_immediate(self, *_args, **_kwargs):
        raise AssertionError("lightweight lyric follow must not use immediate sync reads")

    def read_progress_immediate(self, *_args, **_kwargs):
        raise AssertionError("lightweight lyric follow must not use screenshot progress reads")

    def read(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.read_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos, force_progress_image))
        return NeteasePositionSample(144.0, 240.0, "netease-uia-progress", 0.92, sampled_at=time.monotonic())


class FakeLyricGeometryPositionReader(FakePositionReader):
    def __init__(self):
        super().__init__()
        self.geometry_calls = []

    def position_from_lyric_click_point(self, point, timed_lyrics, duration_seconds):
        self.geometry_calls.append((point, tuple(timed_lyrics), duration_seconds))
        return NeteasePositionSample(160.0, 240.0, "netease-uia-lyrics", 0.62, 1, "line", time.monotonic())

    def read_immediate(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.immediate_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos, force_progress_image))
        return NeteasePositionSample(144.0, 240.0, "netease-uia-progress", 0.92, sampled_at=time.monotonic())


class FakeQQLyricGeometryPositionReader(FakeLyricGeometryPositionReader):
    source_prefix = "qq"

    def position_from_lyric_click_point(self, point, timed_lyrics, duration_seconds):
        self.geometry_calls.append((point, tuple(timed_lyrics), duration_seconds))
        return NeteasePositionSample(166.0, 240.0, "qq-uia-lyrics", 0.64, 2, "after", time.monotonic())

    def read_immediate(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.immediate_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos, force_progress_image))
        return NeteasePositionSample(145.0, 240.0, "qq-uia-progress", 0.92, sampled_at=time.monotonic())


class FakeLyricClickProgressReader(FakePositionReader):
    def __init__(self):
        super().__init__()
        self.geometry_calls = []

    def position_from_lyric_click_point(self, point, timed_lyrics, duration_seconds):
        self.geometry_calls.append((point, tuple(timed_lyrics), duration_seconds))
        raise AssertionError("lyric click should be treated as a progress anchor read")

    def read_immediate(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.immediate_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos, force_progress_image))
        return NeteasePositionSample(170.0, 240.0, "netease-uia-progress", 0.88, sampled_at=time.monotonic())


class FakeLyricClickMissReader(FakePositionReader):
    def __init__(self):
        super().__init__()
        self.hit_calls = []

    def lyric_click_point_hits_visible_row(self, point, title="", *_args, **_kwargs):
        self.hit_calls.append((point, title))
        return False

    def read_immediate(self, *_args, **_kwargs):
        raise AssertionError("non-lyric clicks must not force a progress anchor read")


class FakeLyricClickContentFallbackReader(FakePositionReader):
    def __init__(self):
        super().__init__()
        self.hit_calls = []
        self.content_calls = []

    def lyric_click_point_hits_visible_row(self, point, title="", *_args, **_kwargs):
        self.hit_calls.append((point, title))
        return False

    def lyric_click_point_may_hit_player_content(self, point, title="", *_args, **_kwargs):
        self.content_calls.append((point, title))
        return True

    def read_immediate(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.immediate_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos, force_progress_image))
        return NeteasePositionSample(150.0, 240.0, "netease-uia-progress", 0.88, sampled_at=time.monotonic())


class FakeLyricClickBurstReader(FakePositionReader):
    def __init__(self):
        super().__init__()
        self.positions = [144.0, 168.0]

    def read_immediate(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.immediate_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos, force_progress_image))
        value = self.positions[min(len(self.immediate_calls) - 1, len(self.positions) - 1)]
        return NeteasePositionSample(value, 240.0, "netease-uia-progress", 0.90, sampled_at=time.monotonic())


class FakeBusyProgressReader(FakePositionReader):
    def __init__(self):
        super().__init__()
        self._refreshing = True
        self._last_sample = NeteasePositionSample(80.0, 240.0, "netease-uia-progress", 0.88, sampled_at=time.monotonic())

    def read_immediate(self, title, artist, timed_lyrics, duration_seconds, interactive=False, allow_lyric_anchor=False, click_pos=None, force_progress_image=False):
        self.immediate_calls.append((title, artist, tuple(timed_lyrics), duration_seconds, interactive, allow_lyric_anchor, click_pos, force_progress_image))
        if self._refreshing and not (interactive or allow_lyric_anchor or force_progress_image):
            return self._last_sample
        self._last_sample = NeteasePositionSample(172.0, 240.0, "netease-uia-progress", 0.90, sampled_at=time.monotonic())
        return self._last_sample


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
    def test_ember_dual_line_setting_enables_provider_dual_line_fetch_for_ember_style(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_dual_line_lyrics": False,
            "v2_lyric_flow_style": "ember_fade",
            "v2_ember_dual_line_enabled": True,
        }.get(key, default)

        self.assertTrue(provider._dual_line_lyrics_enabled())

    def test_ember_dual_line_setting_does_not_enable_provider_for_other_styles(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_dual_line_lyrics": False,
            "v2_lyric_flow_style": "gelv_songscatter",
            "v2_ember_dual_line_enabled": True,
        }.get(key, default)

        self.assertFalse(provider._dual_line_lyrics_enabled())

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

    def test_live_audio_analyzer_treats_silent_blocks_as_no_live_audio(self):
        import numpy as np

        analyzer = LiveAudioAnalyzer.__new__(LiveAudioAnalyzer)
        analyzer._smooth = [0.22, 0.20, 0.14]
        silent_block = np.zeros((1024, 2), dtype=np.float32)

        frame = analyzer._analyze_block(silent_block, 44100, np)

        self.assertIsNone(frame)

    def test_live_audio_analyzer_stop_marks_capture_inactive(self):
        analyzer = LiveAudioAnalyzer.__new__(LiveAudioAnalyzer)
        analyzer._running = True
        analyzer._status = "capturing"
        analyzer._thread = None

        analyzer.stop()

        self.assertFalse(analyzer._running)
        self.assertEqual(analyzer._status, "stopped")

    def test_live_audio_analyzer_selects_output_loopback_by_endpoint_id(self):
        analyzer = LiveAudioAnalyzer.__new__(LiveAudioAnalyzer)
        analyzer._running = True
        analyzer._status = "starting"
        analyzer._last_error = ""
        analyzer._analyze_block = lambda *_args: None
        speaker = SimpleNamespace(id="render-endpoint-id", name="Shared Bluetooth name")
        selected_ids = []
        recorder_settings = []

        class FakeRecorder:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def record(self, *, numframes):
                analyzer._running = False
                return None

        class FakeLoopback:
            isloopback = True

            def recorder(self, *, samplerate, channels, blocksize, exclusive_mode):
                recorder_settings.append((samplerate, channels, blocksize, exclusive_mode))
                return FakeRecorder()

        loopback = FakeLoopback()

        def get_microphone(*, id, include_loopback):
            selected_ids.append(id)
            self.assertTrue(include_loopback)
            return loopback if id == speaker.id else SimpleNamespace(isloopback=False)

        with patch("soundcard.default_speaker", return_value=speaker), patch(
            "soundcard.get_microphone", side_effect=get_microphone
        ):
            analyzer._capture_loop()

        self.assertEqual(selected_ids, [speaker.id])
        self.assertEqual(recorder_settings, [(44100, 2, 4096, False)])
        self.assertEqual(analyzer._status, "capturing")
        self.assertEqual(analyzer._last_error, "")

    def test_live_audio_analyzer_never_opens_a_real_microphone(self):
        analyzer = LiveAudioAnalyzer.__new__(LiveAudioAnalyzer)
        analyzer._running = True
        analyzer._status = "starting"
        analyzer._last_error = ""
        microphone = SimpleNamespace(isloopback=False)
        microphone.recorder = lambda **_kwargs: self.fail("real microphone must not open")

        with patch("soundcard.default_speaker", return_value=SimpleNamespace(id="render-endpoint-id")), patch(
            "soundcard.get_microphone", return_value=microphone
        ):
            analyzer._capture_loop()

        self.assertFalse(analyzer._running)
        self.assertEqual(analyzer._status, "unavailable")
        self.assertIn("loopback endpoint", analyzer._last_error)

    def test_windows_provider_starts_with_optional_music_attachments_cold(self):
        calls = {"audio": 0, "netease": 0, "qq": 0}

        class CountingAudioAnalyzer:
            def __init__(self):
                calls["audio"] += 1

        class CountingNeteaseReader:
            def __init__(self):
                calls["netease"] += 1

        class CountingQQReader:
            def __init__(self):
                calls["qq"] += 1

        class QuietAuxProvider:
            def __init__(self, *_args, **_kwargs):
                pass

            def plugins(self):
                return ()

            def messages(self):
                return ()

            def diagnostics(self):
                return {}

        original_audio = providers_module.LiveAudioAnalyzer
        original_netease = providers_module.NeteasePositionReader
        original_qq = providers_module.QQPositionReader
        original_plugins = providers_module.RealPluginProvider
        original_messages = providers_module.WindowsMessageDetector
        original_start_loop = WindowsMediaProvider._start_loop
        providers_module.LiveAudioAnalyzer = CountingAudioAnalyzer
        providers_module.NeteasePositionReader = CountingNeteaseReader
        providers_module.QQPositionReader = CountingQQReader
        providers_module.RealPluginProvider = QuietAuxProvider
        providers_module.WindowsMessageDetector = QuietAuxProvider
        WindowsMediaProvider._start_loop = lambda self: None
        try:
            provider = WindowsMediaProvider(settings_getter=lambda key, default=None: {
                "v2_music_app_profiles": {
                    "netease": {"path": "", "sync_enabled": False},
                    "qq": {"path": "", "sync_enabled": False},
                },
                "v2_music_auto_detect": False,
                "v2_enable_live_audio_analysis": False,
            }.get(key, default))
        finally:
            providers_module.LiveAudioAnalyzer = original_audio
            providers_module.NeteasePositionReader = original_netease
            providers_module.QQPositionReader = original_qq
            providers_module.RealPluginProvider = original_plugins
            providers_module.WindowsMessageDetector = original_messages
            WindowsMediaProvider._start_loop = original_start_loop

        self.assertEqual(calls, {"audio": 0, "netease": 0, "qq": 0})
        self.assertIsNone(provider.audio_analyzer)
        self.assertIsNone(provider.netease_position)
        self.assertIsNone(provider.qq_position)

    def test_live_audio_constructor_failure_does_not_escape_provider_path(self):
        class FailingAudioAnalyzer:
            def __init__(self):
                raise RuntimeError("bluetooth endpoint unavailable")

        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {
            "v2_music_auto_detect": True,
            "v2_enable_live_audio_analysis": True,
        }.get(key, default)
        provider.audio_analyzer = None

        original_audio = providers_module.LiveAudioAnalyzer
        providers_module.LiveAudioAnalyzer = FailingAudioAnalyzer
        try:
            with patch("api.providers.time.monotonic", return_value=100.0):
                self.assertFalse(provider.has_live_audio())
        finally:
            providers_module.LiveAudioAnalyzer = original_audio

        self.assertIsNone(provider.audio_analyzer)
        self.assertGreater(getattr(provider, "_audio_analyzer_retry_not_before", 0.0), 100.0)

    def test_unavailable_live_audio_analyzer_is_released_and_cools_down(self):
        class UnavailableAudioAnalyzer:
            def __init__(self):
                self.stopped = False

            def diagnostics(self):
                return {
                    "status": "unavailable",
                    "running": False,
                    "has_recent_frame": False,
                    "last_error": "device invalidated",
                }

            def stop(self):
                self.stopped = True

        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {
            "v2_music_auto_detect": True,
            "v2_enable_live_audio_analysis": True,
        }.get(key, default)
        analyzer = UnavailableAudioAnalyzer()
        provider.audio_analyzer = analyzer

        with patch("api.providers.time.monotonic", return_value=200.0):
            self.assertIsNone(provider._ensure_audio_analyzer_if_requested())

        self.assertTrue(analyzer.stopped)
        self.assertIsNone(provider.audio_analyzer)
        self.assertGreater(getattr(provider, "_audio_analyzer_retry_not_before", 0.0), 200.0)

    def test_disabling_live_audio_releases_existing_analyzer(self):
        class RunningAudioAnalyzer:
            def __init__(self):
                self.stopped = False

            def stop(self):
                self.stopped = True

        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {
            "v2_music_auto_detect": False,
            "v2_enable_live_audio_analysis": False,
        }.get(key, default)
        analyzer = RunningAudioAnalyzer()
        provider.audio_analyzer = analyzer

        self.assertIsNone(provider._ensure_audio_analyzer_if_requested())

        self.assertTrue(analyzer.stopped)
        self.assertIsNone(provider.audio_analyzer)

    def test_reactive_background_requests_live_audio_without_auto_hide(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {
            "v2_music_auto_detect": False,
            "v2_enable_live_audio_analysis": False,
            "v2_music_reactive_background_enabled": True,
        }.get(key, default)

        self.assertTrue(provider._live_audio_requested())

    def test_music_auto_detect_does_not_open_live_audio_capture(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {
            "v2_music_auto_detect": True,
            "v2_enable_live_audio_analysis": False,
            "v2_music_reactive_background_enabled": False,
        }.get(key, default)

        self.assertFalse(provider._live_audio_requested())

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

    def test_delayed_media_refresh_uses_original_timeline_sample_time_once(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        data = empty_visual_data("Song", "Artist")
        data = replace(data, track=replace(
            data.track,
            is_playing=True,
            position_seconds=12.0,
            duration_seconds=60.0,
            position_source="windows-media",
            position_sampled_at=100.0,
        ))

        class DoneFuture:
            def done(self):
                return True

            def result(self):
                return data

        provider._refresh_future = DoneFuture()
        provider._last_data = None
        provider._last_ready_at = 0.0
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._consume_refresh_future(101.4)

        self.assertAlmostEqual(provider._last_data.track.position_seconds, 13.4)
        self.assertEqual(provider._last_data.track.position_sampled_at, 100.0)
        later = provider._project_visual_position(provider._last_data, 101.9, provider._last_ready_at)
        self.assertAlmostEqual(later.track.position_seconds, 13.9)
        uia = replace(data, track=replace(data.track, position_source="netease-uia-progress"))
        self.assertIs(WindowsMediaProvider._advance_delayed_media_sample(uia, 101.4), uia)

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
            position_sampled_at=99.7,
        )
        data = replace(data, track=track)

        projected = provider._project_visual_position(data, now=105.0, anchor_at=100.0)

        self.assertAlmostEqual(projected.track.position_seconds, 165.0)
        self.assertEqual(projected.track.position_sampled_at, 99.7)

    def test_cached_coarse_lyric_sample_stops_before_unobserved_next_line(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        base = empty_visual_data("Song", "Artist")
        lines = (TimedLyricLine(20.0, "current"), TimedLyricLine(24.0, "next"))
        coarse = replace(
            base,
            track=replace(
                base.track,
                is_playing=True,
                duration_seconds=40.0,
                position_seconds=23.92,
                position_source="netease-uia-lyrics",
                timed_lyrics=lines,
            ),
        )

        projected = provider._project_visual_position(coarse, now=101.0, anchor_at=100.0)

        self.assertLess(projected.track.position_seconds, 24.0)
        exact = replace(coarse, track=replace(coarse.track, position_source="netease-uia-progress"))
        self.assertGreater(provider._project_visual_position(exact, now=101.0, anchor_at=100.0).track.position_seconds, 24.0)

    def test_player_anchor_fast_refresh_window_uses_short_interval(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._music_player_adapter = "netease"
        provider._source_app_interactive = False
        provider._source_app_foreground_at = 0.0
        provider._refresh_interval = 0.65
        provider._player_anchor_fast_refresh_until = 105.0

        self.assertAlmostEqual(provider._current_refresh_interval(104.2), 0.12)
        self.assertEqual(provider._current_refresh_interval(105.2), 0.65)

    def test_no_session_status_keeps_recent_paused_track_briefly(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        data = empty_visual_data("Song", "Artist", "cloudmusic.exe")
        provider._last_data = replace(
            data,
            track=replace(
                data.track,
                title="Song",
                artist="Artist",
                is_playing=False,
                position_seconds=30.0,
                duration_seconds=180.0,
            ),
        )
        provider._last_ready_at = 100.0
        provider._provider_status = "no_session"
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.has_live_audio = lambda: False

        retained = provider._cached_or_status_visual(106.0)
        expired = provider._cached_or_status_visual(111.0)

        self.assertEqual(retained.track.title, "Song")
        self.assertEqual(expired.track.title, NO_SESSION_VISUAL_DATA.track.title)

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

    def test_large_player_progress_backwards_seek_commits_after_repeated_advancing_reads(self):
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
        self.assertAlmostEqual(stabilized.track.position_seconds, 120.4)
        provider._last_data = stabilized
        provider._last_ready_at = 100.4
        second = replace(incoming, track=replace(incoming.track, position_seconds=35.5))
        stabilized = provider._stabilize_incoming_position(second, now=100.9)
        self.assertAlmostEqual(stabilized.track.position_seconds, 120.9)
        provider._last_data = stabilized
        provider._last_ready_at = 100.9
        third = replace(incoming, track=replace(incoming.track, position_seconds=36.0))
        stabilized = provider._stabilize_incoming_position(third, now=101.4)
        self.assertAlmostEqual(stabilized.track.position_seconds, 36.0)

    def test_cached_uia_sample_cannot_confirm_player_position_discontinuity(self):
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
                position_sampled_at=99.8,
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
                position_sampled_at=100.3,
            ),
        )
        for now, position in ((100.4, 35.0), (100.9, 35.5), (101.4, 36.0)):
            repeated = replace(incoming, track=replace(incoming.track, position_seconds=position))
            stabilized = provider._stabilize_incoming_position(repeated, now=now)
            self.assertAlmostEqual(stabilized.track.position_seconds, 120.0 + now - 100.0)
            provider._last_data = stabilized
            provider._last_ready_at = now
        fresh = replace(incoming, track=replace(
            incoming.track, position_seconds=36.5, position_sampled_at=101.8,
        ))
        stabilized = provider._stabilize_incoming_position(fresh, now=101.9)
        self.assertAlmostEqual(stabilized.track.position_seconds, 36.5)

    def test_medium_background_player_progress_backstep_is_smoothed(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        data = empty_visual_data("Song", "Artist", "cloudmusic.exe")
        provider._last_ready_at = 100.0
        provider._last_data = replace(
            data,
            track=replace(
                data.track,
                track_id="same-track",
                is_playing=True,
                position_seconds=50.0,
                duration_seconds=180.0,
                position_source="netease-uia-progress",
                source_app_foreground=False,
                position_interactive=False,
            ),
        )
        incoming = replace(
            data,
            track=replace(
                data.track,
                track_id="same-track",
                is_playing=True,
                position_seconds=51.0,
                duration_seconds=180.0,
                position_source="netease-uia-progress",
                source_app_foreground=False,
                position_interactive=False,
            ),
        )

        stabilized = provider._stabilize_incoming_position(incoming, now=104.0)

        self.assertAlmostEqual(stabilized.track.position_seconds, 54.0, delta=0.02)

    def test_repeated_medium_background_player_backstep_eventually_commits(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        data = empty_visual_data("Song", "Artist", "cloudmusic.exe")
        provider._last_ready_at = 100.0
        provider._last_data = replace(
            data,
            track=replace(
                data.track,
                track_id="same-track",
                is_playing=True,
                position_seconds=50.0,
                duration_seconds=180.0,
                position_source="netease-uia-progress",
            ),
        )
        for now, raw in ((100.2, 45.2), (100.65, 45.65), (101.1, 46.1)):
            incoming = replace(
                data,
                track=replace(
                    data.track,
                    track_id="same-track",
                    is_playing=True,
                    position_seconds=raw,
                    duration_seconds=180.0,
                    position_source="netease-uia-progress",
                ),
            )
            accepted = provider._stabilize_incoming_position(incoming, now)
            provider._last_data = accepted
            provider._last_ready_at = now
        self.assertAlmostEqual(accepted.track.position_seconds, 46.1)

    def test_single_false_forward_progress_spike_does_not_latch(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        data = empty_visual_data("Song", "Artist", "cloudmusic.exe")
        provider._last_ready_at = 100.0
        provider._last_data = replace(
            data,
            track=replace(
                data.track,
                track_id="same-track",
                is_playing=True,
                position_seconds=50.0,
                duration_seconds=180.0,
                position_source="netease-uia-progress",
            ),
        )
        spike = replace(data, track=replace(
            data.track, track_id="same-track", is_playing=True,
            position_seconds=55.2, duration_seconds=180.0,
            position_source="netease-uia-progress",
        ))
        stable = provider._stabilize_incoming_position(spike, 100.2)
        self.assertAlmostEqual(stable.track.position_seconds, 50.2)
        provider._last_data = stable
        provider._last_ready_at = 100.2
        correction = replace(data, track=replace(
            data.track, track_id="same-track", is_playing=True,
            position_seconds=50.5, duration_seconds=180.0,
            position_source="netease-uia-progress",
        ))
        stable = provider._stabilize_incoming_position(correction, 100.5)
        self.assertAlmostEqual(stable.track.position_seconds, 50.5)
        self.assertIsNone(provider._pending_player_position_discontinuity)

    def test_single_foreground_uia_backstep_waits_for_confirming_progress(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._music_player_adapter = "netease"
        data = empty_visual_data("Song", "Artist", "cloudmusic.exe")
        provider._last_ready_at = 100.0
        provider._last_data = replace(
            data,
            track=replace(
                data.track, track_id="same-track", is_playing=True,
                position_seconds=50.0, duration_seconds=180.0,
                position_source="netease-uia-progress", source_app_foreground=True,
                position_interactive=True,
            ),
        )
        incoming = replace(
            data,
            track=replace(
                data.track, track_id="same-track", is_playing=True,
                position_seconds=8.0, duration_seconds=180.0,
                position_source="netease-uia-progress", source_app_foreground=True,
                position_interactive=True,
            ),
        )

        stable = provider._stabilize_incoming_position(incoming, 100.2)

        self.assertAlmostEqual(stable.track.position_seconds, 50.2)
        self.assertIsNotNone(provider._pending_player_position_discontinuity)
        self.assertGreater(provider._player_anchor_fast_refresh_until, 100.2)

    def test_slow_but_consistent_uia_readback_can_repair_position(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        data = empty_visual_data("Song", "Artist", "cloudmusic.exe")
        provider._last_ready_at = 100.0
        provider._last_data = replace(
            data,
            track=replace(
                data.track, track_id="same-track", is_playing=True,
                position_seconds=90.0, duration_seconds=180.0,
                position_source="netease-uia-progress",
            ),
        )
        first = replace(
            data,
            track=replace(
                data.track, track_id="same-track", is_playing=True,
                position_seconds=32.0, duration_seconds=180.0,
                position_source="netease-uia-progress",
            ),
        )
        held = provider._stabilize_incoming_position(first, 100.2)
        provider._last_data = held
        provider._last_ready_at = 100.2
        second = replace(first, track=replace(first.track, position_seconds=34.0))

        repaired = provider._stabilize_incoming_position(second, 102.2)

        self.assertAlmostEqual(repaired.track.position_seconds, 34.0)

    def test_legacy_player_position_is_replaced_by_authoritative_system_timeline(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakePositionReader()
        base = empty_visual_data()
        timed = FakeTimedLyricsStore().resolve_cached_or_local("Song", "Artist").timed_lyrics
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = FakeTimedLyricsStore()
        previous_track = replace(
            base.track,
            title="Song",
            artist="Artist",
            source_app="cloudmusic.exe",
            track_id="same-track",
            is_playing=True,
            duration_seconds=240.0,
            position_seconds=120.0,
            position_source="netease-uia-progress",
            position_confidence=0.90,
            timed_lyrics=timed,
        )
        provider._last_data = replace(base, track=previous_track)
        provider._last_ready_at = time.monotonic()
        provider._source_app_foreground = False
        provider._source_app_foreground_at = 0.0
        provider._last_raw_timeline_track_id = "same-track"
        provider._last_raw_timeline_position = 95.4
        provider._last_raw_timeline_at = provider._last_ready_at
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
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: True
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 96.0, 240.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertAlmostEqual(data.track.position_seconds, 96.0, delta=0.25)
        self.assertEqual(data.track.position_source, "windows-media")
        self.assertEqual(reader.read_calls, [])
        self.assertFalse(data.track.source_app_foreground)
        self.assertGreater(data.track.position_sampled_at, 0.0)

    def test_legacy_uia_progress_cannot_override_system_sample(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeLaggedProgressReader()
        base = empty_visual_data()
        timed = FakeTimedLyricsStore().resolve_cached_or_local("Song", "Artist").timed_lyrics
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = FakeTimedLyricsStore()
        previous_track = replace(
            base.track,
            title="Song",
            artist="Artist",
            source_app="cloudmusic.exe",
            track_id="same-track",
            is_playing=True,
            duration_seconds=240.0,
            position_seconds=120.0,
            position_source="netease-uia-progress",
            position_confidence=0.90,
            timed_lyrics=timed,
        )
        provider._last_data = replace(base, track=previous_track)
        provider._last_ready_at = time.monotonic()
        provider._source_app_foreground = False
        provider._source_app_foreground_at = 0.0
        provider._last_raw_timeline_track_id = "same-track"
        provider._last_raw_timeline_position = 95.4
        provider._last_raw_timeline_at = provider._last_ready_at
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
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: True
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 96.0, 240.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertAlmostEqual(data.track.position_seconds, 96.0, delta=0.25)
        self.assertEqual(data.track.position_source, "windows-media")
        self.assertGreater(data.track.position_sampled_at, 0.0)

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

    def test_select_media_session_excludes_our_explicit_smtc_identity(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        own = FakeSession("GluSpot", "Own song", "Own artist", True, 15.0, 200.0)
        external = FakeSession("cloudmusic.exe", "Other song", "Artist", False, 12.0, 200.0)

        selected = asyncio.run(provider._select_media_session(own, [own, external]))

        self.assertIs(selected, external)
        self.assertEqual(provider._media_session_count, 1)
        self.assertNotIn("GluSpot", " ".join(provider._media_session_candidates))

    def test_our_retired_smtc_session_is_never_current_fallback(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        own = FakeSession("gluspot", "", "", False)

        self.assertIsNone(asyncio.run(provider._select_media_session(own, [own])))
        self.assertEqual(provider._media_session_count, 0)
        self.assertEqual(provider._selected_media_session_index, -1)

    def test_unrelated_python_session_remains_an_external_candidate(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        python = FakeSession("python.exe", "Other app song", "Artist", True, 15.0, 200.0)

        self.assertIs(asyncio.run(provider._select_media_session(python, [python])), python)
        self.assertEqual(provider._media_session_count, 1)

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

    def test_music_player_adapter_requires_configured_profile_path_when_profiles_exist(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
            "v2_music_app_profiles": {
                "netease": {"path": "", "sync_enabled": True},
                "qq": {"path": "", "sync_enabled": True},
            },
        }.get(key, default)

        self.assertEqual(provider._music_player_adapter_for("cloudmusic.exe", "cloudmusic.exe"), "system")
        self.assertEqual(provider._music_player_adapter_for("QQ闊充箰", "QQMusic.exe"), "system")

    def test_legacy_ncm_executable_path_can_activate_netease_profile(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._last_foreground_snapshot = {}
        provider.settings_getter = lambda key, default=None: {
            "v2_netease_betterncm_path": r"C:\Program Files\NetEase\CloudMusic\cloudmusic.exe",
            "v2_music_app_profiles": {
                "netease": {"path": "", "sync_enabled": True},
            },
        }.get(key, default)

        self.assertEqual(provider._music_player_adapter_for("cloudmusic.exe", "cloudmusic.exe"), "netease")
        self.assertTrue(provider._player_progress_plugin_enabled())

    def test_profile_without_effective_path_is_not_reported_as_enabled(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {
            "v2_music_app_profiles": {
                "netease": {"path": "", "sync_enabled": True},
            },
        }.get(key, default)

        self.assertFalse(provider._player_progress_plugin_enabled())

    def test_music_player_adapter_allows_configured_netease_profile_path(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
            "v2_music_app_profiles": {
                "netease": {"path": r"C:\Program Files\NetEase\CloudMusic\cloudmusic.exe", "sync_enabled": True},
            },
        }.get(key, default)

        self.assertEqual(provider._music_player_adapter_for("cloudmusic.exe", "cloudmusic.exe"), "netease")

    def test_configured_player_adapter_survives_temporary_foreground_window_change(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {
            "v2_music_player_mode": "netease",
            "v2_music_app_profiles": {
                "netease": {
                    "path": r"A:\CloudMusic\cloudmusic.exe",
                    "sync_enabled": True,
                },
            },
        }.get(key, default)
        provider._last_foreground_snapshot = {
            "process_path": r"A:\CloudMusic\cloudmusic.exe",
            "process": "cloudmusic.exe",
        }

        first = provider._music_player_adapter_for("Unknown.Media.App", "Unknown.Media.App")
        provider._last_foreground_snapshot = {
            "process_path": r"C:\Program Files\Browser\browser.exe",
            "process": "browser.exe",
        }
        second = provider._music_player_adapter_for("Unknown.Media.App", "Unknown.Media.App")

        self.assertEqual(first, "netease")
        self.assertEqual(second, "netease")

    def test_music_player_adapter_allows_configured_qq_profile_path(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
            "v2_music_app_profiles": {
                "qq": {"path": r"D:\Apps\Tencent\QQMusic\QQMusic.exe", "sync_enabled": True},
            },
        }.get(key, default)

        self.assertEqual(provider._music_player_adapter_for("QQMusic.exe", "QQMusic.exe"), "qq")

    def test_music_app_profiles_auto_match_detected_player_without_legacy_auto_toggle(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "netease",
            "v2_music_app_profiles": {
                "netease": {"path": r"C:\Music\cloudmusic.exe", "sync_enabled": True},
                "qq": {"path": r"C:\Music\QQMusic.exe", "sync_enabled": True},
            },
        }.get(key, default)

        self.assertEqual(provider._music_player_adapter_for("QQMusic.exe", "QQMusic.exe"), "qq")

    def test_betterncm_adapter_requires_enabled_netease_profile(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "netease_betterncm",
            "v2_music_app_profiles": {
                "netease": {
                    "path": r"C:\Program Files\NetEase\CloudMusic\cloudmusic.exe",
                    "sync_enabled": True,
                    "betterncm_enabled": False,
                },
            },
        }.get(key, default)

        self.assertEqual(provider._music_player_adapter_for("cloudmusic.exe", "cloudmusic.exe"), "system")

        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "netease_betterncm",
            "v2_music_app_profiles": {
                "netease": {
                    "path": r"C:\Program Files\NetEase\CloudMusic\cloudmusic.exe",
                    "sync_enabled": True,
                    "betterncm_enabled": True,
                },
            },
        }.get(key, default)

        self.assertEqual(provider._music_player_adapter_for("cloudmusic.exe", "cloudmusic.exe"), "netease_betterncm")

    def test_system_lyric_offset_is_shared_and_ignores_legacy_player_profiles(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {
            "v2_music_app_profiles": {
                "netease": {"path": r"C:\Music\cloudmusic.exe", "sync_enabled": True, "lyric_offset_seconds": 0.75},
                "qq": {"path": r"C:\Music\QQMusic.exe", "sync_enabled": True, "lyric_offset_seconds": -1.25},
            },
            "v2_system_lyric_offset_seconds": 0.5,
        }.get(key, default)

        for adapter in ("netease", "netease_betterncm", "qq", "system"):
            self.assertEqual(provider._lyric_offset_seconds_for_adapter(adapter), 0.5)

    def test_lightweight_follow_provider_has_no_forced_anchor_interfaces(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)

        for name in (
            "request_progress_anchor_refresh",
            "_progress_poll_anchor_due",
            "_progress_poll_cached_refresh_due",
            "_progress_poll_interval_seconds_for_adapter",
            "_progress_poll_refresh_interval",
            "_start_global_pointer_sampler",
            "_sample_global_pointer_once",
            "_record_global_mouse_event",
            "_record_source_app_pointer_state",
            "_source_app_recent_click_position",
            "_global_recent_click_position",
            "_lyric_click_debug_snapshot",
            "_schedule_lyric_click_progress_retry",
        ):
            self.assertFalse(hasattr(provider, name), name)

    def test_lyrics_found_progress_retry_runs_five_seconds_after_lyrics_load(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._player_anchor_fast_refresh_until = 0.0

        provider._schedule_lyrics_found_progress_retry("track-1", 100.0)

        self.assertFalse(provider._lyrics_found_progress_retry_due("track-1", 104.9))
        self.assertTrue(provider._lyrics_found_progress_retry_due("track-1", 105.1))
        self.assertTrue(provider._lyrics_found_progress_retry_due("track-1", 106.8))
        self.assertFalse(provider._lyrics_found_progress_retry_due("track-1", 107.4))

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

    def test_fresh_paused_lyric_anchor_is_accepted(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        self.assertTrue(provider._should_use_player_sample("netease", "netease-uia-lyrics", 0.2, False))

    def test_paused_system_timeline_ignores_legacy_player_lyric_anchor(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakePausedLyricReader()
        base = empty_visual_data()
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = FakeTimedLyricsStore()
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
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: False
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", False, 0.0, 240.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(data.track.position_seconds, 0.0)
        self.assertFalse(data.track.is_playing)
        self.assertEqual(data.track.position_source, "windows-media")
        self.assertEqual(reader.read_calls, [])

    def test_playing_system_clock_does_not_use_old_player_anchor(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakePositionReader()
        reader.read = lambda *_args, **_kwargs: NeteasePositionSample(
            23.92, 40.0, "netease-uia-lyrics", 0.86, 0, "current",
            time.monotonic() - 0.5, True,
        )

        class TwoLineStore(FakeLyricsStore):
            def resolve_cached_or_local(self, *_args):
                lines = (TimedLyricLine(20.0, "current"), TimedLyricLine(24.0, "next"))
                return SimpleNamespace(timed_lyrics=lines, lyrics=("current", "next"), source="test")

        provider.fallback = EmptyIslandDataProvider(empty_visual_data())
        provider.lyrics_store = TwoLineStore()
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
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
        }.get(key, default)
        provider._is_source_app_foreground = lambda *_args: False
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")
        provider._get_session = lambda: asyncio.sleep(
            0, result=FakeSession("cloudmusic.exe", "Song", "Artist", True, 0.0, 40.0)
        )

        data = asyncio.run(provider._read_visual_data())

        self.assertEqual(data.track.position_source, "windows-media")
        self.assertEqual(data.track.position_seconds, 0.0)

    def test_playing_coarse_lyric_backstep_does_not_replay_line(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        data = empty_visual_data("Song", "Artist", "cloudmusic.exe")
        provider._pending_player_position_discontinuity = None
        provider._local_seek_position_seconds = None
        provider._last_ready_at = 100.0
        provider._last_data = replace(
            data,
            track=replace(
                data.track,
                track_id="same-track",
                is_playing=True,
                position_seconds=50.0,
                duration_seconds=180.0,
                position_source="netease-uia-lyrics",
            ),
        )
        incoming = replace(
            data,
            track=replace(
                data.track,
                track_id="same-track",
                is_playing=True,
                position_seconds=47.8,
                duration_seconds=180.0,
                position_source="netease-uia-lyrics",
                source_app_foreground=True,
                position_interactive=True,
            ),
        )

        stable = provider._stabilize_incoming_position(incoming, now=100.5)

        self.assertAlmostEqual(stable.track.position_seconds, 50.5, delta=0.02)

    def test_netease_art_search_requires_exact_track_identity(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        payload = {
            "result": {
                "songs": [
                    {
                        "name": "No Space Remix",
                        "artists": [{"name": "alextbh"}],
                        "album": {"picUrl": "https://example.test/wrong.jpg"},
                    },
                    {
                        "name": "no space",
                        "artists": [{"name": "alextbh"}],
                        "album": {"picUrl": "https://example.test/correct.jpg"},
                    },
                ]
            }
        }

        class Response:
            _read = False

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read1(self, _limit):
                if self._read:
                    return b""
                self._read = True
                return __import__("json").dumps(payload).encode("utf-8")

        with patch("api.providers.urlopen", return_value=Response()):
            url = provider._resolve_netease_art_url("no space", "alextbh")

        self.assertEqual(url, "https://example.test/correct.jpg")

    def test_netease_art_search_resolves_legacy_album_id_to_pic_url(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        search_payload = {
            "result": {
                "songs": [
                    {
                        "name": "no space",
                        "artists": [{"name": "alextbh"}],
                        "album": {"id": 80358190, "picId": 109951164208449488},
                    }
                ]
            }
        }
        class Response:
            def __init__(self, payload):
                self.payload = payload
                self._read = False

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read1(self, _limit):
                if self._read:
                    return b""
                self._read = True
                return __import__("json").dumps(self.payload).encode("utf-8")

        with patch(
            "api.providers.urlopen",
            return_value=Response(search_payload),
        ):
            url = provider._resolve_netease_art_url("no space", "alextbh")

        self.assertEqual(
            url,
            "https://p1.music.126.net/c5wY2dM--lFY52kjRPHg6Q==/109951164208449488.jpg?param=2560y2560",
        )

    def test_live_deep_in_shallow_end_catalog_duplicates_do_not_block_unknown_metadata(self):
        # NetEase's real response while Windows supplied only title/artist:
        # the EP has two regional IDs, followed by a tour compilation. The
        # reference returns the first exact song, rather than rejecting all.
        songs = [
            {"name": "Deep In The Shallow End", "artists": [{"name": "Jenevieve"}], "duration": 214025,
             "album": {"name": "Rendezvous - EP", "id": 151284437, "picId": 109951167858662243}},
            {"name": "Deep In The Shallow End", "artists": [{"name": "Jenevieve"}], "duration": 214025,
             "album": {"name": "Rendezvous - EP", "id": 151285319, "picId": 109951167858656895}},
            {"name": "Deep In The Shallow End", "artists": [{"name": "Jenevieve"}], "duration": 214025,
             "album": {"name": "贰零贰肆巡演 · 2024中国首演特别版", "id": 254427823, "picId": 109951170171699617}},
        ]
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._fetch_current_art_json = lambda *_args: {"code": 200, "result": {"songs": songs}}
        self.assertEqual(
            provider._resolve_netease_art_url("Deep In The Shallow End", "Jenevieve"),
            "https://p1.music.126.net/VEK_5p05jzF6M0vqdWQpSw==/109951167858662243.jpg?param=2560y2560",
        )

    def test_netease_reference_metadata_matching_does_not_add_empty_artist_gate(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._fetch_current_art_json = lambda *_args: {"result": {"songs": [
            {"name": "No  Space", "artists": [], "album": {"picUrl": "https://example.test/matched.jpg"}},
        ]}}
        self.assertEqual(provider._resolve_netease_art_url("no space", "alextbh"), "https://example.test/matched.jpg")

    def test_netease_api_denial_enters_source_cooldown_before_caa_fallback(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._online_current_art_lookup_enabled = True
        provider._music_player_adapter = "netease"
        provider._musicbrainz_artwork_resolver = SimpleNamespace(
            resolve=lambda *_args, **_kwargs: "https://archive.org/download/valid/front.jpg"
        )
        requested = []
        provider._schedule_queue_album_art_prefetch = lambda url: requested.append(url)
        provider._fetch_current_art_json = lambda *_args: {"code": 405, "result": {"songs": []}}
        jobs = []

        class ManualThread:
            def __init__(self, *, target, **_kwargs):
                self.target = target

            def start(self):
                jobs.append(self.target)

        class ManualTimer:
            def __init__(self, _seconds, callback):
                self.callback = callback

            def start(self):
                pass

            def cancel(self):
                pass

        with patch("api.providers.threading.Thread", ManualThread), patch("api.providers.threading.Timer", ManualTimer):
            self.assertTrue(provider._schedule_current_netease_art_lookup("track", "Song", "Artist", album_title="Album", duration_seconds=240))
            jobs.pop(0)()
            self.assertEqual(provider._current_art_lookup_sources["track"], "cover-art-archive")
            self.assertEqual(requested, ["https://archive.org/download/valid/front.jpg"])
            self.assertGreater(provider._netease_art_source_failed_until, time.monotonic() + 40.0)

            provider._current_art_url_by_track_id.pop("track")
            self.assertTrue(provider._schedule_current_netease_art_lookup("track", "Song", "Artist", album_title="Album", duration_seconds=240))
            jobs.pop(0)()
            self.assertEqual(requested, ["https://archive.org/download/valid/front.jpg"] * 2)

    def test_current_online_art_is_bound_to_exact_track_id(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._current_art_url_by_track_id = {"old-track": "https://example.test/old.jpg"}
        provider._queue_album_art_bytes = lambda track: str(track.album_art_url).encode("utf-8")

        self.assertEqual(provider._current_online_album_art_bytes("new-track"), b"")
        self.assertEqual(
            provider._current_online_album_art_bytes("old-track"),
            b"https://example.test/old.jpg",
        )

    def test_recent_lyric_click_does_not_enable_any_legacy_reader_or_hit_test(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeLightweightFollowReader()
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
                timed_lyrics=(TimedLyricLine(96.0, "before"), TimedLyricLine(160.0, "line")),
            ),
        )
        now = time.monotonic()
        provider._last_ready_at = now
        provider._source_app_foreground_at = now
        provider._source_app_recent_click_at = now
        provider._source_app_recent_click_pos = (520, 360)
        provider._source_app_recent_pointer_at = now
        provider._source_app_recent_pointer_pos = (520, 360)
        provider._global_recent_click_at = 0.0
        provider._global_recent_click_pos = None
        provider._global_recent_pointer_at = 0.0
        provider._global_recent_pointer_pos = None
        provider._global_pointer_lock = None
        provider._last_raw_timeline_track_id = "same-track"
        provider._last_raw_timeline_position = 96.0
        provider._last_raw_timeline_at = now
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider._queue_art_cache = {}
        provider.netease_position = reader
        provider.qq_position = None
        provider._music_player_adapter_for = lambda _source_app, _raw_source_app="": "netease"
        provider._player_position_reader_for = lambda _adapter: reader
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
        self.assertEqual(reader.hit_calls, [])
        self.assertEqual(reader.content_calls, [])
        self.assertEqual(reader.read_calls, [])
        self.assertEqual(data.track.position_source, "windows-media")

    def test_background_player_window_does_not_override_system_metadata(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeWindowTrackPositionReader()
        resets = []
        reader.reset = lambda: resets.append(True)
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
        self.assertEqual(reader.current_track_calls, 0)
        self.assertEqual(data.track.title, "Old Song")
        self.assertEqual(data.track.artist, "Old Artist")
        self.assertEqual(data.track.duration_seconds, 180.0)
        self.assertEqual(data.track.track_id, "old-track")
        self.assertEqual(resets, [])

    def test_unobserved_player_version_does_not_replace_current_system_track(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        base = empty_visual_data()
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = FakeLyricsStore()
        provider._last_data = replace(
            base,
            track=replace(
                base.track, title="Song", artist="Artist", track_id="old-version",
                source_app="cloudmusic.exe", duration_seconds=180.0,
                position_seconds=95.0, album_art_bytes=b"old-cover",
            ),
        )
        provider._last_ready_at = time.monotonic()
        provider._source_app_foreground_at = 0.0
        provider._last_raw_timeline_track_id = "old-version"
        provider._last_raw_timeline_position = 95.0
        provider._last_raw_timeline_at = provider._last_ready_at
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider.netease_position = SimpleNamespace(
            source_prefix="netease",
            current_track=lambda: SimpleNamespace(
                title="Song", artist="Artist", album_title="New Album", duration_seconds=220.0,
            ),
            duration_for_track=lambda *_args: 220.0,
        )
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": False,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "netease",
        }.get(key, default)
        provider._is_source_app_foreground = lambda *_args: False
        provider._schedule_current_netease_art_lookup = lambda *_args, **_kwargs: False
        thumbnails = []
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(
            0, result=thumbnails.append(True) or b"old-thumbnail"
        )

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 95.0, 180.0)

        provider._get_session = fake_get_session
        data = asyncio.run(provider._read_visual_data())

        self.assertEqual(data.track.duration_seconds, 180.0)
        self.assertAlmostEqual(data.track.position_seconds, 95.0, delta=0.25)
        self.assertEqual(data.track.album_art_bytes, b"old-thumbnail")
        self.assertEqual(thumbnails, [True])
        self.assertEqual(data.track.track_id, "old-version")

    def test_same_metadata_different_duration_gets_new_track_identity(self):
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
        provider.netease_position = None
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda _key, default=None: default
        provider._is_source_app_foreground = lambda *_args: False
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")
        session = FakeSession("cloudmusic.exe", "Same Song", "Artist", True, 100.0, 120.0)
        provider._get_session = lambda: asyncio.sleep(0, result=session)

        first = asyncio.run(provider._read_visual_data())
        provider._last_data = first
        session.timeline = FakeTimeline(0.0, 0.0)
        unknown_duration = asyncio.run(provider._read_visual_data())
        # With no version/album metadata a duration dropout is deliberately
        # treated as the current track; do not guess a same-name replacement.
        self.assertEqual(unknown_duration.track.track_id, first.track.track_id)

        provider._last_data = unknown_duration
        session.timeline = FakeTimeline(0.0, 210.0)
        new_version = asyncio.run(provider._read_visual_data())
        self.assertNotEqual(new_version.track.track_id, first.track.track_id)
        self.assertEqual(new_version.track.position_seconds, 0.0)

        provider._last_data = new_version
        session.properties.album_title = "Album One"
        same_album_added = asyncio.run(provider._read_visual_data())
        self.assertEqual(same_album_added.track.track_id, new_version.track.track_id)
        provider._last_data = same_album_added
        session.properties.album_title = ""
        temporarily_unknown_album = asyncio.run(provider._read_visual_data())
        self.assertEqual(temporarily_unknown_album.track.track_id, same_album_added.track.track_id)
        provider._last_data = temporarily_unknown_album
        session.properties.album_title = "Album Two"
        new_album = asyncio.run(provider._read_visual_data())
        self.assertNotEqual(new_album.track.track_id, same_album_added.track.track_id)

    def test_current_system_read_never_prefetches_private_player_queue(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeQueuePositionReader()
        queue_ready = False
        queue_reads = []
        read_adjacent = reader.adjacent_track

        def adjacent_when_ready(title, artist, direction):
            queue_reads.append((title, artist, direction))
            if not queue_ready:
                return None
            return read_adjacent(title, artist, direction)

        reader.adjacent_track = adjacent_when_ready

        class CachedCurrentLyricsStore(TrackingLyricsStore):
            def resolve_cached_or_local(self, title, artist, album_title="", duration_seconds=0.0):
                self.local_calls.append((title, artist, album_title, duration_seconds))
                if title == "Song":
                    lines = (TimedLyricLine(0.0, "current line"),)
                    return SimpleNamespace(timed_lyrics=lines, lyrics=("current line",), source="local:test")
                return SimpleNamespace(timed_lyrics=(), lyrics=(), source="")

        lyrics_store = CachedCurrentLyricsStore()
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
            "v2_music_surface_preload_count": "corrupt",
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: True
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 12.0, 240.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(queue_reads, [])
        self.assertEqual(provider._music_surface_preload_count(), 6)

        queue_ready = True
        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(reader.adjacent_calls, [])
        self.assertEqual(lyrics_store.background_calls, [])
        queue_reads_after_prefetch = len(queue_reads)
        asyncio.run(provider._read_visual_data())
        self.assertEqual(len(queue_reads), queue_reads_after_prefetch)

    def test_same_track_refresh_keeps_loaded_lyrics_when_lookup_temporarily_empty(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakePositionReader()
        lyrics_store = TrackingLyricsStore()
        base = empty_visual_data()
        previous_lines = (
            TimedLyricLine(10.0, "loaded line"),
            TimedLyricLine(20.0, "next loaded line"),
        )
        previous_track = replace(
            base.track,
            title="Song",
            artist="Artist",
            lyrics=tuple(line.text for line in previous_lines),
            timed_lyrics=previous_lines,
            lyrics_source="local:test",
            source_app="cloudmusic.exe",
            track_id="stable-track",
            is_playing=True,
            duration_seconds=240.0,
            position_seconds=35.0,
        )
        provider.fallback = EmptyIslandDataProvider(base)
        provider.lyrics_store = lyrics_store
        provider._last_data = replace(base, track=previous_track)
        provider._last_ready_at = time.monotonic()
        provider._source_app_foreground_at = 0.0
        provider._last_raw_timeline_track_id = "stable-track"
        provider._last_raw_timeline_position = 35.0
        provider._last_raw_timeline_at = time.monotonic()
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
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: False
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 36.0, 240.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(data.track.track_id, "stable-track")
        self.assertEqual(data.track.timed_lyrics, previous_lines)
        self.assertEqual(data.track.lyrics, tuple(line.text for line in previous_lines))
        self.assertEqual(data.track.lyrics_source, "local:test")
        self.assertEqual(lyrics_store.background_calls, [])

    def test_current_system_read_does_not_expose_private_player_album_queue(self):
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

        def colors_from_art(art, fallback, secondary):
            if art == cover_bytes:
                raise AssertionError("queue color extraction should not run in provider refresh path")
            return fallback, secondary

        provider._colors_from_art = colors_from_art

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 12.0, 240.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(reader.upcoming_calls, [])
        self.assertEqual(data.track.album_queue, ())

    def test_legacy_enabled_player_settings_cannot_start_readers_or_foreground_detection(self):
        for app_id in ("cloudmusic.exe", "QQMusic.exe"):
            with self.subTest(app_id=app_id):
                provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
                provider.fallback = EmptyIslandDataProvider(empty_visual_data())
                provider.lyrics_store = FakeLyricsStore()
                provider._last_data = None
                provider._last_ready_at = 0.0
                provider._last_raw_timeline_track_id = provider._local_seek_track_id = ""
                provider._last_raw_timeline_position = provider._local_seek_position_seconds = None
                provider._last_raw_timeline_at = provider._local_seek_at = 0.0
                provider._art_cache = provider._queue_art_cache = {}
                provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
                provider.message_detector = SimpleNamespace(messages=lambda: ())
                provider.settings_getter = lambda key, default=None: {
                    "v2_enable_player_progress_plugin": True,
                    "v2_player_progress_auto_detect": True,
                    "v2_music_player_mode": "netease_betterncm",
                    "v2_music_app_profiles": {
                        "netease": {"path": r"C:\Music\cloudmusic.exe", "sync_enabled": True},
                        "qq": {"path": r"C:\Music\QQMusic.exe", "sync_enabled": True},
                    },
                    "v2_system_lyric_offset_seconds": 0.25,
                }.get(key, default)
                provider._get_session = lambda: asyncio.sleep(
                    0, result=FakeSession(app_id, "System Song", "System Artist", True, 12.0, 240.0),
                )
                provider._read_thumbnail = lambda *_args: asyncio.sleep(0, result=b"")
                provider._schedule_current_netease_art_lookup = lambda *_args, **_kwargs: False
                for name in ("_music_player_adapter_for", "_player_position_reader_for",
                             "_is_source_app_foreground", "_foreground_netease_track_metadata",
                             "_player_window_track_metadata"):
                    setattr(provider, name, lambda *_args, _name=name, **_kwargs: self.fail(_name))
                with patch.object(providers_module, "NeteasePositionReader", side_effect=AssertionError("UIA reader")), patch.object(
                    providers_module, "QQPositionReader", side_effect=AssertionError("QQ UIA reader"),
                ):
                    data = asyncio.run(provider._read_visual_data())
                self.assertEqual(data.track.title, "System Song")
                self.assertEqual(data.track.artist, "System Artist")
                self.assertEqual(data.track.position_source, "windows-media")
                self.assertFalse(data.track.source_app_foreground)
                self.assertEqual(data.track.lyric_offset_seconds, 0.25)
                self.assertEqual(data.track.album_queue, ())

    def test_album_queue_entries_preserve_metadata_when_art_is_missing(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: {"v2_album_island_cover_count": 5}.get(key, default)
        provider._queue_album_art_bytes = lambda track: b""

        class MetadataOnlyQueueReader:
            def upcoming_tracks(self, title, artist, count):
                return (
                    SimpleNamespace(
                        title="Next Without Art",
                        artist="Artist",
                        album_title="Album",
                        duration_seconds=180.0,
                        album_art_url="",
                    ),
                )

        entries = provider._album_queue_entries_from_player("Song", "Artist", MetadataOnlyQueueReader())

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].title, "Next Without Art")
        self.assertEqual(entries[0].album_art_bytes, b"")

    def test_queue_art_cache_miss_schedules_nonblocking_prefetch(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"GLUSPOT_ALBUM_ART_CACHE_DIR": root}):
            provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
            provider._queue_art_cache = {}
            scheduled = []
            provider._schedule_queue_album_art_prefetch = lambda url: scheduled.append(url) or True
            track = SimpleNamespace(album_art_bytes=b"", image_bytes=b"", album_art_url="https://example.test/next.png")

            self.assertEqual(provider._queue_album_art_bytes(track), b"")
            self.assertEqual(scheduled, ["https://example.test/next.png"])

    def test_current_original_art_cache_is_preferred_over_system_thumbnail(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        provider._queue_art_cache = {"https://example.test/current.png": png}
        provider._queue_art_inflight = set()
        provider._queue_art_failed_until = {}
        provider._queue_art_lock = threading.Lock()
        track = SimpleNamespace(album_art_bytes=b"", image_bytes=b"", album_art_url="https://example.test/current.png")

        self.assertEqual(provider._queue_album_art_bytes(track), png)

    def test_original_art_disk_cache_is_available_on_first_read(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._queue_art_cache = {}
        provider._queue_art_inflight = set()
        provider._queue_art_failed_until = {}
        provider._queue_art_lock = threading.Lock()
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        url = "https://example.test/persisted.png"
        track = SimpleNamespace(album_art_bytes=b"", image_bytes=b"", album_art_url=url)
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"GLUSPOT_ALBUM_ART_CACHE_DIR": root}):
            provider._persist_queue_art(url, png)
            provider._queue_art_cache = {}

            loaded = provider._queue_album_art_bytes(track)

        self.assertEqual(loaded, png)

    def test_queue_art_prefetch_caches_valid_image_and_requests_refresh(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._queue_art_cache = {}
        provider._queue_art_inflight = set()
        provider._queue_art_failed_until = {}
        provider._queue_art_lock = threading.Lock()
        provider._queue_art_slots = threading.BoundedSemaphore(3)
        refreshed = threading.Event()
        provider._last_refresh_at = 12.0
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return png

        with patch("api.providers.urlopen", return_value=Response()):
            self.assertTrue(provider._schedule_queue_album_art_prefetch("https://example.test/next.png"))
            for _ in range(100):
                if provider._last_refresh_at == 0.0:
                    break
                refreshed.wait(0.01)

        self.assertEqual(provider._queue_art_cache["https://example.test/next.png"], png)
        self.assertEqual(provider._queue_art_inflight, set())
        self.assertEqual(provider._last_refresh_at, 0.0)

    def test_queue_art_download_accepts_complete_five_megabyte_image_but_caps_oversize(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._queue_art_cache = {}
        provider._queue_art_inflight = set()
        provider._queue_art_failed_until = {}
        provider._queue_art_lock = threading.Lock()
        provider._queue_art_slots = threading.BoundedSemaphore(1)
        provider._persist_queue_art = lambda *_args: None
        provider._last_refresh_at = 1.0
        minimal_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        complete_image = minimal_png + b"x" * (5 * 1024 * 1024)
        oversize_image = minimal_png + b"x" * (provider.QUEUE_ART_MAX_BYTES + 1)
        jobs, read_limits = [], []

        class ManualThread:
            def __init__(self, *, target, **_kwargs):
                self.target = target

            def start(self):
                jobs.append(self.target)

        class Response:
            def __init__(self, content):
                self.content = content

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, limit):
                read_limits.append(limit)
                return self.content[:limit]

        with patch("api.providers.threading.Thread", ManualThread), patch(
            "api.providers.urlopen", side_effect=[Response(complete_image), Response(oversize_image)]
        ):
            self.assertTrue(provider._schedule_queue_album_art_prefetch("https://example.test/large.png"))
            jobs.pop(0)()
            self.assertEqual(provider._queue_art_cache["https://example.test/large.png"], complete_image)
            self.assertTrue(provider._schedule_queue_album_art_prefetch("https://example.test/too-large.png"))
            jobs.pop(0)()

        self.assertEqual(read_limits, [provider.QUEUE_ART_MAX_BYTES + 1] * 2)
        self.assertNotIn("https://example.test/too-large.png", provider._queue_art_cache)
        self.assertGreater(provider._queue_art_failed_until["https://example.test/too-large.png"], time.monotonic())

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
        self.assertEqual(reader.adjacent_calls, [("Song", "Artist", "next"), ("Next Song", "Next Artist", "next")])
        self.assertEqual(
            lyrics_store.background_calls,
            [
                ("Next Song", "Next Artist", "Next Album", 201.0),
                ("Third Song", "Third Artist", "Third Album", 199.0),
            ],
        )

    def test_netease_upcoming_lyrics_use_one_bounded_queue_read(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        lyrics_store = TrackingLyricsStore()
        provider.lyrics_store = lyrics_store
        provider.settings_getter = lambda key, default=None: {"v2_enable_dual_line_lyrics": False}.get(key, default)
        calls = []

        class BatchQueueReader:
            def upcoming_tracks(self, title, artist, count):
                calls.append((title, artist, count))
                return (
                    SimpleNamespace(title="Next", artist="B", album_title="Two", duration_seconds=201.0),
                    SimpleNamespace(title="Third", artist="C", album_title="Three", duration_seconds=199.0),
                )

            def adjacent_track(self, *_args):
                raise AssertionError("batch prefetch should not re-read each adjacent track")

        reader = BatchQueueReader()
        self.assertTrue(provider._prefetch_netease_upcoming_lyrics("Current", "A", reader, count=2))
        self.assertEqual(calls, [("Current", "A", 2)])
        self.assertEqual(
            lyrics_store.background_calls,
            [("Next", "B", "Two", 201.0), ("Third", "C", "Three", 199.0)],
        )
        self.assertFalse(provider._prefetch_netease_upcoming_lyrics("Current", "A", reader, count=2))
        self.assertEqual(calls, [("Current", "A", 2)])

    def test_current_lookup_blocks_automatic_netease_upcoming_lyric_prefetch(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeTwoStepQueuePositionReader()
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
            "v2_enable_player_progress_plugin": True,
            "v2_player_progress_auto_detect": True,
            "v2_music_player_mode": "netease",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: False
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "Song", "Artist", True, 12.0, 240.0)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(reader.adjacent_calls, [])
        self.assertEqual(lyrics_store.background_calls, [("Song", "Artist", "", 240.0)])

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

        # Losing the player's original-art URL on an unfocused refresh must
        # not replace an already sharp current cover with a system thumbnail.
        original_cover = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        provider._last_data = replace(data, track=replace(data.track, album_art_bytes=original_cover))
        provider._queue_album_art_bytes = lambda _track: b""
        before = events.count(("thumbnail", "", ""))
        unfocused = asyncio.run(provider._read_visual_data())
        self.assertEqual(unfocused.track.album_art_bytes, original_cover)
        self.assertEqual(events.count(("thumbnail", "", "")), before)

    def test_slow_thumbnail_does_not_hold_track_and_refreshes_when_ready(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._last_refresh_at = 4.0

        async def scenario():
            release = asyncio.Event()

            async def slow_thumbnail(_properties, _track_key):
                await release.wait()
                return b"thumbnail"

            provider._read_thumbnail = slow_thumbnail
            first = await provider._thumbnail_for_track(None, "song")
            self.assertEqual(first, b"")
            self.assertEqual(len(provider._pending_thumbnail_tasks), 1)
            release.set()
            await asyncio.wait_for(next(iter(provider._pending_thumbnail_tasks.values())), 0.2)
            await asyncio.sleep(0)
            self.assertEqual(provider._last_refresh_at, 0.0)
            self.assertEqual(await provider._thumbnail_for_track(None, "song"), b"thumbnail")

        asyncio.run(scenario())

    def test_current_track_stops_lyric_lookup_after_first_confirmed_empty_result(self):
        with tempfile.TemporaryDirectory() as root:
            provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
            provider.lyrics_store = LocalLyricsStore(Path(root))
            provider.settings_getter = lambda _key, default=None: default
            started = threading.Event()
            finish = threading.Event()
            searches = []

            def lookup(title, *_args):
                searches.append(title)
                started.set()
                finish.wait(2.0)
                return LyricsMatch((), (), "")

            provider.lyrics_store._resolve_online = lookup
            first_match, first_pending, first_started = provider._ensure_lyrics_lookup_started("Song", "Artist")
            self.assertEqual(first_match, LyricsMatch((), (), ""))
            self.assertTrue(first_pending)
            self.assertTrue(first_started)
            self.assertTrue(started.wait(2.0))

            finish.set()
            deadline = time.monotonic() + 2.0
            while provider.lyrics_store.is_lookup_pending("Song", "Artist") and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertFalse(provider.lyrics_store.is_lookup_pending("Song", "Artist"))

            with patch("api.lyrics.time.monotonic", return_value=time.monotonic() + 3600.0):
                match, pending, started_again = provider._ensure_lyrics_lookup_started("Song", "Artist")
            self.assertEqual(match, LyricsMatch((), (), ""))
            self.assertFalse(pending)
            self.assertFalse(started_again)
            self.assertEqual(searches, ["Song"])

            with patch("api.lyrics.threading.Thread"):
                _other_match, other_pending, other_started = provider._ensure_lyrics_lookup_started("Next Song", "Artist")
            self.assertTrue(other_pending)
            self.assertTrue(other_started)

    def test_provider_requests_throttled_unit_enrichment_for_cached_line_lyrics(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda _key, default=None: default

        class RecordingStore(FakeLyricsStore):
            def __init__(self):
                self.enrich_calls = []

            def resolve_cached_or_local(self, *_args):
                return LyricsMatch((TimedLyricLine(1.0, "你好"),), ("你好",), "local:artist - song.lrc")

            def enrich_in_background(self, *args):
                self.enrich_calls.append(args)
                return False

        provider.lyrics_store = RecordingStore()
        match, pending, started = provider._ensure_lyrics_lookup_started("Song", "Artist")

        self.assertEqual(match.timed_lyrics[0].text, "你好")
        self.assertFalse(pending or started)
        self.assertEqual(provider.lyrics_store.enrich_calls, [("Song", "Artist", "", 0.0, True)])

    def test_provider_partial_word_cache_keeps_seeking_until_the_song_has_coverage(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda _key, default=None: default
        measured = TimedLyricLine(1, "Keep singing", (
            TimedLyricUnit(1, 1.5, "Keep "), TimedLyricUnit(1.5, 2, "singing")), "unit")
        ordinary = TimedLyricLine(4, "The rest of the verse still needs its real word times")
        class RecordingStore(FakeLyricsStore):
            def __init__(self):
                self.enrich_calls = []
                self.rows = (measured, ordinary)
            def resolve_cached_or_local(self, *_args):
                return LyricsMatch(self.rows, tuple(row.text for row in self.rows), "netease")
            def enrich_in_background(self, *args):
                self.enrich_calls.append(args)
                return False
        provider.lyrics_store = RecordingStore()
        result, pending, started = provider._ensure_lyrics_lookup_started("Song", "Artist")
        self.assertIs(result.timed_lyrics[0], measured)
        self.assertFalse(pending or started)
        self.assertEqual(provider.lyrics_store.enrich_calls, [("Song", "Artist", "", 0.0, True)])
        provider.lyrics_store.rows = (measured,)
        provider._ensure_lyrics_lookup_started("Song", "Artist")
        self.assertEqual(len(provider.lyrics_store.enrich_calls), 1)

    def test_provider_pending_then_empty_result_feeds_fullscreen_no_lyrics_target(self):
        from local.music_surface_window import MusicSurfaceWindow

        with tempfile.TemporaryDirectory() as root:
            provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
            provider.lyrics_store = LocalLyricsStore(Path(root))
            provider.settings_getter = lambda _key, default=None: default
            started_lookup = threading.Event()
            complete_lookup = threading.Event()

            def empty_lookup(*_args):
                started_lookup.set()
                complete_lookup.wait(2.0)
                return LyricsMatch((), (), "")

            provider.lyrics_store._resolve_online = empty_lookup
            _match, pending, started = provider._ensure_lyrics_lookup_started("Song", "Artist")
            self.assertTrue(pending and started)
            self.assertTrue(started_lookup.wait(2.0))
            owner = SimpleNamespace(track_title="Song", lyrics=["查找歌词中"], timed_lyrics=[],
                                    no_lyrics_text="暂无歌词", enable_dual_line_lyrics=False,
                                    music_intro_cleanup_enabled=True)
            surface = MusicSurfaceWindow.__new__(MusicSurfaceWindow)
            surface.owner = owner
            surface._fullscreen = True
            surface._display_lyrics_cache_key = None
            surface._display_lyrics_cache = None
            self.assertFalse(surface._no_lyrics_cover_target())

            complete_lookup.set()
            deadline = time.monotonic() + 2.0
            while provider.lyrics_store.is_lookup_pending("Song", "Artist") and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertFalse(provider.lyrics_store.is_lookup_pending("Song", "Artist"))
            match, pending, started = provider._ensure_lyrics_lookup_started("Song", "Artist")
            lyrics = match.lyrics or (("查找歌词中",) if pending else ("暂无歌词",))
            owner.lyrics = list(lyrics)

            self.assertEqual(lyrics, ("暂无歌词",))
            self.assertFalse(pending or started)
            self.assertTrue(surface._no_lyrics_cover_target())

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

    def test_run_cancels_timed_out_future_to_prevent_late_seek(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._loop = object()

        class TimedOutFuture:
            cancelled = False

            def result(self, *, timeout):
                self.timeout = timeout
                raise FutureTimeoutError()

            def cancel(self):
                self.cancelled = True
                return True

        future = TimedOutFuture()
        with patch("api.providers.asyncio.run_coroutine_threadsafe", return_value=future):
            self.assertIsNone(provider._run("seek coroutine"))

        self.assertEqual(future.timeout, 1.4)
        self.assertTrue(future.cancelled)

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

    def test_configured_player_seek_uses_verified_progress_control_before_gsmtc(self):
        for adapter in ("netease", "netease_betterncm", "qq"):
            with self.subTest(adapter=adapter):
                provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
                base = empty_visual_data()
                provider._loop = object()
                provider._last_data = replace(
                    base,
                    track=replace(base.track, title="Song", artist="Artist", track_id="track", duration_seconds=200.0),
                )
                provider._last_ready_at = 0.0
                provider._local_seek_position_seconds = None
                provider._local_seek_at = 0.0
                provider._local_seek_track_id = ""
                provider._music_player_adapter = adapter
                calls = []
                reader = SimpleNamespace(
                    seek_playback_position=lambda position, duration, title="", artist="": calls.append(
                        ("player", position, duration, title, artist)
                    ) or True,
                )
                provider._player_position_reader_for = lambda selected: reader
                provider._run = lambda _coro: calls.append(("gsmtc",)) or True
                provider._seek_position = lambda _position: None
                provider._request_immediate_media_refresh = lambda: calls.append(("refresh",))

                self.assertTrue(provider.seek_playback_position(80.0))
                self.assertEqual(calls[0], ("player", 80.0, 200.0, "Song", "Artist"))
                self.assertNotIn(("gsmtc",), calls)
                self.assertAlmostEqual(provider._local_seek_position_seconds, 80.0)

    def test_configured_player_seek_falls_back_to_gsmtc(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        base = empty_visual_data()
        provider._loop = object()
        provider._last_data = replace(
            base,
            track=replace(base.track, title="Song", artist="Artist", track_id="track", duration_seconds=240.0),
        )
        provider._last_ready_at = 0.0
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._music_player_adapter = "netease"
        calls = []
        reader = SimpleNamespace(
            seek_playback_position=lambda position, duration, title="", artist="": calls.append(
                ("player", position, duration, title, artist)
            ) or False,
            verify_playback_position=lambda *_args, **_kwargs: False,
        )

        async def verified_gsmtc_seek(position, **kwargs):
            calls.append(("gsmtc-verified", position, kwargs))
            return True

        provider._player_position_reader_for = lambda adapter: reader
        provider._seek_position_verified = verified_gsmtc_seek
        provider._run = lambda coro: asyncio.run(coro)
        provider._request_immediate_media_refresh = lambda: calls.append(("refresh",))

        self.assertTrue(provider.seek_playback_position(72.0))
        self.assertEqual(calls[0], ("player", 72.0, 240.0, "Song", "Artist"))
        self.assertEqual(calls[1][0:2], ("gsmtc-verified", 72.0))
        self.assertEqual(calls[-1], ("refresh",))
        self.assertEqual(provider._local_seek_track_id, "track")

    def test_configured_player_gsmtc_ack_without_timeline_change_is_not_success(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        base = empty_visual_data()
        provider._last_data = replace(
            base,
            track=replace(base.track, title="Song", artist="Artist", track_id="track", duration_seconds=240.0),
        )
        provider._loop = object()
        provider._music_player_adapter = "netease"
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._player_position_reader_for = lambda _adapter: SimpleNamespace(
            seek_playback_position=lambda *_args, **_kwargs: False,
            verify_playback_position=lambda *_args, **_kwargs: False,
        )
        session = FakeSession("cloudmusic.exe", "Song", "Artist", position=16.0, duration=240.0)

        async def get_session():
            return session

        provider._get_session = get_session
        provider._run = lambda coro: asyncio.run(coro)
        refreshes = []
        provider._request_immediate_media_refresh = lambda: refreshes.append(True)

        async def immediate_sleep(_seconds):
            return None

        with patch("api.providers.asyncio.sleep", immediate_sleep):
            self.assertFalse(provider.seek_playback_position(72.0, expected_track_id="track"))

        self.assertEqual(session.seek_requests, [720_000_000])
        self.assertIsNone(provider._local_seek_position_seconds)
        self.assertEqual(refreshes, [])

    def test_configured_player_gsmtc_timeline_readback_commits_seek(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        base = empty_visual_data()
        provider._last_data = replace(
            base,
            track=replace(base.track, title="Song", artist="Artist", track_id="track", duration_seconds=240.0),
        )
        provider._loop = object()
        provider._music_player_adapter = "netease"
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._player_position_reader_for = lambda _adapter: SimpleNamespace(
            seek_playback_position=lambda *_args, **_kwargs: False,
            verify_playback_position=lambda *_args, **_kwargs: False,
        )

        class MovingSession(FakeSession):
            async def try_change_playback_position_async(self, requested):
                self.seek_requests.append(int(requested))
                self.timeline.position = FakeTimeSpan(requested / 10_000_000)
                return True

        session = MovingSession("cloudmusic.exe", "Song", "Artist", position=16.0, duration=240.0)

        async def get_session():
            return session

        provider._get_session = get_session
        provider._run = lambda coro: asyncio.run(coro)
        provider._request_immediate_media_refresh = lambda: None

        async def immediate_sleep(_seconds):
            return None

        with patch("api.providers.asyncio.sleep", immediate_sleep):
            self.assertTrue(provider.seek_playback_position(72.0, expected_track_id="track"))

        self.assertAlmostEqual(provider._local_seek_position_seconds, 72.0)

    def test_verified_timeline_after_physical_click_skips_second_gsmtc_seek(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        base = empty_visual_data()
        provider._last_data = replace(
            base,
            track=replace(base.track, title="Song", artist="Artist", track_id="track", duration_seconds=240.0),
        )
        provider._loop = object()
        provider._music_player_adapter = "netease"
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        reader = SimpleNamespace(
            seek_playback_position=lambda *_args, **_kwargs: False,
            last_physical_seek_attempted=True,
            verify_playback_position=lambda *_args, **_kwargs: False,
        )
        provider._player_position_reader_for = lambda _adapter: reader
        session = FakeSession("cloudmusic.exe", "Song", "Artist", position=72.0, duration=240.0)

        async def get_session():
            return session

        provider._get_session = get_session
        provider._run = lambda coro: asyncio.run(coro)
        provider._request_immediate_media_refresh = lambda: None

        self.assertTrue(provider.seek_playback_position(72.0, expected_track_id="track"))
        self.assertEqual(session.seek_requests, [])
        self.assertAlmostEqual(provider._local_seek_position_seconds, 72.0)

    def test_seek_rejects_changed_track_before_and_after_uia_call(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        base = empty_visual_data()
        original = replace(base, track=replace(base.track, title="Song", artist="Artist", track_id="track", duration_seconds=200.0))
        provider._last_data = original
        provider._loop = None
        provider._music_player_adapter = "qq"
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        calls = []

        def seek(*_args, **_kwargs):
            calls.append("uia")
            provider._last_data = replace(
                original,
                track=replace(original.track, track_id="new-track", title="Next Song"),
            )
            return True

        provider._player_position_reader_for = lambda _adapter: SimpleNamespace(seek_playback_position=seek)
        provider._request_immediate_media_refresh = lambda: calls.append("refresh")

        self.assertFalse(provider.seek_playback_position(72.0, expected_track_id="not-this-track"))
        self.assertEqual(calls, [])
        self.assertFalse(provider.seek_playback_position(72.0, expected_track_id="track"))
        self.assertEqual(calls, ["uia"])
        self.assertIsNone(provider._local_seek_position_seconds)

    def test_system_seek_uses_gsmtc_before_any_player_fallback(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._loop = object()
        provider._last_data = None
        provider._last_ready_at = 0.0
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._music_player_adapter = "system"
        calls = []

        async def gsmtc_seek(position):
            calls.append(("gsmtc", position))
            return True

        provider._seek_position = gsmtc_seek
        provider._run = lambda coro: asyncio.run(coro)
        provider._player_position_reader_for = lambda adapter: calls.append(("player", adapter))
        provider._request_immediate_media_refresh = lambda: calls.append(("refresh",))

        self.assertTrue(provider.seek_playback_position(12.0))
        self.assertEqual(calls[0], ("gsmtc", 12.0))
        self.assertNotIn(("player", "system"), calls)

    def test_verified_local_seek_invalidates_old_lyric_row_reader_sample(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._last_data = None
        provider._music_player_adapter = "netease"
        resets = []
        provider._player_position_reader_for = lambda _adapter: SimpleNamespace(reset=lambda: resets.append(True))

        provider._remember_local_seek_for_track("track", 24.0, 120.0)

        self.assertEqual(resets, [True])
        self.assertEqual(provider._local_seek_track_id, "track")
        self.assertEqual(provider._local_seek_position_seconds, 24.0)

    def test_verified_seeks_keep_distinct_event_times_through_cached_projection(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        base = empty_visual_data("Song", "Artist")
        provider._last_data = replace(
            base,
            track=replace(base.track, track_id="track", duration_seconds=120.0, is_playing=True),
        )
        provider._music_player_adapter = "system"

        with patch("api.providers.time.monotonic", side_effect=(10.0, 11.0)):
            provider._remember_local_seek_for_track("track", 24.0, 120.0)
            first = provider._last_data.track.position_sampled_at
            first_cached = provider._project_visual_position(provider._last_data, now=10.5, anchor_at=10.0)
            self.assertEqual(first_cached.track.position_sampled_at, first)
            provider._remember_local_seek_for_track("track", 48.0, 120.0)
            second = provider._last_data.track.position_sampled_at

        self.assertEqual(first, 10.0)
        self.assertEqual(second, 11.0)
        self.assertNotEqual(first, second)

    def test_failed_seek_does_not_publish_local_position_or_refresh(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        base = empty_visual_data()
        provider._loop = object()
        provider._last_data = replace(base, track=replace(base.track, duration_seconds=200.0))
        provider._last_ready_at = 0.0
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._music_player_adapter = "qq"
        calls = []
        reader = SimpleNamespace(
            seek_playback_position=lambda *_args, **_kwargs: False,
            verify_playback_position=lambda *_args, **_kwargs: False,
        )

        async def failed_seek(_position, **_kwargs):
            return False

        provider._player_position_reader_for = lambda adapter: reader
        provider._seek_position_verified = failed_seek
        provider._run = lambda coro: asyncio.run(coro)
        provider._request_immediate_media_refresh = lambda: calls.append("refresh")

        self.assertFalse(provider.seek_playback_position(64.0))
        self.assertIsNone(provider._local_seek_position_seconds)
        self.assertEqual(calls, [])

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

    def test_foreground_window_title_cannot_override_system_metadata_or_thumbnail(self):
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
        thumbnail_calls = []
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(
            0, result=thumbnail_calls.append(_track_key) or b"stale-thumbnail",
        )

        async def fake_get_session():
            return FakeSession("cloudmusic.exe", "butterflies", "Denise Julia", True, 161.0, 212.79)

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(data.track.title, "butterflies")
        self.assertEqual(data.track.artist, "Denise Julia")
        self.assertEqual(data.track.track_id != "", True)
        self.assertAlmostEqual(data.track.position_seconds, 161.0, delta=0.25)
        self.assertEqual(data.track.album_art_bytes, b"stale-thumbnail")
        self.assertEqual(thumbnail_calls, [data.track.track_id])

    def test_same_track_art_selection_keeps_the_sharper_cover_when_focus_changes(self):
        def png(side, color):
            image = QImage(side, side, QImage.Format.Format_RGB32)
            image.fill(color)
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            image.save(buffer, "PNG")
            return bytes(data)

        sharp = png(320, 0xFF203060)
        small = png(48, 0xFF806020)
        self.assertEqual(WindowsMediaProvider._preferred_album_art_bytes(small, sharp), sharp)
        self.assertEqual(WindowsMediaProvider._preferred_album_art_bytes(sharp, small), sharp)
        # More bytes at identical dimensions do not prove the replacement is
        # visually better; preserve the current same-track image on a tie.
        self.assertEqual(WindowsMediaProvider._preferred_album_art_bytes(sharp, sharp + b"padding"), sharp)

    def test_netease_small_thumbnail_starts_full_size_lookup_and_upgrades_same_track(self):
        def png(side):
            image = QImage(side, side, QImage.Format.Format_RGB32)
            image.fill(0xFF203060)
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            image.save(buffer, "PNG")
            return bytes(data)

        thumbnail = png(1199)
        original = png(2560)
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
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
        provider.netease_position = None
        provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_music_player_mode": "netease",
            "v2_enable_player_progress_plugin": False,
            "v2_player_progress_auto_detect": False,
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: False
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=thumbnail)
        provider._colors_from_art = lambda _art, fallback, secondary: (fallback, secondary)
        provider._get_session = lambda: asyncio.sleep(
            0, result=FakeSession("cloudmusic.exe", "Song", "Artist", True, 12.0, 240.0)
        )
        requested = []
        provider._schedule_current_netease_art_lookup = lambda *args, **kwargs: requested.append((args, kwargs)) or True

        first = asyncio.run(provider._read_visual_data())
        self.assertEqual(first.track.album_art_bytes, thumbnail)
        self.assertEqual(first.track.album_art_source, "thumbnail")
        self.assertEqual(len(requested), 1)
        self.assertEqual(requested[0][0][1:], ("Song", "Artist"))
        self.assertEqual(requested[0][1], {"album_title": "", "duration_seconds": 240.0})

        provider._last_data = first
        provider._current_art_url_by_track_id = {first.track.track_id: "https://example.test/original.jpg"}
        provider._queue_album_art_bytes = lambda _track: original
        second = asyncio.run(provider._read_visual_data())
        self.assertEqual(second.track.track_id, first.track.track_id)
        self.assertEqual(second.track.album_art_bytes, original)
        self.assertEqual(second.track.album_art_source, "original")
        self.assertEqual(len(requested), 1)

    def test_foreground_title_change_corrects_cached_track_immediately(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        base = empty_visual_data()
        cached = replace(
            base,
            track=replace(
                base.track,
                title="Old Song",
                artist="Old Artist",
                lyrics=(),
                timed_lyrics=(),
                lyrics_source="",
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

    def test_foreground_title_change_does_not_clear_loaded_lyrics_from_cache(self):
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
        provider._last_foreground_snapshot = {"title": "Transient Window Title - Noise"}
        provider.netease_position = SimpleNamespace(reset=lambda: None)
        provider.qq_position = None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: True

        corrected = provider._foreground_correct_cached_data(cached, time.monotonic())

        self.assertEqual(corrected.track.track_id, "old-track")
        self.assertEqual(corrected.track.title, "Old Song")
        self.assertEqual(corrected.track.lyrics, ("old lyric",))
        self.assertEqual(corrected.track.timed_lyrics, (TimedLyricLine(12.0, "old lyric"),))

    def test_recent_click_without_position_does_not_claim_lyric_click_anchor(self):
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
        self.assertEqual(reader.immediate_calls, [])
        self.assertNotEqual(data.track.position_source, "netease-uia-progress")

    def test_netease_lyric_click_without_position_does_not_force_progress_anchor_in_grace_period(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeLyricClickProgressReader()
        session = FakeSession("cloudmusic.exe", "Song", "Artist", True, 96.0, 240.0)
        base = empty_visual_data()
        timed = FakeTimedLyricsStore().resolve_cached_or_local("Song", "Artist").timed_lyrics
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
                timed_lyrics=timed,
            ),
        )
        now = time.monotonic()
        provider._last_ready_at = now
        provider._source_app_foreground_at = now
        provider._source_app_recent_click_at = now
        provider._source_app_recent_click_pos = None
        provider._source_app_recent_pointer_at = 0.0
        provider._source_app_recent_pointer_pos = None
        provider._global_recent_click_at = 0.0
        provider._global_recent_click_pos = None
        provider._global_recent_pointer_at = 0.0
        provider._global_recent_pointer_pos = None
        provider._global_pointer_lock = None
        provider._last_raw_timeline_track_id = "same-track"
        provider._last_raw_timeline_position = 96.0
        provider._last_raw_timeline_at = now
        provider._local_seek_position_seconds = None
        provider._local_seek_at = 0.0
        provider._local_seek_track_id = ""
        provider._art_cache = {}
        provider._player_anchor_fast_refresh_until = 0.0
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
            return session

        provider._get_session = fake_get_session

        data = asyncio.run(provider._read_visual_data())

        self.assertIsNotNone(data)
        self.assertEqual(reader.immediate_calls, [])
        self.assertNotEqual(data.track.position_source, "netease-uia-progress")
        self.assertEqual(getattr(provider, "_player_anchor_fast_refresh_until", 0.0), 0.0)

    def test_dual_line_enrichment_is_disabled_by_default(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider.settings_getter = lambda key, default=None: default

        self.assertFalse(provider._dual_line_lyrics_enabled())

    def test_qq_syncs_once_when_delayed_lyrics_finish_loading(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        reader = FakeQQPositionReader()
        lyrics_store = AppearingLyricsStore()
        base = empty_visual_data()
        old_track = replace(
            base.track,
            title="Song",
            artist="Artist",
            source_app="QQMusic.exe",
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
        provider._lyrics_loaded_sync_track_ids = set()
        provider._lyrics_found_progress_retry_track_id = ""
        provider._lyrics_found_progress_retry_not_before = 0.0
        provider._lyrics_found_progress_retry_until = 0.0
        provider._netease_upcoming_prefetch_keys = set()
        provider.netease_position = None
        provider.qq_position = reader
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_enable_player_progress_plugin": False,
            "v2_player_progress_auto_detect": False,
            "v2_music_player_mode": "qq",
            "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda _foreground, _now: None
        provider._is_source_app_foreground = lambda _source_app, _raw_source_app: False
        provider._read_thumbnail = lambda _properties, _track_key: asyncio.sleep(0, result=b"")

        async def fake_get_session():
            return FakeSession("QQMusic.exe", "Song", "Artist", True, 0.0, 240.0)

        provider._get_session = fake_get_session

        with patch("api.providers.time.monotonic", return_value=100.0):
            first = asyncio.run(provider._read_visual_data())
        provider._last_data = first
        provider._last_ready_at = 100.0
        reader.immediate_calls.clear()
        with patch("api.providers.time.monotonic", return_value=101.0):
            second = asyncio.run(provider._read_visual_data())

        self.assertEqual(reader.immediate_calls, [])
        provider._last_data = second
        provider._last_ready_at = 101.0
        with patch("api.providers.time.monotonic", return_value=106.1):
            third = asyncio.run(provider._read_visual_data())

        self.assertEqual(reader.immediate_calls, [])
        self.assertNotEqual(third.track.position_source, "qq-uia-progress")

class ProviderArtworkUpgradeRecoveryTests(unittest.TestCase):
    raw_url = "https://p1.music.126.net/cover/123.jpg?param=500y500"
    upgraded_url = "https://p1.music.126.net/cover/123.jpg?param=2560y2560"

    @staticmethod
    def png(side):
        image = QImage(side, side, QImage.Format.Format_RGB32)
        image.fill(0xFF203060)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        return bytes(data)

    def provider(self):
        provider = WindowsMediaProvider.__new__(WindowsMediaProvider)
        provider._queue_art_cache = {}
        provider._queue_art_disk_path = lambda _url: None
        provider._persist_queue_art = lambda *_args: None
        return provider

    def test_player_tiny_target_cache_and_disk_retry_without_losing_preview(self):
        provider = self.provider()
        tiny, original = self.png(100), self.png(1417)
        jobs = []

        class ManualThread:
            def __init__(self, *, target, **_kwargs):
                self.target = target

            def start(self):
                jobs.append(self.target)

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit):
                return original

        track = SimpleNamespace(album_art_url=self.upgraded_url, album_art_bytes=tiny)
        provider._queue_art_cache[self.upgraded_url] = tiny
        with tempfile.TemporaryDirectory() as root, patch("api.providers.threading.Thread", ManualThread):
            disk_path = Path(root) / "cover.img"
            disk_path.write_bytes(tiny)
            provider._queue_art_disk_path = lambda _url: disk_path
            with patch("api.providers.time.monotonic", return_value=100.0):
                self.assertEqual(provider._queue_album_art_bytes(track), tiny)
                self.assertNotIn(self.upgraded_url, provider._queue_art_cache)
                self.assertFalse(disk_path.exists())
                self.assertEqual(provider._queue_album_art_bytes(track), tiny)
                self.assertEqual(jobs, [])
            with patch("api.providers.time.monotonic", return_value=130.1):
                self.assertEqual(provider._queue_album_art_bytes(track), tiny)
                self.assertEqual(provider._queue_album_art_bytes(track), tiny)
                self.assertEqual(len(jobs), 1)
                with patch("api.providers.urlopen", return_value=Response()):
                    jobs.pop(0)()
                self.assertEqual(provider._queue_album_art_bytes(track), original)
                self.assertFalse(provider._album_art_url_is_preview(self.upgraded_url, original))
            self.assertEqual(jobs, [])

    def test_known_500_preview_stays_pending_until_upgraded_source_arrives(self):
        provider = self.provider()
        preview, original = self.png(500), self.png(1417)
        provider._queue_art_cache[self.raw_url] = preview
        scheduled = []
        provider._schedule_queue_album_art_prefetch = lambda url: scheduled.append(url) or True
        track = SimpleNamespace(album_art_url=self.raw_url, album_art_bytes=preview)
        self.assertEqual(provider._queue_album_art_bytes(track), preview)
        self.assertEqual(scheduled, [self.upgraded_url])
        self.assertTrue(provider._album_art_url_is_preview(self.raw_url, preview))
        provider._current_art_lookup_lock = threading.Lock()
        provider._current_art_url_by_track_id = {"track": self.raw_url}
        self.assertEqual(
            provider._current_artwork_lookup_state("track", preview, eligible=True, online_art=preview, source="thumbnail"),
            "pending",
        )
        provider._queue_art_cache[self.upgraded_url] = original
        self.assertEqual(provider._queue_album_art_bytes(track), original)
        self.assertFalse(provider._album_art_url_is_preview(self.raw_url, original))
        self.assertEqual(
            provider._current_artwork_lookup_state("track", original, eligible=True, online_art=original, source="netease-original"),
            "ready",
        )

    def test_completed_smaller_original_keeps_best_variant_without_permanent_pending(self):
        provider = self.provider()
        preview, original = self.png(800), self.png(640)
        provider._queue_art_cache = {self.raw_url: preview, self.upgraded_url: original}
        scheduled = []
        provider._schedule_queue_album_art_prefetch = lambda url: scheduled.append(url) or True
        track = SimpleNamespace(album_art_url=self.raw_url, album_art_bytes=preview)
        selected = provider._queue_album_art_bytes(track)
        self.assertEqual(selected, preview)
        self.assertEqual(scheduled, [])
        self.assertFalse(provider._album_art_url_is_preview(self.raw_url, selected))
        provider._current_art_lookup_lock = threading.Lock()
        provider._current_art_url_by_track_id = {"track": self.raw_url}
        self.assertEqual(provider._current_artwork_lookup_state(
            "track", selected, eligible=True, online_art=selected, source="thumbnail"), "ready")

    def test_visual_handoff_preserves_known_500_preview_provenance(self):
        provider = self.provider()
        preview, original = self.png(500), self.png(1417)
        provider.fallback = EmptyIslandDataProvider(empty_visual_data())
        provider.lyrics_store = FakeLyricsStore()
        provider._last_data = None
        provider._last_ready_at = provider._source_app_foreground_at = 0.0
        provider._last_raw_timeline_track_id = provider._local_seek_track_id = ""
        provider._last_raw_timeline_position = provider._local_seek_position_seconds = None
        provider._last_raw_timeline_at = provider._local_seek_at = 0.0
        provider._art_cache = {}
        provider.netease_position = provider.qq_position = None
        provider.plugin_provider = SimpleNamespace(plugins=lambda: ())
        provider.message_detector = SimpleNamespace(messages=lambda: ())
        provider.settings_getter = lambda key, default=None: {
            "v2_music_player_mode": "netease", "v2_enable_player_progress_plugin": False,
            "v2_player_progress_auto_detect": False, "v2_enable_dual_line_lyrics": False,
        }.get(key, default)
        provider._record_source_app_pointer_state = lambda *_args: None
        provider._is_source_app_foreground = lambda *_args: False
        provider._read_thumbnail = lambda *_args: asyncio.sleep(0, result=b"")
        provider._colors_from_art = lambda _art, fallback, secondary: (fallback, secondary)
        provider._get_session = lambda: asyncio.sleep(0, result=FakeSession("cloudmusic.exe", "Song", "Artist", True, 12, 240))
        provider._schedule_current_netease_art_lookup = lambda *_args, **_kwargs: False
        provider._schedule_queue_album_art_prefetch = lambda _url: False
        def online_art(track_key):
            provider._current_art_url_by_track_id = {track_key: self.raw_url}
            provider._current_art_lookup_sources = {track_key: "netease"}
            return provider._queue_album_art_bytes(SimpleNamespace(album_art_url=self.raw_url))

        provider._current_online_album_art_bytes = online_art
        provider._queue_art_cache[self.raw_url] = preview
        first = asyncio.run(provider._read_visual_data())
        self.assertEqual(first.track.album_art_bytes, preview)
        self.assertEqual(first.track.album_art_source, "thumbnail")
        self.assertNotEqual(first.track.album_art_lookup_state, "ready")
        provider._last_data = first
        provider._queue_art_cache[self.upgraded_url] = original
        second = asyncio.run(provider._read_visual_data())
        self.assertEqual(second.track.track_id, first.track.track_id)
        self.assertEqual(second.track.album_art_bytes, original)
        self.assertEqual(second.track.album_art_source, "netease-original")
        self.assertEqual(second.track.album_art_lookup_state, "ready")

    def test_unknown_600_source_original_retains_reference_completion(self):
        provider = self.provider()
        original = self.png(600)
        self.assertFalse(provider._album_art_url_is_preview("https://example.test/cover.jpg?param=500y500", original))
        self.assertEqual(
            provider._current_artwork_lookup_state("track", original, eligible=True, source="original"),
            "ready",
        )


if __name__ == "__main__":
    unittest.main()
