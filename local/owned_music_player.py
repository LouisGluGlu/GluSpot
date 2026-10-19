"""Autonomous Qt playback with a source clock, never external-player scraping.

The post-login product surface can call play_queue/play_daily/play_personal_fm.
Fetching streams and lyrics is asynchronous; stale replies cannot replace a song.
"""
from __future__ import annotations

import time
import threading
import random
import copy
import re
from collections import deque
from dataclasses import dataclass, replace
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from api.models import AlbumQueueEntry, ColorToken, IslandVisualData, TrackData
from api.lyric_timeline import has_sufficient_word_timing
from api.online_music import CatalogPage, LyricPayload, OnlineMusicError, OnlineSong, StreamInfo
from api.online_music_artwork import ONLINE_ARTWORK_MAX_BYTES, fetch_online_artwork, online_artwork_colors, online_artwork_min_side, online_artwork_thumbnail_bytes
from api.owned_stream_cache import OwnedStreamCache, PreparedAudio, StreamDownloadError
from local.music_tasks import MusicTasks

_DEFAULT_STREAM_CACHE = object()


@dataclass
class _StreamRecovery:
    epoch: int
    position_ms: int
    deadline: float
    reason: str
    phase: str = "authorize"
    resume: bool = True
    progress_position_ms: int = 0


@dataclass
class _CatalogStream:
    epoch: int
    source: QUrl
    decoded_duration_ms: int
    ended: bool = False
    error: QMediaPlayer.Error | None = None
    playlist: _PlaylistContinuation | None = None


@dataclass(frozen=True)
class _PlaylistStart:
    source_id: str
    playlist_id: str
    page: CatalogPage
    offset: int
    pages: int


@dataclass
class _PlaylistContinuation:
    source_id: str
    playlist_id: str
    catalog_epoch: int
    queue_epoch: int
    next_offset: int
    pages: int
    request: tuple | None = None
    retries: int = 0


class OwnedMusicPlayer(QObject):
    ARTWORK_NEIGHBORS = 3
    ARTWORK_VISIBLE_LIMIT = 24
    ARTWORK_VISIBLE_BUFFER = 2
    ARTWORK_THUMBNAIL_SIDE = 320
    ARTWORK_ORIGINAL_SIDE = 2560
    PLAYLIST_MAX_PAGES = 100
    PLAYLIST_TRANSIENT_RETRIES = 2
    PLAYLIST_RETRY_SECONDS = .75
    ARTWORK_CACHE_MAX_BYTES = 32 * 1024 * 1024
    PLAYBACK_MODES = frozenset({"sequential", "shuffle", "repeat_one"})
    SHUFFLE_HISTORY_LIMIT = 256
    STREAM_FAULT_STALL_SECONDS = 1.0
    STREAM_FAULT_END_TOLERANCE_MS = 500
    STREAM_RECOVERY_TIMEOUT_SECONDS = 12.0
    STREAM_RECOVERY_SEEK_TOLERANCE_MS = 250
    DJ_PREFETCH_FILES = 2
    DJ_CATALOG_TRANSIENT_RETRIES = 2
    ARTWORK_TRANSIENT_RETRIES = 2
    ARTWORK_RETRY_SECONDS = .75
    AUDIO_PREPARATION_BUSY_SECONDS = 3.0
    changed = Signal()
    queueArtworkChanged = Signal()
    statusChanged = Signal(str)
    _control_requested = Signal(object)

    def __init__(self, service, lyrics_store, parent=None, *, player=None, audio=None, dj_player=None, dj_audio=None, tasks=None, artwork_tasks=None, prefetch_tasks=None,
            stream_cache=_DEFAULT_STREAM_CACHE, download_tasks=None):
        super().__init__(parent)
        self.service = service
        self.lyrics_store = lyrics_store
        self.audio = audio or QAudioOutput(self)
        self.player = player or QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.dj_audio = dj_audio or QAudioOutput(self)
        self.dj_player = dj_player or QMediaPlayer(self)
        self.dj_player.setAudioOutput(self.dj_audio)
        self._dj_generation = 0
        self._dj_overlay_voices = ()
        self._dj_overlay_position = 0
        self._dj_source_ready = False
        self._dj_prepared_audio = None
        self._dj_overlay_retry = None
        self._dj_overlay_retry_deadline = 0.0
        self._dj_pending_preview = None
        self._dj_overlay_is_preview = False
        self._dj_preview_music_running = False
        self._dj_mix_generation = 0
        self._dj_music_mix_start_seconds = None
        self._dj_preview_voice = None
        self._dj_preview_guard_timer = QTimer(self)
        self._dj_preview_guard_timer.setInterval(100)
        self._dj_preview_guard_timer.timeout.connect(self._guard_dj_preview)
        self._dj_music_gain = None
        self._dj_changing_gain = False
        self._dj_music_start_seconds = 0.0
        self._dj_music_end_seconds = 0.0
        self._dj_pcm_clock_floor = 0.0
        self._dj_restore_start_seconds = None
        self._dj_restore_epoch = 0
        self._dj_gain_restore_history = deque(maxlen=32)
        self._dj_restore_timer = QTimer(self)
        self._dj_restore_timer.setInterval(16)
        self._dj_restore_timer.timeout.connect(self._restore_dj_music_gain)
        self.dj_player.playbackStateChanged.connect(self._dj_state_changed)
        self.dj_player.mediaStatusChanged.connect(self._dj_media_status)
        self.dj_player.errorOccurred.connect(self._dj_error)
        if hasattr(self.audio, "volumeChanged"):
            self.audio.volumeChanged.connect(self._dj_music_volume_changed)
        self.tasks = tasks or MusicTasks(self)
        self.tasks.completed.connect(self._completed)
        self._control_requested.connect(self._apply_control_request, Qt.ConnectionType.QueuedConnection)
        # Image downloads/decoding cannot occupy the stream/lyric workers.
        self.artwork_tasks = artwork_tasks or MusicTasks(self)
        self.artwork_tasks.completed.connect(self._completed)
        self.prefetch_tasks = prefetch_tasks or MusicTasks(self)
        self.prefetch_tasks.completed.connect(self._completed)
        self.stream_cache = OwnedStreamCache() if stream_cache is _DEFAULT_STREAM_CACHE else stream_cache
        self.download_tasks = (download_tasks or MusicTasks(self)) if self.stream_cache is not None else None
        if self.download_tasks is not None:
            self.download_tasks.completed.connect(self._completed)
        self._prepared_audio = None
        self._prefetched_audio = None
        self._audio_prefetch_request = None
        self._audio_prefetch_generation = 0
        self._dj_prefetched_audio = {}
        self._dj_prefetch_requests = {}
        self._dj_prefetch_failed = set()
        self._dj_prefetch_serial = 0
        self._dj_prefetch_timer = QTimer(self)
        self._dj_prefetch_timer.setSingleShot(True)
        self._dj_prefetch_timer.setInterval(150)
        self._dj_prefetch_timer.timeout.connect(self._retry_dj_prefetch)
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
        self._catalog_pending_epoch = 0
        self._catalog_pending_kind = ""
        self._catalog_pending_source = ""
        self._playlist_continuation = None
        self._playlist_request_serial = 0
        self._playlist_waiting_next = False
        self._playlist_resume = True
        self._playlist_retry_timer = QTimer(self)
        self._playlist_retry_timer.setSingleShot(True)
        self._playlist_retry_timer.timeout.connect(self._submit_playlist_page)
        self._catalog_stream = None
        self._fm_source = ""
        self._fm_kind = "fm"
        self.personal_dj_enabled = False
        self.personal_dj_voice_key = ""
        self._fm_generation = 0
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
        self._lyrics_enrichment_pending = None
        self._lyrics_enrichment_attempted = False
        self._lyrics_enrichment_source = None
        self.playback_mode = "sequential"
        self._normal_playback_mode = "sequential"
        self._mode_epoch = 0
        self._random = random.Random()
        self._shuffle_history = []
        self._shuffle_cursor = -1
        self._shuffle_bag = deque()
        self._shuffle_pending = deque()
        self._play_next_indices = deque()
        self._queue_view_source = None
        self._queue_view_epoch = -1
        self._queue_view_songs = ()
        self._queue_view_indices = ()
        self._queue_view_art_keys = frozenset()
        self._queue_view_serial = 0
        self._art_cache = {}
        self._art_sizes = {}
        self._art_cache_targets = {}
        self._art_window = {}
        self._art_distances = {}
        self._art_attempted = set()
        self._art_failed = set()
        self._art_retry_counts = {}
        self._art_retry_at = {}
        self._art_retry_timer = QTimer(self)
        self._art_retry_timer.setSingleShot(True)
        self._art_retry_timer.timeout.connect(self._pump_artwork)
        self._art_running = {}
        self._art_request = 0
        self._art_targets = {}
        self._art_request_targets = {}
        self._art_visible_hint = None
        self._art_visible_keys = ()
        self._art_buffer_keys = ()
        self._art_hint_songs = {}
        self._art_prefetch_all = False
        self._art_sequential_keys = ()
        self._art_sequential_pending = deque()
        self._fm_prefetch_pending = False
        self._fm_prefetch_attempt = None
        self._fm_prefetch_request = None
        self._fm_prefetch_serial = 0
        self._fm_prefetch_retry_attempt = None
        self._fm_prefetch_retry_count = 0
        self._fm_advance_pending = False
        self._fm_skip_voice_pending = False
        self._autoplay_after_load = True
        self._output_preparing = False
        self._output_prepare_epoch = 0
        self._output_prepare_resume = False
        self._dj_state = "idle"
        self._dj_next_title = ""
        self._dj_token = 0
        self._dj_identity = None
        self._dj_active_epoch = 0
        self._dj_failed_until = 0.0
        self._dj_failure = None
        self._dj_completion_serial = 0
        self._dj_completion = None
        self._dj_intro_failure = None
        self._fm_resume = True
        self._dj_next_lyrics = None
        self._ready_song_lyrics = None
        self._closed = False

    def _set_status(self, value):
        self.status = value
        self.statusChanged.emit(value)

    def set_output_preparing(self, preparing):
        """Hold decoder time while a selected synchronized route authenticates.

        The output bridge calls this only on our Qt thread. Keeping the actual
        decoder paused preserves the introduction; muting alone loses its first
        seconds before the native PCM input is ready.
        """
        preparing = bool(preparing)
        if preparing:
            if not self._output_preparing or self._output_prepare_epoch != self._epoch:
                self._output_prepare_resume = (self._stream_recovery.resume if self._stream_recovery is not None
                    else self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                    or self._autoplay_after_load and self._stream_epoch != self._epoch)
            self._output_preparing = True
            self._output_prepare_epoch = self._epoch
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            if self.dj_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.dj_player.pause()
            return
        if not self._output_preparing:
            return
        resume = self._output_prepare_resume
        self._output_preparing = False
        self._output_prepare_resume = False
        if (resume and not self._closed and self.track is not None
                and (self._stream_recovery is None or self._stream_recovery.phase == "progress")
                and (self._stream_epoch == self._epoch or self._catalog_stream_is_current())):
            self._play_music()
        self._try_start_dj_overlay()

    def _play_music(self):
        if self._output_preparing:
            self._output_prepare_resume = True
            return
        self.player.play()

    def play_queue(self, songs, index=0):
        return self._replace_queue(songs, index)

    def _refresh_queue_view(self):
        # Queue storage is immutable. Cache the ordinary-song view by object
        # identity so paint/tick consumers need neither I/O nor repeated copies.
        # The serial survives stop/replacement, fencing stale positional clicks.
        if self._queue_view_source is not self.queue or self._queue_view_epoch != self._queue_epoch:
            self._queue_view_source = self.queue
            self._queue_view_epoch = self._queue_epoch
            self._queue_view_serial += 1
            self._queue_view_indices = tuple(i for i, song in enumerate(self.queue)
                if song.media_kind != "dj_voice")
            self._queue_view_songs = tuple(self.queue[i] for i in self._queue_view_indices)
            self._queue_view_art_keys = frozenset(self._art_key(song) for song in self._queue_view_songs)

    def playback_queue_snapshot(self):
        """Return immutable ordinary songs in queue order, with no DJ speech."""
        if self._closed or self.track is None:
            return ()
        self._refresh_queue_view()
        return self._queue_view_songs

    def playback_queue_token(self):
        """Opaque queue-version token for guarding a later positional click."""
        if self._closed or self.track is None:
            return None
        self._refresh_queue_view()
        return self._queue_view_serial

    def playback_origin_token(self):
        """Keep a library origin through song changes and appended queue pages.

        Unlike the positional-click token, this only changes on replacement
        or stop, so adding a next song cannot restart its parent playlist.
        """
        return self._queue_epoch if not self._closed and self.track is not None else None

    def playback_queue_artwork(self, song):
        """Return only an already cached queue thumbnail, without I/O or work."""
        if (self._closed or self.track is None or not isinstance(song, OnlineSong)
                or song.media_kind == "dj_voice"):
            return b""
        key = self._art_key(song)
        # Completed covers survive scrolling within this same queue, subject
        # to the shared byte cap. Reads never acquire arbitrary supplied URLs.
        self._refresh_queue_view()
        if key not in self._queue_view_art_keys:
            return b""
        cached = self._art_cache.get(key)
        return cached[0] if cached else b""

    def prefetch_queue_artwork(self, indices, expected_queue_token=None):
        """Hint at most 24 visible ordinary rows; never fetch in the getter.

        The two-row buffer on either side adds at most four entries. Existing
        current/next artwork has priority, and all work shares the two artwork
        workers, byte cap and bounded transient retry policy.
        """
        if (self._closed or self.track is None or not isinstance(indices, (tuple, list))
                or len(indices) > self.ARTWORK_VISIBLE_LIMIT
                or any(not isinstance(index, int) or isinstance(index, bool) for index in indices)
                or expected_queue_token is not None and (
                    not isinstance(expected_queue_token, int) or isinstance(expected_queue_token, bool))):
            return False
        indices = tuple(sorted(set(indices)))
        if QThread.currentThread() != self.thread():
            return self._request_control("queue_artwork", (indices, expected_queue_token), self.track.track_id)
        self._refresh_queue_view()
        if (expected_queue_token is not None and expected_queue_token != self._queue_view_serial
                or any(not 0 <= index < len(self._queue_view_indices) for index in indices)):
            return False
        hint = self._queue_view_serial, indices
        if hint == self._art_visible_hint:
            return True
        self._art_visible_hint = hint
        self._art_visible_keys = tuple(self._art_key(self._queue_view_songs[index]) for index in indices)
        buffers = (() if not indices else (
            *range(max(0, indices[0] - self.ARTWORK_VISIBLE_BUFFER), indices[0]),
            *range(indices[-1] + 1, min(len(self._queue_view_songs), indices[-1] + 1 + self.ARTWORK_VISIBLE_BUFFER)),
        ))
        self._art_buffer_keys = tuple(self._art_key(self._queue_view_songs[index]) for index in buffers)
        self._art_hint_songs = {self._art_key(self._queue_view_songs[index]): self._queue_view_songs[index]
                               for index in (*indices, *buffers)}
        self._refresh_artwork_window()
        self._pump_artwork()
        return True

    def playback_queue_index(self):
        """Current row in playback_queue_snapshot, or -1 when unavailable."""
        if self._closed or self.track is None:
            return -1
        self._refresh_queue_view()
        target = self._introduced_song_index() if self._is_voice_segment() else self.index
        try:
            return self._queue_view_indices.index(target)
        except ValueError:
            return -1

    def select_queue_index(self, index, expected_queue_token=None):
        """Select a displayed ordinary-song row without replacing its queue.

        A genuine associated DJ introduction is retained. Choosing the current
        row leaves its decoder and clock intact; a different row resumes music.
        """
        if (self._closed or self.track is None or not isinstance(index, int) or isinstance(index, bool)):
            return False
        if QThread.currentThread() != self.thread():
            return self._request_control("queue_index", (index, expected_queue_token), self.track.track_id)
        self._refresh_queue_view()
        if (expected_queue_token is not None and expected_queue_token != self._queue_view_serial
                or not 0 <= index < len(self._queue_view_indices)):
            return False
        target = self._queue_view_indices[index]
        current = self._introduced_song_index() if self._is_voice_segment() else self.index
        if target == current and not self._catalog_pending_epoch and not self._fm_advance_pending:
            return True
        if self._uses_shuffle_order():
            # This deliberate song choice joins history, preserving the planned
            # future and enabling Previous to return to the sounding song.
            self._shuffle_cursor += 1
            self._shuffle_history.insert(self._shuffle_cursor, target)
            self._shuffle_pending = deque(i for i in self._shuffle_pending if i != target)
            self._shuffle_bag = deque(i for i in self._shuffle_bag if i != target)
            self._trim_shuffle_history()
        self.index = self._intro_start_for_song(target)
        return self._load_song()

    def enqueue_next(self, song: OnlineSong) -> bool:
        """Retain the sounding song and queue, playing this addition next once.

        The newest deliberate addition has priority over repeat-one and random
        order. It has no fabricated DJ speech; genuine following introductions
        and their song associations remain together. Each source still resolves
        its own authorized stream through the existing service.
        """
        if (self._closed or not isinstance(song, OnlineSong) or song.media_kind != "song"
                or not isinstance(song.source_id, str) or not song.source_id.strip()
                or not isinstance(song.song_id, str) or not song.song_id.strip()
                or not isinstance(song.title, str) or not song.title.strip()
                or self.track is None or not 0 <= self.index < len(self.queue)
                or self._catalog_pending_epoch or self._fm_advance_pending):
            return False
        if QThread.currentThread() != self.thread():
            return self._request_control("enqueue_next", song, self.track.track_id)
        # A legacy voice position owns the song it introduces, even though its
        # metadata is already visible. Never insert between that pair.
        introduced = self._introduced_song_index() if self._is_voice_segment() else None
        insertion = (introduced if introduced is not None else self.index) + 1
        shifted = lambda i: i + 1 if i >= insertion else i
        self._shuffle_history = [shifted(i) for i in self._shuffle_history]
        self._shuffle_pending = deque(shifted(i) for i in self._shuffle_pending)
        self._shuffle_bag = deque(shifted(i) for i in self._shuffle_bag)
        self._play_next_indices = deque(shifted(i) for i in self._play_next_indices)
        self.queue = self.queue[:insertion] + (song,) + self.queue[insertion:]
        self._play_next_indices.appendleft(insertion)
        self._mode_epoch += 1
        self._clear_audio_prefetch()
        self._clear_dj_audio_prefetch(keep_introduction=True)
        generation = (self._epoch, self._catalog_epoch, self._queue_epoch, self._mode_epoch)
        self._refresh_artwork_window()
        self._apply_cached_artwork()
        if self._closed or generation != (self._epoch, self._catalog_epoch, self._queue_epoch, self._mode_epoch):
            return True
        self._pump_artwork()
        self._prefetch_fm()
        self._prefetch_audio()
        if self._playlist_waiting_next and self._play_next_indices:
            return self._advance_queue(autoplay=self._playlist_resume)
        return True

    def _replace_queue(self, songs, index=0, *, fm_source="", fm_kind="fm", prefetch_all_artwork=False):
        if self._closed:
            return False
        songs = tuple(songs)
        if not songs or not all(isinstance(song, OnlineSong) for song in songs):
            return False
        if not 0 <= index < len(songs):
            return False
        self._clear_dj_presentation()
        self._catalog_epoch += 1
        self._set_fm_source(fm_source, fm_kind)
        self._reset_queue_cache()
        self._art_prefetch_all = bool(prefetch_all_artwork)
        self.queue = songs
        self.index = index
        self._reset_playback_order()
        return self._load_song()

    def is_personal_fm(self):
        return bool(self._fm_source)

    def personal_dj_available(self):
        return self._fm_source == "netease"

    def is_personal_dj_speaking(self):
        if self._dj_overlay_is_preview:
            return bool(self._dj_overlay_voices and self._dj_source_ready and not self._closed
                and self.dj_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
        retained = self._catalog_stream if self._catalog_stream_is_current() else None
        return bool(self.personal_dj_enabled and self._fm_source == "netease"
            and self._dj_overlay_voices and self._dj_source_ready
            and self.track is not None and not self._closed
            and (retained is None or not retained.ended and retained.error is None)
            and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            and self.dj_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)

    def personal_dj_audio_token(self):
        return (self._epoch, self._dj_generation,
            self._dj_overlay_voices[self._dj_overlay_position].song_id if self._dj_overlay_voices else "",
            self._dj_mix_generation)

    def personal_dj_music_gain(self):
        if self._dj_restore_start_seconds is not None:
            return self._dj_restore_factor(self._qt_position_ms() / 1000.0)
        return .2 if self._dj_music_gain is not None else 1.0

    def personal_dj_music_gain_envelopes(self):
        return tuple((start, end, cutoff) for epoch, start, end, cutoff in self._dj_gain_restore_history
            if epoch == self._epoch)

    def _dj_restore_factor(self, seconds):
        progress = max(0.0, min(1.0, seconds - self._dj_restore_start_seconds))
        return .2 + .8 * progress * progress * (3.0 - 2.0 * progress)

    def _cancel_dj_gain_restore(self):
        if self._dj_restore_start_seconds is not None and self._dj_gain_restore_history:
            epoch, start, end, cutoff = self._dj_gain_restore_history[-1]
            self._dj_gain_restore_history[-1] = (epoch, start, end,
                min(cutoff, max(start, self._qt_position_ms() / 1000.0, self._dj_pcm_clock_floor)))
        self._dj_restore_start_seconds = None
        self._dj_restore_timer.stop()

    def _restore_dj_music_gain(self):
        if self._dj_restore_start_seconds is None:
            return
        if self._closed or self._dj_restore_epoch != self._epoch:
            self._set_music_duck(False)
            return
        seconds = self._qt_position_ms() / 1000.0
        factor = self._dj_restore_factor(seconds)
        self._dj_changing_gain = True
        try:
            self.audio.setVolume(self._dj_music_gain * factor)
        finally:
            self._dj_changing_gain = False
        if seconds >= self._dj_restore_start_seconds + 1.0:
            self._dj_restore_start_seconds = None
            self._dj_restore_timer.stop()
            self._dj_music_gain = None

    def _set_music_duck(self, enabled, *, ramp=False):
        volume = getattr(self.audio, "volume", None)
        setter = getattr(self.audio, "setVolume", None)
        if not callable(volume) or not callable(setter):
            return
        if enabled and self._dj_music_gain is None:
            self._dj_music_gain = volume()
        original = self._dj_music_gain
        if original is None:
            return
        if not enabled and ramp and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            start = max(self._qt_position_ms() / 1000.0, self._dj_music_end_seconds)
            self._dj_restore_start_seconds = start
            self._dj_restore_epoch = self._epoch
            self._dj_gain_restore_history.append((self._epoch, start, start + 1.0, start + 1.0))
            self._dj_restore_timer.start()
            return
        self._cancel_dj_gain_restore()
        if not enabled:
            self._dj_music_gain = None
        self._dj_changing_gain = True
        try:
            setter(original * .2 if enabled else original)
        finally:
            self._dj_changing_gain = False

    def _dj_music_volume_changed(self, value):
        if self._dj_music_gain is not None and not self._dj_changing_gain:
            # A deliberate local gain change becomes the new baseline;
            # endpoint/system percentages are managed elsewhere.
            self._dj_music_gain = value
            if self._dj_restore_start_seconds is not None:
                self._restore_dj_music_gain()
            else:
                self._set_music_duck(True)

    def _stop_dj_overlay(self, *, restore_ramp=False, keep_preview=False, keep_completion=False):
        self._dj_overlay_retry = None
        self._dj_overlay_retry_deadline = 0.0
        self._dj_intro_failure = None
        if not keep_completion:
            self._dj_completion = None
        if self._dj_overlay_voices and self._dj_source_ready:
            position = getattr(self.dj_player, "position", None)
            voice_ms = position() if callable(position) else max(0, int(position or 0))
            self._dj_music_end_seconds = self._dj_music_start_seconds + voice_ms / 1000.0
        self._dj_generation += 1
        self._dj_overlay_voices = ()
        self._dj_overlay_is_preview = False
        self._dj_preview_music_running = False
        self._dj_music_mix_start_seconds = None
        self._dj_preview_voice = None
        self._dj_preview_guard_timer.stop()
        if not keep_preview:
            self._dj_pending_preview = None
        self._dj_source_ready = False
        self.dj_player.stop()
        self.dj_player.setSource(QUrl())
        if self._dj_prepared_audio is not None:
            self.stream_cache.release(self._dj_prepared_audio)
            self._dj_prepared_audio = None
        self._set_music_duck(False, ramp=restore_ramp)

    def _prepare_dj_overlay(self, voices, prepared=None):
        self._dj_completion = None
        self._dj_intro_failure = None
        self._dj_overlay_voices = tuple(voices)
        self._dj_overlay_position = 0
        self._dj_next_title = self.track.title if self.track is not None else ""
        self._dj_state = "loading"
        self._dj_token += 1
        self._dj_active_epoch = self._epoch
        self._dj_failed_until = 0.0
        self._dj_failure = None
        self._request_dj_overlay_audio(prepared)

    def _request_dj_overlay_audio(self, prepared=None):
        voice = self._dj_overlay_voices[self._dj_overlay_position]
        token = ("dj_stream", self._epoch, self._catalog_epoch, self._dj_generation, voice.song_id)
        if prepared is None and not self._dj_overlay_is_preview:
            prepared = self._take_dj_prefetched_audio(voice)
        if prepared is not None:
            self._dj_overlay_completed(("dj_audio", *token[1:]), prepared, None)
            return
        if not self._dj_overlay_is_preview and voice in self._dj_prefetch_requests:
            # The already authorized future introduction now owns this
            # foreground position. Keep its complete-file download alive.
            return
        if not self.tasks.submit(token, lambda: self._resolve_stream(voice)
                if self._dj_overlay_request_current(token) else None):
            self._schedule_dj_overlay_retry(token)

    def _schedule_dj_overlay_retry(self, token, stream=None):
        """Retry transient foreground contention within one bounded budget."""
        if not self._dj_overlay_request_current(token):
            return
        now = time.monotonic()
        if not self._dj_overlay_retry_deadline:
            self._dj_overlay_retry_deadline = now + self.AUDIO_PREPARATION_BUSY_SECONDS
        if now >= self._dj_overlay_retry_deadline:
            self._fail_dj_overlay("busy")
            return
        self._dj_overlay_retry = (token, stream)
        self._dj_state = "loading"
        self._dj_prefetch_timer.start()
        self.changed.emit()

    def _submit_dj_overlay_download(self, token, stream):
        deadline = self._dj_overlay_retry_deadline
        if not self.download_tasks.submit(token, lambda: self._prepare_audio(stream,
                cancelled=lambda: not self._dj_overlay_request_current(token),
                busy_deadline=deadline or None)):
            self._schedule_dj_overlay_retry(token, stream)

    def _retry_dj_overlay(self):
        retry, self._dj_overlay_retry = self._dj_overlay_retry, None
        if retry is None:
            return
        token, stream = retry
        if not self._dj_overlay_request_current(token):
            self._dj_overlay_retry_deadline = 0.0
            return
        if time.monotonic() >= self._dj_overlay_retry_deadline:
            self._fail_dj_overlay("busy")
            return
        voice = self._dj_overlay_voices[self._dj_overlay_position]
        if (isinstance(voice.direct_stream, StreamInfo) and voice.direct_stream.expires_at > 0
                and time.time() >= voice.direct_stream.expires_at):
            self._fail_dj_overlay("expired")
            return
        if stream is None:
            self._request_dj_overlay_audio()
        else:
            self._submit_dj_overlay_download(token, stream)

    def _dj_overlay_request_current(self, token):
        return (not self._closed and bool(self._dj_overlay_voices)
            and (not self._dj_overlay_is_preview or self._dj_preview_authorized())
            and token[1] == self._epoch and token[2] == self._catalog_epoch
            and token[3] == self._dj_generation
            and token[4] == self._dj_overlay_voices[self._dj_overlay_position].song_id)

    def _dj_overlay_completed(self, token, result, error):
        prepared = result if isinstance(result, PreparedAudio) else None
        if not self._dj_overlay_request_current(token):
            if prepared is not None:
                self._release_unused_audio(prepared)
            return
        voice = self._dj_overlay_voices[self._dj_overlay_position]
        if error or result is None:
            self._fail_dj_overlay(self._request_failure(error))
            return
        stream = prepared.stream if prepared is not None else result
        if (not isinstance(stream, StreamInfo) or not stream.playable
                or (stream.source_id, stream.song_id) != (voice.source_id, voice.song_id)):
            if prepared is not None:
                self._release_unused_audio(prepared)
            self._fail_dj_overlay("invalid_response")
            return
        if token[0] == "dj_stream" and self.stream_cache is not None:
            audio_token = ("dj_audio", *token[1:])
            self._submit_dj_overlay_download(audio_token, stream)
            return
        if self._dj_source_ready:
            if prepared is not None:
                self._release_unused_audio(prepared)
            return
        self._dj_prepared_audio = prepared
        self._dj_overlay_retry = None
        self._dj_overlay_retry_deadline = 0.0
        self._dj_source_ready = True
        self.dj_player.setSource(QUrl.fromLocalFile(str(prepared.path)) if prepared is not None else QUrl(stream.url))
        if self._dj_overlay_request_current(token):
            self._dj_state = "ready"
            if (not self._dj_overlay_is_preview and (self._stream_epoch == self._epoch
                    or self._catalog_stream_is_current()) and self._autoplay_after_load):
                self._play_music()
            self._try_start_dj_overlay()
            self._prefetch_audio()
            self.changed.emit()

    def _try_start_dj_overlay(self):
        if self._output_preparing:
            return
        if self._dj_overlay_is_preview and self._dj_overlay_voices and self._dj_source_ready and not self._closed:
            if not self._dj_preview_authorized():
                self._stop_dj_overlay()
                self._clear_dj_presentation()
                return
            self._sync_dj_preview_music_state()
            if self.dj_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                self.dj_player.play()
            return
        if (not self._dj_overlay_voices or not self._dj_source_ready or self._closed
                or not self.personal_dj_enabled or self._stream_epoch != self._epoch and not self._catalog_stream_is_current()
                or self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState):
            return
        if self.dj_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            position = getattr(self.dj_player, "position", None)
            voice_ms = position() if callable(position) else max(0, int(position or 0))
            self._dj_music_start_seconds = max((self._qt_position_ms() - voice_ms) / 1000.0,
                self._dj_pcm_clock_floor, self._dj_music_end_seconds if self._dj_overlay_position else 0.0)
            self._set_music_duck(True)
            self.dj_player.play()

    def _sync_dj_preview_music_state(self):
        if not self._dj_overlay_is_preview or not self._dj_source_ready:
            return
        running = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if running == self._dj_preview_music_running:
            if not running:
                self._set_music_duck(False)
            return
        position = getattr(self.dj_player, "position", None)
        voice_seconds = (position() if callable(position) else max(0, int(position or 0))) / 1000.0
        boundary = max(self._qt_position_ms() / 1000.0, self._dj_pcm_clock_floor)
        self._dj_mix_generation += 1
        self._dj_music_end_seconds = boundary
        self._dj_preview_music_running = running
        if running:
            # Speech keeps its own live clock while music can pause/resume.
            # Only its mapping into the retained music PCM clock changes.
            self._dj_music_start_seconds = boundary - voice_seconds
            self._dj_music_mix_start_seconds = boundary
            self._set_music_duck(True)
        else:
            self._dj_music_mix_start_seconds = None
            self._set_music_duck(False)

    def _dj_state_changed(self, state):
        if self._dj_overlay_voices and self._dj_source_ready:
            self._dj_state = "speaking" if self.is_personal_dj_speaking() else "ready"
            self.changed.emit()

    def _dj_media_status(self, status):
        if not self._dj_overlay_voices or not self._dj_source_ready or self._closed:
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            position = getattr(self.dj_player, "position", None)
            voice_ms = position() if callable(position) else max(0, int(position or 0))
            duration = getattr(self.dj_player, "duration", None)
            if callable(duration):
                voice_ms = max(voice_ms, duration())
            self._dj_music_end_seconds = self._dj_music_start_seconds + voice_ms / 1000.0
            self._dj_source_ready = False
            self.dj_player.stop()
            self.dj_player.setSource(QUrl())
            if self._dj_prepared_audio is not None:
                self.stream_cache.release(self._dj_prepared_audio)
                self._dj_prepared_audio = None
            self._dj_generation += 1
            self._dj_overlay_position += 1
            if self._dj_overlay_position < len(self._dj_overlay_voices):
                self._dj_overlay_retry = None
                self._dj_overlay_retry_deadline = 0.0
                self._dj_token += 1
                self._dj_state = "loading"
                self._request_dj_overlay_audio()
            else:
                # An actual final decoder EOF is the sole completion event.
                # Pauses, loading between parts, cancellation and auditions
                # must never masquerade as a finished song introduction.
                completion = (self._epoch, self._catalog_epoch, self._fm_generation,
                    self.track.track_id if self.track is not None else "")
                genuine = (not self._dj_overlay_is_preview and self.personal_dj_enabled
                    and self.personal_dj_available() and self._dj_active_epoch == self._epoch
                    and self._autoplay_after_load and not self._catalog_pending_epoch
                    and bool(completion[-1]) and all(voice.media_kind == "dj_voice"
                        and voice.source_id == self._fm_source
                        and (not voice.introduced_song_id or
                            completion[-1] == f"owned:{voice.source_id}:{voice.introduced_song_id}")
                        for voice in self._dj_overlay_voices))
                self._stop_dj_overlay(restore_ramp=True, keep_preview=True, keep_completion=True)
                self._clear_dj_presentation(keep_completion=True)
                if genuine and completion == self._dj_completion_context():
                    self._dj_completion_serial += 1
                    self._dj_completion = (self._dj_completion_serial, time.monotonic(), completion)
                self._maybe_start_dj_preview()
            self.changed.emit()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._fail_dj_overlay("resource")

    def _dj_error(self, code, *_args):
        if code != QMediaPlayer.Error.NoError and self._dj_overlay_voices and not self._closed:
            self._fail_dj_overlay({QMediaPlayer.Error.NetworkError: "network",
                QMediaPlayer.Error.FormatError: "format", QMediaPlayer.Error.AccessDeniedError: "access_denied"}.get(code, "resource"))

    def _fail_dj_overlay(self, reason):
        title, token = self._dj_next_title, self._dj_token
        preview = self._dj_overlay_is_preview
        context = self._dj_completion_context()
        genuine = (not preview and bool(self._dj_overlay_voices) and self.personal_dj_enabled
            and self.personal_dj_available() and self._dj_active_epoch == self._epoch)
        self._stop_dj_overlay()
        self._dj_state = "failed"
        self._dj_next_title = title
        self._dj_token = token
        self._dj_failed_until = time.monotonic() + 2.0
        self._dj_failure = (title, token)
        if genuine and context == self._dj_completion_context():
            self._dj_intro_failure = (context, self._dj_failed_until)
        # Speech failure cannot stop a healthy music decoder or erase lyrics.
        if not preview and self._stream_epoch == self._epoch and self._autoplay_after_load:
            self._play_music()
        self.changed.emit()

    def get_personal_dj_presentation(self):
        extra = {"completion_id": 0, "completed_track_id": "", "completed_at": 0.0,
            "is_preview": bool(self._dj_overlay_is_preview), "next_preload_state": "idle",
            "next_preload_title": "", "next_preload_track_id": "",
            "introduction_pending": False, "introduction_failed": False}
        if self._closed or (not self._dj_overlay_is_preview and (not self.personal_dj_enabled or not self.personal_dj_available())):
            return {"state": "idle", "next_title": "", "speaking": False,
                "cover_paused": False, "token": self._dj_token, **extra}
        completion = self._dj_completion
        extra["introduction_pending"] = bool(not self._dj_overlay_is_preview
            and self._dj_overlay_voices and self._dj_active_epoch == self._epoch
            and self.track is not None)
        failure = self._dj_intro_failure
        extra["introduction_failed"] = bool(failure is not None
            and failure[0] == self._dj_completion_context() and time.monotonic() < failure[1])
        if completion is not None and completion[2] == self._dj_completion_context():
            extra.update(completion_id=completion[0], completed_track_id=completion[2][-1],
                completed_at=completion[1])
        preload_state, preload_song = self._next_dj_preload()
        if preload_song is not None:
            extra.update(next_preload_state=preload_state, next_preload_title=preload_song.title,
                next_preload_track_id=f"owned:{preload_song.source_id}:{preload_song.song_id}")
        speaking = self.is_personal_dj_speaking()
        state = "speaking" if speaking else self._dj_state
        if state == "failed" and self._dj_failed_until <= time.monotonic():
            state = "idle"
        if self._dj_failed_until > time.monotonic() and not self._is_voice_segment():
            state = "failed"
            if self._dj_failure is not None:
                return {"state": state, "next_title": self._dj_failure[0],
                    "speaking": False, "cover_paused": False, "token": self._dj_failure[1], **extra}
        return {"state": state,
            "next_title": self._dj_next_title, "speaking": speaking,
            "cover_paused": bool(self._dj_overlay_voices and self._dj_music_gain is not None),
            "token": self._dj_token, **extra}

    def _dj_completion_context(self):
        return (self._epoch, self._catalog_epoch, self._fm_generation,
            self.track.track_id if self.track is not None else "")

    def _next_dj_preload(self):
        """Describe only the actual next song's complete genuine introduction.

        The rolling cache can keep a later introduction warm when the immediate
        next recommendation has no speech. Such a file cannot claim that the
        next song is ready. Partial multipart speech is deliberately loading.
        This reads already verified in-memory leases, with no paint-time I/O.
        """
        if (not self.personal_dj_enabled or self._fm_source != "netease"
                or self._fm_kind != "dj" or self._catalog_pending_epoch):
            return "idle", None
        upcoming = self._upcoming_indices(include_voice=False)
        if not upcoming:
            return "idle", None
        target = upcoming[0]
        start = self._intro_start_for_song(target)
        if start == target:
            return "idle", None
        song, voices = self.queue[target], self.queue[start:target]
        now = time.time()
        if any(voice in self._dj_prefetch_failed or not isinstance(voice.direct_stream, StreamInfo)
                or not voice.direct_stream.playable
                or voice.direct_stream.expires_at > 0 and now >= voice.direct_stream.expires_at
                for voice in voices):
            return "failed", song
        prepared = tuple(self._dj_prefetched_audio.get(voice) for voice in voices)
        if all(isinstance(audio, PreparedAudio) and audio.byte_count > 0
                and audio.stream == voice.direct_stream
                and (audio.stream.expires_at <= 0 or now < audio.stream.expires_at)
                for voice, audio in zip(voices, prepared)):
            return "ready", song
        return "loading", song

    def _set_dj_presentation(self, state, voice_index):
        target = self._introduced_song_index(voice_index)
        if target is None:
            return
        voice = self.queue[voice_index]
        identity = (voice.source_id, voice.song_id)
        new_segment = self._is_voice_segment() and voice_index == self.index and self._dj_active_epoch != self._epoch
        if identity != self._dj_identity or new_segment:
            self._dj_token += 1
            self._dj_identity = identity
        if new_segment:
            self._dj_active_epoch = self._epoch
        self._dj_state = state
        self._dj_next_title = self.queue[target].title
        if state == "failed":
            self._dj_failed_until = time.monotonic() + 2.0
            self._dj_failure = (self._dj_next_title, self._dj_token)
        elif state == "speaking":
            self._dj_failed_until = 0.0
        self.changed.emit()

    def _clear_dj_presentation(self, *, keep_completion=False):
        if not keep_completion:
            self._dj_completion = None
        self._dj_intro_failure = None
        self._dj_state = "idle"
        self._dj_next_title = ""
        self._dj_identity = None
        self._dj_active_epoch = 0
        self._dj_failed_until = 0.0
        self._dj_failure = None

    def _is_voice_segment(self):
        return bool(self.queue and 0 <= self.index < len(self.queue)
            and self.queue[self.index].media_kind == "dj_voice")

    @staticmethod
    def _prioritize_initial_dj_introduction(songs):
        # A genuine DJ batch can start with ordinary, unintroduced songs.
        # Only explicit entry prioritizes the first complete introduction and
        # its song. Keep those leading songs immediately afterwards; ongoing
        # service batches retain their original order in _unique_fm_songs.
        start = next((i for i, song in enumerate(songs) if song.media_kind == "dj_voice"), None)
        if start in (None, 0):
            return songs
        target = next((i for i in range(start + 1, len(songs))
            if songs[i].media_kind != "dj_voice"), None)
        if target is None:
            return songs
        song = songs[target]
        if (song.source_id != "netease" or song.media_kind != "song"
                or any(voice.source_id != song.source_id or voice.introduced_song_id != song.song_id
                    for voice in songs[start:target])):
            return songs
        return songs[start:target + 1] + songs[:start] + songs[target + 1:]

    def _introduced_song_index(self, voice_index=None):
        voice_index = self.index if voice_index is None else voice_index
        if not 0 <= voice_index < len(self.queue) or self.queue[voice_index].media_kind != "dj_voice":
            return None
        target = next((i for i in range(voice_index + 1, len(self.queue))
            if self.queue[i].media_kind != "dj_voice"), None)
        if target is None:
            return None
        voice, song = self.queue[voice_index], self.queue[target]
        if voice.source_id != song.source_id or (voice.introduced_song_id
                and voice.introduced_song_id != song.song_id):
            return None
        return target

    def _intro_start_for_song(self, target):
        if not self.personal_dj_enabled or self._fm_source != "netease":
            return target
        start = target
        while start > 0 and self.queue[start - 1].media_kind == "dj_voice":
            if self._introduced_song_index(start - 1) != target:
                break
            start -= 1
        return start

    def _voice_failed(self, reason):
        if not self._is_voice_segment():
            return False
        target = self._introduced_song_index()
        title, token = self._dj_next_title, self._dj_token
        self._dj_state = "failed"
        self._dj_failed_until = time.monotonic() + 2.0
        self._dj_failure = (title, token)
        self.failure_reason = reason
        generation = (self._epoch, self._catalog_epoch, self._queue_epoch, self._mode_epoch, self.index)
        self.changed.emit()
        if (self._closed or generation !=
                (self._epoch, self._catalog_epoch, self._queue_epoch, self._mode_epoch, self.index)):
            return True
        if target is None:
            # Invalid/orphan speech cannot hold the decoder indefinitely.
            self._stream_epoch = 0
            self.player.stop()
            self._set_status("stream_unavailable")
            return True
        self.index = target
        self._load_song(autoplay=self._autoplay_after_load)
        if self._stream_epoch != self._epoch:
            self._dj_state, self._dj_next_title, self._dj_token = "failed", title, token
            self.changed.emit()
        return True

    @staticmethod
    def _personal_dj_voice_key_valid(key):
        return isinstance(key, str) and (not key or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key) is not None)

    def preview_personal_dj_voice(self, timbre_key):
        """Audition the real catalog sample without changing the music queue.

        Selection and restored preferences remain separate. A genuine current
        introduction finishes first; only the newest explicit audition survives.
        Paused music retains its pause and defers this layer until resume.
        """
        if self._closed or not self._personal_dj_voice_key_valid(timbre_key):
            return False
        if QThread.currentThread() != self.thread():
            return self._request_control("personal_dj_preview", timbre_key, self.track.track_id if self.track else None)
        self._dj_pending_preview = None
        if self._catalog_pending_epoch:
            return False
        cached = getattr(self.service, "personal_dj_voices_cached", None)
        voices = cached("netease") if callable(cached) else ()
        voice = next((v for v in voices if v.timbre_key == timbre_key), None)
        stream = getattr(voice, "preview_stream", None)
        if (not isinstance(stream, StreamInfo) or not stream.playable or stream.quality != "dj_preview"
                or stream.source_id != "netease" or stream.song_id != f"dj-preview:{timbre_key}"):
            return False
        sample = OnlineSong("netease", stream.song_id, voice.name,
            duration_seconds=voice.preview_duration_seconds, media_kind="dj_voice", direct_stream=stream)
        self._dj_pending_preview = (self._epoch, self._queue_epoch, self._catalog_epoch, sample, voice)
        self._dj_preview_guard_timer.start()
        if self._dj_overlay_is_preview:
            self._stop_dj_overlay(keep_preview=True)
        self._maybe_start_dj_preview()
        return True

    def _dj_preview_authorized(self, voice=None):
        voice = self._dj_preview_voice if voice is None else voice
        cached = getattr(self.service, "personal_dj_voices_cached", None)
        # Account cache replacement produces new immutable voice objects.
        # Identity fences logout/relogin even when keys/URLs are identical.
        return callable(cached) and any(entry is voice for entry in cached("netease"))

    def _guard_dj_preview(self):
        pending = self._dj_pending_preview
        if pending is not None and not self._dj_preview_authorized(pending[4]):
            self._dj_pending_preview = None
        if self._dj_overlay_is_preview and not self._dj_preview_authorized():
            self._stop_dj_overlay()
            self._clear_dj_presentation()
            self.changed.emit()
        if self._dj_pending_preview is None and not self._dj_overlay_is_preview:
            self._dj_preview_guard_timer.stop()

    def _maybe_start_dj_preview(self):
        pending = self._dj_pending_preview
        if pending is None or self._dj_overlay_voices or self._closed:
            return
        if pending[:3] != (self._epoch, self._queue_epoch, self._catalog_epoch):
            self._dj_pending_preview = None
            return
        if not self._dj_preview_authorized(pending[4]):
            self._dj_pending_preview = None
            return
        self._dj_pending_preview = None
        self._dj_overlay_is_preview = True
        self._dj_preview_voice = pending[4]
        self._dj_preview_guard_timer.start()
        self._prepare_dj_overlay((pending[3],))
        self._dj_next_title = pending[3].title
        self.changed.emit()

    def restore_personal_dj_voice(self, timbre_key):
        """Restore only the saved preference; the service validates it on fetch.

        Startup has no account catalog yet. This never guesses a name/voice,
        fetches a directory, or changes the official client's preference.
        """
        if self._closed or not self._personal_dj_voice_key_valid(timbre_key):
            return False
        if QThread.currentThread() != self.thread():
            return self._request_control("personal_dj_voice_restore", timbre_key, None)
        return self._set_personal_dj_voice_preference(timbre_key)

    def set_personal_dj_voice(self, timbre_key):
        """Select an actual cached account voice without GUI-thread network I/O."""
        if self._closed or not self._personal_dj_voice_key_valid(timbre_key):
            return False
        if QThread.currentThread() != self.thread():
            return self._request_control("personal_dj_voice", timbre_key, None)
        cached = getattr(self.service, "personal_dj_voices_cached", None)
        voices = cached("netease") if callable(cached) else ()
        if timbre_key and not any(getattr(voice, "timbre_key", None) == timbre_key for voice in voices):
            return False
        return self._set_personal_dj_voice_preference(timbre_key)

    def _set_personal_dj_voice_preference(self, timbre_key):
        if timbre_key == self.personal_dj_voice_key:
            return True
        self.personal_dj_voice_key = timbre_key
        initial_dj = self._catalog_pending_epoch and self._catalog_pending_kind == "dj"
        active_dj = self._fm_source == "netease" and self._fm_kind == "dj" and bool(self.queue)
        if not initial_dj and not active_dj:
            self.changed.emit()
            return True
        self._fm_generation += 1
        self._mode_epoch += 1
        self._clear_dj_audio_prefetch(keep_introduction=True)
        generation = (self._epoch, self._catalog_epoch, self._queue_epoch, self._mode_epoch)
        if initial_dj:
            # Replace a pending first entry with the newly selected request;
            # _catalog retains any healthy music that was already sounding.
            self.play_personal_fm("netease")
            return True
        waiting = self._fm_advance_pending
        explicit_catalog = False
        if self._catalog_pending_epoch:
            if self._catalog_pending_kind not in {"fm_continue", "fm_skip_continue"}:
                # An explicit new library request still owns the pending action.
                # Still discard its retained DJ queue's obsolete future voice;
                # a failed replacement must not bring the old voice back.
                explicit_catalog = True
                waiting = False
            else:
                retained = self._catalog_stream if self._catalog_stream_is_current() else None
                self._catalog_epoch += 1
                self._catalog_pending_epoch = 0
                ended = self._restore_catalog_stream()
                if (retained is not None and retained.error is not None and not ended
                        and retained.epoch == self._epoch and retained.source == self.player.source()):
                    self._error(retained.error)
                if (self._closed or (self._epoch, self._queue_epoch, self._mode_epoch)
                        != (generation[0], generation[2], generation[3])):
                    return True
                waiting = waiting or ended or retained is None
                generation = (self._epoch, self._catalog_epoch, self._queue_epoch, self._mode_epoch)
        self._fm_prefetch_pending = False
        self._fm_prefetch_attempt = None
        self._fm_advance_pending = waiting
        self._fm_skip_voice_pending = False
        # Finish the current genuine introduction and its associated song.
        # Everything after it belongs to the old voice's recommendation batch.
        introduced = self._introduced_song_index() if self._is_voice_segment() else None
        keep_to = introduced if introduced is not None else self.index
        self._retain_fm_queue(keep_to)
        retained_audio = (self._take_prefetched_audio(self.queue[introduced])
                          if introduced is not None else None)
        self._clear_audio_prefetch()
        if retained_audio is not None:
            self._prefetched_audio = retained_audio
        if introduced is None and not self._dj_overlay_voices:
            self._clear_dj_presentation()
            self._dj_next_lyrics = None
        self._refresh_artwork_window()
        self._apply_cached_artwork()
        if self._closed or generation != (self._epoch, self._catalog_epoch, self._queue_epoch, self._mode_epoch):
            return True
        self._pump_artwork()
        if not explicit_catalog:
            self._prefetch_fm()
            self._prefetch_audio()
        return True

    def _fm_fetch_action(self, kind, source):
        fetch = self.service.personal_dj if kind == "dj" else self.service.personal_fm
        key = self.personal_dj_voice_key
        # Empty preferences retain the default service contract and remain
        # compatible with existing traditional/default test providers.
        return (lambda: fetch(source, timbre_key=key)) if kind == "dj" and key else lambda: fetch(source)

    def set_personal_dj_enabled(self, enabled):
        """Change FM's genuine introduction feature without reopening music.

        A separate recommendation generation fences old FM/DJ replies while
        the current song's stream, download lease and lyric epoch remain valid.
        The preference can be restored before a catalog is opened.
        """
        if not isinstance(enabled, bool) or self._closed:
            return False
        if QThread.currentThread() != self.thread():
            return self._request_control("personal_dj", enabled, None)
        if enabled == self.personal_dj_enabled:
            return True
        first_entry = bool(not self.queue and self._catalog_pending_epoch
            and self._catalog_pending_kind in {"fm", "dj"}
            and self._catalog_pending_source == "netease")
        retained = self._catalog_stream if self._catalog_stream_is_current() else None
        waiting = self._fm_advance_pending or (self._stream_epoch == 0
            and self.status in {"loading_catalog", "catalog_unavailable"}
            and (retained is None or retained.ended
                or self._catalog_pending_kind in {"fm_continue", "fm_skip_continue"}))
        if (self._catalog_pending_epoch and self._catalog_pending_kind
                not in {"fm", "dj", "fm_continue", "fm_skip_continue"}):
            # Even an old FM EOF cannot supersede an explicitly selected new
            # playlist/daily catalog while that replacement is still pending.
            waiting = False
        self.personal_dj_enabled = enabled
        if not enabled:
            self._stop_dj_overlay()
        self._clear_dj_presentation()
        self._fm_generation += 1
        self._mode_epoch += 1
        self._clear_dj_audio_prefetch()
        if first_entry:
            # The preference retires the in-flight first batch. With no
            # current queue there is no continuation to fetch a replacement;
            # keep the original entry pending under the new feature choice.
            self.play_personal_fm("netease")
            return True
        ended = False
        if (self._catalog_pending_epoch and self._catalog_pending_kind
                in {"fm", "dj", "fm_continue", "fm_skip_continue"}):
            # The preference invalidates this request's service generation.
            # End its transaction now, rather than waiting for a stale reply
            # that can no longer finish the current source's clock or EOF.
            self._catalog_epoch += 1
            self._catalog_pending_epoch = 0
            ended = self._restore_catalog_stream()
            self._set_status("catalog_unavailable")
            if (retained is not None and retained.error is not None and not ended
                    and retained.epoch == self._epoch and retained.source == self.player.source()):
                self._error(retained.error)
        if self._fm_source != "netease" or not self.queue:
            if ended:
                self._end_current_stream()
            self.changed.emit()
            return True
        self._fm_kind = "dj" if enabled else "fm"
        self._fm_prefetch_pending = False
        self._fm_prefetch_attempt = None
        self._fm_advance_pending = waiting
        self._fm_skip_voice_pending = False
        # A new DJ batch owns its following songs; avoid inserting speech
        # before unrelated recommendations from the former FM batch.
        future_indices = () if enabled else tuple(i for i in range(self.index + 1, len(self.queue))
            if self.queue[i].media_kind != "dj_voice")
        self._retain_fm_queue(self.index, future_indices)
        future = self.queue[self.index + 1:]
        voice_current = self.queue[self.index].media_kind == "dj_voice"
        introduced_audio = (self._take_prefetched_audio(future[0])
            if not enabled and voice_current and future else None)
        self._clear_audio_prefetch()
        if introduced_audio is not None:
            self._prefetched_audio = introduced_audio
        if not enabled and voice_current:
            # Stop speech immediately on explicit disable, and select the song
            # it introduced. Old speech downloads cannot replace that song.
            target = self.index + 1
            if target < len(self.queue):
                resume = self._autoplay_after_load
                self.index = target
                self._load_song(autoplay=resume)
                return True
            self.player.stop()
            self._advance_fm(skip_voice=True)
            return True
        self._refresh_artwork_window()
        self._apply_cached_artwork()
        self._pump_artwork()
        self._prefetch_fm()
        self._prefetch_audio()
        return True

    def available_playback_modes(self):
        return ("sequential", "repeat_one") if self.is_personal_fm() else ("sequential", "shuffle", "repeat_one")

    def _retain_fm_queue(self, keep_to, future_indices=()):
        # Changing DJ voice/preference replaces service recommendations, while
        # songs explicitly added by the listener keep their next-song priority.
        future = tuple(future_indices)
        retained = (*range(keep_to + 1),
            *(i for i in self._play_next_indices if i > keep_to and i not in future), *future)
        mapped = {old: new for new, old in enumerate(retained)}
        self.queue = tuple(self.queue[i] for i in retained)
        self._play_next_indices = deque(mapped[i] for i in self._play_next_indices if i in mapped)

    def _set_fm_source(self, source, kind="fm"):
        if (source, kind if source else "fm") != (self._fm_source, self._fm_kind):
            self._fm_generation += 1
        if source and not self._fm_source:
            self._normal_playback_mode = self.playback_mode
            self.playback_mode = "sequential"
        elif self._fm_source and not source:
            self.playback_mode = self._normal_playback_mode
        self._fm_source = source
        self._fm_kind = kind if source else "fm"

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
        self._clear_audio_prefetch()
        if self.track is not None and self.queue:
            self._refresh_artwork_window()
            self._apply_cached_artwork()
            self._pump_artwork()
            self._prefetch_fm()
            self._prefetch_audio()
        else:
            self.changed.emit()
        return True

    def _reset_playback_order(self):
        self._mode_epoch += 1
        self._shuffle_history = [self.index] if 0 <= self.index < len(self.queue) else []
        self._shuffle_cursor = len(self._shuffle_history) - 1
        self._shuffle_pending.clear()
        bag = ([i for i in self._shuffle_indices()
                if i not in self._play_next_indices and (self.queue[i].source_id, self.queue[i].song_id)
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

    def _upcoming_indices(self, *, include_voice=True):
        if not self.queue:
            return ()
        if self._uses_shuffle_order():
            self._ensure_shuffle_future()
            future = self._shuffle_history[self._shuffle_cursor + 1:] + list(self._shuffle_pending)
        elif self._fm_source:
            future = tuple(i for i in range(self.index + 1, len(self.queue))
                if include_voice or self.queue[i].media_kind != "dj_voice")
        else:
            # The list loops, including at its ends. Do not duplicate a small
            # queue's cover or advertise the current track as an upcoming cover.
            future = tuple((self.index + step) % len(self.queue)
                         for step in range(1, min(self.ARTWORK_NEIGHBORS, len(self.queue) - 1) + 1))
        priority = tuple(self._play_next_indices)
        return (*priority, *(i for i in future if i not in priority))[:self.ARTWORK_NEIGHBORS]

    def _previous_indices(self, *, include_voice=True):
        if self._uses_shuffle_order():
            return tuple(reversed(self._shuffle_history[max(0, self._shuffle_cursor - self.ARTWORK_NEIGHBORS):self._shuffle_cursor]))
        if self._fm_source:
            return tuple(i for i in range(self.index - 1, -1, -1)
                if include_voice or self.queue[i].media_kind != "dj_voice")[:self.ARTWORK_NEIGHBORS]
        return tuple((self.index - step) % len(self.queue)
                     for step in range(1, min(self.ARTWORK_NEIGHBORS, len(self.queue) - 1) + 1))

    def _advance_queue(self, direction=1, *, autoplay=True):
        if direction > 0 and self._play_next_indices:
            self.index = self._play_next_indices.popleft()
            if self._uses_shuffle_order():
                self._shuffle_cursor += 1
                self._shuffle_history.insert(self._shuffle_cursor, self.index)
                self._trim_shuffle_history()
            return self._load_song(autoplay=autoplay)
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
                self._trim_shuffle_history()
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
                if (direction > 0 and target >= len(self.queue)
                        and self._playlist_continuation is not None):
                    return self._wait_for_playlist_page(autoplay=autoplay)
                self.index = target % len(self.queue)
        return self._load_song(autoplay=autoplay)

    def _trim_shuffle_history(self):
        history_limit = min(self.SHUFFLE_HISTORY_LIMIT, len(self.queue) + self.ARTWORK_NEIGHBORS)
        if len(self._shuffle_history) > history_limit and self._shuffle_cursor > 0:
            self._shuffle_history.pop(0)
            self._shuffle_cursor -= 1

    def play_daily(self, source_id="netease"):
        return self._catalog("daily", source_id, lambda: self.service.daily_recommendations(source_id))

    def play_personal_fm(self, source_id="netease"):
        kind = "dj" if source_id == "netease" and self.personal_dj_enabled else "fm"
        return self._catalog(kind, source_id, self._fm_fetch_action(kind, source_id))

    def play_personal_fm_selection(self, source_id, songs, index=0):
        """Play an already browsed FM song, retaining genuine FM continuation.

        Browse results contain ordinary songs. They cannot acquire invented
        introductions; enabled DJ applies to subsequent verified service batches.
        """
        if self._closed or source_id != "netease" or not isinstance(index, int) or isinstance(index, bool):
            return False
        try:
            songs = tuple(songs)
        except TypeError:
            return False
        if (not songs or not 0 <= index < len(songs)
                or not all(isinstance(song, OnlineSong) and song.source_id == source_id
                    and song.media_kind == "song" and isinstance(song.song_id, str)
                    and song.song_id.strip() and isinstance(song.title, str) and song.title.strip()
                    for song in songs)):
            return False
        return self._replace_queue(songs, index, fm_source=source_id,
            fm_kind="dj" if self.personal_dj_enabled else "fm")

    def play_personal_dj(self, source_id="netease"):
        # Compatibility entry point: this is FM with introductions enabled,
        # never a separate playback mode or catalog destination.
        return self.set_personal_dj_enabled(True) and self.play_personal_fm(source_id)

    def play_playlist(self, source_id, playlist_id):
        epoch = self._catalog_epoch + 1
        def fetch():
            offset = 0
            # Start as soon as the first real song window is available. Empty
            # filtered windows may still lead to valid songs on a later page.
            for pages in range(1, self.PLAYLIST_MAX_PAGES + 1):
                if self._closed or epoch != self._catalog_epoch:
                    return ()
                page = self.service.playlist_tracks(source_id, playlist_id, limit=100, offset=offset)
                if self._closed or epoch != self._catalog_epoch:
                    return ()
                songs = self._playlist_page_songs(page, source_id)
                if songs or not page.has_more or page.next_offset <= offset:
                    return _PlaylistStart(source_id, playlist_id, page, offset, pages)
                offset = page.next_offset
            return ()
        return self._catalog("playlist", source_id, fetch)

    @staticmethod
    def _playlist_page_songs(page, source_id):
        if (not isinstance(page, CatalogPage) or not isinstance(page.items, (tuple, list))
                or not isinstance(page.next_offset, int) or isinstance(page.next_offset, bool)
                or page.next_offset < 0 or not isinstance(page.has_more, bool)
                or not all(isinstance(song, OnlineSong) and song.source_id == source_id
                    and song.media_kind == "song" and isinstance(song.song_id, str) and song.song_id.strip()
                    and isinstance(song.title, str) and song.title.strip() for song in page.items)):
            raise OnlineMusicError("invalid_response", source_id)
        return tuple(page.items)

    def _cancel_playlist_loading(self):
        self._playlist_continuation = None
        self._playlist_retry_timer.stop()
        self._playlist_waiting_next = False
        self._playlist_resume = True

    def _playlist_context_is_current(self, context):
        return (context is not None and context is self._playlist_continuation and not self._closed
                and context.catalog_epoch == self._catalog_epoch and context.queue_epoch == self._queue_epoch
                and not self._fm_source and self.track is not None)

    def _submit_playlist_page(self):
        context = self._playlist_continuation
        if (not self._playlist_context_is_current(context) or context.request is not None
                or self._stream_epoch != self._epoch and not self._playlist_waiting_next):
            return False
        self._playlist_request_serial += 1
        token = ("playlist_page", context.queue_epoch, context.catalog_epoch,
                 self._playlist_request_serial, context.source_id, context.playlist_id, context.next_offset)
        context.request = token
        def fetch():
            if not self._playlist_context_is_current(context) or context.request != token:
                return None
            page = self.service.playlist_tracks(context.source_id, context.playlist_id,
                                                limit=100, offset=token[-1])
            if not self._playlist_context_is_current(context) or context.request != token:
                return None
            self._playlist_page_songs(page, context.source_id)
            return page
        if not self.prefetch_tasks.submit(token, fetch):
            context.request = None
            self._playlist_page_failed(context, "busy")
            return False
        return True

    def _playlist_page_failed(self, context, error):
        if not self._playlist_context_is_current(context):
            return
        if error in {"network", "timeout", "busy", "incomplete"} and context.retries < self.PLAYLIST_TRANSIENT_RETRIES:
            context.retries += 1
            self._playlist_retry_timer.start(round(self.PLAYLIST_RETRY_SECONDS * context.retries * 1000))
            return
        self._playlist_continuation = None
        self._playlist_retry_timer.stop()
        if self._playlist_waiting_next:
            self._playlist_waiting_next = False
            self.failure_reason = self._request_failure(error)
            self._set_status("catalog_unavailable")
            self.changed.emit()

    def _playlist_page_completed(self, token, page, error):
        context = self._playlist_continuation
        if not self._playlist_context_is_current(context) or context.request != token:
            return
        context.request = None
        try:
            songs = self._playlist_page_songs(page, context.source_id) if not error else ()
        except OnlineMusicError:
            error = "invalid_response"
            songs = ()
        if error:
            self._playlist_page_failed(context, error)
            return
        context.retries = 0
        context.pages += 1
        old_length = len(self.queue)
        if songs:
            self.queue += songs
            if self._uses_shuffle_order():
                known = {(song.source_id, song.song_id) for song in self.queue[:old_length]}
                new_indices = []
                for index in range(old_length, len(self.queue)):
                    identity = self.queue[index].source_id, self.queue[index].song_id
                    if identity not in known:
                        known.add(identity)
                        new_indices.append(index)
                self._random.shuffle(new_indices)
                self._shuffle_bag.extend(new_indices)
            self._refresh_artwork_window()
            self._apply_cached_artwork()
            if not self._playlist_context_is_current(context):
                return
            self._pump_artwork()
            self._prefetch_audio()
            if not self._playlist_context_is_current(context):
                return
        finished = (not page.has_more or page.next_offset <= token[-1]
                    or context.pages >= self.PLAYLIST_MAX_PAGES)
        if finished:
            self._playlist_continuation = None
        else:
            context.next_offset = page.next_offset
        if self._playlist_waiting_next and (self.index + 1 < len(self.queue) or finished):
            self._advance_queue(autoplay=self._playlist_resume)
        if not finished:
            self._submit_playlist_page()

    def _wait_for_playlist_page(self, *, autoplay):
        # An explicit Next retires its old decoder now, even if the next
        # catalog window is still arriving. It cannot replay the old tail.
        self._playlist_waiting_next = True
        self._playlist_resume = autoplay
        self._autoplay_after_load = autoplay
        self._stream_epoch = 0
        self._clear_stream_fault()
        self._clear_stream_recovery()
        generation = (self._epoch, self._catalog_epoch, self._queue_epoch, self._mode_epoch)
        self.player.stop()
        if self._closed or generation != (self._epoch, self._catalog_epoch, self._queue_epoch, self._mode_epoch):
            return False
        self.player.setSource(QUrl())
        if self._closed or generation != (self._epoch, self._catalog_epoch, self._queue_epoch, self._mode_epoch):
            return False
        self._release_prepared_audio()
        self.track = replace(self.track, is_playing=False)
        self._set_status("loading_catalog")
        if self._closed or generation != (self._epoch, self._catalog_epoch, self._queue_epoch, self._mode_epoch):
            return False
        self.changed.emit()
        if self._closed or generation != (self._epoch, self._catalog_epoch, self._queue_epoch, self._mode_epoch):
            return False
        self._submit_playlist_page()
        return True

    def _catalog(self, kind, source_id, action):
        if self._closed:
            return False
        playlist = (self._playlist_continuation
            if self._playlist_context_is_current(self._playlist_continuation) else None)
        self._cancel_playlist_loading()
        # The existing Qt source continues while its replacement is fetched.
        # Retain only a healthy, genuinely opened stream, never pending audio
        # or an in-progress recovery. Consecutive requests share that source.
        if not self._catalog_stream_is_current():
            self._catalog_stream = (_CatalogStream(self._epoch, QUrl(self.player.source()),
                self._decoded_duration_ms, playlist=playlist) if self.track is not None
                and self._stream_epoch == self._epoch and self._stream_epoch != 0
                and self._stream_fault is None and self._stream_recovery is None
                and not self.player.source().isEmpty() else None)
        self._catalog_epoch += 1
        self._catalog_pending_epoch = self._catalog_epoch
        self._catalog_pending_kind = kind
        self._catalog_pending_source = source_id
        self._fm_prefetch_pending = False
        self._fm_advance_pending = False
        self._fm_skip_voice_pending = False
        self._clear_audio_prefetch()
        self._clear_dj_audio_prefetch(keep_introduction=True)
        epoch = self._catalog_epoch
        # A previous stream or Qt state must not confirm a new catalog opening.
        self._stream_epoch = 0
        self._clear_stream_recovery()
        self._clear_stream_fault()
        self._decoded_duration_ms = 0
        self.failure_reason = ""
        self._set_status("loading_catalog")
        generation = self._fm_generation
        fm_request = kind in {"fm", "dj", "fm_continue", "fm_skip_continue"}
        accepted = self.tasks.submit(("catalog", epoch, kind, source_id, generation),
            lambda: action() if not self._closed and epoch == self._catalog_epoch
            and (not fm_request or generation == self._fm_generation) else ())
        if not accepted:
            self._catalog_pending_epoch = 0
            retained = self._catalog_stream
            ended = self._restore_catalog_stream()
            self._set_status("busy")
            current = (retained is not None and retained.epoch == self._epoch
                and retained.source == self.player.source())
            if ended and current:
                self._end_current_stream()
            elif current and retained.error is not None:
                self._error(retained.error)
        return accepted

    def _catalog_stream_is_current(self):
        stream = self._catalog_stream
        return bool(stream is not None and not self._closed and self.track is not None
            and stream.epoch == self._epoch and stream.source == self.player.source())

    def _restore_catalog_stream(self):
        current = self._catalog_stream_is_current()
        stream, self._catalog_stream = self._catalog_stream, None
        if not current:
            return False
        generation = (self._epoch, self._catalog_epoch, self._queue_epoch, self._mode_epoch)
        # Never play here: a user's pause during the request remains a pause.
        self._stream_epoch = stream.epoch
        self._decoded_duration_ms = stream.decoded_duration_ms
        position = self._qt_position_ms()
        if round(self.track.position_seconds * 1000) != position:
            self._position_changed(position)
        if (self._closed or self.track is None or generation != (self._epoch,
                self._catalog_epoch, self._queue_epoch, self._mode_epoch)
                or stream.source != self.player.source()):
            return False
        if stream.decoded_duration_ms > 0 and round(self.track.duration_seconds * 1000) != stream.decoded_duration_ms:
            self._duration_changed(stream.decoded_duration_ms)
        playing = (self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            and not stream.ended and not self._is_voice_segment())
        if self.track.is_playing != playing:
            self.track = replace(self.track, is_playing=playing)
            self.changed.emit()
        if (self._closed or generation != (self._epoch, self._catalog_epoch,
                self._queue_epoch, self._mode_epoch) or stream.source != self.player.source()):
            return False
        if (stream.playlist is not None and stream.playlist.queue_epoch == self._queue_epoch
                and not self._fm_source):
            # A cancelled/failed replacement returns the original playlist's
            # unfinished tail, but never its old request token or in-flight
            # result. Reissue that offset under the current catalog epoch.
            self._playlist_continuation = replace(stream.playlist,
                catalog_epoch=self._catalog_epoch, request=None)
            if not stream.ended and stream.error is None:
                self._submit_playlist_page()
        return stream.ended

    def _catalog_failed(self, error):
        self._catalog_pending_epoch = 0
        self.failure_reason = self._request_failure(error)
        retained = self._catalog_stream if self._catalog_stream_is_current() else None
        ended = self._restore_catalog_stream()
        self._set_status("catalog_unavailable")
        current = (retained is not None and retained.epoch == self._epoch
            and retained.source == self.player.source())
        if ended and current:
            self._end_current_stream()
        elif current and retained.error is not None:
            self._error(retained.error)

    def cancel_pending_selection(self):
        """Cancel a library selection without reopening its retained source.

        Catalog lookup can keep healthy existing music alive. Once a new
        stream owns the decoder there is no retained source to roll back to.
        Ordinary playback and an already reported failure are left alone.
        """
        if self._closed:
            return False
        if QThread.currentThread() != self.thread():
            return self._request_control("cancel_selection", None, None)
        if not (self._catalog_pending_epoch and self._catalog_pending_kind
                in {"playlist", "daily", "fm", "dj"}):
            if self.status == "loading_stream":
                self.stop()
                return True
            return False
        self._catalog_epoch += 1
        self._catalog_pending_epoch = 0
        self._cancel_playlist_loading()
        retained = self._catalog_stream if self._catalog_stream_is_current() else None
        if retained is None:
            self.stop()
            return True
        generation = (self._epoch, self._catalog_epoch, self._queue_epoch, self._mode_epoch)
        ended = self._restore_catalog_stream()
        if (self._closed or generation != (self._epoch, self._catalog_epoch,
                self._queue_epoch, self._mode_epoch) or self.track is None
                or retained.source != self.player.source()):
            return True
        self._set_status("trial" if self._stream_trial else "playing")
        if (self._closed or generation != (self._epoch, self._catalog_epoch,
                self._queue_epoch, self._mode_epoch) or self.track is None
                or retained.source != self.player.source()):
            return True
        if ended:
            self._end_current_stream()
        elif retained.error is not None:
            self._error(retained.error)
        self.changed.emit()
        return True

    def _load_song(self, *, autoplay=True, introduction=(), introduction_audio=None):
        if self._closed:
            return False
        self._playlist_waiting_next = False
        if self._is_voice_segment():
            target = self._introduced_song_index()
            if target is None:
                return self._voice_failed("invalid_response")
            if self.personal_dj_enabled:
                introduction = self.queue[self.index:target]
                introduction_audio = self._take_prefetched_audio(self.queue[self.index])
            self.index = target
        self._stop_dj_overlay()
        if self._catalog_pending_epoch:
            # A later explicit song choice cancels a pending replacement.
            self._catalog_epoch += 1
        self._catalog_pending_epoch = 0
        self._catalog_stream = None
        old_track = self.track
        voice = self._is_voice_segment()
        introduced = self._introduced_song_index() if voice else None
        if voice and (not self.personal_dj_enabled or introduced is None):
            if introduced is not None:
                self.index = introduced
                return self._load_song(autoplay=autoplay)
            return self._voice_failed("invalid_response")
        song = self.queue[self.index]
        self._play_next_indices = deque(i for i in self._play_next_indices if i != self.index)
        visual_song = self.queue[introduced] if voice else song
        same_visual = old_track is not None and old_track.track_id == f"owned:{visual_song.source_id}:{visual_song.song_id}"
        keep_visual = same_visual and (voice or self._dj_active_epoch == self._epoch)
        ready_lyrics = self._dj_next_lyrics
        self._ready_song_lyrics = (ready_lyrics if not voice and ready_lyrics is not None
            and ready_lyrics[:2] == (song.source_id, song.song_id) else None)
        self._dj_next_lyrics = None
        self._autoplay_after_load = autoplay
        self._epoch += 1
        if not keep_visual:
            self._reset_lyrics_payload()
        else:
            # A parse task belongs to its decoder epoch even when the same
            # paused visual/payload survives speech -> speech/music.
            self._lyrics_parse_pending = None
        epoch = self._epoch
        self._stream_epoch = 0
        self._stream_trial = False
        self._clear_stream_recovery()
        self._stream_recovery_attempted = False
        self._clear_stream_fault()
        self._decoded_duration_ms = 0
        self._fm_advance_pending = False
        self._fm_skip_voice_pending = False
        self.failure_reason = ""
        if self._fm_source and self.index > self.ARTWORK_NEIGHBORS:
            keep_from = self.index - self.ARTWORK_NEIGHBORS
            if self._fm_kind == "dj":
                previous = self._previous_indices(include_voice=False)
                keep_from = previous[-1] if previous else 0
                keep_from = self._intro_start_for_song(keep_from)
            if self._play_next_indices:
                keep_from = min(keep_from, min(self._play_next_indices))
            self.queue = self.queue[keep_from:]
            self.index -= keep_from
            self._play_next_indices = deque(i - keep_from for i in self._play_next_indices if i >= keep_from)
        self._refresh_artwork_window()
        song = self.queue[self.index]
        introduced = self._introduced_song_index() if voice else None
        prefetched = self._take_prefetched_audio(song)
        self._clear_audio_prefetch()
        cached = self._art_cache.get(self._art_key(song))
        self.player.stop()
        self.player.setSource(QUrl())
        self._release_prepared_audio()
        visual_song = self.queue[introduced] if voice else song
        visual_key = self._art_key(visual_song)
        visual_cached = self._art_cache.get(visual_key)
        visual_preview = self._artwork_is_preview(visual_key)
        visual_pending = (visual_key in self._art_retry_at or (self._queue_epoch, visual_key) in self._art_running.values()
                          or self._art_targets.get(visual_key, self.ARTWORK_ORIGINAL_SIDE) < self.ARTWORK_ORIGINAL_SIDE)
        self.track = (replace(old_track, is_playing=False, position_seconds=0.0,
            position_observed_seconds=0.0, position_sampled_at=time.monotonic(),
            album_queue=self._album_queue()) if keep_visual else TrackData(
            visual_song.title, visual_song.artist, ("加载歌词中",), visual_cached[1] if visual_cached else ColorToken(58, 68, 88),
            visual_cached[2] if visual_cached else ColorToken(34, 44, 62), (),
            album_art_bytes=visual_cached[0] if visual_cached else b"",
            source_app=f"GluSpot · {visual_song.source_id}", is_playing=False,
            duration_seconds=visual_song.duration_seconds, track_id=f"owned:{visual_song.source_id}:{visual_song.song_id}",
            position_source="owned-player", position_confidence=1.0,
            source_app_id="GluSpot", position_sampled_at=time.monotonic(), position_observed_seconds=0.0,
            album_art_source="thumbnail" if visual_preview else "original" if visual_cached else "",
            album_art_lookup_state=("pending" if visual_pending else "failed") if visual_preview
                else "original" if visual_cached else "failed" if self._art_key(visual_song) in self._art_failed
                else "pending" if visual_song.artwork_url else "miss",
            album_queue=self._album_queue(),
        ))
        if introduction:
            self._prepare_dj_overlay(introduction, introduction_audio)
            preferences = self._lyrics_preferences_snapshot()
            if not self.tasks.submit(("dj_lyrics", epoch, self._catalog_epoch,
                    song.source_id, song.song_id, self._lyrics_generation),
                    lambda: self._fetch_lyrics(song, preferences) if not self._closed
                    and epoch == self._epoch else None):
                self._lyrics_reparse_failed()
        else:
            self._clear_dj_presentation()
        if voice:
            self._set_dj_presentation("loading", self.index)
            target = self.queue[introduced]
            if keep_visual and self._lyric_payload is not None:
                # Multi-part speech has a new decoder epoch but the same
                # genuine visual payload. Resume a cancelled preference parse
                # without waiting for another account request or audio file.
                self._queue_lyrics_reparse()
            preferences = self._lyrics_preferences_snapshot()
            if not self.tasks.submit(("dj_lyrics", epoch, self._catalog_epoch, target.source_id, target.song_id, self._lyrics_generation),
                lambda: self._fetch_lyrics(target, preferences) if not self._closed
                and epoch == self._epoch and self._is_voice_segment() else None) and self._lyrics_parse_pending is None:
                self._lyrics_reparse_failed()
        elif not introduction:
            self._dj_state = "idle"
            if self._dj_failed_until <= time.monotonic():
                self._dj_next_title = ""
        self._set_status("loading_stream")
        catalog_epoch = self._catalog_epoch
        # DJ/catalog lookahead follows the chosen song immediately, including
        # paused/repeat-one navigation, rather than waiting for its decoder.
        if self.personal_dj_enabled and self._fm_source == "netease" and self._fm_kind == "dj":
            self._prefetch_fm()
            self._prefetch_audio()
        if prefetched is not None:
            self._audio_completed(("audio", epoch, catalog_epoch), prefetched, None)
            return True
        accepted = self.tasks.submit(("stream", epoch, catalog_epoch), lambda: self._resolve_stream(song)
            if not self._closed and epoch == self._epoch and catalog_epoch == self._catalog_epoch else None)
        if not accepted:
            self._stop_dj_overlay()
            if voice:
                self._voice_failed("busy")
                return True
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
        if token[0] == "dj_prefetch":
            self._dj_prefetch_completed(token, result, error)
            return
        if token[0] in {"dj_stream", "dj_audio"}:
            self._dj_overlay_completed(token, result, error)
            return
        if token[0] in {"audio", "audio_prefetch"}:
            self._audio_completed(token, result, error)
            return
        if self._closed:
            return
        kind, epoch, *extra = token
        if kind == "artwork":
            self._artwork_completed(epoch, extra, result, error)
            return
        if kind == "fm_prefetch":
            self._fm_prefetch_completed(epoch, extra, result, error)
            return
        if kind == "playlist_page":
            self._playlist_page_completed(token, result, error)
            return
        if kind == "catalog":
            if epoch != self._catalog_epoch or epoch != self._catalog_pending_epoch:
                return
            catalog_kind, source, generation = extra
            if (catalog_kind in {"fm", "dj", "fm_continue", "fm_skip_continue"}
                    and generation != self._fm_generation):
                return
            if error or not result:
                self._catalog_failed(error)
                return
            playlist_start = result if catalog_kind == "playlist" and isinstance(result, _PlaylistStart) else None
            if playlist_start is not None:
                try:
                    result = self._playlist_page_songs(playlist_start.page, source)
                except OnlineMusicError:
                    self._catalog_failed("invalid_response")
                    return
                if not result:
                    self._catalog_failed(None)
                    return
            if catalog_kind in {"fm_continue", "fm_skip_continue"}:
                incoming = self._unique_fm_songs(source, result)
                if not incoming:
                    self._catalog_failed(None)
                    return
                self._catalog_pending_epoch = 0
                self._catalog_stream = None
                self.index = len(self.queue)
                self.queue += incoming
                if self._fm_kind == "dj":
                    self._fm_prefetch_attempt = None
                if catalog_kind == "fm_skip_continue":
                    while self.index < len(self.queue) and self.queue[self.index].media_kind == "dj_voice":
                        self.index += 1
                    if self.index >= len(self.queue):
                        self._set_status("catalog_unavailable")
                        return
                self._load_song(autoplay=self._fm_resume)
                return
            if not all(isinstance(song, OnlineSong) for song in result):
                self._catalog_failed("invalid_response")
                return
            self._catalog_pending_epoch = 0
            self._catalog_stream = None
            if catalog_kind == "dj":
                result = self._prioritize_initial_dj_introduction(tuple(result))
            next_queue_epoch = self._queue_epoch + 1
            self._replace_queue(result, fm_source=source if catalog_kind in {"fm", "dj"} else "",
                fm_kind="dj" if catalog_kind == "dj" else "fm", prefetch_all_artwork=catalog_kind == "playlist")
            if (playlist_start is not None and not self._closed
                    and self._catalog_epoch == epoch + 1 and self._queue_epoch == next_queue_epoch
                    and playlist_start.source_id == source and playlist_start.page.has_more
                    and playlist_start.page.next_offset > playlist_start.offset
                    and playlist_start.pages < self.PLAYLIST_MAX_PAGES):
                self._playlist_continuation = _PlaylistContinuation(source, playlist_start.playlist_id,
                    self._catalog_epoch, self._queue_epoch, playlist_start.page.next_offset, playlist_start.pages)
                # The real catalog and stream URL operations share a serial
                # bridge. Let first-song authorization/preparation finish
                # before another catalog page can take that transport lock.
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
                if self._voice_failed(self.failure_reason):
                    return
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
                if self._voice_failed(self.failure_reason):
                    return
                if self._stream_recovery is not None:
                    self._finish_stream_failure(self.failure_reason)
                    return
                self._set_status("stream_unavailable")
                self._lyrics_unavailable()
                return
            if self.stream_cache is not None:
                catalog_epoch = self._catalog_epoch
                accepted = self.download_tasks.submit(("audio", epoch, catalog_epoch),
                    lambda: self._prepare_audio(result,
                        cancelled=lambda: not self._audio_request_is_current(epoch, catalog_epoch)))
                if not accepted:
                    self._audio_completed(("audio", epoch, catalog_epoch), None, "busy")
                return
            self._start_stream_audio(result, QUrl(result.url))
        elif kind == "lyrics":
            if self._stream_epoch != epoch and not self._catalog_stream_is_current():
                return
            if error or result is None:
                self._lyrics_reparse_failed()
                return
            payload, match = result
            song = self.queue[self.index]
            if not isinstance(payload, LyricPayload) or (payload.source_id, payload.song_id) != (song.source_id, song.song_id):
                self._lyrics_reparse_failed()
                return
            self._lyric_payload = payload
            if extra and extra[0] != self._lyrics_generation:
                self._queue_lyrics_reparse()
                return
            self._apply_lyrics_match(match)
        elif kind == "dj_lyrics":
            catalog_epoch, source, identity, generation = extra
            if catalog_epoch != self._catalog_epoch:
                return
            if error or result is None:
                # A failed first target fetch must finish the loading marker.
                # A retained multipart payload may still be parsing offline.
                if self._lyrics_parse_pending is None:
                    self._lyrics_reparse_failed()
                return
            payload, match = result
            target = self.index
            if (target is not None and isinstance(payload, LyricPayload)
                    and (payload.source_id, payload.song_id) == (source, identity)
                    and (self.queue[target].source_id, self.queue[target].song_id) == (source, identity)):
                self._dj_next_lyrics = (source, identity, payload, match, generation)
                # The visible paused song is the one this genuine speech
                # introduces, including during multi-part introductions.
                if self.track.track_id == f"owned:{source}:{identity}":
                    self._lyric_payload = payload
                    if generation == self._lyrics_generation:
                        self._apply_lyrics_match(match)
                    else:
                        self._queue_lyrics_reparse()
        elif kind == "lyrics_refresh":
            if self._lyrics_parse_pending != token or not self._lyrics_parse_context_is_current(epoch):
                return
            self._lyrics_parse_pending = None
            if extra[0] != self._lyrics_generation:
                self._queue_lyrics_reparse()
            elif not error:
                self._apply_lyrics_match(result)
            else:
                self._lyrics_reparse_failed()
        elif kind == "lyrics_enrichment":
            if self._lyrics_enrichment_pending != token:
                return
            self._lyrics_enrichment_pending = None
            catalog_epoch, _generation, song = extra
            if (catalog_epoch != self._catalog_epoch or error or result is None
                    or not self._lyrics_enrichment_context_is_current(song, epoch)):
                return
            # The worker retained original payloads, not an old preference's
            # display result. Incrementing the established parser generation
            # coalesces with an in-flight toggle parse and renders only the
            # latest choices; no secondary-source failure clears live lyrics.
            self._lyrics_enrichment_source = result
            self._lyrics_generation += 1
            self._queue_lyrics_reparse()

    def _resolve_stream(self, song):
        if song.media_kind == "dj_voice":
            stream = song.direct_stream
            if (not isinstance(stream, StreamInfo) or not stream.playable
                    or (stream.source_id, stream.song_id) != (song.source_id, song.song_id)):
                raise OnlineMusicError("invalid_response", song.source_id)
            if stream.expires_at > 0 and time.time() >= stream.expires_at:
                raise OnlineMusicError("expired", song.source_id)
            return stream
        return self.service.stream_url(song.source_id, song.song_id)

    def _audio_request_is_current(self, epoch, catalog_epoch):
        return (not self._closed and epoch == self._epoch and catalog_epoch == self._catalog_epoch
            and self.track is not None and self.status not in {"playback_failed", "stream_unavailable"})

    def _audio_completed(self, token, result, error):
        prepared = result if isinstance(result, PreparedAudio) else None
        if token[0] == "audio_prefetch":
            if not self._closed and self._audio_prefetch_request == token:
                self._audio_prefetch_request = None
                if (prepared is not None and not error
                        and (prepared.stream.source_id, prepared.stream.song_id) == token[-2:]):
                    self._prefetched_audio = prepared
                    target = next((i for i, song in enumerate(self.queue)
                        if (song.source_id, song.song_id) == token[-2:] and song.media_kind == "dj_voice"), None)
                    if target is not None and not self._dj_overlay_voices:
                        self._set_dj_presentation("ready", target)
                    return
                target = next((i for i, song in enumerate(self.queue)
                    if (song.source_id, song.song_id) == token[-2:] and song.media_kind == "dj_voice"), None)
                if target is not None and not self._dj_overlay_voices:
                    self._set_dj_presentation("failed", target)
            if prepared is not None:
                self._release_unused_audio(prepared)
            return
        _, epoch, catalog_epoch = token
        if not self._audio_request_is_current(epoch, catalog_epoch):
            if prepared is not None:
                self._release_unused_audio(prepared)
            return
        if self._prepared_audio is not None and self._stream_epoch == epoch:
            # A repeated worker delivery cannot reopen the decoder or unpin
            # the file it is already reading.
            if prepared is not None:
                self._release_unused_audio(prepared)
            return
        if error or prepared is None:
            if prepared is not None:
                self._release_unused_audio(prepared)
            reason = self._request_failure(error)
            if self._voice_failed(reason):
                return
            if self._stream_recovery is not None:
                self._finish_stream_failure(reason)
            else:
                self.failure_reason = reason
                self._set_status("stream_unavailable")
                self._lyrics_reparse_failed()
            return
        song = self.queue[self.index]
        if (prepared.stream.source_id, prepared.stream.song_id) != (song.source_id, song.song_id):
            self._release_unused_audio(prepared)
            self.failure_reason = "invalid_response"
            if self._voice_failed("invalid_response"):
                return
            self._set_status("stream_unavailable")
            self._lyrics_reparse_failed()
            return
        self._prepared_audio = prepared
        self._start_stream_audio(prepared.stream, QUrl.fromLocalFile(str(prepared.path)))

    def _release_unused_audio(self, prepared):
        if (self.stream_cache is not None and all(owned is None or owned.lease != prepared.lease
                for owned in (self._prepared_audio, self._prefetched_audio, self._dj_prepared_audio,
                    *self._dj_prefetched_audio.values()))):
            self.stream_cache.release(prepared)

    def _prepare_audio(self, stream, *, cancelled, busy_deadline=None):
        # An obsolete HTTP reader can take its bounded I/O timeout to notice
        # cancellation. Do not turn its temporary cache slot into a permanent
        # failure for a newer explicit song choice.
        deadline = (time.monotonic() + self.AUDIO_PREPARATION_BUSY_SECONDS
            if busy_deadline is None else busy_deadline)
        while True:
            if cancelled():
                raise StreamDownloadError("cancelled")
            try:
                return self.stream_cache.prepare(stream, cancelled=cancelled)
            except StreamDownloadError as error:
                if error.code != "busy" or time.monotonic() >= deadline:
                    raise
                time.sleep(.05)

    def _start_stream_audio(self, result, source):
        epoch = self._epoch
        self._stream_epoch = epoch
        self._stream_trial = bool(result.trial)
        if self._stream_recovery is not None:
            self._stream_recovery.phase = "load"
        self.player.setSource(source)
        if self._stream_epoch != epoch or self.status == "playback_failed":
            return
        if self._stream_recovery is not None:
            song = self.queue[self.index]
            if self._lyric_payload is None and song.media_kind != "dj_voice":
                preferences = self._lyrics_preferences_snapshot()
                if not self.tasks.submit(("lyrics", epoch, self._lyrics_generation),
                        lambda: self._fetch_lyrics(song, preferences)
                        if not self._closed and epoch == self._epoch else None):
                    self._lyrics_reparse_failed()
            self._check_stream_recovery()
            return
        if self._is_voice_segment():
            self._set_dj_presentation("ready", self.index)
        if self._autoplay_after_load and (not self._dj_overlay_voices or self._dj_source_ready):
            self._play_music()
        else:
            self.player.pause()
            if not self._autoplay_after_load:
                self._set_status("trial" if self._stream_trial else "playing")
        # QMediaPlayer confirms PlayingState asynchronously. A synchronous
        # decoder/network error must not be overwritten by a queued play.
        if self._stream_epoch != epoch or self.status == "playback_failed":
            return
        song = self.queue[self.index]
        if song.media_kind == "dj_voice":
            self._prefetch_fm()
            self._prefetch_audio()
            return
        if self._dj_state == "failed":
            self._dj_state = "idle"
        ready, self._ready_song_lyrics = self._ready_song_lyrics, None
        if ready is not None and ready[:2] == (song.source_id, song.song_id):
            self._lyric_payload = ready[2]
            # Speech and its paused visual song share this payload. Changed
            # preferences invalidate only the parsed match, so the genuine
            # payload can be reparsed without another account request.
            if ready[4] == self._lyrics_generation:
                self._apply_lyrics_match(ready[3])
            else:
                self._queue_lyrics_reparse()
        elif (self._lyric_payload is not None
                and (self._lyric_payload.source_id, self._lyric_payload.song_id) == (song.source_id, song.song_id)):
            self._queue_lyrics_reparse()
        else:
            preferences = self._lyrics_preferences_snapshot()
            if not self.tasks.submit(("lyrics", epoch, self._lyrics_generation), lambda: self._fetch_lyrics(song, preferences)
                    if not self._closed and epoch == self._epoch else None):
                self._lyrics_unavailable()
        self._prefetch_fm()
        self._prefetch_audio()
        self._submit_playlist_page()

    def _release_prepared_audio(self):
        if self._prepared_audio is not None:
            self.stream_cache.release(self._prepared_audio)
            self._prepared_audio = None

    def _clear_audio_prefetch(self):
        self._audio_prefetch_request = None
        self._audio_prefetch_generation += 1
        if self._prefetched_audio is not None:
            self.stream_cache.release(self._prefetched_audio)
            self._prefetched_audio = None

    def _take_prefetched_audio(self, song):
        prepared = self._prefetched_audio
        if (prepared is not None and (prepared.stream.source_id, prepared.stream.song_id) == (song.source_id, song.song_id)
                and (prepared.stream.expires_at <= 0 or time.time() < prepared.stream.expires_at)):
            self._prefetched_audio = None
            return prepared
        return None

    def _prefetch_audio(self):
        if self.personal_dj_enabled and self._fm_source == "netease" and self._fm_kind == "dj":
            self._prefetch_dj_audio()
            if not self._play_next_indices:
                return
        if (self.stream_cache is None or self._closed or self.track is None
                or self.playback_mode == "repeat_one" and not self._play_next_indices
                    and self.queue[self.index].media_kind != "dj_voice"
                or self._prepared_audio is None
                or self._stream_epoch != self._epoch):
            return
        upcoming = self._upcoming_indices()
        if not upcoming:
            return
        song = self.queue[upcoming[0]]
        current = self.queue[self.index]
        if (song.source_id, song.song_id) == (current.source_id, current.song_id):
            return
        if (self._prefetched_audio is not None
                and (self._prefetched_audio.stream.source_id, self._prefetched_audio.stream.song_id) == (song.source_id, song.song_id)):
            return
        identity = (self._queue_epoch, self._catalog_epoch, self._mode_epoch, song.source_id, song.song_id)
        if self._audio_prefetch_request is not None and self._audio_prefetch_request[2:] == identity:
            return
        self._clear_audio_prefetch()
        token = ("audio_prefetch", self._audio_prefetch_generation, *identity)
        self._audio_prefetch_request = token
        if song.media_kind == "dj_voice" and not self._dj_overlay_voices:
            self._set_dj_presentation("loading", upcoming[0])

        def prepare():
            cancelled = lambda: self._closed or self._audio_prefetch_request != token
            if cancelled():
                return None
            return self.stream_cache.prepare(self._resolve_stream(song), cancelled=cancelled)

        if not self.download_tasks.submit(token, prepare):
            self._audio_prefetch_request = None
            if song.media_kind == "dj_voice" and not self._dj_overlay_voices:
                self._set_dj_presentation("failed", upcoming[0])

    def _dj_audio_window(self):
        if (self._closed or self.stream_cache is None or not self.personal_dj_enabled
                or self._fm_source != "netease" or self._fm_kind != "dj"
                or not 0 <= self.index < len(self.queue)):
            return ()
        foreground, voices, remaining, upcoming = (), [], (), ()
        if self._dj_overlay_voices and not self._dj_overlay_is_preview:
            current = self._dj_overlay_voices[self._dj_overlay_position]
            if not self._dj_source_ready and current in self._dj_prefetch_requests:
                foreground = (current,)
            remaining = self._dj_overlay_voices[self._dj_overlay_position + 1:]
        # Preserve the service association. A song-only recommendation has no
        # fabricated speech; the next genuine introduction can still warm up.
        for target in self._upcoming_indices(include_voice=False):
            start = self._intro_start_for_song(target)
            if start < target:
                upcoming = self.queue[start:target]
                break
        # Keep the following song's first real introduction warm even during
        # a long multipart current speech. Spare slots continue each sequence.
        voices.extend(remaining[:1])
        voices.extend(upcoming[:1])
        voices.extend(remaining[1:])
        voices.extend(upcoming[1:])
        return (*foreground, *tuple(dict.fromkeys(voices))[:self.DJ_PREFETCH_FILES])

    def _clear_dj_audio_prefetch(self, *, keep_introduction=False):
        self._dj_prefetch_timer.stop()
        retained = (set(self._dj_overlay_voices[self._dj_overlay_position:])
            if keep_introduction and not self._dj_overlay_is_preview else set())
        for voice in tuple(self._dj_prefetch_requests):
            if voice not in retained:
                del self._dj_prefetch_requests[voice]
        self._dj_prefetch_failed.clear()
        for voice in tuple(self._dj_prefetched_audio):
            if voice not in retained:
                self._release_unused_audio(self._dj_prefetched_audio.pop(voice))
        if self._dj_overlay_retry is not None:
            if self._dj_overlay_request_current(self._dj_overlay_retry[0]):
                self._dj_prefetch_timer.start()
            else:
                self._dj_overlay_retry = None
                self._dj_overlay_retry_deadline = 0.0

    def _retry_dj_prefetch(self):
        self._retry_dj_overlay()
        self._prefetch_fm()
        self._prefetch_audio()

    def _take_dj_prefetched_audio(self, voice):
        prepared = self._dj_prefetched_audio.pop(voice, None)
        if prepared is not None and prepared.stream.expires_at > 0 and time.time() >= prepared.stream.expires_at:
            self._release_unused_audio(prepared)
            return None
        return prepared

    def _prefetch_dj_audio(self):
        desired = self._dj_audio_window()
        wanted = set(desired)
        for voice in tuple(self._dj_prefetch_requests):
            if voice not in wanted:
                del self._dj_prefetch_requests[voice]
        for voice in tuple(self._dj_prefetched_audio):
            if voice not in wanted:
                self._release_unused_audio(self._dj_prefetched_audio.pop(voice))
        self._dj_prefetch_failed.intersection_update(wanted)
        if not desired:
            return
        # One background download leaves a worker/cache slot for foreground
        # audio. Preparation has no dependence on play/pause or repeat mode.
        current = (self._dj_overlay_voices[self._dj_overlay_position]
            if self._dj_overlay_voices and not self._dj_overlay_is_preview and not self._dj_source_ready else None)
        if any(voice != current for voice in self._dj_prefetch_requests):
            return
        missing = next((voice for voice in desired if voice not in self._dj_prefetched_audio
            and voice not in self._dj_prefetch_requests and voice not in self._dj_prefetch_failed), None)
        if missing is None:
            return
        self._dj_prefetch_serial += 1
        token = ("dj_prefetch", self._dj_prefetch_serial, self._queue_epoch,
            self._catalog_epoch, self._fm_generation)
        self._dj_prefetch_requests[missing] = token
        target = next((i for i, voice in enumerate(self.queue) if voice == missing), None)
        if target is not None and not self._dj_overlay_voices:
            self._set_dj_presentation("loading", target)
        if self._closed or self._dj_prefetch_requests.get(missing) != token:
            return

        def prepare():
            cancelled = lambda: self._closed or self._dj_prefetch_requests.get(missing) != token
            if cancelled():
                return None
            return self._prepare_audio(self._resolve_stream(missing), cancelled=cancelled)

        if not self.download_tasks.submit(token, prepare):
            if not self._closed and self._dj_prefetch_requests.get(missing) == token:
                del self._dj_prefetch_requests[missing]
                self._dj_prefetch_timer.start()

    def _dj_prefetch_completed(self, token, result, error):
        voice = next((voice for voice, pending in self._dj_prefetch_requests.items() if pending == token), None)
        prepared = result if isinstance(result, PreparedAudio) else None
        if voice is None or self._closed:
            if prepared is not None:
                self._release_unused_audio(prepared)
            return
        del self._dj_prefetch_requests[voice]
        if prepared is not None and (error or prepared.stream != voice.direct_stream):
            self._release_unused_audio(prepared)
            prepared, error = None, error or "invalid_response"
        if prepared is not None and prepared.stream.expires_at > 0 and time.time() >= prepared.stream.expires_at:
            self._release_unused_audio(prepared)
            prepared, error = None, "expired"
        current = (self._dj_overlay_voices[self._dj_overlay_position]
            if self._dj_overlay_voices and not self._dj_overlay_is_preview else None)
        if current == voice and not self._dj_source_ready:
            foreground = ("dj_audio", self._epoch, self._catalog_epoch,
                self._dj_generation, voice.song_id)
            if error == "busy":
                # The background download's contention budget may have run
                # before navigation promoted it. Give the now-current part
                # the existing bounded foreground preparation budget.
                self._schedule_dj_overlay_retry(("dj_stream", *foreground[1:]))
            else:
                self._dj_overlay_completed(foreground, prepared, error)
        elif prepared is not None and voice in self._dj_audio_window():
            self._dj_prefetched_audio[voice] = prepared
            target = next((i for i, song in enumerate(self.queue) if song == voice), None)
            if target is not None and not self._dj_overlay_voices:
                self._set_dj_presentation("ready", target)
        else:
            if prepared is not None:
                self._release_unused_audio(prepared)
            if error == "busy":
                self._dj_prefetch_timer.start()
                return
            self._dj_prefetch_failed.add(voice)
            target = next((i for i, song in enumerate(self.queue) if song == voice), None)
            if target is not None and not self._dj_overlay_voices:
                self._set_dj_presentation("failed", target)
        self._prefetch_dj_audio()

    def _apply_lyrics_match(self, match):
        if match is None or not match.lyrics:
            self._lyrics_reparse_failed()
            self._schedule_lyrics_enrichment(match)
            return
        self.track = replace(self.track, timed_lyrics=match.timed_lyrics, lyrics=match.lyrics,
                             lyrics_source=match.source)
        self.changed.emit()
        self._schedule_lyrics_enrichment(match)

    def _reset_lyrics_payload(self):
        self._lyric_payload = None
        self._lyrics_generation += 1
        self._lyrics_parse_pending = None
        self._lyrics_enrichment_pending = None
        self._lyrics_enrichment_attempted = False
        self._lyrics_enrichment_source = None

    def _lyrics_enrichment_context_is_current(self, song, epoch):
        return (not self._closed and epoch == self._epoch and self.track is not None
            and 0 <= self.index < len(self.queue) and self.queue[self.index] == song
            and self.track.track_id == f"owned:{song.source_id}:{song.song_id}"
            and self._lyric_payload is not None
            and (self._lyric_payload.source_id, self._lyric_payload.song_id)
                == (song.source_id, song.song_id)
            and self._lyrics_parse_context_is_current(epoch))

    def _schedule_lyrics_enrichment(self, baseline):
        """Search one exact-ID fallback after official lyrics are visible."""
        if self._lyrics_enrichment_attempted or self._lyrics_enrichment_pending is not None:
            return False
        if not (0 <= self.index < len(self.queue)):
            return False
        song, epoch = self.queue[self.index], self._epoch
        resolver = getattr(self.lyrics_store, "resolve_trusted_lyrics", None)
        compatible = getattr(self.lyrics_store, "is_compatible_lyric_upgrade", None)
        if (song.source_id not in {"netease", "qq"} or song.media_kind != "song"
                or not song.title.strip() or not song.artist.strip()
                or not callable(resolver) or not callable(compatible)
                or not self._lyrics_enrichment_context_is_current(song, epoch)):
            return False
        if baseline is not None and has_sufficient_word_timing(baseline.timed_lyrics):
            return False
        preferences = self._lyrics_preferences_snapshot()
        parser = copy.copy(self.lyrics_store)
        parser.settings_getter = lambda key, default=None: preferences.get(key, default)
        token = ("lyrics_enrichment", epoch, self._catalog_epoch, self._lyrics_generation, song)
        self._lyrics_enrichment_attempted = True
        self._lyrics_enrichment_pending = token

        def search():
            # Workers inspect only immutable/state snapshots. Native player
            # methods in _lyrics_parse_context_is_current stay on Qt's thread.
            if (self._closed or epoch != self._epoch or token[2] != self._catalog_epoch
                    or self.track is None or not (0 <= self.index < len(self.queue))
                    or self.queue[self.index] != song
                    or self.track.track_id != f"owned:{song.source_id}:{song.song_id}"):
                return None
            candidate = parser.resolve_trusted_lyrics(song.source_id, song.song_id,
                song.title, song.artist, song.album, song.duration_seconds)
            return candidate if candidate is not None and parser.is_compatible_lyric_upgrade(
                baseline, candidate,
            ) else None

        # Reuse the bounded low-priority catalog/prefetch workers. A slow
        # backup lookup cannot occupy either foreground stream/lyric worker.
        if not self.prefetch_tasks.submit(token, search):
            self._lyrics_enrichment_pending = None
            return False
        return True

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
        if payload is None or self._closed or self.track is None or not self._lyrics_parse_context_is_current(epoch):
            return False
        token = ("lyrics_refresh", epoch, self._lyrics_generation)
        preferences = self._lyrics_preferences_snapshot()
        enrichment = self._lyrics_enrichment_source
        song = self.queue[self.index] if 0 <= self.index < len(self.queue) else None
        self._lyrics_parse_pending = token
        accepted = self.tasks.submit(token, lambda: self._parse_lyrics_payload(payload, preferences, enrichment, song=song)
            if not self._closed and epoch == self._epoch and token[2] == self._lyrics_generation else None)
        if not accepted:
            self._lyrics_parse_pending = None
            self._lyrics_reparse_failed()
        return accepted

    def _lyrics_parse_context_is_current(self, epoch):
        if self._stream_epoch == epoch or self._catalog_stream_is_current():
            return True
        # Parsing the introduced song is pure visual work. Its verified
        # association and payload identity remain valid before speech audio is
        # ready; the completion token still fences decoder/preference epochs.
        target = self.index if self._dj_overlay_voices else self._introduced_song_index() if self._is_voice_segment() else None
        if target is None or self.track is None or self._lyric_payload is None:
            return False
        song = self.queue[target]
        return (self.track.track_id == f"owned:{song.source_id}:{song.song_id}"
            and (self._lyric_payload.source_id, self._lyric_payload.song_id) == (song.source_id, song.song_id))

    def _lyrics_reparse_failed(self):
        # Keep established lyrics after a failed preference change, while a
        # failed first parse must terminate the existing loading marker.
        if (self.track is not None and not self.track.timed_lyrics and not self.track.lyrics_source
                and self.track.lyrics == ("加载歌词中",)):
            self._lyrics_unavailable()

    def _lyrics_unavailable(self):
        if self.track is not None:
            self.track = replace(self.track, timed_lyrics=(),
                lyrics=("暂无歌词",), lyrics_source="")
            self.changed.emit()

    @staticmethod
    def _art_key(song):
        return song.source_id, song.song_id, song.artwork_url

    def _artwork_is_preview(self, key):
        return (self._art_cache_targets.get(key, self.ARTWORK_ORIGINAL_SIDE) < self.ARTWORK_ORIGINAL_SIDE
                or 0 < self._art_sizes.get(key, 0) < 320)

    def _reset_queue_cache(self):
        self._cancel_playlist_loading()
        self._clear_audio_prefetch()
        self._clear_dj_audio_prefetch()
        self._play_next_indices.clear()
        self._queue_epoch += 1
        self._art_cache.clear()
        self._art_sizes.clear()
        self._art_cache_targets.clear()
        self._art_window.clear()
        self._art_distances.clear()
        self._art_attempted.clear()
        self._art_failed.clear()
        self._art_retry_counts.clear()
        self._art_retry_at.clear()
        self._art_retry_timer.stop()
        self._art_targets.clear()
        self._art_visible_hint = None
        self._art_visible_keys = ()
        self._art_buffer_keys = ()
        self._art_hint_songs.clear()
        self._art_prefetch_all = False
        self._art_sequential_keys = ()
        self._art_sequential_pending.clear()
        self._fm_prefetch_pending = False
        self._fm_prefetch_attempt = None
        self._fm_advance_pending = False
        self._fm_skip_voice_pending = False

    def _refresh_artwork_window(self):
        self._refresh_queue_view()
        if self._art_visible_hint is not None:
            if self._art_visible_hint[0] != self._queue_view_serial:
                self._art_visible_hint = None
                self._art_visible_keys = ()
                self._art_buffer_keys = ()
                self._art_hint_songs.clear()
        order = (self.index, *self._upcoming_indices(include_voice=False), *self._previous_indices(include_voice=False))
        self._art_window = {self._art_key(self.queue[i]): self.queue[i] for i in order
                            if self.queue[i].artwork_url}
        self._art_window.update((key, song) for key, song in self._art_hint_songs.items() if song.artwork_url)
        # Only a deliberately started playlist acquires its complete ordinary
        # song artwork. Catalog browsing never opens song pages for covers.
        # This contains immutable URL metadata, not decoded images or jobs;
        # acquisition still shares the two workers and compressed-byte cap.
        self._art_sequential_keys = ()
        if self._art_prefetch_all:
            self._art_window.update((self._art_key(song), song) for song in self._queue_view_songs if song.artwork_url)
            self._art_sequential_keys = tuple(dict.fromkeys(
                self._art_key(song) for song in self._queue_view_songs if song.artwork_url))
        self._art_sequential_pending = deque(key for key in self._art_sequential_keys if key not in self._art_attempted)
        self._art_distances = {}
        for rank, i in enumerate(order):
            key = self._art_key(self.queue[i])
            self._art_distances[key] = min(rank, self._art_distances.get(key, 7))
        for rank, key in enumerate((*self._art_visible_keys, *self._art_buffer_keys), start=len(order)):
            self._art_distances.setdefault(key, rank)
        for rank, key in enumerate(self._art_sequential_keys, start=len(self._art_distances)):
            self._art_distances.setdefault(key, rank)
        # Scrolling changes acquisition priority, not the lifetime of a good
        # same-queue thumbnail. FM-pruned/removed songs leave this cache, while
        # queue replacement clears it through the existing epoch reset.
        self._art_cache = {key: value for key, value in self._art_cache.items() if key in self._queue_view_art_keys}
        self._art_sizes = {key: side for key, side in self._art_sizes.items() if key in self._art_cache}
        self._art_cache_targets = {key: target for key, target in self._art_cache_targets.items() if key in self._art_cache}
        self._art_attempted.intersection_update(self._art_window)
        self._art_failed.intersection_update(self._art_window)
        self._art_retry_counts = {key: count for key, count in self._art_retry_counts.items() if key in self._art_window}
        self._art_retry_at = {key: due for key, due in self._art_retry_at.items() if key in self._art_window}
        self._art_targets = {key: target for key, target in self._art_targets.items() if key in self._art_window}

    def _album_queue(self):
        entries = []
        # AlbumQueueEntry is the provider's upcoming-track contract. Previous
        # songs remain in the owned queue/cache and the island's own history.
        for i in self._upcoming_indices(include_voice=False):
            song = self.queue[i]
            cached = self._art_cache.get(self._art_key(song))
            entries.append(AlbumQueueEntry(song.title, song.artist, song.album,
                album_art_bytes=cached[0] if cached else b"", track_id=f"owned:{song.source_id}:{song.song_id}",
                album_color=cached[1] if cached else None, secondary_color=cached[2] if cached else None))
        return tuple(entries)

    def _pump_artwork(self):
        if self._closed or self.track is None:
            return
        upcoming = self._upcoming_indices(include_voice=False)
        neighbors = (self.index, *upcoming, *self._previous_indices(include_voice=False))
        original_indices = (self.index, *upcoming[:1]) if self._art_prefetch_all else neighbors
        original_keys = {self._art_key(self.queue[i]) for i in original_indices}
        current = self._art_key(self.queue[self.index])
        # The current cover and immediate real next song retain priority. A
        # scrolled-to window then fills before more distant neighbor upgrades.
        priority_neighbors = () if self._art_prefetch_all else neighbors
        priority = dict.fromkeys((current, *(self._art_key(self.queue[i]) for i in upcoming[:1]),
            *self._art_visible_keys, *(self._art_key(self.queue[i]) for i in priority_neighbors),
            *self._art_buffer_keys, *self._art_retry_at))
        def candidates():
            yield from priority
            # A cursor consumes background metadata once, rather than scanning
            # a large finished playlist on every thumbnail completion.
            while self._art_sequential_pending and len(self._art_running) < 2:
                yield self._art_sequential_pending.popleft()
        for key in candidates():
            if len(self._art_running) >= 2:
                break
            if key not in self._art_window or (self._queue_epoch, key) in self._art_running.values():
                continue
            target = self.ARTWORK_ORIGINAL_SIDE if key in original_keys else self.ARTWORK_THUMBNAIL_SIDE
            if self._art_targets.get(key, target) < target:
                # A ready list thumbnail remains visible while its newly
                # selected/current-neighbor original upgrades independently.
                self._art_attempted.discard(key)
                self._art_retry_at.pop(key, None)
                self._art_retry_counts.pop(key, None)
            due = self._art_retry_at.get(key)
            if due is not None and due <= time.monotonic():
                self._art_retry_at.pop(key, None)
                self._art_attempted.discard(key)
            elif due is not None:
                continue
            if (key in self._art_attempted
                    or key in self._art_cache and not 0 < self._art_sizes.get(key, 0) < 320
                    and self._art_cache_targets.get(key, target) >= target):
                continue
            self._art_request += 1
            request, epoch = self._art_request, self._queue_epoch
            self._art_attempted.add(key)
            self._art_targets[key] = target
            self._art_running[request] = (epoch, key)
            self._art_request_targets[request] = target
            accepted = self.artwork_tasks.submit(("artwork", epoch, key, request),
                lambda key=key, epoch=epoch, target=target: self._fetch_artwork(key, epoch, target))
            if not accepted:
                self._art_running.pop(request, None)
                self._art_request_targets.pop(request, None)
                self._art_failed.add(key)
                self._schedule_artwork_retry(key)
                if key == current:
                    self.track = replace(self.track, album_art_lookup_state="failed")
                    self.changed.emit()
        if self._art_retry_at:
            delay = max(.01, min(self._art_retry_at.values()) - time.monotonic())
            self._art_retry_timer.start(max(1, round(delay * 1000)))

    def _schedule_artwork_retry(self, key):
        count = self._art_retry_counts.get(key, 0)
        if count >= self.ARTWORK_TRANSIENT_RETRIES or key not in self._art_window:
            return
        self._art_retry_counts[key] = count + 1
        self._art_retry_at[key] = time.monotonic() + self.ARTWORK_RETRY_SECONDS * (count + 1)
        self._art_retry_timer.start(max(1, round(self.ARTWORK_RETRY_SECONDS * 1000)))

    def _artwork_completed(self, epoch, extra, result, error):
        key, request = extra
        requested_target = self._art_request_targets.pop(request, self.ARTWORK_ORIGINAL_SIDE)
        if self._art_running.pop(request, None) != (epoch, key):
            return
        if epoch == self._queue_epoch and key in self._art_window and self.track is not None:
            published = None
            if error or not result or len(result[0]) > ONLINE_ARTWORK_MAX_BYTES:
                self._art_failed.add(key)
                if error in {"network", "timeout", "incomplete", "invalid_image", "busy"}:
                    self._schedule_artwork_retry(key)
            else:
                self._art_failed.discard(key)
                side = result[3] if len(result) > 3 else 0
                cache_target = requested_target
                previous = self._art_cache.get(key)
                if previous and self._art_sizes.get(key, 0) > side:
                    result = previous
                    side = self._art_sizes[key]
                    cache_target = self._art_cache_targets.get(key, self.ARTWORK_ORIGINAL_SIDE)
                self._art_cache[key] = result[:3]
                self._art_sizes[key] = side
                self._art_cache_targets[key] = cache_target
                self._art_targets.setdefault(key, requested_target)
                if previous is None or previous[0] != result[0]:
                    published = result[0]
                if 0 < side < 320:
                    self._schedule_artwork_retry(key)
                else:
                    self._art_retry_at.pop(key, None)
                # Originals stay compressed; protect the current cover while
                # evicting the farthest neighbors if the shared byte cap fills.
                current = self._art_key(self.queue[self.index])
                while sum(len(value[0]) for value in self._art_cache.values()) > self.ARTWORK_CACHE_MAX_BYTES:
                    candidates = [key for key in self._art_cache if key != current]
                    if not candidates:
                        break
                    farthest = max(candidates, key=lambda key: self._art_distances.get(key, float("inf")))
                    self._art_cache.pop(farthest)
                    self._art_sizes.pop(farthest, None)
                    self._art_cache_targets.pop(farthest, None)
            self._apply_cached_artwork()
            if (published is not None and not self._closed and epoch == self._queue_epoch
                    and key in self._art_window and self._art_cache.get(key, (None,))[0] == published):
                self.queueArtworkChanged.emit()
        self._pump_artwork()

    def _apply_cached_artwork(self):
        visual_song = (next((song for song in self.queue
            if self.track.track_id == f"owned:{song.source_id}:{song.song_id}"), None)
            if self._is_voice_segment() else self.queue[self.index])
        cached = self._art_cache.get(self._art_key(visual_song)) if visual_song is not None else None
        changes = {"album_queue": self._album_queue()}
        if cached:
            key = self._art_key(visual_song)
            preview = self._artwork_is_preview(key)
            pending = (key in self._art_retry_at or (self._queue_epoch, key) in self._art_running.values()
                       or self._art_targets.get(key, self.ARTWORK_ORIGINAL_SIDE) < self.ARTWORK_ORIGINAL_SIDE)
            changes.update(album_art_bytes=cached[0], album_color=cached[1], secondary_color=cached[2],
                           album_art_source="thumbnail" if preview else "original",
                           album_art_lookup_state="pending" if preview and pending else "failed" if preview else "original")
        elif visual_song is not None and self._art_key(visual_song) in self._art_failed:
            changes["album_art_lookup_state"] = "failed"
        self.track = replace(self.track, **changes)
        self.changed.emit()

    def _fetch_artwork(self, key, epoch, target_side=2560):
        if self._closed or epoch != self._queue_epoch or key not in self._art_window:
            return None
        if target_side == self.ARTWORK_THUMBNAIL_SIDE:
            content = fetch_online_artwork(key[2], raise_errors=True, target_side=target_side)
            if content:
                content = online_artwork_thumbnail_bytes(content, target_side)
        else:
            content = fetch_online_artwork(key[2], raise_errors=True)
        if not content or self._closed or epoch != self._queue_epoch or key not in self._art_window:
            return None
        primary, companion = online_artwork_colors(content, ColorToken(58, 68, 88), ColorToken(34, 44, 62))
        side = online_artwork_min_side(content)
        return (content, primary, companion, side) if not self._closed and epoch == self._queue_epoch and key in self._art_window else None

    def _prefetch_fm(self):
        if (not self._fm_source or self._fm_prefetch_pending or self._closed
                or not self.queue or self._catalog_pending_epoch):
            return
        dj = self.personal_dj_enabled and self._fm_source == "netease" and self._fm_kind == "dj"
        future_count = (len(self._upcoming_indices(include_voice=False)) if dj
            else len(self.queue) - self.index - 1)
        if (not dj and self.playback_mode == "repeat_one" and self.queue[self.index].media_kind != "dj_voice"
                or future_count >= self.ARTWORK_NEIGHBORS):
            return
        attempt = (self._queue_epoch, self.queue[-1].song_id)
        if attempt == self._fm_prefetch_attempt:
            return
        retry_attempt = (attempt, self._catalog_epoch, self._fm_generation)
        if retry_attempt != self._fm_prefetch_retry_attempt:
            self._fm_prefetch_retry_attempt = retry_attempt
            self._fm_prefetch_retry_count = 0
        self._fm_prefetch_attempt = attempt
        epoch, source, catalog_epoch = self._queue_epoch, self._fm_source, self._catalog_epoch
        generation = self._fm_generation
        fetch = self._fm_fetch_action(self._fm_kind, source)
        self._fm_prefetch_serial += 1
        token = ("fm_prefetch", epoch, catalog_epoch, source, generation, self._fm_prefetch_serial)
        self._fm_prefetch_request = token
        self._fm_prefetch_pending = True
        if not self.prefetch_tasks.submit(token,
                lambda: fetch() if not self._closed and epoch == self._queue_epoch
                and catalog_epoch == self._catalog_epoch and generation == self._fm_generation
                and self._fm_prefetch_request == token else ()):
            self._fm_prefetch_pending = False
            self._fm_prefetch_request = None
            if dj:
                self._fm_prefetch_attempt = None
                self._dj_prefetch_timer.start()

    def _fm_prefetch_completed(self, epoch, extra, result, error):
        catalog_epoch, source, generation, _serial = extra
        if (epoch != self._queue_epoch or catalog_epoch != self._catalog_epoch or source != self._fm_source
                or generation != self._fm_generation
                or self._fm_prefetch_request != ("fm_prefetch", epoch, *extra)):
            return
        self._fm_prefetch_pending = False
        self._fm_prefetch_request = None
        if error or not result:
            if (self.personal_dj_enabled and self._fm_kind == "dj" and source == "netease"
                    and error in {"network", "timeout", "busy"}
                    and self._fm_prefetch_retry_count < self.DJ_CATALOG_TRANSIENT_RETRIES):
                self._fm_prefetch_retry_count += 1
                self._fm_prefetch_attempt = None
                self._dj_prefetch_timer.start()
                return
            if self._fm_advance_pending:
                self._fm_advance_pending = False
                self._fm_skip_voice_pending = False
                self.failure_reason = self._request_failure(error)
                self._set_status("catalog_unavailable")
            return
        incoming = self._unique_fm_songs(source, result)
        if incoming:
            self.queue += incoming
            if self._fm_kind == "dj":
                self._fm_prefetch_attempt = None
            self._refresh_artwork_window()
            self._apply_cached_artwork()
            self._pump_artwork()
            self._prefetch_audio()
            if self._fm_kind == "dj":
                self._prefetch_fm()
        if self._fm_advance_pending:
            self._fm_advance_pending = False
            if self.index + 1 < len(self.queue):
                self.index += 1
                if self._fm_skip_voice_pending:
                    while self.index < len(self.queue) and self.queue[self.index].media_kind == "dj_voice":
                        self.index += 1
                self._fm_skip_voice_pending = False
                if self.index >= len(self.queue):
                    self._set_status("catalog_unavailable")
                    return
                self._load_song(autoplay=self._fm_resume)
            else:
                self._set_status("catalog_unavailable")

    def _unique_fm_songs(self, source, result):
        if self._fm_kind == "dj":
            # A speech segment may introduce a previously recommended song.
            # Preserve complete service pairs rather than orphan introductions
            # by deduplicating their songs against the previous batch.
            incoming = tuple(result)
            if not (0 < len(incoming) <= 96 and all(isinstance(song, OnlineSong)
                    and song.source_id == source for song in incoming)):
                return ()
            # The verified service can omit introductions for some songs,
            # including the first. Retain its actual order without dropping
            # music or inventing speech for an unintroduced recommendation.
            return incoming
        existing = {(song.source_id, song.song_id) for song in self.queue}
        incoming = []
        for song in result:
            if isinstance(song, OnlineSong) and song.source_id == source and (source, song.song_id) not in existing:
                incoming.append(song)
                existing.add((source, song.song_id))
                if len(incoming) == self.ARTWORK_NEIGHBORS:
                    break
        return tuple(incoming)

    def _advance_fm(self, *, skip_voice=False):
        self._fm_resume = self._autoplay_after_load
        # Replacing a catalog preserves healthy music; accepting Next at the
        # queue tail is a navigation boundary. Retire the skipped decoders
        # immediately, before either a pending or a new catalog can finish.
        # Old multipart downloads must not regain the new catalog's epoch.
        self._epoch += 1
        boundary = (self._epoch, self._queue_epoch, self._catalog_epoch, self._fm_generation)
        self._stream_epoch = 0
        self._catalog_stream = None
        self._stop_dj_overlay()
        if self._closed or boundary != (self._epoch, self._queue_epoch, self._catalog_epoch, self._fm_generation):
            return False
        self._clear_dj_audio_prefetch()
        self._clear_stream_recovery()
        self._clear_stream_fault()
        self.player.stop()
        # Decoder signals may synchronously select another source or stop
        # ownership. Never clear that newly selected source or its lease.
        if self._closed or boundary != (self._epoch, self._queue_epoch, self._catalog_epoch, self._fm_generation):
            return False
        self.player.setSource(QUrl())
        if self._closed or boundary != (self._epoch, self._queue_epoch, self._catalog_epoch, self._fm_generation):
            return False
        self._release_prepared_audio()
        if self.track is not None:
            self.track = replace(self.track, is_playing=False)
        self.changed.emit()
        if self._closed or boundary != (self._epoch, self._queue_epoch, self._catalog_epoch, self._fm_generation):
            return False
        if self._fm_prefetch_pending:
            self._fm_advance_pending = True
            self._fm_skip_voice_pending = skip_voice
            self._stream_epoch = 0
            self._set_status("loading_catalog")
            return True
        source = self._fm_source
        fetch = self._fm_fetch_action(self._fm_kind, source)
        return self._catalog("fm_skip_continue" if skip_voice else "fm_continue", source, fetch)

    def _lyrics_preferences_snapshot(self):
        # The source parser reads these several times. Capture once on the
        # owner thread so a button change cannot mix preferences within a row.
        defaults = {"v2_enable_dual_line_lyrics": False, "v2_lyric_flow_style": "",
            "v2_ember_dual_line_enabled": False, "v2_dual_line_secondary_source": "translation",
            "v2_lyric_chinese_conversion": "off", "v2_cantonese_secondary_priority": "phonetic_first"}
        return {key: self.lyrics_store._setting_value(key, value) for key, value in defaults.items()}

    def _fetch_lyrics(self, song, preferences):
        if song.media_kind == "dj_voice":
            return LyricPayload(song.source_id, song.song_id), None
        try:
            payload = self.service.lyrics(song.source_id, song.song_id)
        except OnlineMusicError as error:
            # A verified queue song remains an exact-ID lookup target when
            # its official lyric endpoint is unavailable. Preserve that
            # failure's provenance without inventing any lyric text. Explicit
            # service/account cancellation keeps its existing request fence.
            if error.code in {"cancelled", "closed"}:
                raise
            payload = LyricPayload(song.source_id, song.song_id,
                source=f"{song.source_id}_unavailable")
        return payload, self._parse_lyrics_payload(payload, preferences, song=song)

    def _parse_lyrics_payload(self, payload, preferences, enrichment=None, *, song=None):
        # Parsing is read-only; a shallow view preserves the established
        # conversion/timing machinery without changing the shared store getter.
        parser = copy.copy(self.lyrics_store)
        parser.settings_getter = lambda key, default=None: preferences.get(key, default)
        baseline = parser.parse_online_lyrics(
            raw_lrc=payload.raw_lrc, raw_yrc=payload.raw_yrc, translation=payload.translation,
            romanization=payload.romanization, source=payload.source or payload.source_id,
            raw_qrc=payload.raw_qrc, raw_ttml=payload.raw_ttml,
            raw_netease=payload.raw_netease)
        if (baseline is not None and song is not None and song.media_kind == "song"
                and (payload.source_id, payload.song_id) == (song.source_id, song.song_id)):
            # The immutable queue song was captured with this worker request.
            # Bind its verified identity without consulting mutable Qt state.
            baseline = replace(baseline, verified_identity=(song.source_id, song.song_id,
                song.title, song.artist, song.album, song.duration_seconds))
        if enrichment is None:
            return baseline
        candidate = parser.reparse_source_match(enrichment)
        if candidate is not None and parser.is_compatible_lyric_upgrade(baseline, candidate):
            # Identity/version/clock compatibility precedes companion reuse.
            # The official payload stays intact for later language toggles.
            return parser._retain_matching_subrows(candidate, baseline) if baseline is not None else candidate
        return baseline

    def _position_changed(self, milliseconds):
        if self._closed or (self._stream_epoch != self._epoch
                and not self._catalog_stream_is_current()):
            return
        if self._is_voice_segment():
            # The decoder is speech; the introduced song's visible clock
            # stays paused at zero until its music actually starts.
            return
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
        if self._catalog_pending_epoch and self._catalog_stream_is_current():
            self._catalog_stream.decoded_duration_ms = max(0, milliseconds)
        elif self._closed or self._stream_epoch != self._epoch:
            return
        if self.track is not None and self._stream_epoch == self._epoch:
            self._decoded_duration_ms = max(0, milliseconds)
        if self._is_voice_segment():
            return
        if self.track is not None and milliseconds > 0:
            self.track = replace(self.track, duration_seconds=milliseconds / 1000.0)

    def _state_changed(self, state):
        if self._dj_overlay_is_preview:
            self._sync_dj_preview_music_state()
        if (self._stream_fault is not None and state in {
                QMediaPlayer.PlaybackState.PausedState, QMediaPlayer.PlaybackState.PlayingState}):
            epoch, reason, _position, _observed_at = self._stream_fault
            self._stream_fault = (epoch, reason, self._qt_position_ms(), time.monotonic())
        if self.track is not None:
            self.track = replace(self.track, is_playing=(state == QMediaPlayer.PlaybackState.PlayingState
                and (self._stream_epoch == self._epoch or self._catalog_stream_is_current()) and not self._closed
                and not self._is_voice_segment()
                and self.status != "playback_failed" and self._stream_recovery is None))
            if self._is_voice_segment() and self._stream_epoch == self._epoch:
                self._dj_state = "speaking" if state == QMediaPlayer.PlaybackState.PlayingState else "ready"
            if (state == QMediaPlayer.PlaybackState.PlayingState
                    and self._stream_epoch == self._epoch and not self._catalog_pending_epoch
                    and not self._closed and self._stream_recovery is None):
                self._set_status("trial" if self._stream_trial else "playing")
            self.changed.emit()
        if self._dj_overlay_voices:
            if state == QMediaPlayer.PlaybackState.PlayingState:
                self._try_start_dj_overlay()
            elif not self._dj_overlay_is_preview:
                self.dj_player.pause()
        elif state == QMediaPlayer.PlaybackState.PlayingState:
            self._maybe_start_dj_preview()

    def _media_status(self, status):
        if self._catalog_pending_epoch and self._catalog_stream_is_current():
            if status == QMediaPlayer.MediaStatus.EndOfMedia:
                self._catalog_stream.ended = True
                self.track = replace(self.track, is_playing=False)
                self.changed.emit()
            elif status == QMediaPlayer.MediaStatus.InvalidMedia:
                if self._catalog_stream.error is None:
                    self._catalog_stream.error = QMediaPlayer.Error.ResourceError
            return
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
        self._stop_dj_overlay()
        self._clear_stream_fault()
        self._stream_epoch = 0
        if self._is_voice_segment():
            if self.index + 1 < len(self.queue):
                self.index += 1
                self._load_song(autoplay=self._autoplay_after_load)
            else:
                self._voice_failed("invalid_response")
            return
        if (self.playback_mode == "repeat_one" and not self._play_next_indices
                and self.queue[self.index].media_kind != "dj_voice"):
            self._load_song()
        else:
            self._advance_queue()

    @staticmethod
    def _request_failure(error):
        return error if error in {"auth_required", "credential", "timeout", "network", "unsupported", "invalid_response", "response",
            "incomplete", "expired", "too_large", "storage", "access_denied"} else "unavailable"

    def _error(self, code, *_args):
        if code != QMediaPlayer.Error.NoError and self._catalog_pending_epoch and self._catalog_stream_is_current():
            # Keep the old decoder's buffered-tail evidence. If replacement
            # fails, replay this into the existing fault/recovery machinery;
            # successful replacement discards it with the old source.
            self._catalog_stream.error = code
            return
        if (code == QMediaPlayer.Error.NoError or self._closed or self.track is None
                or self._stream_epoch != self._epoch):
            return
        reason = {
            QMediaPlayer.Error.NetworkError: "network",
            QMediaPlayer.Error.FormatError: "format",
            QMediaPlayer.Error.AccessDeniedError: "access_denied",
            QMediaPlayer.Error.ResourceError: "resource",
        }.get(code, "backend")
        if self._voice_failed(reason):
            return
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
        if self._is_voice_segment() and not callable(position):
            return max(0, int(position or 0))
        return max(0, int(position())) if callable(position) else round(self.track.position_seconds * 1000) if self.track else 0

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
        self._stop_dj_overlay()
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
        self._release_prepared_audio()
        accepted = self.tasks.submit(("stream", epoch, catalog_epoch),
            lambda: self._resolve_stream(song)
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
            self._play_music()
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
        self._stop_dj_overlay()
        if self._voice_failed(reason):
            return
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
            if self._output_preparing:
                self._output_prepare_resume = not self._output_prepare_resume
                self._autoplay_after_load = self._output_prepare_resume
                self._fm_resume = self._playlist_resume = self._output_prepare_resume
                if self._stream_recovery is not None:
                    self._stream_recovery.resume = self._output_prepare_resume
                self.changed.emit()
                return True
            if self._catalog_stream is not None:
                self._catalog_stream.ended = False
            if self._stream_recovery is not None:
                self._stream_recovery.resume = not self._stream_recovery.resume
                if not self._stream_recovery.resume:
                    self.player.pause()
                elif self._stream_recovery.phase == "progress":
                    self._play_music()
                self.changed.emit()
                return True
            if self._dj_overlay_voices and not self._dj_overlay_is_preview and (
                    self._stream_epoch != self._epoch and not self._catalog_stream_is_current() or not self._dj_source_ready):
                # Preparation now owns separate music and speech sources.
                # A user pause changes intent without starting a partial pair.
                self._autoplay_after_load = not self._autoplay_after_load
                self._fm_resume = self._autoplay_after_load
                if not self._autoplay_after_load:
                    self.player.pause()
                    self.dj_player.pause()
                self.changed.emit()
                return True
            if self._stream_epoch == 0 and (self._is_voice_segment() and self.status == "loading_stream"
                    or self.status == "loading_catalog" and (self._fm_advance_pending or self._playlist_waiting_next
                        or self._catalog_pending_epoch and self._catalog_pending_kind
                        in {"fm_continue", "fm_skip_continue"})):
                self._autoplay_after_load = not self._autoplay_after_load
                self._fm_resume = self._autoplay_after_load
                self._playlist_resume = self._autoplay_after_load
                self.changed.emit()
                return True
            if self.status in {"playback_failed", "stream_unavailable"} and self._stream_epoch == 0:
                return self._load_song() if self.queue and 0 <= self.index < len(self.queue) else False
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self._autoplay_after_load = False
                self.player.pause()
            else:
                self._autoplay_after_load = True
                self._play_music()
            return True
        if command in {"previous", "next"}:
            if command == "next":
                self._autoplay_after_load = True
                if self._play_next_indices:
                    return self._advance_queue()
            if self._fm_source and (self._fm_kind == "dj"
                    or any(song.media_kind == "dj_voice" for song in self.queue)):
                step = -1 if command == "previous" else 1
                base = self._introduced_song_index() if self._is_voice_segment() else self.index
                if base is None:
                    return False
                target = base + step
                while 0 <= target < len(self.queue) and self.queue[target].media_kind == "dj_voice":
                    target += step
                if target < 0:
                    return False
                if target >= len(self.queue):
                    return self._advance_fm(skip_voice=not self.personal_dj_enabled)
                self.index = self._intro_start_for_song(target)
                resume = True if command == "next" else (self._autoplay_after_load if self._stream_epoch == 0
                    else self.player.playbackState() != QMediaPlayer.PlaybackState.PausedState)
                return self._load_song(autoplay=resume)
            return self._advance_queue(-1 if command == "previous" else 1)
        return False

    def seek_playback_position(self, seconds, expected_track_id=None):
        if self.track is None or (expected_track_id and expected_track_id != self.track.track_id):
            return False
        if self._closed:
            return False
        if self._is_voice_segment():
            # The visible timestamp belongs to the paused introduced song.
            # It must not seek the independent spoken introduction.
            return False
        if QThread.currentThread() != self.thread():
            return self._request_control("seek", seconds, expected_track_id or self.track.track_id)
        if not self.player.isSeekable():
            return False
        self._stop_dj_overlay()
        seconds = max(0.0, min(float(seconds), self.track.duration_seconds))
        recovery = self._stream_recovery
        if recovery is not None and recovery.phase == "authorize":
            return False
        self._clear_stream_recovery()
        self._clear_stream_fault()
        if self._catalog_stream is not None:
            self._catalog_stream.ended = False
        epoch, stream_epoch = self._epoch, self._stream_epoch
        self.player.setPosition(round(seconds * 1000))
        if recovery is not None:
            if self._closed or self._epoch != epoch or self._stream_epoch != stream_epoch or self.status == "playback_failed":
                return False
            self._set_status("trial" if self._stream_trial else "playing")
            if self._closed or self._epoch != epoch or self._stream_epoch != stream_epoch or self.status == "playback_failed":
                return False
            if recovery.resume:
                self._play_music()
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
            if kind == "personal_dj":
                result["succeeded"] = self.set_personal_dj_enabled(value)
            elif kind == "personal_dj_voice":
                result["succeeded"] = self.set_personal_dj_voice(value)
            elif kind == "personal_dj_voice_restore":
                result["succeeded"] = self.restore_personal_dj_voice(value)
            elif kind == "personal_dj_preview":
                result["succeeded"] = self.preview_personal_dj_voice(value)
            elif kind == "mode":
                result["succeeded"] = self.set_playback_mode(value)
            elif kind == "cancel_selection":
                result["succeeded"] = self.cancel_pending_selection()
            elif self.track is not None and self.track.track_id == track_id:
                if kind == "lyrics_refresh":
                    result["succeeded"] = self.refresh_lyrics()
                elif kind == "enqueue_next":
                    result["succeeded"] = self.enqueue_next(value)
                elif kind == "queue_index":
                    result["succeeded"] = self.select_queue_index(*value)
                elif kind == "queue_artwork":
                    result["succeeded"] = self.prefetch_queue_artwork(*value)
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
        if self.download_tasks is not None:
            self.download_tasks.close()
        if self.stream_cache is not None:
            self.stream_cache.close()

    def stop(self):
        """Invalidate queued I/O before disabling or changing playback modes."""
        self._epoch += 1
        self._stop_dj_overlay()
        self._reset_lyrics_payload()
        self._catalog_epoch += 1
        self._catalog_pending_epoch = 0
        self._catalog_stream = None
        self._dj_state = "idle"
        self._dj_next_title = ""
        self._dj_identity = None
        self._dj_active_epoch = 0
        self._dj_failed_until = 0.0
        self._dj_failure = None
        self._dj_next_lyrics = None
        self._ready_song_lyrics = None
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
        self._release_prepared_audio()
        self._set_status("idle")
        self.changed.emit()
