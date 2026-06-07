from __future__ import annotations

import asyncio
import ctypes
import hashlib
import io
import math
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import replace
from typing import Literal
from urllib.request import Request, urlopen

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage

from api.audio_analyzer import LiveAudioAnalyzer
from api.lyrics import LocalLyricsStore
from api.message_detector import WindowsMessageDetector
from api.models import AlbumQueueEntry, AudioFrame, ColorToken, IslandVisualData, TrackData
from api.netease_position import NeteasePositionReader, QQPositionReader
from api.plugin_provider import RealPluginProvider


PlaybackCommand = Literal["previous", "toggle_play_pause", "next"]
PROJECTED_POSITION_SOURCES = {
    "windows-media",
    "local-estimate",
    "local-background",
    "local-seek",
    "interactive",
    "interactive-hold",
    "netease-uia-progress",
    "netease-uia-lyrics",
    "qq-uia-progress",
}
STABILIZED_LOCAL_POSITION_SOURCES = {"local-estimate", "local-background", "local-seek", "interactive-hold"}
STABILIZED_PLAYER_POSITION_SOURCES = {"netease-uia-progress", "qq-uia-progress"}

MediaManager = None
PlaybackStatus = None
Buffer = None
DataReader = None
InputStreamOptions = None
WINSdk_IMPORT_ATTEMPTED = False
WINSdk_IMPORT_ERROR = ""


def empty_visual_data(title: str = "暂无媒体", detail: str = "打开音乐播放器后会自动同步", source_app: str = "system") -> IslandVisualData:
    key_raw = f"empty\0{title}\0{detail}\0{source_app}"
    return IslandVisualData(
        track=TrackData(
            title=title,
            artist=detail,
            lyrics=("暂无歌词",),
            album_color=ColorToken(58, 68, 88),
            secondary_color=ColorToken(34, 44, 62),
            audio_frames=(),
            source_app=source_app,
            is_playing=False,
            duration_seconds=0.0,
            position_seconds=0.0,
            timed_lyrics=(),
            lyrics_source="",
            track_id=hashlib.sha1(key_raw.encode("utf-8", errors="ignore")).hexdigest()[:16],
        ),
        messages=(),
        plugins=(),
    )


EMPTY_VISUAL_DATA = empty_visual_data()


MISSING_WINSDK_VISUAL_DATA = empty_visual_data(
    title="媒体依赖缺失",
    detail="运行 setup-env.bat 后重新启动",
    source_app="dependency",
)


NO_SESSION_VISUAL_DATA = empty_visual_data(
    title="暂无媒体",
    detail="播放一首歌后会自动同步",
    source_app="windows-media",
)


class EmptyIslandDataProvider:
    def __init__(self, visual_data: IslandVisualData | None = None):
        self.visual_data = visual_data or EMPTY_VISUAL_DATA

    def get_visual_data(self) -> IslandVisualData:
        return self.visual_data

    def get_audio_frame(self) -> AudioFrame | None:
        return None

    def send_playback_command(self, command: PlaybackCommand) -> bool:
        return False

    def seek_playback_position(self, position_seconds: float) -> bool:
        return False


class DependencyMissingProvider(EmptyIslandDataProvider):
    def __init__(self):
        super().__init__(MISSING_WINSDK_VISUAL_DATA)


class NoMediaSessionProvider(EmptyIslandDataProvider):
    def __init__(self):
        super().__init__(NO_SESSION_VISUAL_DATA)


def _ensure_winsdk_loaded() -> bool:
    global MediaManager, PlaybackStatus, Buffer, DataReader, InputStreamOptions, WINSdk_IMPORT_ATTEMPTED, WINSdk_IMPORT_ERROR
    if WINSdk_IMPORT_ATTEMPTED:
        return MediaManager is not None
    WINSdk_IMPORT_ATTEMPTED = True
    try:
        from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager
        from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionPlaybackStatus
        from winsdk.windows.storage.streams import Buffer as WinBuffer
        from winsdk.windows.storage.streams import DataReader as WinDataReader
        from winsdk.windows.storage.streams import InputStreamOptions as WinInputStreamOptions
    except ImportError as exc:
        WINSdk_IMPORT_ERROR = str(exc)
        return False
    MediaManager = GlobalSystemMediaTransportControlsSessionManager
    PlaybackStatus = GlobalSystemMediaTransportControlsSessionPlaybackStatus
    Buffer = WinBuffer
    DataReader = WinDataReader
    InputStreamOptions = WinInputStreamOptions
    WINSdk_IMPORT_ERROR = ""
    return True


class WindowsMediaProvider:
    def __init__(self, fallback=None, settings_getter=None):
        if fallback is None:
            fallback = EmptyIslandDataProvider()
        self.fallback = fallback
        self.lyrics_store = LocalLyricsStore()
        self._loop = None
        self._thread = None
        self._manager = None
        self._last_data: IslandVisualData | None = None
        self._last_refresh_at = 0.0
        self._refresh_interval = 0.55
        self._refresh_future = None
        self._art_cache: dict[str, bytes] = {}
        self._queue_art_cache: dict[str, bytes] = {}
        self._provider_status = "starting"
        self._last_ready_at = 0.0
        self.settings_getter = settings_getter or (lambda key, default=None: default)
        self._local_seek_position_seconds: float | None = None
        self._local_seek_at = 0.0
        self._local_seek_track_id = ""
        self._last_foreground_snapshot: dict[str, object] = {}
        self._source_app_foreground = False
        self._source_app_interactive = False
        self._source_app_foreground_at = 0.0
        self._source_app_left_down = False
        self._source_app_left_down_at = 0.0
        self._source_app_left_down_pos: tuple[int, int] | None = None
        self._source_app_left_down_started_in_foreground = False
        self._source_app_recent_click_at = 0.0
        self._source_app_recent_click_pos: tuple[int, int] | None = None
        self._source_app_recent_pointer_at = 0.0
        self._global_left_down = False
        self._global_left_down_at = 0.0
        self._global_left_down_pos: tuple[int, int] | None = None
        self._global_recent_click_at = 0.0
        self._global_recent_click_pos: tuple[int, int] | None = None
        self._global_recent_pointer_at = 0.0
        self._global_recent_pointer_pos: tuple[int, int] | None = None
        self._global_pointer_lock = threading.Lock()
        self._global_pointer_stop = threading.Event()
        self._global_pointer_thread = None
        self._raw_source_app_user_model_id = ""
        self._music_player_adapter = "system"
        self._last_confirmed_music_player_adapter = "system"
        self._last_confirmed_music_player_adapter_until = 0.0
        self._last_confirmed_music_player_source_key = ""
        self._recent_skip_command_at = 0.0
        self._player_anchor_fast_refresh_until = 0.0
        self._last_raw_timeline_track_id = ""
        self._last_raw_timeline_position: float | None = None
        self._last_raw_timeline_at = 0.0
        self._netease_lyric_sync_track_ids: set[str] = set()
        self._netease_upcoming_prefetch_keys: set[tuple[str, str]] = set()
        self._upcoming_lyric_prefetch_track_ids: set[str] = set()
        self._media_session_count = 0
        self._media_session_candidates: tuple[str, ...] = ()
        self._media_session_error = ""
        self._selected_media_session_index = -1
        self.audio_analyzer: LiveAudioAnalyzer | None = None
        self.netease_position: NeteasePositionReader | None = None
        self.qq_position: QQPositionReader | None = None
        self.plugin_provider = RealPluginProvider(settings_getter)
        self.message_detector = WindowsMessageDetector(settings_getter)
        self._start_loop()

    def get_visual_data(self) -> IslandVisualData:
        now = time.monotonic()
        if self._loop is None:
            return self.fallback.get_visual_data()
        self._record_global_pointer_state(now)
        self._consume_refresh_future(now)
        refresh_interval = self._current_refresh_interval(now)
        if self._last_data is not None and now - self._last_refresh_at < refresh_interval:
            projected = self._project_visual_position(self._last_data, now, self._last_ready_at)
            return replace(projected, messages=self.message_detector.messages(), plugins=self.plugin_provider.plugins())
        self._last_refresh_at = now
        if self._refresh_future is None or self._refresh_future.done():
            try:
                self._refresh_future = asyncio.run_coroutine_threadsafe(self._read_visual_data(), self._loop)
            except Exception:
                self._refresh_future = None
        return self._cached_or_status_visual(now)

    def _current_refresh_interval(self, now: float) -> float:
        if self._music_player_adapter in {"netease", "netease_betterncm", "qq"}:
            fast_until = float(getattr(self, "_player_anchor_fast_refresh_until", 0.0) or 0.0)
            if fast_until > 0.0 and now <= fast_until:
                return 0.12
        if self._music_player_adapter in {"netease", "netease_betterncm", "qq"} and (
            self._source_app_interactive or (self._source_app_foreground_at and now - self._source_app_foreground_at <= 3.5)
        ):
            return 0.14
        return self._refresh_interval

    def _mark_player_anchor_fast_refresh(self, now: float | None = None, duration: float = 1.35):
        anchor_at = time.monotonic() if now is None else float(now)
        try:
            span = max(0.25, min(3.0, float(duration)))
        except (TypeError, ValueError):
            span = 1.35
        self._player_anchor_fast_refresh_until = max(
            float(getattr(self, "_player_anchor_fast_refresh_until", 0.0) or 0.0),
            anchor_at + span,
        )

    def _consume_refresh_future(self, now: float):
        future = self._refresh_future
        if future is None or not future.done():
            return
        self._refresh_future = None
        try:
            data = future.result()
        except Exception:
            data = None
        if data is None:
            return
        data = self._stabilize_incoming_position(data, now)
        data = self._apply_recent_local_seek_to_visual_data(data, now)
        self._last_data = data
        self._last_ready_at = now

    def _stabilize_incoming_position(self, data: IslandVisualData, now: float) -> IslandVisualData:
        previous = self._last_data
        if previous is None:
            return data
        track = data.track
        previous_track = previous.track
        if track.track_id != previous_track.track_id or not track.is_playing:
            return data
        if track.position_source not in STABILIZED_LOCAL_POSITION_SOURCES | STABILIZED_PLAYER_POSITION_SOURCES:
            return data
        projected_previous = self._project_visual_position(previous, now, self._last_ready_at)
        projected_position = float(projected_previous.track.position_seconds)
        if projected_position <= float(track.position_seconds) + 0.05:
            return data
        if track.position_source in STABILIZED_PLAYER_POSITION_SOURCES and projected_position - float(track.position_seconds) > 1.20:
            backstep = projected_position - float(track.position_seconds)
            if self._should_preserve_player_backstep(track, previous_track, backstep):
                return data
        updated = replace(track, position_seconds=projected_position)
        return replace(data, track=updated)

    def _should_preserve_player_backstep(self, track: TrackData, previous_track: TrackData, backstep_seconds: float) -> bool:
        if bool(getattr(track, "source_app_foreground", False)) or bool(getattr(track, "position_interactive", False)):
            return True
        duration = max(float(getattr(track, "duration_seconds", 0.0) or 0.0), float(getattr(previous_track, "duration_seconds", 0.0) or 0.0))
        seek_threshold = max(8.0, min(24.0, duration * 0.05))
        return float(backstep_seconds) >= seek_threshold

    def _recent_local_seek_age(self, now: float) -> float | None:
        if self._local_seek_position_seconds is None:
            return None
        seek_at = float(self._local_seek_at or 0.0)
        if seek_at <= 0.0:
            return None
        age = now - seek_at
        if age < 0.0 or age > 4.5:
            return None
        return age

    def _project_recent_local_seek_position(self, duration_seconds: float, is_playing: bool, now: float) -> float:
        age = self._recent_local_seek_age(now)
        if age is None:
            return 0.0
        position = max(0.0, float(self._local_seek_position_seconds or 0.0))
        if is_playing:
            position += age
        duration = max(0.0, float(duration_seconds or 0.0))
        if duration > 0.0:
            position = min(duration, position)
        return position

    def _apply_recent_local_seek_to_visual_data(self, data: IslandVisualData, now: float) -> IslandVisualData:
        age = self._recent_local_seek_age(now)
        if age is None:
            return data
        track = data.track
        track_id = str(track.track_id or "")
        local_track_id = str(self._local_seek_track_id or "")
        if local_track_id and local_track_id != track_id:
            return data
        if not local_track_id and track_id:
            self._local_seek_track_id = track_id
        position = self._project_recent_local_seek_position(track.duration_seconds, track.is_playing, now)
        source = str(track.position_source or "")
        player_seek_sources = STABILIZED_PLAYER_POSITION_SOURCES | {"netease-uia-lyrics", "qq-uia-lyrics"}
        if source in player_seek_sources and abs(float(track.position_seconds) - position) <= 2.5:
            return data
        updated_track = replace(
            track,
            position_seconds=position,
            position_source="local-seek",
            position_confidence=max(float(track.position_confidence), 0.68),
            position_interactive=True,
        )
        return replace(data, track=updated_track)

    def _project_visual_position(self, data: IslandVisualData, now: float, anchor_at: float) -> IslandVisualData:
        track = data.track
        if not track.is_playing:
            return data
        if track.position_source not in PROJECTED_POSITION_SOURCES:
            return data
        elapsed = max(0.0, now - float(anchor_at or now))
        if elapsed <= 0.01:
            return data
        position = max(0.0, float(track.position_seconds)) + elapsed
        if track.duration_seconds > 0.0:
            position = min(float(track.duration_seconds), position)
        if position <= float(track.position_seconds) + 0.01:
            return data
        return replace(data, track=replace(track, position_seconds=position))

    def _cached_or_status_visual(self, now: float) -> IslandVisualData:
        plugins = self.plugin_provider.plugins()
        messages = self.message_detector.messages()
        if self._provider_status == "missing_winsdk":
            return replace(MISSING_WINSDK_VISUAL_DATA, messages=messages, plugins=plugins)
        if self._provider_status == "no_session":
            grace_seconds = 4.0
            if self._last_data is not None:
                last_track = self._last_data.track
                if last_track.is_playing or self.has_live_audio():
                    grace_seconds = 18.0
                elif str(last_track.title or last_track.artist).strip():
                    grace_seconds = 10.0
            if self._last_data is not None and now - self._last_ready_at < grace_seconds:
                return replace(self._project_visual_position(self._last_data, now, self._last_ready_at), messages=messages, plugins=plugins)
            return replace(NO_SESSION_VISUAL_DATA, messages=messages, plugins=plugins)
        if self._last_data:
            cached = self._foreground_correct_cached_data(self._last_data, now)
            return replace(self._project_visual_position(cached, now, self._last_ready_at), messages=messages, plugins=plugins)
        return replace(self.fallback.get_visual_data(), messages=messages, plugins=plugins)

    def get_audio_frame(self) -> AudioFrame | None:
        audio_analyzer = self._ensure_audio_analyzer_if_requested()
        if audio_analyzer is None:
            return None
        track = self._last_data.track if self._last_data is not None else None
        live_audio = audio_analyzer.has_recent_real_frame()
        if not live_audio:
            return None
        return audio_analyzer.get_frame(
            is_playing=True if track is None else track.is_playing,
            position_seconds=0.0 if track is None else track.position_seconds,
        )

    def has_live_audio(self) -> bool:
        audio_analyzer = self._ensure_audio_analyzer_if_requested()
        return False if audio_analyzer is None else audio_analyzer.has_recent_real_frame()

    def _live_audio_requested(self) -> bool:
        settings_getter = getattr(self, "settings_getter", None)
        if not callable(settings_getter):
            return False
        return bool(settings_getter("v2_music_auto_detect", False) or settings_getter("v2_enable_live_audio_analysis", False))

    def _ensure_audio_analyzer_if_requested(self) -> LiveAudioAnalyzer | None:
        if not self._live_audio_requested():
            return None
        analyzer = getattr(self, "audio_analyzer", None)
        if analyzer is None:
            analyzer = LiveAudioAnalyzer()
            self.audio_analyzer = analyzer
        return analyzer

    def _cold_attachment_diagnostics(self, service: str) -> dict[str, object]:
        return {
            "service_name": service,
            "available": None,
            "refreshing": False,
            "last_error": "not enabled",
            "last_source": "",
            "last_confidence": 0.0,
            "last_lyric_index": -1,
            "last_lyric_text": "",
            "last_sample_age": None,
            "last_progress_probe": {},
        }

    def diagnostics(self) -> dict[str, object]:
        track = self._last_data.track if self._last_data is not None else None
        return {
            "provider_status": self._provider_status,
            "winsdk_loaded": MediaManager is not None,
            "winsdk_import_attempted": WINSdk_IMPORT_ATTEMPTED,
            "winsdk_import_error": WINSdk_IMPORT_ERROR,
            "media_session_count": self._media_session_count,
            "media_session_candidates": self._media_session_candidates,
            "media_session_error": self._media_session_error,
            "selected_media_session_index": self._selected_media_session_index,
            "has_last_data": self._last_data is not None,
            "last_ready_age": round(time.monotonic() - self._last_ready_at, 3) if self._last_ready_at else None,
            "track_title": "" if track is None else track.title,
            "track_artist": "" if track is None else track.artist,
            "track_id": "" if track is None else track.track_id,
            "source_app": "" if track is None else track.source_app,
            "is_playing": False if track is None else track.is_playing,
            "raw_source_app": self._raw_source_app_user_model_id,
            "music_player_mode": self._music_player_mode(),
            "player_progress_plugin_enabled": self._player_progress_plugin_enabled(),
            "player_progress_auto_detect": self._player_progress_auto_detect_enabled(),
            "music_player_adapter": self._music_player_adapter,
            "source_app_foreground": self._source_app_foreground,
            "source_app_interactive": self._source_app_interactive,
            "foreground_process": str(self._last_foreground_snapshot.get("process", "")),
            "foreground_title": str(self._last_foreground_snapshot.get("title", "")),
            "duration_seconds": 0.0 if track is None else track.duration_seconds,
            "position_seconds": 0.0 if track is None else track.position_seconds,
            "position_source": "" if track is None else track.position_source,
            "position_confidence": 0.0 if track is None else track.position_confidence,
            "raw_timeline_position": 0.0 if self._last_raw_timeline_position is None else self._last_raw_timeline_position,
            "raw_timeline_age": None if self._last_raw_timeline_at <= 0.0 else round(time.monotonic() - self._last_raw_timeline_at, 3),
            "lyrics_source": "" if track is None else track.lyrics_source,
            "timed_lyrics_count": 0 if track is None else len(track.timed_lyrics),
            "audio": self.audio_analyzer.diagnostics() if self.audio_analyzer is not None else {"status": "not enabled", "running": False, "has_recent_frame": False, "last_error": ""},
            "netease_position": self.netease_position.diagnostics() if self.netease_position is not None else self._cold_attachment_diagnostics("netease"),
            "qq_position": self.qq_position.diagnostics() if self.qq_position is not None else self._cold_attachment_diagnostics("qq"),
            "plugins": self.plugin_provider.diagnostics(),
            "messages": self.message_detector.diagnostics(),
        }

    def send_playback_command(self, command: PlaybackCommand) -> bool:
        if self._loop is None:
            return False
        result = bool(self._run(self._send_command(command)))
        if result:
            if command in {"previous", "next"}:
                self._recent_skip_command_at = time.monotonic()
            self._request_immediate_media_refresh()
            self._prefetch_adjacent_lyrics_for_command(command)
        return result

    def _request_immediate_media_refresh(self):
        self._last_refresh_at = 0.0
        if self._loop is None:
            return
        future = getattr(self, "_refresh_future", None)
        if future is not None and not future.done():
            try:
                future.cancel()
            except Exception:
                pass
            self._refresh_future = None
        try:
            self._refresh_future = asyncio.run_coroutine_threadsafe(self._read_visual_data(), self._loop)
        except Exception:
            self._refresh_future = None

    def _prefetch_adjacent_lyrics_for_command(self, command: PlaybackCommand) -> bool:
        if command not in {"previous", "next"}:
            return False
        direction = "previous" if command == "previous" else "next"
        reader = self._player_position_reader_for(self._music_player_adapter)
        live_track_reader = getattr(reader, "current_track", None)
        if callable(live_track_reader):
            try:
                live_track = live_track_reader()
            except Exception:
                live_track = None
            live_title = str(getattr(live_track, "title", "") or "").strip()
            if live_title:
                live_artist = str(getattr(live_track, "artist", "") or "").strip()
                if self._prefetch_adjacent_lyrics(live_title, live_artist, reader, direction):
                    return True
        track = self._last_data.track if self._last_data is not None else None
        if track is None:
            return False
        return self._prefetch_adjacent_lyrics(track.title, track.artist, reader, direction)

    def _prefetch_adjacent_lyrics(self, title: str, artist: str, player_position_reader, direction: str = "next") -> bool:
        adjacent_reader = getattr(player_position_reader, "adjacent_track", None)
        if not callable(adjacent_reader) or not title:
            return False
        try:
            adjacent = adjacent_reader(title, artist, direction)
        except Exception:
            return False
        return self._prefetch_lyrics_for_track(adjacent)

    def _prefetch_netease_upcoming_lyrics(self, title: str, artist: str, player_position_reader, count: int = 2) -> bool:
        adjacent_reader = getattr(player_position_reader, "adjacent_track", None)
        if not callable(adjacent_reader) or not title:
            return False
        keys = getattr(self, "_netease_upcoming_prefetch_keys", None)
        if not isinstance(keys, set):
            keys = set()
            self._netease_upcoming_prefetch_keys = keys
        current_title = str(title or "").strip()
        current_artist = str(artist or "").strip()
        started_any = False
        for _index in range(max(0, int(count))):
            key = (self._stable_text_key(current_title), self._stable_text_key(current_artist))
            if key in keys:
                break
            try:
                adjacent = adjacent_reader(current_title, current_artist, "next")
            except Exception:
                break
            keys.add(key)
            next_title = str(getattr(adjacent, "title", "") or "").strip() if adjacent is not None else ""
            next_artist = str(getattr(adjacent, "artist", "") or "").strip() if adjacent is not None else ""
            if not next_title:
                break
            started_any = self._prefetch_lyrics_for_track(adjacent) or started_any
            current_title = next_title
            current_artist = next_artist
        return started_any

    def _should_prefetch_upcoming_lyrics(self, track_key: str, lyrics_match, lyrics_lookup_pending: bool, lookup_started: bool) -> bool:
        if bool(lyrics_lookup_pending) or bool(lookup_started):
            return False
        if not (getattr(lyrics_match, "timed_lyrics", ()) or getattr(lyrics_match, "lyrics", ())):
            return False
        key = str(track_key or "").strip()
        if not key:
            return False
        prefetched = getattr(self, "_upcoming_lyric_prefetch_track_ids", None)
        if not isinstance(prefetched, set):
            prefetched = set()
            self._upcoming_lyric_prefetch_track_ids = prefetched
        if key in prefetched:
            return False
        prefetched.add(key)
        return True

    def _prefetch_lyrics_for_track(self, track) -> bool:
        if track is None:
            return False
        title = str(getattr(track, "title", "") or "").strip()
        artist = str(getattr(track, "artist", "") or "").strip()
        album_title = str(getattr(track, "album_title", "") or "").strip()
        if not title:
            return False
        try:
            duration_seconds = max(0.0, float(getattr(track, "duration_seconds", 0.0) or 0.0))
        except Exception:
            duration_seconds = 0.0
        try:
            _match, _pending, started = self._ensure_lyrics_lookup_started(title, artist, album_title, duration_seconds)
            return bool(started)
        except Exception:
            return False

    def _album_queue_entries_from_player(self, title: str, artist: str, player_position_reader) -> tuple[AlbumQueueEntry, ...]:
        if player_position_reader is None or not title:
            return ()
        count = 2
        try:
            configured = int(self.settings_getter("v2_album_island_cover_count", 5))
            if configured in (3, 5, 7):
                count = max(1, configured // 2)
        except Exception:
            count = 2
        upcoming_reader = getattr(player_position_reader, "upcoming_tracks", None)
        tracks = ()
        if callable(upcoming_reader):
            try:
                tracks = tuple(upcoming_reader(title, artist, count))
            except Exception:
                tracks = ()
        entries: list[AlbumQueueEntry] = []
        seen: set[tuple[str, str]] = set()
        for track in tracks:
            queued_title = str(getattr(track, "title", "") or "").strip()
            queued_artist = str(getattr(track, "artist", "") or "").strip()
            if not queued_title:
                continue
            key = (queued_title.casefold(), queued_artist.casefold())
            if key in seen:
                continue
            seen.add(key)
            art = self._queue_album_art_bytes(track)
            entries.append(
                AlbumQueueEntry(
                    title=queued_title,
                    artist=queued_artist,
                    album_title=str(getattr(track, "album_title", "") or "").strip(),
                    album_art_bytes=art,
                    track_id=self._track_key("", queued_title, queued_artist, str(getattr(track, "album_title", "") or ""), float(getattr(track, "duration_seconds", 0.0) or 0.0)),
                )
            )
            if len(entries) >= count:
                break
        return tuple(entries)

    def _queue_album_art_bytes(self, track) -> bytes:
        data = getattr(track, "album_art_bytes", b"") or getattr(track, "image_bytes", b"") or b""
        if isinstance(data, bytes) and data:
            return data
        url = str(getattr(track, "album_art_url", "") or "").strip()
        if not url.startswith(("http://", "https://")):
            return b""
        cache = getattr(self, "_queue_art_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._queue_art_cache = cache
        if url in cache:
            return cache[url]
        try:
            request = Request(url, headers={"User-Agent": "GluSpot/1.0"})
            with urlopen(request, timeout=1.2) as response:
                content = response.read(512 * 1024)
        except Exception:
            content = b""
        if content:
            cache[url] = bytes(content)
            if len(cache) > 18:
                cache.pop(next(iter(cache)))
        return bytes(content)

    def _ensure_lyrics_lookup_started(self, title: str, artist: str, album_title: str = "", duration_seconds: float = 0.0, allow_background: bool = True):
        lyrics_match = self.lyrics_store.resolve_cached_or_local(title, artist, album_title, duration_seconds)
        lyrics_lookup_pending = self.lyrics_store.is_lookup_pending(title, artist, album_title, duration_seconds)
        lookup_started = False
        if allow_background and not lyrics_match.timed_lyrics and not lyrics_match.lyrics and not lyrics_lookup_pending:
            lookup_started = bool(self.lyrics_store.resolve_in_background(title, artist, album_title, duration_seconds))
            lyrics_lookup_pending = lookup_started
        elif self._dual_line_lyrics_enabled() and lyrics_match.timed_lyrics and not self._has_multiline_lyrics(lyrics_match.timed_lyrics):
            self.lyrics_store.enrich_in_background(title, artist, album_title, duration_seconds)
        return lyrics_match, lyrics_lookup_pending, lookup_started

    def seek_playback_position(self, position_seconds: float) -> bool:
        if self._loop is None:
            return False
        result = self._run(self._seek_position(position_seconds))
        if result:
            self._remember_local_seek(position_seconds)
            self._request_immediate_media_refresh()
        return bool(result)

    def _remember_local_seek(self, position_seconds: float):
        track = self._last_data.track if self._last_data is not None else None
        if track is None:
            self._remember_local_seek_for_track("", position_seconds, 0.0)
            return
        self._remember_local_seek_for_track(track.track_id, position_seconds, track.duration_seconds)

    def _remember_local_seek_for_track(self, track_id: str, position_seconds: float, duration_seconds: float = 0.0):
        position = max(0.0, float(position_seconds))
        duration = max(0.0, float(duration_seconds))
        if duration > 0.0:
            position = min(duration, position)
        self._local_seek_position_seconds = position
        self._local_seek_at = time.monotonic()
        self._local_seek_track_id = str(track_id or "")
        if self._last_data is not None and self._last_data.track.track_id == self._local_seek_track_id:
            track = self._last_data.track
            updated_track = replace(
                track,
                position_seconds=position,
                position_source="local-seek",
                position_confidence=max(float(track.position_confidence), 0.58),
            )
            self._last_data = replace(self._last_data, track=updated_track)
        self._last_ready_at = self._local_seek_at

    def _start_loop(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="WindowsMediaProvider", daemon=True)
        self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _start_global_pointer_sampler(self):
        if sys.platform != "win32":
            return
        thread = threading.Thread(target=self._run_global_pointer_sampler, name="GlobalPointerSampler", daemon=True)
        self._global_pointer_thread = thread
        thread.start()

    def _run_global_pointer_sampler(self):
        while not self._global_pointer_stop.wait(0.025):
            self._sample_global_pointer_once(time.monotonic())

    def _run(self, coro):
        if self._loop is None:
            return None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=1.4)
        except Exception:
            return None

    async def _get_session(self):
        if not _ensure_winsdk_loaded():
            self._provider_status = "missing_winsdk"
            self._reset_media_session_diagnostics("winsdk-missing")
            return None
        errors: list[str] = []
        if self._manager is None:
            try:
                self._manager = await MediaManager.request_async()
            except Exception as exc:
                self._provider_status = "no_session"
                self._reset_media_session_diagnostics(f"manager-request-failed: {self._brief_error(exc)}")
                return None

        current_session = None
        try:
            current_session = self._manager.get_current_session()
        except Exception as exc:
            errors.append(f"current-session-failed: {self._brief_error(exc)}")

        sessions = self._media_sessions()
        if self._media_session_error:
            errors.append(self._media_session_error)

        session = await self._select_media_session(current_session, sessions)
        manager_read_failed = bool(errors) and current_session is None and not sessions
        if manager_read_failed:
            self._manager = None
        if errors:
            self._media_session_error = " | ".join(dict.fromkeys(errors))
        elif session is None:
            self._media_session_error = "no-sessions"
        else:
            self._media_session_error = ""
        if session is None:
            self._provider_status = "no_session"
        return session

    def _reset_media_session_diagnostics(self, error: str = ""):
        self._media_session_count = 0
        self._media_session_candidates = ()
        self._selected_media_session_index = -1
        self._media_session_error = error

    def _brief_error(self, exc: Exception) -> str:
        detail = str(exc).strip()
        name = type(exc).__name__
        if detail:
            return f"{name}: {detail[:160]}"
        return name

    def _media_sessions(self) -> list[object]:
        try:
            sessions = list(self._manager.get_sessions() or [])
        except Exception as exc:
            self._media_session_error = f"get-sessions-failed: {self._brief_error(exc)}"
            return []
        self._media_session_error = ""
        return sessions

    async def _select_media_session(self, current_session, sessions: list[object]):
        candidates: list[object] = []
        if current_session is not None:
            candidates.append(current_session)
        for session in sessions:
            if all(session is not existing for existing in candidates):
                candidates.append(session)
        self._media_session_count = len(candidates)
        if not candidates:
            self._media_session_candidates = ()
            self._selected_media_session_index = -1
            return None

        scored: list[tuple[float, int, object, str]] = []
        for index, session in enumerate(candidates):
            score, label = await self._media_session_score(session, current_session is not None and session is current_session)
            scored.append((score, index, session, label))
        unique_labels: list[str] = []
        for _score, _index, _session, label in scored:
            if label not in unique_labels:
                unique_labels.append(label)
        self._media_session_count = len(unique_labels)
        self._media_session_candidates = tuple(unique_labels[:8])
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_index, best_session, _label = scored[0]
        self._selected_media_session_index = best_index
        return best_session if best_score > 0.0 else current_session

    async def _media_session_score(self, session, is_current: bool) -> tuple[float, str]:
        raw_source = str(getattr(session, "source_app_user_model_id", "") or "")
        title = ""
        artist = ""
        status = ""
        position_seconds = 0.0
        duration_seconds = 0.0
        score = 0.0
        try:
            playback = session.get_playback_info()
            status_value = getattr(playback, "playback_status", "")
            status = getattr(status_value, "name", str(status_value))
            if self._is_playing(playback):
                score += 45.0
        except Exception:
            status = "playback?"
        try:
            timeline = session.get_timeline_properties()
            position_seconds = self._timespan_seconds(getattr(timeline, "position", None))
            duration_seconds = self._timespan_seconds(getattr(timeline, "end_time", None)) - self._timespan_seconds(getattr(timeline, "start_time", None))
            if position_seconds > 0.0:
                score += 8.0
            if duration_seconds > 0.0:
                score += 8.0
        except Exception:
            pass
        try:
            properties = await session.try_get_media_properties_async()
            title = str(getattr(properties, "title", "") or "").strip()
            artist = str(getattr(properties, "artist", "") or "").strip()
            if title:
                score += 70.0
            if artist:
                score += 25.0
        except Exception:
            pass
        source_hint = raw_source.casefold()
        if any(token in source_hint for token in ("cloudmusic", "netease", "qqmusic", "spotify", "kugou", "kuwo", "douyin", "qishui")):
            score += 14.0
        if is_current:
            score += 28.0
        label_title = title or "-"
        label_artist = artist or "-"
        label_source = raw_source or "-"
        return score, f"{label_source} / {status or '-'} / {label_title} / {label_artist} / {position_seconds:.1f}s/{max(0.0, duration_seconds):.1f}s"

    async def _read_visual_data(self) -> IslandVisualData | None:
        session = await self._get_session()
        if session is None:
            return None
        try:
            properties = await session.try_get_media_properties_async()
            timeline = session.get_timeline_properties()
            playback = session.get_playback_info()
        except Exception:
            return None

        fallback_data = self.fallback.get_visual_data()
        fallback_track = fallback_data.track
        title = str(getattr(properties, "title", "") or "").strip()
        artist = str(getattr(properties, "artist", "") or "").strip()
        album_title = str(getattr(properties, "album_title", "") or "").strip()
        raw_source_app = str(getattr(session, "source_app_user_model_id", "") or "")
        source_app = self._source_app_name(session)
        read_at = time.monotonic()
        source_app_foreground = self._is_source_app_foreground(source_app, raw_source_app)
        player_adapter = self._music_player_adapter_for(source_app, raw_source_app)
        player_position_reader = self._player_position_reader_for(player_adapter)
        self._record_source_app_pointer_state(source_app_foreground, read_at)
        if source_app_foreground:
            self._source_app_foreground_at = read_at
        source_app_interactive = source_app_foreground or (
            self._source_app_foreground_at > 0.0 and read_at - self._source_app_foreground_at <= 3.5
        )
        adapter_interactive = player_adapter in {"netease", "qq"} and source_app_interactive
        foreground_metadata = self._foreground_track_metadata(player_adapter, source_app_foreground)
        player_window_track = None
        if foreground_metadata is not None:
            foreground_title, foreground_artist = foreground_metadata
            if foreground_title and foreground_artist and (foreground_title != title or foreground_artist != artist):
                title = foreground_title
                artist = foreground_artist
        else:
            player_window_track = self._player_window_track_metadata(player_adapter, player_position_reader)
            if player_window_track is not None:
                player_title = str(getattr(player_window_track, "title", "") or "").strip()
                player_artist = str(getattr(player_window_track, "artist", "") or "").strip()
                if player_title and player_artist and (player_title != title or player_artist != artist):
                    title = player_title
                    artist = player_artist
                    player_album = str(getattr(player_window_track, "album_title", "") or "").strip()
                    if player_album:
                        album_title = player_album
        self._raw_source_app_user_model_id = raw_source_app
        self._music_player_adapter = player_adapter
        self._source_app_foreground = source_app_foreground
        self._source_app_interactive = source_app_interactive
        is_playing = self._is_playing(playback)
        position_seconds = self._timespan_seconds(getattr(timeline, "position", None))
        duration_seconds = self._timespan_seconds(getattr(timeline, "end_time", None)) - self._timespan_seconds(getattr(timeline, "start_time", None))
        if duration_seconds < 0:
            duration_seconds = 0.0
        if player_window_track is not None:
            try:
                player_duration = max(0.0, float(getattr(player_window_track, "duration_seconds", 0.0) or 0.0))
            except Exception:
                player_duration = 0.0
            if player_duration > 0.0:
                duration_seconds = player_duration
        track_key = self._track_key(source_app, title, artist, album_title, duration_seconds)
        previous_track = self._last_data.track if self._last_data is not None else None
        if previous_track is not None and self._same_track_metadata(previous_track, title, artist):
            track_key = previous_track.track_id
        same_track_as_previous = previous_track is not None and previous_track.track_id == track_key
        previous_position = max(0.0, float(previous_track.position_seconds)) if previous_track is not None else 0.0
        previous_duration = max(0.0, float(previous_track.duration_seconds)) if previous_track is not None else 0.0
        preserve_previous_lyrics = bool(same_track_as_previous and previous_track is not None and previous_track.timed_lyrics)
        lyrics_match, lyrics_lookup_pending, _lyrics_lookup_started = self._ensure_lyrics_lookup_started(
            title,
            artist,
            album_title,
            duration_seconds,
            allow_background=not preserve_previous_lyrics,
        )
        album_art = await self._read_thumbnail(properties, track_key)
        album_color, secondary_color = self._colors_from_art(album_art, fallback_track.album_color, fallback_track.secondary_color)
        timed_lyrics = lyrics_match.timed_lyrics
        lyrics_source = lyrics_match.source
        lyrics = lyrics_match.lyrics or tuple(line.text for line in timed_lyrics) or (("查找歌词中",) if lyrics_lookup_pending else ("暂无歌词",))
        if preserve_previous_lyrics and previous_track is not None and not timed_lyrics:
            timed_lyrics = previous_track.timed_lyrics
            lyrics = previous_track.lyrics or tuple(line.text for line in timed_lyrics)
            lyrics_source = previous_track.lyrics_source
            lyrics_lookup_pending = False
        if self._should_prefetch_upcoming_lyrics(track_key, lyrics_match, lyrics_lookup_pending, _lyrics_lookup_started):
            if self._is_netease_adapter(player_adapter):
                self._prefetch_netease_upcoming_lyrics(title, artist, player_position_reader, count=2)
            else:
                self._prefetch_adjacent_lyrics(title, artist, player_position_reader, "next")
        album_queue = self._album_queue_entries_from_player(title, artist, player_position_reader)
        position_source = "windows-media"
        position_confidence = 1.0 if position_seconds > 0.0 else (0.26 if duration_seconds > 0.0 else 0.18)
        raw_timeline_position = position_seconds
        previous_raw_timeline_position: float | None = None
        if self._last_raw_timeline_track_id == track_key:
            previous_raw_timeline_position = self._last_raw_timeline_position
        self._last_raw_timeline_track_id = track_key
        self._last_raw_timeline_position = raw_timeline_position
        self._last_raw_timeline_at = read_at
        if player_position_reader is not None and duration_seconds <= 0.0:
            local_duration = player_position_reader.duration_for_track(title, artist)
            if local_duration > 0.0:
                duration_seconds = local_duration
                position_confidence = max(position_confidence, 0.26)
        local_seek_age = self._recent_local_seek_age(time.monotonic())
        pending_cold_start_seek = (
            previous_track is None
            and not str(self._local_seek_track_id or "")
            and local_seek_age is not None
        )
        local_seek_active = (
            local_seek_age is not None
            and (
                (same_track_as_previous and self._local_seek_track_id == track_key)
                or pending_cold_start_seek
            )
        )
        if pending_cold_start_seek and track_key:
            self._local_seek_track_id = track_key
        local_seek_estimate = 0.0
        if local_seek_active:
            local_seek_estimate = self._project_recent_local_seek_position(duration_seconds, is_playing, time.monotonic())
        if pending_cold_start_seek:
            position_seconds = local_seek_estimate
            position_source = "local-seek"
            position_confidence = max(position_confidence, 0.68)
        if same_track_as_previous:
            if duration_seconds <= 0.0 and previous_duration > 0.0:
                duration_seconds = previous_duration
            if local_seek_active and raw_timeline_position <= 0.0:
                position_seconds = local_seek_estimate
                position_source = "local-seek"
                position_confidence = max(position_confidence, 0.58)
            elif self._local_seek_track_id == track_key and raw_timeline_position > 0.0 and not local_seek_active:
                self._local_seek_position_seconds = None
                self._local_seek_at = 0.0
                self._local_seek_track_id = ""
            if position_source != "local-seek" and adapter_interactive:
                interactive_position = self._interactive_position_from_raw(
                    raw_timeline_position,
                    previous_position,
                    previous_raw_timeline_position,
                )
                if interactive_position is not None:
                    position_seconds, position_source, interactive_confidence = interactive_position
                    position_confidence = max(position_confidence, interactive_confidence)
            elif player_adapter in {"netease", "qq"} and position_source != "local-seek" and self._should_distrust_background_position(
                adapter_interactive,
                is_playing,
                raw_timeline_position,
                previous_position,
            ):
                elapsed = max(0.0, time.monotonic() - float(self._last_ready_at or time.monotonic()))
                estimated_position = previous_position + (elapsed if is_playing else 0.0)
                if duration_seconds > 0.0:
                    estimated_position = min(duration_seconds, estimated_position)
                position_seconds = estimated_position
                position_source = "local-background"
                position_confidence = max(position_confidence, 0.46)
            if player_adapter in {"netease", "qq"} and position_seconds <= 0.0 and (is_playing or previous_position > 0.0 or previous_track.position_source == "local-estimate"):
                elapsed = max(0.0, time.monotonic() - float(self._last_ready_at or time.monotonic()))
                estimated_position = previous_position + (elapsed if is_playing else 0.0)
                if duration_seconds > 0.0:
                    estimated_position = min(duration_seconds, estimated_position)
                position_seconds = estimated_position
                position_source = "local-estimate"
                position_confidence = max(position_confidence, 0.34 if is_playing else 0.30)
        elif self._local_seek_track_id and self._local_seek_track_id != track_key:
            self._local_seek_position_seconds = None
            self._local_seek_at = 0.0
            self._local_seek_track_id = ""
        recent_source_pointer = self._source_app_recent_pointer_activity(read_at)
        recent_global_pointer = self._global_recent_pointer_activity(read_at)
        recent_click_pos = self._source_app_recent_click_position(read_at)
        if recent_click_pos is None and source_app_foreground:
            recent_click_pos = self._global_recent_click_position(read_at)
        recent_pointer_pos = self._source_app_recent_pointer_position(read_at)
        if recent_pointer_pos is None and source_app_foreground:
            recent_pointer_pos = self._global_recent_pointer_position(read_at)
        if recent_pointer_pos is None:
            recent_pointer_pos = recent_click_pos
        recent_lyric_click = self._source_app_recent_click(read_at) or (
            source_app_foreground and self._global_recent_click(read_at)
        )
        recent_skip_command = 0.0 <= read_at - float(getattr(self, "_recent_skip_command_at", 0.0) or 0.0) <= 2.8
        lyric_click_as_progress_anchor = (
            self._is_netease_adapter(player_adapter)
            and source_app_interactive
            and recent_lyric_click
            and bool(timed_lyrics)
        )
        allow_lyric_click_anchor = False
        lyric_click_pos = recent_click_pos if allow_lyric_click_anchor else None
        lyric_click_local_sample = None
        lyric_click_local_applied = False
        lyric_click_geometry_reader = getattr(player_position_reader, "position_from_lyric_click_point", None)
        if allow_lyric_click_anchor and callable(lyric_click_geometry_reader):
            try:
                lyric_click_local_sample = lyric_click_geometry_reader(lyric_click_pos, timed_lyrics, duration_seconds)
            except Exception:
                lyric_click_local_sample = None
        if lyric_click_local_sample is not None and (same_track_as_previous or previous_track is None):
            lyric_click_pos = None
            allow_lyric_click_anchor = False
            position_seconds = max(0.0, float(lyric_click_local_sample.position_seconds))
            if duration_seconds > 0.0:
                position_seconds = min(duration_seconds, position_seconds)
            position_source = "local-seek"
            position_confidence = max(position_confidence, float(getattr(lyric_click_local_sample, "confidence", 0.0) or 0.0), 0.68)
            self._remember_local_seek_for_track(track_key, position_seconds, duration_seconds)
            await self._seek_session_position(session, position_seconds)
            self._consume_recent_click_anchor(recent_click_pos)
            local_seek_active = True
            local_seek_estimate = position_seconds
            lyric_click_local_applied = True
        progress_point_position = None
        progress_point_reader = getattr(player_position_reader, "position_from_progress_point", None)
        if (
            callable(progress_point_reader)
            and player_adapter in {"netease", "qq"}
            and source_app_foreground
            and recent_pointer_pos is not None
            and duration_seconds > 0.0
            and (recent_source_pointer or recent_global_pointer or recent_lyric_click)
        ):
            try:
                progress_point_position = progress_point_reader(recent_pointer_pos, duration_seconds)
            except Exception:
                progress_point_position = None
        if progress_point_position is not None:
            allow_lyric_click_anchor = False
            lyric_click_pos = None
            if same_track_as_previous or previous_track is None:
                self._mark_player_anchor_fast_refresh(read_at)
                position_seconds = max(0.0, float(progress_point_position))
                if duration_seconds > 0.0:
                    position_seconds = min(duration_seconds, position_seconds)
                position_source = "local-seek"
                position_confidence = max(position_confidence, 0.68)
                self._remember_local_seek_for_track(track_key, position_seconds, duration_seconds)
                local_seek_active = True
                local_seek_estimate = position_seconds
        force_player_anchor_read = (
            player_adapter in {"netease", "qq"}
            and (source_app_foreground or lyric_click_as_progress_anchor)
            and (recent_source_pointer or recent_global_pointer or lyric_click_as_progress_anchor)
            and not lyric_click_local_applied
        )
        track_change_anchor_read = (
            player_adapter in {"netease", "qq"}
            and (source_app_foreground or recent_skip_command)
            and previous_track is not None
            and not same_track_as_previous
        )
        lyrics_loaded_anchor_read = (
            player_position_reader is not None
            and player_adapter in {"netease", "netease_betterncm", "qq"}
            and self._should_sync_after_lyrics_loaded(track_key, previous_track, timed_lyrics)
            and not lyric_click_local_applied
        )
        should_read_player_anchor = player_position_reader is not None and bool(title)
        if should_read_player_anchor:
            read_immediate = getattr(player_position_reader, "read_immediate", None)
            use_immediate_reader = callable(read_immediate) and (
                previous_track is None
                or track_change_anchor_read
                or force_player_anchor_read
                or lyrics_loaded_anchor_read
                or recent_skip_command
            )
            force_progress_image = bool(
                previous_track is None
                or track_change_anchor_read
                or force_player_anchor_read
                or lyrics_loaded_anchor_read
                or lyric_click_as_progress_anchor
                or recent_skip_command
            )
            if use_immediate_reader:
                if force_player_anchor_read or track_change_anchor_read or lyrics_loaded_anchor_read or recent_skip_command:
                    self._mark_player_anchor_fast_refresh(read_at, duration=2.6 if lyric_click_as_progress_anchor else 1.35)
                player_sample = read_immediate(
                    title,
                    artist,
                    timed_lyrics,
                    duration_seconds,
                    interactive=source_app_interactive,
                    allow_lyric_anchor=allow_lyric_click_anchor,
                    click_pos=lyric_click_pos,
                    force_progress_image=force_progress_image,
                )
            else:
                player_sample = player_position_reader.read(
                    title,
                    artist,
                    timed_lyrics,
                    duration_seconds,
                    interactive=source_app_interactive,
                    allow_lyric_anchor=allow_lyric_click_anchor,
                    click_pos=lyric_click_pos,
                    force_progress_image=force_progress_image,
                )
            if player_sample is not None:
                if duration_seconds <= 0.0 and player_sample.duration_seconds > 0.0:
                    duration_seconds = player_sample.duration_seconds
                player_sample_source = str(getattr(player_sample, "source", ""))
                source_prefix = getattr(player_position_reader, "source_prefix", player_adapter)
                if player_sample_source.startswith(f"{source_prefix}-uia-"):
                    player_sample_age = time.monotonic() - float(getattr(player_sample, "sampled_at", 0.0) or 0.0)
                    fresh_player_anchor = 0.0 <= player_sample_age <= 0.75
                    projected_player_position = float(player_sample.position_seconds)
                    if is_playing:
                        projected_player_position += max(0.0, player_sample_age)
                    if player_sample_source == f"{source_prefix}-uia-progress":
                        projected_player_position += self._player_seek_correction_seconds(
                            player_adapter,
                            source_app_interactive,
                            is_playing,
                            raw_timeline_position,
                            previous_raw_timeline_position,
                            previous_position,
                        )
                    if duration_seconds > 0.0:
                        projected_player_position = min(duration_seconds, projected_player_position)
                    if (
                        local_seek_active
                        and player_sample_source == f"{source_prefix}-uia-progress"
                        and abs(projected_player_position - local_seek_estimate) > 2.5
                    ):
                        player_sample_follows_recent_seek = False
                    else:
                        player_sample_follows_recent_seek = True
                    if player_sample_follows_recent_seek and self._should_apply_player_sample(
                        source_prefix,
                        player_sample_source,
                        player_sample_age,
                        is_playing,
                        source_app_interactive,
                        position_seconds,
                        projected_player_position,
                        fresh_player_anchor,
                        user_initiated=force_player_anchor_read or track_change_anchor_read or lyrics_loaded_anchor_read,
                    ):
                        if player_sample_source == f"{source_prefix}-uia-progress":
                            self._mark_player_anchor_fast_refresh(read_at)
                        position_seconds = projected_player_position
                        position_source = player_sample.source
                        position_confidence = player_sample.confidence
                        if (
                            player_sample_source == f"{source_prefix}-uia-lyrics"
                            and allow_lyric_click_anchor
                            and source_app_foreground
                        ):
                            self._remember_local_seek_for_track(track_key, projected_player_position, duration_seconds)
                            await self._seek_session_position(session, projected_player_position)
                            self._consume_recent_click_anchor(lyric_click_pos)
        if recent_skip_command and not same_track_as_previous:
            self._recent_skip_command_at = 0.0
        if not title and not artist:
            self._provider_status = "no_session"
            return None
        self._provider_status = "ready"
        track = TrackData(
            title=title or fallback_track.title,
            artist=artist or fallback_track.artist,
            lyrics=lyrics,
            album_color=album_color,
            secondary_color=secondary_color,
            audio_frames=fallback_track.audio_frames,
            album_art_bytes=album_art,
            source_app=source_app,
            is_playing=is_playing,
            duration_seconds=duration_seconds,
            position_seconds=position_seconds,
            timed_lyrics=timed_lyrics,
            lyrics_source=lyrics_source,
            track_id=track_key,
            position_source=position_source,
            position_confidence=position_confidence,
            source_app_foreground=source_app_foreground,
            position_interactive=source_app_interactive,
            album_queue=album_queue,
            lyric_offset_seconds=self._lyric_offset_seconds_for_adapter(player_adapter),
        )
        data = replace(fallback_data, track=track, messages=self.message_detector.messages(), plugins=self.plugin_provider.plugins())
        return self._apply_recent_local_seek_to_visual_data(data, time.monotonic())

    async def _send_command(self, command: PlaybackCommand) -> bool:
        session = await self._get_session()
        if session is None:
            return False
        try:
            if command == "previous":
                return bool(await session.try_skip_previous_async())
            if command == "next":
                return bool(await session.try_skip_next_async())
            return bool(await session.try_toggle_play_pause_async())
        except Exception:
            return False

    async def _seek_position(self, position_seconds: float) -> bool:
        session = await self._get_session()
        if session is None:
            return False
        return await self._seek_session_position(session, position_seconds)

    async def _seek_session_position(self, session, position_seconds: float) -> bool:
        try:
            requested = max(0, int(round(float(position_seconds) * 10_000_000)))
            return bool(await session.try_change_playback_position_async(requested))
        except Exception:
            return False

    async def _read_thumbnail(self, properties, track_key: str) -> bytes:
        thumbnail = getattr(properties, "thumbnail", None)
        if thumbnail is None:
            return b""
        if Buffer is None or DataReader is None:
            return b""
        try:
            stream = await thumbnail.open_read_async()
            size = int(getattr(stream, "size", 0) or 0)
            if size <= 0 or size > 12_000_000:
                return b""
            cache_key = self._thumbnail_cache_key(track_key, thumbnail, size)
            if cache_key in self._art_cache:
                return self._art_cache[cache_key]
            if hasattr(stream, "seek"):
                stream.seek(0)
            buffer = Buffer(size)
            options = InputStreamOptions.READ_AHEAD if InputStreamOptions is not None else 0
            filled = await stream.read_async(buffer, size, options)
            actual = int(getattr(filled, "length", 0) or 0)
            if actual <= 0:
                return b""
            reader = DataReader.from_buffer(filled)
            data = self._reader_bytes(reader, actual)
            if not self._is_valid_image_bytes(data):
                return b""
            self._art_cache[cache_key] = data
            if len(self._art_cache) > 36:
                self._art_cache.pop(next(iter(self._art_cache)))
            return data
        except Exception:
            return b""

    def _reader_bytes(self, reader, length: int) -> bytes:
        try:
            raw = reader.read_bytes(length)
        except TypeError:
            data = bytearray(length)
            reader.read_bytes(data)
            return bytes(data)
        if raw is None:
            return b""
        if isinstance(raw, bytes):
            return raw
        if isinstance(raw, bytearray):
            return bytes(raw)
        return bytes(int(value) & 0xFF for value in raw)

    def _thumbnail_cache_key(self, track_key: str, thumbnail, size: int) -> str:
        raw = f"{track_key}\0{getattr(thumbnail, 'content_type', '')}\0{getattr(thumbnail, 'size', '')}\0{size}"
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()

    def _is_valid_image_bytes(self, data: bytes) -> bool:
        return bool(data) and not QImage.fromData(data).isNull()

    def _same_track_metadata(self, previous: TrackData, title: str, artist: str) -> bool:
        title_key = self._stable_text_key(title)
        if not title_key:
            return False
        previous_title_key = self._stable_text_key(previous.title)
        if title_key != previous_title_key:
            return False
        artist_key = self._stable_text_key(artist)
        previous_artist_key = self._stable_text_key(previous.artist)
        return not artist_key or not previous_artist_key or artist_key == previous_artist_key

    def _stable_text_key(self, value: str) -> str:
        return "".join(str(value or "").casefold().split())

    def _foreground_correct_cached_data(self, data: IslandVisualData, now: float) -> IslandVisualData:
        track = data.track
        player_adapter = str(getattr(self, "_music_player_adapter", "") or "").strip().casefold()
        raw_source_app = str(getattr(self, "_raw_source_app_user_model_id", "") or "")
        if player_adapter not in {"netease", "qq"}:
            return data
        if not self._is_source_app_foreground(track.source_app, raw_source_app):
            return data
        metadata = self._foreground_track_metadata(player_adapter, True)
        if metadata is None:
            return data
        title, artist = metadata
        if self._same_track_metadata(track, title, artist):
            return data
        track_key = self._track_key(track.source_app, title, artist, "", 0.0)
        reader = self._player_position_reader_for(player_adapter)
        reset = getattr(reader, "reset", None)
        if callable(reset):
            reset()
        corrected_track = replace(
            track,
            title=title,
            artist=artist,
            lyrics=(),
            timed_lyrics=(),
            lyrics_source="",
            track_id=track_key,
            position_seconds=0.0,
            duration_seconds=0.0,
            position_source="foreground-title",
            position_confidence=0.40,
            source_app_foreground=True,
            position_interactive=True,
        )
        self._last_data = replace(data, track=corrected_track)
        self._last_ready_at = now
        self._last_raw_timeline_track_id = ""
        self._last_raw_timeline_position = None
        self._last_raw_timeline_at = 0.0
        self._local_seek_position_seconds = None
        self._local_seek_at = 0.0
        self._local_seek_track_id = ""
        return self._last_data

    def _should_distrust_background_position(
        self,
        source_app_interactive: bool,
        is_playing: bool,
        raw_timeline_position: float,
        previous_position: float,
    ) -> bool:
        if source_app_interactive or not is_playing or previous_position <= 0.5:
            return False
        if raw_timeline_position <= 0.0:
            return True
        return previous_position > 2.0 and raw_timeline_position + 1.25 < previous_position

    def _interactive_position_from_raw(
        self,
        raw_timeline_position: float,
        previous_position: float,
        previous_raw_timeline_position: float | None,
    ) -> tuple[float, str, float] | None:
        raw_position = max(0.0, float(raw_timeline_position))
        previous_position = max(0.0, float(previous_position))
        if raw_position > 0.0:
            return raw_position, "interactive", 0.86
        if self._looks_like_seek_to_start(raw_position, previous_position, previous_raw_timeline_position):
            return 0.0, "interactive", 0.82
        if previous_position > 0.0:
            return previous_position, "interactive-hold", 0.64
        return None

    def _looks_like_seek_to_start(
        self,
        raw_position: float,
        previous_position: float,
        previous_raw_timeline_position: float | None,
    ) -> bool:
        if previous_raw_timeline_position is None:
            return False
        previous_raw_position = max(0.0, float(previous_raw_timeline_position))
        previous_position = max(0.0, float(previous_position))
        if raw_position > 0.12 or previous_raw_position <= 3.0 or previous_position <= 3.0:
            return False
        return abs(previous_raw_position - previous_position) <= max(5.0, previous_position * 0.12)

    def _is_source_app_foreground(self, source_app: str, raw_source_app: str) -> bool:
        snapshot = self._foreground_snapshot()
        self._last_foreground_snapshot = snapshot
        if not snapshot:
            return False
        haystack = " ".join(str(snapshot.get(key, "")) for key in ("title", "class_name", "process_path", "process")).casefold()
        tokens = self._source_match_tokens(source_app, raw_source_app)
        return any(token and token in haystack for token in tokens)

    def _source_match_tokens(self, source_app: str, raw_source_app: str) -> tuple[str, ...]:
        haystack = f"{source_app} {raw_source_app}".casefold()
        tokens: list[str] = []
        if "netease" in haystack or "cloudmusic" in haystack:
            tokens.extend(("cloudmusic", "orpheus", "netease"))
        if "qqmusic" in haystack or "qq" in haystack:
            tokens.extend(("qqmusic",))
        if "spotify" in haystack:
            tokens.extend(("spotify",))
        if "kugou" in haystack:
            tokens.extend(("kugou",))
        if "kuwo" in haystack:
            tokens.extend(("kuwo",))
        for piece in (source_app, raw_source_app):
            lowered = str(piece or "").casefold()
            for splitter in ("!", ".", "_", "-", " "):
                lowered = lowered.replace(splitter, "\\")
            for token in lowered.split("\\"):
                if len(token) >= 4 and token not in tokens:
                    tokens.append(token)
        return tuple(tokens)

    def _foreground_snapshot(self) -> dict[str, object]:
        if sys.platform != "win32":
            return {}
        try:
            user32 = ctypes.windll.user32
            hwnd = int(user32.GetForegroundWindow())
            if hwnd <= 0:
                return {}
            process_id = self._window_process_id(hwnd)
            process_path = self._process_path(process_id)
            return {
                "hwnd": hwnd,
                "title": self._window_title(hwnd),
                "class_name": self._window_class(hwnd),
                "process_id": process_id,
                "process_path": process_path,
                "process": process_path.rsplit("\\", 1)[-1] if process_path else "",
            }
        except Exception:
            return {}

    def _window_process_id(self, hwnd: int) -> int:
        try:
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
            return int(pid.value)
        except Exception:
            return 0

    def _window_title(self, hwnd: int) -> str:
        try:
            user32 = ctypes.windll.user32
            length = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
            if length <= 0:
                return ""
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(wintypes.HWND(hwnd), buffer, length + 1)
            return buffer.value.strip()
        except Exception:
            return ""

    def _window_class(self, hwnd: int) -> str:
        try:
            buffer = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetClassNameW(wintypes.HWND(hwnd), buffer, 256)
            return buffer.value.strip()
        except Exception:
            return ""

    def _process_path(self, process_id: int) -> str:
        if not process_id or sys.platform != "win32":
            return ""
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return buffer.value
        except Exception:
            return ""
        finally:
            kernel32.CloseHandle(handle)
        return ""

    def _source_app_name(self, session) -> str:
        value = str(getattr(session, "source_app_user_model_id", "") or "")
        lowered = value.lower()
        aliases = (
            ("spotify", "Spotify"),
            ("netease", "网易云音乐"),
            ("cloudmusic", "网易云音乐"),
            ("qqmusic", "QQ 音乐"),
            ("kugou", "酷狗音乐"),
            ("kuwo", "酷我音乐"),
            ("douyin", "汽水音乐"),
            ("qishui", "汽水音乐"),
        )
        for token, name in aliases:
            if token in lowered:
                return name
        return value.rsplit("!", 1)[-1] or "系统媒体"

    def _is_netease_source(self, source_app: str) -> bool:
        lowered = (source_app or "").lower()
        return "网易云" in source_app or "netease" in lowered or "cloudmusic" in lowered

    def _music_player_mode(self) -> str:
        settings_getter = getattr(self, "settings_getter", None)
        if not callable(settings_getter):
            settings_getter = lambda _key, default=None: default
        mode = str(settings_getter("v2_music_player_mode", "netease") or "netease").strip().casefold()
        if mode in {"", "auto", "system"}:
            return "netease"
        return mode if mode in {"netease", "netease_betterncm", "qishui", "qq", "kugou"} else "netease"

    def _player_progress_plugin_enabled(self) -> bool:
        settings_getter = getattr(self, "settings_getter", None)
        if not callable(settings_getter):
            return False
        profiles = settings_getter("v2_music_app_profiles", None)
        if isinstance(profiles, dict):
            for service, profile in profiles.items():
                if (
                    isinstance(profile, dict)
                    and bool(profile.get("sync_enabled", False))
                    and bool(self._effective_music_app_profile_path(str(service), profile))
                ):
                    return True
            return False
        return bool(settings_getter("v2_enable_player_progress_plugin", False))

    def _player_progress_auto_detect_enabled(self) -> bool:
        settings_getter = getattr(self, "settings_getter", None)
        if not callable(settings_getter):
            return False
        return bool(settings_getter("v2_player_progress_auto_detect", False))

    def _music_app_profiles(self) -> dict[str, object] | None:
        settings_getter = getattr(self, "settings_getter", None)
        if not callable(settings_getter):
            return None
        profiles = settings_getter("v2_music_app_profiles", None)
        return profiles if isinstance(profiles, dict) else None

    def _music_app_profile(self, service: str) -> dict[str, object] | None:
        profiles = self._music_app_profiles()
        if profiles is None:
            return None
        key = "netease" if str(service or "").strip().casefold() in {"netease", "netease_betterncm"} else str(service or "").strip().casefold()
        profile = profiles.get(key, {})
        return profile if isinstance(profile, dict) else {}

    def _music_app_profile_allows_adapter(self, service: str, source_app: str, raw_source_app: str) -> bool:
        profile = self._music_app_profile(service)
        if profile is None:
            return True
        if not bool(profile.get("sync_enabled", False)):
            return False
        service = str(service or "").strip().casefold()
        if service == "netease_betterncm" and not bool(profile.get("betterncm_enabled", False)):
            return False
        path = self._effective_music_app_profile_path(service, profile)
        if not path:
            return False
        return self._configured_player_path_matches(path, source_app, raw_source_app)

    def _effective_music_app_profile_path(self, service: str, profile: dict[str, object] | None = None) -> str:
        service = str(service or "").strip().casefold()
        if profile is None:
            profile = self._music_app_profile(service) or {}
        path = str(profile.get("path", "") or "").strip().strip('"')
        if path:
            return path
        if service in {"netease", "netease_betterncm"}:
            legacy_path = self._legacy_netease_executable_path()
            if legacy_path:
                return legacy_path
        return ""

    def _legacy_netease_executable_path(self) -> str:
        settings_getter = getattr(self, "settings_getter", None)
        if not callable(settings_getter):
            return ""
        path = str(settings_getter("v2_netease_betterncm_path", "") or "").strip().strip('"')
        if self._looks_like_executable_path(path):
            return path
        return ""

    @staticmethod
    def _looks_like_executable_path(path: str) -> bool:
        normalized = str(path or "").replace("/", "\\").strip().strip('"')
        basename = normalized.rsplit("\\", 1)[-1].casefold()
        return bool(basename.endswith(".exe") and len(basename) > 4)

    def _configured_player_path_matches(self, configured_path: str, source_app: str, raw_source_app: str) -> bool:
        path = str(configured_path or "").replace("/", "\\").strip().strip('"').casefold()
        if not path:
            return False
        basename = path.rsplit("\\", 1)[-1]
        stem = basename.rsplit(".", 1)[0] if "." in basename else basename
        hint = f"{source_app} {raw_source_app}".casefold()
        if (basename and basename in hint) or (stem and len(stem) >= 3 and stem in hint):
            return True
        snapshot = getattr(self, "_last_foreground_snapshot", {}) or {}
        process_path = str(snapshot.get("process_path", "") or "").replace("/", "\\").strip().strip('"').casefold()
        process_name = str(snapshot.get("process", "") or "").strip().casefold()
        if process_path and (process_path == path or process_path.endswith("\\" + basename)):
            return True
        return bool(process_name and process_name == basename)

    def _lyric_offset_seconds_for_adapter(self, service: str) -> float:
        profile = self._music_app_profile(service)
        if profile is None:
            return 0.0
        try:
            offset = float(profile.get("lyric_offset_seconds", 0.0) or 0.0)
        except (TypeError, ValueError):
            offset = 0.0
        return max(-12.0, min(12.0, offset))

    def _dual_line_lyrics_enabled(self) -> bool:
        settings_getter = getattr(self, "settings_getter", None)
        if not callable(settings_getter):
            return False
        return bool(settings_getter("v2_enable_dual_line_lyrics", False))

    def _is_netease_adapter(self, player_adapter: str) -> bool:
        return str(player_adapter or "").strip().casefold() in {"netease", "netease_betterncm"}

    def _should_sync_after_lyrics_loaded(self, track_key: str, previous_track: TrackData | None, timed_lyrics) -> bool:
        if not track_key or not timed_lyrics or previous_track is None:
            return False
        if previous_track.track_id != track_key or previous_track.timed_lyrics:
            return False
        synced = getattr(self, "_lyrics_loaded_sync_track_ids", None)
        if not isinstance(synced, set):
            synced = getattr(self, "_netease_lyric_sync_track_ids", None)
        if not isinstance(synced, set):
            synced = set()
        self._lyrics_loaded_sync_track_ids = synced
        self._netease_lyric_sync_track_ids = synced
        if track_key in synced:
            return False
        synced.add(track_key)
        return True

    def _should_sync_netease_after_lyrics_loaded(self, track_key: str, previous_track: TrackData | None, timed_lyrics) -> bool:
        return self._should_sync_after_lyrics_loaded(track_key, previous_track, timed_lyrics)

    def _player_position_reader_for(self, service: str):
        service = str(service or "").strip().casefold()
        if service in {"netease", "netease_betterncm"}:
            reader = getattr(self, "netease_position", None)
            if reader is None:
                if not self._should_lazy_create_player_reader(service):
                    return None
                reader = NeteasePositionReader()
                self.netease_position = reader
            return reader
        if service == "qq":
            reader = getattr(self, "qq_position", None)
            if reader is None:
                if not self._should_lazy_create_player_reader(service):
                    return None
                reader = QQPositionReader()
                self.qq_position = reader
            return reader
        return None

    def _should_lazy_create_player_reader(self, service: str) -> bool:
        profiles = self._music_app_profiles()
        if profiles is not None:
            profile = self._music_app_profile(service) or {}
            return bool(profile.get("sync_enabled", False)) and bool(self._effective_music_app_profile_path(service, profile))
        return self._player_progress_plugin_enabled()

    def _music_player_source_matches(self, service: str, source_app: str, raw_source_app: str = "") -> bool:
        service = str(service or "").strip().casefold()
        source_hint = f"{source_app} {raw_source_app}".casefold()
        if service in {"netease", "netease_betterncm"}:
            return self._is_netease_source(f"{source_app} {raw_source_app}")
        if service == "qishui":
            return any(token in source_hint for token in ("汽水", "qishui", "douyin"))
        if service == "qq":
            return any(token in source_hint for token in ("qqmusic", "qq"))
        if service == "kugou":
            return "kugou" in source_hint
        return False

    def _foreground_track_metadata(self, player_adapter: str, source_app_foreground: bool) -> tuple[str, str] | None:
        if not source_app_foreground or player_adapter not in {"netease", "qq"}:
            return None
        snapshot = getattr(self, "_last_foreground_snapshot", {}) or {}
        title = str(snapshot.get("title", "") or "").strip()
        if not title or " - " not in title:
            return None
        track_title, track_artist = title.rsplit(" - ", 1)
        track_title = track_title.strip()
        track_artist = track_artist.strip()
        if not track_title or not track_artist:
            return None
        if len(track_title) > 160 or len(track_artist) > 120:
            return None
        return track_title, track_artist

    def _player_window_track_metadata(self, player_adapter: str, player_position_reader):
        if player_adapter not in {"netease", "qq"}:
            return None
        current_track = getattr(player_position_reader, "current_track", None)
        if not callable(current_track):
            return None
        try:
            track = current_track()
        except Exception:
            return None
        title = str(getattr(track, "title", "") or "").strip()
        artist = str(getattr(track, "artist", "") or "").strip()
        if not title or not artist:
            return None
        return track

    def _should_use_player_sample(self, source_prefix: str, sample_source: str, sample_age: float, is_playing: bool) -> bool:
        source_prefix = str(source_prefix or "").strip().casefold()
        if sample_source == f"{source_prefix}-uia-progress":
            return 0.0 <= float(sample_age) <= 1.5
        return False

    def _should_use_click_seek_sample(self, source_prefix: str, sample_source: str, sample_age: float, is_playing: bool) -> bool:
        source_prefix = str(source_prefix or "").strip().casefold()
        if sample_source == f"{source_prefix}-uia-lyrics":
            return bool(is_playing) and 0.0 <= float(sample_age) <= 0.9
        return False

    def _should_apply_player_sample(
        self,
        source_prefix: str,
        sample_source: str,
        sample_age: float,
        is_playing: bool,
        source_app_interactive: bool,
        current_position: float,
        projected_sample_position: float,
        fresh_player_anchor: bool,
        user_initiated: bool = False,
    ) -> bool:
        current_position = max(0.0, float(current_position))
        projected_sample_position = max(0.0, float(projected_sample_position))
        sample_source = str(sample_source or "").strip().casefold()
        source_prefix = str(source_prefix or "").strip().casefold()
        if sample_source == f"{source_prefix}-uia-lyrics":
            if not self._should_use_click_seek_sample(source_prefix, sample_source, sample_age, is_playing):
                return False
            if not source_app_interactive or not fresh_player_anchor:
                return False
            if current_position <= 0.0:
                return True
            return abs(projected_sample_position - current_position) >= 0.30
        if not self._should_use_player_sample(source_prefix, sample_source, sample_age, is_playing):
            return False
        if current_position <= 0.0:
            return True
        if sample_source == f"{source_prefix}-uia-progress" and source_app_interactive:
            if not user_initiated and projected_sample_position < current_position - 0.85:
                return False
            return abs(projected_sample_position - current_position) >= 0.30
        if user_initiated and sample_source == f"{source_prefix}-uia-progress" and fresh_player_anchor:
            return True
        if not fresh_player_anchor:
            return False
        return abs(projected_sample_position - current_position) >= 1.25

    def _should_use_netease_sample(self, sample_source: str, sample_age: float, is_playing: bool) -> bool:
        return self._should_use_player_sample("netease", sample_source, sample_age, is_playing)

    def _player_seek_correction_seconds(
        self,
        player_adapter: str,
        source_app_interactive: bool,
        is_playing: bool,
        raw_timeline_position: float,
        previous_raw_timeline_position: float | None,
        previous_position: float,
    ) -> float:
        if not self._is_netease_adapter(player_adapter) or not source_app_interactive or not is_playing:
            return 0.0
        if previous_raw_timeline_position is None:
            return 0.0
        if raw_timeline_position <= 0.0:
            return 0.0
        raw_delta = abs(float(raw_timeline_position) - float(previous_raw_timeline_position))
        if raw_delta < max(0.85, float(previous_position) * 0.08):
            return 0.0
        return 1.5

    def _record_source_app_pointer_state(self, source_app_foreground: bool, now: float):
        try:
            user32 = ctypes.windll.user32
            state = int(user32.GetAsyncKeyState(0x01))
            down = bool(state & 0x8000)
            pressed_since_last = bool(state & 0x0001)
            point = wintypes.POINT()
            if not user32.GetCursorPos(ctypes.byref(point)):
                point = None
        except Exception:
            return
        pos = None if point is None else (int(point.x), int(point.y))
        was_down = bool(getattr(self, "_source_app_left_down", False))
        if pressed_since_last and source_app_foreground:
            self._source_app_recent_pointer_at = now
            self._source_app_recent_pointer_pos = pos
            if not down:
                self._source_app_recent_click_at = now
                self._source_app_recent_click_pos = pos
        if down and not was_down:
            if source_app_foreground:
                self._source_app_recent_pointer_at = now
                self._source_app_recent_pointer_pos = pos
                self._source_app_recent_click_at = 0.0
                self._source_app_recent_click_pos = None
            self._source_app_left_down_at = now
            self._source_app_left_down_pos = pos
            self._source_app_left_down_started_in_foreground = bool(source_app_foreground)
        elif not down and was_down:
            start = getattr(self, "_source_app_left_down_pos", None)
            duration = max(0.0, now - float(getattr(self, "_source_app_left_down_at", now)))
            distance = 0.0
            if start is not None and pos is not None:
                distance = math.hypot(float(pos[0] - start[0]), float(pos[1] - start[1]))
            if (
                bool(getattr(self, "_source_app_left_down_started_in_foreground", False))
                and source_app_foreground
            ):
                self._source_app_recent_pointer_at = now
                self._source_app_recent_pointer_pos = pos or start
                if duration <= 0.65 and distance <= 18.0:
                    self._source_app_recent_click_at = now
                    self._source_app_recent_click_pos = pos or start
                else:
                    self._source_app_recent_click_at = 0.0
                    self._source_app_recent_click_pos = None
            self._source_app_left_down_pos = None
            self._source_app_left_down_started_in_foreground = False
        self._source_app_left_down = down

    def _record_global_pointer_state(self, now: float):
        self._sample_global_pointer_once(now)

    def _sample_global_pointer_once(self, now: float):
        try:
            user32 = ctypes.windll.user32
            state = int(user32.GetAsyncKeyState(0x01))
            down = bool(state & 0x8000)
            pressed_since_last = bool(state & 0x0001)
            point = wintypes.POINT()
            if not user32.GetCursorPos(ctypes.byref(point)):
                point = None
        except Exception:
            return
        pos = None if point is None else (int(point.x), int(point.y))
        lock = getattr(self, "_global_pointer_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._global_pointer_lock = lock
        with lock:
            was_down = bool(getattr(self, "_global_left_down", False))
            if pressed_since_last:
                self._global_recent_pointer_at = now
                self._global_recent_pointer_pos = pos
                if not down:
                    self._global_recent_click_at = now
                    self._global_recent_click_pos = pos
            if down and not was_down:
                self._global_recent_pointer_at = now
                self._global_recent_pointer_pos = pos
                self._global_recent_click_at = 0.0
                self._global_recent_click_pos = None
                self._global_left_down_at = now
                self._global_left_down_pos = pos
            elif not down and was_down:
                start = getattr(self, "_global_left_down_pos", None)
                duration = max(0.0, now - float(getattr(self, "_global_left_down_at", now)))
                distance = 0.0
                if start is not None and pos is not None:
                    distance = math.hypot(float(pos[0] - start[0]), float(pos[1] - start[1]))
                self._global_recent_pointer_at = now
                self._global_recent_pointer_pos = pos or start
                if duration <= 0.65 and distance <= 18.0:
                    self._global_recent_click_at = now
                    self._global_recent_click_pos = pos or start
                else:
                    self._global_recent_click_at = 0.0
                    self._global_recent_click_pos = None
                self._global_left_down_pos = None
            self._global_left_down = down

    def _source_app_recent_click(self, now: float) -> bool:
        click_at = float(getattr(self, "_source_app_recent_click_at", 0.0) or 0.0)
        return click_at > 0.0 and 0.0 <= now - click_at <= 1.8

    def _source_app_recent_click_position(self, now: float) -> tuple[int, int] | None:
        if not self._source_app_recent_click(now):
            return None
        pos = getattr(self, "_source_app_recent_click_pos", None)
        if not isinstance(pos, tuple) or len(pos) != 2:
            return None
        try:
            return int(pos[0]), int(pos[1])
        except Exception:
            return None

    def _source_app_recent_pointer_position(self, now: float) -> tuple[int, int] | None:
        if not self._source_app_recent_pointer_activity(now):
            return None
        pos = getattr(self, "_source_app_recent_pointer_pos", None)
        if not isinstance(pos, tuple) or len(pos) != 2:
            return None
        try:
            return int(pos[0]), int(pos[1])
        except Exception:
            return None

    def _global_recent_click(self, now: float) -> bool:
        lock = getattr(self, "_global_pointer_lock", None)
        if lock is None:
            click_at = float(getattr(self, "_global_recent_click_at", 0.0) or 0.0)
        else:
            with lock:
                click_at = float(getattr(self, "_global_recent_click_at", 0.0) or 0.0)
        return click_at > 0.0 and 0.0 <= now - click_at <= 1.8

    def _global_recent_click_position(self, now: float) -> tuple[int, int] | None:
        if not self._global_recent_click(now):
            return None
        lock = getattr(self, "_global_pointer_lock", None)
        if lock is None:
            pos = getattr(self, "_global_recent_click_pos", None)
        else:
            with lock:
                pos = getattr(self, "_global_recent_click_pos", None)
        if not isinstance(pos, tuple) or len(pos) != 2:
            return None
        try:
            return int(pos[0]), int(pos[1])
        except Exception:
            return None

    def _global_recent_pointer_position(self, now: float) -> tuple[int, int] | None:
        if not self._global_recent_pointer_activity(now):
            return None
        lock = getattr(self, "_global_pointer_lock", None)
        if lock is None:
            pos = getattr(self, "_global_recent_pointer_pos", None)
        else:
            with lock:
                pos = getattr(self, "_global_recent_pointer_pos", None)
        if not isinstance(pos, tuple) or len(pos) != 2:
            return None
        try:
            return int(pos[0]), int(pos[1])
        except Exception:
            return None

    def _source_app_recent_pointer_activity(self, now: float) -> bool:
        pointer_at = float(getattr(self, "_source_app_recent_pointer_at", 0.0) or 0.0)
        click_at = float(getattr(self, "_source_app_recent_click_at", 0.0) or 0.0)
        activity_at = max(pointer_at, click_at)
        return activity_at > 0.0 and 0.0 <= now - activity_at <= 1.8

    def _global_recent_pointer_activity(self, now: float) -> bool:
        lock = getattr(self, "_global_pointer_lock", None)
        if lock is None:
            pointer_at = float(getattr(self, "_global_recent_pointer_at", 0.0) or 0.0)
            click_at = float(getattr(self, "_global_recent_click_at", 0.0) or 0.0)
        else:
            with lock:
                pointer_at = float(getattr(self, "_global_recent_pointer_at", 0.0) or 0.0)
                click_at = float(getattr(self, "_global_recent_click_at", 0.0) or 0.0)
        activity_at = max(pointer_at, click_at)
        return activity_at > 0.0 and 0.0 <= now - activity_at <= 1.8

    def _detect_music_player_service(self, source_app: str, raw_source_app: str = "") -> str:
        for service in ("netease", "qishui", "qq", "kugou"):
            if self._music_player_source_matches(service, source_app, raw_source_app):
                return service
        return "system"

    def _music_player_adapter_for(self, source_app: str, raw_source_app: str = "") -> str:
        mode = self._music_player_mode()
        detected = self._detect_music_player_service(source_app, raw_source_app)
        profiles = self._music_app_profiles()
        if profiles is not None:
            candidate = "system"
            if mode == "netease_betterncm" and self._music_player_source_matches(mode, source_app, raw_source_app):
                candidate = mode
            elif detected != "system":
                candidate = detected
            elif self._music_player_source_matches(mode, source_app, raw_source_app):
                candidate = mode
            if candidate != "system" and self._music_app_profile_allows_adapter(candidate, source_app, raw_source_app):
                return self._remember_confirmed_music_player_adapter(candidate, source_app, raw_source_app)
            if candidate != "system":
                return "system"
            path_candidates = []
            if mode in {"netease", "netease_betterncm", "qishui", "qq", "kugou"}:
                path_candidates.append(mode)
            path_candidates.extend(("netease", "qq", "qishui", "kugou"))
            seen: set[str] = set()
            for path_candidate in path_candidates:
                if path_candidate in seen:
                    continue
                seen.add(path_candidate)
                if self._music_app_profile_allows_adapter(path_candidate, source_app, raw_source_app):
                    return self._remember_confirmed_music_player_adapter(path_candidate, source_app, raw_source_app)
            sticky = self._sticky_music_player_adapter_for(source_app, raw_source_app, detected)
            if sticky != "system":
                return sticky
            return "system"
        if not self._player_progress_plugin_enabled():
            if detected in {"netease", "qq"}:
                return detected
            if mode in {"netease", "qq"} and self._music_player_source_matches(mode, source_app, raw_source_app):
                return mode
            return "system"
        if self._player_progress_auto_detect_enabled():
            if detected != "system":
                return detected
            if self._music_player_source_matches(mode, source_app, raw_source_app):
                return mode
            return "system"
        if self._music_player_source_matches(mode, source_app, raw_source_app):
            return mode
        return "system"

    def _remember_confirmed_music_player_adapter(self, adapter: str, source_app: str, raw_source_app: str) -> str:
        adapter = str(adapter or "system").strip().casefold()
        if adapter != "system":
            self._last_confirmed_music_player_adapter = adapter
            self._last_confirmed_music_player_adapter_until = time.monotonic() + 8.0
            self._last_confirmed_music_player_source_key = self._music_player_session_hint_key(source_app, raw_source_app)
        return adapter

    def _sticky_music_player_adapter_for(self, source_app: str, raw_source_app: str, detected: str) -> str:
        if str(detected or "system").strip().casefold() != "system":
            return "system"
        adapter = str(getattr(self, "_last_confirmed_music_player_adapter", "system") or "system").strip().casefold()
        if adapter == "system":
            return "system"
        until = float(getattr(self, "_last_confirmed_music_player_adapter_until", 0.0) or 0.0)
        if time.monotonic() > until:
            return "system"
        current_key = self._music_player_session_hint_key(source_app, raw_source_app)
        previous_key = str(getattr(self, "_last_confirmed_music_player_source_key", "") or "")
        if current_key and previous_key and current_key != previous_key:
            return "system"
        if not self._music_app_profile_sticky_enabled(adapter):
            return "system"
        return adapter

    def _music_player_session_hint_key(self, source_app: str, raw_source_app: str) -> str:
        return self._stable_text_key(f"{source_app}\0{raw_source_app}")

    def _music_app_profile_sticky_enabled(self, service: str) -> bool:
        profiles = self._music_app_profiles()
        if profiles is None:
            return True
        profile = self._music_app_profile(service) or {}
        if not bool(profile.get("sync_enabled", False)):
            return False
        service = str(service or "").strip().casefold()
        if service == "netease_betterncm" and not bool(profile.get("betterncm_enabled", False)):
            return False
        return bool(self._effective_music_app_profile_path(service, profile))

    def _consume_recent_click_anchor(self, click_pos: tuple[int, int] | None):
        if click_pos is None:
            return
        self._source_app_recent_click_at = 0.0
        self._source_app_recent_click_pos = None
        lock = getattr(self, "_global_pointer_lock", None)
        if lock is None:
            self._global_recent_click_at = 0.0
            self._global_recent_click_pos = None
        else:
            with lock:
                self._global_recent_click_at = 0.0
                self._global_recent_click_pos = None

    def _is_playing(self, playback) -> bool:
        try:
            status = playback.playback_status
            if PlaybackStatus is not None:
                return status == PlaybackStatus.PLAYING
            return int(status) == 4
        except Exception:
            return True

    def _timespan_seconds(self, value) -> float:
        try:
            if value is None:
                return 0.0
            duration = getattr(value, "total_seconds", None)
            if callable(duration):
                return max(0.0, float(duration()))
            return max(0.0, float(value.duration) / 10_000_000.0)
        except Exception:
            try:
                return max(0.0, float(value) / 10_000_000.0)
            except Exception:
                try:
                    return max(0.0, float(value))
                except Exception:
                    return 0.0

    def _colors_from_art(self, art: bytes, fallback: ColorToken, secondary: ColorToken) -> tuple[ColorToken, ColorToken]:
        if not art:
            return fallback, secondary
        image = QImage.fromData(art)
        if image.isNull():
            return fallback, secondary
        scaled = image.scaled(
            56,
            56,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        clusters: list[dict[str, float]] = []
        total_pixels = max(1, scaled.width() * scaled.height())
        valid_pixels = 0
        low_saturation_pixels: list[tuple[float, float, float]] = []

        for y in range(scaled.height()):
            for x in range(scaled.width()):
                color = QColor(scaled.pixel(x, y))
                if color.alpha() < 24:
                    continue
                hue, saturation, lightness, _alpha = color.getHslF()
                if lightness < 0.16 or lightness > 0.86:
                    continue
                if saturation < 0.12:
                    continue
                if hue < 0:
                    continue
                valid_pixels += 1
                sample = (hue, saturation, lightness)
                if saturation < 0.22:
                    low_saturation_pixels.append(sample)
                self._add_color_cluster(clusters, sample)

        if not clusters:
            return self._soft_fallback_pair(fallback, secondary)

        valid_ratio = valid_pixels / total_pixels
        ranked = sorted(
            clusters,
            key=lambda cluster: self._cluster_score(cluster, valid_pixels, total_pixels),
            reverse=True,
        )
        if valid_ratio < 0.055:
            ranked = self._rank_sparse_color_clusters(ranked, low_saturation_pixels, valid_pixels, total_pixels)

        primary_cluster = ranked[0]
        secondary_cluster = self._secondary_cluster(primary_cluster, ranked)
        primary = self._token_from_cluster(primary_cluster)
        if secondary_cluster is None:
            secondary_token = self._related_token(primary, 0.14 if primary_cluster["l"] < 0.48 else -0.14)
        else:
            secondary_token = self._token_from_cluster(secondary_cluster)
        return primary, secondary_token

    def _add_color_cluster(self, clusters: list[dict[str, float]], sample: tuple[float, float, float]):
        hue, saturation, lightness = sample
        for cluster in clusters:
            if self._hue_distance_value(cluster["h"], hue) < 18.0 / 360.0 and abs(cluster["s"] - saturation) < 0.20 and abs(cluster["l"] - lightness) < 0.18:
                count = cluster["count"] + 1.0
                cluster["h_sin"] += math.sin(hue * math.tau)
                cluster["h_cos"] += math.cos(hue * math.tau)
                cluster["s_sum"] += saturation
                cluster["l_sum"] += lightness
                cluster["count"] = count
                cluster["h"] = math.atan2(cluster["h_sin"], cluster["h_cos"]) / math.tau
                if cluster["h"] < 0:
                    cluster["h"] += 1.0
                cluster["s"] = cluster["s_sum"] / count
                cluster["l"] = cluster["l_sum"] / count
                return
        clusters.append(
            {
                "h": hue,
                "s": saturation,
                "l": lightness,
                "h_sin": math.sin(hue * math.tau),
                "h_cos": math.cos(hue * math.tau),
                "s_sum": saturation,
                "l_sum": lightness,
                "count": 1.0,
            }
        )

    def _cluster_score(self, cluster: dict[str, float], valid_pixels: int, total_pixels: int) -> tuple[float, float, float, float]:
        color_ratio = cluster["count"] / max(1, valid_pixels)
        total_ratio = cluster["count"] / max(1, total_pixels)
        saturation = cluster["s"]
        lightness = cluster["l"]
        moderate_saturation = 1.0 - min(1.0, abs(saturation - 0.34) / 0.44)
        glow_lightness = 1.0 - min(1.0, abs(lightness - 0.52) / 0.36)
        return color_ratio, total_ratio, moderate_saturation, glow_lightness

    def _rank_sparse_color_clusters(self, ranked: list[dict[str, float]], low_saturation_pixels: list[tuple[float, float, float]], valid_pixels: int, total_pixels: int) -> list[dict[str, float]]:
        if not low_saturation_pixels:
            return sorted(
                ranked,
                key=lambda cluster: (
                    cluster["count"] / max(1, valid_pixels),
                    cluster["count"] / max(1, total_pixels),
                    -cluster["s"],
                ),
                reverse=True,
            )
        low_clusters: list[dict[str, float]] = []
        for sample in low_saturation_pixels:
            self._add_color_cluster(low_clusters, sample)
        if not low_clusters:
            return ranked
        low_ranked = sorted(low_clusters, key=lambda cluster: self._cluster_score(cluster, valid_pixels, total_pixels), reverse=True)
        return low_ranked + [cluster for cluster in ranked if cluster not in low_ranked]

    def _secondary_cluster(self, primary: dict[str, float], ranked: list[dict[str, float]]) -> dict[str, float] | None:
        for cluster in ranked[1:]:
            hue_distance = self._hue_distance_value(primary["h"], cluster["h"])
            if hue_distance < 10.0 / 360.0 and abs(primary["l"] - cluster["l"]) < 0.12 and abs(primary["s"] - cluster["s"]) < 0.14:
                continue
            return cluster
        return None

    def _token_from_cluster(self, cluster: dict[str, float]) -> ColorToken:
        return self._correct_glow_token(cluster["h"], cluster["s"], cluster["l"])

    def _correct_glow_token(self, hue: float, saturation: float, lightness: float) -> ColorToken:
        if lightness > 0.72:
            lightness = max(0.60, min(0.68, lightness - 0.16))
            saturation = max(saturation, 0.22)
            saturation = min(0.36, saturation + 0.04)
        elif lightness < 0.28:
            lightness = max(0.34, min(0.46, lightness + 0.14))
            saturation = min(0.45, max(saturation, saturation + 0.04))
        if saturation < 0.18:
            saturation = max(0.20, min(0.32, saturation + 0.10))
        saturation = max(0.18, min(0.55, saturation))
        lightness = max(0.30, min(0.70, lightness))
        color = QColor.fromHslF(hue % 1.0, saturation, lightness, 1.0)
        return ColorToken(color.red(), color.green(), color.blue())

    def _related_token(self, token: ColorToken, lightness_delta: float) -> ColorToken:
        color = QColor(token.red, token.green, token.blue)
        hue, saturation, lightness, _alpha = color.getHslF()
        if hue < 0:
            hue = 0.58
        return self._correct_glow_token(hue, saturation, lightness + lightness_delta)

    def _soft_fallback_pair(self, fallback: ColorToken, secondary: ColorToken) -> tuple[ColorToken, ColorToken]:
        primary_color = QColor(fallback.red, fallback.green, fallback.blue)
        secondary_color = QColor(secondary.red, secondary.green, secondary.blue)
        primary_h, primary_s, primary_l, _ = primary_color.getHslF()
        secondary_h, secondary_s, secondary_l, _ = secondary_color.getHslF()
        if primary_h < 0:
            primary_h, primary_s, primary_l = 0.58, 0.24, 0.48
        primary = self._correct_glow_token(primary_h, primary_s, primary_l)
        if secondary_h < 0 or self._hue_distance_value(primary_h, secondary_h) > 35.0 / 360.0:
            secondary_token = self._related_token(primary, 0.12)
        else:
            secondary_token = self._correct_glow_token(secondary_h, secondary_s, secondary_l)
        return primary, secondary_token

    def _glow_candidate(self, bucket: tuple[int, int, int, int, float, float, float], total_pixels: int):
        r, g, b, count, sat_sum, val_sum, hue_sum = bucket
        count = max(1, count)
        sat = sat_sum / count
        val = val_sum / count
        hue = hue_sum / count
        if sat < 0.25 or val < 0.25 or val > 0.75:
            return None
        avg_r = r // count
        avg_g = g // count
        avg_b = b // count
        coverage = min(0.22, count / max(1, total_pixels)) / 0.22
        ideal_sat = 1.0 - min(1.0, abs(sat - 0.62) / 0.38)
        ideal_val = 1.0 - min(1.0, abs(val - 0.52) / 0.30)
        score = ideal_sat * 0.42 + ideal_val * 0.38 + coverage * 0.20
        color = QColor.fromHsvF(hue, max(0.36, min(0.82, sat)), max(0.36, min(0.68, val)), 1.0)
        return score, hue, ColorToken(color.red(), color.green(), color.blue())

    def _hue_distance_value(self, a: float, b: float) -> float:
        delta = abs(a - b) % 1.0
        return min(delta, 1.0 - delta)

    def _rotated_token(self, token: ColorToken, amount: float) -> ColorToken:
        color = QColor(token.red, token.green, token.blue)
        hue, sat, val, _ = color.getHsvF()
        if hue < 0:
            return ColorToken(150, 97, 147)
        rotated = QColor.fromHsvF((hue + amount) % 1.0, max(0.42, sat), max(0.42, min(0.68, val)), 1.0)
        return ColorToken(rotated.red(), rotated.green(), rotated.blue())

    def _bright_token(self, bucket: tuple[int, int, int, int, float], vivid: bool = True) -> ColorToken:
        r, g, b, count, _score = bucket
        count = max(1, count)
        color = QColor(r // count, g // count, b // count)
        hue, sat, val, _ = color.getHsvF()
        if hue < 0:
            gray = max(48, min(208, int((r + g + b) / (3 * count))))
            return ColorToken(gray, gray, gray)
        if vivid:
            sat = max(0.54, min(0.96, sat + 0.18))
            val = max(0.58, min(0.96, val + 0.18))
        else:
            sat = 0.0
            val = max(0.44, min(0.88, val + 0.10))
        bright = QColor.fromHsvF(hue, sat, val, 1.0)
        return ColorToken(bright.red(), bright.green(), bright.blue())

    def _average_token(self, bucket: tuple[int, int, int, int]) -> ColorToken:
        r, g, b, count = bucket
        count = max(1, count)
        return ColorToken(r // count, g // count, b // count)

    def _track_key(self, source_app: str, title: str, artist: str, album: str, duration: float) -> str:
        raw = f"{source_app}\0{title}\0{artist}\0{album}"
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]

    def _has_multiline_lyrics(self, timed_lyrics) -> bool:
        return any("\n" in str(getattr(line, "text", "") or "") for line in timed_lyrics or ())
