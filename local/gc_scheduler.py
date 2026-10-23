"""Run Python's cyclic GC on the Qt main thread at safe points instead of whenever allocation trips it.

Automatic GC can fire mid-animation (a full pass stalls ~40ms) or mid-Qt call, destroying Qt objects
held in reference cycles at an unsafe moment. Young generations are collected every tick; the full
generation waits until no animation is running, or until max_full_interval has passed.
"""
from __future__ import annotations

import gc
import time

from PySide6.QtCore import QTimer


class GcScheduler:
    def __init__(self, *, busy=lambda: False, gc_module=gc, clock=time.monotonic,
                 idle_full_interval=30.0, max_full_interval=120.0):
        self._busy = busy
        self._gc = gc_module
        self._clock = clock
        self._idle_full_interval = float(idle_full_interval)
        self._max_full_interval = float(max_full_interval)
        self._last_full = clock()
        self._young_threshold, self._middle_threshold, _ = gc_module.get_threshold()

    def start(self):
        self._gc.disable()
        self._last_full = self._clock()

    def freeze(self):
        """Move startup objects out of later full passes; call once startup has settled."""
        self._gc.collect()
        self._gc.freeze()
        self._last_full = self._clock()

    def tick(self) -> int | None:
        """Collect at most one generation; return which one, or None."""
        now = self._clock()
        since_full = now - self._last_full
        if since_full >= self._max_full_interval or (
                since_full >= self._idle_full_interval and not self._busy()):
            self._gc.collect(2)
            self._last_full = now
            return 2
        young, middle, _ = self._gc.get_count()
        if middle >= self._middle_threshold:
            self._gc.collect(1)
            return 1
        if young >= self._young_threshold:
            self._gc.collect(0)
            return 0
        return None


def install_gc_scheduler(parent, *, busy, tick_ms=250, freeze_after_ms=5000) -> GcScheduler:
    """Disable automatic GC and drive it from a main-thread timer owned by parent."""
    scheduler = GcScheduler(busy=busy)
    scheduler.start()
    timer = QTimer(parent)
    timer.setInterval(int(tick_ms))
    timer.timeout.connect(scheduler.tick)
    timer.start()
    scheduler.timer = timer
    QTimer.singleShot(int(freeze_after_ms), scheduler.freeze)
    return scheduler
