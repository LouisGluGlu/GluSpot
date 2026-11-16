"""One bounded CPU worker per live owner; GUI-only cache/result delivery."""
from __future__ import annotations

from collections import OrderedDict
import time

from PySide6.QtCore import QObject, Signal, Slot
from local.music_tasks import MusicTasks
from local.music_lyric_preparation import snapshot_lyric_input, prepare_lyric_presentation, retained_size


def lyric_owner_signature(owner):
    timed = getattr(owner, "timed_lyrics", ()) or ()
    units = getattr(owner, "timed_lyric_units", ()) or ()
    kinds = getattr(owner, "timed_lyric_timing_kinds", ()) or ()
    return (
        str(getattr(owner, "media_track_id", "") or ""),
        str(getattr(owner, "track_title", "") or ""), str(getattr(owner, "artist", "") or ""),
        int(getattr(owner, "_lyric_presentation_revision", 0)), tuple(timed),
        tuple(str(line) for line in (getattr(owner, "lyrics", ()) or ())),
        id(units), len(units), id(kinds), len(kinds),
    )


class LyricPresentationStore(QObject):
    changed = Signal()
    MAX_ENTRIES = 4
    MAX_BYTES = 8 * 1024 * 1024

    def __init__(self, owner, *, tasks=None):
        super().__init__(owner)
        self.owner = owner
        self.tasks = tasks if tasks is not None else MusicTasks(self, worker_count=1, task_timeout_seconds=5.)
        self.tasks.completed.connect(self._completed)
        close_tasks = self.tasks.close
        self.destroyed.connect(lambda *_args: close_tasks())
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.current = None
        self.last_valid = None
        self.base = None
        self.active = None
        self.active_snapshot = None
        self.pending = None
        self.failed = None
        self.retried = None
        self.generation = 0
        self.request_serial = 0
        self.closed = False
        self.hits = self.misses = self.discarded = self.submitted = 0
        self.prepare_seconds = 0.

    def get(self, *, translated=None, source=None):
        if self.closed:
            return None
        base = lyric_owner_signature(self.owner)
        if base != self.base:
            self.base = base
            self.generation += 1
            self.pending = None
            self.failed = None
            self.retried = None
            self.current = None
            if self.previous_compatible() is None:
                self.last_valid = None
        translated = bool(getattr(self.owner, "enable_dual_line_lyrics", False)) if translated is None else bool(translated)
        override = None if source is None else (tuple(source[0]), id(source[1]), id(source[2]))
        key = (base, translated, override)
        if self.current is not None and self.current[0] == key:
            self.hits += 1
            return self.current[1]
        cached = self.cache.pop(key, None)
        if cached is not None:
            self.cache[key] = cached
            self.hits += 1
            return cached[0]
        self.misses += 1
        if self.failed == key or (self.active is not None and self.active[1] == key):
            return None
        if self.pending is None or self.pending[1] != key:
            # Snapshot before submitting: no owner, QWidget, font, layout,
            # QPixmap or mutable provider object crosses the worker boundary.
            self.pending = (self.generation, key, snapshot_lyric_input(
                self.owner, translated=translated, source=source,
            ))
        self._start()
        return None

    def previous_compatible(self, *, translated=None):
        """Retain a valid island image during a settings/timing-only update.

        Island playback indexes belong to the owner's raw rows. Never reuse
        old indexed plans if text/timestamps/order changed, even on one song.
        A surface can retain both companion states; island callers selecting
        one state must not inherit the other while their worker is pending.
        """
        title = str(getattr(self.owner, "track_title", "") or "")
        artist = str(getattr(self.owner, "artist", "") or "")
        identity = str(getattr(self.owner, "media_track_id", "") or "") or f"{title}\x1f{artist}"
        timed = tuple(getattr(self.owner, "timed_lyrics", ()) or ())
        lines = tuple(getattr(self.owner, "lyrics", ()) or ())
        candidates = [self.last_valid]
        if translated is not None:
            # This cache is bounded to four entries. A previous matching
            # selection can survive a source revision without reshaping or
            # briefly showing a different companion state on the island.
            candidates.extend(entry[0] for entry in reversed(self.cache.values()))
        for result in candidates:
            if result is None:
                continue
            source = result.source
            if translated is not None and source.translated != bool(translated):
                continue
            if ((source.identity, source.title, source.artist) == (identity, title, artist)
                    and source.timed == timed and source.lines == lines):
                return result
        return None

    def _start(self):
        if self.closed or self.active is not None or self.pending is None:
            return
        generation, key, snapshot = self.pending
        self.pending = None
        self.request_serial += 1
        token = (generation, key, self.request_serial)
        self.active = token
        self.active_snapshot = snapshot
        def work():
            started = time.perf_counter()
            result = prepare_lyric_presentation(snapshot)
            elapsed = time.perf_counter() - started
            return result, retained_size((key, result)), elapsed
        if self.tasks.submit(token, work):
            self.submitted += 1
        else:
            self.active = None
            self.active_snapshot = None
            self.failed = key

    @Slot(object, object, object)
    def _completed(self, token, value, error):
        if self.closed or token != self.active:
            return
        self.active = None
        snapshot, self.active_snapshot = self.active_snapshot, None
        generation, key, _serial = token
        # Recheck actual owner values, not just the last request. A provider
        # can change songs while this window is hidden and has no paint tick.
        if generation != self.generation or key[0] != lyric_owner_signature(self.owner):
            self.discarded += 1
        elif error is not None or value is None:
            self.failed = key
            # Publish failure so a cached loading sentinel can leave the
            # skeleton immediately. One bounded retry may still recover ink;
            # it never reopens a visible loader or replaces established text.
            if self.retried != key:
                self.retried = key
                self.pending = (generation, key, snapshot)
            self.changed.emit()
        else:
            result, byte_count, self.prepare_seconds = value
            self.failed = None
            self.current = (key, result)
            self.last_valid = result
            if byte_count <= self.MAX_BYTES:
                self.cache[key] = (result, byte_count)
                self.cache_bytes += byte_count
                while len(self.cache) > self.MAX_ENTRIES or self.cache_bytes > self.MAX_BYTES:
                    _old_key, (_old_result, old_bytes) = self.cache.popitem(last=False)
                    self.cache_bytes -= old_bytes
            else:
                # A single unusually large current song remains usable;
                # never retain multiple such results in the reusable cache.
                self.cache.clear()
                self.cache_bytes = 0
            self.changed.emit()
        self._start()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.generation += 1
        self.pending = None
        self.cache.clear()
        self.current = None
        self.last_valid = None
        self.cache_bytes = 0
        self.tasks.close()
