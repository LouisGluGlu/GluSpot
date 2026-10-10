"""Autonomous Qt playback with a source clock, never external-player scraping.

The post-login product surface can call play_queue/play_daily/play_personal_fm.
Fetching streams and lyrics is asynchronous; stale replies cannot replace a song.
"""
from __future__ import annotations

import time
import threading
from dataclasses import replace
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QThread, QUrl, Qt, Signal, Slot
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from api.models import AlbumQueueEntry, ColorToken, IslandVisualData, TrackData
from api.online_music import OnlineSong
from api.online_music_artwork import fetch_online_artwork, online_artwork_colors
from local.music_tasks import MusicTasks


class OwnedMusicPlayer(QObject):
    changed = Signal()
    statusChanged = Signal(str)
    _control_requested = Signal(object)

    def __init__(self, service, lyrics_store, parent=None, *, player=None, audio=None, tasks=None, artwork_tasks=None):
        super().__init__(parent)
        self.service = service
        self.lyrics_store = lyrics_store
        self.audio = audio or QAudioOutput(self)
        self.player = player or QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.tasks = tasks or MusicTasks(self)
        self.tasks.completed.connect(self._completed)
        self._control_requested.connect(self._apply_control_request, Qt.ConnectionType.QueuedConnection)
        # Image downloads/decoding cannot occupy the stream/lyric workers.
        self.artwork_tasks = artwork_tasks or MusicTasks(self)
        self.artwork_tasks.completed.connect(self._completed)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._state_changed)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.errorOccurred.connect(self._error)
        self.queue: tuple[OnlineSong, ...] = ()
        self.index = -1
        self.track: TrackData | None = None
        self.status = "idle"
        self.failure_reason = ""
        self._epoch = 0
        self._catalog_epoch = 0
        self._fm_source = ""
        self._stream_epoch = 0
        self._stream_trial = False
        self._closed = False

    def _set_status(self, value):
        self.status = value
        self.statusChanged.emit(value)

    def play_queue(self, songs, index=0):
        songs = tuple(songs)
        if not songs or not all(isinstance(song, OnlineSong) for song in songs):
            return False
        if not 0 <= index < len(songs):
            return False
        self._catalog_epoch += 1
        self._fm_source = ""
        self.queue = songs
        self.index = index
        return self._load_song()

    def play_daily(self, source_id="netease"):
        return self._catalog("daily", source_id, lambda: self.service.daily_recommendations(source_id))

    def play_personal_fm(self, source_id="netease"):
        return self._catalog("fm", source_id, lambda: self.service.personal_fm(source_id))

    def play_playlist(self, source_id, playlist_id):
        epoch = self._catalog_epoch + 1
        def fetch():
            songs, offset = [], 0
            # Bounded pagination follows service evidence, not guessed totals.
            for _ in range(100):
                if self._closed or epoch != self._catalog_epoch:
                    return ()
                page = self.service.playlist_tracks(source_id, playlist_id, limit=100, offset=offset)
                if self._closed or epoch != self._catalog_epoch:
                    return ()
                songs.extend(page.items)
                if not page.has_more or page.next_offset <= offset:
                    return tuple(songs)
                offset = page.next_offset
            return tuple(songs)
        return self._catalog("playlist", source_id, fetch)

    def _catalog(self, kind, source_id, action):
        if self._closed:
            return False
        self._catalog_epoch += 1
        epoch = self._catalog_epoch
        # A previous stream or Qt state must not confirm a new catalog opening.
        self._stream_epoch = 0
        self.failure_reason = ""
        self._set_status("loading_catalog")
        accepted = self.tasks.submit(("catalog", epoch, kind, source_id),
            lambda: action() if not self._closed and epoch == self._catalog_epoch else ())
        if not accepted:
            self._set_status("busy")
        return accepted

    def _load_song(self):
        if self._closed:
            return False
        self._epoch += 1
        epoch = self._epoch
        self._stream_epoch = 0
        self._stream_trial = False
        self.failure_reason = ""
        song = self.queue[self.index]
        self.player.stop()
        self.player.setSource(QUrl())
        self.track = TrackData(
            song.title, song.artist, ("加载歌词中",), ColorToken(58, 68, 88), ColorToken(34, 44, 62), (),
            source_app=f"GluSpot · {song.source_id}", is_playing=False,
            duration_seconds=song.duration_seconds, track_id=f"owned:{song.source_id}:{song.song_id}",
            position_source="owned-player", position_confidence=1.0,
            source_app_id="GluSpot", position_sampled_at=time.monotonic(), position_observed_seconds=0.0,
            album_art_lookup_state="pending" if song.artwork_url else "miss",
            album_queue=tuple(AlbumQueueEntry(s.title, s.artist, s.album,
                track_id=f"owned:{s.source_id}:{s.song_id}") for s in self.queue),
        )
        self._set_status("loading_stream")
        catalog_epoch = self._catalog_epoch
        accepted = self.tasks.submit(("stream", epoch, catalog_epoch), lambda: self.service.stream_url(song.source_id, song.song_id)
            if not self._closed and epoch == self._epoch and catalog_epoch == self._catalog_epoch else None)
        if not accepted:
            self._set_status("busy")
            self._lyrics_unavailable()
        else:
            self.changed.emit()
        if song.artwork_url:
            fallback, secondary = self.track.album_color, self.track.secondary_color
            art_accepted = self.artwork_tasks.submit(("artwork", epoch),
                lambda: self._fetch_artwork(song.artwork_url, fallback, secondary, epoch))
            if not art_accepted:
                self.track = replace(self.track, album_art_lookup_state="failed")
                self.changed.emit()
        return accepted

    def _completed(self, token, result, error):
        if self._closed:
            return
        kind, epoch, *extra = token
        if kind == "catalog":
            if epoch != self._catalog_epoch:
                return
            if error or not result:
                self.failure_reason = self._request_failure(error)
                self._set_status("catalog_unavailable")
                return
            catalog_kind, source = extra
            if self.play_queue(result):
                self._fm_source = source if catalog_kind == "fm" else ""
            return
        if epoch != self._epoch or self.track is None:
            return
        if kind == "stream":
            if extra and extra[0] != self._catalog_epoch:
                return
            if error or result is None or not result.playable:
                self.failure_reason = self._request_failure(error)
                self._set_status("stream_unavailable")
                self._lyrics_unavailable()
                return
            try:
                parsed = urlsplit(result.url)
                valid_url = (parsed.scheme in {"https", "http"} and bool(parsed.hostname)
                    and not parsed.username and not parsed.password and len(result.url) <= 16384
                    and not any(ord(character) < 32 for character in result.url))
            except (TypeError, ValueError):
                valid_url = False
            if not valid_url:
                self.failure_reason = "invalid_stream"
                self._set_status("stream_unavailable")
                self._lyrics_unavailable()
                return
            self._stream_epoch = epoch
            self._stream_trial = bool(result.trial)
            self.player.setSource(QUrl(result.url))
            if self._stream_epoch != epoch or self.status == "playback_failed":
                return
            self.player.play()
            # QMediaPlayer confirms PlayingState asynchronously. A synchronous
            # decoder/network error must not be overwritten by a queued play.
            if self._stream_epoch != epoch or self.status == "playback_failed":
                return
            song = self.queue[self.index]
            if not self.tasks.submit(("lyrics", epoch), lambda: self._fetch_lyrics(song)
                    if not self._closed and epoch == self._epoch else None):
                self._lyrics_unavailable()
        elif kind == "lyrics":
            if error or result is None or not result.lyrics:
                self._lyrics_unavailable()
                return
            self.track = replace(self.track, timed_lyrics=result.timed_lyrics, lyrics=result.lyrics,
                                 lyrics_source=result.source)
            self.changed.emit()
        elif kind == "artwork":
            if error or not result:
                self.track = replace(self.track, album_art_lookup_state="failed")
            else:
                content, primary, secondary = result
                queue = tuple(replace(entry, album_art_bytes=content, album_color=primary,
                    secondary_color=secondary) if entry.track_id == self.track.track_id else entry
                    for entry in self.track.album_queue)
                self.track = replace(self.track, album_art_bytes=content, album_color=primary,
                    secondary_color=secondary, album_art_source="original",
                    album_art_lookup_state="original", album_queue=queue)
            self.changed.emit()

    def _lyrics_unavailable(self):
        if self.track is not None:
            self.track = replace(self.track, timed_lyrics=(), lyrics=("暂无歌词",), lyrics_source="")
            self.changed.emit()

    def _fetch_artwork(self, url, fallback, secondary, epoch):
        if self._closed or epoch != self._epoch:
            return None
        content = fetch_online_artwork(url)
        if not content or self._closed or epoch != self._epoch:
            return None
        primary, companion = online_artwork_colors(content, fallback, secondary)
        return (content, primary, companion) if not self._closed and epoch == self._epoch else None

    def _fetch_lyrics(self, song):
        payload = self.service.lyrics(song.source_id, song.song_id)
        return self.lyrics_store.parse_online_lyrics(
            raw_lrc=payload.raw_lrc, raw_yrc=payload.raw_yrc, translation=payload.translation,
            romanization=payload.romanization, source=payload.source or song.source_id)

    def _position_changed(self, milliseconds):
        if self.track is not None:
            seconds = max(0.0, milliseconds / 1000.0)
            self.track = replace(self.track, position_seconds=seconds, position_observed_seconds=seconds,
                                 position_sampled_at=time.monotonic())
            self.changed.emit()

    def _duration_changed(self, milliseconds):
        if self.track is not None and milliseconds > 0:
            self.track = replace(self.track, duration_seconds=milliseconds / 1000.0)

    def _state_changed(self, state):
        if self.track is not None:
            self.track = replace(self.track, is_playing=(state == QMediaPlayer.PlaybackState.PlayingState
                and self.status != "playback_failed"))
            if (state == QMediaPlayer.PlaybackState.PlayingState
                    and self._stream_epoch == self._epoch and not self._closed):
                self._set_status("trial" if self._stream_trial else "playing")
            self.changed.emit()

    def _media_status(self, status):
        if self._closed or self.track is None:
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.index + 1 < len(self.queue):
                self.index += 1
                self._load_song()
            elif self._fm_source:
                self.play_personal_fm(self._fm_source)
            else:
                self._set_status("ended")

    @staticmethod
    def _request_failure(error):
        return error if error in {"auth_required", "credential", "timeout", "network", "unsupported", "invalid_response", "response"} else "unavailable"

    def _error(self, code, *_args):
        if self._closed or self.track is None or self._stream_epoch != self._epoch:
            return
        self._stream_epoch = 0
        self.failure_reason = {
            QMediaPlayer.Error.NetworkError: "network",
            QMediaPlayer.Error.FormatError: "format",
            QMediaPlayer.Error.AccessDeniedError: "access_denied",
            QMediaPlayer.Error.ResourceError: "resource",
        }.get(code, "backend")
        self.track = replace(self.track, is_playing=False)
        self._set_status("playback_failed")
        self._lyrics_unavailable()

    def send_playback_command(self, command):
        if self.track is None or self._closed:
            return False
        if command not in {"toggle_play_pause", "previous", "next"}:
            return False
        if QThread.currentThread() != self.thread():
            return self._request_control("command", command, self.track.track_id)
        if command == "toggle_play_pause":
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            else:
                self.player.play()
            return True
        if command in {"previous", "next"}:
            target = self.index + (-1 if command == "previous" else 1)
            if 0 <= target < len(self.queue):
                self.index = target
                return self._load_song()
            if command == "next" and self._fm_source:
                return self.play_personal_fm(self._fm_source)
        return False

    def seek_playback_position(self, seconds, expected_track_id=None):
        if self.track is None or (expected_track_id and expected_track_id != self.track.track_id):
            return False
        if self._closed:
            return False
        if QThread.currentThread() != self.thread():
            return self._request_control("seek", seconds, expected_track_id or self.track.track_id)
        if not self.player.isSeekable():
            return False
        seconds = max(0.0, min(float(seconds), self.track.duration_seconds))
        self.player.setPosition(round(seconds * 1000))
        return True

    def _request_control(self, kind, value, track_id):
        # Existing island command/seek callers run on daemon workers. Qt media
        # objects stay on their owning thread; the worker receives actual success.
        request = (kind, value, track_id, time.monotonic() + 2.0, threading.Event(), {})
        self._control_requested.emit(request)
        if not request[4].wait(2.0):
            request[5]["cancelled"] = True
            return False
        return bool(request[5].get("succeeded", False))

    @Slot(object)
    def _apply_control_request(self, request):
        kind, value, track_id, deadline, completed, result = request
        try:
            if (self._closed or result.get("cancelled") or time.monotonic() >= deadline or self.track is None
                    or self.track.track_id != track_id):
                return
            result["succeeded"] = (self.send_playback_command(value) if kind == "command"
                                   else self.seek_playback_position(value, track_id))
        finally:
            completed.set()

    def visual_data(self):
        if self.track is None:
            return None
        return IslandVisualData(self.track, (), ())

    def close(self):
        self._closed = True
        self.stop()
        self.tasks.close()
        self.artwork_tasks.close()

    def stop(self):
        """Invalidate queued I/O before disabling or changing playback modes."""
        self._epoch += 1
        self._catalog_epoch += 1
        self._stream_epoch = 0
        self._stream_trial = False
        self.failure_reason = ""
        self._fm_source = ""
        self.track = None
        self.queue = ()
        self.index = -1
        self.player.stop()
        self.player.setSource(QUrl())
        self._set_status("idle")
        self.changed.emit()
