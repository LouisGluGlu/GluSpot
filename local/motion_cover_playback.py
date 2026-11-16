"""Muted looping motion covers painted inside the existing rounded cover slot.

QMediaPlayer decodes on the multimedia thread. Frames are scaled to the cover
slot and drawn by the same painter that draws the static cover, so lyrics and
the private-broadcast overlay stay above the video. Hidden, minimized, and
uncovered surfaces pause and then stop the decoder.
"""
from __future__ import annotations

import os
import threading
import time
import weakref

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QImage, QPainter

from api.artwork_sources import artwork_text_key
from api.motion_cover import (
    CancelToken, MotionCoverCache, MotionCoverResolver, MotionResult, MotionTrack,
    default_motion_cache_dir, download_video, log_motion,
)

FADE_SECONDS = 0.28
_session = None


def install_motion_cover(session) -> None:
    global _session
    _session = session


def shared_session():
    return _session


def motion_track_from_host(host) -> MotionTrack:
    if host is None:
        return MotionTrack("")
    title = str(getattr(host, "track_title", "") or "")
    artist = str(getattr(host, "artist", "") or "")
    track_id = str(getattr(host, "media_track_id", "") or "")
    album = ""
    raw_queue = getattr(host, "album_queue", [])
    queue = raw_queue if isinstance(raw_queue, (list, tuple)) else []
    for entry in queue:
        if isinstance(entry, dict):
            entry_title = str(entry.get("title", "") or "")
            entry_artist = str(entry.get("artist", "") or "")
            entry_id = str(entry.get("track_id", "") or "")
            entry_album = str(entry.get("album_title", "") or "")
        else:
            entry_title = str(getattr(entry, "title", "") or "")
            entry_artist = str(getattr(entry, "artist", "") or "")
            entry_id = str(getattr(entry, "track_id", "") or "")
            entry_album = str(getattr(entry, "album_title", "") or "")
        same_id = bool(track_id) and entry_id == track_id
        same_names = artwork_text_key(entry_title) == artwork_text_key(title) and artwork_text_key(entry_artist) == artwork_text_key(artist)
        if same_id or same_names:
            album = entry_album
            break
    try:
        duration = float(getattr(host, "media_duration_seconds", 0.0) or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return MotionTrack(
        title=title, artist=artist, album=album, duration_seconds=max(0.0, duration),
        track_id=track_id, source_app=str(getattr(host, "media_source_app", "") or ""),
    )


def observe_host(host) -> None:
    session = shared_session()
    if session is None:
        return
    session.note_track(motion_track_from_host(host))
    session.note_playback(bool(getattr(host, "media_is_playing", True)))


def note_cover_visible(widget, rect: QRectF) -> None:
    session = shared_session()
    if session is None or widget is None:
        return
    shown = _widget_shown(widget)
    side = rect.width() if isinstance(rect, QRectF) else 0.0
    session.note_cover(widget, side if shown else 0.0, shown, rect if shown else QRectF())


def note_cover_hidden(widget) -> None:
    session = shared_session()
    if session is not None and widget is not None:
        session.note_cover(widget, 0.0, False, QRectF())


def _widget_shown(widget) -> bool:
    try:
        return bool(widget.isVisible()) and not bool(widget.isMinimized())
    except RuntimeError:
        return False


def paint_motion_cover(painter: QPainter, rect: QRectF, track_key: str) -> None:
    """Draw the current frame inside the caller's clip. Opacity eases in from 0."""
    session = shared_session()
    if session is None or rect.isEmpty():
        return
    image, opacity = session.reveal(track_key)
    if image is None or image.isNull() or opacity <= 0.004:
        return
    painter.save()
    painter.setOpacity(painter.opacity() * opacity)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    source = _center_square(image)
    painter.drawImage(rect, image, source)
    painter.restore()


def _center_square(image: QImage) -> QRectF:
    side = min(image.width(), image.height())
    return QRectF((image.width() - side) / 2.0, (image.height() - side) / 2.0, side, side)


class _Delivery(QObject):
    ready = Signal(int, object)


class MotionCoverSession(QObject):
    """One decoder shared by the island, the window, and the library player."""

    def __init__(self, *, enabled: bool = True, resolver: MotionCoverResolver | None = None,
                 player_factory=None, parent=None):
        super().__init__(parent)
        cache_dir = default_motion_cache_dir()
        self._resolver = resolver or MotionCoverResolver(MotionCoverCache(cache_dir))
        self._player_factory = player_factory
        self._enabled = bool(enabled)
        self._generation = 0
        self._key = ""
        self._track: MotionTrack | None = None
        self._image = QImage()
        self._fade_at = 0.0
        self._source = ""
        self._loaded_source = ""
        self._failed = False
        self._playing = False
        self._song_playing = True
        self._hidden_since = 0.0
        self._player = None
        self._sink = None
        self._widgets: dict[int, weakref.ReferenceType] = {}
        self._sides: dict[int, int] = {}
        self._rects: dict[int, QRectF] = {}
        self._cancel = CancelToken()
        self._delivery = _Delivery(self)
        self._delivery.ready.connect(self._on_ready)
        self._watch = QTimer(self)
        self._watch.setInterval(250)
        self._watch.timeout.connect(self._watch_visibility)
        self._fade = QTimer(self)
        self._fade.setInterval(16)
        self._fade.timeout.connect(self._tick_fade)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        track = self._track
        if not enabled:
            self.clear()
            self._track = track
            return
        if track is not None and track.lookup_allowed():
            self._key = ""
            self.note_track(track)

    def clear(self) -> None:
        self._generation += 1
        self._cancel.cancel()
        self._cancel = CancelToken()
        self._key = ""
        self._track = None
        self._image = QImage()
        self._fade_at = 0.0
        self._fade.stop()
        self._source = ""
        self._failed = False
        self._stop_decoder(immediate=True)
        self._notify()

    def note_track(self, track: MotionTrack) -> None:
        self._track = track
        if not self._enabled:
            return
        if not track.lookup_allowed():
            if self._key:
                self.clear()
                self._track = track
            return
        key = track.cache_key()
        if key == self._key:
            return
        self._generation += 1
        self._cancel.cancel()
        self._cancel = CancelToken()
        self._key = key
        self._image = QImage()
        self._fade_at = 0.0
        self._source = ""
        self._loaded_source = ""
        self._failed = False
        self._stop_decoder(immediate=True)
        generation = self._generation
        cancel = self._cancel
        threading.Thread(
            target=self._resolve, args=(track, generation, cancel),
            name="GluSpotMotionCover", daemon=True,
        ).start()

    def note_playback(self, playing: bool) -> None:
        """Pause the decoder with the song and continue from the same frame."""
        playing = bool(playing)
        if playing == self._song_playing:
            return
        self._song_playing = playing
        self._sync_playback()

    def note_cover(self, widget, logical_side: float, shown: bool, rect: QRectF) -> None:
        identifier = id(widget)
        self._widgets[identifier] = weakref.ref(widget)
        pixels = 0
        if shown and logical_side > 0:
            try:
                ratio = float(widget.devicePixelRatioF())
            except (RuntimeError, TypeError, ValueError):
                ratio = 1.0
            pixels = max(1, min(720, int(round(float(logical_side) * max(1.0, ratio)))))
            self._hidden_since = 0.0
        current = self._rects.get(identifier)
        if self._sides.get(identifier) == pixels and isinstance(rect, QRectF) and current == rect:
            return
        self._sides[identifier] = pixels
        if isinstance(rect, QRectF) and not rect.isEmpty():
            self._rects[identifier] = QRectF(rect)
        self._sync_playback()
        if not self._watch.isActive():
            self._watch.start()

    def reveal(self, track_key: str) -> tuple[QImage | None, float]:
        if track_key != self._key or self._image.isNull():
            return None, 0.0
        if self._fade_at <= 0.0:
            self._fade_at = time.monotonic()
            if not self._fade.isActive():
                self._fade.start()
            return self._image, 0.0
        opacity = min(1.0, max(0.0, (time.monotonic() - self._fade_at) / FADE_SECONDS))
        return self._image, opacity

    def supply_frame(self, image: QImage) -> None:
        """Test seam for a decoded frame. Real playback arrives through QVideoSink."""
        self._accept_frame(QImage(image))

    def shutdown(self) -> None:
        self._generation += 1
        self._cancel.cancel()
        self._watch.stop()
        self._fade.stop()
        self._stop_decoder(immediate=True)
        self._image = QImage()

    def _resolve(self, track: MotionTrack, generation: int, cancel: CancelToken) -> None:
        try:
            result = self._resolver.resolve(track, cancel)
        except Exception as exc:
            log_motion("motion", "error", 0, type(exc).__name__)
            result = MotionResult("none", "motion")
        if generation == self._generation and not cancel.is_set():
            self._delivery.ready.emit(generation, result)
        if result.kind == "hit" and result.url and not result.local_path and generation == self._generation:
            path = download_video(result.url, self._resolver.cache, track.cache_key(), cancel)
            if path and generation == self._generation:
                self._delivery.ready.emit(generation, MotionResult("hit", result.provider, result.url, path))

    def _on_ready(self, generation: int, result: MotionResult) -> None:
        if generation != self._generation or not self._enabled or not isinstance(result, MotionResult):
            return
        if result.kind != "hit":
            return
        source = result.local_path if result.local_path and os.path.isfile(result.local_path) else result.url
        if not source:
            return
        if self._source == source:
            return
        # A file that finishes after streaming has started must not restart the loop.
        if self._loaded_source and result.local_path:
            return
        self._source = source
        self._failed = False
        self._sync_playback()

    def _ensure_player(self) -> bool:
        if self._player is not None:
            return True
        if self._player_factory is not None:
            self._player = self._player_factory(self._accept_frame)
            return self._player is not None
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
        except Exception as exc:
            log_motion("player", "error", 0, type(exc).__name__)
            return False
        player = QMediaPlayer(self)
        sink = QVideoSink(self)
        player.setVideoOutput(sink)
        player.setLoops(QMediaPlayer.Loops.Infinite)
        sink.videoFrameChanged.connect(self._on_video_frame)
        player.errorOccurred.connect(self._on_player_error)
        self._player = player
        self._sink = sink
        return True

    def _on_video_frame(self, frame) -> None:
        if not self._playing or frame is None or not frame.isValid():
            return
        now = time.monotonic()
        if now - getattr(self, "_last_frame_at", 0.0) < (1.0 / 30.0):
            return
        self._last_frame_at = now
        image = frame.toImage()
        if image.isNull():
            return
        self._accept_frame(image.copy())

    def _accept_frame(self, image: QImage) -> None:
        if image.isNull() or not self._playing:
            return
        side = self._target_side()
        if side > 0 and max(image.width(), image.height()) > side:
            image = image.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
        self._image = image
        self._notify()

    def _on_player_error(self, *_args) -> None:
        error = getattr(self._player, "errorString", lambda: "")()
        # errorString can echo a URL. Keep only the enum name.
        name = "MediaError"
        error_value = getattr(self._player, "error", lambda: None)()
        if error_value is not None:
            name = type(error_value).__name__ if not hasattr(error_value, "name") else str(error_value.name)
        log_motion("player", "error", 0, name)
        del error
        self._failed = True
        self._playing = False
        self._image = QImage()
        self._fade_at = 0.0
        self._stop_decoder(immediate=True)
        self._notify()

    def _target_side(self) -> int:
        sides = [side for side in self._sides.values() if side > 0]
        return max(sides) if sides else 480

    def _visible(self) -> bool:
        alive = False
        for identifier, ref in list(self._widgets.items()):
            widget = ref()
            if widget is None:
                self._widgets.pop(identifier, None)
                self._sides.pop(identifier, None)
                self._rects.pop(identifier, None)
                continue
            if not _widget_shown(widget):
                self._sides[identifier] = 0
            if self._sides.get(identifier, 0) > 0:
                alive = True
        if alive:
            self._hidden_since = 0.0
        elif self._hidden_since <= 0.0:
            self._hidden_since = time.monotonic()
        return alive

    def _sync_playback(self) -> None:
        visible = self._visible()
        should = bool(self._enabled and self._source and not self._failed and visible and self._song_playing)
        if not should and self._player is None:
            self._playing = False
            return
        if not self._ensure_player():
            return
        player = self._player
        if should:
            if self._loaded_source != self._source:
                url = QUrl.fromLocalFile(self._source) if os.path.isfile(self._source) else QUrl(self._source)
                player.setSource(url)
                set_loops = getattr(player, "setLoops", None)
                if callable(set_loops):
                    from PySide6.QtMultimedia import QMediaPlayer
                    set_loops(QMediaPlayer.Loops.Infinite)
                self._loaded_source = self._source
                self._image = QImage()
                self._fade_at = 0.0
            self._playing = True
            if not _is_playing(player):
                player.play()
            return
        self._playing = False
        hidden_for = time.monotonic() - self._hidden_since if self._hidden_since else 0.0
        self._stop_decoder(immediate=hidden_for >= 1.0)

    def _stop_decoder(self, *, immediate: bool) -> None:
        self._playing = False
        player = self._player
        if player is None:
            return
        if immediate:
            player.stop()
            self._loaded_source = ""
        elif _is_playing(player):
            player.pause()

    def _watch_visibility(self) -> None:
        self._sync_playback()
        if not self._widgets and not self._source:
            self._watch.stop()

    def _tick_fade(self) -> None:
        if self._fade_at <= 0.0:
            self._fade.stop()
            return
        if (time.monotonic() - self._fade_at) >= FADE_SECONDS:
            self._fade.stop()
        self._notify()

    def _notify(self) -> None:
        for identifier, ref in list(self._widgets.items()):
            widget = ref()
            if widget is None:
                continue
            rect = self._rects.get(identifier)
            try:
                if rect is not None and not rect.isEmpty():
                    widget.update(rect.adjusted(-2, -2, 2, 2).toAlignedRect())
                else:
                    widget.update()
            except RuntimeError:
                self._widgets.pop(identifier, None)


def _is_playing(player) -> bool:
    state = getattr(player, "state", None)
    if isinstance(state, str):
        return state == "playing"
    try:
        from PySide6.QtMultimedia import QMediaPlayer
        return player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
    except Exception:
        return False
