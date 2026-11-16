"""Optional UIA adapter with a single quarantinable STA worker.

A COM call cannot be killed safely. A timed-out operation keeps its worker
slot until completion; new reads/writes fail closed and no replacement thread
is spawned. Cached native wrappers never leave their apartment.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import sys
import threading
import time

from api.capture.contracts import TimelineSample


class StaWorker:
    """One lazy daemon STA thread, one pending operation, bounded caller waits."""

    def __init__(self, *, timeout=0.7, initializer=None):
        self.timeout = timeout
        self.initializer = initializer or self._initialize_com
        self._queue = queue.Queue(maxsize=1)
        self._lock = threading.Lock()
        self._thread = None
        self._pending = None
        self._closed = False
        self.timeouts = 0
        self.errors = 0
        self.calls = 0

    @staticmethod
    def _initialize_com():
        if sys.platform != 'win32':
            raise OSError('UI Automation requires Windows')
        import ctypes
        # Initialize before pywinauto/comtypes is imported on this apartment.
        result = ctypes.windll.ole32.CoInitializeEx(None, 2)
        if result not in (0, 1):
            raise OSError(f'Cannot initialize UIA STA: {result}')
        return ctypes.windll.ole32.CoUninitialize

    def _run(self):
        cleanup = None
        initialization_error = None
        try:
            try:
                cleanup = self.initializer()
            except Exception as exc:
                initialization_error = exc
            while True:
                item = self._queue.get()
                if item is None:
                    return
                operation, future = item
                try:
                    if initialization_error is not None:
                        raise initialization_error
                    with self._lock:
                        closed_before_dispatch = self._closed
                    if closed_before_dispatch:
                        raise RuntimeError('UIA worker closed before dispatch')
                    result = operation()
                    error = None
                except Exception as exc:
                    self.errors += 1
                    result, error = None, exc
                with self._lock:
                    if self._pending is future:
                        self._pending = None
                    closed = self._closed
                # Release the slot before notifying asyncio; its consumer may
                # immediately request the next independent physical read.
                if error is None:
                    future.set_result(result)
                else:
                    future.set_exception(error)
                if closed:
                    return
        finally:
            if callable(cleanup):
                cleanup()

    @property
    def busy(self):
        with self._lock:
            return self._pending is not None

    async def call(self, operation):
        with self._lock:
            if self._closed or self._pending is not None:
                raise RuntimeError('UIA worker unavailable or quarantined')
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='GluSpotCaptureUIA', daemon=True)
                self._thread.start()
            future = concurrent.futures.Future()
            self._pending = future
            self.calls += 1
            self._queue.put_nowait((operation, future))
        try:
            # Shield leaves the future alive after timeout/cancellation, so the
            # slot remains occupied until the actual native operation finishes.
            wrapped = asyncio.wrap_future(future)
            # A cancelled/timed-out caller still owns observing a late COM
            # exception; consuming it does not change normal await behavior.
            wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            done, _pending = await asyncio.wait({wrapped}, timeout=self.timeout)
            if not done:
                raise asyncio.TimeoutError()
            return wrapped.result()
        except asyncio.TimeoutError:
            self.timeouts += 1
            raise

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._thread is not None and self._pending is None:
                self._queue.put_nowait(None)


class UiaBackend:
    """Fresh progress calibration and one-attempt seek on an owned STA worker."""

    def __init__(self, service: str, track, *, worker=None, reader_factory=None, clock=time.monotonic):
        self.service = service
        self.track = track
        self.clock = clock
        self.worker = worker or StaWorker()
        self._owns_worker = worker is None
        self._reader_factory = reader_factory
        self._reader = None
        self._requests: set[str] = set()
        self._closed = False
        self._next_calibration = 0.0
        self._last_calibration = 0.0
        self._last_raw_sample = None

    def _get_reader(self):
        if self._reader is None:
            if self._reader_factory is not None:
                self._reader = self._reader_factory()
            else:
                # pywinauto is imported lazily inside these reader methods.
                from api.netease_position import NeteasePositionReader, QQPositionReader
                self._reader = QQPositionReader() if self.service == 'qq' else NeteasePositionReader()
        return self._reader

    def _invalidate(self):
        if self._reader is not None:
            self._reader.reset()
        self._reader = None
        self._last_raw_sample = None

    async def read(self):
        if self._closed:
            return None
        track = self.track
        def operation():
            if self._closed:
                return None
            try:
                sample = self._get_reader().read_progress_position(track.title, track.artist, track.duration_seconds)
                if sample is None:
                    self._invalidate()
                return sample
            except Exception:
                self._invalidate()
                raise
        sample = await self.worker.call(operation)
        if sample is None or self._closed:
            return None
        return TimelineSample(track.source_app_id, track.track_id, sample.position_seconds,
                              self.clock(), time.time(), is_playing=(track.is_playing if sample.is_playing is None else sample.is_playing),
                              end_seconds=sample.duration_seconds, source='uia')

    async def seek(self, position_seconds, *, request_id):
        if self._closed or request_id in self._requests:
            return False
        # Reserve before the worker call; timeouts cannot cause resubmission.
        self._requests.add(request_id)
        if len(self._requests) > 256:
            self._requests = {request_id}
        track = self.track
        def operation():
            if self._closed:
                return False
            try:
                return self._get_reader().seek_playback_position_once(
                    position_seconds, track.duration_seconds, track.title, track.artist)
            except Exception:
                self._invalidate()
                raise
        return await self.worker.call(operation)

    async def calibrate(self, *, anomaly=False):
        """At most one sample per 7.5 s, or per 2 s during an anomaly."""
        now = self.clock()
        if self._closed or self.worker.busy or now < self._next_calibration:
            return None
        interval = 2.0 if anomaly else 7.5
        if now - self._last_calibration < interval:
            return None
        self._next_calibration = now + interval
        self._last_calibration = now
        try:
            return await self.read()
        except Exception:
            return None  # Includes COM errors, disappearing windows and UIPI.

    def close(self):
        self._closed = True
        if self._owns_worker:
            self.worker.close()

    def diagnostics(self):
        return dict(service=self.service, calls=self.worker.calls, timeouts=self.worker.timeouts,
                    errors=self.worker.errors, quarantined=self.worker.busy)
