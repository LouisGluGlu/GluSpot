"""Autonomous Qt playback with a source clock, never external-player scraping.

The post-login product surface can call play_queue/play_daily/play_personal_fm.
Fetching streams and lyrics is asynchronous; stale replies cannot replace a song.
"""
from __future__ import annotations

import time
import threading
import random
import copy
from collections import deque
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from api.models import AlbumQueueEntry, ColorToken, IslandVisualData, TrackData
from api.online_music import LyricPayload, OnlineSong
from api.online_music_artwork import ONLINE_ARTWORK_MAX_BYTES, fetch_online_artwork, online_artwork_colors
from local.music_tasks import MusicTasks


@dataclass
class _StreamRecovery:
    epoch: int
    position_ms: int
    deadline: float
    reason: str
    phase: str = "authorize"
    resume: bool = True
    progress_position_ms: int = 0


class OwnedMusicPlayer(QObject):
    ARTWORK_NEIGHBORS = 3
    ARTWORK_CACHE_MAX_BYTES = 32 * 1024 * 1024
    PLAYBACK_MODES = frozenset({"sequential", "shuffle", "repeat_one"})
    SHUFFLE_HISTORY_LIMIT = 256
    STREAM_FAULT_STALL_SECONDS = 1.0
    STREAM_FAULT_END_TOLERANCE_MS = 500
    STREAM_RECOVERY_TIMEOUT_SECONDS = 12.0
    STREAM_RECOVERY_SEEK_TOLERANCE_MS = 250
    changed = Signal()
    statusChanged = Signal(str)
    _control_requested = Signal(object)

    def __init__(self, service, lyrics_store, parent=None, *, player=None, audio=None, tasks=None, artwork_tasks=None, prefetch_tasks=None):
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
        self.prefetch_tasks = prefetch_tasks or MusicTasks(self)
        self.prefetch_tasks.completed.connect(self._completed)
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
        self._decoded_duration_ms = 0
        self._stream_fault = None
        self._stream_recovery = None
        self._stream_recovery_attempted = False
        self._stream_fault_timer = QTimer(self)
        self._stream_fault_timer.setInterval(250)
        self._stream_fault_timer.timeout.connect(self._check_stream_fault)
        self._queue_epoch = 0
        # Only the current song's immutable, service-bounded source payload is
        # retained. Preferences reparse it off the GUI thread without new I/O.
        self._lyric_payload: LyricPayload | None = None
        self._lyrics_generation = 0
        self._lyrics_parse_pending = None
        self.playback_mode = "sequential"
        self._normal_playback_mode = "sequential"
        self._mode_epoch = 0
        self._random = random.Random()
        self._shuffle_history = []
        self._shuffle_cursor = -1
        self._shuffle_bag = deque()
        self._shuffle_pending = deque()
        self._art_cache = {}
        self._art_window = {}
        self._art_distances = {}
        self._art_attempted = set()
        self._art_failed = set()
        self._art_running = {}
        self._art_request = 0
        self._fm_prefetch_pending = False
        self._fm_prefetch_attempt = None
        self._fm_advance_pending = False
        self._closed = False

    def _set_status(self, value):
        self.status = value
        self.statusChanged.emit(value)

    def play_queue(self, songs, index=0):
        return self._replace_queue(songs, index)

    def _replace_queue(self, songs, index=0, *, fm_source=""):
        if self._closed:
            return False
        songs = tuple(songs)
        if not songs or not all(isinstance(song, OnlineSong) for song in songs):
            return False
        if not 0 <= index < len(songs):
            return False
        self._catalog_epoch += 1
        self._set_fm_source(fm_source)
        self._reset_queue_cache()
        self.queue = songs
        self.index = index
        self._reset_playback_order()
        return self._load_song()

    def is_personal_fm(self):
        return bool(self._fm_source)

    def available_playback_modes(self):
        return ("sequential", "repeat_one") if self.is_personal_fm() else ("sequential", "shuffle", "repeat_one")

    def _set_fm_source(self, source):
        if source and not self._fm_source:
            self._normal_playback_mode = self.playback_mode
            self.playback_mode = "sequential"
        elif self._fm_source and not source:
            self.playback_mode = self._normal_playback_mode
        self._fm_source = source

    def set_playback_mode(self, mode):
        if not isinstance(mode, str) or mode not in self.available_playback_modes() or self._closed:
            return False
        if QThread.currentThread() != self.thread():
            return self._request_control("mode", mode, None)
        if mode == self.playback_mode:
            return True
        self.playback_mode = mode
        if not self._fm_source:
            self._normal_playback_mode = mode
        self._reset_playback_order()
        if self.track is not None and self.queue:
            self._refresh_artwork_window()
            self._apply_cached_artwork()
            self._pump_artwork()
        else:
            self.changed.emit()
        return True

    def _reset_playback_order(self):
        self._mode_epoch += 1
        self._shuffle_history = [self.index] if 0 <= self.index < len(self.queue) else []
        self._shuffle_cursor = len(self._shuffle_history) - 1
        self._shuffle_pending.clear()
        bag = ([i for i in self._shuffle_indices()
                if (self.queue[i].source_id, self.queue[i].song_id)
                    != (self.queue[self.index].source_id, self.queue[self.index].song_id)]
               if self._uses_shuffle_order() and self._shuffle_history else [])
        if bag:
            self._random.shuffle(bag)
        self._shuffle_bag = deque(bag)

    def _shuffle_indices(self):
        # Duplicate catalog entries are one song in a random cycle. Otherwise
        # two positions with the same source/id could repeat immediately.
        unique = {}
        for i, song in enumerate(self.queue):
            unique.setdefault((song.source_id, song.song_id), i)
        return tuple(unique.values())

    def _uses_shuffle_order(self):
        # Personal FM is already a service-owned continuous stream. Random
        # queue mode must not turn its previous recommendations into a loop.
        return self.playback_mode == "shuffle" and not self._fm_source

    def _ensure_shuffle_future(self):
        required = max(0, self.ARTWORK_NEIGHBORS - (len(self._shuffle_history) - self._shuffle_cursor - 1))
        while self.queue and len(self._shuffle_pending) < required:
            tail = self._shuffle_pending[-1] if self._shuffle_pending else self._shuffle_history[-1]
            if not self._shuffle_bag:
                bag = list(self._shuffle_indices())
                self._random.shuffle(bag)
                if len(bag) > 1 and ((self.queue[bag[0]].source_id, self.queue[bag[0]].song_id)
                                    == (self.queue[tail].source_id, self.queue[tail].song_id)):
                    bag[0], bag[1] = bag[1], bag[0]
                self._shuffle_bag = deque(bag)
            self._shuffle_pending.append(self._shuffle_bag.popleft())

    def _upcoming_indices(self):
        if not self.queue:
            return ()
        if self._uses_shuffle_order():
            self._ensure_shuffle_future()
            future = self._shuffle_history[self._shuffle_cursor + 1:] + list(self._shuffle_pending)
            return tuple(future[:self.ARTWORK_NEIGHBORS])
        if self._fm_source:
            return tuple(range(self.index + 1, min(len(self.queue), self.index + self.ARTWORK_NEIGHBORS + 1)))
        # The list loops, including at its ends. Do not duplicate a small
        # queue's cover or advertise the current track as an upcoming cover.
        return tuple((self.index + step) % len(self.queue)
                     for step in range(1, min(self.ARTWORK_NEIGHBORS, len(self.queue) - 1) + 1))

    def _previous_indices(self):
        if self._uses_shuffle_order():
            return tuple(reversed(self._shuffle_history[max(0, self._shuffle_cursor - self.ARTWORK_NEIGHBORS):self._shuffle_cursor]))
        if self._fm_source:
            return tuple(range(self.index - 1, max(-1, self.index - self.ARTWORK_NEIGHBORS - 1), -1))
        return tuple((self.index - step) % len(self.queue)
                     for step in range(1, min(self.ARTWORK_NEIGHBORS, len(self.queue) - 1) + 1))

    def _advance_queue(self, direction=1):
        if self._uses_shuffle_order():
            if direction < 0:
                if self._shuffle_cursor <= 0:
                    return False
                self._shuffle_cursor -= 1
            elif self._shuffle_cursor + 1 < len(self._shuffle_history):
                self._shuffle_cursor += 1
            else:
                self._ensure_shuffle_future()
                self._shuffle_history.append(self._shuffle_pending.popleft())
                self._shuffle_cursor += 1
                history_limit = min(self.SHUFFLE_HISTORY_LIMIT, len(self.queue) + self.ARTWORK_NEIGHBORS)
                if len(self._shuffle_history) > history_limit:
                    self._shuffle_history.pop(0)
                    self._shuffle_cursor -= 1
            self.index = self._shuffle_history[self._shuffle_cursor]
        else:
            target = self.index + direction
            if self._fm_source:
                if target >= len(self.queue):
                    return self._advance_fm()
                if target < 0:
                    return False
                self.index = target
            else:
                self.index = target % len(self.queue)
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
        self._fm_prefetch_pending = False
        self._fm_advance_pending = False
        epoch = self._catalog_epoch
        # A previous stream or Qt state must not confirm a new catalog opening.
        self._stream_epoch = 0
        self._clear_stream_recovery()
        self._clear_stream_fault()
        self._decoded_duration_ms = 0
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
        self._reset_lyrics_payload()
        epoch = self._epoch
        self._stream_epoch = 0
        self._stream_trial = False
        self._clear_stream_recovery()
        self._stream_recovery_attempted = False
        self._clear_stream_fault()
        self._decoded_duration_ms = 0
        self._fm_advance_pending = False
        self.failure_reason = ""
        if self._fm_source and self.index > self.ARTWORK_NEIGHBORS:
            self.queue = self.queue[self.index - self.ARTWORK_NEIGHBORS:]
            self.index = self.ARTWORK_NEIGHBORS
        self._refresh_artwork_window()
        song = self.queue[self.index]
        cached = self._art_cache.get(self._art_key(song))
        self.player.stop()
        self.player.setSource(QUrl())
        self.track = TrackData(
            song.title, song.artist, ("加载歌词中",), cached[1] if cached else ColorToken(58, 68, 88),
            cached[2] if cached else ColorToken(34, 44, 62), (),
            album_art_bytes=cached[0] if cached else b"",
            source_app=f"GluSpot · {song.source_id}", is_playing=False,
            duration_seconds=song.duration_seconds, track_id=f"owned:{song.source_id}:{song.song_id}",
            position_source="owned-player", position_confidence=1.0,
            source_app_id="GluSpot", position_sampled_at=time.monotonic(), position_observed_seconds=0.0,
            album_art_source="original" if cached else "",
            album_art_lookup_state="original" if cached else "failed" if self._art_key(song) in self._art_failed
                else "pending" if song.artwork_url else "miss",
            album_queue=self._album_queue(),
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
        key = self._art_key(song)
        if not cached and key not in self._art_failed:
            self._art_attempted.discard(key)
        self._pump_artwork()
        return accepted

    def _completed(self, token, result, error):
        if self._closed:
            return
        kind, epoch, *extra = token
        if kind == "artwork":
            self._artwork_completed(epoch, extra, result, error)
            return
        if kind == "fm_prefetch":
            self._fm_prefetch_completed(epoch, extra, result, error)
            return
        if kind == "catalog":
            if epoch != self._catalog_epoch:
                return
            if error or not result:
                self.failure_reason = self._request_failure(error)
                self._set_status("catalog_unavailable")
                return
            catalog_kind, source = extra
            if catalog_kind == "fm_continue":
                incoming = self._unique_fm_songs(source, result)
                if not incoming:
                    self._set_status("catalog_unavailable")
                    return
                self.index = len(self.queue)
                self.queue += incoming
                self._load_song()
                return
            self._replace_queue(result, fm_source=source if catalog_kind == "fm" else "")
            return
        if epoch != self._epoch or self.track is None:
            return
        if kind == "stream":
            if extra and extra[0] != self._catalog_epoch:
                return
            if self._stream_epoch == 0 and self.status == "playback_failed":
                return
            if error or result is None or not result.playable:
                self.failure_reason = self._request_failure(error)
                if self._stream_recovery is not None:
                    self._finish_stream_failure(self.failure_reason)
                    return
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
                if self._stream_recovery is not None:
                    self._finish_stream_failure(self.failure_reason)
                    return
                self._set_status("stream_unavailable")
                self._lyrics_unavailable()
                return
            self._stream_epoch = epoch
            self._stream_trial = bool(result.trial)
            if self._stream_recovery is not None:
                self._stream_recovery.phase = "load"
            self.player.setSource(QUrl(result.url))
            if self._stream_epoch != epoch or self.status == "playback_failed":
                return
            if self._stream_recovery is not None:
                if self._lyric_payload is None:
                    song = self.queue[self.index]
                    preferences = self._lyrics_preferences_snapshot()
                    if not self.tasks.submit(("lyrics", epoch, self._lyrics_generation),
                            lambda: self._fetch_lyrics(song, preferences)
                            if not self._closed and epoch == self._epoch else None):
                        self._lyrics_reparse_failed()
                self._check_stream_recovery()
                return
            self.player.play()
            # QMediaPlayer confirms PlayingState asynchronously. A synchronous
            # decoder/network error must not be overwritten by a queued play.
            if self._stream_epoch != epoch or self.status == "playback_failed":
                return
            song = self.queue[self.index]
            preferences = self._lyrics_preferences_snapshot()
            if not self.tasks.submit(("lyrics", epoch, self._lyrics_generation), lambda: self._fetch_lyrics(song, preferences)
                    if not self._closed and epoch == self._epoch else None):
                self._lyrics_unavailable()
            self._prefetch_fm()
        elif kind == "lyrics":
            if self._stream_epoch != epoch:
                return
            if error or result is None:
                self._lyrics_unavailable()
                return
            payload, match = result
            song = self.queue[self.index]
            if not isinstance(payload, LyricPayload) or (payload.source_id, payload.song_id) != (song.source_id, song.song_id):
                self._lyrics_unavailable()
                return
            self._lyric_payload = payload
            if extra and extra[0] != self._lyrics_generation:
                self._queue_lyrics_reparse()
                return
            self._apply_lyrics_match(match)
        elif kind == "lyrics_refresh":
            if self._lyrics_parse_pending != token or self._stream_epoch != epoch:
                return
            self._lyrics_parse_pending = None
            if extra[0] != self._lyrics_generation:
                self._queue_lyrics_reparse()
            elif not error:
                self._apply_lyrics_match(result)
            else:
                self._lyrics_reparse_failed()

    def _apply_lyrics_match(self, match):
        if match is None or not match.lyrics:
            self._lyrics_unavailable()
            return
        self.track = replace(self.track, timed_lyrics=match.timed_lyrics, lyrics=match.lyrics,
                             lyrics_source=match.source)
        self.changed.emit()

    def _reset_lyrics_payload(self):
        self._lyric_payload = None
        self._lyrics_generation += 1
        self._lyrics_parse_pending = None

    def refresh_lyrics(self):
        """Reparse the current source lyrics for session display preferences."""
        if self._closed or self.track is None:
            return False
        if QThread.currentThread() != self.thread():
            return self._request_control("lyrics_refresh", None, self.track.track_id)
        self._lyrics_generation += 1
        # A pending first fetch will retain its payload and reparse under this
        # generation. Repeated requests coalesce into at most one active parse.
        if self._lyric_payload is None or self._lyrics_parse_pending is not None:
            return True
        return self._queue_lyrics_reparse()

    def _queue_lyrics_reparse(self):
        if self._lyrics_parse_pending is not None:
            return True
        payload, epoch = self._lyric_payload, self._epoch
        if payload is None or self._closed or self.track is None or self._stream_epoch != epoch:
            return False
        token = ("lyrics_refresh", epoch, self._lyrics_generation)
        preferences = self._lyrics_preferences_snapshot()
        self._lyrics_parse_pending = token
        accepted = self.tasks.submit(token, lambda: self._parse_lyrics_payload(payload, preferences)
            if not self._closed and epoch == self._epoch and token[2] == self._lyrics_generation else None)
        if not accepted:
            self._lyrics_parse_pending = None
            self._lyrics_reparse_failed()
        return accepted

    def _lyrics_reparse_failed(self):
        # Keep established lyrics after a failed preference change, while a
        # failed first parse must terminate the existing loading marker.
        if (self.track is not None and not self.track.timed_lyrics and not self.track.lyrics_source
                and self.track.lyrics == ("加载歌词中",)):
            self._lyrics_unavailable()

    def _lyrics_unavailable(self):
        if self.track is not None:
            self.track = replace(self.track, timed_lyrics=(), lyrics=("暂无歌词",), lyrics_source="")
            self.changed.emit()

    @staticmethod
    def _art_key(song):
        return song.source_id, song.song_id, song.artwork_url

    def _reset_queue_cache(self):
        self._queue_epoch += 1
        self._art_cache.clear()
        self._art_window.clear()
        self._art_distances.clear()
        self._art_attempted.clear()
        self._art_failed.clear()
        self._fm_prefetch_pending = False
        self._fm_prefetch_attempt = None
        self._fm_advance_pending = False

    def _refresh_artwork_window(self):
        order = (self.index, *self._upcoming_indices(), *self._previous_indices())
        self._art_window = {self._art_key(self.queue[i]): self.queue[i] for i in order
                            if self.queue[i].artwork_url}
        self._art_distances = {}
        for rank, i in enumerate(order):
            key = self._art_key(self.queue[i])
            self._art_distances[key] = min(rank, self._art_distances.get(key, 7))
        self._art_cache = {key: value for key, value in self._art_cache.items() if key in self._art_window}
        self._art_attempted.intersection_update(self._art_window)
        self._art_failed.intersection_update(self._art_window)

    def _album_queue(self):
        entries = []
        # AlbumQueueEntry is the provider's upcoming-track contract. Previous
        # songs remain in the owned queue/cache and the island's own history.
        for i in self._upcoming_indices():
            song = self.queue[i]
            cached = self._art_cache.get(self._art_key(song))
            entries.append(AlbumQueueEntry(song.title, song.artist, song.album,
                album_art_bytes=cached[0] if cached else b"", track_id=f"owned:{song.source_id}:{song.song_id}",
                album_color=cached[1] if cached else None, secondary_color=cached[2] if cached else None))
        return tuple(entries)

    def _pump_artwork(self):
        if self._closed or self.track is None:
            return
        order = (self.index, *self._upcoming_indices(), *self._previous_indices())
        for i in order:
            if len(self._art_running) >= 2:
                break
            song = self.queue[i]
            key = self._art_key(song)
            if (key not in self._art_window or key in self._art_cache or key in self._art_attempted
                    or (self._queue_epoch, key) in self._art_running.values()):
                continue
            self._art_request += 1
            request, epoch = self._art_request, self._queue_epoch
            self._art_attempted.add(key)
            self._art_running[request] = (epoch, key)
            accepted = self.artwork_tasks.submit(("artwork", epoch, key, request),
                lambda key=key, epoch=epoch: self._fetch_artwork(key, epoch))
            if not accepted:
                self._art_running.pop(request, None)
                self._art_failed.add(key)
                if i == self.index:
                    self.track = replace(self.track, album_art_lookup_state="failed")
                    self.changed.emit()

    def _artwork_completed(self, epoch, extra, result, error):
        key, request = extra
        if self._art_running.pop(request, None) != (epoch, key):
            return
        if epoch == self._queue_epoch and key in self._art_window and self.track is not None:
            if error or not result or len(result[0]) > ONLINE_ARTWORK_MAX_BYTES:
                self._art_failed.add(key)
            else:
                self._art_cache[key] = result
                # Originals stay compressed; protect the current cover while
                # evicting the farthest neighbors if the shared byte cap fills.
                current = self._art_key(self.queue[self.index])
                while sum(len(value[0]) for value in self._art_cache.values()) > self.ARTWORK_CACHE_MAX_BYTES:
                    candidates = [key for key in self._art_cache if key != current]
                    if not candidates:
                        break
                    farthest = max(candidates, key=lambda key: self._art_distances[key])
                    self._art_cache.pop(farthest)
            self._apply_cached_artwork()
        self._pump_artwork()

    def _apply_cached_artwork(self):
        cached = self._art_cache.get(self._art_key(self.queue[self.index]))
        changes = {"album_queue": self._album_queue()}
        if cached:
            changes.update(album_art_bytes=cached[0], album_color=cached[1], secondary_color=cached[2],
                           album_art_source="original", album_art_lookup_state="original")
        elif self._art_key(self.queue[self.index]) in self._art_failed:
            changes["album_art_lookup_state"] = "failed"
        self.track = replace(self.track, **changes)
        self.changed.emit()

    def _fetch_artwork(self, key, epoch):
        if self._closed or epoch != self._queue_epoch or key not in self._art_window:
            return None
        content = fetch_online_artwork(key[2])
        if not content or self._closed or epoch != self._queue_epoch or key not in self._art_window:
            return None
        primary, companion = online_artwork_colors(content, ColorToken(58, 68, 88), ColorToken(34, 44, 62))
        return (content, primary, companion) if not self._closed and epoch == self._queue_epoch and key in self._art_window else None

    def _prefetch_fm(self):
        if (not self._fm_source or self._fm_prefetch_pending or self._closed
                or len(self.queue) - self.index - 1 >= self.ARTWORK_NEIGHBORS):
            return
        attempt = (self._queue_epoch, self.queue[-1].song_id)
        if attempt == self._fm_prefetch_attempt:
            return
        self._fm_prefetch_attempt = attempt
        epoch, source, catalog_epoch = self._queue_epoch, self._fm_source, self._catalog_epoch
        self._fm_prefetch_pending = True
        if not self.prefetch_tasks.submit(("fm_prefetch", epoch, catalog_epoch, source),
                lambda: self.service.personal_fm(source) if not self._closed and epoch == self._queue_epoch
                and catalog_epoch == self._catalog_epoch else ()):
            self._fm_prefetch_pending = False

    def _fm_prefetch_completed(self, epoch, extra, result, error):
        catalog_epoch, source = extra
        if epoch != self._queue_epoch or catalog_epoch != self._catalog_epoch or source != self._fm_source:
            return
        self._fm_prefetch_pending = False
        if error or not result:
            if self._fm_advance_pending:
                self._fm_advance_pending = False
                self.failure_reason = self._request_failure(error)
                self._set_status("catalog_unavailable")
            return
        incoming = self._unique_fm_songs(source, result)
        if incoming:
            self.queue += incoming
            self._refresh_artwork_window()
            self._apply_cached_artwork()
            self._pump_artwork()
        if self._fm_advance_pending:
            self._fm_advance_pending = False
            if self.index + 1 < len(self.queue):
                self.index += 1
                self._load_song()
            else:
                self._set_status("catalog_unavailable")

    def _unique_fm_songs(self, source, result):
        existing = {(song.source_id, song.song_id) for song in self.queue}
        incoming = []
        for song in result:
            if isinstance(song, OnlineSong) and song.source_id == source and (source, song.song_id) not in existing:
                incoming.append(song)
                existing.add((source, song.song_id))
                if len(incoming) == self.ARTWORK_NEIGHBORS:
                    break
        return tuple(incoming)

    def _advance_fm(self):
        if self._fm_prefetch_pending:
            self._fm_advance_pending = True
            self._stream_epoch = 0
            self._set_status("loading_catalog")
            return True
        source = self._fm_source
        return self._catalog("fm_continue", source, lambda: self.service.personal_fm(source))

    def _lyrics_preferences_snapshot(self):
        # The source parser reads these several times. Capture once on the
        # owner thread so a button change cannot mix preferences within a row.
        defaults = {"v2_enable_dual_line_lyrics": False, "v2_lyric_flow_style": "",
            "v2_ember_dual_line_enabled": False, "v2_dual_line_secondary_source": "translation",
            "v2_lyric_chinese_conversion": "off", "v2_cantonese_secondary_priority": "phonetic_first"}
        return {key: self.lyrics_store._setting_value(key, value) for key, value in defaults.items()}

    def _fetch_lyrics(self, song, preferences):
        payload = self.service.lyrics(song.source_id, song.song_id)
        return payload, self._parse_lyrics_payload(payload, preferences)

    def _parse_lyrics_payload(self, payload, preferences):
        # Parsing is read-only; a shallow view preserves the established
        # conversion/timing machinery without changing the shared store getter.
        parser = copy.copy(self.lyrics_store)
        parser.settings_getter = lambda key, default=None: preferences.get(key, default)
        return parser.parse_online_lyrics(
            raw_lrc=payload.raw_lrc, raw_yrc=payload.raw_yrc, translation=payload.translation,
            romanization=payload.romanization, source=payload.source or payload.source_id)

    def _position_changed(self, milliseconds):
        recovery = self._stream_recovery
        if recovery is not None and (recovery.phase != "progress"
                or milliseconds < recovery.position_ms - self.STREAM_RECOVERY_SEEK_TOLERANCE_MS):
            return
        if self.track is not None:
            seconds = max(0.0, milliseconds / 1000.0)
            self.track = replace(self.track, position_seconds=seconds, position_observed_seconds=seconds,
                                 position_sampled_at=time.monotonic())
            self.changed.emit()

    def _duration_changed(self, milliseconds):
        if self.track is not None and self._stream_epoch == self._epoch:
            self._decoded_duration_ms = max(0, milliseconds)
        if self.track is not None and milliseconds > 0:
            self.track = replace(self.track, duration_seconds=milliseconds / 1000.0)

    def _state_changed(self, state):
        if (self._stream_fault is not None and state in {
                QMediaPlayer.PlaybackState.PausedState, QMediaPlayer.PlaybackState.PlayingState}):
            epoch, reason, _position, _observed_at = self._stream_fault
            self._stream_fault = (epoch, reason, self._qt_position_ms(), time.monotonic())
        if self.track is not None:
            self.track = replace(self.track, is_playing=(state == QMediaPlayer.PlaybackState.PlayingState
                and self.status != "playback_failed" and self._stream_recovery is None))
            if (state == QMediaPlayer.PlaybackState.PlayingState
                    and self._stream_epoch == self._epoch and not self._closed and self._stream_recovery is None):
                self._set_status("trial" if self._stream_trial else "playing")
            self.changed.emit()

    def _media_status(self, status):
        if self._closed or self.track is None or self._stream_epoch != self._epoch:
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._stream_recovery is not None:
                self._finish_stream_failure(self._stream_recovery.reason)
            else:
                self._end_current_stream()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            if self._stream_recovery is not None:
                self._finish_stream_failure(self._stream_recovery.reason)
            elif self._stream_fault is not None:
                self._finish_stream_failure(self._stream_fault[1])

    def _end_current_stream(self):
        self._clear_stream_fault()
        if self.playback_mode == "repeat_one":
            self._load_song()
        else:
            self._advance_queue()

    @staticmethod
    def _request_failure(error):
        return error if error in {"auth_required", "credential", "timeout", "network", "unsupported", "invalid_response", "response"} else "unavailable"

    def _error(self, code, *_args):
        if (code == QMediaPlayer.Error.NoError or self._closed or self.track is None
                or self._stream_epoch != self._epoch):
            return
        reason = {
            QMediaPlayer.Error.NetworkError: "network",
            QMediaPlayer.Error.FormatError: "format",
            QMediaPlayer.Error.AccessDeniedError: "access_denied",
            QMediaPlayer.Error.ResourceError: "resource",
        }.get(code, "backend")
        if self._stream_recovery is not None:
            self._finish_stream_failure(reason)
            return
        if (code in {QMediaPlayer.Error.ResourceError, QMediaPlayer.Error.NetworkError}
                and (self._stream_fault is not None
                     or self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)):
            # FFmpeg can report a failed HTTP tail read while decoded audio is
            # still rendering. Keep its genuine clock and lyrics until that
            # buffer drains; repeated demux errors cannot reset the watchdog.
            if self._stream_fault is None:
                self._stream_fault = (self._epoch, reason, self._qt_position_ms(), time.monotonic())
                self._stream_fault_timer.start()
            self.failure_reason = reason
            return
        self._finish_stream_failure(reason)

    def _qt_position_ms(self):
        position = getattr(self.player, "position", None)
        return max(0, int(position())) if callable(position) else round(self.track.position_seconds * 1000)

    def _clear_stream_fault(self):
        self._stream_fault = None
        if self._stream_recovery is None:
            self._stream_fault_timer.stop()

    def _clear_stream_recovery(self):
        self._stream_recovery = None
        if self._stream_fault is None:
            self._stream_fault_timer.stop()

    def _recover_stream(self, reason, position):
        if (self._stream_recovery_attempted or not self.queue or not 0 <= self.index < len(self.queue)
                or position <= 0 or self._decoded_duration_ms <= position
                or not self.player.isSeekable()):
            return False
        self._stream_recovery_attempted = True
        self._clear_stream_fault()
        self._epoch += 1
        epoch, catalog_epoch = self._epoch, self._catalog_epoch
        self._stream_epoch = 0
        self._decoded_duration_ms = 0
        self._lyrics_generation += 1
        self._lyrics_parse_pending = None
        self._stream_recovery = _StreamRecovery(epoch, position,
            time.monotonic() + self.STREAM_RECOVERY_TIMEOUT_SECONDS, reason)
        recovery = self._stream_recovery
        self.track = replace(self.track, is_playing=False, position_seconds=position / 1000,
            position_observed_seconds=position / 1000, position_sampled_at=time.monotonic())
        self._set_status("recovering_stream")
        if self._stream_recovery is not recovery or self._epoch != epoch:
            return True
        # The changed decoder has a new media token, including for the shared
        # output bridge. Invalidate its old PCM before resetting Qt's source.
        self.changed.emit()
        if self._stream_recovery is not recovery or self._epoch != epoch:
            return True
        self.player.stop()
        if self._stream_recovery is not recovery or self._epoch != epoch:
            return True
        self.player.setSource(QUrl())
        if self._stream_recovery is not recovery or self._epoch != epoch:
            return True
        song = self.queue[self.index]
        accepted = self.tasks.submit(("stream", epoch, catalog_epoch),
            lambda: self.service.stream_url(song.source_id, song.song_id)
            if not self._closed and epoch == self._epoch and catalog_epoch == self._catalog_epoch else None)
        if accepted:
            self._stream_fault_timer.start()
        else:
            self._finish_stream_failure(reason)
        return True

    def _check_stream_recovery(self):
        recovery = self._stream_recovery
        if recovery is None:
            return
        if self._closed or self.track is None or recovery.epoch != self._epoch:
            self._clear_stream_recovery()
            return
        if time.monotonic() >= recovery.deadline:
            self._finish_stream_failure(recovery.reason)
            return
        if recovery.phase == "authorize":
            return
        if recovery.phase == "load":
            if (self.player.mediaStatus() not in {QMediaPlayer.MediaStatus.LoadedMedia,
                    QMediaPlayer.MediaStatus.BufferingMedia, QMediaPlayer.MediaStatus.BufferedMedia}
                    or not self.player.isSeekable() or self._decoded_duration_ms <= 0):
                return
            if recovery.position_ms >= self._decoded_duration_ms:
                self._finish_stream_failure(recovery.reason)
                return
            recovery.phase = "seek"
            self.player.setPosition(recovery.position_ms)
            if self._stream_recovery is not recovery or self._stream_epoch != recovery.epoch:
                return
        position = self._qt_position_ms()
        if recovery.phase == "seek":
            if abs(position - recovery.position_ms) > self.STREAM_RECOVERY_SEEK_TOLERANCE_MS:
                return
            recovery.progress_position_ms = position
            self.track = replace(self.track, position_seconds=position / 1000,
                position_observed_seconds=position / 1000, position_sampled_at=time.monotonic())
            self.changed.emit()
            if self._stream_recovery is not recovery or self._stream_epoch != recovery.epoch:
                return
            if not recovery.resume:
                self._clear_stream_recovery()
                self.failure_reason = ""
                self._set_status("trial" if self._stream_trial else "playing")
                self.changed.emit()
                return
            recovery.phase = "progress"
            self.player.play()
        elif recovery.phase == "progress" and (not recovery.resume or position > recovery.progress_position_ms + 20):
            self._clear_stream_recovery()
            self.failure_reason = ""
            self.track = replace(self.track,
                is_playing=self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
                position_seconds=position / 1000, position_observed_seconds=position / 1000,
                position_sampled_at=time.monotonic())
            self._set_status("trial" if self._stream_trial else "playing")
            self.changed.emit()

    def _check_stream_fault(self):
        if self._stream_recovery is not None:
            self._check_stream_recovery()
            return
        fault = self._stream_fault
        if fault is None:
            return
        epoch, reason, previous_position, last_progress_at = fault
        if self._closed or self.track is None or epoch != self._epoch or self._stream_epoch != epoch:
            self._clear_stream_fault()
            return
        now, position = time.monotonic(), self._qt_position_ms()
        if (self.player.playbackState() == QMediaPlayer.PlaybackState.PausedState
                or position > previous_position):
            self._stream_fault = (epoch, reason, position, now)
            return
        if now - last_progress_at < self.STREAM_FAULT_STALL_SECONDS:
            return
        # Only decoder duration plus a drained, observed playback clock can
        # stand in for a missing EndOfMedia. Catalog/lyrics never imply EOF.
        if (position > 0 and self._decoded_duration_ms > 0
                and 0 <= self._decoded_duration_ms - position <= self.STREAM_FAULT_END_TOLERANCE_MS):
            self.player.pause()
            self._end_current_stream()
        else:
            if not self._recover_stream(reason, position):
                self._finish_stream_failure(reason)

    def _finish_stream_failure(self, reason):
        self._clear_stream_recovery()
        self._clear_stream_fault()
        self._stream_epoch = 0
        self._reset_lyrics_payload()
        self.failure_reason = reason
        self.track = replace(self.track, is_playing=False)
        self._set_status("playback_failed")
        self._lyrics_reparse_failed()
        self.player.pause()
        self.changed.emit()

    def send_playback_command(self, command):
        if self.track is None or self._closed:
            return False
        if command not in {"toggle_play_pause", "previous", "next"}:
            return False
        if QThread.currentThread() != self.thread():
            return self._request_control("command", command, self.track.track_id)
        if command == "toggle_play_pause":
            if self._stream_recovery is not None:
                self._stream_recovery.resume = not self._stream_recovery.resume
                if not self._stream_recovery.resume:
                    self.player.pause()
                elif self._stream_recovery.phase == "progress":
                    self.player.play()
                self.changed.emit()
                return True
            if self.status in {"playback_failed", "stream_unavailable"} and self._stream_epoch == 0:
                return self._load_song() if self.queue and 0 <= self.index < len(self.queue) else False
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            else:
                self.player.play()
            return True
        if command in {"previous", "next"}:
            return self._advance_queue(-1 if command == "previous" else 1)
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
        recovery = self._stream_recovery
        if recovery is not None and recovery.phase == "authorize":
            return False
        self._clear_stream_recovery()
        self._clear_stream_fault()
        epoch, stream_epoch = self._epoch, self._stream_epoch
        self.player.setPosition(round(seconds * 1000))
        if recovery is not None:
            if self._closed or self._epoch != epoch or self._stream_epoch != stream_epoch or self.status == "playback_failed":
                return False
            self._set_status("trial" if self._stream_trial else "playing")
            if self._closed or self._epoch != epoch or self._stream_epoch != stream_epoch or self.status == "playback_failed":
                return False
            if recovery.resume:
                self.player.play()
        return True

    def _request_control(self, kind, value, track_id):
        # Existing island command/seek callers run on daemon workers. Qt media
        # objects stay on their owning thread; the worker receives actual success.
        generation = (self._epoch, self._catalog_epoch, self._queue_epoch, self._mode_epoch)
        request = (kind, value, track_id, time.monotonic() + 2.0, threading.Event(), {"generation": generation})
        self._control_requested.emit(request)
        if not request[4].wait(2.0):
            request[5]["cancelled"] = True
            return False
        return bool(request[5].get("succeeded", False))

    @Slot(object)
    def _apply_control_request(self, request):
        kind, value, track_id, deadline, completed, result = request
        try:
            if (self._closed or result.get("cancelled") or time.monotonic() >= deadline
                    or result.get("generation") != (self._epoch, self._catalog_epoch, self._queue_epoch, self._mode_epoch)):
                return
            if kind == "mode":
                result["succeeded"] = self.set_playback_mode(value)
            elif self.track is not None and self.track.track_id == track_id:
                if kind == "lyrics_refresh":
                    result["succeeded"] = self.refresh_lyrics()
                else:
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
        self.prefetch_tasks.close()

    def stop(self):
        """Invalidate queued I/O before disabling or changing playback modes."""
        self._epoch += 1
        self._reset_lyrics_payload()
        self._catalog_epoch += 1
        self._stream_epoch = 0
        self._stream_trial = False
        self._clear_stream_recovery()
        self._clear_stream_fault()
        self._decoded_duration_ms = 0
        self.failure_reason = ""
        self._set_fm_source("")
        self._reset_queue_cache()
        self.track = None
        self.queue = ()
        self.index = -1
        self._reset_playback_order()
        self.player.stop()
        self.player.setSource(QUrl())
        self._set_status("idle")
        self.changed.emit()
