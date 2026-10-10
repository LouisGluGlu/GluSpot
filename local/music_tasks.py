"""Bounded daemon workers for account/catalog I/O; callbacks run on Qt's thread."""
from __future__ import annotations

import queue
import threading

from PySide6.QtCore import QObject, Signal


class MusicTasks(QObject):
    completed = Signal(object, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = queue.Queue(maxsize=24)
        self._closed = False
        for index in range(2):
            threading.Thread(target=self._work, name=f"GluSpotMusic-{index}", daemon=True).start()

    def submit(self, token, action) -> bool:
        if self._closed:
            return False
        try:
            self._queue.put_nowait((token, action))
            return True
        except queue.Full:
            return False

    def _work(self):
        while True:
            item = self._queue.get()
            if item is None:
                return
            token, action = item
            result, error = None, None
            if not self._closed:
                try:
                    result = action()
                except Exception as exc:
                    # Never send arbitrary network exception text (which can
                    # contain a signed URL or account cookie) to UI/logs.
                    error = getattr(exc, "code", "request_failed")
            if not self._closed:
                self.completed.emit(token, result, error)

    def close(self):
        self._closed = True
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        for _ in range(2):
            self._queue.put_nowait(None)
