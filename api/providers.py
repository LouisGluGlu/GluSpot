from __future__ import annotations

import asyncio
import base64
from concurrent.futures import TimeoutError as FutureTimeoutError
import ctypes
import hashlib
import io
import json
import math
import os
import re
import secrets
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.request import Request, urlopen
from urllib.parse import parse_qs, urlencode, urlsplit

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage

from api.audio_analyzer import LiveAudioAnalyzer
from api.audio_routes import AudioRouteSnapshot
from api.artwork_sources import (
    ARTWORK_USER_AGENT, MusicBrainzArtworkResolver,
    artwork_duration_matches, artwork_text_key, normalize_artwork_url, read_artwork_response,
)
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
    "qq-uia-lyrics",
}
STABILIZED_LOCAL_POSITION_SOURCES = {"local-estimate", "local-background", "local-seek", "interactive-hold"}
STABILIZED_PLAYER_POSITION_SOURCES = {
    "netease-uia-progress",
    "netease-uia-lyrics",
    "qq-uia-progress",
    "qq-uia-lyrics",
}
PLAYER_POSITION_ADAPTERS = {"netease", "netease_betterncm", "qq"}
LYRICS_FOUND_PROGRESS_RETRY_DELAY_SECONDS = 5.0
LYRICS_FOUND_PROGRESS_RETRY_WINDOW_SECONDS = 7.2
PLAYER_ANCHOR_FAST_REFRESH_MAX_SECONDS = 5.5
TRACK_CHANGE_STRONG_REFRESH_SECONDS = 10.0
AUDIO_ANALYZER_RETRY_DELAY_SECONDS = 12.0

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

    def get_audio_route_snapshot(self) -> AudioRouteSnapshot:
        return AudioRouteSnapshot(state="unavailable", status_message="当前媒体来源不支持设备管理")

    def refresh_audio_outputs(self) -> bool:
        return False

    def select_audio_output(self, device_id: str) -> bool:
        return False

    def stop_audio_cast(self) -> bool:
        return False

    def send_playback_command(self, command: PlaybackCommand) -> bool:
        return False

    def seek_playback_position(self, position_seconds: float, expected_track_id: str | None = None) -> bool:
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
    # Request a generous source size; a CDN can return its smaller original.
    # The reference's 1200-pixel threshold only schedules background lookup.
    CURRENT_ART_TARGET_SIDE = 2560
    CURRENT_ART_LOOKUP_SIDE = 1200
    CURRENT_ART_PREVIEW_SIDE = 320
    QUEUE_ART_MAX_BYTES = 8 * 1024 * 1024

    def __init__(self, fallback=None, settings_getter=None):
        if fallback is None:
            fallback = EmptyIslandDataProvider()
        self.fallback = fallback
        try:
            self.lyrics_store = LocalLyricsStore(settings_getter=settings_getter)
        except OSError:
            self.lyrics_store = LocalLyricsStore(root=Path(__file__).resolve().parents[1] / "lyrics", settings_getter=settings_getter)
        self._loop = None
        self._thread = None
        self._manager = None
        self._last_data: IslandVisualData | None = None
        self._last_refresh_at = 0.0
        self._refresh_interval = 0.55
        self._refresh_future = None
        self._refresh_future_cache_epoch = 0
        self._music_cache_lock = threading.RLock()
        self._music_cache_epoch = 0
        self._artwork_cache_epoch = 0
        self._art_cache: dict[str, bytes] = {}
        self._pending_thumbnail_tasks: dict[str, asyncio.Task] = {}
        self._ready_thumbnail_bytes: dict[str, bytes] = {}
        self._queue_art_cache: dict[str, bytes] = {}
        self._queue_art_inflight: set[str] = set()
        self._queue_art_failed_until: dict[str, float] = {}
        self._queue_art_lock = threading.Lock()
        self._queue_art_slots = threading.BoundedSemaphore(3)
        self._artwork_cache_root = self._queue_art_disk_path("")
        if self._artwork_cache_root is not None:
            self._artwork_cache_root = self._artwork_cache_root.parent.absolute()
        self._current_art_url_by_track_id: dict[str, str] = {}
        self._current_art_lookup_inflight: set[str] = set()
        self._current_art_lookup_failed_until: dict[str, float] = {}
        self._current_art_lookup_lock = threading.Lock()
        self._current_art_lookup_generations: dict[str, tuple[object, float]] = {}
        self._current_art_lookup_slots = threading.BoundedSemaphore(2)
        self._current_art_lookup_sources: dict[str, str] = {}
        self._current_art_rejected_urls: dict[str, tuple[float, str]] = {}
        self._musicbrainz_artwork_resolver = MusicBrainzArtworkResolver()
        self._online_current_art_lookup_enabled = True
        self._provider_status = "starting"
        self._last_ready_at = 0.0
        self.settings_getter = settings_getter or (lambda key, default=None: default)
        self._local_seek_position_seconds: float | None = None
        self._local_seek_at = 0.0
        self._local_seek_track_id = ""
        self._pending_player_position_discontinuity: dict[str, object] | None = None
        self._last_foreground_snapshot: dict[str, object] = {}
        self._source_app_foreground = False
        self._source_app_interactive = False
        self._source_app_foreground_at = 0.0
        self._raw_source_app_user_model_id = ""
        self._music_player_adapter = "system"
        self._last_confirmed_music_player_adapter = "system"
        self._last_confirmed_music_player_adapter_until = 0.0
        self._last_confirmed_music_player_source_key = ""
        self._recent_skip_command_at = 0.0
        self._player_anchor_fast_refresh_until = 0.0
        self._media_strong_tracking_until = 0.0
        self._last_playback_toggle_at = 0.0
        self._last_playback_toggle_track_id = ""
        self._raw_playback_track_id = ""
        self._raw_playback_is_playing: bool | None = None
        self._raw_playback_changed_at = 0.0
        self._lyrics_found_progress_retry_track_id = ""
        self._lyrics_found_progress_retry_not_before = 0.0
        self._lyrics_found_progress_retry_until = 0.0
        self._last_raw_timeline_track_id = ""
        self._last_raw_timeline_position: float | None = None
        self._last_raw_timeline_at = 0.0
        self._raw_timeline_observation_signature: tuple[object, ...] | None = None
        self._raw_timeline_observation_sampled_at = 0.0
        self._netease_lyric_sync_track_ids: set[str] = set()
        self._netease_upcoming_prefetch_keys: set[tuple[str, str]] = set()
        self._upcoming_lyric_prefetch_track_ids: set[str] = set()
        self._media_session_count = 0
        self._media_session_candidates: tuple[str, ...] = ()
        self._media_session_error = ""
        self._selected_media_session_index = -1
        self.audio_analyzer: LiveAudioAnalyzer | None = None
        self._audio_analyzer_retry_not_before = 0.0
        self._audio_analyzer_last_error = ""
        self.netease_position: NeteasePositionReader | None = None
        self.qq_position: QQPositionReader | None = None
        self.plugin_provider = RealPluginProvider(settings_getter)
        self.message_detector = WindowsMessageDetector(settings_getter)
        self._audio_route_controller = None
        self._audio_route_init_lock = threading.Lock()
        self._audio_route_closed = False
        self._start_loop()

    def start_audio_outputs(self):
        """Start passive endpoint monitoring; mDNS starts when the chooser opens."""
        if getattr(self, "_audio_route_controller", None) is not None:
            return self._audio_route_controller
        lock = getattr(self, "_audio_route_init_lock", None)
        if lock is None:
            lock = self._audio_route_init_lock = threading.Lock()
        with lock:
            if getattr(self, "_audio_route_controller", None) is None:
                if getattr(self, "_audio_route_closed", False):
                    raise RuntimeError("Audio output service is closed")
                from api.audio_route_controller import AudioRouteController
                source_id = str(self.settings_getter("v2_airplay_sender_id", ""))
                if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", source_id):
                    source_id = "02:" + ":".join(f"{part:02X}" for part in secrets.token_bytes(5))
                    setter = getattr(self, "settings_setter", None)
                    if callable(setter):
                        setter("v2_airplay_sender_id", source_id)
                self._audio_route_controller = AudioRouteController(
                    latency_seconds=self.settings_getter("v2_homepod_lyric_delay_seconds", 1.5), source_id=source_id)
        return self._audio_route_controller

    def get_audio_route_snapshot(self) -> AudioRouteSnapshot:
        controller = getattr(self, "_audio_route_controller", None)
        return controller.snapshot() if controller is not None else AudioRouteSnapshot()

    def refresh_audio_outputs(self) -> bool:
        return self.start_audio_outputs().refresh()

    def select_audio_output(self, device_id: str) -> bool:
        return self.start_audio_outputs().select(str(device_id))

    def stop_audio_cast(self) -> bool:
        controller = getattr(self, "_audio_route_controller", None)
        return controller.stop_cast() if controller is not None else False

    def add_homepod(self, host: str, port: int = 7000) -> bool:
        return self.start_audio_outputs().add_homepod(host, port)

    def set_audio_output_latency(self, seconds: float) -> bool:
        controller = self.start_audio_outputs()
        value = controller._normalize_latency(seconds)
        setter = getattr(self, "settings_setter", None)
        if callable(setter):
            setter("v2_homepod_lyric_delay_seconds", value)
        return controller.set_latency(value)

    def close_audio_outputs(self):
        lock = getattr(self, "_audio_route_init_lock", None)
        if lock is None:
            lock = self._audio_route_init_lock = threading.Lock()
        with lock:
            self._audio_route_closed = True
            controller = getattr(self, "_audio_route_controller", None)
            if controller is not None:
                controller.close()

    def get_visual_data(self) -> IslandVisualData:
        now = time.monotonic()
        lock = getattr(self, "_music_cache_lock", None)
        if lock is None:
            lock = self._music_cache_lock = threading.RLock()
        if not lock.acquire(blocking=False):
            # Cache deletion runs on a worker. Keep the GUI's existing media
            # snapshot moving without waiting for disk I/O or starting work.
            data = getattr(self, "_last_data", None)
            if data is None:
                return self.fallback.get_visual_data()
            projected = self._project_visual_position(data, now, getattr(self, "_last_ready_at", 0.0))
            return replace(projected, messages=self.message_detector.messages(), plugins=self.plugin_provider.plugins())
        try:
            if self._loop is None:
                return self.fallback.get_visual_data()
            self._consume_refresh_future(now)
            refresh_interval = self._current_refresh_interval(now)
            force_anchor_refresh = self._lyrics_found_progress_retry_cached_refresh_due(now)
            if self._last_data is not None and not force_anchor_refresh and now - self._last_refresh_at < refresh_interval:
                projected = self._project_visual_position(self._last_data, now, self._last_ready_at)
                return replace(projected, messages=self.message_detector.messages(), plugins=self.plugin_provider.plugins())
            self._last_refresh_at = now
            if self._refresh_future is None or self._refresh_future.done():
                try:
                    self._refresh_future_cache_epoch = int(getattr(self, "_music_cache_epoch", 0))
                    self._refresh_future = asyncio.run_coroutine_threadsafe(self._read_visual_data(), self._loop)
                except Exception:
                    self._refresh_future = None
            return self._cached_or_status_visual(now)
        finally:
            lock.release()

    def clear_music_cache(self, kind: str) -> dict[str, object]:
        """Clear GluSpot-generated music data while preserving the media clock."""
        if kind not in {"artwork", "lyrics"}:
            raise ValueError("music cache kind must be 'artwork' or 'lyrics'")
        result: dict[str, object] = {"kind": kind, "deleted_files": 0, "deleted_bytes": 0, "errors": [], "refresh_requested": True}
        lock = getattr(self, "_music_cache_lock", None)
        if lock is None:
            lock = self._music_cache_lock = threading.RLock()
        with lock:
            self._music_cache_epoch = int(getattr(self, "_music_cache_epoch", 0)) + 1
            future = getattr(self, "_refresh_future", None)
            self._refresh_future = None
            if future is not None:
                try:
                    future.cancel()
                except Exception:
                    pass
            if kind == "artwork":
                lookup_lock = getattr(self, "_current_art_lookup_lock", None)
                if lookup_lock is None:
                    lookup_lock = self._current_art_lookup_lock = threading.Lock()
                art_lock = getattr(self, "_queue_art_lock", None)
                if art_lock is None:
                    art_lock = self._queue_art_lock = threading.Lock()
                with lookup_lock, art_lock:
                    self._artwork_cache_epoch = int(getattr(self, "_artwork_cache_epoch", 0)) + 1
                    for name in (
                        "_art_cache", "_ready_thumbnail_bytes", "_queue_art_cache", "_queue_art_failed_until",
                        "_queue_art_inflight", "_current_art_url_by_track_id", "_current_art_lookup_inflight",
                        "_current_art_lookup_failed_until", "_current_art_lookup_generations",
                        "_current_art_lookup_sources", "_current_art_rejected_urls",
                    ):
                        values = getattr(self, name, None)
                        if isinstance(values, (dict, set)):
                            values.clear()
                    pending = getattr(self, "_pending_thumbnail_tasks", {})
                    tasks = list(pending.values()) if isinstance(pending, dict) else []
                    if isinstance(pending, dict):
                        pending.clear()
                    for task in tasks:
                        try:
                            task.get_loop().call_soon_threadsafe(task.cancel)
                        except (RuntimeError, AttributeError):
                            pass
                    self._netease_art_source_failed_until = 0.0
                    resolver = getattr(self, "_musicbrainz_artwork_resolver", None)
                    if resolver is not None and callable(getattr(resolver, "clear_cache", None)):
                        resolver.clear_cache()
                    self._album_art_min_side.cache_clear()
                    self._clear_artwork_disk_cache(result)
            else:
                try:
                    cleared = self.lyrics_store.clear_generated_cache()
                    result["deleted_files"] = int(cleared.get("deleted_files", 0))
                    result["deleted_bytes"] = int(cleared.get("deleted_bytes", 0))
                    result["errors"] = list(cleared.get("errors", ()))
                except Exception as exc:
                    result["errors"].append(f"Lyrics cache: {type(exc).__name__}: {exc}")
                for name in ("_netease_upcoming_prefetch_keys", "_upcoming_lyric_prefetch_track_ids"):
                    values = getattr(self, name, None)
                    if isinstance(values, set):
                        values.clear()
            data = getattr(self, "_last_data", None)
            if data is not None:
                if kind == "artwork":
                    track = replace(
                        data.track, album_art_bytes=b"", album_art_source="", album_art_lookup_state="pending",
                        album_queue=tuple(replace(entry, album_art_bytes=b"") for entry in data.track.album_queue),
                    )
                else:
                    # Otherwise the same-track retention branch suppresses
                    # another lyric lookup after its generated file is removed.
                    track = replace(data.track, lyrics=("查找歌词中",), timed_lyrics=(), lyrics_source="")
                self._last_data = replace(data, track=track)
            self._last_refresh_at = 0.0
            self._music_cache_last_clear = {
                "kind": kind, "deleted_files": int(result["deleted_files"]),
                "deleted_bytes": int(result["deleted_bytes"]), "error_count": len(result["errors"]),
                "refresh_requested": True,
            }
        return result

    def _clear_artwork_disk_cache(self, result: dict[str, object]) -> None:
        path = self._queue_art_disk_path("")
        if path is None:
            return
        root = path.parent.absolute()
        anchor = getattr(self, "_artwork_cache_root", root)
        try:
            if anchor is None or root != anchor or root.resolve() != root or root.is_symlink() or root.is_junction():
                result["errors"].append("Artwork cache directory was redirected; no files were removed")
                return
            if not root.exists():
                return
            for candidate in root.iterdir():
                # Only our SHA-256 URL cache files, including an interrupted
                # atomic write, are owned by this API. Do not recurse.
                if candidate.suffix not in {".img", ".tmp"}:
                    continue
                parts = candidate.stem.split(".")
                digest = parts[0]
                if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                    continue
                if len(parts) > 1 and (
                    candidate.suffix != ".tmp" or len(parts) != 2 or len(parts[1]) != 8
                    or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_" for char in parts[1])
                ):
                    continue
                if candidate.is_symlink() or candidate.is_junction() or not candidate.is_file():
                    continue
                if candidate.parent.resolve() != root or candidate.resolve().parent != root:
                    continue
                try:
                    size = candidate.stat().st_size
                    candidate.unlink()
                    result["deleted_files"] += 1
                    result["deleted_bytes"] += size
                except OSError as exc:
                    result["errors"].append(f"{candidate.name}: {exc}")
        except OSError as exc:
            result["errors"].append(f"Artwork cache directory: {exc}")

    def _current_refresh_interval(self, now: float) -> float:
        interval = float(getattr(self, "_refresh_interval", 0.55) or 0.55)
        if now < float(getattr(self, "_media_strong_tracking_until", 0.0) or 0.0):
            interval = min(interval, 0.12)
        if self._supports_player_position_adapter(self._music_player_adapter):
            fast_until = float(getattr(self, "_player_anchor_fast_refresh_until", 0.0) or 0.0)
            if fast_until > 0.0 and now <= fast_until:
                interval = min(interval, 0.12)
        if self._supports_player_position_adapter(self._music_player_adapter) and (
            self._source_app_interactive or (self._source_app_foreground_at and now - self._source_app_foreground_at <= 3.5)
        ):
            interval = min(interval, 0.14)
        lyrics_found_retry_interval = self._lyrics_found_progress_retry_refresh_interval(now)
        if lyrics_found_retry_interval is not None:
            interval = min(interval, lyrics_found_retry_interval)
        return max(0.05, interval)

    def _mark_player_anchor_fast_refresh(self, now: float | None = None, duration: float = 1.35):
        anchor_at = time.monotonic() if now is None else float(now)
        try:
            span = max(0.25, min(PLAYER_ANCHOR_FAST_REFRESH_MAX_SECONDS, float(duration)))
        except (TypeError, ValueError):
            span = 1.35
        self._player_anchor_fast_refresh_until = max(
            float(getattr(self, "_player_anchor_fast_refresh_until", 0.0) or 0.0),
            anchor_at + span,
        )

    def _schedule_lyrics_found_progress_retry(self, track_id: str, now: float):
        if not track_id:
            return
        anchor_at = float(now)
        self._lyrics_found_progress_retry_track_id = str(track_id)
        self._lyrics_found_progress_retry_not_before = anchor_at + LYRICS_FOUND_PROGRESS_RETRY_DELAY_SECONDS
        self._lyrics_found_progress_retry_until = anchor_at + LYRICS_FOUND_PROGRESS_RETRY_WINDOW_SECONDS

    def _lyrics_found_progress_retry_due(self, track_id: str, now: float) -> bool:
        if not track_id or str(getattr(self, "_lyrics_found_progress_retry_track_id", "") or "") != str(track_id):
            return False
        not_before = float(getattr(self, "_lyrics_found_progress_retry_not_before", 0.0) or 0.0)
        retry_until = float(getattr(self, "_lyrics_found_progress_retry_until", 0.0) or 0.0)
        current = float(now)
        if retry_until <= 0.0 or current > retry_until:
            self._clear_lyrics_found_progress_retry(track_id)
            return False
        return current >= not_before

    def _clear_lyrics_found_progress_retry(self, track_id: str = ""):
        if track_id and str(getattr(self, "_lyrics_found_progress_retry_track_id", "") or "") not in {"", str(track_id)}:
            return
        self._lyrics_found_progress_retry_track_id = ""
        self._lyrics_found_progress_retry_not_before = 0.0
        self._lyrics_found_progress_retry_until = 0.0

    def _consume_refresh_future(self, now: float):
        lock = getattr(self, "_music_cache_lock", None)
        if lock is None:
            lock = self._music_cache_lock = threading.RLock()
        with lock:
            self._consume_current_refresh_future(now)

    def _consume_current_refresh_future(self, now: float):
        future = self._refresh_future
        if future is None or not future.done():
            return
        self._refresh_future = None
        if int(getattr(self, "_refresh_future_cache_epoch", 0)) != int(getattr(self, "_music_cache_epoch", 0)):
            return
        try:
            data = future.result()
        except Exception:
            data = None
        if data is None:
            return
        data = self._advance_delayed_media_sample(data, now)
        data = self._stabilize_incoming_position(data, now)
        data = self._apply_recent_local_seek_to_visual_data(data, now)
        if self._last_data is None or self._last_data.track.track_id != data.track.track_id:
            self._media_strong_tracking_until = now + TRACK_CHANGE_STRONG_REFRESH_SECONDS
        self._last_data = data
        self._last_ready_at = now

    @staticmethod
    def _advance_delayed_media_sample(data: IslandVisualData, now: float) -> IslandVisualData:
        track = data.track
        if not track.is_playing or track.position_source not in {"windows-media", "interactive"}:
            return data
        sampled_at = float(track.position_sampled_at or 0.0)
        if sampled_at <= 0.0:
            return data
        # The session timeline was sampled before lyric and artwork work. A
        # completed refresh must carry that elapsed playback into its first
        # visual frame; future cached frames still project from delivery time.
        elapsed = max(0.0, now - sampled_at)
        if elapsed <= 0.01:
            return data
        position = float(track.position_seconds) + elapsed
        if track.duration_seconds > 0.0:
            position = min(float(track.duration_seconds), position)
        return replace(data, track=replace(track, position_seconds=position))

    def _raw_timeline_observation_at(
        self, track_key: str, timeline: object, is_playing: bool, sampled_at: float,
    ) -> float:
        """Keep repeated reads of one Windows timeline as one observation.

        GSMTC may return the same position and LastUpdatedTime through many
        polls. Retrieval time is not evidence of a second seek. The first
        monotonic observation stays the projection origin; a changed position,
        source update, track or play state creates a fresh observation.
        """
        signature = (
            str(track_key), getattr(timeline, "last_updated_time", None),
            self._timespan_seconds(getattr(timeline, "position", None)),
            self._timespan_seconds(getattr(timeline, "start_time", None)),
            self._timespan_seconds(getattr(timeline, "end_time", None)),
            bool(is_playing),
        )
        previous = getattr(self, "_raw_timeline_observation_signature", None)
        anchor = float(getattr(self, "_raw_timeline_observation_sampled_at", 0.0) or 0.0)
        if signature != previous or anchor <= 0.0 or sampled_at < anchor:
            self._raw_timeline_observation_signature = signature
            self._raw_timeline_observation_sampled_at = float(sampled_at)
        return float(self._raw_timeline_observation_sampled_at)

    def _stabilize_incoming_position(self, data: IslandVisualData, now: float) -> IslandVisualData:
        previous = self._last_data
        if previous is None:
            self._clear_pending_player_position_discontinuity()
            return data
        track = data.track
        previous_track = previous.track
        if track.track_id != previous_track.track_id or not track.is_playing:
            self._clear_pending_player_position_discontinuity()
            return data
        if track.position_source not in STABILIZED_LOCAL_POSITION_SOURCES | STABILIZED_PLAYER_POSITION_SOURCES:
            self._clear_pending_player_position_discontinuity()
            return data
        projected_previous = self._project_visual_position(previous, now, self._last_ready_at)
        projected_position = float(projected_previous.track.position_seconds)
        raw_position = float(track.position_seconds)
        if track.position_source in STABILIZED_PLAYER_POSITION_SOURCES:
            discontinuity = raw_position - projected_position
            if abs(discontinuity) > 1.20:
                if self._recent_verified_local_seek_matches_track(track.track_id, now):
                    self._clear_pending_player_position_discontinuity()
                    return data
                if discontinuity < 0.0 and self._should_preserve_player_backstep(track, previous_track, abs(discontinuity)):
                    # Foreground/interactive reads and the existing large-seek
                    # backward seek threshold remain authoritative on their
                    # first sample. A forward spike still needs confirmation.
                    self._clear_pending_player_position_discontinuity()
                    return data
                if self._player_position_discontinuity_confirmed(track, projected_position, now):
                    self._clear_pending_player_position_discontinuity()
                    return data
                updated = replace(track, position_seconds=projected_position)
                return replace(data, track=updated)
            self._clear_pending_player_position_discontinuity()
        if projected_position <= raw_position + 0.05:
            return data
        updated = replace(track, position_seconds=projected_position)
        return replace(data, track=updated)

    def _clear_pending_player_position_discontinuity(self):
        self._pending_player_position_discontinuity = None

    def _recent_verified_local_seek_matches_track(self, track_id: str, now: float) -> bool:
        if getattr(self, "_local_seek_position_seconds", None) is None:
            return False
        seek_at = float(getattr(self, "_local_seek_at", 0.0) or 0.0)
        age = float(now) - seek_at
        if seek_at <= 0.0 or age < 0.0 or age > 4.5:
            return False
        seek_track_id = str(getattr(self, "_local_seek_track_id", "") or "")
        return not seek_track_id or seek_track_id == str(track_id or "")

    def _player_position_discontinuity_confirmed(
        self,
        track: TrackData,
        projected_position: float,
        now: float,
    ) -> bool:
        """Confirm a medium background jump from consecutive advancing reads."""
        raw_position = max(0.0, float(track.position_seconds))
        observed_at = float(now)
        direction = 1 if raw_position > float(projected_position) else -1
        track_id = str(track.track_id or "")
        source = str(track.position_source or "")
        sampled_at = float(getattr(track, "position_sampled_at", 0.0) or 0.0)
        pending = getattr(self, "_pending_player_position_discontinuity", None)

        def begin_candidate() -> bool:
            self._pending_player_position_discontinuity = {
                "track_id": track_id,
                "source": source,
                "direction": direction,
                "first_position": raw_position,
                "first_at": observed_at,
                "last_position": raw_position,
                "last_at": observed_at,
                "sampled_at": sampled_at,
            }
            # Confirm a suspected external seek promptly rather than waiting
            # for the normal background polling cadence. The held position
            # remains visible until the next samples agree.
            if self._supports_player_position_adapter(getattr(self, "_music_player_adapter", "")):
                self._mark_player_anchor_fast_refresh(observed_at, duration=1.35)
            return False

        if not isinstance(pending, dict):
            return begin_candidate()
        if (
            str(pending.get("track_id", "")) != track_id
            or str(pending.get("source", "")) != source
            or int(pending.get("direction", 0) or 0) != direction
        ):
            return begin_candidate()
        # A cached UIA observation can be reprojected on several provider
        # refreshes. Elapsed wall time and the projected position do not turn
        # it into an independent confirmation of a surprising seek.
        if sampled_at > 0.0 and sampled_at == float(pending.get("sampled_at", 0.0) or 0.0):
            return False
        first_at = float(pending.get("first_at", observed_at) or observed_at)
        last_at = float(pending.get("last_at", observed_at) or observed_at)
        first_position = float(pending.get("first_position", raw_position) or raw_position)
        last_position = float(pending.get("last_position", raw_position) or raw_position)
        sample_gap = observed_at - last_at
        sample_advance = raw_position - last_position
        total_elapsed = observed_at - first_at
        total_advance = raw_position - first_position
        continuous = (
            0.0 < sample_gap <= 2.6
            and -0.12 <= sample_advance <= sample_gap + 0.85
            and total_elapsed >= 0.0
            and abs(total_advance - total_elapsed) <= 1.1
        )
        if not continuous:
            return begin_candidate()
        pending["last_position"] = raw_position
        pending["last_at"] = observed_at
        pending["sampled_at"] = sampled_at
        return total_elapsed >= 0.8 and total_advance >= 0.18

    def _should_preserve_player_backstep(self, track: TrackData, previous_track: TrackData, backstep_seconds: float) -> bool:
        if str(getattr(track, "position_source", "") or "").startswith(("netease-uia-", "qq-uia-")):
            # UIA controls and lyric rows may briefly expose stale playback
            # state during a song/focus transition. Even in foreground, a
            # backstep needs advancing repeated samples unless GluSpot just
            # confirmed an explicit seek (handled by the caller).
            return False
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
            # Keep the verified Seek event's identity through cached projections.
            # Repainting or polling the same Seek is not a second user action.
            position_sampled_at=float(self._local_seek_at),
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
        if str(track.position_source or "").endswith("-uia-lyrics"):
            # A visible lyric row is a coarse observation of that row only.
            # Do not manufacture the next row by projecting a cached sample
            # while the player has not yet exposed the next highlighted row.
            position = min(position, self._lyric_sample_line_end(track.timed_lyrics, float(track.position_seconds)))
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
        try:
            live_audio = audio_analyzer.has_recent_real_frame()
        except Exception as exc:
            self._release_audio_analyzer(f"audio-frame-check-failed: {self._brief_error(exc)}", cooldown=True)
            return None
        if not live_audio:
            return None
        try:
            return audio_analyzer.get_frame(
                is_playing=True if track is None else track.is_playing,
                position_seconds=0.0 if track is None else track.position_seconds,
            )
        except Exception as exc:
            self._release_audio_analyzer(f"audio-frame-read-failed: {self._brief_error(exc)}", cooldown=True)
            return None

    def has_live_audio(self) -> bool:
        audio_analyzer = self._ensure_audio_analyzer_if_requested()
        if audio_analyzer is None:
            return False
        try:
            return audio_analyzer.has_recent_real_frame()
        except Exception as exc:
            self._release_audio_analyzer(f"audio-live-check-failed: {self._brief_error(exc)}", cooldown=True)
            return False

    def _live_audio_requested(self) -> bool:
        settings_getter = getattr(self, "settings_getter", None)
        if not callable(settings_getter):
            return False
        # Auto-hiding the music island must remain a metadata-only feature.
        # Opening a WASAPI loopback stream here can reconfigure Bluetooth
        # endpoints (notably AirPods) and audibly degrade playback quality.
        # Loopback capture is reserved for features that explicitly request
        # live audio analysis.
        return bool(
            settings_getter("v2_enable_live_audio_analysis", False)
            or settings_getter("v2_music_reactive_background_enabled", False)
        )

    def _ensure_audio_analyzer_if_requested(self) -> LiveAudioAnalyzer | None:
        if not self._live_audio_requested():
            self._release_audio_analyzer("disabled", cooldown=False)
            return None
        now = time.monotonic()
        retry_not_before = float(getattr(self, "_audio_analyzer_retry_not_before", 0.0) or 0.0)
        if retry_not_before > now:
            return None
        analyzer = getattr(self, "audio_analyzer", None)
        if analyzer is not None and self._audio_analyzer_unavailable(analyzer):
            self._release_audio_analyzer(self._audio_analyzer_error(analyzer) or "audio analyzer unavailable", cooldown=True, now=now)
            return None
        if analyzer is None:
            try:
                analyzer = LiveAudioAnalyzer()
            except Exception as exc:
                self.audio_analyzer = None
                self._audio_analyzer_last_error = self._brief_error(exc)
                self._audio_analyzer_retry_not_before = now + AUDIO_ANALYZER_RETRY_DELAY_SECONDS
                return None
            self.audio_analyzer = analyzer
            self._audio_analyzer_last_error = ""
        return analyzer

    def _audio_analyzer_unavailable(self, analyzer: object) -> bool:
        diagnostics = self._safe_audio_analyzer_diagnostics(analyzer)
        status = str(diagnostics.get("status", "") or "").strip().casefold()
        running = bool(diagnostics.get("running", True))
        has_recent_frame = bool(diagnostics.get("has_recent_frame", False))
        return status in {"unavailable", "failed", "stopped"} and not running and not has_recent_frame

    def _audio_analyzer_error(self, analyzer: object) -> str:
        diagnostics = self._safe_audio_analyzer_diagnostics(analyzer)
        last_error = str(diagnostics.get("last_error", "") or "").strip()
        status = str(diagnostics.get("status", "") or "").strip()
        return last_error or status

    def _safe_audio_analyzer_diagnostics(self, analyzer: object) -> dict[str, object]:
        diagnostics_fn = getattr(analyzer, "diagnostics", None)
        if not callable(diagnostics_fn):
            return {}
        try:
            diagnostics = diagnostics_fn()
        except Exception as exc:
            return {"status": "error", "running": False, "has_recent_frame": False, "last_error": self._brief_error(exc)}
        return diagnostics if isinstance(diagnostics, dict) else {}

    def _release_audio_analyzer(self, reason: str = "", cooldown: bool = False, now: float | None = None):
        analyzer = getattr(self, "audio_analyzer", None)
        if analyzer is not None:
            stop = getattr(analyzer, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception as exc:
                    reason = f"{reason}; stop-failed: {self._brief_error(exc)}" if reason else f"stop-failed: {self._brief_error(exc)}"
        self.audio_analyzer = None
        if reason and reason != "disabled":
            self._audio_analyzer_last_error = str(reason)
        elif reason == "disabled":
            self._audio_analyzer_last_error = ""
        if cooldown:
            current = time.monotonic() if now is None else float(now)
            self._audio_analyzer_retry_not_before = current + AUDIO_ANALYZER_RETRY_DELAY_SECONDS
        else:
            self._audio_analyzer_retry_not_before = 0.0

    def _audio_diagnostics(self) -> dict[str, object]:
        analyzer = getattr(self, "audio_analyzer", None)
        if analyzer is not None:
            diagnostics = self._safe_audio_analyzer_diagnostics(analyzer)
            if diagnostics:
                return diagnostics
        now = time.monotonic()
        retry_not_before = float(getattr(self, "_audio_analyzer_retry_not_before", 0.0) or 0.0)
        last_error = str(getattr(self, "_audio_analyzer_last_error", "") or "")
        if self._live_audio_requested():
            if retry_not_before > now:
                return {
                    "status": "cooldown",
                    "running": False,
                    "has_recent_frame": False,
                    "last_real_frame_age": None,
                    "last_error": last_error,
                    "retry_in_seconds": round(retry_not_before - now, 3),
                }
            return {
                "status": "not started",
                "running": False,
                "has_recent_frame": False,
                "last_real_frame_age": None,
                "last_error": last_error,
            }
        return {"status": "not enabled", "running": False, "has_recent_frame": False, "last_real_frame_age": None, "last_error": ""}

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
        audio_route = self.get_audio_route_snapshot()
        return {
            "audio_route": {
                "state": audio_route.state,
                "active_id": audio_route.active_id,
                "pending_id": audio_route.pending_id,
                "cast_source_id": audio_route.cast_source_id,
                "device_count": len(audio_route.devices),
                "status": audio_route.status_message,
            },
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
            "music_cache": self._music_cache_diagnostics(),
            "timed_lyrics_count": 0 if track is None else len(track.timed_lyrics),
            "audio": self._audio_diagnostics(),
            "netease_position": self.netease_position.diagnostics() if self.netease_position is not None else self._cold_attachment_diagnostics("netease"),
            "qq_position": self.qq_position.diagnostics() if self.qq_position is not None else self._cold_attachment_diagnostics("qq"),
            "plugins": self.plugin_provider.diagnostics(),
            "messages": self.message_detector.diagnostics(),
        }

    def _music_cache_diagnostics(self) -> dict[str, object]:
        data = getattr(self, "_last_data", None)
        track = data.track if data is not None else None
        return {
            "artwork_format_version": 1,
            "epoch": int(getattr(self, "_music_cache_epoch", 0)),
            "artwork_epoch": int(getattr(self, "_artwork_cache_epoch", 0)),
            "artwork_memory_entries": len(getattr(self, "_queue_art_cache", {})),
            "artwork_downloads_pending": len(getattr(self, "_queue_art_inflight", ())),
            "artwork_lookups_pending": len(getattr(self, "_current_art_lookup_inflight", ())),
            "display_artwork_min_side": self._album_art_min_side(track.album_art_bytes) if track is not None else 0,
            "display_artwork_source": str(track.album_art_source) if track is not None else "",
            "display_artwork_lookup_state": str(track.album_art_lookup_state) if track is not None else "",
            "last_clear": dict(getattr(self, "_music_cache_last_clear", {})),
        }

    def send_playback_command(self, command: PlaybackCommand) -> bool:
        if self._loop is None:
            return False
        command_at = time.monotonic()
        command_data = getattr(self, "_last_data", None)
        command_track_id = str(command_data.track.track_id or "") if command_data is not None else ""
        result = bool(self._run(self._send_command(command)))
        if result:
            if command in {"previous", "next"}:
                self._recent_skip_command_at = time.monotonic()
                self._media_strong_tracking_until = self._recent_skip_command_at + TRACK_CHANGE_STRONG_REFRESH_SECONDS
            elif command == "toggle_play_pause":
                self._last_playback_toggle_at = command_at
                self._last_playback_toggle_track_id = command_track_id
                self._media_strong_tracking_until = max(
                    float(getattr(self, "_media_strong_tracking_until", 0.0) or 0.0), command_at + 2.5,
                )
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
            self._refresh_future_cache_epoch = int(getattr(self, "_music_cache_epoch", 0))
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

    def _music_surface_preload_count(self) -> int:
        try:
            return max(1, min(6, int(self.settings_getter("v2_music_surface_preload_count", 6))))
        except (TypeError, ValueError, OverflowError):
            return 6

    def _prefetch_netease_upcoming_lyrics(self, title: str, artist: str, player_position_reader, count: int = 2) -> bool:
        adjacent_reader = getattr(player_position_reader, "adjacent_track", None)
        upcoming_reader = getattr(player_position_reader, "upcoming_tracks", None)
        if not (callable(adjacent_reader) or callable(upcoming_reader)) or not title:
            return False
        keys = getattr(self, "_netease_upcoming_prefetch_keys", None)
        if not isinstance(keys, set):
            keys = set()
            self._netease_upcoming_prefetch_keys = keys
        current_title = str(title or "").strip()
        current_artist = str(artist or "").strip()
        limit = max(0, min(6, int(count)))
        if limit <= 0 or (self._stable_text_key(current_title), self._stable_text_key(current_artist)) in keys:
            return False
        # The queue reader already exposes a bounded batch. Reading that once
        # avoids reopening and walking the playlist for each upcoming song.
        if callable(upcoming_reader):
            try:
                upcoming = tuple(upcoming_reader(current_title, current_artist, limit))[:limit]
            except Exception:
                upcoming = ()
            if upcoming:
                started_any = False
                for adjacent in upcoming:
                    key = (self._stable_text_key(current_title), self._stable_text_key(current_artist))
                    if key in keys:
                        break
                    next_title = str(getattr(adjacent, "title", "") or "").strip()
                    next_artist = str(getattr(adjacent, "artist", "") or "").strip()
                    if not next_title:
                        break
                    keys.add(key)
                    started_any = self._prefetch_lyrics_for_track(adjacent) or started_any
                    current_title, current_artist = next_title, next_artist
                return started_any
        if not callable(adjacent_reader):
            return False
        started_any = False
        for _index in range(limit):
            key = (self._stable_text_key(current_title), self._stable_text_key(current_artist))
            if key in keys:
                break
            try:
                adjacent = adjacent_reader(current_title, current_artist, "next")
            except Exception:
                break
            next_title = str(getattr(adjacent, "title", "") or "").strip() if adjacent is not None else ""
            next_artist = str(getattr(adjacent, "artist", "") or "").strip() if adjacent is not None else ""
            if not next_title:
                break
            keys.add(key)
            started_any = self._prefetch_lyrics_for_track(adjacent) or started_any
            current_title = next_title
            current_artist = next_artist
        return started_any

    def _should_prefetch_upcoming_lyrics(self, track_key: str, lyrics_match, lyrics_lookup_pending: bool, lookup_started: bool, *, mark_attempt: bool = True) -> bool:
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
        if mark_attempt:
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
        count = self._music_surface_preload_count()
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
        epoch = int(getattr(self, "_artwork_cache_epoch", 0))
        data = getattr(track, "album_art_bytes", b"") or getattr(track, "image_bytes", b"") or b""
        data = data if isinstance(data, bytes) and self._is_valid_image_bytes(data) else b""
        url = str(getattr(track, "album_art_url", "") or "").strip()
        if not url.startswith(("http://", "https://")):
            return data
        original_url = url
        url = normalize_artwork_url(url, self.CURRENT_ART_TARGET_SIDE)
        cache = getattr(self, "_queue_art_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._queue_art_cache = cache
        candidates = (url, original_url) if url != original_url else (url,)
        selected = b""
        normalized_cached = False
        for candidate_url in candidates:
            content = cache.get(candidate_url, b"")
            if content and not self._is_valid_image_bytes(content):
                if cache.get(candidate_url) is content:
                    cache.pop(candidate_url, None)
                content = b""
            if not content:
                disk_path = self._queue_art_disk_path(candidate_url)
                if disk_path is not None and disk_path.is_file():
                    try:
                        content = disk_path.read_bytes()
                    except OSError:
                        content = b""
                    if content and not self._is_valid_image_bytes(content):
                        content = b""
                    if content:
                        lock = getattr(self, "_queue_art_lock", None)
                        if lock is None:
                            lock = self._queue_art_lock = threading.Lock()
                        with lock:
                            if epoch != int(getattr(self, "_artwork_cache_epoch", 0)):
                                return b""
                            cache[candidate_url] = content
                        try:
                            os.utime(disk_path, None)
                        except OSError:
                            pass
            if content:
                selected = self._preferred_album_art_bytes(selected, content)
                if candidate_url == url:
                    # A valid but tiny target response is still a preview.
                    # Retain these bytes for this frame, but let the same URL
                    # recover after the ordinary bounded download cooldown.
                    normalized_cached = not self._reject_tiny_queue_art(url, content)
        if not normalized_cached:
            # The player's known URL is a stronger identity than a search
            # result. Download its original while retaining any supplied or
            # cached preview for the first media frame.
            if epoch != int(getattr(self, "_artwork_cache_epoch", 0)):
                return b""
            self._schedule_queue_album_art_prefetch(url)
            if url != original_url and float(getattr(self, "_queue_art_failed_until", {}).get(url, 0.0) or 0.0) > time.monotonic():
                # Some CDN variants reject a requested size. The untouched
                # URL remains a fallback, without repeatedly downloading it.
                if not cache.get(original_url):
                    self._schedule_queue_album_art_prefetch(original_url)
        return self._preferred_album_art_bytes(selected, data) if epoch == int(getattr(self, "_artwork_cache_epoch", 0)) else b""

    def _reject_tiny_queue_art(self, url: str, content: bytes, *, cooldown: float = 30.0) -> bool:
        if not 0 < self._album_art_min_side(content) < self.CURRENT_ART_PREVIEW_SIDE:
            return False
        lock = getattr(self, "_queue_art_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._queue_art_lock = lock
        with lock:
            cache = getattr(self, "_queue_art_cache", {})
            current = cache.get(url, b"")
            if current and current != content:
                return False
            cache.pop(url, None)
            failures = getattr(self, "_queue_art_failed_until", None)
            if not isinstance(failures, dict):
                failures = {}
                self._queue_art_failed_until = failures
            failures[url] = max(float(failures.get(url, 0.0) or 0.0), time.monotonic() + cooldown)
            disk_path = self._queue_art_disk_path(url)
            if disk_path is not None:
                try:
                    disk_path.unlink(missing_ok=True)
                except OSError:
                    pass
        return True

    def _album_art_url_is_preview(self, url: str, content: bytes) -> bool:
        """Keep a known small URL as a preview until its source upgrade arrives."""
        if self._album_art_min_side(content) < self.CURRENT_ART_PREVIEW_SIDE:
            return True
        normalized = normalize_artwork_url(url, self.CURRENT_ART_TARGET_SIDE)
        if normalized == url:
            return False
        try:
            params = parse_qs(urlsplit(url).query)
            value = next((values[0] for key, values in params.items() if key.casefold() == "param"), "")
            size = value.casefold().split("y")
            small_request = len(size) == 2 and all(0 < int(side) < self.CURRENT_ART_LOOKUP_SIDE for side in size)
        except (ValueError, TypeError):
            small_request = False
        if not small_request:
            return False
        # Reading the upgraded cache proves that these are the target URL's
        # real bytes, even when the source original is smaller than 2560.
        return getattr(self, "_queue_art_cache", {}).get(normalized, b"") != content

    def _current_online_album_art_bytes(self, track_id: str) -> bytes:
        url = str(getattr(self, "_current_art_url_by_track_id", {}).get(str(track_id or ""), "") or "").strip()
        if not url:
            return b""
        return self._queue_album_art_bytes(type("CurrentTrackArt", (), {"album_art_url": url})())

    def _current_artwork_lookup_state(
        self, track_id: str, display_art: bytes, *, eligible: bool,
        online_art: bytes = b"", source: str = "",
    ) -> str:
        """Report whether this track can still replace its visible preview."""
        if "original" in source and self._album_art_min_side(display_art) >= self.CURRENT_ART_PREVIEW_SIDE:
            return "ready"
        now = time.monotonic()
        lock = getattr(self, "_current_art_lookup_lock", None)
        if lock is None:
            return "pending" if eligible else "failed"
        with lock:
            pending_lookup = track_id in getattr(self, "_current_art_lookup_inflight", set())
            url = str(getattr(self, "_current_art_url_by_track_id", {}).get(track_id, "") or "")
            failed_lookup = float(getattr(self, "_current_art_lookup_failed_until", {}).get(track_id, 0.0) or 0.0) > now
        if pending_lookup:
            return "pending"
        if url:
            art_lock = getattr(self, "_queue_art_lock", None)
            if art_lock is not None:
                with art_lock:
                    download_url = normalize_artwork_url(url, self.CURRENT_ART_TARGET_SIDE)
                    if float(getattr(self, "_queue_art_failed_until", {}).get(download_url, 0.0) or 0.0) > now:
                        return "failed"
            if self._album_art_min_side(online_art) > 0:
                if self._album_art_url_is_preview(url, online_art):
                    return "failed" if self._album_art_min_side(online_art) < self.CURRENT_ART_PREVIEW_SIDE else "pending"
                return "ready"
            # The URL has been found; its download or next cache read can
            # still upgrade the visible cover.
            return "pending"
        if failed_lookup or not eligible:
            return "failed"
        # A bounded worker slot can be busy on an adjacent song. The next
        # provider refresh may start this track's lookup.
        return "pending"

    def _schedule_current_netease_art_lookup(
        self, track_id: str, title: str, artist: str, *,
        album_title: str = "", duration_seconds: float = 0.0,
    ) -> bool:
        if not bool(getattr(self, "_online_current_art_lookup_enabled", False)):
            return False
        track_id = str(track_id or "").strip()
        title = str(title or "").strip()
        artist = str(artist or "").strip()
        if not track_id or not title or not str(artist or "").strip():
            return False
        urls = getattr(self, "_current_art_url_by_track_id", None)
        if not isinstance(urls, dict):
            urls = {}
            self._current_art_url_by_track_id = urls
        inflight = getattr(self, "_current_art_lookup_inflight", None)
        if not isinstance(inflight, set):
            inflight = set()
            self._current_art_lookup_inflight = inflight
        failed_until = getattr(self, "_current_art_lookup_failed_until", None)
        if not isinstance(failed_until, dict):
            failed_until = {}
            self._current_art_lookup_failed_until = failed_until
        lock = getattr(self, "_current_art_lookup_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._current_art_lookup_lock = lock
        generations = getattr(self, "_current_art_lookup_generations", None)
        if not isinstance(generations, dict):
            generations = {}
            self._current_art_lookup_generations = generations
        rejected = getattr(self, "_current_art_rejected_urls", None)
        if not isinstance(rejected, dict):
            rejected = {}
            self._current_art_rejected_urls = rejected
        sources = getattr(self, "_current_art_lookup_sources", None)
        if not isinstance(sources, dict):
            sources = {}
            self._current_art_lookup_sources = sources
        slots = getattr(self, "_current_art_lookup_slots", None)
        if slots is None:
            slots = threading.BoundedSemaphore(2)
            self._current_art_lookup_slots = slots
        resolver = getattr(self, "_musicbrainz_artwork_resolver", None)
        if resolver is None:
            resolver = MusicBrainzArtworkResolver()
            self._musicbrainz_artwork_resolver = resolver
        now = time.monotonic()
        token = object()
        deadline = now + 9.0
        # Preserve the historical method name and three positional arguments.
        # The platform is captured before the thread can observe another song.
        adapter = str(getattr(self, "_music_player_adapter", "netease") or "netease")
        netease_art_source = self._is_netease_adapter(adapter) or self._is_netease_source(
            str(getattr(self, "_raw_source_app_user_model_id", "") or "")
        )
        with lock:
            existing_url = urls.get(track_id)
            if existing_url:
                download_url = normalize_artwork_url(existing_url, self.CURRENT_ART_TARGET_SIDE)
                download_failures = getattr(self, "_queue_art_failed_until", {})
                download_failed_until = max(
                    float(download_failures.get(download_url, 0.0) or 0.0),
                    float(download_failures.get(existing_url, 0.0) or 0.0),
                )
                cached_side = self._album_art_min_side(getattr(self, "_queue_art_cache", {}).get(download_url, b""))
                if 0 < cached_side < self.CURRENT_ART_PREVIEW_SIDE:
                    # A valid tiny image is still a preview. Use the same
                    # bounded rejection path as an invalid download, allowing
                    # a strict alternate source and later retry after cooldown.
                    download_failed_until = max(download_failed_until, now + 45.0)
                    self._reject_tiny_queue_art(
                        download_url, getattr(self, "_queue_art_cache", {}).get(download_url, b""), cooldown=45.0,
                    )
                if download_failed_until > now:
                    rejected[track_id] = (download_failed_until, existing_url)
                    urls.pop(track_id, None)
                else:
                    return False
            if track_id in inflight or float(failed_until.get(track_id, 0.0) or 0.0) > now:
                return False
            if not slots.acquire(blocking=False):
                return False
            inflight.add(track_id)
            generations[track_id] = (token, deadline)
            epoch = int(getattr(self, "_artwork_cache_epoch", 0))

        def expire():
            with lock:
                if generations.get(track_id, (None, 0.0))[0] is token:
                    generations.pop(track_id, None)
                    inflight.discard(track_id)
                    failed_until[track_id] = time.monotonic() + 45.0
                    while len(failed_until) > 64:
                        failed_until.pop(next(iter(failed_until)))
                    self._last_refresh_at = 0.0

        # A late thread cannot publish an expired attempt into a newer retry.
        # This is a delivery deadline, not cancellation of a blocking socket.
        expiry = threading.Timer(9.0, expire)
        expiry.daemon = True
        expiry.start()

        def lookup():
            url, source = "", ""
            try:
                with lock:
                    if epoch != int(getattr(self, "_artwork_cache_epoch", 0)):
                        return
                # Artwork retrieval does not require an enabled progress-sync
                # adapter. A NetEase GSMTC session still has the reference's
                # cover fallback when its optional UIA reader is disabled.
                if netease_art_source and float(getattr(self, "_netease_art_source_failed_until", 0.0) or 0.0) <= time.monotonic():
                    try:
                        url = self._resolve_netease_art_url(
                            title, artist, album_title=album_title,
                            duration_seconds=duration_seconds, deadline=min(deadline, time.monotonic() + 3.0),
                        )
                    except Exception:
                        with lock:
                            if generations.get(track_id, (None, 0.0))[0] is token:
                                self._netease_art_source_failed_until = time.monotonic() + 45.0
                    if url:
                        source = "netease"
                rejected_until, rejected_url = rejected.get(track_id, (0.0, ""))
                if url and url == rejected_url and rejected_until > time.monotonic():
                    url = ""
                if not url:
                    url = resolver.resolve(title, artist, album_title, duration_seconds, deadline=deadline)
                    if url:
                        source = "cover-art-archive"
                if url and url == rejected_url and rejected_until > time.monotonic():
                    url = ""
            except Exception:
                url = ""
            finally:
                expiry.cancel()
                slots.release()
            with lock:
                if generations.get(track_id, (None, 0.0))[0] is not token:
                    return
                generations.pop(track_id, None)
                inflight.discard(track_id)
                if url and time.monotonic() < deadline:
                    urls[track_id] = url
                    sources[track_id] = source
                    failed_until.pop(track_id, None)
                    while len(urls) > 24:
                        removed = next(iter(urls))
                        urls.pop(removed)
                        sources.pop(removed, None)
                else:
                    url = ""
                    failed_until[track_id] = time.monotonic() + 45.0
                while len(failed_until) > 64:
                    failed_until.pop(next(iter(failed_until)))
                while len(rejected) > 24:
                    rejected.pop(next(iter(rejected)))
            if url:
                self._schedule_queue_album_art_prefetch(url)
            self._last_refresh_at = 0.0

        try:
            threading.Thread(target=lookup, name="GluSpotCurrentArt", daemon=True).start()
        except RuntimeError:
            expiry.cancel()
            slots.release()
            expire()
            return False
        return True

    def _resolve_netease_art_url(
        self, title: str, artist: str, *, album_title: str = "",
        duration_seconds: float = 0.0, deadline: float | None = None,
    ) -> str:
        query = " ".join(part for part in (str(title or "").strip(), str(artist or "").strip()) if part)
        if not query:
            return ""
        endpoint = "https://music.163.com/api/search/get/web?" + urlencode(
            {"type": "1", "s": query, "offset": "0", "limit": "8"}
        )
        request = Request(
            endpoint,
            headers={
                "User-Agent": ARTWORK_USER_AGENT,
                "Referer": "https://music.163.com/",
            },
        )
        deadline = min(time.monotonic() + 3.0, deadline) if deadline is not None else time.monotonic() + 3.0
        payload = self._fetch_current_art_json(request, deadline)
        if isinstance(payload, dict) and "code" in payload:
            try:
                response_code = int(payload["code"])
            except (TypeError, ValueError) as exc:
                raise ValueError("NetEase artwork API status is invalid") from exc
            if response_code != 200:
                raise ValueError(f"NetEase artwork API returned {response_code}")
        songs = payload.get("result", {}).get("songs", []) if isinstance(payload, dict) else []
        matches = []
        for candidate in songs if isinstance(songs, list) else ():
            if not isinstance(candidate, dict):
                continue
            candidate_title = str(candidate.get("name") or candidate.get("title") or "").strip()
            artists = candidate.get("artists") or candidate.get("ar") or ()
            candidate_artists = "/".join(
                str(item.get("name") or "").strip()
                for item in artists
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            )
            if not self._metadata_matches(candidate_title, candidate_artists, title, artist):
                continue
            album = candidate.get("album") or candidate.get("al") or {}
            if not isinstance(album, dict):
                continue
            if album_title and artwork_text_key(album.get("name") or album.get("title")) != artwork_text_key(album_title):
                continue
            try:
                candidate_duration = float(candidate.get("duration") or candidate.get("dt") or 0.0) / 1000.0
            except (TypeError, ValueError):
                continue
            if not artwork_duration_matches(duration_seconds, candidate_duration, require_known=duration_seconds > 0.0):
                continue
            matches.append((candidate, album))
        # Restore the reference's ordered exact title/artist lookup when the
        # player exposes neither an album nor a duration. Catalog duplicates
        # and compilations must not turn a usable first result into no artwork.
        # Known album/duration evidence has already filtered wrong editions.
        for candidate, album in matches:
            url = str(album.get("picUrl") or album.get("pic_url") or album.get("cover") or "").strip()
            if not url:
                url = self._netease_art_url_from_pic_id(album.get("picId") or album.get("pic_id"))
            if not url:
                album_id = album.get("id") or candidate.get("albumId") or candidate.get("album_id")
                if album_id is not None:
                    detail_request = Request(
                        f"https://music.163.com/api/album/{album_id}",
                        headers={
                            "User-Agent": ARTWORK_USER_AGENT,
                            "Referer": "https://music.163.com/",
                        },
                    )
                    detail = self._fetch_current_art_json(detail_request, deadline)
                    detail_album = detail.get("album", {}) if isinstance(detail, dict) else {}
                    if isinstance(detail_album, dict):
                        url = str(detail_album.get("picUrl") or detail_album.get("blurPicUrl") or "").strip()
            if url.startswith(("http://", "https://")):
                return normalize_artwork_url(url, self.CURRENT_ART_TARGET_SIDE)
        return ""

    @staticmethod
    def _fetch_current_art_json(request, deadline: float):
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            raise TimeoutError("artwork metadata budget expired")
        with urlopen(request, timeout=min(3.0, remaining)) as response:
            content = read_artwork_response(response, deadline, clock=time.monotonic)
        return json.loads(content.decode("utf-8", errors="replace"))

    @staticmethod
    def _netease_art_url_from_pic_id(pic_id) -> str:
        value = str(pic_id or "").strip()
        if not value or not value.isdigit():
            return ""
        magic = b"3go8&$8*3*3h0k(2)2"
        raw = value.encode("ascii")
        encrypted = bytes(byte ^ magic[index % len(magic)] for index, byte in enumerate(raw))
        digest = base64.b64encode(hashlib.md5(encrypted).digest()).decode("ascii")
        digest = digest.replace("/", "_").replace("+", "-")
        return f"https://p1.music.126.net/{digest}/{value}.jpg"

    @staticmethod
    def _queue_art_disk_path(url: str) -> Path | None:
        root = str(os.environ.get("GLUSPOT_ALBUM_ART_CACHE_DIR", "") or "").strip()
        if not root:
            local_root = str(os.environ.get("LOCALAPPDATA", "") or "").strip()
            if not local_root:
                return None
            root = str(Path(local_root) / "GluSpot" / "cache" / "album_art")
        return Path(root) / f"{hashlib.sha256(str(url).encode('utf-8', errors='ignore')).hexdigest()}.img"

    def _persist_queue_art(self, url: str, content: bytes) -> None:
        path = self._queue_art_disk_path(url)
        if path is None or not content:
            return
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(prefix=f"{path.stem}.", suffix=".tmp", dir=path.parent)
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
            os.replace(temporary, path)
            files = sorted(path.parent.glob("*.img"), key=lambda item: item.stat().st_mtime, reverse=True)
            for stale in files[24:]:
                stale.unlink(missing_ok=True)
        except OSError:
            return
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def _schedule_queue_album_art_prefetch(self, url: str) -> bool:
        inflight = getattr(self, "_queue_art_inflight", None)
        if not isinstance(inflight, set):
            inflight = set()
            self._queue_art_inflight = inflight
        lock = getattr(self, "_queue_art_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._queue_art_lock = lock
        failed_until = getattr(self, "_queue_art_failed_until", None)
        if not isinstance(failed_until, dict):
            failed_until = {}
            self._queue_art_failed_until = failed_until
        with lock:
            if self._is_valid_image_bytes(getattr(self, "_queue_art_cache", {}).get(url, b"")):
                return False
            if url in inflight or float(failed_until.get(url, 0.0) or 0.0) > time.monotonic():
                return False
            inflight.add(url)
            epoch = int(getattr(self, "_artwork_cache_epoch", 0))

        def download():
            content = b""
            slots = getattr(self, "_queue_art_slots", None)
            if slots is None:
                slots = threading.BoundedSemaphore(3)
                self._queue_art_slots = slots
            with slots:
                try:
                    if epoch != int(getattr(self, "_artwork_cache_epoch", 0)):
                        return
                    request = Request(url, headers={"User-Agent": "GluSpot/1.0"})
                    with urlopen(request, timeout=3.0) as response:
                        content = response.read(self.QUEUE_ART_MAX_BYTES + 1)
                    if len(content) > self.QUEUE_ART_MAX_BYTES:
                        content = b""
                    if content and QImage.fromData(content).isNull():
                        content = b""
                except Exception:
                    content = b""
            with lock:
                if epoch != int(getattr(self, "_artwork_cache_epoch", 0)):
                    return
                inflight.discard(url)
                if content:
                    failed_until.pop(url, None)
                    cache = getattr(self, "_queue_art_cache", None)
                    if not isinstance(cache, dict):
                        cache = {}
                        self._queue_art_cache = cache
                    cache[url] = bytes(content)
                    while len(cache) > 18:
                        cache.pop(next(iter(cache)))
                    self._persist_queue_art(url, bytes(content))
                else:
                    failed_until[url] = time.monotonic() + 30.0
            self._last_refresh_at = 0.0

        threading.Thread(target=download, name="GluSpotQueueArt", daemon=True).start()
        return True

    def _ensure_lyrics_lookup_started(self, title: str, artist: str, album_title: str = "", duration_seconds: float = 0.0, allow_background: bool = True):
        lyrics_match = self.lyrics_store.resolve_cached_or_local(title, artist, album_title, duration_seconds)
        lyrics_lookup_pending = self.lyrics_store.is_lookup_pending(title, artist, album_title, duration_seconds)
        lookup_started = False
        if allow_background and not lyrics_match.timed_lyrics and not lyrics_match.lyrics and not lyrics_lookup_pending:
            lookup_started = bool(self.lyrics_store.resolve_in_background(title, artist, album_title, duration_seconds))
            lyrics_lookup_pending = lookup_started
        elif (self._dual_line_lyrics_enabled() and lyrics_match.timed_lyrics
              and not LocalLyricsStore.has_secondary_companion(lyrics_match.timed_lyrics)):
            # Retaining the current song suppresses another initial lookup,
            # but its existing original can still gain a companion. The store
            # keeps this single-flight and applies its completion cooldown.
            self.lyrics_store.enrich_in_background(title, artist, album_title, duration_seconds)
        elif lyrics_match.timed_lyrics and not any(
            bool(getattr(line, "units", ())) for line in lyrics_match.timed_lyrics
        ):
            # Older generated online caches predate word timestamps. The
            # store checks cache provenance and throttles repeated attempts;
            # hand-authored local lyrics are never replaced by this path. This
            # remains eligible while the same song's old rows are retained.
            self.lyrics_store.enrich_in_background(title, artist, album_title, duration_seconds, True)
        return lyrics_match, lyrics_lookup_pending, lookup_started

    def seek_playback_position(self, position_seconds: float, expected_track_id: str | None = None) -> bool:
        try:
            position = max(0.0, float(position_seconds))
        except (TypeError, ValueError):
            return False
        expected_track_id = str(expected_track_id or "").strip()
        track = self._last_data.track if self._last_data is not None else None
        current_track_id = str(getattr(track, "track_id", "") or "").strip()
        if expected_track_id and current_track_id != expected_track_id:
            return False
        guarded_track_id = expected_track_id or current_track_id
        result = False
        # NetEase and QQ sometimes acknowledge GSMTC without moving the real
        # playhead. Their route is therefore successful only after UIA or the
        # selected session timeline can be read back at the requested target.
        adapter = str(getattr(self, "_music_player_adapter", "") or "").strip().casefold()
        if self._supports_player_position_adapter(adapter):
            result = self._seek_player_position_fallback(position, guarded_track_id)
            if not result and self._loop is not None and track is not None:
                reader = self._player_position_reader_for(adapter)
                if bool(getattr(reader, "last_physical_seek_attempted", False)):
                    result = bool(
                        self._run(
                            self._verify_current_session_position(
                                position,
                                title=str(track.title or ""),
                                artist=str(track.artist or ""),
                                adapter=adapter,
                            )
                        )
                    )
            if not result and self._loop is not None and track is not None:
                result = bool(
                    self._run(
                        self._seek_position_verified(
                            position,
                            title=str(track.title or ""),
                            artist=str(track.artist or ""),
                            adapter=adapter,
                        )
                    )
                )
                if not result:
                    result = self._verify_player_position_fallback(position, guarded_track_id)
        elif self._loop is not None:
            # Generic system media keeps the normal GSMTC acknowledgement
            # contract because there is no player-specific progress reader.
            result = bool(self._run(self._seek_position(position)))
        if result and guarded_track_id and not self._seek_track_is_current(guarded_track_id):
            result = False
        if result:
            self._remember_local_seek(position)
            self._mark_player_anchor_fast_refresh(duration=2.4)
            self._request_immediate_media_refresh()
        return bool(result)

    def _seek_player_position_fallback(self, position_seconds: float, expected_track_id: str = "") -> bool:
        if expected_track_id and not self._seek_track_is_current(expected_track_id):
            return False
        adapter = str(getattr(self, "_music_player_adapter", "") or "").strip().casefold()
        reader = self._player_position_reader_for(adapter)
        seek = getattr(reader, "seek_playback_position", None)
        if not callable(seek):
            return False
        track = self._last_data.track if self._last_data is not None else None
        title = str(getattr(track, "title", "") or "").strip()
        artist = str(getattr(track, "artist", "") or "").strip()
        duration = max(0.0, float(getattr(track, "duration_seconds", 0.0) or 0.0))
        if duration <= 0.0:
            duration_reader = getattr(reader, "duration_for_track", None)
            if callable(duration_reader):
                try:
                    duration = max(0.0, float(duration_reader(title, artist) or 0.0))
                except Exception:
                    duration = 0.0
        if duration <= 0.0:
            return False
        try:
            result = bool(seek(position_seconds, duration, title=title, artist=artist))
        except Exception:
            return False
        return result and (not expected_track_id or self._seek_track_is_current(expected_track_id))

    def _verify_player_position_fallback(self, position_seconds: float, expected_track_id: str = "") -> bool:
        if expected_track_id and not self._seek_track_is_current(expected_track_id):
            return False
        adapter = str(getattr(self, "_music_player_adapter", "") or "").strip().casefold()
        reader = self._player_position_reader_for(adapter)
        verify = getattr(reader, "verify_playback_position", None)
        if not callable(verify):
            return False
        track = self._last_data.track if self._last_data is not None else None
        if track is None:
            return False
        try:
            result = bool(
                verify(
                    position_seconds,
                    max(0.0, float(track.duration_seconds)),
                    title=str(track.title or ""),
                    artist=str(track.artist or ""),
                )
            )
        except Exception:
            return False
        return result and (not expected_track_id or self._seek_track_is_current(expected_track_id))

    def _seek_track_is_current(self, expected_track_id: str) -> bool:
        expected = str(expected_track_id or "").strip()
        if not expected:
            return True
        track = self._last_data.track if self._last_data is not None else None
        return track is not None and str(track.track_id or "").strip() == expected

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
        # An explicit, verified seek invalidates the UIA reader's previous
        # lyric-row time. Otherwise its continuity filter can mistake the
        # newly highlighted nearby row for an early forward misread.
        adapter = str(getattr(self, "_music_player_adapter", "") or "")
        reader = self._player_position_reader_for(adapter) if adapter in PLAYER_POSITION_ADAPTERS else None
        reset_reader = getattr(reader, "reset", None)
        if callable(reset_reader):
            reset_reader()
        if self._last_data is not None and self._last_data.track.track_id == self._local_seek_track_id:
            track = self._last_data.track
            updated_track = replace(
                track,
                position_seconds=position,
                position_source="local-seek",
                position_sampled_at=float(self._local_seek_at),
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

    def _run(self, coro):
        if self._loop is None:
            return None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=1.4)
        except FutureTimeoutError:
            future.cancel()
            return None
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
        cache_lock = getattr(self, "_music_cache_lock", None)
        if cache_lock is None:
            cache_lock = self._music_cache_lock = threading.RLock()
        with cache_lock:
            epoch = int(getattr(self, "_music_cache_epoch", 0))
            artwork_epoch = int(getattr(self, "_artwork_cache_epoch", 0))
        session = await self._get_session()
        if session is None:
            return None
        try:
            properties = await session.try_get_media_properties_async()
            timeline = session.get_timeline_properties()
            playback = session.get_playback_info()
            timeline_sampled_at = time.monotonic()
        except Exception:
            return None
        if epoch != int(getattr(self, "_music_cache_epoch", 0)):
            return None

        fallback_data = self.fallback.get_visual_data()
        fallback_track = fallback_data.track
        title = str(getattr(properties, "title", "") or "").strip()
        artist = str(getattr(properties, "artist", "") or "").strip()
        session_title = title
        session_artist = artist
        album_title = str(getattr(properties, "album_title", "") or "").strip()
        raw_source_app = str(getattr(session, "source_app_user_model_id", "") or "")
        source_app = self._source_app_name(session)
        read_at = time.monotonic()
        source_app_foreground = self._is_source_app_foreground(source_app, raw_source_app)
        player_adapter = self._music_player_adapter_for(source_app, raw_source_app)
        player_position_reader = self._player_position_reader_for(player_adapter)
        if source_app_foreground:
            self._source_app_foreground_at = read_at
        source_app_interactive = source_app_foreground or (
            self._source_app_foreground_at > 0.0 and read_at - self._source_app_foreground_at <= 3.5
        )
        adapter_interactive = self._supports_player_position_adapter(player_adapter) and source_app_interactive
        raw_timeline_interactive = adapter_interactive and source_app_foreground
        foreground_metadata = self._foreground_track_metadata(player_adapter, source_app_foreground)
        player_window_track = None
        if foreground_metadata is not None:
            foreground_title, foreground_artist = foreground_metadata
            if foreground_title and foreground_artist and (foreground_title != title or foreground_artist != artist):
                title = foreground_title
                artist = foreground_artist
            player_window_track = self._player_window_track_metadata(player_adapter, player_position_reader, title, artist)
            if player_window_track is not None and not self._track_object_matches_metadata(player_window_track, title, artist):
                player_window_track = None
        else:
            player_window_track = self._player_window_track_metadata(player_adapter, player_position_reader, title, artist)
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
        session_duration_seconds = duration_seconds
        session_metadata_matches = self._metadata_matches(session_title, session_artist, title, artist)
        if not session_metadata_matches:
            # Window/queue metadata can advance before GSMTC.  Its thumbnail,
            # timeline and album title still belong to the old song during
            # that interval, so never combine them with the new lyrics/title.
            position_seconds = 0.0
            duration_seconds = 0.0
            album_title = ""
        if player_window_track is not None:
            try:
                player_duration = max(0.0, float(getattr(player_window_track, "duration_seconds", 0.0) or 0.0))
            except Exception:
                player_duration = 0.0
            if player_duration > 0.0:
                duration_seconds = player_duration
                if (
                    session_metadata_matches
                    and session_duration_seconds > 0.0
                    and not self._same_track_duration(session_duration_seconds, player_duration)
                ):
                    # A new version of the same named song can advance in the
                    # player window before the Windows session. Its old
                    # timeline and thumbnail must not follow the new lyrics.
                    session_metadata_matches = False
                    position_seconds = 0.0
                    album_title = str(getattr(player_window_track, "album_title", "") or "").strip()
        track_key = self._track_key(source_app, title, artist, album_title, duration_seconds)
        previous_track = self._last_data.track if self._last_data is not None else None
        previous_album = ""
        if previous_track is not None and str(getattr(self, "_accepted_album_track_id", "") or "") == str(previous_track.track_id or ""):
            previous_album = str(getattr(self, "_accepted_album_title", "") or "")
        same_album = (
            not previous_album
            or not album_title
            or self._stable_text_key(previous_album) == self._stable_text_key(album_title)
        )
        if (
            previous_track is not None
            and self._same_track_metadata(previous_track, title, artist)
            and self._same_track_duration(previous_track.duration_seconds, duration_seconds)
            and same_album
        ):
            track_key = previous_track.track_id
        same_track_as_previous = previous_track is not None and previous_track.track_id == track_key
        self._observe_raw_playback_state(track_key, is_playing, timeline_sampled_at, previous_track)
        if previous_track is not None and not same_track_as_previous:
            # The reader's own cache key is title/artist-based. The provider
            # also knows album and version identity, so invalidate its sample
            # before a new track can briefly inherit the old playback time.
            reset_player_position = getattr(player_position_reader, "reset", None)
            if callable(reset_player_position):
                reset_player_position()
            self._clear_lyrics_found_progress_retry()
            synced = getattr(self, "_lyrics_loaded_sync_track_ids", None)
            if isinstance(synced, set):
                synced.clear()
            legacy_synced = getattr(self, "_netease_lyric_sync_track_ids", None)
            if isinstance(legacy_synced, set):
                legacy_synced.clear()
        previous_position = max(0.0, float(previous_track.position_seconds)) if previous_track is not None else 0.0
        previous_duration = max(0.0, float(previous_track.duration_seconds)) if previous_track is not None else 0.0
        preserve_previous_lyrics = bool(same_track_as_previous and previous_track is not None and previous_track.timed_lyrics)
        with cache_lock:
            if epoch != int(getattr(self, "_music_cache_epoch", 0)):
                return None
            lyrics_match, lyrics_lookup_pending, _lyrics_lookup_started = self._ensure_lyrics_lookup_started(
                title, artist, album_title, duration_seconds, allow_background=not preserve_previous_lyrics,
            )
            # These cache reads only schedule network work. A refresh from
            # before clear cannot start a download in the new cache epoch.
            original_album_art = self._queue_album_art_bytes(player_window_track) if player_window_track is not None else b""
            online_album_art = self._current_online_album_art_bytes(track_key)
        # The configured player's original-art URL may disappear from its
        # window reader when focus moves away.  Keep the current track's
        # already-decoded source bytes rather than replacing the sharp cover
        # with a smaller Windows media thumbnail on the next refresh.
        retained_album_art = (
            previous_track.album_art_bytes
            if same_track_as_previous and previous_track is not None
            else b""
        )
        album_art = self._preferred_album_art_bytes(original_album_art, retained_album_art)
        # A player's own full-sized URL is tied to the exact current album.
        # Search only fills a missing/small source; do not replace a verified
        # original merely because the CDN did not return all requested pixels.
        if self._album_art_min_side(original_album_art) < self.CURRENT_ART_LOOKUP_SIDE:
            album_art = self._preferred_album_art_bytes(album_art, online_album_art)
        album_art_source = ""
        if album_art:
            if album_art == original_album_art:
                original_url = str(getattr(player_window_track, "album_art_url", "") or "").strip()
                album_art_source = "thumbnail" if self._album_art_url_is_preview(original_url, album_art) else "original"
            elif album_art == online_album_art:
                source = str(getattr(self, "_current_art_lookup_sources", {}).get(track_key, "") or "")
                online_url = str(getattr(self, "_current_art_url_by_track_id", {}).get(track_key, "") or "")
                album_art_source = "thumbnail" if self._album_art_url_is_preview(online_url, album_art) else (
                    f"{source}-original" if source else "original"
                )
            elif album_art == retained_album_art and previous_track is not None:
                album_art_source = str(getattr(previous_track, "album_art_source", "") or "")
        if not album_art and session_metadata_matches:
            album_art = await self._thumbnail_for_track(properties, track_key, artwork_epoch)
            if epoch != int(getattr(self, "_music_cache_epoch", 0)):
                return None
            if album_art:
                album_art_source = "thumbnail"
        if 0 < self._album_art_min_side(album_art) < self.CURRENT_ART_PREVIEW_SIDE:
            album_art_source = "thumbnail"
        # Keep the reference's async 1200-pixel lookup trigger separate from
        # original-source completion. It is neither a decode nor a UI gate.
        if self._album_art_min_side(album_art) < self.CURRENT_ART_LOOKUP_SIDE:
            with cache_lock:
                if epoch != int(getattr(self, "_music_cache_epoch", 0)):
                    return None
                self._schedule_current_netease_art_lookup(
                    track_key, title, artist, album_title=album_title, duration_seconds=duration_seconds,
                )
        album_art_lookup_state = self._current_artwork_lookup_state(
            track_key, album_art,
            eligible=bool(getattr(self, "_online_current_art_lookup_enabled", False) and track_key and title and artist),
            online_art=online_album_art, source=album_art_source,
        )
        album_color, secondary_color = self._colors_from_art(album_art, fallback_track.album_color, fallback_track.secondary_color)
        timed_lyrics = lyrics_match.timed_lyrics
        lyrics_source = lyrics_match.source
        lyrics = lyrics_match.lyrics or tuple(line.text for line in timed_lyrics) or (("查找歌词中",) if lyrics_lookup_pending else ("暂无歌词",))
        if preserve_previous_lyrics and previous_track is not None and not timed_lyrics:
            timed_lyrics = previous_track.timed_lyrics
            lyrics = previous_track.lyrics or tuple(line.text for line in timed_lyrics)
            lyrics_source = previous_track.lyrics_source
            lyrics_lookup_pending = False
        if player_position_reader is not None and self._should_sync_after_lyrics_loaded(
            track_key, previous_track, timed_lyrics, read_at
        ):
            self._mark_player_anchor_fast_refresh(read_at, duration=1.35)
        is_netease = self._is_netease_adapter(player_adapter)
        if self._should_prefetch_upcoming_lyrics(
            track_key, lyrics_match, lyrics_lookup_pending, _lyrics_lookup_started, mark_attempt=not is_netease
        ):
            if is_netease:
                preload_count = self._music_surface_preload_count()
                self._prefetch_netease_upcoming_lyrics(title, artist, player_position_reader, count=preload_count)
                # A missing or changing queue is transient. Only retire this
                # track's prefetch attempt once an actual next entry exists.
                current_queue_key = (self._stable_text_key(title), self._stable_text_key(artist))
                if current_queue_key in getattr(self, "_netease_upcoming_prefetch_keys", set()):
                    self._upcoming_lyric_prefetch_track_ids.add(track_key)
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
            if position_source != "local-seek" and raw_timeline_interactive:
                interactive_position = self._interactive_position_from_raw(
                    raw_timeline_position,
                    previous_position,
                    previous_raw_timeline_position,
                    protect_stale_foreground_backstep=source_app_foreground,
                )
                if interactive_position is not None:
                    position_seconds, position_source, interactive_confidence = interactive_position
                    position_confidence = max(position_confidence, interactive_confidence)
            elif self._supports_player_position_adapter(player_adapter) and position_source != "local-seek" and self._should_distrust_background_position(
                raw_timeline_interactive,
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
            if self._supports_player_position_adapter(player_adapter) and position_seconds <= 0.0 and (is_playing or previous_position > 0.0 or previous_track.position_source == "local-estimate"):
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
        # Cached visual polls preserve this original observation time. The
        # music surface must not mistake a reprojected GSMTC value for a new
        # independent player sample when confirming a surprising clock jump.
        raw_observed_at = (
            self._raw_timeline_observation_at(track_key, timeline, self._is_playing(playback), timeline_sampled_at)
            if session_metadata_matches else 0.0
        )
        raw_clock_advancing = self._raw_timeline_corroborates_lyric_anchor(
            track_key, raw_timeline_position, raw_observed_at, time.monotonic(),
            is_playing=self._is_playing(playback), metadata_matches=session_metadata_matches,
        )
        position_sampled_at = float(self._local_seek_at) if position_source == "local-seek" else (
            raw_observed_at if position_source in {"windows-media", "interactive"}
            else timeline_sampled_at if session_metadata_matches else 0.0
        )
        should_read_player_anchor = player_position_reader is not None and bool(timed_lyrics)
        if should_read_player_anchor:
            player_sample = player_position_reader.read(
                title,
                artist,
                timed_lyrics,
                duration_seconds,
                interactive=source_app_interactive,
            )
            if player_sample is not None and self._player_sample_after_playback_boundary(track_key, player_sample):
                if duration_seconds <= 0.0 and player_sample.duration_seconds > 0.0:
                    duration_seconds = player_sample.duration_seconds
                player_sample_source = str(getattr(player_sample, "source", ""))
                source_prefix = getattr(player_position_reader, "source_prefix", player_adapter)
                if player_sample_source.startswith(f"{source_prefix}-uia-"):
                    player_sample_age = time.monotonic() - float(getattr(player_sample, "sampled_at", 0.0) or 0.0)
                    fresh_player_anchor = 0.0 <= player_sample_age <= 0.75
                    use_player_sample = self._should_use_player_sample(
                        source_prefix, player_sample_source, player_sample_age, is_playing
                    )
                    corroborated_coarse_clock = (
                        raw_clock_advancing
                        and player_sample_source == f"{source_prefix}-uia-lyrics"
                        and position_source != "local-seek"
                        and not self._recent_verified_local_seek_matches_track(track_key, time.monotonic())
                    )
                    sample_playing = getattr(player_sample, "is_playing", None)
                    if use_player_sample and sample_playing is not None and not corroborated_coarse_clock:
                        is_playing = bool(sample_playing)
                    projected_player_position = float(player_sample.position_seconds)
                    if is_playing:
                        projected_player_position += max(0.0, player_sample_age)
                    projected_player_position += self._player_seek_correction_seconds(
                        player_adapter,
                        source_app_interactive,
                        is_playing,
                        raw_timeline_position,
                        previous_raw_timeline_position,
                        previous_position,
                    )
                    if player_sample_source == f"{source_prefix}-uia-lyrics":
                        lyric_index = int(getattr(player_sample, "lyric_index", -1))
                        if 0 <= lyric_index < len(timed_lyrics):
                            projected_player_position = min(
                                projected_player_position,
                                self._lyric_sample_line_end(timed_lyrics, float(timed_lyrics[lyric_index].time_seconds)),
                            )
                    if duration_seconds > 0.0:
                        projected_player_position = min(duration_seconds, projected_player_position)
                    player_disagrees = abs(projected_player_position - float(position_seconds)) >= 1.25
                    raw_projected_position = raw_timeline_position + max(0.0, time.monotonic() - raw_observed_at)
                    if duration_seconds > 0.0:
                        raw_projected_position = min(duration_seconds, raw_projected_position)
                    if use_player_sample and corroborated_coarse_clock and (
                        abs(projected_player_position - raw_projected_position) >= 1.25
                        or abs(float(position_seconds) - raw_projected_position) >= 1.25
                    ):
                        # A selected/scrolled lyric row can remain enlarged
                        # through many scans. Persistence is not independent
                        # playback evidence when GSMTC itself is advancing.
                        # Recover the raw clock even if an earlier coarse
                        # sample already contaminated the local estimate.
                        # Delivery projects this original observation once.
                        position_seconds = raw_timeline_position
                        position_source = "windows-media"
                        position_sampled_at = raw_observed_at
                    elif use_player_sample and (
                        position_seconds <= 0.0 or (fresh_player_anchor and player_disagrees)
                    ):
                        position_seconds = projected_player_position
                        position_source = player_sample.source
                        position_confidence = player_sample.confidence
                        position_sampled_at = float(getattr(player_sample, "sampled_at", 0.0) or 0.0)
        if not title and not artist:
            self._provider_status = "no_session"
            return None
        if epoch != int(getattr(self, "_music_cache_epoch", 0)):
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
            position_sampled_at=position_sampled_at,
            source_app_foreground=source_app_foreground,
            position_interactive=source_app_interactive,
            album_queue=album_queue,
            lyric_offset_seconds=self._lyric_offset_seconds_for_adapter(player_adapter),
            lyric_offset_adapter=player_adapter,
            album_art_source=album_art_source,
            album_art_lookup_state=album_art_lookup_state,
        )
        with cache_lock:
            if epoch != int(getattr(self, "_music_cache_epoch", 0)):
                return None
            if album_title or not same_track_as_previous:
                self._accepted_album_title = str(album_title or "")
            self._accepted_album_track_id = track_key
            data = replace(fallback_data, track=track, messages=self.message_detector.messages(), plugins=self.plugin_provider.plugins())
            return self._apply_recent_local_seek_to_visual_data(data, time.monotonic())

    def _observe_raw_playback_state(
        self, track_id: str, is_playing: bool, sampled_at: float,
        previous_track: TrackData | None,
    ) -> None:
        previous_id = str(getattr(self, "_raw_playback_track_id", "") or "")
        previous_state = getattr(self, "_raw_playback_is_playing", None)
        if not previous_id and previous_track is not None and previous_track.track_id == track_id:
            previous_id = track_id
            previous_state = bool(previous_track.is_playing)
        if previous_id != track_id:
            self._raw_playback_changed_at = 0.0
        elif previous_state is not None and bool(previous_state) != bool(is_playing):
            self._raw_playback_changed_at = float(sampled_at)
        self._raw_playback_track_id = str(track_id)
        self._raw_playback_is_playing = bool(is_playing)

    def _player_sample_after_playback_boundary(self, track_id: str, sample: object) -> bool:
        boundary = float(getattr(self, "_raw_playback_changed_at", 0.0) or 0.0)
        if str(getattr(self, "_last_playback_toggle_track_id", "") or "") == str(track_id):
            boundary = max(boundary, float(getattr(self, "_last_playback_toggle_at", 0.0) or 0.0))
        if boundary <= 0.0:
            return True
        # UIA read() returns its latest cached observation while scheduling a
        # single background read. A pre-pause/resume sample must not override
        # the newer playback state or anchor the resumed clock to old time.
        return float(getattr(sample, "sampled_at", 0.0) or 0.0) > boundary

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

    async def _seek_position_verified(
        self,
        position_seconds: float,
        *,
        title: str,
        artist: str,
        adapter: str,
    ) -> bool:
        """Seek a configured player and require its real timeline to move."""
        session = await self._get_session()
        if session is None or not await self._seek_session_matches_track(session, title, artist, adapter):
            return False
        try:
            timeline = session.get_timeline_properties()
            before = self._timespan_seconds(getattr(timeline, "position", None))
            duration = self._timespan_seconds(getattr(timeline, "end_time", None)) - self._timespan_seconds(
                getattr(timeline, "start_time", None)
            )
        except Exception:
            return False
        duration = max(0.0, duration)
        target = max(0.0, float(position_seconds))
        if duration > 0.0:
            target = min(duration, target)
        acknowledged = await self._seek_session_position(session, target)
        if not acknowledged:
            return False

        tolerance = max(1.35, min(2.8, duration * 0.012 if duration > 0.0 else 1.35))
        before_at_target = abs(before - target) <= tolerance
        for _attempt in range(8):
            await asyncio.sleep(0.055)
            try:
                current_timeline = session.get_timeline_properties()
                current = self._timespan_seconds(getattr(current_timeline, "position", None))
            except Exception:
                continue
            if abs(current - target) > tolerance:
                continue
            # A target already reached before the request is also a verified
            # success; otherwise require an observable timeline change.
            if not before_at_target and abs(current - before) < max(0.35, tolerance * 0.35):
                continue
            if not await self._seek_session_matches_track(session, title, artist, adapter):
                return False
            # Selection can return a fresh wrapper for the same GSMTC session,
            # so compare the selected session's live track instead of Python
            # object identity.
            selected = await self._get_session()
            return selected is not None and await self._seek_session_matches_track(selected, title, artist, adapter)
        return False

    async def _verify_current_session_position(
        self,
        position_seconds: float,
        *,
        title: str,
        artist: str,
        adapter: str,
    ) -> bool:
        """Read back a physical player click through the selected media session."""
        session = await self._get_session()
        if session is None or not await self._seek_session_matches_track(session, title, artist, adapter):
            return False
        target = max(0.0, float(position_seconds))
        for attempt in range(6):
            try:
                timeline = session.get_timeline_properties()
                current = self._timespan_seconds(getattr(timeline, "position", None))
                duration = self._timespan_seconds(getattr(timeline, "end_time", None)) - self._timespan_seconds(
                    getattr(timeline, "start_time", None)
                )
            except Exception:
                current = -1.0
                duration = 0.0
            if duration > 0.0:
                target = min(duration, target)
            tolerance = max(1.35, min(2.8, duration * 0.012 if duration > 0.0 else 1.35))
            if current >= 0.0 and abs(current - target) <= tolerance:
                if not await self._seek_session_matches_track(session, title, artist, adapter):
                    return False
                selected = await self._get_session()
                return selected is not None and await self._seek_session_matches_track(selected, title, artist, adapter)
            if attempt < 5:
                await asyncio.sleep(0.055)
        return False

    async def _seek_session_matches_track(self, session, title: str, artist: str, adapter: str) -> bool:
        raw_source = str(getattr(session, "source_app_user_model_id", "") or "")
        source_app = self._source_app_name(session)
        if adapter and not self._music_player_source_matches(adapter, source_app, raw_source):
            return False
        try:
            properties = await session.try_get_media_properties_async()
        except Exception:
            return False
        actual_title = self._stable_text_key(str(getattr(properties, "title", "") or ""))
        expected_title = self._stable_text_key(title)
        if not expected_title or actual_title != expected_title:
            return False
        expected_artist = self._stable_text_key(artist)
        actual_artist = self._stable_text_key(str(getattr(properties, "artist", "") or ""))
        return not expected_artist or not actual_artist or actual_artist == expected_artist

    async def _seek_session_position(self, session, position_seconds: float) -> bool:
        try:
            requested = max(0, int(round(float(position_seconds) * 10_000_000)))
            return bool(await session.try_change_playback_position_async(requested))
        except Exception:
            return False

    async def _read_thumbnail(self, properties, track_key: str) -> bytes:
        epoch = int(getattr(asyncio.current_task(), "_gluspot_artwork_cache_epoch", getattr(self, "_artwork_cache_epoch", 0)))
        if epoch != int(getattr(self, "_artwork_cache_epoch", 0)):
            return b""
        thumbnail = getattr(properties, "thumbnail", None)
        if thumbnail is None:
            return b""
        if Buffer is None or DataReader is None:
            return b""
        try:
            stream = await thumbnail.open_read_async()
            if epoch != int(getattr(self, "_artwork_cache_epoch", 0)):
                return b""
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
            lock = getattr(self, "_queue_art_lock", None)
            if lock is None:
                lock = self._queue_art_lock = threading.Lock()
            with lock:
                if epoch != int(getattr(self, "_artwork_cache_epoch", 0)):
                    return b""
                self._art_cache[cache_key] = data
                if len(self._art_cache) > 36:
                    self._art_cache.pop(next(iter(self._art_cache)))
            return data
        except Exception:
            return b""

    async def _thumbnail_for_track(self, properties, track_key: str, expected_epoch: int | None = None) -> bytes:
        epoch = int(getattr(self, "_artwork_cache_epoch", 0)) if expected_epoch is None else expected_epoch
        if epoch != int(getattr(self, "_artwork_cache_epoch", 0)):
            return b""
        ready = getattr(self, "_ready_thumbnail_bytes", None)
        if ready is None:
            ready = self._ready_thumbnail_bytes = {}
        cached = ready.get(track_key)
        if cached:
            return cached
        pending = getattr(self, "_pending_thumbnail_tasks", None)
        if pending is None:
            pending = self._pending_thumbnail_tasks = {}
        task = pending.get(track_key)
        if task is not None:
            if task.done():
                return self._finish_thumbnail_task(track_key, task, epoch=epoch)
            return b""
        # A WinRT stream occasionally never resolves. Shielding it without
        # a deadline pins this track in the pending map forever. Keep the
        # first-frame 50 ms budget, but bound the background attempt too.
        read_task = asyncio.create_task(self._read_thumbnail(properties, track_key))
        read_task._gluspot_artwork_cache_epoch = epoch
        task = asyncio.create_task(asyncio.wait_for(
            read_task,
            timeout=float(getattr(self, "THUMBNAIL_READ_TIMEOUT_SECONDS", 2.5)),
        ))
        lock = getattr(self, "_queue_art_lock", None)
        if lock is None:
            lock = self._queue_art_lock = threading.Lock()
        with lock:
            if epoch != int(getattr(self, "_artwork_cache_epoch", 0)):
                task.cancel()
                return b""
            pending[track_key] = task
        try:
            # Fast cached thumbnails keep the current first-frame behavior.
            # A slow WinRT stream must not hold the new track and its lyric
            # loading state behind artwork I/O.
            await asyncio.wait_for(asyncio.shield(task), timeout=0.05)
        except asyncio.TimeoutError:
            if task.done():
                return self._finish_thumbnail_task(track_key, task, epoch=epoch)
            task.add_done_callback(lambda done: self._finish_thumbnail_task(track_key, done, refresh=True, epoch=epoch))
            return b""
        except Exception:
            if pending.get(track_key) is task:
                pending.pop(track_key, None)
            return b""
        return self._finish_thumbnail_task(track_key, task, epoch=epoch)

    def _finish_thumbnail_task(self, track_key: str, task: asyncio.Task, *, refresh: bool = False, epoch: int | None = None) -> bytes:
        lock = getattr(self, "_queue_art_lock", None)
        if lock is None:
            lock = self._queue_art_lock = threading.Lock()
        with lock:
            if epoch is not None and epoch != int(getattr(self, "_artwork_cache_epoch", 0)):
                return b""
            pending = getattr(self, "_pending_thumbnail_tasks", None)
            if not isinstance(pending, dict) or pending.get(track_key) is not task:
                return b""
            pending.pop(track_key, None)
            try:
                content = task.result()
            except (asyncio.CancelledError, Exception):
                return b""
            if not content:
                return b""
            ready = getattr(self, "_ready_thumbnail_bytes", None)
            if not isinstance(ready, dict):
                ready = self._ready_thumbnail_bytes = {}
            ready[track_key] = bytes(content)
            while len(ready) > 36:
                ready.pop(next(iter(ready)))
            if refresh:
                self._last_refresh_at = 0.0
            return ready[track_key]

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
        return self._album_art_min_side(data) > 0

    @staticmethod
    @lru_cache(maxsize=36)
    def _album_art_min_side(data: bytes) -> int:
        if not isinstance(data, bytes) or not data:
            return 0
        image = QImage.fromData(data)
        return 0 if image.isNull() else min(image.width(), image.height())

    @classmethod
    def _preferred_album_art_bytes(cls, *candidates: bytes) -> bytes:
        """Keep the sharpest valid cover; retain the earlier one on a size tie."""
        valid = [data for data in candidates if isinstance(data, bytes) and data and cls._album_art_min_side(data) > 0]
        if not valid:
            return b""
        return max(valid, key=cls._album_art_min_side)

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

    @staticmethod
    def _same_track_duration(previous_duration: float, current_duration: float) -> bool:
        """A stable duration change distinguishes versions with the same title and artist."""
        previous = max(0.0, float(previous_duration or 0.0))
        current = max(0.0, float(current_duration or 0.0))
        if previous <= 0.0 or current <= 0.0:
            return True
        return abs(current - previous) <= max(5.0, max(current, previous) * 0.04)

    def _metadata_matches(self, first_title: str, first_artist: str, second_title: str, second_artist: str) -> bool:
        first_title_key = self._stable_text_key(first_title)
        second_title_key = self._stable_text_key(second_title)
        if not first_title_key or not second_title_key or first_title_key != second_title_key:
            return False
        first_artist_key = self._stable_text_key(first_artist)
        second_artist_key = self._stable_text_key(second_artist)
        return not first_artist_key or not second_artist_key or first_artist_key == second_artist_key

    def _track_object_matches_metadata(self, track, title: str, artist: str) -> bool:
        return self._metadata_matches(
            str(getattr(track, "title", "") or ""),
            str(getattr(track, "artist", "") or ""),
            title,
            artist,
        )

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
        if track.timed_lyrics or track.lyrics:
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
            position_sampled_at=0.0,
            position_confidence=0.40,
            source_app_foreground=True,
            position_interactive=True,
        )
        self._last_data = replace(data, track=corrected_track)
        self._last_ready_at = now
        self._last_raw_timeline_track_id = ""
        self._last_raw_timeline_position = None
        self._last_raw_timeline_at = 0.0
        self._raw_timeline_observation_signature = None
        self._raw_timeline_observation_sampled_at = 0.0
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
        protect_stale_foreground_backstep: bool = False,
    ) -> tuple[float, str, float] | None:
        raw_position = max(0.0, float(raw_timeline_position))
        previous_position = max(0.0, float(previous_position))
        if raw_position > 0.0:
            if protect_stale_foreground_backstep and self._looks_like_stale_foreground_raw_position(
                raw_position,
                previous_position,
                previous_raw_timeline_position,
            ):
                return previous_position, "interactive-hold", 0.64
            return raw_position, "interactive", 0.86
        if self._looks_like_seek_to_start(raw_position, previous_position, previous_raw_timeline_position):
            return 0.0, "interactive", 0.82
        if previous_position > 0.0:
            return previous_position, "interactive-hold", 0.64
        return None

    def _looks_like_stale_foreground_raw_position(
        self,
        raw_position: float,
        previous_position: float,
        previous_raw_timeline_position: float | None,
    ) -> bool:
        raw_position = max(0.0, float(raw_position))
        previous_position = max(0.0, float(previous_position))
        if previous_position <= 0.5 or raw_position + 1.25 >= previous_position:
            return False
        if previous_raw_timeline_position is None:
            return True
        previous_raw = max(0.0, float(previous_raw_timeline_position))
        if previous_raw <= 0.0:
            return True
        backward_seek_threshold = max(2.5, min(12.0, previous_position * 0.04))
        if previous_raw - raw_position >= backward_seek_threshold:
            return False
        return True

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
        if bool(settings_getter("v2_enable_dual_line_lyrics", False)):
            return True
        style = str(settings_getter("v2_lyric_flow_style", "") or "").strip().casefold()
        return style == "ember_fade" and bool(settings_getter("v2_ember_dual_line_enabled", False))

    def _is_netease_adapter(self, player_adapter: str) -> bool:
        return str(player_adapter or "").strip().casefold() in {"netease", "netease_betterncm"}

    def _supports_player_position_adapter(self, player_adapter: str) -> bool:
        return str(player_adapter or "").strip().casefold() in PLAYER_POSITION_ADAPTERS

    def _lyrics_found_progress_retry_cached_refresh_due(self, now: float) -> bool:
        track_id = str(getattr(self, "_lyrics_found_progress_retry_track_id", "") or "").strip()
        if not track_id:
            return False
        not_before = float(getattr(self, "_lyrics_found_progress_retry_not_before", 0.0) or 0.0)
        retry_until = float(getattr(self, "_lyrics_found_progress_retry_until", 0.0) or 0.0)
        current = float(now)
        return retry_until > 0.0 and not_before > 0.0 and not_before <= current <= retry_until

    def _lyrics_found_progress_retry_refresh_interval(self, now: float) -> float | None:
        track_id = str(getattr(self, "_lyrics_found_progress_retry_track_id", "") or "").strip()
        if not track_id:
            return None
        not_before = float(getattr(self, "_lyrics_found_progress_retry_not_before", 0.0) or 0.0)
        retry_until = float(getattr(self, "_lyrics_found_progress_retry_until", 0.0) or 0.0)
        current = float(now)
        if retry_until <= 0.0 or current > retry_until:
            self._clear_lyrics_found_progress_retry(track_id)
            return None
        if current >= not_before:
            return 0.05
        try:
            refresh_anchor = float(getattr(self, "_last_refresh_at", 0.0) or 0.0)
        except (TypeError, ValueError):
            refresh_anchor = current
        return max(0.05, not_before - refresh_anchor)

    def _should_sync_after_lyrics_loaded(self, track_key: str, previous_track: TrackData | None, timed_lyrics, now: float | None = None) -> bool:
        if not track_key or not timed_lyrics:
            return False
        current = time.monotonic() if now is None else float(now)
        if self._lyrics_found_progress_retry_due(track_key, current):
            return True
        synced = getattr(self, "_lyrics_loaded_sync_track_ids", None)
        if not isinstance(synced, set):
            synced = getattr(self, "_netease_lyric_sync_track_ids", None)
        if not isinstance(synced, set):
            synced = set()
        self._lyrics_loaded_sync_track_ids = synced
        self._netease_lyric_sync_track_ids = synced
        if track_key in synced:
            return False
        if previous_track is not None and previous_track.track_id == track_key and previous_track.timed_lyrics:
            synced.add(track_key)
            return False
        synced.add(track_key)
        self._schedule_lyrics_found_progress_retry(track_key, current)
        return False

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

    def _player_window_track_metadata(self, player_adapter: str, player_position_reader, title: str = "", artist: str = ""):
        if player_adapter not in {"netease", "qq"}:
            return None
        queue_metadata = getattr(player_position_reader, "track_metadata", None)
        if callable(queue_metadata) and title:
            try:
                track = queue_metadata(title, artist)
            except Exception:
                track = None
            if track is not None:
                return track
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

    def _raw_timeline_corroborates_lyric_anchor(
        self, track_key: str, raw_position: float, observed_at: float, now: float,
        *, is_playing: bool, metadata_matches: bool,
    ) -> bool:
        """Trust natural raw progress, never renewed retrieval timestamps."""
        if not metadata_matches or not is_playing or raw_position <= 0.0 or observed_at <= 0.0:
            self._raw_lyric_anchor_clock = None
            return False
        previous = getattr(self, "_raw_lyric_anchor_clock", None)
        if (
            not isinstance(previous, dict)
            or previous.get("track_key") != str(track_key)
            or now < float(previous["advanced_at"])
            or observed_at < float(previous["advanced_at"])
        ):
            self._raw_lyric_anchor_clock = {
                "track_key": str(track_key), "position": float(raw_position),
                "advanced_at": float(observed_at), "natural_advances": 0,
            }
            return False
        advance = float(raw_position) - float(previous["position"])
        if abs(advance) > 0.02:
            interval = float(observed_at) - float(previous["advanced_at"])
            natural = (
                0.0 < interval <= 3.0 and advance > 0.05
                and abs(advance - interval) <= max(0.40, interval * 0.35)
            )
            previous["natural_advances"] = int(previous["natural_advances"]) + 1 if natural else 0
            previous["position"] = float(raw_position)
            previous["advanced_at"] = float(observed_at)
        # Repeated reads (even with a fresh LastUpdatedTime) do not renew
        # advanced_at. A frozen Windows clock can still use the UIA fallback.
        return int(previous["natural_advances"]) >= 2 and 0.0 <= now - float(previous["advanced_at"]) <= 2.5

    def _should_use_player_sample(self, source_prefix: str, sample_source: str, sample_age: float, is_playing: bool) -> bool:
        source_prefix = str(source_prefix or "").strip().casefold()
        if sample_source == f"{source_prefix}-uia-progress":
            return 0.0 <= float(sample_age) <= 1.5
        if sample_source == f"{source_prefix}-uia-lyrics":
            return 0.0 <= float(sample_age) <= 0.75
        return False

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
        opaque_pixels = 0
        chromatic_pixels = 0
        total_lightness = 0.0
        low_saturation_pixels: list[tuple[float, float, float]] = []

        for y in range(scaled.height()):
            for x in range(scaled.width()):
                color = QColor(scaled.pixel(x, y))
                if color.alpha() < 24:
                    continue
                hue, saturation, lightness, _alpha = color.getHslF()
                opaque_pixels += 1
                total_lightness += lightness
                channels = (color.red(), color.green(), color.blue())
                chroma = (max(channels) - min(channels)) / 255.0
                if saturation >= 0.16 and chroma >= 0.07:
                    chromatic_pixels += 1
                if lightness < 0.03 or lightness > 0.97:
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

        # A mostly black-and-white cover is artwork, not a missing cover.
        # Small coloured lettering should not turn its ambience blue.
        if opaque_pixels and chromatic_pixels / opaque_pixels < 0.12:
            gray = round(56 + 34 * total_lightness / opaque_pixels)
            return ColorToken(gray, gray, gray), ColorToken(gray - 20, gray - 20, gray - 20)

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
        if saturation < 0.16:
            gray = round(56 + 34 * max(0.0, min(1.0, lightness)))
            return ColorToken(gray, gray, gray)
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
        # A player can publish two versions with identical metadata. The
        # duration is a stable version hint once known; adjacent refreshes
        # still reuse the previous identity through _same_track_duration.
        length = max(0.0, float(duration or 0.0))
        duration_hint = str(int(round(length))) if length > 0.0 else "unknown"
        raw = f"{source_app}\0{title}\0{artist}\0{album}\0{duration_hint}"
        return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]

    @staticmethod
    def _lyric_sample_line_end(timed_lyrics, position_seconds: float) -> float:
        base = max(0.0, float(position_seconds))
        for line in timed_lyrics or ():
            start = max(0.0, float(getattr(line, "time_seconds", 0.0) or 0.0))
            if start > base + 0.005:
                return max(base, start - 0.08)
        return float("inf")

    def _has_multiline_lyrics(self, timed_lyrics) -> bool:
        return any("\n" in str(getattr(line, "text", "") or "") for line in timed_lyrics or ())
