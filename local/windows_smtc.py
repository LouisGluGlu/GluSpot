"""Publish owned Qt playback to Windows System Media Transport Controls.

The source-less, muted WinRT player owns only a system control session. Qt
remains the decoder/output authority. WinRT work stays off the GUI thread;
native button callbacks carry a track generation back through a queued signal.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import math
import sys
import threading
import time
from types import SimpleNamespace

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from local.runtime_platform import owned_playback_only


@dataclass(frozen=True)
class SmtcSnapshot:
    key: tuple
    title: str
    artist: str
    album: str
    artwork: bytes
    status: str
    position: float
    duration: float
    seekable: bool


def _seconds(value):
    try:
        number = float(value)
        return max(0.0, number) if math.isfinite(number) else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _owned_key(owned):
    track = owned.track
    return (str(track.track_id), *(getattr(owned, name, 0) for name in
        ("_epoch", "_catalog_epoch", "_queue_epoch", "_mode_epoch")))


def _playback_intent(owned):
    recovery = getattr(owned, "_stream_recovery", None)
    if recovery is not None:
        return bool(recovery.resume)
    if owned.status in {"loading_catalog", "loading_stream", "recovering_stream"}:
        return bool(getattr(owned, "_autoplay_after_load", False))
    return _audibly_playing(owned)


def _audibly_playing(owned):
    return bool(owned.track.is_playing or owned.is_personal_dj_speaking()
        or owned._is_voice_segment() and owned.player.playbackState().name == "PlayingState")


def owned_smtc_snapshot(owned, active):
    """Read the existing owned-player contract on its GUI thread only."""
    track = owned.track
    if not active or track is None or getattr(owned, "_closed", False):
        return None
    if _audibly_playing(owned):
        status = "playing"
    elif owned.status in {"playback_failed", "stream_unavailable", "catalog_unavailable"}:
        status = "stopped"
    elif _playback_intent(owned):
        status = "changing"
    else:
        status = "paused"
    duration = _seconds(track.duration_seconds)
    position = min(_seconds(track.position_seconds), duration) if duration else 0.0
    # Legacy speech entries show the associated song clock, never a seekable
    # narration clock. The same existing guard also protects actual seeking.
    seekable = bool(duration and not owned._is_voice_segment() and owned.player.isSeekable())
    song = next((song for song in owned.queue
        if f"owned:{song.source_id}:{song.song_id}" == track.track_id), None)
    return SmtcSnapshot(_owned_key(owned), track.title, track.artist,
        str(getattr(song, "album", "") or ""), track.album_art_bytes,
        status, position, duration, seekable)


def _load_sdk():
    from winsdk.windows.media import (MediaPlaybackStatus, MediaPlaybackType,
        SystemMediaTransportControlsButton, SystemMediaTransportControlsTimelineProperties)
    from winsdk.windows.media.playback import MediaPlayer
    from winsdk.windows.storage.streams import (DataWriter, InMemoryRandomAccessStream,
        RandomAccessStreamReference)
    return SimpleNamespace(MediaPlayer=MediaPlayer, PlaybackStatus=MediaPlaybackStatus,
        PlaybackType=MediaPlaybackType, Button=SystemMediaTransportControlsButton,
        Timeline=SystemMediaTransportControlsTimelineProperties, DataWriter=DataWriter,
        MemoryStream=InMemoryRandomAccessStream, StreamReference=RandomAccessStreamReference)


class _NativeSmtcSession:
    def __init__(self, command_callback, *, sdk=None):
        self.sdk = sdk or _load_sdk()
        self.player = self.sdk.MediaPlayer()
        self.controls = None
        self._tokens = []
        self._snapshot = None
        self._metadata = None
        self._art_stream = None
        self._closed = False
        self._command_callback = command_callback
        try:
            # Disable WinRT's automatic playback command handling: the real
            # sources and transport belong exclusively to OwnedMusicPlayer.
            self.player.is_muted = True
            self.player.command_manager.is_enabled = False
            self.controls = self.player.system_media_transport_controls
            self.controls.is_enabled = False
            for name in ("play", "pause", "next", "previous"):
                setattr(self.controls, f"is_{name}_enabled", True)
            for name in ("stop", "record", "fast_forward", "rewind", "channel_up", "channel_down"):
                setattr(self.controls, f"is_{name}_enabled", False)
            self._tokens.append(("button_pressed", self.controls.add_button_pressed(self._button_pressed)))
            self._tokens.append(("playback_position_change_requested",
                self.controls.add_playback_position_change_requested(self._position_requested)))
        except Exception:
            self.close()
            raise

    def _request(self, kind, value=None):
        snapshot = self._snapshot
        if snapshot is not None:
            self._command_callback((snapshot.key, kind, value, time.monotonic() + 2.0))

    def _button_pressed(self, _sender, args):
        commands = {self.sdk.Button.PLAY: "play", self.sdk.Button.PAUSE: "pause",
            self.sdk.Button.NEXT: "next", self.sdk.Button.PREVIOUS: "previous"}
        command = commands.get(args.button)
        if command:
            self._request(command)

    def _position_requested(self, _sender, args):
        if self._snapshot is not None and self._snapshot.seekable:
            self._request("seek", args.requested_playback_position.total_seconds())

    async def _thumbnail(self, content):
        if not content:
            return None, None
        stream = self.sdk.MemoryStream()
        writer = self.sdk.DataWriter(stream)
        try:
            writer.write_bytes(content)
            await writer.store_async()
            writer.detach_stream()
            stream.seek(0)
            return self.sdk.StreamReference.create_from_stream(stream), stream
        except Exception:
            stream.close()
            raise
        finally:
            writer.close()

    async def publish(self, snapshot):
        metadata = (snapshot.key[0], snapshot.title, snapshot.artist, snapshot.album, snapshot.artwork)
        if metadata != self._metadata:
            updater = self.controls.display_updater
            # This winsdk projection rejects None for thumbnail despite its
            # Optional stub. clear_all removes the preceding song's artwork.
            updater.clear_all()
            updater.type = self.sdk.PlaybackType.MUSIC
            updater.app_media_id = snapshot.key[0]
            updater.music_properties.title = snapshot.title
            updater.music_properties.artist = snapshot.artist
            updater.music_properties.album_title = snapshot.album
            old_stream = self._art_stream
            # Artwork failure cannot prevent transport and title publication.
            try:
                thumbnail, self._art_stream = await self._thumbnail(snapshot.artwork)
            except Exception:
                thumbnail, self._art_stream = None, None
            if thumbnail is not None:
                updater.thumbnail = thumbnail
            updater.update()
            if old_stream is not None:
                old_stream.close()
            self._metadata = metadata
        timeline = self.sdk.Timeline()
        timeline.start_time = timeline.min_seek_time = timedelta()
        timeline.position = timedelta(seconds=snapshot.position)
        timeline.end_time = timedelta(seconds=snapshot.duration)
        timeline.max_seek_time = timedelta(seconds=snapshot.duration if snapshot.seekable else 0.0)
        self.controls.update_timeline_properties(timeline)
        self.controls.playback_status = getattr(self.sdk.PlaybackStatus, snapshot.status.upper())
        self._snapshot = snapshot
        self.controls.is_enabled = True

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._snapshot = None
        if self.controls is not None:
            for event, token in self._tokens:
                try:
                    getattr(self.controls, f"remove_{event}")(token)
                except Exception:
                    pass
            self._tokens.clear()
            try:
                self.controls.is_enabled = False
                self.controls.playback_status = self.sdk.PlaybackStatus.CLOSED
                self.controls.display_updater.clear_all()
                self.controls.display_updater.update()
            except Exception:
                pass
        if self._art_stream is not None:
            try:
                self._art_stream.close()
            except Exception:
                pass
            self._art_stream = None
        try:
            self.player.close()
        except Exception:
            pass


class _SmtcWorker:
    """A one-slot mailbox bounds publication during rapid Qt clock updates."""
    def __init__(self, command_callback, *, session_factory=_NativeSmtcSession):
        self._callback = command_callback
        self._session_factory = session_factory
        self._condition = threading.Condition()
        self._pending = False
        self._latest = None
        self._closed = False
        self.last_error = ""
        self._thread = threading.Thread(target=self._run, name="GluSpotSMTC", daemon=True)
        self._thread.start()

    def submit(self, snapshot):
        with self._condition:
            if not self._closed:
                self._latest, self._pending = snapshot, True
                self._condition.notify()

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify()
        self._thread.join(timeout=1.0)

    def _run(self):
        asyncio.run(self._run_async())

    async def _run_async(self):
        session = None
        retry_at = 0.0
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closed or self._pending)
                    if self._closed:
                        return
                    snapshot, self._pending = self._latest, False
                if snapshot is None:
                    if session is not None:
                        session.close()
                        session = None
                    continue
                if session is None and time.monotonic() < retry_at:
                    continue
                try:
                    if session is None:
                        session = self._session_factory(self._callback)
                    await session.publish(snapshot)
                    self.last_error = ""
                except Exception as exc:
                    # Bounded non-sensitive diagnostic; do not include a track,
                    # address or account payload in an exception string.
                    self.last_error = type(exc).__name__
                    retry_at = time.monotonic() + 5.0
                    if session is not None:
                        session.close()
                        session = None
        finally:
            if session is not None:
                session.close()


class WindowsSmtcBridge(QObject):
    _command_requested = Signal(object)

    def __init__(self, owned_player, active_getter, parent=None, *, worker_factory=None):
        super().__init__(parent)
        self.owned = owned_player
        self._active_getter = active_getter
        self._closed = False
        self._last_publication = None
        self._worker = None
        self._command_requested.connect(self._apply_command, Qt.ConnectionType.QueuedConnection)
        if worker_factory is not None or sys.platform == "win32" and not owned_playback_only():
            self._worker = (worker_factory or _SmtcWorker)(self._command_requested.emit)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.refresh)
        if self._worker is not None:
            self._timer.start()
        self.owned.changed.connect(self.refresh)
        self.owned.statusChanged.connect(self.refresh)
        self.refresh()

    @Slot()
    def refresh(self):
        if self._closed or self._worker is None:
            return
        snapshot = owned_smtc_snapshot(self.owned, self._active_getter())
        publication = None if snapshot is None else (
            snapshot.key, snapshot.title, snapshot.artist, snapshot.album, snapshot.artwork,
            snapshot.status, int(snapshot.position * 2), snapshot.duration, snapshot.seekable)
        # Retry a failed OS session at the bounded timer cadence. Ordinary
        # position publication stays at most twice per source-clock second.
        if publication != self._last_publication or self._worker.last_error:
            self._last_publication = publication
            self._worker.submit(snapshot)

    @Slot(object)
    def _apply_command(self, request):
        key, command, value, deadline = request
        if (self._closed or time.monotonic() > deadline or not self._active_getter()
                or self.owned.track is None or _owned_key(self.owned) != key):
            return
        if command == "seek":
            self.owned.seek_playback_position(value, key[0])
        elif command in {"play", "pause"}:
            # Native Play/Pause are idempotent, unlike the existing toggle.
            # Pending DJ/catalog work uses the player's current resume intent.
            if _playback_intent(self.owned) != (command == "play"):
                self.owned.send_playback_command("toggle_play_pause")
        elif command in {"previous", "next"}:
            self.owned.send_playback_command(command)
        self.refresh()

    @Slot()
    def close(self):
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self.owned.changed.disconnect(self.refresh)
        self.owned.statusChanged.disconnect(self.refresh)
        if self._worker is not None:
            self._worker.close()
