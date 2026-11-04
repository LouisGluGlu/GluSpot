"""Bounded daemon workers for account/catalog I/O; callbacks run on Qt's thread."""
from __future__ import annotations

import queue
import threading
import time

from PySide6.QtCore import QObject, Signal
from api.online_music_diagnostics import login_trace, record_login_phase


class MusicTasks(QObject):
    completed = Signal(object, object, object)

    def __init__(self, parent=None, *, worker_count=2, task_timeout_seconds=None):
        super().__init__(parent)
        self._queue = queue.Queue(maxsize=24)
        self._closed = False
        self._worker_count = max(1, min(2, int(worker_count)))
        self._task_timeout_seconds = (max(.01, float(task_timeout_seconds))
            if task_timeout_seconds is not None else None)
        # Timed pools retain at most one reserve action per worker. A blocked
        # socket cannot poison both dispatchers, and repeated timeouts cannot
        # create unlimited abandoned threads. Ordinary pools keep their direct
        # execution path and existing backend cancellation contracts.
        self._action_slots = threading.BoundedSemaphore(self._worker_count * 2)
        for index in range(self._worker_count):
            threading.Thread(target=self._work, name=f"GluSpotMusic-{index}", daemon=True).start()

    def submit(self, token, action) -> bool:
        if self._closed:
            return False
        try:
            self._queue.put_nowait((token, action, time.monotonic()))
            return True
        except queue.Full:
            return False

    def discard(self, predicate):
        """Remove actions that have not started; running I/O needs backend cancel."""
        with self._queue.mutex:
            retained = [item for item in self._queue.queue
                        if item is None or not predicate(item[0])]
            removed = len(self._queue.queue) - len(retained)
            self._queue.queue.clear()
            self._queue.queue.extend(retained)
            self._queue.not_full.notify_all()
        return removed

    def _work(self):
        while True:
            item = self._queue.get()
            if item is None:
                return
            token, action, queued_at = item
            result, error = None, None
            if not self._closed:
                try:
                    kind, generation, *extra = token if isinstance(token, tuple) and len(token) >= 2 else ("", 0)
                    source = extra[0] if extra else ""
                    if kind == "open" and len(extra) > 1:
                        source = extra[1]
                    if kind in {"account", "begin", "poll", "open"} and source in {"netease", "qq", "kugou"}:
                        deadline = queued_at + 18.
                        record_login_phase(source, kind, "task_queue", time.monotonic() - queued_at,
                                           generation, "timeout" if time.monotonic() >= deadline else "none")
                        if time.monotonic() >= deadline:
                            error = "timeout"
                        else:
                            with login_trace(source, kind, generation, deadline=deadline):
                                result = action()
                    else:
                        if self._task_timeout_seconds is None:
                            result = action()
                        else:
                            result, error = self._timed_action(action,
                                queued_at + self._task_timeout_seconds)
                except Exception as exc:
                    # Never send arbitrary network exception text (which can
                    # contain a signed URL or account cookie) to UI/logs.
                    error = getattr(exc, "code", "request_failed")
            if not self._closed:
                self.completed.emit(token, result, error)

    def _timed_action(self, action, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self._action_slots.acquire(blocking=False):
            return None, "timeout"
        done, outcome = threading.Event(), {}

        def execute():
            try:
                if not self._closed:
                    outcome["result"] = action()
            except Exception as exc:
                outcome["error"] = getattr(exc, "code", "request_failed")
            finally:
                self._action_slots.release()
                done.set()

        try:
            threading.Thread(target=execute, name="GluSpotMusicAction", daemon=True).start()
        except RuntimeError:
            self._action_slots.release()
            return None, "busy"
        if not done.wait(remaining) or time.monotonic() >= deadline:
            return None, "timeout"
        return outcome.get("result"), outcome.get("error")

    def close(self):
        self._closed = True
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        for _ in range(self._worker_count):
            self._queue.put_nowait(None)
